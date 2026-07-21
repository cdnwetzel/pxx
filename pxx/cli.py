"""pxx: orchestrator for the offline aider workflow.

Detects Ollama endpoints, selects models, applies safety tags, manages
path-prefix scoping, and dispatches to various dogfooding modes.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pxx import agent_manifest
from pxx import (
    _git,
    audit,
    doctor,
    drift,
    governance,
    review_gate,
    safety,
    self_modes,
    tool_capture,
    workflow,
)
from pxx import loop as loop_mod
from pxx import docs_sme
from pxx._core_files import is_core
from pxx.commands_index import CommandInfo, list_commands
from pxx.endpoints import Endpoint, detect_endpoint
from pxx.memory import _SERVICE_DIR as MEMORY_SERVICE_DIR
from pxx.memory import AgentmemoryManager
from pxx.observer import AiderMemoryObserver
from pxx.router import _SERVICE_DIR as ROUTER_SERVICE_DIR
from pxx.router import NineRouterManager
from pxx.scope import (
    extract_scope_args,
    format_for_env,
    is_path_trusted,
    load_trusted_paths,
    resolve_scopes,
    trusted_paths_config_path,
)

logger = logging.getLogger(__name__)

# Path constants — define first since compat aliases below reference REPO_ROOT.
PKG_DIR = Path(__file__).parent
REPO_ROOT = PKG_DIR.parent
SYSTEM_PROMPT = PKG_DIR / "prompts" / "system.md"
SELF_IMPROVE_PROMPT = PKG_DIR / "prompts" / "self-improve.md"
AIDER_CONF = REPO_ROOT / "config" / "aider.conf.yml"
MODEL_SETTINGS = REPO_ROOT / "config" / "model-settings.yml"
MODEL_METADATA = REPO_ROOT / "config" / "model-metadata.json"

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

# Default models. All are env-overridable: PXX_MODEL forces one model for the
# session regardless of endpoint; PXX_OLLAMA_MODEL/PXX_VLLM_MODEL adjust the
# per-backend defaults. vLLM model ids are server-specific, so anyone running
# their own vLLM should set PXX_VLLM_MODEL (or PXX_MODEL) to match it; litellm
# needs the openai/ prefix to route an OpenAI-compatible endpoint via
# OPENAI_API_BASE.
STUDIO_DEFAULT = os.environ.get("PXX_OLLAMA_MODEL", "ollama_chat/devstral:24b")
# PXX_VLLM_MODEL may be a comma list pairing entries with PXX_VLLM_URL; the
# first entry doubles as the fallback for endpoints without a paired model.
VLLM_DEFAULT = (
    os.environ.get("PXX_VLLM_MODEL", "openai/qwen2.5-coder-14b").split(",")[0].strip()
)
T1_DEFAULT = "ollama_chat/qwen2.5-coder:7b"
VLLM_T3_DEFAULT = VLLM_DEFAULT

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
                f"--tier t1 requires an Ollama endpoint, but "
                f"{endpoint.name} ({endpoint.backend}) is available. "
                f"Check that your Ollama is reachable, or use --tier t2/t3."
            )

        # A vLLM endpoint that declares its served model wins over the tier
        # table for vLLM tiers — the table's VLLM_DEFAULT only fits the
        # first-listed endpoint.
        if endpoint.backend == "vllm" and endpoint.model:
            return endpoint.model

        key = (endpoint.backend, tier)
        if key in _TIER_MODEL:
            return _TIER_MODEL[key]
        # Fallback for unknown tier
        return _TIER_MODEL.get((endpoint.backend, "t2"), STUDIO_DEFAULT)

    # No tier specified: use backend-based default
    if endpoint.backend == "vllm":
        return endpoint.model or VLLM_DEFAULT
    return STUDIO_DEFAULT


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
        raise ValueError(
            f"Invalid tier '{tier}'. Must be one of: {', '.join(sorted(VALID_TIERS))}"
        )

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
        "pxx: aider not found. It installs with pxx — try "
        "`pip install --force-reinstall pxx-orchestrator` (or `uv sync` in a repo checkout).",
        file=sys.stderr,
    )
    sys.exit(1)


def _headless_consent_args(isatty: bool, user_args: list[str]) -> list[str]:
    """Args to append when stdin is not a TTY and no consent flag was given.

    aider's interactive confirms crash on a non-TTY stdin (prompt_toolkit
    raises OSError registering the fd), so headless one-shots — --loop
    rounds, cron jobs, CI smoke tests — need --yes. pxx's own self-modes
    are the primary headless callers and always want --yes semantics, so
    inject rather than hard-fail; the caller-visible stderr notice is
    printed at the call site.
    """
    consent = {"--yes", "--yes-always", "--no"}
    if isatty:
        return []
    if any(a in consent or a.split("=", 1)[0] in consent for a in user_args):
        return []
    return ["--yes"]


def _build_aider_args(
    aider_bin: str,
    model: str,
    user_args: list[str],
    in_git_repo: bool,
    edit_mode: bool,
    extra_reads: list[Path] | None = None,
) -> list[str]:
    """Construct the argv to exec into aider with."""
    has_chat_mode = any(
        a == "--chat-mode" or a.startswith("--chat-mode=") for a in user_args
    )
    chat_mode_args: list[str] = []
    if not has_chat_mode and not edit_mode:
        # Only inject in ask mode. Edit mode lets aider use its default +
        # config's edit-format=diff. Note: --self-improve must NOT reach here
        # with edit_mode=True — its suggest-only contract depends on aider
        # running in ask mode, not just on the prompt text (see main()).
        chat_mode_args = ["--chat-mode", "ask"]

    extra_read_args: list[str] = []
    for p in extra_reads or []:
        extra_read_args.extend(["--read", str(p)])

    # Pass aider's model config files only when present, so a checkout missing
    # one can't break the launch. model-settings.yml carries per-model edit
    # format + Ollama num_ctx; model-metadata.json describes models litellm has
    # no metadata for (e.g. a local vLLM's context window).
    settings_args = (
        ["--model-settings-file", str(MODEL_SETTINGS)]
        if MODEL_SETTINGS.exists()
        else []
    )
    metadata_args = (
        ["--model-metadata-file", str(MODEL_METADATA)]
        if MODEL_METADATA.exists()
        else []
    )

    # Pass pxx's bundled aider config only when present. It ships with a repo
    # checkout but not a `pip install` (it's outside the package), so a pip
    # install falls back to aider's own config / ~/.aider.conf.yml.
    config_args = ["--config", str(AIDER_CONF)] if AIDER_CONF.exists() else []

    args = [
        aider_bin,
        "--model",
        model,
        # .gitignore hygiene is a repo decision, not a per-session one — an
        # ask-mode session must never mutate the working tree (user args come
        # later in argv, so a caller can still opt back in).
        "--no-gitignore",
        "--read",
        str(SYSTEM_PROMPT),
        *extra_read_args,
        *config_args,
        *settings_args,
        *metadata_args,
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
        (
            f'You: "Try `/load {example.path}` — it is tuned for exactly '
            f"this kind of task. Share the function if you want me to apply "
            f'hints directly instead."'
        ),
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

    if "--doctor" in sys.argv:
        remote_stats = doctor.Doctor().print_report()
        sys.exit(0 if remote_stats.in_sync else 1)

    if "--upgrade" in sys.argv or "--update" in sys.argv:
        from pxx import upgrade

        sys.exit(upgrade.upgrade_main())

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
        # Collect findings and compute verdict. No review artifacts at all means
        # NO_REVIEW (absence of information), which fails closed into rejected —
        # reviewer silence must never launder into approval.
        if not review_gate.has_review_evidence(root):
            verdict = "NO_REVIEW"
        else:
            findings = review_gate.collect_active_findings(root)
            verdict = review_gate.compute_verdict(findings)
        # Load workflow state and record verdict
        state = workflow.load_state(root) or workflow.WorkflowState()
        new_phase = "approved" if verdict == "APPROVE" else "rejected"
        new_state = workflow.transition(state, new_phase, review_verdict=verdict)
        workflow.save_state(new_state, root)
        print(f"pxx: review pass complete. verdict={verdict}.", file=sys.stderr)
        if "--heal" in sys.argv:
            # --heal is exactly one REVISE round (#009). NO_REVIEW and
            # all-UNPARSEABLE refuse inside heal_once — their remedy is the
            # review itself, not an edit round.
            heal_scopes, _ = extract_scope_args(sys.argv[1:])
            if not heal_scopes:
                print(
                    "pxx: --heal needs --scope <path> for its edit round.",
                    file=sys.stderr,
                )
                sys.exit(2)
            sys.exit(loop_mod.heal_once(root, heal_scopes[0]))
        sys.exit(0)

    if "--check" in sys.argv:
        root = _git_repo_root()
        if root is None:
            print("pxx: --check requires a git repo.", file=sys.stderr)
            sys.exit(1)
        sys.exit(
            governance.run_governance_check(
                root,
                full_content="--all-files" in sys.argv,
                shipped_content="--shipped" in sys.argv,
                allow_empty_denylist="--allow-empty-denylist" in sys.argv,
            )
        )

    if "--compare" in sys.argv:
        # Promotion policy (#017): exact case-by-case verdict over two
        # persisted scorecards. Exit 0 only when the candidate is eligible.
        from pxx import promotion

        idx = sys.argv.index("--compare")
        if idx + 2 >= len(sys.argv):
            print(
                "pxx: usage: pxx --compare <baseline.json> <candidate.json>",
                file=sys.stderr,
            )
            sys.exit(2)
        base = promotion.load_scorecard(Path(sys.argv[idx + 1]))
        cand = promotion.load_scorecard(Path(sys.argv[idx + 2]))
        decision = promotion.compare(base, cand)
        for reason in decision.reasons:
            print(f"  {reason}")
        print(f"promotion: {'ELIGIBLE' if decision.eligible else 'NOT ELIGIBLE'}")
        sys.exit(0 if decision.eligible else 1)

    if "--calibrate" in sys.argv:
        # Reviewer calibration (#014.3): the production review path scored
        # against ground-truth diffs. Threshold breach exits non-zero.
        from pxx import calibration

        # Fail closed + LOUD on an empty corpus, same as --eval: the cases
        # ship only with a repo checkout, so a pip-installed copy has none.
        # (Without this it exits 1 incidentally via 0-recall — correct
        # outcome, misleading message.)
        if not calibration.load_calibration_cases():
            print(
                f"pxx calibrate: NO CASES FOUND in {calibration.CALIBRATION_DIR} "
                "— the calibration corpus ships only with a repo checkout. "
                "Failing closed.",
                file=sys.stderr,
            )
            sys.exit(2)
        report = calibration.run_calibration()
        for v in report.verdicts:
            mark = "ok " if v.correct else "MISS" if v.kind == "defect" else "FP  "
            if not v.available:
                mark = "DOWN"
            print(
                f"{mark} {v.kind:<7} {v.case_id:<32} findings={v.findings} "
                f"{'fmt-ok' if v.format_compliant else 'fmt-BAD'}"
            )
        print(
            f"reviewer={report.reviewer_model}  recall={report.recall:.2f} "
            f"fp_rate={report.false_positive_rate:.2f} "
            f"format={report.format_compliance:.2f} "
            f"availability={report.availability:.2f}"
        )
        out = calibration.save_report(report)
        print(f"report: {out}")
        print(
            "thresholds: "
            + ("PASS" if report.within_thresholds else "BREACHED (fail closed)")
        )
        sys.exit(0 if report.within_thresholds else 1)

    if "--eval-live" in sys.argv:
        # Live-agent arm (#013): the real loop inside a fixture worktree,
        # judged by the same hidden checks as the scripted arms.
        from pxx import evaluation

        idx = sys.argv.index("--eval-live")
        if idx + 1 >= len(sys.argv):
            print("pxx: usage: pxx --eval-live <case-id> [--keep]", file=sys.stderr)
            sys.exit(2)
        case = evaluation.find_case(sys.argv[idx + 1])
        if case is None:
            print(f"pxx: no eval case {sys.argv[idx + 1]!r}", file=sys.stderr)
            sys.exit(2)
        result, live_run_id = evaluation.run_live_arm(case, keep="--keep" in sys.argv)
        print(f"case: {case.id}  run_id: {live_run_id}")
        for f in result.failures:
            print(f"  {f.check}: {f.detail}")
        print(f"live arm: {'PASS' if result.ok else 'FAIL'}")
        sys.exit(0 if result.ok else 1)

    if "--eval" in sys.argv:
        # Eval-lab self-check (#013): honest arms must pass, cheat arms must
        # be CAUGHT by the hidden checks. A suite whose cheats slip through
        # is a broken laboratory — exit 1.
        from pxx import evaluation

        idx = sys.argv.index("--eval")
        which = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "all"
        tiers = evaluation.TIERS if which == "all" else (which,)
        bad = 0
        total_cases = 0
        for tier in tiers:
            results = evaluation.self_check_suite(tier)
            if not results:
                print(f"pxx eval: no cases in tier {tier!r}", file=sys.stderr)
                continue
            total_cases += len(results)
            for r in results:
                mark = "ok " if r.ok else "FAIL"
                extra = ""
                if r.arm == "cheat" and r.ok:
                    extra = f"  caught: {r.failures[0].check}"
                elif not r.ok:
                    extra = "  " + "; ".join(
                        f"{f.check}: {f.detail}" for f in r.failures
                    )
                print(f"{mark} {tier:<11} {r.case_id:<26} {r.arm:<6}{extra}")
                bad += 0 if r.ok else 1
        # Fail closed on an empty corpus: "no cases found" is NOT "all passed".
        # evals/ ships only with a repo checkout, so a pip-installed copy has
        # zero cases — that must exit non-zero, not a silent green gate.
        if total_cases == 0:
            print(
                f"pxx eval: NO CASES FOUND in {evaluation.EVALS_DIR} — the eval "
                "corpus ships only with a repo checkout. Failing closed.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"pxx eval: {'PASS' if bad == 0 else f'{bad} FAILURE(S)'}")
        sys.exit(0 if bad == 0 else 1)

    if "--runs" in sys.argv:
        # Outcome inspection (#012): recent loop runs, newest first, projected
        # from the audit stream — terminal codes, never message parsing.
        from pxx import outcomes

        rows = outcomes.recent_outcomes(limit=20)
        if not rows:
            print("pxx: no recorded loop runs (terminal records start 2026-07-16).")
            sys.exit(0)
        for o in rows:
            print(
                f"{o.run_id}  {o.terminal_code:<22} rounds={o.rounds} "
                f"diff={o.diff_lines:<5} {o.agent_version_id or '-'}"
            )
        sys.exit(0)

    if "--analyze" in sys.argv:
        # Experience mining (#015): deterministic weakness clustering over the
        # run-outcome stream. Surfaces what to look at; proposes nothing.
        from pxx import improvement

        obs = improvement.analyze_recent()
        if not obs:
            print("pxx: no patterns (need more recorded runs).")
            sys.exit(0)
        for o in obs:
            print(f"[{o.kind}] {o.summary}")
            print(
                f"    evidence: {len(o.evidence)} run(s), strength={o.evidence_strength}"
            )
        sys.exit(0)

    if "--evaluate-candidate" in sys.argv:
        # Candidate evaluation (#016→17): both-arms live eval + compare for a
        # persisted candidate → a promotion verdict. Human-gated; never applies.
        from pxx import candidate_eval, candidates

        idx = sys.argv.index("--evaluate-candidate")
        if idx + 1 >= len(sys.argv):
            print(
                "pxx: usage: pxx --evaluate-candidate <candidate-id>", file=sys.stderr
            )
            sys.exit(2)
        root = _git_repo_root() or Path.cwd()
        cand = candidates.load_candidate(root, sys.argv[idx + 1])
        if cand is None:
            print(f"pxx: no candidate {sys.argv[idx + 1]!r}", file=sys.stderr)
            sys.exit(2)
        print(
            f"pxx: evaluating {cand.candidate_id} ({cand.field}={cand.value}) "
            "— baseline vs candidate over the live corpus, this drives real "
            "loops and will take a while..."
        )
        record = candidate_eval.evaluate_candidate(cand, candidate_eval.live_runner())
        if record.get("error"):
            print(f"pxx: {record['error']}", file=sys.stderr)
            for r in record.get("reasons", []):
                print(f"  - {r}", file=sys.stderr)
            sys.exit(1)
        print(f"  gained: {record['gained'] or '(none)'}")
        print(f"  lost:   {record['lost'] or '(none)'}")
        for reason in record["policy_reasons"]:
            print(f"  {reason}")
        print(
            f"promotion: {'ELIGIBLE' if record['policy_eligible'] else 'NOT ELIGIBLE'}"
            " — human-gated; nothing applied."
        )
        sys.exit(0 if record["policy_eligible"] else 1)

    if "--propose" in sys.argv:
        # Candidate proposal (#016): declarative delta on an ALLOWLISTED
        # behavior field, integrity-validated, persisted, NEVER auto-applied.
        # usage: pxx --propose <field> <value> --because <observation-id>
        from pxx import candidates

        # Auto mode: mine weaknesses (Phase 15) → propose validated candidates
        # (Phase 16) in one step. The chain, self-starting.
        if "--auto" in sys.argv:
            from pxx import improvement

            obs = improvement.analyze_recent()
            cands = improvement.propose_from_observations(
                obs, current_review_mode=review_gate.review_mode()
            )
            if not cands:
                print(
                    "pxx propose --auto: no candidate proposed "
                    "(no rule matched the current weaknesses)."
                )
                sys.exit(0)
            root = _git_repo_root() or Path.cwd()
            for c in cands:
                candidates.save_candidate(root, c)
                print(f"pxx propose --auto: {c.candidate_id} — {c.field}={c.value}")
                print(f"    from: {c.from_observation}")
                print(f"    why:  {c.rationale}")
            print(
                "  next: evaluate baseline vs candidate, then pxx --compare "
                "(human-gated; nothing auto-applies)"
            )
            sys.exit(0)

        idx = sys.argv.index("--propose")
        rest = sys.argv[idx + 1 :]
        if len(rest) < 2:
            print(
                "pxx: usage: pxx --propose <field> <value> "
                "[--because <obs>] [--baseline <val>] [--why <text>]\n"
                "       pxx --propose --auto   (mine weaknesses → validated candidates)",
                file=sys.stderr,
            )
            sys.exit(2)
        field, value = rest[0], rest[1]

        def _opt(flag: str, default: str) -> str:
            return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

        cand = candidates.Candidate(
            candidate_id=f"cand-{audit.make_session_id()}",
            field=field,
            value=value,
            baseline_value=_opt("--baseline", None) or None,
            rationale=_opt("--why", "(none given)"),
            from_observation=_opt("--because", ""),
        )
        result = candidates.validate_candidate(cand)
        if not result.ok:
            print("pxx propose: REJECTED (fail closed)", file=sys.stderr)
            for r in result.reasons:
                print(f"  - {r}", file=sys.stderr)
            sys.exit(1)
        root = _git_repo_root() or Path.cwd()
        d = candidates.save_candidate(root, cand)
        print(f"pxx propose: candidate {cand.candidate_id} VALIDATED")
        print(
            f"  {cand.field} = {cand.value}  (overlay: {candidates.env_overlay(cand)})"
        )
        print(f"  saved: {d}")
        print(
            "  next: evaluate baseline vs candidate, then pxx --compare (human-gated)"
        )
        sys.exit(0)

    if "--verify" in sys.argv:
        # VerificationPacket for one run (#012 consumption): the evidence a
        # reviewer reads instead of trusting a claim of completion. With no
        # run-id, the most recent run.
        from pxx import outcomes

        idx = sys.argv.index("--verify")
        run_arg = (
            sys.argv[idx + 1]
            if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-")
            else None
        )
        if run_arg:
            outcome = outcomes.outcome_for_run(run_arg)
        else:
            recent = outcomes.recent_outcomes(limit=1)
            outcome = recent[0] if recent else None
        if outcome is None:
            print(f"pxx: no run found for {run_arg or 'latest'}", file=sys.stderr)
            sys.exit(1)
        print(outcomes.format_packet(outcomes.verification_packet(outcome)))
        sys.exit(0)

    if "--manifest" in sys.argv:
        # Behavior identity inspection (#011): print the current AgentManifest
        # and its agent_version_id as JSON, then exit.
        try:
            mf_endpoint = detect_endpoint()
            mf_model = model_for(mf_endpoint)
        except RuntimeError as e:
            print(f"pxx: --manifest: {e}", file=sys.stderr)
            sys.exit(1)
        mf = agent_manifest.current_manifest(
            editor_backend=mf_endpoint.backend,
            editor_model=mf_model,
            max_rounds=loop_mod.DEFAULT_MAX_ROUNDS,
            max_seconds=loop_mod.DEFAULT_MAX_SECONDS,
            diff_budget=loop_mod.DEFAULT_DIFF_BUDGET_LINES,
        )
        print(
            json.dumps(
                {
                    "agent_version_id": agent_manifest.agent_version_id(mf),
                    "manifest": dataclasses.asdict(mf),
                },
                indent=2,
            )
        )
        sys.exit(0)

    if "--loop" in sys.argv:
        # EXPERIMENTAL (#009). Most conservative posture: pxx repo only (the
        # self-fix rounds chdir there), --scope required, clean tree required,
        # pxx git hooks required, bounded rounds, never pushes.
        root = _git_repo_root()
        if root is None or root.resolve() != REPO_ROOT.resolve():
            print(
                "pxx: --loop is experimental and currently runs only inside "
                "the pxx repo (self-fix rounds operate there).",
                file=sys.stderr,
            )
            sys.exit(1)
        task, argv_rest = _extract_self_fix_task(
            ["--self-fix" if a == "--loop" else a for a in sys.argv[1:]]
        )
        if not task:
            print('pxx: usage: pxx --loop "<task>" --scope <path>', file=sys.stderr)
            sys.exit(2)
        loop_scopes, _ = extract_scope_args(argv_rest)
        if not loop_scopes:
            print(
                "pxx: --loop refuses to run without --scope — one scoped "
                "path per loop is the unit of iteration.",
                file=sys.stderr,
            )
            sys.exit(2)
        if len(loop_scopes) > 1:
            print(
                f"pxx: --loop uses one scope per loop; ignoring extra scopes: "
                f"{', '.join(loop_scopes[1:])}",
                file=sys.stderr,
            )
        if _git_dirty():
            print(
                "pxx: --loop refuses to start on a dirty tree — commit or "
                "stash first (rounds must be cleanly attributable).",
                file=sys.stderr,
            )
            sys.exit(1)
        max_rounds = loop_mod.DEFAULT_MAX_ROUNDS
        if "--max-rounds" in sys.argv:
            idx = sys.argv.index("--max-rounds")
            if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
                max_rounds = int(sys.argv[idx + 1])
        print(
            "pxx: --loop is EXPERIMENTAL — bounded autonomous rounds; never "
            "pushes; stops on APPROVE/REJECT/no-progress/budget.",
            file=sys.stderr,
        )
        # Behavior identity (#011 minimum): one run_id ties the loop session,
        # every round record, each child self-fix session (via PXX_RUN_ID),
        # and the cross-session capture together; agent_version_id names the
        # behavior configuration that produced them. Best-effort: identity
        # capture must never gate or break a run — the loop's own preflight
        # does the real gating.
        run_id = audit.make_session_id()
        os.environ["PXX_RUN_ID"] = run_id
        version_id: str | None = None
        manifest_dict: dict | None = None
        try:
            loop_endpoint = detect_endpoint()
            manifest = agent_manifest.current_manifest(
                editor_backend=loop_endpoint.backend,
                editor_model=model_for(loop_endpoint),
                max_rounds=max_rounds,
                max_seconds=loop_mod.DEFAULT_MAX_SECONDS,
                diff_budget=loop_mod.DEFAULT_DIFF_BUDGET_LINES,
            )
            version_id = agent_manifest.agent_version_id(manifest)
            manifest_dict = dataclasses.asdict(manifest)
        except Exception:
            pass
        _try_write_session_start(
            {
                "session_class": "loop",
                "cwd": str(Path.cwd()),
                "task": task,
                "run_id": run_id,
                "agent_version_id": version_id,
                "manifest": manifest_dict,
            }
        )
        sys.exit(
            loop_mod.run_loop(
                root,
                task,
                loop_scopes[0],
                max_rounds=max_rounds,
                run_id=run_id,
                agent_version=version_id,
            )
        )

    _self_sanity_check()
    _emit_core_restart_banner()

    with contextlib.suppress(Exception):
        audit.prune_old_logs()

    edit_mode = (
        "--edit" in sys.argv or "--self-fix" in sys.argv or "--self-improve" in sys.argv
    )
    big_mode = "--big" in sys.argv
    dry_run = "--dry-run" in sys.argv
    anywhere_mode = "--anywhere" in sys.argv
    self_improve_mode = "--self-improve" in sys.argv
    self_fix_mode = "--self-fix" in sys.argv
    with_router = "--with-router" in sys.argv
    with_memory = "--with-memory" in sys.argv
    with_docs = "--with-docs" in sys.argv

    # #006 M2: optional pre-edit drift check.
    # Off by default; PXX_AUTOCHECK_DRIFT=1 to opt-in.
    autocheck = os.environ.get("PXX_AUTOCHECK_DRIFT") == "1"
    skip_check = "--no-check-sync" in sys.argv
    if edit_mode and autocheck and not skip_check:
        res = drift.check_sync()
        if not res.is_synced:
            drift.print_report(res)

    if self_fix_mode and self_improve_mode:
        print(
            "pxx: --self-fix and --self-improve are mutually exclusive.",
            file=sys.stderr,
        )
        sys.exit(2)
    if with_docs and with_router:
        print(
            "pxx: --with-docs and --with-router both rewrite the aider endpoint "
            "and can't be combined. Pick one.",
            file=sys.stderr,
        )
        sys.exit(2)
    if self_improve_mode and "--edit" in sys.argv:
        print(
            (
                "pxx: --self-improve is ask-only — remove --edit "
                "(Tier 2 is suggest-only by design)."
            ),
            file=sys.stderr,
        )
        sys.exit(2)

    self_fix_task: str | None = None
    argv_after_self_fix = sys.argv[1:]
    if self_fix_mode:
        self_fix_task, argv_after_self_fix = _extract_self_fix_task(argv_after_self_fix)

    if self_improve_mode or self_fix_mode:
        # #001 default: self-modes operate on the pxx repo itself. The
        # evaluation harness (#013) retargets loop rounds at a fixture
        # worktree via PXX_SELF_FIX_ROOT — the trusted-paths gate below
        # applies to wherever we land, so the sovereignty boundary holds.
        os.chdir(os.environ.get("PXX_SELF_FIX_ROOT") or REPO_ROOT)

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
            "--with-router",
            "--with-memory",
            "--with-docs",
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
        has_message = any(
            a == "--message" or a.startswith("--message=") for a in user_args
        )
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
            f"pxx: safety tag {safety_tag} — undo session with: "
            f"git reset --hard {safety_tag}",
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
            "pxx: --dry-run set — aider will describe changes but not "
            "write or commit them.",
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

    headless_args = _headless_consent_args(sys.stdin.isatty(), user_args)
    if headless_args:
        print("pxx: non-TTY stdin — passing --yes to aider", file=sys.stderr)
        user_args = [*user_args, *headless_args]

    args = _build_aider_args(
        aider_bin,
        model,
        user_args,
        in_git_repo,
        # --self-improve sets edit_mode (safety tag, trusted-path gate) but its
        # suggest-only contract requires aider itself to run in ask mode — so
        # for chat-mode purposes it is NOT an edit session.
        edit_mode and not self_improve_mode,
        extra_reads=extra_reads,
    )

    root = _git_repo_root() if in_git_repo else None
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
    # Behavior identity (#011 minimum): every session record names the exact
    # behavior configuration that ran it. Loop children inherit PXX_RUN_ID so
    # their sessions group under the parent run. Best-effort — identity
    # capture must never break a session.
    with contextlib.suppress(Exception):
        session_manifest = agent_manifest.current_manifest(
            editor_backend=endpoint.backend,
            editor_model=model,
            max_rounds=loop_mod.DEFAULT_MAX_ROUNDS,
            max_seconds=loop_mod.DEFAULT_MAX_SECONDS,
            diff_budget=loop_mod.DEFAULT_DIFF_BUDGET_LINES,
        )
        record["agent_version_id"] = agent_manifest.agent_version_id(session_manifest)
        record["manifest"] = dataclasses.asdict(session_manifest)
    record["run_id"] = os.environ.get("PXX_RUN_ID")
    _try_write_session_start(record)

    # Build isolated environment for aider to prevent OPENAI_API_KEY from
    # leaking to git hooks or other subprocesses spawned by aider.
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = "EMPTY"

    # Route aider through the docs-rag-sme proxy if requested (#009). The SME is
    # an external service with no lifecycle for pxx to manage, so it needs only
    # an env tweak before the handoff — not supervisor mode.
    if with_docs:
        sme = docs_sme.sme_base_url()
        if not docs_sme.probe_sme(sme):
            print(
                f"pxx: --with-docs: docs-rag-sme not reachable at {sme}. "
                f"Start it (uv run docs-sme) or set PXX_DOCS_SME_URL.",
                file=sys.stderr,
            )
            sys.exit(1)
        pyver = docs_sme.resolve_python_version(Path.cwd())
        notified = docs_sme.notify_version(sme, pyver)
        env["OPENAI_API_BASE"] = f"{sme}/v1"
        note = (
            "" if notified else " (version notify failed; retrieving across versions)"
        )
        print(
            f"pxx: docs-RAG SME at {sme} — aider routed through it; "
            f"python={pyver or 'any'}{note}",
            file=sys.stderr,
        )
        if endpoint.backend != "vllm":
            print(
                "pxx: note — SME forwards to its own upstream "
                "(DOCS_SME_UPSTREAM, default the vLLM :8003). Ensure it "
                f"serves the selected model ({model}).",
                file=sys.stderr,
            )

    # The pxx contract: by default, replace this process with aider and get out
    # of the way. Supervisor mode below is entered only when we must stay alive
    # to manage 9router / agentmemory and the observer thread.
    if not (with_router or with_memory):
        os.execve(aider_bin, args, env)
        return  # unreachable: execve replaces the process image

    # Phase 5 Tier 1: supervisor mode for 9router + agentmemory
    router_manager: NineRouterManager | None = None
    memory_manager: AgentmemoryManager | None = None
    observer: AiderMemoryObserver | None = None

    try:
        # Start 9router if requested
        if with_router:
            if (
                not shutil.which("nine-router")
                and not Path(ROUTER_SERVICE_DIR).exists()
            ):
                print(
                    "pxx: --with-router needs the 9router service, which ships with "
                    "a repo checkout (services/9router), not the pip package. Clone "
                    "the repo or install the `nine-router` console script.",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                router_manager = NineRouterManager()
                router_manager._start_with_retries(max_attempts=3)
                env["OPENAI_API_BASE"] = "http://127.0.0.1:20128/v1"
                router_status = "✓" if router_manager.get_status() else "?"
                print(
                    f"pxx: 9router started (port 20128) {router_status}",
                    file=sys.stderr,
                )
            except RuntimeError as e:
                print(
                    f"pxx: --with-router could not start 9router: {e}\n"
                    "  The router service ships with a repo checkout "
                    "(services/9router), not the pip package — clone the repo or "
                    "install the `nine-router` console script to use --with-router.",
                    file=sys.stderr,
                )
                sys.exit(1)

        # Start agentmemory if requested
        if with_memory:
            if (
                not shutil.which("agentmemory")
                and not Path(MEMORY_SERVICE_DIR).exists()
            ):
                print(
                    "pxx: --with-memory needs the agentmemory service, which ships "
                    "with a repo checkout (services/agentmemory), not the pip "
                    "package. Clone the repo or install the `agentmemory` console "
                    "script.",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                memory_manager = AgentmemoryManager()
                memory_manager.start()
                print("pxx: agentmemory started (port 3111)", file=sys.stderr)
            except Exception as e:
                print(
                    f"pxx: --with-memory could not start agentmemory: {e}\n"
                    "  The memory service ships with a repo checkout "
                    "(services/agentmemory), not the pip package — clone the repo "
                    "or install the `agentmemory` console script to use --with-memory.",
                    file=sys.stderr,
                )
                sys.exit(1)

        # Launch aider as a subprocess so we can supervise the services.
        aider_proc = subprocess.Popen(args, env=env)

        # Start observer thread if memory is active
        if with_memory and memory_manager:
            observer = AiderMemoryObserver(aider_proc, "http://127.0.0.1:3111")
            observer.start()

        # Wait for aider to finish
        exit_code = aider_proc.wait()

        # Capture tool calls from aider session (Phase 6.4)
        if with_memory and exit_code == 0 and root:
            try:
                project_scope = scope_prefixes[0] if scope_prefixes else "default"
                captured = tool_capture.capture_session_tools(
                    sha, root, project=project_scope
                )
                if captured > 0:
                    print(
                        f"pxx: captured {captured} observations from aider session",
                        file=sys.stderr,
                    )
            except Exception as e:
                logger.warning(f"Failed to capture tool calls: {e}")

        # Aider finished — clean up subprocesses gracefully
        if memory_manager:
            memory_manager.stop()
        if router_manager:
            usage = router_manager.get_usage()
            router_manager.stop()
            if usage and "total_tokens" in usage:
                print(
                    f"pxx: 9router stats — tokens={usage.get('total_tokens', 0)}, "
                    f"cost=${usage.get('total_cost', 0):.4f}",
                    file=sys.stderr,
                )

        sys.exit(exit_code)

    except KeyboardInterrupt:
        # Clean up on user interrupt
        if observer:
            observer.thread = None
        if memory_manager:
            memory_manager.stop()
        if router_manager:
            router_manager.stop()
        sys.exit(130)  # Standard exit code for SIGINT

    except Exception as e:
        # Clean up on error
        if observer:
            observer.thread = None
        if memory_manager:
            memory_manager.stop()
        if router_manager:
            router_manager.stop()
        print(f"pxx: supervisor error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
