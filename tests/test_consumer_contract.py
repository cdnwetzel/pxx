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


def test_the_summary_tags_satisfy_psoperator(monkeypatch, tmp_path, capsys) -> None:
    """psoperator harness/adapters.py:22-26 reads the LAST `[net: ...]` and
    `[committed <sha>]` markers from the coder's output."""
    outcome = RunOutcome(
        code=TerminalCode.COMPLETED,
        summary="done [net: pxx-pre/20261018T000000Z+stash] [committed abcd1234]",
        session_id="abc123",
    )
    line = _cli_outcome_line(monkeypatch, tmp_path, capsys, "run", outcome)
    nets = re.findall(r"\[net:\s*([^\]]+)\]", line)
    commits = re.findall(r"\[committed\s+([0-9a-f]+)\]", line)
    assert nets[-1].strip() == "pxx-pre/20261018T000000Z+stash"
    assert commits[-1] == "abcd1234"


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


def test_the_audit_chain_verifies_with_acps_algorithm(tmp_path) -> None:
    """acp pipeline/worker.py:228-306 re-implements pxx's audit chain:
    `<state_dir>/audit/<local-date>.jsonl`, `hash = sha256(prev + json.dumps(
    event, sort_keys=True))`, and a `.head` of {count, hash}. pxx's writer
    (events.AuditLog) must keep producing exactly that."""
    log = AuditLog(tmp_path, "abc123")
    bus = EventBus()
    bus.subscribe(log.record)
    data = dict.fromkeys(_ACP_REQUIRED, True) | {"tool_count": 0, "payload_mode": "bare"}
    asyncio.run(bus.emit("session_start", {"run_id": "r"}, session_id="abc123"))
    asyncio.run(bus.emit("prompt_rendered", data, session_id="abc123"))

    files = sorted((tmp_path / "audit").glob("*.jsonl"))
    assert len(files) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", files[0].name)
    prev, count, found = "0" * 64, 0, None
    for raw in files[0].read_text().splitlines():
        rec = json.loads(raw)
        assert rec["prev_hash"] == prev
        digest = hashlib.sha256((prev + json.dumps(rec["event"], sort_keys=True)).encode())
        assert rec["hash"] == digest.hexdigest()
        prev, count = rec["hash"], count + 1
        ev = rec["event"]
        if ev.get("kind") == "prompt_rendered" and ev.get("session_id") == "abc123":
            found = ev["data"]
    head = json.loads(files[0].with_suffix(".head").read_text())
    assert head["count"] == count and head["hash"] == prev
    assert found is not None and all(k in found for k in _ACP_REQUIRED)


# --- 3. The gate_decision invariant --------------------------------------------------


#: dx learn_sources.py:78-84 -- gates allowed to carry no verdict.
_DX_NON_VERDICT_GATES = frozenset({"transport_retry", "fallback"})


def test_every_gate_decision_literal_carries_allowed() -> None:
    """dx src/dx/learn_sources.py:126-138 QUARANTINES a whole run when any
    `gate_decision` lacks a boolean `allowed` (except the two resilience
    notices). Every dict literal in pxx that names a `gate` must carry it."""
    offenders = []
    for path in sorted(_PXX.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                k.value: v
                for k, v in zip(node.keys, node.values, strict=True)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            gate = keys.get("gate")
            if not (isinstance(gate, ast.Constant) and isinstance(gate.value, str)):
                continue
            if gate.value in _DX_NON_VERDICT_GATES:
                continue
            if "allowed" not in keys:
                offenders.append(f"{path.relative_to(_PXX.parent)}:{node.lineno} {gate.value}")
    assert offenders == [], offenders


def test_the_gate_decision_sweep_sees_real_emitters() -> None:
    """Guard against a vacuous sweep: it must find the gates pxx is known to
    emit, so a refactor that hides them from the AST scan fails here."""
    seen = set()
    for path in _PXX.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "gate"
                        and isinstance(v, ast.Constant)
                    ):
                        seen.add(v.value)
    for gate in ("tests", "review", "scope_recheck", "diff_complete", "safety_net", "scope"):
        assert gate in seen, gate


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
