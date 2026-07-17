# Phase 5: Complete End-to-End Implementation Prompt

**Objective:** Transform pxx from a single-tool orchestrator into a polyglot AI tooling coordinator by integrating three production-grade systems: 9router (token compression + routing), agentmemory (persistent memory with hybrid retrieval), and agent-skills (encoded SDLC workflows). Aider remains autonomous; Studio remains the LLM source; Neo gains intelligence without added local model load.

**Constraint:** All three tools are localhost-only, zero-external-DB. Phase 5 respects these constraints while adapting them to pxx's two-machine architecture.

**Scope:** Four tiers of work, 8 weeks estimated effort. Implementation is sequential within tiers (Tier 1 → Tier 2 → Tier 3 → Tier 4).

---

## Context: Architecture & Design

### Two-Machine Setup
- **Neo** (8GB MacBook, LAN/VPN): runs pxx orchestrator, 9router proxy, agentmemory memory server, aider
- **Studio** (M4 Max 36GB): hosts Ollama with devstral:24b (default)
- pxx stays supervisor; aider stays autonomous; Studio is the LLM source

### Three Integrations

**1. 9router (Token Compression + Provider Routing)**
- Next.js-based OpenAI-compatible proxy
- RTK compression: 20-40% savings on git diff, grep, test output, build logs
- Fallback chain: Studio Ollama → GLM/MiniMax → Kiro free tier
- Headless config via YAML (no UI needed on Neo)
- Health check on port 20128

**2. agentmemory (Persistent Memory)**
- BM25 (keyword) + vector (semantic) + knowledge graph (entities) via Reciprocal Rank Fusion
- Local embeddings (Xenova all-MiniLM-L6-v2, 384 dims)
- No external DB: all-in-memory indices persisted to SQLite
- Hybrid search: 95.2% retrieval accuracy vs. 86% single-strategy
- Observer pattern: watch aider stdout, extract tool calls, send to agentmemory

**3. agent-skills (SDLC Workflows)**
- 8 markdown-based skills (/spec, /plan, /build, /test, /review, /ship, /security-audit, /simplify)
- Loadable via aider's `/load` mechanism
- Encode production discipline (vertical slicing, TDD, 5-axis review, etc.)
- No infrastructure needed; pure markdown + system prompt integration

### Backlog Scores (Incorporating Oz Context)
- **9router:** 3.27 (Phase 2) — Highest priority, lowest risk, token savings day 1
- **agentmemory:** 3.00 (Phase 2/3 boundary, elevated) — Highest LTV, observer pattern needs design
- **agent-skills:** 2.82 (Phase 3) — TTV=4 (quick-win), parallel track with 9router

### Recommended Sequencing
1. **Tier 1: 9router + agentmemory infrastructure** (Weeks 1-2) — Core supervisor mode
2. **Tier 2: agentmemory integration** (Weeks 2-3) — Memory injection into system prompt
3. **Tier 3: agent-skills workflows** (Weeks 3-4) — Skill files, discovery, system prompt updates
4. **Tier 4: Cost & observability** (Weeks 4-5) — `--doctor` stats, audit log events, metrics

---

## Tier 1: Core Infrastructure (Weeks 1-2)

**Goal:** 9router + agentmemory running as supervised subprocesses on Neo. Aider receives requests via 9router. agentmemory server is listening.

### Tasks

#### 1.1 Create `pxx/router.py` — 9router Lifecycle Management

**File:** `pxx/router.py` (new)

Implement:
- `NineroterManager` class
- `start()` — spawn 9router subprocess, wait for health check (port 20128)
- `stop()` — graceful termination with timeout
- `get_usage()` — query `/v1/usage` for token counts, cost, compression stats
- `get_status()` — query provider health and fallback chain state
- `_wait_for_ready(timeout)` — block until 9router responds

Dependencies:
- `subprocess.Popen` for lifecycle
- `requests` for health checks and API queries
- `pathlib.Path` for config file handling

