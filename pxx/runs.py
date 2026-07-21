"""Phase 12: read-side projections over run directories.

Reads ``state_dir/runs/<run_id>/`` (``manifest.json``, ``task.json``,
``outcome.json``) into :class:`RunRecord` projections and derives aggregate
metrics. Partial run dirs (a crashed run may lack ``outcome.json``) are
tolerated: missing data degrades to neutral defaults, never an exception.

Pure functions with the filesystem injected via ``state_dir``/``path``
arguments — no I/O at import time.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("pxx.runs")


@dataclass(frozen=True)
class RunRecord:
    """RunOutcome-ish projection of one run directory."""

    run_id: str
    agent_version_id: str = ""
    backend: str = ""
    provider: str = ""
    model: str = ""
    code: str = ""  # terminal code; "" when outcome.json is missing
    summary: str = ""
    rounds: int = 0
    tokens: int = 0
    diff_lines: int = 0
    cost_usd: float | None = None
    memory: bool = False  # memory context was injected (from task.json)
    memory_context_bytes: int = 0
    ts: float = 0.0
    # Phase 11.3 identity threading (from task.json)
    task_id: str = ""
    repository_fingerprint: str = ""
    starting_commit: str = ""
    # Phase 12.1 per-leg evidence (from outcome.json)
    contributing_codes: tuple[str, ...] = ()
    edit_seconds: float = 0.0
    test_seconds: float = 0.0
    review_seconds: float = 0.0
    files_changed: int = 0
    introduced_failures: int = 0
    audit_sampled: bool = False

    @property
    def ok(self) -> bool:
        return self.code == "COMPLETED"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _to_int(value: Any, default: int = 0) -> int:
    """Defensive int coercion: malformed data degrades to a neutral default,
    never an exception (one bad run dir must not crash every projection)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_run(run_dir: Path) -> RunRecord | None:
    """Project one run dir; None only when the dir holds no run data at all."""
    manifest = _read_json(run_dir / "manifest.json")
    outcome = _read_json(run_dir / "outcome.json")
    task = _read_json(run_dir / "task.json")
    if not (manifest or outcome or task):
        return None
    cost = outcome.get("cost_usd")
    return RunRecord(
        run_id=str(outcome.get("run_id") or task.get("run_id") or run_dir.name),
        agent_version_id=str(
            outcome.get("agent_version_id") or manifest.get("agent_version_id") or ""
        ),
        backend=str(manifest.get("backend") or ""),
        provider=str(manifest.get("provider") or ""),
        model=str(manifest.get("model") or ""),
        code=str(outcome.get("code") or ""),
        summary=str(outcome.get("summary") or ""),
        rounds=_to_int(outcome.get("rounds")),
        tokens=_to_int(outcome.get("tokens")),
        diff_lines=_to_int(outcome.get("diff_lines")),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        memory=bool(task.get("memory") or False),
        memory_context_bytes=_to_int(task.get("memory_context_bytes")),
        ts=_to_float(outcome.get("ts") or task.get("ts")),
        task_id=str(task.get("task_id") or ""),
        repository_fingerprint=str(task.get("repository_fingerprint") or ""),
        starting_commit=str(task.get("starting_commit") or ""),
        contributing_codes=tuple(str(c) for c in (outcome.get("contributing_codes") or ())),
        edit_seconds=_to_float(outcome.get("edit_seconds")),
        test_seconds=_to_float(outcome.get("test_seconds")),
        review_seconds=_to_float(outcome.get("review_seconds")),
        files_changed=_to_int(outcome.get("files_changed")),
        introduced_failures=_to_int(outcome.get("introduced_failures")),
        audit_sampled=bool(outcome.get("audit_sampled") or False),
    )


