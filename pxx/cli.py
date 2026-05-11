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

PKG_DIR = Path(__file__).parent
REPO_ROOT = PKG_DIR.parent
SYSTEM_PROMPT = PKG_DIR / "prompts" / "system.md"
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

    Default chat mode is `ask` (read-only); `--edit` flips to `code` (the
    standard editing flow). Explicit `--chat-mode` in user_args wins over both.
    Optional ``extra_reads`` are passed as additional ``--read`` files after
    the system prompt (e.g., the commands-context file).
    """
    has_chat_mode = any(a == "--chat-mode" or a.startswith("--chat-mode=") for a in user_args)
    chat_mode_args: list[str] = []
    if not has_chat_mode:
        chat_mode_args = ["--chat-mode", "code" if edit_mode else "ask"]

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

    # M3 (#002): pre-launch self-sanity. Runs before any other work so a
    # self-edit that broke pxx surfaces a clear recovery message rather
    # than a confusing mid-startup crash.
    _self_sanity_check()

    try:
        endpoint = detect_endpoint()
    except RuntimeError as e:
        print(f"pxx: {e}", file=sys.stderr)
        sys.exit(1)

    edit_mode = "--edit" in sys.argv
    big_mode = "--big" in sys.argv
    user_args = [a for a in sys.argv[1:] if a not in ("--edit", "--big")]
    in_git_repo = _in_git_repo()

    os.environ["OLLAMA_API_BASE"] = endpoint.url
    # M4 (#002): --big tells the pre-commit hook to skip the per-session
    # diff cap. Set the env var before exec so the hook (which runs
    # later, in the aider-spawned shell) sees it.
    if big_mode:
        os.environ["PXX_ALLOW_BIG_DIFF"] = "1"
    model = model_for(endpoint)
    aider_bin = _find_aider()

    # M1 (#002): pre-session safety tag for --edit sessions in a git repo.
    # Prune old tags first (cheap), then create today's.
    safety_tag: str | None = None
    if edit_mode and in_git_repo:
        _prune_old_safety_tags()
        safety_tag = _create_safety_tag()

    mode_label = "edit" if edit_mode else "ask (read-only — pass --edit to allow changes)"
    print(
        f"pxx: endpoint={endpoint.name} ({endpoint.url})  model={model}  mode={mode_label}",
        file=sys.stderr,
    )
    if safety_tag:
        print(
            f"pxx: safety tag {safety_tag} — undo session with: git reset --hard {safety_tag}",
            file=sys.stderr,
        )
    if big_mode and edit_mode:
        print(
            "pxx: --big set — pre-commit diff cap bypassed for this session.",
            file=sys.stderr,
        )
    elif big_mode and not edit_mode:
        print(
            "pxx: --big has no effect in ask mode (no commits to gate); ignored.",
            file=sys.stderr,
        )

    if not in_git_repo:
        print(
            "pxx: no git repo here — auto-commits disabled. Run `git init` to enable.",
            file=sys.stderr,
        )

    commands_context = _write_commands_context(list_commands())
    extra_reads = [commands_context] if commands_context else []

    args = _build_aider_args(
        aider_bin, model, user_args, in_git_repo, edit_mode, extra_reads=extra_reads
    )
    os.execv(aider_bin, args)


if __name__ == "__main__":
    main()
