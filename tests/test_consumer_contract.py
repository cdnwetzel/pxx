"""Consumer contract: what other projects read from pxx, pinned in one place.

pxx is consumed by the downstream tools named below. Each test names the
consumer code that depends on the surface it pins, so a failure says who
breaks. Changing one of these surfaces is allowed only deliberately: update the
test, note it under "downstream" in CHANGELOG.md, and fix or coordinate the
consumer first.

The parsers and algorithms below are copied from the consumers on purpose: the
point is that pxx's real output satisfies the consumer's real check.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_backends_native import completion, make_backend, make_ctx

from pxx.events import AuditLog, EventBus
from pxx.outcome import RunOutcome, TerminalCode

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")
_PXX = Path(__file__).resolve().parent.parent / "pxx"


# --- 1. The outcome line ----------------------------------------------------------


def _cli_outcome_line(monkeypatch, tmp_path, capsys, command: str, outcome: RunOutcome) -> str:
    """What `pxx <command>` prints for ``outcome``, through the real CLI path."""
    import pxx.cli as cli

    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(tmp_path / key.lower()))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_make_backend", lambda name, settings: object())
    monkeypatch.setattr(cli, "_run_session", lambda settings, backend, task: outcome)
    cli.main([command, "-m", "contract task"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[")]
    assert lines, "no outcome line printed"
    return lines[-1]


def test_the_run_outcome_trailer_satisfies_acp(monkeypatch, tmp_path, capsys) -> None:
    """acp pipeline/worker.py:205-226 finds the session id in the
    `(rounds= tokens= diff_lines= session=)` trailer, which must be the LAST
    thing on the line, in that field order."""
    outcome = RunOutcome(
        code=TerminalCode.COMPLETED,
        summary="done [net: pxx-pre/20261018T000000Z+stash]",
        rounds=2,
        tokens=40,
        diff_lines=3,
        session_id="abc123",
    )
    line = _cli_outcome_line(monkeypatch, tmp_path, capsys, "run", outcome)
    acp_trailer = re.compile(
        r"\(\s*rounds=\d+\s+tokens=\d+\s+diff_lines=\d+\s+session=([0-9a-f]+)\s*\)\s*$"
    )
    m = acp_trailer.search(line)
    assert m and m.group(1) == "abc123", line
    assert line.startswith("[COMPLETED]")  # acp worker.py:208 prefixes
    assert re.search(r"\bsession=([0-9a-f]+)\b", line).group(1) == "abc123"


_PSO_NET = re.compile(r"\[net:\s*([^\]]+)\]")  # psoperator harness/adapters.py:24
_PSO_COMMITTED = re.compile(r"\[committed\s+([0-9a-f]+)\]")  # adapters.py:25


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _assert_psoperator_tags(summary: str, repo: Path) -> None:
    """psoperator takes the LAST `[net: ...]` (dropping `+stash`) as the rewind
    ref and the LAST `[committed <sha>]` as the commit: both must be real."""
    nets, commits = _PSO_NET.findall(summary), _PSO_COMMITTED.findall(summary)
    assert nets and commits, summary
    ref = nets[-1].strip().removesuffix("+stash")
    _git_out(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")  # a real ref
    assert _git_out(repo, "rev-parse", "HEAD").startswith(commits[-1])


@needs_git
def test_a_committing_session_emits_the_tags_psoperator_reads(tmp_path) -> None:
    """psoperator harness/adapters.py:22-26 parses the tags pxx's SESSION
    writes (pxx/session.py: the net suffix and `[committed <sha>]`), from the
    real producer -- not a hand-written summary."""
    from dataclasses import replace

    from test_session import MockBackend, Session, _git_repo, _settings

    repo = tmp_path / "repo"
    _git_repo(repo)
    (repo / "wip.txt").write_text("operator dirt\n")  # so the net stashes: `+stash`
    for key, val in (("user.name", "t"), ("user.email", "t@e.c")):
        _git_out(repo, "config", key, val)
    backend = MockBackend(
        [{"tool": "write_file", "args": {"path": "a.txt", "content": "x\n"}}, {"done": "ok"}]
    )
    outcome = asyncio.run(
        Session(replace(_settings(tmp_path), auto_commit=True), backend, cwd=repo).run("edit")
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "+stash]" in outcome.summary
    _assert_psoperator_tags(outcome.summary, repo)


@needs_git
def test_a_committing_loop_emits_the_tags_psoperator_reads(tmp_path) -> None:
    """psoperator drives `pxx loop --commit`; the loop writes its own net
    suffix and `[committed <sha>]` (pxx/loop.py) -- pinned from the real loop."""
    from dataclasses import replace

    from test_loop import Factory, ScriptedBackend, ScriptedReviewer, _init_repo, _settings

    from pxx.loop import run_loop

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    for key, val in (("user.name", "t"), ("user.email", "t@e.c")):
        _git_out(repo, "config", key, val)
    outcome = asyncio.run(
        run_loop(
            "task",
            replace(_settings(tmp_path), auto_commit=True),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="true",
            reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    _assert_psoperator_tags(outcome.summary, repo)


# --- 2. The audit receipt ------------------------------------------------------------


_ACP_REQUIRED = ("payload_mode", "system_message_present", "tools_key_present", "tool_count")
_ACP_OPTIONAL = ("response_format_present", "response_format_sha256", "json_schema_name")


def test_prompt_rendered_carries_the_fields_acp_reads(tmp_path) -> None:
    """acp pipeline/worker.py:255-274 takes these keys from the native
    backend's `prompt_rendered` event (pxx backends/native.py:276)."""

    def handler(request):
        import httpx

        return httpx.Response(200, json=completion("ok"))

    ctx = make_ctx(tmp_path)
    asyncio.run(make_backend(handler).run("hi", ctx))
    [event] = [e for e in ctx.bus.history if e.kind == "prompt_rendered"]
    for key in _ACP_REQUIRED + _ACP_OPTIONAL:
        assert key in event.data, key
    assert event.data["payload_mode"] in ("bare", "default")


