# pxx Roadmap

> This document replaces the v1 phase ledger (phases 0–22), which described
> the 1.x self-improvement program as planned against the 1.x codebase. That
> history is preserved in git at this path before this commit. For the v2
> architecture contracts see `DESIGN.md` and `DESIGN-ROADMAP.md`.

## Shipped in 2.0.0

The complete program, built and verified (build track M0 → B10, each
milestone reviewer-verified by execution):

- **Async runtime; pxx owns the loop** — pluggable backends (native /
  aider / mock / replay), fresh context per round, bounded loops with a
  recovery ladder.
- **Fail-closed safety** — permission modes, canonicalized scope,
  deterministic hooks, budgets, the action broker as the single
  authorization authority.
- **Measurement** — 23-code terminal taxonomy with contributing codes, full
  per-leg RunOutcome, commit-bound reviews, immutable agent manifests with
  drift sentinels (served-model fingerprints), identity threading.
- **Evaluation** — a 30-case self-checking corpus across five families,
  held-out partitioning, reviewer calibration, deterministic replay.
- **Learning** — root-cause mining (correlation-only), constrained
  candidates on an allowlisted surface with an apply→verify write boundary,
  semantic loop detection.
- **Memory** — five knowledge layers, measured observed_utility via
  ablations, no success auto-conversion, entropy control (golden-principle
  lints, grades, deterministic GC).
- **Promotion & deployment** — held-out-only, multi-metric (cost ≤ 1.15×),
  risk-routed, hard-gates-absolute promotion; stable→candidate→shadow→
  canary→stable channels; seven circuit breakers; evidence-gated
  auto-promotion with preconditions and post-promotion auto-rollback.
- **Operation & orchestration** — scheduled improvement daemon, task
  reconciliation, checkpoint/resume, goal orchestration with per-node
  worktree isolation, full typed event vocabulary, outcome projection.
- **Authority & legibility** — WORKFLOW.md machine contract hashed into
  agent identity, ambiguity gate, evidence-linked findings, audit sampling.

## Next (2.x hardening)

- Earned enablement: run the daemon in production, accumulate the real-run
  and human-promotion counts the auto-promotion readiness bars require
  (100 real runs, 3 human promotions) — auto-promotion stays report-and-refuse
  until the platform earns it.
- Live (non-scripted) eval arms on real endpoints, with the calibration
  fp-rate tracked against production fp.
- The `pxx-reviews` triage loop for boundary-review artifacts.

## Later

- Model-backed boundary roles (today's are deterministic).
- Cross-repo knowledge federation.

## Release story

2.0.0 **replaces** the 1.3.x line on the `pxx-orchestrator` PyPI name
(requires-python >= 3.11; the aider backend is an optional, python-gated
extra, so the core installs and imports cleanly on 3.13 — no 1.3.3-style
fallback hole). The 1.x line ends at v1.3.3; 2.0.0 publishes as rc first
(2.0.0rc1 → soak → 2.0.0).
