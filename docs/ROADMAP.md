# pxx Roadmap

> This document replaces the v1 phase ledger (phases 0–22), which described
> the 1.x self-improvement program as planned against the 1.x codebase. That
> history is preserved in git at this path before this commit. For the v2
> architecture contracts see `DESIGN.md` and `DESIGN-ROADMAP.md`.

## Partnership / grant readiness — the bar (2026-08-02)

North star: pxx should be evaluable by a top-tier compute program (e.g. the
Apodex Frontier Program) as a **fully-audited, fail-closed engine that already
works** — not a proposal. The bar IS the project's existing discipline made
explicit: every external claim maps 1:1 to a dated receipt (R-NNN) with a
provenance grade and an explicit boundary; nothing aspirational is ever stated
as done; a compute request is framed as *scaling a proven engine*, never as
proof we already ran at scale.

**Already at the bar — do NOT re-do:** 23 evidence-gated receipts (R-001–R-023;
grades + boundaries documented at the top of `docs/RECEIPTS.md`); published MIT
package on PyPI (`pxx-orchestrator`, currently 2.3.2); host-enforced scope +
protected control plane proven un-subvertable by the agent (R-014); role-based
two-box routing (R-008/R-011); durable, un-gameable readiness ledger
(R-016/R-020); zero-phantom degrade across remote/local nodes (R-023); README
install + `pxx doctor` + hands-on tutorial (R-001, 6/6 on 8 GB). The public
narrative is grounded, not vaporware.

**Genuine gaps (evaluation-readiness, small):**
- [x] CI status badges (CI/PyPI/pyversions/MIT) in the README — done 2026-08-02.
- [x] Author receipts **R-024–R-026** for the just-shipped, independently
  re-verified fixes — F1 (untracked-restore on abort), F3 (model-404 advances
  the fallback chain), F5 (read/write scope split). Done 2026-08-02; RECEIPTS.md
  is current with PyPI 2.3.2.
- [ ] A one-command clean-machine reproduction of R-001 (install → `pxx doctor`
  → tutorial 6/6) captured as a dated check, so "runs in 5 min" is itself
  receipted.
- [ ] Submission hygiene (NOT a repo change): external links must target `v2`
  or a version tag — the default branch is `v2` and `main` is retired, so any
  `blob/main/…` URL 404s. (The in-repo README already uses `v2`.)

**The at-scale research a compute grant funds — honestly NOT yet done; each
lands as a NEW receipt only after it runs, never claimed in advance:**
- [ ] `pxx compare` / eval matrix at N-thousand parallel tool-calling loops.
  Today: single / small-scale. This is the core compute ask, not a current
  capability.
- [ ] Distributed verifier routing *at scale* — extend per-role routing
  (R-008/R-011) to many decoupled coder/judge nodes over isolated overlays.
- [ ] Long-horizon degradation studies: agentic drift, context-window
  degradation (extends R-006), tool-call template failures (extends R-010),
  over multi-GB corpora — measured, receipted, fp-rate tracked vs production.
- [ ] Live model-scored eval arms on real endpoints (the `ArmRunner` seam /
  R-019) — already under "Next"; scale is what the compute unlocks.

Guardrail: this section is itself subject to the receipts discipline. An item
moves from "gap" to "done" only when a receipt exists; a compute request
describes scaling a proven engine, never a result we have not produced.

## Shipped in 2.0.0

The complete program, built and verified (build track M0 → B10, each
milestone reviewer-verified by execution):

- **Async runtime; pxx owns the loop** — pluggable backends (native /
  aider / mock / replay), fresh context per round, bounded loops with a
  recovery ladder.
- **Fail-closed safety** — permission modes, canonicalized scope,
  deterministic hooks, budgets, the action broker as the single
  authorization authority.
- **Measurement** — 23-code terminal taxonomy with contributing codes, full
  per-leg RunOutcome, commit-bound reviews, immutable agent manifests with
  drift sentinels (served-model fingerprints), identity threading.
- **Evaluation** — a 30-case self-checking corpus across five families,
  held-out partitioning, reviewer calibration, deterministic replay.
- **Learning** — root-cause mining (correlation-only), constrained
  candidates on an allowlisted surface with an apply→verify write boundary,
  semantic loop detection.
- **Memory** — five knowledge layers, measured observed_utility via
  ablations, no success auto-conversion, entropy control (golden-principle
  lints, grades, deterministic GC).
- **Promotion & deployment** — held-out-only, multi-metric (cost ≤ 1.15×),
  risk-routed, hard-gates-absolute promotion; stable→candidate→shadow→
  canary→stable channels; seven circuit breakers; evidence-gated
  auto-promotion with preconditions and post-promotion auto-rollback.
- **Operation & orchestration** — scheduled improvement daemon, task
  reconciliation, checkpoint/resume, goal orchestration with per-node
  worktree isolation, full typed event vocabulary, outcome projection.
- **Authority & legibility** — WORKFLOW.md machine contract hashed into
  agent identity, ambiguity gate, evidence-linked findings, audit sampling.

## Next (2.x hardening)

**Sequencing (recorded 2026-08-08, post-2.4.0 / post-n8n battery R-035–R-040).**
Waves 1–3 shipped in 2.4.0, so the near-term order is:
1. ~~**Full-VRAM 30B benchmark**~~ **DONE 2026-08-09 (R-041–R-043):** full-VRAM 40 GB
   (69.3 tok/s) vs. 16 GB offload (31.8) ≈ 2.2×; dual-GPU NVLINK fan-out 198%; active-
   params dominate throughput. Now informs the multi-role routing assignments below.
2. ~~**Pluggable HITL transport**~~ **DONE 2026-08-19 (R-044/045 + R-046):** Slack Socket
   Mode approve / abort / modify, and the P4 shared-nonce bridge that lets a paused
   `pxx run`'s PreToolUse gate drive that card directly (see *Later*). Self-hosted `ntfy`
   remains the sovereign fallback. **One thing still owed:** a single live run joining the
   two halves (a real `pxx run` released by a real tap in Slack) — mechanism is
   Reproducible, the joined round-trip is not yet Attested.
3. **Live eval arms** (below) — the 2.5 headline, *unblocked* by Wave 2 and now the head
   of the queue.
Backlog candidate that emerged from the n8n work: a **first-class pxx n8n node** (vs. the
HTTP-node pattern already proven in R-035–R-040).

