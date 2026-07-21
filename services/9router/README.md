# 9router

Request router for pxx aider orchestration. Routes requests to primary Ollama endpoint with optional fallback chains and metrics tracking.

## Installation

```bash
pip install 9router
```

## Usage

### Start the service

```bash
9router -listen 127.0.0.1:20128
```

Or with custom endpoint:

```bash
PXX_ROUTER_PRIMARY=http://workstation:11434 9router -listen 127.0.0.1:20128
```

### Environment Variables

- `PXX_ROUTER_HOST`: Bind host (default: 127.0.0.1)
- `PXX_ROUTER_PORT`: Bind port (default: 20128)
- `PXX_ROUTER_PRIMARY`: Primary Ollama endpoint (default: http://localhost:11434)
- `PXX_ROUTER_FALLBACKS`: Fallback endpoints, comma-separated

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

Proxies to primary endpoint's model list.

#### Chat Completions
```
POST /v1/chat/completions
```

Proxies OpenAI-compatible chat requests to primary/fallback endpoints.

#### Usage Stats
```
GET /v1/usage
```

Response:
```json
{
  "active_requests": 0,
  "total_requests": 42,
  "total_errors": 1,
  "error_rate": 0.024,
  "latency_p99_ms": 245.5,
  "total_tokens": 125000,
  "cached_tokens": 12500,
  "compression_ratio": 0.1
}
```

#### Status
```
GET /status
```

Response:
```json
{
  "available": true,
  "endpoint": "http://localhost:11434",
  "primary": "http://localhost:11434",
  "fallbacks": [],
  "metrics": { ... }
}
```

## Metrics

Tracks the following metrics:
- **active_requests**: Currently in-flight requests
- **total_requests**: Total requests processed
- **total_errors**: Total requests that failed
- **error_rate**: Fraction of requests that failed (0-1)
- **latency_p99_ms**: 99th percentile latency in milliseconds
- **total_tokens**: Total tokens processed
- **cached_tokens**: Tokens saved via caching
- **compression_ratio**: Cached tokens / total tokens

## Configuration

9router is designed to be stateless and can be started alongside pxx:

```bash
# Terminal 1: Start router
9router -listen 127.0.0.1:20128

# Terminal 2: Start pxx with router enabled
cd ~/some-project
pxx --with-router
```

pxx will automatically detect the router at `127.0.0.1:20128` and route aider requests through it.

## Integration with pxx

When `pxx --with-router` is enabled:
1. pxx checks for 9router health at startup
2. If available, sets `OLLAMA_API_BASE=http://127.0.0.1:20128`
3. aider routes all completions through 9router
4. pxx records metrics post-session

## Fallback Chains

Configure fallback endpoints for resilience:

```bash
PXX_ROUTER_FALLBACKS="http://backup1:11434,http://backup2:11434" 9router
```

9router will try endpoints in order:
1. Primary
2. Backup1
3. Backup2
4. ...

First reachable endpoint wins.
