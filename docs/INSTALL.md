# Installation Guide

Get pxx and its services running in minutes.

## Quick Start

**Prerequisites:**
- Python 3.11+ (`python --version`)
- Ollama running and reachable (default: `http://workstation.splawoffice.local:11434`)
  - Set `PXX_OLLAMA_BASE` to override

**Installation (from source):**
```bash
# Clone and install pxx
git clone https://github.com/cdnwetzel/pxx
cd pxx
pip install -e .

# Install optional services (for memory + routing)
cd services/agentmemory && pip install -e . && cd ../..
cd services/9router && pip install -e . && cd ../..
```

**Verify:**
```bash
pxx --list-commands     # Should show available commands
```

**First run:**
```bash
# Auto-starts agentmemory and 9router in supervisor mode
pxx --edit --with-memory
```

## Detailed Installation

### Prerequisites

- **Python 3.11+** — `python --version`
- **Ollama** — Running locally or on network (required for inference)
  - Studio: `http://workstation.splawoffice.local:11434` (LAN or VPN)
  - Local: Ollama on your machine for fallback
  - Override: Set `PXX_OLLAMA_BASE=<url>` to specify endpoint
- **Git** (optional) — For version control
- **pip or uv** — Package installation

### Option A: Install as User Tool

Fast, isolated installation for end users.

```bash
# Using pip (system)
pip install --user pxx

# Using uv (recommended)
uv tool install pxx

# Verify installation
pxx --list-commands
```

### Option B: Install for Development

Full editable installation with test suite and linting.

```bash
# Clone repository
git clone https://github.com/cdnwetzel/pxx
cd pxx

# Create virtual environment with uv
uv sync --extra dev

# Run tests
uv run pytest -q

# Run pxx from source
uv run pxx --edit

# Or install locally for direct access
uv tool install --editable .
```

### Optional Services

agentmemory (observation storage & search) and 9router (request routing) are optional but recommended for full feature set.

**Install agentmemory:**
```bash
# Standalone service
pip install agentmemory

# Or as part of pxx ecosystem
pip install pxx[memory]
```

**Install 9router:**
```bash
pip install 9router

# Or with pxx
pip install pxx[router]
```

**Install both:**
```bash
pip install pxx[all]
```

### Platform-Specific Notes

**macOS (Intel):**
```bash
# hnswlib requires compilation; prebuilt wheel available
pip install --pre pxx

# If compilation needed:
brew install llvm
LDFLAGS="-L/usr/local/opt/llvm/lib" CPPFLAGS="-I/usr/local/opt/llvm/include" pip install pxx
```

**macOS (Apple Silicon):**
```bash
# Native support; no special steps needed
pip install pxx
```

**Linux:**
```bash
# Standard installation
pip install pxx

# Debian/Ubuntu with venv isolation
python3 -m venv ~/pxx-env
source ~/pxx-env/bin/activate
pip install pxx
```

**Windows (WSL2 recommended):**
```bash
# WSL2 Ubuntu environment
wsl --install
wsl --update

# Then follow Linux instructions
pip install pxx
```

### Verify Installation

```bash
# Check pxx version
pxx --version

# Check available commands
pxx --list-commands

# Test basic functionality (no edits)
pxx "What is pxx?"

# Test with memory (if installed)
pxx --edit --with-memory "Improve error handling"
```

## Configuration

### Environment Variables

**Core pxx:**
```bash
PXX_OLLAMA_BASE=http://workstation:11434  # Ollama endpoint
PXX_MODEL=ollama_chat/devstral:24b            # Force model
PXX_AUTOCHECK_DRIFT=1                         # Pre-edit drift check
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
# Create config
mkdir -p ~/.config/pxx
cat > ~/.config/pxx/trusted-paths << 'EOF'
/Users/your-username/projects/
/Users/your-username/work/
EOF

# Now pxx will block edits outside these paths
pxx --edit  # ✓ Works in ~/projects/
cd /tmp && pxx --edit  # ✗ Blocked (outside trusted paths)
pxx --edit --anywhere  # ✓ Override one-shot

# Remove config to disable
rm ~/.config/pxx/trusted-paths
```

## Uninstall

```bash
# Using pip
pip uninstall pxx agentmemory 9router

# Using uv
uv tool uninstall pxx
```

## Troubleshooting

**"pxx: command not found"**
- Ensure installation completed: `pip install pxx`
- Check PATH includes pip install location
- Try `python -m pxx` as alternative

**"Unable to open config file"**
- Config file mismatch; delete `~/.config/pxx/` and restart
- Or use default config: `pxx` (no config needed)

**"No Ollama endpoint found"**
- Ollama not running; start: `ollama serve`
- Network issue; check: `curl http://127.0.0.1:11434/api/tags`
- Override: `PXX_OLLAMA_BASE=http://your-server:11434 pxx`

**"hnswlib compilation failed"**
- Install build tools: `pip install --upgrade setuptools wheel`
- Try precompiled wheel: `pip install --pre hnswlib`
- Skip vector index: works fine with brute-force search

## Next Steps

1. **Read the examples:** `docs/EXAMPLES.md`
2. **Deploy in production:** `docs/DEPLOY.md`
3. **Explore the API:** `docs/API.md`
4. **Check CLAUDE.md** for pxx-specific development info

## Support

- **Issues:** https://github.com/cdnwetzel/pxx/issues
- **Discussions:** https://github.com/cdnwetzel/pxx/discussions
- **Documentation:** https://github.com/cdnwetzel/pxx/tree/main/docs
