# API Reference

Complete API documentation for pxx and its services.

## agentmemory Service

RESTful API for observation storage, search, and management. Default: `http://127.0.0.1:3111`

### Health & Status

**GET /health**
```bash
curl http://127.0.0.1:3111/health
# {
#   "status": "healthy",
#   "version": "1.0.0"
# }
```

**GET /status**
```bash
curl http://127.0.0.1:3111/status
# {
#   "service": "agentmemory",
#   "version": "1.0.0",
#   "status": "healthy"
# }
```

### Observations

**POST /observations** — Store observation
```bash
curl -X POST http://127.0.0.1:3111/observations \
  -H "Content-Type: application/json" \
  -d '{"project": "default", "content": "Aider edited pxx/cli.py (5+ 3-)"}'

# {
#   "id": "obs-a1b2c3d4e5f6",
#   "project": "default",
#   "created_at": "2026-06-04T14:30:00",
#   "message": "Observation stored"
# }
```

**POST /search** — Search observations (hybrid BM25 + vector)
```bash
curl -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{
    "project": "default",
    "query": "Python code changes",
    "limit": 10
  }'

# {
#   "query": "Python code changes",
#   "project": "default",
#   "results": [
#     {
#       "id": "obs-xxx",
#       "content": "Aider edited pxx/cli.py (5+ 3-)",
#       "score": 0.95,
#       "created_at": "2026-06-01T10:00:00",
#       "last_accessed": "2026-06-04T14:30:00",
#       "access_count": 5
#     }
#   ],
#   "count": 1
# }
```

**POST /inject** — Get observations for context injection
```bash
curl -X POST http://127.0.0.1:3111/inject \
  -H "Content-Type: application/json" \
  -d '{
    "project": "default",
    "query": "recent changes",
    "limit": 5,
    "max_chars": 8000
  }'

# {
#   "project": "default",
#   "query": "recent changes",
#   "observations": [
#     "[obs-xxx] Aider edited pxx/cli.py (5+ 3-) (score: 0.95)",
#     "[obs-yyy] Tool call capture implemented (score: 0.88)"
#   ],
#   "count": 2,
#   "size_chars": 120
# }
```

**DELETE /forget/{observation_id}** — Delete observation
```bash
curl -X DELETE http://127.0.0.1:3111/forget/obs-a1b2c3d4e5f6

# {
#   "id": "obs-a1b2c3d4e5f6",
#   "message": "Observation deleted"
# }
```

### Projects

**GET /project/{project}/stats** — Project statistics
```bash
curl http://127.0.0.1:3111/project/default/stats

# {
#   "project": "default",
#   "observation_count": 42,
#   "size_mb": 0.5
# }
```

**DELETE /project/{project}** — Delete all observations in project
```bash
curl -X DELETE http://127.0.0.1:3111/project/default

# {
#   "project": "default",
#   "deleted": 42,
#   "message": "Deleted 42 observations"
# }
```

### Cleanup & Retention

**GET /cleanup?dry_run=true** — Preview cleanup
```bash
curl "http://127.0.0.1:3111/cleanup?dry_run=true"

# {
#   "expired_count": 5,
#   "size_freed_mb": 0.2,
#   "projects_affected": ["default", "temp"],
#   "dry_run": true
# }
```

**POST /cleanup** — Execute cleanup
```bash
curl -X POST http://127.0.0.1:3111/cleanup \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'

# {
#   "cleanup_triggered": true,
#   "dry_run": false,
#   "result": {
#     "expired_count": 5,
#     "size_freed_mb": 0.2,
#     "projects_affected": ["default"],
#     "archive": {
#       "archived_count": 5,
#       "archive_path": "/home/user/.pxx/memory-archive/2026-06/...",
#       "archive_date": "2026-06"
#     }
#   }
# }
```

**GET /retention/config** — Get retention settings
```bash
curl http://127.0.0.1:3111/retention/config

# {
#   "default_ttl_days": 90,
#   "project_overrides": {"temp": 7},
#   "cleanup_enabled": true,
#   "cleanup_interval_seconds": 3600,
#   "cleanup_stats": {
#     "last_cleanup": "2026-06-04T13:00:00",
#     "total_expired": 100,
#     "total_freed_mb": 5.0
#   }
# }
```

**POST /retention/config** — Set project TTL
```bash
curl -X POST http://127.0.0.1:3111/retention/config \
  -H "Content-Type: application/json" \
  -d '{"project": "temp", "ttl_days": 7}'

# {
#   "project": "temp",
#   "ttl_days": 7,
#   "message": "Retention config updated"
# }
```

### Archival

**GET /archive/list** — List archives
```bash
curl http://127.0.0.1:3111/archive/list

# {
#   "archives": [
#     {
#       "path": "/home/user/.pxx/memory-archive/2026-06/archive-20260604-143000.jsonl",
#       "date": "2026-06",
#       "count": 5,
#       "size_kb": 2.3,
#       "modified": "2026-06-04T14:30:00"
#     }
#   ],
#   "count": 1
# }
```

**GET /archive/stats** — Archive statistics
```bash
curl http://127.0.0.1:3111/archive/stats

# {
#   "total_archives": 12,
#   "total_observations": 1500,
#   "total_size_mb": 75.3,
#   "oldest_archive": "2026-03",
#   "newest_archive": "2026-06"
# }
```

**GET /archive/search** — Search archives
```bash
curl "http://127.0.0.1:3111/archive/search?query=Python&limit=10"

# {
#   "query": "Python",
#   "results": [
#     {
#       "id": "obs-xxx",
#       "project": "default",
#       "content": "Aider edited pxx/cli.py (5+ 3-)",
#       "created_at": "2026-06-01T10:00:00",
#       "archived_at": "2026-06-04T14:30:00",
#       "archive_file": "..."
#     }
#   ],
#   "count": 1
# }
```

### Monitoring

**GET /metrics** — Service metrics
```bash
curl http://127.0.0.1:3111/metrics

# {
#   "service": "agentmemory",
#   "version": "1.0.0",
#   "cache": {
#     "size": 15,
#     "maxsize": 128,
#     "utilization": "11.7%"
#   },
#   "status": "healthy"
# }
```

## pxx CLI

Command-line interface for orchestrating aider sessions with memory.

**Basic usage:**
```bash
pxx                    # Ask mode (read-only), memory disabled
pxx --edit             # Edit mode, memory disabled
pxx --edit --with-memory  # Edit mode, memory injection enabled
```

**Options:**
- `--edit` — Allow file modifications (safety gate)
- `--with-memory` — Enable observation injection and capture
- `--with-router` — Enable request routing through 9router
- `--big` — Bypass diff cap for large changes
- `--self-test` — Run pxx's own test suite
- `--self-lint` — Lint pxx codebase
- `--self-improve` — Ask-mode session for improving pxx
- `--self-fix "<task>"` — Autonomous edit with scope control
- `--list-commands` — Show available slash commands

## 9router (Optional)

OpenAI-compatible proxy with token tracking and provider fallback. Default: `http://127.0.0.1:20128/v1`

Used internally by pxx when `--with-router` is enabled. Compatible with any OpenAI-compatible client.

```bash
curl http://127.0.0.1:20128/health
# {"status": "healthy"}

curl http://127.0.0.1:20128/v1/usage
# {"total_tokens": 50000, "total_cost": 1.25}

curl http://127.0.0.1:20128/v1/status
# {"active_provider": "ollama", "fallback_chain": [...]}
```
