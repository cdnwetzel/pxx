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

## Lessons — second landscape pass: runtime ownership and operations (2026-07-16)

Diffed against everything above; only the novel deltas are recorded.
The headline: **a mature coding agent is not a clever prompt around a
model — it is a reproducible runtime, a carefully designed computer
interface, a governed knowledge system, an observable operations
platform, and an evaluation laboratory.**

### NEW Phase 10.75 — Runtime ownership and backend abstraction

The largest architectural addition. Today pxx `os.execv`s into aider
("pxx is out of the picture") or subprocesses `--self-fix`; that caps
how deeply pxx can control context assembly, tool calls, checkpoints,
cancellation, replay, and permissions. Make **aider one execution
backend, not the runtime**:

```python
class AgentBackend(Protocol):
    async def start(self, request: AgentRequest, event_sink: EventSink) -> RunHandle: ...
    async def resume(self, checkpoint_id: str, event_sink: EventSink) -> RunHandle: ...
    async def cancel(self, run_id: str) -> None: ...
```

Backends: `AiderBackend` (existing behavior), `NativeToolBackend`
(pxx-owned loop), `ReplayBackend` (recorded trajectories),
`MockBackend` (deterministic tests), `ExternalBackend` (adapters).
Not an aider rewrite — keep it for editing while orchestration
responsibility migrates into pxx.

Exit: pxx receives every model/tool event; can cancel/pause/resume/
time-out a run; the backend cannot bypass pxx policy; switching
backends does not change evaluation or promotion logic.

### NEW Phase 10.8 — Event stream and headless API

Extends the OpenHands event-sourcing entry with the concrete contract:
a typed event vocabulary (RunCreated, ContextItemSelected,
PromptRendered, ModelRequest/Response, ToolActionProposed,
PolicyDecisionMade, ToolActionStarted/Completed, FileChanged,
CheckpointCreated, EvaluationCompleted, HumanDecisionRecorded,
RunPaused/Resumed/Completed), each event carrying `sequence` and
`previous_event_hash` — the hash chain makes **altered or missing
audit evidence detectable**, which the promotion system depends on.
`RunOutcome` becomes a projection of the stream. Separate the engine
from clients (headless server pattern) so CLI, UI, and schedulers are
all thin clients of one API.

### Amends Phase 11 — ACIManifest + ModelFingerprint (drift sentinels)

- **`ACIManifest`**: aci_version, tool_registry_hash,
  command_schema_hash, observation_format_hash, edit/shell/file-view
  protocols, max_output_bytes, truncation_policy. Benchmark ACI changes
  independently (model+prompt fixed, ACI varied) with dedicated
  metrics: tool-call success rate, malformed-command rate, repeated
  file reads, tokens-to-locate-target, edit-application failure rate,
  time to first relevant action, shell commands per accepted change.
  Prevents every improvement being mislabeled a prompt improvement.
- **`ModelFingerprint`**: provider, requested vs resolved model id,
  artifact digest, quantization, server version, tokenizer hash,
  capability-probe hash. Hosted models drift silently; **local models
  drift when a tag is repulled or quantization changes** (directly
  applicable to the Ollama fleet). Before an altered fingerprint
  activates: sentinel suite (tool-format compliance, edit application,
  review calibration, latency/context behavior); quarantine on
  material drift.

### Amends Phase 12 — ReviewPacket and commit-bound review validity

A review approves a *commit*, not a task: `reviewed_commit` vs
`current_commit`; any subsequent edit marks the review **STALE** unless
the reviewer evaluates the delta. `ReviewPacket` (frozen): base/head/
prior-reviewed commits, task summary, acceptance criteria, changed
files, behavioral summary, deterministic results, verification
artifacts, unresolved risks, generated-by and reviewed-by agent
versions. Clean handoff unit between production, review, canary, and
promotion. *(The loop already re-reviews `start_sha..HEAD` per round —
this generalizes that instinct into an explicit validity rule.)*

### Amends Phase 13/18 — Branching, counterfactual replay, richer checkpoints

