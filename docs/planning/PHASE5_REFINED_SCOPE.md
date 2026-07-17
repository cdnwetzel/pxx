# Phase 5: AI Tooling Sovereignty — Refined Scope & Design

**Date:** 2026-06-02  
**Status:** Design Phase — Incorporates findings from all three tool deep dives  
**Audience:** pxx maintainers, Studio operators, Neo aider users  

---

## Executive Summary

Phase 5 transforms pxx from **single-tool orchestrator** into a **polyglot AI tooling coordinator** by integrating three specialized, battle-tested systems:

1. **9router** — Local OpenAI-compatible proxy with 20-40% token compression
2. **agentmemory** — Persistent memory fusion (BM25 + vector + graph retrieval)
3. **agent-skills** — Production SDLC workflows encoded as reusable skills

**Strategic outcome:** pxx becomes the **coordination layer** between local tools (Ollama, aider, Claude Code) and external constraints (cost, context limits, multi-agent coherence). Aider stays autonomous; Studio remains the LLM source; Neo gains intelligence without adding local model load.

**Key constraint:** All three tools are designed for **localhost-only operation** and **zero external database dependencies**. Phase 5 respects these constraints while adapting them to pxx's two-machine architecture.

---

## Part 0: Competitive Landscape & Strategic Validation

### The Oz Launch (2026-05-19)

On May 19, 2026, Warp launched **Oz**, a commercial cloud platform for multi-harness orchestration of coding agents. Oz bundles exactly the capabilities Phase 5 proposes to build:

- **Cross-harness Agent Memory** (BM25 + vector, pluggable data sources, writable)
- **Multi-agent orchestration** (parallel subagents, coordinated execution)
- **Governance & billing** (per-team controls, granular permissions, audit logs)
- **Self-hosting options** (Kubernetes, Docker, direct execution)

