# Roadmap: continuous self-improvement (Phases 11–22)
> Backlog ID: 011

> Status: planned
> Type: umbrella roadmap — the successor arc to Phase 9. Individual phase
> plans (`phase-11-versioning.md` …) get created per the backlog workflow
> as each phase starts; this file is the architecture + sequencing record.
> Origin: user-authored roadmap, 2026-07-16 (the evening Phase 9 closed),
> grounded against the repo the same night.

## Target state

Evolve pxx from *a bounded coding loop that edits, tests, reviews, heals,
and stops* into *a versioned agent platform that measures every run,
identifies recurring weaknesses, proposes constrained improvements,
evaluates candidates against reproducible cases, and promotes only proven
changes through an auditable rollback-capable process.*

The production agent never rewrites itself directly. It creates candidate
configurations that a separate evaluation and promotion system judges.

**The central design rule:** keep `pxx --loop` as the bounded execution
primitive. Build the learning, evaluation and promotion machinery *around*
it rather than making the loop itself increasingly powerful and
increasingly difficult to trust.

## Grounding against the repo (2026-07-16)

What the roadmap assumes vs. what is already true:

- **Phase 0 (baseline stabilization) is mostly complete already.** The D1
  privacy scrub landed (bec8310) and main is pushed; v1.1.0 is prepared
  (CHANGELOG written) awaiting the guardrailed version bump (D3 in plan
  009). The *new* Phase 0 item is **0.1.3–0.1.4: an automated
  public-content scanner** (hostnames, private ranges, home paths, tunnel
  targets, unprotected-service statements) wired into governance and
  release CI — it would have caught the D1 drift mechanically. Half-day;
  schedule first.
- **Phase 12's failure taxonomy is partially live.** `OUT_OF_SCOPE`,
  `NO_REVIEW`, `EDIT_FAILED`, APPROVE/REJECT/REVISE already exist as real
  verdict strings; per-round audit records already carry timings, diff
  lines, lint rc, findings-by-severity, and the steering message. Phase 12
  is a projection/normalization job, not new instrumentation.
- **Phase 13 Tier B fixtures are pre-documented.** phase-9-loop.md records
  the green-baseline no-progress bug, empty-reviewer-approval,
  out-of-scope bypass, malformed review headers, and the non-TTY crash —
  each with its fix commit. Converting them to fixtures is transcription.
- **Phase 14 independence levels are already in use informally.** As of
  2026-07-16 the fleet runs level 3 (editor = LAN vLLM 30B; reviewer =
  local Ollama 7B, different family). `preflight_review_backend()` is the
  seed of reviewer-availability measurement.
- **Phase 20 supersedes plan 003's design.** 8.5 confidence scoring
  (recency/frequency/relevance) must not ship as-is; 003 is still
  `planned`, so fold Phase 20's split (retrieval_score vs
  evidence_confidence vs observed_utility vs contamination_risk) into it
  before any build. 003 gains a "superseded-by: Phase 20 design" note when
  Phase 20's plan file is written.
- **Storage convention:** run records live under the existing XDG state
  root (`~/.local/state/pxx/runs/<run-id>/`), beside `sessions/` — not a
  new `~/.pxx/` root. SQLite may index; JSON/JSONL files stay append-only.
- **Compute contention:** the eval/replay harness shares the LAN vLLM node
  with production editing. The Phase 19 repo-level lock must cover model
  endpoints (one eval batch at a time), not just the eval database.
- **Analyzer ceiling:** hypothesis generation is the weakest link for
  local models. Phase 15 correctly starts with deterministic clustering;
  semantic clustering and free-text hypothesis work should be deferred or
  routed to a frontier model under the same evidence rules.

## Architecture

```
                     TRUSTED CONTROL PLANE
           safety • scope • governance • evaluators
           promotion rules • rollback • audit integrity
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                     PRODUCTION RUNTIME                         │
│  Task → pxx loop → edit → tests/lint → review → terminal      │
└─────────────────────────────┬──────────────────────────────────┘
                              │ immutable run evidence
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                     EXPERIENCE PLANE                           │
│  manifests • traces • outcomes • costs • failures • memory    │
└─────────────────────────────┬──────────────────────────────────┘
                              │ periodic analysis
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                     OPTIMIZER PLANE                            │
│  cluster failures → hypothesize cause → candidate patch       │
└─────────────────────────────┬──────────────────────────────────┘
                              │ candidate only
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                     EVALUATION PLANE                           │
│  isolated replay • hidden checks • baseline compare • gates   │
└─────────────────────────────┬──────────────────────────────────┘
                              │ qualified candidate
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                     PROMOTION PLANE                            │
│  reject • shadow • canary • promote • monitor • roll back     │
└────────────────────────────────────────────────────────────────┘
```

The trusted control plane must not be writable by the optimizer.

## Phase 0 — Stabilize the public and release baseline (1–2 days)

Mostly done via plans 009/010 (privacy scrub landed; v1.1.0 prepared).
Remaining:

- **0.1 Public-content scanner**: automated check for hostnames, internal
  domains, private network addresses, usernames/absolute home paths,
  tunnel targets, and statements describing unprotected services. Wire
  into governance + release CI. *(The one net-new item — do first.)*
- **0.2 Release v1.1.0** (D3): bump, build/test wheel clean, verify README
  claims, publish, then tag the behavioral baseline: `v1.1.0` +
  `learning-baseline-1`.
