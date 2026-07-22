"""pxx command-line interface.

argparse-based, with a 1.x compatibility shim: bare ``pxx`` is ``pxx ask``,
``--edit`` selects edit mode, ``--with-memory`` is a no-op (memory is
default-on now), ``--doctor``/``--self-test``/``--self-lint`` map to new
subcommands, and genuinely unknown flags are forwarded to aider (only when
the aider backend is active) with a deprecation warning.

Exit codes: 0 = COMPLETED, 2 = gate/budget stop (a gate evaluated evidence
and stopped something), 64 = usage error (bad invocation: unknown command,
empty task, invalid candidate, missing approver/evidence, empty corpus),
130 = interrupted, 1 = anything else. CI keying on "2 = a gate fired" can
rely on usage problems never reading as 2.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import load_settings
from .errors import PxxError
from .events import AuditLog, Event
from .outcome import RunOutcome, TerminalCode
from .safety import PermissionMode
from .session import Session

#: sysexits.h-style usage error: the invocation itself is wrong (distinct
#: from 2, which means a gate evaluated evidence and stopped the run).
EXIT_USAGE = 64

SUBCOMMANDS = (
    "ask",
    "edit",
    "plan",
    "run",
    "loop",
    "chat",
    "memory",
    "mcp",
    "serve",
    "doctor",
    "upgrade",
    "audit",
    "runs",
    "agents",
    "verify",
    "metrics",
    "eval",
    "calibrate",
    "improve",
    "propose",
    "compare",
    "agent",
    "promote",
    "check",
    "goal",
    "review",
    "workflow",
    "context",
    "docs",
)

_MODE_BY_COMMAND = {
    "ask": PermissionMode.ASK,
    "edit": PermissionMode.EDIT,
    "plan": PermissionMode.PLAN,
    "run": PermissionMode.AUTO,
    "loop": PermissionMode.AUTO,
    "chat": PermissionMode.EDIT,
}

_GATE_CODES = {
    TerminalCode.BUDGET_EXCEEDED,
    TerminalCode.ROUND_CAP,
    TerminalCode.DIFF_CAP,
    TerminalCode.OUT_OF_SCOPE,
    TerminalCode.HOOK_DENIED,
    TerminalCode.HOOKS_MISSING,
    TerminalCode.NO_TEST_PROGRESS,
    TerminalCode.CLARIFICATION_REQUIRED,
    TerminalCode.TEST_RUN_FAILED,
    TerminalCode.TEST_REGRESSION,
    TerminalCode.LINT_BLOCKED,
    TerminalCode.REVIEW_REJECTED,
    TerminalCode.REVIEW_UNAVAILABLE,
    TerminalCode.REVIEW_EMPTY,
    TerminalCode.REVIEW_UNPARSEABLE,
    TerminalCode.LOOP_DETECTED,
    TerminalCode.MERGE_CONFLICT,
}

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

_SELF_TEST_TASK = "Run the project test suite, diagnose any failures, and fix them."
_SELF_LINT_TASK = "Run the project linter and fix all findings."


def exit_code_for(outcome: RunOutcome) -> int:
    """Map a terminal code to a process exit code."""
    if outcome.code is TerminalCode.COMPLETED:
        return 0
    if outcome.code in _GATE_CODES:
        return 2
    if outcome.code is TerminalCode.INTERRUPTED:
        return 130
    return 1


# ---------------------------------------------------------------------------
# 1.x compatibility shim


def _compat_rewrite(argv: list[str]) -> list[str]:
    """Rewrite legacy 1.x invocations into 2.0 subcommand form.

    Runs before argparse so legacy flags never hit argparse errors.
    A first token that is neither a known subcommand nor a flag nor an
    existing file is almost always a typo'd verb: fail loud (exit 64)
    instead of silently routing it to ``ask`` (which would hit a model).
    """
    if argv and (argv[0] in SUBCOMMANDS or argv[0] in ("--version", "--help", "-h")):
        return argv
    if argv and not argv[0].startswith("-") and not Path(argv[0]).exists():
        import difflib

        near = difflib.get_close_matches(argv[0], SUBCOMMANDS, n=1)
        hint = f" (did you mean '{near[0]}'?)" if near else ""
        print(f"pxx: usage: unknown command: {argv[0]!r}{hint}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    out: list[str] = []
    mode = "ask"
    for token in argv:
        if token == "--edit":
            mode = "edit"
        elif token == "--with-memory":
            continue  # memory is default-on in 2.0; no service needed
        elif token == "--doctor":
            return ["doctor"]
        elif token == "--review":
            print("pxx: --review is deprecated; mapping to `pxx review`", file=sys.stderr)
            return ["review"]
        elif token == "--self-test":
            print("pxx: --self-test is deprecated; mapping to `pxx run`", file=sys.stderr)
            return ["run", "-m", _SELF_TEST_TASK]
        elif token == "--self-lint":
            print("pxx: --self-lint is deprecated; mapping to `pxx run`", file=sys.stderr)
            return ["run", "-m", _SELF_LINT_TASK]
        else:
            out.append(token)
    return [mode, *out]


# ---------------------------------------------------------------------------
# Parser


def _add_run_options(parser: argparse.ArgumentParser, *, files: bool = True) -> None:
    parser.add_argument("-m", "--message", help="task / prompt text (default: stdin)")
    if files:
        parser.add_argument("files", nargs="*", help="context files (noted in the prompt)")
    parser.add_argument("--model", help="model name (e.g. qwen2.5-coder:7b)")
    parser.add_argument("--base-url", help="endpoint base URL")
    parser.add_argument("--provider", choices=["ollama", "openai", "vllm", "openai-compatible"])
    parser.add_argument("--scope", help="comma-separated repo-relative scope prefixes")
    parser.add_argument("--budget-rounds", type=int, help="max agent rounds")
    parser.add_argument("--budget-tokens", type=int, help="max tokens")
    parser.add_argument("--budget-cost", type=float, help="max cost in USD")
    parser.add_argument("--budget-seconds", type=float, help="max wall-clock seconds")
    parser.add_argument("--budget-diff-lines", type=int, help="max diff lines")
    parser.add_argument("--no-memory", action="store_true", help="disable memory")
    parser.add_argument("--sandbox", action="store_true", help="sandbox shell commands")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="commit the session's work on COMPLETED (undo still: git reset --hard <net tag>)",
    )
    parser.add_argument(
        "--with-mcp",
        action="append",
        default=[],
        metavar="NAME=CMD",
        help="attach an MCP stdio server (repeatable; CMD is shlex-split)",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "native", "aider"],
        default=None,
        help="execution backend (default: auto for ask/edit/plan/chat, native for run/loop)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pxx", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"pxx {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("ask", "edit", "plan", "run", "chat"):
        _add_run_options(sub.add_parser(name), files=name != "run")
    _add_run_options(sub.add_parser("loop"), files=False)

    memory = sub.add_parser("memory", help="inspect the persistent memory store")
    mem_sub = memory.add_subparsers(dest="memory_command", required=True)
    mem_search = mem_sub.add_parser("search")
    mem_search.add_argument("query")
    mem_search.add_argument("-k", type=int, default=8)
    mem_add = mem_sub.add_parser("add")
    mem_add.add_argument("content")
    mem_add.add_argument("--tags", default="", help="comma-separated tags")
    mem_add.add_argument("--kind", default="note")
    mem_sub.add_parser("list")
    mem_forget = mem_sub.add_parser("forget")
    mem_forget.add_argument("id", type=int)
    mem_sub.add_parser("gc", help="run one deterministic garbage-collection pass")
    mem_sub.add_parser("grades", help="per-layer memory health grades")

    sub.add_parser("mcp", help="run the pxx MCP stdio server")

    serve = sub.add_parser("serve", help="run the headless HTTP API")
    serve.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8400, help="bind port (default: 8400)")

    sub.add_parser("doctor", help="health checks")
    sub.add_parser("upgrade", help="self-update pxx")

    audit = sub.add_parser("audit", help="inspect the hash-chained audit log")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_verify = audit_sub.add_parser("verify")
    audit_verify.add_argument("path")
    audit_tail = audit_sub.add_parser("tail")
    audit_tail.add_argument("-n", type=int, default=20, help="number of records")
    audit_tail.add_argument("--date", help="YYYY-MM-DD (default: today)")

    # --- self-improvement platform verbs (see DESIGN-ROADMAP.md) -------------

    runs = sub.add_parser("runs", help="inspect recorded runs")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_sub.add_parser("list")
    runs_list.add_argument("-n", "--limit", type=int, default=20)
    runs_show = runs_sub.add_parser("show")
    runs_show.add_argument("run_id")
    runs_export = runs_sub.add_parser("export")
    runs_export.add_argument("path", help="destination .jsonl file")
    runs_export.add_argument("-n", "--limit", type=int, default=1000)
    runs_resume = runs_sub.add_parser(
        "resume", help="resume a run from its checkpoint (deterministic replay)"
    )
    runs_resume.add_argument("run_id")

    agents = sub.add_parser("agents", help="agent versions and their runs")
    agents_sub = agents.add_subparsers(dest="agents_command", required=True)
    agents_sub.add_parser("list")
    agents_show = agents_sub.add_parser("show")
    agents_show.add_argument("agent_version_id")

    verify = sub.add_parser("verify", help="verification packet for a run (default: latest run)")
    verify.add_argument("run_id", nargs="?")

    metrics = sub.add_parser("metrics", help="aggregate metrics over recorded runs")
    met_sub = metrics.add_subparsers(dest="metrics_command", required=True)
    for name in ("summary", "failures", "memory-impact"):
        met_sub.add_parser(name).add_argument(
            "-n", "--limit", type=int, default=None, help="cap runs analyzed (default: all)"
        )
    met_export = met_sub.add_parser("export")
    met_export.add_argument("path", help="destination .json file")
    met_export.add_argument(
        "-n", "--limit", type=int, default=None, help="cap runs analyzed (default: all)"
    )
    met_compare = met_sub.add_parser(
        "compare", help="per-metric delta between two agents' run sets"
    )
    met_compare.add_argument("agent_a", help="baseline agent_version_id")
    met_compare.add_argument("agent_b", help="candidate agent_version_id")

    evalp = sub.add_parser("eval", help="run the eval corpus (default root: ./evals)")
    eval_sub = evalp.add_subparsers(dest="eval_command", required=True)
    for name in ("run", "self-check"):
        p = eval_sub.add_parser(name)
        p.add_argument("--corpus", help="corpus root containing tier dirs (default: ./evals)")
    eval_report = eval_sub.add_parser("report")
    eval_report.add_argument("--corpus", help="corpus root containing tier dirs (default: ./evals)")
    eval_report.add_argument("--out", help="also write the scorecard JSON to this path")
    eval_report.add_argument(
        "--partition",
        choices=["dev", "held-out", "all"],
        default="all",
        help="score only one corpus partition (promotion evidence must be held-out)",
    )

    calibrate = sub.add_parser(
        "calibrate",
        help="reviewer calibration suite (requires a reachable model endpoint; "
        "exits 2 on threshold breach)",
    )
    calibrate.add_argument("--corpus", help="calibration cases dir (default: ./evals/calibration)")

    improve = sub.add_parser("improve", help="self-improvement mining and cycles")
    imp_sub = improve.add_subparsers(dest="improve_command", required=True)
    for name in ("analyze", "clusters", "proposals"):
        imp_sub.add_parser(name).add_argument(
            "-n", "--limit", type=int, default=None, help="cap runs analyzed (default: all)"
        )
    imp_sub.add_parser("cycle", help="run one propose-only improvement cycle")
    imp_eval = imp_sub.add_parser(
        "evaluate-candidate",
        help="evaluate a candidate against the held-out corpus (both arms)",
    )
    imp_eval.add_argument("candidate_id")
    imp_eval.add_argument("--corpus", help="corpus root containing tier dirs (default: ./evals)")
    imp_sub.add_parser("principles", help="run golden-principle lints over the source tree")
    imp_sub.add_parser("readiness", help="report auto-promotion readiness bars")
    imp_auto = imp_sub.add_parser(
        "auto-promote",
        help="evidence-gated auto-promotion (default: report and refuse)",
    )
    imp_auto.add_argument("candidate_id")
    imp_auto.add_argument(
        "--consent",
        action="store_true",
        help="actually promote when every bar is green (default: refuse)",
    )
    imp_sub.add_parser("status", help="operator view: cycle, queue, inbox, daemon")
    imp_daemon = imp_sub.add_parser("daemon", help="run the improvement daemon")
    imp_daemon.add_argument(
        "--interval", type=float, default=3600.0, help="seconds between cycle ticks"
    )
    imp_daemon.add_argument("--once", action="store_true", help="run a single tick and exit")
    imp_sub.add_parser("pause", help="pause the daemon at the next tick boundary")
    imp_sub.add_parser("resume", help="resume a paused daemon")

    propose = sub.add_parser("propose", help="write a validated, immutable improvement candidate")
    propose.add_argument("--id", required=True, help="candidate id")
    propose.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="settings overlay: review_mode=..., budgets.<field>=N (tighten-only), "
        "model=..., fallback_models=a,b, memory_retrieval_limit=N (exactly one)",
    )
    propose.add_argument(
        "--content", metavar="PATH", help="content candidate target (pxx/prompts/*.md)"
    )
    propose.add_argument("--text", help="new prompt content (with --content)")
    propose.add_argument("--text-file", help="read new prompt content from a file")
    propose.add_argument("--rationale", required=True, help="why this change")
    propose.add_argument("--evidence", required=True, help="comma-separated evidence run_ids")

    compare = sub.add_parser(
        "compare", help="compare two scorecard JSON files (exit 2 when not eligible)"
    )
    compare.add_argument("baseline", help="baseline scorecard JSON")
    compare.add_argument("candidate", help="candidate scorecard JSON")
    compare.add_argument(
        "--human-override",
        metavar="APPROVER",
        help="promote past soft failures (never rescues hard-gate failures)",
    )

    agent = sub.add_parser("agent", help="deployment channels (stable/candidate/shadow)")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_activate = agent_sub.add_parser("activate")
    agent_activate.add_argument("channel", choices=["stable", "candidate", "shadow", "canary"])
    agent_activate.add_argument("agent_version_id")
    agent_sub.add_parser("rollback", help="restore the previous stable version")
    agent_sub.add_parser("canary", help="show canary status and green-run accounting")
    agent_sub.add_parser("history")
    agent_sub.add_parser("channels", help="show current channel assignments")

    promote = sub.add_parser(
        "promote",
        help="record a human-gated promotion for a candidate (never auto-applies)",
    )
    promote.add_argument("candidate_id")
    promote.add_argument("--approver", help="approver identity (default: $USER)")
    promote.add_argument(
        "--scorecard",
        metavar="PATH",
        help="candidate scorecard JSON with REAL hard-gate evidence (required; "
        "produced by `pxx eval report --out`)",
    )

    check = sub.add_parser(
        "check", help="scan content for secrets/internal data (exit 2 on findings)"
    )
    check.add_argument(
        "--all-files",
        action="store_true",
        help="scan all tracked files (default: staged files only)",
    )
    check.add_argument(
        "--denylist",
        help="path to a public-denylist file (default: ~/.config/pxx/public-denylist)",
    )
    check.add_argument(
        "--require-denylist",
        action="store_true",
        help="fail when the denylist loads empty (armed release gate: "
        "an empty denylist means the hostname dimension is silently off)",
    )

    goal = sub.add_parser("goal", help="decompose a goal into a task DAG and run it")
    _add_run_options(goal, files=False)

    review = sub.add_parser(
        "review", help="read-only review of the current diff (exit 2 on REVISE)"
    )
    review_source = review.add_mutually_exclusive_group()
    review_source.add_argument(
        "--staged", action="store_true", help="review the staged diff instead of the working tree"
    )
    review_source.add_argument(
        "--since", metavar="SHA", help="review the working tree against this commit"
    )

    workflow = sub.add_parser("workflow", help="inspect the WORKFLOW.md contract")
    wf_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    wf_sub.add_parser("validate", help="validate WORKFLOW.md (exit 2 on failure)")

    context = sub.add_parser("context", help="agent-legibility checks")
    ctx_sub = context.add_subparsers(dest="context_command", required=True)
    ctx_sub.add_parser("audit", help="verify agent-legible docs are present + consistent")

    docs = sub.add_parser("docs", help="documentation consistency checks")
    docs_sub = docs.add_subparsers(dest="docs_command", required=True)
    docs_sub.add_parser("check", help="verify documented pxx verbs exist")

    return parser


# ---------------------------------------------------------------------------
# Run-ish commands (ask/edit/plan/run/loop/chat)


def _resolve_backend_name(command: str, requested: str | None) -> str:
    if requested in ("native", "aider"):
        return requested
    if command in ("run", "loop"):
        return "native"
    # auto: aider when available, else pxx's native loop
    return "aider" if shutil.which("aider") else "native"


def _make_backend(name: str, settings):
    """Instantiate a backend by name (lazy imports: optional deps)."""
    try:
        if name == "aider":
            from .backends.aider import AiderBackend

            return AiderBackend()
        from .backends.native import NativeBackend

        return NativeBackend()
    except ImportError as exc:
        raise PxxError(f"backend '{name}' unavailable: {exc}") from exc


def _cli_overrides(args: argparse.Namespace, permission: PermissionMode) -> dict:
    overrides: dict = {"permission": str(permission)}
    if args.model:
        overrides["model"] = args.model
    if args.provider:
        overrides["provider"] = args.provider
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.scope:
        overrides["scope"] = [s.strip() for s in args.scope.split(",") if s.strip()]
    budgets = {}
    for value, key in (
        (args.budget_rounds, "max_rounds"),
        (args.budget_tokens, "max_tokens"),
        (args.budget_cost, "max_cost_usd"),
        (args.budget_seconds, "max_wall_seconds"),
        (args.budget_diff_lines, "max_diff_lines"),
    ):
        if value is not None:
            budgets[key] = value
    if budgets:
        overrides["budgets"] = budgets
    if args.no_memory:
        overrides["memory_enabled"] = False
    if args.sandbox:
        overrides["sandbox_shell"] = True
    if getattr(args, "commit", False):
        overrides["auto_commit"] = True
    if args.with_mcp:
        specs = []
        for item in args.with_mcp:
            name, sep, cmd = item.partition("=")
            if not sep or not name.strip() or not cmd.strip():
                raise PxxError(f"--with-mcp expects NAME=CMD, got {item!r}")
            specs.append({"name": name.strip(), "command": shlex.split(cmd)})
        overrides["mcp_servers"] = specs
    return overrides


def _read_task(args: argparse.Namespace) -> str:
    task = args.message
    if task is None and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    return task or ""


def _handle_unknown_flags(unknown: list[str], backend_name: str) -> str:
    """1.x compat: forward unknown flags to aider; otherwise warn + ignore."""
    note = ""
    for flag in unknown:
        if backend_name == "aider":
            print(f"pxx: unknown flag forwarded: {flag}", file=sys.stderr)
        else:
            print(f"pxx: ignoring unknown flag: {flag}", file=sys.stderr)
    if unknown and backend_name == "aider":
        note = "\n\n[1.x compat] forwarded aider flags: " + " ".join(unknown)
    return note


def _run_session(settings, backend, task: str) -> RunOutcome:
    session = Session(settings, backend, cwd=Path.cwd())
    return asyncio.run(session.run(task))


def _cmd_run_like(args: argparse.Namespace, unknown: list[str]) -> int:
    backend_name = _resolve_backend_name(args.command, args.backend)
    task = _read_task(args)
    if getattr(args, "files", None):
        task += "\n\nContext files (user-supplied): " + ", ".join(args.files)
    task += _handle_unknown_flags(unknown, backend_name)
    if not task.strip():
        print("pxx: usage: a task is required (-m/--message or stdin)", file=sys.stderr)
        return EXIT_USAGE
    settings = load_settings(Path.cwd(), _cli_overrides(args, _MODE_BY_COMMAND[args.command]))
    backend = _make_backend(backend_name, settings)
    outcome = _run_session(settings, backend, task)
    print(
        f"[{outcome.code}] {outcome.summary} "
        f"(rounds={outcome.rounds} tokens={outcome.tokens} diff_lines={outcome.diff_lines})"
    )
    return exit_code_for(outcome)


def _cmd_loop(args: argparse.Namespace, unknown: list[str]) -> int:
    # K6: the loop engine is native-only. A parsed --backend must be rejected
    # loudly (fail-loud), never silently dropped.
    if getattr(args, "backend", None) not in (None, "native"):
        print(
            f"pxx: loop runs on the native backend only "
            f"(--backend {args.backend} is not supported)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        from .loop import run_loop
    except ImportError:
        print("pxx: loop engine unavailable (pxx.loop not installed)", file=sys.stderr)
        return 1
    task = _read_task(args)
    task += _handle_unknown_flags(unknown, "native")
    if not task.strip():
        print("pxx: usage: a task is required (-m/--message or stdin)", file=sys.stderr)
        return EXIT_USAGE
    settings = load_settings(Path.cwd(), _cli_overrides(args, PermissionMode.AUTO))
    lint_command = None
    if (Path.cwd() / "WORKFLOW.md").is_file():
        from .errors import ConfigError
        from .workflow import load_workflow

        try:
            lint_command = load_workflow(Path.cwd()).commands.get("lint") or None
        except ConfigError as exc:
            print(f"pxx: error: {exc}", file=sys.stderr)
            return 1
    outcome = asyncio.run(run_loop(task, settings, lint_command=lint_command))
    print(f"[{outcome.code}] {outcome.summary}")
    return exit_code_for(outcome)


async def _chat_printer(event: Event) -> None:
    if event.kind == "model_response":
        text = event.data.get("text") or event.data.get("preview") or ""
        if text:
            print(text)


def _cmd_chat(args: argparse.Namespace, unknown: list[str]) -> int:
    _handle_unknown_flags(unknown, "native")
    settings = load_settings(Path.cwd(), _cli_overrides(args, PermissionMode.EDIT))
    backend = _make_backend("native" if args.backend in (None, "auto") else args.backend, settings)
    session = Session(settings, backend, cwd=Path.cwd())
    session.bus.subscribe(_chat_printer)
    print("pxx chat (native backend). Type 'exit' or Ctrl-D to quit.")
    while True:
        try:
            line = input("pxx> ")
        except EOFError:
            break
        if line.strip().lower() in ("exit", "quit"):
            break
        if not line.strip():
            continue
        outcome = asyncio.run(session.run(line))
        if not outcome.ok:
            print(f"[{outcome.code}] {outcome.summary}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# memory / audit / mcp / serve / doctor / upgrade


def _maybe_await(value):
    """Tolerate sync or async implementations of parallel-built modules."""
    import inspect

    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _obs_field(obs, name, default=""):
    return getattr(obs, name, default)


def _print_observations(rows) -> None:
    for obs in rows:
        tags = _obs_field(obs, "tags", []) or []
        if not isinstance(tags, (list, tuple)):
            tags = [str(tags)]
        content = str(_obs_field(obs, "content"))[:70]
        print(
            f"{_obs_field(obs, 'id'):>6}  {_obs_field(obs, 'kind'):<12}  "
            f"{','.join(str(t) for t in tags):<24}  {content}"
        )


def _cmd_memory(args: argparse.Namespace) -> int:
    settings = load_settings(Path.cwd())
    try:
        from .memory.store import MemoryStore
    except ImportError:
        print("pxx: memory store unavailable (pxx.memory not installed)", file=sys.stderr)
        return 1
    store = MemoryStore(settings.memory_dir / "memory.db")
    project = Path.cwd().name
    cmd = args.memory_command
    if cmd == "search":
        rows = _maybe_await(store.search(project, args.query, k=args.k))
        _print_observations(rows)
    elif cmd == "add":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        obs_id = _maybe_await(store.add(project, args.kind, args.content, tags=tags, source="cli"))
        print(f"added observation {obs_id}")
    elif cmd == "list":
        _print_observations(_maybe_await(store.list(project)))
    elif cmd == "forget":
        _maybe_await(store.forget(args.id))
        print(f"forgot observation {args.id}")
    elif cmd == "gc":
        from .entropy import run_gc

        report = run_gc(store)
        print(
            f"gc: archived_expired={report.archived_expired} "
            f"pruned_low_utility={report.pruned_low_utility} "
            f"auto_quarantined={report.auto_quarantined}"
        )
    elif cmd == "grades":
        from .entropy import quality_grades

        for layer, grade in quality_grades(store).items():
            print(f"{layer}: {grade}")
    return 0


def _summarize_event_data(data: dict) -> str:
    parts = []
    for key in ("code", "summary", "tool", "gate", "model", "backend", "path", "allowed"):
        if key in data:
            parts.append(f"{key}={str(data[key])[:60]}")
    return " ".join(parts)


def _cmd_audit(args: argparse.Namespace) -> int:
    import json
    import time

    if args.audit_command == "verify":
        ok = AuditLog.verify(Path(args.path))
        print("OK" if ok else "CORRUPT")
        return 0 if ok else 1
    # tail
    settings = load_settings(Path.cwd())
    day = args.date or time.strftime("%Y-%m-%d")
    path = settings.state_dir / "audit" / f"{day}.jsonl"
    if not path.is_file():
        print(f"pxx: no audit file for {day} ({path})", file=sys.stderr)
        return 1
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    for raw in lines[-args.n :]:
        try:
            event = json.loads(raw)["event"]
        except (KeyError, json.JSONDecodeError):
            print(raw[:120])
            continue
        print(
            f"{event.get('seq', 0):>6}  {event.get('kind', '?'):<16}  "
            f"{_summarize_event_data(event.get('data', {}))}"
        )
    return 0


def _cmd_mcp() -> int:
    try:
        from .mcp.server import main as mcp_main
    except ImportError:
        print("pxx: MCP server unavailable (pxx.mcp not installed)", file=sys.stderr)
        return 1
    _maybe_await(mcp_main())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import os

    settings = load_settings(Path.cwd())
    if args.host not in _LOOPBACK_HOSTS and not os.environ.get("PXX_SERVER_TOKEN"):
        print(
            f"pxx: WARNING: binding to {args.host} without PXX_SERVER_TOKEN — "
            "the API will be UNAUTHENTICATED on the network. "
            "Set PXX_SERVER_TOKEN or bind to 127.0.0.1.",
            file=sys.stderr,
        )
    try:
        from .server import run_server
    except ImportError as exc:
        print(f"pxx: serve requires the 'server' extra (fastapi+uvicorn): {exc}", file=sys.stderr)
        return 1
    run_server(settings, host=args.host, port=args.port)
    return 0


def _cmd_doctor() -> int:
    from .doctor import print_report, run_doctor

    settings = load_settings(Path.cwd())
    checks = asyncio.run(run_doctor(settings))
    return 0 if print_report(checks) else 1


def _cmd_upgrade() -> int:
    from .upgrade import upgrade

    result = asyncio.run(upgrade())
    print(result.message)
    return 0 if result.status in ("updated", "current") else 1


# ---------------------------------------------------------------------------
# self-improvement platform verbs (runs/agents/verify/metrics/eval/calibrate/
# improve/propose/compare/agent/promote/check/goal)


def _state_dir() -> Path:
    return load_settings(Path.cwd()).state_dir


def _runs(state_dir: Path, limit: int | None):
    from .runs import list_runs

    return list_runs(state_dir, limit=limit if limit is not None else 1_000_000)


def _print_run_row(run) -> None:
    print(
        f"{run.run_id:<40}  {run.code or 'UNKNOWN':<16}  "
        f"agent={run.agent_version_id or '?'} model={run.model or '?'} "
        f"rounds={run.rounds} tokens={run.tokens}"
    )


def _cmd_runs(args: argparse.Namespace) -> int:
    from dataclasses import fields

    from .runs import export_jsonl

    state_dir = _state_dir()
    if args.runs_command == "resume":
        from .resume import resume_run, write_checkpoint

        settings = load_settings(Path.cwd())
        run_dir = state_dir / "runs" / args.run_id
        if not run_dir.is_dir():
            print(f"pxx: no such run: {args.run_id}", file=sys.stderr)
            return 1
        if not (run_dir / "checkpoint.json").is_file():
            checkpoint = write_checkpoint(state_dir, args.run_id)
            print(f"checkpoint written ({checkpoint.events_count} events)")
        outcome = asyncio.run(resume_run(state_dir, args.run_id, settings, cwd=Path.cwd()))
        print(f"[{outcome.code}] {outcome.summary}")
        return exit_code_for(outcome)
    runs = _runs(state_dir, getattr(args, "limit", None))
    if args.runs_command == "list":
        if not runs:
            print("no runs recorded")
        for run in runs:
            _print_run_row(run)
        return 0
    if args.runs_command == "show":
        for run in runs:
            if run.run_id == args.run_id:
                for f in fields(run):
                    print(f"{f.name}: {getattr(run, f.name)}")
                return 0
        print(f"pxx: no such run: {args.run_id}", file=sys.stderr)
        return 1
    # export
    try:
        written = export_jsonl(runs, Path(args.path))
    except OSError as exc:
        print(f"pxx: cannot export runs to {args.path}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {written} run(s) to {args.path}")
    return 0


def _cmd_agents(args: argparse.Namespace) -> int:
    import json

    from .runs import group_by_agent, quarantined_agents

    state_dir = _state_dir()
    groups = group_by_agent(state_dir)
    quarantined = quarantined_agents(state_dir)
    if args.agents_command == "list":
        if not groups:
            print("no agents recorded")
        for agent_id in sorted(groups):
            runs = groups[agent_id]
            completed = sum(r.ok for r in runs)
            rate = round(completed / len(runs), 4) if runs else 0.0
            models = sorted({r.model for r in runs if r.model})
            marker = "  QUARANTINED (model drift)" if agent_id in quarantined else ""
            print(
                f"{agent_id:<20}  runs={len(runs)} completed={completed} "
                f"success_rate={rate} models={','.join(models) or '?'}{marker}"
            )
        return 0
    # show
    runs = groups.get(args.agent_version_id)
    if not runs:
        print(f"pxx: no such agent: {args.agent_version_id}", file=sys.stderr)
        return 1
    manifest_path = state_dir / "runs" / runs[0].run_id / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if manifest:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    for run in runs:
        _print_run_row(run)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from .verify import format_packet, packet_for_run

    state_dir = _state_dir()
    run_id = args.run_id
    if run_id is None:
        latest = _runs(state_dir, 1)
        if not latest:
            raise PxxError("no runs recorded; nothing to verify")
        run_id = latest[0].run_id
    print(format_packet(packet_for_run(state_dir, run_id)))
    return 0


def _cmd_metrics(args: argparse.Namespace) -> int:
    import json
    from dataclasses import asdict

    from . import runs as runs_mod

    state_dir = _state_dir()
    runs = _runs(state_dir, getattr(args, "limit", None))
    command = args.metrics_command
    if command == "compare":
        groups = runs_mod.group_by_agent(state_dir)
        a_runs = groups.get(args.agent_a)
        b_runs = groups.get(args.agent_b)
        if a_runs is None or b_runs is None:
            missing = args.agent_a if a_runs is None else args.agent_b
            print(f"pxx: usage: no runs recorded for agent {missing}", file=sys.stderr)
            return EXIT_USAGE
        comparison = runs_mod.metrics_compare(a_runs, b_runs)
        print(
            f"A={args.agent_a} runs={comparison.a.total} success_rate={comparison.a.success_rate}"
        )
        print(
            f"B={args.agent_b} runs={comparison.b.total} success_rate={comparison.b.success_rate}"
        )
        print(
            f"delta_success_rate={comparison.delta_success_rate} "
            f"delta_avg_rounds={comparison.delta_avg_rounds} "
            f"delta_avg_tokens={comparison.delta_avg_tokens} "
            f"delta_known_cost_usd={comparison.delta_known_cost_usd}"
        )
        return 0
    if command == "summary":
        summary = runs_mod.metrics_summary(runs)
        print(
            f"total={summary.total} completed={summary.completed} "
            f"failed={summary.failed} success_rate={summary.success_rate}"
        )
        print(
            f"rounds={summary.total_rounds} tokens={summary.total_tokens} "
            f"diff_lines={summary.total_diff_lines} cost_usd={summary.known_cost_usd}"
        )
        for code, count in summary.by_code.items():
            print(f"  {code}: {count}")
    elif command == "failures":
        report = runs_mod.metrics_failures(runs)
        print(f"failures={report.total_failures}")
        for code, count in report.by_code.items():
            print(f"  {code}: {count}")
        for run in report.runs:
            _print_run_row(run)
    elif command == "memory-impact":
        impact = runs_mod.memory_impact(runs)
        for label, cohort in (
            ("with_memory", impact.with_memory),
            ("without_memory", impact.without_memory),
        ):
            print(
                f"{label}: runs={cohort.runs} completed={cohort.completed} "
                f"success_rate={cohort.success_rate} avg_rounds={cohort.avg_rounds} "
                f"avg_tokens={cohort.avg_tokens}"
            )
        print(f"delta_success_rate={impact.delta_success_rate} (correlation, not causation)")
    else:  # export
        payload = {
            "summary": asdict(runs_mod.metrics_summary(runs)),
            "failures": asdict(runs_mod.metrics_failures(runs)),
            "memory_impact": asdict(runs_mod.memory_impact(runs)),
        }
        try:
            Path(args.path).write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
            )
        except OSError as exc:
            print(f"pxx: cannot write metrics to {args.path}: {exc}", file=sys.stderr)
            return 1
        print(f"wrote metrics to {args.path}")
    return 0


# --- eval / calibrate ----------------------------------------------------------

_EVAL_TIERS = ("micro", "regression", "adversarial")


def _load_eval_cases(corpus: str | None):
    from .eval.cases import load_cases

    root = Path(corpus) if corpus else Path.cwd() / "evals"
    cases = []
    for tier in _EVAL_TIERS:
        tier_dir = root / tier
        if tier_dir.is_dir():
            cases.extend(load_cases(tier_dir))
    return cases, root


def _cmd_eval(args: argparse.Namespace) -> int:
    from .eval import harness
    from .eval import report as report_mod
    from .manifest import build_manifest

    settings = load_settings(Path.cwd())
    cases, root = _load_eval_cases(args.corpus)
    if not cases:
        print(f"pxx: usage: no eval cases found under {root} (fail-closed)", file=sys.stderr)
        return EXIT_USAGE
    command = args.eval_command
    if command == "report" and getattr(args, "partition", "all") != "all":
        from .eval.cases import Partition

        wanted = Partition(args.partition)
        cases = [c for c in cases if c.partition is wanted]
        if not cases:
            print(
                f"pxx: usage: no eval cases in partition {args.partition!r} (fail-closed)",
                file=sys.stderr,
            )
            return EXIT_USAGE
    if command == "self-check":
        failed = 0
        for case in cases:
            result = harness.self_check(case)
            print(f"{case.id}: {'ok' if result.ok else 'FAIL'}")
            if not result.ok:
                failed += 1
                for name in result.honest_failures:
                    print(f"  honest_failed: {name}")
                if not result.cheat_caught:
                    print("  cheat_not_caught")
        print(f"self-check: {len(cases) - failed}/{len(cases)} ok")
        return 2 if failed else 0

    results = [harness.run_case(case) for case in cases]
    if command == "run":
        for result in results:
            print(f"{result.case_id}: {'pass' if result.passed else 'fail'}")
            for check in result.checks:
                if not check.ok:
                    print(f"  failed_check: {check.name}")
        failed = sum(not r.passed for r in results)
        print(f"eval: {len(results) - failed}/{len(results)} passed")
        return 2 if failed else 0

    # report: build + render the scorecard (a failed case is data, not an error)
    verdicts = [
        report_mod.CaseVerdict(case_id=r.case_id, passed=r.passed, failed_checks=r.failed_checks)
        for r in results
    ]
    manifest = build_manifest(settings, "native")
    partition = getattr(args, "partition", "all")
    scorecard = report_mod.build_scorecard(
        manifest.agent_version_id, cases, verdicts, partition=partition
    )
    print(report_mod.render(scorecard), end="")
    # Hard gates are computed from the actual run evidence; a gate with no
    # evidence is False (fail closed) — never assumed green.
    gates = report_mod.compute_gates(cases, results)
    for gate, held in sorted(gates.items()):
        print(f"gate {gate}: {'held' if held else 'NOT HELD / no evidence'}")
        if not held:
            print(
                f"pxx: hard gate {gate} not held or unmeasured in this run",
                file=sys.stderr,
            )
    if args.out:
        import json

        payload = {
            "agent_version_id": scorecard.agent_version_id,
            "corpus_fingerprint": scorecard.corpus_fingerprint,
            "partition": scorecard.partition,
            "verdicts": {v.case_id: v.passed for v in scorecard.verdicts},
            "gates": gates,
            "families": {fam: list(counts) for fam, counts in scorecard.families.items()},
            "metrics": report_mod.arm_metrics(results),
            "passed": scorecard.passed,
            "failed": scorecard.failed,
            "total": scorecard.total,
        }
        try:
            Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            print(f"pxx: cannot write scorecard to {args.out}: {exc}", file=sys.stderr)
            return 1
        print(f"wrote scorecard to {args.out}")
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibration import breaches, load_cases, run_calibration
    from .review import NativeReviewer

    settings = load_settings(Path.cwd())
    corpus = Path(args.corpus) if args.corpus else Path.cwd() / "evals" / "calibration"
    if not corpus.is_dir() or not any(corpus.glob("*.toml")):
        print(f"pxx: usage: no calibration cases found in {corpus} (fail-closed)", file=sys.stderr)
        return EXIT_USAGE
    cases = load_cases(corpus)
    reviewer = NativeReviewer(settings.model)
    report = asyncio.run(run_calibration(reviewer, cases))
    print(
        f"recall={report.recall:.3f} fp_rate={report.fp_rate:.3f} "
        f"format_compliance={report.format_compliance:.3f} "
        f"availability={report.availability:.3f}"
    )
    for warning in report.warnings:
        print(f"pxx: calibration warning: {warning}", file=sys.stderr)
    for result in report.results:
        print(f"{result.case_id}: {'ok' if result.passed else 'FAIL'} verdict={result.verdict}")
    problems = breaches(report)
    if problems:
        for problem in problems:
            print(f"breach: {problem}", file=sys.stderr)
        return 2
    print("calibration ok")
    return 0


# --- improve / propose / compare / agent / promote ------------------------------


def _mined_runs(state_dir: Path, limit: int | None) -> list[dict]:
    return [
        {
            "run_id": run.run_id,
            "terminal_code": run.code,
            "backend": run.backend,
            "model": run.model,
            "agent_version_id": run.agent_version_id,
            "rounds": run.rounds,
            "memory_used": run.memory,
        }
        for run in _runs(state_dir, limit)
    ]


def _print_cluster(cluster) -> None:
    print(
        f"{cluster.terminal_code or 'UNKNOWN'} backend={cluster.backend or '?'} "
        f"model={cluster.model or '?'} memory={cluster.memory_used} "
        f"rounds={cluster.rounds_bucket} size={cluster.size} label={cluster.label}"
    )
    print(f"  runs: {', '.join(cluster.run_ids)}")


def _cmd_improve(args: argparse.Namespace) -> int:
    import json

    state_dir = _state_dir()
    command = args.improve_command
    if command == "status":
        from .improve.scheduler import is_paused

        report_path = state_dir / "cycle-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text())
            print(
                f"cycle: {report.get('cycle_id', '-')} "
                f"candidates={len(report.get('candidates', []))} "
                f"proposals={report.get('proposals', 0)}"
            )
        else:
            print("cycle: none run yet")
        from .improve.tasks import TaskStore

        tasks = TaskStore(state_dir)
        counts: dict[str, int] = {}
        for task in tasks.list():
            counts[task.state] = counts.get(task.state, 0) + 1
        queue = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"
        print(f"queue: {queue}")
        for box in ("qualified", "rejected", "human-review-required"):
            box_dir = state_dir / "inbox" / box
            n = len(list(box_dir.glob("*.json"))) if box_dir.is_dir() else 0
            print(f"inbox {box}: {n}")
        print(f"daemon: {'paused' if is_paused(state_dir) else 'running'}")
        return 0
    if command == "daemon":
        from .improve.scheduler import run_daemon

        report = run_daemon(
            state_dir,
            interval_seconds=args.interval,
            max_ticks=1 if args.once else None,
        )
        print(
            f"daemon: ticks={report.ticks} cycles={report.cycles_run} "
            f"paused-skips={report.skipped_paused} ({report.stopped_reason})"
        )
        return 0
    if command == "pause":
        from .improve.scheduler import set_paused

        set_paused(state_dir, True)
        print("daemon paused (halts at the next tick boundary)")
        return 0
    if command == "resume":
        from .improve.scheduler import set_paused

        set_paused(state_dir, False)
        print("daemon resumed")
        return 0
    if command == "readiness":
        from .improve.autopromote import evaluate_readiness, gather_counts
        from .improve.evidence import check_preconditions, preconditions_met

        preconditions = check_preconditions(Path.cwd(), state_dir)
        for item in preconditions:
            print(f"precondition {item.name}: {'ok' if item.ok else 'MISSING'} ({item.detail})")
        report = evaluate_readiness(gather_counts(state_dir, evals_dir=Path.cwd() / "evals"))
        for name, ok in sorted(report.bars.items()):
            print(f"bar {name}: {'green' if ok else 'unmet'}")
        ready = preconditions_met(preconditions) and report.green
        print(f"readiness: {'READY' if ready else 'NOT-READY'}")
        return 0 if ready else 2
    if command == "auto-promote":
        from .errors import CandidateInvalid
        from .improve.autopromote import (
            auto_promote,
            evaluate_readiness,
            gather_counts,
        )
        from .improve.candidates import read_candidate, validate_candidate
        from .improve.channels import ChannelManager
        from .improve.evidence import (
            check_preconditions,
            compute_evidence,
            preconditions_met,
        )

        candidate_dir = state_dir / "candidates" / args.candidate_id
        if not (candidate_dir / "candidate.json").is_file():
            print(f"pxx: no such candidate: {args.candidate_id}", file=sys.stderr)
            return 1
        try:
            candidate = read_candidate(candidate_dir)
            validate_candidate(candidate)
        except CandidateInvalid as exc:
            print(f"pxx: usage: candidate invalid: {exc}", file=sys.stderr)
            return EXIT_USAGE

        preconditions = check_preconditions(Path.cwd(), state_dir)
        if not preconditions_met(preconditions):
            missing = [p.name for p in preconditions if not p.ok]
            print(
                "pxx: auto-promotion is globally disabled — mandatory items "
                "missing: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        report = evaluate_readiness(gather_counts(state_dir, evals_dir=Path.cwd() / "evals"))
        channels = ChannelManager(state_dir)
        evidence = compute_evidence(
            args.candidate_id,
            state_dir,
            corpus_root=Path.cwd() / "evals",
            channels=channels,
        )
        verdict = auto_promote(
            candidate,
            evidence,
            readiness_report=report,
            state_dir=state_dir,
            commit=bool(args.consent),
        )
        # human-visibility bundle: patch class, rationale, expected-vs-observed,
        # rollback command — always printed, promoted or not.
        print(f"candidate: {args.candidate_id} ({candidate.change_class} -> {candidate.target})")
        print(f"rationale: {candidate.rationale}")
        for name, detail in sorted(evidence.details.items()):
            print(f"evidence {name}: {detail}")
        print(f"risk: {verdict.risk}")
        if verdict.reasons:
            for reason in verdict.reasons:
                print(f"refuse: {reason}")
        if verdict.promoted:
            print(f"auto-promoted: record {verdict.record_path}")
            print(f"rollback command: {verdict.rollback_command}")
            return 0
        if verdict.would_promote:
            print("all bars green; --consent NOT given — refusing by default posture")
            return 2
        print(
            "posture: report-and-refuse (default); pass --consent to promote "
            "when every bar is green"
        )
        return 2
    if command == "principles":
        from .entropy import run_golden_principles

        violations = run_golden_principles(Path.cwd())
        for violation in violations:
            print(f"{violation.path}:{violation.line}: [{violation.principle}] {violation.message}")
        if violations:
            print(f"pxx principles: {len(violations)} violation(s)", file=sys.stderr)
            return 2
        print("pxx principles: clean")
        return 0
    if command == "evaluate-candidate":
        from .improve.candidate_eval import evaluate_candidate

        corpus = Path(args.corpus) if args.corpus else Path.cwd() / "evals"
        try:
            verdict = evaluate_candidate(args.candidate_id, state_dir, corpus_root=corpus)
        except PxxError as exc:
            print(f"pxx: usage: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(verdict.reason)
        if verdict.gained:
            print("gained: " + ", ".join(verdict.gained))
        if verdict.lost:
            print("lost: " + ", ".join(verdict.lost))
        if verdict.hard_gate_failures:
            print("hard-gate failures: " + ", ".join(verdict.hard_gate_failures))
        print(f"evaluated {verdict.case_count} held-out cases (both arms)")
        return 0 if verdict.promoted else 2
    if command == "cycle":
        from .improve.cycle import run_cycle

        report = run_cycle(state_dir, mode="propose-only")
        print(f"cycle {report.cycle_id} (mode={report.mode})")
        print(
            f"runs={report.runs_collected} clusters={report.clusters} proposals={report.proposals}"
        )
        print("candidates: " + (", ".join(report.candidates) or "none"))
        for skip in report.skipped:
            print(f"skipped: {skip['signature']} ({skip['reason']})")
        for signature in report.human_review:
            print(f"human-review-required: {signature}")
        print("stopped before promotion (propose-only)")
        return 0
    from .improve.mining import cluster_outcomes, propose_from_clusters

    mined = _mined_runs(state_dir, args.limit)
    clusters = cluster_outcomes(mined)
    if command == "analyze":
        proposals = propose_from_clusters(clusters)
        print(f"runs={len(mined)} clusters={len(clusters)} proposals={len(proposals)}")
        for cluster in clusters:
            _print_cluster(cluster)
        for proposal in proposals:
            print(json.dumps(proposal.to_dict(), sort_keys=True))
    elif command == "clusters":
        if not clusters:
            print("no clusters (no runs recorded)")
        for cluster in clusters:
            _print_cluster(cluster)
    else:  # proposals
        proposals = propose_from_clusters(clusters)
        if not proposals:
            print("no proposals")
        for proposal in proposals:
            print(json.dumps(proposal.to_dict(), sort_keys=True))
    return 0


def _parse_propose_set(pair: str, settings) -> tuple[str, object, dict | None]:
    """Map one --set KEY=VALUE pair to (target, value, baseline_budgets)."""
    from dataclasses import asdict

    from .errors import CandidateInvalid

    key, sep, raw = pair.partition("=")
    key = key.strip()
    raw = raw.strip()
    if not sep or not key or not raw:
        raise CandidateInvalid(f"--set expects KEY=VALUE, got {pair!r}")
    if key == "review_mode":
        return "review_mode", raw, None
    if key.startswith("budgets."):
        field = key.removeprefix("budgets.").strip()
        try:
            number: object = int(raw)
        except ValueError:
            try:
                number = float(raw)
            except ValueError:
                raise CandidateInvalid(f"budget value must be numeric: {raw!r}") from None
        return "budgets", {field: number}, asdict(settings.budgets)
    if key == "model":
        return "model", raw, None
    if key == "fallback_models":
        return "fallback_models", [m.strip() for m in raw.split(",") if m.strip()], None
    if key == "memory_retrieval_limit":
        try:
            return "memory_retrieval_limit", int(raw), None
        except ValueError:
            raise CandidateInvalid(f"memory_retrieval_limit must be an integer: {raw!r}") from None
    raise CandidateInvalid(f"unknown --set key: {key!r}")


def _build_candidate(args: argparse.Namespace, settings):
    from .errors import CandidateInvalid
    from .improve.candidates import CandidateClass, make_candidate

    evidence = tuple(e.strip() for e in args.evidence.split(",") if e.strip())
    if args.content:
        if args.sets:
            raise CandidateInvalid("use either --set or --content, not both")
        if args.text is not None:
            value = args.text
        elif args.text_file:
            try:
                value = Path(args.text_file).read_text()
            except OSError as exc:
                raise CandidateInvalid(f"cannot read --text-file: {exc}") from exc
        else:
            raise CandidateInvalid("--content requires --text or --text-file")
        return make_candidate(
            args.id, CandidateClass.CONTENT, args.content, value, args.rationale, evidence
        )
    if len(args.sets) != 1:
        raise CandidateInvalid(
            "exactly one --set KEY=VALUE is required (one behavioral variable per candidate)"
        )
    target, value, baseline = _parse_propose_set(args.sets[0], settings)
    return make_candidate(
        args.id,
        CandidateClass.SETTINGS,
        target,
        value,
        args.rationale,
        evidence,
        baseline_budgets=baseline,
    )


def _cmd_propose(args: argparse.Namespace) -> int:
    from .errors import CandidateInvalid
    from .improve.candidates import write_candidate

    settings = load_settings(Path.cwd())
    try:
        candidate = _build_candidate(args, settings)
        path = write_candidate(candidate, settings.state_dir)
    except CandidateInvalid as exc:
        print(f"pxx: usage: candidate rejected: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"candidate written (immutable): {path}")
    return 0


@dataclass(frozen=True)
class _ScorecardJSON:
    """Duck-typed scorecard view for pxx.improve.promotion.compare."""

    agent_version_id: str
    corpus_fingerprint: str
    verdicts: dict[str, bool]
    gates: dict[str, bool]
    partition: str = ""
    metrics: dict[str, float | None] = None  # type: ignore[assignment]


def _load_scorecard_json(path: str) -> _ScorecardJSON:
    import json

    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PxxError(f"cannot read scorecard {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PxxError(f"scorecard {path} must be a JSON object")
    verdicts = data.get("verdicts")
    if isinstance(verdicts, list):  # tolerate a list of {case_id, passed} dicts
        verdicts = {
            str(v.get("case_id")): bool(v.get("passed")) for v in verdicts if isinstance(v, dict)
        }
    if not isinstance(verdicts, dict):
        verdicts = {}
    gates = data.get("gates")
    if not isinstance(gates, dict):
        gates = {}
    return _ScorecardJSON(
        agent_version_id=str(data.get("agent_version_id", "")),
        corpus_fingerprint=str(data.get("corpus_fingerprint", "")),
        verdicts={str(k): bool(v) for k, v in verdicts.items()},
        gates={str(k): bool(v) for k, v in gates.items()},
        partition=str(data.get("partition", "")),
        metrics=dict(metrics_data) if isinstance(metrics_data := data.get("metrics"), dict) else {},
    )


def _cmd_compare(args: argparse.Namespace) -> int:
    from .improve.promotion import compare

    baseline = _load_scorecard_json(args.baseline)
    candidate = _load_scorecard_json(args.candidate)
    verdict = compare(baseline, candidate, human_override=args.human_override)
    print(verdict.reason)
    print(f"route: {verdict.route} (bars: {', '.join(verdict.required_bars)})")
    if verdict.gained:
        print("gained: " + ", ".join(verdict.gained))
    if verdict.lost:
        print("lost: " + ", ".join(verdict.lost))
    if verdict.hard_gate_failures:
        print("hard-gate failures: " + ", ".join(verdict.hard_gate_failures))
    if verdict.metric_failures:
        print("metric regressions: " + "; ".join(verdict.metric_failures))
    for name, detail in sorted(verdict.metrics_report.items()):
        print(f"metric {name}: {detail}")
    if verdict.override_refused_hard_gate:
        print("human override REFUSED (hard-gate failure is absolute)", file=sys.stderr)
    return 0 if verdict.promoted else 2


def _passing_promotion(state_dir: Path, version_id: str) -> dict | None:
    """Return the promotion record proving ``version_id`` may go stable.

    A passing record names the version (as candidate_id or record id) and
    carries ALL hard gates as real ``True`` values. Anything else — missing
    record, missing gates, a False gate — is not evidence (fail closed).
    """
    import json

    from .improve.promotion import HARD_GATES

    prom_dir = state_dir / "promotions"
    try:
        files = sorted(prom_dir.glob("*.json"))
    except OSError:
        return None
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("candidate_id") != version_id and data.get("id") != version_id:
            continue
        gates = data.get("gates")
        if isinstance(gates, dict) and all(gates.get(g) is True for g in HARD_GATES):
            return data
    return None


def _cmd_agent(args: argparse.Namespace) -> int:
    from .improve.channels import Channel, ChannelManager

    manager = ChannelManager(_state_dir())
    command = args.agent_command
    if command == "channels":
        for channel in (
            Channel.STABLE,
            Channel.CANDIDATE,
            Channel.SHADOW,
            Channel.CANARY,
        ):
            print(f"{channel}: {manager.current(channel) or '-'}")
        retired = manager.retired()
        print(f"retired: {', '.join(retired) if retired else '-'}")
        return 0
    if command == "canary":
        status = manager.canary_status()
        if status.agent_version_id is None:
            print("no canary active")
            return 0
        print(f"canary: {status.agent_version_id}")
        print(f"runs={status.runs} green={status.green} failures={status.failures}")
        print("eligible_to_advance: " + ("yes" if status.eligible_to_advance else "no"))
        return 0
    if command == "activate":
        if (
            args.channel == "stable"
            and _passing_promotion(manager.state_dir, args.agent_version_id) is None
        ):
            print(
                f"pxx: refusing to activate stable <- {args.agent_version_id}: "
                "no passing promotion record (all hard gates green) for this "
                "version; run `pxx promote` with a real scorecard first",
                file=sys.stderr,
            )
            return 2
        manager.activate(args.channel, args.agent_version_id)
        print(f"{args.channel} <- {args.agent_version_id}")
        return 0
    if command == "rollback":
        previous = manager.rollback()
        if previous is None:
            print("pxx: nothing to roll back to", file=sys.stderr)
            return 1
        print(f"stable <- {previous} (rolled back)")
        return 0
    # history
    events = manager.history()
    if not events:
        print("no channel history")
    for event in events:
        detail = f" ({event.detail})" if event.detail else ""
        print(f"{event.ts} {event.action} {event.channel} {event.agent_version_id}{detail}")
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    import os

    from .errors import CandidateInvalid
    from .improve.candidates import read_candidate, validate_candidate
    from .improve.channels import ChannelManager
    from .improve.promotion import HARD_GATES, build_record, write_promotion_record

    state_dir = _state_dir()
    candidate_dir = state_dir / "candidates" / args.candidate_id
    if not (candidate_dir / "candidate.json").is_file():
        print(f"pxx: no such candidate: {args.candidate_id}", file=sys.stderr)
        return 1
    try:
        candidate = read_candidate(candidate_dir)
        validate_candidate(candidate)
    except CandidateInvalid as exc:
        print(f"pxx: usage: candidate invalid: {exc}", file=sys.stderr)
        return EXIT_USAGE
    approver = args.approver or os.environ.get("USER", "")
    if not approver:
        print("pxx: usage: an approver is required (--approver or $USER)", file=sys.stderr)
        return EXIT_USAGE
    # Real gate evidence is mandatory: a promotion record built on gates={}
    # would let `pxx agent activate stable` pass on nothing.
    if not args.scorecard:
        print(
            "pxx: usage: --scorecard PATH is required (real hard-gate evidence "
            "from `pxx eval report --out`)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    scorecard = _load_scorecard_json(args.scorecard)
    missing = [gate for gate in HARD_GATES if gate not in scorecard.gates]
    if missing:
        print(
            "pxx: usage: scorecard carries no hard-gate evidence for: " + ", ".join(missing),
            file=sys.stderr,
        )
        return EXIT_USAGE
    not_held = [gate for gate in HARD_GATES if scorecard.gates.get(gate) is not True]
    if not_held:
        print(
            "pxx: promotion refused — hard gates not held in the scorecard: " + ", ".join(not_held),
            file=sys.stderr,
        )
        return 2
    baseline = ChannelManager(state_dir).current("stable") or "unknown"
    record = build_record(
        args.candidate_id,
        baseline_id=baseline,
        candidate_id=args.candidate_id,
        eval_ids=candidate.evidence,
        gates=dict(scorecard.gates),
        approver=approver,
        rollback_target=baseline,
    )
    try:
        path = write_promotion_record(record, state_dir)
    except FileExistsError:
        print(
            f"pxx: promotion already recorded (append-only): {args.candidate_id}",
            file=sys.stderr,
        )
        return 1
    print(f"promotion recorded (NOT applied): {path}")
    print(f"approver={approver} baseline={baseline} rollback_target={baseline}")
    from .audit_sampling import audit_sample

    sample = audit_sample(args.candidate_id, promotion=True)
    print(f"human audit: {'FLAGGED' if sample.sampled else 'not sampled'} ({sample.reason})")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import subprocess

    from .governance import load_denylist, scan_content, scan_staged

    denylist = load_denylist(Path(args.denylist) if args.denylist else None)
    if args.require_denylist and not denylist:
        print(
            "pxx check: ARMED GATE FAILURE: the denylist is empty — the "
            "internal-hostname dimension would be silently off (the 1.3.x "
            "silent-green bug). Refusing to report clean.",
            file=sys.stderr,
        )
        return 2
    if not denylist:
        print(
            "pxx check: note: no public-denylist configured "
            "(~/.config/pxx/public-denylist); internal-hostname checks are off",
            file=sys.stderr,
        )
    if args.all_files:
        try:
            proc = subprocess.run(
                ["git", "ls-files"],
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"pxx: git ls-files failed: {exc}", file=sys.stderr)
            return 1
        if proc.returncode != 0:
            print("pxx: --all-files requires a git repository", file=sys.stderr)
            return 1
        paths = [Path.cwd() / name for name in proc.stdout.splitlines() if name.strip()]
        findings = scan_content(paths, denylist=denylist)
    else:
        findings = scan_staged(denylist=denylist)
    for finding in findings:
        print(f"{finding.path}:{finding.line}: [{finding.rule}] {finding.preview}")
    if findings:
        print(f"pxx check: {len(findings)} finding(s)", file=sys.stderr)
        return 2
    print("pxx check: clean")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """Read-only review of the current diff — no Session, no tools, no writes."""
    from .review import NativeReviewer, ReviewMode, Verdict, collect_review_diff, review_changes

    diff, dropped = collect_review_diff(
        Path.cwd(), staged=bool(args.staged), since=args.since or ""
    )
    for rel in dropped:
        print(
            f"pxx review: excluded {rel} (governance scan tripped; not uploaded)",
            file=sys.stderr,
        )
    if not diff.strip():
        print("pxx: usage: no diff to review (tree is clean)", file=sys.stderr)
        return EXIT_USAGE
    settings = load_settings(Path.cwd())
    result = asyncio.run(
        review_changes(
            diff,
            "Review the current diff for correctness, risks, and missing tests.",
            NativeReviewer(settings.model),
            ReviewMode.ADVISORY,
        )
    )
    print(f"verdict: {result.verdict}")
    for finding in result.findings:
        loc = finding.file + (f":{finding.line}" if finding.line is not None else "")
        print(f"{finding.id} [{finding.severity}] {loc} — {finding.message}")
    if result.review_error:
        print(f"pxx: review degraded: {result.review_error} (advisory)", file=sys.stderr)
    return 2 if result.verdict is Verdict.REVISE else 0


def _cmd_workflow(args: argparse.Namespace) -> int:
    from .errors import ConfigError
    from .workflow import load_workflow

    try:
        workflow = load_workflow(Path.cwd())
    except ConfigError as exc:
        print(f"pxx workflow: INVALID: {exc}", file=sys.stderr)
        return 2
    print(f"WORKFLOW.md valid (schema v{workflow.schema_version})")
    print(
        f"states: {' -> '.join(workflow.states)} (terminal: {', '.join(workflow.terminal_states)})"
    )
    print(f"commands: {', '.join(sorted(workflow.commands))}")
    print(f"permissions: {', '.join(sorted(workflow.permissions))}")
    print(f"protected paths: {len(workflow.protected_paths)} entries")
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    """Agent-legibility audit: required docs present + mirrors in sync."""
    from .errors import ConfigError
    from .protected_paths import PROTECTED_PREFIXES
    from .workflow import load_workflow

    root = Path.cwd()
    failures: list[str] = []
    for doc in ("AGENTS.md", "DESIGN.md", "WORKFLOW.md", "docs/TRUST_BOUNDARY.md"):
        if (root / doc).is_file():
            print(f"ok: {doc} present")
        else:
            failures.append(f"missing {doc}")
    try:
        workflow = load_workflow(root)
        print("ok: WORKFLOW.md valid")
        if set(workflow.protected_paths) != set(PROTECTED_PREFIXES):
            failures.append("WORKFLOW.md [protected_paths] does not mirror PROTECTED_PREFIXES")
        else:
            print("ok: WORKFLOW.md protected_paths mirror in sync")
    except ConfigError as exc:
        failures.append(f"WORKFLOW.md invalid: {exc}")
    boundary = root / "docs" / "TRUST_BOUNDARY.md"
    if boundary.is_file():
        listed: list[str] = []
        in_fence = False
        for line in boundary.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if in_fence:
                    break  # only the first fenced block is the protected list
                in_fence = True
                continue
            if in_fence and stripped.startswith("- "):
                listed.append(stripped[2:].strip())
        if tuple(listed) != PROTECTED_PREFIXES:
            failures.append("docs/TRUST_BOUNDARY.md does not mirror PROTECTED_PREFIXES")
        else:
            print("ok: TRUST_BOUNDARY.md mirror in sync")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 2
    print("context audit: clean")
    return 0


def _cmd_docs(args: argparse.Namespace) -> int:
    """Docs consistency: every backticked `pxx <verb>` in the docs exists.

    Only spans that START with ``pxx `` are command references; mid-span
    mentions and backticked prose (``pxx owns the runtime``) are skipped.
    A verb is flagged only when the span is command-shaped: the verb is
    followed by nothing, a flag, or another subcommand.
    """
    import re

    root = Path.cwd()
    failures: list[str] = []
    checked = 0
    span_re = re.compile(r"`(pxx [^`]+)`")
    docs = [root / "README.md", root / "DESIGN.md", root / "WORKFLOW.md"]
    if (root / "docs").is_dir():
        docs += sorted((root / "docs").glob("*.md"))
    for doc in docs:
        if not doc.is_file():
            continue
        for match in span_re.finditer(doc.read_text(errors="replace")):
            tokens = match.group(1).split()
            if len(tokens) < 2:
                continue
            verb = tokens[1]
            if verb.startswith("-"):  # legacy flag reference (`pxx --edit`)
                continue
            parts = verb.split("/")  # slash enumeration (`pxx ask/edit`)
            if all(part in SUBCOMMANDS for part in parts):
                checked += 1
                continue
            if "/" in verb:
                checked += 1
                failures.append(f"{doc.relative_to(root)}: documents unknown verb `pxx {verb}`")
                continue
            follower = tokens[2] if len(tokens) > 2 else ""
            command_shaped = not follower or follower.startswith("-") or follower in SUBCOMMANDS
            if command_shaped:
                checked += 1
                failures.append(f"{doc.relative_to(root)}: documents unknown verb `pxx {verb}`")
    for failure in sorted(set(failures)):
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 2
    print(f"docs check: clean ({checked} documented verbs verified)")
    return 0


_GOAL_PLANNER_PROMPT = (
    "Decompose the following goal into a task DAG. Respond with ONLY JSON of "
    'the form {"tasks": [{"id": "...", "title": "...", "scope": "...", '
    '"depends_on": ["..."], "test_command": "..."}]}. Rules: unique ids, '
    "no dependency cycles, scopes are repo-relative paths within the repo.\n\n"
    "Goal:\n"
)


def _cmd_goal(args: argparse.Namespace, unknown: list[str]) -> int:
    from dataclasses import replace

    from .goal import run_goal

    _handle_unknown_flags(unknown, "native")
    goal = _read_task(args)
    if not goal.strip():
        print("pxx: usage: a goal is required (-m/--message or stdin)", file=sys.stderr)
        return EXIT_USAGE
    settings = load_settings(Path.cwd(), _cli_overrides(args, PermissionMode.AUTO))
    backend_name = _resolve_backend_name("run", args.backend)
    planner_settings = replace(settings, permission=PermissionMode.ASK)

    async def planner(text: str) -> str:
        backend = _make_backend(backend_name, planner_settings)
        session = Session(planner_settings, backend, cwd=Path.cwd())
        outcome = await session.run(_GOAL_PLANNER_PROMPT + text)
        if outcome.code is not TerminalCode.COMPLETED:
            raise PxxError(f"planner did not complete: {outcome.code}")
        return outcome.summary

    outcome = asyncio.run(
        run_goal(
            goal,
            settings,
            cwd=Path.cwd(),
            planner=planner,
            backend_factory=lambda: _make_backend(backend_name, settings),
        )
    )
    print(f"[{outcome.code}] {outcome.summary}")
    return exit_code_for(outcome)


# ---------------------------------------------------------------------------
# entry point


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    if not argv:
        parser.print_help()
        return 0
    argv = _compat_rewrite(argv)
    args, unknown = parser.parse_known_args(argv)
    command = args.command
    try:
        if command in ("ask", "edit", "plan", "run"):
            return _cmd_run_like(args, unknown)
        if command == "loop":
            return _cmd_loop(args, unknown)
        if command == "chat":
            return _cmd_chat(args, unknown)
        if command == "memory":
            return _cmd_memory(args)
        if command == "mcp":
            return _cmd_mcp()
        if command == "serve":
            return _cmd_serve(args)
        if command == "doctor":
            return _cmd_doctor()
        if command == "upgrade":
            return _cmd_upgrade()
        if command == "audit":
            return _cmd_audit(args)
        if command == "runs":
            return _cmd_runs(args)
        if command == "agents":
            return _cmd_agents(args)
        if command == "verify":
            return _cmd_verify(args)
        if command == "metrics":
            return _cmd_metrics(args)
        if command == "eval":
            return _cmd_eval(args)
        if command == "calibrate":
            return _cmd_calibrate(args)
        if command == "improve":
            return _cmd_improve(args)
        if command == "propose":
            return _cmd_propose(args)
        if command == "compare":
            return _cmd_compare(args)
        if command == "agent":
            return _cmd_agent(args)
        if command == "promote":
            return _cmd_promote(args)
        if command == "check":
            return _cmd_check(args)
        if command == "goal":
            return _cmd_goal(args, unknown)
        if command == "review":
            return _cmd_review(args)
        if command == "workflow":
            return _cmd_workflow(args)
        if command == "context":
            return _cmd_context(args)
        if command == "docs":
            return _cmd_docs(args)
    except KeyboardInterrupt:
        print("\npxx: interrupted", file=sys.stderr)
        return 130
    except PxxError as exc:
        print(f"pxx: error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
