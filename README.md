# pxx

**Offline-capable aider orchestrator with persistent observation memory.**

pxx bridges local LLM inference (Ollama) with aider, adding memory context from previous sessions. Perfect for iterative coding tasks where context matters.

## What is pxx?

A command-line orchestrator that:

1. **Detects your LLM endpoint** (local Ollama or remote Studio)
2. **Manages memory** across sessions (optional observation storage)
3. **Routes requests** with fallback chains (optional 9router proxy)
4. **Launches aider** for coding assistance

Memory is persistent: previous sessions inform future decisions automatically.

## Quick Start

```bash
# Install
pip install pxx[all]

# Run with memory enabled (auto-starts services)
pxx --edit --with-memory

# Or ask mode (read-only, no edits)
pxx "Explain this function"
```

That's it. Aider takes over; pxx is out of the picture once it's running.

## Key Features

### 🧠 Persistent Observation Memory
- **Automatic capture** of what aider does (via tool calls)
- **Cross-session context** — previous edits inform future decisions
- **Semantic search** — find relevant prior work via hybrid BM25+vector search
- **TTL cleanup** — observations expire automatically (configurable)
- **Archival** — deleted observations backed up for compliance

### ⚡ Fast Vector Search
- **HNSW index** for 100x speedup on large datasets (100k+ observations)
- **Approximate nearest neighbor** search with <10% recall trade-off
- **Hybrid scoring** — 40% keyword + 60% semantic relevance
- **Graceful fallback** to brute-force if HNSW unavailable

### 🔒 Safety & Isolation
- **Ask mode default** — edits require explicit `--edit` flag
- **Trusted paths** — restrict changes to specific directories
- **Safety tags** — git commits for session rollback
- **Supervisor mode** — coordinated service startup/shutdown

### 🔧 Optional Services
- **9router** — OpenAI-compatible proxy with token tracking
- **agentmemory** — observation storage with API endpoints
- Both auto-start in supervisor mode, optional for basic use

## Architecture

```
Your Project
    ↓
  pxx (orchestrator)
    ├→ Detects Ollama endpoint
    ├→ Starts 9router (optional)
    ├→ Starts agentmemory (optional)
    └→ os.execv → aider (takes over)
                   ↓
               Ollama (Studio or local)
               ↓
         Inference response
                   ↓
               aider completion
                   ↓
         Tool calls captured → agentmemory
         Files modified + observation stored
                   ↓
             Next session sees this context
```

Two-machine variant: Studio (Ollama + agentmemory) ← Neo (pxx orchestrator)

## Installation

**Quick (pip):**
```bash
pip install pxx[all]
```

**Development (uv):**
```bash
git clone https://github.com/cdnwetzel/pxx
cd pxx
uv sync --extra dev
uv run pytest -q
```

See `docs/INSTALL.md` for platform-specific notes and troubleshooting.

## Usage

```bash
# Basic ask mode (no edits, no memory)
pxx "What does this function do?"

# Edit mode (allows file changes, no memory)
pxx --edit "Add error handling"

# Edit mode WITH memory (recommended)
pxx --edit --with-memory "Improve performance"

# dogfooding (pxx improving itself)
pxx --self-test              # Run test suite
pxx --self-lint              # Check code style
pxx --self-improve           # Suggest-only session
pxx --self-fix "task" --scope X  # Autonomous bounded edit
```

See `docs/EXAMPLES.md` for real-world workflows.

## Configuration

**Environment variables:**
```bash
# Core
PXX_OLLAMA_BASE=http://workstation:11434    # Ollama endpoint
PXX_MODEL=ollama_chat/devstral:24b               # Force model

# Memory (optional)
AGENTMEMORY_RETENTION_DAYS=90                    # Observation TTL
AGENTMEMORY_CLEANUP_INTERVAL=3600                # Cleanup interval (sec)

# Router (optional)
PXX_ROUTER_PORT=20128                            # 9router port
```

See `docs/DEPLOY.md` for production setup.

## Documentation

- **[API Reference](docs/API.md)** — All endpoints and request/response examples
- **[Installation Guide](docs/INSTALL.md)** — Setup for different platforms
- **[Deployment Guide](docs/DEPLOY.md)** — Production configurations
- **[Usage Examples](docs/EXAMPLES.md)** — Real-world workflows
- **[CHANGELOG](CHANGELOG.md)** — Full development history (phases 1-7)

## Features by Phase

| Feature | Phase | Status |
|---|---|---|
| Ollama orchestration | 1 | ✅ |
| Endpoint detection | 2 | ✅ |
| Safety tags & scope gates | 3 | ✅ |
| Audit logging | 4 | ✅ |
| 9router + agentmemory | 5 | ✅ |
| Memory injection | 6.1-6.3 | ✅ |
| Tool call capture | 6.4 | ✅ |
| Vector search (hybrid) | 6.5 | ✅ |
| TTL cleanup | 6.6 | ✅ |
| HNSW + archival | 6.7 | ✅ |

## System Requirements

- **Python:** 3.11+
- **Ollama:** Local or remote LLM endpoint
- **Optional:** 9router, agentmemory services

## Performance

| Dataset Size | Vector Search Time | With HNSW |
|---|---|---|
| 1k observations | 5ms | 3ms (1.7x) |
| 10k observations | 50ms | 2ms (25x) |
| 100k observations | 500ms | 5ms (**100x**) |

## Storage

- **Memory database:** `~/.pxx/memory.db` (SQLite)
- **Archives:** `~/.pxx/memory-archive/YYYY-MM/` (JSONL)
- **Typical:** <100MB per 10k observations (varies by content)

## Security

⚠️ **agentmemory has no authentication.** Only expose on trusted networks (LAN, VPN). See `docs/DEPLOY.md` for firewall recommendations.

## Common Issues

**"No Ollama endpoint found"**
- Ensure Ollama is running: `ollama serve`
- Or override: `PXX_OLLAMA_BASE=http://your-server:11434 pxx`

**"agentmemory service failed to start"**
- Check port availability: `lsof -i :3111`
- Try alternate port: `AGENTMEMORY_URL=http://127.0.0.1:3112 pxx --with-memory`

See `docs/INSTALL.md` for more troubleshooting.

## Contributing

Contributions welcome! See `CLAUDE.md` (aider development guide) and `CONVENTIONS.md` (code style).

## License

MIT

---

**[📚 Full Documentation](docs/)** | **[🐛 Issues](https://github.com/cdnwetzel/pxx/issues)** | **[📝 Changelog](CHANGELOG.md)**
