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
    clone_repo_for_content_eval,
    evaluate_content_candidate,
    run_content_candidate_in_fixture,
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


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


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
        applied = apply_content_candidate(repo, _cc(content="new prompt\n"))
        assert applied.dest.read_text() == "new prompt\n"
        assert applied.dest == repo / "pxx/prompts/system.md"

    def test_verify_clean_when_only_target_touched(self, tmp_path):
        repo = _repo(tmp_path)
        applied = apply_content_candidate(repo, _cc(content="new prompt\n"))
        assert verify_only_touched_target(repo, _cc(), applied.base_sha) == []

    def test_verify_catches_a_protected_file_also_changed(self, tmp_path):
        # Simulate a candidate/write that ALSO touched the evaluator. The
        # verify derives paths from git, not the candidate's claim, so it
        # catches this regardless of what the candidate declared.
        repo = _repo(tmp_path)
        applied = apply_content_candidate(repo, _cc(content="new prompt\n"))
        (repo / "pxx" / "review_gate.py").write_text("# TAMPERED\n")
        violations = verify_only_touched_target(repo, _cc(), applied.base_sha)
        assert any("protected path" in v and "review_gate" in v for v in violations)

    def test_verify_catches_an_unexpected_extra_file(self, tmp_path):
        repo = _repo(tmp_path)
        applied = apply_content_candidate(repo, _cc(content="new prompt\n"))
        (repo / "pxx" / "prompts" / "other.md").write_text("stray\n")
        violations = verify_only_touched_target(repo, _cc(), applied.base_sha)
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


class TestP1CommittedEscapesVisibleToVerify:
    """[P1] fail-open: the live sweep auto-commits, so a tree-only verify sees a
    committed escape as clean. verify diffs against the pre-write HEAD instead."""

    def test_verify_catches_a_committed_escape(self, tmp_path):
        repo = _repo(tmp_path)
        applied = apply_content_candidate(repo, _cc(content="new\n"))
        # ALSO escape to the grader, then COMMIT everything (as the loop does).
        (repo / "pxx" / "review_gate.py").write_text("# TAMPERED\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "loop round", "--no-verify")
        violations = verify_only_touched_target(repo, _cc(), applied.base_sha)
        assert any("protected path" in v and "review_gate" in v for v in violations)

    def test_verify_clean_for_a_committed_legit_write(self, tmp_path):
        repo = _repo(tmp_path)
        applied = apply_content_candidate(repo, _cc(content="new\n"))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "loop round", "--no-verify")
        assert verify_only_touched_target(repo, _cc(), applied.base_sha) == []

    def test_verify_requires_base_sha(self):
        # The pair can't be mismatched: no None default to fall through on.
        import inspect

        sig = inspect.signature(verify_only_touched_target)
        assert sig.parameters["base_sha"].default is inspect.Parameter.empty


class TestP2SymlinkWriteThrough:
    """[P2] fail-open in the write window: write_text follows symlinks, so a
    planted link in the allowlisted dir could land the write on the grader."""

    def test_apply_refuses_to_write_through_a_symlink(self, tmp_path):
        import pytest

        repo = _repo(tmp_path)
        link = repo / "pxx" / "prompts" / "x.md"
        link.symlink_to("../review_gate.py")  # planted redirect to the grader
        with pytest.raises(ValueError):
            apply_content_candidate(
                repo, _cc(target="pxx/prompts/x.md", content="pwn\n")
            )
        # the grader is untouched — caught BEFORE the write, not post-hoc
        assert (repo / "pxx/review_gate.py").read_text() == "# the grader\n"

    def test_apply_refuses_a_symlinked_parent(self, tmp_path):
        import pytest

        repo = _repo(tmp_path)
        (repo / "evil").mkdir()
        # pxx/commands is a symlink to a dir outside the allowlist
        (repo / "pxx" / "commands").symlink_to(repo / "evil")
        with pytest.raises(ValueError):
            apply_content_candidate(
                repo, _cc(target="pxx/commands/x.md", content="pwn\n")
            )
        assert not (repo / "evil" / "x.md").exists()


