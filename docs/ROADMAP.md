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
  injection point.
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

## Later

- Model-backed boundary roles (today's are deterministic). **First step
  shipped in 2.2.0** (see above): the reviewer role can run on its own
  model/endpoint. Remaining:
  - **Extend per-role routing beyond `review`** — `[roles.coder]` /
    `[roles.planner]` on the same overlay mechanism, so e.g. an
    NL-interpreter/planner runs on the Mac and feeds the GPU coder (only the
    reviewer role is wired through the runtime today; the config surface is
    fail-closed on any other role name).
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

## Release story

2.0.0 **replaces** the 1.3.x line on the `pxx-orchestrator` PyPI name
(requires-python >= 3.11; the aider backend is an optional, python-gated
extra, so the core installs and imports cleanly on 3.13 — no 1.3.3-style
fallback hole). The 1.x line ends at v1.3.3; 2.0.0 publishes as rc first
(2.0.0rc1 → soak → 2.0.0).
