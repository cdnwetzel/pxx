# Phase 9: Closed-Loop Autonomy (`pxx --loop`)

## Overview

**Goal:** Drive pxx's existing single-shot autonomy in a bounded, self-verifying
cycle: edit → test → review → heal → repeat, until the change is approved or a
guard stops it.

**Status:** `in-progress` — 9.1 done (verifier hardening); 9.2+9.3 (driver+guards, one review surface) next.

**Key Finding:** Every component of an autonomous loop already exists in main —
*except the loop itself*. There is a state machine (`workflow.py`), a
deterministic verdict engine (`review_gate.compute_verdict`), a healing-prompt
builder (`review_gate.build_healing_prompt`), a pre-push governance gate
(`governance.run_governance_check`), per-session audit, cost/token metrics, and
post-session capture (`tool_capture`). Nothing drives them in a cycle:

- `workflow.WorkflowState.healing_attempts` is a field **no code ever
  increments**.
- `review_gate.build_healing_prompt()` is **defined but never called**.
- `pxx --review --heal` is **advertised in user-facing strings**
  (`workflow.py`, `governance.py`) but **has no handler in `cli.py`**.

So this is a *composition* task — the same "wiring, not invention" move that made
Phase-5 Tier 3 cheap — not new machinery. The estimated core is ~100 lines of
orchestration plus guards and tests.

**Blocked by:** Nothing. (Builds only on shipped code.)

---

## Design: the loop is orchestration over shipped primitives

A `pxx --loop "<task>" --scope <file> [--max-rounds N]` session runs:

```
record session_start (audit)            # pxx/audit.py
tag safety point (#002)                 # pxx/safety.py  -> rollback anchor
baseline = failing-test count           # self_modes.self_test()
for round in 1..max_rounds:
    bounded edit       -> --self-fix path, SELF_FIX_DIFF_CAP, [autonomous] commit
    verify             -> self_modes.self_test() + self_lint()
    review             -> review_gate.run_review_pass() + collect_active_findings()
    verdict            -> review_gate.compute_verdict()   # REJECT|REVISE|APPROVE
    workflow.transition(state, ...)      # persist phase + healing_attempts (+1)
    audit round record
    branch on verdict (below)
```

**Verdict handling** (`compute_verdict`: P0→REJECT, P1→REVISE, P2/none→APPROVE):

