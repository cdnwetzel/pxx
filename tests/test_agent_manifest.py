"""Tests for pxx.agent_manifest — behavior identity (#011 minimum)."""

from __future__ import annotations

import dataclasses

from pxx import agent_manifest


def _mf(**overrides):
    base = dict(
        editor_backend="vllm",
        editor_model="openai/test-model",
        max_rounds=3,
        max_seconds=1800.0,
        diff_budget=150,
    )
    base.update(overrides)
    return agent_manifest.current_manifest(**base)


class TestCurrentManifest:
    def test_fields_populated(self, monkeypatch):
        monkeypatch.setenv("PXX_REVIEW_BACKEND", "local")
        monkeypatch.setenv("PXX_REVIEW_MODEL", "reviewer-7b")
        mf = _mf()
        assert mf.manifest_version == agent_manifest.MANIFEST_VERSION
        assert mf.editor_model == "openai/test-model"
        assert mf.reviewer_model == "reviewer-7b"
        assert len(mf.edit_prompt_hash) == 16
        assert len(mf.healing_prompt_hash) == 16
        assert len(mf.review_prompt_hash) == 16

    def test_no_urls_or_paths_in_manifest(self, monkeypatch):
        monkeypatch.setenv("PXX_REVIEW_URL", "http://127.0.0.1:9999")
        mf = _mf()
        for value in dataclasses.asdict(mf).values():
            assert "http" not in str(value)
            assert "/Users/" not in str(value)


class TestAgentVersionId:
    def test_same_config_same_id(self, monkeypatch):
        monkeypatch.setenv("PXX_REVIEW_BACKEND", "local")
        a = agent_manifest.agent_version_id(_mf())
        b = agent_manifest.agent_version_id(_mf())
        assert a == b
        assert a.startswith("agent-") and len(a) == len("agent-") + 12

    def test_any_field_change_changes_id(self, monkeypatch):
        monkeypatch.setenv("PXX_REVIEW_BACKEND", "local")
        base = agent_manifest.agent_version_id(_mf())
        assert agent_manifest.agent_version_id(_mf(editor_model="other")) != base
        assert agent_manifest.agent_version_id(_mf(max_rounds=4)) != base
        assert agent_manifest.agent_version_id(_mf(max_seconds=900.0)) != base

    def test_reviewer_env_change_changes_id(self, monkeypatch):
        monkeypatch.setenv("PXX_REVIEW_MODEL", "model-a")
        a = agent_manifest.agent_version_id(_mf())
        monkeypatch.setenv("PXX_REVIEW_MODEL", "model-b")
        b = agent_manifest.agent_version_id(_mf())
        assert a != b

    def test_review_mode_changes_id(self, monkeypatch):
        monkeypatch.setenv("PXX_REVIEW_MODE", "blocking")
        blocking = agent_manifest.agent_version_id(_mf())
        monkeypatch.setenv("PXX_REVIEW_MODE", "advisory")
        advisory = agent_manifest.agent_version_id(_mf())
        assert blocking != advisory
