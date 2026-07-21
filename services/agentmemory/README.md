# agentmemory

> **Status: experimental, source-only.** Not published on PyPI; install from
> this repository. The storage/search API below works and is tested; the
> automatic pxx integration (live capture, session injection) is **not
> wired** in this release — see "Integration with pxx".

Observation storage service for pxx aider sessions. Supports per-project
scoping, hybrid BM25+vector search, and a `/command` endpoint for memory
management.

## Installation

```bash
# From the repository root (not on PyPI):
pip install -e services/agentmemory
```

## Usage

### Start the service

```bash
agentmemory
```

Or with a custom port:

```bash
PXX_MEMORY_PORT=3111 agentmemory
```

### Environment Variables

- `PXX_MEMORY_HOST`: Bind host (default: 127.0.0.1)
- `PXX_MEMORY_PORT`: Bind port (default: 3111)
- `AGENTMEMORY_RETENTION_DAYS`: Observation TTL (default: 90)
- `AGENTMEMORY_CLEANUP_INTERVAL`: Cleanup check interval in seconds (default: 3600)
- `AGENTMEMORY_CLEANUP_ENABLED`: Auto-cleanup on/off (default: true)

### API Endpoints

#### Health Check
```
GET /health
```

Response:
```json
{
  "status": "healthy",
  "version": "1.0.0"
}
```

#### Store Observation
```
POST /observations
```

Request:
```json
{
  "project": "/path/to/project",
  "content": "Observation text"
}
```

Response:
```json
{
  "id": "obs-abc123",
  "project": "/path/to/project",
  "created_at": "2026-06-03T...",
  "message": "Observation stored"
}
```

#### Search Observations
```
POST /search
```

Request:
```json
{
  "project": "/path/to/project",
  "query": "search terms",
  "limit": 10
}
```

Response:
```json
{
  "query": "search terms",
  "project": "/path/to/project",
  "results": [
    {
      "id": "obs-abc123",
      "content": "Observation text",
      "score": 0.85,
      "created_at": "2026-06-03T...",
      "access_count": 5
    }
  ],
  "count": 1
}
```

#### Inject Observations
```
POST /inject
```

Get observations for context injection. Respects token/character limits.
(This endpoint works; note that pxx does not call it automatically during
sessions in this release.)

Request:
```json
{
  "project": "/path/to/project",
  "query": "search terms",
  "limit": 5,
  "max_chars": 8000
}
```

Response:
```json
{
  "project": "/path/to/project",
  "query": "search terms",
  "observations": [
    "[obs-abc123] Observation text (score: 0.85)",
    ...
  ],
  "count": 1,
  "size_chars": 35
}
```

#### Project Statistics
```
GET /project/{project}/stats
```

Response:
```json
{
  "project": "/path/to/project",
  "observation_count": 42,
  "size_mb": 1.23
}
```

#### Delete Project
```
DELETE /project/{project}
```

Delete all observations for a project.

Response:
```json
{
  "project": "/path/to/project",
  "deleted": 42,
  "message": "Deleted 42 observations"
}
```

#### Execute Command
```
POST /command
```

Request:
```json
{
  "project": "/path/to/project",
  "command": "recall",
  "args": {
    "query": "Python tips",
    "limit": 5
  }
}
```

Supported commands:
- `recall` — search observations (`query`, `limit`)
- `remember` — save observation (`title`, `content`)
- `forget` — delete observation (`id`)

Response (varies by command):
```json
{
  "query": "Python tips",
  "results": [...],
  "count": 1
}
```

## Per-Project Scoping

All observations are scoped to a project path. This prevents cross-project leakage:

- Observations stored with `project=/path/to/repo` are isolated
- Searches on `project=/path/to/repo` only return that project's observations
- Deleting a project purges all its observations

## Storage

Observations are stored in SQLite at `~/.pxx/memory.db`. Storage is persistent across sessions.

## Integration with pxx

When `pxx --with-memory` is enabled:
1. pxx starts and health-checks the agentmemory service
2. After the aider session exits cleanly, pxx stores a post-session edit
   summary derived from the session's git diff

Not wired in this release:
- **Live capture** — the runtime observer is disabled (TTY/output-format
  constraints), so individual aider tool calls are not recorded as they
  happen
- **Automatic injection** — pxx does not query `/inject` or place prior
  observations into the aider prompt; retrieval is explicit (this API, or
  `remember`/`recall` via `/command`)

## Search & Ranking

Observations are ranked using hybrid BM25 + vector similarity (nominally
40% keyword / 60% semantic):

- Term frequency within document
- Inverse document frequency across project
- Document length normalization
- Embedding similarity

Higher scores indicate higher relevance to the query. The HNSW vector index
is experimental: the production path does not populate it yet, and no public
latency/recall benchmark exists — see the repository README for the current
claim policy.
