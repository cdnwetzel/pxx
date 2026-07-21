# Changelog

All notable changes to pxx and its ecosystem across development phases.

## Unreleased

### Security

- pxx now refuses to launch aider's architect mode (`--architect`,
  `--chat-mode architect`, `--edit-format architect` — and aider's
  unambiguous argparse abbreviations of them, down to `--ar` / `--chat-m` /
  `--edit-`) — aider ≤0.86.2 is affected by PYSEC-2026-2335
  (CVE-2026-10175): architect mode auto-applies its editor stage, so
  prompt-injected content can become committed code. pxx sessions run
  ask/diff only; the refusal lifts when a fixed aider release ships.
- Dispositioned both live advisories against the pinned aider 0.86.2
  (2335: denied + documented; 2336 `/web` SSRF: accepted with a documented
  egress compensating control, fix-cherry-pick path for credential-bearing
  deploys) — see SECURITY.md and docs/security-advisory-dispositions.md.

### Corrected

- Corrected Python 3.13 installation guidance: current pip refuses the
  dependency set rather than silently selecting a 1.2.x release in a clean
  resolver test. Forced incompatible installs may still fail on `audioop`.
- Retracted unverified HNSW speedup and recall claims from current-facing
  documentation. Production index population and reproducible scale/recall
  benchmarks remain pending; the historical entries below are preserved as
  the claims made by their releases.
- Made `pxx --help` and `pxx --version` local metadata operations and excluded
  `SKILL_TEMPLATE.md` from the slash-command listing.
- Demoted the optional services to their implemented state. 9router is now
  documented as an experimental single-upstream proxy: fallback chains,
  token/cost metrics, and `/v1/status` / `/v1/usage` exist only as unwired
  modules, not in the running service. The memory documentation now states
  that runtime capture and automatic session injection are not wired — what
  `--with-memory` does today is start the service and store a post-session
  git-diff summary.
- Removed documentation for settings and commands that do not exist
  (`AGENTMEMORY_URL`, `agentmemory server --port`, `pxx --list-skills`,
  `pxx --no-memory`, `scripts/doctor.sh`, router host/port settings).

### Changed

- `pxx --review` now preflights the configured review backend and fails fast
  with a clear message when it is unusable, instead of failing partway
  through the review pass.

## [1.3.3.post1] — 2026-07-19

Docs post-release (PEP 440 `.post1` — no code change).

- **Python 3.13 install-fallback warning** surfaced on the PyPI project page
  (README `long_description`) and in `docs/INSTALL.md`: on 3.13+ a plain
  `pip install pxx-orchestrator` silently falls back to an old 1.2.x build that
  crashes at import (`audioop`, PEP 594); install against 3.11/3.12 explicitly.
  Corrected the earlier "installers auto-select" wording, which held only for
  `uv tool`/`pipx`, not a plain `pip install` on a 3.13 interpreter. This is a
  reader-facing mitigation, not a cure for the silent auto-fallback.

## [1.3.3] — 2026-07-19

Security/privacy re-freeze. No behavioral change to orchestration.

### Security / privacy

- Removed inadvertently-committed personal data (stray session-dump artifacts
  under `review/` that reached the public repo).
- De-identified all fleet references across the tracked tree — hostnames,
  mirror handles, and firm-linked model ids replaced with generic placeholders
  (`vllm-host-1`, `workstation`, `gpu-node-1`, `inference-node`, `coder-lora`).
- Extended the public-content gate to run a **full-tree** scan
  (`pxx --check --all-files`) in CI/release, not just the shipped-wheel subset,
  so docs/plans/deploy/config are covered and this can't recur.

### Fixed

- `doctor.py`: mirror default is now just `origin` (override via
  `PXX_MIRROR_REMOTES`); `--doctor` no longer exits non-zero when no mirror is
  reachable/present (N/A ≠ out-of-sync).
- `_git.is_dirty()` now fails **closed** — an errored/unknown git status is
  treated as dirty, so `--edit` never starts an unstashed session on a real
  dirty tree.
- `safety.create_tag()` no longer silently returns after stashing on a
  same-second tag collision — it prints a `git stash list` recovery pointer.
- `safety._has_unmerged_autonomous_commits()` derives the current branch's
  upstream (`@{upstream}`) instead of hardcoding `origin/main`.
- Docs: `pip install pxx[all]` → `pip install pxx-orchestrator` (CHANGELOG,
  REVIEWER_PROMPTS).

## [1.3.2] — 2026-07-18