Config file: `~/.9router/config.yml`
```yaml
server:
  bind: 127.0.0.1:20128
  api_key_required: false

providers:
  studio_ollama:
    type: openai_compatible
    base_url: http://workstation:11434/v1
    model: devstral:24b
    priority: 1

  glm:
    type: api_key
    provider: zhipu
    api_key: ${GLM_API_KEY}
    model: glm-4-vision
    priority: 2
    cooldown: 60s

  kiro:
    type: api_key
    provider: kiro
    api_key: ${KIRO_API_KEY}
    model: general
    priority: 3
    cooldown: 300s

combos:
  default:
    - studio_ollama
    - glm
    - kiro
    fallback_strategy: exponential_backoff

compression:
  enabled: true
  formats:
    git_diff: {enabled: true, max_hunk_lines: 100}
    grep: {enabled: true}
    find: {enabled: true}
    build_output: {enabled: true}
  safety_gates:
    min_input_size: 500
    max_input_size: 100_000
    never_empty: true
```

**Tests:** `tests/test_router.py`
- Unit test: `NineroterManager` can start/stop cleanly
- Integration test: health check succeeds within timeout
- Integration test: `get_usage()` returns dict with expected keys (tokens, cost, compression)

#### 1.2 Create `pxx/memory.py` — agentmemory Lifecycle & Config

**File:** `pxx/memory.py` (new)

Implement:
- `AgentmemoryManager` class
- `start()` — spawn agentmemory subprocess with local embeddings config
- `stop()` — graceful termination, flush indices
- `health_check()` — verify server is listening on port 3111
- Config generation at `~/.agentmemory/.env`

Config file: `~/.agentmemory/.env`
```ini
EMBEDDING_PROVIDER=local
AGENTMEMORY_AUTO_COMPRESS=true
BM25_WEIGHT=0.5
VECTOR_WEIGHT=0.5
TOKEN_BUDGET=2000
MEMORY_ARCHIVE_AFTER_DAYS=7
STATE_BACKEND=sqlite
STATE_PATH=~/.pxx/memory.db
```

**Tests:** `tests/test_memory.py`
- Unit test: config file is created with correct values
- Integration test: subprocess starts, health check succeeds

#### 1.3 Create `pxx/observer.py` — Aider Output Parser & Observer Thread

**File:** `pxx/observer.py` (new)

Implement:
- `AiderOutputParser` class
  - `parse_stream(stdout_iter)` → yields `(event_type, payload)` tuples
  - Event types: `tool_call`, `tool_result`, `error`, `conversation_start`
  - Parse aider's JSON-formatted tool calls (format depends on aider's `--edit-format`)
  - Extract tool results from marked sections

- `AiderMemoryObserver` class
  - `__init__(aider_proc, memory_api_base)`
  - `start()` — spawn observer thread
  - `_run()` — main observer loop (read aider stdout line-by-line, parse, send to agentmemory)
  - `_send_to_memory(hook_payload)` → POST to `/mem/observe` endpoint
  - Error handling: log but don't block aider on memory failures

Hook payload structure:
```python
{
    "hook_type": "pre_tool_use" | "post_tool_use" | "tool_result",
    "data": {
        "tool_name": str,
        "tool_input": dict,
        "tool_output": str,  # for post_tool_use
        "success": bool
    },
    "timestamp": ISO 8601,
    "project_path": str  # for scoping
}
```

**Tests:** `tests/test_observer.py`
- Unit test: `AiderOutputParser.parse_stream()` extracts tool calls from JSON
- Unit test: Tool result extraction from marked sections
- Integration test: Observer thread sends HTTP POST to agentmemory
- Integration test: Observer logs errors but doesn't raise (async safety)

#### 1.4 Modify `pxx/cli.py` — Supervisor Mode & Subprocess Orchestration

**Changes to `pxx/cli.py`:**

Add command-line flags:
- `--with-router` — start 9router
- `--with-memory` — start agentmemory + observer

Modify `main()`:
1. After `detect_endpoint()` and `model_for()`, before aider spawn:
   - If `args.with_router`: call `NineroterManager.start()`
   - If `args.with_memory`: call `AgentmemoryManager.start()`

2. Set `env['OPENAI_API_BASE'] = 'http://127.0.0.1:20128/v1'` before aider spawn

3. Change from `os.execve(aider_binary, args, env)` to:
   ```python
   aider_proc = subprocess.Popen([aider_binary, *args], env=env, stdout=PIPE, stderr=PIPE)
   
   if args.with_memory:
       observer = AiderMemoryObserver(aider_proc)
       observer.start()
   
   aider_returncode = aider_proc.wait()
   
   if args.with_memory:
       agentmemory_manager.stop()  # flush indices
   if args.with_router:
       router_manager.stop()
   
   return aider_returncode
   ```

