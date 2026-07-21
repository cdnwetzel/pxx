# Phase 5 Final Validation Suite (Integrated)

**Scope:** Comprehensive test of Phase 5 ("AI Tooling Sovereignty") integration.  
**Strategy:** Static checks → Negative tests → Functional integration tests → Sign-off.  
**Duration:** ~30 minutes  
**Review:** Execute all tests, capture outputs, return for final GO/NO-GO.

---

## Prerequisites

```bash
cd /Users/you/ai/pxx
git checkout main
uv sync --extra dev

# Verify Studio is reachable
curl -s http://workstation:11434/api/tags | jq .models[0].name || echo "❌ Studio unreachable"
```

---

## PHASE A: Static Sanity Checks (Fast Fail)

These catch file/config issues before anything runs.

### A-01: Python Import Health

**Command:**
```bash
cd /Users/you/ai/pxx
uv run python3 -c "
import pxx.cli
import pxx.skills
import pxx.cost_metrics
import pxx.doctor
import pxx.observer
import pxx.memory_analytics
import pxx.memory_commands
print('✓ All Phase 5 imports successful')
"
```

**GO Criteria:** ✅ Output ends with "✓ All Phase 5 imports successful"  
**NO-GO Criteria:** ❌ ImportError, ModuleNotFoundError, or SyntaxError

---

### A-02: Test Suite Sanity (No Hidden Failures)

**Command:**
```bash
cd /Users/you/ai/pxx
uv run pytest tests/ --collect-only -q 2>&1 | tail -1
```

**GO Criteria:** ✅ Output shows a count (e.g., "586 tests collected")  
**NO-GO Criteria:** ❌ Error collecting tests, or count < 150

---

### A-03: Skill Files Are Parseable

**Command:**
```bash
cd /Users/you/ai/pxx
uv run python3 << 'EOF'
from pathlib import Path
import re

skills_dir = Path(__file__).parent / "pxx" / "commands"
skills = []
for f in sorted(skills_dir.glob("*.md")):
    if f.name == "SKILL_TEMPLATE.md":
        continue
    content = f.read_text()
    match = re.match(r"^#\s+(/[\w-]+)\s*—\s*(.+?)$", content, re.MULTILINE)
    if match:
        skills.append(match.group(1))
    else:
        print(f"❌ {f.name} has no valid header")

print(f"Found {len(skills)} parseable skills: {', '.join(sorted(skills))}")
if len(skills) == 13:
    print("✓ All 13 skills have valid headers")
else:
    print(f"❌ Expected 13 skills, found {len(skills)}")
EOF
```

**GO Criteria:**
- ✅ Output shows 13 parseable skills
- ✅ All required skills present: /spec, /plan, /build, /test, /review, /ship, /security-audit, /simplify, /audit, /docstring, /refactor, /refocus, /typecheck

**NO-GO Criteria:**
- ❌ Fewer than 13 skills
- ❌ Parse errors on any skill file

---

### A-04: System Prompt Lists Skills

**Command:**
```bash
cd /Users/you/ai/pxx
grep -c "/spec\|/plan\|/build\|/audit\|/refactor" pxx/prompts/system.md
```

**GO Criteria:** ✅ Output shows count ≥ 10 (confirms skills documented)  
**NO-GO Criteria:** ❌ Count = 0 or output shows missing skills

---

### A-05: Port Documentation Is Correct

**Command:**
```bash
cd /Users/you/ai/pxx
grep "9000\|20128" docs/PHASE5_TUNING.md | head -5
```

**GO Criteria:** ✅ All matches show "20128" (not "9000")  
**NO-GO Criteria:** ❌ Any line contains "9000" (old port)

---

### A-06: CostMetrics Is Wired

**Command:**
```bash
cd /Users/you/ai/pxx
grep -c "CostMetrics\|cost_metrics" pxx/cli.py
```

**GO Criteria:** ✅ Output shows count ≥ 3 (import + instantiation)  
**NO-GO Criteria:** ❌ Count = 0 (means F-001 not fixed)

