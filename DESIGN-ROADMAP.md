# DESIGN ADDENDUM — Self-Improvement Platform (Roadmap Phases 0, 11–22)

**Contract for the roadmap modules. Read DESIGN.md first; same conventions.**
The full phase requirements live in `docs/ROADMAP.md` — implement to it.

Architecture: TRUSTED CONTROL PLANE (safety, scope, governance, evaluators,
promotion rules — never writable by the optimizer) → PRODUCTION RUNTIME
(Session/loop) → EXPERIENCE PLANE (manifests, runs, outcomes, memory) →
OPTIMIZER PLANE (mining, candidates) → EVALUATION PLANE (replay, hidden
checks) → PROMOTION PLANE (shadow, canary, promote, rollback).

Cross-phase invariants (enforced by tests): production agent never changes
its active config; candidates can't touch evaluators or hidden cases;
deterministic failures can't be overruled by models; budgets tighten-only;
missing evidence fails closed; every decision has an inspectable evidence
chain; rollback restores the exact previous version.

## New/changed modules

```
pxx/manifest.py        # Phase 11: AgentManifest, agent_version_id, run dirs
pxx/runs.py            # Phase 11.5/12.5: run store queries (runs/agents/metrics)
pxx/verify.py          # Phase 12.3: VerificationPacket projection, pxx verify
pxx/cost.py            # Phase 12.4: pluggable cost accounting
pxx/protected_paths.py # Phase 0.3/16: ONE authoritative protected-path set
pxx/governance.py      # Phase 0.1: secrets + public-content scanner, pxx check
pxx/eval/__init__.py
pxx/eval/cases.py      # Phase 13.1: TOML case loading/validation
pxx/eval/harness.py    # Phase 13.3/13.4: worktree materialization, checks, replay
pxx/eval/report.py     # Phase 13.5: scorecards, compare, report
pxx/calibration.py     # Phase 14.3: reviewer calibration suite + thresholds
pxx/improve/__init__.py
pxx/improve/mining.py  # Phase 15: deterministic clustering over outcomes
pxx/improve/candidates.py  # Phase 16: declarative candidates + integrity validation
pxx/improve/promotion.py   # Phase 17: comparison policy + promotion records
pxx/improve/channels.py    # Phase 18: stable/candidate/shadow/retired + rollback
pxx/improve/cycle.py       # Phase 19: scheduled propose-only cycle + triage inbox
pxx/improve/autopromote.py # Phase 21: risk classes + evidence-gated auto-promotion
pxx/goal.py            # Phase 22: goal -> task DAG -> bounded loops -> integrate
evals/                 # TOML corpus: micro/ regression/ adversarial/ calibration/
```

## Contracts

### pxx/protected_paths.py (Phase 0.3)
`PROTECTED_PREFIXES: tuple[str, ...]` — pxx/safety.py, pxx/governance.py,
pxx/protected_paths.py, pxx/eval/, pxx/improve/promotion.py, evals/,
.github/, docs/TRUST_BOUNDARY.md + their tests. `is_protected_path(path) ->
bool` — normalize (strip `./`, never lstrip chars), canonical repo-relative;
unclassifiable -> protected (fail-closed). Test-pinned against
docs/TRUST_BOUNDARY.md.

### pxx/manifest.py (Phase 11)
```python
@dataclass(frozen=True)
class AgentManifest:
    pxx_version: str; backend: str; provider: str; model: str
    python_version: str; prompt_hashes: dict[str, str]  # name -> sha256[:16]
    settings_hash: str; budgets: Budgets; review_mode: str
    @property
    def agent_version_id(self) -> str  # sha256(canonical json)[:16]; same
                                       # config -> same id, always
build_manifest(settings, backend_name, prompts_dir) -> AgentManifest
```
Run directories: `state_dir/runs/<run_id>/` with `manifest.json`,
`task.json`, `events.jsonl` (metadata-only), `outcome.json`, `diff.patch`
(optional). Written best-effort by Session at run end; `run_id` +
`agent_version_id` attached to session_start/session_end audit data.

