# Migrating from pxx 1.x to 2.0

pxx 2.0 is a rewrite. Most 1.x habits carry over; the architecture underneath
changed completely.

## Command mapping

| 1.x | 2.0 |
|---|---|
| `pxx` (bare) | `pxx ask` (bare still works) |
| `pxx --edit -m "..."` | `pxx edit -m "..."` (legacy flag rewritten) |
| `pxx --with-memory` | memory is in-process and **on by default** (`--no-memory` to disable) |
| `pxx --with-router` | gone — fallback chains live in config (`[[fallback_models]]`) |
| `pxx --with-docs` | gone — use an MCP server (`[[mcp_servers]]`) |
| `pxx --loop "task" --scope X` | not rewritten — run `pxx loop -m "task" --scope X` instead (works in any repo now) |
| `pxx --self-test` / `--self-lint` | rewritten to `pxx run -m "<suite/lint task>"` (deprecation warning); direct equivalent: `uv run pytest` / `uv run ruff check` |
| `pxx --doctor` | `pxx doctor` |
| `pxx --upgrade` | not rewritten — run `pxx upgrade` instead |
| unknown aider flags | forwarded only when the aider backend is active (deprecation warning) |

Only `--edit`, `--with-memory`, `--doctor`, `--self-test`, `--self-lint` are
rewritten by the compat shim; other legacy flags are ignored with a warning.

## Python API / importable modules

2.0 reorganized the package internals. Downstream Python tools that *imported*
1.x modules break at **import time** — before any CLI flag or argument matters.
The internal module layout is **not** a public API; depend on the stable CLI.

| 1.x import | 2.0 |
|---|---|
| `from pxx.scope import is_path_trusted, load_trusted_paths` | gone — scope/trust live in `pxx.safety` (`ScopeGate`, `canonicalize`). To gate against `~/.config/pxx/trusted-paths`, read the file directly (skip comment/blank lines; a path is trusted if it equals or is under a listed root). |
| `from pxx.audit import log_dir` | gone — run outcomes/verdicts are exposed via the CLI (`pxx runs list`, `pxx verify`) and the hash-chained log under `~/.local/state/pxx/audit/`. |

**Guidance for integrations** (an assistant/NL front-end or dispatcher that
shells out to pxx): drive pxx through its **CLI**, not its internals — run
`pxx loop -m "..." --scope ... --review`, then read the terminal code
(`[COMPLETED]`, `[OUT_OF_SCOPE]`, `[BUDGET_EXCEEDED]`, ...) from the run's own
output, or query `pxx runs list`. Don't `import pxx.*` internals; they move
between releases. (A 1.x-era NL dispatcher broke exactly this way — dead on 2.x
until it was decoupled from `pxx.scope`/`pxx.audit` and pointed at the CLI.)

## Environment variables

| 1.x | 2.0 |
|---|---|
| `PXX_OLLAMA_BASE` | still works; prefer `PXX_BASE_URL` or `base_url` in TOML |
| `PXX_OLLAMA_MODEL` / `PXX_MODEL` | still works; prefer `model` in TOML |
| `AGENTMEMORY_*` | gone — memory is in-process (`memory_dir`, TTL via store API) |
| `PXX_ROUTER_PORT` | gone — no router service |
| `~/.config/pxx/env` | still loaded (`setdefault`; real env wins) |

## Data

- `~/.pxx/memory.db` (1.x schema): detected on first 2.0 run and moved to
  `memory.db.v1-backup` (WAL sidecars included). 2.0 starts a fresh schema.
- Audit logs move from `~/.local/state/pxx/sessions/` to
  `~/.local/state/pxx/audit/` and are now hash-chained (`pxx audit verify`).

## Behavioral differences to expect

- The 1.x **safety net is back**: edit-capable sessions (`edit`/`run`/`loop`/`goal`)
  stash uncommitted work and tag HEAD (`pxx-pre/<ts>`) before writing.
  Restore with `git reset --hard <tag>` + `git stash pop`; disable with
  `safety_net = false` in config.
- `pxx ask/edit` uses the **aider backend when the `aider` binary is present**,
  else the native backend. Force one with `--backend native|aider`.
- The native backend enforces scope/permission in-process; aider mode keeps
  aider's own UX but no longer receives 1.x's config files — port custom
  aider settings to your own `~/.aider.conf.yml`.
- `--loop` is no longer pxx-repo-only; it needs `test_command` configured
  (TOML or `PXX_TEST_COMMAND`) to do useful work.
- Services (`agentmemory`, `9router`, `docs-rag-sme`) are gone. Their jobs are
  in-process (memory, routing) or replaced by MCP (docs/tools).
