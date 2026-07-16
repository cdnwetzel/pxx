# Phase 8: Infrastructure Hardening & Scaling
> Backlog ID: 001

## Overview

**Goal:** Fix critical infrastructure gaps discovered in v1.0.0 code review. Enable confident scaling to 100k+ observations and multi-machine deployments.

**Status:** `done` (2026-07-16 audit) — Tier 1 (8.1–8.3) complete and verified
in code (`vector_index.py` save/load/remove_embedding, BM25 index persistence,
`test_concurrency.py`); the checkbox lists below predate completion and were
not ticked retroactively. Everything else this plan pointed at has its own
backlog ID: Tier 2/3 is 002, 8.5 confidence scoring is 003 — this plan's own
scope is exhausted.

**Timeline:** 4-6 weeks (Tier 1), then 6-8 weeks (Tier 2)

**Key Finding:** v1.0 is production-ready for local single-user use but has three architectural gaps blocking scale:
1. HNSW vector index cannot delete observations (unbounded memory growth)
2. BM25 re-indexes every search (O(n) performance penalty)
3. No concurrency tests (unsafe under multi-session load)

**Risk if skipped:** Teams using pxx at scale will hit OOM (vector index fills with dead observations) and search latency (O(n) search on 100k+ obs).

---

## Tier 1: Foundational (Phases 8.1-8.3)

**Objective:** Fix architectural gaps. Unblock scale to 100k+ observations.

### Phase 8.1: HNSW Persistence & Deletion

**What:** Persist HNSW vector index to disk. Rebuild on schema change, not startup.

**Why:** 
- Current HNSW lives in memory. Rebuild from 100k embeddings on startup takes 5+ seconds.
- HNSW doesn't support deletion. Expired observations linger forever → unbounded memory.
- Multi-machine: if service restarts, index lost.

**Tasks:**
- [ ] Add `vector_index.save(path)` and `load(path)` methods
- [ ] Serialize to `.pxx/vector.hnswlib` on shutdown
- [ ] Load on startup if exists and schema matches
- [ ] Rebuild if schema/embedding-dim mismatch
- [ ] Add tests: serialize/deserialize, schema migration
- [ ] Update cleanup: rebuild after deleting 1000+ observations

**Metrics:**
- Startup time: 5s → 0.5s (for 50k observations)
- Memory: stable instead of growing

**Effort:** 3-4 days

**Status:** `done` — `feat(8.1)`; see `agentmemory_pkg/vector_index.py`, `tests/test_vector_index.py`.

### Phase 8.2: Persistent BM25 Indexing

**What:** Store BM25 term frequencies + IDF in SQLite. Rebuild only on observation changes.

**Why:**
- Current `search.py` re-indexes all observations on every query
- For 100+ observations: O(n) term-frequency recomputation per search
- Persistent index: O(log n) term lookup, instant reuse

**Tasks:**
- [ ] Create `bm25_index` table in SQLite: (term, doc_freq, idf)
- [ ] Build index on observation insert/delete
- [ ] Lazy-load index on SearchEngine startup
- [ ] Update `BM25Ranker` to use cached index instead of recomputing
- [ ] Add tests: index correctness, incremental updates
- [ ] Benchmark: 100 obs search latency before/after

**Metrics:**
- Search time: stable O(log n) instead of O(n)
- Latency: 10k obs: 50ms → 2ms

**Effort:** 2-3 days

**Status:** `done` — `feat(8.2)`; see `agentmemory_pkg/search.py`, `tests/test_bm25_persistence.py`.

### Phase 8.3: Concurrency & Scale Testing

**What:** Add comprehensive concurrency tests + performance baselines.

**Why:**
- Zero tests for concurrent writes (two aider sessions simultaneously)
- HNSW rebuild under load untested
- No performance baselines (how slow is 10k? 100k?)
- Embeddings model loaded without locks (race condition)

**Tasks:**
- [ ] Add concurrent-write tests: two threads storing observations simultaneously
- [ ] Add HNSW rebuild test under concurrent search
- [ ] Add performance baselines: search latency at 1k, 10k, 100k observations
- [ ] Add threading locks to embeddings model lazy-load
- [ ] Add SQLite concurrent-access tests (WAL mode, timeout handling)
- [ ] Document limits: max concurrent sessions, max observations

