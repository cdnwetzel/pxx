# Scoping & Dry-Run

> Backlog ID: **003**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **planned**. Blocks: 001. Blocked by: —.

## Context

Today `pxx --edit` gives aider edit authority over the entire repo (bounded
only by `.aiderignore`, if present). For real Python codebases that have
years of work in them, that's too coarse. We need three additions:

- **Scope** — restrict edits to a specific path within the repo. Lets the
  user say "edit only `tests/`" (the stepping-stone phase from the
  brainstorm), or "refactor only `module_x/`" without risking siblings.
- **Dry-run** — see concrete diffs that *would* apply, without applying or
  committing. Different from ask mode: ask is conversational ("here's what
  I'd change"); dry-run produces real SEARCH/REPLACE blocks the user can
  inspect or `git apply` manually.
- **Trusted paths** — optional belt-and-suspenders. If the user has
  populated a `trusted-paths` config, `pxx --edit` outside those paths
  requires an extra `--anywhere` flag. If no config exists, behavior is
  unchanged (all paths trusted by default).

This plan blocks #001 because dogfooding the agent against its own code
needs `--scope` to limit each session to a single module or test directory,
so a bad edit can't cascade across the codebase.

## The three mechanisms

### S1 — `--scope <path>` flag

`pxx --edit --scope <path>` (with optional repeats: `--scope a/ --scope b/`)
restricts the session to edits under one or more path prefixes within the
repo.

**Enforcement layers, from soft to hard:**

1. **Prompt-level** *(always on)*: when `--scope` is set, pxx injects a
   directive into the system prompt:

   > SCOPE: This session may only edit files under: `<paths>`. If asked to
   > change a file outside this scope, refuse and tell the user to widen
   > the scope. Do not produce SEARCH/REPLACE blocks for out-of-scope files.

2. **Pre-commit gate** *(if #002 is also installed)*: pxx exports
   `PXX_SCOPE=<colon-separated-paths>` before exec'ing aider. The
   pre-commit hook from #002 reads it and rejects commits that touch
   files outside any scope. This is the hard enforcement layer — it
   works even if the model ignores the prompt directive.

3. **Banner** *(always on)*: `pxx: ... scope=<paths>` printed at launch
   so the active restriction is visible.

**Resolution rules:**

- Paths are interpreted relative to the **repo root** (not cwd), so
  `--scope tests/` works regardless of where the user runs `pxx`.
- Multiple `--scope` flags combine as a union (any of the paths is
  in-scope).
- v1 supports directory prefixes only — no globs (`**/*.py`). Globs are
  deferred until prefix matching proves insufficient.
- A file is in-scope if its path *starts with* any scope prefix after
  normalization.

**Interactions:**

- `--scope` without `--edit`: ask mode. Scope still tightens the repo-map
  by setting `--subtree-only` on aider (one of the scopes); the model
  sees less noise.
- `--scope` with `--big` (from #002): scope still enforced; only the diff
  cap is bypassed.

### S2 — `--dry-run` flag

`pxx --edit --dry-run` enables aider's existing `--dry-run` mode. Aider
produces concrete SEARCH/REPLACE blocks but does not modify any file and
does not commit. The output remains visible to the user.

**Minimal implementation:**

- `--dry-run` is parsed out of `sys.argv` by pxx (so we can show it in the
  banner) and then re-injected into aider's args.
- Banner shows `mode=dry-run (no files will change)`.
- Combinations with other flags:
  - `--dry-run` without `--edit`: redundant (ask mode doesn't write
    anyway). pxx warns but proceeds.
  - `--dry-run` with `--big`: pointless (nothing commits). pxx warns
    but proceeds — `--big` is a no-op in dry-run.
  - `--dry-run` with `--scope`: works — scope still constrains what
    the model proposes; dry-run just suppresses application.

**Why both `--dry-run` and ask mode exist:**

- Ask mode is *conversational*: the model can describe changes in prose,
  point at lines, suggest approaches. No structured diff output.
- Dry-run produces actual diff blocks — useful when the user wants to
  copy/paste into a patch, or when they want to review the model's
  exact intended edit, character for character.

### S3 — Trusted paths config (optional)

A config file at `${XDG_CONFIG_HOME:-$HOME/.config}/pxx/trusted-paths`,
one absolute or `~/`-prefixed path per line. Comments (`#`) and blank
lines ignored.

**Semantics:**

- If the file **doesn't exist or is empty**, all paths are trusted —
  `pxx --edit` works anywhere. No behavior change for users who never
  set this up.
- If the file **exists and has entries**, `pxx --edit` outside any
  trusted prefix requires `--anywhere`. The error message points the
  user at the config file and shows the closest matching prefix.
- `--anywhere` overrides the check for a single session.

**Example file:**

```
# Paths where pxx --edit is allowed without --anywhere
~/ai/code_pro/pxx
~/work/ps_aios
~/personal/scratch
```

**Why opt-in by default:** the goal is a "belt-and-suspenders" feature for
users who want extra protection. Making it required out of the box would
add friction for users who haven't yet decided which paths matter to
them.

## Files to modify

| Path                                          | Change                                                                                                  |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `pxx/cli.py`                                  | Parse `--scope` (multi), `--dry-run`, `--anywhere`. Inject scope directive into system prompt path. Export `PXX_SCOPE` env. Set banner. Pass `--dry-run` to aider. |
| `pxx/scope.py` *(new)*                        | Pure helpers: resolve scope paths relative to repo root; load + parse `trusted-paths`; check whether cwd is under a trusted prefix.                                |
| `pxx/prompts/system.md`                       | Add a conditional "SCOPE" paragraph that pxx fills in based on `--scope`. (Or keep this in cli.py via a `--read` of a generated file.)                              |
| `scripts/.git-hooks/pre-commit` *(if #002 has landed)* | Add a section that reads `PXX_SCOPE` and rejects commits with out-of-scope files.                                                                          |
| `tests/test_cli.py`                           | Tests for argv parsing of `--scope`/`--dry-run`/`--anywhere`; combinations with `--edit`/`--big`.                                                                  |
| `tests/test_scope.py` *(new)*                 | Tests for `pxx.scope`: prefix resolution, trusted-paths loading, in-scope check, edge cases (no config, empty config, paths with trailing slash, `~/` expansion). |
| `CLAUDE.md`                                   | Document `--scope`, `--dry-run`, `--anywhere`, and the `trusted-paths` config.                                                                                     |
| `README.md`                                   | Add subsections under "Modes" describing the new flags.                                                                                                            |

**Existing primitives to reuse:**

- `pxx/cli.py:_build_aider_args()` — extend to take scope + dry-run +
  anywhere
- `pxx/cli.py:_in_git_repo()` — used to find the repo root (extend to
  `_repo_root()` returning the path)
- `pxx/endpoints.py:Endpoint` dataclass pattern — copy for `Scope`
  if needed (probably not; a `list[Path]` is sufficient)

## Implementation order

Three commits, smallest first:

1. **S2 (dry-run)** — single flag passthrough + banner. ~10 lines + a test.
   Lowest risk, can land standalone.
2. **S1 (scope)** — new module `pxx.scope`, CLI flag parsing, prompt
   injection, env export. ~80 lines + tests. No hard enforcement yet
   (relies on prompt + #002 pre-commit hook when present).
3. **S3 (trusted paths)** — new file in config dir, loader in
   `pxx.scope`, CLI check, `--anywhere` flag. ~40 lines + tests.

Each commit has its own verification scenarios; bail and reassess if any
land with unexpected friction.

## Coordination with #002

S1's hard enforcement layer depends on the pre-commit hook from #002.
**Order matters:**

- If #002 lands first, S1 just adds a scope-check block to the
  already-installed hook. Hard enforcement works immediately.
- If #003 lands first, S1 ships with prompt-only enforcement. The
  scope-check block in the hook is added later, when #002 lands.

The plans don't have a hard ordering requirement, but landing #002 first
gives #003 the cleaner story.

## Verification

| Scenario                                                                       | Expected outcome                                                                                              |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `pxx --edit --scope tests/` in pxx repo                                        | Banner shows `scope=tests/`; aider launches; system prompt mentions scope                                     |
| `pxx --edit --scope tests/ --scope docs/` ; aider asked to edit `pxx/cli.py`   | Model refuses (prompt-driven); if #002 installed, pre-commit also rejects on attempt                          |
| `pxx --edit --scope tests/` ; aider edits and commits `tests/test_cli.py`     | Allowed                                                                                                       |
| `pxx --edit --scope ../outside/` (outside repo)                                | Hard error at launch: scope must be within repo root                                                          |
| `pxx --edit --dry-run` ; aider produces a SEARCH/REPLACE block                | Block printed; no file modified; no commit                                                                    |
| `pxx --dry-run` (no `--edit`)                                                  | Warning: dry-run is redundant in ask mode; aider launches anyway                                              |
| `pxx --edit` in `/tmp/random-dir` with `trusted-paths` containing only `~/ai/` | Hard error: "not under any trusted path; pass --anywhere to override or add to <config-path>"                 |
| `pxx --edit --anywhere` in `/tmp/random-dir` (with trusted-paths configured)   | Allowed; banner shows `mode=edit (untrusted path)`                                                            |
| `pxx --edit` with no `trusted-paths` file                                      | Allowed (opt-in feature; absent config = all paths trusted)                                                   |
| `pxx --edit --scope tests/ --big` and commit 200 lines in `tests/`             | Allowed (scope and big both honored; scope still enforced)                                                    |
| `pxx --edit --scope tests/ --dry-run` ; aider proposes edits outside `tests/` | Model refuses (prompt); nothing applied either way                                                            |

## Open design notes (deferred)

- **Globs in `--scope`** — directory prefixes cover the common case. Add
  globs only when a real use case appears.
- **`--scope` interpreted via `.gitattributes` or aiderignore-style
  patterns** — same. Deferred.
- **Trusted-paths with negation** (`!~/dangerous/`) — deferred. Single
  positive list is enough for v1.
- **`--scope @<branch>` to scope to files modified vs a branch** — clever
  but speculative. Defer until a request surfaces.
- **Detection of out-of-scope read access** — v1 lets the model read any
  file (aider's repo-map sees the whole tree). Tightening this would
  require deeper aider integration. Defer.

## Status updates needed in `backlog.md` when this completes

- `#003` status: `planned` → `in-progress` → `done`
- `#001` "Blocked by": `002, 003` → `002` (when 003 lands)
- If `#002` also lands first: `#001` "Blocked by": `002, 003` → `003`
- When both 002 and 003 done: `#001` status: `blocked` → `proposed`
