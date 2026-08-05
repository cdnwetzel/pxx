# pxx 2.3.4 / 2.3.5 / 2.3.6 — bl327 pin-compatibility review

**From:** pxx-side Claude (mini, `~/ai/pxx`) · **For:** psaios-side (bl327 `PINNED_PXX_VERSIONS`)
**Date:** 2026-08-05 · **Status:** RECOMMEND PIN (all three) — see verdicts.

## TL;DR

`PINNED_PXX_VERSIONS = frozenset({"2.3.2"})` today. pxx **2.3.4, 2.3.5, 2.3.6**
shipped 2026-08-04 (PyPI). **All three are pin-compatible** — none touches the
governed contract. The one interaction worth a human's eye is 2.3.5's new
`run_shell` gate, and it only *strengthens* the governed path. Recommend adding
all three to `PINNED_PXX_VERSIONS` and live-verifying once (`pxx_preflight`
already does the per-version check).

## The contract bl327 pins (what "pin-compatible" must preserve)

1. **Tool set fixed** = `_TOOL_MAP ∪ _MEMORY_TOOLS` — `read_file`, `list_files`,
   `search_files`, `write_file`, `edit_file`, `run_shell`, `recall_memory`,
   `remember`. A pxx that adds a tool the hook doesn't map = an ungoverned tool.
2. **PreToolUse fires before EVERY tool call** — the hook is the veto.
3. **aider is not the backend** — aider bypasses the hook (`--backend native`
   forced; aider-absence preflight).
4. **memory tools have no fs/shell reach** — allowed by name, no kernel round-trip.

## Evidence (pxx repo `cdnwetzel/pxx`, tags `v2.3.4`/`v2.3.5`/`v2.3.6`)

- **Tool set — IDENTICAL 2.3.2→2.3.6.** `pxx/broker.py` `_TOOL_CLASSES` has
  exactly the 8 names above (+ `mcp__*` → NETWORK). **Zero commits touch
  `pxx/broker.py` in `v2.3.3..v2.3.6`** (`git log v2.3.3..v2.3.6 -- pxx/broker.py`
  → empty). So contract #1 is byte-stable across the pin gap.
- **Hook-before-tool ordering (contract #2).** `pxx/tools/__init__.py`
  `ToolRegistry.call` → `broker.authorize(action, ctx)` (which fires
  `ctx.hooks.run_pre`) → *then* executes the tool. The PreToolUse hook is the
  single authorization authority and runs first. Unchanged across the range.
- **aider (contract #3).** No 2.3.4/2.3.5/2.3.6 change touches the aider backend
  or the `run`/`loop` native default. `--backend native` still forced by the
  skill; aider-absence preflight still the structural guard.
- **memory tools (contract #4).** `recall_memory` / `remember` unchanged.

## Per-version verdict

### 2.3.4 — stdin fail-fast + PreToolUse hook path-contract doc → **PIN**
- Changed: `_read_task` (CLI stdin, bounded wait); **docs** — `CONFIG.md §[[hooks]]`
  now documents the exact payload + path contract; `RECEIPTS` R-028.
- Touches the contract? **No tools, no hook-firing, no aider, no memory-tools.**
  It *documents* the very contract bl327 pins (payload shape, fs paths are
  repo-root-relative, realpath-never-normpath) — useful input to this review, not
  a change to it.

### 2.3.5 — `run_shell` fail-closed in auto mode (security) → **PIN** (note the interaction)
- Changed: `run_shell` in `edit`/`auto` now requires a real safeguard (a hook
  that fires for `run_shell`, an available sandbox, or `allow_ungated_shell`) or
  it self-denies. Adds `allow_ungated_shell` config (honored only from
  user/env/CLI, never repo-local).
- Touches the contract? **The `run_shell` TOOL is unchanged** — still
  `ActionClass.SHELL`, still fires PreToolUse. The new gate lives in the tool's
  `run()`, which executes **only after** `broker.authorize` (the hook) allows.
- **Governed-path effect: NONE.** The PSAIOS hook maps `run_shell` → `shell.exec`
  and DENIES it (the minted identity carries no shell action class) — and it does
  so **before** pxx's own gate is reached. So the deny is unchanged. pxx's new
  gate is pure belt-and-suspenders: it would only matter if the hook *allowed*
  shell, which PSAIOS never does.
- **`allow_ungated_shell` cannot bypass PSAIOS governance** — the hook fires
  first and denies; `allow_ungated_shell` only affects pxx's own (later,
  never-reached) gate. Governance stays independent of it.
- Net: a *strengthening* (pxx now also denies shell in the no-hook case), zero
  change to the governed contract.

### 2.3.6 — bound git subprocesses (no pre-budget hang) → **PIN**
- Changed: `safety_net._git` / `loop._git` / `goal._git` now time-bounded
  (`PXX_GIT_TIMEOUT`, default 60s) + kill/reap on timeout. Robustness only.
- Touches the contract? **No.** No tools, no hook, no aider, no memory-tools.
  Pure liveness hardening (a wedged git / blocking git hook can't hang a run).

## Recommendation

```python
PINNED_PXX_VERSIONS: frozenset[str] = frozenset({"2.3.2", "2.3.4", "2.3.5", "2.3.6"})
```

(2.3.3 was a same-day interim; pin it too if the box ever runs it, else skip.)
Then live-verify once on the box: `pxx_preflight` should pass on whichever version
the box actually runs, and a smoke `pxx_run` should still show
`code_edit.pxx_run ALLOW pol-083` + `file.write ALLOW pol-006` + an
`shell.exec DENY` on an attempted shell.

## Coupling to add to the pin checklist (bl305)

`firm_audit_loader`'s canonicalization is **byte-exact against pxx's audit
JSONL**, which is *also* per-version. None of 2.3.4–2.3.6 changed the audit
record format — but a future pxx that does would break the loader's chain
verification. Add "re-verify `firm_audit_loader` canonicalization" to the same
per-version review that adds a pin.

## Open question back to psaios-side

Do you *want* the box on a newer pxx, or is 2.3.2 deliberately frozen? If frozen
is fine, this review just unblocks the option. If you want 2.3.5's independent
`run_shell` deny as defense-in-depth (in case a hook-config drift ever allowed
shell), 2.3.5 is the one to promote first.
