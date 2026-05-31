> Backlog ID: 024

# L-03: Python 3.13 Ceiling Documentation

**Status:** proposed  
**Effort:** ~5 minutes  
**Complexity:** TRIVIAL  
**Severity:** LOW

---

## Problem

`pyproject.toml` pins Python to `<3.13` but the reason is not documented. Future maintainers won't know if this is:
- A known incompatibility to fix later
- A conservative ceiling that can be removed
- A policy decision

---

## Solution

Add a comment to `pyproject.toml` explaining the ceiling:

```toml
# Python 3.13: Not yet tested. asyncio and subprocess behavior changes
# in 3.13 may affect endpoint detection and aider subprocess handling.
# Conservative: keep 3.12 as max until migration tested.
python = ">=3.11,<3.13"
```

---

## Why Deferred

**Guardrail constraint:** CLAUDE.md lists `pyproject.toml` as a hard guardrail:
> "Must NOT be modified without explicit user request — wrong values break installs, OOM the Studio, or alter agent behavior subtly"

Even documentation comments on config files require user request per governance rules.

---

## Recommendation

User can request this directly; agent should skip. Low priority (documenting a non-issue).

---

## Blocked by

None

## Blocks

None