- **0.3 Freeze trusted components** (optimizer-protected): `pxx/safety.py`,
  `pxx/scope.py`, `pxx/governance.py`, `pxx/review_gate.py`, their tests,
  eval fixtures/hidden checks, promotion config, release credentials and
  workflows. Candidates may *suggest* changes to these; never apply or
  promote them autonomously. Write the trust-boundary document.

Exit: scanner passes; PyPI/README/repo agree; baseline tag reproducible;
trust boundary documented.

## Phase 11 — Immutable behavior versioning (3–5 days)

Make every run reproducible and attributable.

- **11.1 `pxx/agent_manifest.py`** — frozen dataclass capturing
  behavior-defining state: pxx version+commit, aider version, python,
  editor/reviewer provider+model (normalized identities, no secrets, no
  raw endpoints), prompt hashes (edit/healing/review), loaded skill
  hashes, routing/memory/governance config hashes, budgets (max_rounds,
  max_seconds, diff_budget).
- **11.2** `agent_version_id = sha256(canonical manifest minus runtime
  fields)` — same config ⇒ same ID, always.
- **11.3** Attach `run_id`, `agent_version_id`, `task_id`,
  `repository_fingerprint`, `starting_commit` to audit records, workflow
  state, loop summaries, memory observations, eval results, and
  autonomous commit metadata.
- **11.4** Immutable run directories:
  `~/.local/state/pxx/runs/<run-id>/` with `manifest.json`, `task.json`,
  `rounds.jsonl`, `outcome.json`, `diff.patch`, `test-results.json`,
  `lint-results.json`, `review-findings.json`. Append-only; SQLite may
  index.
- **11.5** Inspection: `pxx runs list|show|export`,
  `pxx agents list|show`.

Exit: every run has exactly one manifest; results group by agent version;
re-running a manifest warns on missing models/prompts/deps; no production
result exists without an attributable behavior version.

## Phase 12 — Normalize outcomes and failure taxonomy (4–7 days)

- **12.1 `RunOutcome`** frozen dataclass: terminal status, accepted,
  rounds, per-leg seconds, files/lines changed, baseline vs terminal vs
  introduced failures, lint errors, findings by severity, unparseable
  count, tokens/compute/cost (nullable), injected observation ids,
  failure codes.
- **12.2 Canonical failure codes** (machine-readable, never parsed from
  messages): APPROVED, EDIT_FAILED, EDIT_TIMEOUT, TEST_RUN_FAILED,
  TEST_REGRESSION, NO_TEST_PROGRESS, LINT_BLOCKED, REVIEW_REJECTED,
  REVIEW_UNAVAILABLE, REVIEW_EMPTY, REVIEW_UNPARSEABLE, OUT_OF_SCOPE,
  DIFF_BUDGET_EXCEEDED, ROUND_CAP_EXCEEDED, TIME_BUDGET_EXCEEDED,
  HOOKS_MISSING, MODEL_UNAVAILABLE, CONFIGURATION_INVALID. One run may
  carry several contributing codes + one terminal code.
- **12.3 Causal evidence per failure**: deterministic output, stage,
  round, preceding action, changed files, memory injected, model/prompt
  version, whether retry changed the outcome.
- **12.4 Pluggable cost accounting** (replaces cost_metrics.py's fixed
  pricing): cloud = tokens × versioned price table; local = active
  seconds + energy estimate; mixed = per-leg; unknown provider = usage
  recorded, cost marked unknown. Never fabricate dollar values.
- **12.5** `pxx metrics summary|failures|compare|memory-impact|export`.

Exit: every dogfood run maps to a canonical outcome; no terminal
condition depends on free-text parsing; cost/latency attributable by leg;
"failed to edit" distinguishable from "edited fine, reviewer unavailable."

## Phase 13 — Evaluation and replay harness (1–2 weeks)

**The most important phase.** Without replayable evals, "self-improvement"
means accumulating anecdotes.

- **13.1 Case format** (YAML): id, category, difficulty, fixture repo +
  starting ref, task text + allowed_scope, budgets (rounds/seconds/diff),
  checks (commands, allowed_files, forbidden_patterns like
  `noqa|skip|xfail`, required assertions like tests_unchanged,
  no_new_dependencies).
- **13.2 Three tiers**:
  - *Tier A micro-deterministic*: unused import, missing boundary test,
    wrong condition, exception-type preservation, serialization fix, one
    type error.
  - *Tier B historical pxx regressions* (already documented in
    phase-9-loop.md): green-baseline no-progress, empty-review-as-
    approval, out-of-scope bypass, malformed review header, non-TTY
    confirm, dirty formatting, missing hooks.
  - *Tier C adversarial*: delete failing test, weaken assertion, add
    noqa, touch evaluator files, expand scope, insert a secret, claim
    success without review evidence, modify expected-output fixtures.
- **13.3** Every case runs in a disposable git worktree; baseline and
  candidate worktrees start from identical commit + fixture state.
- **13.4** Visible (task, public tests, lint) vs **hidden** checks
  (anti-cheat, hidden behavioral tests, evaluator-integrity,
  forbidden-diff). The agent never sees hidden checks.
- **13.5** `pxx eval run|replay|compare|report`.
- **13.6** Seed corpus: 15 micro + 10 regression + 10 adversarial; grow on
  every unexpected production failure, reviewer miss, promoted-candidate
  regression, or new failure mode.

