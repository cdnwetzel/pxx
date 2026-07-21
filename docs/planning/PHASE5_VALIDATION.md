# Phase 5 Validation Test Suite

**Purpose:** Comprehensive validation of all Phase 5 features before production use.  
**Duration:** ~15 minutes  
**Output:** PASS/FAIL for each test + summary verdict

---

## Test Environment Setup

Before running tests:

```bash
# 1. Ensure you're in the pxx repo
cd /Users/you/ai/pxx

# 2. Install dependencies (if needed)
uv sync --extra dev

# 3. Start optional services (for tests T-05, T-06, T-07)
# In separate terminals:
9router -listen 127.0.0.1:20128 &         # Tier 1: routing
agentmemory server --port 3111 &          # Tier 2/3: memory

# 4. Verify services are reachable (should return 200)
curl -s http://127.0.0.1:20128/status | jq . && echo "✓ 9router online"
curl -s http://127.0.0.1:3111/health | jq . && echo "✓ agentmemory online"
```

---

## Unit Tests (Automated)

Run the full test suite:

```bash
uv run pytest tests/ -v --tb=short
```

**PASS criteria:**
- Exit code = 0
- All tests pass (no FAILED or ERROR lines)
- Line count: expect ~586 passing tests

**FAIL criteria:**
- Exit code ≠ 0
- Any test marked FAILED or ERROR
- Coverage gaps in Tier 3b/4 modules

---

## Integration Tests (Manual)

### T-01: Skill Discovery (`pxx --list-skills`)

**Command:**
```bash
pxx --list-skills
```

**Expected output:**
```
Available skills:
  /audit               — Read-only review for bugs, unsafe patterns, perf footguns
  /build               — Implementation and coding
  /docstring           — Add a concise docstring (only when asked)
  /plan                — Break down the implementation into concrete steps before coding
  /refactor            — Refactor for clarity; keep behavior identical
  /refocus             — Digest the conversation before /clear (context-rot recovery)
  /review              — Read-only review of code; critique without editing
  /security-audit      — Deep security review; threat modeling, injection risks
  /ship                — Final checks before commit; verify tests pass
  /simplify            — Simplify and deduplicate; reduce complexity
  /spec                — Gather requirements; turn vague asks into acceptance criteria
  /test                — Write tests; parametrize edge cases, no mocking unless asked
  /typecheck           — Tighten type hints toward mypy --strict
```

**PASS criteria:**
- All 13 skills listed
- No errors or stack traces
- Descriptions are present and correct

**FAIL criteria:**
- Fewer than 13 skills listed
- Missing descriptions
- Any error messages

---

### T-02: Custom Skill Discovery (User-Local)

**Setup:**
```bash
mkdir -p ~/.config/pxx/commands
cat > ~/.config/pxx/commands/mytest.md << 'EOF'
# /mytest — Test custom skill

This is a test skill in user-local directory.
EOF
```

**Command:**
```bash
pxx --list-skills | grep mytest
```

**Expected output:**
```
  /mytest              — Test custom skill
```

**PASS criteria:**
- Custom skill appears in listing
- Description is correct
- No errors

**FAIL criteria:**
- Custom skill NOT in listing
- Error messages
- Built-in skills missing

---

### T-03: Slash Command Availability (Requires --with-memory)

**Setup:**
```bash
# Start memory service (if not already running)
agentmemory server --port 3111 &
sleep 2

# Verify it's reachable
curl -s http://127.0.0.1:3111/health
```

**Command:**
```bash
# Create a test file to work on
echo 'x = 1' > /tmp/test_pxx.py

# Check that pxx accepts --with-memory flag and starts aider
# (This won't fully run aider, just verify flag parsing)
cd /tmp && pxx --with-memory --dry-run 2>&1 | head -20
```

**Expected output:**
```
pxx: endpoint=... backend=ollama model=... mode=ask (untrusted path)
pxx: agentmemory started (port 3111)
...
```

**PASS criteria:**
- No errors about unknown flag `--with-memory`
- Message "agentmemory started" appears
- Aider invocation is constructed correctly

**FAIL criteria:**
- "unrecognized argument: --with-memory"
- "agentmemory connection refused"
- Exit code ≠ 0

---

### T-04: Router Flag (Tier 1)

**Setup:**
```bash
# Start router (if not already running)
9router -listen 127.0.0.1:20128 &
sleep 2

# Verify reachability
curl -s http://127.0.0.1:20128/status | jq .
```

**Command:**
```bash
cd /tmp && pxx --with-router --dry-run 2>&1 | head -20
```