### Greenfield / from-scratch readiness — from the clone-from-docs probe (2026-08-18)

A probe that asked pxx to build a multi-integration app **from its documentation alone**
surfaced two items. Both recorded honestly, including a correction.

1. **Planner now runs on the `roles.plan` lane — SHIPPED.** The goal planner previously ran
   on the *coder* model; it now resolves `settings.effective_role("plan")` (a testable
   `_planner_settings` helper in `cli.py`), so a reasoning/planning model can decompose the
   goal while the coder builds it — the second consumer of the 2.5 role-lane map, matching
   the fleet SDLC design's separate PLAN role (reasoning brain != builder). Falls back to
   the coder model when `[roles.plan]` is unset (byte-identical to before).
   **Correction (honesty):** the probe first reported the planner *failing greenfield with
   OUT_OF_SCOPE* — that was a **HARNESS ARTIFACT** (a `uv run --directory` invocation ran the
   planner in the wrong repository), NOT a pxx defect. A clean reproduce shows `pxx goal`
   works greenfield end-to-end (planner -> worktree nodes -> integration-merged `stack.py`
   + tests). So this ship is an **architectural enhancement** (reasoning-planner support),
   not a bug-fix; the greenfield-failure claim is withdrawn. Receipt: the clean reproduce +
   `_planner_settings` unit tests (plan-role wiring + coder fallback).

2. **Greenfield test-gate vacuous-pass — INVESTIGATED, NOT A DEFECT (withdrawn 2026-08-18).**
   The probe reported a node reaching `outcome.json code=COMPLETED` on a module containing
   `SyntaxError: 'await' outside async function` (which pytest cannot even collect),
   suggesting the regression-relative gate (`loop.py:664-680`) vacuously passes broken
   greenfield code. **A deterministic reproduce disproves it:** `run_loop` driven with a
   suite that fails from round 1 (a broken "baseline" — the greenfield case) terminates
   `LOOP_DETECTED` / a non-success code, NEVER `COMPLETED`
   (`tests/test_loop.py::test_greenfield_failing_baseline_never_completes`); and the actual
   test_command returns non-zero on the broken code, so the gate sees the failure. So the
   node's recorded `COMPLETED` was a **confound** (lost per-round log + a sandbox whose state
   did not match the recorded outcome) — the same failure mode as the Gap B report, which
   also dissolved. **The gate is sound; the claim is withdrawn.** A regression guard now
   locks in that broken-from-baseline greenfield code cannot be declared done. Net: BOTH
   greenfield "gaps" from this probe were investigation/harness artifacts, not pxx defects —
   the real deliverables were the docs-as-framework validation and the `roles.plan` planner
   wiring (2.5.2). Lesson reinforced: reproduce cleanly before carding a gap.

- Earned enablement: run the daemon in production, accumulate the real-run
  and human-promotion counts the auto-promotion readiness bars require
  (100 real runs, 3 human promotions) — auto-promotion stays report-and-refuse
  until the platform earns it. ~~**IN PROGRESS since 2026-07-29**: the daemon
  runs under launchd on the primary workstation (hourly ticks)~~; first
  proposal human-reviewed and rejected same day. **Corrected 2026-08-01
  (dogfood):** the improvement daemon is NOT running — there is no launchd job
  for it (only SSH tunnels + a watch are under launchd), no live process, and
  the run-dir timeline shows no hourly cadence, so the automatic accumulation
  this item depends on was not happening. `daemon: running` reflects a
  control-file flag, not liveness. Earned enablement therefore accrues only from
  manual runs today — see the *daemon liveness* follow-up below, which is the
  prerequisite for this whole item. ~~real_runs at 50/100.~~
  **Corrected 2026-08-01 (R-013/R-014 dogfood):** `real_runs` is a live subdir
  count of `~/.local/state/pxx/runs/`, not a durable counter; the state dir was
  cleared out-of-band since, so the live count was **16/100** at the R-013/R-014
  snapshot (then **17/100** after the R-015 loop run — the count is live and
  moves with each recorded run) — the earlier "50" no longer holds. The `unresolved_critical_defects` ledger was likewise cleared
  (now absent → bar fails closed). Honest current bars: eval_cases 50/50 green;
  real_runs 16/100, human_promotions 0/3, unresolved_critical_defects unmet —
  NOT-READY.
  - **F-1 filter shipped (2026-08-01):** `real_runs` now counts a run only if it
    did genuine work — a real backend (not the `mock`/`replay` test doubles), a
    recorded terminal outcome, and tokens spent or a diff produced. Closes the
    "any subdir counts" gaming (a zero-work `MODEL_UNAVAILABLE` probe no longer
    inflates the bar). See the R-016 receipt.
  - **Follow-ups (best-practices, human-gated control plane):**
    - ~~*Durability / evidenced ledger*: the count is still a live `iterdir()`,
      so an external run-dir clear silently erases earned progress~~ **shipped
      (2026-08-01, R-020):** `real_runs` is now reconciled through a durable
      append-only ledger (`real-runs.jsonl`) — every genuine run is recorded
      once by id (once its append succeeds), and the count is the number of
      distinct recorded ids, which — for successfully-persisted ids — never
      shrinks when run dirs are rotated/cleared. Persistence is best-effort (an
      id whose append fails can still be lost on a later clear). Corrupt/
      undecodable ledger contents tolerated (fail closed); duplicate lines
      deduped on read.
    - *Daemon liveness*: ~~`daemon: running` reflects a control-file flag, not
      liveness~~ **status fixed (2026-08-01, R-018):** `pxx improve status` now
      reports **running / paused / stopped** from real process liveness — a live
      daemon holds the `daemon.lock` flock (`scheduler.is_running`), released by
      the OS even on a crash. The live workstation now honestly reads `daemon:
      stopped`. ~~**Still open:** *actually running* the daemon — no launchd job
      exists for it.~~ **Stood up (2026-08-01, R-022):** a macOS LaunchAgent
      (`local.pxx.improve-daemon`) runs `pxx improve daemon --once` hourly under
      pxx 2.3.0 — propose-only, non-mutating, logs to `~/Library/Logs/pxx-improve.log`,
      pause/resume honored per tick. Note the accrual boundary: the daemon
      accrues *proposals for human triage*, NOT `real_runs` — that bar still
      moves only from genuine `pxx` agent runs. Under the `--once`/cron model,
      `status` reads `stopped` between ticks by design.
