# Installation Guide

Get pxx and its optional services running in minutes.

The PyPI distribution is **`pxx-orchestrator`** (the name `pxx` belongs to an
unrelated 2023 project); the installed command and import package are both
`pxx`.

## Quick Start

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

**Prerequisites:**
- **Python 3.11 or 3.12** (`python --version`). **Not 3.13+** — the pinned
  `aider-chat` requires `<3.13` (its `pydub` imports the `audioop` stdlib module
  that PEP 594 removed in 3.13). `uv tool`/`pipx` pick a supported interpreter
  automatically; a plain `pip install` on a 3.13+ interpreter does **not** (see
  the warning above).
- Ollama running and reachable (default: `http://localhost:11434`)
  - Set `PXX_OLLAMA_BASE` to override

**Install (pip):**
```bash
pip install pxx-orchestrator
```

**Verify:**
```bash
pxx --list-commands     # Should show available commands
```

**First run (read-only ask mode):**
```bash
pxx
```

## Detailed Installation

### Prerequisites

- **Python 3.11 or 3.12** — `python --version` (see the ceiling note above)
- **Ollama** — running locally or on a reachable host (required for inference)
  - Default endpoint: `http://localhost:11434`
  - Override: set `PXX_OLLAMA_BASE=<url>`
- **Git** (optional) — for auto-commits, safety tags, scoping
- **pip or uv** — package installation

### Option A: Install as a User Tool

Fast, isolated installation for end users.

```bash
# Using uv (recommended)
uv tool install pxx-orchestrator

# Or using pip
pip install --user pxx-orchestrator

# Verify
pxx --list-commands
```

Because `requires-python` is capped at `<3.13`, `uv tool install` /
`pipx install` auto-select a supported interpreter (3.12) — no `--python` pin
needed.

### Option B: Install for Development

Full editable installation with test suite and linting.

```bash
git clone https://github.com/cdnwetzel/pxx
cd pxx
uv sync --extra dev      # creates .venv/ with dev deps (pytest, ruff)
uv run pytest -q
uv run pxx --edit        # run from source
```

### Optional Services (from a repo checkout)

`agentmemory` (observation storage & search) and `9router` (request routing)
are **not published on PyPI** — install them from this repository. `pxx` on
PyPI is core-only; there are no `pxx[memory]` / `pxx[router]` / `pxx[all]`
extras.

```bash
git clone https://github.com/cdnwetzel/pxx
cd pxx
pip install -e services/agentmemory
pip install -e services/9router
```

Then pxx can auto-start them in supervisor mode:
```bash
pxx --edit --with-memory     # starts agentmemory
pxx --edit --with-router     # starts 9router
```

### Platform Notes

The core package is pure Python — `pip install pxx-orchestrator` needs no
compiler on macOS (Intel or Apple Silicon), Linux, or Windows (WSL2
recommended). The only requirement is a **3.11 or 3.12** interpreter:

```bash
# If your default python3 is 3.13+, create the venv with a supported version:
python3.12 -m venv .venv && source .venv/bin/activate
pip install pxx-orchestrator
# or with uv:
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install pxx-orchestrator
```

(The optional `agentmemory` service pulls `hnswlib`, which may compile from
source on some platforms — that's a service concern, not the core install.)

## Upgrading

`pxx --upgrade` detects how pxx was installed and runs the right command; it
refuses on an editable checkout (use `git pull` there). Or upgrade by hand:

| Installed via | Upgrade |
|---|---|
| `uv tool install` | `uv tool upgrade pxx-orchestrator` |
| `pipx` | `pipx upgrade pxx-orchestrator` |
| `pip` (in a venv) | `pip install -U pxx-orchestrator` |
| editable checkout | `git pull && uv sync --extra dev` |

Existing installs already sit on Python ≤3.12, so an in-place upgrade is safe;
the `<3.13` ceiling only affects *fresh* installs on a too-new interpreter.

### Verify Installation

```bash
pxx --version           # (execs into aider; the pxx banner prints first)
pxx --list-commands     # available commands
pxx                     # read-only ask mode
```

## Configuration

### Environment Variables

**Core pxx:**
```bash
PXX_OLLAMA_BASE=http://your-ollama-host:11434  # Ollama endpoint
PXX_MODEL=ollama_chat/devstral:24b             # Force model
PXX_AUTOCHECK_DRIFT=1                           # Pre-edit drift check
```

**agentmemory (if using --with-memory):**
```bash
AGENTMEMORY_RETENTION_DAYS=90      # Observation TTL (default)
AGENTMEMORY_CLEANUP_INTERVAL=3600  # Cleanup check interval (seconds)
AGENTMEMORY_CLEANUP_ENABLED=true   # Auto-cleanup on/off
```

**9router (if using --with-router):**
```bash
PXX_ROUTER_PORT=20128       # Router port
PXX_ROUTER_HOST=127.0.0.1   # Router host
```

### Trusted Paths (Safety Gate)

Optional: restrict pxx to specific directories.

```bash
mkdir -p ~/.config/pxx
cat > ~/.config/pxx/trusted-paths << 'EOF'
/Users/your-username/projects/
/Users/your-username/work/
EOF

pxx --edit             # ✓ Works inside a trusted path
cd /tmp && pxx --edit  # ✗ Blocked (outside trusted paths)
pxx --edit --anywhere  # ✓ Override one-shot

rm ~/.config/pxx/trusted-paths   # remove to disable
```

## Uninstall

```bash
# pip
pip uninstall pxx-orchestrator

# uv tool
uv tool uninstall pxx-orchestrator
```

## Troubleshooting

**`ResolutionImpossible` mentioning `aider-chat`, or `Cannot import
'setuptools.build_meta'` while building numpy, or "no matching distribution …
aider-chat"**
- Your interpreter is newer than 3.12. pxx supports **3.11 or 3.12** only.
  Create the venv with a supported Python:
  `python3.12 -m venv .venv` (or `uv venv --python 3.12 .venv`), then install.

**"pxx: command not found"**
- Ensure installation completed: `pip install pxx-orchestrator`
- Check PATH includes the pip/uv install location

**"No Ollama endpoint found"**
- Ollama not running; start: `ollama serve`
- Network issue; check: `curl http://127.0.0.1:11434/api/tags`
- Override: `PXX_OLLAMA_BASE=http://your-server:11434 pxx`

**"hnswlib compilation failed" (optional agentmemory service only)**
- Install build tools: `pip install --upgrade setuptools wheel`
- Vector index is optional — search falls back to brute force without it

## Next Steps

1. **Read the examples:** `docs/EXAMPLES.md`
2. **Deploy in production:** `docs/DEPLOY.md`
3. **Explore the API:** `docs/API.md`
4. **Check CLAUDE.md** for pxx-specific development info

## Support

- **Issues:** https://github.com/cdnwetzel/pxx/issues
- **Discussions:** https://github.com/cdnwetzel/pxx/discussions
- **Documentation:** https://github.com/cdnwetzel/pxx/tree/main/docs
