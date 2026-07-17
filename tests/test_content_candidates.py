"""Tests for pxx.content_candidates — the content change-class (#016).

The safety spine is requirement #1: validate-path, write-path, and the
post-write verify-path all derive from ONE value. These tests exercise that
against a real git repo, plus the adversarial path shapes a content diff
carries."""

from __future__ import annotations

import subprocess

from pxx.content_candidates import (
    ContentCandidate,
    apply_content_candidate,
    changed_paths,
    validate_content_candidate,
    verify_only_touched_target,
)


def _cc(**kw):
    base = dict(
        candidate_id="cc-1",
        target="pxx/prompts/system.md",
        content="You are a careful editor.\n",
        baseline_sha256=None,
        rationale="tighten the editor prompt (measured)",
        from_observation="obs-edit-format-failures",
    )
    base.update(kw)
    return ContentCandidate(**base)


class TestValidateContentCandidate:
    def test_prompt_target_passes(self):
        assert validate_content_candidate(_cc()).ok

    def test_command_target_passes(self):
        assert validate_content_candidate(_cc(target="pxx/commands/refactor.md")).ok

    def test_protected_target_rejected(self):
        for t in ("pxx/review_gate.py", "pxx/evaluation.py", "evals/micro/m1.toml"):
            r = validate_content_candidate(_cc(target=t))
            assert not r.ok, t

    def test_source_target_rejected_not_behavior_text(self):
        r = validate_content_candidate(_cc(target="pxx/duration.py"))
        assert not r.ok and any("behavior text" in x for x in r.reasons)

    def test_traversal_into_protected_space_rejected(self):
        # The requirement-#1 win: one normalization catches the escape — a
        # prompt-looking target that resolves into the evaluator.
        r = validate_content_candidate(_cc(target="pxx/prompts/../review_gate.py"))
        assert not r.ok

    def test_absolute_target_rejected(self):
        assert not validate_content_candidate(_cc(target="/etc/prompt.md")).ok

    def test_empty_content_rejected(self):
        assert not validate_content_candidate(_cc(content="   ")).ok

    def test_missing_evidence_rejected(self):
        assert not validate_content_candidate(_cc(from_observation="")).ok


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "pxx" / "prompts").mkdir(parents=True)
    (tmp_path / "pxx" / "prompts" / "system.md").write_text("old prompt\n")
    (tmp_path / "pxx" / "review_gate.py").write_text("# the grader\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base", "--no-verify")
    return tmp_path


class TestApplyAndVerify:
    def test_apply_writes_the_one_canonical_target(self, tmp_path):
        repo = _repo(tmp_path)
        dest = apply_content_candidate(repo, _cc(content="new prompt\n"))
        assert dest.read_text() == "new prompt\n"
        assert dest == repo / "pxx/prompts/system.md"

    def test_verify_clean_when_only_target_touched(self, tmp_path):
        repo = _repo(tmp_path)
        apply_content_candidate(repo, _cc(content="new prompt\n"))
        assert verify_only_touched_target(repo, _cc()) == []

    def test_verify_catches_a_protected_file_also_changed(self, tmp_path):
        # Simulate a candidate/write that ALSO touched the evaluator. The
        # verify derives paths from git --name-only, not the candidate's
        # claim, so it catches this regardless of what the candidate declared.
        repo = _repo(tmp_path)
        apply_content_candidate(repo, _cc(content="new prompt\n"))
        (repo / "pxx" / "review_gate.py").write_text("# TAMPERED\n")
        violations = verify_only_touched_target(repo, _cc())
        assert any("protected path" in v and "review_gate" in v for v in violations)

    def test_verify_catches_an_unexpected_extra_file(self, tmp_path):
        repo = _repo(tmp_path)
        apply_content_candidate(repo, _cc(content="new prompt\n"))
        (repo / "pxx" / "prompts" / "other.md").write_text("stray\n")
        violations = verify_only_touched_target(repo, _cc())
        assert any("unexpected path" in v for v in violations)

    def test_changed_paths_reads_from_git(self, tmp_path):
        repo = _repo(tmp_path)
        apply_content_candidate(repo, _cc(content="x\n"))
        assert "pxx/prompts/system.md" in changed_paths(repo)

    def test_apply_refuses_invalid_candidate(self, tmp_path):
        repo = _repo(tmp_path)
        import pytest

        with pytest.raises(ValueError):
            apply_content_candidate(repo, _cc(target="pxx/review_gate.py"))
        # and the protected file was NOT written
        assert (repo / "pxx/review_gate.py").read_text() == "# the grader\n"


class TestRequirementOneEquivalence:
    """validate-path, write-path, verify-path derive from ONE value."""

    def test_write_and_verify_agree_on_the_same_canonical_target(self, tmp_path):
        repo = _repo(tmp_path)
        # A target with a ./ that normalizes to the same canonical path.
        c = _cc(target="./pxx/prompts/system.md", content="v2\n")
        dest = apply_content_candidate(repo, c)
        assert dest == repo / "pxx/prompts/system.md"
        # verify (git --name-only derived) sees exactly that path -> clean
        assert verify_only_touched_target(repo, c) == []
