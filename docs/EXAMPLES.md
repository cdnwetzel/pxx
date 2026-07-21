# Usage Examples

Real-world workflows with pxx and its optional services.

## Example 1: Basic Ask-Mode (No Memory)

Read-only exploration of a codebase without AI modification.

```bash
cd ~/my-python-project

# Ask about code without allowing edits
pxx "What does the function parse_config do?"

# Output:
# pxx: endpoint=studio (http://workstation:11434)  mode=ask (read-only)
# 
# Claude (via aider):
# Looking at parse_config, it:
# 1. Reads JSON config file
# 2. Validates schema
# 3. Returns Config object
```

**Use case:** Code review, analysis, learning codebase structure

## Example 2: Edit Mode with Memory Injection

Make changes with context from previous sessions.

```bash
cd ~/my-python-project

# Session 1: Improve error handling
pxx --edit --with-memory
# aider> @add_error_handling_to_parsing

# After edit, aider exits
# pxx automatically captures: "Tool call: edited parser.py (12+ 8-)"

---

# Session 2 (later): Improve related code
pxx --edit --with-memory

# aider context now includes:
# [observation] Tool call: edited parser.py (12+ 8-) [score: 0.92]
# 
# aider knows about the previous error handling changes
# and can build on that work

# aider> @improve_validation
# aider makes improvements informed by prior context
```

**Memory flow:**
1. Session 1 edits files → tool_capture extracts observation
2. Observation stored in agentmemory with embedding
3. Session 2 queries agentmemory: "recent changes"
4. Returns previous edits as context (via /inject endpoint)
5. Aider sees context, makes informed decisions

## Example 3: Autonomous Self-Fix with Scope Control

pxx improving pxx itself.

```bash
cd ~/pxx

# Test-driven improvement: fix failing tests
pxx --self-fix "Make test_vector_index pass" --scope tests/

# What happens:
# 1. pxx creates safety tag (can undo with git reset)
# 2. Runs pytest to find failures
# 3. aider runs in edit mode with scope limited to tests/
# 4. aider fixes test issues
# 5. pxx auto-commits with [autonomous] tag
# 6. User verifies and merges if satisfied

# Undo if something went wrong:
git reset --hard <safety-tag>
```

**Features:**
- `--scope X` — limit changes to specific path prefix
- Auto-commit with `[autonomous]` tag for traceability
- Safety tag allows instant rollback
- Observation capture for future reference

## Example 4: Memory-Enhanced Refactoring

Large refactoring with full context history.

```bash
cd ~/large-project

# Setup: 3 sessions, building on each other

# Session 1: Refactor module A
pxx --edit --with-memory
# aider> @refactor_database_layer_to_use_sqlalchemy
# Edits: database.py, migrations/, models/
# Observation: "Refactored database layer to use SQLAlchemy (45+ 32-)"

---

# Session 2: Update tests for module A
pxx --edit --with-memory
# Memory shows: previous refactoring changes
# aider> @update_tests_for_sqlalchemy
# Edits: tests/test_database.py
# Observation: "Updated database tests for SQLAlchemy (20+ 15-)"

---

# Session 3: Refactor dependent code in module B
pxx --edit --with-memory
# Memory shows: both previous refactorings
# aider> @refactor_models_for_new_database
# Edits: models/ (uses SQLAlchemy knowledge from session 1)
# Observation: "Refactored models for SQLAlchemy (30+ 25-)"

# Result: Each session builds on previous context
# No need to explain SQLAlchemy refactoring twice
```

**Benefits:**
- Implicit context from previous sessions
- Fewer explanations needed
- Consistent approach across multiple changes
- Observation search helps find related work

## Example 5: Managing Observation Archival

Compliance and audit trail.

```bash
# Check what will be cleaned up
curl "http://127.0.0.1:3111/cleanup?dry_run=true"
# {
#   "expired_count": 15,
#   "size_freed_mb": 0.8,
#   "projects_affected": ["default", "temp"]
# }

# Perform cleanup (archives before deleting)
curl -X POST http://127.0.0.1:3111/cleanup \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'

# Check archive stats
curl http://127.0.0.1:3111/archive/stats
# {
#   "total_archives": 12,
#   "total_observations": 1500,
#   "total_size_mb": 75.3
# }

# Search archived observations
curl "http://127.0.0.1:3111/archive/search?query=Python&limit=10"
# Returns matching archived observations with metadata

# Manual audit: browse archives
ls -la ~/.pxx/memory-archive/2026-06/
# Lists all June 2026 archive files

# Long-term storage: backup archives
tar -czf ~/compliance-backup-2026-06.tar.gz \
  ~/.pxx/memory-archive/2026-06/
```

**Use cases:**
- Compliance: prove what was done and when
- Audit trail: trace decisions and changes
- Recovery: find deleted observations
- Analysis: what changes happened when

