# pxx

Personal Python coding agent. Thin wrapper over [aider](https://aider.chat). Fully offline-capable.

## Architecture

- **Heavy end:** Mac Studio `workstation` (M4 Max, 36GB, 410GB/s) runs Ollama. Default model: `devstral:24b` (alternates pulled: `qwen2.5:32b-instruct-q4_K_M`, `qwen3:14b`, `qwen3:8b`)
- **Thin end:** MacBook Neo (A18 Pro, 8GB) runs `pxx`, auto-detects where to send requests
- **Remote access:** your existing SSL VPN. Nothing exposed to the public internet.
- **Endpoint priority:** Studio LAN (office) → Studio over VPN (remote) → Neo localhost (offline, `qwen3:4b`)

## Setup

On the Studio (one-time):

```bash
bash scripts/setup-studio.sh
```

On the Neo (one-time):

```bash
bash scripts/setup-neo.sh
```

Add to `~/.zshrc` on the Neo:

```bash
export PXX_STUDIO_LAN_URL=http://workstation:11434
export PXX_STUDIO_REMOTE_URL=http://workstation:11434
```

## Modes — reviewer first, edit on opt-in

`pxx` defaults to a **read-only ask mode** so you can safely run it against any
codebase (real, unfamiliar, sensitive) without risking accidental edits. To
allow edits, pass `--edit`.

```bash
pxx                  # ask mode: read files, answer questions, NO file changes
pxx --edit           # code mode: standard aider — propose diffs, auto-commit
```

The startup banner prints the active mode so you always know which one you're in.

### Trusted paths (optional belt-and-suspenders)

If you populate `~/.config/pxx/trusted-paths` (one absolute or `~/`-prefixed
path per line; `#` comments OK), `pxx --edit` outside any listed prefix is
hard-blocked with a message naming the cwd, the config file, and the closest
matching prefix. Pass `--anywhere` to override for one session — the banner
then annotates `mode=edit (untrusted path)`. With no config file, all paths
are trusted (opt-in feature, no behavior change by default).

## Self-modes — portable health checks

Two flags run pxx's own test/lint gate against the pxx repo, regardless of cwd.
They short-circuit before endpoint detection, so they work offline and don't
need Ollama:

```bash
pxx --self-test      # uv run pytest -q against the pxx repo
pxx --self-lint      # uv run ruff check . AND uv run ruff format --check .
```

Both return Unix-style exit codes — non-zero if anything fails. `--self-lint`
runs both ruff sub-commands every time (no short-circuit on first failure) so
you see every violation in one pass.

A third self-mode opens an aider session pre-loaded with a "suggest only,
no edits" prompt for reviewing the pxx codebase for improvements:

```bash
pxx --self-improve                              # ask mode, banner=ask (self-improve)
pxx --self-improve --message "focus on cli.py"  # seed the review with a topic
```

`--self-improve` always targets the pxx repo (regardless of cwd) and refuses
to combine with `--edit` (the session is suggest-only by design — copy the
markdown list and apply it manually with a regular `pxx --edit` session).

A fourth self-mode opens a *bounded autonomous edit* session targeting one
module — pxx improving pxx, with safety gates from `#002` and scope
enforcement from `#003` doing the bounds-checking:

```bash
pxx --self-fix "fix typo in cli.py banner" --scope pxx/cli.py
```

`--self-fix` always requires `--scope` (refuses to run without it), tightens
the per-commit diff cap to 60 lines (raise once with `PXX_DIFF_CAP=N`, or
bypass with `--big`), and commits land with `[autonomous]` prepended to the
first line so they're filterable from manual commits:

```bash
git log --oneline --grep '^\[autonomous\]'        # show autonomous commits
git log --invert-grep --grep '^\[autonomous\]'    # hide them
```

**No-push convention:** `--self-fix` commits stay on your local branch.
`pxx` never invokes `git push` / `git deliver` on its own; that's always
an explicit human action.

## Daily use

```bash
# At the office, on the LAN, exploring a codebase:
cd ~/some-python-project
pxx                                      # ask mode (default, no edits possible)

# Same project, ready to make changes:
pxx --edit                               # code mode

# Remote: bring up the SSL VPN, then:
pxx                                      # auto-detects Studio over VPN (ask)
pxx --edit                               # ...or in edit mode

# Offline (no VPN, off-LAN):
pxx                                      # falls through to local qwen3:4b (ask)
```

`pxx` prints which endpoint, model, and **mode** it picked before launching aider,
so you always know which brain you're talking to and whether it can write.

## Slash commands

Inside an aider chat:

```
/load <path-to-pxx>/pxx/commands/refactor.md
```

| Command | Purpose |
|---|---|
| `refactor` | clarify, keep behavior identical |
| `test` | parametrized pytest tests |
| `typecheck` | tighten type hints |
| `docstring` | concise docstring (only when asked) |
| `audit` | read-only security/correctness review |
| `refocus` | beat context rot: digest the convo, then `/clear` |

## Context-rot discipline

Long sessions drift. Recommended cadence:

- Reset (`/clear`) every ~10 turns or when responses start ignoring stated constraints
- Run `/load .../commands/refocus.md` first to get a digest you can paste back
- Drop files (`/drop <path>`) the moment they're done — don't carry dead context
- Let aider's repo-map work; add files only when needed
- Default `--edit-format diff` keeps tool output compact

## Per-project conventions

Copy `config/conventions.md` into each project root as `CONVENTIONS.md`. Aider auto-reads it.

## Pre-flight check

```bash
bash scripts/doctor.sh
```

Reports: which endpoints are reachable, memory pressure, loaded models, CPU temp,
and **cross-machine sync status** (drift detection).

### Cross-machine drift detection

Because `pxx` runs on two machines (Neo and Studio), they can occasionally
drift out of sync if you forget to `git deliver` / `rsync`.

```bash
pxx --check-sync     # manual check: Neo HEAD vs Studio HEAD over SSH
```

Optional auto-check on every `--edit` session:
- Set `export PXX_AUTOCHECK_DRIFT=1` in `~/.zshrc` to opt-in.
- `pxx --edit --no-check-sync` bypasses the check for one session.

The check is informational: if drift is detected, `pxx` warns but continues
launching aider. A hard block would risk workflow traps on flaky networks.

## Developing pxx itself

```bash
uv sync --extra dev          # one-time per machine — creates .venv/
uv run pytest -q             # tests on the pure helper functions (or: pxx --self-test)
uv run ruff check --fix      # lint + auto-fix
uv run ruff format           # format
```

See `CLAUDE.md` for the full dev workflow and project conventions.

### Auto-restart hint when core modules change

Because pxx is installed `--editable`, edits to `pxx/cli.py` or
`pxx/endpoints.py` change the source on disk immediately, but the
already-running aider session keeps the old module objects in memory.
Two cooperating mechanisms surface that:

- **M1 (post-commit):** install the git hooks once with
  `pxx --install-hook`. After any commit that touches a "core" pxx
  module (see `pxx/_core_files.py`), the hook prints a one-line
  stderr notice reminding you to restart pxx — the current process is
  running the old code.
- **M2 (next-launch banner):** when you re-launch pxx from the pxx
  repo, it compares the previous session's HEAD (from the audit log)
  to the current HEAD. If a core file changed in that range, the
  banner reads `pxx: loaded freshly-edited cli.py (commit <sha>)`.

Together: warning at commit time, confirmation at restart time.

## Environment overrides

| Var | Purpose |
|---|---|
| `PXX_OLLAMA_BASE` | Force a specific endpoint URL, skip detection. Uses Studio default model unless `PXX_MODEL` is also set — set both when overriding to a small-memory host |
| `PXX_STUDIO_LAN_URL` | Override default `http://workstation:11434` |
| `PXX_STUDIO_REMOTE_URL` | Studio's VPN-reachable URL (work-internal DNS or IP) |
| `PXX_MODEL`            | Force a specific model regardless of endpoint |
| `PXX_AUTOCHECK_DRIFT`  | Set to `1` to run a drift check before every `--edit` session |