class TestP3CasePreservingWritePath:
    """[P3] casefolded canonical must not be the write path — System.md would
    write to system.md on a case-sensitive FS (CI is Ubuntu, eval is Linux)."""

    def test_apply_writes_declared_casing(self, tmp_path):
        repo = _repo(tmp_path)
        applied = apply_content_candidate(
            repo, _cc(target="pxx/prompts/System.md", content="x\n")
        )
        assert applied.dest.name == "System.md"  # not casefolded to system.md

    def test_canonical_repo_path_preserves_case(self):
        from pxx.protected_paths import canonical_repo_path

        assert canonical_repo_path("pxx/prompts/System.md") == "pxx/prompts/System.md"

    def test_is_protected_path_still_case_insensitive(self):
        from pxx.protected_paths import is_protected_path

        # boundary decision still folds — an uppercased grader is protected
        assert is_protected_path("PXX/REVIEW_GATE.PY")


class TestP4PorcelainQuoting:
    """[P4] fail-closed: git C-quotes paths with spaces/non-ASCII without -z,
    and the quotes false-flagged as an unexpected path. -z carries raw bytes."""

    def test_verify_clean_for_a_target_with_a_space(self, tmp_path):
        repo = _repo(tmp_path)
        c = _cc(target="pxx/prompts/my prompt.md", content="x\n")
        applied = apply_content_candidate(repo, c)
        assert verify_only_touched_target(repo, c, applied.base_sha) == []

    def test_verify_clean_for_a_non_ascii_target(self, tmp_path):
        repo = _repo(tmp_path)
        c = _cc(target="pxx/prompts/café.md", content="x\n")
        applied = apply_content_candidate(repo, c)
        assert verify_only_touched_target(repo, c, applied.base_sha) == []


class TestG1PositiveVerification:
    """[G1] The required base_sha stops a DROPPED sha but not a WRONG one: a
    rev-parse taken AFTER the auto-commit is a valid string with an empty diff,
    which an all-negative check passes vacuously. verify must POSITIVELY require
    the target to appear in the changed set."""

    def test_post_commit_base_sha_on_committed_escape_is_not_vacuous(self, tmp_path):
        repo = _repo(tmp_path)
        apply_content_candidate(repo, _cc(content="new\n"))
        (repo / "pxx" / "review_gate.py").write_text("# TAMPERED\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "loop round", "--no-verify")
        wrong = _head(repo)  # rev-parse AFTER the auto-commit → empty base..HEAD
        violations = verify_only_touched_target(repo, _cc(), wrong)
        assert violations != []  # fails closed, not a vacuous clean pass

    def test_correct_base_sha_committed_legit_write_still_clean(self, tmp_path):
        repo = _repo(tmp_path)
        applied = apply_content_candidate(repo, _cc(content="new\n"))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "loop round", "--no-verify")
        assert verify_only_touched_target(repo, _cc(), applied.base_sha) == []

    def test_noop_write_target_unchanged_is_a_violation(self, tmp_path):
        repo = _repo(tmp_path)
        # content identical to the fixture's system.md → git shows no change
        applied = apply_content_candidate(repo, _cc(content="old prompt\n"))
        violations = verify_only_touched_target(repo, _cc(), applied.base_sha)
        assert any("expected target" in v for v in violations)


class TestRequirementOneEquivalence:
    """validate-path, write-path, verify-path derive from ONE value."""

    def test_write_and_verify_agree_on_the_same_canonical_target(self, tmp_path):
        repo = _repo(tmp_path)
        # A target with a ./ that normalizes to the same canonical path.
        c = _cc(target="./pxx/prompts/system.md", content="v2\n")
        applied = apply_content_candidate(repo, c)
        assert applied.dest == repo / "pxx/prompts/system.md"
        # verify (git-derived) sees exactly that path -> clean
        assert verify_only_touched_target(repo, c, applied.base_sha) == []


def _commit_runner(fixture, applied):
    # a loop that commits its work (as aider does), touching nothing extra
    _git(fixture, "add", "-A")
    _git(fixture, "commit", "-q", "-m", "loop round", "--no-verify")
    return 0


def _escape_runner(fixture, applied):
    # a loop that ALSO commits an escape to the grader
    (fixture / "pxx" / "review_gate.py").write_text("# TAMPERED\n")
    _git(fixture, "add", "-A")
    _git(fixture, "commit", "-q", "-m", "loop round", "--no-verify")
    return 0


