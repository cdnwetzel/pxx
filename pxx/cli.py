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

from pxx import _git, audit, drift, governance, review_gate, safety, self_modes, workflow
from pxx._core_files import is_core
from pxx.commands_index import CommandInfo, list_commands
from pxx.cost_metrics import CostMetrics, TokenMetrics
from pxx.endpoints import Endpoint, detect_endpoint
from pxx.memory import AgentmemoryManager
from pxx.memory_analytics import MemoryAnalytics
from pxx.memory_injection import MemoryInjector
from pxx.observer import AiderMemoryObserver
from pxx.router import NineroterManager
from pxx.scope import (
    extract_scope_args,
    format_for_env,
    is_path_trusted,
    load_trusted_paths,
    resolve_scopes,
    trusted_paths_config_path,
)
from pxx.skills import SkillRegistry

# Path constants — define first since compat aliases below reference REPO_ROOT.
PKG_DIR = Path(__file__).parent
REPO_ROOT = PKG_DIR.parent
SYSTEM_PROMPT = PKG_DIR / "prompts" / "system.md"
SELF_IMPROVE_PROMPT = PKG_DIR / "prompts" / "self-improve.md"
AIDER_CONF = REPO_ROOT / "config" / "aider.conf.yml"
MODEL_SETTINGS = REPO_ROOT / "config" / "model-settings.yml"

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

STUDIO_DEFAULT = "ollama_chat/devstral:24b"
NEO_DEFAULT = "ollama_chat/qwen3:4b"
VLLM_DEFAULT = "devstral-24b"
T1_DEFAULT = "ollama_chat/qwen3-coder:7b-q4_K_M"
VLLM_T3_DEFAULT = "qwen3-coder-72b"

# Tier routing: (backend, tier) -> model name
_TIER_MODEL = {
    ("ollama", "t1"): T1_DEFAULT,
    ("ollama", "t2"): STUDIO_DEFAULT,  # fallback if vLLM unavailable
    ("ollama", "t3"): T1_DEFAULT,  # fallback if vLLM unavailable
    ("vllm", "t1"): T1_DEFAULT,  # fast path: use Ollama even when vLLM available
    ("vllm", "t2"): VLLM_DEFAULT,
    ("vllm", "t3"): VLLM_T3_DEFAULT,
}


def model_for(endpoint: Endpoint, tier: str | None = None) -> str:
    # Override model selection with PXX_MODEL environment variable.
    override = os.environ.get("PXX_MODEL")
    if override:
        return override

    if tier:
        # Tier 1 requires Ollama; reject vLLM endpoints
        if tier == "t1" and endpoint.backend == "vllm":
            raise RuntimeError(
                f"--tier t1 requires Ollama endpoint, but {endpoint.name} ({endpoint.backend}) "
                f"is available. Check Studio connectivity or use --tier t2/t3."
            )

        key = (endpoint.backend, tier)
        if key in _TIER_MODEL:
            return _TIER_MODEL[key]
        # Fallback for unknown tier
        return _TIER_MODEL.get((endpoint.backend, "t2"), STUDIO_DEFAULT)

    # No tier specified: use backend-based default
    if endpoint.backend == "vllm":
        return VLLM_DEFAULT
    return NEO_DEFAULT if endpoint.name == "neo" else STUDIO_DEFAULT


def _extract_tier(argv: list[str]) -> tuple[str | None, list[str]]:
    """Extract --tier value from argv, return (tier, remaining_argv).

    Handles: --tier t1, --tier=t2, or no tier specified.
    Raises ValueError if tier is invalid.
    """
    VALID_TIERS = {"t1", "t2", "t3"}
    tier = None
    remaining = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--tier" and i + 1 < len(argv):
            tier = argv[i + 1]
            i += 2
        elif arg.startswith("--tier="):
            tier = arg.split("=", 1)[1]
            i += 1
        else:
            remaining.append(arg)
            i += 1

    if tier is not None and tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier '{tier}'. Must be one of: {', '.join(sorted(VALID_TIERS))}")

    return tier, remaining


