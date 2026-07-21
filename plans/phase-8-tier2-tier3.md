# Phase 8: Tier 2 & Tier 3 — Feature Expansion & Intelligence Layer
> Backlog ID: 002

> **Prerequisite:** Phase 8 Tier 1 (HNSW persistence, BM25 indexing, concurrency tests) — COMPLETE ✅
>
> **2026-07-16 audit:** 8.4 is `done` (see its section); 8.5 has its own plan
> (backlog 003); 8.6–8.8 have not started and nothing is actively in flight —
> backlog status corrected `in-progress` → `planned`. Whether to invest in the
> Tier 2/3 team/scale features at all is an open direction decision (solo
> usage + the Phase 9 loop have been the actual priority since June); see
> plans/open-items-2026-07-16.md, decision D4. The "Target Release: v1.1.0"
> line below predates the loop work — v1.1.0 is now scoped around Phase 9
> (see CHANGELOG.md), not Tier 2.

---

## Overview

**Tier 2 (Phases 8.4-8.6):** Add capabilities unlocked by solid infrastructure. Enable team adoption and multi-machine workflows.

**Tier 3 (Phases 8.7-8.8):** Intelligence layer. Make memory self-optimizing and automatically guide aider.

**Target Release:** v1.1.0 after Tier 2 completion

**Timeline:** 6-8 weeks (Tier 2: 3-4 weeks, Tier 3: 2-3 weeks)

---

## Tier 2: Feature Expansion (Phases 8.4-8.6)

### Phase 8.4: Multi-modal Observations

**Goal:** Extend observations beyond plain text. Include code structure + test results.

**What's the problem?**
- Current: "edited file X" (text only)
- Better: "modified function foo() in module bar.py, added test test_foo(), caused regression test_baz() to fail"

**Tasks:**

1. **AST-based code structure** (2 days)
   - Parse diffs using `ast` module
   - Extract function/class boundaries
   - Record: function name, line range, change type (add/modify/delete)
   - Handle multiple languages via file extension heuristics

2. **Test result capture** (1.5 days)
   - Run test suite post-aider-session
   - Parse output: which tests pass/fail
   - Detect new failures (regressions)
   - Store in observation metadata

3. **Tool capture enhancement** (1 day)
   - Update `tool_capture.py` to extract structured metadata
   - Store in observations: `metadata: {functions: [...], tests: [...], regressions: [...]}`
   - Make searchable: "which observations modified function X?"

4. **Tests & docs** (0.5 days)
   - Test AST extraction (10+ test cases)
   - Test regression detection
   - Document metadata schema

**Metrics:**
- Observations include structured function/class metadata
- Searchable by "modifications to function X"
- Regression detection accuracy: >95%

**Effort:** 5-7 days

**Status:** `done` — structured metadata (functions/classes via `parse_code_changes`, test names via `extract_test_names`) lands in `pxx/tool_capture.py`. (`phase-8.5-confidence-scoring.md` correctly treats 8.4 as a completed prerequisite.)

---

### Phase 8.5: Observation Confidence Scoring

**Goal:** Score observations by usefulness. Prioritize high-quality ones.

**What's the problem?**
- Current: All observations equal weight
- Better: Observe that "frequently-recalled observations are 10x more useful than old ones"

**Scoring formula:**
```
confidence = (recency_decay * 0.3) + (access_frequency * 0.3) + (relevance * 0.4)

recency_decay = 1.0 - (days_since_creation / 90)  # decay to 0 at 90 days
access_frequency = min(access_count / 10, 1.0)    # cap at 10 accesses
relevance = vector_search_rank / total_results     # how high did it rank?
```

**Tasks:**

1. **Score calculation** (1 day)
   - Add `confidence_score` field to Observation
   - Calculate on observation creation
   - Update on each access
   - Store in database

2. **Search integration** (1 day)
   - Sort results by confidence (not just relevance score)
   - Return confidence in search results
   - Use confidence in hybrid search weighting

3. **Cleanup enhancement** (0.5 days)
   - Only archive observations below confidence threshold
   - Keep high-confidence observations longer
   - Log confidence distribution

4. **Tests** (0.5 days)
   - Test confidence decay over time
   - Test access frequency impact
   - Test archive threshold behavior

