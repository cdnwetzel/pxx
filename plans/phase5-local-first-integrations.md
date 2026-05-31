# Phase 5: Local-First Integrations & Learnings Loop

> Backlog ID: 043–049

## Overview

Phase 5 integrates three open-source projects to complete pxx's Tier 4 learnings loop and improve architectural flexibility:

1. **agentmemory** — Persistent memory server with hybrid retrieval (BM25 + embeddings + knowledge graph)
2. **agent-skills** — Declarative skill workflow system (refactor hardcoded self-modes to YAML)
3. **9router** — Local proxy with optional token compression (20–40% context overhead reduction)

**Context:** Phase 4 shipped 6 items (1 HIGH security fix + 5 MEDIUM governance/audit improvements). v2.1+ is production-ready. Phase 5 unlocks Tier 4 (learnings loop) and improves extensibility.

**Timeline:** ~15 days (critical path 5–7 days, parallel tracks 2–3 days)

---

## Phase 5 Items

### Critical Path: Tier 4 Learnings Loop (5–7 days)

#### #043: agentmemory — Persistent Memory Server Setup

**Goal:** Install and wire agentmemory to pxx's audit log pipeline.

**Scope:**
- Install agentmemory on localhost:8000
- Wire to read audit logs from `~/.local/state/pxx/sessions/`
- Schema: `{path, tier, model, outcome, duration_sec, learned}`
- Health check: `/health` endpoint
- Test: basic query (e.g., "learnings for pxx/cli.py")

**Blocks:** #044 (indexer)

---

#### #044: agentmemory — Daily Audit-Log Indexer

**Goal:** Automatically extract learnings from audit logs and index them.

**Scope:**
- New module: `pxx/memory_indexer.py`
- Nightly job (or triggered): read 24h of audit logs
- Extract (scope, tier, model, outcome, duration) tuples
- Build embeddings: "What went well/poorly for path X?"
- Push to agentmemory BM25 + vector DB + knowledge graph
- Example output: "Tier 2 struggled with CLI arg parsing—T3 needed."

**Blocks:** #045 (query layer)

---

#### #045: agentmemory — Pre-Session Learnings Query + MCP

**Goal:** Query learnings before each session and inject into aider.

**Scope:**
- New MCP server: `pxx/mcp_memory.py` (wraps agentmemory)
- Pre-session: query "learnings for <current_scope>"
- Hybrid search: BM25 (keyword) + semantic (embeddings)
- Generate `learnings-<scope>.md` dynamically
- Inject into aider via `--read learnings.md`
- Env var: `PXX_ENABLE_LEARNINGS` (default: true for T3+, false for T1)

**Expected outcome:** Tier 4 complete. Aider receives contextual memory from past sessions.

---

### Parallel Track: Workflow Discipline (2–3 days, run during #043–#045)

#### #046: agent-skills — Refactor self_modes.py to YAML

**Goal:** Transition from hardcoded self-modes to declarative skill workflows.

**Scope:**
- Refactor `pxx/self_modes.py` logic into `pxx/skills/` directory:
  - `spec.yml` (scoping constraints)
  - `plan.yml` (analysis gates)
  - `build.yml` (edit constraints + diff cap)
  - `test.yml` (validation gates)
  - `review.yml` (governance checks)
  - `ship.yml` (push policy)
- New module: `pxx/skill_runner.py` (YAML loader + executor)
- Backward compat: existing CLI flags still work