Privacy-gate hardening + upgrade robustness. No change to orchestration behavior.

### Fixed

- **Release gate now proves it was armed.** The public-content scanner catches
  bare fleet hostnames only via an untracked denylist; in CI that denylist is
  absent, so `pxx --check --shipped` silently reported clean while a hostname
  shipped (it did, in 1.3.0/1.3.1). Now a shipped/audit scan with **0 denylist
  patterns soft-fails loudly** (`--allow-empty-denylist` is an explicit,
  still-noisy opt-out). `release.yml` arms the denylist from a secret and runs
  the gate **without** the opt-out; `ci.yml` opts out (fork-PR-safe) so pushes
  aren't blocked but still warn.
- **`pxx --upgrade` no longer tracebacks** when `uv`/`pipx` isn't on PATH — it
  prints a one-line instruction and exits non-zero.
- **Fleet hostnames scrubbed from the shipped distribution** — a docstring in
  `pxx/protected_paths.py`, comments in `pxx/endpoints.py`, and test fixtures
  named real hosts and shipped in the wheel/sdist. All replaced with
  placeholders/generic phrasing; a regression test scans the whole shipped scope
  under the denylist so it can't recur.

### Changed

- Plan D2.4 amended so plan and code agree: invoking `--upgrade` is itself the
  consent (a deliberate top-level verb like `--doctor`), no separate confirm.

### Tests

- `upgrade_main` exit-code contracts (editable/offline/up-to-date/not-on-PATH),
  gate coverage-disabled behavior, workflow-parity, and shipped-scope hostname
  regression. 976 passed / 12 skipped.

## [1.3.1] — 2026-07-17

Install & upgrade UX, plus a safety-spine hardening. No change to orchestration
behavior — this is a patch that makes a clean install work by default, tells the
truth about how to install/upgrade, and closes one fail-open escape.

### Fixed

- **Install works on a default interpreter.** `requires-python` was capped
  `>=3.11,<3.13`. Without the ceiling, `uv tool install` / `pip install`
  auto-selected Python 3.13+, where aider crashes at import — PEP 594 removed
  the `audioop` stdlib module that `aider-chat`'s `pydub` needs (on 3.14 it
  surfaces earlier as a raw `ResolutionImpossible`). The bound tracks
  `aider-chat`'s own `<3.13` and is revisited under the aider upgrade
  discipline. Pinned by `tests/test_packaging.py`.
- **content-candidate safety spine: rename-collapse escape closed.**
  `changed_paths` now passes `--no-renames` to both git reads, so a
  `git mv <protected-grader> <allowed-target>` can no longer collapse the
  protected DELETION into its destination and slip past
  `verify_only_touched_target` (a fail-open hole in the 1.3.0 spine). Regression
  test fails before the flag, passes after.

### Added

- **`pxx --upgrade`** (alias `--update`) — self-updates the installed
  distribution: detects `uv tool` / `pipx` / `pip`, reports current → latest
  from PyPI, and refuses on an editable checkout (use `git pull` there).
  Offline-safe. Pure-function tests, no network in the suite.

### Docs

- Install docs corrected: canonical name `pxx-orchestrator` (not `pxx`, an
  unrelated project); no fictional `pxx[all|memory|router]` extras; optional
  services (`agentmemory`, `9router`) are source-installed, not PyPI packages.
- Python range stated as **3.11 or 3.12** with the `<3.13` rationale, plus a
  troubleshooting entry mapping the too-new-Python symptoms to the fix.
- An "Upgrading" section (per install method) in README and INSTALL.
- Scrubbed fleet hostnames from `docs/INSTALL.md` and `docs/DEPLOY.md`
  (placeholders only — public-repo privacy contract).

## [1.3.0] — 2026-07-17

**Content change-class candidates.** The self-improvement system gains its
first *file-mutating* candidate class: proposals that rewrite the behavior
text steering the agent (prompt & command files), evaluated and human-promoted
— never auto-applied. Built and hardened end to end through the coder⇄reviewer
review protocol; every fix below is proven fail-closed by a named test.

### Added