**Metrics:**
- High-confidence observations (>0.7) used 5x more often than low (<0.3)
- Retention: keep high-confidence indefinitely, low-confidence at TTL
- Search ranking improved by ~20% (measured by user satisfaction)

**Effort:** 2-3 days

**Status:** `planned`

---

### Phase 8.6: Multi-Machine Collaboration

**Goal:** Enable observation sync across machines (Studio + Neo).

**What's the problem?**
- Current: Each machine has local `~/.pxx/memory.db`
- User works on Neo (M1) and Studio (M4); observations don't sync
- Better: Central observation pool accessible from both machines

**Architecture:**
```
Neo (local)                  Studio (central)
~/.pxx/memory.db   <--sync--->  /opt/agentmemory/obs.db
  |                              |
  +---> local search            +---> shared search
  |                              |
  +---> fallback if sync down    +---> read replica on Neo
```

**Tasks:**

1. **Auth layer** (1.5 days)
   - Add API key auth to agentmemory
   - Token generation/validation
   - Rate limiting per API key
   - Docs for multi-machine setup

2. **Replication** (2 days)
   - Sync worker: periodically push new observations to central
   - Pull worker: fetch new observations from central
   - Merge strategy: timestamp-based (latest wins)
   - Handle conflicts (two machines edit same obs simultaneously)
   - Queue for offline mode (sync when online)

3. **Local cache** (1.5 days)
   - Observe locally, sync asynchronously
   - Read-through cache (local + central)
   - Cache invalidation on remote updates
   - Fallback if central unreachable

4. **Tests & docs** (1 day)
   - Test sync correctness (10 scenarios)
   - Test offline queue behavior
   - Test conflict resolution
   - Deployment guide (Studio central, Neo replica)

**Configuration:**
```bash
# Neo: .pxxrc
[memory]
central_url = http://studio:3111
api_key = <generated>
sync_interval = 300s  # sync every 5 min

# Studio: agentmemory.env
AUTH_ENABLED = true
REPLICATION_ENABLED = true
```

**Metrics:**
- Sync latency: <1s (local), <5s (remote)
- Conflict resolution: 100% deterministic
- Offline queue: survives 24h without sync
- Multi-machine searches work seamlessly

**Effort:** 5-7 days

**Status:** `planned`

---

## Tier 2 Success Criteria

- [ ] Observations include AST metadata (functions, classes, change types)
- [ ] Test results captured (pass/fail, regressions detected)
- [ ] Confidence scoring improves ranking by ~20%
- [ ] High-confidence observations prioritized in archival
- [ ] Multi-machine sync works (Neo ↔ Studio)
- [ ] API key auth enables team deployments
- [ ] Offline queue + merge strategy tested
- [ ] 40+ new tests, all passing
- [ ] Comprehensive deployment guide

**Timeline:** 3-4 weeks

---

## Tier 3: Intelligence Layer (Phases 8.7-8.8)

### Phase 8.7: Memory-Guided Aider

**Goal:** Auto-select slash commands based on injected memory.

**What's the problem?**
- Current: Memory injects observations, aider unaware
- Better: When memory recalls "project uses pytest", auto-offer `/load typecheck`

**How it works:**
```
Observation: "Added tests using pytest framework for model validation"
  ↓
Pattern match: "pytest" (detected)
  ↓
Recommendation: `/load test` (recommended for test-heavy projects)
  ↓
Aider prompt: "Suggested tools based on recent work: /load test"
```

**Tasks:**

1. **Pattern extraction** (1.5 days)
   - Build tool pattern map: pattern → `/load` command
   - Patterns: pytest, ruff, mypy, black, git, docker, etc.
   - Extract from injected observations
   - Confidence scores (how certain the pattern applies?)

2. **Context injection enhancement** (1 day)
   - Extend context injection to include "recommended commands"
   - Format: "Based on recent work in this project, consider: /load typecheck, /load test"
   - Include confidence so aider can weight suggestions

3. **Validation** (1 day)
   - Test pattern detection accuracy (90%+ precision)
   - Test command recommendations
   - Manual review of corner cases

