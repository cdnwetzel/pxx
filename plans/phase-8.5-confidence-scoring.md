# Phase 8.5: Observation Confidence Scoring
> Backlog ID: 003

> **Prerequisite:** Phase 8.4 (Metadata schema) — COMPLETE ✅

---

## Overview

Phase 8.4 added structured metadata to observations (functions, files, tests). **Phase 8.5 makes
observations self-ranking:** each observation gets a persistent `confidence_score` reflecting how
useful it is likely to be, based on:

1. **Recency** (30% weight) — observations decay over 90 days
2. **Access frequency** (30% weight) — frequently-recalled observations score higher
3. **Relevance** (40% weight) — how well it ranked when retrieved via search

This enables:
- **Search prioritization** — high-confidence observations bubble up in results
- **Smart archival** — high-confidence observations survive longer; low-confidence ones expire sooner
- **20% search ranking improvement** — confidence multiplier weights results differently

---

## Confidence Formula

```
confidence = (recency_decay * 0.3) + (access_frequency * 0.3) + (relevance * 0.4)

recency_decay    = max(0.0, 1.0 - (days_since_creation / 90))  # decays to 0 at 90 days
access_frequency = min(access_count / 10, 1.0)                  # capped at 10 accesses
relevance        = search_rank_score                             # 0.5 at creation, actual score on access
```

**On creation:** `confidence_score` calculated with `relevance = 0.5` (neutral prior).  
**On access:** `confidence_score` recalculated with actual search rank, stored to DB.  
**Score range:** [0.0, 1.0] (rounded to 4 decimals).

---

## Tasks

### 1. Data Model & DB Layer (2 days)

**`storage.py` changes:**

- Add `confidence_score: float | None = None` field to `Observation` dataclass
- Add `confidence_score REAL DEFAULT NULL` column to observations table (with migration)
- Update `_init_db()` with column migration using existing try/ALTER pattern
- Add `_compute_confidence(created_at, access_count, relevance=0.5) -> float` helper
- Update `store()` signature: add `confidence_score: float | None = None` parameter
- Update `store()` to call `_compute_confidence()` if not provided, then INSERT
- Update all 4 SELECT/constructor sites to read and populate `confidence_score`:
  - `_get_by_id()` (line 170)
  - `get_by_project()` (line 205)
  - `_rows_to_observations()` (line 414)
  - `cleanup_expired()` (line 483) — can omit if priority is tight
- Add `update_confidence(obs_id: str, relevance: float) -> None` method
- Add `get_by_confidence(project: str, min_score: float, max_score: float) -> list[Observation]` method

**Success metrics:**
- All 4 existing SELECT paths return observations with non-None `confidence_score`
- New observations have `confidence_score` between 0.4-0.6 (neutral prior)
- DB migration runs without errors on existing databases

### 2. Search Integration (1 day)

**`search.py` changes:**

- In `SearchEngine._hybrid_search()` (line 161), after computing `0.4 * bm25 + 0.6 * vector`:
  - Multiply by confidence weight: `(0.7 + 0.3 * obs.confidence_score)` or `0.85` if None
  - Effect: confidence=1.0 gives 1.0× multiplier, confidence=0.0 gives 0.7× (floor at 0.7)
- After returning results, call `store.update_confidence(obs.id, raw_score)` for each result
- Update `SearchEngine.search()` return type to include `confidence_score` in result dicts

**Success metrics:**
- High-confidence observations (>0.7) rank higher than identical low-confidence (<0.3) in results
- Search results include `confidence_score` field
- No latency regression (update_confidence is fire-and-forget)

### 3. Cleanup Enhancement (1 day)

**`cleanup.py` changes:**

- Add confidence-based archival logic (second-pass query):
  ```sql
  DELETE FROM observations
  WHERE confidence_score < 0.3
  AND expires_at IS NOT NULL AND expires_at < datetime('now', '+7 days')
  ```
  Archives low-confidence observations 7 days before TTL expiry.

- Add confidence-based TTL extension:
  When an observation is accessed (via search), if `confidence_score > 0.7`:
  - Extend `expires_at` by 30 days (cap at 365 days from creation)
  
- Add `log_confidence_distribution()` method:
  - Logs bucket counts: (<0.3, 0.3-0.7, >0.7)
  - Called on cleanup completion for observability

**Success metrics:**
- High-confidence (>0.7) observations kept indefinitely (until access stops or confidence drops)
- Low-confidence (<0.3) observations removed earlier than TTL
- Confidence distribution logged for debugging

### 4. API Surface (`main.py`) (1 day)

**`main.py` changes:**

- `POST /observations` (line 74): Accept `confidence_score` in request body, pass to `store.store()`, return in response
- `POST /search` (line 100): Include `confidence_score` in each result dict
- `POST /inject` (line 143): Include `confidence_score` in each result dict
- No new routes needed

**Success metrics:**
- Search responses include `confidence_score` in each observation dict
- No breaking changes to existing API surface (new field only)

### 5. Tests (1 day)

**New file: `tests/test_confidence.py`** (11 test cases)

Follow pattern from `test_retention.py`: inline `tempfile.TemporaryDirectory()`, raw sqlite3 for
timestamp manipulation, simple module-level test functions.

