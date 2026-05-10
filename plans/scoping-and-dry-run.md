# Scoping & Dry-Run

> Backlog ID: **003**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed** (stub). Blocks: 001. Blocked by: —.

## Problem

`pxx` today is binary: ask mode (read everything, edit nothing) or `--edit`
mode (edit anywhere aider thinks needs editing, bounded only by `.aiderignore`
if present). For real Python codebases with sensitive sections, that's too
coarse. We need:

- "Edit only under this path" — useful for the stepping-stone "test-only
  modifications" phase from the framework brainstorm, or for letting pxx
  refactor a module without risking siblings.
- True dry-run — show concrete diffs that *would* apply but don't apply or
  commit. Different from ask mode: ask is conversational ("here's what I'd
  change"); dry-run produces real diff blocks the user can `git apply`.
- An optional trusted-paths list — paths under which `pxx --edit` doesn't
  need extra confirmation. Anywhere else, `--edit` requires an extra
  acknowledgment flag.

## Capabilities to design

- **`--scope <path>` flag**: aider treats files outside the path as
  read-only (`--read` adds them as context) or refuses to add them entirely.
  Multiple `--scope` flags should combine (union).
- **`--dry-run` flag**: aider runs but every diff is written to
  `~/.pxx/dryruns/<ts>.patch` instead of being applied. Banner says
  `mode=dry-run`.
- **Trusted-paths config**: optional `~/.pxx/trusted-paths` (one prefix per
  line). When cwd is under a trusted prefix, `pxx --edit` proceeds normally.
  Outside trusted prefixes, `pxx --edit` requires `--anywhere` to confirm.

## Open questions

1. Does `--scope` use aider's `--read` mechanism for out-of-scope files
   (they remain as context, no edits) or skip them entirely (they're invisible
   to the model)? Probably `--read` — the model often needs to *see* a file
   it can't edit.
2. How does `--dry-run` interact with auto-commits? Dry-run obviously disables
   auto-commit; should the banner say so explicitly?
3. Trusted-paths config location — `~/.pxx/trusted-paths` matches the audit
   log plan (#004); XDG-friendly would be `~/.config/pxx/trusted-paths`. Pick
   one and be consistent.
4. Should `--scope` accept globs (e.g., `--scope 'src/**/*.py'`) or only
   directory prefixes? Globs are powerful but harder to reason about.
5. Should `--scope` and `--edit` interact differently — e.g., `--scope`
   without `--edit` means "ask mode but only about these files"?

## Non-goals

- A general-purpose "permissions" system. This is opinionated and minimal.
- Compatibility with aider's internal `--file` and `--read` semantics if
  they conflict — pxx's flags compose on top of aider's, not replace them.

## Verification

- `pxx --edit --scope tests/` should allow edits to `tests/*` and refuse
  to edit anything else, even if the model proposes it.
- `pxx --dry-run` should produce a `.patch` file matching what aider
  would otherwise commit, and the working tree should be unchanged.
- Running `pxx --edit` in `~/some-random-dir` (not under a trusted prefix)
  should prompt or require `--anywhere`.
