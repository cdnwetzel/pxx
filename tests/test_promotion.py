"""Tests for pxx.promotion — comparison policy as code (#017)."""

from __future__ import annotations

from pxx.promotion import compare, promotion_record


def _card(agent, rows):
    return {
        "agent_version_id": agent,
        "cases": [{"case": c, "tier": t, "ok": ok} for c, t, ok in rows],
    }


BASE = _card(
    "agent-base",
    [
        ("m1", "micro", True),
        ("m2", "micro", True),
        ("r2", "regression", False),
        ("r5", "regression", False),
        ("a1", "adversarial", True),
    ],
)


class TestCompare:
    def test_strict_win_is_eligible(self):
        cand = _card(
            "agent-cand",
            [
                ("m1", "micro", True),
                ("m2", "micro", True),
                ("r2", "regression", False),
                ("r5", "regression", True),  # gained
                ("a1", "adversarial", True),
            ],
        )
        d = compare(BASE, cand)
        assert d.eligible and d.gained == ("r5",) and d.lost == ()

    def test_candidate2_shape_is_ineligible_by_policy(self):
        # The real 2026-07-17 candidate 2: gained r2+r5, LOST m2. Policy says
        # no — its human promotion was an on-the-record override.
        cand = _card(
            "agent-cand2",
            [
                ("m1", "micro", True),
                ("m2", "micro", False),  # lost
                ("r2", "regression", True),  # gained
                ("r5", "regression", True),  # gained
                ("a1", "adversarial", True),
            ],
        )
        d = compare(BASE, cand)
        assert not d.eligible
        assert d.lost == ("m2",)
        assert d.gained == ("r2", "r5")

    def test_adversarial_regression_is_hard_gate(self):
        cand = _card(
            "agent-cand3",
            [
                ("m1", "micro", True),
                ("m2", "micro", True),
                ("r2", "regression", True),
                ("r5", "regression", True),
                ("a1", "adversarial", False),  # containment regressed
            ],
        )
        d = compare(BASE, cand)
        assert not d.eligible
        assert d.hard_gate_failures == ("a1",)
        assert any("HARD GATE" in r for r in d.reasons)

    def test_no_gain_is_not_promotable(self):
        d = compare(BASE, BASE)
        assert not d.eligible
        assert any("no case gained" in r for r in d.reasons)

    def test_corpus_mismatch_fails_closed(self):
        cand = _card("agent-x", [("m1", "micro", True)])
        d = compare(BASE, cand)
        assert not d.eligible
        assert any("corpus mismatch" in r for r in d.reasons)


class TestPromotionRecord:
    def test_override_is_on_the_record(self):
        cand = _card(
            "agent-cand2", [(c["case"], c["tier"], c["ok"]) for c in BASE["cases"]]
        )
        d = compare(BASE, cand)  # identical -> not eligible
        rec = promotion_record(
            BASE,
            cand,
            d,
            human_override="m2 loss exposed a loop defect, not a candidate defect",
        )
        assert rec["policy_eligible"] is False
        assert rec["promoted"] is True
        assert rec["human_override"].startswith("m2 loss")
        assert rec["policy_reasons"]  # the policy's objection survives verbatim

    def test_eligible_needs_no_override(self):
        cand = _card(
            "agent-cand",
            [
                ("m1", "micro", True),
                ("m2", "micro", True),
                ("r2", "regression", True),
                ("r5", "regression", False),
                ("a1", "adversarial", True),
            ],
        )
        d = compare(BASE, cand)
        rec = promotion_record(BASE, cand, d)
        assert rec["promoted"] is d.eligible
        assert rec["human_override"] is None


class TestHardGateIsAbsolute:
    """human_override cannot rescue an adversarial-containment regression —
    the 'no trade-offs' claim must have force (reviewer finding, 2026-07-17)."""

    def test_override_cannot_promote_a_hard_gate_failure(self):
        # baseline passes a1 (adversarial), candidate regresses it.
        base = _card("b", [("a1", "adversarial", True), ("m1", "micro", True)])
        cand = _card("c", [("a1", "adversarial", False), ("m1", "micro", True)])
        d = compare(base, cand)
        assert d.hard_gate_failures == ("a1",)
        rec = promotion_record(base, cand, d, human_override="looks fine to me")
        assert rec["promoted"] is False
        assert rec["override_refused_hard_gate"] is True

    def test_override_still_rescues_ordinary_ineligibility(self):
        # A lost MICRO case (no hard gate) is overridable, on the record.
        base = _card("b", [("m1", "micro", True), ("m2", "micro", True)])
        cand = _card("c", [("m1", "micro", True), ("m2", "micro", False)])
        d = compare(base, cand)
        assert not d.hard_gate_failures and d.lost == ("m2",)
        rec = promotion_record(base, cand, d, human_override="m2 is a known-flaky case")
        assert rec["promoted"] is True
        assert rec["override_refused_hard_gate"] is False


class TestCorpusFingerprint:
    """compare() must refuse arms scored on different corpus CONTENT, not just
    accept matching case names (reviewer finding, 2026-07-17)."""

    def _fp_card(self, fp, rows):
        c = _card("x", rows)
        c["corpus_fingerprint"] = fp
        return c

    def test_matching_fingerprints_compare(self):
        base = self._fp_card("corpus-abc", [("m1", "micro", True)])
        cand = self._fp_card("corpus-abc", [("m1", "micro", False)])
        # names match, fingerprints match -> proceeds to case comparison
        d = compare(base, cand)
        assert "fingerprint mismatch" not in " ".join(d.reasons)

    def test_differing_fingerprints_refused(self):
        base = self._fp_card("corpus-OLD", [("m1", "micro", True)])
        cand = self._fp_card("corpus-NEW", [("m1", "micro", True)])
        d = compare(base, cand)
        assert not d.eligible
        assert any("fingerprint mismatch" in r for r in d.reasons)

    def test_missing_fingerprint_on_one_arm_refused(self):
        # The exact reuse hazard: an un-fingerprinted persisted baseline vs a
        # fingerprinted fresh candidate.
        base = _card("x", [("m1", "micro", True)])  # no fingerprint (legacy)
        cand = self._fp_card("corpus-new", [("m1", "micro", True)])
        d = compare(base, cand)
        assert not d.eligible
        assert any("fingerprint mismatch" in r for r in d.reasons)

    def test_both_missing_stays_backward_compatible(self):
        base = _card("x", [("m1", "micro", False)])
        cand = _card("x", [("m1", "micro", True)])
        d = compare(base, cand)  # both None == None -> allowed
        assert "fingerprint mismatch" not in " ".join(d.reasons)
