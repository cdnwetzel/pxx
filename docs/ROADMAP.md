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

## Shipped in 2.1.1 (2026-07-26)

Defects verified against code on 2026-07-26 (live 6/6 tutorial run on local
Ollama plus two independent adversarial review rounds, both
verify-by-execution; reproduction records in
`~/.local/state/pxx/runs/20260726T15*`). Authoritative list in
CHANGELOG.md; highlights:

- **`GIT_*` environment scrub** (`pxx/gitenv.py`, every git spawn site +
  `run_shell` + the aider process env) — pxx invoked from inside a git
  hook previously operated on the *caller's* repository (proven: a leaked
  `GIT_DIR` staged deletion of every tracked file). Found and expanded by
  the independent review after the original patch list was drafted — the
  strongest item in the release came out of the review process itself.
- **Findings-less REVISE degrades to NO_REVIEW** instead of burning
  healing rounds on zero bullets (`review_error="empty"`).
- **Broken-aider fallback** — auto selection health-probes
  `aider --version`, falls back to native with a warning.
- **Doctor depth** — model presence verified on reachable endpoints
  (empty Ollama endpoints flagged), aider binary actually executed.
- **Context overflow surfaces actionably** ("raise `num_ctx`"), and
  **`edit_file` misses steer the model** to re-read and retry.
- **Tutorial troubleshooting** updated to match (incl. the loud-failing
  Ollama ≥ 0.32 behavior).
- Repo hygiene (preceded the patch): `.gitignore` + pre-commit privacy
  gate for `private/`, `review/`, `services/`, `.aider.*`; PyPI sidebar
  and README links fixed (no `main` branch exists).

## Open (post-2.1.1)

- `services/` tree fate: own repo vs. deliberately tracked after a
  privacy scan (ignored, not tracked, today — human decision).
- Wire up the `unresolved_critical_defects` readiness count (the bar
  currently fails on *None* — a tracking gap, not a defect count) and add
  6 eval cases to reach the 50-case bar.
- Warn on set-but-unconsumed `PXX_*` env vars (deferred from 2.1.1).
- A quickstart subcommand (proposed, does not exist yet): scaffold the
  tutorial sandbox from packaged resources (today the wheel ships neither
  the tutorial nor the setup script).
- Auto-backend probe latency (~1–2 s per invocation with a healthy aider
  installed): cache or probe-on-failure redesign (deferred, documented).
- **Detect tool-call-shaped prose** (from the R-007 Camelid lane map): when
  a completion's `content` contains a well-formed `<tool_call>` block but
  `tool_calls` is empty, the serving layer dropped the call — pxx should
  warn (or parse) instead of treating it as a final answer. Gives the
  tutorial's "describes edits instead of making them" symptom a
  machine-detectable signature.
- ~~Reviewer timeout on real-repo diffs~~ **shipped in 2.1.2**
  (2026-07-27, ~24 h find-to-ship): `PXX_REVIEW_TIMEOUT` with
  `PXX_NATIVE_TIMEOUT` fallback, never-blank failure reasons, and the
  reviewer context-overflow message — see CHANGELOG. Remaining parity
  note from its review (non-blocking): malformed timeout env values fall
  back silently in both the reviewer and the native backend; a warning
  would be cheap insurance.

## Later

- Model-backed boundary roles (today's are deterministic).
- Cross-repo knowledge federation.

## Release story

2.0.0 **replaces** the 1.3.x line on the `pxx-orchestrator` PyPI name
(requires-python >= 3.11; the aider backend is an optional, python-gated
extra, so the core installs and imports cleanly on 3.13 — no 1.3.3-style
fallback hole). The 1.x line ends at v1.3.3; 2.0.0 publishes as rc first
(2.0.0rc1 → soak → 2.0.0).