4. Add startup banner:
   - If `--with-router`: print "pxx: 9router running on :20128"
   - If `--with-memory`: print "pxx: agentmemory running on :3111"
   - Print fallback chain status (via `router_manager.get_status()`)

**No changes to:** `detect_endpoint()`, `model_for()`, safety checks, scope resolution. Supervisor mode is optional and additive.

#### 1.5 Modify `pxx/audit.py` — Log Router & Memory Events

**Changes to audit log:**

Add event types:
- `router_start(config_path, fallback_chain)` — log 9router startup
- `router_stop(usage_stats)` — log final token counts, compression %
- `memory_start()` — log agentmemory startup
- `memory_stop(index_size_mb)` — log final index size
- `memory_injection(num_observations, tokens_used)` — log when memory is injected into system prompt

Audit log entry schema:
```python
{
    "timestamp": ISO 8601,
    "event_type": str,
    "detail": dict
}
```

**Test:** `tests/test_audit.py`
- Verify audit log entries are well-formed JSON

### Acceptance Criteria — Tier 1

- [ ] `pxx --with-router --with-memory` starts aider with both subprocesses running
- [ ] Health checks pass (curl localhost:20128, localhost:3111)
- [ ] Aider requests route through 9router (observable via curl or logs)
- [ ] Observer thread watches aider stdout without blocking
- [ ] Graceful shutdown: all subprocesses terminate within 3 seconds on aider exit
- [ ] `--doctor` shows router + memory running
- [ ] Audit log includes startup/shutdown events
- [ ] Tests pass: `pytest tests/test_router.py tests/test_memory.py tests/test_observer.py`

---

## Tier 2: agentmemory Integration (Weeks 2-3)

**Goal:** Aider sessions start with relevant past memories injected into system prompt. Memory is project-scoped.

### Tasks

#### 2.1 Implement Memory Injection in System Prompt

**File:** `pxx/cli.py` (modify in supervisor section)

Before spawning aider:
1. Query agentmemory for top-5 relevant memories for this project
   ```python
   if args.with_memory:
       memories = requests.post(
           "http://127.0.0.1:3111/mem/search",
           json={"query": project_name_or_path, "limit": 5, "format": "full"},
           timeout=2
       ).json()
   ```

2. Build "Recalled Memory" block
   ```
   ## Recalled Memory
   
   ### Session: 2026-05-30T18:22:00Z (3 days ago)
   **Type:** error
   **Concepts:** JWT, expiration
   **Files:** src/auth/middleware.ts
   **Context:** JWT tokens were expiring after 1 hour...
   ```

3. Inject after system prompt load, before task description
   - Token-budget aware: cap injected memory at 20% of total context window
   - Truncate narratives if approaching limit

**Files to modify:**
- `pxx/prompts/system.md` — add "## Recalled Memory" section before task input
- `pxx/cli.py` — call memory search, format block, inject

**Test:**
- Unit test: memory injection doesn't break aider's prompt parsing
- Integration test: session 1 captures observation, session 2 retrieves it

#### 2.2 Implement Project-Scoped Memory Isolation

**File:** `pxx/observer.py` (modify hook payload)

Add project scoping:
```python
hook_payload = {
    "hook_type": "post_tool_use",
    "project_path": os.getcwd(),  # or user-specified scope
    "data": {...}
}
```

**File:** `pxx/cli.py` (modify memory query)

When searching, filter by project:
```python
memories = requests.post(
    "http://127.0.0.1:3111/mem/search",
    json={
        "query": task_description,
        "project_filter": os.getcwd(),  # current working directory
        "limit": 5
    }
).json()
```

**Add flag:** `--cross-project-memory` to unlock cross-project retrieval

**Test:**
- Integration test: two sessions in different projects don't share memories
- Integration test: `--cross-project-memory` flag enables cross-project search

#### 2.3 Implement Memory Hygiene

**File:** `pxx/cli.py` (modify after supervisor startup)

Auto-archive on startup:
```python
if args.with_memory:
    memory_mgr.auto_archive(max_size_mb=200)  # if index > 200 MB, archive old
```

**Add new command:** `pxx --purge-memory [project_path]`
- Deletes all memories for a specific project or current directory
- Prompts for confirmation

