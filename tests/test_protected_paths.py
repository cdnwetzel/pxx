"""Tests for pxx.protected_paths — the single authoritative protected set.

The whole point of single-sourcing is that the two static mirrors
(.aiderignore, TRUST_BOUNDARY.md) cannot drift from the canonical list.
These tests are that enforcement — they fail on drift in either direction
for every entry, not just the pxx/*.py ones."""

from __future__ import annotations

from pathlib import Path

from pxx.protected_paths import PROTECTED_PREFIXES, is_protected_path

REPO = Path(__file__).resolve().parent.parent


class TestIsProtectedPath:
    def test_exact_and_prefix_matches(self):
        assert is_protected_path("pxx/review_gate.py")
        assert is_protected_path("evals/micro/m1.toml")  # inside a protected dir
        assert is_protected_path("evals")  # the dir itself, no trailing slash
        assert is_protected_path(".github/workflows/ci.yml")

    def test_leading_dotslash_normalized(self):
        assert is_protected_path("./pxx/governance.py")

    def test_unprotected_paths_pass(self):
        assert not is_protected_path("pxx/duration.py")
        assert not is_protected_path("pxx/endpoints.py")
        assert not is_protected_path("README.md")
        assert not is_protected_path("docs/DEPLOY.md")

    def test_the_protected_list_module_protects_itself(self):
        assert is_protected_path("pxx/protected_paths.py")


class TestFailsClosedOnDiffPathShapes:
    """The enforcement floor for content candidates: a diff path must not
    dodge protection via git prefixes, traversal, case, or absolute paths
    (reviewer day-one finding, 2026-07-17). Fail closed on anything
    unclassifiable."""

    def test_git_diff_ab_prefixes_do_not_dodge(self):
        # raw `git diff` prefixes every path with a/ or b/.
        assert is_protected_path("a/pxx/evaluation.py")
        assert is_protected_path("b/evals/m1.toml")
        assert is_protected_path("a/pxx/governance.py")

    def test_dotdot_traversal_into_protected_space_caught(self):
        assert is_protected_path("pxx/../evals/m1.toml")
        assert is_protected_path("evals/../pxx/review_gate.py")

    def test_case_insensitive_variant_caught(self):
        # macOS default FS is case-insensitive; PXX/... writes the same file.
        assert is_protected_path("PXX/EVALUATION.PY")
        assert is_protected_path("pxx/Review_Gate.py")

    def test_backslash_paths_caught(self):
        assert is_protected_path("pxx\\loop.py")

    def test_surrounding_whitespace_stripped(self):
        assert is_protected_path("  pxx/promotion.py  ")

    def test_absolute_path_fails_closed(self):
        assert is_protected_path("/etc/passwd")
        assert is_protected_path("/Users/you/pxx/evaluation.py")

    def test_repo_escaping_traversal_fails_closed(self):
        assert is_protected_path("../../secrets")
        assert is_protected_path("..")

    def test_empty_and_nonstring_fail_closed(self):
        assert is_protected_path("")
        assert is_protected_path("   ")
        assert is_protected_path(None)  # type: ignore[arg-type]

    def test_legit_paths_not_over_protected(self):
        # Fail-closed must not become "protect everything" — real editable
        # files still pass, including dirs that merely start with a/b letters.
        for ok in (
            "pxx/duration.py",
            "pxx/endpoints.py",
            "README.md",
            "src/lib.py",
            "app/main.py",
            "a/b/note.md",
            "tests/test_x.py",
        ):
            assert not is_protected_path(ok), ok


class TestMirrorsMatchCanonical:
    def test_aiderignore_contains_every_protected_path(self):
        ignore = (REPO / ".aiderignore").read_text()
        for p in PROTECTED_PREFIXES:
            assert p in ignore, f"{p} in canonical list but missing from .aiderignore"

    def test_trust_boundary_doc_references_every_protected_path(self):
        doc = (REPO / "docs" / "TRUST_BOUNDARY.md").read_text()
        for p in PROTECTED_PREFIXES:
            # dir entries appear with or without trailing slash in prose
            needle = p.rstrip("/")
            assert needle in doc, f"{p} in canonical list but not in TRUST_BOUNDARY.md"

    def test_every_gate_and_eval_module_is_protected(self):
        # A candidate must not be able to edit anything that grades or judges it.
        for mod in (
            "pxx/review_gate.py",
            "pxx/governance.py",
            "pxx/evaluation.py",
            "pxx/calibration.py",
            "pxx/promotion.py",
            "pxx/candidates.py",
            "pxx/candidate_eval.py",
            "pxx/loop.py",
            "pxx/safety.py",
            "pxx/scope.py",
            "evals/",
        ):
            assert mod in PROTECTED_PREFIXES, mod