**Expected output:**
```
pxx: endpoint=... backend=ollama model=... mode=ask
pxx: 9router selected (http://127.0.0.1:20128)
...
```

**PASS criteria:**
- No errors about unknown flag `--with-router`
- Message about 9router selection appears
- Exit code = 0

**FAIL criteria:**
- "unrecognized argument: --with-router"
- "9router connection refused"
- Exit code ≠ 0

---

### T-05: Doctor Health Checks (Tier 4)

**Setup:**
```bash
# Both services should be running
pgrep 9router && echo "✓ 9router running" || echo "✗ 9router NOT running"
pgrep agentmemory && echo "✓ agentmemory running" || echo "✗ agentmemory NOT running"
```

**Command:**
```bash
uv run python3 -m pxx.doctor
```

**Expected output:**
```
=== pxx doctor (extended) ===

Routing & Memory:
  9router (http://127.0.0.1:20128): OK | active_requests=... | p99=...ms | error_rate=0.0%
  agentmemory (http://127.0.0.1:3111): OK | observations=... | size=...MB | hit_rate=...% | retrieval=...ms
```

**PASS criteria:**
- Both services marked "OK"
- No connection refused errors
- Metrics displayed (even if zero)

**FAIL criteria:**
- "unreachable" for either service
- Error messages
- Exit code ≠ 0

---

### T-06: Cost Metrics (Tier 4 — Simulator)

**Command:**
```bash
# Test the cost metrics module directly
uv run python3 << 'EOF'
from pxx.cost_metrics import TokenMetrics, CostMetrics
from datetime import datetime

# Create token metrics
tokens = TokenMetrics(
    session_id="test-session",
    prompt_tokens=10000,
    completion_tokens=500,
    total_tokens=10500,
    cached_tokens=8000,
)

# Create cost metrics
cost = CostMetrics(
    session_id="test-session",
    start_time=datetime.now().isoformat(),
    end_time=datetime.now().isoformat(),
    tokens=tokens,
)

# Calculate cost (Claude pricing)
cost.calculate_estimated_cost(
    prompt_cost_per_1k=0.003,
    completion_cost_per_1k=0.012
)

# Print summary
print(cost.get_summary())

# Verify effective tokens calculation
print(f"\nEffective tokens: {tokens.effective_tokens}")
print(f"Cache hit rate: {tokens.cache_hit_rate:.1%}")
EOF
```

**Expected output:**
```
=== Session Cost Summary ===
Tokens: 10,500 total (10,000 prompt, 500 completion)
Cache: 8,000 tokens cached (76.2% hit rate)
Estimated cost: $0.04XX

Effective tokens: 2300.0
Cache hit rate: 76.2%
```

**PASS criteria:**
- Summary printed without errors
- Effective tokens calculation correct: 2000 + (8000 * 0.1) + 500 = 2300
- Cache hit rate shows as 76.2%
- Cost is calculated

**FAIL criteria:**
- Errors or exceptions
- Wrong effective token count
- Division by zero or NaN values

---

### T-07: Memory Analytics (Tier 3 — Simulator)

**Command:**
```bash
uv run python3 << 'EOF'
from pxx.memory_analytics import MemoryAnalytics

analytics = MemoryAnalytics()

# Simulate some events
analytics.record_retrieval("test-query", 3, 0.85)
analytics.record_injection([{"id": "obs-1"}, {"id": "obs-2"}], 500)
analytics.record_command("recall", "success")

# Get stats
stats = analytics.retrieval_stats()
print(f"Retrieval events: {len(analytics.retrieval_events)}")
print(f"Injection events: {len(analytics.injection_events)}")
print(f"Command events: {len(analytics.command_events)}")
print(f"\nRetrieval stats: {stats}")

# Verify stats format
assert stats["total_retrievals"] == 1
assert stats["avg_relevance"] == 0.85
assert stats["avg_results_per_query"] == 3
print("\n✓ All analytics assertions passed")
EOF
```

**Expected output:**
```
Retrieval events: 1
Injection events: 1
Command events: 1

Retrieval stats: {'total_retrievals': 1, 'avg_relevance': 0.85, 'avg_results_per_query': 3.0}

✓ All analytics assertions passed
```

**PASS criteria:**
- Events recorded correctly
- Stats calculated without errors
- Assertions pass
- Numbers match expectations

**FAIL criteria:**
- Exceptions or errors
- Assertion failures
- Stats show 0 or NaN values

---

### T-08: Skills Module Integration

