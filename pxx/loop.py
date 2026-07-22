"""Bounded autonomous loop: edit -> test -> review -> heal.

Carries forward the pxx 1.x control-loop semantics on the 2.0 runtime:

- fresh backend (fresh context) per round — an invariant;
- scope re-check after every round (backend commits can bypass hooks);
- diff-line budget accounting against the pre-loop git state;
- monotonic failing-set progress (round 1 establishes the baseline; a later
  round must not add new failures, else ``NO_PROGRESS``);
- a fail-closed review gate (``BLOCKING`` by default).

All git/subprocess I/O degrades gracefully outside a git repository
(changes are treated as untracked-only; guards that need git are skipped).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .backends.base import AgentBackend
from .config import Settings
from .errors import BudgetExceeded
from .events import Event, EventBus
from .outcome import RunOutcome, TerminalCode
from .review import (
    Finding,
    Reviewer,
    ReviewMode,
    ReviewPacket,
    Verdict,
    build_healing_prompt,
    review_changes,
)
from .safety import BudgetGuard, ScopeGate
from .session import Session

log = logging.getLogger("pxx.loop")

TEST_TIMEOUT_SECONDS = 300.0
_NO_REPO_DIFF = "(no git repository; diff unavailable)"

_REPLAN_PREFIX = (
    "You made NO measurable progress last round (identical failing set, "
    "identical diff, identical findings). Stop editing blindly: restate the "
    "objective, diagnose the root cause, and take a DIFFERENT approach.\n\n"
)


@dataclass(frozen=True)
class ProgressVector:
    """Semantic loop-detection state (Phase 15 amend): what changed since
    last round — failing set, diff content, and finding identities."""

    failing_set: frozenset[str]
    diff_hash: str
    finding_keys: frozenset[tuple]


def _finding_keys(finds: tuple[Finding, ...]) -> frozenset[tuple]:
    return frozenset((f.file, f.line, f.message[:40]) for f in finds)


async def _git(root: Path, *args: str) -> str | None:
    """Run a git command; return stdout, or None on any failure (incl. no git)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return out.decode(errors="replace")


async def _in_git_repo(root: Path) -> bool:
    out = await _git(root, "rev-parse", "--is-inside-work-tree")
    return out is not None and out.strip() == "true"


async def _changed_paths(root: Path, pre_sha: str | None = None) -> list[str]:
    """Repo-relative paths changed vs the pre-loop state.

    Covers committed work (``pre_sha``..working tree), staged/unstaged
    tracked changes, and untracked files — a backend that commits (aider
    auto-commits) leaves a clean ``git status`` but must still be caught.
    Rename detection is disabled so a rename can't collapse away the source
    path; if a rename ever slips through, BOTH source and destination are
    reported (fail-safe).
    """
    paths: set[str] = set()
    if pre_sha:
        # -z: NUL-separated, path quoting fully disabled (spaces/non-ASCII exact)
        out = await _git(root, "diff", "--name-only", "--no-renames", "-z", pre_sha)
        if out:
            paths.update(n for n in out.split("\0") if n.strip())
    out = await _git(root, "status", "--porcelain", "--no-renames", "--untracked-files=all", "-z")
    if not out:
        return sorted(paths)
    for entry in out.split("\0"):
        if len(entry) >= 4:
            path = entry[3:]
            if " -> " in path:  # rename: keep source AND destination
                src, _, dst = path.partition(" -> ")
                paths.add(src)
                paths.add(dst)
                continue
            paths.add(path)
    return sorted(paths)


async def _diff_since(root: Path, pre_sha: str | None) -> str:
    """Diff of the working tree against the pre-loop state (renames expanded
    to delete+add so a rename can't shrink the accounted diff)."""
    if pre_sha:
        out = await _git(root, "diff", "--no-renames", pre_sha)
    else:
        out = await _git(root, "diff", "--no-renames", "HEAD") or await _git(
            root, "diff", "--no-renames"
        )
    return out or ""


def _diff_line_count(diff: str) -> int:
    n = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            n += 1
    return n


_FAILURE_RES = (
    re.compile(r"^FAILED\s+(\S+)", re.MULTILINE),  # pytest
    re.compile(r"^FAIL:\s*(\S+)", re.MULTILINE),  # unittest
)


