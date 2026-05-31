> Backlog ID: 017

# Tier 4: Learnings Loop — Distillation from Autonomous Sessions

**Status:** proposed  
**Effort:** TBD  
**Complexity:** TBD

---

## Problem

Autonomous dogfooding sessions (#012, #015) generate operational data (session metadata, timing, scope decisions, tool interactions) captured in the audit log (#004). This data is currently not analyzed — insights about what pxx does, how it fails, and where to improve are locked in JSONL logs.

---

## Solution

Build a learnings-extraction pipeline that:

1. Reads session audit logs post-mortem
2. Aggregates patterns (e.g., "autonomous edits to this module fail X% of the time")
3. Distills into actionable findings (e.g., "scope boundaries need clarification")
4. Surfaces as prose docs (e.g., `learnings.md`) for future dogfooding cycles

This closes the feedback loop: autonomous sessions → data → learnings → better dogfooding.

---

## Scope

- Implement audit-log readers (JSONL parsing, filtering by session_class)
- Define learning extractors (timing, scope-hit-rate, error patterns)
- Generate learnings document
- Document the distillation methodology

---

## Blocked by

- #001 (Tier 1–3 dogfooding must land first; Tier 4 is built on their output)

## Blocks

None (Tier 4 is introspective, not a prerequisite for other work)

---

## Success Criteria

- Learnings doc is generated from a real session audit
- Document identifies at least 3 actionable insights
- Process is documented for manual or scripted re-runs