---

## PHASE B: Negative Tests (What Should NOT Happen)

These verify we don't regress into known failure modes.

### B-01: Doctor Doesn't Crash When Services Are Offline

**Command:**
```bash
cd /Users/you/ai/pxx
# Kill services if running
pkill -f "9router|agentmemory" 2>/dev/null || true
sleep 1

# Doctor should exit 0 even without services
uv run python3 -m pxx.doctor 2>&1 | tee /tmp/doctor_offline.log
echo "Exit code: $?"
```

**GO Criteria:**
- ✅ Exit code = 0
- ✅ Output mentions "unavailable" or "not running" (graceful)
- ✅ No stack trace or error

**NO-GO Criteria:**
- ❌ Exit code ≠ 0
- ❌ Traceback in output
- ❌ Hangs for >5 seconds

---

### B-02: Skills Don't Load If Directory Missing

**Command:**
```bash
cd /tmp
mkdir -p /tmp/pxx_empty
uv run python3 << 'EOF'
from pxx.skills import SkillRegistry
from pathlib import Path

registry = SkillRegistry(Path("/tmp/pxx_empty"))
skills = registry.discover()
print(f"Skills found: {len(skills)}")
if len(skills) == 0:
    print("✓ Gracefully returns empty list when dir doesn't exist")
else:
    print("❌ Should return empty list, got:", skills)
EOF
```

**GO Criteria:** ✅ Returns 0 skills gracefully  
**NO-GO Criteria:** ❌ Crash or error

---

### B-03: Cost Calculation Doesn't Divide By Zero

**Command:**
```bash
cd /Users/you/ai/pxx
uv run python3 << 'EOF'
from pxx.cost_metrics import TokenMetrics

# Edge case: zero tokens
tokens = TokenMetrics(
    session_id="zero",
    prompt_tokens=0,
    completion_tokens=0,
    total_tokens=0,
    cached_tokens=0,
)

try:
    rate = tokens.cache_hit_rate
    print(f"Cache hit rate with 0 tokens: {rate}")
    print("✓ No division by zero")
except ZeroDivisionError:
    print("❌ Division by zero error")
except Exception as e:
    print(f"❌ Unexpected error: {e}")
EOF
```

**GO Criteria:** ✅ No exception, returns 0 or NaN gracefully  
**NO-GO Criteria:** ❌ ZeroDivisionError

---

## PHASE C: Functional Integration Tests (Runtime Validation)

These verify the system actually works end-to-end.

### C-01: Skill Discovery Works

**Command:**
```bash
cd /Users/you/ai/pxx
pxx --list-skills 2>&1 | head -20
```

**GO Criteria:**
- ✅ Lists at least 10 skills
- ✅ Format shows name and description
- ✅ No error messages

**NO-GO Criteria:**
- ❌ "command not found" or "unrecognized arguments"
- ❌ Less than 10 skills
- ❌ Error output

---

### C-02: Custom Skills Are Discoverable

**Command:**
```bash
mkdir -p ~/.config/pxx/commands
cat > ~/.config/pxx/commands/mytest.md << 'EOF'
# /mytest — Integration test skill

This is a temporary test skill.
EOF

cd /Users/you/ai/pxx
pxx --list-skills 2>&1 | grep mytest
echo "Result: $?"
```

**GO Criteria:** ✅ Output shows "/mytest" (exit code 0)  
**NO-GO Criteria:** ❌ grep returns 1 (skill not found)

---

### C-03: Doctor Shows Status When Services Start

**Command:**
```bash
# Start services in background
9router -listen 127.0.0.1:20128 &
ROUTER_PID=$!
sleep 2

agentmemory server --port 3111 &
MEMORY_PID=$!
sleep 2

cd /Users/you/ai/pxx
uv run python3 -m pxx.doctor 2>&1 | tee /tmp/doctor_online.log
echo "---"
echo "Router PID: $ROUTER_PID"
echo "Memory PID: $MEMORY_PID"

# Clean up
kill $ROUTER_PID $MEMORY_PID 2>/dev/null || true
```