async def _run_tests(root: Path, command: str) -> tuple[bool, set[str], str]:
    """Run the test command; return (passed, failing-set, output tail)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return False, {f"spawn-error:{command}"}, f"could not run test command: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TEST_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        tail = f"test command timed out after {TEST_TIMEOUT_SECONDS:.0f}s"
        return False, {f"timeout:{command}"}, tail
    text = out.decode(errors="replace")
    failing: set[str] = set()
    if proc.returncode != 0:
        for rx in _FAILURE_RES:
            failing.update(rx.findall(text))
        if not failing:
            failing.add(f"exit:{proc.returncode}")
    return proc.returncode == 0, failing, text.strip()[-1500:]


def _default_factory(settings: Settings) -> Callable[[], AgentBackend]:
    def make() -> AgentBackend:
        from .backends import get_backend  # lazy: backends package may load later

        return get_backend("native", settings)

    return make


async def _run_lint(root: Path, command: str) -> tuple[bool, str]:
    """Run the lint command; return (passed, output tail). Best-effort: a
    spawn failure counts as NOT passed (a gate that can't run is not green)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), TEST_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        return False, f"lint command failed to run: {exc}"
    text = out.decode(errors="replace")
    return proc.returncode == 0, text.strip()[-1000:]


def _resolve_root(cwd: Path | None) -> Path:
    return Path(os.path.realpath(cwd if cwd is not None else os.getcwd()))


async def run_loop(
    task: str,
    settings: Settings,
    *,
    cwd: Path | None = None,
    backend_factory: Callable[[], AgentBackend] | None = None,
    test_command: str | None = None,
    reviewer: Reviewer | None = None,
    review_mode: ReviewMode = ReviewMode.BLOCKING,
    max_rounds: int = 3,
    bus: EventBus | None = None,
    lint_command: str | None = None,
    safety_net: bool | None = None,
) -> RunOutcome:
    """Run the bounded edit -> test -> review loop for ``task``."""
    root = _resolve_root(cwd)
    parent_bus = bus or EventBus()
    factory = backend_factory or _default_factory(settings)
    scope = ScopeGate(root, settings.scope, settings.trusted_paths)
    budgets = BudgetGuard(settings.budgets)
    command = test_command or settings.test_command
    in_repo = await _in_git_repo(root)
    pre_sha = None
    if in_repo:
        head = await _git(root, "rev-parse", "HEAD")
        pre_sha = head.strip() if head else None

    # K5: the loop ties ONE net at start — its per-round Sessions are
    # constructed with safety_net=False so rounds don't re-stash. Callers
    # that orchestrate their own loops (goal nodes) pass safety_net=False:
    # per-node nets spam shared tag refs and race under parallelism.
    net_enabled = settings.safety_net if safety_net is None else safety_net
    net = None
    if in_repo and net_enabled:
        from .safety_net import tie_safety_net

        net = await tie_safety_net(root, f"loop-{uuid.uuid4().hex[:8]}")
        if net is not None:
            await parent_bus.emit(
                "gate_decision",
                {
                    "gate": "safety_net",
                    "allowed": True,
                    "tag": net.tag or "",
                    "stash": net.stash_message or "",
                },
            )
    net_suffix = ""
    if net is not None:
        net_suffix = f" [net: {net.tag or 'no-tag'}{'+stash' if net.stash_message else ''}]"

    # Baseline for the end-of-loop commit delta — taken AFTER the net ties,
    # so it is correct in all three cases: stash succeeded (clean tree),
    # stash failed (dirt recorded in baseline and excluded), safety_net=false
    # (same). Uses pxx.worktree — the same delta implementation as Session.
    worktree_start = None
    if settings.auto_commit and in_repo:
        from .worktree import worktree_snapshot

        worktree_start = await worktree_snapshot(root)

    # Per-round sessions never commit mid-loop: auto_commit fires ONCE at the
    # end of a completed loop (B1.4), not per round.
    round_settings = replace(settings, auto_commit=False)

    async def _complete(outcome: RunOutcome) -> RunOutcome:
        """End-of-loop commit when --commit is on (once per completed loop)."""
        if settings.auto_commit and outcome.code is TerminalCode.COMPLETED:
            from .safety_net import commit_session_work
            from .worktree import worktree_delta

            sha = None
            if worktree_start is None:
                # Fail CLOSED: the delta is unknown; add -A would sweep
                # baseline dirt. Skip the commit instead.
                log.warning(
                    "auto-commit skipped: worktree snapshot unavailable "
                    "(git failed) — nothing committed"
                )
            else:
                changed, _ = await worktree_delta(root, worktree_start)
                sha = await commit_session_work(
                    root,
                    task_preview=task,
                    net_tag=net.tag if net else None,
                    only=set(changed),  # the loop's own delta, never baseline dirt
                )
            if sha:
                await parent_bus.emit("observation", {"source": "auto_commit", "sha": sha})
                outcome = replace(outcome, summary=f"{outcome.summary} [committed {sha[:8]}]")
        return outcome

    baseline_failures: set[str] | None = None
    previous_failing: set[str] = set()
    findings: tuple[Finding, ...] = ()
    tokens = 0
    accounted_diff = 0
    summary = "no rounds run"
    contributing: list[str] = []
    legs = {
        "edit_seconds": 0.0,
        "test_seconds": 0.0,
        "review_seconds": 0.0,
        "files_changed": 0,
        "baseline_failures": 0,
        "introduced_failures": 0,
        "terminal_failures": 0,
        "lint_errors": 0,
        "unparseable_review_count": 0,
    }
    last_review_verdict: Verdict | None = None
    failing: set[str] = set()
    previous_vector: ProgressVector | None = None
    stagnation = 0
    replan = False

    async def _forward(event: Event) -> None:
        await parent_bus.emit(event.kind, event.data, session_id=event.session_id)

    def _check_stagnation(round_no: int) -> RunOutcome | None:
        """Recovery ladder (Phase 15 amend): detect a semantically stuck loop
        (identical failing set + diff + findings across rounds). Step 1:
        re-plan next round. Step 2: stop with LOOP_DETECTED — never just
        raise the round limit."""
        nonlocal previous_vector, stagnation, replan
        vector = ProgressVector(
            failing_set=frozenset(failing),
            diff_hash=hashlib.sha256(diff_text.encode()).hexdigest()[:16],
            finding_keys=_finding_keys(findings),
        )
        if previous_vector is not None and vector == previous_vector:
            stagnation += 1
        else:
            stagnation = 0
            replan = False
        previous_vector = vector
        if stagnation == 1:
            replan = True
            return None
        if stagnation >= 2:
            return _outcome(
                TerminalCode.LOOP_DETECTED,
                f"round {round_no}: no semantic progress across "
                f"{stagnation + 1} consecutive rounds (identical failing set, "
                "diff, and findings) — stopped by the recovery ladder",
                round_no,
                findings,
            )
        return None

    def _outcome(
        code: TerminalCode, text: str, round_no: int, finds: tuple[Finding, ...] = ()
    ) -> RunOutcome:
        by_severity: dict[str, int] = {}
        for f in finds:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        return RunOutcome(
            code=code,
            summary=text + net_suffix,
            rounds=round_no,
            tokens=tokens,
            diff_lines=accounted_diff,
            findings=tuple(asdict(f) for f in finds),
            contributing_codes=tuple(dict.fromkeys(contributing)),
            findings_by_severity=by_severity,
            **legs,
        )

    for round_no in range(1, max_rounds + 1):
        try:
            budgets.consume(rounds=1)
        except BudgetExceeded as exc:
            return _outcome(TerminalCode.BUDGET_EXCEEDED, str(exc), round_no - 1, findings)

        backend = factory()  # fresh backend/context per round — an invariant
        if round_no == 1:
            prompt = task
        else:
            prompt = (_REPLAN_PREFIX if replan else "") + build_healing_prompt(
                task, findings, round_no
            )

        # Fresh bus per round so each Session's AuditLog keeps a clean hash
        # chain; events are forwarded to the caller-visible bus.
        session_bus = EventBus()
        session_bus.subscribe(_forward)
        edit_start = time.monotonic()
        outcome = await Session(
            round_settings, backend, cwd=root, bus=session_bus, safety_net=False
        ).run(prompt, check_clarity=round_no == 1)
        legs["edit_seconds"] += time.monotonic() - edit_start
        tokens += outcome.tokens
        if outcome.code is not TerminalCode.COMPLETED:
            if net_suffix:
                outcome = replace(outcome, summary=outcome.summary + net_suffix)
            return outcome  # backend-level terminal code short-circuits the loop

        # Guard 1: scope re-check (backend commits can bypass hooks).
        if in_repo:
            changed = await _changed_paths(root, pre_sha)
            legs["files_changed"] = len(changed)
            offenders = [p for p in changed if not scope.in_scope(root / p)]
            await parent_bus.emit(
                "gate_decision",
                {
                    "gate": "scope_recheck",
                    "round": round_no,
                    "allowed": not offenders,
                    "changed_paths": len(changed),
                    "violations": offenders[:10],
                },
            )
            if offenders:
                return _outcome(
                    TerminalCode.OUT_OF_SCOPE,
                    f"round {round_no}: changed paths outside scope: " + ", ".join(offenders[:5]),
                    round_no,
                    findings,
                )

        # Guard 2: diff accounting against the pre-loop git state.
        diff_text = ""
        if in_repo:
            diff_text = await _diff_since(root, pre_sha)
            total = _diff_line_count(diff_text)
            try:
                budgets.consume(diff_lines=total - accounted_diff)
                accounted_diff = total
            except BudgetExceeded as exc:
                await parent_bus.emit(
                    "gate_decision",
                    {
                        "gate": "diff_budget",
                        "round": round_no,
                        "allowed": False,
                        "diff_lines": total,
                    },
                )
                return _outcome(TerminalCode.DIFF_CAP, str(exc), round_no, findings)
            await parent_bus.emit(
                "gate_decision",
                {"gate": "diff_budget", "round": round_no, "allowed": True, "diff_lines": total},
            )
            await parent_bus.emit("budget", {"round": round_no, **budgets.snapshot()})

        # Guard 3: tests with monotonic failing-set progress.
        if command:
            test_start = time.monotonic()
            passed, failing, test_tail = await _run_tests(root, command)
            legs["test_seconds"] += time.monotonic() - test_start
            infra = {
                f
                for f in failing
                if f.startswith(("spawn-error:", "timeout:")) or f in ("exit:126", "exit:127")
            }
            if infra and infra == failing:
                # the suite itself could not run — infrastructure, not a regression
                return _outcome(
                    TerminalCode.TEST_RUN_FAILED,
                    f"round {round_no}: test command could not run: "
                    + ", ".join(sorted(infra)[:5]),
                    round_no,
                    findings,
                )
            if baseline_failures is None:
                baseline_failures = set(failing)
                legs["baseline_failures"] = len(failing)
            new_failures = sorted(failing - baseline_failures)
            legs["terminal_failures"] = len(failing)
            await parent_bus.emit(
                "gate_decision",
                {
                    "gate": "tests",
                    "round": round_no,
                    "allowed": not new_failures,
                    "passed": passed,
                    "failing": len(failing),
                    "new_failures": new_failures[:10],
                },
            )
            if new_failures:
                legs["introduced_failures"] = len(new_failures)
                return _outcome(
                    TerminalCode.TEST_REGRESSION,
                    f"round {round_no}: new test failures beyond baseline: "
                    + ", ".join(new_failures[:5]),
                    round_no,
                    findings,
                )
            if not passed:
                if failing and failing == previous_failing:
                    contributing.append("NO_TEST_PROGRESS")
                previous_failing = set(failing)
                findings = tuple(
                    Finding(
                        id=f"T-{i + 1:03d}",
                        severity="high",
                        file=name,
                        line=None,
                        message=f"failing test: {test_tail[-200:]}",
                    )
                    for i, name in enumerate(sorted(failing))
                )
                summary = f"round {round_no}: tests still failing ({len(failing)})"
                stuck = _check_stagnation(round_no)
                if stuck is not None:
                    return stuck
                continue

        # Guard 3b: lint gate (when configured — from WORKFLOW.md commands).
        if lint_command:
            lint_ok, lint_tail = await _run_lint(root, lint_command)
            await parent_bus.emit(
                "gate_decision",
                {"gate": "lint", "round": round_no, "allowed": lint_ok},
            )
            if not lint_ok:
                legs["lint_errors"] += 1
                return _outcome(
                    TerminalCode.LINT_BLOCKED,
                    f"round {round_no}: lint gate failed: {lint_tail[-300:]}",
                    round_no,
                    findings,
                )

        # Guard 4: review gate (only when tests pass or no tests configured).
        if reviewer is None:
            return await _complete(
                _outcome(
                    TerminalCode.COMPLETED,
                    f"completed in {round_no} round(s); "
                    + ("tests passed" if command else "no test command"),
                    round_no,
                    findings,
                )
            )
        head_at_diff = None
        if in_repo:
            head = await _git(root, "rev-parse", "HEAD")
            head_at_diff = head.strip() if head else None
        review_start = time.monotonic()
        result = await review_changes(
            diff_text if in_repo else _NO_REPO_DIFF, task, reviewer, review_mode
        )
        legs["review_seconds"] += time.monotonic() - review_start
        if result.review_error == "unparseable":
            legs["unparseable_review_count"] += 1

        # Commit-bound validity (Phase 12 amend): a review approves a COMMIT.
        # If HEAD moved between the diff and the verdict, the packet is STALE
        # and cannot approve the newer tree — re-review once, fail closed after.
        if in_repo and head_at_diff and result.verdict is Verdict.APPROVE:
            head_now = await _git(root, "rev-parse", "HEAD")
            packet = ReviewPacket(
                task=task[:200],
                base_sha=pre_sha or "",
                head_sha=head_at_diff,
                verdict=str(result.verdict),
                findings=result.findings,
                verify_command=command or "",
                reviewer=getattr(reviewer, "name", "reviewer"),
            )
            if head_now and packet.is_stale(head_now.strip()):
                await parent_bus.emit(
                    "gate_decision",
                    {
                        "gate": "review_stale",
                        "round": round_no,
                        "allowed": False,
                        "reviewed_head": head_at_diff[:12],
                        "current_head": head_now.strip()[:12],
                    },
                )
                diff_text = await _diff_since(root, pre_sha)
                head2 = await _git(root, "rev-parse", "HEAD")
                review_start = time.monotonic()
                result = await review_changes(diff_text, task, reviewer, review_mode)
                legs["review_seconds"] += time.monotonic() - review_start
                head_now2 = await _git(root, "rev-parse", "HEAD")
                if head2 and head_now2 and head2.strip() != head_now2.strip():
                    return _outcome(
                        TerminalCode.REVIEW_UNAVAILABLE,
                        f"round {round_no}: review cannot bind to a stable HEAD "
                        "(tree keeps moving)",
                        round_no,
                        findings,
                    )

        findings = result.findings
        last_review_verdict = result.verdict
        await parent_bus.emit(
            "gate_decision",
            {
                "gate": "review",
                "round": round_no,
                "allowed": not result.blocked,
                "verdict": str(result.verdict),
                "mode": str(result.mode),
                "findings": len(result.findings),
            },
        )
        if result.verdict is Verdict.REVISE:
            # Actionable feedback: heal next round (round cap still bounds it).
            contributing.append("REVIEW_REJECTED")
            summary = f"round {round_no}: reviewer requested changes ({len(findings)} findings)"
            stuck = _check_stagnation(round_no)
            if stuck is not None:
                return stuck
            continue
        if result.blocked:
            # NO_REVIEW in blocking mode: fail-closed, with the SPECIFIC cause.
            code = {
                "unavailable": TerminalCode.REVIEW_UNAVAILABLE,
                "empty": TerminalCode.REVIEW_EMPTY,
                "unparseable": TerminalCode.REVIEW_UNPARSEABLE,
            }.get(result.review_error, TerminalCode.REVIEW_UNPARSEABLE)
            return _outcome(
                code,
                f"round {round_no}: review gate blocked "
                f"({result.review_error or 'unknown'}, verdict {result.verdict})",
                round_no,
                findings,
            )
        # APPROVE, or NO_REVIEW in advisory mode (nothing actionable -> accept).
        if result.review_error:
            contributing.append(
                {
                    "unavailable": "REVIEW_UNAVAILABLE",
                    "empty": "REVIEW_EMPTY",
                    "unparseable": "REVIEW_UNPARSEABLE",
                }[result.review_error]
            )
        return await _complete(
            _outcome(
                TerminalCode.COMPLETED,
                f"completed in {round_no} round(s) (verdict {result.verdict})",
                round_no,
                findings,
            )
        )

    if last_review_verdict is Verdict.REVISE:
        contributing.append("REVIEW_REJECTED")
    return _outcome(
        TerminalCode.ROUND_CAP,
        f"round cap reached ({max_rounds}); last: {summary}",
        max_rounds,
        findings,
    )
