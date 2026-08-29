"""Diff reconstruction for the review bench (prototypes/review_bench/bench.py).

This parser has produced four defects, every one of which failed QUIETLY — a case
silently dropped, a hunk silently omitted, a pre-image silently overwritten. A
measurement harness that reconstructs the wrong diff scores the wrong thing and
says nothing, so the parser is pinned here.

The example is a prototype, not part of the pxx package, so it is loaded by path
(same pattern as tests/test_hitl_gate_bridge.py).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_BENCH = Path(__file__).resolve().parent.parent / "prototypes" / "review_bench" / "bench.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("review_bench", _BENCH)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: bench.py uses `from __future__ import annotations`
    # plus @dataclass, and dataclasses resolve annotations through
    # sys.modules[cls.__module__]. Without this the import raises inside
    # dataclasses rather than anywhere obvious.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_reconstructs_before_and_after(bench):
    files = bench.reconstruct(
        "--- a/app/u.py\n+++ b/app/u.py\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n"
    )
    assert len(files) == 1
    assert files[0].path == "app/u.py"
    assert files[0].before.splitlines() == ["keep", "old"]
    assert files[0].after.splitlines() == ["keep", "new"]


def test_handles_multi_file_diffs(bench):
    """A single-file parser silently dropped the second file of a two-file case."""
    files = bench.reconstruct(
        "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-a\n+A\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1,1 +1,1 @@\n-b\n+B\n"
    )
    assert [f.path for f in files] == ["a.py", "b.py"]


def test_hunk_content_that_looks_like_a_header_is_content(bench):
    """A valid added line whose source text is '++ /dev/null' is encoded in the
    diff as '+++ /dev/null'. Classifying by prefix alone read that as a file
    header, rejected it as an unsafe path, and dropped the entire case."""
    files = bench.reconstruct("--- a/n.py\n+++ b/app/n.py\n@@ -1,1 +1,2 @@\n keep\n+++ /dev/null\n")
    assert len(files) == 1
    assert files[0].after.splitlines() == ["keep", "++ /dev/null"]


def test_a_real_deletion_header_is_still_rejected(bench):
    """The counterpart: '+++ /dev/null' in HEADER position is a deletion, which
    cannot be materialised, and must reject the case rather than write to an
    absolute path (Path(out) / '/dev/null' discards out)."""
    with pytest.raises(bench.UnsafeDiffPath):
        bench.reconstruct("--- a/x.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-gone\n")


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../etc/passwd", "b/../../x"])
def test_absolute_and_traversing_paths_are_rejected(bench, bad):
    with pytest.raises(bench.UnsafeDiffPath):
        bench.reconstruct(f"--- a/x\n+++ {bad}\n@@ -1,1 +1,1 @@\n-a\n+b\n")


def test_rejection_aborts_the_case_not_just_the_hunk(bench):
    """Skipping only the offending file would leave the rest reconstructed and
    silently incomplete — a PR that does not carry the case it claims to."""
    with pytest.raises(bench.UnsafeDiffPath):
        bench.reconstruct(
            "--- a/ok.py\n+++ b/ok.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
            "--- a/x\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-gone\n"
        )


def test_empty_diff_yields_nothing_rather_than_raising(bench):
    """edge-empty-diff must be reported as unscaffoldable, not crash the run."""
    assert bench.reconstruct("") == []
    assert bench.reconstruct("no headers here\njust text\n") == []


def test_every_corpus_case_round_trips(bench):
    """Whole-corpus guard: reconstruction must not regress on real cases."""
    from pxx.calibration import load_cases

    cases = load_cases("evals/calibration")
    built = 0
    for case in cases:
        try:
            files = bench.reconstruct(case.diff)
        except bench.UnsafeDiffPath:  # none today; would be reported, not silent
            continue
        if files:
            built += 1
            for f in files:
                assert not f.path.startswith("/")
                assert ".." not in Path(f.path).parts
    # 13 of 14 reconstruct; edge-empty-diff has no hunk and cannot be a PR.
    assert built == 13, f"expected 13 reconstructable cases, got {built}"
