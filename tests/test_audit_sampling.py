"""Tests for pxx.audit_sampling: deterministic human audit sampling (B1.4)."""

from __future__ import annotations

from pxx.audit_sampling import ORDINARY_RATE, audit_sample


def test_promotions_always_flagged() -> None:
    for run_id in ("run-a", "run-b", "20260718T000000Z-deadbeef"):
        sample = audit_sample(run_id, promotion=True)
        assert sample.sampled and sample.rate == 1.0


def test_high_risk_always_flagged() -> None:
    for run_id in ("run-a", "run-b", "20260718T000000Z-deadbeef"):
        assert audit_sample(run_id, risk="high").sampled


def test_deterministic_across_passes() -> None:
    ids = [f"202607{i:02d}T000000Z-{n:08x}" for i in range(10, 20) for n in range(10)]
    first = [audit_sample(r).sampled for r in ids]
    second = [audit_sample(r).sampled for r in ids]
    assert first == second  # reproducible, no RNG


def test_ordinary_rate_approx_20_percent() -> None:
    ids = [f"run-{n:06d}" for n in range(2000)]
    rate = sum(audit_sample(r).sampled for r in ids) / len(ids)
    assert abs(rate - ORDINARY_RATE) < 0.03  # hash sampling lands near the policy rate


def test_reason_names_the_policy() -> None:
    assert "20%" in audit_sample("run-x").reason
    assert "promotions" in audit_sample("run-x", promotion=True).reason