def _set_backend_env(endpoint: Endpoint) -> None:
    if endpoint.backend == "vllm":
        os.environ["OPENAI_API_BASE"] = endpoint.url + "/v1"
        os.environ.setdefault("OPENAI_API_KEY", "EMPTY")
    else:
        os.environ["OLLAMA_API_BASE"] = endpoint.url


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


def _print_skill_listing() -> None:
    """Print available agent skills to stdout."""
    registry = SkillRegistry()
    skills = registry.discover()
    if not skills:
        print("No skills found in pxx/commands/", file=sys.stderr)
        return

    print(registry.format_list())
    print()
    print("Use `/load <path>` in aider to load a skill:")
    for skill in skills:
        print(f"  /load {skill.path}")


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

    if "--list-skills" in sys.argv:
        _print_skill_listing()
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

    if "--review" in sys.argv:
        root = _git_repo_root()
        if root is None:
            print("pxx: --review requires a git repo.", file=sys.stderr)
            sys.exit(1)
        # Run review pass and compute verdict
        exit_code = review_gate.run_review_pass(root)
        if exit_code != 0:
            sys.exit(exit_code)
        # Collect findings and compute verdict
        findings = review_gate.collect_active_findings(root)
        verdict = review_gate.compute_verdict(findings)
        # Load workflow state and record verdict
        state = workflow.load_state(root) or workflow.WorkflowState()
        new_phase = "approved" if verdict == "APPROVE" else "rejected"
        new_state = workflow.transition(state, new_phase, review_verdict=verdict)
        workflow.save_state(new_state, root)
        print(f"pxx: review pass complete. verdict={verdict}.", file=sys.stderr)
        sys.exit(0)

    if "--check" in sys.argv:
        root = _git_repo_root()
        if root is None:
            print("pxx: --check requires a git repo.", file=sys.stderr)
            sys.exit(1)
        sys.exit(governance.run_governance_check(root))

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
    with_router = "--with-router" in sys.argv
    with_memory = "--with-memory" in sys.argv
    with_memory_injection = "--with-memory-injection" in sys.argv

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

    scope_args, argv_after_scope = extract_scope_args(argv_after_self_fix)
    try:
        tier, argv_after_tier = _extract_tier(argv_after_scope)
    except ValueError as e:
        print(f"pxx: {e}", file=sys.stderr)
        sys.exit(2)

    # Convert tier to preferred_backend for endpoint detection.
    # Tier 1 is Ollama-exclusive (faster startup); Tier 2/3 prefer vLLM if available.
    preferred_backend = None
    if tier:
        preferred_backend = "ollama" if tier == "t1" else "vllm"

    try:
        endpoint = detect_endpoint(preferred_backend=preferred_backend)
    except RuntimeError as e:
        print(f"pxx: {e}", file=sys.stderr)
        sys.exit(1)

    user_args = [
        a
        for a in argv_after_tier
        if a
        not in (
            "--edit",
            "--big",
            "--anywhere",
            "--self-improve",
            "--self-fix",
            "--check-sync",
            "--no-check-sync",
            "--tier",
        )
    ]
    # Also filter out tier values that follow --tier
    filtered_user_args = []
    skip_next = False
    for a in user_args:
        if skip_next:
            skip_next = False
            continue
        if a == "--tier":
            skip_next = True
        elif not a.startswith("--tier="):
            filtered_user_args.append(a)
    user_args = filtered_user_args
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

    _set_backend_env(endpoint)
    if big_mode:
        os.environ["PXX_ALLOW_BIG_DIFF"] = "1"
    if self_fix_mode:
        os.environ["PXX_AUTONOMOUS"] = "1"
        if "PXX_DIFF_CAP" not in os.environ:
            os.environ["PXX_DIFF_CAP"] = str(SELF_FIX_DIFF_CAP)

    try:
        model = model_for(endpoint, tier=tier)
    except RuntimeError as e:
        print(f"pxx: {e}", file=sys.stderr)
        sys.exit(1)

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

    tier_str = f"  tier={tier}" if tier else ""
    banner = (
        f"pxx: endpoint={endpoint.name} ({endpoint.url})  backend={endpoint.backend}"
        f"{tier_str}  model={model}  mode={mode_label}"
    )
    print(banner, file=sys.stderr)
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

    # Phase 5 Tier 2: Memory injection into system prompt
    root = _git_repo_root() if in_git_repo else None
    if with_memory_injection and with_memory:
        injector = MemoryInjector("http://127.0.0.1:3111")
        cwd = str(Path.cwd())
        args = injector.inject_into_aider_args(
            args, repo_root=root, cwd=cwd, tmp_dir=Path(tempfile.gettempdir())
        )
        print(
            "pxx: memory injection enabled — observations from previous sessions loaded",
            file=sys.stderr,
        )
    sha = _git_head_sha() if in_git_repo else None
    git_dirty: bool | None = _git_dirty() if in_git_repo else None
    # Privacy contract: this record must not contain sensitive env vars
    # (TOKEN, KEY, SECRET, PASSWORD). Callers should use audit.is_sensitive_env()
    # to validate when adding new fields.
    record: dict = {
        "session_class": _determine_session_class(
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

    # Build isolated environment for aider subprocess to prevent OPENAI_API_KEY
    # from leaking to git hooks or other subprocesses spawned by aider.
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = "EMPTY"

    # Phase 5 Tier 1: Optional service infrastructure
    router_manager: NineroterManager | None = None
    memory_manager: AgentmemoryManager | None = None

    try:
        # Start 9router if requested (routes requests at network layer)
        if with_router:
            router_manager = NineroterManager()
            router_manager.start()
            env["OPENAI_API_BASE"] = "http://127.0.0.1:20128/v1"
            router_status = "✓" if router_manager.get_status() else "?"
            print(f"pxx: 9router started (port 20128) {router_status}", file=sys.stderr)

        # Start agentmemory if requested (infrastructure only; runtime observation not yet implemented)
        if with_memory:
            memory_manager = AgentmemoryManager()
            memory_manager.start()
            print(
                "pxx: agentmemory started (port 3111, infrastructure mode)",
                file=sys.stderr,
            )

        # Launch aider with Popen (no stdout capture) to preserve terminal TTY
        # Aider inherits stdin/stdout/stderr, giving it full interactive terminal access.
        # Note: Runtime memory capture via observer is blocked pending solution to:
        # 1. TTY preservation (aider is a TUI and needs isatty()=true)
        # 2. Output format (aider's tool_calls are internal, not serialized to stdout)
        # See pxx/observer.py for details on what needs to be solved.
        aider_proc = subprocess.Popen(args, env=env)

        # Wait for aider to finish
        exit_code = aider_proc.wait()

        # Clean up services after aider exits
        if memory_manager:
            memory_manager.stop()

        # Print 9router statistics if available
        router_usage = None
        if router_manager:
            router_usage = router_manager.get_usage()
            router_manager.stop()
            if router_usage and "total_tokens" in router_usage:
                print(
                    f"pxx: 9router stats — tokens={router_usage.get('total_tokens', 0)}, "
                    f"cost=${router_usage.get('total_cost', 0):.4f}",
                    file=sys.stderr,
                )

        sys.exit(exit_code)

    except KeyboardInterrupt:
        # Clean up on user interrupt (Ctrl+C)
        if memory_manager:
            memory_manager.stop()
        if router_manager:
            router_manager.stop()
        sys.exit(130)  # Standard exit code for SIGINT

    except Exception as e:
        # Clean up on error
        if memory_manager:
            memory_manager.stop()
        if router_manager:
            router_manager.stop()
        print(f"pxx: service error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
