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
from dataclasses import dataclass, field
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
    all_findings: list[review_gate.Finding] = field(default_factory=list)
    note: str = ""  # diagnosable reason for NO_REVIEW variants


def _hooks_installed(root: Path) -> bool:
    """True iff BOTH pxx-managed hooks are installed at git's *active* hook path.

    Resolved via `git rev-parse --git-path` so core.hooksPath redirection and
    worktrees (.git-as-file) can't produce a false positive — the dangerous
    direction, since the --yes doctrine's boundary would silently not exist.
    pre-commit carries the scope gate/diff cap/test gates; prepare-commit-msg
    carries the [autonomous] tagging (run #1's untagged commit came from
    exactly this hook being absent).
    """
    for hook_name in ("pre-commit", "prepare-commit-msg"):
        r = subprocess.run(
            ["git", "rev-parse", "--git-path", f"hooks/{hook_name}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if r.returncode != 0:
            return False
        hook = Path(r.stdout.strip())
        if not hook.is_absolute():
            hook = root / hook
        try:
            if "pxx-managed" not in hook.read_text(encoding="utf-8"):
                return False
        except OSError:
            return False
    return True


def _require_hooks(root: Path) -> bool:
    """Shared precondition for every edit-round caller; prints the remedy."""
    if _hooks_installed(root):
        return True
    _say(
        "the pxx git hooks (scope gate, diff cap, [autonomous] tagging) are "
        "not installed — --yes rounds are unbounded without them. "
        "Install: pxx --install-hook"
    )
    return False


def _say(msg: str) -> None:
    print(f"pxx loop: {msg}", file=sys.stderr)


def _failing_tests(root: Path, timeout: float = 600.0) -> set[str] | None:
    """Run pytest and return the set of failing test ids, or None if the run
    itself broke (collection error, missing uv) — the loop fails closed on None.
    `timeout` lets the loop charge the test leg against its wall-clock budget.
    """
    try:
        r = subprocess.run(
            ["uv", "run", "pytest", "-q", "--tb=no", "-rf"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
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


def _run_edit_round(
    root: Path, message: str, scope: str, timeout: float | None = None
) -> int:
    """One bounded edit: a pxx --self-fix subprocess (safety tag, diff cap,
    [autonomous] tagging, execve into aider — all reused). --yes because a
    non-interactive round must never be asked a question.

    `timeout` is the remaining wall-clock budget: a wedged aider must not be
    able to defeat the loop's time guard. A timeout is just a failed round
    (rc 124) — one stop semantics for "the edit round didn't complete".
    """
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
    try:
        r = subprocess.run(cmd, cwd=root, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124
    return r.returncode


def _review_verdict(
    root: Path, timeout: float | None = None, diff_base: str | None = None
) -> RoundResult:
    """Run a review pass and classify the result, including the no-heal cases.

    Each NO_REVIEW variant carries a distinct, diagnosable note — "the pass
    failed/timed out", "ran but left no artifacts" (output-contract breach),
    and "only unparseable findings" are three different remedies. `diff_base`
    scopes the local reviewer to the loop's changes (``diff_base..HEAD``).
    """
    rc = review_gate.run_review_pass(root, timeout=timeout, diff_base=diff_base)
    if rc != 0:
        return RoundResult("NO_REVIEW", [], note="review pass failed or timed out")
    if not review_gate.has_review_evidence(root):
        return RoundResult(
            "NO_REVIEW",
            [],
            note=(
                "review ran but left no artifacts at review/claude/ — "
                "check the output contract"
            ),
        )

    findings = review_gate.collect_active_findings(root)
    verdict = review_gate.compute_verdict(findings)
    healable = [f for f in findings if f.severity.upper() not in _NON_HEALABLE]

    if verdict == "REVISE" and not healable:
        # REVISE driven only by UNPARSEABLE findings: the remedy is fixing or
        # re-running the review, not pointing aider at a malformed header.
        return RoundResult(
            "NO_REVIEW",
            [],
            all_findings=findings,
            note="review produced only unparseable findings — fix or re-run it",
        )
    return RoundResult(verdict, healable, all_findings=findings)


def _format_scope(root: Path, scope: str) -> None:
    """Deterministically format the round's output and commit the fixup.

    Run #1 left aider's output check-clean but format-dirty, which would block
    APPROVE forever while the healing message never mentioned lint. Formatting
    is a solved problem — run the formatter, don't ask a 14B to do it.
    """
    subprocess.run(
        ["uv", "run", "ruff", "format", scope],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=60,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", scope],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if dirty.stdout.strip():
        subprocess.run(["git", "add", scope], cwd=root, check=False, timeout=10)
        subprocess.run(
            ["git", "commit", "-q", "-m", "[autonomous] style: ruff format (loop)"],
            cwd=root,
            check=False,
            timeout=120,
        )


def _lint_feedback(root: Path) -> str:
    """Concise ruff output for the healing message when the lint gate is red —
    the model must be told WHAT is wrong, not just re-fed the same findings."""
    r = subprocess.run(
        ["uv", "run", "ruff", "check", "pxx/", "tests/", "--output-format=concise"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    lines = r.stdout.strip().splitlines()[:15]
    return "Lint errors to fix:\n" + "\n".join(lines) if lines else ""


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
    if not _require_hooks(root):
        return 1
    start_sha = _head_sha(root)

    baseline = _failing_tests(root)
    if baseline is None:
        _say("cannot measure the test baseline (pytest run broke) — refusing.")
        return 1
    _say(f"baseline: {len(baseline)} failing test(s); cap={max_rounds} rounds.")

    state = workflow.load_state(root) or workflow.WorkflowState()
    prev_baseline_failing = baseline
    prev_healable: int | None = None
    message = task

    for round_no in range(1, max_rounds + 1):
        elapsed = time.monotonic() - started
        if elapsed > max_seconds:
            _say(f"wall-clock budget ({max_seconds:.0f}s) exhausted — stopping.")
            return 1

        _say(f"round {round_no}: edit")
        # The subprocess gets the REMAINING budget (floored) so a wedged aider
        # can't defeat the time guard between top-of-round checks.
        t0 = time.monotonic()
        edit_rc = _run_edit_round(
            root, message, scope, timeout=max(60.0, max_seconds - elapsed)
        )
        edit_s = time.monotonic() - t0
        if edit_rc != 0:
            why = "timed out" if edit_rc == 124 else f"failed (rc {edit_rc})"
            _say(f"edit round {why} — stopping (fail closed).")
            workflow.save_state(
                workflow.transition(state, "rejected", review_verdict="EDIT_FAILED"),
                root,
            )
            try:
                audit.write_session_start(
                    {
                        "session_class": "loop-round",
                        "round": round_no,
                        "verdict": "EDIT_FAILED",
                        "edit_rc": edit_rc,
                    }
                )
            except Exception:
                pass
            return 1

        _format_scope(root, scope)

        t0 = time.monotonic()
        remaining = max(60.0, max_seconds - (time.monotonic() - started))
        failing = _failing_tests(root, timeout=min(600.0, remaining))
        test_s = time.monotonic() - t0
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

        t0 = time.monotonic()
        # The review leg is charged against the SAME wall-clock budget as the
        # edit leg (its F3 sibling) — one review must not silently consume the
        # whole loop's time.
        remaining = max(60.0, max_seconds - (time.monotonic() - started))
        result = _review_verdict(
            root, timeout=min(900.0, remaining), diff_base=start_sha
        )
        review_s = time.monotonic() - t0
        state = workflow.transition(
            state,
            "review_pending",
            healing_attempts=state.healing_attempts + 1,
            review_verdict=result.verdict,
        )
        workflow.save_state(state, root)
        # Per-round audit deliberately reuses write_session_start: one JSONL
        # stream for all session events; session_class "loop-round" is the
        # discriminator.
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
                    # Run #2 calibration capture: the message that drove this
                    # round (steering is a measurement, not a vibe), per-leg
                    # wall-clock, and reviewer format compliance.
                    "message": message[:2000],
                    "edit_s": round(edit_s),
                    "test_s": round(test_s),
                    "review_s": round(review_s),
                    "findings_by_severity": {
                        sev: sum(
                            1 for f in result.all_findings if f.severity.upper() == sev
                        )
                        for sev in ("P0", "P1", "P2", "UNPARSEABLE")
                    },
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
                result.note
                or "no usable review evidence — fix/re-run the review, not "
                "another edit round."
            )
            return 1

        # REVISE (or APPROVE blocked by failing baseline tests / lint):
        # progress guard before another round. With a non-empty baseline the
        # metric is the baseline failing set; with a GREEN baseline that rule
        # is degenerate (0 >= 0 stops every loop at round 2), so progress is
        # measured on the loop's actual work: healable findings must strictly
        # decrease between rounds.
        if round_no > 1:
            if baseline:
                if len(baseline_failing) >= len(prev_baseline_failing):
                    _say(
                        "no progress on the baseline failing set "
                        f"({len(prev_baseline_failing)} → {len(baseline_failing)}) "
                        "— stopping."
                    )
                    workflow.save_state(
                        workflow.transition(
                            state, "rejected", review_verdict=result.verdict
                        ),
                        root,
                    )
                    return 1
            elif prev_healable is not None and len(result.healable) >= prev_healable:
                _say(
                    "no progress on healable findings "
                    f"({prev_healable} → {len(result.healable)}) — stopping."
                )
                workflow.save_state(
                    workflow.transition(
                        state, "rejected", review_verdict=result.verdict
                    ),
                    root,
                )
                return 1
        prev_baseline_failing = baseline_failing
        prev_healable = len(result.healable)
        message = _healing_message(task, result.healable, failing)
        if lint_rc != 0:
            lint_note = _lint_feedback(root)
            if lint_note:
                message = f"{message}\n\n{lint_note}"

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
    if not _require_hooks(root):
        return 1
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
    edit_rc = _run_edit_round(root, message, scope, timeout=DEFAULT_MAX_SECONDS)
    if edit_rc != 0:
        why = "timed out" if edit_rc == 124 else f"failed (rc {edit_rc})"
        _say(f"edit round {why} — not reviewing a round that didn't complete.")
        return 1
    _format_scope(root, scope)

    result = _review_verdict(root)
    _say(f"post-heal verdict: {result.verdict}")
    return 0 if result.verdict == "APPROVE" else 1
