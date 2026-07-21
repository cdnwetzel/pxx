# Usage Examples

Real-world workflows with pxx and its optional services.

> **Memory examples are experimental.** `--with-memory` requires the
> source-installed `agentmemory` service. In this release it stores a
> post-session edit summary (git-diff based); it does **not** capture aider
> activity live and does **not** inject prior observations into the aider
> session automatically. The examples below show the behavior as implemented,
> and say so where a workflow depends on manual retrieval.

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

## Example 2: Edit Mode with Observation Storage

Make changes and keep a searchable record of what happened.

```bash
cd ~/my-python-project

# Session 1: Improve error handling
pxx --edit --with-memory
# aider> @add_error_handling_to_parsing

# After aider exits cleanly, pxx stores a summary derived from the
# session's git diff, e.g. "Aider edited parser.py (12+ 8-)", in the
# agentmemory service.

---

# Session 2 (later): retrieve prior work explicitly
curl -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{"project": "default", "query": "error handling parser", "limit": 5}'

# Then start the next session and paste or describe what you found:
pxx --edit --with-memory
```

**Memory flow (as implemented):**
1. Session 1 edits files → after exit, pxx stores a git-diff summary observation
2. The observation is searchable via the service's `/search` and `/inject`
   endpoints (BM25 + vector hybrid scoring)
3. Automatic injection into session 2's prompt is **not wired** in this
   release — retrieval is explicit (API, or `/remember` for manual notes)

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
- Post-session edit summary stored (with `--with-memory`)

## Example 4: Memory-Enhanced Refactoring (intended workflow)

Large refactoring with full context history.

> This is the workflow the memory system is being built toward. Steps 1–2
> work today (summaries are stored and searchable); step 3 — prior context
> appearing in the session automatically — is **not wired** in this release.
> Until it is, retrieve observations via `/search` and bring them into the
> session yourself.

```bash
cd ~/large-project

# Setup: 3 sessions, building on each other

# Session 1: Refactor module A
pxx --edit --with-memory
# aider> @refactor_database_layer_to_use_sqlalchemy
# Edits: database.py, migrations/, models/
# Stored summary: "Aider edited database.py (45+ 32-) ..."

---

# Session 2: Update tests for module A
pxx --edit --with-memory
# aider> @update_tests_for_sqlalchemy

---

# Session 3: Refactor dependent code in module B
pxx --edit --with-memory
# aider> @refactor_models_for_new_database
```

**Benefits (once injection is wired):**
- Implicit context from previous sessions
- Fewer explanations needed
- Consistent approach across multiple changes
- Observation search helps find related work

## Example 5: Managing Observation Archival

Housekeeping for the observation store.

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
tar -czf ~/memory-backup-2026-06.tar.gz \
  ~/.pxx/memory-archive/2026-06/
```

**Use cases:**
- Audit trail: trace what changed and when
- Recovery: find deleted observations
- Analysis: what changes happened when

## Example 6: Hybrid Memory Search

Using keyword and semantic signals with the optional memory service.

```bash
# Query the service directly (or via /inject for prompt-sized results)
curl -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{"project": "default", "query": "database changes", "limit": 5}'
# agentmemory combines BM25 and vector similarity to rank observations
```

The HNSW implementation is experimental: the production observation path does
not yet populate it, and the repository has no reproducible 100k/recall
benchmark. Until both gaps close, use this example to validate relevance, not
to infer a latency guarantee.

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

## Example 9: Post-Session Edit Summaries

What `--with-memory` captures today.

```bash
# Session 1: Add retry logic
pxx --edit --with-memory
# aider> "Add exponential backoff to service startup"
#
# aider edits pxx/cli.py and tests/test_cli.py, then exits.
# pxx derives a summary from the session's git diff and stores it:
#   "Aider edited pxx/cli.py (15+ 5-)"
#   "Aider edited tests/test_cli.py (10+ 2-)"

---

# Later: find that work again
curl -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{"project": "default", "query": "retry logic", "limit": 5}'
```

Note what this is not: live capture of individual aider tool calls (read /
edit events as they happen) is not wired in this release, and stored
summaries are not injected into later sessions automatically.

## Workflow Recommendations

**For incremental development:**
```bash
# Iterative improvement loop
for i in {1..5}; do
  pxx --edit --with-memory "Improve $feature iteration $i"
done
# Each iteration is stored as a searchable summary
```

**For large refactors:**
```bash
# Break into phases; use /search between phases to recall earlier ones
pxx --edit --with-memory "Phase 1: Extract interface"
pxx --edit --with-memory "Phase 2: Update implementations"
pxx --edit --with-memory "Phase 3: Clean up transitional code"
```

**For learning new code:**
```bash
# Build understanding gradually
pxx "Explain the authentication flow"
pxx "How is caching implemented?"
pxx --edit --with-memory "Improve error handling in cache"
```

## Common Patterns

| Goal | Command | Memory? | Tools |
|---|---|---|---|
| Review code | `pxx "...question..."` | ❌ | aider only |
| Make edits | `pxx --edit` | ❌ | aider only |
| **Learn & improve** | `pxx --edit --with-memory` | ⚠️ summaries stored; no auto-injection | aider + memory service |
| **Refactor series** | `pxx --self-fix "..." --scope X` | ❌ | aider + safety |
| Improve pxx itself | `pxx --self-test` / `--self-lint` | ❌ | tests/lint |
