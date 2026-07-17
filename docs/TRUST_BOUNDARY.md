# Trust boundary — optimizer-protected components

> Roadmap Phase 0.3 (plans/roadmap-continuous-self-improvement.md).
> Established 2026-07-16, alongside the v1.1.0 / learning-baseline-1 tags.

pxx is growing a continuous self-improvement system: analyzers that mine
run evidence, generators that propose candidate configuration changes,
and (eventually) automatic promotion of narrowly-scoped low-risk
changes. That system judges its own work — so the components that do the
judging, gating, and permissioning must sit **outside** its reach.

## Protected components

Candidate agents and any automated improvement process may **propose**
changes to these (as reviewable diffs for a human), but may never apply,
commit, or promote changes to them autonomously:

| Component | Why protected |
|---|---|
| `pxx/safety.py` | #002 safety tags — the rollback primitive |
| `pxx/scope.py` | Scope resolution + trusted-path gates — the write boundary |
| `pxx/governance.py` | Secrets + public-content scanning, version sync, verdict gating |
| `pxx/review_gate.py` | Verdict engine, review backends, preflight — the checker |
| `pxx/loop.py` guards | Round/diff/time budgets, progress + scope guards, fail-closed branches |
| `tests/` for the above | A gate whose tests the optimizer can edit is not a gate |
| Eval fixtures & hidden checks | (future, `evals/`) — anti-cheat surface; see roadmap Phase 13.4 |
| Promotion configuration | (future) — thresholds, allowlists, risk classes |
| `.github/workflows/` | Release path and credentials — publish is human-controlled |
| `.aiderignore`, `CONVENTIONS.md`, `pyproject.toml`, `config/*.yml` | Existing guardrail files (CLAUDE.md "Hard guardrails") |
| `~/.config/pxx/content-denylist`, `private/` | The scanner's literal denylist — self-editing it would blind the scanner |

## Enforcement layers (today)

1. `.aiderignore` + CONVENTIONS refuse-and-ask rules (editor-level).
2. The loop's scope gate: `--scope` + `_out_of_scope_changes()` stop any
   round that touches paths outside its declared scope, fail-closed.
3. The pre-commit hook's scope check on non-aider commits.
4. This document: the declared policy that future phases (14's action
   broker, 16's candidate integrity validation) must enforce
   mechanically — candidate validation MUST reject any change whose
   target matches this list.

## Invariants (from the roadmap, restated as boundary rules)

- The production agent never changes its own active configuration.
- The candidate generator cannot modify its own evaluator, fixtures, or
  hidden checks.
- A model verdict can never override a failed deterministic gate.
- The optimizer cannot expand its own permissions, budgets, or this
  list. Removing an entry from this document is itself a
  human-only change.
- The self-improvement agent never installs an extension and approves
  its own new permissions.
- Pushing, merging, and publishing remain human-controlled.

## Change process

Editing this document — including loosening any entry — requires a
human-authored commit with rationale in the commit message. Automated
processes citing this file must treat its list as a deny-set, matched
by path prefix, before any other permission logic runs.
