"""Measured observed_utility via memory ablations (Phase 20.3).

The only reliable way to know whether an observation HELPS is to compare
matched runs with vs without it injected. This module attributes that delta
from the run record stream (``injected_observation_ids`` + task_id, wired
in B2) and writes the measured value back to the store, where it feeds
search ranking. Nothing here is estimated: no matched pairs, no utility
change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .store import MemoryStore


@dataclass(frozen=True)
class UtilityMeasurement:
    """The measured effect of one observation across matched run pairs."""

    observation_id: str
    pairs: int  # matched task groups contributing evidence
    runs_with: int
    runs_without: int
    success_with: float  # success rate with the observation injected
    success_without: float
    utility: float  # 0..1 measured value written to the store


def compute_utility(
    runs_with: list[Mapping[str, Any]], runs_without: list[Mapping[str, Any]]
) -> float:
    """Measured utility in [0, 1] from matched with/without run sets.

    0.5 = no measurable effect; success-rate delta dominates, cheaper/faster
    runs (fewer rounds) add a small term. Never exceeds [0, 1]; empty side
    means no measurement (caller must not write).
    """
    if not runs_with or not runs_without:
        raise ValueError("compute_utility needs both with and without runs")
    success_with = sum(1 for r in runs_with if r.get("ok")) / len(runs_with)
    success_without = sum(1 for r in runs_without if r.get("ok")) / len(runs_without)
    rounds_with = sum(float(r.get("rounds") or 0) for r in runs_with) / len(runs_with)
    rounds_without = sum(float(r.get("rounds") or 0) for r in runs_without) / len(runs_without)
    success_delta = success_with - success_without  # [-1, 1]
    rounds_term = max(-1.0, min(1.0, (rounds_without - rounds_with) / 10.0))
    return max(0.0, min(1.0, 0.5 + 0.45 * success_delta + 0.05 * rounds_term))


def measure_utilities(
    store: MemoryStore,
    project: str,
    runs: Iterable[Mapping[str, Any]],
) -> list[UtilityMeasurement]:
    """Attribute measured utility for every observation seen in ``runs``.

    ``runs`` are run records (mappings) carrying ``task_id``, ``ok``,
    ``rounds``, and ``injected_observation_ids``. For each observation with
    matched evidence (at least one task group having BOTH with- and
    without-injection runs), the measured value is written via
    ``store.set_utility``. Observations without matched pairs keep their
    prior value — no fabricated measurements.
    """
    by_task: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        task_id = str(run.get("task_id") or "")
        if task_id:
            by_task.setdefault(task_id, []).append(run)

    obs_ids: set[str] = set()
    for run in runs:
        for oid in run.get("injected_observation_ids") or ():
            obs_ids.add(str(oid))

    measurements: list[UtilityMeasurement] = []
    for oid in sorted(obs_ids):
        with_runs: list[Mapping[str, Any]] = []
        without_runs: list[Mapping[str, Any]] = []
        pairs = 0
        for task_runs in by_task.values():
            injected = [r for r in task_runs if oid in (r.get("injected_observation_ids") or ())]
            clean = [r for r in task_runs if oid not in (r.get("injected_observation_ids") or ())]
            if injected and clean:
                pairs += 1
                with_runs.extend(injected)
                without_runs.extend(clean)
        if not pairs:
            continue  # no matched evidence; never guess
        utility = compute_utility(with_runs, without_runs)
        written = False
        try:
            written = store.set_utility(int(oid), utility)
        except (ValueError, TypeError):
            written = False
        if written:
            measurements.append(
                UtilityMeasurement(
                    observation_id=oid,
                    pairs=pairs,
                    runs_with=len(with_runs),
                    runs_without=len(without_runs),
                    success_with=sum(1 for r in with_runs if r.get("ok")) / len(with_runs),
                    success_without=sum(1 for r in without_runs if r.get("ok")) / len(without_runs),
                    utility=utility,
                )
            )
    return measurements


__all__ = ["UtilityMeasurement", "compute_utility", "measure_utilities"]
