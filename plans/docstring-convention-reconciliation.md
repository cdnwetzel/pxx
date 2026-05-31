# Reconcile docstring convention across style guide and code

> Backlog ID: **014**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**. Blocks: `013`. Blocked by: `—`.
>
> Stub drafted in response to Claude review finding **F-009** (sixth pass,
> 2026-05-13), with corroboration in Codex's eighth-pass observations.

## Context

`CONVENTIONS.md` and `pxx/prompts/system.md` both state: *"No docstrings
unless asked; no comments unless the why is non-obvious."* But ~20+
docstrings have shipped across `pxx/scope.py`, `pxx/cli.py`, `pxx/audit.py`,
`tests/test_install_hook.py`, and elsewhere — many of them carrying real
"why" content (e.g., `_create_safety_tag`'s explanation of the tag-namespace
design, `is_path_trusted`'s closest-match algorithm).

This is convention-divergence: the written rule misleads new readers
(human or agent). Two reconciliation directions are possible — pick one
and apply it once, or the drift will widen on every new feature.

## The N mechanisms

### M1 — Decide direction

Two options:

- **(A) Loosen the rule** to *"Docstrings on public functions when the WHY
  is non-trivial; no docstrings for simple internal helpers."* This matches
  what's actually shipping today. Most existing docstrings stay; a few
  trivial ones get demoted to inline comments or removed.
- **(B) Tighten enforcement** of the existing "no docstrings" rule. Demote
  the ~20 existing docstrings to `# why:` block comments where the content
  is load-bearing; delete the rest.

**Recommendation:** **(A)** — the existing docstrings carry real value
and removing them would lose context. The rule is the thing that needs to
move, not the code.

### M2 — Apply the chosen convention

After M1's decision:

- Update `CONVENTIONS.md` to state the new rule explicitly, with an
  example of "yes-write-a-docstring" vs "no-don't-bother".
- Update `pxx/prompts/system.md` to match.
- (If (A) was chosen) Skim existing code for the few cases that violate
  the new rule; align them.
- (If (B) was chosen) Demote/delete the existing ~20 docstrings; this is
  the bigger change.

## Verification

| Scenario                                                                   | Expected outcome                                            |
| -------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `CONVENTIONS.md` rule matches `pxx/prompts/system.md` rule verbatim        | One source of truth; aider sees the same rule pxx-the-team sees |
| Random existing docstring inspected — does it match the new rule?          | Yes (after M2 sweep)                                        |
| A future PR adds a new function — does the new convention make the choice obvious? | Yes (the rule includes an example)                  |

## Non-goals

- **Not a sweep of comments.** This plan is specifically about docstrings;
  `# why:` comments are a separate (already-stable) convention.
- **Not a typing rewrite.** Type hints are a different rule in the same
  doc and are out of scope here.

## Open questions

1. **Which direction (A vs B)?** User decision.
2. **Should this land before #013 (cli.py module split)?** If yes, the
   module split applies the new convention in one pass; if no, the
   reconciliation happens twice. Recommend: **yes**, this lands first —
   it's smaller and lower-risk.
3. **Does the convention change affect tests?** Tests have docstrings
   (e.g., `"""Tests for pxx.audit — session JSONL log (#004)."""` at the
   top of `tests/test_audit.py`). Probably treat test-module-level
   docstrings as exempt (they're documentation for the reader, not
   internal helpers).

## Status updates needed in `backlog.md` when this completes

- `#014` status: `proposed` → `planned` → `in-progress` → `done`
- If `#013` is planned, note that #013 should pick up the new convention.
