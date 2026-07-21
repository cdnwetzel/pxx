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

**The authoritative machine list is `pxx/protected_paths.py::PROTECTED_PREFIXES`,
and `is_protected_path()` is the one decision function the candidate validator
and the eval content-check both call. This table and `.aiderignore` are static
mirrors of that list (a doc can't import; aider can't run Python); tests hold
both to it. Edit the list in one place — `protected_paths.py` — and the tests
enforce the mirrors.**

| Component | Why protected |
|---|---|
| `pxx/safety.py` | #002 safety tags — the rollback primitive |
| `pxx/scope.py` | Scope resolution + trusted-path gates — the write boundary |
| `pxx/governance.py` | Secrets + public-content scanning, version sync, verdict gating |
| `pxx/review_gate.py` | Verdict engine, review backends, preflight — the checker |
| `pxx/loop.py` | Round/diff/time budgets, progress + scope guards, fail-closed branches |
| `pxx/evaluation.py`, `evals/` | The eval harness + fixtures/hidden checks — the grader |
| `pxx/calibration.py`, `pxx/promotion.py` | Reviewer scoring + comparison policy — the judges |
| `pxx/candidates.py`, `pxx/candidate_eval.py`, `pxx/content_candidates.py`, `pxx/improvement.py` | Candidate generation, evaluation, content-change-class, and mining — the optimizer must not edit itself |
| `pxx/protected_paths.py` | The protected list itself — rewriting it defeats the boundary |
| `tests/` for the above | A gate whose tests the optimizer can edit is not a gate |
| Promotion configuration | (future) — thresholds, allowlists, risk classes |
| `.github/workflows/` | Release path and credentials — publish is human-controlled |
| `.aiderignore`, `CONVENTIONS.md`, `pyproject.toml`, `config/*.yml` | Existing guardrail files (CLAUDE.md "Hard guardrails") |
| `~/.config/pxx/content-denylist`, `private/` | The scanner's literal denylist — self-editing it would blind the scanner |

## Enforcement layers (today) — and the honest gap

1. **The loop's scope gate** (`--scope` + `_out_of_scope_changes()`): the
   *primary* mechanism. A round terminates `OUT_OF_SCOPE`, fail-closed, if
   any change escapes its declared scope. A loop is only as protected as its
   scope is narrow, though — a run explicitly scoped *at* an evaluator file
   would be permitted (see the gap below).
2. The pre-commit hook's scope check on non-aider commits.
3. `.aiderignore` refuse-and-ask (editor-level).

**GAP CLOSED (2026-07-17, from independent review).** `.aiderignore` now
lists the protected code modules (`safety`, `scope`, `governance`,
`review_gate`, `loop`, `evaluation`, `calibration`, `promotion`), `evals/`,
and the specific tests that grade them — so aider (and therefore
`--self-fix`/`--loop`) refuses to edit its own gates or evaluator, the
editor-level backstop this layer always claimed. Precise per-file rather than
a blanket `tests/`, so the loop can still write legitimate tests elsewhere.
Phase 16's candidate-integrity validation must enforce the SAME list
mechanically (defense in depth); until it exists, `.aiderignore` + the scope
gate are the enforcement.
4. This document: the declared policy that Phase 16's candidate-integrity
   validation MUST enforce mechanically — reject any change whose target
   matches the table above.

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