**Test:**
- Integration test: memories older than 7 days are archived
- Integration test: `--purge-memory` removes scoped memories

### Acceptance Criteria — Tier 2

- [ ] Session 1: run `pxx --with-memory --edit`, make a tool call, exit
- [ ] Session 2: run `pxx --with-memory` on same project, system prompt includes "Recalled Memory" block
- [ ] Memory doesn't leak across projects
- [ ] Context injection doesn't break aider's parsing
- [ ] `--doctor` shows memory stats (observations KB, projects, last auto-compress)
- [ ] Audit log includes memory injection events
- [ ] Tests pass: `pytest tests/test_memory.py -k integration`

---

## Tier 3: agent-skills Workflows (Weeks 3-4)

**Goal:** Reusable, production-hardened workflows available in aider. Users can load skills via `/load` mechanism.

### Tasks

#### 3.1 Write 8 Skill Markdown Files

**Directory:** `pxx/commands/` (new)

Create these 8 files:

**`spec.md`** — Specification Workflow
- 4 gated phases: Specify → Plan → Tasks → Implement (human review between each)
- 6 core areas: objective, tech stack, commands, project structure, code style, testing strategy, boundaries
- Assumption surfacing and testable success criteria reframing

**`plan.md`** — Vertical Slicing Workflow
- Decompose spec into small, verifiable tasks
- Vertical slicing (one complete feature path per task), not horizontal layers
- Dependency graphing, task sizing (XS/S/M/L, max L=5-8 files)
- Checkpoints between phases

**`build.md`** — TDD RED-GREEN-REFACTOR Cycle
- Incremental implementation + test-driven development
- 5 implementation rules: simplicity first, scope discipline, one thing at a time, keep compilable, feature flags
- Prove-It pattern for bug fixes

**`test.md`** — Test Pyramid Workflow
- Test pyramid (80% unit, 15% integration, 5% E2E)
- Test sizing by resource model (small/medium/large)
- DAMP over DRY in tests
- Structured coverage: happy path, edge cases, error paths, concurrency

**`review.md`** — 5-Axis Code Review
- Correctness, readability, architecture, security, performance
- Change sizing ~100 lines per review
- Severity categorization (Critical/Important/Suggestion/Nit/FYI)
- Verification of testing and build output

**`ship.md`** — Pre-Deployment Orchestration
- Fan-out 3 specialist personas: code-reviewer, security-auditor, test-engineer
- Parallel review, merge findings, GO/NO-GO decision
- Mandatory rollback plan with trigger conditions and RTO targets

**`security-audit.md`** — Security-Focused Review Lens
- Extract from /review's 5-axis
- Focus on OWASP top 10, auth, data handling, secrets management

**`simplify.md`** — Code Simplification & Reuse
- Simplification + reuse, no behavior change
- Anti-rationalization: explain why simpler is better

**Format (YAML frontmatter + markdown):**
```markdown
---
name: /spec
description: Capture requirements and assumptions before building
category: lifecycle
phase: discovery
outputs: [specification document, success criteria, tech stack]
complexity: low
time_estimate: 15-30 minutes
dependencies: []
---

# Specification Workflow

Use this command to capture requirements...

## Objective
Lock down what success looks like...

## Steps

### 1. Capture Core Areas
...

## Decision Criteria
When to stop: ...
```

**Tests:**
- Lint: all markdown files are valid (headers, code blocks, links)
- Content: all skills have frontmatter with required keys
- Examples: each skill includes 1-2 concrete examples

#### 3.2 Implement Skill Discovery: `pxx --list-skills`

**File:** `pxx/cli.py` (add new command)

New function: `list_skills()`
- Scan `pxx/commands/*.md`
- Parse frontmatter (YAML)
- Display table: name, category, description, complexity, time_estimate
- Show how to invoke: `/load /Users/you/.pxx/commands/SKILL.md`

**Output example:**
```
Available Skills:
  /spec (discovery)        Capture requirements before building     [low, 15-30m]
  /plan (discovery)        Decompose into vertical slices            [med, 1h]
  /build (execution)       RED-GREEN-REFACTOR TDD cycle             [med, 2-4h]
  /test (validation)       Test pyramid (80/15/5)                   [med, 1h]
  /review (qa)             5-axis code review                       [med, 1-2h]
  /ship (deployment)       Pre-launch orchestration                 [high, 2-3h]
  /security-audit (qa)     Security-focused review lens             [med, 1h]
  /simplify (maintenance)  Code simplification & reuse              [low, 30-60m]

Load with: /load /Users/you/.pxx/commands/SKILL.md
```

