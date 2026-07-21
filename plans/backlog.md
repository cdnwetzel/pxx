# Plans backlog — master inventory

Every plan in `plans/` has a row here with a stable numeric ID. The status
column must reflect current reality — update it **in the same commit** as
the work that motivates the change (see "Status hygiene" in CLAUDE.md).

Statuses: `planned` | `in-progress` | `blocked` | `done`.

| ID  | Plan | Status | Blocks | Blocked by |
|-----|------|--------|--------|------------|
| 001 | [Phase 8: Infrastructure Hardening & Scaling](phase-8.md) | done | — | — |
| 002 | [Phase 8 Tier 2/3: Feature Expansion & Intelligence](phase-8-tier2-tier3.md) | planned | — | — |
| 003 | [Phase 8.5: Observation Confidence Scoring](phase-8.5-confidence-scoring.md) | planned | — | — |
| 004 | [Phase 9: Closed-Loop Autonomy (`pxx --loop`)](phase-9-loop.md) | done | — | — |
| 005 | [Phase 10: PyPI-Ready](phase-10-pypi-ready.md) | done | — | — |
| 006 | [Docs-RAG SME retrieval proxy](docs-rag-sme.md) | done | — | — |
| 007 | [session hardening](session-hardening.md) | done | — | — |
| 008 | [2026-07-16 session: Phase 9 dogfood + docs-sme A/B](session-2026-07-16-loop-dogfood.md) | done | — | — |
| 009 | [Open items & remediation plan (post-dogfood sweep)](open-items-2026-07-16.md) | done | — | — |
| 010 | [2026-07-17 session: decisions, scrub, push, v1.1.0](session-2026-07-17-decisions-and-release.md) | done | — | — |
| 011 | [Roadmap: continuous self-improvement (Phases 11–22)](roadmap-continuous-self-improvement.md) | in-progress | — | — |
| 012 | Phase 0.5: Continuous verification (CI + package smoke) — tracked in roadmap 011 | planned | — | — |
| 013 | [pxx 1.3.1 — Install & Upgrade UX](pxx-1.3.1-install-upgrade.md) | done | — | — |

Next free ID: **014**

## Workflow for adding a new plan

1. Read this file — make sure no existing plan covers the idea; if one
   nearly does, expand it instead of creating a duplicate.
2. Pick the next free ID (line above) and create `plans/<slug>.md`.
3. Add `> Backlog ID: NNN` as the first line after the title in the new
   plan file.
4. Add a row to the table with status `planned` (or `blocked` + the
   blocking IDs in "Blocked by").
5. Bump the "Next free ID" line — never let it lag.

## Provenance

Created 2026-07-15. CLAUDE.md had described this inventory before it
existed (drift found while landing plan 007); IDs 001–006 were assigned
retroactively in file-history order and statuses taken from each plan's
own header at that date.
