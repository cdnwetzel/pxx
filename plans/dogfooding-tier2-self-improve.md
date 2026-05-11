# Dogfooding Tier 2 — `--self-improve` (suggest-only)

> Backlog ID: **011**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **planned**. Blocks: `—`. Blocked by: `—`. Parent: `#001`. Sibling: `#010` (Tier 1, done).

## Context

`#001 Dogfooding` defines a five-tier path. Tier 1 (`#010`) is done: pxx can
run its own test/lint gate from any cwd. Tier 2 is the next step on the
"observation → bounded autonomy" axis: **pxx proposes improvements; the human
decides which to apply.** Still no edits — the suggestion phase is read-only.

The dogfooding plan describes Tier 2 as "already supported by the existing
aider workflow plus the `/audit` slash command. Tier 2 just formalizes the
convention and gives it a flag." That framing keeps the scope tight: this is
**not** a new mechanism, it's a one-flag entry-point that pre-configures the
opening prompt so every self-improvement session starts the same way.

Why now: with Tier 1 shipped, pxx can already *prove its own health* before a
session. Tier 2 turns that health check into a starting point: "pxx is green
— now, what could be better?" The output of one `--self-improve` session
becomes the candidate list for the next regular `pxx --edit` session.

## The two changes

### F1 — `pxx --self-improve` flag

A new short-circuit flag on `cli.py`. It:

1. `cd`s into `REPO_ROOT` (the pxx repo) regardless of where it was invoked.
2. Builds an aider invocation in **ask mode** (read-only) with two extra
   `--read` files:
   - `pxx/prompts/system.md` (already loaded by every session)
   - `pxx/prompts/self-improve.md` (new — see P1 below)
3. Forwards remaining user args to aider (so `--message "<seed>"` and friends
   still work).
4. `os.execv`s into aider, same pattern as the default `pxx` flow.

The flag is incompatible with `--edit` and `--self-test` / `--self-lint`;
those combinations exit 2 with a clear message (we don't want a typo to
silently flip the mode to edit).

### P1 — `pxx/prompts/self-improve.md` (new prompt file)

A static markdown file with the opening directive. Tone matches
`pxx/commands/audit.md` (the existing read-only-review slash command) but is
tuned for *forward-looking improvement suggestions*, not just defect-spotting.

Content sketch (will be tuned during implementation, kept short):

```
# Self-improvement review

You are reviewing the pxx codebase for **improvements you would propose to a
human reviewer**. You will NOT edit any files in this session.

## Output format

Produce a single markdown response with this structure:

    ## Suggestions — <YYYY-MM-DD>

    1. **<short title>** — <one-paragraph rationale>
       - Files: `path/to/file.py:L42-L60`
       - Class: docs-drift | scattered-config | convention-divergence | rotting-list | test-gap | other
       - Effort: small | medium | large
       - Risk: low | medium | high
       - Why now: <one line>

    2. ...

Up to ten suggestions, ordered most-valuable first. If you cannot find ten
material improvements, stop — do not pad the list. Quality beats quantity.

## Scope of "improvement"

The three human-curated reviewers at `../review/` (Claude, Gemini, Codex)
consistently surface five classes of issue in this codebase. These are the
in-scope categories — prefer findings that fit one of these classes, since
they're the patterns that empirically matter here:

1. **Docs ↔ code drift** — README claims, help text, comments that contradict
   actual behavior (model names, version pins, env-var lists, command
   examples). The most durable drift pattern in the repo.
2. **Configuration scattered across sources** — values duplicated in setup
   scripts, `pyproject.toml`, config YAMLs, and code defaults with no single
   source of truth (Python version pins, model names, hostnames).
3. **Unenforced conventions** — `CONVENTIONS.md` / `CLAUDE.md` claims that
   diverge from actual code (e.g., "no docstrings" stated, but docstrings
   present). Code that contradicts a stated guardrail.
4. **Rotting enumerated lists** — hardcoded inventories in docs (test counts,
   helper function counts, env-var tables, plan-status summaries) that go
   stale as the codebase grows.
5. **Missing test surface** — code paths, shell scripts, or new flags with
   behavior but no automated coverage.

Also in-scope when material: bugs, latent footguns, dead code, opportunities
to delete code without losing capability. Tag these as "other".

Out of scope: stylistic rewrites that ruff already enforces; new features
that aren't motivated by an observed problem; speculative refactors with no
near-term consumer; findings already documented in `../review/` (these are
upstream of you — surface what the reviewers missed, not echoes of what
they found).

## Hard rules

- Do NOT produce SEARCH/REPLACE blocks. This session is suggest-only.
- Do NOT propose changes to files in `.aiderignore` (model-settings, scripts,
  pyproject, install scripts, governance docs).
- Do NOT propose new dependencies without a separate "why this dep" line.
- If the codebase looks healthy and you find nothing material, say so
  explicitly. Empty findings are a valid outcome.
```