**GO Criteria:**
- ✅ Output includes "9router" section
- ✅ Output includes "agentmemory" section
- ✅ Both marked "OK" or show health metrics
- ✅ No "Connection refused" errors

**NO-GO Criteria:**
- ❌ "9router: unreachable"
- ❌ "agentmemory: unreachable"
- ❌ Crash or error

---

### C-04: CostMetrics Calculation Is Sound

**Command:**
```bash
cd /Users/you/ai/pxx
uv run python3 << 'EOF'
from pxx.cost_metrics import TokenMetrics, CostMetrics
from datetime import datetime

# Test case from validation suite: 10k prompt, 8k cached, 500 completion
tokens = TokenMetrics(
    session_id="test",
    prompt_tokens=10000,
    completion_tokens=500,
    total_tokens=10500,
    cached_tokens=8000,
)

cost = CostMetrics(
    session_id="test",
    start_time=datetime.now().isoformat(),
    end_time=datetime.now().isoformat(),
    tokens=tokens,
)

# Expected: effective_tokens = 500 + (8000 * 0.1) + (10000 - 8000)
#                             = 500 + 800 + 2000 = 3300
expected_effective = 500 + (8000 * 0.1) + (10000 - 8000)
actual_effective = tokens.effective_tokens

print(f"Expected effective tokens: {expected_effective}")
print(f"Actual effective tokens: {actual_effective}")

if abs(actual_effective - expected_effective) < 0.01:
    print("✓ Effective token calculation correct")
else:
    print("❌ Effective token calculation wrong")

cache_rate = tokens.cache_hit_rate
print(f"Cache hit rate: {cache_rate:.1%}")
if 0.75 < cache_rate < 0.77:
    print("✓ Cache hit rate correct (76.2%)")
else:
    print(f"❌ Cache hit rate expected 76.2%, got {cache_rate:.1%}")

cost.calculate_estimated_cost(prompt_cost_per_1k=0.003, completion_cost_per_1k=0.012)
print(f"Estimated cost: ${cost.estimated_cost_usd:.4f}")
if 0.01 < cost.estimated_cost_usd < 0.10:
    print("✓ Cost calculation reasonable")
else:
    print("❌ Cost calculation suspicious")
EOF
```

**GO Criteria:**
- ✅ Effective tokens = 3300.0 (or very close)
- ✅ Cache hit rate = 76.2%
- ✅ Estimated cost is between $0.01 and $0.10

**NO-GO Criteria:**
- ❌ Effective tokens calculation wrong
- ❌ Cache hit rate > 100% or < 0%
- ❌ Cost is negative or zero

---

### C-05: Memory Analytics Tracks Events

**Command:**
```bash
cd /Users/you/ai/pxx
uv run python3 << 'EOF'
from pxx.memory_analytics import MemoryAnalytics

analytics = MemoryAnalytics()

# Simulate typical session
analytics.record_retrieval("bug fix", 5, 0.88)
analytics.record_retrieval("performance", 2, 0.65)
analytics.record_injection([{"id": "obs-1"}, {"id": "obs-2"}], 1200)
analytics.record_command("recall", "success")
analytics.record_command("remember", "success")

stats = analytics.retrieval_stats()
print(f"Total retrievals: {stats['total_retrievals']}")
print(f"Avg relevance: {stats['avg_relevance']:.2f}")
print(f"Avg results per query: {stats['avg_results_per_query']:.1f}")

if stats['total_retrievals'] == 2:
    print("✓ Retrieval events recorded correctly")
else:
    print("❌ Wrong retrieval count")

if 0.70 < stats['avg_relevance'] < 0.90:
    print("✓ Relevance calculation reasonable")
else:
    print("❌ Relevance calculation wrong")

if len(analytics.injection_events) == 1:
    print("✓ Injection events recorded")
else:
    print("❌ Injection events not recorded")

if len(analytics.command_events) == 2:
    print("✓ Command events recorded")
else:
    print("❌ Command events not recorded")
EOF
```