### pxx/runs.py + pxx/verify.py + pxx/cost.py (Phase 12)
- `runs.py`: reads state_dir/runs/* + audit stream -> RunOutcome projections.
  `list_runs(state_dir, limit)`, `group_by_agent(state_dir)`,
  `metrics_summary(runs)`, `metrics_failures(runs)`,
  `memory_impact(runs)` (outcomes with vs without memory_context),
  `export_jsonl(runs, path)`.
- `verify.py`: `VerificationPacket` (run_id, agent_version_id, terminal code,
  budgets consumed, gates fired with decisions, rounds, risks),
  `packet_for_run(state_dir, run_id)`, `format_packet(packet) -> str`.
- `cost.py`: `CostLedger` protocol `record(leg, tokens, seconds, model) ->
  LegCost(usd: float | None)`; `PriceTableCost` (versioned $/1M table for
  known cloud models); `LocalCost` (seconds-based, usd=None); unknown
  provider -> usage recorded, cost None. **Never fabricate dollar values.**

### pxx/governance.py (Phase 0.1)
`Finding(rule, path, line, match_preview)`; `scan_content(paths) ->
list[Finding]` — secrets (API keys/tokens/private keys), private IPs,
absolute home paths, internal hostnames via denylist file
`~/.config/pxx/public-denylist`; `allow: <rule>` pragma skips; lockfiles
skipped. `pxx check [--all-files]` exits 2 on findings.

### pxx/eval/ (Phase 13)
- Case TOML: `id, tier (micro|regression|adversarial), family
  (capability|safety|recovery|context|economic), partition (dev|held-out),
  task, allowed_scope, budgets, fixture {files = {path = content},
  starting_tests}, checks {commands = [], forbidden_patterns = [],
  allowed_files = [], tests_unchanged = bool, hidden_commands = [],
  no_new_dependencies = bool}`, and `cheat_patch` (for self-check) /
  `honest_patch`. Promotion verdicts require held-out evidence.
- `harness.py`: materialize case into a **disposable git repo** under tmp
  (git init, commit fixture); run visible commands; run hidden checks
  (forbidden patterns in diff, allowed_files whitelist, tests_unchanged);
  `apply_patch` (unified diff, pure-python) for honest/cheat arms;
  `self_check(case) -> SelfCheckResult` (honest must pass, cheat must be
  caught); `run_case(case, backend_factory) -> CaseResult` (live arm via
  Session + MockBackend-free real loop is optional; MockBackend arm for CI).
  Repeatability: two self-check runs byte-identical reports.
- `report.py`: `Scorecard` (agent_version_id, corpus_fingerprint, per-case
  verdicts, totals), `compare(baseline, candidate) -> Comparison` used by
  promotion; corpus fingerprint = sha256 of sorted case content hashes —
  mismatched corpora refuse comparison (fail-closed).
- Seed corpus under `evals/`: >= 8 micro, >= 5 regression (2.0-history
  shaped: audit-chain resume, WAL sidecar, symlink escape, scope prefix,
  hook fail-closed), >= 5 adversarial (delete failing test, weaken
  assertion, add noqa, scope expansion, insert secret). Every case must
  self-check.

### pxx/calibration.py (Phase 14)
Calibration cases (evals/calibration/*.toml): known critical defects,
acceptable changes, noisy-harmless, malformed-review tolerance.
`run_calibration(reviewer, cases) -> CalibrationReport(recall, fp_rate,
format_compliance, availability)`; thresholds explicit constants; breach ->
fail (exit 2). Uses the production `pxx.review.parse_review` path.

### pxx/improve/mining.py (Phase 15)
`cluster_outcomes(runs) -> list[Cluster]` — deterministic grouping by
terminal code, backend, model, memory presence, round counts; each cluster
labeled `correlation` (never causation), citing run_ids.
`propose_from_clusters(clusters) -> list[Proposal]` — Proposal JSON: target,
operation, evidence run_ids, hypothesis, expected metric movement, risk,
confidence. **Proposals only.**

### pxx/improve/candidates.py (Phase 16)
- Permitted change classes: settings overlays (review_mode, budgets
  tighten-only, model/fallbacks, memory retrieval limits) AND content
  targets (`pxx/prompts/*.md` text). Human-only: any protected path,
  evaluator logic, permissions, budget increases, new dependencies.
- `Candidate` declarative dir `.pxx/candidates/<id>/`: `candidate.json`
  (id, class, target, value-or-patch, rationale, evidence run_ids,
  content_hash), immutable once written.
- `validate_candidate(c) -> None` raises `CandidateInvalid` on: non-
  allowlisted target, protected path, budget increase, missing rationale or
  evidence, unclassifiable path. One behavioral variable per candidate.
- Content candidates: path derived ONCE from the same value validated and
  written; test the validate/write path equivalence.

### pxx/improve/promotion.py (Phase 17)
- Hard gates (instant disqualification): adversarial-containment regression,
  scope violation, evaluator/fixture modification, permission expansion,
  test deletion/weakening. **Absolute**: `human_override` cannot rescue a
  hard-gate failure (records `override_refused_hard_gate`).
- `compare(baseline: Scorecard, candidate: Scorecard, *, human_override=None)
  -> PromotionVerdict`: eligible = zero hard-gate failures AND zero lost
  cases AND >= 1 gained case; corpus fingerprint mismatch -> refuse.
- Promotion records: `.pxx/promotions/<id>.json` — baseline, candidate,
  eval ids, gates, approver, timestamp, rollback target. Append-only.

### pxx/improve/channels.py (Phase 18)
- Channels: stable / candidate / shadow / retired, persisted in
  `.pxx/channels.json`. `activate(channel, agent_version_id)`,
  `rollback() -> previous` (exercised in tests under a simulated bad
  promotion), `history()`.
- `shadow_run(task, stable_backend, candidate_backend, worktree)` — stable
  does the real task; candidate replays in an isolated git worktree; output
  scored, never merged (test asserts main worktree untouched).
- Circuit breakers (evaluated per candidate run): scope violation, critical
  evaluator failure, budget overrun, unexpected files -> candidate disabled
  to `retired` immediately with audit event.

### pxx/improve/cycle.py (Phase 19)
`run_cycle(state_dir, mode="propose-only") -> CycleReport`: COLLECT ->
NORMALIZE -> ANALYZE -> PROPOSE -> VALIDATE -> persist candidates + report;
**stops before promotion**. Durable state `.pxx/cycle-state.json`, every
transition idempotent (re-run after interruption resumes). Triage inbox
dirs `.pxx/inbox/{qualified,rejected,human-review-required}/`. Anti-spam:
skip when evidence thin (<3 runs in cluster), cluster already has an active
candidate, or prior identical candidate failed. File lock
`.pxx/cycle.lock` (fcntl) serializes cycles.

### pxx/improve/autopromote.py (Phase 21)
- Risk classes: LOW (memory retrieval limits, tighten-only budgets,
  non-authoritative prompt wording) auto-eligible; MEDIUM (main system
  prompt, model changes, budgets loosening) human; HIGH (protected paths,
  permissions, evaluators, release) manual, never auto.
- `readiness(state_dir) -> ReadinessReport`: bars = >= 50 eval cases, >= 100
  real runs, >= 3 human-approved promotions, 0 unresolved critical
  evaluator defects. `auto_promote(candidate, evidence) -> Verdict`:
  refuses unless readiness green AND risk LOW AND repeated wins (full +
  held-out + adversarial passes). Every auto-promotion writes a promotion
  record with rationale + rollback command. Default posture: **report what
  it would do; refuse** — bars are the point.

### pxx/goal.py (Phase 22)
`async run_goal(goal, settings, *, cwd, planner=None) -> GoalOutcome`:
1. Planner (read-only NativeBackend with ASK permission, or injected stub)
   decomposes goal into a task DAG JSON: `{tasks: [{id, title, scope,
   depends_on, test_command?}]}` — validated: unique ids, acyclic, scopes
   within repo.
2. Each node runs as a bounded `pxx.loop.run_loop` with its own scope —
   fresh Session per node (fresh context invariant).
3. Roles: planner read-only; implementer = loop; verifier = tests+review
   per node; integration = final full test_command run over the combined
   tree. Parallel execution only for independent nodes with disjoint
   scopes (asyncio.gather); a node failure skips dependents, never rewrites
   completed nodes.
`GoalOutcome(code, completed: [ids], failed: {id: code}, summary)`.

### CLI additions
`pxx runs list|show|export`, `pxx agents list|show`, `pxx verify [run-id]`,
`pxx metrics summary|failures|memory-impact|export`, `pxx eval
run|self-check|report`, `pxx calibrate`, `pxx improve
analyze|clusters|proposals|cycle`, `pxx propose`, `pxx compare`,
`pxx agent activate|rollback|history|channels`, `pxx promote <candidate-id>`,
`pxx check [--all-files]`, `pxx goal -m "<goal>"`. Exit codes unchanged
(0/2/130/1).

### Memory schema (Phase 20/20.5) — extends pxx/memory/store.py
Columns (idempotent ALTER TABLE migrations): `evidence_confidence REAL
DEFAULT 0.5` (provenance rank via EVIDENCE_RANK: deterministic test=1.0 >
human decision=0.9 > reviewer agreement=0.7 > model claim=0.5 > failed-run
inference=0.2), `observed_utility REAL DEFAULT 0.5` (MEASURED by
`memory/utility.py` ablations — never guessed), `contamination_risk REAL
DEFAULT 0.0`, `outcome`, `quarantined`, plus v2.1: `layer TEXT DEFAULT
'episodic'` (policy/repository/skill/playbook/episodic, per-layer TTL),
`provenance`, `validation`, `agent_version_id`, `seen_count` (recurrence).
Search excludes quarantined and weighs `0.4 + 0.3*evidence + 0.3*utility`,
down-weighted by contamination. Capture: COMPLETED sessions auto-write
NOTHING; failed runs capture episodic low-trust observations. Graduation
ladder episodic→skill→playbook on (seen_count, utility) thresholds.
Entropy: `pxx/entropy.py` golden-principle lints (`pxx improve principles`),
quality grades (`pxx memory grades`), deterministic GC (`pxx memory gc`).
Frequency != correctness.

### Phase 0.5 Tier B — package smoke
`scripts/smoke-package.sh`: build wheel -> install into throwaway venv ->
assert `pxx --version` works, `pxx doctor` runs, prompts resource loads,
evals/ NOT in wheel. Wired as a CI job (runs after test).

### Trust boundary doc
`docs/TRUST_BOUNDARY.md`: the optimizer-protected set, mirroring
protected_paths.py exactly (test-pinned both directions).