**Test:**
- Unit test: `--list-skills` lists all 8 skills with correct metadata
- Integration test: `/load` paths in output are correct

#### 3.3 Update System Prompt with Skills Section

**File:** `pxx/prompts/system.md`

Add new section after other instructions:

```markdown
## Available Skills

You have access to 8 reusable SDLC workflows designed to encode production discipline. Load skills with `/load <path>` in aider chat:

- **/spec** — Capture requirements and surface assumptions before building
- **/plan** — Decompose into vertical slices (complete feature paths per task)
- **/build** — TDD RED-GREEN-REFACTOR cycle for incremental development
- **/test** — Test pyramid (80% unit, 15% integration, 5% E2E)
- **/review** — 5-axis code review (correctness, readability, architecture, security, perf)
- **/ship** — Pre-deployment orchestration with parallel specialist reviews
- **/security-audit** — Security-focused review lens (OWASP, auth, secrets)
- **/simplify** — Code simplification & reuse without behavior change

Example:
```
/load /Users/you/.pxx/commands/spec.md
Now let's follow the spec workflow for the auth feature...
```

Skills are optional — follow them to encode discipline, or work directly if you prefer.
```

**Test:**
- Lint: system prompt is valid markdown
- Content: all 8 skills are mentioned with descriptions

#### 3.4 Add Template for Custom Skills

**File:** `pxx/commands/SKILL_TEMPLATE.md`

Template for users to create custom skills:

```markdown
---
name: /custom-skill-name
description: What this skill does (one line)
category: [lifecycle|execution|qa|maintenance|custom]
phase: [discovery|execution|validation|deployment]
outputs: [list of output artifacts]
complexity: [low|med|high]
time_estimate: human-readable time
dependencies: [other skill IDs or features this depends on]
---

# Skill Title

One-paragraph overview of what this skill accomplishes and when to use it.

## Objective
What is locked down by following this workflow?

## Steps

### 1. First Step
Description and instructions.

### 2. Second Step
...

## Decision Criteria
When is this skill done? What signals completion?

## Example
Concrete walk-through of applying this skill to a real scenario.
```

**Not a test, but document in README or CLI help.**

### Acceptance Criteria — Tier 3

- [ ] `pxx --list-skills` lists all 8 skills with metadata
- [ ] Each skill markdown file is well-formed (valid frontmatter, code examples, links resolve)
- [ ] System prompt includes "Available Skills" section
- [ ] `/load /path/to/spec.md` works in aider chat
- [ ] User can follow /spec workflow end-to-end without errors
- [ ] All skills are mentioned in `pxx --list-skills` and system prompt
- [ ] Tests pass: `pytest tests/test_skills.py`

---

## Tier 4: Cost & Observability (Weeks 4-5)

**Goal:** Visibility into token usage, compression, fallback chains, memory indices.

### Tasks

#### 4.1 Extend `pxx --doctor` with Router & Memory Stats

**File:** `pxx/cli.py` (modify `doctor_command()`)

Add sections:

**9router Status:**
```
9router (localhost:20128)
  Status: running
  Fallback chain: Studio Ollama → GLM → Kiro
  Token compression: 28% average (git diff, grep)
  Session cost: $0.012 (Studio free + compression)
  
  Provider status:
    studio_ollama: healthy
    glm: healthy (cooldown 0s)
    kiro: healthy (cooldown 0s)
```

**agentmemory Status:**
```
agentmemory (localhost:3111)
  Status: running
  Indices size: 42 MB (BM25, vectors, graph)
  Observations: 156 across 4 projects
  Last compress: 2026-06-02T14:22:00Z (5 min ago)
  Retrieval accuracy: 94% (from logs)
```

Call `router_manager.get_usage()` and `router_manager.get_status()` to populate.

**Test:**
- Unit test: `--doctor` output includes router section
- Integration test: `--doctor` queries real 9router and agentmemory if running

#### 4.2 Extend Audit Log with Router & Memory Events

**File:** `pxx/audit.py`

Already sketched in Tier 1.5. Ensure these events are logged:

