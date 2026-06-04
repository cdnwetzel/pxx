# Phase 6 Roadmap - Post-Phase 5 Priorities

**Date:** 2026-06-04  
**Phase 5 Status:** ✅ COMPLETE - Memory middleware working, services operational

---

## Current State

**What's Done (Phase 5):**
- ✅ 9router proxy service (OpenAI-compatible)
- ✅ agentmemory service (BM25 storage & search)
- ✅ Memory injection into system prompt
- ✅ Project scoping
- ✅ Endpoint caching
- ✅ pxx supervisor mode integration

**What's Verified:**
- ✅ Memory search working
- ✅ Observations injected into prompt
- ✅ Services start/stop cleanly
- ✅ Logging shows injection happening

**What's Pending:**
- ⚠️ Observation capture (tool calls) - needs aider session testing
- ⚠️ Slash commands (/recall, /remember, /forget) - architectural limitation
- ⚠️ pxx CLI import issues - separate module resolution needed
- ⚠️ /forget endpoint - agentmemory deletion not implemented

---

## Phase 6 Priority Options

### Option A: Observation Capture & Tool Integration (HIGH IMPACT)
**Effort:** 2-3 days | **Impact:** Completes memory lifecycle

Test and enable observation capture when aider calls tools:
1. Run aider with tools enabled
2. Verify tool_calls captured and stored as observations
3. Test tool use recall in subsequent sessions
4. Document tool capture patterns

**Deliverables:**
- Tool call capture verified in real aider sessions
- E2E test showing tool use → storage → retrieval
- Documentation of memory lifecycle

---

### Option B: Slash Command Implementation (MEDIUM IMPACT)
**Effort:** 3-4 days | **Impact:** User-facing memory controls

Implement /recall, /remember, /forget commands:
1. **Challenge:** aider intercepts `/` before HTTP layer
2. **Solutions:**
   - Poll `.aider.chat.history.md` after session for commands
   - Inject commands via stdin before aider startup
   - Build aider plugin (requires aider API)
   - Alternative: Discord-style commands (e.g., `!recall`)

**Deliverables:**
- Slash command handler in pxx
- Post-session observation extraction
- User documentation

---

### Option C: pxx CLI Module Resolution (BLOCKING)
**Effort:** 1 day | **Impact:** Unblocks full integration testing

Fix import errors in pxx/cli.py:
1. Missing modules (_git, audit, drift, governance, etc.)
2. Likely requires:
   - Scanning git history to find missing files
   - Or completing module implementations
   - Or reorganizing package structure

**Deliverables:**
- `pxx --help` works
- `pxx --with-memory` works
- Full end-to-end workflow testable

---

### Option D: Production Polish (STABILITY)
**Effort:** 2-3 days | **Impact:** Production readiness

Performance, reliability, edge cases:
1. Load testing (multiple concurrent aider sessions)
2. Memory persistence across restarts
3. Error handling and graceful degradation
4. Implement /forget (delete observations)
5. Advanced search (vector similarity)
6. Memory pruning/retention policies

**Deliverables:**
- Performance benchmarks
- SLA documentation
- Reliability improvements

---

### Option E: Release & Documentation (SHIPPING)
**Effort:** 1-2 days | **Impact:** Users can install & use

Prepare v3.0 release:
1. Update README with Phase 5 features
2. Write architecture guide
3. API documentation for services
4. Quickstart guide for memory features
5. Troubleshooting guide
6. Tag release and publish to PyPI

**Deliverables:**
- v3.0 release notes
- Updated docs
- Installation guide
- Usage examples

---

## Recommendation: Priority Sequence

**Recommended Path for Maximum Impact:**

1. **First: Option C (CLI Module Resolution)** — 1 day
   - Unblocks all other testing
   - Enables `pxx --with-memory` workflows
   - Foundation for everything else

2. **Second: Option A (Observation Capture)** — 2-3 days
   - Completes memory lifecycle
   - Tests with real aider workflows
   - Validates tool call capture

3. **Third: Option D (Production Polish)** — 2-3 days
   - Solidify reliability
   - Performance optimization
   - Implement missing features (/forget, etc.)

4. **Fourth: Option E (Release)** — 1-2 days
   - Ship v3.0
   - Public availability

**Rationale:**
- Fix CLI first (it's blocking everything)
- Test capture (proves end-to-end works)
- Polish (makes production-ready)
- Ship (users benefit)

This sequence has maximum throughput and builds from foundation → verification → polish → release.

---

## Work Breakdown: Option C (Recommended First Step)

### pxx CLI Module Resolution

**Problem:** pxx/cli.py imports missing modules:
- `_git` — git CLI wrappers
- `audit` — session logging/audit
- `drift` — remote HEAD tracking
- `governance` — permissions/gates
- `review_gate` — code review checks
- `safety` — sanity checks
- `self_modes` — dogfooding tiers
- `workflow` — orchestration

**Investigation Needed:**
1. Check if modules exist in git history (deleted?)
2. Check if they're partially implemented
3. Determine why they weren't merged with Phase 5

**Fix Options:**
- A: Restore missing modules from git
- B: Complete partial implementations
- C: Reorganize imports (use stubs if needed)
- D: Identify which modules are actually used

**Estimated time:** 4-8 hours depending on complexity

---

## Timeline Estimate

| Phase | Option | Days | Ready By |
|-------|--------|------|----------|
| 6.1 | C - CLI Fix | 1 | Immediate |
| 6.2 | A - Capture | 2-3 | +2-3 days |
| 6.3 | D - Polish | 2-3 | +4-6 days |
| 6.4 | E - Release | 1-2 | +5-8 days |

**Total Path to v3.0 Release:** ~8 days

---

## Known Unknowns

1. **Observation Capture:** Will tool_calls serialize correctly through HTTP?
2. **Slash Commands:** Can we intercept user input before aider?
3. **pxx CLI:** How many modules are missing vs. partially done?
4. **Performance:** What's the latency impact of memory searches?
5. **User Demand:** Which features matter most to users?

**Recommendation:** Start with Option C to unblock testing, then use real workflows to guide priority of A/D/E.

---

## Next Steps

**Immediate (this session):**
1. Audit pxx/cli.py imports
2. Locate missing modules in git history or worktrees
3. Determine fix approach

**Then:**
- Implement fixes
- Run full `pxx --with-memory` workflow
- Observe behavior, test memory injection in real aider session
- Decide on remaining Phase 6 scope

---

## Files to Review

- `/Users/you/ai/pxx/pxx/cli.py` — Import statements, line 17
- `/Users/you/ai/pxx/pxx/` — Check which modules exist
- `/Users/you/ai/pxx/.claude/worktrees/*/pxx/` — Check other worktrees for modules

---

**Status:** Ready to proceed with Phase 6 planning once next priority is chosen.