**Provenance of the five issue classes:** distilled from a scan of the most
recent observation files in `../review/claude/`, `../review/gemini/`, and
`../review/codex/` on 2026-05-11. The categories are what those three agents
empirically catch most often when reviewing pxx — making them what an
unprompted model is *least* likely to volunteer on its own, hence worth
spelling out.

## Design decisions (locked)

| Choice                                  | Decision                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------ |
| Mode                                    | Ask mode always — `--self-improve --edit` is rejected at startup                |
| cwd                                     | Always `REPO_ROOT` (like `--self-test`)                                         |
| Prompt delivery                         | Static `pxx/prompts/self-improve.md` via `--read` (no tempfile)                 |
| Short-circuit point                     | Below `--list-commands`/`--install-hook`, above the trusted-paths gate          |
| Trusted-paths interaction               | Skipped — ask mode never edits, so the gate doesn't apply                       |
| Exec model                              | `os.execv` into aider, same as default `pxx`                                    |
| `--message` passthrough                 | Yes — user can seed with a topic, e.g. `pxx --self-improve --message "focus on cli.py"` |
| Banner mode label                       | `mode=ask (self-improve)` so it's visibly distinct from a plain `pxx` session   |

## Open design choices (need user input)

These three are real choices; the rest of the design is independent of them.

1. **Where do the suggestions live after the session ends?**
   - **(A) Chat-only** — model output stays in the aider transcript; user
     copies anything worth keeping. Zero new machinery. Default per the
     dogfooding plan's "or to chat output the user can copy."
   - **(B) Dedicated `suggestions/` directory** — `suggestions/YYYY-MM-DD-HHMMSS.md`
     written by aider when the model is told to do so. Keeps `plans/` reserved
     for human-authored backlog items. Requires the prompt to instruct aider
     to write a specific file, and the user to `/add` it first or pre-create it.
   - **(C) Under `plans/suggestions/`** — same as B but inside the plans tree.
     The dogfooding doc hints at this with "`plans/suggestions-YYYY-MM-DD.md`",
     but mixing "plans" (numbered, backlog-tracked) with "suggestions"
     (ephemeral, model-generated) blurs the backlog's meaning.

   **Recommendation:** start with **A** for v1. If the chat-copy workflow proves
   annoying after a few real sessions, escalate to **B** (cleaner separation
   than C). Either way, deferrable.

2. **What happens if the user runs `--self-improve` outside the pxx repo?**
   - **(A) Always target pxx** — same as `--self-test`; the flag means
     "review pxx itself."
   - **(B) Target cwd if it looks like a Python project (`pyproject.toml`
     present), otherwise pxx.** Lets the prompt-as-template be useful for
     other codebases.

   **Recommendation:** **A** for the same reason as Tier 1 — `--self-` means
   pxx. Generalizing to other repos is a different feature (`pxx --improve`
   without the `self-` prefix) and a different plan.

3. **Should `--self-improve` also lint+test first as a precondition?**
   - **(A) No** — user can chain: `pxx --self-test && pxx --self-improve`.
   - **(B) Yes — auto-invoke `_self_test()` + `_self_lint()` before launching
     the aider session, abort on non-green.** Forces the model to only review
     code that's already passing its own gates.

   **Recommendation:** **A** — composition over coupling. If the user wants
   the green-precondition, the shell `&&` is one keypress. Coupling them
   inside `--self-improve` makes the flag heavier and harder to test.

## Files to modify

