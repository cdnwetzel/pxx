# agentmemory

Persistent observation storage service for pxx aider sessions. Supports per-project scoping, BM25 search, and slash commands for memory management.

## Installation

```bash
pip install agentmemory
```

## Usage

### Start the service

```bash
agentmemory
```

Or with custom port:

```bash
PXX_MEMORY_PORT=3111 agentmemory
```

### Environment Variables

- `PXX_MEMORY_HOST`: Bind host (default: 127.0.0.1)
- `PXX_MEMORY_PORT`: Bind port (default: 3111)

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

#### Execute Slash Command
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

## Slash Commands

### /recall <query>

Search saved observations.

```
/recall "Python tips"
```

Returns up to 5 most relevant observations ranked by BM25 score.

### /remember "title" "content"

Manually save an observation.

```
/remember "Design pattern" "Use factory pattern for creating instances"
```

### /forget <id>

Delete an observation.

```
/forget obs-abc123
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
1. pxx checks for agentmemory health at startup
2. aider subprocess activities are captured as observations
3. Observations are injected into the system prompt
4. `/recall`, `/remember`, `/forget` commands are available

## Search & Ranking

Observations are ranked using BM25 (Okapi Best Matching) relevance scoring:
- Term frequency within document
- Inverse document frequency across project
- Document length normalization

Higher scores indicate higher relevance to the query.