def _acp_payload_receipt(state_dir: Path, session_id: str, today) -> dict:
    """acp pipeline/worker.py:235-279 (payload_receipt), the lookup half: only
    TODAY and YESTERDAY by LOCAL date, then the session's event. ``today`` is
    injected so the test can freeze ACP's clock the same way as pxx's."""
    import datetime

    for day in (today, today - datetime.timedelta(days=1)):
        path = state_dir / "audit" / f"{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        _acp_verify_chain(path)
        for line in path.read_text().splitlines():
            ev = json.loads(line).get("event") or {}
            if ev.get("kind") == "prompt_rendered" and ev.get("session_id") == session_id:
                return ev.get("data") or {}
    raise AssertionError(f"ACP's lookup finds no receipt under {state_dir}/audit")


def _acp_verify_chain(path: Path) -> None:
    """acp pipeline/worker.py:281-306 (_verify_pxx_audit)."""
    prev, count = "0" * 64, 0
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        rec = json.loads(raw)
        assert rec.get("prev_hash") == prev
        payload = json.dumps(rec["event"], sort_keys=True)
        digest = hashlib.sha256((prev + payload).encode()).hexdigest()
        assert rec.get("hash") == digest
        prev, count = digest, count + 1
    head = json.loads(path.with_suffix(".head").read_text())
    assert head.get("count") == count and head.get("hash") == prev


def test_acps_receipt_lookup_finds_pxxs_audit_log(tmp_path, monkeypatch) -> None:
    """acp pipeline/worker.py:228-306 re-implements pxx's audit chain and finds
    the log by LOCAL date (today, then yesterday). The clock is frozen at
    05:00 UTC in UTC-12, where the local date is the day BEFORE UTC's: a writer
    that named its file by UTC would be "tomorrow" to ACP and fail here. (A
    yesterday-tolerant lookup hides a UTC writer in zones AHEAD of UTC, which
    is why the zone is behind it.)"""
    import calendar
    import datetime
    import time as _real_time
    import types

    import pxx.events as events_mod

    frozen = calendar.timegm((2026, 10, 18, 5, 0, 0))
    monkeypatch.setenv("TZ", "Etc/GMT+12")  # POSIX sign: UTC-12
    _real_time.tzset()
    try:
        fake = types.SimpleNamespace(
            **{k: getattr(_real_time, k) for k in dir(_real_time) if not k.startswith("__")}
        )
        fake.time = lambda: frozen
        fake.localtime = lambda t=None: _real_time.localtime(frozen if t is None else t)
        fake.gmtime = lambda t=None: _real_time.gmtime(frozen if t is None else t)
        fake.strftime = lambda fmt, t=None: _real_time.strftime(
            fmt, _real_time.localtime(frozen) if t is None else t
        )
        monkeypatch.setattr(events_mod, "time", fake)
        log = AuditLog(tmp_path, "abc123")
        bus = EventBus()
        bus.subscribe(log.record)
        data = {k: True for k in _ACP_REQUIRED} | {"tool_count": 0, "payload_mode": "bare"}
        asyncio.run(bus.emit("session_start", {"run_id": "r"}, session_id="abc123"))
        asyncio.run(bus.emit("prompt_rendered", data, session_id="abc123"))
        acp_today = datetime.date.fromtimestamp(frozen)  # ACP's date.today(), frozen
        assert acp_today.isoformat() == "2026-10-17"  # the setup really is a day behind UTC
        found = _acp_payload_receipt(tmp_path, "abc123", acp_today)
    finally:
        monkeypatch.undo()
        _real_time.tzset()
    assert all(k in found for k in _ACP_REQUIRED)


