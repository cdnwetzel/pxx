# PyPI Publishing Guide for Phase 5 Services

## Packages

### 9router (v0.1.0)
- **Wheel:** `services/9router/dist/9router-0.1.0-py3-none-any.whl` (6.0 KB)
- **Source:** `services/9router/dist/9router-0.1.0.tar.gz` (6.1 KB)
- **Console Script:** `nine-router`
- **Entry Point:** `9router_pkg.main:main`
- **Dependencies:** fastapi>=0.104.0, uvicorn[standard]>=0.24.0, httpx>=0.25.0
- **Python:** >=3.11

### agentmemory (v0.1.0)
- **Wheel:** `services/agentmemory/dist/agentmemory-0.1.0-py3-none-any.whl` (8.9 KB)
- **Source:** `services/agentmemory/dist/agentmemory-0.1.0.tar.gz` (8.8 KB)
- **Console Script:** `agentmemory`
- **Entry Point:** `agentmemory_pkg.main:main`
- **Dependencies:** fastapi>=0.104.0, uvicorn[standard]>=0.24.0
- **Python:** >=3.11

## Installation (from PyPI)

Once published, users can install with:

```bash
# Install both services
pip install 9router agentmemory

# Or individually
pip install 9router
pip install agentmemory
```

## Verification (Post-Install)

After installation, verify both services are available:

```bash
# Check console scripts are in PATH
which nine-router
which agentmemory

# Start 9router
nine-router &
# Should bind to http://127.0.0.1:20128

# Start agentmemory (in another terminal)
agentmemory &
# Should bind to http://127.0.0.1:3111

# Test health endpoints
curl http://127.0.0.1:20128/health
curl http://127.0.0.1:3111/health
```

## Publishing Steps

### Prerequisites
```bash
# Install twine for publishing
pip install twine
```

### Upload to PyPI

```bash
# Publish 9router
cd services/9router
twine upload dist/*

# Publish agentmemory
cd ../agentmemory
twine upload dist/*
```

### Upload to TestPyPI (Recommended First)

Test publishing to TestPyPI before pushing to production:

```bash
# Publish 9router to TestPyPI
cd services/9router
twine upload -r testpypi dist/*

# Publish agentmemory to TestPyPI
cd ../agentmemory
twine upload -r testpypi dist/*

# Test installation from TestPyPI
pip install -i https://test.pypi.org/simple/ 9router==0.1.0
pip install -i https://test.pypi.org/simple/ agentmemory==0.1.0
```

## Integration with pxx

After publishing to PyPI, update pxx to use installed packages:

```bash
# Add to pxx's dependencies or setup.py
pip install 9router agentmemory

# pxx will auto-detect and use installed services via:
# - PXX_ROUTER_API=http://127.0.0.1:20128
# - PXX_MEMORY_API=http://127.0.0.1:3111
```

## Build Artifacts

Build date: 2026-06-03

- **9router:** 6.0 KB (wheel), 6.1 KB (source)
- **agentmemory:** 8.9 KB (wheel), 8.8 KB (source)

Both packages are production-ready and have been locally verified:
- ✓ Port binding verified
- ✓ Service startup verified
- ✓ Clean shutdown verified
- ✓ No runtime errors

## Next Steps

1. Publish to TestPyPI (verify installation)
2. Publish to PyPI (production)
3. Update pxx to depend on these packages
4. Wire Phase 5 integration in `pxx/cli.py`
5. Ship Phase 5 production release