**Metrics:**
- Concurrency: safe up to N simultaneous sessions (measure + document)
- Performance: latency curve at scale (1k, 10k, 100k)

**Effort:** 2-3 days

**Status:** `done` — `feat(8.3)`; see `tests/test_concurrency.py`.

---

## Tier 2: Feature Expansion (Phases 8.4-8.6)

**Objective:** Add capabilities unlocked by Tier 1. Enable multi-machine + confidence scoring.

### Phase 8.4: Multi-modal Observations

**What:** Extend observations beyond text. Add AST-based code structure + test results.

**Why:**
- Current: observations are text only (from diffs)
- Better: "modified function X in module Y, added test T, caused regression R"
- Enables richer context injection

**Tasks:**
- [ ] Parse diffs to extract function/class boundaries (via AST)
- [ ] Record what changed: function name, line range, change type (add/modify/delete)
- [ ] Capture test results: which tests pass/fail post-edit
- [ ] Update tool_capture.py to include structured metadata
- [ ] Store in observations: `metadata: {functions: [...], tests: [...], regressions: [...]}`

**Effort:** 5-7 days

**Status:** `done` — structured metadata (functions/classes/tests) lands in `pxx/tool_capture.py` (`parse_code_changes`, `extract_test_names`).

### Phase 8.5: Observation Confidence Scoring

**What:** Score observations by recency, access frequency, and relevance.

**Why:**
- Current: all observations equal. Better: prioritize high-quality ones.
- Old observations (90+ days) less useful; frequently-recalled ones more valuable
- Impact: better context injection (use high-confidence obs first)

**Tasks:**
- [ ] Add `confidence_score` field to Observation
- [ ] Compute: `(recency_decay * 0.3) + (access_frequency * 0.3) + (relevance * 0.4)`
- [ ] Sort search results by confidence (not just relevance)
- [ ] Update inject endpoint to return top-K by confidence
- [ ] Update archive: only archive observations below confidence threshold
- [ ] Tests: verify confidence decays over time

**Effort:** 2-3 days

**Status:** `planned` — design complete, not yet implemented (no `confidence_score` field yet). See `phase-8.5-confidence-scoring.md`.

### Phase 8.6: Multi-Machine Collaboration

**What:** Enable observation sync across machines (Studio + Neo).

**Why:**
- Current: per-machine in `~/.pxx/memory.db`. Observations don't sync.
- User works on two machines; observations from Neo don't reach Studio.
- Already structured (API layer ready); just needs auth + replication.

**Tasks:**
- [ ] Add auth to agentmemory (API key or OAuth)
- [ ] Add replication: agentmemory syncs new observations to central server
- [ ] Configure: `AGENTMEMORY_SYNC_URL` for central server
- [ ] Local cache: observe locally, sync periodically to central
- [ ] Merge strategy: handle conflicting observations (timestamp wins)
- [ ] Tests: concurrent writes to local + central, sync correctness

**Effort:** 5-7 days

**Status:** `planned`

---

## Tier 3: Intelligence Layer (Phases 8.7-8.8)

**Objective:** Let memory make aider smarter automatically.

### Phase 8.7: Memory-Guided Aider

**What:** Auto-select slash commands based on injected memory.

**Why:**
- Current: memory injects observations, aider unaware
- Better: when memory recalls "project uses ruff", auto-offer `/load typecheck`
- Impact: aider contextually smarter without user intervention

**Tasks:**
- [ ] Extend context injection to include "recommended commands"
- [ ] Parse injected observations for tool patterns (pytest, ruff, mypy, etc.)
- [ ] Build command map: pattern → `/load` command
- [ ] Add to aider prompt: "suggested tools based on recent work: ..."
- [ ] Tests: verify correct tools offered for observed patterns

**Effort:** 2-3 days

**Status:** `planned`

### Phase 8.8: Adaptive Observation Decay

**What:** Exponential decay + adaptive pruning based on project velocity.

