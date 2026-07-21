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
  itself introduces GATE APPROVE (a fix that breaks a neighbor cannot earn
  exit 0 — m2 evidence, 2026-07-17) and a stop with live regressions
  terminates as TEST_REGRESSION
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

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pxx import audit, review_gate, tool_capture, workflow
from pxx.scope import is_in_scope

DEFAULT_MAX_ROUNDS = 3
DEFAULT_DIFF_BUDGET_LINES = 150
DEFAULT_MAX_SECONDS = 1800.0
DEFAULT_EDIT_RETRIES = (
    2  # 14B occasionally emits a malformed edit; retry before failing
)

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
            # -rfE reports FAILED *and* ERROR lines. A test that ERRORs (a
            # raising fixture, a collection/import break) is not a pass — but
            # -rf alone reported only FAILED, so an all-error suite parsed to
            # an EMPTY set and read green. In advisory mode this test oracle is
            # the only enforcement gate, so that silence became an APPROVE.
            ["uv", "run", "pytest", "-q", "--tb=no", "-rfE"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode not in (0, 1):  # 0 = green, 1 = test failures; else broken
        return None
    # Both FAILED and ERROR count as not-passing; either populates the set so
    # the loop's gates never treat a broken suite as clean.
    return {
        m.group(2)
        for m in re.finditer(r"^(FAILED|ERROR) ([^\s]+)", r.stdout, re.MULTILINE)
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
    # The child self-fix chdirs to the pxx repo by default (#001); tell it
    # the loop's actual root so eval-fixture loops (#013) edit the fixture,
    # not pxx. For pxx-repo loops this is the same directory as before.
    env = os.environ | {"PXX_SELF_FIX_ROOT": str(root)}
    try:
        r = subprocess.run(cmd, cwd=root, env=env, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124
    return r.returncode


def _run_edit_round_retried(
    root: Path,
    message: str,
    scope: str,
    deadline: float,
    retries: int = DEFAULT_EDIT_RETRIES,
) -> int:
    """Edit round with bounded retries for *genuine* failures.

    The 14B intermittently emits a malformed SEARCH/REPLACE that aider can't
    apply (rc 1) even for a well-formed task; a fresh attempt usually succeeds.
    Timeouts (rc 124 = wedged aider) are NOT retried — that would only burn the
    wall-clock budget. Each attempt gets the remaining budget (`deadline` is the
    absolute monotonic time the loop must stop by); retries stop once under the
    60s floor.
    """
    rc = 1
    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if remaining < 60.0:
            break
        rc = _run_edit_round(root, message, scope, timeout=remaining)
        if rc in (0, 124):
            return rc
        if attempt < retries:
            _say(f"edit round failed (rc {rc}) — retrying ({attempt + 1}/{retries}).")
    return rc


def _review_verdict(
    root: Path,
    timeout: float | None = None,
    diff_base: str | None = None,
    task: str | None = None,
) -> RoundResult:
    """Run a review pass and classify the result, including the no-heal cases.

    Each NO_REVIEW variant carries a distinct, diagnosable note — "the pass
    failed/timed out", "ran but left no artifacts" (output-contract breach),
    and "only unparseable findings" are three different remedies. `diff_base`
    scopes the local reviewer to the loop's changes (``diff_base..HEAD``).
    """
    rc = review_gate.run_review_pass(
        root, timeout=timeout, diff_base=diff_base, task=task
    )
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


def _lint_scope(root: Path, scope: str) -> int:
    """Lint gate limited to the loop's OWN scope, not the whole pxx/ tests/ tree.

    The pre-commit scope gate forbids the loop from committing files outside
    ``scope``, so a pre-existing format/lint issue elsewhere in the tree would
    deadlock APPROVE — the loop can neither fix it (scope gate rejects the
    commit) nor pass the gate. Judge only what the loop can actually own, exactly
    as the baseline-failing-set rule gates on the loop's own regressions and not
    pre-existing red tests. Returns ``check | format`` (0 == clean).
    """
    check = subprocess.run(
        ["uv", "run", "ruff", "check", scope],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=60,
    ).returncode
    fmt = subprocess.run(
        ["uv", "run", "ruff", "format", "--check", scope],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=60,
    ).returncode
    return check | fmt


def _lint_feedback(root: Path, scope: str) -> str:
    """Concise ruff output (scoped) for the healing message when the lint gate is
    red — the model must be told WHAT is wrong, not just re-fed the same findings."""
    r = subprocess.run(
        ["uv", "run", "ruff", "check", scope, "--output-format=concise"],
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


def _out_of_scope_changes(root: Path, start_sha: str, scope: str) -> list[str]:
    """Changed paths since the loop's start commit that fall outside its scope.

    aider commits with ``--no-verify``, so the pre-commit scope gate never sees
    the loop's own commits (confirmed empirically 2026-07-16) — this is the
    loop-level enforcement of the same boundary. Covers committed changes plus
    anything dirty/untracked (the loop starts on a clean tree, so everything
    that appears mid-loop is the loop's own doing).
    """
    committed = subprocess.run(
        ["git", "diff", "--name-only", f"{start_sha}..HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    ).stdout.splitlines()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    ).stdout.splitlines()
    dirty = [line[3:].split(" -> ")[-1] for line in status if line.strip()]
    paths = {p.strip() for p in committed + dirty if p.strip()}
    return sorted(p for p in paths if not is_in_scope(p, [scope]))


def _terminal(
    rc: int,
    code: str,
    rounds: int,
    run_id: str | None,
    agent_version: str | None,
    start_sha: str | None = None,
    end_sha: str | None = None,
) -> int:
    """Write the run's machine-readable terminal record (#012) and return rc.

    The terminal_code names WHY the loop stopped — downstream projection
    (pxx/outcomes.py) never parses messages. Best-effort like every audit
    write; the code must be a member of outcomes.FAILURE_CODES.
    """
    try:
        audit.write_session_start(
            {
                "session_class": "loop-terminal",
                "run_id": run_id,
                "agent_version_id": agent_version,
                "terminal_code": code,
                "rounds": rounds,
                "exit": rc,
                "start_sha": start_sha,
                "end_sha": end_sha,
            }
        )
    except Exception:
        pass
    return rc


def _capture_loop_summary(
    root: Path,
    start_sha: str,
    scope: str,
    task: str,
    verdict: str,
    rounds: int,
    run_id: str | None = None,
    agent_version: str | None = None,
) -> None:
    """Cross-session capture on a terminal review verdict (9.4). Best-effort:
    agentmemory being down degrades to a no-op; never affects the exit code.

    Privacy (a256a04): observation content carries the repo-relative scope, the
    task text, and loop metadata — never absolute paths or hostnames.
    """
    try:
        tool_capture.capture_session_tools(start_sha, root, project=scope)
        tool_capture.post_observations_to_memory(
            [
                {
                    "content": (
                        f"pxx --loop terminal verdict {verdict} after {rounds} "
                        f"round(s) on scope {scope}: {task}"
                    )[:500],
                    "metadata": {
                        "type": "loop_summary",
                        "verdict": verdict,
                        "rounds": rounds,
                        "run_id": run_id,
                        "agent_version_id": agent_version,
                    },
                }
            ],
            project=scope,
        )
    except Exception:
        pass


def run_loop(
    root: Path,
    task: str,
    scope: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    diff_budget: int = DEFAULT_DIFF_BUDGET_LINES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    run_id: str | None = None,
    agent_version: str | None = None,
) -> int:
    """Drive bounded edit→test→review rounds to a terminal verdict.

    Returns 0 only on APPROVE; 1 on every guard stop, REJECT, or no-review
    outcome (fail closed). Never pushes.

    ``run_id``/``agent_version`` (#011): one id links the loop session, every
    round record, child sessions, and the cross-session capture; callers that
    omit them get a generated run_id and no version claim.
    """
    started = time.monotonic()
    if run_id is None:
        run_id = audit.make_session_id()
    advisory = review_gate.review_mode() == "advisory"
    if not _require_hooks(root):
        return _terminal(1, "HOOKS_MISSING", 0, run_id, agent_version)
    preflight_err = review_gate.preflight_review_backend()
    if preflight_err:
        # Advisory mode: a down reviewer must not block a run the
        # deterministic gates can still carry — warn, don't refuse.
        if advisory:
            _say(
                f"review backend preflight failed: {preflight_err} — advisory mode, continuing."
            )
        else:
            _say(
                f"review backend preflight failed: {preflight_err} — refusing to start."
            )
            return _terminal(1, "REVIEW_UNAVAILABLE", 0, run_id, agent_version)
    start_sha = _head_sha(root)

    baseline = _failing_tests(root)
    if baseline is None:
        _say("cannot measure the test baseline (pytest run broke) — refusing.")
        return _terminal(1, "TEST_RUN_FAILED", 0, run_id, agent_version, start_sha)
    _say(f"baseline: {len(baseline)} failing test(s); cap={max_rounds} rounds.")

    state = workflow.load_state(root) or workflow.WorkflowState()
    state.run_id = run_id
    state.agent_version_id = agent_version
    prev_baseline_failing = baseline
    prev_healable: int | None = None
    last_introduced: set[str] = set()
    message = task

    for round_no in range(1, max_rounds + 1):
        elapsed = time.monotonic() - started
        if elapsed > max_seconds:
            _say(f"wall-clock budget ({max_seconds:.0f}s) exhausted — stopping.")
            return _terminal(
                1,
                "TIME_BUDGET_EXCEEDED",
                round_no - 1,
                run_id,
                agent_version,
                start_sha,
            )

        _say(f"round {round_no}: edit")
        # The subprocess gets the REMAINING budget (floored) so a wedged aider
        # can't defeat the time guard between top-of-round checks.
        t0 = time.monotonic()
        edit_rc = _run_edit_round_retried(root, message, scope, started + max_seconds)
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
                        "run_id": run_id,
                        "agent_version_id": agent_version,
                    }
                )
            except Exception:
                pass
            return _terminal(
                1,
                "EDIT_TIMEOUT" if edit_rc == 124 else "EDIT_FAILED",
                round_no,
                run_id,
                agent_version,
                start_sha,
                _head_sha(root),
            )

        _format_scope(root, scope)

        t0 = time.monotonic()
        remaining = max(60.0, max_seconds - (time.monotonic() - started))
        failing = _failing_tests(root, timeout=min(600.0, remaining))
        test_s = time.monotonic() - t0
        if failing is None:
            _say("test run broke mid-loop — stopping (fail closed).")
            return _terminal(
                1,
                "TEST_RUN_FAILED",
                round_no,
                run_id,
                agent_version,
                start_sha,
                _head_sha(root),
            )
        lint_rc = _lint_scope(root, scope)

        spent = _diff_lines_since(root, start_sha)
        if spent > diff_budget:
            _say(
                f"cumulative diff budget exceeded ({spent} > {diff_budget}) — stopping."
            )
            return _terminal(
                1,
                "DIFF_BUDGET_EXCEEDED",
                round_no,
                run_id,
                agent_version,
                start_sha,
                _head_sha(root),
            )

        off_scope = _out_of_scope_changes(root, start_sha, scope)
        if off_scope:
            _say(
                "out-of-scope changes detected (aider commits bypass the "
                f"pre-commit scope gate): {', '.join(off_scope)} — "
                "stopping (fail closed). Tree left for inspection."
            )
            workflow.save_state(
                workflow.transition(state, "rejected", review_verdict="OUT_OF_SCOPE"),
                root,
            )
            return _terminal(
                1,
                "OUT_OF_SCOPE",
                round_no,
                run_id,
                agent_version,
                start_sha,
                _head_sha(root),
            )

        baseline_failing = failing & baseline
        introduced_failing = failing - baseline
        if introduced_failing:
            _say(
                f"note: {len(introduced_failing)} new failing test(s) introduced by the loop — gating APPROVE."
            )

        t0 = time.monotonic()
        # The review leg is charged against the SAME wall-clock budget as the
        # edit leg (its F3 sibling) — one review must not silently consume the
        # whole loop's time.
        remaining = max(60.0, max_seconds - (time.monotonic() - started))
        result = _review_verdict(
            root, timeout=min(900.0, remaining), diff_base=start_sha, task=task
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
                    "run_id": run_id,
                    "agent_version_id": agent_version,
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

        mode_note = " (advisory)" if advisory else ""
        _say(
            f"round {round_no}: verdict={result.verdict}{mode_note} "
            f"baseline-failing={len(baseline_failing)} diff={spent}"
        )
        if advisory and result.healable:
            _say(
                f"advisory: reviewer raised {len(result.healable)} finding(s) — "
                "recorded, not gating (deterministic gates decide)."
            )

        # Advisory mode: the deterministic gates alone decide APPROVE; the
        # reviewer's verdict is recorded but never required. Blocking mode:
        # the reviewer must also say APPROVE.
        if (
            (advisory or result.verdict == "APPROVE")
            and not baseline_failing
            and not introduced_failing
            and lint_rc == 0
        ):
            workflow.save_state(
                workflow.transition(state, "approved", review_verdict="APPROVE"),
                root,
            )
            _capture_loop_summary(
                root,
                start_sha,
                scope,
                task,
                "APPROVE",
                round_no,
                run_id=run_id,
                agent_version=agent_version,
            )
            # APPROVE ships EVIDENCE, not just a verdict (#012 packet): the
            # deterministic facts that earned it, on one line. Full packet via
            # `pxx --verify <run-id>`.
            _say(
                f"evidence: tests green (0 baseline, 0 introduced), lint clean, "
                f"{spent} diff line(s) over {round_no} round(s), "
                f"review={result.verdict}{mode_note}"
            )
            _say(
                "APPROVE — stopping. Commits stay local ([autonomous]); push is yours. "
                f"Evidence: pxx --verify {run_id}"
            )
            return _terminal(
                0,
                "APPROVED",
                round_no,
                run_id,
                agent_version,
                start_sha,
                _head_sha(root),
            )
        # The reviewer-verdict-driven stops below are the GATE. In advisory
        # mode they are skipped entirely — a REJECT/NO_REVIEW never blocks a
        # run whose deterministic gates would otherwise heal or pass.
        if not advisory and result.verdict == "REJECT":
            workflow.save_state(
                workflow.transition(state, "rejected", review_verdict="REJECT"), root
            )
            _capture_loop_summary(
                root,
                start_sha,
                scope,
                task,
                "REJECT",
                round_no,
                run_id=run_id,
                agent_version=agent_version,
            )
            _say("REJECT (P0) — stopping for a human. Tree left for inspection.")
            return _terminal(
                1,
                "REVIEW_REJECTED",
                round_no,
                run_id,
                agent_version,
                start_sha,
                _head_sha(root),
            )
        if not advisory and result.verdict == "NO_REVIEW":
            workflow.save_state(
                workflow.transition(state, "rejected", review_verdict="NO_REVIEW"),
                root,
            )
            _capture_loop_summary(
                root,
                start_sha,
                scope,
                task,
                "NO_REVIEW",
                round_no,
                run_id=run_id,
                agent_version=agent_version,
            )
            _say(
                result.note
                or "no usable review evidence — fix/re-run the review, not "
                "another edit round."
            )
            return _terminal(
                1,
                "REVIEW_UNAVAILABLE",
                round_no,
                run_id,
                agent_version,
                start_sha,
                _head_sha(root),
            )

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
                    return _terminal(
                        1,
                        "TEST_REGRESSION" if introduced_failing else "NO_TEST_PROGRESS",
                        round_no,
                        run_id,
                        agent_version,
                        start_sha,
                        _head_sha(root),
                    )
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
                return _terminal(
                    1,
                    "TEST_REGRESSION" if introduced_failing else "NO_TEST_PROGRESS",
                    round_no,
                    run_id,
                    agent_version,
                    start_sha,
                    _head_sha(root),
                )
        prev_baseline_failing = baseline_failing
        prev_healable = len(result.healable)
        last_introduced = introduced_failing
        message = _healing_message(task, result.healable, failing)
        if lint_rc != 0:
            lint_note = _lint_feedback(root, scope)
            if lint_note:
                message = f"{message}\n\n{lint_note}"

    _say(f"round cap ({max_rounds}) reached — stopping.")
    workflow.save_state(
        workflow.transition(state, "rejected", review_verdict="ROUND_CAP"), root
    )
    return _terminal(
        1,
        "TEST_REGRESSION" if last_introduced else "ROUND_CAP_EXCEEDED",
        max_rounds,
        run_id,
        agent_version,
        start_sha,
        _head_sha(root),
    )


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