| Test | What it verifies |
|---|---|
| `test_confidence_formula_correctness` | Unit test formula with known inputs |
| `test_confidence_on_creation` | New obs has confidence ≈ 0.5 |
| `test_confidence_decays_with_age` | Backdate created_at 60 days → confidence drops |
| `test_confidence_increases_with_access` | Increment access_count → confidence rises |
| `test_confidence_stored_in_db` | `_get_by_id()` returns non-None confidence_score |
| `test_update_confidence_after_search` | `update_confidence()` changes DB value |
| `test_confidence_in_search_results` | Search result dicts include confidence_score key |
| `test_high_confidence_survives_cleanup` | obs.confidence > 0.7, near-TTL → NOT cleaned up |
| `test_low_confidence_expires_early` | obs.confidence < 0.3, near-TTL → cleaned up |
| `test_get_by_confidence_range` | `get_by_confidence("proj", 0.5, 1.0)` filters correctly |
| `test_confidence_ordering` | High-confidence obs ranks higher than low in search |

**Success metrics:**
- 11/11 tests passing
- Coverage: formula, creation, decay, access, persistence, search, cleanup
- No regressions in `test_retention.py` or `test_metadata.py`

---

## Critical Files

| File | Changes |
|---|---|
| `services/agentmemory/agentmemory_pkg/storage.py` | Dataclass field, DB migration, compute/update/get-by-confidence methods, all SELECT sites |
| `services/agentmemory/agentmemory_pkg/search.py` | Confidence weighting in `_hybrid_search()`, call `update_confidence()` |
| `services/agentmemory/agentmemory_pkg/cleanup.py` | Second-pass query for low-confidence, TTL extension, distribution logging |
| `services/agentmemory/agentmemory_pkg/main.py` | Accept/return confidence_score in POST `/observations` and `/search` |
| `services/agentmemory/tests/test_confidence.py` | NEW — 11 test cases |

---

## Key Naming Note

**Do NOT confuse these fields:**
- **`Observation.score`** (line 20, existing) — transient search-rank score, set at query time, never persisted
- **`Observation.confidence_score`** (new) — persistent confidence metric stored in DB, calculated on creation and access

They coexist. `score` is used by search to carry result ranking; `confidence_score` is metadata
about the observation's long-term usefulness.

---

## Using pxx --self-improve for This Phase

**Yes, use it for design assistance, but not for autonomous multi-file edits.**

Lessons from Phase 8.4 dogfooding:
- ✅ `pxx --self-improve` works well for designing individual methods before writing them
- ✅ Use `/add <file>` to load files, then ask for design of one method at a time
- ❌ Don't pass long prompts as CLI args (aider treats them as filenames)
- ❌ Don't rely on aider for atomic multi-file edits (causes SEARCH/REPLACE failures)
- ❌ Use `PXX_PRECOMMIT_SKIP=1` to commit WIP if ruff blocks you

**Recommended workflow:**

```bash
# Session 1: design confidence calculation
pxx --self-improve
# In aider: add storage.py
# Ask: "Design _compute_confidence() with this formula: [paste]"
# Take the code, implement manually

# Session 2: design search integration
pxx --self-improve
# Add search.py
# Ask: "Show me how to factor confidence into _hybrid_search()"

# Session 3: generate test cases
pxx --self-improve
# Ask: "Generate 11 test cases following test_retention.py pattern"
```

**Do NOT use `pxx --self-fix`** — concurrent multi-file edits will produce SEARCH/REPLACE
conflicts.

---

## Verification

```bash
# 1. DB migration
python services/agentmemory/agentmemory_pkg/migrations/add_confidence_column.py
sqlite3 ~/.pxx/memory.db ".schema observations" | grep confidence_score

# 2. Run new tests
cd services/agentmemory
./.venv/bin/python -m pytest tests/test_confidence.py -v

# 3. Regression test
pxx --self-test 2>&1 | tail -5

# 4. Smoke test
curl -s -X POST http://127.0.0.1:3111/observations \
  -H "Content-Type: application/json" \
  -d '{"project":"test","content":"test observation"}' \
  | python3 -m json.tool | grep confidence_score

curl -s -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{"project":"test","query":"test"}' \
  | python3 -m json.tool | grep confidence_score
```

---

## Success Criteria

- [ ] All 5 data files updated without breaking existing tests
- [ ] 11 new tests passing
- [ ] `pxx --self-test` passes (no regressions)
- [ ] Search results include `confidence_score` field
- [ ] High-confidence (>0.7) observations rank higher than low (<0.3)
- [ ] DB migration runs cleanly on existing databases
- [ ] Smoke tests show confidence_score in API responses

---

## Timeline

- **Design phase:** 1 day (this doc + dogfooding design sessions)
- **Implementation phase:** 5 days (1+1+1+1+1)
- **Testing & integration:** 2 days
- **Total:** ~1 week

---

## Next Phase (8.6)

Phase 8.6 builds on confidence scoring:
- **Multi-Machine Collaboration** — replicate observations + confidence across Studio ↔ Neo
- API key auth + replication workers + conflict resolution
- Local cache with confidence-based prioritization

---

**Status:** `planned`  
**Backlog ID:** 8.5  
**Blocked by:** None (Phase 8.4 complete ✅)  
**Blocks:** Phase 8.6, Phase 8.7, Phase 8.8  