- **Content change-class candidates** (`pxx/content_candidates.py`). A content
  candidate rewrites behavior text under `pxx/prompts/` or `pxx/commands/`
  only. It is validated, applied, and verified through ONE path derivation
  (review requirement #1), and the live-eval envelope evaluates it in an
  isolated clone: `evaluate_content_candidate` does clean-clone → apply → run
  → verify → restore. Human-gated by construction — the generator can never
  apply, commit, or promote its own change.

### Hardened

- **Requirement-#1 single derivation.** Validate-path, write-path, and the
  post-write verify-path all derive from the same `canonical_repo_path`; the
  post-write check reads the ACTUAL changed paths from git, not the
  candidate's claim.
- **P1 — committed-escape visibility.** Verify diffs against the pre-write
  HEAD (`apply` returns it), so an escape the live sweep already auto-committed
  is still caught, not read as a clean tree.
- **P2 — symlink write rejection.** `apply` refuses a symlinked or redirected
  destination before writing, so a planted link can't land the write on a
  protected file.
- **P3 — case-preserving write path.** `canonical_repo_path` no longer
  casefolds the path it writes to (`System.md` stayed `System.md` on
  case-sensitive filesystems); casefolding happens only at comparison.
- **P4 — robust porcelain parsing.** `git status --porcelain -z` (NUL-split)
  removes C-quoting and rename-split ambiguity for paths with spaces or
  non-ASCII bytes.
- **G1 — positive verification.** Verify requires the declared target to
  APPEAR in the changed set; an empty or target-absent set is a violation, not
  a vacuous pass — closing the case where a valid-but-wrong `base_sha` yields
  an empty diff.
- **G2/G3 — live-eval envelope.** The envelope threads `apply`'s own
  `base_sha` into verify (no re-derivation) and asserts the fixture is clean
  before apply (a dirty tree can mask a real escape), failing loud otherwise.

## [1.2.2] — 2026-07-17

**More fail-closed hardening (review rounds 3–4) + the candidate/promotion
chain.** Continues 1.2.1's theme: gates that stated a guarantee they didn't
enforce, all now proven fail-closed with tests.

### Fixed

- **Promotion hard gate is now absolute.** An adversarial-containment
  regression can no longer be promoted by a `human_override` — overriding a
  security regression previously took the same one string as overriding a
  lost micro-case. Override now rescues ordinary ineligibility only; a
  hard-gate failure records `override_refused_hard_gate` and stays unpromoted.
- **Promotion comparability is checked by content, not names.** Scorecards
  carry a corpus fingerprint (content hash + case count); `compare()` refuses
  arms scored on different corpora (and a missing fingerprint differs from a
  present one, so a pre-fingerprint baseline is refused with "re-score")
  rather than issuing an authoritative verdict on arms that never ran the
  same test.
- **Tighten-only budget guard fails closed.** A candidate that nulls
  `baseline_value` on a budget field no longer skips the monotonicity check
  and runs a loosened budget; a missing or non-integer baseline now rejects.
- **Pre-commit hook now runs `ruff format --check`** (matching CI), and the
  vendored `services/` subprojects are excluded from the parent repo's ruff
  config — closing the hook/CI drift that let a format-only diff slip past
  locally.

### Added

- **Candidate evaluation** (`pxx --evaluate-candidate <id>`, experimental,
  repo-checkout): runs the live corpus at baseline and under a candidate's
  overlay, then the promotion policy — a promotion verdict in one command,
  human-gated, never auto-applied.
- **Auto-generated candidates** (`pxx --propose --auto`): maps a mined
  weakness to a validated candidate; the integrity validator is the gate even
  for auto-generated proposals.

## [1.2.1] — 2026-07-17

**Fail-closed hardening: gates that passed on silence now fail loud.** Two
independent review passes found five gates whose failure mode was silence
rather than refusal; all are fixed, several verified in the published 1.2.0
wheel.

### Fixed

- **Test oracle blind to pytest ERRORs.** The loop's test gate ran `-rf`
  (FAILED lines only), so an all-ERROR suite (raising fixture, import break)
  parsed to an empty set and read *green* — and in advisory mode that oracle
  is the sole enforcement gate. Now `-rfE` with `^(FAILED|ERROR)` parsing.
- **Review oracle accepted prose as a clean bill.** A local-reviewer reply
  like "The code looks correct." parsed to zero findings → APPROVE (the
  blocking-mode default reviewer hits this routinely). Non-compliant output —
  neither the exact no-findings line nor a parseable F-NNN finding — now
  fails closed.
- **Governance scanner failed open on git error.** A scan that couldn't run
  git returned an empty violation list ("clean"); it now returns an
  error-severity violation and blocks.
- **`pxx --eval` / `--calibrate` green on an empty corpus.** The corpus ships
  only with a repo checkout, so a pip install self-checked zero cases and
  exited 0 — an unconditionally-green gate. Both now fail closed (exit 2)
  with an explicit "no cases found" message.
- **Pre-commit hook shebang portability.** The installer put the pxx-managed
  marker above the shebang, so git ran the hook under `/bin/sh` — bash on
  macOS, dash on Ubuntu (where `set -o pipefail` is illegal). Shebang is now
  line 1; regression-tested.

### Added

- **CI on push/PR** (`.github/workflows/ci.yml`): runs lint + the full suite
  + the shipped-content gate on every push and PR to `main` — previously the
  tests ran only through a skippable local hook.
- **Trust-boundary enforcement**: `.aiderignore` now protects the evaluator,
  gate modules, `evals/`, and their grading tests — the candidate generator
  cannot edit its own grader.
- **Constrained candidate generation (experimental, `pxx --propose`)**: a
  declarative, single-variable, allowlisted behavior proposal with a
  fail-closed integrity validator; validated and persisted, never
  auto-applied. Repo-checkout feature.

## [1.2.0] — 2026-07-17

**The measurement-and-evaluation foundation: pxx now attributes, scores, and
mines its own runs — the base layer of the continuous-self-improvement
roadmap (Phases 11–15, minimum slices).**

### Added — ships in the package, works pip-installed

- **Advisory review mode** (`PXX_REVIEW_MODE=advisory`): the local reviewer's
  verdict is recorded and surfaced but never blocks a run whose deterministic
  gates (tests, lint, scope, regression) are green — resolves the finding
  that no local reviewer both catches defects and stays quiet, so a
  false-positive reviewer could otherwise spin the heal loop. `blocking`
  (default) is unchanged.
- **Introduced-regression gate**: a loop round that fixes its target but
  breaks a previously-passing test can no longer earn APPROVE; such a stop
  terminates as `TEST_REGRESSION`.
- **Behavior identity** (`pxx --manifest`): every run carries an
  `AgentManifest` and a stable `agent_version_id` (versions, models, prompt
  hashes, budgets, review mode) plus a `run_id` threaded through rounds,
  child sessions, and capture. Model ids only — no endpoints or paths.
- **Normalized outcomes + failure taxonomy** (`pxx --runs`): every loop exit
  writes a machine-readable terminal record (19 canonical codes); outcomes
  are projected from the audit stream, never parsed from messages.
- **Verification packets** (`pxx --verify [run-id]`): APPROVE ships evidence
  — baseline/result commits, the deterministic commands run, results, and
  risks — not just a verdict.
- **Experience mining** (`pxx --analyze`): deterministic weakness clustering
  over the run stream (dominant failures, per-agent failure rate, regression
  vs peers), every observation traceable to run ids.
- **Promotion comparison policy** (`pxx --compare a.json b.json`): exact
  case-by-case verdicts with a hard gate on adversarial-containment
  regressions and an on-the-record human-override field.
- **Public-content scanner in governance** (`pxx --check --all-files`,
  `--shipped`): flags private IPs, internal hostnames, home paths, and
  unprotected-service statements; the release workflow gates on the shipped
  file set.

### Added — repo-checkout only (like `--with-router`/`--with-memory`)

- **Evaluation laboratory** (`pxx --eval`, `--eval-live`, `--calibrate`):
  a 30-case corpus (micro/regression/adversarial) with hidden anti-cheat
  checks, a live-agent arm that runs the real loop in disposable worktrees,
  and a reviewer-calibration suite. These read `evals/` and `WORKFLOW.md`,
  which ship only with a repo checkout — not in the pip package.

### Changed

- The review prompt is task-aware (v2): the requested change is marked
  intentional, out-of-scope code is off-limits, and a finding must name a
  concrete failing input — calibration-driven, cut the local reviewer's
  false-positive rate materially.

*Packaging note (unchanged): `config/` and `evals/` ship only with a repo
checkout; the pip-installed CLI's editing/measurement surfaces work, while
the eval/calibration harness is a checkout feature.*

## [1.1.0] — 2026-07-16

**Closed-loop autonomy (`pxx --loop`), sovereign local review, and
multi-endpoint vLLM chains.**

### Added

- **`pxx --loop "<task>" --scope <path>`** (experimental): bounded autonomous
  edit → test → review → heal rounds to a terminal verdict. Fail-closed
  verdict semantics (`NO_REVIEW` on missing/empty review evidence), three
  independent guards (round cap, baseline-failing-set progress, cumulative
  diff budget) plus a wall-clock budget, healing prompts built from the
  actual failing-test list and lint output, per-round audit records, and
  commits tagged `[autonomous]` — the loop never pushes. Live-validated
  2026-07-16 (APPROVE on a genuine task with zero manual intervention).
- **`pxx --review [--heal]`**: standalone review pass; `--heal` runs exactly
  one REVISE round from the findings.
- **Local review backend** (`PXX_REVIEW_BACKEND=local`, the default): the
  session diff is judged by a local OpenAI-compatible model
  (`PXX_REVIEW_URL` / `PXX_REVIEW_MODEL` / `PXX_REVIEW_TIMEOUT`) — sovereign
  by default; `claude` opt-in for supervised runs.
- **Loop safety hardening**: review-backend preflight (the loop refuses to
  start when the reviewer endpoint is unreachable or not serving the
  configured model), empty reviewer output fails closed instead of counting
  as "no findings", and a loop-level scope guard stops the loop fail-closed
  (`OUT_OF_SCOPE`) if any change escapes `--scope` — aider commits bypass
  git hooks, so the loop enforces the boundary itself.
- **Multi-candidate vLLM chains**: `PXX_VLLM_URL` / `PXX_VLLM_MODEL` accept
  comma-separated lists paired positionally (per-endpoint models); probes in
  order, first reachable wins; warns when the model list doesn't pair every
  URL.
- **`PXX_DEBUG=1`**: per-candidate probe-failure logging during endpoint
  detection; detection failure now names every candidate tried.
- **Headless hardening**: when stdin is not a TTY and no consent flag was
  passed, pxx appends `--yes` for aider (with a stderr notice) — one-shot
  `--message` runs, cron, and the loop no longer crash on interactive
  confirms.
- **Cross-session capture**: terminal loop verdicts store a summary
  observation (best-effort, degrades silently when agentmemory is down).

### Changed

- `--no-gitignore` is always passed to aider: ask mode is guaranteed
  read-only — no more silent `.gitignore` mutation.
- The local-review prompt judges the post-change code, not removed lines.
- Model configs (ship with a repo checkout): `openai/Qwen3-Coder` registered
  (28k input / 4k output, diff edit format).

### Fixed

- Scope-aware lint gate: pre-existing format debt outside `--scope` no
  longer deadlocks a loop's APPROVE.
- Endpoint detection retries a vLLM probe once before falling through;
  retired a stale localhost candidate.
- Edit rounds retry once on genuine aider failure (malformed-edit flakiness
  with smaller models).

*Packaging note (unchanged from 1.0.0): `config/` files ship only with a
repo checkout; the pip-installed CLI uses fallback paths and may show a
litellm metadata warning for unregistered models.*

## [1.0.0] — 2026-06-04 Release

**Production-ready: pxx orchestrator with full memory enhancement and advanced search.**

### Phase 6.4: Tool Call Capture
- Extract observations from aider's tool calls (file edits)
- Parse git diffs post-session to identify changes
- Automatically post observations to agentmemory
- Enable feedback loops: previous sessions → future context

**Commit:** 86b5bee

### Phase 6.5: Vector Search with HNSW
- Implement hybrid BM25 (keyword) + vector (semantic) search
- Use sentence-transformers for 384-dim embeddings
- Support approximate nearest neighbor search via HNSW
- Achieve 100x speedup on large datasets (100k+ observations)
- Fallback to brute-force if HNSW unavailable
- 40% keyword weight + 60% semantic weight in hybrid ranking

**Commit:** 007ad3d

### Phase 6.6: Observation Lifecycle with TTL
- Add expires_at field to observations
- Configurable retention per project (default 90 days)
- Background cleanup thread (hourly, configurable)
- Statistics tracking: expired count, space freed, projects affected
- Per-project TTL overrides via API
- Dry-run preview before cleanup

**Features:**
- CleanupManager for background garbage collection
- Storage.cleanup_expired(dry_run) for manual control
- API endpoints: GET /cleanup, POST /cleanup, GET/POST /retention/config
- Environment variables: AGENTMEMORY_RETENTION_DAYS, AGENTMEMORY_CLEANUP_INTERVAL

**Commit:** 451ecd3

### Phase 6.7: Advanced Features
- **A. HNSW Vector Index Optimization**
  - O(log n) similarity search (vs O(n) brute-force)
  - 25-100x speedup depending on dataset size
  - Thread-safe index with graceful fallback
  - Integrated into SearchEngine._hybrid_search()

- **B. Observation Archival**
  - Archive observations before deletion (compliance)
  - JSONL format with full metadata preservation
  - Date-based directory structure (~/.pxx/memory-archive/YYYY-MM/)
  - Archive search, stats, and listing endpoints
  - Auto-integration into cleanup flow

**Features:**
- ArchiveManager for archival operations
- API endpoints: GET /archive/list, /archive/stats, /archive/search
- Complete observation recovery capability
- Audit trail for compliance

**Commit:** f9b96c5

---

## [0.2.0] — Phase 6 (Memory Enhancement) Baseline

**Supervisor mode with memory injection pipeline complete.**

### Phase 6.1: Console Script & Supervisor Mode
- Fixed setuptools entry points (9router→nine-router naming)
- Both services (9router, agentmemory) start cleanly
- Supervisor mode coordinates startup, environment variables, shutdown
- Exponential backoff retry logic for service startup
- Proper cleanup on session exit (SIGINT, error)

**Commit:** 577ba13 (cleanup), 86b5bee (supervisor)

### Phase 6.2: Memory Injection End-to-End
- Observations flow from agentmemory → system prompt
- AiderMemoryObserver thread captures tool calls
- Middleware injects observations into OpenAI-compatible request
- Verified with real aider sessions on pxx codebase
- Full pipeline tested: store → search → inject → aider

**Commit:** b690791

### Phase 6.3: Production Polish
- /forget endpoint for manual observation deletion
- SearchCache layer (LRU, 100x speedup on repeated queries)
- Cache invalidation on all mutation endpoints
- /metrics endpoint for monitoring
- Cache statistics and utilization tracking

**Commit:** d2c5c69 (config), e1601e9 (args), 861b5b3 (naming)

---

## [0.1.0] — Phase 5 (Infrastructure) Baseline

**Two-machine architecture with routing and memory services.**

### Phase 5: Infrastructure Foundation
- 9router service: OpenAI-compatible proxy with request routing
- agentmemory service: BM25-based observation search
- Tier 1 routing: provider fallback chains
- Tier 2+ memory: session memory with /inject endpoint
- Supervisor mode integration point for both services
- Environment variable configuration
- Health checks and lifecycle management

---

## Prior Versions (Phases 1-4)

### Phase 4: Audit & Distillation
- Session audit log (#004) with structured metadata recording
- post-commit hook for core file change notifications
- Launch banner with git diff detection

### Phase 3: Safety & Scope
- Safety tag system (#002) for session rollback capability
- Trusted path gates (#003) for edit-mode path restriction
- Git state sanity checks before edit mode
- Environment isolation for secret management

### Phase 2: Endpoint Detection
- Multi-endpoint probing with timeout strategy
- Fallback from Studio (primary) to local Ollama
- per-machine configuration (PXX_OLLAMA_BASE override)
- Model selection based on endpoint tier

### Phase 1: Orchestration Basics
- aider integration with os.execv handoff
- Ask/edit mode dispatch
- Model inference endpoint detection
- Command-line interface and help system

---

## Known Limitations & Future Work

### Current Limitations
- HNSW doesn't support true deletion (mappings cleaned, data remains)
- Vector search trades ~10% recall for speed
- Archive search uses simple substring (not semantic)
- agentmemory is unauthenticated by design — deploy localhost/trusted-LAN only

### Future Enhancements
- Archive restoration (undelete capability)
- Archive compression (gzip/brotli)
- Long-term archival (S3, cold storage)
- Vector index persistence and serialization
- Semantic archive search (vectors for archived observations)
- agentmemory authentication (OAuth, API keys)
- Advanced retention policies (by project, by age, by size)
- Observation consolidation (merge duplicates)
- Cost tracking and budgeting

---

## Version History Summary

| Version | Date | Phases | Focus | Commits |
|---|---|---|---|---|
| 0.1.0 | 2026-05-15 | 5 | Infrastructure (routing, memory services) | - |
| 0.2.0 | 2026-05-28 | 6.1-6.3 | Memory injection (console scripts, observation flow, polish) | 5 |
| 1.0.0 | 2026-06-04 | 6.4-6.7 | Advanced (tool capture, vector search, TTL, archival) | 4 |

---

## Installation & Support

- **Install:** `pip install pxx-orchestrator` or see `docs/INSTALL.md`
- **Deploy:** `docs/DEPLOY.md` for production setup
- **Examples:** `docs/EXAMPLES.md` for real-world workflows
- **API:** `docs/API.md` for complete endpoint reference
- **Issues:** https://github.com/cdnwetzel/pxx/issues

---

## Contributing

pxx development is documented in `CLAUDE.md` (aider/Claude-specific guidance) and `CONVENTIONS.md` (code style).

## License

MIT
