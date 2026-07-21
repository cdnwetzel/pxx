"""Tests for pxx.outcomes — failure taxonomy and RunOutcome projection (#012)."""

from __future__ import annotations

import json

from pxx import outcomes
from pxx.outcomes import (
    FAILURE_CODES,
    outcome_from_records,
    recent_outcomes,
    verification_packet,
)


def _terminal(code="APPROVED", run_id="r1", **kw):
    return {
        "session_class": "loop-terminal",
        "run_id": run_id,
        "agent_version_id": "agent-x",
        "terminal_code": code,
        "rounds": kw.get("rounds", 2),
        "exit": 0 if code == "APPROVED" else 1,
        "start_sha": kw.get("start_sha", "aaa"),
        "end_sha": kw.get("end_sha", "bbb"),
    }


def _round(n, run_id="r1", **kw):
    return {
        "session_class": "loop-round",
        "round": n,
        "run_id": run_id,
        "verdict": kw.get("verdict", "APPROVE"),
        "edit_s": kw.get("edit_s", 10),
        "test_s": kw.get("test_s", 5),
        "review_s": kw.get("review_s", 1),
        "diff_lines": kw.get("diff_lines", 40),
        "baseline_failing": kw.get("baseline_failing", 0),
        "introduced_failing": kw.get("introduced_failing", 0),
        "findings_by_severity": kw.get(
            "findings_by_severity", {"P0": 0, "P1": 0, "P2": 0, "UNPARSEABLE": 0}
        ),
    }


class TestFailureCodes:
    def test_taxonomy_is_closed_and_stable(self):
        assert "APPROVED" in FAILURE_CODES
        assert "OUT_OF_SCOPE" in FAILURE_CODES
        assert "INTERRUPTED" in FAILURE_CODES
        assert len(FAILURE_CODES) == 19

    def test_loop_emits_only_canonical_codes(self):
        # Every literal passed to _terminal() in the driver must be canonical.
        import re
        from pathlib import Path

        import pxx.loop as loop_mod

        src = Path(loop_mod.__file__).read_text(encoding="utf-8")
        used = set(re.findall(r'_terminal\(\s*\d,\s*"([A-Z_]+)"', src))
        used |= set(re.findall(r'"(EDIT_TIMEOUT|EDIT_FAILED)"', src))
        assert used and used <= FAILURE_CODES


class TestOutcomeProjection:
    def test_approved_run_projects(self):
        records = [_round(1, verdict="REVISE"), _round(2), _terminal("APPROVED")]
        o = outcome_from_records(records)
        assert o is not None
        assert o.accepted is True
        assert o.terminal_code == "APPROVED"
        assert o.rounds == 2
        assert o.edit_seconds == 20 and o.test_seconds == 10
        assert o.verdicts == ("REVISE", "APPROVE")
        assert o.start_sha == "aaa" and o.end_sha == "bbb"

    def test_findings_are_summed_across_rounds(self):
        records = [
            _round(
                1, findings_by_severity={"P0": 1, "P1": 2, "P2": 0, "UNPARSEABLE": 1}
            ),
            _round(
                2, findings_by_severity={"P0": 0, "P1": 1, "P2": 3, "UNPARSEABLE": 0}
            ),
            _terminal("REVIEW_REJECTED"),
        ]
        o = outcome_from_records(records)
        assert (o.findings_p0, o.findings_p1, o.findings_p2) == (1, 3, 3)
        assert o.findings_unparseable == 1
        assert o.accepted is False

    def test_no_terminal_record_yields_none(self):
        assert outcome_from_records([_round(1)]) is None

    def test_zero_round_failure_projects(self):
        o = outcome_from_records([_terminal("HOOKS_MISSING", rounds=0)])
        assert o is not None and o.rounds == 0 and not o.accepted


class TestVerificationPacket:
    def test_packet_carries_commits_and_results(self):
        o = outcome_from_records([_round(1), _terminal("APPROVED", rounds=1)])
        p = verification_packet(o)
        assert p.baseline_commit == "aaa" and p.result_commit == "bbb"
        assert p.accepted is True
        assert any("terminal=APPROVED" in r for r in p.verification_results)
        assert p.unresolved_risks == ()

    def test_packet_surfaces_risks(self):
        o = outcome_from_records(
            [
                _round(1, introduced_failing=2),
                _terminal("ROUND_CAP_EXCEEDED", rounds=1),
            ]
        )
        p = verification_packet(o)
        assert any("newly failing" in r for r in p.unresolved_risks)


class TestRecentOutcomes:
    def test_reads_jsonl_and_orders_newest_first(self, tmp_path):
        recs_a = [
            _round(1, run_id="20260101T000000-aa"),
            _terminal("APPROVED", run_id="20260101T000000-aa"),
        ]
        recs_b = [
            _round(1, run_id="20260202T000000-bb"),
            _terminal("OUT_OF_SCOPE", run_id="20260202T000000-bb"),
        ]
        f = tmp_path / "2026-07-16.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in recs_a + recs_b) + "\n")
        rows = recent_outcomes(directory=tmp_path)
        assert [r.run_id for r in rows] == [
            "20260202T000000-bb",
            "20260101T000000-aa",
        ]
        assert rows[0].terminal_code == "OUT_OF_SCOPE"

    def test_garbage_lines_and_empty_dir_are_tolerated(self, tmp_path):
        (tmp_path / "bad.jsonl").write_text("not json\n{}\n")
        assert recent_outcomes(directory=tmp_path) == []
        assert recent_outcomes(directory=tmp_path / "missing") == []

    def test_limit_respected(self, tmp_path):
        lines = []
        for i in range(5):
            rid = f"2026010{i}T000000-{i:02d}"
            lines.append(json.dumps(_terminal("APPROVED", run_id=rid)))
        (tmp_path / "log.jsonl").write_text("\n".join(lines) + "\n")
        assert len(outcomes.recent_outcomes(limit=3, directory=tmp_path)) == 3


class TestPacketConsumption:
    """VerificationPacket is now READ, not just typed (#012 consumption)."""

    def test_outcome_for_run_projects_by_id(self, tmp_path):
        import json as _json

        recs = [
            _round(1, run_id="20260717T090000-aa"),
            _terminal("APPROVED", run_id="20260717T090000-aa"),
        ]
        other = [_terminal("OUT_OF_SCOPE", run_id="20260717T090000-bb")]
        (tmp_path / "log.jsonl").write_text(
            "\n".join(_json.dumps(r) for r in recs + other) + "\n"
        )
        o = outcomes.outcome_for_run("20260717T090000-aa", directory=tmp_path)
        assert o is not None and o.terminal_code == "APPROVED"
        assert outcomes.outcome_for_run("nope", directory=tmp_path) is None

    def test_format_packet_is_human_readable_evidence(self):
        o = outcome_from_records([_round(1), _terminal("APPROVED", rounds=1)])
        text = outcomes.format_packet(verification_packet(o))
        assert "verification packet" in text
        assert "APPROVED" in text
        assert "aaa" in text and "bbb" in text  # baseline + result commits
        assert "risks:     none" in text

    def test_format_packet_surfaces_risks(self):
        o = outcome_from_records(
            [_round(1, introduced_failing=1), _terminal("ROUND_CAP_EXCEEDED", rounds=1)]
        )
        text = outcomes.format_packet(verification_packet(o))
        assert "newly failing" in text
