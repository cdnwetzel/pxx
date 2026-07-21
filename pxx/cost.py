"""Phase 12.4: pluggable cost accounting.

A :class:`CostLedger` records per-leg usage (tokens, seconds, model) and
returns a :class:`LegCost` whose ``usd`` is ``None`` whenever a dollar value
cannot be grounded in the versioned price table. **Never fabricate dollar
values**: unknown models, unknown providers, and local compute all record
usage with ``usd=None``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("pxx.cost")

#: Version stamp of the built-in price table. Bump on every price change.
PRICE_TABLE_VERSION = "2025-06"

#: USD per 1M tokens (input rate) for known cloud models.
PRICES: dict[str, float] = {
    "gpt-4o": 2.50,
    "gpt-4o-mini": 0.15,
    "gpt-4.1": 2.00,
    "gpt-4.1-mini": 0.40,
    "gpt-4.1-nano": 0.10,
    "o4-mini": 1.10,
    "claude-opus-4": 15.00,
    "claude-sonnet-4": 3.00,
    "claude-haiku-3.5": 0.80,
}

#: Providers whose compute is local (seconds-only, never priced).
LOCAL_PROVIDERS = frozenset({"ollama", "vllm"})

#: Providers billed against the versioned price table.
CLOUD_PROVIDERS = frozenset({"openai", "anthropic"})


@dataclass(frozen=True)
class LegCost:
    """Cost/usage attribution for one leg of a run."""

    leg: str
    tokens: int
    seconds: float
    model: str
    usd: float | None  # None = unknown / not priced (never fabricated)


class CostLedger(Protocol):
    """Pluggable per-leg cost accounting."""

    def record(self, leg: str, tokens: int, seconds: float, model: str) -> LegCost:
        """Record one leg's usage and return its (possibly unpriced) cost."""
        ...


class PriceTableCost:
    """Cloud pricing from the versioned table; unknown model -> usd None."""

    def __init__(self, prices: dict[str, float] | None = None) -> None:
        self.prices = dict(PRICES if prices is None else prices)
        self.legs: list[LegCost] = []

    def record(self, leg: str, tokens: int, seconds: float, model: str) -> LegCost:
        rate = self.prices.get(model)
        usd = round(tokens * rate / 1_000_000, 6) if rate is not None else None
        if usd is None:
            log.info("no price table entry for model %r; cost recorded as unknown", model)
        cost = LegCost(leg=leg, tokens=tokens, seconds=seconds, model=model, usd=usd)
        self.legs.append(cost)
        return cost

    def total_usd(self) -> float | None:
        """Sum of priced legs; None when no leg could be priced."""
        known = [leg.usd for leg in self.legs if leg.usd is not None]
        return round(sum(known), 6) if known else None


class LocalCost:
    """Local compute: seconds-based accounting, usd always None.

    Local models have no invoice; fabricating a dollar value would be a
    category error. Usage (tokens/seconds) is still recorded per leg.
    """

    def __init__(self) -> None:
        self.legs: list[LegCost] = []

    def record(self, leg: str, tokens: int, seconds: float, model: str) -> LegCost:
        cost = LegCost(leg=leg, tokens=tokens, seconds=seconds, model=model, usd=None)
        self.legs.append(cost)
        return cost

    def total_seconds(self) -> float:
        return round(sum(leg.seconds for leg in self.legs), 3)


def ledger_for(provider: str) -> CostLedger:
    """Pick the ledger for a provider.

    Local providers get seconds-only accounting; known cloud providers get
    the versioned price table; anything unknown records usage with cost
    None (an empty price table prices nothing).
    """
    if provider in LOCAL_PROVIDERS:
        return LocalCost()
    if provider in CLOUD_PROVIDERS:
        return PriceTableCost()
    log.info("unknown provider %r; usage recorded, cost unknown", provider)
    return PriceTableCost(prices={})
