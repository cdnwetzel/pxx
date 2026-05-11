# Gemini CLI Workflow Integration

> Backlog ID: **010**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**.
> Blocks: —. Blocked by: —.

## Context

The user has specified a mandatory workflow for Gemini CLI:
1. Always write a plan file (using `plans/_template.md`).
2. Gemini CLI is pre-approved to proceed with implementation after planning.
3. Successful changes must be committed and pushed.

This plan establishes the `GEMINI.md` file to record these instructions and any Gemini-specific project conventions.

## The 1 mechanism

### M1 — GEMINI.md

Create `GEMINI.md` in the project root to capture:
- The Research -> Plan -> Execute -> Commit/Push lifecycle.
- Reference to `plans/` directory for planning.
- Pre-approval for autonomous execution following a proposed plan.
- Automated commit and push mandate.

## Files to modify

| Path                         | Change                                  |
| ---------------------------- | --------------------------------------- |
| `GEMINI.md` *(new)*          | Create with project-specific instructions |
| `plans/backlog.md`           | Register this plan (ID 010)              |
| `plans/gemini-workflow-integration.md` *(new)* | This plan file |

## Implementation order

1. Create `plans/gemini-workflow-integration.md`.
2. Update `plans/backlog.md`.
3. Create `GEMINI.md`.
4. Commit and push.

## Verification

| Scenario                                    | Expected outcome                         |
| ------------------------------------------- | ---------------------------------------- |
| `GEMINI.md` exists                          | Contains the user's workflow instructions |
| `backlog.md` is updated                     | ID 010 is registered and next ID advances |
| Git status                                  | Clean after commit and push              |