**Metrics:**
- Pattern detection: >90% precision
- Aider uses recommended commands: 70% adoption rate
- Session time: 5-10% faster with guided commands

**Effort:** 2-3 days

**Status:** `planned`

---

### Phase 8.8: Adaptive Observation Decay

**Goal:** TTL self-tunes to project velocity.

**What's the problem?**
- Current: Fixed 90-day TTL (one-size-fits-all)
- Fast-moving projects: 90 days is too long, old code irrelevant
- Slow projects: 90 days not enough, keep institutional knowledge
- Better: Measure velocity, adapt TTL

**How it works:**
```
Measure velocity: changes per day over last 30 days
  ↓
Calculate adaptive TTL:
  1-2 changes/day (slow) → 180-day TTL
  5+ changes/day (fast) → 30-day TTL
  (linear interpolation between)
  ↓
Prune: observations below importance threshold (exponential decay)
```

**Importance formula:**
```
importance = recency_weight * access_weight * code_quality

recency_weight = e^(-days_since_creation / project_half_life)
access_weight = min(access_count / 5, 1.0)
code_quality = (confidence_score from 8.5)

project_half_life = 90 / velocity (adaptive)
```

**Tasks:**

1. **Velocity measurement** (1 day)
   - Track changes per day over last 30 days
   - Categorize: slow (<2), medium (2-5), fast (5+)
   - Update weekly

2. **Adaptive TTL** (1 day)
   - Calculate TTL based on velocity
   - Update observation expiration on cleanup
   - Log TTL changes for debugging

3. **Importance decay** (1 day)
   - Implement exponential decay formula
   - Mark observations below threshold for archival
   - Preserve high-importance indefinitely

4. **Tests** (0.5 days)
   - Test velocity calculation
   - Test adaptive TTL at different velocities
   - Test importance decay curve

**Metrics:**
- Adaptive TTL: 30-180 days depending on velocity
- Importance decay: smooth curve (no cliff edges)
- Archival rate: adapts to project velocity
- Memory usage: stable (independent of TTL)

**Effort:** 2-3 days

**Status:** `planned`

---

## Tier 3 Success Criteria

- [ ] Aider auto-offered context-appropriate commands
- [ ] Pattern detection: >90% precision
- [ ] Command adoption rate: >60%
- [ ] Observation decay adapts to project velocity
- [ ] Importance scores drive archival decisions
- [ ] All features tested + documented
- [ ] 20+ new tests, all passing

**Timeline:** 2-3 weeks

---

## Full Phase 8 Summary (After All Tiers)

**Infrastructure (Tier 1 — COMPLETE ✅):**
- HNSW persistence + deletion
- BM25 persistent indexing
- Concurrency safety + performance baselines

**Features (Tier 2):**
- Multi-modal observations (code structure + tests)
- Confidence scoring (prioritize useful observations)
- Multi-machine sync (Studio ↔ Neo)

**Intelligence (Tier 3):**
- Memory-guided aider (auto-recommended commands)
- Adaptive observation decay (velocity-aware TTL)

**Outcomes:**
- v1.0.0 → v1.1.0 (feature-complete, team-ready)
- Scales to 100k+ observations
- Enables multi-user, multi-machine deployments
- Makes memory intelligent (self-optimizing)

---

## Release Checklist (v1.1.0)

- [ ] All Tier 2 features implemented + tested
- [ ] All Tier 3 features implemented + tested
- [ ] 60+ new tests, all passing
- [ ] Performance benchmarks (Tier 1 validated)
- [ ] Multi-machine deployment guide
- [ ] Team-collaboration examples
- [ ] GitHub release notes
- [ ] PyPI publication ready

---

## Next Steps

1. **Tomorrow:** Start Phase 8.4 (multi-modal observations)
   - AST extraction from diffs
   - Test result capture post-session
   - Update tool_capture.py

2. **Week 2:** Phases 8.5-8.6 (confidence + sync)
3. **Week 3-4:** Phases 8.7-8.8 (intelligence)
4. **Release:** v1.1.0 → PyPI

---

**Status:** Ready to execute
**Estimated completion:** 6-8 weeks (Tier 2 first, then Tier 3)
**Next blocker:** None (Tier 1 complete)