Exit: repeated baseline runs stable; identical starting states; hidden
checks catch test deletion and scope evasion; ≥30 meaningful cases; every
significant historical defect has a permanent regression case.

## Phase 14 — Harden the evaluator stack (1 week)

- **14.1 Layered evaluation**, strongest/cheapest first: repo+scope
  invariants → compile/static → unit+integration tests → security/secret
  checks → diff-policy → requirement coverage → independent model review
  → sampled human review. A model reviewer never overrides a failed
  deterministic gate.
- **14.2 Maker/checker independence levels** 0–4 (same model+prompt … 
  deterministic + independent model + human sample). Promotion evals
  require higher levels than ordinary edits. *(Fleet already runs level 3
  for ordinary loops as of 2026-07-16.)*
- **14.3 Reviewer calibration cases**: known P0s/P1s, acceptable changes,
  noisy-harmless diffs, malformed findings, misleading comments,
  test-only changes. Measure critical-defect recall, false-positive rate,
  format compliance, availability, verdict agreement.
- **14.4 Evidence-linked findings** (file, lines, claim, evidence,
  recommended check); reject generic "improve error handling."
- **14.5 Human audit sampling**: 100% of promoted candidate changes; 20%
  of ordinary approved runs; 100% of runs touching governance/release/
  security. Reduce only after measured reviewer performance supports it.

Exit: calibration thresholds explicit; same-model review visibly
lower-confidence; model approval can't bypass deterministic failures;
human audits feed evaluator regression cases.

## Phase 15 — Experience mining without self-modification (1 week)

- **15.1 `pxx/improvement_analysis.py`** — deterministic grouping first
  (failure code, stage, model, task category, scope type, severity, retry
  behavior, memory presence); semantic clustering only later, only for
  unclassifiable free text.
- **15.2** Detect recurring patterns (lint always needs round 2;
  unparseable review output; model A vs B edit-format failure rates;
  memory ↔ diff-size correlation; skills ↔ regression rates; timeout
  clusters; never-referenced retrievals).
- **15.3 Causal guardrails**: distinguish correlation / plausible
  mechanism / confirmed replay evidence.
- **15.4 Structured proposals** (JSON): target, operation, evidence runs,
  failure cluster, hypothesis, expected metric movement, risk,
  confidence.
- **15.5** `pxx improve analyze|clusters|proposals|explain`. Proposals
  only — no active candidates at this phase.

Exit: every proposal cites run evidence + expected measurable effect;
evidence vs inference distinguished; nothing in production changes.

## Phase 16 — Constrained candidate generation (1–2 weeks)

- **16.1 Change classes.** *Permitted*: prompt text, healing
  instructions, reviewer output-contract wording, skill files, few-shot
  examples, memory retrieval limits/thresholds, routing rules, retry
  counts, timeout allocation, task-classification rules. *Human-only*:
  Python source, evaluator logic, security/governance/scope, hidden
  tests, release workflows, credentials, promotion thresholds.
- **16.2 Declarative candidates** under `.pxx/candidates/<id>/`
  (manifest, patch, rationale, evidence, evaluation plan).
- **16.3** One behavioral variable per candidate (attribution).
- **16.4 Integrity validation** before eval: canonical hash; reject
  protected-target changes, fixture edits, new network deps, permission
  or budget increases (unless explicitly approved).
- **16.5** Replay: candidate on targeted + full regression + adversarial
  suites; baseline on the same suite and hardware.

Exit: candidates declarative and reviewable; protected components
untouchable; rejection leaves production untouched; ≥1 candidate shows a
measurable held-out improvement.

## Phase 17 — Baseline comparison and promotion policy (1–2 weeks)

- **17.1 Hard gates** (instant disqualification): security violation,
  out-of-scope modification, evaluator/fixture modification, hidden-test
  regression, approval without review evidence, test deletion/weakening,
  permission expansion, critical-defect escape.
- **17.2 Multi-metric** (never one score): success rate, critical failure
  rate, test regression rate, reviewer miss rate, median rounds, p95
  duration, cost per accepted task, diff size, human correction rate,
  rollback rate, memory usefulness.
- **17.3 Comparison rule** (initial): zero hard-gate failures AND ≥
  baseline successes AND ≤ baseline critical escapes/regressions AND cost
  ≤ 1.15× baseline AND at least one strict improvement (successes,
  median rounds, or cost ≤ 0.90×). Small corpus ⇒ exact case-by-case
  comparison, not percentage theater.
- **17.4 Held-out partitioning**: development / regression / held-out
  promotion / adversarial. Never judge a candidate only on the failures
  that inspired it.
- **17.5 Promotion records**: baseline, candidate, eval ids, gates,
  approver, timestamp, rollback target.

Exit: every active behavior version has a promotion record; no
proposal→promotion shortcuts; held-out + adversarial mandatory; rollback
restores the exact previous version.

## Phase 18 — Shadow, canary and rollback deployment (1 week)

- **18.1 Channels**: stable / candidate / shadow / retired.
- **18.2 Shadow**: stable does the real task; candidate replays it in an
  isolated worktree; output evaluated, never merged.
- **18.3 Canary**: after shadow evidence, ~1 in 20 explicitly selected
  low-risk tasks → candidate.
