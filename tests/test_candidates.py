"""Tests for pxx.candidates — constrained candidate generation (#016).

The validator is the safety-critical core: the code path that makes "the
candidate generator cannot modify its own evaluator" TRUE. It must fail
closed on every attempt to escape the allowlist."""

from __future__ import annotations

from pxx.candidates import (
    ALLOWED_FIELDS,
    PROTECTED_PREFIXES,
    Candidate,
    env_overlay,
    save_candidate,
    validate_candidate,
)


def _c(**kw):
    base = dict(
        candidate_id="cand-1",
        field="review_mode",
        value="advisory",
        baseline_value="blocking",
        rationale="advisory resolves the FP-spin (measured)",
        from_observation="obs-dominant-NO_TEST_PROGRESS",
    )
    base.update(kw)
    return Candidate(**base)


class TestValidatorAcceptsGoodCandidates:
    def test_allowlisted_field_passes(self):
        assert validate_candidate(_c()).ok

    def test_budget_tighten_passes(self):
        assert validate_candidate(
            _c(field="max_rounds", value="2", baseline_value="3")
        ).ok

    def test_reviewer_model_swap_passes(self):
        assert validate_candidate(
            _c(field="reviewer_model", value="Qwen3-Coder", baseline_value="q7b")
        ).ok


class TestValidatorFailsClosed:
    def test_non_allowlisted_field_rejected(self):
        r = validate_candidate(_c(field="editor_model", value="x"))
        assert not r.ok and any("not allowlisted" in x for x in r.reasons)

    def test_protected_path_as_field_rejected(self):
        for target in ("pxx/governance.py", "pxx/evaluation.py", "evals/", "config/"):
            r = validate_candidate(_c(field=target, value="x"))
            assert not r.ok, target
            assert any("protected" in x for x in r.reasons)

    def test_declared_protected_target_rejected(self):
        r = validate_candidate(_c(protected_targets_touched=("pxx/review_gate.py",)))
        assert not r.ok and any("protected target" in x for x in r.reasons)

    def test_budget_increase_rejected(self):
        r = validate_candidate(_c(field="max_rounds", value="5", baseline_value="3"))
        assert not r.ok and any("only be lowered" in x for x in r.reasons)

    def test_null_baseline_on_budget_field_fails_closed(self):
        # The bypass: nulling baseline_value skipped the tighten-only check
        # entirely, letting a loosened budget through (reviewer round 4).
        r = validate_candidate(_c(field="max_rounds", value="999", baseline_value=None))
        assert not r.ok and any("tighten-only budget" in x for x in r.reasons)

    def test_non_integer_baseline_on_budget_field_fails_closed(self):
        r = validate_candidate(
            _c(field="max_rounds", value="2", baseline_value="three")
        )
        assert not r.ok and any("tighten-only budget" in x for x in r.reasons)

    def test_non_integer_budget_rejected(self):
        r = validate_candidate(
            _c(field="diff_budget", value="lots", baseline_value="150")
        )
        assert not r.ok

    def test_bad_review_mode_rejected(self):
        r = validate_candidate(_c(field="review_mode", value="yolo"))
        assert not r.ok and any("blocking|advisory" in x for x in r.reasons)

    def test_missing_rationale_rejected(self):
        assert not validate_candidate(_c(rationale="  ")).ok

    def test_missing_observation_rejected(self):
        assert not validate_candidate(_c(from_observation="")).ok


class TestConsistencyWithTrustBoundary:
    def test_protected_prefixes_cover_the_gate_modules(self):
        # The three enforcement surfaces (this list, .aiderignore, the doc)
        # must agree. Pin the gate modules here so drift fails a test.
        for mod in (
            "pxx/governance.py",
            "pxx/review_gate.py",
            "pxx/evaluation.py",
            "pxx/calibration.py",
            "pxx/promotion.py",
            "pxx/loop.py",
            "pxx/safety.py",
            "pxx/scope.py",
            "evals/",
        ):
            assert mod in PROTECTED_PREFIXES, mod

    def test_aiderignore_lists_every_protected_code_path(self):
        # Defense in depth must actually be in depth: every protected code
        # module in candidates.py must also be in .aiderignore.
        ignore = (
            __import__("pathlib").Path(__file__).resolve().parent.parent
            / ".aiderignore"
        ).read_text()
        for p in PROTECTED_PREFIXES:
            if p.startswith("pxx/") and p.endswith(".py"):
                assert p in ignore, (
                    f"{p} protected in candidates.py but not .aiderignore"
                )


class TestEnvOverlay:
    def test_overlay_maps_to_env_var(self):
        assert env_overlay(_c()) == {"PXX_REVIEW_MODE": "advisory"}

    def test_every_allowed_field_has_an_env_var(self):
        for f, var in ALLOWED_FIELDS.items():
            assert var.startswith("PXX_"), f


class TestPersistence:
    def test_save_writes_declarative_json(self, tmp_path):
        d = save_candidate(tmp_path, _c())
        import json

        data = json.loads((d / "candidate.json").read_text())
        assert data["field"] == "review_mode"
        assert data["from_observation"] == "obs-dominant-NO_TEST_PROGRESS"
