# VS Code (Continue.dev) Integration

> Backlog ID: **009**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed** (stub, longer-horizon). Blocks: `—`. Blocked by: `—`.
>
> Explicitly captured as a long-horizon stub. Not on the near-term
> implementation path. The stub preserves the design constraints so
> that whenever the user does want VS Code alongside the CLI, the
> integration is principled rather than improvised.

## Context

pxx today is CLI-only — `pxx [--edit] [other args]` in a terminal.
[Continue.dev](https://continue.dev) is a VS Code (and JetBrains) extension
that brings coding-assistant UX into the editor: inline edits, chat
panel, optional autocomplete. Continue can be pointed at any OpenAI-
or Ollama-compatible backend, including the same Studio / Neo Ollama
instances pxx uses.

The user mentioned VS Code interest earlier in pxx's design conversation.
We deferred it explicitly to keep the CLI experience tight. This stub
captures the integration intent without committing to implementation.

## The mechanisms (sketch — to be expanded when fleshed out)

- **Continue config generation**: `pxx config continue` writes (or
  merges into) `~/.continue/config.json`, pointed at the same endpoint
  pxx detects, using the same model defaults, the same system prompt,
  and Continue-equivalent representations of the slash commands.
- **Shared system prompt**: Continue reads `pxx/prompts/system.md`
  directly (or a Continue-formatted derivation) — single source of
  truth so editor and CLI never diverge on style/behavior rules.
- **Shared model settings**: Continue's per-model config draws from
  `config/model-settings.yml` so context windows match what pxx uses,
  avoiding silent OOMs on the Studio.
- **Ask-by-default mirror**: Continue defaults to its read-only / chat
  mode, with explicit opt-in to edit — matches pxx's reviewer-first
  posture (commit `957e4d0`) so the editor isn't a backdoor around the
  CLI's safety default.

## Open questions

1. Should Continue and pxx **share state** — e.g., should #004's audit
   log include Continue sessions, or stay separate? Affects scope.
2. Does Continue's config surface allow custom slash commands cleanly?
   If not, just document the manual workflow.
3. What does the install bundle look like — extension only, config only,
   both via a one-shot script? Probably both, opt-in.
4. Does this need to work on the Studio (if the user ever opens VS Code
   there directly) or is it Neo-only?
5. JetBrains support is in Continue itself; should `pxx config jetbrains`
   exist, or is VS Code-only acceptable in v1?

## Coordination notes

- **#001 dogfooding**: editor-side edits would benefit from the same
  safety net (#002). The pre-commit hook from #002 catches them as long
  as Continue commits via git. The diff cap likewise.
- **#003 scoping**: Continue has its own notion of "scope" (open files,
  workspace folder); reconcile semantics or document the difference.
- **#004 audit log**: open question above — log Continue sessions or
  treat them as out-of-scope?

## Verification (placeholders — to be expanded when fleshed out)

| Scenario                                                                  | Expected outcome                                                                                   |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Run `pxx config continue` on a fresh Neo                                  | `~/.continue/config.json` exists, points at the right Ollama endpoint, uses pxx's system prompt    |
| Open VS Code, trigger Continue chat                                       | Same model, same system prompt, same conventions as `pxx` in the terminal                          |
| Use Continue to make a file edit in pxx's repo                            | The pre-commit hook from #002 gates the commit identically to a CLI-driven aider edit              |
| `pxx --scope tests/` discipline vs Continue                               | Either Continue respects an analogous scope, or the difference is documented and obvious           |

## Non-goals

- Forking or rewriting Continue
- Building a custom VS Code extension (use Continue as-is)
- Supporting non-Ollama backends through this integration (it's about
  mirroring pxx's offline-first posture)
- Anything that requires Continue to read from pxx's running process
  state

## Status updates needed in `backlog.md` when this completes

- `#009` status: `proposed` → `planned` → `in-progress` → `done`
- No automatic cross-plan column changes. When fleshed out, coordination
  with #004 (Continue sessions in the audit log) may surface as either
  a hard dependency or an explicit "out of scope" boundary.
