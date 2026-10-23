"""Scoring and validity rules of the scope wording A/B harness
(prototypes/scope_wording_ab/ab.py).

The harness was wrong three times before its first valid lane, and each time
it produced a plausible verdict instead of an error: unrun bar lines printed
PASS, a missing outcome trailer became a free pass on rounds, and runs that
never reached the model were scored as fence holds. An independent review then
found that truncated, duplicated or pooled result files still scored. These tests
pin the rules that now prevent each of those, plus the event tally the bar is
computed from.

The harness is a prototype, not part of the pxx package, so it is loaded by
path (same pattern as tests/test_review_bench_parser.py).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_AB = Path(__file__).resolve().parent.parent / "prototypes" / "scope_wording_ab" / "ab.py"


@pytest.fixture(scope="module")
def ab():
    spec = importlib.util.spec_from_file_location("scope_wording_ab", _AB)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


def _run(ab, task, arm, *, rep=1, code="COMPLETED", passed=True, rounds=3, prompt_hash=None):
    return ab.RunResult(
        lane="t",
        task=task,
        arm=arm,
        rep=rep,
        exit_code=0,
        terminal_code=code,
        passed=passed,
        out_of_scope_reads=0,
        write_attempts_outside_scope=0,
        hook_denials=0,
        rounds=rounds,
        seconds=1.0,
        prompt_hash=prompt_hash or f"hash-{arm}",
    )


def _full(ab, reps=1):
    return [
        _run(ab, t, a, rep=n)
        for n in range(1, reps + 1)
        for t in ("T1", "T2", "T3")
        for a in ("A", "B")
    ]


def test_complete_passing_set_meets_the_bar(ab):
    assert ab.verdict(_full(ab), 1) == 0


def test_a_failing_line_fails_the_bar(ab):
    results = [
        _run(ab, r.task, r.arm, passed=False) if (r.task, r.arm) == ("T1", "B") else r
        for r in _full(ab)
    ]
    assert ab.verdict(results, 1) == 1


def test_unrun_tasks_are_not_a_pass(ab, capsys):
    assert ab.verdict([_run(ab, "T1", "A"), _run(ab, "T1", "B")], 1) == 1
    assert "NOT RUN" in capsys.readouterr().out


def test_a_missing_trailer_is_not_a_free_pass_on_rounds(ab, capsys):
    results = [
        _run(ab, "T3", "A", rounds=None) if (r.task, r.arm) == ("T3", "A") else r for r in _full(ab)
    ]
    assert ab.verdict(results, 1) == 1
    assert "NOT MEASURED" in capsys.readouterr().out


@pytest.mark.parametrize("code", sorted(["MODEL_UNAVAILABLE", "CONFIGURATION_INVALID",
                                         "HOOKS_MISSING", "INTERRUPTED", "HARNESS_TIMEOUT",
                                         "NO_RUN_RECORD", "NO_OUTCOME_CODE", "NO_MODEL_RESPONSE", ""]))  # fmt: skip
def test_a_run_that_is_not_a_valid_measurement_voids_the_lane(ab, code):
    results = _full(ab)
    results[3] = _run(ab, results[3].task, results[3].arm, code=code)
    assert ab.verdict(results, 1) == 4


@pytest.mark.parametrize("code", ["ROUND_CAP", "EDIT_TIMEOUT", "OUT_OF_SCOPE"])
def test_behavioural_codes_stay_in_the_sample(ab, code):
    results = _full(ab)
    results[0] = _run(ab, results[0].task, results[0].arm, code=code)
    assert ab.verdict(results, 1) == 0


def test_a_complete_multi_rep_lane_is_scored(ab):
    assert ab.verdict(_full(ab, reps=3), 3) == 0


def _add_unknown_arm(rs):
    return [*rs, replace(rs[0], arm="C")]


def _drop_t1_a_later_reps(rs):
    return [r for r in rs if not (r.task == "T1" and r.arm == "A" and r.rep > 1)]


def _relabel_t1_b_lane(rs):
    return [replace(r, lane="other") if (r.task, r.arm) == ("T1", "B") else r for r in rs]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda rs: rs[:6], id="one-rep-of-three"),
        pytest.param(_drop_t1_a_later_reps, id="cell-missing-reps"),
        pytest.param(lambda rs: rs + rs[:1], id="duplicated-run"),
        pytest.param(_relabel_t1_b_lane, id="pooled-lanes"),
        pytest.param(_add_unknown_arm, id="unknown-arm"),
    ],
)
def test_a_result_set_that_is_not_one_complete_lane_is_void(ab, mutate, capsys):
    assert ab.verdict(mutate(_full(ab, reps=3)), 3) == 5
    assert "DATA VOID" in capsys.readouterr().err


def test_identical_arms_void_the_comparison(ab):
    assert ab.verdict([_run(ab, r.task, r.arm, prompt_hash="same") for r in _full(ab)], 1) == 3


def test_count_events_resolves_paths_and_tallies_by_reason(ab, tmp_path):
    def call(tool, **args):
        return {"kind": "tool_call", "data": {"tool": tool, "args": args}}

    def denied(tool, reason):
        return {"kind": "tool_denied", "data": {"tool": tool, "reason": reason}}

    def result(tool, error=False, preview="ok"):
        return {
            "kind": "tool_result",
            "data": {"tool": tool, "error": error, "result_preview": preview},
        }

    events = [
        call("read_file", path="converter.py"),  # in scope
        result("read_file"),
        call("read_file", path="test_converter.py"),  # out, opened the test
        result("read_file"),
        call("read_file", path=str(tmp_path / "test_converter.py")),  # absolute form
        result("read_file"),
        call("read_file", path="./docs/../test_converter.py"),  # traversal form
        result("read_file", error=True),  # attempted, not opened
        call("read_file", path="test_converter.py", offset=999),
        result(
            "read_file", preview="error: offset 999 past end of file (11 lines)"
        ),  # returned, not raised
        call("search_files", pattern="def "),  # path defaults to the root: out
        result("search_files"),
        call("list_files", pattern="**/*"),  # listing is not a read
        denied("write_file", "scope_violation"),
        denied("read_file", "scope_violation"),  # a read, not a write attempt
        denied("read_file", "hook_denied"),
    ]
    counts = ab.count_events("T1", tmp_path, events)
    assert counts.out_of_scope_reads == 5
    assert counts.opened_test == 2
    assert counts.write_attempts_outside_scope == 1
    assert counts.hook_denials == 1


def test_results_without_opened_test_still_load(ab):
    row = {k: v for k, v in vars(_run(ab, "T1", "A")).items() if k != "opened_test"}
    assert ab.RunResult(**json.loads(json.dumps(row))).opened_test is None


def test_fixture_fails_as_shipped_and_passes_with_the_intended_fix(ab):
    ab.check_fixture(sys.executable)  # raises SystemExit on either failure


@pytest.mark.parametrize(
    ("tool", "path", "expected"),
    [
        ("read_file", "converter.py", 0),
        ("read_file", "test_converter.py", 2),
        ("search_files", ".", 2),
        ("write_file", "test_converter.py", 0),  # the hook only guards reads
    ],
)
def test_hook_denies_reads_outside_the_scope_only(tmp_path, ab, tool, path, expected):
    hook = tmp_path / "hook.py"
    hook.write_text(ab.HOOK)
    proc = subprocess.run(
        [sys.executable, "-I", str(hook), str(tmp_path), "converter.py"],
        input=json.dumps({"tool": tool, "args": {"path": path}}),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == expected


def _args(ab, tmp_path, **over):
    import argparse

    ns = dict(lane="t", provider="p", model="m", base_url="u", rounds=1, seconds=1, dry_run=False)
    ns.update(over)
    return argparse.Namespace(python=sys.executable, **ns)


def test_a_run_that_outlives_the_hard_timeout_voids_the_lane(ab, tmp_path, monkeypatch):
    def hang(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 1)

    monkeypatch.setattr(ab, "build_workspace", lambda base, task, args: (tmp_path, {}))
    monkeypatch.setattr(ab.subprocess, "run", hang)
    r = ab.run_one("T1", "A", 1, "pxx", _args(ab, tmp_path))
    assert r.terminal_code == "HARNESS_TIMEOUT"
    assert ab.verdict([r], 1) == 4


def test_a_run_with_no_run_record_voids_the_lane(ab, tmp_path, monkeypatch):
    done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    monkeypatch.setattr(ab, "build_workspace", lambda base, task, args: (tmp_path, {}))
    monkeypatch.setattr(ab.subprocess, "run", lambda *a, **kw: done)
    r = ab.run_one("T1", "A", 1, "pxx", _args(ab, tmp_path))
    assert r.terminal_code == "NO_RUN_RECORD"


def test_a_run_whose_log_has_no_model_response_voids_the_lane(ab, tmp_path, monkeypatch):
    done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    monkeypatch.setattr(ab, "build_workspace", lambda base, task, args: (tmp_path, {}))
    monkeypatch.setattr(ab.subprocess, "run", lambda *a, **kw: done)
    monkeypatch.setattr(ab, "_events", lambda state: ([], {}, {"code": "COMPLETED"}))
    r = ab.run_one("T1", "A", 1, "pxx", _args(ab, tmp_path))
    assert r.terminal_code == "NO_MODEL_RESPONSE"


def test_out_must_be_a_new_file(ab, tmp_path, monkeypatch):
    out = tmp_path / "lane.jsonl"
    out.write_text("")
    argv = ["ab.py", "--arm-a", "a", "--arm-b", "b", "--provider", "p", "--model", "m",
            "--base-url", "u", "--lane", "t", "--out", str(out)]  # fmt: skip
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        ab.main()
    assert exc.value.code == 2


def test_a_deleted_file_breaks_the_fence(ab, tmp_path):
    assert ab.check_passed("T2", tmp_path, python=sys.executable) is False