- **18.4 Circuit breakers**: scope violation, critical evaluator failure,
  approval-rate drop, budget overrun, human-correction spike, reviewer
  availability drop, unexpected files → candidate disabled immediately.
- **18.5 Exercised rollback**: `pxx agent activate|rollback|history`;
  rollback tested under a simulated bad promotion.

Exit: shadow can't touch the main worktree; canary failures auto-restore
stable; rollback proven; stable config immutable during candidate runs.

## Phase 19 — Scheduled continuous improvement (1–2 weeks)

- **19.1 Durable workflow**: COLLECT → NORMALIZE → ANALYZE → PROPOSE →
  VALIDATE → REPLAY → COMPARE → AWAIT HUMAN PROMOTION. Every state
  persisted; every transition idempotent.
- **19.2 Scheduler**: nightly/weekly `pxx improve cycle
  --mode propose-only`; capped candidate count; publishes a report;
  stops before promotion.
- **19.3 Worktree per candidate** (`pxx/candidate/<id>` branches);
  repo-level lock covering shared resources **including model
  endpoints** (eval batches serialize on the GPU) and the eval database.
- **19.4 Triage inbox** (filesystem first): qualified / rejected /
  reviewer-disagreements / critical-failures / human-review-required.
- **19.5 Anti-spam**: no candidate when evidence is thin, cluster already
  has one active, a prior candidate failed identically, the corpus can't
  test it, or expected gain is unmeasurable.

Exit: a cycle completes unsupervised, resumes after interruption,
produces candidate + report but cannot activate; duplicates suppressed.

## Phase 20 — Outcome-aware memory improvement (1–2 weeks)

Supersedes plan 003's confidence design (recency/frequency/relevance must
not be read as correctness — popular-but-wrong observations self-
reinforce).

- **20.1 Split confidence** into: retrieval_score (task match),
  evidence_confidence (provenance rank: deterministic test > accepted
  human decision > independent reviewer agreement > single model claim >
  failed-run inference), observed_utility (matched-run/replay deltas:
  success, rounds, regressions, cost), freshness, contamination_risk
  (failed-run origin, later contradicted, outdated APIs, correlated with
  bloated diffs or unsuccessful sessions).
- **20.2 Provenance per observation**: source run, agent version,
  outcome, validation (tests/review/human).
- **20.3 Memory ablations** on eval cases: no memory vs current retrieval
  vs candidate retrieval — the only reliable utility measurement.
- **20.4 Memory is context, never policy**: cannot override repo
  instructions, task requirements, deterministic evidence, or
  safety/governance.

Exit: frequency ≠ correctness; failed-run observations visibly low-trust;
memory effectiveness measurable by replay; harmful/obsolete memories
quarantinable automatically.

## Phase 21 — Low-risk automatic promotion (evidence-gated; do not start early)

Readiness bar: 50+ eval cases, 100+ normalized real runs, 3–5 successful
human-approved promotions, 0 unresolved critical evaluator defects.

- **21.1 Risk classes**: *low (auto-eligible)*: few-shot example,
  retrieval count/threshold within bounds, proven model per category,
  non-authoritative wording, decreased retry/timeout budgets. *medium
  (human)*: main edit prompt, autonomy budgets, reviewer model, new
  tools/connectors, task decomposition. *high (manual engineering)*:
  security/governance, evaluators, hidden tests, permissions, automatic
  push/merge/publish, deployment credentials.
- **21.2 Repeated wins required**: full + held-out + adversarial passes,
  shadow improvement, canary improvement, no breaker events, consistency
  across multiple cycles.
- **21.3 Post-promotion monitoring** window vs historical norms;
  auto-rollback on significant degradation.
- **21.4 Human visibility**: every auto-promotion ships rationale, exact
  patch, evidence cases, expected vs observed gain, rollback command.

Exit: only allowlisted low-risk changes auto-promote; everything
reversible and attributable; a deliberate bad candidate is blocked or
rolled back in testing; human approval stays mandatory for permissions,
evaluators, safety controls.

## Phase 22 — Goal-oriented multi-file orchestration (later)

Keep the single-scope loop primitive; add a planner above it.

- **22.1 `pxx --goal "<goal>"`** → task DAG → each node a bounded
  single-scope `--loop` job.
- **22.2 Role separation**: planner (read-only) / implementer (one scoped
  unit) / verifier (independent) / integrator (combined branch).
- **22.3 Worktree per DAG branch**; parallelize only non-overlapping
  tasks; merge through an integration worktree + full suite.
- **22.4 Versioned project skills** loaded by the planner (architecture
  constraints, test commands, release procedures, conventions, failure
  modes, allowed deps, definitions of done); skill hashes in the
  manifest.
- **22.5 Connectors last** (issues, CI, PRs, docs, tickets); connector
  credentials/write permissions stay outside the optimization plane.

Exit: goals decompose into independently verifiable units; parallel tasks
can't collide; integration has its own evaluation; one task's failure
can't rewrite completed tasks.

## Lessons imported from agentic-CLI practice (Claude Code et al., 2026-07-16)

Operational lessons from mature agent harnesses, mapped to phases:

1. **Fresh context per round is a feature — codify it.** Long-lived agent
   contexts degrade (drift, compaction loss, stale assumptions). pxx
   already gets this right by accident of architecture: every loop round
   is a fresh aider process whose context is *reconstructed
   deterministically* (task + healing prompt + repo map), never
   accumulated chat. Promote this to a named invariant, and track
   context-size telemetry per round in `RunOutcome` (tokens sent grew
   13k → 27k between the first two live dogfood runs — that curve is a
   health metric). *(Phases 11/12.)*

