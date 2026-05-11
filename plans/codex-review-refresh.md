# Codex Review Refresh

> Backlog ID: **011**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **in-progress**.
> Blocks: —. Blocked by: —.

## Context

The review directory at `../review/` has an ownership inventory that assigns
the numbered review series and `inventory.md` to Codex. The pxx repo now has a
formal `plans/backlog.md` inventory plus several detailed plan files, so the
Codex-owned review artifacts need a source-truth refresh against the current
code and planning surface.

This pass is documentation-only. It should not edit Gemini-owned review files,
Claude guidance, code, tests, setup scripts, or model/config guardrails.

## The 2 mechanisms

### M1 — Inventory-gated ownership check

Read `../review/inventory.md` first and treat it as the write boundary. Only
Codex-owned review files may be updated, and ownership changes are avoided
unless the inventory itself is stale.

### M2 — Current-source refresh

Refresh Codex-owned review files against the current pxx tree, with special
attention to `plans/backlog.md`, the registered `plans/*.md` files, and any
untracked plan files visible in the working tree.

## Files to modify

| Path | Change |
| ---- | ------ |
| `plans/backlog.md` | Register this Codex refresh plan. |
| `plans/codex-review-refresh.md` *(new)* | Plan file for this pass. |
| `../review/README.md` | Refresh Codex-owned index if plan inventory status is stale. |
| `../review/01-overview.md` | Refresh repo layout and planning surface if stale. |
| `../review/02-architecture.md` | Refresh architecture notes if current source has drifted. |
| `../review/03-behaviors-and-guardrails.md` | Refresh guardrail and planning notes if stale. |
| `../review/04-observations.md` | Refresh observations against current inventory and plans. |
| `../review/00-init-codebase-notes.md` | Refresh only if current inventory invalidates initial notes. |
| `../review/01-line-referenced-pass.md` | Refresh only if cited findings need source-truth updates. |
| `../review/inventory.md` | Refresh only if ownership metadata is stale. |

## Implementation order

1. Register this plan in `plans/backlog.md`.
2. Read current pxx source, registered plans, and Codex-owned review files.
3. Update only stale Codex-owned review artifacts.
4. Run lightweight verification for docs and repo status.
5. Commit and push the pxx/review documentation changes.

## Verification

| Scenario | Expected outcome |
| -------- | ---------------- |
| Ownership boundary | `git diff` shows no edits to Gemini-owned review files. |
| Backlog registration | `plans/backlog.md` includes ID 011 and next free ID is 012. |
| Review refresh | Codex-owned review files describe the current planning inventory. |
| Working tree | Only intended files are staged and committed. |

## Non-goals

- Implementing any pxx feature plan.
- Editing Gemini-owned review files.
- Rewriting historical review notes unless source drift requires it.

## Status updates needed in `backlog.md` when this completes

- `#011` status: `in-progress` -> `done`.
