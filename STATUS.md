# pxx — project status

_Last updated: 2026-06-10_

## Released

**`pxx-orchestrator 1.0.0` is live on PyPI** — <https://pypi.org/project/pxx-orchestrator/>
(`pip install pxx-orchestrator`; the command and import package are `pxx`).
Verified installable from both PyPI and TestPyPI in clean venvs. Core scope
(Option B): the orchestrator + ask/edit against any Ollama; the optional
supervisor services (`--with-memory`, `--with-router`, `--with-docs`) live in
`services/` and require a repo checkout.

## Current phase work

| Phase | Status |
|---|---|
| 8 (memory infrastructure) | Tier 1 (8.1–8.3) + 8.4 done; 8.5 confidence scoring designed, planned |
| 9 (closed-loop autonomy, `plans/phase-9-loop.md`) | 9.1+9.1b done (verifier hardening); 9.2+9.3 done (driver + guards); 9.4 in-loop half done; **`pxx --loop` / `--heal` shipped experimental** |
| 10 (PyPI-ready, `plans/phase-10-pypi-ready.md`) | done — shipped |

## The loop (Phase 9) — where it stands

`pxx --loop "<task>" --scope <path>` drives bounded edit→test→review→heal
rounds over the existing primitives. Conservative posture: experimental,
pxx-repo-only, requires `--scope`, a clean tree, and the pxx git hooks
(`pxx --install-hook`); never pushes.

- **Live dogfood run #1**: the model implemented a seeded single-file task
  correctly in one round (3/3 tests); every guard fired as designed; the
  review leg exposed environment gaps (timeout, lint scope, missing hook
  boundary, missing reviewer output contract) — all fixed, with a second-side
  verification pass adopted in full.
- **Run #2 (next)**: hooks installed, comparable seeded task; success
  criterion is measuring whether a REVISE round's healing prompt steers
  round 2, via the per-round audit capture (verbatim healing message, per-leg
  wall-clock, findings-by-severity).

## Quality state

- Test suite: **690 passed** (from 357 at the start of 2026-06-10; the
  merge-loss recovery restored 11 lost suites and surfaced real bugs —
  including an index-vs-worktree secrets-scanner bypass — all fixed).
- Verdict engine fails closed: unknown severities → REVISE, no review
  evidence → NO_REVIEW (never silent approval); near-miss finding headers
  surface as UNPARSEABLE instead of vanishing.
- Repo de-identified and public-safe; gitleaks over full history: no leaks.

## Key conventions

- Dual-remote: every push fans out to both mirrors (`git push origin main`).
- Machine-local config: `~/.config/pxx/env` (KEY=VALUE; never in the repo).
- `aider-chat` is exact-pinned by design; bump only via the discipline in
  `CLAUDE.md`.
- Plan statuses update in the same commit as the work (`plans/*.md`).