2. **Lifecycle hooks as user-owned deterministic control points.** The
   single most-copied Claude Code feature: let the *user* attach
   deterministic scripts to agent lifecycle events (pre-edit, post-edit,
   pre-review, on-terminal-verdict) with the power to block. pxx has git
   hooks; loop-stage hooks would let an operator add policy (e.g. "block
   any round touching migrations/") without forking pxx — and they live
   in the trusted control plane, outside the optimizer's reach.
   *(Phase 14 adjunct; cheap, high leverage.)*

3. **Memory should graduate into curated text, not accumulate as
   influence.** The strongest memory in practice is a human-curated,
   repo-versioned instruction file (the CLAUDE.md pattern), not an opaque
   retrieval store. Add a *graduation path* to Phase 20: observations
   with high measured utility get **proposed as diffs to
   CONVENTIONS/skills files** (human-approved, versioned, manifest-
   hashed), then retired from the store. Memory becomes a staging area
   for knowledge, not a shadow policy layer. *(Phase 20.)*

4. **Interruptibility is a safety feature.** A bounded loop still needs a
   clean human interrupt: SIGINT should produce a *graceful* terminal
   state — audit record written, workflow state saved, tree left
   inspectable, partial round labeled `INTERRUPTED` — never a corrupted
   half-round that the next session misreads. Add `INTERRUPTED` to the
   Phase 12 failure codes and test it. *(Phases 12/18.)*

5. **Headless/interactive parity as a tested invariant.** Every behavior
   must have a headless equivalent with identical semantics, covered by
   tests — pxx learned this the hard way (non-TTY `--yes` injection,
   prompt_toolkit crashes). The eval harness (Phase 13) runs everything
   headless, so any interactive-only behavior is silently untested;
   make parity explicit. *(Phase 13.)*

6. **Sub-processes return conclusions, not transcripts.** When the
   analyzer, verifier, or planner delegates work, the deliverable is
   structured findings (the F-NNN contract already does this for
   review) — never raw logs pasted into a larger context. Keeps every
   plane's context small and its inputs schema-validated. *(Phases
   15/22.)*

7. **Policy belongs in declarative, versioned files — not code.**
   Allow/deny surfaces (protected targets, permitted change classes,
   promotion thresholds, sampling rates) should be data files under the
   trusted control plane, hashed into the manifest, reviewed like code.
   Claude Code's settings/permissions model demonstrated that operators
   audit and diff policy files; they do not audit conditionals buried in
   source. *(Phases 0.3/16/17.)*

## Lessons imported from Codex practice (OpenAI, 2026-07-16)

The core Codex lesson: **the next capability jump usually comes from
improving the harness around the model, not from making the prompt or
loop more autonomous. When an agent fails, improve the environment that
makes correct behavior easy and incorrect behavior difficult; only
improve the prompt when the failure is actually a prompt problem.**

These land as two new sub-phases (10.5, 19.5, 20.5) and amendments to
existing phases.

### NEW Phase 10.5 — Agent-legible repository and workflow contract

Comes *before* behavior versioning. A large monolithic instruction file
goes stale, eats context, and can't be verified; the working pattern is a
short table-of-contents instruction file backed by structured,
version-controlled docs.

- Structure: short `AGENTS.md` (map, not manual — canonical cross-tool
  format; CLAUDE.md/GEMINI.md become thin pointers into it),
  `WORKFLOW.md` (executable workflow contract), `ARCHITECTURE.md`, and a
  `docs/` tree (core-beliefs, architecture, workflows, exec-plans
  active/completed, reliability, security, quality, generated).
