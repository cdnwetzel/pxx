"""Tests for pxx.cost: versioned price table, local seconds, never-fabricate."""

from __future__ import annotations

from pxx.cost import (
    PRICE_TABLE_VERSION,
    PRICES,
    LegCost,
    LocalCost,
    PriceTableCost,
    ledger_for,
)


def test_known_model_is_priced() -> None:
    ledger = PriceTableCost()
    cost = ledger.record("edit", tokens=1_000_000, seconds=12.5, model="gpt-4o-mini")
    assert cost.usd == PRICES["gpt-4o-mini"]
    assert cost.tokens == 1_000_000
    assert cost.seconds == 12.5
    assert cost.leg == "edit"
    assert ledger.total_usd() == PRICES["gpt-4o-mini"]


def test_price_table_math_scales_with_tokens() -> None:
    ledger = PriceTableCost()
    cost = ledger.record("review", tokens=250_000, seconds=3.0, model="claude-sonnet-4")
    assert cost.usd == round(250_000 * PRICES["claude-sonnet-4"] / 1_000_000, 6)


def test_unknown_model_records_usage_but_no_dollars() -> None:
    ledger = PriceTableCost()
    cost = ledger.record("edit", tokens=500, seconds=1.0, model="mystery-model")
    assert cost.usd is None  # never fabricated
    assert ledger.legs == [cost]  # usage still recorded
    assert ledger.total_usd() is None


def test_total_usd_sums_only_priced_legs() -> None:
    ledger = PriceTableCost()
    ledger.record("a", tokens=1_000_000, seconds=1.0, model="gpt-4o-mini")
    ledger.record("b", tokens=1_000_000, seconds=1.0, model="unknown")
    assert ledger.total_usd() == PRICES["gpt-4o-mini"]


def test_local_cost_is_seconds_based_and_never_priced() -> None:
    ledger = LocalCost()
    first = ledger.record("edit", tokens=900, seconds=30.0, model="qwen2.5-coder:7b")
    second = ledger.record("review", tokens=1200, seconds=45.0, model="qwen2.5-coder:7b")
    assert first.usd is None and second.usd is None
    assert ledger.total_seconds() == 75.0
    assert len(ledger.legs) == 2


def test_ledger_for_provider_routing() -> None:
    assert isinstance(ledger_for("ollama"), LocalCost)
    assert isinstance(ledger_for("vllm"), LocalCost)
    assert isinstance(ledger_for("openai"), PriceTableCost)
    cloud = ledger_for("openai")
    assert cloud.record("x", tokens=1_000_000, seconds=1.0, model="gpt-4o").usd is not None


def test_unknown_provider_records_usage_with_cost_none() -> None:
    ledger = ledger_for("some-new-provider")
    cost = ledger.record("edit", tokens=100, seconds=2.0, model="whatever")
    assert isinstance(cost, LegCost)
    assert cost.usd is None
    assert cost.tokens == 100


def test_price_table_is_versioned() -> None:
    assert PRICE_TABLE_VERSION
    assert "gpt-4o" in PRICES  # table actually keyed by model
