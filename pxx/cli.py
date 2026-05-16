"""pxx: orchestrator for the offline aider workflow.

Detects Ollama endpoints, selects models, applies safety tags, manages
path-prefix scoping, and dispatches to various dogfooding modes.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pxx import _git, audit, drift, safety, self_modes
from pxx._core_files import is_core
from pxx.commands_index import CommandInfo, list_commands
from pxx.endpoints import Endpoint, detect_endpoint
from pxx.scope import (
    extract_scope_args,
    format_for_env,
    is_path_trusted,
    load_trusted_paths,
    resolve_scopes,
    trusted_paths_config_path,
)

# Compatibility re-exports for moved symbols.
# Tests monkeypatch these names on the cli module, so we must use them
# internally within this module too.
SAFETY_TAG_PREFIX = safety.SAFETY_TAG_PREFIX
_in_git_repo = _git.is_in_repo
_git_dirty = _git.is_dirty
_has_commits = _git.has_commits
_git_repo_root = _git.repo_root
_git_head_sha = _git.head_sha
_create_safety_tag = safety.create_tag
_prune_old_safety_tags = safety.prune_old_tags


def _self_sanity_check(module_name: str = "pxx.endpoints") -> None:
    return safety.sanity_check(REPO_ROOT, module_name)


def _self_test() -> int:
    return self_modes.self_test(REPO_ROOT)


def _self_lint() -> int:
    return self_modes.self_lint(REPO_ROOT)


_extract_self_fix_task = self_modes.extract_self_fix_task
_determine_session_class = self_modes.determine_session_class
SELF_FIX_DIFF_CAP = self_modes.SELF_FIX_DIFF_CAP

PKG_DIR = Path(__file__).parent
REPO_ROOT = PKG_DIR.parent
SYSTEM_PROMPT = PKG_DIR / "prompts" / "system.md"
SELF_IMPROVE_PROMPT = PKG_DIR / "prompts" / "self-improve.md"
AIDER_CONF = REPO_ROOT / "config" / "aider.conf.yml"
MODEL_SETTINGS = REPO_ROOT / "config" / "model-settings.yml"

STUDIO_DEFAULT = "ollama_chat/devstral:24b"
NEO_DEFAULT = "ollama_chat/qwen3:4b"


def model_for(endpoint: Endpoint) -> str:
    # Only the "neo" endpoint name gets NEO_DEFAULT; every other name
    # (including PXX_OLLAMA_BASE "override") is assumed to be a Studio-class
    # machine and gets STUDIO_DEFAULT. Override the assumption with PXX_MODEL.
    override = os.environ.get("PXX_MODEL")
    if override:
        return override
    return NEO_DEFAULT if endpoint.name == "neo" else STUDIO_DEFAULT


def _find_aider() -> str:
    # Prefer the aider binary in our own venv if it exists.
    same_venv = Path(sys.executable).parent / "aider"
    if same_venv.exists():
        return str(same_venv)
    found = shutil.which("aider")
    if found:
        return found
    print(
        "pxx: aider not found. Reinstall: uv tool install --editable . --python 3.12",
        file=sys.stderr,
    )
    sys.exit(1)


def _build_aider_args(
    aider_bin: str,
    model: str,
    user_args: list[str],
    in_git_repo: bool,
    edit_mode: bool,
    extra_reads: list[Path] | None = None,
) -> list[str]:
    """Construct the argv to exec into aider with."""
    has_chat_mode = any(a == "--chat-mode" or a.startswith("--chat-mode=") for a in user_args)
    chat_mode_args: list[str] = []
    if not has_chat_mode and not edit_mode:
        # Only inject in ask mode. Edit mode lets aider use its default +
        # config's edit-format=diff.
        chat_mode_args = ["--chat-mode", "ask"]

    extra_read_args: list[str] = []
    for p in extra_reads or []:
        extra_read_args.extend(["--read", str(p)])

    args = [
        aider_bin,
        "--model",
        model,
        "--read",
        str(SYSTEM_PROMPT),
        *extra_read_args,
        "--config",
        str(AIDER_CONF),
        "--model-settings-file",
        str(MODEL_SETTINGS),
        *chat_mode_args,
    ]
    if not in_git_repo:
        args.append("--no-git")
    args.extend(user_args)
    return args


COMMANDS_CONTEXT_FILE = "pxx-commands-context.md"
"""Filename used for the in-session command-listing context file in $TMPDIR."""

SCOPE_CONTEXT_FILE = "pxx-scope-context.md"
"""Filename used for the in-session scope-directive context file in $TMPDIR."""


def _try_write_session_start(record: dict) -> None:
    """Write a session_start record, swallowing all errors (#004)."""
    with contextlib.suppress(Exception):
        audit.write_session_start(record)


def _write_commands_context(commands: list[CommandInfo]) -> Path | None:
    """Write the slash-command listing to a tempfile for aider's `--read` context."""
    if not commands:
        return None

    tmp = Path(tempfile.gettempdir()) / COMMANDS_CONTEXT_FILE
    # Find a representative example for the routing instruction.
    example = next((c for c in commands if c.name == "typecheck"), commands[0])
    lines = [
        "# Available slash commands",
        "",
        "**Before answering any request, scan this list first.** If the user's",
        "message maps to one of these commands, your reply MUST lead with the",
        "matching `/load <path>` line and a one-sentence pitch — only fall",
        "through to direct help if the user declines or no command applies.",
        "Do not invent commands; only suggest from this list.",
        "",
        "## Example",
        "",
        'User: "Add type hints to this function"',
        f'You: "Try `/load {example.path}` — it is tuned for exactly this kind of task.',
        '     Share the function if you want me to apply hints directly instead."',
        "",
        "## Commands",
        "",
    ]
    for c in commands:
        lines.append(f"- `/load {c.path}` — {c.description}")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def _print_command_listing() -> None:
    """Print available slash commands and their /load paths to stdout."""
    commands = list_commands()
    if not commands:
        print("No slash commands found in pxx/commands/", file=sys.stderr)
        return

    name_width = max(len(c.name) for c in commands)
    print("Available slash commands:")
    print()
    for c in commands:
        print(f"  /{c.name:<{name_width}}  — {c.description}")
    print()
    print("Paste-ready /load lines:")
    for c in commands:
        print(f"  /load {c.path}")


def _write_scope_context(scope_prefixes: list[str]) -> Path | None:
    """Write a scope-directive markdown file for aider's `--read` context."""
    if not scope_prefixes:
        return None

    tmp = Path(tempfile.gettempdir()) / SCOPE_CONTEXT_FILE
    lines = [
        "# SCOPE RESTRICTION",
        "",
        "**This session may only edit files under these path prefixes:**",
        "",
    ]
    for p in scope_prefixes:
        lines.append(f"- `{p or '(repo root)'}`")
    lines.extend(
        [
            "",
            "If asked to change a file outside this scope, refuse and tell the",
            "user to widen the scope by re-running pxx with another `--scope <path>`.",
            "Do not produce SEARCH/REPLACE blocks for out-of-scope files.",
            "",
            "If the user's task requires editing files outside this scope, say so",
            "explicitly and ask them to widen the scope; do not try to work around",
            "the restriction.",
        ]
    )
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def _emit_core_restart_banner() -> None:
    """Print a one-line banner if a core pxx module changed since the
    previous session in this repo (#008 M2).
    """
    if not _in_git_repo():
        return
    root = _git_repo_root()
    if root is None or root.resolve() != REPO_ROOT.resolve():
        return
    cur_sha = _git_head_sha()
    if not cur_sha:
        return
    try:
        prev_sha = audit.last_session_head_for(str(root))
    except Exception:  # noqa: BLE001 — audit lookup is best-effort
        return
    if not prev_sha or prev_sha == cur_sha:
        return
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{prev_sha}..{cur_sha}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        return
    core_changed = [f for f in result.stdout.strip().splitlines() if is_core(f)]
    if not core_changed:
        return
    short = cur_sha[:7]
    names = ", ".join(Path(p).name for p in core_changed)
    print(
        f"pxx: loaded freshly-edited {names} (commit {short})",
        file=sys.stderr,
    )


def _install_precommit_hook() -> None:
    """Invoke scripts/install-precommit-hook.sh in the current working dir."""
    script = REPO_ROOT / "scripts" / "install-precommit-hook.sh"
    if not script.exists():
        print(f"pxx: installer script not found at {script}", file=sys.stderr)
        sys.exit(1)
    cmd = ["bash", str(script)]
    if "--force" in sys.argv:
        cmd.append("--force")
    if "--uninstall" in sys.argv:
        cmd.append("--uninstall")
    result = subprocess.run(cmd, check=False)
    sys.exit(result.returncode)


def main() -> None:
    if "--list-commands" in sys.argv:
        _print_command_listing()
        sys.exit(0)

    if "--check-sync" in sys.argv:
        res = drift.check_sync()
        drift.print_report(res)
        sys.exit(0 if res.is_synced or res.error else 1)

    if "--install-hook" in sys.argv:
        _install_precommit_hook()

    if "--self-test" in sys.argv:
        _try_write_session_start({"session_class": "self-test", "cwd": str(Path.cwd())})
        sys.exit(_self_test())
    if "--self-lint" in sys.argv:
        _try_write_session_start({"session_class": "self-lint", "cwd": str(Path.cwd())})
        sys.exit(_self_lint())

    _self_sanity_check()
    _emit_core_restart_banner()

    with contextlib.suppress(Exception):
        audit.prune_old_logs()

    edit_mode = "--edit" in sys.argv or "--self-fix" in sys.argv or "--self-improve" in sys.argv
    big_mode = "--big" in sys.argv
    dry_run = "--dry-run" in sys.argv
    anywhere_mode = "--anywhere" in sys.argv
    self_improve_mode = "--self-improve" in sys.argv
    self_fix_mode = "--self-fix" in sys.argv

    # #006 M2: optional pre-edit drift check.
    # Off by default; PXX_AUTOCHECK_DRIFT=1 to opt-in.
    autocheck = os.environ.get("PXX_AUTOCHECK_DRIFT") == "1"
    skip_check = "--no-check-sync" in sys.argv
    if edit_mode and autocheck and not skip_check:
        res = drift.check_sync()
        if not res.is_synced:
            drift.print_report(res)

    if self_fix_mode and self_improve_mode:
        print("pxx: --self-fix and --self-improve are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    if self_improve_mode and "--edit" in sys.argv:
        print(
            "pxx: --self-improve is ask-only — remove --edit (Tier 2 is suggest-only by design).",
            file=sys.stderr,
        )
        sys.exit(2)

    self_fix_task: str | None = None
    argv_after_self_fix = sys.argv[1:]
    if self_fix_mode:
        self_fix_task, argv_after_self_fix = _extract_self_fix_task(argv_after_self_fix)

    if self_improve_mode or self_fix_mode:
        os.chdir(REPO_ROOT)

    untrusted_override = False
    if edit_mode:
        trusted_prefixes = load_trusted_paths()
        if trusted_prefixes:
            path_trusted, closest = is_path_trusted(Path.cwd(), trusted_prefixes)
            if not path_trusted:
                if not anywhere_mode:
                    cfg = trusted_paths_config_path()
                    print(
                        f"pxx: cwd is not under any trusted prefix.\n"
                        f"  cwd:          {Path.cwd()}\n"
                        f"  config:       {cfg}\n"
                        f"  closest:      {closest}\n"
                        f"  Override one-shot: pxx --edit --anywhere ...\n"
                        f"  Or trust this path: add it to {cfg}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                untrusted_override = True

    try:
        endpoint = detect_endpoint()
    except RuntimeError as e:
        print(f"pxx: {e}", file=sys.stderr)
        sys.exit(1)

    scope_args, argv_after_scope = extract_scope_args(argv_after_self_fix)
    user_args = [
        a
        for a in argv_after_scope
        if a
        not in (
            "--edit",
            "--big",
            "--anywhere",
            "--self-improve",
            "--self-fix",
            "--check-sync",
            "--no-check-sync",
        )
    ]
    if self_fix_task:
        has_message = any(a == "--message" or a.startswith("--message=") for a in user_args)
        if not has_message:
            user_args = ["--message", self_fix_task, *user_args]

    in_git_repo = _in_git_repo()
    scope_prefixes: list[str] = []
    if scope_args:
        if not in_git_repo:
            print(
                "pxx: --scope ignored outside a git repo (no commit gate to anchor).",
                file=sys.stderr,
            )
        else:
            root = _git_repo_root()
            if root is None:
                print(
                    "pxx: --scope ignored — could not determine git repo root.",
                    file=sys.stderr,
                )
            else:
                try:
                    scope_prefixes = resolve_scopes(scope_args, root)
                except ValueError as e:
                    print(f"pxx: {e}", file=sys.stderr)
                    sys.exit(1)
                os.environ["PXX_SCOPE"] = format_for_env(scope_prefixes)

    if self_fix_mode and not scope_prefixes:
        print(
            "pxx: --self-fix requires --scope <path>; "
            "refusing to run an autonomous edit without explicit scope.",
            file=sys.stderr,
        )
        sys.exit(2)

    os.environ["OLLAMA_API_BASE"] = endpoint.url
    if big_mode:
        os.environ["PXX_ALLOW_BIG_DIFF"] = "1"
    if self_fix_mode:
        os.environ["PXX_AUTONOMOUS"] = "1"
        if "PXX_DIFF_CAP" not in os.environ:
            os.environ["PXX_DIFF_CAP"] = str(SELF_FIX_DIFF_CAP)

    model = model_for(endpoint)
    aider_bin = _find_aider()

    safety_tag: str | None = None
    empty_repo = False
    if edit_mode and in_git_repo:
        if _has_commits():
            _prune_old_safety_tags()
            safety_tag = _create_safety_tag()
        else:
            empty_repo = True

    if self_improve_mode:
        mode_label = "ask (self-improve)"
    elif edit_mode:
        parts: list[str] = []
        if untrusted_override:
            parts.append("untrusted path")
        if self_fix_mode:
            parts.append("autonomous")
        mode_label = "edit" + (f" ({', '.join(parts)})" if parts else "")
    else:
        mode_label = "ask (read-only — pass --edit to allow changes)"

    print(
        f"pxx: endpoint={endpoint.name} ({endpoint.url})  model={model}  mode={mode_label}",
        file=sys.stderr,
    )
    if self_fix_mode:
        cap = os.environ.get("PXX_DIFF_CAP", str(SELF_FIX_DIFF_CAP))
        print(
            f"pxx: --self-fix: task={self_fix_task!r}  diff_cap={cap}  "
            f"commits will be tagged [autonomous].",
            file=sys.stderr,
        )
    if safety_tag:
        print(
            f"pxx: safety tag {safety_tag} — undo session with: git reset --hard {safety_tag}",
            file=sys.stderr,
        )
    elif empty_repo:
        print(
            "pxx: empty git repo (no commits yet) — safety tag skipped. "
            "Make at least one commit to enable it.",
            file=sys.stderr,
        )

    if big_mode and edit_mode and not dry_run:
        print("pxx: --big set — pre-commit diff cap bypassed for this session.", file=sys.stderr)
    elif big_mode and not edit_mode:
        print(
            "pxx: --big has no effect in ask mode (no commits to gate); ignored.",
            file=sys.stderr,
        )
    elif big_mode and dry_run:
        print(
            "pxx: --big has no effect with --dry-run (no commits will land); ignored.",
            file=sys.stderr,
        )

    if dry_run and edit_mode:
        print(
            "pxx: --dry-run set — aider will describe changes but not write or commit them.",
            file=sys.stderr,
        )
    elif dry_run and not edit_mode:
        print(
            "pxx: --dry-run is redundant in ask mode (no writes either way); ignored.",
            file=sys.stderr,
        )

    if scope_prefixes:
        display = ", ".join(p or "(repo root)" for p in scope_prefixes)
        print(
            f"pxx: scope={display} — session limited to these prefixes "
            "(hook will reject out-of-scope commits).",
            file=sys.stderr,
        )

    if not in_git_repo:
        print(
            "pxx: no git repo here — auto-commits disabled. Run `git init` to enable.",
            file=sys.stderr,
        )

    commands_context = _write_commands_context(list_commands())
    extra_reads = [commands_context] if commands_context else []
    scope_context = _write_scope_context(scope_prefixes)
    if scope_context is not None:
        extra_reads.append(scope_context)
    if self_improve_mode:
        extra_reads.append(SELF_IMPROVE_PROMPT)

    args = _build_aider_args(
        aider_bin, model, user_args, in_git_repo, edit_mode, extra_reads=extra_reads
    )

    root = _git_repo_root() if in_git_repo else None
    sha = _git_head_sha() if in_git_repo else None
    git_dirty: bool | None = _git_dirty() if in_git_repo else None
    record: dict = {
        "session_class": self_modes.determine_session_class(
            edit_mode, dry_run, self_improve_mode, self_fix_mode
        ),
        "model": model,
        "endpoint_name": endpoint.name,
        "endpoint_url": endpoint.url,
        "cwd": str(Path.cwd()),
        "git_repo_root": str(root) if root else None,
        "git_head_sha": sha,
        "git_dirty": git_dirty,
        "scope": list(scope_prefixes),
        "edit_mode": edit_mode,
        "dry_run": dry_run,
        "big": big_mode,
        "autonomous": self_fix_mode,
        "diff_cap": int(os.environ.get("PXX_DIFF_CAP", "100")),
        "untrusted_path": untrusted_override,
        "aider_history_path": ".aider.chat.history.md",
    }
    _try_write_session_start(record)

    os.execv(aider_bin, args)


if __name__ == "__main__":
    main()
