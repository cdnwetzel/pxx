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
