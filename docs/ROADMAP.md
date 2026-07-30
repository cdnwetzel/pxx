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
  until the platform earns it. **IN PROGRESS since 2026-07-29**: the daemon
  runs under launchd on the primary workstation (hourly ticks); first
  proposal human-reviewed and rejected same day. real_runs at 50/100.
- Live (non-scripted) eval arms on real endpoints, with the calibration
  fp-rate tracked against production fp.
- The `pxx-reviews` triage loop for boundary-review artifacts. The
  *proposal-inbox* half **shipped in 2.1.5** (2026-07-29): `pxx improve
  triage list|qualify|reject` with reviewer identity, and the cycle honors
  human dispositions instead of re-proposing them every tick (found live
  on the daemon's first day). Boundary-review artifacts remain open.

## Shipped in 2.1.6 + 2.1.7 (2026-07-30)

Same-day pair; authoritative detail in CHANGELOG.md.

- **2.1.6 — unknown-flag subcommand hint**: `pxx --upgrade` (and any
  dash-flag naming a subcommand) now hints the right spelling instead of
  being silently ignored by the 1.x compat handler. Also closed the
  long-open py3.13/aider work order as resolved-by-redesign — the
  marker-gated `[aider]` extra already fences the broken chain, and the
  v1-era `requires-python` cap would regress working 3.13 native installs
  (reviewer-verified against the published wheel).
- **2.1.7 — `pxx upgrade` verifies its own outcome** (PR #7): found by
  dogfooding the 2.1.6 rollout minutes after publish — uv exits 0 on a
  stale index and the old code claimed an upgrade that never happened (the
  pass-on-silence class, reaching the upgrade path itself). Post-upgrade
  fresh-process probe of the invoking entry point, anchored banner parse,
  bounded probe with kill-and-reap on cancellation, index-ahead installs
  count as success. Review: CodeRabbit PR lane (1 actionable, fixed
  in-flight) + three adversarial Claude rounds, both converged clean.

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
- ~~Wire up the `unresolved_critical_defects` readiness count and add
  6 eval cases to reach the 50-case bar~~ **shipped** (2026-07-28): the
  defects ledger CLI + 6 real-shape eval cases landed in `4ae0371`
  (R-004: both bars green), then **2.1.3** hardened the ledger after the
  first external-tool review pass (CodeRabbit CLI, 5 confirmed findings):
  fail-closed shape validation (an object-shaped section previously
  counted as zero and could green the bar), flock + tmp-then-replace
  writes, CLI exit-2 on corrupt ledgers — see CHANGELOG. Automatic
  CodeRabbit review now runs on every PR (`.coderabbit.yaml`, verified
  live on #4).
- ~~Warn on set-but-unconsumed `PXX_*` env vars~~ **shipped in 2.1.4**
  (2026-07-29, PR #5): warn-once typo insurance; git-hook/CI vars
  allowlisted; warn-only. First live invocation surfaced 6 real
  set-but-unconsumed fleet vars from `~/.config/pxx/env` — an allowlist
  knob for deliberately-shared vars is an open follow-up if the noise
  proves unwanted.
- A quickstart subcommand (proposed, does not exist yet): scaffold the
  tutorial sandbox from packaged resources (today the wheel ships neither
  the tutorial nor the setup script).
- Auto-backend probe latency (~1–2 s per invocation with a healthy aider
  installed): cache or probe-on-failure redesign (deferred, documented).
- ~~Detect tool-call-shaped prose~~ (from the R-007 Camelid lane map)
  **shipped in 2.1.4** (2026-07-29, PR #5): `tool_call_prose` event +
  warning, model re-prompted, actionable failure after repeated drops —
  the "describes edits instead of making them" symptom is now
  machine-detectable AND self-healing. The bare-JSON shape was added
  from live evidence: a dogfooded run exhibited the exact failure while
  the detector was being built (qwen2.5-coder:7b answered with raw
  `edit_file` JSON, `diff_lines=0`).
- ~~Reviewer timeout on real-repo diffs~~ **shipped in 2.1.2**
  (2026-07-27, ~24 h find-to-ship): `PXX_REVIEW_TIMEOUT` with
  `PXX_NATIVE_TIMEOUT` fallback, never-blank failure reasons, and the
  reviewer context-overflow message — see CHANGELOG. ~~Remaining parity
  note from its review (non-blocking): malformed timeout env values fall
  back silently~~ resolved in **2.1.4**: presence-wins semantics (the
  review knob never silently falls through to the native one — production
  had the eval corpus's own or-falsy trap), warnings on malformed values,
  non-finite values rejected, and the env read moved to the config
  boundary.

## Later

- Model-backed boundary roles (today's are deterministic).
- Cross-repo knowledge federation.

## Release story

2.0.0 **replaces** the 1.3.x line on the `pxx-orchestrator` PyPI name
(requires-python >= 3.11; the aider backend is an optional, python-gated
extra, so the core installs and imports cleanly on 3.13 — no 1.3.3-style
fallback hole). The 1.x line ends at v1.3.3; 2.0.0 publishes as rc first
(2.0.0rc1 → soak → 2.0.0).