# --- 3. The gate_decision invariant --------------------------------------------------


#: dx learn_sources.py:78-84 -- gates allowed to carry no verdict.
_DX_NON_VERDICT_GATES = frozenset({"transport_retry", "fallback"})
#: Functions that forward a payload built elsewhere (safety_net.settle_failed_run
#: builds it as a literal, which the sweep checks).
_PASS_THROUGH_EMITTERS = frozenset({"_emit_net"})


def _dx_reads_gate_events(events: list[dict]) -> None:
    """dx src/dx/learn_sources.py:126-138, verbatim in effect: a gate_decision
    without a str gate, or without a BOOLEAN `allowed` (outside the two
    resilience notices), quarantines the whole run."""
    for ev in events:
        if ev.get("kind") != "gate_decision":
            continue
        data = ev.get("data")
        assert isinstance(data, dict) and isinstance(data.get("gate"), str), ev
        if "allowed" not in data:
            assert data["gate"] in _DX_NON_VERDICT_GATES, ev
            continue
        assert isinstance(data["allowed"], bool), ev


def test_every_gate_decision_emit_is_checkable_and_carries_allowed() -> None:
    """Static half: every `emit("gate_decision", X)` passes a dict LITERAL (or
    is a documented pass-through), no `dict(gate=...)` call builds one, and
    every literal naming a verdict gate has an `allowed` that is not a
    non-boolean constant. Payloads the sweep cannot read fail it."""
    offenders = []

    class Visit(ast.NodeVisitor):
        def __init__(self, path):
            self.path, self.fn = path, []

        def _where(self, node):
            return f"{self.path.relative_to(_PXX.parent)}:{node.lineno}"

        def visit_FunctionDef(self, node):
            self.fn.append(node.name)
            self.generic_visit(node)
            self.fn.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            args = node.args
            if args and isinstance(args[0], ast.Constant) and args[0].value == "gate_decision":
                payload = args[1] if len(args) > 1 else None
                if not isinstance(payload, ast.Dict) and not (
                    isinstance(payload, ast.Name)
                    and self.fn
                    and self.fn[-1] in _PASS_THROUGH_EMITTERS
                ):
                    offenders.append(f"{self._where(node)} unreadable gate_decision payload")
            if isinstance(node.func, ast.Name) and node.func.id == "dict":
                if any(k.arg == "gate" for k in node.keywords):
                    offenders.append(f"{self._where(node)} dict(gate=...) payload")
            self.generic_visit(node)

        def visit_Dict(self, node):
            keys = {
                k.value: v
                for k, v in zip(node.keys, node.values, strict=True)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            gate = keys.get("gate")
            if isinstance(gate, ast.Constant) and gate.value not in _DX_NON_VERDICT_GATES:
                allowed = keys.get("allowed")
                if allowed is None:
                    offenders.append(f"{self._where(node)} {gate.value}: no `allowed`")
                elif isinstance(allowed, ast.Constant) and not isinstance(allowed.value, bool):
                    offenders.append(f"{self._where(node)} {gate.value}: non-bool `allowed`")
            self.generic_visit(node)

    for path in sorted(_PXX.rglob("*.py")):
        Visit(path).visit(ast.parse(path.read_text(), filename=str(path)))
    assert offenders == [], offenders


@needs_git
def test_real_runs_emit_gate_decisions_dx_accepts(tmp_path) -> None:
    """Runtime half: the gate_decision events real runs write to events.jsonl
    pass dx's reader -- a COMPLETED loop, an out-of-scope loop (scope_recheck
    denied, safety-net restore) and a hook-denied session (safety_net)."""
    from dataclasses import replace

    from test_loop import Factory, ScriptedBackend, ScriptedReviewer, _init_repo, _settings
    from test_session import MockBackend, Session, _git_repo
    from test_session import _settings as _session_settings

    from pxx.loop import run_loop
    from pxx.safety import Hook

    def loop_run(name, scope, edits):
        repo = tmp_path / name
        repo.mkdir()
        _init_repo(repo, {"a.py": "x = 0\n"})
        asyncio.run(
            run_loop(
                "task",
                replace(_settings(tmp_path / f"{name}-home"), scope=scope),
                cwd=repo,
                backend_factory=Factory([ScriptedBackend(edits=edits)]),
                test_command="true",
                reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
            )
        )
        return tmp_path / f"{name}-home" / "state"

    states = [
        loop_run("done", (), {"a.py": "x = 1\n"}),
        loop_run("fenced", ("a.py",), {"a.py": "x = 1\n", "b.py": "y = 1\n"}),
    ]
    repo = tmp_path / "hooked"
    _git_repo(repo)
    settings = _session_settings(
        tmp_path / "hooked-home",
        hooks=(Hook(event="PreToolUse", command="sh -c 'exit 2'", matcher="run_shell"),),
    )
    backend = MockBackend(
        [
            {"tool": "write_file", "args": {"path": "n.txt", "content": "n\n"}},
            {"tool": "run_shell", "args": {"command": "true"}},
            {"done": "x"},
        ]
    )
    asyncio.run(Session(settings, backend, cwd=repo).run("write then shell"))
    states.append(tmp_path / "hooked-home" / "state")

    gates = set()
    for state in states:
        for events_file in (state / "runs").glob("*/events.jsonl"):
            events = [json.loads(ln) for ln in events_file.read_text().splitlines()]
            _dx_reads_gate_events(events)
            gates |= {e["data"]["gate"] for e in events if e["kind"] == "gate_decision"}
    for gate in ("tests", "review", "scope_recheck", "safety_net"):
        assert gate in gates, (gate, gates)  # the scenarios really reach these emitters


# --- 4. The loop's run directory -------------------------------------------------------


@needs_git
@pytest.mark.parametrize("intent", [None, "eval"])
def test_a_loop_run_dir_has_what_dx_reads(tmp_path, intent) -> None:
    """dx src/dx/run_facts.py:33-41,153-222 and learn_sources.py:221-343:
    - the loop dir name contains `-loop-` and its task.json has mode "loop";
    - outcome.json carries code, rounds, the test counts, test_command,
      review_seconds, unparseable_review_count, contributing_codes, ts,
      summary, and run_id EQUAL to the directory name (dx reads
      agent_version_id with a default, so it is not pinned);
    - task.json and the session_start events agree on run_intent /
      run_intent_provenance -- dx treats a disagreement as an intent
      conflict -- and a declared intent carries its provenance;
    - events.jsonl has a tests gate_decision; diff.patch is the loop's diff."""
    from dataclasses import replace

    from test_loop import Factory, ScriptedBackend, ScriptedReviewer, _init_repo, _settings

    from pxx.loop import run_loop

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    settings = _settings(tmp_path)
    asyncio.run(
        run_loop(
            "task",
            replace(settings, run_intent=intent) if intent else settings,
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="true",
            reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
        )
    )
    loops = [d for d in (tmp_path / "state" / "runs").iterdir() if "-loop-" in d.name]
    assert len(loops) == 1, loops
    d = loops[0]
    task = json.loads((d / "task.json").read_text())
    assert task["mode"] == "loop"
    assert task["run_intent"] == (intent or "unknown")
    if intent:
        assert task["run_intent_provenance"] == "declared"
    outcome = json.loads((d / "outcome.json").read_text())
    for key in (
        "code",
        "rounds",
        "baseline_failures",
        "terminal_failures",
        "introduced_failures",
        "test_seconds",
        "test_command",
        "review_seconds",
        "unparseable_review_count",
        "contributing_codes",
        "ts",
        "summary",
        "run_id",
    ):
        assert key in outcome, key
    assert outcome["run_id"] == d.name
    events = [json.loads(ln) for ln in (d / "events.jsonl").read_text().splitlines()]
    starts = [e["data"] for e in events if e["kind"] == "session_start"]
    pair = (task.get("run_intent"), task.get("run_intent_provenance"))
    assert starts
    assert all((s.get("run_intent"), s.get("run_intent_provenance")) == pair for s in starts)
    tests = [e for e in events if e["kind"] == "gate_decision" and e["data"]["gate"] == "tests"]
    assert tests and {"passed", "failing", "new_failures", "sandboxed", "allowed"} <= set(
        tests[-1]["data"]
    )
    assert (d / "diff.patch").read_text().startswith("diff --git")


# --- 5. The Python API consumers import ------------------------------------------------


def test_the_python_api_consumers_import() -> None:
    """dx imports these directly (src/dx/approval_tier.py:41, cmd_run.py:114,
    salvage.py:82, learn_bridge.py:61-62; tests/test_learn.py:471-475,
    test_learn_forward.py); sandboxed callers read Settings().memory_dir and
    .state_dir to grant write access to them."""
    from pxx.config import Settings, load_settings
    from pxx.improve.cycle import proposal_slug  # noqa: F401
    from pxx.improve.mining import Proposal, RootCause  # noqa: F401
    from pxx.improve.triage import dispose  # noqa: F401
    from pxx.loop import run_loop
    from pxx.protected_paths import PROTECTED_PREFIXES

    s = Settings()
    assert isinstance(s.memory_dir, Path) and isinstance(s.state_dir, Path)
    assert "cwd" in inspect.signature(load_settings).parameters
    assert hasattr(s, "effective_review_model")
    params = inspect.signature(run_loop).parameters
    for kw in ("backend_factory", "test_command", "reviewer", "max_rounds"):
        assert kw in params, kw
    assert isinstance(PROTECTED_PREFIXES, tuple) and PROTECTED_PREFIXES


# --- 6. The PreToolUse hook protocol ---------------------------------------------------


def test_a_pretooluse_hook_gets_tool_and_args_and_the_callers_env(tmp_path, monkeypatch) -> None:
    """A policy daemon's PreToolUse hook in a governed deployment
    reads stdin JSON {"tool", "args": {"path"|"command"}} and the env vars
    PXX_SCOPE / PXX_SESSION_ID, and denies with exit 2.

    pxx passes its OWN environment through to the hook unchanged; it does not
    set PXX_SCOPE or PXX_SESSION_ID itself -- the caller (the orchestrator that
    launches pxx) must export them. Pinned so neither side assumes the other sets them."""
    from pxx.errors import HookDenied
    from pxx.safety import Hook, HookRunner

    out = tmp_path / "hook-saw.json"
    script = tmp_path / "hook.py"
    script.write_text(
        "import json, os, sys\n"
        "payload = json.load(sys.stdin)\n"
        f"open({str(out)!r}, 'w').write(json.dumps({{'payload': payload, "
        "'scope': os.environ.get('PXX_SCOPE'), 'session': os.environ.get('PXX_SESSION_ID')}))\n"
        "sys.exit(2 if payload['args'].get('path') == 'deny.txt' else 0)\n"
    )
    monkeypatch.setenv("PXX_SCOPE", "/pilot")
    monkeypatch.setenv("PXX_SESSION_ID", "task-1-20261018")
    runner = HookRunner((Hook(event="PreToolUse", command=f"{sys.executable} {script}"),))

    asyncio.run(runner.run_pre("read_file", {"path": "ok.txt"}))
    saw = json.loads(out.read_text())
    assert saw["payload"] == {"tool": "read_file", "args": {"path": "ok.txt"}}
    assert saw["scope"] == "/pilot" and saw["session"] == "task-1-20261018"
    with pytest.raises(HookDenied):
        asyncio.run(runner.run_pre("read_file", {"path": "deny.txt"}))

    monkeypatch.delenv("PXX_SCOPE")
    asyncio.run(runner.run_pre("read_file", {"path": "ok.txt"}))
    assert json.loads(out.read_text())["scope"] is None  # pxx never invents it
