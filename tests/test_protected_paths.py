"""Protected-path set tests: normalization, fail-closed, doc mirror."""

from __future__ import annotations

from pathlib import Path

from pxx.protected_paths import PROTECTED_PREFIXES, is_protected_path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "TRUST_BOUNDARY.md"


def _doc_protected_list(text: str) -> list[str]:
    """Parse the single fenced '- path' bullet list out of TRUST_BOUNDARY.md."""
    entries: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                break  # only the first fenced block is the protected list
            in_fence = True
            continue
        if in_fence and stripped.startswith("- "):
            entries.append(stripped[2:].strip())
    return entries


# --- doc mirror (both directions) --------------------------------------------


def test_doc_exists_and_mirrors_prefixes_exactly():
    text = DOC.read_text(encoding="utf-8")
    doc_entries = _doc_protected_list(text)
    assert doc_entries, "no fenced '- path' list found in TRUST_BOUNDARY.md"
    # Bidirectional equality, order included.
    assert doc_entries == list(PROTECTED_PREFIXES)
    assert set(doc_entries) == set(PROTECTED_PREFIXES)


def test_prefixes_are_well_formed():
    for prefix in PROTECTED_PREFIXES:
        assert prefix == prefix.strip()
        assert not prefix.startswith(("/", "./"))
        assert "\\" not in prefix


# --- exact files and directory prefixes ---------------------------------------


def test_exact_files_protected():
    for path in (
        "pxx/safety.py",
        "pxx/errors.py",
        "pxx/governance.py",
        "pxx/protected_paths.py",
        "docs/TRUST_BOUNDARY.md",
        "tests/test_safety.py",
        "tests/test_governance.py",
        "tests/test_protected_paths.py",
        "scripts/smoke-package.sh",
    ):
        assert is_protected_path(path), path


def test_directory_prefixes_protect_children():
    for path in (
        "pxx/eval/cases.py",
        "pxx/eval/harness.py",
        "pxx/improve/promotion.py",
        "pxx/improve/autopromote.py",
        "evals/micro/fix-typo.toml",
        "evals/adversarial/insert-secret.toml",
        ".github/workflows/ci.yml",
    ):
        assert is_protected_path(path), path


def test_directory_prefix_without_trailing_slash_protected():
    for path in ("pxx/eval", "pxx/improve", "evals", ".github"):
        assert is_protected_path(path), path


def test_normal_repo_paths_unprotected():
    for path in (
        "pxx/loop.py",
        "pxx/session.py",
        "pxx/cli.py",
        "pxx/prompts/native_system.md",
        "tests/test_cli.py",
        "README.md",
        "pyproject.toml",
        "src/main.py",
    ):
        assert not is_protected_path(path), path


def test_exact_match_does_not_leak_to_similar_names():
    # File entries are exact matches, not prefixes.
    assert not is_protected_path("pxx/safety.py.bak")
    assert not is_protected_path("tests/test_safety_extra.py")
    assert not is_protected_path("docs/TRUST_BOUNDARY.md.old")


# --- normalization -------------------------------------------------------------


def test_backslashes_normalized():
    assert is_protected_path("pxx\\safety.py")
    assert is_protected_path("pxx\\eval\\cases.py")


def test_leading_dot_slash_stripped_once():
    assert is_protected_path("./pxx/safety.py")
    assert is_protected_path("./evals/micro/x.toml")


def test_dot_github_stays_protected_regression():
    # 1.x bug: lstrip("./") ate the leading '.' and unprotected .github/.
    assert is_protected_path(".github/")
    assert is_protected_path(".github/workflows/ci.yml")
    assert is_protected_path("./.github/workflows/ci.yml")


def test_dot_segments_resolved_lexically():
    assert is_protected_path("foo/../pxx/safety.py")
    assert is_protected_path("pxx/../evals/x.toml")
    assert not is_protected_path("pxx/eval/../loop.py")


# --- fail-closed on unclassifiable input ---------------------------------------


def test_empty_and_blank_fail_closed():
    assert is_protected_path("")
    assert is_protected_path("   ")


def test_absolute_and_home_paths_fail_closed():
    assert is_protected_path("/etc/passwd")
    assert is_protected_path("/repo/pxx/loop.py")
    assert is_protected_path("~/something")
    assert is_protected_path("C:\\pxx\\loop.py")
    assert is_protected_path("c:/pxx/loop.py")


def test_root_escape_fails_closed():
    assert is_protected_path("..")
    assert is_protected_path("../outside.py")
    assert is_protected_path("pxx/../../outside.py")


def test_nul_and_bare_dot_fail_closed():
    assert is_protected_path("pxx/\x00loop.py")
    assert is_protected_path(".")


# --- A2: case-insensitive volumes must not bypass the deny predicate ------------------


def test_protected_paths_match_case_insensitively() -> None:
    """A2: on macOS/Windows, PXX/safety.py IS pxx/safety.py — the deny
    predicate must fire for every spelling."""
    for protected in (
        "pxx/safety.py",
        "pxx/eval/harness.py",
        "evals/micro/x.toml",
        "pxx/improve/promotion.py",
    ):
        for variant in {protected, protected.upper(), protected.title()}:
            assert is_protected_path(variant), variant


def test_casefold_still_allows_normal_paths() -> None:
    assert not is_protected_path("src/main.py")
    assert not is_protected_path("SRC/MAIN.PY")