- `router_start` — 9router subprocess started
- `router_stop` — 9router stopped, final usage stats
- `memory_start` — agentmemory subprocess started
- `memory_stop` — agentmemory stopped, index size
- `memory_injection` — memory was injected into system prompt (count, tokens)
- `memory_observe` — observation sent to agentmemory (tool name, project path)

**Test:**
- Integration test: audit log contains expected events after session

#### 4.3 Add Cost Metrics to Session Summary

**File:** `pxx/cli.py` (after aider exits)

Print summary before returning:

```
Session Summary (2026-06-02T14:30:00Z)
  Duration: 12m 34s
  Aider requests: 23
  Tokens sent: 1,234
  Tokens saved (compression): 340 (28%)
  Cost: $0.006 (Studio free; compression saved ~$0.002)
  Fallbacks used: 0 (Studio always healthy)
  Memories captured: 12
  Memories injected: 5 (22 tokens)
```

Pull data from:
- `router_manager.get_usage()`
- Audit log entries
- agentmemory compression stats

**Test:**
- Integration test: session summary is printed and contains expected keys

#### 4.4 Document Performance Targets & Tuning

**File:** `docs/PHASE5_TUNING.md` (new)

Document:
- BM25/Vector/Graph weight tuning (default 0.5/0.5/0.3, configurable via env)
- Embedding provider trade-offs (local vs. remote)
- RTK compression format coverage (which content types are handled)
- Memory index limits (when to archive)
- Context injection budget (how much memory can fit in context)

**Not a test, but reference documentation.**

### Acceptance Criteria — Tier 4

- [ ] `pxx --doctor` includes "9router Status" and "agentmemory Status" sections
- [ ] Session summary is printed after aider exits
- [ ] Audit log contains router/memory events
- [ ] Cost calculation is accurate (spot-check against 9router `/v1/usage`)
- [ ] Compression % shown in `--doctor` matches actual compression
- [ ] All 4 tiers' acceptance criteria pass

---

## Testing & Verification Strategy

### Test Coverage by Tier

**Tier 1:**
- `tests/test_router.py` — 9router subprocess lifecycle
- `tests/test_memory.py` — agentmemory subprocess lifecycle
- `tests/test_observer.py` — aider output parser, observer thread
- `tests/test_cli.py` — supervisor mode, subprocess orchestration

**Tier 2:**
- `tests/test_memory.py` — memory injection, project scoping, hygiene
- `tests/test_cli.py` — system prompt building, context injection

**Tier 3:**
- `tests/test_skills.py` — skill file parsing, listing, loading

**Tier 4:**
- `tests/test_doctor.py` — router/memory stats in `--doctor`
- `tests/test_audit.py` — event logging

### Integration Test Workflow

1. **Tier 1 validation:**
   ```bash
   pxx --with-router --with-memory --self-test
   # Verify: subprocesses start, aider can make requests, shutdown is clean
   ```

2. **Tier 2 validation:**
   ```bash
   # Session 1
   cd /tmp/test-project && pxx --with-memory --edit
   # (make some tool calls)
   # (exit)
   
   # Session 2
   cd /tmp/test-project && pxx --with-memory
   # Check: system prompt includes "Recalled Memory" block
   ```

3. **Tier 3 validation:**
   ```bash
   pxx --list-skills
   # Verify: 8 skills listed
   
   cd /tmp/test-project && pxx --with-memory
   # In aider: /load /Users/you/.pxx/commands/spec.md
   # Follow spec workflow, verify it works
   ```

4. **Tier 4 validation:**
   ```bash
   pxx --with-router --with-memory --doctor
   # Check: router & memory stats present
   
   pxx --with-router --with-memory
   # (run aider session)
   # Check: session summary is printed, audit log has events
   ```

---

## Acceptance Criteria — All Tiers

- [ ] All unit tests pass: `pytest tests/test_*.py -v`
- [ ] Integration tests pass: all four workflows above work end-to-end
- [ ] Aider still generates correct code (functionality unchanged)
- [ ] No regressions on existing pxx features (detection, safety, scope, audit)
- [ ] Lint passes: `ruff check pxx tests`
- [ ] All new code has type hints
- [ ] Docstrings on public functions
- [ ] README updated with Tier 1-4 setup and usage
- [ ] 4 commits (one per tier), each with clear message
- [ ] Audit log grows correctly over sessions

---

## File Inventory (New & Modified)

