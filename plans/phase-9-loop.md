# Phase 9: Closed-Loop Autonomy (`pxx --loop`)
> Backlog ID: 004

## Overview

**Goal:** Drive pxx's existing single-shot autonomy in a bounded, self-verifying
cycle: edit → test → review → heal → repeat, until the change is approved or a
guard stops it.

**Status:** `done` (2026-07-16) — 9.1+9.1b (verifier hardening), 9.2+9.3 (driver+guards), live dogfood (APPROVE on a genuine task, vllm-host-1/Qwen3-Coder — see Live dogfood #2), and 9.4 cross-session capture + privacy check all landed. Only the `--rollback` opt-in remains, explicitly deferred by decision (stop-and-report is the shipped default); it does not hold this plan open.

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
- [x] `test_governance.py` — in two steps. The restored suite covered the
      three *checkers* only; the verification pass correctly flagged the
      original checkbox as overstating. **9.1b** closed the remainder:
      `run_governance_check` aggregator tests (exit 0/1, skip-guard
      RuntimeError outside pytest, invalid-config warning path), an
      `errors="replace"` fix for the UnicodeDecodeError crash on staged
      binaries (a bug in the same-day index-fix code, with regression test),
      all 9 secret patterns parametrized, the em-dash near-miss guard in
      `parse_findings` (header-like lines that fail the format surface as
      UNPARSEABLE findings → REVISE, instead of silently dropping), and the
      healing-prompt ordering test.

**Effort:** 1-2 days (actual: collapsed by the restoration). **Status:** `done`

## Phase 9.2: The loop driver

**What:** `pxx --loop "<task>" --scope <file>` in `cli.py`, dispatching a new
`pxx/loop.py` that composes the primitives above.

**Why:** This is the missing 100 lines. Keep it a thin orchestrator; all logic
stays in the modules it calls.

**Tasks:**
- [x] `--loop` flag + task/scope parsing (reuses `extract_scope_args` /
      `extract_self_fix_task`); conservative gates enforced at the cli:
      pxx-repo-only (v1), `--scope` required, clean tree required,
      EXPERIMENTAL banner, `--max-rounds`.
- [x] `pxx/loop.py`: round loop, verdict branching, healing-prompt feedback
      (gate findings + live failing-test list — 9.4's direct-feedback design,
      built in from day one), `workflow` persistence (`healing_attempts`
      increments per round), per-round `audit` records. Each edit round is a
      `pxx --self-fix` subprocess with `--yes` (reuses safety tag, diff cap,
      [autonomous] tagging, execve handoff).
- [x] `build_healing_prompt` wired; `--heal` is real: `heal_once()` = exactly
      one REVISE round, dispatched from `pxx --review --heal --scope <path>`.
- [x] Terminal states: APPROVE / REJECT / round-cap, REJECT = stop-and-report
      per Decisions. ( **Deferred:** the `--rollback` opt-in flag for
      auto-revert to the #002 tag — stop-and-report is the shipped default.)
- [x] **All-UNPARSEABLE must not heal either.** Same family as NO_REVIEW
      (verified-pass input): if a REVISE verdict is driven *only* by
      UNPARSEABLE findings, the healing prompt would tell aider to "address" a
      malformed markdown header — the remedy is fixing/re-running the review,
      not editing code. The driver branches on "at least one substantive
      finding (P1 or unknown-real)"; all-UNPARSEABLE behaves like NO_REVIEW.
- [x] **NO_REVIEW must not heal.** NO_REVIEW lands in phase `rejected`, whose
      resume message generically suggests `--heal` — but healing NO_REVIEW is
      nonsensical (no findings → empty prompt → the exact spin the REVISE
      invariant guards against). The driver special-cases it: NO_REVIEW's
      remedy is "run a review", never a heal round; make the rejected-phase
      message verdict-aware when `--heal` lands. (Done: `resume_state`'s
      rejected message now branches on the verdict.)

**Effort:** 2-3 days. **Status:** `done` (except the deferred `--rollback`
opt-in, noted above)

## Phase 9.3: Termination guards (the real design work)

**What:** Three independent stop conditions; any one fires → loop aborts.

**Why:** The guards are what separate "autonomous" from "runaway." Each kills a
distinct failure mode.

- [x] **Round cap** (default 3) — hard ceiling on iterations.
- [x] **Monotonic-progress rule** — measured against the **baseline test set
      captured before round 1**: failures within that set must *strictly
      decrease* each round, else abort. Tests the loop itself introduces are
      tracked separately (a good round may add tests that initially fail —
      naive whole-count monotonicity would punish exactly the right behavior).
      Kills the "iterates 100 times making no progress" cost failure mode.
- [x] **Cumulative diff budget** (default 150 lines) — a budget across *all* rounds (not just the
      per-commit `SELF_FIX_DIFF_CAP = 60`), so N rounds can't smuggle in an
      N×60-line rewrite.
- [x] **No-push is absolute** — the driver has no push code path; APPROVE
      stops with tagged commits.
- [x] **Budget in wall-clock** (default 1800s). **Deferred:** token counting —
      aider doesn't expose per-run token totals to a parent process without
      output scraping; wall-clock + rounds + diff budget bound the same failure
      mode. Revisit if/when aider grows a machine-readable usage report.

**Effort:** 2 days. **Status:** `done` (token budget deferred, bounded
equivalently by wall-clock/rounds/diff)

### Post-9.3 hardening (second-side verification of bf7c490)

The web-side verification confirmed the architecture and all loop/verifier
tests, and found three runtime defects in the "round goes wrong" seams —
fixed before the first live `--loop` run:

- **F1 (P1)** — the progress guard was degenerate on a green baseline
  (`0 >= 0` stopped every loop at round 2 with a misleading message, making
  `--max-rounds > 2` unreachable). Green-baseline loops now measure progress
  on **healable findings strictly decreasing**; non-empty baselines keep the
  baseline-set rule; the stop message names the metric it used.
- **F2 (P1)** — the edit round's exit code was discarded: a scope refusal,
  aider crash, or pre-commit rejection proceeded to test+review anyway.
  Nonzero rc now stops fail-closed (verdict `EDIT_FAILED`, audited);
  `heal_once` refuses to review a round that didn't complete.
- **F3 (P2)** — a wedged aider defeated the wall-clock budget (no subprocess
  timeout). The edit subprocess now gets the **remaining** budget as its
  timeout; a timeout is rc 124 = F2's failed-round path (one stop semantics).
- Minors: dead `RoundResult.failing_tests` dropped; per-round audit-stream
  choice documented; extra `--scope` args now warn instead of vanishing.

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
- [x] Driver passes round-N failing tests directly into the round-N+1 healing
      prompt (no retrieval on this path; built with the 9.2 driver).
- [x] On terminal verdict, `tool_capture.capture_session_tools()` stores the
      loop summary for cross-session recall. (2026-07-16: `_capture_loop_summary`
      fires on APPROVE/REJECT/NO_REVIEW — terminal review verdicts, not guard
      aborts; best-effort, degrades to a no-op when agentmemory is down.)
- [x] Privacy check: loop audit/memory records must honor the de-identification
      contract (commit a256a04) — no machine paths/hostnames in anything that
      could reach a public artifact. (2026-07-16 result: memory observations
      carry only repo-relative paths — test-pinned; per-round audit records are
      likewise repo-relative; the top-level loop session record includes `cwd`,
      acceptable because audit logs are local-only state. HOWEVER the check
      surfaced a breach in a different surface: the public GitHub repo's
      CLAUDE.md/plans carry fleet hostnames/IPs — tracked as a user decision in
      plans/open-items-2026-07-16.md, not a loop-record issue.)

**Effort:** 1 day (shrunk by dropping in-loop retrieval). **Status:** `done`
(8.5 confidence scoring is **off the loop's critical path entirely**)

### Post-dogfood hardening (2026-07-16, same day as live dogfood #2)

- Scope gate: aider commits with `--no-verify`, so the pre-commit scope gate
  never sees the loop's own commits (confirmed empirically: an off-scope
  commit with `PXX_SCOPE` set lands silently under `--no-verify`).
  **Decision — loop-level guard, not `--git-commit-verify`:** forcing aider
  through the full hook would re-run pytest per aider commit and let the
  hook's diff-cap deadlock legitimate rounds; lint/tests/diff already have
  loop-level gates ("judge only what the loop can own"). The missing one was
  scope: `_out_of_scope_changes()` now checks every round (committed + dirty
  + untracked vs the start SHA) and stops the loop fail-closed with verdict
  `OUT_OF_SCOPE`.
- Review leg: empty reviewer output fails closed; `preflight_review_backend()`
  refuses to start against an unusable backend (see Live dogfood #2 findings).

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
  (worked end-to-end: env-file config → tunnel → gpu-node-1 14b → ask-mode one-shot):
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

## Live dogfood #1 (2026-06-10) — transcript and calibration

**Setup:** seeded task — implement `pxx/duration.py::human_duration` against 3
failing tests (committed first; non-empty baseline). Fresh review state. gpu-node-1
14B via tunnel. Hooks NOT installed (as found).

**Result:** round 1: the model implemented the function correctly in one shot
— clean, typed, 3/3 tests pass, committed autonomously (`2c18f6b`), 16 diff
lines, baseline 3→0. The review pass then timed out (claude --print > 300s) →
verdict NO_REVIEW → the loop stopped fail-closed after exactly one round with
the correct remedy message. Per-round audit record and workflow state both
written as designed. Exit 1.

**Verdict on the loop machinery: everything fired as designed.** Baseline
measured, edit rc captured, diff counted against budget, NO_REVIEW refused a
second round. The convergence thesis is *half*-confirmed: the edit→test leg
converged in one round; the review→heal leg never got to run.

**Calibration findings → fixed immediately:**
- **A.** `run_review_pass`'s fixed 300s timeout guaranteed NO_REVIEW on a real
  full-repo review. Now `PXX_REVIEW_TIMEOUT` (default 900s).
- **B.** `self_lint` linted the whole tree including `services/*` (separate
  packages, own tooling) — pre-existing service debt made the loop's lint gate
  structurally red (APPROVE unreachable). Now scoped to `pxx/ tests/`.
- **C.** With hooks absent, an auto-`--yes`'d aider mutated `.gitignore` out
  of scope (re-adding the over-broad `.aider*` pattern!) and its commit lacked
  the `[autonomous]` tag. The --yes doctrine's boundary didn't exist. `--loop`
  now **refuses to start unless the pxx pre-commit hook is installed**.
- Minor: the loop runs `ruff format --check` but aider's output needed
  formatting — the round left `duration.py` check-clean but format-dirty
  (human-fixed post-run; consider a format step in the round).

**Pre-run-#2 fix pass (second-side verification verdicts, all adopted):**

- **Review output contract (P1).** The web side predicted run #2's NO_REVIEW
  before it happened: nothing told the reviewer where to write or what format
  the parser accepts (repo has codex/copilot/gemini review dirs, no claude/).
  `run_review_pass` now sends an explicit contract — target path
  `review/claude/claude-findings.md` + the exact `### F-NNN — … (P0, state:
  open)` header format + a clean-pass sentinel. NO_REVIEW variants now carry
  distinct diagnosable notes ("pass failed/timed out" vs "ran but left no
  artifacts — check the output contract" vs "only unparseable findings").
- **Shared hook gate (P1).** Moved from the cli layer into `loop.py` —
  `_require_hooks()` is called by BOTH `run_loop` and `heal_once`, so every
  current and future edit-round caller inherits it. Hardened per review:
  resolves the ACTIVE hook path via `git rev-parse --git-path` (core.hooksPath
  redirection and worktrees can't false-positive), and checks BOTH hooks —
  pre-commit (scope/diff/test gates) and prepare-commit-msg ([autonomous]
  tagging; run #1's untagged commit came from exactly that hook's absence).
- **Format step + lint-aware healing (P1, load-bearing for run #2).** Each
  round now runs `ruff format <scope>` after the edit and commits the fixup
  (formatting is deterministic — don't ask a 14B to do it), and when the lint
  gate is red the healing message includes concise ruff output (the model must
  be told WHAT is wrong; previously the loop stopped on the progress guard
  with the model never informed).
- **Budget-charged review + test legs (P2).** `run_review_pass(timeout=)` and
  `_failing_tests(timeout=)`; the loop passes min(ceiling, remaining budget)
  for both — the F3 siblings. Standalone `pxx --review` keeps the
  PXX_REVIEW_TIMEOUT/900s ceiling. Honest consequence accepted: the wall-clock
  budget now bounds everything, which is what "wall-clock budget" means.
- **Q3 (untagged commit `2c18f6b`):** stays in history — rewriting a
  PyPI-public repo is worse than the anomaly; the post-mortem + aider's
  Co-authored-by trailer are the provenance.
- **Q4 capture additions implemented:** per-round audit now records the
  verbatim healing message (capped 2000 chars), per-leg wall-clock
  (edit_s/test_s/review_s), and findings-by-severity incl. UNPARSEABLE count.

**Next live run (#2):** install hooks first (now enforced); comparable seeded
task; success criterion: a REVISE round's healing prompt visibly steers
round 2 — measured via the persisted per-round messages, not vibes.

## Live dogfood #2 (2026-07-16) — first APPROVE, vllm-host-1/Qwen3-Coder

Two runs, both genuine test-gap tasks (not seeded), office MacBook →
vllm-host-1 (`Qwen3-Coder` 30B-A3B FP8). Full transcripts in the 2026-07-16
audit log (`session_class: loop-round`).

**Run A** — add `_scrub_url` tests to `tests/test_audit.py` (zero prior
coverage): edit leg 28s, clean diff-format edit on the first attempt,
model self-corrected the missing import mid-response (second S/R block,
unprompted). Commit `a6a0c97`. Verdict `NO_REVIEW`: the review default
(`127.0.0.1:11434` + `qwen2.5:7b-instruct`) cannot work on this MacBook —
its Ollama has no models — so the leg 404'd and the loop correctly failed
closed. Fixed machine-locally: `PXX_REVIEW_URL=http://vllm-host-1:8001`,
`PXX_REVIEW_MODEL=Qwen3-Coder` in `~/.config/pxx/env` (trade-off noted
there: editor and reviewer are the same model until an independent
reviewer exists).

**Run B** — create `tests/test_self_modes.py` (`determine_session_class`,
`extract_self_fix_task`; both untested): round 1 edit 45s, reviewer said
APPROVE but `lint_rc=1` (F401 unused import) blocked termination — the
healing prompt carried the verbatim ruff output into round 2, which fixed
it in 17s → **APPROVE, exit 0**, commits `b5123f8`/`98c54ca`/`76492c2`.
This is the "healing prompt visibly steers round 2" criterion, observed
live. Wall-clock ≈90s of a 1800s budget; reviews sub-second.

**Hypothesis confirmed:** the 9ca1a22 malformed-edit retry path never
fired across three edit legs — it is gpu-node-1-legacy under Qwen3-Coder.
Keep it: it costs nothing when idle and the gpu-node-1 is still tier 3.

**Reviewer calibration:** the vllm-host-1 reviewer was hand-verified against a
planted-bug diff (two deliberate logic errors) — both flagged as P0 in
correct F-NNN format. The "no findings" APPROVE is a judgment, not a
rubber stamp.

**Findings (fixed):** aider's own commits bypass the pre-commit hook
(aider defaults `--no-git-commit-verify`), so the loop's `_lint_scope`
gate is the effective lint boundary — it worked. The `review/` findings
artifact was untracked and `is_dirty` counts untracked files, so residue
from one loop would block the next — now gitignored (`/review/`).

**Finding (fixed same-day):** `_run_local_review` mapped *empty* reviewer
output to "# Review pass: no findings." — a hollow response silently became
APPROVE instead of NO_REVIEW. Now fails closed (empty output → rc 1 → NO_REVIEW).
Same commit adds `preflight_review_backend()`: the loop refuses to start when
the review backend is unreachable or (for authoritative model listings,
including Ollama's `"data": null` empty shape) not serving the configured
model — run A paid a full edit+test leg before discovering that; now it costs
one GET.

## Success Criteria

- [x] `review_gate`, `workflow`, `governance` have unit tests (9.1 ✅).
- [x] `pxx --loop "<task>" --scope <file>` drives edit→test→review→heal to a
      terminal verdict (unit/guard-tested; live dogfood ✅ 2026-07-16 —
      APPROVE on a genuine task, see Live dogfood #2).
- [x] `healing_attempts` increments; `build_healing_prompt` is called; `--heal`
      is real (one REVISE round; `--loop` folds over it — see Decisions).
- [x] All three guards demonstrably stop a pathological loop (round cap,
      baseline-set failures not strictly decreasing, cumulative diff budget) —
      each has a dedicated test.
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
