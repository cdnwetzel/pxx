# Phase 9: Closed-Loop Autonomy (`pxx --loop`)

## Overview

**Goal:** Drive pxx's existing single-shot autonomy in a bounded, self-verifying
cycle: edit → test → review → heal → repeat, until the change is approved or a
guard stops it.

**Status:** `planned`

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
caller, `healing_attempts` gets incremented, and `--heal` becomes real (or the
loop subsumes it — see Open Questions).

---

## Phase 9.1: Test the verifiers first (gating prerequisite)

**What:** Unit tests for the gates the loop will trust, before empowering them.

**Why:** A loop is only as good as its verifier, and right now the verifier is
the least-tested code in the repo: `review_gate.py`, `workflow.py`, and
`governance.py` have **zero dedicated tests**. Empowering an untested verifier to
gate autonomous edits is the highest risk here.

**Tasks:**
- [ ] `test_review_gate.py`: `parse_findings` (P0/P1/P2 extraction, malformed
      input), `compute_verdict` (REJECT/REVISE/APPROVE truth table),
      `build_healing_prompt` (P1-only, empty, ordering).
- [ ] `test_workflow.py`: `transition` phase moves + `healing_attempts` updates,
      `load_state`/`save_state` round-trip, `resume_state`.
- [ ] `test_governance.py`: `run_governance_check` allow/deny on a synthetic
      verdict + `[autonomous]` commit set.

**Effort:** 1-2 days. **Status:** `planned`

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
- [ ] **Monotonic-progress rule** — the failing-test count must *strictly
      decrease* each round, else abort. Kills the "iterates 100 times making no
      progress" cost failure mode.
- [ ] **Cumulative diff budget** — a budget across *all* rounds (not just the
      per-commit `SELF_FIX_DIFF_CAP = 60`), so N rounds can't smuggle in an
      N×60-line rewrite.
- [ ] **No-push is absolute** — the loop never pushes; APPROVE just stops with
      tagged commits.
- [ ] **Budget in tokens + wall-clock, not dollars** — `cost_metrics.py`
      currently prices at `$0.003/1k` (cloud rates); inference here is local and
      free, so the loop's budget gate must count tokens and wall-clock.

**Effort:** 2 days. **Status:** `planned`

## Phase 9.4: Per-round learning via `tool_capture` (not the observer)

**What:** Inject "what failed in round N and why" into round N+1's context.

**Why:** This is the real synergy with the Phase-8 memory stack. `tool_capture`
(post-session git-diff + test-name parsing) **works today in supervisor mode** —
it does not depend on the runtime observer, which is honestly documented as
blocked (aider's TUI can't run under piped stdout; see `pxx/observer.py`). Use
the working path; leave real-time observation deferred.

**Tasks:**
- [ ] After each round, `tool_capture.capture_session_tools()` the round's diff +
      failing tests.
- [ ] Feed the prior round's capture into the next round via `MemoryInjector`.
- [ ] (Stronger once Phase 8.5 confidence scoring lands — sequence 8.5 before
      relying on ranked recall.)

**Effort:** 2-3 days. **Status:** `planned` (soft-depends on Phase 8.4 ✅ /
benefits from 8.5)

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

---

## Success Criteria

- [ ] `review_gate`, `workflow`, `governance` have unit tests (9.1).
- [ ] `pxx --loop "<task>" --scope <file>` drives edit→test→review→heal to a
      terminal verdict.
- [ ] `healing_attempts` increments; `build_healing_prompt` is called; `--heal`
      is real (or removed as superseded — see Open Questions).
- [ ] All three guards demonstrably stop a pathological loop (round cap,
      non-decreasing test count, diff budget).
- [ ] Budget reported in tokens + wall-clock; never pushes; every round audited.

---

## Open Questions

1. **`--heal` vs `--loop`.** `--heal` is currently advertised but unimplemented.
   Options: (a) implement `--heal` as a single REVISE round and have `--loop`
   call it N times, or (b) drop `--heal` (strip the 3 suggestion strings + the
   dead `build_healing_prompt` caller) and ship only `--loop`. Leaning (a):
   `--heal` = one round is a natural, testable primitive.
2. **Rollback policy on REJECT.** Auto-rollback to the safety tag, or stop and
   leave the tree for inspection? Default to stop-and-report; `--loop --rollback`
   to opt into auto-revert.
3. **8.5 sequencing.** Per-round learning (9.4) is stronger with confidence
   scoring (8.5). Do 8.5 first, or ship 9.4 against unranked recall and upgrade?

---

## Dependencies

**Blocked by:** Nothing.
**Soft-depends on:** Phase 8.4 (✅ done — metadata capture) for 9.4; benefits
from Phase 8.5 (confidence scoring, `planned`).
**Unblocks:** Hands-off bounded refactors/bugfixes on a single scoped file.

---

## Notes

- The deferred runtime observer (PTY support for `observer.py`) is **not** on
  this plan's critical path — `tool_capture` covers per-round capture. A
  PTY-backed supervisor is a separate, optional follow-up that would also clear
  the `xfail` in `test_memory_e2e.py::test_memory_persistence_across_sessions`.