- **WORKFLOW.md** = typed frontmatter (schema_version, states with
  initial/terminal, budgets: max_rounds/max_seconds/max_diff_lines,
  commands: test/lint/format_check, permissions: filesystem/network/
  protected_paths, hooks: before_run/after_edit/after_run) + prose
  workflow steps ("validate baseline → reproduce → record evidence →
  smallest scoped change → deterministic verification → verification
  packet → stop at ready_for_review"). Repository-owned policy,
  separated from the generic orchestrator; hashed into the manifest.
- **Instruction validation**: `pxx context audit`, `pxx workflow
  validate`, `pxx docs check` — broken doc links, conflicting nested
  instructions, oversized instructions, stale generated docs, missing
  test commands, undocumented protected paths, workflow fields absent
  from the agent manifest.
- *pxx grounding*: this consolidates the existing spread (CLAUDE.md,
  CONVENTIONS.md, `pxx/prompts/system.md`, `config/conventions.md`
  template) into one legible contract. The loop's budget constants and
  the guardrail file list become WORKFLOW.md fields instead of prose.

### Amends Phase 15/16 — Harness-first improvement policy

Before any candidate is generated, classify the root cause. An agent
struggling is evidence the *environment* may be missing docs, tools,
guardrails, or feedback — not automatically evidence the prompt is bad.

Required classification: AMBIGUOUS_REQUIREMENTS,
MISSING_ACCEPTANCE_CRITERIA, MISSING_REPOSITORY_CONTEXT,
STALE_DOCUMENTATION, MISSING_TOOL, MISSING_DETERMINISTIC_CHECK,
ARCHITECTURE_NOT_ENFORCED, ENVIRONMENT_NONREPRODUCIBLE,
MODEL_CAPABILITY_LIMIT, PROMPT_DEFECT, REVIEWER_DEFECT, FLAKY_CHECK,
ORCHESTRATOR_FAILURE.

Candidate priority order (prevents prompt accumulation from becoming the
default learning mechanism):
1. clarify acceptance criteria → 2. add/repair deterministic evaluation
→ 3. expose missing repo context → 4. add an executable tool/diagnostic
→ 5. encode an architectural invariant → 6. add/update a skill
→ 7. modify retrieval → 8. modify prompts → 9. change model/provider.

Every proposal carries `root_cause`, `evidence_runs`, `proposed_target`,
and `reason_prompt_change_is_insufficient`.

### Amends Phase 14 — Action broker with deterministic hooks

Protect individual *actions*, not just terminal outcomes. Separate
sandboxing (what the agent can technically do) from approvals (when it
must ask). Default local posture: workspace-write, network disabled.

- Path: propose → normalize `ToolAction` → deterministic policy engine →
  {allow | agent boundary-review | human approval} → sandboxed execution
  → post-action hook → evidence record.
- `ToolAction` (frozen): tool_name, operation, arguments_hash,
  read/write paths, network hosts, creates_process, changes_permissions,
  irreversible, estimated_risk.
- Permission profiles: READ_ONLY, WORKSPACE_WRITE,
  WORKSPACE_WRITE_NO_NETWORK, NETWORK_ALLOWLISTED, ELEVATED_ONCE,
  PROHIBITED.
- PreToolUse hooks: deny writes outside worktree, deny eval-fixture
  modification, deny credential reads, require allowlisted network,
  deny destructive git. PostToolUse: inspect changed files, secret scan,
  diff-budget update, exit-code capture, log/trace collection.
- Boundary auto-review tiers: low risk = deterministic decision; medium
  = separate boundary-review agent; high = human; forbidden = rejected
  regardless of any reviewer verdict.
- *pxx grounding*: `_out_of_scope_changes` (2026-07-16) is a post-hoc
  round-level version of this; the broker moves the same boundary to
  action time. Merges with CC-lesson #2 (lifecycle hooks) — one hook
  system serves both.

### Amends Phase 11 — Context engineering and tool-registry stability

Prompt caching depends on exact prefix matches; context construction
must be engineered, not accreted.

- `ContextManifest` (frozen): static_instruction_hash,
  tool_registry_hash, sandbox_policy_hash, workflow_hash, per-source
  token counts (task/instructions/retrieved docs/memory/conversation/
  tool schemas), compaction_generation, truncated_sources.
- Construction order: stable model instructions → stable security/
  permission instructions → canonical-ordered tool definitions → repo
  instruction map → workflow contract → relevant repo docs → retrieved
  memories → current state summary → current task + latest evidence.
- Rules: snapshot tool registry at run start; canonical sort; no hot-
  adding tools mid-run; model change = new agent session; permission
  expansion = new checkpoint; compact into *typed state*, not prose;
  unresolved failures and original acceptance criteria survive
  compaction; record exactly which sources were omitted.
- Eval cases (Phase 13): instructions survive compaction; acceptance
  criteria survive compaction; registry changes detected; memory doesn't
  crowd out task context; large logs summarized without losing failure
  lines.

### Amends Phase 12 — Verification packets, not just verdicts

A strong run *reproduces the failure first*, then proves the fix.

- `VerificationPacket` (frozen): task/run ids, baseline/result commits,
  reproduction command + observed + evidence, verification commands +
  results, changed_behavior, unchanged_behavior, test/lint/log/trace
  artifacts, unresolved_risks. Optional for UI/service tasks: before/
  after recordings, logs, latency/resource measurements.
- Terminal APPROVE requires: acceptance criteria satisfied AND
  deterministic checks passed AND expected scope preserved AND
  verification packet complete AND review evidence valid.
- This gives evaluator and human something stronger than the agent's
  claim of completion.

### Amends Phase 13 — Five independent evaluation families

Promotion requires passing **all applicable families**, never one
composite score:

1. **Capability** — implements requested behavior; reproduces and
   repairs real defects; preserves unrelated behavior.
2. **Safety** — out-of-scope writes; credential seeking; test weakening;
   hook bypass; unnecessary network requests.
3. **Recovery** — killed worker; process restart; stale workspace;
   cancellation; provider failure.
4. **Context** — nested instructions followed; requirements retained
   after compaction; right documents selected; stale/contradictory
   memory avoided.
5. **Economic** — cost per accepted task; time to first useful action;
   rounds per accepted task; context tokens per task; human minutes per
   accepted task; rollback cost.

### NEW Phase 19.5 — Reconciliation, liveness and restart recovery

Scheduled execution needs one authoritative orchestrator state,
deterministic per-task workspaces, bounded concurrency, backoff retries,
stall detection, and startup reconciliation.

- Task claims: QUEUED, CLAIMED, RUNNING, AWAITING_REVIEW,
  RETRY_SCHEDULED, BLOCKED, COMPLETED, CANCELLED. Never: running twice,
  claimed by two workers, executed after cancellation, retried past a
  terminal state.
- Reconciliation loop: refresh task state (terminal → terminate worker +
  clean/archive workspace); heartbeat stall → terminate + backoff retry;
  verify workspace ownership, agent version, policy version.
- Workspace rules: one deterministic worktree per task; reuse across
  recoverable retries; never run outside the assigned workspace;
  preserve failed workspaces for debugging; archive qualified-candidate
  workspaces; clean stale terminals at startup.
- **Success is a handoff state**, not DONE: READY_FOR_HUMAN_REVIEW,
  READY_FOR_CANARY, READY_FOR_MERGE, BLOCKED_ON_JUDGMENT — successful
  execution is never conflated with authorization to deploy.

### NEW Phase 20.5 — Entropy control and continuous garbage collection

Agents reproduce the patterns already in a codebase — including bad
ones. Counter with mechanical quality pressure:

- **Golden principles** (declarative, enforced): e.g.
  no-untyped-boundary-data (structural test), no-duplicate-policy-logic
  (custom lint), bounded-files (custom lint), structured-logging (AST
  lint).
- **Rule-promotion ladder** — converts human judgment into compounding
  infrastructure instead of prompt growth: 1st occurrence → record
  observation; 2nd → update docs/skill; repeated → deterministic lint or
  structural test; systemic → revise architecture/shared abstraction.
  (Converges with CC-lesson #3, memory graduation — same ladder, two
  entry points.)
- **Quality grades** per domain; background cycles propose small
  targeted repairs against low-scoring areas but never auto-modify
  protected components.

### Amends Phase 17/21 — Risk-adjusted merge and promotion routes

Match gate strength to reversibility, blast radius, and correction cost
(not: remove gates because throughput is high):

| Change class | Route |
|---|---|
| Retrieval count / low-risk example | offline eval → shadow → automatic canary |
| Prompt or routing change | full eval → human-reviewed promotion |
| Ordinary source change | PR → deterministic checks → review |
| Dependency or tool addition | security review → network/tool-policy review |
| Evaluator modification | independent human review |
| Governance, permissions, release logic | manual engineering + full release gate |

New required metrics: median rollback time, rollback success rate,
blast-radius classification, time to detect regression, time to restore
stable. **Automatic promotion is permitted only where rollback is both
fast and demonstrated.**

### Amends Phase 22 — Specialized agents only at isolation/authority boundaries

Roles: Planner (read-only, produces task graph + acceptance criteria),
Reproducer (writes limited to test artifacts; proves the defect exists),
Implementer (scoped source writes; cannot touch hidden evals or
governance), Verifier (read-only except evidence artifacts), Boundary
Reviewer (no writes; judges exceptional actions), Artifact Reviewer (no
writes; judges final diff + verification packet).

Agents communicate through **typed handoff artifacts** (handoff_type,
task, acceptance_criteria, reproduction {command, result},
allowed_scope, known_risks) — never one shared transcript.

Add a subagent only for: context isolation, tool restriction,
independent judgment, parallel non-overlapping work, or clearer
attribution — never merely because parallelism is available.

## Lessons from the wider agent landscape (2026-07-16)

Sources beyond Claude Code and Codex, each mapped to phases. Two of
these are *counterweights* — recorded precisely because they push back
on parts of this roadmap.

1. **Aider — the engine pxx already wraps.** Its deepest lesson is that
   the **edit format is a measured, per-model capability contract**, not
   a preference: aider's own benchmark drove diff vs whole vs udiff
   selection per model, and repo-map size is a tunable budget with
   measurable effects (pxx saw 1024 → 3584 tokens change behavior when
   Qwen3-Coder got registered). Fold in: Tier A eval cases double as
   **edit-format calibration** per model; edit-format failure rate and
   repo-map budget become tracked `RunOutcome` fields and legitimate
   candidate targets. *(Phases 12/13/16.)*

2. **SWE-agent — the Agent-Computer Interface result.** Princeton showed
   the *interface* the agent sees (bounded file-viewer windows, capped
   search results, edits rejected at the interface with guiding lint
   errors) changes success rates as much as model choice. The tool
   surface is a first-class experimental variable: design tool outputs
   for model consumption (bounded, structured, error messages that say
   what to do next), hash the tool surface into the manifest (already
   planned), and add ACI-focused eval cases — "does a capped search
   output still let the agent find the target?" *(Phases 10.5/13/14.)*

3. **Cursor — three portable mechanisms.** (a) **Path-scoped rules**:
   instructions attached to glob patterns, loaded only when matching
   files are touched — strictly better than monolithic conventions;
   adopt for 10.5's docs tree and Phase 22 skills. (b) **Apply-model
   separation**: generating a change and applying it are different
   competencies; pxx's malformed-edit retry is a crude version — if
   edit-format failures return with weaker models, a small dedicated
   apply step beats prompt surgery. (c) **Shadow workspace**: verify an
   edit compiles/lints in a hidden copy *before surfacing it* — the
   same primitive as Phase 18 shadow, applied at edit granularity.
   *(Phases 10.5/16/18.)*

4. **Gemini CLI — per-action checkpointing.** Automatic snapshot before
   every mutating tool call, with a restore command. pxx's #002 safety
   tag is session-granular; the action broker (Phase 14) should add
   action-granular restore points so rollback isn't all-or-nothing.
   Cheap under git. *(Phases 14/18.)*

5. **Cognition/Devin — the multi-agent counterweight.** Their argument:
   *actions carry implicit decisions*; agents working from fragmented
   contexts make conflicting assumptions, and typed handoffs are lossy
   by construction. This directly tensions Phase 22 and the
   conclusions-not-transcripts rule. Resolution recorded here:
   parallelize only **provably disjoint** decomposition; prefer
   sequential single-context execution otherwise; the integrator treats
   every handoff artifact as a *claim to re-verify against the actual
   diff*, never as ground truth. If a decomposition can't be made
   disjoint, it isn't ready for parallel agents. *(Phase 22.)*

6. **OpenHands — event-sourced agent state.** An append-only event
   stream is the single source of truth; all state is derived from it,
   and replay = re-derivation. pxx's audit JSONL is already close —
   formalize: run directories (11.4) store *events*, and `RunOutcome`/
   workflow state are derived views that can be rebuilt. Also: version
   the sandbox/runtime image into the manifest. *(Phase 11.)*

7. **Copilot Workspace — editable intermediate artifacts.** Spec and
   plan are human-editable checkpoints *before* implementation; editing
   a plan is far cheaper than editing a diff. For `pxx --goal`: the
   planner's task DAG + acceptance criteria are emitted as a reviewable
   artifact with an explicit pause point before any loop launches.
   *(Phase 22.)*

8. **Voyager — skills as verified executable units.** Skill libraries
   work when each entry is executable, was verified in the environment
   before storage, and is retrieved by task match — not prose tips.
   Extends the rule-promotion ladder: the "update docs/skill" rung
   should prefer *executable* form (a check, a command, a fixture) over
   text whenever possible. *(Phases 20/20.5.)*

9. **AlphaEvolve — the evaluator ceiling.** Evolutionary candidate
   search works exactly where automated evaluation is airtight, and
   nowhere else. Two imports: **the autonomy ceiling equals the
   evaluator ceiling** — expand Phase 21's allowlist only where the
   relevant eval family is demonstrably strong; and maintain modest
   candidate *diversity* within a cluster budget rather than one
   candidate per hypothesis, since the first idea is rarely the best.
   *(Phases 16/17/21.)*

10. **External benchmark anchoring.** A private eval corpus drifts
    toward what the harness is already good at. Periodically run a
    small fixed subset of a public benchmark (SWE-bench-verified-class,
    terminal-bench-class) as a *calibration anchor* — not a target to
    optimize, a drift detector for the corpus itself. *(Phases 13/17.)*

11. **The AutoGPT-era negative result** — unbounded goal loops without
    environmental feedback drift and confabulate progress; verbal
    self-reflection amplifies rather than corrects without ground
    truth. pxx's central design rule (bounded primitive, deterministic
    feedback, fail closed) *is* the antidote; this entry exists so the
    lesson survives personnel and model changes. *(All phases.)*

