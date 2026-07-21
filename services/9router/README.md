# 9router

> **Status: experimental, source-only.** Not published on PyPI; install from
> this repository. Not claimed as a stable feature of the current pxx
> release.

OpenAI-compatible **single-upstream** proxy for pxx aider orchestration. It
binds a fixed loopback address (`127.0.0.1:20128`) and forwards requests to
one Ollama endpoint.

## Installation

```bash
# From the repository root (not on PyPI):
pip install -e services/9router
```

## Usage

### Start the service

```bash
nine-router
```

The bind address is fixed at `127.0.0.1:20128`. The upstream is chosen with:

```bash
PXX_OLLAMA_BASE=http://workstation:11434 nine-router
```

(`pxx --with-router` starts and supervises the service for you.)

### Environment Variables

- `PXX_OLLAMA_BASE`: upstream Ollama endpoint (default: `http://localhost:11434`)
- `PXX_MEMORY_ENABLED`: set to `0` to disable the experimental memory
  middleware (default: enabled — see the caveat below)

`PXX_ROUTER_HOST`, `PXX_ROUTER_PORT`, `PXX_ROUTER_PRIMARY`, and
`PXX_ROUTER_FALLBACKS` do **not** configure the running service in this
release.

### API Endpoints

#### Health Check
```
GET /health
```

Response (healthy):
```json
{
  "status": "healthy",
  "endpoint": "http://localhost:11434"
}
```

#### List Models
```
GET /v1/models
```

Proxies the upstream's model list, in OpenAI format.

#### Chat Completions
```
POST /v1/chat/completions
```

Proxies OpenAI-compatible chat requests to the single upstream.

### Not wired in this release

The repository contains modules and references that the running service does
**not** use yet:

- `EndpointRouter` (primary/fallback chains) — the app posts to one upstream
- `RouterMetrics` (token/cost/latency stats) — not integrated
- `/v1/status` and `/v1/usage` endpoints — not implemented, though
  `pxx/router.py` and the doctor reference them (they degrade gracefully)
- The memory middleware (`memory_middleware.py`) is enabled by default but
  only partially wired: its storage calls hit a retrieval endpoint, so a 200
  response does not mean anything was stored. Treat it as inert and disable
  it with `PXX_MEMORY_ENABLED=0` if you run the proxy standalone.

## Integration with pxx

When `pxx --with-router` is enabled:
1. pxx starts 9router (fixed `127.0.0.1:20128`) and waits for health
2. aider's API base is pointed at the proxy
3. Requests are forwarded to the single Ollama upstream
