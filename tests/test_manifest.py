"""Tests for pxx.manifest: agent_version_id determinism + RunDirWriter."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from pxx.config import ModelRef, Settings
from pxx.manifest import (
    AgentManifest,
    RunDirWriter,
    build_manifest,
    hash_prompts,
)
from pxx.safety import Budgets, PermissionMode


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = Settings(
        model=ModelRef(provider="ollama", model="test-model"),
        permission=PermissionMode.AUTO,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    return replace(base, **overrides) if overrides else base


# --- agent_version_id determinism --------------------------------------------


def test_same_config_same_agent_version_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = build_manifest(settings, "mock", prompts_dir=tmp_path / "none")
    second = build_manifest(settings, "mock", prompts_dir=tmp_path / "none")
    assert first.agent_version_id == second.agent_version_id
    assert len(first.agent_version_id) == 16


def test_budget_change_changes_agent_version_id(tmp_path: Path) -> None:
    base = build_manifest(_settings(tmp_path), "mock", prompts_dir=tmp_path / "none")
    tighter = build_manifest(
        _settings(tmp_path, budgets=Budgets(max_rounds=5)),
        "mock",
        prompts_dir=tmp_path / "none",
    )
    assert base.agent_version_id != tighter.agent_version_id


def test_model_change_changes_agent_version_id(tmp_path: Path) -> None:
    base = build_manifest(_settings(tmp_path), "mock", prompts_dir=tmp_path / "none")
    other = build_manifest(
        _settings(tmp_path, model=ModelRef(provider="ollama", model="other-model")),
        "mock",
        prompts_dir=tmp_path / "none",
    )
    assert base.agent_version_id != other.agent_version_id


def test_canonical_form_has_no_secrets_or_urls(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        model=ModelRef(
            provider="openai",
            model="gpt-4o",
            base_url="https://api.openai.com",
            api_key="sk-secret",
        ),
    )
    manifest = build_manifest(settings, "native", prompts_dir=tmp_path / "none")
    blob = json.dumps(manifest.canonical(), sort_keys=True)
    assert "sk-secret" not in blob
    assert "https://api.openai.com" not in blob
    assert str(tmp_path) not in blob


def test_prompt_hashes_feed_identity(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "a.md").write_text("prompt v1")
    manifest = build_manifest(_settings(tmp_path), "mock", prompts_dir=prompts)
    assert manifest.prompt_hashes == hash_prompts(prompts)
    (prompts / "a.md").write_text("prompt v2")
    changed = build_manifest(_settings(tmp_path), "mock", prompts_dir=prompts)
    assert changed.agent_version_id != manifest.agent_version_id


def test_hash_prompts_missing_dir_degrades_to_empty(tmp_path: Path) -> None:
    assert hash_prompts(tmp_path / "does-not-exist") == {}


def test_packaged_prompts_hash_deterministically() -> None:
    assert hash_prompts() == hash_prompts()


# --- RunDirWriter round-trip ---------------------------------------------------


def test_run_dir_writer_round_trip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manifest = build_manifest(settings, "mock", prompts_dir=tmp_path / "none")
    writer = RunDirWriter.open(settings.state_dir, "20260701T000000Z-deadbeef")

    writer.write_manifest(manifest)
    writer.write_task({"run_id": "x", "task": "do it", "memory": False})
    writer.append_event({"kind": "session_start", "data": {"run_id": "x"}, "seq": 1})
    writer.append_event({"kind": "gate_decision", "data": {"gate": "tests"}, "seq": 2})
    writer.write_outcome({"code": "COMPLETED", "rounds": 1})
    writer.write_diff("--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+x\n")

    run_dir = settings.state_dir / "runs" / "20260701T000000Z-deadbeef"
    stored = json.loads((run_dir / "manifest.json").read_text())
    assert stored["agent_version_id"] == manifest.agent_version_id
    assert stored["backend"] == "mock"
    assert json.loads((run_dir / "task.json").read_text())["task"] == "do it"
    events = (run_dir / "events.jsonl").read_text().splitlines()
    assert len(events) == 2
    assert json.loads(events[1])["kind"] == "gate_decision"
    assert json.loads((run_dir / "outcome.json").read_text())["code"] == "COMPLETED"
    assert (run_dir / "diff.patch").read_text().startswith("--- a/f")


def test_run_dir_writer_is_best_effort_on_broken_dir(tmp_path: Path) -> None:
    """Every write swallows failures — telemetry must never raise."""
    blocker = tmp_path / "state" / "runs"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a dir")  # runs/ is a file: mkdir + writes fail
    writer = RunDirWriter.open(tmp_path / "state", "r1")
    manifest = AgentManifest(
        pxx_version="0",
        backend="b",
        provider="p",
        model="m",
        python_version="3",
        prompt_hashes={},
        settings_hash="h",
        budgets=Budgets(),
        review_mode="blocking",
    )
    writer.write_manifest(manifest)
    writer.write_task({"task": "t"})
    writer.append_event({"kind": "session_start"})
    writer.write_outcome({"code": "COMPLETED"})
    writer.write_diff("patch")


def test_run_dir_writer_open_creates_dirs(tmp_path: Path) -> None:
    writer = RunDirWriter.open(tmp_path / "deep" / "state", "run-1")
    assert writer.run_dir.is_dir()
    assert writer.run_dir == tmp_path / "deep" / "state" / "runs" / "run-1"


# --- B1.5: workflow + protected-paths hashes feed agent_version_id --------------


def test_workflow_edit_changes_agent_version_id(tmp_path: Path) -> None:
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("# contract v1\n")
    first = build_manifest(_settings(tmp_path), "mock", workflow_path=wf)
    assert first.workflow_hash
    wf.write_text("# contract v2 (edited)\n")
    second = build_manifest(_settings(tmp_path), "mock", workflow_path=wf)
    assert first.agent_version_id != second.agent_version_id


def test_unrelated_edit_does_not_change_agent_version_id(tmp_path: Path) -> None:
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("# contract\n")
    other = tmp_path / "NOTES.md"
    other.write_text("v1\n")
    first = build_manifest(_settings(tmp_path), "mock", workflow_path=wf)
    other.write_text("v2 — unrelated edit\n")
    second = build_manifest(_settings(tmp_path), "mock", workflow_path=wf)
    assert first.agent_version_id == second.agent_version_id


def test_protected_paths_change_changes_agent_version_id(tmp_path: Path, monkeypatch) -> None:
    first = build_manifest(_settings(tmp_path), "mock")
    assert first.protected_paths_hash
    monkeypatch.setattr("pxx.protected_paths.PROTECTED_PREFIXES", ("pxx/safety.py", "pxx/extra.py"))
    second = build_manifest(_settings(tmp_path), "mock")
    assert first.agent_version_id != second.agent_version_id


# --- B2.5: drift sentinels (ModelFingerprint / ACI / Context) --------------------


def test_probe_ollama_fingerprint_from_tags():
    import asyncio

    import httpx

    from pxx.manifest import probe_model_fingerprint

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen3:30b",
                        "digest": "sha256:abc123",
                        "modified_at": "2026-07-01T00:00:00Z",
                    }
                ]
            },
        )

    model = ModelRef(provider="ollama", model="qwen3:30b", base_url="http://x.local")
    fp = asyncio.run(probe_model_fingerprint(model, transport=httpx.MockTransport(handler)))
    assert fp.digest == "sha256:abc123"
    assert fp.hash


def test_probe_openai_compatible_fingerprint():
    import asyncio

    import httpx

    from pxx.manifest import probe_model_fingerprint

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})

    model = ModelRef(provider="openai", model="gpt-4o", base_url="http://x.local")
    fp = asyncio.run(probe_model_fingerprint(model, transport=httpx.MockTransport(handler)))
    assert fp.resolved_id == "gpt-4o"
    assert fp.hash


def test_probe_failure_degrades_to_empty():
    import asyncio

    import httpx

    from pxx.manifest import probe_model_fingerprint

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    model = ModelRef(provider="ollama", model="m", base_url="http://x.local")
    fp = asyncio.run(probe_model_fingerprint(model, transport=httpx.MockTransport(handler)))
    assert fp.hash == ""  # telemetry degrades, never gates


def test_same_name_new_digest_trips_new_agent_version_id(tmp_path: Path) -> None:
    from pxx.manifest import ModelFingerprint

    settings = _settings(tmp_path)
    old = build_manifest(
        settings,
        "mock",
        fingerprint=ModelFingerprint("ollama", "qwen3:30b", digest="sha256:OLD"),
    )
    new = build_manifest(
        settings,
        "mock",
        fingerprint=ModelFingerprint("ollama", "qwen3:30b", digest="sha256:NEW"),
    )
    assert old.model_fingerprint != new.model_fingerprint
    assert old.agent_version_id != new.agent_version_id


def test_aci_hash_changes_with_tool_set(tmp_path: Path, monkeypatch) -> None:
    from pxx.manifest import aci_hash

    base = aci_hash("")

    class TinyRegistry:
        def specs(self):
            return []

    monkeypatch.setattr("pxx.tools.default_registry", lambda: TinyRegistry())
    reduced = aci_hash("")
    assert base != reduced


def test_aci_hash_changes_with_workflow() -> None:
    from pxx.manifest import aci_hash

    assert aci_hash("aaa") != aci_hash("bbb")


def test_context_hash_changes_with_prompts(tmp_path: Path) -> None:
    from pxx.manifest import context_hash

    settings = _settings(tmp_path)
    assert context_hash(settings, {"a.md": "111"}) != context_hash(settings, {"a.md": "222"})


def test_quarantined_agents_marks_older_fingerprint(tmp_path: Path) -> None:
    import json as _json

    from pxx.runs import quarantined_agents

    state = tmp_path / "state"
    for run_id, fp, ts in (
        ("run-old", "fp-old", 1.0),
        ("run-new", "fp-new", 2.0),
    ):
        run_dir = state / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            _json.dumps(
                {
                    "agent_version_id": f"agent-{fp}",
                    "backend": "mock",
                    "model": "qwen3:30b",
                    "model_fingerprint": fp,
                }
            )
        )
        (run_dir / "outcome.json").write_text(
            _json.dumps(
                {
                    "run_id": run_id,
                    "agent_version_id": f"agent-{fp}",
                    "code": "COMPLETED",
                    "ts": ts,
                }
            )
        )
    quarantined = quarantined_agents(state)
    assert quarantined == {"agent-fp-old"}  # the re-pulled bytes supersede