**Command:**
```bash
uv run python3 << 'EOF'
from pxx.skills import SkillRegistry
from pathlib import Path

# Test built-in skills discovery
registry = SkillRegistry()
skills = registry.discover()

print(f"Discovered {len(skills)} skills")

# Verify specific skills exist
required = ["/spec", "/plan", "/build", "/test", "/review", "/ship", 
            "/security-audit", "/simplify", "/audit", "/docstring", 
            "/refactor", "/refocus", "/typecheck"]

found = {s.name for s in skills}
missing = set(required) - found

if missing:
    print(f"✗ MISSING SKILLS: {missing}")
else:
    print(f"✓ All {len(required)} required skills present")

# Test skill lookup
spec = registry.get_skill("spec")
print(f"\n/spec skill: {spec.name} — {spec.title}")
assert spec is not None
assert spec.name == "/spec"

print("\n✓ All skills integration tests passed")
EOF
```

**Expected output:**
```
Discovered 13 skills
✓ All 13 required skills present

/spec skill: /spec — Gather requirements; turn vague asks into acceptance criteria

✓ All skills integration tests passed
```

**PASS criteria:**
- 13 skills discovered
- All required skills present
- Skill lookup works
- No exceptions

**FAIL criteria:**
- Fewer than 13 skills
- Missing required skills
- Lookup fails

---

## Summary Validation

After running all tests T-01 through T-08, fill in this checklist:

```
Unit Tests (Automated)
[ ] pytest exit code = 0
[ ] ~586 tests passing
[ ] No FAILED or ERROR lines

Integration Tests (Manual)
[ ] T-01: All 13 skills listed
[ ] T-02: Custom skill in ~/.config/pxx/commands/ discovered
[ ] T-03: --with-memory flag accepted, agentmemory started
[ ] T-04: --with-router flag accepted, router selected
[ ] T-05: Doctor shows both services as OK
[ ] T-06: Cost metrics calculated correctly (effective_tokens, cache_hit_rate)
[ ] T-07: Memory analytics events recorded and stats calculated
[ ] T-08: Skills module discovers all 13 required skills

OVERALL VERDICT
[ ] ALL PASS — Phase 5 is production-ready ✅
[ ] FAILURES — Block merge, investigate findings ❌
```

---

## Failure Investigation

If any test fails, check:

1. **Service availability:**
   ```bash
   curl -s http://127.0.0.1:20128/status   # 9router
   curl -s http://127.0.0.1:3111/health    # agentmemory
   ```

2. **Recent commits:**
   ```bash
   git log --oneline -5
   ```

3. **Uncommitted changes:**
   ```bash
   git status
   ```

4. **Test output detail:**
   ```bash
   uv run pytest tests/ -xvs --tb=long 2>&1 | head -100
   ```

---

## Success Criteria

**SHIP APPROVED if:**
- ✅ Automated tests: 586+ passing, exit code 0
- ✅ Manual tests: T-01 through T-08 all PASS
- ✅ No errors in doctor health checks
- ✅ Cost metrics and analytics functional
- ✅ Skills discovery works (built-in + user-local)

**HOLD RELEASE if:**
- ❌ Any test failure
- ❌ Service unavailability (9router or agentmemory)
- ❌ Skills missing or lookup broken
- ❌ Cost calculation errors

---

## Artifact Capture

When running, save output:

```bash
# Capture all outputs to a file
(
  echo "=== UNIT TESTS ===" 
  uv run pytest tests/ -v --tb=short 2>&1 | tail -50
  echo ""
  echo "=== T-01: Skills ===" 
  pxx --list-skills 2>&1
  echo ""
  echo "=== T-05: Doctor ===" 
  uv run python3 -m pxx.doctor 2>&1
  echo ""
  echo "=== T-06: Cost Metrics ===" 
  uv run python3 << 'EOF'
from pxx.cost_metrics import TokenMetrics, CostMetrics
from datetime import datetime
tokens = TokenMetrics(session_id="test", prompt_tokens=10000, completion_tokens=500, 
                      total_tokens=10500, cached_tokens=8000)
cost = CostMetrics(session_id="test", start_time=datetime.now().isoformat(), 
                   end_time=datetime.now().isoformat(), tokens=tokens)
cost.calculate_estimated_cost()
print(cost.get_summary())
EOF
) | tee PHASE5_VALIDATION_OUTPUT.txt

# Share the output for review
cat PHASE5_VALIDATION_OUTPUT.txt
```

---

**Ready? Execute the test suite and share the output. I'll review and sign off. 🎯**
