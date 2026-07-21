# Phase 4: Packaging Plan

## Goal
Build wheel and sdist for both services, verify console scripts work.

## Steps

### 9router
```bash
cd services/9router
uv build                    # Creates dist/9router-0.1.0-py3-none-any.whl + .tar.gz
pip install dist/*.whl      # Install wheel
nine-router --help          # Verify console script
```

### agentmemory
```bash
cd services/agentmemory
uv build                    # Creates dist/agentmemory-0.1.0-py3-none-any.whl + .tar.gz
pip install dist/*.whl      # Install wheel
agentmemory --help          # Verify console script
```

## Verification
- Both packages install without errors
- Console scripts are in PATH and executable
- Services can start from installed packages (not just source)
- Startup behaves identically to source-based runs

## Success Criteria
- ✓ dist/ directories contain .whl and .tar.gz for both
- ✓ both `nine-router` and `agentmemory` are in PATH
- ✓ Running them starts the services on correct ports
- ✓ Ready for Phase 5 (pxx integration)

## Output
Create PYPI_PUBLISH.md with:
- twine upload instructions
- package info (name, version, deps)
- Installation instructions
