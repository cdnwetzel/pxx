> Backlog ID: 025

# L-05: OPENAI_API_KEY Environment Isolation

**Status:** planned  
**Effort:** ~30 minutes  
**Complexity:** MEDIUM  
**Severity:** LOW

---

## Problem

`OPENAI_API_KEY="EMPTY"` placeholder is set globally in `os.environ` before `os.execv` into aider:

```python
os.environ["OPENAI_API_KEY"] = "EMPTY"  # Set globally
os.execv(...)  # aider + any git hooks inherit this
```

This placeholder leaks to:
- Git hooks (pre-commit, post-commit, etc.)
- Other subprocesses spawned by aider
- Anywhere with environment visibility

---

## Solution

Pass `OPENAI_API_KEY` only to aider subprocess via explicit environment dict:

```python
env = os.environ.copy()
env["OPENAI_API_KEY"] = "EMPTY"
os.execve(aider_path, args, env)  # Only aider gets it
```

Git hooks and other subprocesses won't see the placeholder.

---

## Why Deferred

1. **Prior failure:** Earlier implementation attempt caused test failures
   - Tests monkeypatch `os.execv`, not `os.execve`
   - Refactoring required careful handling of test mock infrastructure
2. **Low impact:** `OPENAI_API_KEY="EMPTY"` is harmless placeholder, no security risk
3. **Complexity:** Requires refactoring exec boundary + test setup
4. **Token budget:** Would consume ~40-50 tokens for full fix + verification

---

## What Changed Since Prior Attempt

- `os.execv` → `os.execve` requires environment dict
- Test mocks need to be updated to support both function signatures
- Conftest setup may need refactoring

---

## Recommendation

Candidate for Phase 4 if environment isolation becomes higher priority. Good candidate after #023 (test refactor) completes, since new test patterns will make this easier.

---

## Success Criteria

- `OPENAI_API_KEY` not inherited by git hooks
- All tests pass (with updated mocks)
- No performance regression in exec path
- Aider still receives the placeholder correctly

---

## Blocked by

None (lower priority, can start after higher-impact work)

## Blocks

None
