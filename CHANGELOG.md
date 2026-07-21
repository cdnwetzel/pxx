# Changelog

All notable changes to pxx are documented here. The 1.x series history is
preserved in git (tag `v1.3.3` and earlier).

## [Unreleased]

B10 — orchestration & event fidelity (roadmap phases 22 + 10.8):

### Added

- **Per-node worktree isolation** (resolves O4): every goal node now runs
  its loop in its OWN git worktree — a node's mid-run state is invisible to
  siblings (test-pinned) — and changes merge into the main tree only at
  integration, with conflicts caught as `MERGE_CONFLICT` instead of silent
  clobbering. Disjoint-scope remains a fast-path guard for non-git trees.
- **Boundary role agents** (`pxx.roles`): Reproducer, Boundary-Reviewer
  (invoked on HIGH-tier broker decisions, auditable on the stream), and
  Artifact-Reviewer (vets the merged goal artifact; protected-path content
  rejects as `OUT_OF_SCOPE`) — with schema-versioned, fail-closed typed
  handoff artifacts and versioned planner skills loaded from the B5 skill
  layer.
- **Complete event vocabulary**: `run_created`, `prompt_rendered`,
  `tool_action_proposed`, `policy_decision`, `checkpoint_created`,
  `run_paused`, `resumed`, `evaluation_completed` — each emitted at its real
  site; unknown kinds rejected.
- **Outcome projection** (`pxx.projection`): the persisted `outcome.json`
  is now projected FROM the run's event stream — the audit log is the
  single source of truth and the record cannot disagree with it.

All four overclaims (O1–O4) are now resolved by building. The full
roadmap build-out (B1–B10) is complete.

B9 — continuous operation (roadmap phases 19 + 19.5 + 10.75):

### Added

- **Scheduler/daemon** (`improve/scheduler.py`, `pxx improve daemon`): drives
  `run_cycle` on an interval with three non-overlap guarantees (daemon
  flock, cycle flock, repo/GPU work lock), durable pause control that halts
  cleanly at tick boundaries, and deterministic per-candidate worktrees.
- **Task-claim state machine** (`improve/tasks.py`): QUEUED → CLAIMED →
  RUNNING → AWAITING_REVIEW → DONE | FAILED, durable claims + heartbeats +
  stall detection, and startup reconciliation that requeues crashed tasks
  (never lost, never duplicated, idempotent).
- **Checkpoint + resume** (`pxx/resume.py`, `pxx runs resume <id>`): a run
  pauses into a checkpoint and resumes deterministically via the replay
  substrate to the same terminal outcome; `checkpoint_created` joins the
  event vocabulary (B10).
- **Operator control plane**: `pxx improve status` (cycle, queue, inbox
  counts, daemon state), `pxx improve pause` / `resume`.

B8 — evidence-gated auto-promotion (roadmap phase 21):

### Added

- **CLI reachability**: `pxx improve readiness` (per-bar status +
  preconditions) and `pxx improve auto-promote <id> [--consent]`. Default
  posture is report-and-refuse; `--consent` is required to actually
  promote. Every decision prints the human-visibility bundle (candidate,
  rationale, expected-vs-observed evidence details, rollback command).
- **Real evidence producer** (`improve/evidence.py`): the four evidence
  bars — full corpus, held-out, adversarial, canary — are COMPUTED from
  records (evaluation.json, the canary ledger), never accepted as input
  booleans. Missing evidence = False bar (the M0 F1 anti-pattern, closed
  here too).
- **Precondition gate**: the roadmap's "ten mandatory items" (action
  broker, taxonomy, held-out corpus, calibration corpus, real hard gates,
  canary channel, promotion records, apply envelope, measured utility,
  workflow contract) are verified by execution; any missing item globally
  disables auto-promotion.
- **Post-promotion monitoring** (`monitor_promotion`): a tripped breaker on
  the new stable auto-rolls-back to the exact prior stable with a recorded
  reason; a healthy window does nothing.

Overclaim O1 is now fully resolved by building: auto-promotion is real,
CLI-reachable, evidence-gated, and globally disabled until the platform
earns it.

B7 — deployment: canary + circuit breakers (roadmap phase 18):

### Added

