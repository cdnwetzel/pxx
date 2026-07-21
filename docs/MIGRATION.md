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