**New Files:**
```
pxx/router.py                  # 9router lifecycle
pxx/memory.py                  # agentmemory lifecycle
pxx/observer.py                # aider output parser & observer thread
pxx/commands/spec.md           # /spec workflow
pxx/commands/plan.md           # /plan workflow
pxx/commands/build.md          # /build workflow
pxx/commands/test.md           # /test workflow
pxx/commands/review.md         # /review workflow
pxx/commands/ship.md           # /ship workflow
pxx/commands/security-audit.md # security lens
pxx/commands/simplify.md       # simplify workflow
pxx/commands/SKILL_TEMPLATE.md # custom skill template
tests/test_router.py           # 9router tests
tests/test_memory.py           # agentmemory tests
tests/test_observer.py         # observer tests
tests/test_skills.py           # skill tests
tests/test_doctor.py           # doctor extension tests
docs/PHASE5_TUNING.md          # tuning guide
```

**Modified Files:**
```
pxx/cli.py                     # supervisor mode, memory injection, skills discovery, doctor extension
pxx/audit.py                   # router/memory events
pxx/prompts/system.md          # skills section, recalled memory block
README.md                       # Tier 1-4 setup & usage
```

---

## Known Risks & Mitigations

### Risk: aider Output Parser Brittleness
**Mitigation:**
- Add configurable markers for tool call detection (env var `AIDER_TOOL_MARKER`)
- Fallback: if parser fails, skip hook (don't crash observer)
- Monitor stderr for parse errors; log to audit

### Risk: Memory Index Grows Without Bound
**Mitigation:**
- Observation deduplication (hash-based)
- Auto-archive on startup if index > 200 MB
- User command `pxx --purge-memory` for cleanup

### Risk: 9router Fallback Chain Thrashing
**Mitigation:**
- Per-provider cooldowns (exponential backoff)
- Status polling via `/v1/status` endpoint
- `--doctor` displays fallback chain state

### Risk: Memory Injection Breaks Aider Parsing
**Mitigation:**
- Inject memory after initial system prompt, before task description
- Token-budget aware (never exceed 90% of context)
- A/B test: disable memory injection, verify aider still works

---

## Estimation & Effort

**Tier 1 (Core Infrastructure):** 2 weeks
- 9router manager: 3 days (start, stop, health check, usage queries)
- agentmemory manager: 3 days (same lifecycle pattern)
- Observer pattern: 4 days (parser, thread, error handling)
- pxx supervisor mode: 2 days (subprocess orchestration)
- Tests & validation: 3 days

**Tier 2 (Memory Integration):** 1 week
- Memory injection: 2 days (query, format, inject)
- Project scoping: 2 days (tag observations, filter search)
- Hygiene (archive, purge): 2 days
- Tests & validation: 1 day

**Tier 3 (Skills):** 1 week
- Write 8 skill files: 3 days (templates exist; ~500 lines each)
- Skill discovery (`--list-skills`): 1 day
- System prompt integration: 1 day
- Tests & validation: 1 day

**Tier 4 (Observability):** 1 week
- `--doctor` extension: 2 days
- Audit log events: 1 day
- Cost metrics: 1 day
- Tuning docs & tests: 2 days

**Total: ~8 weeks**

---

## Success Metrics (Post-Phase 5)

### Quantitative
- **Token compression:** 25-35% savings on typical aider sessions
- **Cost reduction:** 20-30% lower API costs (Studio free; compression reduces fallback usage)
- **Memory recall:** 70%+ of relevant past memories retrieved on new sessions
- **Skill adoption:** 60%+ of users invoke at least one skill per project

### Qualitative
- "Faster convergence on familiar problems" (thanks to memory)
- `--doctor` output gives users confidence in system health
- Spec+Plan→Build workflow reduces back-and-forth revisions

---

## Questions Before Starting?

- **9router binary:** Is it installed globally (`9router` in PATH) or do we need to handle installation?
- **agentmemory binary:** Same — installed globally or need installation?
- **aider output format:** Which `--edit-format` does aider use? (determines parser specifics)
- **Existing audit log schema:** Should we extend or create a separate log for router/memory events?
- **Context window budget:** What's the actual limit for this aider instance? (determines memory injection cap)

---

**Ready to implement? Start with Tier 1. Each tier depends on the previous, but Tier 1 is self-contained.**
