"""Closed-loop autonomy driver (#009): edit → test → review → heal, bounded.

`--heal` is exactly one REVISE round; `--loop` is a fold over it. The driver is
a thin orchestrator: each edit round is a `pxx --self-fix` subprocess (which
reuses the safety tag, diff cap, [autonomous] commit tagging, scope export, and
the execve-into-aider handoff), verification is `self_modes`, and the verdict
comes from the deterministic review gate. Healing prompts are built from the
gate's findings plus the driver's own ground truth (the failing-test list) —
never from raw model suggestions, and never via fuzzy retrieval.

Guards (any one fires → the loop stops):
- round cap (default 3)
- baseline-set monotonic progress: failures within the test set that was
  failing BEFORE round 1 must strictly decrease every round; tests the loop
  itself introduces are tracked separately and reported, not gated on
- cumulative diff budget across all rounds (the per-commit cap alone would let
  N rounds smuggle an N×cap rewrite)
- wall-clock budget (inference is local/free, so the budget is time+rounds,
  not dollars)

No-heal special cases: a NO_REVIEW verdict (no review artifacts) or a REVISE
driven only by UNPARSEABLE findings means the remedy is running/fixing the
review — feeding either into an edit round would aim aider at nothing (or at a
malformed markdown header). The loop never pushes; APPROVE just stops with
tagged commits.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pxx import audit, review_gate, self_modes, workflow

DEFAULT_MAX_ROUNDS = 3
DEFAULT_DIFF_BUDGET_LINES = 150
DEFAULT_MAX_SECONDS = 1800.0

# Severities that an edit round can actually act on: P0 is the REJECT path,
# P2 never blocks, UNPARSEABLE is a review artifact problem, not a code one.
_NON_HEALABLE = {"P0", "P2", "UNPARSEABLE"}


@dataclass(frozen=True)
class RoundResult:
    verdict: str  # APPROVE | REVISE | REJECT | NO_REVIEW
    healable: list[review_gate.Finding]
    failing_tests: set[str]


def _say(msg: str) -> None:
    print(f"pxx loop: {msg}", file=sys.stderr)


def _failing_tests(root: Path) -> set[str] | None:
    """Run pytest and return the set of failing test ids, or None if the run
    itself broke (collection error, missing uv) — the loop fails closed on None.
    """
    try:
        r = subprocess.run(
            ["uv", "run", "pytest", "-q", "--tb=no", "-rf"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode not in (0, 1):  # 0 = green, 1 = test failures; else broken
        return None
    return {
        m.group(1) for m in re.finditer(r"^FAILED ([^\s]+)", r.stdout, re.MULTILINE)
    }


def _diff_lines_since(root: Path, base_sha: str) -> int:
    """Total added+removed lines from base_sha to HEAD (cumulative budget)."""
    r = subprocess.run(
        ["git", "diff", "--numstat", f"{base_sha}..HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    total = 0
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            total += int(parts[0]) + int(parts[1])
    return total


def _head_sha(root: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return r.stdout.strip()


def _run_edit_round(root: Path, message: str, scope: str) -> int:
    """One bounded edit: a pxx --self-fix subprocess (safety tag, diff cap,
    [autonomous] tagging, execve into aider — all reused). --yes because a
    non-interactive round must never be asked a question."""
    cmd = [
        sys.executable,
        "-m",
        "pxx.cli",
        "--self-fix",
        message,
        "--scope",
        scope,
        "--yes",
        "--no-stream",
    ]
    r = subprocess.run(cmd, cwd=root, check=False)
    return r.returncode


def _review_verdict(root: Path) -> RoundResult:
    """Run a review pass and classify the result, including the no-heal cases."""
    rc = review_gate.run_review_pass(root)
    if rc != 0:
        return RoundResult("NO_REVIEW", [], set())
    if not review_gate.has_review_evidence(root):
        return RoundResult("NO_REVIEW", [], set())

    findings = review_gate.collect_active_findings(root)
    verdict = review_gate.compute_verdict(findings)
    healable = [f for f in findings if f.severity.upper() not in _NON_HEALABLE]

    if verdict == "REVISE" and not healable:
        # REVISE driven only by UNPARSEABLE findings: the remedy is fixing or
        # re-running the review, not pointing aider at a malformed header.
        return RoundResult("NO_REVIEW", [], set())
    return RoundResult(verdict, healable, set())


def _healing_message(
    task: str, healable: list[review_gate.Finding], failing: set[str]
) -> str:
    """Findings from the deterministic gate + the driver's own ground truth."""
    parts = [task]
    prompt = review_gate.build_healing_prompt(healable)
    if prompt:
        parts.append(prompt)
    if failing:
        parts.append(
            "Currently failing tests:\n" + "\n".join(f"- {t}" for t in sorted(failing))
        )
    return "\n\n".join(parts)


