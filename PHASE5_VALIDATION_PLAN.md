# Phase 5 Validation Plan

**Goal:** Verify 9router + agentmemory infrastructure works in real aider sessions before shipping.

**Timeline:** ~4-6 hours of testing, can run in parallel with Oz documentation work.

---

## Part 1: Service Installation & Health Checks

### Step 1.1: Install Updated Packages

The services code has been updated with project scoping and endpoint caching. Install them on Neo:

```bash
# From the worktree directory
cd /Users/you/ai/pxx/.claude/worktrees/tier3-memory-commands

# Install 9router (with latest project scoping + endpoint caching)
pip3 install --user --force-reinstall --no-deps ./services/9router

# Install agentmemory (with project scoping support)
pip3 install --user --force-reinstall --no-deps ./services/agentmemory
```

**Expected output:** Both packages should install cleanly. If you see dependency errors, we may need to sync dependencies.

---

## Part 2: Functional Tests

### Step 2.1: Start Services Manually (baseline test)

Before running through pxx, verify services start and health-check:

```bash
# Terminal 1: Start 9router
9router --listen 127.0.0.1:20128

# Terminal 2: Start agentmemory (in another terminal)
agentmemory server --port 3111

# Terminal 3: Health check both
curl http://127.0.0.1:20128/health
curl http://127.0.0.1:3111/health
```

**Expected:** Both return 200 status code within 5 seconds.

**Record:**
- Does 9router start without errors?
- Does agentmemory start without errors?
- Do both health checks pass?

### Step 2.2: Test Endpoint Caching

Measure that endpoint probe caching reduces latency (30-second TTL):

```bash
# With 9router running, measure response time on first request vs. second
time curl http://127.0.0.1:20128/v1/models
time curl http://127.0.0.1:20128/v1/models

# Second call should be ~100ms faster (no endpoint probe timeout)
```

**Expected:** Second call is noticeably faster (200-300ms vs. 1-2s).

**Record:**
- First call latency: ___ms
- Second call latency: ___ms
- Improvement: ___% faster

### Step 2.3: Test Memory Injection

Start both services and verify memory injection works:

```bash
# POST a /v1/chat/completions request with a message
curl -X POST http://127.0.0.1:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "devstral:24b",
    "messages": [
      {"role": "user", "content": "What is the weather?"}
    ]
  }'
```

