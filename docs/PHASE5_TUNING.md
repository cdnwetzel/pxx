# Phase 5 Tuning & Observability Guide

This guide covers tuning and observability for Phase 5 features: memory injection (Tier 2), memory enhancement (Tier 3a), and cost metrics (Tier 4).

## Table of Contents

1. [Memory Tuning](#memory-tuning)
2. [Cost Analysis](#cost-analysis)
3. [Diagnostics & Doctor](#diagnostics--doctor)
4. [Troubleshooting](#troubleshooting)

---

## Memory Tuning

### Configuration

Memory tuning is controlled via environment variables. Set these before starting pxx:

```bash
# Relevance threshold (0.0-1.0, default 0.5)
# Only observations with score >= threshold are injected
export PXX_MEMORY_THRESHOLD=0.6

# Maximum number of observations per injection (default 5)
export PXX_MEMORY_LIMIT=8

# Maximum context size in characters (default 8000)
export PXX_MEMORY_MAX_CONTEXT=10000

# Reserved headroom for aider responses (default 2000)
export PXX_MEMORY_HEADROOM=3000

# Run drift check before every --edit session (default off)
export PXX_AUTOCHECK_DRIFT=1
```

### Tuning Strategy

**Goal:** Maximize memory benefit while staying within aider's context budget.

#### Rule 1: Start Conservative

Begin with tight defaults, then loosen:

```bash
# Session 1: Tight tuning
export PXX_MEMORY_THRESHOLD=0.7  # Only high-confidence observations
export PXX_MEMORY_LIMIT=3        # Few observations per injection

# Session 2: Measure hit rate
pxx --list-skills  # See available memory commands and skills

# Session 3: Loosen if hit rate is low
export PXX_MEMORY_THRESHOLD=0.5  # Allow more observations
export PXX_MEMORY_LIMIT=5        # More observations per injection
```

#### Rule 2: Monitor Memory Contribution

Use `/recall` slash command during a session to measure effectiveness:

```
/recall authentication bug          # Search for matching observations
/recall build errors                # Search for observations about build failures
```

**Note:** `/recall` is a memory command (runtime), not a `/load`-able skill. It returns search results during the aider session. If `/recall` frequently returns 0 results, lower `PXX_MEMORY_THRESHOLD` or increase `PXX_MEMORY_LIMIT`.

To refine code after recalling observations, use the `/simplify` or `/refactor` skills separately:
```
/load /Users/you/ai/pxx/pxx/commands/simplify.md
```

#### Rule 3: Balance Context

Monitor your context usage and reserve headroom:

```bash
# Example: Context window = 128k tokens = ~512k characters

# If aider feels constrained during long sessions:
export PXX_MEMORY_MAX_CONTEXT=5000      # Reduce memory budget
export PXX_MEMORY_HEADROOM=5000         # Increase aider headroom

# If you have room and memory isn't helping:
export PXX_MEMORY_MAX_CONTEXT=12000     # Expand memory budget
export PXX_MEMORY_HEADROOM=1000         # Tighter headroom
```

### Observation Lifecycle

Observations are captured automatically but can be managed manually:

```
# Recall and review past observations
/recall <query>

# Save an important finding for future sessions
/remember "Bug fix title" "Detailed explanation"

# Remove stale or incorrect observations
/forget obs-123
```

### Analytics

View memory usage patterns:

```bash
# (Future) Memory analytics API
# Track most-retrieved observations, cold observations, hit rates
```

---

## Cost Analysis

### Session Cost Metrics

After each session, pxx logs cost metrics including:

- **Tokens:** prompt, completion, cached, effective
- **Cache hit rate:** percentage of tokens served from cache
- **Memory:** observation count, total size
- **Router:** request count, latency
- **Estimated cost:** USD based on token usage

### Token Accounting

pxx tracks three types of tokens:

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

#### Cache Hits

- **Enable memory injection** (`--with-memory`) to cache reusable context
- **Use consistent scopes** — same files per session maximize cache hits
- **Review memory contributions** — if memory overhead > benefit, reduce limits

#### Token Efficiency

- **Use `/spec` and `/plan`** before `/build` to guide the model (cheaper edits)
- **Load relevant skills** (`/load pxx/commands/spec.md`) to frame the task
- **Ask narrowly** — specific requests cost fewer tokens than vague ones

#### Fallback Cost

- **Tier 2 (memory injection)** is a sunk cost once computed; using it doesn't increase token cost
- **Tier 3b (skills)** are loaded at session start; cost is amortized across the session
- **Tier 4 (router)** adds negligible latency (same LAN); no token cost

---

## Diagnostics & Doctor

### Running Doctor

Check system health with the extended doctor:

```bash
# Full health check (bash + Python)
pxx --doctor

# Or run each separately:
bash scripts/doctor.sh        # Ollama, endpoints, drift, CPU temp
python3 -m pxx.doctor        # Router, memory, cost metrics
```

### Doctor Output

Example:

```
=== pxx doctor (extended) ===

Routing & Memory:
  9router (http://127.0.0.1:20128): OK | active_requests=2 | p99=145ms | error_rate=0.0%
  agentmemory (http://127.0.0.1:3111): OK | observations=342 | size=8.5MB | hit_rate=68.5% | retrieval=42ms

=== Run full doctor ===
  bash scripts/doctor.sh  (Ollama, endpoints, drift)
```

**Red flags:**

- `9router: unreachable` — Router is not running. Start it: `9router -listen 127.0.0.1:20128`
- `agentmemory: unreachable` — agentmemory is not running. Start it: `agentmemory server --port 3111`
- `error_rate > 5%` — Router is experiencing errors; check logs
- `hit_rate < 30%` — Memory observations are not relevant; review tuning

### Environment Variables for Doctor

```bash
# Router endpoint
export PXX_ROUTER_API=http://127.0.0.1:20128

# Memory endpoint
export PXX_MEMORY_API=http://127.0.0.1:3111
```

---

## Troubleshooting

### Memory is Slowing Down Sessions

**Symptom:** pxx --edit takes longer than usual; memory injection is blocking.

**Solution:**

1. Check if agentmemory is running:
   ```bash
   pxx --doctor
   ```

2. Reduce memory context budget:
   ```bash
   export PXX_MEMORY_MAX_CONTEXT=4000
   ```

3. Lower retrieval limits:
   ```bash
   export PXX_MEMORY_LIMIT=3
   ```

4. (Temporary) Disable memory injection:
   ```bash
   pxx --edit --no-memory
   ```

### Observations Are Never Recalled

**Symptom:** `/recall <query>` returns "No observations found" even for recent fixes.

**Solution:**

1. Check relevance threshold:
   ```bash
   # Lower the threshold to match broader queries
   export PXX_MEMORY_THRESHOLD=0.3
   ```

2. Verify memory server is capturing observations:
   ```bash
   pxx --doctor
   # If observation_count is low, server may not be receiving data
   ```

3. Check if observations are being saved:
   ```bash
   # Use /remember to manually save important findings
   /remember "Search phrase" "Detailed content"
   ```

### Router is Not Routing

**Symptom:** pxx --with-router doesn't distribute requests to a secondary backend.

**Solution:**

1. Verify router is running:
   ```bash
   pxx --doctor
   ```

2. Check router configuration:
   ```bash
   9router --help
   ```

3. Ensure both primary and fallback endpoints are available:
   ```bash
   bash scripts/doctor.sh
   ```

### High Cost Estimates

**Symptom:** Session cost is unexpectedly high.

**Solution:**

1. Check token count breakdown:
   ```bash
   # Review aider's token accounting in the session log
   # Look for large completion_tokens (model output)
   ```

2. Review memory contribution:
   - If memory is large but hit rate is low, reduce limits
   - If memory is not helping, disable it for this session

3. Use skills to guide the model:
   ```bash
   # Load /plan early to guide implementation
   /load pxx/commands/plan.md
   ```

---

## Advanced Topics

### Custom Tuning Profiles

Create shell functions for different use cases:

```bash
# Tight tuning for small changes
alias pxx-tight='PXX_MEMORY_THRESHOLD=0.7 PXX_MEMORY_LIMIT=3 pxx'

# Loose tuning for exploration
alias pxx-loose='PXX_MEMORY_THRESHOLD=0.3 PXX_MEMORY_LIMIT=10 pxx'

# Memory disabled
alias pxx-nomem='PXX_MEMORY_LIMIT=0 pxx'
```

### Audit Log Analysis

Review session metrics from the audit log:

```bash
# Find expensive sessions
jq '.estimated_cost_usd' ~/.local/state/pxx/sessions/*.jsonl | sort -rn | head -5

# Count memory vs. router-only sessions
jq 'select(.with_memory == true) | .estimated_cost_usd' ~/.local/state/pxx/sessions/*.jsonl | wc -l
jq 'select(.with_router == true) | .estimated_cost_usd' ~/.local/state/pxx/sessions/*.jsonl | wc -l

# Find cold observations (rarely used)
# (Requires memory analytics integration)
```

---

## Summary

- **Start conservative:** tight thresholds, small limits
- **Monitor hit rates:** use `/recall` to verify observations are useful
- **Balance context:** reserve enough headroom for aider
- **Use doctor regularly:** check system health before sessions
- **Review costs:** track token usage to optimize spending
- **Load skills early:** `/load` prompts guide the model and reduce revisions
