# pxx

**Offline-capable aider orchestrator with persistent observation memory.**

pxx bridges local LLM inference (Ollama) with aider, adding memory context from previous sessions. Perfect for iterative coding tasks where context matters.

## What is pxx?

A command-line orchestrator that:

1. **Detects your LLM endpoint** (local or networked Ollama, optional vLLM)
2. **Manages memory** across sessions (optional observation storage)
3. **Routes requests** with fallback chains (optional 9router proxy)
4. **Launches aider** for coding assistance

Memory is persistent: previous sessions inform future decisions automatically.

## Prerequisites & Assumptions

Before installing, you need:

| Prerequisite | Why | Notes |
|---|---|---|
| **Python 3.11 or 3.12** | runs pxx and aider | 3.12 is tested day-to-day. **Not 3.13+** — the pinned `aider-chat` requires `<3.13` (its `pydub` needs the `audioop` module PEP 594 removed in 3.13). `uv tool`/`pipx` auto-select a supported interpreter; a plain `pip install` on 3.13+ does **not** (it falls back to an old broken build — see the install warning). |
| **[Ollama](https://ollama.com)** installed and running | the LLM backend | `ollama serve`; local `localhost:11434` by default, or any reachable host via `PXX_OLLAMA_BASE` |
| **At least one pulled model** | aider needs a model that exists | e.g. `ollama pull qwen2.5-coder:7b`, then `export PXX_MODEL=ollama_chat/qwen2.5-coder:7b` |
| **git** (recommended) | auto-commits, safety tags, scoping | pxx works outside a git repo too (it passes `--no-git` to aider) |

You do **not** install aider separately — `pip install pxx-orchestrator` brings
`aider-chat` as a pinned dependency (pinned deliberately; aider releases
weekly and can change behavior pxx depends on).

Assumptions pxx makes:

- **Ask mode is the default** (read-only). Nothing is edited until you pass `--edit`.
- **Your LLM endpoint is trusted.** pxx sends no credentials to Ollama/vLLM — run it
  against localhost or a network you trust, not the open internet.
- **The default model is `devstral:24b`** (a public Ollama model, ~14 GB). If you
  haven't pulled it, set `PXX_MODEL` or `PXX_OLLAMA_MODEL` to a model you have.
- **aider takes over your terminal** once launched — pxx execs into it and gets
  out of the way.
- Endpoint detection probes (1s timeout each): `PXX_OLLAMA_BASE` override →
  optional vLLM endpoint(s) (`PXX_VLLM_URL`, comma-separated, probed in
  order; default `127.0.0.1:8003`) → Ollama (`PXX_STUDIO_LAN_URL`, default
  `localhost:11434`). First reachable wins.

## Quick Start

**Requires:** [Ollama](https://ollama.com) running and reachable (local by default).

> **⚠️ Installing on Python 3.13+**
> pxx supports **Python 3.11–3.12** (aider's dependencies are not yet 3.13-ready). On Python 3.13 or
> newer, a plain `pip install pxx-orchestrator` will **silently install an old 1.2.x build** that
> crashes at startup — the current release is capped at `<3.13`, so pip skips it and falls back.
> Install against 3.11/3.12 explicitly:
> ```bash
> uv tool install --python 3.12 pxx-orchestrator     # recommended
> # or:  pipx install --python 3.12 pxx-orchestrator
> # or:  python3.12 -m pip install pxx-orchestrator
> ```
> If you already hit `ModuleNotFoundError: ... audioop`, you installed an old build under 3.13 —
> reinstall with one of the commands above.

```bash
# Install the core (the command is `pxx`; the PyPI name differs
# because `pxx` was taken by an unrelated 2023 project)
pip install pxx-orchestrator

# Point pxx at your Ollama if it isn't on localhost:11434
export PXX_OLLAMA_BASE=http://your-ollama-host:11434   # optional

# Ask mode (read-only — safe to run anywhere): opens an interactive aider chat
pxx

# One-shot question (--message and other aider flags pass straight through)
pxx --message "Explain main.py"

# Edit mode (allows file changes)
pxx --edit --message "Add error handling to main.py"
```

That's it for the core. Aider takes over; pxx is out of the picture once it's
running.

**Optional services** (`--with-memory`, `--with-router`, `--with-docs`) are not
in the pip package — they live in `services/` and need a repo checkout:

```bash
git clone https://github.com/cdnwetzel/pxx && cd pxx
uv sync --extra dev                 # core, editable
(cd services/agentmemory && uv sync)
(cd services/9router && uv sync)
pxx --edit --with-memory            # auto-starts the service
```

> **Note on "offline-capable":** pxx doesn't run inference locally — it orchestrates aider against your Ollama instance. The "offline" part means no cloud dependency: all LLM calls stay on your network.

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
               Ollama (local or networked)
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

The optional services can run on the same machine or a separate host (point
pxx at them with the env vars below); the core needs only an Ollama endpoint.

## Installation

**Core (pip):**
```bash
pip install pxx-orchestrator   # installs the `pxx` command
```
This gives you the orchestrator + ask/edit against any Ollama. The optional
`--with-memory` / `--with-router` / `--with-docs` services are not packaged on
PyPI — see "Optional services" in Quick Start to run them from a repo checkout.

**Development (uv):**
```bash
git clone https://github.com/cdnwetzel/pxx
cd pxx
uv sync --extra dev
uv run pytest -q
```

**Upgrading:** `pxx --upgrade` detects how pxx was installed and runs the right
command (it refuses on an editable checkout — use `git pull` there). Or do it
by hand:

| Installed via | Upgrade |
|---|---|
| `uv tool install` | `uv tool upgrade pxx-orchestrator` |
| `pipx` | `pipx upgrade pxx-orchestrator` |
| `pip` (in a venv) | `pip install -U pxx-orchestrator` |
| editable checkout | `git pull && uv sync --extra dev` |

See [docs/INSTALL.md](https://github.com/cdnwetzel/pxx/blob/main/docs/INSTALL.md) for platform-specific notes and troubleshooting.

## Usage

```bash
# Interactive ask mode (read-only chat; no edits)
pxx

# Add files to the chat — positional args pass through to aider as files
pxx main.py utils.py

# One-shot prompts use aider's --message flag (passes through)
pxx --message "What does process_data() in main.py do?"

# Edit mode (allows file changes)
pxx --edit --message "Add error handling to main.py"

# Edit mode WITH memory (repo checkout only — see Optional services)
pxx --edit --with-memory

# Dogfooding (when developing pxx itself, from a repo checkout)
pxx --self-test              # Run test suite
pxx --self-lint              # Check code style
pxx --self-improve           # Suggest-only session
pxx --self-fix "task" --scope X  # Autonomous bounded edit
```

Any flag pxx doesn't recognize is forwarded to aider unchanged — your aider
muscle memory (`--message`, `--model`, file args, ...) works through pxx.

See [docs/EXAMPLES.md](https://github.com/cdnwetzel/pxx/blob/main/docs/EXAMPLES.md) for real-world workflows.

## Configuration

All settings are environment variables. You can also put them in
`~/.config/pxx/env` (KEY=VALUE lines) — pxx loads that file at startup, so your
endpoints/models follow you across shells without touching shell profiles.
Real environment variables override the file.

**Environment variables:**
```bash
# Core
PXX_OLLAMA_BASE=http://localhost:11434           # Ollama endpoint (default)
PXX_MODEL=ollama_chat/qwen2.5-coder:7b           # Force one model for the session
PXX_OLLAMA_MODEL=ollama_chat/llama3.1:8b         # Default Ollama model
                                                 #   (ships as devstral:24b — set
                                                 #   this to a model you've pulled)
PXX_VLLM_MODEL=openai/your-served-model          # Model id if you use a vLLM
                                                 #   endpoint (server-specific).
                                                 #   Comma list pairs with a
                                                 #   comma list in PXX_VLLM_URL

# Memory (optional)
AGENTMEMORY_RETENTION_DAYS=90                    # Observation TTL
AGENTMEMORY_CLEANUP_INTERVAL=3600                # Cleanup interval (sec)

# Router (optional)
PXX_ROUTER_PORT=20128                            # 9router port
```

See [docs/DEPLOY.md](https://github.com/cdnwetzel/pxx/blob/main/docs/DEPLOY.md) for production setup.

## Documentation

- **[API Reference](https://github.com/cdnwetzel/pxx/blob/main/docs/API.md)** — All endpoints and request/response examples
- **[Installation Guide](https://github.com/cdnwetzel/pxx/blob/main/docs/INSTALL.md)** — Setup for different platforms
- **[Deployment Guide](https://github.com/cdnwetzel/pxx/blob/main/docs/DEPLOY.md)** — Production configurations
- **[Usage Examples](https://github.com/cdnwetzel/pxx/blob/main/docs/EXAMPLES.md)** — Real-world workflows
- **[CHANGELOG](https://github.com/cdnwetzel/pxx/blob/main/CHANGELOG.md)** — Full development history (phases 1-7)

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

- **Python:** 3.11 or 3.12 (not 3.13+ — `aider-chat` pins `<3.13`)
- **Ollama:** Local or remote LLM endpoint
- **Optional:** 9router, agentmemory services (run from a repo checkout — not published on PyPI)

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

⚠️ **agentmemory does not authenticate requests.** Only expose on trusted networks (LAN, VPN). See [docs/DEPLOY.md](https://github.com/cdnwetzel/pxx/blob/main/docs/DEPLOY.md) for firewall recommendations.

## Common Issues

**"No Ollama endpoint found"**
- Ensure Ollama is running: `ollama serve`
- Or override: `PXX_OLLAMA_BASE=http://your-server:11434 pxx`

**"agentmemory service failed to start"**
- Check port availability: `lsof -i :3111`
- Try alternate port: `AGENTMEMORY_URL=http://127.0.0.1:3112 pxx --with-memory`

See [docs/INSTALL.md](https://github.com/cdnwetzel/pxx/blob/main/docs/INSTALL.md) for more troubleshooting.

## Contributing

Contributions welcome! See [CLAUDE.md](https://github.com/cdnwetzel/pxx/blob/main/CLAUDE.md) (development guide) and [CONVENTIONS.md](https://github.com/cdnwetzel/pxx/blob/main/CONVENTIONS.md) (code style).

## License

MIT

---

**[📚 Full Documentation](https://github.com/cdnwetzel/pxx/tree/main/docs/)** | **[🐛 Issues](https://github.com/cdnwetzel/pxx/issues)** | **[📝 Changelog](https://github.com/cdnwetzel/pxx/blob/main/CHANGELOG.md)**
