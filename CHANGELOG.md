# Changelog

All notable changes to pxx and its ecosystem across development phases.

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

- **Install:** `pip install pxx[all]` or see `docs/INSTALL.md`
- **Deploy:** `docs/DEPLOY.md` for production setup
- **Examples:** `docs/EXAMPLES.md` for real-world workflows
- **API:** `docs/API.md` for complete endpoint reference
- **Issues:** https://github.com/cdnwetzel/pxx/issues

---

## Contributing

pxx development is documented in `CLAUDE.md` (aider/Claude-specific guidance) and `CONVENTIONS.md` (code style).

## License

MIT
