"""Tests for pxx.candidate_eval — both-arms eval + compare (#016->17).

The arm runner is injected, so orchestration is tested without live loops:
a fake runner scripts per-(case, overlay) outcomes."""

from __future__ import annotations

from pxx import candidate_eval
from pxx.candidates import Candidate


def _cand(**kw):
    base = dict(
        candidate_id="cand-x",
        field="review_mode",
        value="advisory",
        baseline_value="blocking",
        rationale="measured fix",
        from_observation="obs-1",
    )
    base.update(kw)
    return Candidate(**base)


def _fake_runner(baseline_ok: dict, candidate_ok: dict):
    """overlay-empty => baseline map; overlay-set => candidate map. Keyed by case id."""

    def run(case, overlay):
        table = candidate_ok if overlay else baseline_ok
        return table.get(case.id, True)

    return run


class TestEvaluateCandidate:
    def test_candidate_that_gains_is_eligible(self, tmp_path):
        # Build a tiny 2-case corpus on disk.
        _write_micro(tmp_path, "c1")
        _write_micro(tmp_path, "c2")
        runner = _fake_runner(
            baseline_ok={"c1": True, "c2": False},  # baseline fails c2
            candidate_ok={"c1": True, "c2": True},  # candidate fixes it
        )
        rec = candidate_eval.evaluate_candidate(_cand(), runner, evals_dir=tmp_path)
        assert rec["policy_eligible"] is True
        assert rec["gained"] == ["c2"] and rec["lost"] == []
        assert rec["candidate"] == "cand-x"

    def test_candidate_that_regresses_is_not_eligible(self, tmp_path):
        _write_micro(tmp_path, "c1")
        _write_micro(tmp_path, "c2")
        runner = _fake_runner(
            baseline_ok={"c1": True, "c2": True},
            candidate_ok={"c1": True, "c2": False},  # candidate breaks c2
        )
        rec = candidate_eval.evaluate_candidate(_cand(), runner, evals_dir=tmp_path)
        assert rec["policy_eligible"] is False
        assert rec["lost"] == ["c2"]

    def test_invalid_candidate_refused_before_running(self, tmp_path):
        _write_micro(tmp_path, "c1")
        ran = []

        def runner(case, overlay):
            ran.append(case.id)
            return True

        bad = _cand(field="pxx/governance.py", value="x")  # protected target
        rec = candidate_eval.evaluate_candidate(bad, runner, evals_dir=tmp_path)
        assert rec["promoted"] is False
        assert "integrity" in rec["error"]
        assert ran == []  # never ran a single case

    def test_empty_corpus_fails_closed(self, tmp_path):
        rec = candidate_eval.evaluate_candidate(
            _cand(), lambda c, o: True, evals_dir=tmp_path
        )
        assert rec["promoted"] is False
        assert "no eval cases" in rec["error"]

    def test_no_gain_is_not_eligible(self, tmp_path):
        _write_micro(tmp_path, "c1")
        rec = candidate_eval.evaluate_candidate(
            _cand(), _fake_runner({"c1": True}, {"c1": True}), evals_dir=tmp_path
        )
        assert rec["policy_eligible"] is False


def _write_micro(evals_dir, cid):
    d = evals_dir / "micro"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.toml").write_text(
        f'''schema_version = 1
id = "{cid}"
tier = "micro"
task = "t"
[fixture]
"src/lib.py" = "x = 1\\n"
[checks]
commands = [["python", "-c", "pass"]]
[patches.honest]
"src/lib.py" = "x = 2\\n"
'''
    )