**GO Criteria:**
- ✅ All events recorded (retrieval, injection, command)
- ✅ Stats calculations match expectations
- ✅ No errors or exceptions

**NO-GO Criteria:**
- ❌ Events not recorded
- ❌ Stats calculations wrong
- ❌ Exceptions during recording

---

## PHASE D: Regression Guard (Pre-Phase-5 Must Still Work)

These verify we didn't break existing functionality.

### D-01: Core CLI Tests Pass

**Command:**
```bash
cd /Users/you/ai/pxx
uv run pytest tests/test_cli.py -q --tb=line 2>&1 | tail -3
```

**GO Criteria:** ✅ Output shows "X passed" with no failures  
**NO-GO Criteria:** ❌ Any "FAILED" or "ERROR" lines

---

### D-02: Endpoint Detection Still Works

**Command:**
```bash
cd /Users/you/ai/pxx
uv run pytest tests/test_endpoints.py -q --tb=line 2>&1 | tail -3
```

**GO Criteria:** ✅ Output shows "X passed" with no failures  
**NO-GO Criteria:** ❌ Any "FAILED" or "ERROR" lines

---

## Summary & Capture Instructions

After running all tests A-01 through D-02:

```bash
# Create capture file with all results
cat > /tmp/PHASE5_TEST_RESULTS.txt << 'CAPTURE_EOF'
=== PHASE A: STATIC SANITY CHECKS ===
[Run A-01 through A-06 above, paste outputs here]

=== PHASE B: NEGATIVE TESTS ===
[Run B-01 through B-03 above, paste outputs here]

=== PHASE C: FUNCTIONAL INTEGRATION ===
[Run C-01 through C-05 above, paste outputs here]

=== PHASE D: REGRESSION GUARD ===
[Run D-01 and D-02 above, paste outputs here]

=== MANUAL VERDICT ===
Mark each test:
A-01: [ ] PASS [ ] FAIL
A-02: [ ] PASS [ ] FAIL
A-03: [ ] PASS [ ] FAIL
A-04: [ ] PASS [ ] FAIL
A-05: [ ] PASS [ ] FAIL
A-06: [ ] PASS [ ] FAIL
B-01: [ ] PASS [ ] FAIL
B-02: [ ] PASS [ ] FAIL
B-03: [ ] PASS [ ] FAIL
C-01: [ ] PASS [ ] FAIL
C-02: [ ] PASS [ ] FAIL
C-03: [ ] PASS [ ] FAIL
C-04: [ ] PASS [ ] FAIL
C-05: [ ] PASS [ ] FAIL
D-01: [ ] PASS [ ] FAIL
D-02: [ ] PASS [ ] FAIL

OVERALL: [ ] GO (all pass) [ ] CONDITIONAL [ ] NO-GO (failures)
CAPTURE_EOF

cat /tmp/PHASE5_TEST_RESULTS.txt
```

---

## GO/NO-GO Decision Matrix

**GO (Ship):** ✅
- All Phase A tests pass (no static issues)
- All Phase B tests pass (no regressions)
- All Phase C tests pass (functional integration works)
- All Phase D tests pass (backward compatibility)

**CONDITIONAL:** ⚠️
- 1-2 minor failures in Phase C (e.g., offline doctor test, but online works)
- Clear workaround or mitigation documented

**NO-GO:** ❌
- Any Phase A failure (static/file issue)
- Any Phase D failure (broke existing code)
- 2+ Phase C failures (integration broken)
- Phase B failure (regression into known bad state)

---

## Next: Return Results

Execute all tests A-01 through D-02, fill in the PHASE A—D outputs above, mark your verdict, and reply with the full capture. I'll review against GO/NO-GO criteria and give final sign-off. 🎯
