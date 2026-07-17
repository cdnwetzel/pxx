# AGENTS.md — map, not manual (roadmap Phase 10.5)

Short table of contents for any coding agent working in this repository.
Deep instructions live in the linked files; do not duplicate them here.

| Concern | Authority |
|---|---|
| Project instructions, guardrails, style | [CLAUDE.md](CLAUDE.md) (canonical; other agents' files should point here) |
| Meta-rules for editing pxx with pxx | [CONVENTIONS.md](CONVENTIONS.md) |
| Executable workflow contract (budgets, commands, protected paths) | [WORKFLOW.md](WORKFLOW.md) — CI-enforced against code |
| What automation may never modify | [docs/TRUST_BOUNDARY.md](docs/TRUST_BOUNDARY.md) |
| Evaluation corpus + reviewer calibration | [evals/README.md](evals/README.md) |
| Long-term roadmap + grounded readiness ledger | [plans/roadmap-continuous-self-improvement.md](plans/roadmap-continuous-self-improvement.md) |
| Plan inventory & status rules | [plans/backlog.md](plans/backlog.md) |

Non-negotiables, in one breath: ask mode is the default and edit requires
`--edit`; the guardrailed files in CLAUDE.md are refuse-and-ask; scope gates
are enforced, not advisory; deterministic gate failures can never be
overridden by model judgment; pushing and publishing are human acts.