**Mapping to pxx gates:**
- `spec.yml`: enforce `PXX_SCOPE` required (#003)
- `plan.yml`: generate safety_tag, show diff preview (#002)
- `build.yml`: enforce tier routing, set `OPENAI_API_KEY=EMPTY` (#L05)
- `test.yml`: run `uv run pytest -q`, verify no secrets
- `review.yml`: governance check, audit summary
- `ship.yml`: manual push required (no-push convention)

**Blocks:** #047 (slash commands)

---

#### #047: agent-skills — Slash Command Integration

**Goal:** Wire slash commands to skill workflows.

**Scope:**
- Map slash commands to skill workflows:
  - `/spec` → `pxx/skills/spec.yml`
  - `/plan` → `pxx/skills/plan.yml`
  - `/build` → `pxx/skills/build.yml`
  - `/test` → `pxx/skills/test.yml`
  - `/review` → `pxx/skills/review.yml`
  - `/ship` → `pxx/skills/ship.yml`
- Each skill emits constraints + gates as aider context
- Update aider prompts to reference skill definitions

**Expected outcome:** Declarative workflow visible to aider. Skills reusable across projects.

---

### Optional Enhancement (1–2 days)

#### #048: 9router — Token Compression Layer

**Goal:** Optional integration with 9router for 20–40% context reduction.

**Scope:**
- New endpoint type: `"9router"` in `pxx/endpoints.py`
- Health check: `curl http://localhost:20128/health`
- Env var: `PXX_USE_9ROUTER` (default: false, user opt-in)
- Record compression metrics in audit log
- Fallback: if 9router unavailable, direct Ollama/vLLM

**Expected outcome:** Optional ~30% context overhead reduction on long sessions.

---

#### #049: Audit Log Schema Evolution

**Goal:** Extend audit log schema to support learnings tracking.

**Scope:**
- Add fields: `learnings_retrieved`, `skill_gates_triggered`
- Backfill historical logs with defaults (non-breaking)
- Update `audit.write_session_start()` signature
- Example new record:
  ```json
  {
    "session_class": "edit",
    "model": "devstral:24b",
    "tier": "t2",
    "endpoint_backend": "vllm",
    "learnings_retrieved": ["cli.py: tier routing"],
    "skill_gates_triggered": ["build:diff_cap", "review:governance"],
    "...": "other fields"
  }
  ```

---

## Dependencies & Timeline

```
Critical Path (Tier 4 Learnings Loop):
  #043 (agentmemory setup)
    ↓ (2 days)
  #044 (audit-log indexer)
    ↓ (3 days)
  #045 (learnings query + MCP) → TIER 4 COMPLETE ✅
    Timeline: Days 1–7

Parallel Track (Workflow Discipline):
  #046 (refactor self_modes → YAML)
    ↓ (1.5 days)
  #047 (slash command integration)
    Timeline: Days 2–4 (run during #043–#044)

Optional Enhancement:
  #048 (9router integration) — Days 3–4
  #049 (audit schema) — Days 4–5

Total Phase 5: ~15 days (critical path 5–7 days, parallel reduces by 3 days)
```

---

## Architecture Sketches

### agentmemory Integration

```
pxx/audit.py (session metadata)
    ↓
pxx/memory_indexer.py (daily)
    • Read: ~/.local/state/pxx/sessions/*.jsonl
    • Extract: (path, tier, model, outcome, duration)
    • Build: embeddings ("What went well/poorly for X?")
    ↓
agentmemory:8000 (BM25 + vector + knowledge graph)
    ↓
pxx/cli.py (pre-session)
    • Query: "learnings for <current_scope>"
    • Hybrid search: keyword + semantic
    ↓
pxx/mcp_memory.py (MCP server)
    ↓
aider context injection
    /load learnings-<scope>.md
```

### agent-skills Refactoring

```
BEFORE:
  pxx/self_modes.py (hardcoded T1/T2/T3)
    ├─ determine_session_class()
    ├─ self_test()
    ├─ self_lint()
    └─ (no extensibility)

AFTER:
  pxx/skills/ (declarative YAML)
    ├─ spec.yml (scoping)
    ├─ plan.yml (safety gates)
    ├─ build.yml (edit constraints)
    ├─ test.yml (validation)
    ├─ review.yml (governance)
    └─ ship.yml (push policy)
  
  pxx/skill_runner.py (executor)
    • Load skill YAML by phase
    • Enforce constraints
    • Record execution trace

  aider slash commands:
    /spec, /plan, /build, /test, /review, /ship
```

### 9router Integration

```
pxx/cli.py
    ↓
detect_endpoint() [with 9router check]
    ↓
9router:20128 (local proxy)
    • OpenAI-compatible API
    • Token compression (RTK, 20–40%)
    • Multi-provider fallback
    ↓
Upstream (Ollama/vLLM)

Optional: PXX_USE_9ROUTER=1
Fallback: direct Ollama/vLLM if unavailable
```

---

## Success Criteria

- **#043–#045 complete:** Tier 4 learnings loop functional. Sessions can query and learn from history.
- **#046–#047 complete:** Self-modes refactored to YAML. Skills reusable. Slash commands wired.
- **#048 optional:** 9router available, 20–40% compression on opt-in.
- **#049 complete:** Audit logs track learnings retrieval and skill gate execution.

**Testing:**
- agentmemory: Query API responds, embeddings searchable, MCP server works
- agent-skills: Slash commands map to skills, YAML loads without error, backward compat verified
- 9router: Health check passes, compression ratio recorded in audit log
- All phases: 400+ tests pass, no regressions

---

## Start Gate

Phase 4 complete ✓
- v2.1+ production-ready
- 382 tests passing
- All HIGH + MEDIUM findings resolved
- Audit logs stable (282 days history)

**Ready to start Phase 5 anytime.**

---

## Notes

- agentmemory is the linchpin for Tier 4; #046–#047 can run in parallel
- 9router is optional enhancement; ship Phase 5 without it if timeline is tight
- All three integrations are **additive** (no breaking changes to existing functionality)
- Learnings loop will improve pxx's ability to learn from past sessions and apply that knowledge to future work