def run_loop(
    root: Path,
    task: str,
    scope: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    diff_budget: int = DEFAULT_DIFF_BUDGET_LINES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> int:
    """Drive bounded edit→test→review rounds to a terminal verdict.

    Returns 0 only on APPROVE; 1 on every guard stop, REJECT, or no-review
    outcome (fail closed). Never pushes.
    """
    started = time.monotonic()
    start_sha = _head_sha(root)

    baseline = _failing_tests(root)
    if baseline is None:
        _say("cannot measure the test baseline (pytest run broke) — refusing.")
        return 1
    _say(f"baseline: {len(baseline)} failing test(s); cap={max_rounds} rounds.")

    state = workflow.load_state(root) or workflow.WorkflowState()
    prev_baseline_failing = baseline
    message = task

    for round_no in range(1, max_rounds + 1):
        if time.monotonic() - started > max_seconds:
            _say(f"wall-clock budget ({max_seconds:.0f}s) exhausted — stopping.")
            return 1

        _say(f"round {round_no}: edit")
        _run_edit_round(root, message, scope)

        failing = _failing_tests(root)
        if failing is None:
            _say("test run broke mid-loop — stopping (fail closed).")
            return 1
        lint_rc = self_modes.self_lint(root)

        spent = _diff_lines_since(root, start_sha)
        if spent > diff_budget:
            _say(
                f"cumulative diff budget exceeded ({spent} > {diff_budget}) — stopping."
            )
            return 1

        baseline_failing = failing & baseline
        introduced_failing = failing - baseline
        if introduced_failing:
            _say(
                f"note: {len(introduced_failing)} new failing test(s) introduced by the loop (tracked, not gated)."
            )

        result = _review_verdict(root)
        state = workflow.transition(
            state,
            "review_pending",
            healing_attempts=state.healing_attempts + 1,
            review_verdict=result.verdict,
        )
        workflow.save_state(state, root)
        try:
            audit.write_session_start(
                {
                    "session_class": "loop-round",
                    "round": round_no,
                    "verdict": result.verdict,
                    "baseline_failing": len(baseline_failing),
                    "introduced_failing": len(introduced_failing),
                    "diff_lines": spent,
                    "lint_rc": lint_rc,
                }
            )
        except Exception:
            pass

        _say(
            f"round {round_no}: verdict={result.verdict} "
            f"baseline-failing={len(baseline_failing)} diff={spent}"
        )

        if result.verdict == "APPROVE" and not baseline_failing and lint_rc == 0:
            workflow.save_state(
                workflow.transition(state, "approved", review_verdict="APPROVE"),
                root,
            )
            _say(
                "APPROVE — stopping. Commits stay local ([autonomous]); push is yours."
            )
            return 0
        if result.verdict == "REJECT":
            workflow.save_state(
                workflow.transition(state, "rejected", review_verdict="REJECT"), root
            )
            _say("REJECT (P0) — stopping for a human. Tree left for inspection.")
            return 1
        if result.verdict == "NO_REVIEW":
            workflow.save_state(
                workflow.transition(state, "rejected", review_verdict="NO_REVIEW"),
                root,
            )
            _say(
                "no usable review evidence (absent or all-unparseable) — the "
                "remedy is fixing/re-running the review, not another edit round."
            )
            return 1

        # REVISE (or APPROVE blocked by failing baseline tests / lint):
        # progress guard before another round.
        if round_no > 1 and len(baseline_failing) >= len(prev_baseline_failing):
            _say(
                "no progress on the baseline failing set "
                f"({len(prev_baseline_failing)} → {len(baseline_failing)}) — stopping."
            )
            workflow.save_state(
                workflow.transition(state, "rejected", review_verdict=result.verdict),
                root,
            )
            return 1
        prev_baseline_failing = baseline_failing
        message = _healing_message(task, result.healable, failing)

    _say(f"round cap ({max_rounds}) reached — stopping.")
    workflow.save_state(
        workflow.transition(state, "rejected", review_verdict="ROUND_CAP"), root
    )
    return 1


def heal_once(root: Path, scope: str) -> int:
    """Exactly one REVISE round against existing review findings.

    The single-round primitive `--loop` folds over; also the handler behind
    `pxx --review --heal`.
    """
    if not review_gate.has_review_evidence(root):
        _say("nothing to heal: no review evidence — run `pxx --review` first.")
        return 1

    findings = review_gate.collect_active_findings(root)
    verdict = review_gate.compute_verdict(findings)
    healable = [f for f in findings if f.severity.upper() not in _NON_HEALABLE]

    if verdict == "APPROVE":
        _say("verdict is APPROVE — nothing to heal.")
        return 0
    if verdict == "REJECT":
        _say("verdict is REJECT (P0) — healing is for P1s; a human owns P0s.")
        return 1
    if not healable:
        _say(
            "REVISE is driven only by unparseable findings — fix or re-run the "
            "review; an edit round has nothing real to aim at."
        )
        return 1

    failing = _failing_tests(root) or set()
    message = _healing_message("Address the review findings below.", healable, failing)
    _run_edit_round(root, message, scope)

    result = _review_verdict(root)
    _say(f"post-heal verdict: {result.verdict}")
    return 0 if result.verdict == "APPROVE" else 1
