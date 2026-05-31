# Backlog

Master inventory of pxx planning docs. Each plan gets a stable numeric ID that
is **never reused**, even if a plan is cancelled.

## Scope of this inventory

This backlog tracks **pxx development work** — changes to pxx's code, tests,
configs, scripts, prompts, slash commands, or repo-level documentation.

It does **NOT** track:

- Review-agent workflows or meta-tooling (Gemini's `GEMINI.md` workflow,
  Codex's review-refresh passes, etc.). Those agents own their own work
  and their output lives in `../review/`.
- Updates to `../review/*` — owned by the review agents per
  `../review/inventory.md`.
- Per-agent instruction files (`CLAUDE.md`, `GEMINI.md`) — those evolve
  independently as the agents themselves do.

If you are an automated agent considering an entry here: ask the user
first if your work is *meta-tooling for yourself* vs. *a change to pxx
itself*. Only the latter belongs in this inventory.

## Workflow for adding a new plan

1. Pick the next free ID from **"Next free ID"** at the bottom of this file.
2. Scan the table below to make sure an existing plan doesn't already cover
   the idea. If one does, expand that plan instead of creating a duplicate.
3. Copy [`_template.md`](_template.md) to `plans/<slug>.md` (filename is
   just the slug; the ID lives in the header block).
4. Fill in the title, ID, status (`proposed` for new stubs, `planned` once
   fleshed out), and the dependency columns.
5. Add a row to the Plans table below with the same ID, title, file link,
   status, and dependencies.
6. Bump the "Next free ID" line.
7. If this plan blocks another, or is blocked by one, fill in the dependency
   columns on both ends so the graph stays consistent.

The template documents which sections are required at "proposed" vs
"planned" stages and which are optional. Following it keeps the plans
comparable and scannable.

## Plans

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 001 | Dogfooding pxx (self-improvement)    | [dogfooding.md](dogfooding.md)                    | in-progress | —        | —          |
| 002 | Safety foundation                    | [safety-foundation.md](safety-foundation.md)      | done     | 001      | —          |
| 003 | Scoping & dry-run                    | [scoping-and-dry-run.md](scoping-and-dry-run.md)  | done     | 001      | —          |
| 004 | Session audit log                    | [session-audit-log.md](session-audit-log.md)      | done     | —        | —          |
| 005 | Setup-script hardening               | [setup-script-hardening.md](setup-script-hardening.md) | done     | —        | —          |
| 006 | Cross-machine drift detection        | [cross-machine-drift-detection.md](cross-machine-drift-detection.md) | done  | —        | —          |

| 007 | Slash-command discoverability        | [slash-command-discoverability.md](slash-command-discoverability.md) | done     | —        | —          |
| 008 | Auto-restart hint after self-edits   | [auto-restart-hint.md](auto-restart-hint.md)                         | done     | —        | —          |
| 009 | VS Code (Continue.dev) integration   | [vscode-continue-integration.md](vscode-continue-integration.md)     | planned  | —        | —          |
| 010 | Dogfooding Tier 1 — self-test/lint   | [dogfooding-tier1-self-test-lint.md](dogfooding-tier1-self-test-lint.md) | done | —    | —          |
| 011 | Dogfooding Tier 2 — self-improve     | [dogfooding-tier2-self-improve.md](dogfooding-tier2-self-improve.md)     | done    | — | —          |
| 012 | Dogfooding Tier 3 — bounded autonomy | [dogfooding-tier3-bounded-autonomy.md](dogfooding-tier3-bounded-autonomy.md) | done    | — | —          |
| 013 | Split `cli.py` into modules          | [cli-module-split.md](cli-module-split.md)                                   | done     | — | —          |
| 014 | Docstring convention reconciliation  | [docstring-convention-reconciliation.md](docstring-convention-reconciliation.md) | done     | — | —          |
| 017 | Dogfooding Tier 4 — Learnings Loop   | [dogfooding-tier4-learnings-loop.md](dogfooding-tier4-learnings-loop.md)     | proposed | — | 001        |

| 015 | Pre-push autonomous-commit gate      | [pre-push-autonomous-gate.md](pre-push-autonomous-gate.md)                   | done | — | —          |
| 016 | Pre-commit hook body test coverage   | [pre-commit-hook-body-tests.md](pre-commit-hook-body-tests.md)               | in-progress | — | —          |

| 018 | vLLM backend integration             | [vllm-backend-integration.md](vllm-backend-integration.md)                   | done     | —  | —          |
| 019 | Multi-tier model routing             | [multi-tier-model-routing.md](multi-tier-model-routing.md)                   | done     | —  | 018        |
| 020 | Workflow state persistence           | [workflow-state-persistence.md](workflow-state-persistence.md)               | done     | 021, 022 | —          |
| 021 | Review framework integration         | [review-framework-integration.md](review-framework-integration.md)           | done     | —        | 020        |
| 022 | Light governance gate                | [light-governance-gate.md](light-governance-gate.md)                         | done     | —        | 020        |

## Phase Summary

**Phase 2 (Completed):** 14 of 17 review findings fixed (82% coverage)
- Safety & routing (Plans 002–022): All done
- Governance & audit (Plans 004, 020–022): All done
- Multi-tier infrastructure (Plans 010–012, 018–019): All done
- Code quality (Plans 013–016): Mostly done, #016 in progress

**Phase 3 (In Progress):** 3 items documented, prioritized for future implementation
- ✅ Done: Test refactor (#023, M-07)
- Pending: Environment isolation (#025, L-05), Docs comment (#024, L-03)

**v2.1 Production Baseline:** All three HIGH regressions fixed, validated by 4 agents, pushed to main.

---

## Phase 3 Deferred Items

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 023 | Test architecture refactor (call → state verification) | [test-refactor-architecture.md](test-refactor-architecture.md) | done | 025 | — |
| 024 | Python 3.13 ceiling documentation | [python313-ceiling-docs.md](python313-ceiling-docs.md) | done | — | — |
| 025 | Environment isolation (OPENAI_API_KEY) | [environment-isolation.md](environment-isolation.md) | done | — | 023 |

## Status legend

- **proposed** — idea captured; not yet committed to implementation
- **planned** — details locked; awaiting kickoff
- **in-progress** — implementation underway
- **blocked** — cannot proceed; see "Blocked by" column
- **done** — implementation complete and verified
- **cancelled** — dropped; kept here for traceability so the ID isn't reused

## Dependency rules

- "Blocks" and "Blocked by" must be mutually consistent. If plan B depends on
  plan A, A's "Blocks" row contains B and B's "Blocked by" row contains A.
- A plan in **blocked** state has at least one non-**done** ID in its
  "Blocked by" column. Once all blockers move to **done**, the status can
  advance to **planned** or **in-progress**.
- A plan cannot block itself. Cycles are bugs; surface them in PRs.

## Phase 4 Proposed Items (from Gemini Phase 3 review)

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 026 | Process-lifecycle management (subprocess.run) | [process-lifecycle-management.md](process-lifecycle-management.md) | proposed | — | — |
| 027 | Audit log advisory locking           | [audit-log-locking.md](audit-log-locking.md)     | proposed | — | — |
| 028 | Git operation timeout standardization | [git-timeout-standardization.md](git-timeout-standardization.md) | proposed | — | — |
| 029 | Environment variable validation (PXX_DIFF_CAP) | [env-var-validation.md](env-var-validation.md) | proposed | — | — |

See `review/gemini/gemini-phase3.md` for detailed rationale and findings (F-019, F-016, F-018, F-020).

## Phase 4 Critical Items (from Codex Phase 3 review)

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 030 | Fix staged-secret scan (read index, not worktree) | [staged-secret-scan-fix.md](staged-secret-scan-fix.md) | proposed | 031 | — |
| 031 | Add index/worktree boundary tests for governance | [governance-boundary-tests.md](governance-boundary-tests.md) | proposed | — | 030 |
| 032 | Consolidate env cleanup across fixtures | [env-cleanup-consolidation.md](env-cleanup-consolidation.md) | proposed | — | — |
| 033 | Add type-safety for model_for() tier handling | [model-tier-type-safety.md](model-tier-type-safety.md) | proposed | — | — |

See `review/codex/codex-phase3.md` for detailed findings (F-001 HIGH, F-002 MEDIUM, F-003/F-004 LOW).

## Phase 4 High-Priority Items (from Copilot Phase 3 review + consensus)

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 034 | **Fix staged-secret scan (git show, not worktree)** | [staged-secret-scan-fix.md](staged-secret-scan-fix.md) | done | 035 | — |
| 035 | Add governance boundary tests (index/worktree) | [governance-boundary-tests.md](governance-boundary-tests.md) | done | — | 034 |
| 036 | Add subprocess timeouts (self_modes.py) | [subprocess-timeout-consistency.md](subprocess-timeout-consistency.md) | done | — | — |
| 037 | Fix test_governance mock patching (pxx.governance module, not global) | [test-governance-mock-patching.md](test-governance-mock-patching.md) | done | — | — |
| 038 | Add tier/endpoint_backend to audit session-start record | [audit-routing-forensics.md](audit-routing-forensics.md) | done | — | — |
| 039 | README: Document --tier/--check flags; handle ValueError for invalid tier | [cli-flags-documentation.md](cli-flags-documentation.md) | done | — | — |
| 040 | Add autouse PXX_GOVERNANCE_SKIP fixture (conftest.py) | [governance-skip-fixture.md](governance-skip-fixture.md) | proposed | — | — |
| 041 | Style consistency: review_gate.py check=False | [subprocess-style-consistency.md](subprocess-style-consistency.md) | proposed | — | — |
| 042 | Standardize git operation timeouts (3s vs 2s) | [git-timeout-standardization.md](git-timeout-standardization.md) | proposed | — | — |

See `review/copilot/copilot-phase3-review.md` for detailed findings (F-001 HIGH confirmed, F-002–F-008 MEDIUM/LOW).

## Phase 5: Local-First Integrations & Learnings Loop

**Context:** 9router (token compression), agentmemory (learnings memory), agent-skills (workflow discipline) — evaluated and designed for pxx.

### Critical Path (Tier 4 Learnings Loop) — 5–7 days

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 043 | agentmemory: Persistent memory server setup | [agentmemory-setup.md](agentmemory-setup.md) | proposed | 044 | — |
| 044 | agentmemory: Daily audit-log indexer | [audit-log-indexer.md](audit-log-indexer.md) | proposed | 045 | 043 |
| 045 | agentmemory: Pre-session learnings query + MCP | [learnings-query-mcp.md](learnings-query-mcp.md) | proposed | — | 044 |

### Parallel Track (Workflow Discipline) — run during indexer dev

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 046 | agent-skills: Refactor self_modes → YAML | [agent-skills-refactor.md](agent-skills-refactor.md) | proposed | 047 | — |
| 047 | agent-skills: Slash command integration | [skill-slash-commands.md](skill-slash-commands.md) | proposed | — | 046 |

### Optional Enhancement

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 048 | 9router: Optional token compression layer | [9router-integration.md](9router-integration.md) | proposed | — | — |
| 049 | Audit log schema evolution (learnings fields) | [audit-schema-evolution.md](audit-schema-evolution.md) | proposed | — | — |

**Total Phase 5:** ~15 days (critical path + parallel + optional)  
**Start gate:** Phase 4 complete ✓ (382 tests passing, all HIGH+MEDIUM items done)

## Next free ID

`050`
