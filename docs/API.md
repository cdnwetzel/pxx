# HTTP API (`pxx serve`)

Requires the `server` extra: `pip install "pxx-orchestrator[server]"`.

```sh
pxx serve [--host 127.0.0.1] [--port 8400]
```

Binds loopback by default. Set `PXX_SERVER_TOKEN` to require
`Authorization: Bearer <token>` on everything except `/v1/health` — mandatory
in practice when binding a non-loopback address (pxx warns loudly).

## Endpoints

### `GET /v1/health`
`{"status": "ok", "version": "2.0.0"}`

### `POST /v1/sessions`
Body: `{"task": "...", "permission": "edit"?, "backend": "native"?}`
Starts a session in the background. → `{"session_id": "..."}`

### `GET /v1/sessions`
Lists tracked sessions with status and terminal code when finished.

### `GET /v1/sessions/{id}/events`
Server-Sent Events stream of the session's typed events (replays history,
then live). Ends after the `session_end` event.

### `POST /v1/sessions/{id}/cancel`
Cooperative cancellation → terminal code `INTERRUPTED`.

### `GET /v1/memory/search?q=...&k=8`
Hybrid memory search for the current project.

### `POST /v1/memory/add`
Body: `{"content": "...", "tags": ["..."]?}` → `{"id": N}`

## Notes

- Sessions run with the same gates as the CLI (scope, hooks, budgets).
- The same memory database backs CLI, MCP server, and HTTP API.
- For stdio-native agents, prefer the MCP interface (`pxx mcp`) over HTTP.