**Expected:** Response comes back with valid LLM completion. Memory middleware should inject context into system prompt (you won't see this in curl, but logs should show it).

**Record:**
- Does the request reach the LLM?
- Does it return a valid completion?
- Any errors in 9router logs?

### Step 2.4: Test Observation Capture

Verify tool calls are extracted and stored:

```bash
# POST a request with tool_calls in the LLM response
# This requires a model that supports function calling (devstral:24b does)
curl -X POST http://127.0.0.1:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "devstral:24b",
    "messages": [{"role": "user", "content": "list files in /tmp"}],
    "tools": [{"type": "function", "function": {"name": "bash", "description": "Run bash", "parameters": {"type": "object", "properties": {}}}}]
  }'
```

**Expected:** Tool calls captured and stored in agentmemory. You can verify by searching agentmemory:

```bash
curl -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{"query": "bash", "limit": 1}'
```

**Record:**
- Does tool call observation get stored?
- Can you search for it?

---

## Part 3: Full pxx Workflow Test

### Step 3.1: Test `pxx --with-router` (routing only)

```bash
# In a test project directory (not pxx itself)
cd ~/some-test-project

# Run pxx with router enabled (no memory yet)
pxx --with-router --message "hello"
```

**Expected:** aider starts with 9router as the proxy. No hang, no errors.

**Record:**
- Does pxx start without errors?
- Does aider chat window appear?
- Can you type a message?

### Step 3.2: Test `pxx --with-memory` (memory only)

```bash
cd ~/some-test-project

# Run pxx with memory enabled
pxx --with-memory --message "hello"
```

**Expected:** agentmemory starts, aider loads. Memory injection silently happens.

**Record:**
- Do both services start?
- Does aider start?
- Any errors in startup?

### Step 3.3: Test `pxx --with-router --with-memory` (full supervisor mode)

```bash
cd ~/some-test-project

# Full supervisor mode with both services
pxx --with-router --with-memory --message "analyze this code"
```

**Expected:** Both services start, aider uses 9router proxy with memory injection.

**Record:**
- Startup time (both services + aider)?
- Any startup errors?
- Can you interact with aider normally?

### Step 3.4: Test Project Scoping

With both services running:

```bash
# Session 1: Create observation in project A
cd ~/project-a
pxx --with-memory --message "remember: project A uses FastAPI"

# Then in same session, trigger a search:
# Type: /recall "FastAPI"
# Should find the observation

# Session 2: Verify project B sees different memory
cd ~/project-b
pxx --with-memory --message "what tech does this use?"

# The "project A uses FastAPI" observation should NOT appear
# because PXX_PROJECT_ROOT scopes memory per project
```

**Expected:** Observations are isolated per project.

**Record:**
- Can you save an observation in project A?
- Can you search and find it in project A?
- Does project B have different memory (no cross-project leakage)?

### Step 3.5: Test Slash Commands (if time permits)

```bash
# With pxx --with-memory running in aider:
# Type these slash commands (though they may be intercepted by aider first)

/recall "devstral"      # Should search memory for "devstral"
/remember "test" "note" # Should store observation
/forget some-id         # Should return "not implemented" (known limitation)
```

**Expected:** /recall and /remember work (or are gracefully ignored by aider). /forget returns "not implemented" message.

**Record:**
- Can you use slash commands?
- Do /recall and /remember work?
- Does /forget return the right message?

---

## Part 4: Clean Exit & Stability

### Step 4.1: Graceful Shutdown

While aider is running:

```bash
# Press Ctrl+D to exit aider
# Observe cleanup
```

**Expected:** aider exits cleanly, 9router and agentmemory shut down gracefully (no zombie processes).

**Record:**
- Does aider exit cleanly?
- Do services shut down without hanging?
- Any orphaned processes? (check `ps aux | grep -E '9router|agentmemory'`)

---

## Summary Form

When tests complete, fill this out:

```
Installation:
- 9router installed: [ ] Pass / [ ] Fail
- agentmemory installed: [ ] Pass / [ ] Fail

Service Health:
- 9router health check: [ ] Pass / [ ] Fail
- agentmemory health check: [ ] Pass / [ ] Fail

Latency Optimization:
- Endpoint caching works: [ ] Pass / [ ] Fail
- Measured improvement: ___% faster

Memory Injection:
- LLM request proxies: [ ] Pass / [ ] Fail
- System prompt injection: [ ] Pass / [ ] Fail (hard to see from outside, may be Pass if no errors)

Observation Capture:
- Tool calls extracted: [ ] Pass / [ ] Fail
- Observations searchable: [ ] Pass / [ ] Fail

Full Workflow:
- pxx --with-router: [ ] Pass / [ ] Fail
- pxx --with-memory: [ ] Pass / [ ] Fail
- pxx --with-router --with-memory: [ ] Pass / [ ] Fail

Project Scoping:
- Project A observations isolated: [ ] Pass / [ ] Fail
- Project B doesn't see project A memory: [ ] Pass / [ ] Fail

Stability:
- Clean shutdown: [ ] Pass / [ ] Fail
- No zombie processes: [ ] Pass / [ ] Fail

Critical Issues Found: _______________
Minor Issues Found: _______________
Ready to Ship: [ ] YES / [ ] NO (conditional on fixes)
```

---

## What I Need From You

When ready to start, let me know:
1. Are you testing on Neo (8GB MacBook) or somewhere else?
2. Do you have a test project directory we can use?
3. Can you run these commands sequentially and report results back?

I'll wait for your test results at each step and help debug if anything fails.