[Oz Launch Post](https://www.warp.dev/blog/oz-multi-harness-cloud-agents)

### What Oz Validates

The fact that a well-funded team (Warp) chose to build these three capabilities as flagship features of a production platform is strong validation that Phase 5's direction is correct:

1. **9router-like routing** — Oz handles multi-harness orchestration (Claude Code, Codex, Warp Agent). Routing and compression are foundational to managing multiple agent sources.
2. **agentmemory-like memory** — Oz ships cross-harness Agent Memory as a research preview. Long-term memory for agents is moving from exploratory to shipping.
3. **agent-skills-like workflows** — Oz's pluggable knowledge sources and writable skill corpus encode SDLC discipline (Oz calls it "organizational knowledge").

These are not speculative; they're shipping.

### Why Oz Is Ruled Out for pxx

Oz is a superb product for teams with cloud infrastructure. It is ruled out for pxx due to a fundamental **constraint mismatch**, not a capability gap:

| Dimension | Oz | pxx Requirement | Conflict |
|-----------|-----|---|---|
| **Deployment** | Cloud + self-hosted K8s | Neo 8GB MacBook, Studio M4 Max | Cloud dependency violates offline operation |
| **Data sovereignty** | Warp-managed or self-hosted | All data stays on Neo/Studio | Cross-machine data sync adds risk |
| **LLM source** | Multi-provider (Claude, Gemini, etc.) | Studio Ollama only | Oz doesn't integrate with local Ollama |
| **Cost model** | Per-team subscription + usage | Zero subscription, local LLM free | Incompatible cost posture |
| **Offline support** | Requires internet | pxx works offline on LAN/VPN | No offline memory/routing in Oz |

**The core issue:** pxx's value proposition is **offline capability** and **data sovereignty**. Oz solves the same problem via cloud. Teams must choose their trust model first; then tools follow.

### Build vs. Buy (Oz Scoring)

For completeness, Oz scores as a "buy" option:

| Factor | Score | Reasoning |
|--------|-------|-----------|
| **LTV** | 3 | High capability (5), but offline constraint makes it unusable for pxx (→3) |
| **EFF** | 5 | Eliminates all three integration efforts in one purchase |
| **IMM** | 4 | In beta now; production-grade in 2-3 months |
| **DEP** | 1 | Nothing in pxx backlog blocks on Oz |
| **RISK** | 2 | Risk of not adopting is low — we're building local equivalents |
| **TTV** | 5 | Usable immediately |

```
Score = (3 + 5 + 4 + 0.5 + 2 + 5) / 5.5 = 19.5 / 5.5 = 3.55 (RULED OUT by constraint)
```

The score is moot. Constraint violations make Oz ineligible, despite strong capabilities.

### Revised Phase 5 Scores (Incorporating Oz Context)

Oz's existence raises the **IMM** and **RISK** scores for agentmemory. The ecosystem is moving fast toward cross-harness memory. Teams without local alternatives will default to cloud. That's sovereignty lost.

#### 1. 9router — Token Compression + Provider Routing

| Factor | Score | Reasoning |
|--------|-------|-----------|
| **LTV** | 4 | Material improvement to daily ops (20-40% compression, fallback). Not foundational — doesn't unlock a new class of work, but meaningfully reduces cost. |
| **EFF** | 4 | Eliminates manual provider switching when Studio is unreachable. RTK auto-compresses on every call (git diff, grep, test output). Reduces daily overhead. |
| **IMM** | 3 | No hard deadline. Studio is stable. But cost savings begin day 1 of setup. "Useful soon." |
| **DEP** | 2 × 0.5 | agentmemory LLM calls route through 9router for compression (1 item). |
| **RISK** | 2 | Without it, external fallback is untracked. Operational inconvenience only — no compliance or security exposure. |
| **TTV** | 4 | Once running, compression is instant. Value in 1-3 days of setup + config. |

**Score = (4 + 4 + 3 + 1.0 + 2 + 4) / 5.5 = 3.27 → Phase 2 (Highest priority).**

#### 2. agent-skills — SDLC Workflow Encoding

| Factor | Score | Reasoning |
|--------|-------|-----------|
| **LTV** | 4 | SDLC discipline encoded as reusable skills (/spec, /plan, /build, /test, /review, /ship). High durability. Not foundational but broadly valuable. |
| **EFF** | 3 | /spec prevents scope-creep revisions; /review automates multi-axis checks. Meaningful friction reduction, not elimination. |
| **IMM** | 2 | No hard deadline. Users can follow steps manually today. Skills are an accelerant, not a gate. |
| **DEP** | 1 × 0.5 | Nothing else blocked on skills. |
| **RISK** | 2 | Without skills, users skip steps (no /spec = scope creep, no /test = coverage gaps). Quality risk only. |
| **TTV** | 4 | Markdown files. Once written, immediately invokable via `/load`. 2-3 days to write + test 8 skills. |

**Score = (4 + 3 + 2 + 0.5 + 2 + 4) / 5.5 = 2.82 → Phase 3 (Quick-win candidate).**

#### 3. agentmemory — Persistent Memory (BM25 + Vector + Graph) ⬆ REVISED

| Factor | Before | After | Reasoning |
|--------|--------|-------|-----------|
| **LTV** | 5 | 5 | Foundational. Unlocks cross-session context awareness and team knowledge distillation. |
| **EFF** | 3 | 3 | Reduces context re-setup (10-30 min per project, per session). Friction reduction, not elimination. |
| **IMM** | 2 | **3** | ⬆ Oz in beta now; ecosystem moving fast. Not building local memory = eventual Oz dependency. |
| **DEP** | 1 × 0.5 | 1 × 0.5 | No other backlog items currently blocked on agentmemory. |
| **RISK** | 2 | **3** | ⬆ Without local memory, teams default to cloud. Sovereignty risk. Compliance/audit trail stays local. |
| **TTV** | 2 | 2 | Observer pattern + server lifecycle + aider output parser + testing = 3-4 weeks. |

**Score = (5 + 3 + 3 + 0.5 + 3 + 2) / 5.5 = 3.00 → Phase 2/3 Boundary (Moved up from Phase 3).**

#### Summary: Priority Table

| Integration | Score | Phase | Oz Equivalent | Why Now? |
|---|---|---|---|---|
| **9router** | 3.27 | **Phase 2** | Oz's multi-harness routing | Lowest risk; token savings begin immediately |
| **agentmemory** | **3.00** | **Phase 2/3** ⬆ | Oz's cross-harness Agent Memory | Oz validated the idea; sovereignty window closing |
| **agent-skills** | 2.82 | Phase 3 | Oz's pluggable knowledge sources | TTV=4 makes it a quick-win after 9router |
| **Warp Oz** | 3.55 | *RULED OUT* | — | Offline constraint violation |

**Recommended sequencing:**

1. **9router first (Phase 2):** Unblocks token compression. Lowest implementation risk.
2. **agent-skills in parallel (Phase 3, high TTV):** Pure markdown, no infrastructure. Quick win.
3. **agentmemory later (Phase 2/3 boundary, elevated priority):** Highest long-term value but observer pattern needs design. Start after 9router is stable and patterns are clear.

---

## Part 1: Sovereignty Audit Findings

### 1.1 9router: Cost Optimization via Local Routing

#### What It Does
9router is a **Next.js-based OpenAI-compatible proxy** that sits between clients (Claude Code, Cursor, aider) and LLM providers. It routes requests, compresses tokens using RTK (Recursive Token Kompression), tracks costs, and handles provider fallbacks.

#### Architecture Match with pxx
| Aspect | 9router Design | pxx Fit |
|--------|---|---|
| **Bind address** | Default `127.0.0.1:20128` (loopback) | 🟢 Neo-only, matches pxx model |
| **Provider integration** | 3 auth patterns (OAuth, API key, custom-compatible) | 🟢 Can route to Studio Ollama + external fallbacks |
| **Fallback chains** | Account-level → Provider-level → Error-driven | 🟢 Neo: Studio → GLM/MiniMax → Kiro free tier |
| **State storage** | SQLite at `~/.9router/db.sqlite3` | 🟢 No external DB dependency |
| **Token compression** | RTK: format-specific filters (20-40% savings) | 🟢 Aider-specific compression (git diff, grep output) could save $100-200/month |
| **Configuration** | Dashboard UI (localhost:20128) or direct SQLite edits | 🟡 Needs headless config for Neo (no X11) |

#### RTK Compression Breakdown (Critical for Phase 5)
9router auto-detects 13 content types in first 2 KB of request:
- **git diff**: Truncates hunks to 100 lines, summarizes +/- counts
- **grep output**: Filters to match lines only, removes line numbers
- **find/ls output**: Keeps structure, strips metadata
- **build logs**: Removes timestamp spam, keeps only errors/warnings
- **tree output**: Collapses deep nesting

**Safety gates:**
- Never returns empty (fallback to original if filter produces nothing)
- Never grows input (original returned if compression fails)
- Skips if <500 bytes (too small) or >100 KB (risk of data loss)

**Aider-specific opportunity:** Aider sends tool outputs (test results, linting errors, code snippets) as `tool_result` messages. RTK could compress these by 25-35%, saving ~100 tokens/session.

#### Key Design Decision: 9router Local-Only Mode for Neo
9router ships with:
- Dashboard UI (requires localhost browser access)
- OAuth provider integrations (requires web login flows)
- Cloud sync option (reaches out to external service)

**Phase 5 approach:** Disable optional features on Neo, retain core:
- No dashboard (headless config via YAML or REST API)
- No OAuth (API key providers only: Anthropic, OpenAI, custom Ollama)
- No cloud sync (local SQLite only)

This is a **subset of 9router**, not a fork—9router's codebase already supports this via environment variables.

---

### 1.2 agentmemory: Persistent Memory Without External DB

#### What It Does
agentmemory is a **persistent memory server** that captures agent actions (tool calls, errors, decisions), compresses them via LLM, and makes them searchable using hybrid retrieval: BM25 (keyword), vector embeddings (semantic), knowledge graph (entities).

#### Architecture Match with pxx
| Aspect | agentmemory Design | pxx Fit |
|--------|---|---|
| **State storage** | iii engine state backend (opaque, no external DB) | 🟢 Could use same SQLite as pxx audit log |
| **Embedding provider** | Pluggable (OpenAI, Gemini, local Xenova, CLIP) | 🟢 Use local embeddings (384-dim) to avoid API calls |
| **BM25 index** | Custom in-memory, serialized to JSON | 🟢 Lightweight, auto-persists |
| **Graph extraction** | LLM extracts entities + relationships | 🟡 Uses same LLM as aider (context-hungry) |
| **Hybrid search** | RRF (Reciprocal Rank Fusion) merges three strategies | 🟢 Proven 95.2% retrieval accuracy |
| **Agent integration** | Hooks, MCP, REST API, NPM skills | 🟡 Aider doesn't have native hooks; need wrapper |

#### Memory Integration Points with pxx
**Three options:**

**Option A: Memory-as-a-service alongside aider (Recommended for Phase 5)**
```
Neo user runs: pxx --edit
  → pxx starts agentmemory server (port 3111)
  → pxx wraps aider with observer hooks:
      - on aider start: send session_start to agentmemory
      - on tool use: parse aider output, send post_tool_use
      - on aider exit: send session_end
  → aider runs as-is (no modifications)
  → agentmemory captures memory independently
```

**Option B: Aider plugin (Out of scope for Phase 5)**
Would require aider-side changes (MCP integration). Not compatible with pxx's orchestrator-not-orchestrated philosophy.

**Option C: Post-session distillation (Simpler, less real-time)**
After aider exits, parse transcript and batch-ingest to agentmemory. Slower, but doesn't require wrapper complexity.

**Phase 5 decision:** Option A (memory-as-a-service). Simplest to prototype, most reliable.

#### Memory Lifecycle for pxx
Observations captured from aider sessions:
```
Session 1: User runs pxx on project X
  → Aider runs; pxx observer watches tool outputs
  → CompressedObservation created: {facts, concepts, narrative}
  → Stored in agentmemory KV (scoped to project X)

Session 2: User runs pxx on project X, asks "how did we solve this error last time?"
  → pxx injects into system prompt: "Relevant memory: ..."
  → Aider sees context without explicit prompting
  → No re-explaining, faster convergence

Session 3: User runs pxx on project Y (different project)
  → Memory isolation: only project X memories filtered out
  → Project Y builds from scratch or pulls cross-project lessons
```

**Token budget:** agentmemory's default is 2000 tokens/search result. On Neo (8GB), expect 100-200 observations per project before vectors exceed 50 MB. Practical limit: ~5 projects worth of memories before archival needed.

---

### 1.3 agent-skills: Workflow Encoding for Aider Sessions

#### What It Does
agent-skills encodes production SDLC workflows (/spec, /plan, /build, /test, /review, /ship) as markdown-based reusable **skills**. Skills are invoked as `/slash_commands` in agent sessions. Each skill has 4 properties: objective, steps, outputs, decision criteria.

#### Architecture Match with pxx
| Aspect | agent-skills Design | pxx Fit |
|--------|---|---|
| **Skill format** | YAML frontmatter + markdown workflow | 🟢 Already used by pxx (pxx/prompts/system.md) |
| **Invocation** | `/command` in aider chat | 🟢 Aider supports `/load <file>` natively |
| **Statefulness** | No central state (rules, spec, plan, commits, conversation) | 🟢 Matches pxx's distributed state model |
| **Personas** | Pre-built (code-reviewer, auditor, test-engineer) | 🟡 pxx has no subagent support today |
| **Extensibility** | Users add custom skills to `.claude/skills/` | 🟢 Aligns with pxx's `.claude/` convention |
| **Error handling** | Structured debugging, stop-the-line, rollback plans | 🟡 Aider's exit codes are coarse; would need wrapping |

#### Skills Applicable to pxx/aider
Nine reusable, production-hardened workflows:

**Core Lifecycle** (Use in most sessions):
1. **`/spec`** — Capture requirements before coding. Forces assumption surfacing + success criteria. Prevents scope creep.
2. **`/plan`** — Decompose into vertical slices (not layers). Guards against broad-but-shallow implementation.
3. **`/build`** — TDD RED-GREEN-REFACTOR discipline. Prevents untested code from merging.
4. **`/test`** — Test pyramid (80/15/5), DAMP over DRY, concurrency testing.
5. **`/review`** — 5-axis code review (correctness, readability, architecture, security, perf). Parallel review without bottleneck.

**Specialist Workflows** (Use for high-stakes or complex changes):
6. **`/ship`** — Pre-deployment orchestration: 3 specialist personas review in parallel, rollback plan mandatory.
7. **`/security-audit`** — Security-focused review lens (from /review's 5-axis).
8. **`/performance-profile`** — Perf testing, bottleneck identification, optimization guardrails.

**Maintenance Workflows** (Use for refactors, tech debt):
9. **`/code-simplify`** — Simplification + reuse, no behavior change. Anti-rationalization: explains why simpler is better.

#### Integration Pattern: Skills in pxx
**Option 1: Load skills from aider `/load` (Simplest)**
```bash
pxx --edit  # User in aider chat
# Inside aider:
/load /Users/you/ai/pxx/pxx/commands/spec.md
/load /Users/you/ai/pxx/pxx/commands/plan.md
```

User manually invokes skills via aider's `/load` mechanism. No pxx changes needed.

**Option 2: Pre-load skills in system prompt (Medium effort)**
```
pxx spawns aider with:
  --read pxx/commands/spec.md
  --read pxx/commands/plan.md
  ...
```

Skills always available in context window (at cost of tokens).

**Option 3: Skill discovery + auto-suggest (Out of scope for Phase 5)**
Analyze task description, suggest relevant skills. Requires NLP or heuristics.

**Phase 5 decision:** Option 1 + template in system prompt noting available skills. User drives invocation.

---

## Part 2: Phase 5 Integration Design

### 2.1 Architectural Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Neo (MacBook 8GB)                                                        │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ pxx orchestrator                                                  │   │
│  │ (supervisor process)                                              │   │
│  │                                                                   │   │
│  │  Detects Studio → Picks model → Runs safety checks               │   │
│  │  Spawns: [9router, agentmemory, aider] as sub-processes          │   │
│  │                                                                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │   │
│  │  │  9router     │  │agentmemory   │  │  aider               │  │   │
│  │  │:20128        │  │:3111         │  │  (stdout observed)   │  │   │
│  │  │              │  │              │  │                      │  │   │
│  │  │ RTK compress │  │ BM25+Vec+Gr  │  │ Reads from 9router   │  │   │
│  │  │ Provider RTG │  │ Captures obs │  │ Writes aider output  │  │   │
│  │  │ Local SQLite │  │ Local SQLite │  │ Agentmemory observer │  │   │
│  │  └──────────────┘  └──────────────┘  │ hooks agent to mem   │  │   │
│  │         ▲                ▲            └──────────────────────┘  │   │
│  │         │                │                    ▲                  │   │
│  │         └─────────────────┴────────────────────┘                │   │
│  │         (pxx observer subprocess)                                │   │
│  │         Monitors aider stdout/stderr, sends hook                │   │
│  │         payloads to agentmemory on tool outputs                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│         ▲                                                                 │
│         │ OPENAI_API_BASE=http://127.0.0.1:20128/v1                     │
│         │ (aider + other Claude Code tools point here)                   │
│         │                                                                 │
└─────────┼─────────────────────────────────────────────────────────────────┘
          │
          │ HTTP to port 20128 + 3111
          │
┌─────────┼─────────────────────────────────────────────────────────────────┐
│ Studio (M4 Max)                                                            │
│                                                                            │
│  Ollama :11434 (devstral:24b, default)                                    │
│  (9router can fall back to GLM/MiniMax/Kiro if Studio unreachable)        │
└────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Subprocess Lifecycle (pxx as supervisor)

**Current state (Phase 4):** pxx detects endpoint, picks model, `os.execv` into aider. Aider runs; pxx exits.

**Phase 5 addition:** pxx stays alive, supervises three child processes:

```python
# Pseudocode: pxx/cli.py main() circa Phase 5

def main():
    endpoint = detect_endpoint()  # e.g., Studio
    model = model_for(endpoint)   # devstral:24b
    
    # Phase 5: Start supervisor mode (if --with-router or --with-memory)
    if args.with_router:
        router_proc = subprocess.Popen(['9router'], env={...})
    
    if args.with_memory:
        memory_proc = subprocess.Popen(['agentmemory'], env={...})
        observer_thread = start_observer(aider_stdout)  # Hooks observer
    
    # Set OPENAI_API_BASE to point to 9router
    env['OPENAI_API_BASE'] = 'http://127.0.0.1:20128/v1'
    
    # Run aider (no longer execv; stays subprocess)
    aider_proc = subprocess.Popen(['aider', ...], stdout=PIPE, stderr=PIPE)
    
    # Observer thread watches aider stdout:
    # - Parses tool calls and outputs
    # - Sends POST to agentmemory /mem/observe
    
    # On aider exit:
    # - Clean up 9router, agentmemory subprocesses
    # - Return exit code
```

**Trade-off:** pxx no longer uses `os.execv` (would simplify to no parent overhead). Instead, parent process stays alive, manages child processes. **Pro:** Cleaner shutdown (can flush memory indices). **Con:** Small memory/CPU overhead for supervisor.

---

### 2.3 9router Integration: Headless Config for Neo

**Challenge:** 9router ships with a web dashboard (localhost:20128/). Neo has no display.

**Solution:** Headless config via YAML + REST API, no UI needed.

#### Configuration Files (Phase 5 design)

**Location:** `~/.9router/config.yml` (replaces SQLite for initial setup)

```yaml
server:
  bind: 127.0.0.1:20128
  api_key_required: false  # Localhost trust model

providers:
  # Primary: Studio Ollama
  studio_ollama:
    type: openai_compatible
    base_url: http://workstation:11434/v1
    model: devstral:24b
    priority: 1

  # Fallback 1: Cheap provider
  glm:
    type: api_key
    provider: zhipu  # GLM provider
    api_key: ${GLM_API_KEY}
    model: glm-4-vision
    priority: 2
    cooldown: 60s

  # Fallback 2: Free tier
  kiro:
    type: api_key
    provider: kiro
    api_key: ${KIRO_API_KEY}
    model: general
    priority: 3
    cooldown: 300s

combos:
  # Route sequence: try Studio first; on failure, GLM; on failure, Kiro
  default:
    - studio_ollama
    - glm
    - kiro
    fallback_strategy: exponential_backoff

compression:
  enabled: true
  formats:
    git_diff:
      enabled: true
      max_hunk_lines: 100
    grep:
      enabled: true
    find:
      enabled: true
    build_output:
      enabled: true
  safety_gates:
    min_input_size: 500
    max_input_size: 100_000
    never_empty: true
```

**Secrets management:** 
- `${GLM_API_KEY}` reads from environment (not stored in config)
- pxx passes through env vars on startup

#### REST API for Querying State

9router's `/v1/usage` endpoint (existing in codebase):
```bash
curl http://localhost:20128/v1/usage

# Response:
{
  "session_tokens": 1234,
  "cost_usd": 0.045,
  "providers": {
    "studio_ollama": { "tokens": 1000, "cost": 0 },
    "glm": { "tokens": 234, "cost": 0.045 }
  },
  "compression": {
    "tokens_saved": 456,
    "formats_used": ["git_diff", "grep"]
  }
}
```

pxx can query this for cost reporting in `--doctor` output.

#### pxx Integration Code (Phase 5 additions)

**File:** `pxx/router.py` (new module)

```python
import subprocess
import requests
import os
from pathlib import Path

class NineroterManager:
    """Lifecycle management for 9router subprocess."""
    
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.process = None
        self.api_base = "http://127.0.0.1:20128"
    
    def start(self) -> None:
        """Start 9router subprocess."""
        env = os.environ.copy()
        env['NINE_ROUTER_CONFIG'] = str(self.config_path)
        env['NODE_ENV'] = 'production'
        
        self.process = subprocess.Popen(
            ['9router'],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for port 20128 to be ready
        self._wait_for_ready(timeout=5)
    
    def stop(self) -> None:
        """Gracefully terminate 9router."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
    
    def get_usage(self) -> dict:
        """Query token usage and cost."""
        resp = requests.get(f"{self.api_base}/v1/usage", timeout=2)
        return resp.json()
    
    def get_status(self) -> dict:
        """Query provider status and fallback chain."""
        resp = requests.get(f"{self.api_base}/v1/status", timeout=2)
        return resp.json()
    
    def _wait_for_ready(self, timeout: int = 5) -> None:
        """Block until 9router responds to health check."""
        import time
        start = time.time()
        while time.time() - start < timeout:
            try:
                requests.get(f"{self.api_base}/health", timeout=1)
                return
            except requests.ConnectionError:
                time.sleep(0.1)
        raise TimeoutError("9router failed to start within timeout")
```

---

### 2.4 agentmemory Integration: Observer Pattern

**Challenge:** aider doesn't expose hooks. Need to observe aider's stdout/stderr to extract tool calls and responses.

**Solution:** Subprocess observer that parses aider's output format and converts to agentmemory events.

#### Observer Design

**File:** `pxx/memory.py` (new module)

```python
import re
import json
from typing import Optional, Iterator
from dataclasses import dataclass

@dataclass
class ToolCall:
    """Parsed aider tool call."""
    tool_name: str
    tool_input: dict
    timestamp: str  # ISO 8601

@dataclass
class ToolResult:
    """Aider tool result."""
    tool_result: str
    success: bool
    timestamp: str

class AiderOutputParser:
    """Parse aider stdout/stderr to extract tool calls and results."""
    
    def parse_stream(self, stdout_iter: Iterator[str]) -> Iterator[tuple[str, dict]]:
        """
        Yield (event_type, payload) tuples.
        
        Event types: tool_call, tool_result, error, conversation_start
        """
        for line in stdout_iter:
            # Aider formats tool calls as JSON blocks (configurable via --edit-format)
            if line.startswith("{") and '"tool_name"' in line:
                try:
                    obj = json.loads(line)
                    if "tool_name" in obj:
                        yield ("tool_call", obj)
                except json.JSONDecodeError:
                    pass
            
            # Aider prefixes tool results with marker (configurable)
            if line.startswith("<tool_result>"):
                # Extract content until </tool_result>
                result_text = self._extract_tag("tool_result", line)
                yield ("tool_result", {"output": result_text, "success": True})
            
            # Parse conversation markers
            if "starting session" in line.lower():
                yield ("conversation_start", {})
```

#### Observer Subprocess

**File:** `pxx/observer.py` (new module)

```python
import subprocess
import requests
from threading import Thread
from queue import Queue
import json
from datetime import datetime

class AiderMemoryObserver:
    """Watch aider subprocess and pipe events to agentmemory."""
    
    def __init__(self, aider_proc: subprocess.Popen, memory_api_base: str = "http://127.0.0.1:3111"):
        self.aider = aider_proc
        self.memory_api = memory_api_base
        self.queue = Queue()
        self.thread = Thread(target=self._run, daemon=True)
    
    def start(self) -> None:
        """Start observer thread."""
        self.thread.start()
    
    def _run(self) -> None:
        """Main observer loop: read aider output, parse, send to memory."""
        parser = AiderOutputParser()
        
        for line in iter(self.aider.stdout.readline, b''):
            line_str = line.decode('utf-8', errors='replace')
            
            for event_type, payload in parser.parse_stream([line_str]):
                if event_type == "tool_call":
                    self._send_to_memory({
                        "hook_type": "pre_tool_use",
                        "data": payload,
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif event_type == "tool_result":
                    self._send_to_memory({
                        "hook_type": "post_tool_use",
                        "data": {
                            "tool_output": payload["output"],
                            "success": payload["success"]
                        },
                        "timestamp": datetime.now().isoformat()
                    })
    
    def _send_to_memory(self, hook_payload: dict) -> None:
        """POST hook event to agentmemory."""
        try:
            resp = requests.post(
                f"{self.memory_api}/mem/observe",
                json=hook_payload,
                timeout=2
            )
            if resp.status_code != 200:
                # Log but don't block aider on memory failures
                print(f"Memory observe failed: {resp.status_code}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"Memory connection error: {e}", file=sys.stderr)
```

#### Memory Server Configuration

**File:** `~/.agentmemory/.env` (auto-created by pxx)

```ini
# Use local embeddings (no API calls)
EMBEDDING_PROVIDER=local
# Xenova/all-MiniLM-L6-v2 (384 dims, ~50MB for 1000 observations)

# LLM for compression: use Studio (same as aider)
ANTHROPIC_API_KEY=unused  # Fallback; pxx will override
ANTHROPIC_MODEL=claude-opus-4-8

# Weights for hybrid search (default: 0.4, 0.6, 0.3)
BM25_WEIGHT=0.5  # Boost keyword search for aider outputs
VECTOR_WEIGHT=0.5

# Auto-compress observations immediately (no batching delay)
AGENTMEMORY_AUTO_COMPRESS=true

# Memory retention (7 days before archive)
MEMORY_ARCHIVE_AFTER_DAYS=7

# State storage (iii engine path; pxx will configure)
STATE_BACKEND=sqlite
STATE_PATH=~/.pxx/memory.db
```

---

### 2.5 agent-skills Integration: Skill Loading & Discovery

**Design:** Skills as markdown files, loadable via aider's `/load` mechanism.

#### Skill Structure

**File:** `pxx/commands/spec.md` (existing, extended for Phase 5)

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

Use this command to capture requirements and surface assumptions **before** writing code.

## Objective
Lock down what success looks like, what's in scope, and what's not.

## Steps

### 1. Capture Core Areas (with examples)
...
```

**Available skills in Phase 5:**
```
pxx/commands/
├── spec.md          # Requirement capture
├── plan.md          # Vertical slicing
├── build.md         # TDD RED-GREEN-REFACTOR
├── test.md          # Test pyramid
├── review.md        # 5-axis review
├── ship.md          # Pre-deployment
├── security-audit.md  # Security lens
└── simplify.md      # Code simplification
```

#### Skill Invocation Pattern

**In aider session:**
```
> /load /Users/you/.pxx/commands/spec.md

> Now let's follow the spec workflow for the auth feature...
```

Aider loads the skill markdown into context; user references it while working.

#### Skill Discovery in pxx

**New command:** `pxx --list-skills`

```bash
$ pxx --list-skills

Available Skills:
  /spec (discovery)        Capture requirements before building
  /plan (discovery)        Decompose into vertical slices
  /build (execution)       RED-GREEN-REFACTOR TDD cycle
  /test (validation)       Test pyramid (80/15/5)
  /review (qa)             5-axis code review
  /ship (deployment)       Pre-launch orchestration
  /security-audit (qa)     Security-focused review
  /simplify (maintenance)  Code simplification & reuse

Load with: /load /Users/you/.pxx/commands/SKILL.md
```

---

## Part 3: Implementation Roadmap (Phase 5)

### Tier 1: Core Infrastructure (Weeks 1-2)

**Goal:** 9router + agentmemory running as supervised subprocesses on Neo.

**Tasks:**
1. **9router headless config** (`pxx/router.py`)
   - Read config from `~/.9router/config.yml` or env
   - Start subprocess, wait for health check
   - Query usage via REST API
   - Stop gracefully on pxx exit

2. **agentmemory lifecycle** (`pxx/memory.py`, `pxx/observer.py`)
   - Start subprocess with local embeddings
   - Implement aider output parser (extract tool calls)
   - Observer thread that sends hook events to memory
   - Graceful shutdown, flush indices

3. **pxx supervisor mode**
   - Add `--with-router` and `--with-memory` flags
   - Spawn subprocesses in sequence (9router → agentmemory)
   - Point aider to 9router via `OPENAI_API_BASE`
   - Clean up on exit

4. **Tests**
   - Unit tests for parser (tool call extraction)
   - Integration test: 3 subprocesses start, health check, stop cleanly
   - Verify aider receives requests via 9router

**Acceptance:** `pxx --edit --with-router --with-memory` on Neo → aider runs, costs tracked, memories captured.

---

### Tier 2: Agent Memory Integration (Weeks 2-3)

**Goal:** Aider sessions start with relevant past context injected.

**Tasks:**
1. **Memory injection into system prompt**
   - On aider startup, query agentmemory for top-5 relevant memories (project-scoped)
   - Inject as "Recalled Memory" block in system prompt
   - Token-budget aware (don't exceed context limit)

2. **Project-scoped memory isolation**
   - Tag observations by project path (from `cwd`)
   - On search, filter by current project
   - Cross-project search as opt-in (flag: `--cross-project-memory`)

3. **Memory hygiene**
   - Auto-archive observations older than 7 days
   - Periodic index compaction (on startup if index > 100 MB)
   - User command: `pxx --purge-memory` (delete all memories for a project)

4. **Tests**
   - Unit test: memory search filters by project
   - Integration test: session 1 captures observation, session 2 retrieves it
   - Verify context injection doesn't break aider behavior

**Acceptance:** User runs two sessions on same project; session 2 has context from session 1 in system prompt.

---

### Tier 3: Skills & Workflows (Weeks 3-4)

**Goal:** Reusable, production-hardened workflows available in aider.

**Tasks:**
1. **Skill files finalization**
   - Adapt agent-skills workflows to aider's format
   - Write /spec, /plan, /build, /test, /review templates
   - Add examples and decision tables

2. **Skill discovery**
   - `pxx --list-skills` command
   - Skill metadata in frontmatter (complexity, phase, time estimate)
   - Auto-suggest relevant skill in `--doctor` output

3. **Integration with system prompt**
   - Add "Available Skills" section to pxx/prompts/system.md
   - Note how to invoke: `/load /path/to/skill.md`
   - Template for custom skills in `.claude/commands/`

4. **Tests**
   - Verify skill files are valid markdown
   - Check all links/references resolve
   - Lint skills for consistency (section headings, code examples)

**Acceptance:** User can `pxx --list-skills`, load a skill, follow its workflow in aider.

---

### Tier 4: Cost & Observability (Weeks 4-5)

**Goal:** Visibility into token usage, compression, and fallback chains.

**Tasks:**
1. **Cost reporting in `--doctor`**
   - Query 9router `/v1/usage` and display:
     - Tokens used this session
     - Tokens saved via compression
     - Cost by provider
     - Fallback chain status

2. **Memory stats in `--doctor`**
   - Observations in KB (size of indices)
   - Projects with memories (list)
   - Last auto-compress timestamp
   - Retrieval accuracy (if tracked)

3. **Audit log integration**
   - Add 9router and agentmemory startup/shutdown to audit log
   - Log token compression stats per session
   - Track memory injection (how much context added, which memories used)

4. **Tests**
   - Verify `--doctor` output includes router + memory stats
   - Check audit log entries are well-formed

**Acceptance:** `pxx --doctor` shows comprehensive status of all three subsystems.

---

## Part 4: Trade-offs & Constraints

### Memory Trade-offs

| Decision | Pro | Con | Phase 5 Scope |
|----------|-----|-----|---|
| **Observer pattern** (watch stdout) | No aider changes needed; decoupled | Fragile to aider format changes | Accept; add regression test |
| **Local embeddings** (Xenova) | No API calls, $0 cost | Slower inference (CPU-bound on Neo) | Accept; async observer |
| **3-month memory archive** | Don't fill disk | Users lose old context | Add `--cross-project-memory` for recall |
| **BM25-heavy weights** (0.5/0.5) | Better for code/error patterns | Less semantic fuzzing | Tunable via env |

### Router Trade-offs

| Decision | Pro | Con | Phase 5 Scope |
|----------|-----|-----|---|
| **Headless config (YAML)** | No UI overhead | Manual editing needed for new providers | Accept; REST API for CRUD in future |
| **No OAuth** | Simpler, local-only | Can't use OAuth providers | Use API key fallbacks |
| **SQLite persistence** | Zero external DB | Can't sync across machines | Document limitations |
| **RTK compression** | 20-40% token savings | Format-specific, may miss edge cases | Start with common formats; expand later |

### Architectural Constraints

1. **pxx stays supervisor, not orchestrator**: aider remains autonomous. pxx's job is observing + routing, not prompting aider to use specific skills.

2. **All state is local**: Memories, router config, audit logs all live on Neo. No cloud sync. Implications: single-machine setup, manual backups if needed.

3. **Token budget is global**: If aider + agentmemory + 9router tracking compete for context, aider wins (takes first 90% of budget).

---

## Part 5: Integration Checkpoints & Validation

### Validation Checklist

**Tier 1 Complete:**
- [ ] 9router starts/stops cleanly; health check works
- [ ] agentmemory starts/stops cleanly; index persists
- [ ] Aider requests routed through 9router (verify via curl to 20128)
- [ ] Aider still generates correct code (behavior unchanged)
- [ ] `--doctor` shows both router + memory running

**Tier 2 Complete:**
- [ ] Session 1: user runs pxx, makes a tool call, aider output captured
- [ ] Session 2: new pxx run, system prompt includes memory from session 1
- [ ] Memory doesn't leak across projects (scoping works)
- [ ] Context injection doesn't break aider's parsing

**Tier 3 Complete:**
- [ ] `pxx --list-skills` lists 8 skills
- [ ] `/load /path/to/spec.md` works in aider chat
- [ ] User can follow spec workflow end-to-end
- [ ] Skills markdown is well-formed (lint passes)

**Tier 4 Complete:**
- [ ] `pxx --doctor` shows token compression %, cost, memory stats
- [ ] Audit log includes router events, memory injections
- [ ] Cost goes down vs. baseline (measure: tokens/session without compression)

---

## Part 6: Success Metrics (Post-Phase 5)

### Quantitative Targets

1. **Token compression:** 25-35% savings on typical aider sessions (git diff, grep, test output)
2. **Cost reduction:** 20-30% lower API costs (Studio Ollama is free; compression reduces fallback usage)
3. **Context recall:** 70%+ of relevant past memories retrieved on new sessions (A/B test: with/without memory injection)
4. **Skill adoption:** 60%+ of users invoke at least one skill per project (measured via audit log)

### Qualitative Targets

1. **User feedback:** "Faster convergence on familiar problems" (thanks to memory)
2. **Operational clarity:** `--doctor` output gives users confidence in system health
3. **Workflow discipline:** Spec+Plan→Build workflow reduces back-and-forth revisions

---

## Part 7: Risk Mitigation

### Risk: aider Output Parser Brittleness
**Mitigation:** 
- Add configurable markers for tool call detection (env var `AIDER_TOOL_MARKER`)
- Fallback: if parser fails, skip hook (don't crash observer)
- Monitor stderr for parse errors; log to audit

### Risk: Memory Index Grows Without Bound
**Mitigation:**
- Observation deduplication (hash-based, prevent duplicates)
- Auto-archive on startup if index > 200 MB
- User command `pxx --prune-memory` for manual cleanup

### Risk: 9router Fallback Chain Thrashing
**Mitigation:**
- Per-provider cooldowns (exponential backoff)
- Status polling via `/v1/status` endpoint
- `--doctor` displays fallback chain state + last error per provider

### Risk: Memory Injection Breaks Aider Parsing
**Mitigation:**
- Inject memory after initial system prompt, before task description
- Token-budget aware (never exceed 90% of context)
- A/B test: disable memory injection, verify aider still works

---

## Conclusion

Phase 5 transforms pxx into a **coordination layer** for modern AI tooling:

- **9router** handles cost optimization (token compression, provider routing)
- **agentmemory** handles context persistence (hybrid retrieval, project scoping)
- **agent-skills** handles workflow discipline (reusable, production-hardened skills)

All three tools are **localhost-only, zero-external-DB**, and **battle-tested** on their own. Phase 5 focuses on integrating them into pxx's ecosystem without forking or modifying their core logic.

**Key principle:** pxx remains a **supervisor and observer**, not an orchestrator. Aider stays autonomous. Users drive workflows. Tools provide guardrails and memory, not constraints.

**Estimated effort:** 8 weeks (Tiers 1-4), with measurable outcomes at each milestone.

---

## Appendix A: File Structure (Phase 5)

```
pxx/
├── cli.py                      # Modified: supervisor mode
├── endpoints.py                # Unchanged
├── safety.py                   # Unchanged
├── scope.py                    # Unchanged
├── audit.py                    # Modified: log router/memory events
├── router.py                   # NEW: 9router lifecycle
├── memory.py                   # NEW: agentmemory config + API
├── observer.py                 # NEW: aider output parser + observer thread
├── commands/
│   ├── spec.md                 # NEW: specification workflow
│   ├── plan.md                 # NEW: vertical slicing workflow
│   ├── build.md                # NEW: TDD RED-GREEN-REFACTOR
│   ├── test.md                 # NEW: test pyramid workflow
│   ├── review.md               # NEW: 5-axis review workflow
│   ├── ship.md                 # NEW: pre-deployment workflow
│   ├── security-audit.md       # NEW: security-focused review
│   └── simplify.md             # NEW: code simplification
├── prompts/
│   └── system.md               # Modified: add "Available Skills" section
└── tests/
    ├── test_router.py          # NEW: 9router integration tests
    ├── test_observer.py        # NEW: aider output parser tests
    └── test_memory.py          # NEW: agentmemory lifecycle tests

~/.9router/
└── config.yml                  # NEW: headless configuration

~/.agentmemory/
└── .env                        # NEW: local embeddings, compression tuning

~/.pxx/
├── audit.db                    # Existing: extended with router/memory events
├── memory.db                   # NEW: agentmemory state storage
└── memory-indices/             # NEW: persisted BM25 + vector indices
    ├── bm25.json
    ├── vectors.bin
    └── graph.json
```

---

## Appendix B: Environment Variables (Phase 5)

| Variable | Default | Purpose |
|----------|---------|---------|
| `PXX_WITH_ROUTER` | `0` | Set to `1` to enable 9router |
| `PXX_WITH_MEMORY` | `0` | Set to `1` to enable agentmemory |
| `NINE_ROUTER_CONFIG` | `~/.9router/config.yml` | 9router config path |
| `NINE_ROUTER_PORT` | `20128` | 9router listen port |
| `AGENTMEMORY_CONFIG` | `~/.agentmemory/.env` | agentmemory env file |
| `AGENTMEMORY_PORT` | `3111` | agentmemory listen port |
| `BM25_WEIGHT` | `0.5` | Hybrid search: keyword weight |
| `VECTOR_WEIGHT` | `0.5` | Hybrid search: semantic weight |
| `MEMORY_ARCHIVE_DAYS` | `7` | Days before auto-archiving observations |
| `PXX_CROSS_PROJECT_MEMORY` | `0` | Set to `1` to recall memories from other projects |

