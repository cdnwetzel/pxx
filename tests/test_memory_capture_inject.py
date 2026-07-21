"""Tests for pxx.memory.capture + pxx.memory.inject.

No network, no Ollama, no aider, no git repo required.
"""

from __future__ import annotations

import asyncio

from pxx.events import Event
from pxx.memory.capture import (
    MAX_CONTENT_CHARS,
    NewObservation,
    observations_from_events,
    observations_from_git,
    record_observations,
)
from pxx.memory.embeddings import HashEmbedder
from pxx.memory.inject import FOOTER, HEADER, build_context
from pxx.memory.store import MemoryStore


def run(coro):
    return asyncio.run(coro)


def ev(kind: str, **data) -> Event:
    return Event(kind=kind, data=data, session_id="s1")


# ------------------------------------------------------------------- capture


def test_observations_from_events_rollups():
    events = [
        ev("session_start", backend="mock"),
        ev("model_request", prompt="secret"),
        ev("tool_call", tool="read_file"),
        ev("tool_result", tool="run_shell", result="3 passed, 0 failed"),
        ev("file_changed", path="pxx/store.py", summary="+12 -3"),
        ev("observation", content="tests live in tests/", tags=["layout"]),
        ev("budget", tokens=10),
        ev("session_end", code="COMPLETED"),
    ]
    obs = observations_from_events(events)
    kinds = [o.kind for o in obs]
    assert kinds == ["tool_result", "file_changed", "observation"]
    assert "run_shell" in obs[0].content
    assert obs[1].content == "changed file: pxx/store.py (+12 -3)"
    assert obs[2].tags == ("layout",)
    # noisy kinds never leak through
    assert all("secret" not in o.content for o in obs)


def test_observations_from_events_dedupes_and_caps():
    long_result = "x" * (MAX_CONTENT_CHARS + 500)
    events = [
        ev("tool_result", tool="t", result=long_result),
        ev("tool_result", tool="t", result=long_result),  # duplicate
    ]
    obs = observations_from_events(events)
    assert len(obs) == 1
    assert len(obs[0].content) <= MAX_CONTENT_CHARS


def test_observations_from_events_skips_empty_results():
    events = [ev("tool_result", tool="t", result=""), ev("file_changed")]
    assert observations_from_events(events) == []


def test_observations_from_git_outside_repo(tmp_path):
    # tmp_path is not a git repo -> robust [] (also covers missing git binary).
    assert run(observations_from_git("deadbeef", tmp_path)) == []


def test_observations_from_git_empty_sha(tmp_path):
    assert run(observations_from_git("", tmp_path)) == []


def test_record_observations_completed_session_writes_nothing(tmp_path):
    """B5.3 / Phase 20.5: a COMPLETED session is never auto-converted into
    knowledge (a success may be right for the wrong reason)."""
    store = MemoryStore(tmp_path / "memory.db")
    store.set_embedder(HashEmbedder())
    events = [
        ev("tool_result", tool="run_shell", result="all tests passed"),
        ev("file_changed", path="a.py"),
        ev("observation", content="remember this fact"),
        ev("session_end", code="COMPLETED"),
    ]
    written = run(record_observations(store, "proj", "s1", events))
    assert written == 0
    assert store.list("proj") == []
    store.close()


def test_record_observations_failed_session_writes_episodic_low_trust(tmp_path):
    """Failed sessions capture episodic observations, visibly low-trust:
    failed_run_inference provenance + contamination + EVIDENCE_RANK wiring."""
    store = MemoryStore(tmp_path / "memory.db")
    store.set_embedder(HashEmbedder())
    events = [
        ev("tool_result", tool="run_shell", result="boom"),
        ev("file_changed", path="a.py"),
        ev("session_start", agent_version_id="agent-x"),
        ev("gate_decision", gate="tests", passed=False),
        ev("session_end", code="TEST_REGRESSION"),
    ]
    written = run(record_observations(store, "proj", "s1", events))
    assert written == 2
    rows = store.list("proj")
    assert {o.layer for o in rows} == {"episodic"}
    assert all(o.provenance == "failed_run_inference" for o in rows)
    assert all(o.evidence_confidence == 0.2 for o in rows)  # EVIDENCE_RANK consulted
    assert all(o.contamination_risk == 0.5 for o in rows)
    assert all(o.outcome == "TEST_REGRESSION" for o in rows)
    assert all(o.agent_version_id == "agent-x" for o in rows)
    assert all(o.validation == "tests" for o in rows)
    store.close()


def test_record_observations_never_raises(tmp_path):
    class BrokenStore:
        async def add(self, *args, **kwargs):
            raise RuntimeError("db gone")

    events = [ev("observation", content="will not be stored")]
    written = run(record_observations(BrokenStore(), "proj", "s1", events))
    assert written == 0


def test_record_observations_bad_events_never_raises(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    # malformed "events" must not crash capture
    written = run(record_observations(store, "proj", "s1", [object(), None]))
    assert written == 0
    store.close()


# -------------------------------------------------------------------- inject


def make_store(tmp_path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory.db")
    store.set_embedder(HashEmbedder())
    return store


def test_build_context_empty_store(tmp_path):
    store = make_store(tmp_path)
    assert run(build_context(store, "proj", "anything")) == ""
    store.close()


def test_build_context_pinned_first_with_footer(tmp_path):
    store = make_store(tmp_path)

    async def go():
        await store.add("proj", "note", "always run ruff before committing", tags=("pinned",))
        await store.add("proj", "note", "the native backend uses chat completions")
        return await build_context(store, "proj", "ruff linting")

    ctx = run(go())
    assert ctx.startswith(HEADER)
    assert ctx.rstrip().endswith(FOOTER)
    pinned_pos = ctx.index("always run ruff")
    other_pos = ctx.index("native backend")
    assert pinned_pos < other_pos
    assert "- [pinned]" in ctx
    store.close()


def test_build_context_respects_budget(tmp_path):
    store = make_store(tmp_path)

    async def go():
        for i in range(20):
            await store.add(
                "proj",
                "note",
                f"observation {i}: a fairly long piece of remembered project trivia",
            )
        return await build_context(store, "proj", "project trivia", budget_tokens=60)

    ctx = run(go())
    assert ctx  # something fit
    assert len(ctx) // 4 <= 60  # hard stop at the token budget
    store.close()


def test_build_context_tiny_budget_returns_empty(tmp_path):
    store = make_store(tmp_path)
    run(store.add("proj", "note", "x" * 400))
    assert run(build_context(store, "proj", "x", budget_tokens=10)) == ""
    store.close()


def test_build_context_excludes_archived(tmp_path):
    store = make_store(tmp_path)

    async def go():
        await store.add("proj", "note", "expired trivia", ttl_days=-1.0)
        store.archive_expired()
        return await build_context(store, "proj", "trivia")

    assert run(go()) == ""
    store.close()


def test_new_observation_defaults():
    obs = NewObservation(kind="note", content="hello")
    assert obs.tags == ()
    assert obs.source == ""
    assert 0.0 < obs.confidence <= 1.0
