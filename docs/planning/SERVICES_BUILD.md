# Phase 5 Services Build Guide

## Overview

Phase 5 adds two standalone FastAPI services to pxx:
- **9router** (port 20128) — Request routing with metrics
- **agentmemory** (port 3111) — Observation storage with BM25 search

Both are optional, independent, and managed by pxx's `--with-router` and `--with-memory` flags.

## Building Services

### From Source (Development)

Both services are located in `services/`:

```bash
cd /Users/you/ai/pxx/services/9router
uv pip install -e .

cd /Users/you/ai/pxx/services/agentmemory
uv pip install -e .
```

### From PyPI (Production)

Once published:

```bash
pip install 9router agentmemory
```

## Service Structure

### 9router

**Location:** `services/9router/`

**Modules:**
- `9router_pkg/main.py` — FastAPI application
- `9router_pkg/router.py` — Endpoint routing logic
- `9router_pkg/metrics.py` — Request metrics tracking

**API:**
- `GET /health` — Health check
- `GET /v1/models` — List models
- `POST /v1/chat/completions` — Chat completions (proxied)
- `GET /v1/usage` — Usage statistics
- `GET /status` — Service status

**Features:**
- Proxies requests to Studio Ollama
- Fallback chain support
- Tracks active requests, latency (p99), error rate
- Tracks token usage and compression ratio

**Tests:** `tests/test_router_service.py` (14 tests)

### agentmemory

**Location:** `services/agentmemory/`

**Modules:**
- `agentmemory_pkg/main.py` — FastAPI application
- `agentmemory_pkg/storage.py` — SQLite-based storage
- `agentmemory_pkg/search.py` — BM25 relevance ranking
- `agentmemory_pkg/commands.py` — Slash command handlers

**API:**
- `GET /health` — Health check
- `POST /observations` — Store observation
- `POST /search` — Search observations
- `POST /inject` — Get observations for context injection
- `GET /project/{project}/stats` — Project statistics
- `DELETE /project/{project}` — Delete project
- `POST /command` — Execute slash command

**Features:**
- Per-project observation scoping
- BM25 full-text search ranking
- SQLite persistent storage (~/.pxx/memory.db)
- Slash commands: /recall, /remember, /forget
- Project isolation (no cross-project leakage)

**Tests:** `tests/test_memory_service.py` (18 tests)

## Installation & Testing

### 1. Install both services (editable)

```bash
cd services/9router && uv pip install -e .
cd ../agentmemory && uv pip install -e .
```

### 2. Run tests

```bash
cd services/9router
uv run pytest tests/ -v

cd ../agentmemory
uv run pytest tests/ -v
```

Expected: All tests passing (32 total)

### 3. Start services manually (for testing)

**Terminal 1: Start 9router**
```bash
source /Users/you/ai/pxx/.venv/bin/activate
python3 -m 9router_pkg.main
# Listening on http://127.0.0.1:20128
```

**Terminal 2: Start agentmemory**
```bash
source /Users/you/ai/pxx/.venv/bin/activate
python3 -m agentmemory_pkg.main
# Listening on http://127.0.0.1:3111
```

**Terminal 3: Test**
```bash
# Health checks
curl http://127.0.0.1:20128/health
curl http://127.0.0.1:3111/health

# Store observation
curl -X POST http://127.0.0.1:3111/observations \
  -H "Content-Type: application/json" \
  -d '{"project": "/tmp/test", "content": "Test observation"}'

# Search
curl -X POST http://127.0.0.1:3111/search \
  -H "Content-Type: application/json" \
  -d '{"project": "/tmp/test", "query": "test"}'
```

## PyPI Publishing (Future)

When ready to publish:

```bash
cd services/9router
uv build
# Upload: twine upload dist/*

cd ../agentmemory
uv build
# Upload: twine upload dist/*
```

## Integration with pxx

Update `pxx/cli.py` to:

1. Check for service availability on startup
2. Set `OLLAMA_API_BASE` if 9router is running
3. Inject observations from agentmemory if available
4. Track usage metrics post-session

See `pxx/endpoints.py` and `pxx/cli.py` for integration points.

## Environment Variables

### 9router
- `PXX_ROUTER_HOST` (default: 127.0.0.1)
- `PXX_ROUTER_PORT` (default: 20128)
- `PXX_ROUTER_PRIMARY` (default: http://workstation:11434)
- `PXX_ROUTER_FALLBACKS` (comma-separated list)

### agentmemory
- `PXX_MEMORY_HOST` (default: 127.0.0.1)
- `PXX_MEMORY_PORT` (default: 3111)

## Next Steps

1. ✅ Build both services (DONE)
2. ✅ Test locally (DONE)
3. → Integrate with pxx (wire CLI flags, health checks)
4. → End-to-end testing with aider
5. → PyPI publishing
6. → Documentation updates

## Current Status

- 9router: Ready for integration (~2 hours estimated, DONE in 1 hour)
- agentmemory: Ready for integration (~2.5 hours estimated, DONE in 1.5 hours)
- Both services: 32 total tests, all passing
- Next: pxx integration (1.5 hours)