**Why:**
- Current: fixed 90-day TTL. One-size-fits-all doesn't work.
- Fast-moving projects: need shorter TTL. Stable projects: keep longer history.
- Impact: memory self-tunes to project velocity

**Tasks:**
- [ ] Add `importance_score` to observations (exponential decay over time)
- [ ] Measure project velocity: changes per day over last N days
- [ ] Adaptive TTL: fast projects (5+ changes/day) → 30 days; slow → 180 days
- [ ] Prune: delete observations below importance threshold
- [ ] Tests: verify decay rate, adaptive TTL calculation

**Effort:** 2-3 days

**Status:** `planned`

---

## Success Criteria

### Tier 1 Complete (Phases 8.1-8.3) ✅
- [x] HNSW persists to disk + rebuilds on schema change
- [x] BM25 indexing is persistent + O(log n) lookups
- [x] 20+ concurrency tests pass (concurrent writes, rebuilds, searches)
- [x] Performance baselines established (1k, 10k, 100k observations)
- [x] Max concurrent sessions documented

### Tier 2 Complete (Phases 8.4-8.6)
- [ ] Observations include AST metadata + test results
- [ ] Confidence scoring improves result ranking
- [ ] Multi-machine sync works (Neo ↔ Studio)
- [ ] Central server auth + replication tested

### Tier 3 Complete (Phases 8.7-8.8)
- [ ] Aider auto-offered context-appropriate commands
- [ ] Observation decay adapts to project velocity
- [ ] All features tested + documented

---

## Dependencies

**Blocked by:** Nothing (v1.0.0 complete, code review done)

**Blocks:** 
- Phase 9 (Closed-loop autonomy — `pxx --loop`; see `phase-9-loop.md`). Soft
  dependency only: 9.4 per-round learning benefits from 8.4 ✅ / 8.5.
- Release v1.1.0

---

## Risk Assessment

**High:**
- HNSW deletion: if skipped, production deployments will OOM after 6 months
- Concurrency: unsafe under multi-session load without tests

**Medium:**
- BM25 persistence: not critical, but search latency grows with observations
- Multi-machine: blocks team adoption

**Low:**
- Confidence scoring, adaptive decay: nice-to-have improvements

---

## Estimated Timeline

| Phase | Effort | Start | End | Blocker? |
|---|---|---|---|---|
| 8.1 HNSW | 3-4d | Week 1 | Week 2 | Critical |
| 8.2 BM25 | 2-3d | Week 2 | Week 2 | High |
| 8.3 Tests | 2-3d | Week 2-3 | Week 3 | High |
| **Tier 1 Complete** | **7-10d** | **Week 1** | **Week 3** | **Critical** |
| 8.4 Multi-modal | 5-7d | Week 4 | Week 5 | Medium |
| 8.5 Confidence | 2-3d | Week 5 | Week 6 | Medium |
| 8.6 Multi-machine | 5-7d | Week 6 | Week 7-8 | Medium |
| **Tier 2 Complete** | **12-17d** | **Week 4** | **Week 8** | **Medium** |
| 8.7 Aider guidance | 2-3d | Week 9 | Week 9 | Low |
| 8.8 Adaptive decay | 2-3d | Week 9-10 | Week 10 | Low |
| **Tier 3 Complete** | **4-6d** | **Week 9** | **Week 10** | **Low** |

**Total Phase 8:** 4-6 weeks (Tier 1), then 6-8 weeks (Tier 2-3)

---

## Next Steps

1. **Code review baseline:** Use `code-review-v1-0-0.md` findings
2. **Team alignment:** Present Tier 1 blockers (HNSW, BM25, concurrency)
3. **Execution:** Start Phase 8.1 (HNSW persistence)
4. **Validation:** Multi-agent reviews (Gemini, Codex, Copilot) inform prioritization

---

## Notes

- Tier 1 is **critical for scaling**. Skip at your peril (production OOM at 100k+ observations).
- Tier 2 enables **team adoption** (multi-machine sync).
- Tier 3 adds **intelligence** (aider becomes context-aware).
- Phase review each tier before moving to next. Release v1.1.0 after Tier 1+2.