def _iter_run_dirs(state_dir: Path) -> Iterable[Path]:
    runs_root = Path(state_dir) / "runs"
    try:
        entries = sorted(runs_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            yield entry


def list_runs(state_dir: Path, limit: int = 20) -> list[RunRecord]:
    """Most recent runs first (run_id carries a UTC timestamp prefix)."""
    runs = [rec for d in _iter_run_dirs(state_dir) if (rec := _load_run(d)) is not None]
    runs.sort(key=lambda r: (r.run_id, r.ts), reverse=True)
    return runs[: max(0, limit)]


def group_by_agent(state_dir: Path) -> dict[str, list[RunRecord]]:
    """All runs grouped by agent_version_id ('unknown' when unattributable)."""
    groups: dict[str, list[RunRecord]] = {}
    for run in list_runs(state_dir, limit=1_000_000):
        groups.setdefault(run.agent_version_id or "unknown", []).append(run)
    return groups


@dataclass(frozen=True)
class MetricsSummary:
    total: int
    completed: int
    failed: int
    success_rate: float
    total_rounds: int
    total_tokens: int
    total_diff_lines: int
    known_cost_usd: float | None  # None when no run has a priced cost
    by_code: dict[str, int]


def metrics_summary(runs: Iterable[RunRecord]) -> MetricsSummary:
    runs = list(runs)
    by_code: dict[str, int] = {}
    completed = 0
    costs: list[float] = []
    for run in runs:
        by_code[run.code or "UNKNOWN"] = by_code.get(run.code or "UNKNOWN", 0) + 1
        completed += run.ok
        if run.cost_usd is not None:
            costs.append(run.cost_usd)
    total = len(runs)
    return MetricsSummary(
        total=total,
        completed=completed,
        failed=total - completed,
        success_rate=round(completed / total, 4) if total else 0.0,
        total_rounds=sum(r.rounds for r in runs),
        total_tokens=sum(r.tokens for r in runs),
        total_diff_lines=sum(r.diff_lines for r in runs),
        known_cost_usd=round(sum(costs), 6) if costs else None,
        by_code=dict(sorted(by_code.items())),
    )


@dataclass(frozen=True)
class FailureReport:
    total_failures: int
    by_code: dict[str, int]
    runs: tuple[RunRecord, ...]


def metrics_failures(runs: Iterable[RunRecord]) -> FailureReport:
    """Failure-focused view: non-COMPLETED runs with counts by terminal code."""
    failures = tuple(r for r in runs if not r.ok)
    by_code: dict[str, int] = {}
    for run in failures:
        key = run.code or "UNKNOWN"
        by_code[key] = by_code.get(key, 0) + 1
    return FailureReport(
        total_failures=len(failures),
        by_code=dict(sorted(by_code.items())),
        runs=failures,
    )


@dataclass(frozen=True)
class CohortStats:
    runs: int
    completed: int
    success_rate: float
    avg_rounds: float
    avg_tokens: float


def _cohort(runs: list[RunRecord]) -> CohortStats:
    total = len(runs)
    completed = sum(r.ok for r in runs)
    return CohortStats(
        runs=total,
        completed=completed,
        success_rate=round(completed / total, 4) if total else 0.0,
        avg_rounds=round(sum(r.rounds for r in runs) / total, 2) if total else 0.0,
        avg_tokens=round(sum(r.tokens for r in runs) / total, 2) if total else 0.0,
    )


@dataclass(frozen=True)
class MemoryImpact:
    """Outcomes with vs without injected memory context (correlation only)."""

    with_memory: CohortStats
    without_memory: CohortStats
    delta_success_rate: float  # with - without; 0.0 when a cohort is empty


def memory_impact(runs: Iterable[RunRecord]) -> MemoryImpact:
    runs = list(runs)
    with_mem = _cohort([r for r in runs if r.memory or r.memory_context_bytes > 0])
    without_mem = _cohort([r for r in runs if not (r.memory or r.memory_context_bytes > 0)])
    delta = 0.0
    if with_mem.runs and without_mem.runs:
        delta = round(with_mem.success_rate - without_mem.success_rate, 4)
    return MemoryImpact(with_memory=with_mem, without_memory=without_mem, delta_success_rate=delta)


def export_jsonl(runs: Iterable[RunRecord], path: Path) -> int:
    """Write one JSON object per run to ``path``. Returns records written."""
    count = 0
    with Path(path).open("w") as fh:
        for run in runs:
            fh.write(json.dumps(asdict(run), sort_keys=True, default=str) + "\n")
            count += 1
    return count


@dataclass(frozen=True)
class MetricsComparison:
    """Per-metric delta between two run sets (A -> B)."""

    a: MetricsSummary
    b: MetricsSummary
    delta_success_rate: float
    delta_avg_rounds: float
    delta_avg_tokens: float
    delta_known_cost_usd: float | None  # None when either side is unpriced


def metrics_compare(runs_a: Iterable[RunRecord], runs_b: Iterable[RunRecord]) -> MetricsComparison:
    """Compare two run sets metric-by-metric (never one composite score)."""
    a = metrics_summary(runs_a)
    b = metrics_summary(runs_b)

    def _avg(total: int, n: int) -> float:
        return total / n if n else 0.0

    cost_delta: float | None = None
    if a.known_cost_usd is not None and b.known_cost_usd is not None:
        cost_delta = round(b.known_cost_usd - a.known_cost_usd, 6)
    return MetricsComparison(
        a=a,
        b=b,
        delta_success_rate=round(b.success_rate - a.success_rate, 4),
        delta_avg_rounds=round(_avg(b.total_rounds, b.total) - _avg(a.total_rounds, a.total), 4),
        delta_avg_tokens=round(_avg(b.total_tokens, b.total) - _avg(a.total_tokens, a.total), 4),
        delta_known_cost_usd=cost_delta,
    )


def quarantined_agents(state_dir: Path) -> set[str]:
    """Agent ids QUARANTINED by model drift (Phase 11 amend).

    When the SAME model name shows up with different served fingerprints
    (an Ollama tag re-pulled to different bytes), the older fingerprint's
    agents are quarantined: their baselines must not judge the new bytes.
    The newest fingerprint group (by latest run ts) stays.
    """
    groups = group_by_agent(state_dir)
    by_model: dict[str, list[tuple[str, str, float]]] = {}
    for agent_id, runs in groups.items():
        manifest = _read_json(Path(state_dir) / "runs" / runs[0].run_id / "manifest.json")
        fingerprint = str(manifest.get("model_fingerprint") or "")
        model = str(manifest.get("model") or runs[0].model or "")
        if not fingerprint:
            continue  # unprobed runs can't drift-match; never quarantined
        latest = max(r.ts for r in runs)
        by_model.setdefault(model, []).append((fingerprint, agent_id, latest))
    quarantined: set[str] = set()
    for entries in by_model.values():
        fingerprints = {fp for fp, _, _ in entries}
        if len(fingerprints) < 2:
            continue
        newest_fp = max(entries, key=lambda e: e[2])[0]
        for fp, agent_id, _ in entries:
            if fp != newest_fp:
                quarantined.add(agent_id)
    return quarantined
