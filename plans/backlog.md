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
| 014 | Docstring convention reconciliation  | [docstring-convention-reconciliation.md](docstring-convention-reconciliation.md) | done     | 013 | —          |
| 017 | Dogfooding Tier 4 — Learnings Loop   | [dogfooding-tier4-learnings-loop.md](dogfooding-tier4-learnings-loop.md)     | proposed | — | 001, 004   |

| 015 | Pre-push autonomous-commit gate      | [pre-push-autonomous-gate.md](pre-push-autonomous-gate.md)                   | proposed | — | —          |
| 016 | Pre-commit hook body test coverage   | [pre-commit-hook-body-tests.md](pre-commit-hook-body-tests.md)               | proposed | — | —          |

| 018 | vLLM backend integration             | [vllm-backend-integration.md](vllm-backend-integration.md)                   | done     | —  | —          |
| 019 | Multi-tier model routing             | [multi-tier-model-routing.md](multi-tier-model-routing.md)                   | done     | —  | 018        |
| 020 | Workflow state persistence           | [workflow-state-persistence.md](workflow-state-persistence.md)               | done     | 021, 022 | —          |
| 021 | Review framework integration         | [review-framework-integration.md](review-framework-integration.md)           | done     | —        | 020        |
| 022 | Light governance gate                | [light-governance-gate.md](light-governance-gate.md)                         | done     | —        | 020        |

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

## Next free ID

`023`
