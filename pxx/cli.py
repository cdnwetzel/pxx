"""pxx entry point: detect endpoint, pick model, exec aider."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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
    """Find the aider binary — prefer the one in our own venv."""
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


def _in_git_repo() -> bool:
    """True if cwd is inside a git work tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            check=False,
            timeout=2,
        )
        return result.returncode == 0 and result.stdout.strip() == b"true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Safety foundation (#002)
# ---------------------------------------------------------------------------

SAFETY_TAG_PREFIX = "pxx-pre/"
SAFETY_TAG_RETENTION_DAYS = 30


def _self_sanity_check(module_name: str = "pxx.endpoints") -> None:
    """Refuse to launch if a critical pxx module fails to import.

    Protects against self-modification (Tier 3 of #001) leaving pxx in a
    broken state. Without this, a bad self-edit produces a confusing crash
    mid-startup rather than a clear "your edit broke me" message.

    Exits with status 2 on failure so a wrapper can distinguish "pxx broken"
    from normal exit codes.
    """
    try:
        importlib.import_module(module_name)
    except Exception as e:
        repo_root = REPO_ROOT
        print(
            f"pxx: own module `{module_name}` failed to import: {e}\n"
            f"  pxx may have been broken by a self-edit.\n"
            f"  Recover with one of:\n"
            f"    git -C {repo_root} reflog\n"
            f"    git -C {repo_root} reset --hard <last-known-good>\n"
            f"    git -C {repo_root} reset --hard pxx-pre/<unix-ts>",
            file=sys.stderr,
        )
        sys.exit(2)