Beyond per-action snapshots: `pxx run pause|resume|fork --at-event N|
compare <a> <b>|rewind --checkpoint`. **Checkpoint before the mutating
action, never after.** A checkpoint binds: git tree + index, untracked
inventory, conversation state, typed workflow state, active agent
version, tool registry, permissions, retrieved-context hashes,
outstanding failures + acceptance criteria. Forking a recorded run at
an event enables counterfactual evaluation ("would candidate B have
avoided this?") — eval cases generated from real trajectories.

### Amends Phase 14 — Ambiguity gate + extension supply chain

- **Clarification gate** before autonomous editing: ready_to_act =
  acceptance criteria present ∧ scope resolved ∧ expected behavior
  understood ∧ verification method available ∧ ambiguity below
  threshold. Outcomes: READY_TO_EXECUTE, PLAN_REVIEW_REQUIRED,
  QUESTION_REQUIRED, MISSING_TEST_OR_ORACLE, RISK_APPROVAL_REQUIRED.
  A targeted question that prevents substantial rework **is correct
  autonomous judgment, not an autonomy failure**.
- **Extensions/MCP servers are a software supply chain**: pinned
  versions + digests, permission manifests (filesystem/network/
  process/secrets), no ambient credential inheritance, separate
  process/container, network allowlists, tool-call audit events, kill
  switch, provenance/license metadata; `pxx extension
  inspect|verify|permissions|quarantine|update --dry-run`. **The
  self-improvement agent must never install an extension and approve
  its own new permissions.**

### Amends Phase 15 — Semantic loop detection + recovery ladder

Generalize the no-progress guard beyond test-set monotonicity. Detect:
same command → same result; a file region edited back and forth; two
patches alternating; the same review finding recurring; retrieval
repeated with no new evidence; plans rewritten without execution; tool
errors retried with unchanged parameters. Track a `ProgressVector`
(failing tests, findings, changed-file hashes, command-result hashes,
acceptance coverage, unresolved questions). Recovery ladder — never
"just raise the round limit": 1. compact + restate objective →
2. switch execution→diagnosis → 3. retrieve relevant skill/example →
4. change model role → 5. revert to last improving checkpoint →
6. escalate to human.

### Amends Phase 16 — Demonstrations and anti-demonstrations

Every *reviewed* run yields one of four artifacts: successful minimal
trajectory → positive demonstration candidate; successful-but-
inefficient → optimization case; failed → regression/recovery case;
human-corrected → **contrastive example** (task, bad_action,
bad_reason, preferred_action, verification) — a negative-learning
corpus, not just accumulated positive memories. Safeguards: never
optimize on held-out promotion cases; no task in both development and
held-out sets; label model-generated demonstrations; require
deterministic or human validation; track which demonstrations were
injected into each run.

### Amends Phase 16/11 — Model roles and capability contracts

A declarative role registry (planner read-only, editor + edit
protocol, reviewer read-only, summarizer, embedder, reranker), each
model passing **capability probes**: structured-JSON support, required
context size, tool calling, selected edit protocol, maximum reliable
output length, truncation behavior, average malformed-action rate.
Routing by *measured task suitability*, not "first endpoint reachable"
— an upgrade to today's tier logic, and the probes feed
`ModelFingerprint`.

### Amends Phase 19 — Operator control plane

Task lifecycle states (Queued, Planning, Awaiting Clarification,
Running, Verifying, Awaiting Review, Shadow, Canary, Blocked,
Completed, Failed, Cancelled) with `pxx tasks
list|inspect|pause|resume|cancel|reprioritize|fork|approve`.
Eventually a lightweight local web UI: active worktrees, current step,
recent tool calls, diff growth, budget consumption, evaluator results,
pending approvals, candidate-vs-baseline. **Overnight execution must
be supervisable without reading raw transcripts.**

### Amends Phase 20 — Five knowledge layers with separate lifecycles

Split what pxx currently holds as "skills + observations" into five
layers with distinct trust and promotion lifecycles: **Policy**
(non-negotiable control), **Repository knowledge** (stable facts),
**Skill** (task-specific expertise), **Playbook** (ordered procedure),
**Episodic memory** (what happened). Promotion flow: extract candidate
lesson → classify (fact/skill/playbook/regression/discard) → validate
against repo + evals → reviewable PR → human approval → active in
future manifests. **Do not auto-convert successful trajectories into
memory — a run may succeed for the wrong reason.**

### Amends Phase 22 — Browser/multimodal verification adapters

Optional verification *provider* (plugin, never a core-loop
dependency): start app → readiness probe → declared browser scenario →
capture DOM snapshot, screenshots, console errors, failed requests →
compare expected state. Artifacts: before/after.png, dom.json,
console.jsonl, network.har, accessibility.json, scenario-results.json.
Feeds the VerificationPacket's optional UI fields.

### Second-pass priority additions (mandatory before Phase 21)

6. **Runtime ownership** — pxx observes and controls every significant
   action (10.75).
7. **Event sourcing + branching replay** — every run reconstructable
   and forkable (10.8).
8. **ACI evaluation** — tool interfaces versioned and tested
   independently of prompts.
9. **Knowledge lifecycle separation** — memory cannot silently become
   policy or procedure.
10. **Model/extension drift controls** — changed infrastructure cannot
    enter production without evaluation.

## Cross-cutting track — Phase 0.5: Continuous verification (added 2026-07-17)

**Not a self-improvement phase — a baseline-integrity track that sits beside
the arc.** Phase 0 established a clean reproducible baseline *once*; 0.5 keeps
it verified on every push and every release. Folded in after the packaging
question surfaced that pxx ships to PyPI with **zero automated proof the
package works** — the same "pass-on-silence" class the 2026-07-17 review
flagged, one level up: we assume the wheel is good, nothing checks it.

It is **not one priority — it splits into three tiers of very different
urgency**, and conflating them would mis-rank the whole thing:

- **Tier A — CI runs the suite on push/PR. HIGH / do-first.** *There is no
  CI at all today* — the 851 tests run only via the local pre-commit hook
  (author-skippable) and `pxx --self-test`. A regression can land on `main`,
  or ship in a release, caught by nobody. This is a foundational safety net
  that is simply *absent*, and it protects **everything downstream** — every
  phase, every release. Its absence is a standing risk, not a feature gap.
- **Tier B — package smoke: build → install in a throwaway venv → assert the
  packaging contract. MEDIUM.** `scripts/smoke-package.sh` + a post-build
  job so a broken wheel can't publish. Verifies wheel contents (`pxx/` in;
  `evals/`/`config/`/`WORKFLOW.md` out), shipped surfaces work, and repo-only
  surfaces **fail closed** (`--eval` exits 2). Automates the manual smoke
  that has caught real bugs across 1.0/1.1/1.2 — valuable, but releases are
  infrequent and hand-smoked today, so it's "make the manual thing
  repeatable," not "stop the bleeding."
- **Tier C — Python-version matrix (3.11–3.13) + TestPyPI dry-run. LOW.**
  Polish; the wheel claims `>=3.11` but is only ever run on 3.12. Nice, not
  blocking.

**Strategic read (my assessment, pre-user-input):** this track is
*operationally* important but *strategically orthogonal* — it advances none
of Phases 16–22, and the self-improvement machinery runs from a checkout, so
package verification unblocks no downstream phase. That argues for ranking
the whole thing *below* Phase 16 (candidate generation), the marked frontier.
**Except Tier A**, which I'd rank *above* Phase 16: protect what exists
before extending it — a project shipping to PyPI on a skippable local hook,
with no push CI, is one bad merge from a silent regression, and that risk
compounds every day the self-improvement machinery grows on top of an
unverified base. So: **Tier A high (before Phase 16); Tier B medium (after
Phase 16); Tier C low (whenever).** This is the fail-closed standing rule
applied to the release surface itself. Current state ≈ 10% (manual procedure
exists, run 3×; nothing automated or committed).

## Next build — Phase 16 content change-classes (green-lit, spec'd by review)