- Live (non-scripted) eval arms on real endpoints, with the calibration
  fp-rate tracked against production fp. The 2.2.0 per-role routing + the
  SSH-tunnelled two-box lane (R-011) now provide the real endpoints this
  needs; the `ArmRunner` seam (`improve/candidate_eval.py`) is the documented
  injection point. **Reprioritized 2026-08-06 (Kimi K3 Swarm audit):** this
  item is *blocked* by Wave 2 below — until a promoted settings overlay actually
  applies to a production run, a live A/B arm measures nothing. Wave 2 (close
  the improvement loop) therefore lands **first**, then this.

### Kimi K3 Swarm audit — Waves 1–3 (recorded 2026-08-06)

An independent architecture + quality audit of the repo at `e770b19` (one commit
past `v2.3.7`) was produced by **Kimi K3 Swarm (2.8T-parameter frontier model,
high-effort, long run)** — six ready-to-apply patches (each `git apply --check`
clean + tested green in a second environment) plus ten design-level findings,
every item citing file:line evidence. Spot-verified against our tree before
recording (the top three code claims reproduced exactly). Landing order approved
2026-08-06. **All of it rides the normal gate** — apply → `verify` in our real
venv → PR → CI + CodeRabbit → merge, with a receipt wherever a claim is made;
an external patch is a *candidate*, not a merge.

**Wave 1 — validated bug/security fixes (land now, each its own PR):**
- **W1.1 — memory capture reads the wrong event key (bug).** `memory/capture.py`
  reads `result`/`output`; `tools/__init__.py` emits `result_preview` → every
  real `tool_result` observation is silently dropped (memory has never recorded
  a tool result). Verified at HEAD.