## Example 6: Vector Search Performance

Demonstrating fast semantic search on large datasets.

```bash
# Scenario: 100k+ observations accumulated over time

# Without vector index (brute-force):
# - Query time: 500ms (scan all observations)
# - Memory: checks every single observation

# With HNSW vector index:
# - Query time: 5ms (100x faster)
# - Memory: approximate nearest neighbor graph
# - Trade-off: ~10% recall loss (acceptable)

# In practice:
pxx --edit --with-memory
# aider> "Find observations about database changes"
# aider queries: /inject with "database changes"
# agentmemory uses HNSW to find relevant observations
# Returns top 5 most relevant in <10ms

# Result: snappier context injection, faster sessions
```

## Example 7: Multi-Project Isolation

Different projects with separate observation stores.

```bash
cd ~/project-a
# Observations stored under project="project-a"
pxx --edit --with-memory

---

cd ~/project-b
# Observations stored under project="project-b"
pxx --edit --with-memory

---

# Check stats per project
curl http://127.0.0.1:3111/project/project-a/stats
# { "observation_count": 42, "size_mb": 0.5 }

curl http://127.0.0.1:3111/project/project-b/stats
# { "observation_count": 28, "size_mb": 0.3 }

# Set different retention per project
curl -X POST http://127.0.0.1:3111/retention/config \
  -d '{"project": "project-a", "ttl_days": 180}'  # 6 months

curl -X POST http://127.0.0.1:3111/retention/config \
  -d '{"project": "project-b", "ttl_days": 30}'   # 1 month

# Search only in project-a
curl -X POST http://127.0.0.1:3111/search \
  -d '{
    "project": "project-a",
    "query": "database refactoring",
    "limit": 10
  }'
```

## Example 8: Trusted Paths Safety Gate

Restricting pxx to specific directories.

```bash
# Setup: trust only important projects
mkdir -p ~/.config/pxx
cat > ~/.config/pxx/trusted-paths << 'EOF'
/Users/dev/work/
/Users/dev/projects/
EOF

# This works (in trusted path)
cd /Users/dev/projects/my-app
pxx --edit  # ✓ Allowed

# This is blocked (outside trusted paths)
cd /tmp/random-code
pxx --edit  # ✗ Blocked: "cwd is not under any trusted prefix"

# Override for one-shot (annotated in audit)
pxx --edit --anywhere  # ✓ Works, but logged as "untrusted path"

# Disable safety gate
rm ~/.config/pxx/trusted-paths
pxx --edit  # ✓ Works anywhere (default)
```

## Example 9: Tool Call Capture and Context

Automatic learning from tool calls.

```bash
# Session 1: Add retry logic
pxx --edit --with-memory
# aider> "Add exponential backoff to service startup"
# 
# aider executes:
# - tool_call: read pxx/cli.py
# - tool_call: edit pxx/cli.py (add retry logic)
# - tool_call: read tests/test_cli.py
# - tool_call: edit tests/test_cli.py (add test for retries)
#
# After session:
# Observation: "Aider edited pxx/cli.py (15+ 5-)"
# Observation: "Aider edited tests/test_cli.py (10+ 2-)"

---

# Session 2: Later, improve the same service
pxx --edit --with-memory
# Memory context includes: previous retry logic work
# aider> "Improve error handling in service startup"
# 
# aider knows about:
# - Existing retry logic (from session 1)
# - Test coverage patterns (from session 1)
# - Can build on existing implementation

# Result: aider makes informed decisions, not starting from scratch
```

## Workflow Recommendations

**For incremental development:**
```bash
# Iterative improvement loop
for i in {1..5}; do
  pxx --edit --with-memory "Improve $feature iteration $i"
done
# Each iteration benefits from previous ones
```

**For large refactors:**
```bash
# Break into phases with memory context
pxx --edit --with-memory "Phase 1: Extract interface"
pxx --edit --with-memory "Phase 2: Update implementations"
pxx --edit --with-memory "Phase 3: Clean up transitional code"
# Each phase knows about previous phases
```

**For learning new code:**
```bash
# Build understanding gradually
pxx "Explain the authentication flow"
pxx "How is caching implemented?"
pxx --edit --with-memory "Improve error handling in cache"
# edits informed by previous understanding
```

## Common Patterns

| Goal | Command | Memory? | Tools |
|---|---|---|---|
| Review code | `pxx "...question..."` | ❌ | aider only |
| Make edits | `pxx --edit` | ❌ | aider only |
| **Learn & improve** | `pxx --edit --with-memory` | ✅ | aider + memory |
| **Refactor series** | `pxx --self-fix "..." --scope X` | ✅ | aider + safety |
| Improve pxx itself | `pxx --self-test` / `--self-lint` | ❌ | tests/lint |