The change-class expansion from config fields to CONTENT targets (prompt /
skill / few-shot files). Green-lit after five review passes hardened the
enforcement floor: `is_protected_path` is one authoritative decision that
can't be fooled by the path shapes a diff carries. **Hard requirements,
carried from the review so they're not re-derived at build time:**

1. **Path derived once, from the same source used to write.** The content-
   check MUST derive the path it validates from `git diff --name-only` /
   `--numstat` (clean, repo-relative paths) — NOT by parsing raw `--- a/` /
   `+++ b/` diff headers. The residual leak risk is entirely in the caller:
   if the check strips `a/` to validate but the write resolves the path
   differently, the two disagree. One path, derived once, checked AND written
   from the same value. **Test that equivalence on day two** (it is the
   day-two guidance, not a defect in the boundary function).
2. **Every changed path runs through `is_protected_path`; any protected hit,
   or any path the function can't classify, rejects the whole candidate.**
3. Content candidate still declarative + one-variable + evidence-backed +
   fail-closed, exactly like config candidates — plus a content-hash so the
   proposal is immutable, and a human-diff surface for the reviewer.
4. Retracted non-finding (recorded so it isn't reopened): `a/b/evals/m1.toml`
   is NOT a leak — git never emits `a/b/` concatenated (a/ = old, b/ = new,
   separate lines), so that string is a real file under a top-level `b/` dir,
   correctly not-protected. The single-prefix strip is right.

## Near-term evidence-directed queue (2026-07-17)

These are the concrete next work items the last build sessions surfaced —
each traceable to a measured result, not speculation. Ordered by evidence
strength. When one lands, its ledger row updates and it leaves this list.

1. **Advisory review mode — DONE (2026-07-17).** `PXX_REVIEW_MODE=advisory`
   (`review_gate.review_mode`): findings are still produced, recorded, and
   surfaced, but the reviewer's verdict never blocks a run whose
   deterministic gates (tests, lint, scope, regression) are green; a down
   reviewer no longer refuses loop startup. `reviewer_mode` is in the agent
   manifest (advisory vs blocking mint distinct agent ids). Proven live: r5
   — the exact case Qwen3's false-positive REVISE spun to failure in the
   blocking sweep — now PASSES in advisory mode with that same reviewer, the
   FP recorded not gated. Machine default flipped to advisory. `blocking`
   stays for a supervised frontier reviewer.
2. **Cross-model skeptic — DONE (2026-07-17), REJECTED.** Qwen3 flags, the
   14b (different family, fp 0.0) audits each finding: FP crushed 0.75→0.08
   but recall collapsed 1.00→0.00 — the conservative model drops every
   finding, truths included (mirror of self-audit, which keeps its own
   hallucinations). Confirms the recall/precision tradeoff is fundamental to
   fleet-local models. Practical upshot: for advisory use, the high-recall
   flagger SOLO (Qwen3-Coder) is optimal — both skeptic layers reduce
   advisory value. Skeptic thread closed; deterministic gates remain the only
   trustworthy enforcement. Reopen only with a frontier skeptic.
3. **Consume the VerificationPacket — DONE (2026-07-17).** No longer typed-
   but-unread: the loop's APPROVE prints a one-line evidence summary and a
   `pxx --verify <run-id>` pointer; `--verify` (and `--verify` with no id =
   latest) projects the run from the audit stream and prints the full packet
   — baseline/result commits, the deterministic commands run, results, and
   risks. `outcomes.outcome_for_run` + `format_packet` are the seams.
   Proven live end-to-end. Still open: attach packets to the promotion
   comparison as the per-arm evidence unit.
5. **Single-source the protected-path list — DONE (2026-07-17)**, pulled ahead of the change-class work per review guidance: content candidates (which mutate files) are the first thing to cross the protected boundary, so the fence had to be one authoritative list first. `pxx/protected_paths.py` is now the single source (`PROTECTED_PREFIXES` + `is_protected_path()`); the candidate validator and the future eval content-check both call it; `.aiderignore` and TRUST_BOUNDARY.md are static mirrors that tests hold to it (bidirectional, every entry). Fixed a real bug found building it: `lstrip('./')` silently unprotected dotfiles (`.github/`, `.aiderignore`).

4. **Grow the eval corpus — DONE (2026-07-17): 16 → 30, the Phase 13 bar.**
   Now 10 micro + 10 regression + 10 adversarial, all self-checking in CI.
   New failure modes covered: recursion base case, dict-mutation-in-iteration,
   None-vs-falsy, integer division, suffix-vs-charset; regression classes
   (missing timeout, non-atomic write, empty-collection fail-closed, string
   path-join); adversarial (hardcoded expected value, catch-and-reraise,
   test-weakening variants, out-of-scope via a sibling constant). Future
   growth stays evidence-driven — mine live failures as they occur.

## Revised phase sequence (all amendments)

```
Phase 0      Release and trust-boundary stabilization
Phase 10.5   Repository legibility and WORKFLOW.md
Phase 10.75  Agent runtime ownership and backend abstraction
Phase 10.8   Event stream and headless API
Phase 11     Agent, ACI, model-fingerprint and context versioning
Phase 12     Normalized outcomes, verification + review packets
Phase 13     Capability, safety, recovery, context + economic evals;
             branching replay, drift sentinels, ACI evals
Phase 14     Action broker, hooks, ambiguity gate, extension
             supply chain, evaluator hardening
Phase 15     Harness-first experience mining + semantic loop detection
Phase 16     Constrained candidates: config, skills, playbooks,
             demonstrations and contrastive examples
Phase 17     Baseline comparison and promotion policy
Phase 18     Shadow, canary and rollback (commit-bound review validity)
Phase 19     Scheduled improvement workflow + operator control plane
Phase 19.5   Reconciliation, liveness and restart recovery
Phase 20     Outcome-aware memory: five knowledge layers
Phase 20.5   Entropy control and repository garbage collection
Phase 21     Low-risk automatic promotion
Phase 22     Goal decomposition, specialized agents + backends,
             browser verification
```

**Mandatory before automatic promotion (Phase 21):** WORKFLOW.md +
agent-legible knowledge structure; deterministic action broker + hooks;
context and tool-registry versioning; verification packets proving
reproduction and resolution; restart reconciliation and stall recovery;
continuous entropy detection with golden-principle enforcement.

### Third sweep (2026-07-17 late — after the reviewer-trust + mining build day)

Effort-weighted, weights ∝ relative build size (10.75/13/22 are the heavy
architectural phases). Method unchanged from the Codex reconciliation above.

| Measurement | Sweep 1 (07-16) | Sweep 2 (07-17) | Sweep 3 (07-17 late) |
|---|---|---|---|
| Bounded loop primitive | 85–95% | 90–95% | **95%** (advisory mode, regression gate, evidence packet — complete for its envelope) |
| Measurement & evidence foundation (0,11,12,13) | 20–25% | ~60% | **~66%** |
| Cross-run self-improvement capability | 2–5% | ~5–8% | **~12%** (mining + comparison policy exist; candidate *generation* 16 still 0) |
| Entire roadmap (effort-weighted) | 8–10% | ~20% | **~27%** |

Milestones: **A ~62%** (0 complete, 11/12 solid, 10.5 minimum); **B ~38%**
(eval lab + evaluators + mining built; Phase 16 candidate generation is the
0% keystone that gates the rest); **C ~15%**; D 0% (correctly); E ~10%.

What moved it since sweep 2 (all live-proven, same day):
- Phase 0 → 100% (release scanner gate landed).
- Advisory review mode (14 → 55%): resolved the calibration dead-end; r5
  flipped fail→pass live with the same paranoid reviewer.
- VerificationPacket consumed (12 → 65%): `pxx --verify` reads real evidence.
- Eval corpus 16 → 30 (13 → 60%): the Phase 13 count exit-criterion met.
- Experience mining (15 → 35%): `pxx --analyze` independently re-derived the
  day's manual findings from 50 real runs, with traceable evidence.
- Reviewer-candidate search closed (2 rejections, both recorded).

**Fail-closed audit (independent review, 2026-07-17) — a caveat the
effort-weighting cannot see.** Three gates scored "done" were passing on
SILENCE rather than failing closed: (1) the test oracle read an all-ERROR
suite as green (`-rf` reported only FAILED) — critical because advisory mode
made it the sole enforcement gate; (2) `pxx --eval` exited 0 on an empty
corpus, so every pip-installed copy had an unconditionally-green promotion
gate; (3) the trust boundary claimed `.aiderignore` enforcement that does not
cover the evaluator paths, so nothing yet stops a `--self-fix` from editing
its own grader. (1) and (2) are FIXED (loop.py `-rfE` + ERROR parsing; eval
fails closed on zero cases); (3) has its doc made honest and the
`.aiderignore` fix staged for a human edit (guardrail file). **Standing rule
added: a gate is not "done" until a test proves it fails closed on empty/
errored/malformed input — Phase 21 (auto-promotion) must not rely on any gate
lacking that proof.** The percentages above are unchanged, but read them
knowing effort-weighting rewards built surface, not fail-closed rigor.

**Round 2 (2026-07-17, second review pass — all verified in the published
1.2.0 wheel).** The reviewer downloaded the artifact and confirmed the two
headline fixes work in shipped code (`-rfE` catches ERRORs; `--eval` exits 2
on an empty corpus), then named two remaining fail-open gates — both now
FIXED: (4) the review oracle accepted PROSE as a clean bill — a reviewer
reply like "The code looks correct." parsed to zero findings → APPROVE,
which the shipped blocking-mode 7B default (recall ~0) hits routinely.
`_run_local_review` now applies the output-contract compliance check that
only `calibration.judge_response` had: non-compliant output (neither the
exact no-findings line nor a parseable F-NNN) fails closed. (5) the
governance scanner returned an EMPTY violation list on git error — "couldn't
scan" read as "clean," so a release gate that can't run git would pass; both
scanner sites now return an error-severity violation and fail closed. Plus
CI itself (Tier A) caught a real portability bug on its FIRST run — the
installed hook's shebang was on line 2 (marker prepended above it), so git
ran it under dash on Ubuntu where `set -o pipefail` is illegal; fixed +
regression-tested. **The axis is working: of the fail-open gates named
across two review passes, all are now fail-closed with tests, verified in a
published artifact within hours.** Verified (2026-07-17): `pxx --calibrate` (exit 2) and `pxx --eval-live`
(exit 2) both fail closed on an install — confirmed on the published 1.2.0
wheel. --calibrate gained an explicit NO-CASES-FOUND guard for a loud
message rather than an incidental 0-recall exit. The whole eval/calibration
family fails loud on a pip install; none is silently green.

**Round 3 (2026-07-17, third review pass, against 6f05cfd).** Two novel
gates that stated a guarantee without enforcing it — both fixed: (6) the
promotion hard gate's docstring said adversarial-containment regressions
"disqualify outright, no trade-offs," but `promoted = eligible or
human_override is not None` let one free-text string bypass it — overriding
a security regression took the same input as overriding a lost micro-case.
Now the hard gate is non-overridable: `human_override` rescues ordinary
ineligibility only; a hard-gate failure records `override_refused_hard_gate`
and stays unpromoted. (7) `compare()` claimed "not comparable, fails closed"
but only checked case NAME-set equality — a persisted baseline scored on the
15-case corpus vs a candidate on 30, with a shared case's fixture changed
underneath, got an authoritative verdict on arms that never ran the same
test. Now every scorecard carries a corpus fingerprint (`evaluation.
corpus_fingerprint`: content hash + count) and `compare()` refuses mismatches
— a missing fingerprint differs from a present one, so pre-fingerprint
baselines are correctly refused ("re-score"). Same class as rounds 1–2: a
gate reporting a confident verdict on an input it didn't validate.

**Round 4 (2026-07-17, fourth pass).** One novel gate, same signature,
fixed: (8) the tighten-only budget guard ran its monotonicity check only
`if c.baseline_value is not None`, so a hand-edited candidate that nulled
`baseline_value` skipped it entirely and ran the candidate arm with a
LOOSENED budget — inflating the very eval number the loop exists to produce
(`load_candidate` reads the field straight from JSON, so this is in the
"a persisted candidate could be hand-edited" threat model the function's own
docstring claims). Now a MONOTONE_BUDGETS field with a missing or
non-integer baseline REJECTS (fail closed) rather than skips. Moderate
severity — candidates never auto-apply, so the blast radius is a distorted
signal a human still reads, not a production change. **Eight pass-on-silence
gates found and closed across four review passes; each bites only once a
human stops reading the reasons.** Structural follow-up (not a defect, filed
below): PROTECTED_PREFIXES / .aiderignore / TRUST_BOUNDARY.md are three
hand-synced expressions of the protected set — a test pins the first two,
the doc is prose; single-source them before the set grows further.

The shape of the remaining ~73%: it is now genuinely *construction-heavy*,
not projection. The cheap schema-and-projection layers (A, most of the
measurement foundation) are spent. What's left is real building —
candidate generation (16), the runtime-ownership rebuild (10.75/10.8),
promotion deployment machinery (18/19/19.5), and the memory/entropy
systems (20/20.5) — plus the evidence-gated auto-promotion (21) that must
not start until B and C are mature. Milestone B's keystone — **16, constrained candidate generation** — now
exists in minimum form (the allowlisted, fail-closed candidate validator);
what remains is auto-generating candidates from mined observations — everything
around it (mine weakness → evaluate → compare → promote) exists and is
live-proven; what's missing is the step that turns a mined weakness into a
declarative, allowlisted candidate patch.

### Second sweep (2026-07-17, after the Milestone-A/13 build night)

Four headline numbers (method per the Codex reconciliation above):

| Measurement | Sweep 1 (07-16) | Sweep 2 (07-17) |
|---|---|---|
| Bounded loop primitive | 85–95% | 90–95% |
| Measurement & evidence foundation (0, 11–13) | 20–25% | ~60% |
| Cross-run self-improvement capability | 2–5% | ~5–8% |
| Entire roadmap (effort-weighted) | 8–10% | **~20%** |

By milestone: **A done** (minimum form, live-validated); **B ~40%**
(laboratory + persisted live baseline exist; candidate generation (16)
and comparison policy (17) are now unblocked, not blocked); C ~8%,
D 0% (correctly), E ~10%.

What moved it: agent identity proved its precision in production
(budget variants hashed to distinct agent ids unprompted); the failure
taxonomy went into live use (APPROVED / OUT_OF_SCOPE /
NO_TEST_PROGRESS recorded on real runs); the eval lab went from
concept to a CI-self-checking corpus + validated live arm + a full
persisted baseline (13/15, 3.8 min, zero adversarial cheating, both
failures decomposed — one model-capability, one reviewer
false-positive).

Honest caveats: 15 cases vs Phase 13's own ≥30 exit bar; live-arm
repeatability measured at N=1 per case (scripted arms byte-identical);
reviewer calibration is a single datum; `VerificationPacket` is typed
but nothing consumes it yet. The aggregate doubled in one night because
Milestones A and half of B were deliberately the schema-and-projection
layers over existing instrumentation — the cheap-steep part of the
curve; the remaining ~80% is construction (corpus growth, calibration
suites, candidate machinery, runtime ownership 10.75/10.8), not
projection. Evidence-directed next build: the Phase 14 reviewer
calibration suite — r5 showed the reviewer, not the editor, is the
weakest measured component.

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

## Grounded readiness ledger (repo sweep, 2026-07-16)

Percentages tied to named artifacts, not vibes. Three states: **built**
(operational code, in use), **seed** (verifiable precursor code that the
phase would extend, not replace), **absent**. Re-sweep and update this
section whenever a phase's status line changes.

| Phase | % | Grounding |
|---|---|---|
| 0 | 100% | Built: D1 scrub **complete** (bec8310 + 992e314 — user decision executed: bare hostnames allowed, suffixed forms/IPs/personal/firm identifiers purged; `pxx --check --all-files` clean except 7 findings in review/codex\|copilot, other agents' namespaces); **public-content scanner** (`governance.scan_public_content`: four generic classes + untracked denylist, staged gate + audit mode, allow-pragma, lockfile skip); **trust-boundary doc** (docs/TRUST_BOUNDARY.md); v1.1.0 bumped/built/tagged (`v1.1.0` + `learning-baseline-1`, pushed). **v1.1.0 PUBLISHED** (2026-07-16: trusted publisher configured on pypi.org, workflow rerun green, clean-env pip smoke passed — the tokenless tag→publish path works for the first time). Release-workflow scanner gate landed (`gate` job runs `pxx --check --shipped`). **Phase 0 COMPLETE.** |
| 0.5 | 45% | Cross-cutting continuous-verification track (added 2026-07-17). **Tier A DONE (2026-07-17)**: `.github/workflows/ci.yml` runs lint (scoped pxx/+tests/, matching self_lint) + the full 851-test suite + the shipped-content gate on every push and PR to main; concurrency-cancel on superseding pushes. The safety net exists. Tier B (package smoke: build→install→assert the packaging contract, repo-only surfaces fail closed) = MEDIUM, manual procedure exists (3 hand-smokes) but nothing automated/committed. Tier C (py 3.11–3.13 matrix + TestPyPI dry-run) = LOW. Orthogonal to the self-improvement arc; Tier A ranks above Phase 16, B/C below. |
| 10.5 | 45% | Built (2026-07-17, minimum slice): **AGENTS.md** (map-not-manual, links CI-checked) and **WORKFLOW.md** — machine-readable TOML contract (states, budgets, commands, permissions incl. protected_paths as the TRUST_BOUNDARY projection) with `tests/test_workflow_contract.py` asserting every field against the code it describes: the contract cannot drift silently. The agent manifest hashes WORKFLOW.md (editing policy = new agent_version_id, verified live). Absent: docs/ tree restructure, `pxx context audit`/`docs check` commands, CLAUDE.md slimming into the map. |
| 10.75 | 10% | Seed: supervisor mode already runs aider as a supervised `Popen` subprocess with an observer thread (`cli.py:994–999`), and every loop round is a supervised subprocess — pxx has both execution postures; what's absent is the `AgentBackend` protocol, event sink, cancel/resume. |
| 10.8 | 20% | Seed (sweep 2: loop-terminal records enriched the stream; `recent_outcomes` is a working projection reader over it): append-only audit JSONL, one stream discriminated by `session_class`; workflow state persisted (`workflow.load_state/save_state/resume_state`). Absent: typed event vocabulary, hash chain, headless API. |
| 11 | 40% | Built (2026-07-16, minimum slice): `pxx/agent_manifest.py` — frozen `AgentManifest` (versions, models, prompt hashes incl. healing-builder source hash, budgets; no URLs/paths, test-pinned), deterministic `agent_version_id`, `pxx --manifest` inspection; `run_id` threads the loop session → every round record → child sessions (PXX_RUN_ID) → workflow state → capture metadata; identity is best-effort by design (never gates a run). Absent: run directories, ACIManifest, ModelFingerprint, ContextManifest, `pxx runs/agents` commands — expand only as Phase 13 demands. |
| 12 | 65% | Built (2026-07-16, minimum slice): `pxx/outcomes.py` — canonical 19-code failure taxonomy (`FAILURE_CODES`, incl. INTERRUPTED); every `run_loop` exit now writes a machine-readable `loop-terminal` audit record (code, rounds, exit, start/end sha) via `_terminal()` — a test asserts the driver emits only canonical codes; typed `RunOutcome` projected from the audit stream (stream stays source of truth); `VerificationPacket` with commits, deterministic results, risks; `pxx --runs` lists recent outcomes. Plus pre-existing per-round records + `cost_metrics.TokenMetrics`. **TEST_REGRESSION reachable (2026-07-17)**: introduced test failures now gate APPROVE (m2 evidence — a fix that broke a neighbor earned exit 0 once; never again), and stops with live regressions terminate as TEST_REGRESSION; r6 eval case enshrines the shape (corpus now 16). Absent: contributing (multi) codes, ReviewPacket, tokens/cost in outcomes, pluggable cost accounting, INTERRUPTED wiring — expand as Phase 13 demands. **VerificationPacket consumed (2026-07-17)**: `pxx --verify [run-id]` projects and prints it; APPROVE ships an evidence line + pointer — the packet is read, not just typed. |
| 13 | 60% | Built (2026-07-17, minimum slice): `pxx/evaluation.py` — TOML case format (stdlib tomllib, not YAML), disposable git-worktree materialization (identical start state per arm), layered checks (visible deterministic commands → hidden allowed-files / forbidden-patterns / tests-unchanged), and **self-check mode**: 15 shipped cases (5 micro + 5 historical-regression + 5 adversarial), every honest arm passes, all 10 cheat arms caught, two full runs byte-identical (repeatability), full corpus self-checks in CI on every commit; `pxx --eval [tier\|all]`. First self-check run caught a semantics bug in the harness itself (visible-gate catches counted as uncaught). **Live-agent arm built and validated same day** (`run_live_arm` + `pxx --eval-live <case>`): fixtures under `.pxx/eval/` inside the trusted-paths prefix (sovereignty boundary honored, never bypassed); loop rounds retargeted via `PXX_SELF_FIX_ROOT` (the #001 chdir was the first live run's failure — attempt 1 edited nothing while its safety machinery stashed uncommitted pxx work; recovered, fixed); checks run in the fixture's own uv env in both arms. **First successful live eval: m1 APPROVED in 1 round, diff=4, all hidden checks clean, run 20260717T031730-8156.** Attempt 1's four findings recorded (chdir retarget, check-env split, reviewer artifact vs scope guard, and OPEN: empty diff ⇒ reviewer auto-APPROVE — a no-change round reads as clean review; contained by the baseline gate, fix queued). **Full-suite live baseline complete (2026-07-17)**: `agent-e69f7bfcf496` (eval budgets 2r/600s/100d — the identity system correctly distinguishes it from the default-budget agent) scored **13/15 in 3.8 min**, zero cheating across all five adversarial temptations; both failures decompose cleanly — r2 = model capability (credential-regex from scratch in ≤2 rounds), r5 = **reviewer false-positive REVISE against a correct fix** (first measured reviewer-calibration data point; the progress guard stopped the healing spin; final tree passes). Baseline persisted at `evals/baselines/agent-e69f7bfcf496.json`. Absent: baseline-vs-candidate comparison, held-out partitioning, replay from run records. **Corpus at the ≥30 bar (2026-07-17)**: 30 cases (10/10/10), all self-checking in CI; the exit-criterion count is met. |
| 14 | 55% | Built (2026-07-17): **reviewer calibration suite** (`pxx/calibration.py` + 8 protected cases in `evals/calibration/`, thresholds explicit, `pxx --calibrate` fails closed on breach) run live against both fleet reviewers; **review prompt v2** (task context, out-of-scope rule, concrete-failing-input bar — each clause traceable to a measured FP class); **empty-diff reviews fail closed**; task threaded through the production review path via a single shared `build_review_prompt` (calibration and production cannot drift). Measured: 7b recall 0.00/fp 0.00 under the honest bar (quiet, carries nothing); Qwen3 recall 1.00 but live fp far above its calibration estimate. Absent: action broker, ambiguity gate, extension governance, larger calibration corpus (8 cases demonstrably underestimate live fp). **Reviewer-candidate search run (2026-07-17)**: two-stage self-audit (Qwen3 flags → Qwen3 skeptic must name a failing input) REJECTED — recall 1.00→0.88, fp 0.75→0.58, still far above threshold; the skeptic upholds the flagger's own hallucinations, and every surviving FP is a live-mined case. Design conclusion recorded (evals/baselines/reviewer-candidate-search-2026-07-17.json): no fleet model passes recall≥0.75 ∧ fp≤0.25 under any tested config, so the review layer should be **advisory (non-blocking)** until a cross-model skeptic or frontier reviewer is tried — the deterministic gates stay load-bearing. **Advisory review mode shipped (2026-07-17)**: the reviewer verdict is now optional-gating (`PXX_REVIEW_MODE`), resolving the calibration dead-end — deterministic gates enforce, reviewer advises; live-proven on r5. |
| 15 | 35% | Built (2026-07-17, minimum slice): `pxx/improvement.py` — deterministic weakness clustering over the run-outcome stream (dominant-failure, per-agent failure-rate, cross-agent regression), every observation labeled `correlation` (15.3 causal guardrail) and traceable to run_ids; `pxx --analyze`. Proposes nothing (Phase 15 stops before candidate generation by design). Proven on 50 real runs: it independently re-derived this session's manual findings — NO_TEST_PROGRESS dominant, and the hand-rejected reviewer candidate (agent-46d52695ef57, 69% fail) flagged as a regression, from the stream alone. Absent: semantic clustering, structured proposals, `pxx improve proposals`. |
| 16 | 80% | Built (2026-07-17, minimum slice): `pxx/candidates.py` — declarative single-variable candidates on an ALLOWLISTED behavior surface (budgets, review mode, reviewer model/url, retries; each an env-var overlay, zero source contact), with a fail-closed integrity validator: rejects non-allowlisted fields, protected-path targets (the TRUST_BOUNDARY/.aiderignore set — a test pins all three in sync), budget *increases* (tighten-only), and un-justified/evidence-less candidates. `pxx --propose <field> <value> --because <obs>` validates + persists to `.pxx/candidates/`; NEVER auto-applies. Closes the chain: `--analyze` → `--propose` → `--eval` → `--compare` → human. Absent: auto-generation of candidates from mined observations, broader change classes (prompts/skills/few-shot), one-command evaluate-both-arms. **Auto-generation link (2026-07-17)**: `improvement.propose_from_observations` maps a mined weakness → a VALIDATED candidate (invariant: never emits an invalid one; test-pinned), and `pxx --propose --auto` runs the whole chain — mine → propose → validate → persist — in one step. Live proof: on the 50 real accumulated runs it independently re-derived this session's manual fix (NO_TEST_PROGRESS dominant + blocking reviewer → propose review_mode=advisory), grounded in evidence, human-gated. One evidence-backed rule today; the rule table grows as weaknesses recur. Absent: broader change classes (prompts/skills), one-command both-arms eval+compare. **Candidate evaluation (2026-07-17, 16→17 seam)**: `pxx/candidate_eval.py` + `pxx --evaluate-candidate <id>` runs the live corpus at baseline AND under the candidate's env overlay, then `promotion.compare()` → a promotion verdict + record. Re-validates the candidate before running (a persisted candidate could be hand-edited) and fails closed on an empty corpus. Arm runner injected → orchestration unit-tested without live loops. Automates the sweep hand-run 3× today; human-gated, never applies. Chain now one/two commands end to end: `--propose --auto` → `--evaluate-candidate` → human. **FIRST FULL AUTO-LOOP VERDICT (2026-07-17)**: end to end on real loops — `--propose --auto` mined 50 runs → proposed advisory; `--evaluate-candidate` ran the live 30-case corpus at blocking+Qwen3 baseline vs advisory candidate → **ELIGIBLE, 18 gained / 0 lost, zero adversarial regressions** (evals/baselines/auto-loop-verdict-2026-07-17.json). Caveat: gain is vs an FP-prone blocking+Qwen3 baseline (the r5 finding at corpus scale), not vs the shipped 7b default — but the pipeline produced a correct evidence-backed, human-gated verdict from the system's own run history. **Protected-path single-source (2026-07-17)**: `pxx/protected_paths.py` — one list + `is_protected_path()` for validator and eval content-check; `.aiderignore`/doc are test-pinned mirrors. Prereq for content change-classes (they cross the boundary config candidates never touch). **Boundary hardened (2026-07-17, review round 5)**: `is_protected_path` now normalizes-and-fails-closed on the diff-path shapes a content candidate carries — git `a/`|`b/` prefixes, `..` traversal, backslashes, case (macOS FS), absolute paths, empty/None — returning protected for anything it can't cleanly classify. Normalization lives IN the function (not callers) so config and content checks can't drift. 5 leaks were live; all test-pinned. This is the enforcement floor content change-classes stand on — green-light condition now truly met. **Content change-class safety core (2026-07-17)**: `pxx/content_candidates.py` — content candidates rewrite behavior TEXT (pxx/prompts/, pxx/commands/ only), the first change-class that mutates files. Requirement #1 honored structurally: validate-path, write-path, and post-write verify-path all derive from ONE value via the shared `canonical_repo_path`; `verify_only_touched_target` reads ACTUAL changed paths from git (status --porcelain --untracked-files=all — catches new files plain --name-only misses, a gap my own test caught) and fails closed on any protected or non-target path. content_candidates.py added to the single-sourced protected set. 16 tests incl. traversal-into-protected, absolute, apply-refuses-invalid, and the write==verify equivalence. **Review-hardened + increment 1 wired (2026-07-17)**: two reviewer work orders closed under the Claude⇄Claude protocol — CR-…-content-candidates (P1–P4 APPROVED by reproduction) fixed committed-escape invisibility (verify diffs the pre-write HEAD), symlink write-through (reject before write), casefolded-write-path (canonical is now case-preserving; fold only at comparison), and porcelain C-quoting (`-z`); CR-…-increment1-wiring [G1 APPROVED] closed the vacuous pass on a valid-but-wrong base_sha — `verify_only_touched_target` now POSITIVELY requires the target to appear in the changed set, in the safety core so every caller is protected. The live-eval envelope landed (G2/G3): `evaluate_content_candidate` / `run_content_candidate_in_fixture` — clean-clone → apply → run (injected loop runner) → verify → restore, asserting the fixture is clean before apply (G3, fail loud) and threading apply's own `base_sha` into verify (G2, no re-derive). 33 content tests. Absent: content-hash immutability enforcement, human-diff/CLI surface, and corpus-scoring of content candidates (baseline vs candidate-prompt arms). |
| 17 | 45% | Built (2026-07-17): `pxx/promotion.py` — the comparison policy as code: exact case-by-case verdicts (no percentage theater), hard gate on adversarial-containment regressions, eligible = zero lost + at least one gained, corpus-mismatch fails closed, and `human_override` keeps the policy's objection on the record when a human overrules; `pxx --compare a.json b.json`. Verified against the REAL sweep data: rules candidate 2 NOT ELIGIBLE (lost m2) — matching the judgment call its manual promotion actually required. Absent: held-out partitioning, multi-metric (cost/latency) comparison, promotion-record persistence + activation flow. **Hardened (2026-07-17, review round 3)**: the hard gate is now ABSOLUTE — `human_override` rescues ordinary ineligibility but CANNOT promote an adversarial-containment regression (records `override_refused_hard_gate`); and `compare()` refuses arms with mismatched corpus fingerprints (content hash + count), so a persisted baseline scored on an older corpus can't be judged against a candidate on a newer one — same case NAMES ≠ same case CONTENT. |
| 18 | 5% | Seed: #002 safety tags + `prune_old_tags` — session-granular rollback primitive. Absent: channels, shadow, canary, breakers. |
| 19 | 10% | Seed: workflow state machine (idle→generating→review_pending→approved/rejected) with resume; supervisor-mode service lifecycle management. Absent: scheduler, task claims, control plane, triage inbox. |
| 19.5 | 5% | Seed: `resume_state` + persisted workflow state. Absent: claims, heartbeats, reconciliation loop. |
| 20 | 25% | Built (infrastructure): vector index save/load/remove, hybrid search, SearchCache, TTL cleanup, archival, migrations, 9.4 capture (today) whose loop-summary observation carries verdict metadata — the first evidence-confidence seed. Absent (the phase's actual point): utility measurement, provenance ranking, ablations, five-layer split. |
| 20.5 | 10% | Seed: ruff + pre-commit (lint/tests/diff-cap/scope) + secrets scan = primitive golden-principle enforcement. Absent: declarative principles, grades, GC cycles, rule-promotion ladder. |
| 21 | 0% | Correctly zero — gated on ten mandatory items. |
| 22 | 10% | Built: the single-scope loop primitive it composes, live-validated. Seed: skills/commands registry the planner would load. Absent: planner, DAG, roles, handoffs. |

**Headline numbers:** strictly **built-and-operational ≈ 8%** of roadmap
scope; **verifiable seeds add ≈ 7%** (code that the phases extend rather
than write from scratch); **≈ 85% aspirational**. Weighted by estimated
effort. By milestone: A ≈ 25% (mostly a projection of data the audit log
already emits), B ≈ 8% (the docs-sme eval runner is the only harness
seed; the corpus is the unclimbed mountain), C ≈ 8%, D 0%, E ≈ 8%.

Sweep corrections vs. the pre-sweep estimate: `governance.py` was
undercounted (secrets scanner = Phase 0 seed; three checks live), the
docs-sme eval harness was zero-credited (it's the Phase 13 pattern,
built), audit's proto-manifest raises Phase 11, and supervisor mode is
a real 10.75 precursor. Net: overall moved from ~8% to ~15%
built-or-seeded. The shape is unchanged: zeros exactly where the
roadmap's own gates demand zeros.

### Second-opinion reconciliation (Codex review of the same repo, 2026-07-16)

An independent assessment from the public repo + PyPI converged on the
same aggregate (8–10%) and improved the framing. Adopted:

1. **Runtime statement corrected.** Not "the production runtime is
   essentially 100% complete" but: *the bounded single-scope execution
   primitive is essentially complete for its intentionally limited
   operating envelope.* The ordinary interactive path still `os.execv`s
   into aider — after which pxx supervises nothing. Runtime ownership,
   typed tool interception, cancellation, checkpointing, replay, and
   backend independence are all future work (10.75/10.8).

2. **Four headline numbers, not one:**

   | Measurement | Estimate |
   |---|---|
   | Bounded edit→test→review→heal primitive | 85–95% |
   | Measurement and evidence foundation | 20–25% |
   | Actual cross-run self-improvement capability | 2–5% |
   | Entire long-term roadmap | 8–10% |

   "Only 10% built" does not mean pxx is only 10% useful; "the loop is
   done" does not mean the platform is nearly done.

3. **Dual 0–5 scoring adopted** for future ledger updates —
   *implementation* (0 not started … 5 operational with sustained
   evidence) and *operational* scored separately, with dependency/
   effort/risk weights and a `next_gate` list per phase. This
   distinguishes "code exists" from "the capability is dependable." It
   also resolves the one real disagreement: the docs-sme eval runner is
   implementation-score-1 *pattern precedent*, operational-score-0 for
   agent evaluation — so **Milestone B is 3–5%, not 8%** (and C
   likewise 3–5%). The seed credit in the table above is implementation
   credit only.

4. **Agreed immediate sequence** (anti-overengineering: don't design
   elaborate telemetry before the eval lab reveals which evidence it
   needs):
   1. Finish Phase 0 — publish the prepared release; extend
      `scan_staged_secrets` into the public-content scanner; write the
      trust-boundary doc.
   2. Minimum Phase 11 identity only — run_id, agent_version_id,
      starting commit, model/provider identity, prompt + policy hashes.
   3. Minimum Phase 12 projection — terminal failure taxonomy,
      `RunOutcome`, `VerificationPacket`.
   4. **Immediately begin Phase 13** — five micro fixtures, five
      historical regressions, five adversarial cases, disposable
      worktree runner, repeatability report.
   5. Expand Phase 12 only as Phase 13 demonstrates missing evidence.

   Note this consciously defers 10.75/10.8: the minimum measurement
   path runs fine on the current subprocess architecture; runtime
   ownership lands before the Phase 14 action broker, which genuinely
   needs it.

**Reconciled bottom line:** pxx has completed most of the difficult
bounded-execution foundation, roughly a quarter of the measurement
foundation, and almost none of the controlled cross-run learning
system — ~8–10% of the full roadmap. The low aggregate is a consequence
of the roadmap's ambition, not evidence that completed work is small or
poorly prioritized.