- **W1.2 — unbounded `git worktree add` (bug; receipt integrity).**
  `improve/channels.py` + `improve/scheduler.py` call `worktree add` with no
  `timeout=` — the two sites the 2.3.6/**R-030** "every git subprocess is
  bounded" work missed. Fix + amend R-030's boundary to name the gap it had.
- **W1.3 — fail-closed secrets gate on auto-commit (security).**
  `commit_session_work` commits the session delta with no content scan;
  `governance.scan_staged` already exists + is fail-closed but is never called
  on the runtime path. Wire it: any finding / unrunnable scan → no commit, work
  left staged (the `None` contract every caller already handles).
- **W1.4 — arm the PR-time governance scan (CI).** `ci.yml` runs `pxx check`
  without `--require-denylist`, so on same-repo PRs (where the secret *is*
  available) an empty denylist passes silently — the 1.3.x silent-green mode.
  Split the step: armed on same-repo PRs/pushes, loudly-unarmed on fork PRs.

**Wave 2 — close the improvement loop (the headline; unblocks live eval arms):**
- **W2 — stable settings overlay + `memory_retrieval_limit` becomes real.** The
  `improve/` plane (mining, content-hashed candidates, promotion guards, shadow/
  canary, autopromote) currently **never changes a production run**:
  `memory_retrieval_limit` has no `Settings` field/reader (`inject.py` hardcodes
  `_SEARCH_HITS=8`), and `ChannelManager.current(STABLE)` is consulted only by
  CLI reporting. Make the knob real, then apply the stable channel's *settings*-
  class overlay at session start — re-validated (content-hash tamper-proof),
  tighten-only budgets against *current* budgets, CLI always wins, fail-closed
  but never bricking (a broken optimizer artifact must never break every run).
  Then and only then: **Live eval arms** (the item above) measures a real effect.

**Wave 3 — learning-loop completeness:**
- **W3 — opt-in success-exemplar capture (`memory_capture_successes`, default
  off).** Today only FAILED sessions capture observations (Phase 20.5 — a
  deliberate "no silent success-to-knowledge"). Add a strictly opt-in path that
  records *gate-verified* successes as one compact `session_outcome` exemplar
  (dedupe grows `seen_count` → the signal the graduation ladder consumes).
  Depends on W1.1 (capture must actually work). Lands with a measured
  before/after RECEIPTS entry via `pxx eval`; fold in the dead `memory/utility.py`
  `measure_utilities` (finding #8) as the exemplar scorer.
- ~~**Clean loop termination (over-work).** On real tasks the loop reproducibly
  hits `BUDGET_EXCEEDED` — the coder keeps making tool calls past a passing
  solution instead of signalling done — observed on two independent codebases
  (R-014, pxx phase-2; R-015, a live SaaS backend).~~ **Shipped in two halves:**
  - **Terminal-code salvage (2.2.0, PR #12, R-017):** an over-worked run that
    *did* leave a verified, in-scope edit (tests pass, review doesn't block) is
    relabeled `COMPLETED` instead of `BUDGET_EXCEEDED`. Corrected the reporting,
    but the coder still burned the rounds (R-017's own boundary said so).
  - **Done-signal early-exit (2.3.7, R-031):** the fix for the waste itself. The
    coder now stops at the first objectively-complete edit-state (scope + diff +
    lint + tests pass) instead of running to the budget cap — an injected oracle
    (`SessionContext.done_check`, no new model-visible tool) the native backend
    consults after each edit turn; the loop's review gate still runs on the
    result. `budgets:tighten_budget` (R-013) only cut it off sooner; this ends it
    when it is actually done. Off-switch: `done_signal=false` / `PXX_DONE_SIGNAL=0`
    for slow suites. Attested two-box rounds-saved measurement pending.
- ~~Reliable reasoning judges for the *blocking* review gate. R-012 found a
  reasoning judge (qwen3.5) intermittently emits no parseable `VERDICT:` line…~~
  **Shipped (2026-08-01, R-019):** the reviewer sends a grammar-constrained
  `response_format` (json_schema) forcing a `{verdict, findings}` object, so a
  reasoning judge always emits a parseable verdict; the parser reads structured
  JSON first and falls back to free text (endpoints that reject/ignore
  `response_format` retry plain). Validated on real hardware: qwen3.5 in
  `--review-mode blocking` — 6/6 parseable, GOOD→APPROVE, BAD→REVISE with
  file-anchored findings that block. Reasoning judges are now usable for the
  blocking gate.
- **Review-gate independence (author != reviewer) — doctor half SHIPPED
  (2026-08-19, 2.5.4).** A reasoning judge that *parses* is not yet a judge that
  *is independent*: with no `[roles.review]` overlay, `effective_review_model`
  falls back to the coder `model`, so the shipped default runs the blocking gate
  as SELF-review — the same weights that produced the defect deciding whether the
  defect exists. An APPROVE from that gate is not evidence, but it is recorded as
  evidence, which is the vacuous-gate pattern this repo treats as a defect class
  in its own right. `pxx doctor` now reports `review:independence` (distinct model
  passes; same model warns; same model on a second endpoint *also* warns — separate
  hardware, identical blind spots), with a negative control proving the check fires
  on the shipped default. **Still open:** the enforcement half. Doctor tells you;
  `pxx loop --review` does not, so a run that never invokes doctor still blocks on
  a gate that cannot fail. Candidate: make `--review-mode blocking` refuse a
  self-review pairing unless an explicit `allow_self_review` opt-in is set — a
  behaviour change, so human-gated rather than autonomous. Related: `pxx calibrate`
  builds its reviewer from the same seam and is equally silent about it.
- **Dogfood-surfaced hardenings (2026-08-01, R-014), human-gated — the files
  are protected control plane, so these need human review, not autonomous
  edits:**
  - ~~`real_runs` bar integrity: `gather_counts` (`autopromote.py`) is a
    guardless live `iterdir()` — mock/replay/crashed/self runs all count, no
    durability. A failed probe bumped it live this session.~~ **Shipped:** the
    filter (count only real-backend runs that reached a terminal outcome with
    token/diff work) landed in **R-016**, and durability (an evidenced
    append-only ledger, so state-dir clears don't regress persisted ids) in
    **R-020** — see the "Follow-ups" entry above.
  - ~~Clarity-gate false-positive: `ready_to_act` (`clarify.py`) refuses any
    edit-verb task mentioning a `*.ext` token absent under cwd, even when the
    file is a runtime/generated artifact only described.~~ **Shipped
    (2026-08-01, R-021):** the missing-file signal is now governed per path —
    it gates only when an edit verb is the nearest cue to a specific path within
    its clause; a path introduced by a creation/description cue (`emits x.json`,
    `such as build/y.json`, `a new z.py`) is not treated as an edit target.
- The `pxx-reviews` triage loop for boundary-review artifacts. The
  *proposal-inbox* half **shipped in 2.1.5** (2026-07-29): `pxx improve
  triage list|qualify|reject` with reviewer identity, and the cycle honors
  human dispositions instead of re-proposing them every tick (found live
  on the daemon's first day). Boundary-review artifacts remain open.

## Shipped in 2.2.0 (2026-08-01)

Per-role model routing — the first step of the *Later* "model-backed boundary
roles" item. Authoritative detail in CHANGELOG.md; receipts R-008…R-012.

- **`[roles.review]` per-role model overlay** (+ `PXX_REVIEW_*`): the
  reviewer/judge runs on its own model/endpoint, resolved from a *sparse*
  overlay against the final coder model (a later `PXX_MODEL`/`PXX_API_KEY`
  still reaches the reviewer). **Reviewer routing is a data-egress surface**
  (the diff + any bearer token go to `base_url`), so — like hooks/MCP — the
  overlay is honoured only from user config, env, or CLI, never repo-local: a
  checked-in `pxx.toml` cannot redirect a review to an attacker endpoint (a
  critical exfil hole caught by the PR #8 CodeRabbit lane, fixed with
  regression tests before merge).
- **`pxx loop --review`** — opt-in model-backed judge in the edit loop
  (`--review-mode blocking|advisory`); off by default, byte-identical when
  unset, `--review-mode` without `--review` is a loud usage error.
- **Reasoning-model judges** — the review parser strips `<think>` scratchpads
  before reading the verdict (qwen3.5 / deepseek-r1 / qwen3 `/think`).
- Verified on real two-box hardware: `qwen3-coder:30b` coder on an RTX 5060 Ti
  (SSH-tunnelled) + a Mac judge complete one autonomous `pxx loop --review`
  (R-011). The GGUF tool-call/template gap that makes the Unsloth Q3 unusable
  as the agentic coder — a correct call emitted as `<tools>` prose the serving
  layer never lifts to structured `tool_calls` — is mapped in R-010.
- **Fixed:** `MemoryStore.add`/`search` were called un-awaited by the memory
  tools and MCP server — every `remember` silently dropped, `recall_memory`
  errored on the real store; now awaited via a shared `await_if_needed`.
- CI parity (PR #9): PR CI now runs `ruff format --check`, matching the release
  `verify` gate that the 2.2.0 tag first tripped (the gate held — nothing
  published — but it cost a formatting hotfix mid-release).

## Shipped in 2.1.6 + 2.1.7 (2026-07-30)

Same-day pair; authoritative detail in CHANGELOG.md.

- **2.1.6 — unknown-flag subcommand hint**: `pxx --upgrade` (and any
  dash-flag naming a subcommand) now hints the right spelling instead of
  being silently ignored by the 1.x compat handler. Also closed the
  long-open py3.13/aider work order as resolved-by-redesign — the
  marker-gated `[aider]` extra already fences the broken chain, and the
  v1-era `requires-python` cap would regress working 3.13 native installs
  (reviewer-verified against the published wheel).
- **2.1.7 — `pxx upgrade` verifies its own outcome** (PR #7): found by
  dogfooding the 2.1.6 rollout minutes after publish — uv exits 0 on a
  stale index and the old code claimed an upgrade that never happened (the
  pass-on-silence class, reaching the upgrade path itself). Post-upgrade
  fresh-process probe of the invoking entry point, anchored banner parse,
  bounded probe with kill-and-reap on cancellation, index-ahead installs
  count as success. Review: CodeRabbit PR lane (1 actionable, fixed
  in-flight) + three adversarial Claude rounds, both converged clean.

## Shipped in 2.1.1 (2026-07-26)

Defects verified against code on 2026-07-26 (live 6/6 tutorial run on local
Ollama plus two independent adversarial review rounds, both
verify-by-execution; reproduction records in
`~/.local/state/pxx/runs/20260726T15*`). Authoritative list in
CHANGELOG.md; highlights:

- **`GIT_*` environment scrub** (`pxx/gitenv.py`, every git spawn site +
  `run_shell` + the aider process env) — pxx invoked from inside a git
  hook previously operated on the *caller's* repository (proven: a leaked
  `GIT_DIR` staged deletion of every tracked file). Found and expanded by
  the independent review after the original patch list was drafted — the
  strongest item in the release came out of the review process itself.
- **Findings-less REVISE degrades to NO_REVIEW** instead of burning
  healing rounds on zero bullets (`review_error="empty"`).
- **Broken-aider fallback** — auto selection health-probes
  `aider --version`, falls back to native with a warning.
- **Doctor depth** — model presence verified on reachable endpoints
  (empty Ollama endpoints flagged), aider binary actually executed.
- **Context overflow surfaces actionably** ("raise `num_ctx`"), and
  **`edit_file` misses steer the model** to re-read and retry.
- **Tutorial troubleshooting** updated to match (incl. the loud-failing
  Ollama ≥ 0.32 behavior).
- Repo hygiene (preceded the patch): `.gitignore` + pre-commit privacy
  gate for `private/`, `review/`, `services/`, `.aider.*`; PyPI sidebar
  and README links fixed (no `main` branch exists).

## Open (post-2.1.1)

- `services/` tree fate: own repo vs. deliberately tracked after a
  privacy scan (ignored, not tracked, today — human decision).
- ~~Wire up the `unresolved_critical_defects` readiness count and add
  6 eval cases to reach the 50-case bar~~ **shipped** (2026-07-28): the
  defects ledger CLI + 6 real-shape eval cases landed in `4ae0371`
  (R-004: both bars green), then **2.1.3** hardened the ledger after the
  first external-tool review pass (CodeRabbit CLI, 5 confirmed findings):
  fail-closed shape validation (an object-shaped section previously
  counted as zero and could green the bar), flock + tmp-then-replace
  writes, CLI exit-2 on corrupt ledgers — see CHANGELOG. Automatic
  CodeRabbit review now runs on every PR (`.coderabbit.yaml`, verified
  live on #4).
- ~~Warn on set-but-unconsumed `PXX_*` env vars~~ **shipped in 2.1.4**
  (2026-07-29, PR #5): warn-once typo insurance; git-hook/CI vars
  allowlisted; warn-only. First live invocation surfaced 6 real
  set-but-unconsumed fleet vars from `~/.config/pxx/env` — an allowlist
  knob for deliberately-shared vars is an open follow-up if the noise
  proves unwanted.
- A quickstart subcommand (proposed, does not exist yet): scaffold the
  tutorial sandbox from packaged resources (today the wheel ships neither
  the tutorial nor the setup script).
- Auto-backend probe latency (~1–2 s per invocation with a healthy aider
  installed): cache or probe-on-failure redesign (deferred, documented).
- ~~Detect tool-call-shaped prose~~ (from the R-007 Camelid lane map)
  **shipped in 2.1.4** (2026-07-29, PR #5): `tool_call_prose` event +
  warning, model re-prompted, actionable failure after repeated drops —
  the "describes edits instead of making them" symptom is now
  machine-detectable AND self-healing. The bare-JSON shape was added
  from live evidence: a dogfooded run exhibited the exact failure while
  the detector was being built (qwen2.5-coder:7b answered with raw
  `edit_file` JSON, `diff_lines=0`).
- ~~Reviewer timeout on real-repo diffs~~ **shipped in 2.1.2**
  (2026-07-27, ~24 h find-to-ship): `PXX_REVIEW_TIMEOUT` with
  `PXX_NATIVE_TIMEOUT` fallback, never-blank failure reasons, and the
  reviewer context-overflow message — see CHANGELOG. ~~Remaining parity
  note from its review (non-blocking): malformed timeout env values fall
  back silently~~ resolved in **2.1.4**: presence-wins semantics (the
  review knob never silently falls through to the native one — production
  had the eval corpus's own or-falsy trap), warnings on malformed values,
  non-finite values rejected, and the env read moved to the config
  boundary.
- **Content-truthfulness gate (community feedback, 2026-08-12).** A deterministic
  claim-vs-read-content check: a separate axis from permission. Scope / R-014 governs
  what the agent may touch; it does not catch a model that stays fully in-scope and
  still reports something false about the code (claiming a file lacks a key it has;
  quoting a comment that isn't there). The objective gates (lint/tests/diff-cap) catch
  broken edits, not confident-but-wrong claims. Add a pass that cross-references the
  model's stated claims against actual tool-read content. Prior art to reuse: the
  faithfulness verifier already running on a sibling RAG system (each answer's claims
  checked against the retrieved text); bring that axis into the coding loop. **First
  increment (prototyped): quote-grounding** — every non-trivial code span the model quotes
  must appear in content it read or wrote (read tool-results + the diff), checked
  line-by-line so elision/reflow don't false-positive; an ungrounded line (a fabricated
  comment, invented code) is flagged. Wire it beside the deterministic objective gates
  (`_edit_objectively_done`), **advisory first** (fp-rate measured like the reviewer's) then
  promotable to a heal trigger. **Negative control (mandatory):** a fabricated quote MUST
  flag and a real one MUST pass, so the check can go red — a check that cannot fail is not a
  check. **Shipped advisory in 2.4.2** (`pxx/truthfulness.py` + native-loop wiring, fail-safe /
  non-blocking, `content_truthfulness` event, negative control in tests). **Still open:**
  promotion to a heal trigger, gated on measuring the false-positive rate on real runs first.
- **Diff-scoped retry on a failed review (community feedback, 2026-08-12).** The heal
  round already threads the reviewer's structured findings (the VERDICT contract's
  per-finding reasons) into the next round and re-edits the already-modified tree, so
  prior good work persists. Sharpening: label the rejection
  explicitly in the retry prompt ("rejected because X; preserve everything else unless
  it caused that"), so a heal fixes only the flagged issue instead of risking a
  reproduced omission or an unrelated regression.
- **External validation (community feedback, 2026-08-12).** A peer running their own
  local-orchestration setup independently converged on three pxx choices, now confirmed
  as good design: capability/role model routing (2.2.0 + the 2.4.1
  `--review-model`/`--review-base-url` flags), deterministic memory pre-injection at
  context-build (`memory_retrieval_limit`, not model-tool-dependent), and a standing
  versioned eval corpus (`pxx eval`) + reviewer-calibration suite (`pxx calibrate`, which
  tracks the judge's false-positive rate) gating every release. The two
  additive directions from the same feedback are the two items above.
- **Context paging — virtual memory for small-context models (evaluate; ref: Camelid's Context
  Paging Runtime, timtoole02, 2026-08).** Let a 4B/8K-window local model work on a whole repo by
  building a fresh, hard-capped **capsule** per action instead of replaying the transcript/repo.
  pxx already has the SPINE: fresh-context-per-round, host-run verification (the model can't grade
  its own work, R-014), honest-stop terminal codes (a non-success stop keeps its own code —
  `OUT_OF_SCOPE`, `LINT_BLOCKED`, … — and is never relabeled `COMPLETED`), typed/gated actions
  (`broker.authorize`), stale-edit rejection (`edit_file` exact-match). The **net-new subsystem**:
  (1) bounded-capsule assembly under a hard INPUT-token cap (real tokenizer) with a prioritized
  eviction order that never drops the target source; (2) **symbol cards + a tiny repo map**
  (structural memory, hash-invalidated); (3) a **`NEED_CONTEXT` page-fault** action (request exact
  source vs. guess; results bounded-by-reference); (4) **sha-256 source pages** as the one
  authority + patches carrying the expected source hash. Synergy: (3)/(4) are the same grounding
  axis as the content-truthfulness gate. Strategic: context assembly is pxx's agent-runtime layer
  while Camelid builds this into the inference-engine layer — decide **build-native vs.
  compose-on-Camelid** (interop today via `openai-compatible`; revive the parked timtoole02 RFC).
  Prototype + Neo (8 GB) receipt plan: `docs/context-paging-prototype.md`. **v0 mechanism BUILT**
  (`prototypes/context_paging/`, not shipped in the wheel): ledger + sha-256 source pages +
  capsule builder (hard cap, eviction never drops the target) + typed actions + crash-safe
  executor (idempotency + reconcile) + the loop. All **4 negative controls proven deterministically**
  in `tests/test_context_paging.py` (stale-sha reject / kill-restart resume no-replay /
  BLOCKED!=COMPLETED / over-budget eviction never drops target). **Still open:** the live 8 GB Neo
  receipt with a real 4B model (`run_neo.py`), then the build-native-vs-Camelid decision.

## Later

- Model-backed boundary roles (today's are deterministic). **First step
  shipped in 2.2.0** (see above): the reviewer role can run on its own
  model/endpoint. Remaining:
  - **Multi-role model routing (fleet-aware)** — extend per-role routing beyond
    `review` to a general `[roles.*]` table so each role runs on its own model
    and endpoint (only the reviewer role is wired through the runtime today; the
    config surface is fail-closed on any other role name). Recorded 2026-08-09
    after the full-VRAM/NVLINK benchmark (R-041–R-043) made the fleet's per-model
    speeds evidence-based.
    - **First buildable slice — three roles, two already proven.** Generalize the
      shipped `review_model` overlay to `[roles.plan] / [roles.code] /
      [roles.review]` and add exactly one *new* role, `plan`. `code`+`review`
      already work (the two-box loop: coder on the GPU box, judge on the Mac).
      The only net-new code is the config generalization (mirror `review_model`)
      plus a model-backed planning pass at task start. Prove it with one governed
      run: strong planner → tight scope → warm coder → separate judge.
    - **Warm/cold latency-class principle (handles Ollama's cold-load cost).**
      Roles split by latency budget: **hot-loop roles (`code`, `review`)** run
      many times per task → keep them *warm*; **one-shot upstream roles
      (`plan`, research, scope-proposal)** run once and tolerate a 30–60 s
      cold-load of the *strongest* model, amortized over a multi-minute task. The
      penalty lands where it doesn't hurt.
    - **Fleet map (avoid swapping by co-residence, not a scheduler).** The
      benchmark showed the 40 GB NVLINK box can hold the entire hot loop warm at
      once (coder ~22 GB + a small judge ~10 GB co-resident); a 16 GB box holds a
      fast MoE scoper (e.g. a 16 B coder at ~94 tok/s); the Mac holds a
      planner/interpreter/judge-fallback. Static per-box pinning first — no
      swap-scheduler on day one.
    - **Mechanism before policy (sequencing).** The routing *mechanism* (`roles`
      dict) is small, low-risk plumbing and can land near-term. The *evidence-
      based* role→model *assignment* rides on **live eval arms** (above), which
      score which model actually wins each role instead of guessing; today's
      benchmark is the first data point.
    - **Invariants kept.** Routing is config-driven, not agent-decided (the agent
      can't re-route itself); every role's model+endpoint lands in the audit log;
      **scoping stays host-enforced** — a planner model may *propose* scope, but
      the host still enforces it (R-014), so `scope` is a proposal feeding a
      policy gate, not a model that decides.
    - **Placement stays pluggable — pxx owns role→model *name*, not model→node.**
      Design constraint pinned up front so this never becomes a *second placement
      authority*: pxx role-routing resolves a logical role to a **model name**;
      turning that into an **endpoint + node** (health/load/failover) is
      *placement*, and placement is a pluggable adapter — a built-in **fleet-map**
      for standalone/OSS users (who have no router), and a thin **router adapter**
      when a placement layer already fronts the models (point `base_url` at the
      router; it does the node selection). Built this way, per-role routing never
      overlaps or fights an external placement/allowlist gate — it feeds it. (For a
      governed consumer that means the reviewer `base_url` resolves to the router /
      an allowlisted endpoint, never an arbitrary model URL.)
    - **Deferred (write down, don't build):** the `research` role (blocked on the
      governed `web_fetch` item below — no point routing a role with no tool yet);
      any auto-swap/model-manager logic (static pinning suffices given fleet VRAM).
  - **Model-back the deterministic Reproducer / Boundary-Reviewer /
    Artifact-Reviewer** roles (`roles.py`), calibrated the same way the
    review judge is (`pxx calibrate`).
- **Local-first reference inference engines.** Camelid / NanoCamelid
  (timtoole02; Rust, MIT, OpenAI-compatible, parity-receipts-driven) already
  drive pxx today via `provider = "openai-compatible"` (R-007 maps the working
  CPU lane and the v0.4.4 tool-surface gap). Direction: a documented,
  receipts-cross-linked "pxx + Camelid" reference stack — pxx as the policy /
  agent runtime, Camelid as the validated engine — and a collaboration RFC.
  Shared "refuse-unverified-output" ethos makes the fit natural; the
  integration stays optional and degrades to any OpenAI-compatible endpoint.
- Cross-repo knowledge federation.
- **Remote human-in-the-loop (HITL) approval — a fail-closed PreToolUse hook.**
  Turn an unattended `pxx run`/`loop` in a write-capable mode into one you
  supervise from your phone: at a gated action (destructive command, commit,
  significant spend) the run pauses, pushes an actionable notification with
  Approve / Abort buttons, and blocks on the response. This reuses the *existing*
  gate seam — a PreToolUse hook that already receives `{"tool","args"}`, already
  blocks the call until it exits, and is honored only from trusted config (never
  repo-local, A0b) — so it is a hook + a tiny listener, not a new subsystem.
  **Non-negotiable hardening (the naive `0.0.0.0:8080` + global signal-file
  script is fail-OPEN and must not gate a control plane):**
  - **Per-request HMAC over the decision, single-use nonce consumed atomically.**
    Sign a *canonical message binding `{request_id, nonce, decision}`* — not the
    nonce alone — so an `approve` signature can never be replayed as `abort` (or
    vice-versa) and a capability can't be swapped by editing a path/param. The
    listener verifies the signature, matches the server-minted nonce to *that
    specific* pending request, and consumes it **atomically** (a compare-and-set,
    so a double-tap / concurrent submit can't both win). Unknown / replayed /
    mismatched → ignored (no forgery, no replay, no wrong-request approval).
  - **Deadline → fail-closed DENY.** The wait has a timeout; no answer = HALT,
    never proceed. A stale signal must never auto-approve a later prompt.
  - **Bind `127.0.0.1`, reach it over a private overlay** (Tailscale/WireGuard /
    SSH tunnel), never a LAN-exposed or reverse-proxied unauth port.
  - **Don't leak the diff/command.** The notification carries a request id + a
    one-line summary only; details are pulled from the box, not shipped to a
    third-party push server.
  - **Receipt the decision — and gate the release ON the receipt, via a STRICT
    path.** A metadata-only `gate_decision` record (tool, args-hash, who, when)
    must **durably persist before the approval is released**. It cannot ride the
    normal path: both `AuditLog.record` and `EventBus.emit` swallow write /
    subscriber failures, so a routine emission could release approval with no
    JSONL line and no advanced `.head` anchor. The approval path performs a
    *verified* append that advances the hash-chain head and checks it, and treats
    any failure as DENY — the audit write is the fail-closed decision, not
    best-effort telemetry, so a log that cannot record yields no approval. A
    lock-screen tap becomes a hash-chained record, which is the whole point.
- **Pluggable HITL transport — Slack (Socket Mode) primary, self-hosted `ntfy`
    sovereign default.** The broker already mints signed, single-use approve/abort
    URLs (R-036/R-038); a *thin transport adapter* renders the message and carries
    back the decision, so the transport is swappable and the crypto/gate is
    unchanged. **Decision (2026-08-09): richer conversational feedback is
    preferred**, so HITL is not just approve/abort — the human can **reply with
    modifications** ("approve but re-scope to fileX", "why did you pick Y?"), which
    tips the primary transport to Slack.
  - **Slack via Socket Mode (primary).** Interactive buttons *and* threaded
    replies/modals, so it covers both simple approve/abort and richer HITL. Socket
    Mode has the app **dial out** over a websocket — **no inbound public endpoint**,
    so the broker stays behind NAT/firewall with nothing exposed; works on Slack
    free tier. Cost/asterisk: Slack is **SaaS** — the approval summary (which
    describes what the agent is about to do) transits Slack's cloud, so it is the
    *convenient/daily* transport, not the one the sovereignty pitch names.
  - **Self-hosted `ntfy` (sovereign default / fallback).** Docker, one binary on the
    fleet; nothing leaves your infra. Button-only (approve/abort, no free-text
    reply) but the on-thesis choice for the regulated / air-gapped pitch and where
    no data may egress. Composes with the existing report/notify seam
    (`PXX_REPORT_CHANNEL`/`PXX_REPORT_TARGET`).
  - **Security invariant (both transports).** **An action-bearing approval is a
    bearer capability** — whoever can tap Approve holds it — so the transport must
    be **auth'd and non-public**: Socket Mode is authenticated with no public
    endpoint; self-hosted `ntfy` is auth'd (tokens/ACLs). Public `ntfy.sh` is
    allowed only for **notify-only** alerts (no action buttons); the
    HMAC-over-decision binding narrows but does not remove the front-running window
    on a public transport, so approvals never ride one.
  - **Shipped (2026-08-09): Slack approve / abort / modify, proven live.** The Socket
    Mode broker (`docs/examples/hitl/slack_hitl_broker.py`) posts a Block Kit card with a
    Modify modal, receives the decision over the app's outbound websocket (no public
    endpoint), and writes a single-use decision. Approve/abort is R-044; the Modify modal
    (a revised scope + note handed back as a structured `modify` decision) is R-045. Setup
    and architecture live at `docs/examples/hitl/README.md`.
  - ~~**P4 (next, not built): bridge the Slack buttons/modal to the pxx PreToolUse
    gate.**~~ **BUILT (2026-08-19, R-046) — mechanism proven, live tap still owed.** The
    shared-nonce bridge shipped: the broker honours the *caller's* nonce (`resolve_nonce`)
    instead of minting its own, and a new **non-blocking** `POST /post-approval` lets the
    gate keep sole ownership of the deadline. Worth recording *why* this was not merely a
    wiring change — pointed at each other beforehand, the two stacks would have failed
    **permanently closed**: the card's buttons wrote `{broker-nonce}.decision` while the
    gate waited on `{gate-nonce}.decision`, so every gated call would have run to its
    deadline and denied. A silent, always-deny failure that looks exactly like a human
    saying no. Evidence: 13 bridge test functions (16 cases), **10 of them negative
    controls**, 3 driving a real `pxx.session.Session` (real `HookRunner`, real
    `ToolRegistry`, real gate subprocess, scripted model only) — approve → `COMPLETED` +
    file written; abort and no-answer → `HOOK_DENIED` + file **not** written; 38 with the
    broker's own suite. Mutating `resolve_nonce` back to the pre-P4
    always-mint behaviour fails 4 tests including the real-session allow path. New
    security surface handled: the caller-supplied nonce becomes a filename, so
    `sanitize_nonce` bounds it to ASCII-alphanumeric (`str.isalnum()` alone admits
    homoglyphs and RTL overrides) and rejects rather than sanitizes.
    **Still owed:** the two halves in ONE live run — a real `pxx run` released by a real
    tap in Slack. The tests use a loopback stub; the Slack leg is attested separately by
    R-044/045. Until that run happens the bridge is **Reproducible, not Attested**, and it
    needs a workspace and a human thumb, so it is Chris's step. Also unbuilt: *acting* on
    a `modify` decision (the gate writes and reads it, but treats any non-`approve` as
    deny), and enforcement of matching `HITL_DIR` between gate and broker (documented and
    printed at startup, not checked).
- **Governed `web_fetch` — a fail-closed research tool (the "browser" gap).** The
  biggest capability gap vs. hosted agents is the inability to read live docs. Close
  it with a *bounded HTTP fetch*, NOT an unrestricted scripted browser — a full
  JS/forms headless browser is a far larger attack surface and is deferred. The
  hazards are two-directional and both are first-class:
  - **Egress is allowlist-only, trusted-config-only.** Fetch only hosts on an
    operator allowlist (honored from user config / env / CLI, never repo-local —
    A0b; a checked-in `pxx.toml` must not add a fetch host). Default DENY; no
    arbitrary URLs; bounded response size + timeout (the git-bounding discipline).
  - **Ingress is UNTRUSTED data, never instructions.** Fetched content is fenced
    and can never carry tool directives or alter the plan (the same prompt-injection
    defense as the judge-input finding); a page saying "run `rm -rf`" is inert text.
  - **New model-visible tool ⇒ deliberate, opt-in, versioned.** Adding `web_fetch`
    changes `broker._TOOL_CLASSES` (byte-pinned by the psaios integration — the
    done-signal was built to avoid exactly this), so it ships OFF by default and the
    tool-surface bump is an explicit, versioned change, not a silent pin break.
  - **Strict receipt + fail-closed list loads.** Every fetch's `gate_decision`
    (host, url-hash, bytes) is a *verified* append that advances the hash-chain head
    — not the best-effort `AuditLog.record`, which swallows write errors — so an
    unrecorded fetch fails closed (no fetch without a receipt). The egress
    **allowlist and the content-denylist load fail-closed too**: missing / empty /
    unreadable denies. An unloadable allowlist is not "no restrictions," and
    `load_denylist` returning `()` must not read as "nothing to scan." Bytes a fetch
    would persist into memory/receipts are scanned before write; a finding *or a
    scan error* aborts the persist — so a fetch can't smuggle a secret or a host
    name out (the leak the armed scan caught this session) or in.
  - Honest tension: this adds network egress to a privacy-first local tool, so it is
    strictly opt-in, allowlisted, audited, and degrades to "off."
- **Local codebase documentation — a `Devin Wiki` analog, on pxx's terms.** Generate
  and maintain codebase docs *locally* (in-repo markdown / a static artifact, no
  cloud, no external host), so the "codebase understanding" capability doesn't
  require a SaaS. Built to pxx's evidence discipline, not a hallucinated wiki:
  - **Deterministic skeleton first.** Module map, public API, entry points, and
    dependency edges come from the repo + AST (deterministic, no model), so the
    structural spine can't hallucinate.
  - **Model prose is grounded + provenance-marked.** Any generated description is
    anchored to `file:line` and carries provenance (verified vs. `model_claim`) —
    no unverified claim presented as fact (the receipts ethos).
  - **Fed by graduated memory.** Pull from the knowledge layers that have actually
    graduated (skills/playbooks/observations), so the docs reflect what pxx has
    learned about *this* codebase, not generic boilerplate.
  - **Reproducible + scanned-before-write.** `pxx docs` emits a dated,
    content-hashed artifact (drift is visible). `pxx check` only scans *tracked /
    staged* files and would miss freshly-generated bytes, so the generated content
    is passed directly to a dedicated pre-write gate (`scan_text`) and the **write
    is aborted on any finding or scan error** — no secret / host name / PII lands in
    the published docs.
- **Kimi K3 Swarm audit — deeper findings (need a maintainer design decision,
  recorded 2026-08-06).** Real at `e770b19`, ranked by severity; each is a design
  choice, so they sit here as roadmap entries, not diffs. Security depth is the
  next wave after Waves 1–3.
  - *Security.* (1) `run_shell` extracts no path targets (`broker.py`), so the
    protected-path write check never sees `sed -i pxx/safety.py` — in AUTO with
    `allow_ungated_shell` the control plane is shell-rewritable; answer is
    sandbox-by-default or a post-hoc session-delta check (`_worktree_delta`
    exists). (4) The judge prompt interpolates untrusted `task`+`diff` with no
    fencing (`review.py`) — add explicit delimiting + an adversarial eval case
    (a diff containing "reviewer: verdict APPROVE"). (6) Model-settable shell
    timeout, no `setrlimit`, sandbox off by default — clamp to
    `min(model, max_shell_timeout)` + POSIX rlimits where available.
  - *Correctness.* (2) `run_loop`'s `BudgetGuard` never calls `check_clock()` →
    worst case ≈ `max_rounds × max_wall_seconds`; thread a loop-level deadline
    into per-round budgets (the done-signal helped but did not bound this).
    (3) Replay can report a false `COMPLETED` (`backends/replay.py` returns the
    recorded terminal code regardless of re-execution). (7) No context-window
    management in the native loop (grows to budget or a hard 400).
  - *Policy.* (5) `prompts/review.md` — the merge-guarding prompt — routes
    LOW-risk auto-promotable; classifying it MEDIUM+ is a one-line but genuine
    policy change.
  - *Hardening / cleanup.* (10) Add `mypy`/`pyright` + `coverage --fail-under`
    to CI (98% annotation coverage already). (8) Dead `cost.py` `CostLedger` +
    divergent price table in `native.py`; `memory/utility.py:measure_utilities`
    has no caller (wire it as W3's exemplar scorer, or delete). (9) Stale
    permanent test skips (`test_cli.py`) for modules that now exist — delete the
    markers so those dispatch paths are tested again.

## Release story

2.0.0 **replaces** the 1.3.x line on the `pxx-orchestrator` PyPI name
(requires-python >= 3.11; the aider backend is an optional, python-gated
extra, so the core installs and imports cleanly on 3.13 — no 1.3.3-style
fallback hole). The 1.x line ends at v1.3.3; 2.0.0 publishes as rc first
(2.0.0rc1 → soak → 2.0.0).