class TestG2FixtureThreadsApplysSha:
    """[G2] The envelope verifies with apply's OWN base_sha — never a fresh
    rev-parse. A committed escape stays catchable as a protected-path
    violation, which is only possible if the pre-write sha was threaded."""

    def test_wiring_catches_a_committed_escape(self, tmp_path):
        repo = _repo(tmp_path)
        res = run_content_candidate_in_fixture(
            repo, _cc(content="new\n"), _escape_runner
        )
        # "protected path" (not merely G1's "expected target") proves the diff
        # spanned the run — i.e. apply's pre-write sha was used, not a re-derive.
        assert any("protected path" in v for v in res.violations)
        assert not res.ok

    def test_wiring_happy_path_is_clean(self, tmp_path):
        repo = _repo(tmp_path)
        res = run_content_candidate_in_fixture(
            repo, _cc(content="new\n"), _commit_runner
        )
        assert res.violations == [] and res.ok
        assert res.base_sha  # apply's pre-write sha, surfaced on the result


class TestG3CleanFixtureAsserted:
    """[G3] A dirty fixture false-flags verify AND can mask a real escape in
    the noise — reject it before apply, fail loud."""

    def test_dirty_fixture_rejected_before_apply(self, tmp_path):
        import pytest

        repo = _repo(tmp_path)
        (repo / "stray.txt").write_text("dirty\n")  # pre-dirty the tree
        with pytest.raises(RuntimeError):
            run_content_candidate_in_fixture(repo, _cc(content="new\n"), _commit_runner)
        # apply never ran — the target is untouched
        assert (repo / "pxx/prompts/system.md").read_text() == "old prompt\n"


class TestContentEvalEnvelope:
    """clone → apply → run → verify → restore, on a clone (live repo untouched)."""

    def test_clone_is_clean_and_isolated(self, tmp_path):
        repo = _repo(tmp_path)
        clone = clone_repo_for_content_eval(repo)
        try:
            assert (clone / "pxx/prompts/system.md").read_text() == "old prompt\n"
        finally:
            import shutil

            shutil.rmtree(clone, ignore_errors=True)

    def test_evaluate_runs_on_a_clone_and_leaves_source_untouched(self, tmp_path):
        repo = _repo(tmp_path)
        res = evaluate_content_candidate(repo, _cc(content="new\n"), _commit_runner)
        assert res.ok
        # the live repo was never written — the sweep ran on the discarded clone
        assert (repo / "pxx/prompts/system.md").read_text() == "old prompt\n"


class TestP5RenameHidesProtectedDeletion:
    """[P5] fail-open: git rename detection collapses a `D <protected>` +
    `A <allowed-target>` pair into one `R100 -> <target>`, hiding the protected
    deletion from verify. `changed_paths` forces `--no-renames` so the `D`
    stays visible. Removing that flag makes both tests fail (the rename
    re-collapses), so they double as the flag's regression guard."""

    def test_committed_rename_escape_is_caught(self, tmp_path):
        repo = _repo(tmp_path)
        base = _head(repo)
        # Poisoned loop renames the grader ONTO the declared (new) target, then
        # commits — exactly what a rename would collapse into a single R100.
        _git(repo, "mv", "pxx/review_gate.py", "pxx/prompts/newnote.md")
        _git(repo, "commit", "-q", "-m", "loop round", "--no-verify")
        cc = _cc(target="pxx/prompts/newnote.md")
        violations = verify_only_touched_target(repo, cc, base)
        assert any("protected path" in v and "review_gate" in v for v in violations)

    def test_staged_rename_escape_is_in_changed_paths_and_caught(self, tmp_path):
        repo = _repo(tmp_path)
        base = _head(repo)
        _git(
            repo, "mv", "pxx/review_gate.py", "pxx/prompts/newnote.md"
        )  # staged, not committed
        assert "pxx/review_gate.py" in changed_paths(repo)
        cc = _cc(target="pxx/prompts/newnote.md")
        violations = verify_only_touched_target(repo, cc, base)
        assert any("protected path" in v and "review_gate" in v for v in violations)