| Path                                       | Change                                                                                   |
| ------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `pxx/cli.py`                               | Add `_self_improve_args()` builder + a short-circuit branch in `main()`. ~30 LOC.        |
| `pxx/prompts/self-improve.md` *(new)*      | The static opening prompt (see P1 above). ~40 lines.                                     |
| `tests/test_cli.py`                        | New `TestSelfImproveFlag` class — argv detection, ask-mode enforcement, reject `--edit` combination, prompt file is `--read`d, `--message` passthrough. ~60 LOC. |
| `README.md`                                | Extend the "Self-modes" subsection (added in #010) with one paragraph on `--self-improve`. |
| `CLAUDE.md`                                | One line under "Using pxx" mirroring the #010 entries.                                    |

**Existing primitives to reuse:**

- `pxx/cli.py:_build_aider_args()` — already takes `extra_reads`. Pass the
  self-improve prompt path through this; do **not** duplicate.
- `pxx/cli.py:REPO_ROOT`, `SYSTEM_PROMPT`, `AIDER_CONF`, `MODEL_SETTINGS` —
  all already resolve correctly; no new constants needed.
- `pxx/cli.py:_find_aider()` — already does the same-venv-first lookup.
- The short-circuit pattern from `#010`'s `_self_test()` / `_self_lint()` —
  mirror it exactly for the `--edit`-incompatibility check.

## Implementation order

One commit, in this order so each step is independently testable:

1. **Land the prompt file** (`pxx/prompts/self-improve.md`) with no wiring.
   Tests can read it directly to assert content invariants.
2. **Wire the CLI flag** (`_self_improve_args()` + main branch). Unit-test the
   argv detection, the rejection of `--edit`, and the args produced.
3. **Update README + CLAUDE.md** docs.
4. **Manual smoke** (paste-back, Neo first): `cd /tmp && pxx --self-improve`
   — aider opens in pxx repo, system prompt loads, self-improve prompt loads,
   model produces a numbered markdown list. Confirm no SEARCH/REPLACE blocks
   appear (the model is correctly suppressed by the prompt).
5. **Status cascade** in `backlog.md`: `#011` `planned` → `done` in the
   landing commit; `#001` stays `in-progress` (Tiers 3/4 still pending).

## Verification

| Scenario                                                                 | Expected outcome                                                                  |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `pxx --self-improve` from anywhere                                       | aider opens in pxx repo, banner shows `mode=ask (self-improve)`, system+self-improve prompts loaded |
| `pxx --self-improve --edit`                                              | exit 2, message: "ask mode is required for --self-improve; remove --edit"         |
| `pxx --self-improve --message "focus on endpoints.py"`                  | aider starts with seed message; suggestions scope to endpoints.py                  |
| Model output contains a SEARCH/REPLACE block                             | prompt failure — log it, tighten the prompt's "Hard rules" section                |
| Session ends with empty findings                                          | model explicitly says "no material improvements found" (per prompt's last rule)   |
| Unit: `--self-improve` short-circuits before endpoint detection? **No.** | We DO want endpoint detection — the model is doing real work. Differs from `--self-test`. |
| Unit: extra_reads list includes both `system.md` AND `self-improve.md`   | assert by counting `--read` flag positions and inspecting the paths               |

## Non-goals

- **No `--self-improve --apply`** that combines suggest + apply. The
  dogfooding plan explicitly forbids this: "Keep them separate so the user
  can review." Tier 3 owns the apply path.
- **No automatic file-write of suggestions** in v1 (per open question #1).
- **No looping** — `--self-improve` is one session, ends like any other.
- **No model fine-tuning loop** — that's Tier 4 (`learnings.md`).
- **No cross-repo generalization** — `--self-` means pxx.

## Coordination with other plans

- **Parent:** `#001` (Dogfooding umbrella). Stays `in-progress` after this lands.
- **Sibling done:** `#010` (Tier 1). Provides `--self-test`/`--self-lint` —
  available to the user as a precondition step (`pxx --self-test &&
  pxx --self-improve`), but **not** auto-invoked (per open question #3).
- **Future Tier 3** will likely build on this: a Tier-3 session might be
  seeded by a specific entry from a Tier-2 session's output.
- **`#004` (Session audit log)** is not a hard dep, but a Tier-2 session is
  exactly the kind of thing #004 would want to capture (suggestion → did the
  user implement it? did the implementation succeed?). Worth revisiting once
  #004 has a status.

## Risks and mitigations

| Risk                                                                  | Mitigation                                                                              |
| --------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Model ignores "do not edit" and produces SEARCH/REPLACE blocks        | Ask mode is already enforced at the aider layer; the prompt is the second belt. If breaches happen, the prompt's "Hard rules" gets tightened.   |
| Suggestions drift toward stylistic/ruff territory                     | Prompt explicitly puts those out of scope. If it keeps happening, narrow the in-scope list further.                                              |
| Output quality varies wildly with model choice                        | Document a recommended model in the README (Studio's `devstral:24b` over Neo's `qwen3:4b`). Tier-2 sessions on the small model are explicitly low-fidelity. |
| `--self-improve --edit` typo gets through                             | Reject combination at startup with exit 2. Tested as a unit case.                       |
| Prompt becomes the de-facto governance doc for what's "an improvement" | Keep the prompt short and link to `CONVENTIONS.md` / `CLAUDE.md` for hard rules — don't duplicate them.                                          |

## Status updates needed in `backlog.md` when this completes

- `#011` status: `planned` → `in-progress` → `done` (in the landing commit).
- `#001` status: stays `in-progress` (Tiers 3/4 still pending).
- `Next free ID`: bump from `011` to `012`.