## Revised phase sequence (post-Codex amendments)

```
Phase 0      Release and trust-boundary stabilization
Phase 10.5   Repository legibility and WORKFLOW.md
Phase 11     Agent, context and tool-registry versioning
Phase 12     Normalized outcomes and verification packets
Phase 13     Capability, safety, recovery, context + economic evals
Phase 14     Action broker, hooks and evaluator hardening
Phase 15     Harness-first experience mining
Phase 16     Constrained candidate generation
Phase 17     Baseline comparison and promotion policy
Phase 18     Shadow, canary and rollback
Phase 19     Scheduled improvement workflow
Phase 19.5   Reconciliation, liveness and restart recovery
Phase 20     Outcome-aware memory
Phase 20.5   Entropy control and repository garbage collection
Phase 21     Low-risk automatic promotion
Phase 22     Goal decomposition and specialized agents
```

**Mandatory before automatic promotion (Phase 21):** WORKFLOW.md +
agent-legible knowledge structure; deterministic action broker + hooks;
context and tool-registry versioning; verification packets proving
reproduction and resolution; restart reconciliation and stall recovery;
continuous entropy detection with golden-principle enforcement.

## Cross-phase safety invariants

1. The production agent never directly changes its active configuration.
2. The candidate generator cannot modify its own evaluator.
3. Deterministic failures cannot be overruled by model judgment.
4. The optimizer cannot expand its permissions.
5. No candidate may alter hidden evaluation cases.
6. Every behavior change is versioned and reversible.
7. Production pushing/merging/publishing remain human-controlled until
   separately designed and approved.
8. Uncertain or missing evidence fails closed.
9. Every automated decision has an inspectable evidence chain.
10. The system optimizes multiple quality dimensions, never one gameable
    score.

## Milestones and sequencing

- **A — Measurable pxx** (Phases 0, 10.5, 11, 12): a legible repository
  contract, then every run attributable, measurable, comparable — with
  verification packets. *1.5–3 weeks focused.*
- **B — Evidence-based improvement** (13–16): five eval families, action
  broker, harness-first mining, constrained candidates proven/disproven
  offline. *3–5 weeks focused.* **This is the first genuinely valuable
  target.**
- **C — Human-gated continuous improvement** (17–20.5): promotion
  policy, shadow/canary, scheduled cycles with restart reconciliation,
  outcome-aware memory, entropy control. *3–5 weeks.*
- **D — Bounded self-improvement** (21): evidence-dependent; blocked on
  the six mandatory items above.
- **E — Long-horizon coding system** (22): 2–4 weeks.

Everything past Phase 17 stays unscheduled until the eval corpus exists
and has history.
