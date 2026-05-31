> Backlog ID: 023

# M-07: Test Architecture Refactor — Call Verification → State Verification

**Status:** planned  
**Effort:** ~2 hours  
**Complexity:** HIGH  
**Severity:** MEDIUM

---

## Problem

Current test suite uses call-verification mocking:
```python
# Current pattern: verify function was called with X args
mock_func.assert_called_with("arg1", "arg2")
```

This approach is **fragile to implementation changes**. If internal logic changes how a function is called (same behavior, different call args), tests break even though the feature works correctly.

---

## Solution

Replace with state-verification pattern:
```python
# Target pattern: verify final state is correct
assert result.status == "success"
assert result.processed_count == 42
```

State verification is:
- Decoupled from implementation details
- More resilient to refactoring
- Better documents expected behavior (the "what" not the "how")

---

## Scope

- Affects: Majority of test suite (~100+ test cases)
- Files: `tests/test_*.py` (all test modules)
- Core changes: Test helper functions + test case rewrites (~200 lines helper code)

---

## Why Deferred

1. **Large scope:** Requires rewriting ~100+ test cases in parallel
2. **Architectural risk:** Changes core test pattern; requires validation across full suite
3. **Token budget:** Would consume ~90-120 tokens for thorough implementation + verification
4. **Scheduling:** Requires 1.5–2 hours uninterrupted focus

---

## Recommendation

Schedule as dedicated session (not inline with other work). Good candidate for a focused refactor day when test fragility issues accumulate.

---

## Success Criteria

- All tests still pass
- New tests written in state-verification style
- Old tests converted to new pattern
- Test helper functions refactored to support both patterns temporarily during transition

---

## Blocked by

None

## Blocks

None