- **APPROVE** → stop. Commits stay tagged `[autonomous]`; **never push**
  (governance gate remains the human's call).
- **REVISE** → feed `build_healing_prompt(findings)` into the next round's
  `--self-fix --message`, increment `workflow.healing_attempts`, continue.
- **REJECT** (P0) → stop immediately; offer rollback to the safety tag.

This finally wires the three inert primitives: `build_healing_prompt` gets a
caller, `healing_attempts` gets incremented, and `--heal` becomes real — one
REVISE round, with `--loop` as a fold over it (see Decisions).

---

## Phase 9.1: Test the verifiers first (gating prerequisite)

**What:** Unit tests for the gates the loop will trust, before empowering them.

**Why (as written, then overtaken):** when this plan was drafted the verifiers
had zero dedicated tests. Later the same day, the merge-loss recovery restored
the lost suites (`test_review_gate.py` 21, `test_workflow.py` 18,
`test_governance.py` 15), so 9.1 became gap-closing + the two fail-closed
changes rather than greenfield test-writing.

**Tasks:**
- [x] `test_review_gate.py`: `parse_findings`, `compute_verdict` truth table,
      `build_healing_prompt` (restored; +15 new tests for the items below;
      `run_review_pass` now covered too).
- [x] **Fail-closed severity handling** — fixed at *both* layers: the parse
      regex no longer silently drops unknown severities (they become visible
      findings, case-normalized), and `compute_verdict` returns REVISE for any
      severity outside {P0,P1,P2} ("p0" can't slip past REJECT either).
      Invariant tested: REVISE ⇒ non-empty healing prompt (unknown-severity
      findings feed `build_healing_prompt` alongside P1).
- [x] **Approval on silence — DECIDED: distinguish.** New
      `has_review_evidence(root)`; `pxx --review` records verdict
      **`NO_REVIEW`** (→ phase `rejected`, fail closed) when no claude-*.md
      artifacts exist. "Review ran and found nothing" still APPROVEs — but only
      with evidence on disk. Reviewer silence can no longer launder into a
      green light.
- [x] `test_workflow.py`: transition/state round-trip/resume (restored suite).
- [x] `test_governance.py`: allow/deny gates (restored suite; the restoration
      also caught + fixed the index-vs-worktree secrets-scanner bug).

**Effort:** 1-2 days (actual: collapsed by the restoration). **Status:** `done`

## Phase 9.2: The loop driver

**What:** `pxx --loop "<task>" --scope <file>` in `cli.py`, dispatching a new
`pxx/loop.py` that composes the primitives above.

**Why:** This is the missing 100 lines. Keep it a thin orchestrator; all logic
stays in the modules it calls.

**Tasks:**
- [ ] `--loop` flag + task/scope parsing (reuse `scope.extract_scope_args`,
      `self_modes.extract_self_fix_task`).
- [ ] `pxx/loop.py`: the round loop, verdict branching, healing-prompt feedback,
      `workflow` persistence, per-round `audit` records.
- [ ] Wire `build_healing_prompt` -> next-round `--message`; increment
      `healing_attempts`.
- [ ] Terminal states: APPROVE/REJECT/round-cap, with optional rollback to the
      #002 safety tag on REJECT.

**Effort:** 2-3 days. **Status:** `planned`

## Phase 9.3: Termination guards (the real design work)

**What:** Three independent stop conditions; any one fires → loop aborts.

**Why:** The guards are what separate "autonomous" from "runaway." Each kills a
distinct failure mode.

- [ ] **Round cap** (default 3) — hard ceiling on iterations.
- [ ] **Monotonic-progress rule** — measured against the **baseline test set
      captured before round 1**: failures within that set must *strictly
      decrease* each round, else abort. Tests the loop itself introduces are
      tracked separately (a good round may add tests that initially fail —
      naive whole-count monotonicity would punish exactly the right behavior).
      Kills the "iterates 100 times making no progress" cost failure mode.
- [ ] **Cumulative diff budget** — a budget across *all* rounds (not just the
      per-commit `SELF_FIX_DIFF_CAP = 60`), so N rounds can't smuggle in an
      N×60-line rewrite.
- [ ] **No-push is absolute** — the loop never pushes; APPROVE just stops with
      tagged commits.
- [ ] **Budget in tokens + wall-clock, not dollars** — `cost_metrics.py`
      currently prices at `$0.003/1k` (cloud rates); inference here is local and
      free, so the loop's budget gate must count tokens and wall-clock.

**Effort:** 2 days. **Status:** `planned`

## Phase 9.4: Feedback — direct in-loop, memory for cross-session only

**What:** Round-to-round feedback is **plain prompt construction from variables
the driver already holds** — the exact failing-test list and diff of the round
just run go straight into `build_healing_prompt`. Deterministic and free.
Routing it through `MemoryInjector`'s fuzzy retrieval would add a failure mode
for zero gain: within a loop, the driver holds the ground truth.

Memory's role is **cross-session learning** ("we attempted this task last week,
here's what broke"): after the loop terminates, `tool_capture` (post-session
git-diff + test-name parsing — works today, does not depend on the blocked
runtime observer) stores a summary observation for future sessions.

**Tasks:**
- [ ] Driver passes round-N failing tests + diff directly into the round-N+1
      healing prompt (no retrieval on this path).
- [ ] On terminal verdict, `tool_capture.capture_session_tools()` stores the
      loop summary for cross-session recall.
- [ ] Privacy check: loop audit/memory records must honor the de-identification
      contract (commit a256a04) — no machine paths/hostnames in anything that
      could reach a public artifact.

**Effort:** 1 day (shrunk by dropping in-loop retrieval). **Status:** `planned`
(8.5 confidence scoring is **off the loop's critical path entirely**)

---

## Constraints (carried from dogfooding)

- **One file per round.** Phase-8.5's dogfooding notes record that `--self-fix`
  multi-file edits produce SEARCH/REPLACE conflicts with the local models. The
  loop's unit of iteration is a single `--scope`'d file; multi-file tasks
  decompose into sequential single-file rounds. The existing scope machinery
  enforces this for free.
- **Reviewer-runtime safety.** `--loop` implies `--edit`/`--self-fix`, which
  trips the #002 safety tag (stashes the working tree). Same rule as
  `--self-fix`: never run `--loop` during a concurrent multi-agent review pass.
- **Non-interactive aider must never be asked a question.** Live dogfood
  (2026-06-10): a one-shot `--self-improve --message` run died with
  `OSError: Errno 22` because aider hit an interactive confirm (an unknown-model
  warning offering a docs URL) and prompt_toolkit can't attach to a non-TTY
  stdin. Every loop round must pass `--yes` (and keep model metadata in sync
  with the served model id) so no confirm-prompt can block an unattended round.
  Auto-confirming is correct, not scary: **the prompt is not the boundary — the
  hook is.** Aider's interactive confirms were never a real gate; the diff cap,
  scope hook, ruff+pytest gate, and review verdict all sit downstream of aider,
  so `--yes` upstream does not widen the blast radius. (Stated here so a future
  reader doesn't "fix" it.)
- **Never act on unverified model findings.** Same dogfood session, second run
  (worked end-to-end: env-file config → tunnel → T5810 14b → ask-mode one-shot):
  the model returned 3 suggestions; the 2 verifiable ones were both FALSE — one
  proposed error handling that already exists verbatim, one "fixed" os.replace's
  Windows atomicity (os.replace IS atomic there; the proposed shutil.move is
  worse). 0-for-2 confirms the plan's stance: REVISE-round healing prompts come
  only from the deterministic review gate, never raw model suggestions.
- **Post-PyPI shipping posture.** As of 2026-06-10 pxx ships to strangers
  without this repo's guardrail culture. `--loop` therefore lands marked
  **experimental** in v1.1 with the most conservative defaults: refuse without
  `--scope`; refuse on a dirty tree outside the safety-tag flow; round cap 3;
  no-push absolute; REJECT stops and reports (never auto-reverts a stranger's
  tree — `--rollback` is opt-in).

---

## Success Criteria

- [x] `review_gate`, `workflow`, `governance` have unit tests (9.1 ✅).
- [ ] `pxx --loop "<task>" --scope <file>` drives edit→test→review→heal to a
      terminal verdict.
- [ ] `healing_attempts` increments; `build_healing_prompt` is called; `--heal`
      is real (one REVISE round; `--loop` folds over it — see Decisions).
- [ ] All three guards demonstrably stop a pathological loop (round cap,
      baseline-set failures not strictly decreasing, cumulative diff budget).
- [ ] Budget reported in tokens + wall-clock; never pushes; every round audited.

---

## Decisions (resolved 2026-06-10, loop-engineering review)

1. **`--heal` = exactly one REVISE round; `--loop` = a fold over it.** The
   decisive argument is testability, not elegance: one round can be unit- and
   integration-tested cheaply, and the loop driver then needs almost no tests
   of its own beyond guard behavior.
2. **Rollback on REJECT: stop-and-report, always.** Hardened by PyPI: never
   auto-revert a stranger's tree. `--loop --rollback` is the explicit opt-in.
3. **8.5 does not gate the loop.** Dissolved by the 9.4 simplification: in-loop
   feedback is direct variable passing; memory (and any future confidence
   ranking) only improves cross-session recall, off the critical path.

## Sequencing

9.1 (verifier tests) → **9.2 + 9.3 together** (driver and guards are one review
surface — a driver without guards shouldn't exist even on a branch) →
simplified 9.4. With `--heal`-as-one-round, the critical path is roughly a week
and nothing on it is blocked.

---

## Dependencies

**Blocked by:** Nothing.
**Soft-depends on:** Phase 8.4 (✅ done — metadata capture) for cross-session
capture. Phase 8.5 is explicitly **not** on the critical path (see Decisions).
**Unblocks:** Hands-off bounded refactors/bugfixes on a single scoped file.

---

## Notes

- The deferred runtime observer (PTY support for `observer.py`) is **not** on
  this plan's critical path — `tool_capture` covers per-round capture. A
  PTY-backed supervisor is a separate, optional follow-up that would also clear
  the `xfail` in `test_memory_e2e.py::test_memory_persistence_across_sessions`.
