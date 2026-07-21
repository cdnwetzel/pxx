# Phase 5 Tuning & Observability Guide

> **Status: experimental, source-only.** This guide covers the optional
> memory and router services (`services/agentmemory`, `services/9router`),
> which are not part of the PyPI package. Several features described here are
> **not wired on the production path** in this release — each section says so
> explicitly. Nothing here applies to the core `pxx` install.

This guide covers tuning and observability for Phase 5 features: memory
settings (Tier 2), memory enhancement (Tier 3a), and metrics (Tier 4).

## Table of Contents

1. [Memory Tuning](#memory-tuning)
2. [Cost Analysis](#cost-analysis)
3. [Diagnostics & Doctor](#diagnostics--doctor)
4. [Troubleshooting](#troubleshooting)

---

## Memory Tuning

### Configuration

Memory settings are controlled via environment variables. Set these before
starting pxx:

```bash
# Relevance threshold (0.0-1.0, default 0.5)
export PXX_MEMORY_THRESHOLD=0.6

# Maximum number of observations per retrieval (default 5)
export PXX_MEMORY_LIMIT=8

# Maximum context size in characters (default 8000)
export PXX_MEMORY_MAX_CONTEXT=10000

# Reserved headroom for aider responses (default 2000)
export PXX_MEMORY_HEADROOM=3000

# Run drift check before every --edit session (default off)
export PXX_AUTOCHECK_DRIFT=1
```

> **Note:** these variables configure the retrieval/tuning layer
> (`pxx/memory_tuning.py`). Automatic injection of observations into aider
> sessions is **not wired** in this release, so today they only affect direct
> use of the memory API (e.g. `/inject` with its own limits). They are
> documented here for the experimental service, not as production behavior.

### Tuning Strategy

**Goal:** keep retrieved observations relevant and within a sane size budget.

#### Rule 1: Start Conservative

Begin with tight defaults, then loosen:

```bash
# Session 1: Tight retrieval
export PXX_MEMORY_THRESHOLD=0.7  # Only high-confidence observations
export PXX_MEMORY_LIMIT=3        # Few observations per query

# See the available slash-command prompts
pxx --list-commands

# Session 2: Loosen if recall is too narrow
export PXX_MEMORY_THRESHOLD=0.5
export PXX_MEMORY_LIMIT=5
```

#### Rule 2: Monitor Retrieval Quality

Query the service directly to see what a query returns:

```bash
curl -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{"project": "default", "query": "authentication bug", "limit": 5}'
```

If searches frequently return 0 results, lower `PXX_MEMORY_THRESHOLD` or
increase `PXX_MEMORY_LIMIT`.

#### Rule 3: Balance Context

Keep retrieved context small relative to the model's window:

```bash
# Example: Context window = 128k tokens = ~512k characters

# Constrain memory context if aider feels crowded:
export PXX_MEMORY_MAX_CONTEXT=5000      # Reduce memory budget
export PXX_MEMORY_HEADROOM=5000         # Increase aider headroom
```

### Observation Lifecycle

Observations are stored per project and can be managed through the service
API (`/command` endpoint):

```bash
# recall — search observations
curl -X POST http://127.0.0.1:3111/command \
  -d '{"project": "default", "command": "recall", "args": {"query": "build errors"}}'

# remember — save a note manually
curl -X POST http://127.0.0.1:3111/command \
  -d '{"project": "default", "command": "remember", "args": {"title": "Bug fix", "content": "..."}}'

# forget — delete an observation
curl -X POST http://127.0.0.1:3111/command \
  -d '{"project": "default", "command": "forget", "args": {"id": "obs-123"}}'
```

### Analytics

View memory usage patterns:

```bash
# (Future) Memory analytics API
# Track most-retrieved observations, cold observations, hit rates
```

---

## Cost Analysis

> **Not wired in this release.** The router does not expose a usage endpoint
> and the cost-metrics module is not called on the production path, so
> token/cost fields are not populated. This section describes the intended
> accounting model, kept for when the wiring lands.

### Session Cost Metrics (intended)

The design calls for post-session metrics including:

- **Tokens:** prompt, completion, cached, effective
- **Cache hit rate:** percentage of tokens served from cache
- **Memory:** observation count, total size
- **Router:** request count, latency
- **Estimated cost:** USD based on token usage

### Token Accounting (intended)

The planned model tracks three types of tokens:

1. **Prompt tokens:** Input to the model (aider context + your instructions)
2. **Completion tokens:** Model output (aider edits, responses)
3. **Cached tokens:** Tokens in prompt cache (discounted ~90%)

**Effective tokens** = (completion tokens) + (cached tokens * 0.1)

Example:
```
Prompt: 10,000 tokens (8,000 cached, 2,000 uncached)
Completion: 500 tokens
Estimated cost: (2,000 + 8,000*0.1 + 500) * $0.012/1k = $0.048
vs. uncached: (10,000 + 500) * $0.012/1k = $0.126
Savings: 62%
```

### Cost Optimization

#### Token Efficiency

- **Use `/spec` and `/plan`** before `/build` to guide the model (cheaper edits)
- **Load relevant skills** (`/load pxx/commands/spec.md`) to frame the task
- **Ask narrowly** — specific requests cost fewer tokens than vague ones

---

## Diagnostics & Doctor

### Running Doctor

Check system health:

```bash
pxx --doctor
```

`pxx --doctor` health-checks the optional services and reports repository
state. In this release the services expose only health/version information,
so router latency/error-rate and memory hit-rate fields are not populated.

### Doctor Output

Example:

```
=== pxx doctor ===

Routing & Memory:
  9router (http://127.0.0.1:20128): OK
  agentmemory (http://127.0.0.1:3111): OK
```

**Red flags:**

- `9router: unreachable` — Router is not running. It is normally started by
  `pxx --with-router` (or run `nine-router` yourself; it binds the fixed
  address `127.0.0.1:20128`).
- `agentmemory: unreachable` — agentmemory is not running. Start it:
  `agentmemory` (env `PXX_MEMORY_HOST` / `PXX_MEMORY_PORT`, default
  `127.0.0.1:3111`).

### Environment Variables for Doctor

```bash
# Router endpoint
export PXX_ROUTER_API=http://127.0.0.1:20128

# Memory endpoint
export PXX_MEMORY_API=http://127.0.0.1:3111
```

---

## Troubleshooting

### Memory Service Issues

**Symptom:** `--with-memory` fails to start or find the service.

**Solution:**

1. Check if agentmemory is running:
   ```bash
   pxx --doctor
   ```

2. Check the fixed port is free:
   ```bash
   lsof -i :3111
   ```

3. To run without the memory service, omit `--with-memory` (that is the
   default).

### Observations Are Never Found

**Symptom:** Searches return "No observations found" even for recent fixes.

**Solution:**

1. Check relevance threshold:
   ```bash
   # Lower the threshold to match broader queries
   export PXX_MEMORY_THRESHOLD=0.3
   ```

2. Verify the service has observations:
   ```bash
   curl http://127.0.0.1:3111/project/default/stats
   # If observation_count is low, nothing has been stored yet — remember
   # that only post-session summaries (and manual /remember notes) are
   # captured in this release.
   ```

3. Save findings manually:
   ```bash
   curl -X POST http://127.0.0.1:3111/command \
     -d '{"project": "default", "command": "remember", "args": {"title": "Search phrase", "content": "Detailed content"}}'
   ```

### Router is Not Routing

**Symptom:** `pxx --with-router` doesn't reach a secondary backend.

**Solution:**

1. Verify router is running:
   ```bash
   pxx --doctor
   ```

2. Check what the proxy forwards to — it is a single-upstream proxy to the
   Ollama endpoint in `PXX_OLLAMA_BASE` (there is no fallback chain in this
   release):
   ```bash
   curl http://127.0.0.1:20128/health
   ```

---

## Advanced Topics

### Custom Tuning Profiles

Create shell functions for different use cases:

```bash
# Tight retrieval for small changes
alias pxx-tight='PXX_MEMORY_THRESHOLD=0.7 PXX_MEMORY_LIMIT=3 pxx'

# Loose retrieval for exploration
alias pxx-loose='PXX_MEMORY_THRESHOLD=0.3 PXX_MEMORY_LIMIT=10 pxx'
```

### Audit Log Analysis

Review session records from the audit log (`~/.local/state/pxx/sessions/`):

```bash
# List session events
jq -r '.event' ~/.local/state/pxx/sessions/*.jsonl | sort | uniq -c

# Note: token/cost fields are not populated in this release (see
# "Cost Analysis" above), so cost-based queries will come up empty.
```

---

## Summary

- **Start conservative:** tight thresholds, small limits
- **Check retrieval quality:** query `/search` directly to verify observations are useful
- **Balance context:** reserve enough headroom for aider
- **Use doctor regularly:** check service health before sessions
- **Load skills early:** `/load` prompts guide the model and reduce revisions