- **CANARY channel**: the full stable→candidate→shadow→canary→stable path
  now exists. Deterministic ~1-in-20 run selection by run_id hash (no RNG);
  canary outcomes accrue as distinct promotion evidence
  (`pxx agent canary`); green-over-20-runs makes a canary eligible to
  advance; a breaker trip retires the canary without touching stable.
- **The 3 missing circuit breakers** (now all 7): approval-rate-drop
  (Δ>0.2 below baseline), human-correction-spike (≥3 overrides/reverts),
  reviewer-availability-drop (<0.5). Each retires the offending channel
  with a recorded reason; healthy signals don't trip.
- **Exercised rollback + history**: activate-stable (gated by a passing
  promotion record, M0 F5) → rollback restores the exact previous stable,
  both events visible in `pxx agent history`.

B6 — promotion rigor (roadmap phase 17):

### Added

- **Held-out judgment recorded**: the promotion verdict records which
  partition produced it; development-only scorecards are refused even when
  the candidate wins every case (from B3.3's prerequisite).
- **Multi-metric guards** (`improve.promotion.compare`): beyond pass/fail —
  the roadmap's `cost ≤ 1.15× baseline` rule plus guards for avg rounds
  (≤1.25×), p95 duration (≤1.25×), diff size (≤1.5×), rollback rate
  (Δ≤0.05), and memory usefulness (drop ≤0.05, fed by B5's measured
  utility). A pass-rate-up/cost-up-16% candidate is NOT eligible. Unpriced
  or unmeasured metrics record as `unmeasured` — never fabricated, never
  silently blocking. Metric regressions are soft (human-overridable); hard
  gates stay absolute and override-proof.
- **Risk-class route table**: `classify_risk` (moved into promotion) maps a
  candidate to LOW/MEDIUM/HIGH → route `fast`/`standard`/`human` with the
  required evidence bars recorded on the verdict (B8's checklist). Unknown
  risk routes human-only (fail closed). `pxx compare` prints route, bars,
  and metric report.

B5 — outcome-aware memory + entropy control (roadmap phases 20 + 20.5):

### Added

- **Measured observed_utility** (`pxx.memory.utility`): memory ablations
  attribute outcome deltas from matched run pairs (with vs without an
  observation injected, by task_id) and write a MEASURED utility back —
  useful observations rise, misleading ones sink in search ranking
  (`0.4 + 0.3*evidence + 0.3*utility`, contamination down-weighted).
  The 5-level EVIDENCE_RANK ladder now drives capture (was dead code), and
  every observation carries provenance, validation dimension, and
  agent_version_id (Phase 20.2).
- **Five knowledge layers** (policy / repository / skill / playbook /
  episodic) with per-layer retention TTLs, layered injection ordering, a
  recurrence signal (`seen_count`), and a graduation ladder — recurring,
  high-utility lessons climb episodic → skill → playbook. v2→v2.1 migration
  preserves existing data.
- **No success auto-conversion** (Phase 20.5): COMPLETED sessions write
  NOTHING automatically; only failed runs capture episodic, low-trust
  (failed_run_inference + contamination) observations.
- **Entropy control** (`pxx.entropy`): golden-principle lints
  (`pxx improve principles`, wired into CI), per-layer quality grades
  (`pxx memory grades`), and a deterministic GC pass (`pxx memory gc`)
  pruning expired, low-utility, and contaminated entries.

B4 — learning & candidates (roadmap phases 15 + 16):

### Added

- **Richer mining** (`pxx.improve.mining`): cluster dimensions expanded
  (stage, task category, scope type, severity, retry behavior); recurring-
  pattern detectors (unparseable reviews, timeout clusters, lint blocks,
  memory↔diff-size correlation, model failure disparity); `RootCause`
  classification on every proposal (AMBIGUOUS_REQUIREMENTS / CONTEXT_MISSING
  / MODEL_CAPABILITY / PROMPT_DEFECT / TOOLING / EVALUATOR_DEFECT) with
  `reason_prompt_change_is_insufficient` — a MODEL_CAPABILITY failure
  proposes a model lever, never a prompt tweak. Correlation-only labeling
  preserved.
- **Semantic loop detection + recovery ladder** (`pxx.loop.ProgressVector`):
  identical failing-set + diff + findings across rounds → step 1 injects a
  re-plan prompt, step 2 stops with `LOOP_DETECTED` — never the blunt round
  cap. Healthy healing loops are unaffected.
- **Broader candidate classes** (`CandidateClass.SKILL / FEWSHOT / PLAYBOOK /
  DEMONSTRATION`), each with an allowlisted target surface and fail-closed
  validation (contrastive "bad + preferred" poles required for
  demonstrations).
- **Apply→verify envelope** (`pxx.improve.apply`): candidates are applied to
  ONLY their declared target and the envelope proves it — committed +
  worktree changes read with `--no-renames` and all untracked files
  enumerated; symlinked targets rejected; tampered candidates re-validated
  on apply.
- **`pxx improve evaluate-candidate <id>`**: re-validate → held-out corpus
  at baseline AND under the candidate → `promotion.compare` → verdict +
  recorded evidence. Candidate integrity validation serves as the
  permission_expansion evidence producer for the candidate arm.

B3 — evaluation depth (roadmap phase 13 + 14.3):

### Added

- **Corpus 18 → 30** (10 micro / 10 regression / 10 adversarial), all
  self-checking in CI with byte-identical repeatability. New regression
  cases are shaped from the repo's own M0/B1/B2 history (truncation anchor,
  stale review, clarify gate, context hint/staleness); new adversarial cases
  cover rename-collapse escapes, hardcoded expectations, weakened timeouts,
  new dependencies, and budget blowouts.
- **`no_new_dependencies`** check: a diff that adds a non-stdlib import or
  touches a dependency manifest fails the case.
- **Five evaluation families** (`Case.family`): capability / safety /
  recovery / context / economic, with a per-family breakdown on every
  scorecard (`family X: n/m` lines).
- **Held-out partitioning** (`Case.partition` dev|held-out):
  `pxx eval report --partition held-out` scores only held-out cases;
  `eval.report.compare` refuses development-only candidates and
  `improve.promotion.compare` requires held-out evidence (Phase 17.4 — the
  hard prerequisite for B6).
- **ReplayBackend**: replays a recorded run's tool calls deterministically
  from its run dir — same broker/gates as a live run, byte-identical across
  replays; truncated recorded args fail closed (metadata-only audit).
- **Calibration**: corpus 8 → 14 (7 flag / 7 clean) plus a verdict-agreement
  metric with an explicit `MIN_AGREEMENT` threshold.

B2 — identity & outcome fidelity (roadmap phases 11 + 12):

### Added

- **Canonical 21-code taxonomy** (`pxx.outcome`, later extended to 23 by
  `LOOP_DETECTED` and `MERGE_CONFLICT`): the coarse `GATE_FAILED` /
  `NO_PROGRESS` / `BACKEND_ERROR` / `SCOPE_VIOLATION` are split into their
  real causes — `EDIT_FAILED`, `EDIT_TIMEOUT`, `TEST_RUN_FAILED`,
  `TEST_REGRESSION`, `NO_TEST_PROGRESS`, `LINT_BLOCKED`, `REVIEW_REJECTED`,
  `REVIEW_UNAVAILABLE`, `REVIEW_EMPTY`, `REVIEW_UNPARSEABLE`, `OUT_OF_SCOPE`,
  `HOOKS_MISSING`, `MODEL_UNAVAILABLE`, `CONFIGURATION_INVALID` (the 12.2
  canonical set, plus `INTERRUPTED`, `CLARIFICATION_REQUIRED`, `HOOK_DENIED`).
  One run carries one terminal code plus `contributing_codes`.
- **Full 12.1 RunOutcome**: per-leg seconds (edit/test/review),
  `files_changed`, baseline/introduced/terminal failure counts, lint errors,
  `findings_by_severity`, `unparseable_review_count`,
  `injected_observation_ids` — persisted to `outcome.json` and round-tripped
  through `runs.py`. A lint gate (from WORKFLOW.md `commands.lint`) joins
  the loop's guards.
- **Commit-bound review validity** (`pxx.review.ReviewPacket`): a review
  approves a commit, not a task — when HEAD advances past the reviewed
  commit the loop forces a re-review, and fails closed if the tree never
  stabilizes.
- **Identity threading** (Phase 11.3): `task_id`, `repository_fingerprint`
  (HEAD + dirty + tracked-count), and `starting_commit` in every run record.
- **Drift sentinels**: `ModelFingerprint` probed from the served model
  (Ollama digest / resolved id) — a same-name re-pull mints a new
  `agent_version_id` and `pxx agents list` marks the superseded agents
  QUARANTINED; `aci_hash` (tool set + WORKFLOW.md) and `context_hash`
  (prompts + memory policy) join the manifest identity.
- `pxx metrics compare A B`: per-metric delta between two agents' run sets.

### Changed

- Exit-code mapping: review/test/lint scope codes exit 2 (gate); config and
  model errors exit 1; usage errors stay 64.

B1 — authority & legibility substrate (roadmap phases 14 + 10.5):

### Added

- **Action broker** (`pxx.broker`): every tool call is classified into a
  typed `ToolAction` (action class, risk tier, targets) and authorized
  through one authority at the `ToolRegistry.call` choke point — per-class
  permission profiles (WORKFLOW.md `[permissions]` or built-in defaults),
  scope enforcement, PreToolUse hooks as the deny substrate, and
  `tool_action_proposed` + `policy_decision` events on every call. Fail
  closed on unclassifiable actions and unknown modes.
- **WORKFLOW.md machine contract** (`pxx.workflow`): repository-owned TOML
  contract (states, budgets, commands, permission profiles, hooks,
  protected-paths mirror) with a fail-closed loader. Hashed — with the
  protected-paths list — into every agent manifest, so contract or guardrail
  edits mint a new `agent_version_id`.
- **Ambiguity gate** (`pxx.clarify`): `ready_to_act` runs before the first
  backend round; ambiguous tasks (empty, missing referenced file, test
  intent without a test command) stop with `CLARIFICATION_REQUIRED` and a
  surfaced question — without editing anything.
- **Evidence-linked findings** (`pxx.review`): findings without a concrete
  anchor (file+line, backticked input/command, named path) are dropped at
  parse time; an all-generic review degrades to `NO_REVIEW` — it can neither
  force healing loops nor silently approve.
- **Deterministic human audit sampling** (`pxx.audit_sampling`): 100% of
  promotions and protected-path-touching runs, ~20% of ordinary runs,
  reproducible by sha256 of the run id; recorded in `outcome.json`.
- **Legibility verbs**: `pxx workflow validate`, `pxx context audit`
  (docs present + trust mirrors in sync), `pxx docs check` (documented verbs
  exist) — wired into CI, plus `ARCHITECTURE.md` (module map).

M0 safety hardening (independent code review, fail-opens first):

### Fixed (security / fail-open)

- `pxx eval report` computed NO hard gates and stamped all five `True` —
  `pxx compare` then judged promotion eligibility on fabricated green.
  Gates are now derived from actual run evidence (`eval.report.compute_gates`);
  a gate with no evidence is `False` (fail closed).
- The loop's post-round scope re-check read only `git status` — a backend
  that commits (aider auto-commit) left a clean tree and escaped scope
  containment. It now diffs `pre_sha`..working-tree plus untracked files.
- All gate-relevant git reads run with `--no-renames`, so a rename can't
  collapse its source path out of scope/allowed-files evidence (loop scope
  re-check, diff budget, eval `allowed_files`, staged scan, memory capture).
- `pxx check` reported "clean" (exit 0) on any git error or outside a repo —
  the staged scan now fails closed (`PxxError` → exit 1).
- `pxx promote` built promotion records with `gates={}` and
  `pxx agent activate stable` applied ANY version unchecked. `promote` now
  requires `--scorecard` with real, all-green hard-gate evidence, and
  `activate stable` requires a passing promotion record for that version.

### Fixed (crash / integrity / leak / minor)

- `pxx runs/metrics/agents/verify` no longer crash on a malformed
  `outcome.json` field (defensive coercion to neutral defaults); export
  verbs report a clean error (exit 1) on unwritable paths.
- Audit: trailing truncation of the hash chain is now detected (`.head`
  sidecar anchors count + tip hash; unanchored logs fail closed), and an
  unparseable tail no longer silently reseeds GENESIS mid-chain — the
  damaged file rotates aside (loudly) and a fresh chain starts.
- MCP clients are tracked and closed after every run (no more leaked
  subprocess/reader task), the session SIGINT handler is removed on exit,
  and a timed-out hook process is reaped (source of the event-loop-closed
  teardown warning; the suite now runs under
  `-W error::pytest.PytestUnraisableExceptionWarning`).
- A typo'd subcommand fails loud (exit 64, with a suggestion) instead of
  silently routing to `ask` and hitting a model; Ctrl-C yields a clean 130
  without a traceback; usage errors are split from gate stops (64 vs 2);
  local/unpriced runs report `cost_usd=None` (never a fabricated $0.00);
  a missing public-denylist and vacuous calibration dimensions now print
  loud warnings instead of passing silently.

### Changed

- `RunOutcome.cost_usd` is now `float | None` (None = unpriced).
- `governance.scan_staged` raises `PxxError` when the staged fileset cannot
  be determined (previously returned `[]`).

Roadmap platform (phases 0, 11–22; see docs/ROADMAP.md and
DESIGN-ROADMAP.md):

### Added

- **Immutable behavior versioning** (`pxx.manifest`, phase 11): every session
  writes a run directory (`state_dir/runs/<run_id>/` with `manifest.json`,
  `task.json`, `events.jsonl`, `outcome.json`, optional `diff.patch`) and
  attaches `run_id` + `agent_version_id` to the audit stream — same config,
  same agent id, always. Best-effort: never gates a run.
- **Run/outcome analytics** (`pxx.runs`, `pxx.verify`, `pxx.cost`, phase 12):
  run store queries and metrics, `VerificationPacket` projection with
  gates-fired evidence, and pluggable cost accounting (versioned price table
  for known cloud models; local/unknown providers record usage with cost
  `None` — never fabricated dollars).
- **Evaluation harness** (`pxx.eval`, phase 13): TOML case format, disposable
  git-repo materialization, visible + hidden checks, pure-python unified-diff
  patching, honest/cheat self-checking corpus (18 seed cases under `evals/`:
  8 micro, 5 regression, 5 adversarial), corpus-fingerprinted scorecards that
  refuse mismatched comparisons.
- **Reviewer calibration** (`pxx.calibration`, phase 14): 8-case calibration
  corpus, recall/false-positive/format-compliance/availability metrics with
  explicit thresholds, using the production `pxx.review.parse_review` path.
- **Experience mining + constrained candidates + promotion policy**
  (`pxx.improve.mining/candidates/promotion`, phases 15–17): deterministic
  failure clustering labeled correlation-only, declarative single-variable
  candidates on an allowlisted surface (settings overlays, tighten-only
  budgets, `pxx/prompts/*.md` content) with fail-closed validation against
  `pxx.protected_paths`, absolute hard gates (no human override), and
  append-only promotion records.
- **Deployment machinery** (`pxx.improve.channels/cycle/autopromote`, phases
  18/19/21): stable/candidate/shadow/retired channels with proven rollback,
  circuit breakers, shadow runs that never touch the main worktree, a durable
  idempotent propose-only improvement cycle with triage inbox and anti-spam
  rules, and evidence-gated auto-promotion that refuses unless all readiness
  bars are green.
- **Goal orchestration** (`pxx.goal`, phase 22): goal → validated task DAG →
  bounded `run_loop` per node with disjoint-scope parallelism, dependent-skip
  on failure, and a final integration test pass.
- **Outcome-aware memory** (phase 20): observations carry
  `evidence_confidence`, `observed_utility`, `contamination_risk`, provenance
  outcome, and quarantine flags; search weighs evidence and excludes
  quarantined entries. Frequency is not correctness.
- **New CLI verbs**: `runs`, `agents`, `verify`, `metrics`, `eval`,
  `calibrate`, `improve`, `propose`, `compare`, `agent`, `promote`, `check`,
  `goal`.
- **Package smoke** (`scripts/smoke-package.sh`, phase 0.5 tier B): build →
  install into a throwaway venv → assert the packaging contract (version,
  doctor, prompts resource, `evals/` excluded, `pxx.eval`/`pxx.improve`
  importable); wired as a CI job after the test matrix.

## [2.0.0] — 2026-07-17

Ground-up rewrite ("pxx_ng"). pxx is now an async, event-sourced agent runtime
rather than an aider launcher.

### Added

- **Async runtime with pluggable backends** (`pxx.backends`): `NativeBackend`
  (pxx's own OpenAI-compatible tool-calling agent loop), `AiderBackend`
  (optional subprocess delegation, `pxx-orchestrator[aider]`), `MockBackend`
  (scripted, for tests). pxx owns the loop; backends cannot bypass policy.
- **Built-in tool surface** (`pxx.tools`): `read_file`, `write_file`,
  `edit_file` (exact-match), `list_files`, `search_files` (rg with pure-python
  fallback), `run_shell` (gated, optional `sandbox-exec`/`bubblewrap`
  sandboxing), `recall_memory`, `remember`. Deliberately ~8 tools for small
  local models.
- **Typed event stream + hash-chained audit** (`pxx.events`): every session
  event flows through an async bus; the audit log is tamper-evident JSONL,
  metadata-only, credential-scrubbed, verifiable via `pxx audit verify`.
- **Integrated memory** (`pxx.memory`): SQLite + FTS5 (BM25) + pure-python
  cosine vector search (0.4/0.6 hybrid), deterministic hash embeddings offline
  with automatic Ollama embeddings when reachable, TTL + monthly JSONL
  archival, deterministic session-start injection, post-session capture from
  the event stream and git diffs. No sidecar service required.
- **MCP interop** (`pxx.mcp`): stdio MCP client (spec 2025-11-25 subset) that
  mounts remote tools as `mcp__<server>__<tool>`, and `pxx mcp`, an MCP server
  exposing pxx memory to other agents.
- **Layered TOML config** (`pxx.config`): CLI > env > project `pxx.toml` >
  user config > defaults; unknown keys rejected. Legacy `PXX_OLLAMA_*` env and
  `~/.config/pxx/env` still honored.
- **Permission modes + hooks + budgets** (`pxx.safety`): ask/plan/edit/auto;
  PreToolUse/PostToolUse hooks as deterministic gates; cumulative budgets
  (rounds, tokens, cost, wall-clock, diff lines) with hard stops.
- **Endpoint router** (`pxx.router`): async probing of Ollama and
  OpenAI-compatible endpoints, fallback chains, known context-window table.
- **Bounded autonomous loop** (`pxx.loop`): edit → test → review rounds with
  fresh context per round, monotonic failing-set progress (`NO_TEST_PROGRESS`),
  post-hoc scope re-check, diff budget, and a fail-closed review gate
  (`pxx.review`, BLOCKING/ADVISORY).
- **Headless server** (`pxx.server`, `[server]` extra): FastAPI app with
  session start/cancel, SSE event streaming, memory proxy, optional bearer
  token auth.
- **New CLI**: `ask` (default), `edit`, `plan`, `run`, `loop`, `chat`,
  `memory`, `mcp`, `serve`, `doctor`, `upgrade`, `audit`. Exit codes:
  0 completed, 2 gate/budget stop, 64 usage error, 130 interrupted, 1 error.
- Terminal codes (`pxx.outcome`) replace message parsing; every run ends with
  exactly one machine-readable code.

### Changed

- Default mode remains read-only (`ask`), now enforced by the tool registry
  rather than aider's chat mode.
- Python requirement is `>=3.11` with **no upper bound** (aider is optional and
  constrained to `<3.13` only within its own extra).
- Core dependencies reduced to a single package: `httpx`.

### Removed (1.x architecture)

- `os.execv` handoff to aider; raw `sys.argv` scanning (argparse now);
  `agentmemory`/`9router`/`docs-rag-sme` sidecar services (memory and routing
  are in-process); the broken stdout-scraping observer and the unwired
  `/recall` slash commands (superseded by event-stream capture and real
  memory tools); the vendored service checkouts.

### Migration

- 1.x flag invocations (`pxx --edit ...`, `pxx --with-memory`) are rewritten
  by a compat shim. A 1.x `~/.pxx/memory.db` is detected and moved aside to
  `memory.db.v1-backup` on first 2.0 run. See docs/MIGRATION.md.
