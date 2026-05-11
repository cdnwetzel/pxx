# VS Code (Continue.dev) Integration

> Backlog ID: **009**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **planned** (longer-horizon — design-complete, implementation deferred).
> Blocks: `—`. Blocked by: `—`.
>
> Planning depth is locked. Implementation is not on the near-term path;
> the user explicitly tagged this as long-horizon. The plan exists so that
> when the time comes — possibly months out — the integration is
> principled rather than improvised.

## Context

pxx today is CLI-only: `pxx [--edit] [other args]` in a terminal.
[Continue.dev](https://continue.dev) is a VS Code (and JetBrains)
extension that brings coding-assistant UX into the editor: inline
edits, chat panel, optional autocomplete. Continue can be pointed at
any OpenAI- or Ollama-compatible backend, including the same Studio
and Neo Ollama instances pxx already uses.

The user has interest in possibly using VS Code alongside the CLI. The
risk of organic adoption (install Continue manually, point it at
Ollama, copy/paste prompts) is **divergence**: the editor and the CLI
end up using different system prompts, different model parameters,
different scope discipline. That defeats the careful conventions pxx
has built up.

This plan defines a thin **adapter layer** that keeps Continue and pxx
in sync without forking either tool.

## The mechanisms

### M1 — `pxx config continue`

A subcommand that generates (or merges) `~/.continue/config.json`
populated from pxx's existing sources of truth:

- **`apiBase`**: same endpoint pxx detects (Studio LAN / remote / Neo
  localhost). Generated from the same detection logic.
- **Models**: `devstral:24b` and `qwen3:4b` mapped to the same default
  selection rules as `pxx/cli.py:model_for()`.
- **`systemMessage`**: inline content of `pxx/prompts/system.md`.
- **`customCommands`**: derived from `pxx/commands/*.md`, one entry per
  file, name = filename stem, prompt = file content, description =
  first-heading text (reuses #007's `commands_index.py`).
- **Analytics + telemetry**: disabled. Matches pxx's privacy posture.
- **Default chat mode**: Continue's "chat" panel (closest to pxx's
  ask-by-default), not the inline-edit mode.

**Merge behavior:** if `~/.continue/config.json` already exists, the
pxx-managed sections are wrapped in `// pxx-managed:start` /
`// pxx-managed:end` JSON-style comments and only those sections are
replaced on regeneration. User-added entries outside the markers are
preserved.

### M2 — Shared system prompt

Continue ships pxx's system prompt inline at config generation time.
The trade-off:

- **Inline (chosen for v1):** Continue reads the prompt as a static
  string at startup. User must re-run `pxx config continue` to pick up
  edits to `system.md`. Simple, no plugin work.
- **Dynamic file reference (deferred):** Continue resolves the system
  prompt at session start by reading the file. Requires checking
  whether Continue's config supports a file-reference format; deferred
  until v2.

### M3 — Slash-command mapping

`pxx/commands/*.md` files become entries in Continue's `customCommands`
array. Each entry is invocable via `/audit`, `/refactor`, etc. inside
the Continue chat panel — the same names the CLI uses.

This requires #007 (`pxx/commands_index.py`) to exist as a pure module
the config generator can import. Without #007, the generator
re-implements the directory scan; with #007, it reuses one source of
truth. Strong soft dependency; not a hard blocker.

### M4 — Ask-by-default mirror

Continue defaults to its "chat" panel (read-and-discuss); inline edits
require explicit user invocation. This matches pxx's reviewer-first
posture so the user isn't surprised by different behavior between CLI
and editor.

Concretely: the generated config does not enable Continue's autocomplete
in v1 (it's a separate behavior with its own model needs). Autocomplete
can be added in v2 with its own small model (`qwen3:4b` would work on
the Neo locally).

## Files to modify

| Path                                          | Change                                                                                              |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `pxx/continue_config.py` *(new)*              | Pure module: `build_config(endpoint, model, system_prompt, commands) -> dict`. JSON-serializable.   |
| `pxx/cli.py`                                  | Add `pxx config continue` subcommand (or `pxx --emit-continue-config`). Detect endpoint, call builder, write file. |
| `pxx/continue_merge.py` *(new)*               | Merge logic for pre-existing `~/.continue/config.json`. Respects pxx-managed markers.               |
| `tests/test_continue_config.py` *(new)*       | Builder tests + merge tests (existing file, no existing file, malformed JSON).                      |
| `README.md`                                   | Add an "Editor integration" subsection.                                                             |
| `CLAUDE.md`                                   | Same — flag that Continue and pxx share state via this generator.                                    |

**Existing primitives to reuse:**

- `pxx/endpoints.py:detect_endpoint()` — same logic the CLI uses; reuse
  for `apiBase`.
- `pxx/cli.py:model_for()` — reuse for default model.
- `pxx/cli.py:SYSTEM_PROMPT` constant — path to system.md.
- `pxx/commands_index.py` *(from #007)* — command listing; reuse if
  available, fall back to direct directory scan if #007 hasn't landed.
- `json` stdlib — no third-party JSON dep needed.

## Implementation order

Four commits, smallest first:

1. **`pxx/continue_config.py` + tests** — pure builder with no caller.
   Test against the existing six commands + a faked endpoint.
2. **`pxx config continue` subcommand in `cli.py`** — writes to a
   tempfile in the first version; user manually moves it. Validates
   end-to-end before automating the merge.
3. **`pxx/continue_merge.py` + tests** — handles the
   already-have-a-config case. Most users without an existing
   Continue setup won't hit this until later.
4. **Polish + documentation** — README/CLAUDE.md, smoke test against
   real Continue extension.

## Coordination notes

- **#007 (Slash-command discoverability)** — reuses `commands_index.py`
  for `customCommands` population. Soft dependency: if #007 isn't
  landed when #009 implementation starts, duplicate a minimal version
  inside `continue_config.py` and harmonize later.
- **#002 (Safety foundation)** — Continue commits via git, so #002's
  pre-commit hook gates Continue edits identically to CLI edits. No
  Continue-specific safety code needed.
- **#003 (Scoping & dry-run)** — Continue has its own "workspace" /
  "open files" concept. Reconcile via documentation: pxx's `--scope`
  doesn't apply to Continue sessions; users wanting that discipline
  rely on Continue's own context controls.
- **#004 (Session audit log)** — out of scope for v1. Continue sessions
  do not write to the pxx audit log. v2 might integrate via a
  Continue plugin if/when one is needed.

## Verification

| Scenario                                                                          | Expected outcome                                                                                          |
| --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `pxx config continue` on a fresh Neo (no existing `~/.continue/config.json`)      | File created with correct apiBase, models, systemMessage, customCommands; analytics disabled              |
| Open VS Code, trigger Continue chat panel                                         | Connects to right Ollama; behaves the same as `pxx` (no `--edit`) from the terminal                       |
| In Continue, type `/audit` (or use custom command picker)                         | Loads the same audit prompt as CLI's `/load .../commands/audit.md`                                        |
| Use Continue to make an edit + commit                                             | Pre-commit hook from #002 gates it identically to a CLI-driven aider edit                                 |
| `pxx config continue` when an existing config has user customizations             | pxx-managed sections updated; non-pxx-managed sections preserved verbatim                                 |
| `pxx config continue` against malformed existing JSON                             | Clear error; refuse to overwrite; suggest the user fix or remove the existing file                        |
| Edit `pxx/prompts/system.md` and re-run `pxx config continue`                     | New config has the updated system message inline                                                          |
| Edit `pxx/commands/audit.md` and re-run `pxx config continue`                     | New config has the updated audit prompt                                                                   |

## Non-goals

- **Forking or rewriting Continue.** Use it as-is.
- **Custom VS Code extension.** No.
- **Non-Ollama backends through this integration.** Continue supports
  many, but pxx's posture is offline-first / Ollama-only.
- **Continue autocomplete in v1.** Out of scope; separate concern (own
  model needs, own latency requirements).
- **JetBrains support.** Continue supports it, but pxx's config
  generator stays VS Code-only in v1.
- **Anything requiring Continue to read from pxx's running process.**
  pxx generates a static config; Continue runs independently.
- **Continue sessions appearing in pxx's audit log (#004).** Out of
  scope for v1; revisit when #004 is mature.

## Status updates needed in `backlog.md` when this completes

- `#009` status: `planned` → `in-progress` → `done`
- No automatic cross-plan column changes. When fleshing out further,
  coordination with `#004` (audit log integration) may surface as
  either a v2 dependency or an explicit boundary.