def _git_dirty() -> bool:
    """True if cwd's git work tree has uncommitted or untracked changes."""
    try:
        # Tracked-but-uncommitted changes (staged or unstaged).
        diff = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        return diff.returncode == 0 and bool(diff.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _has_commits() -> bool:
    """True iff the current git repo has at least one commit (HEAD resolved).

    Empty repos (``git init`` with no commit yet) have an unborn HEAD;
    ``git tag <name>`` fails because there's nothing to point at.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            timeout=2,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _create_safety_tag() -> str | None:
    """Create a local-only safety tag at HEAD; stash dirty state.

    Returns the tag name on success, ``None`` if not in a git repo or git
    operations fail (best-effort — safety tags are a convenience, not a
    correctness guarantee).

    Tag namespace `pxx-pre/<unix-ts>` is local-only by design: `git deliver`
    pushes only `main`, not tags, so safety tags never reach the remotes.
    """
    if not _in_git_repo():
        return None

    ts = int(time.time())
    tag = f"{SAFETY_TAG_PREFIX}{ts}"

    try:
        # Stash any uncommitted changes first so the tag points at a clean
        # HEAD. The stash itself is recoverable via `git stash list`.
        if _git_dirty():
            subprocess.run(
                [
                    "git",
                    "stash",
                    "push",
                    "--include-untracked",
                    "--message",
                    f"{tag}: working state at session start",
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )

        # Create the tag at current HEAD.
        result = subprocess.run(
            ["git", "tag", tag],
            capture_output=True,
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        return tag
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _prune_old_safety_tags(retention_days: int = SAFETY_TAG_RETENTION_DAYS) -> None:
    """Delete `pxx-pre/<ts>` tags older than `retention_days`.

    Best-effort, silent on errors. Tag names embed unix timestamps, so we
    parse the suffix rather than asking git for tag dates. Malformed tags
    in the namespace are skipped.
    """
    if not _in_git_repo():
        return

    cutoff = int(time.time()) - (retention_days * 86400)

    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"{SAFETY_TAG_PREFIX}*"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return
        for tag in result.stdout.strip().splitlines():
            suffix = tag.removeprefix(SAFETY_TAG_PREFIX)
            try:
                ts = int(suffix)
            except ValueError:
                continue
            if ts < cutoff:
                subprocess.run(
                    ["git", "tag", "-d", tag],
                    capture_output=True,
                    check=False,
                    timeout=2,
                )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return


def _build_aider_args(
    aider_bin: str,
    model: str,
    user_args: list[str],
    in_git_repo: bool,
    edit_mode: bool,
    extra_reads: list[Path] | None = None,
) -> list[str]:
    """Construct the argv to exec into aider with.

    In ask mode (default), pass ``--chat-mode ask`` for explicit read-only
    behavior. In edit mode, pass **nothing** — aider uses its default edit
    flow with the edit-format set by ``config/aider.conf.yml`` (currently
    ``diff``). Explicit ``--chat-mode`` in user_args wins over both.

    Note: in aider 0.86.2, ``--chat-mode`` and ``--edit-format`` are the
    same argument under two names, and there is no value called ``code``.
    Passing ``--chat-mode code`` errors out — hence we omit it for edit
    mode and rely on the config default.

    Optional ``extra_reads`` are passed as additional ``--read`` files
    after the system prompt (e.g., the commands-context file).
    """
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


def _write_scope_context(scope_prefixes: list[str]) -> Path | None:
    """Write a scope-directive markdown file for aider's `--read` context.

    Returns the absolute path written, or ``None`` if no scopes were given.
    Same overwrite-fixed-filename pattern as ``_write_commands_context``.
    """
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


def _git_repo_root() -> Path | None:
    """Return the absolute Path of the current git repo's top-level, or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        return Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _write_commands_context(commands: list[CommandInfo]) -> Path | None:
    """Write the slash-command listing to a tempfile for aider's `--read` context.

    Returns the absolute path to the written file, or ``None`` if no commands
    were found. The file is overwritten on each invocation — fixed filename
    means at most one stale file exists, and no cleanup is needed.
    """
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


def _self_test() -> int:
    """Run `uv run pytest -q` against the pxx repo, regardless of cwd (#001 T1).

    Returns the child's exit code. Banner + status line go to stderr so the
    pytest output on stdout stays clean for piping.
    """
    cmd = ["uv", "run", "pytest", "-q"]
    print(
        f"pxx: self-test — running `{' '.join(cmd)}` in {REPO_ROOT}",
        file=sys.stderr,
    )
    rc = subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode
    status = "passed" if rc == 0 else "failed"
    print(f"pxx: self-test — {status} ({rc})", file=sys.stderr)
    return rc


def _self_lint() -> int:
    """Run ruff check and ruff format --check against the pxx repo (#001 T1).

    Both sub-commands always run (don't short-circuit on first failure) so
    the user sees every violation in one pass. Returns 0 only if both pass;
    otherwise the combined non-zero is the bitwise OR of the two exit codes
    — preserving distinguishability if a caller wants to switch on it.
    """
    check_cmd = ["uv", "run", "ruff", "check", "."]
    format_cmd = ["uv", "run", "ruff", "format", "--check", "."]

    print(
        f"pxx: self-lint — running `{' '.join(check_cmd)}` in {REPO_ROOT}",
        file=sys.stderr,
    )
    check_rc = subprocess.run(check_cmd, cwd=REPO_ROOT, check=False).returncode
    print(
        f"pxx: self-lint — running `{' '.join(format_cmd)}` in {REPO_ROOT}",
        file=sys.stderr,
    )
    format_rc = subprocess.run(format_cmd, cwd=REPO_ROOT, check=False).returncode

    combined = check_rc | format_rc
    print(
        f"pxx: self-lint — check={check_rc} format={format_rc} combined={combined}",
        file=sys.stderr,
    )
    return combined


def _install_precommit_hook() -> None:
    """Invoke scripts/install-precommit-hook.sh in the current working dir.

    Used by `pxx --install-hook` to wire the per-repo pre-commit gate
    (ruff + pytest + diff cap) into the cwd's git repo.
    """
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

    if "--install-hook" in sys.argv:
        _install_precommit_hook()
        # _install_precommit_hook exits, so we never reach this line.

    # T1 (#001 Dogfooding): portable health-check commands that target the
    # pxx repo regardless of cwd. They don't need Ollama or the sanity check
    # (the test/lint run itself catches any import-time breakage), so they
    # short-circuit at the same level as --list-commands.
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    if "--self-lint" in sys.argv:
        sys.exit(_self_lint())

    # M3 (#002): pre-launch self-sanity. Runs before any other work so a
    # self-edit that broke pxx surfaces a clear recovery message rather
    # than a confusing mid-startup crash.
    _self_sanity_check()

    edit_mode = "--edit" in sys.argv
    big_mode = "--big" in sys.argv
    # S2 (#003): --dry-run is an aider flag (already in its arg parser);
    # we detect it only for banner purposes and let it pass through to
    # aider naturally — no need to strip it from user_args.
    dry_run = "--dry-run" in sys.argv
    # S3 (#003): --anywhere is a one-session bypass for the trusted-paths
    # gate. Stripped from user_args before aider sees it.
    anywhere_mode = "--anywhere" in sys.argv

    # S3 (#003): trusted-paths gate. Fires only on --edit when the user
    # has populated ~/.config/pxx/trusted-paths. Missing/empty file means
    # all paths trusted (opt-in feature; no behavior change by default).
    # Runs before endpoint detection so a wrong-cwd mistake fails fast,
    # without paying the ~1s probe cost.
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

    # S1 (#003): consume --scope <path> (and --scope=<path>) before
    # the --edit/--big/--anywhere strip, since extract_scope_args needs to
    # see them in argv order to pair flag with value.
    scope_args, argv_after_scope = extract_scope_args(sys.argv[1:])
    user_args = [a for a in argv_after_scope if a not in ("--edit", "--big", "--anywhere")]
    in_git_repo = _in_git_repo()

    # Resolve scope paths against the repo root. Without a git repo, scope
    # has no enforcement surface (no commits to gate), so we warn and drop
    # rather than failing — keeps behavior consistent with the no-repo case
    # for the safety tag in M1.
    scope_prefixes: list[str] = []
    if scope_args:
        if not in_git_repo:
            print(
                "pxx: --scope ignored outside a git repo (no commit gate to anchor).",
                file=sys.stderr,
            )
        else:
            repo_root = _git_repo_root()
            if repo_root is None:
                print(
                    "pxx: --scope ignored — could not determine git repo root.",
                    file=sys.stderr,
                )
            else:
                try:
                    scope_prefixes = resolve_scopes(scope_args, repo_root)
                except ValueError as e:
                    print(f"pxx: {e}", file=sys.stderr)
                    sys.exit(1)
                os.environ["PXX_SCOPE"] = format_for_env(scope_prefixes)

    os.environ["OLLAMA_API_BASE"] = endpoint.url
    # M4 (#002): --big tells the pre-commit hook to skip the per-session
    # diff cap. Set the env var before exec so the hook (which runs
    # later, in the aider-spawned shell) sees it.
    if big_mode:
        os.environ["PXX_ALLOW_BIG_DIFF"] = "1"
    model = model_for(endpoint)
    aider_bin = _find_aider()

    # M1 (#002): pre-session safety tag for --edit sessions in a git repo
    # with at least one commit (empty repos have an unborn HEAD and can't
    # be tagged). Prune old tags first (cheap), then create today's.
    safety_tag: str | None = None
    empty_repo = False
    if edit_mode and in_git_repo:
        if _has_commits():
            _prune_old_safety_tags()
            safety_tag = _create_safety_tag()
        else:
            empty_repo = True

    if edit_mode:
        mode_label = "edit (untrusted path)" if untrusted_override else "edit"
    else:
        mode_label = "ask (read-only — pass --edit to allow changes)"
    print(
        f"pxx: endpoint={endpoint.name} ({endpoint.url})  model={model}  mode={mode_label}",
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
        print(
            "pxx: --big set — pre-commit diff cap bypassed for this session.",
            file=sys.stderr,
        )
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

    args = _build_aider_args(
        aider_bin, model, user_args, in_git_repo, edit_mode, extra_reads=extra_reads
    )
    os.execv(aider_bin, args)


if __name__ == "__main__":
    main()
