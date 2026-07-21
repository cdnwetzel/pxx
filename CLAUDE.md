# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`pxx` is an orchestrator for a personal, offline-capable [aider](https://aider.chat) 
workflow. It probes which Ollama endpoint is reachable, picks a matching 
model, applies safety and scoping gates, and `os.execv`s into aider. Once 
aider takes over the process, pxx is out of the picture.

Fleet (as of 2026-07-15 — vllm-host-1 is the priority inference node after a
head-to-head A/B benchmark; `pxx` runs on the Mac Studio and on the office
MacBook):

- **vllm-host-1** (office LAN, resolve by hostname) — **priority endpoint.** vLLM serving
  `Qwen3-Coder` (Qwen3-Coder-30B-A3B-Instruct, FP8, 32k ctx) directly on the
  office LAN at `:8001`. Won the 2026-07-15 A/B 4/4 on quality with ~0.1s
  TTFT at ~50 tok/s (vs devstral:24b and qwen3:30b-a3b on the Studio).
- **Mac Studio** (`workstation`, M4 Max, 36GB) — runs `pxx` *and* Ollama
  locally (`devstral:24b` default, plus qwen2.5/qwen3 variants). Ollama is a
  system LaunchDaemon (`ai.ollama.serve`, KeepAlive, models on
  `/Volumes/Docking/ollama/models`) **bound to `127.0.0.1` by design** — see
  `~/ai/ollama-migration-runbook.md` on the Studio. It is NOT reachable from
  other machines; off-box use requires an SSH tunnel. Fallback tier only.
- **gpu-node-1** (`gpu-node-1`, 2× RTX A4500 20GB, NVLink) — remote vLLM
  serving `qwen2.5-coder-14b` (+`coder-prod` LoRA) behind an
  audit-proxy on `:8003`. **SSH-only**: the office router NATs only port 22
  to it, so it is reached through a persistent SSH local-forward
  (`deploy/launchd/local.pxx.gpu-node-1-vllm-tunnel.plist` → `127.0.0.1:8003`).
  The old direct-LAN URL is dead — that subnet is gone.
  Fallback tier only.
- **inference-node** (RHEL 10) — a separate inference node (vLLM with legal
  LoRAs on `:8000`, Ollama on `:11434`). Not used by pxx today; it already
  runs the systemd twin of the Studio tunnel (`coder-tunnel.service`).

**Hostname note:** on the office LAN use bare DNS names (`vllm-host-1`,
`workstation` via your office search domain) — mDNS `.local`
names do not resolve reliably.

Endpoint detection (first reachable wins, 1s timeout per probe):
`PXX_OLLAMA_BASE` override → vLLM candidates in `PXX_VLLM_URL` order
(vllm-host-1 first, then gpu-node-1 tunnel) → Ollama (`PXX_STUDIO_LAN_URL`, default
localhost). Tier 1 forces local Ollama; tier 2/3 prefer the first reachable
vLLM. `PXX_VLLM_URL`/`PXX_VLLM_MODEL` accept comma-separated lists, paired
positionally.

**Security posture:** nothing on the fleet has request-level auth; the
network boundary is the auth layer. The Studio's Ollama binds `127.0.0.1`
only (deliberate — see the migration runbook; SSH is the off-box path).
vllm-host-1's vLLM `:8001` is open on the trusted office LAN. The gpu-node-1
audit-proxy binds `0.0.0.0:8003` but only port 22 is NATed to it, so the
SSH tunnel *is* the boundary.

### Phase 5 Infrastructure (Tier 1-4)

Phase 5 adds three optional services for enhanced orchestration:

> Note: these services now run **on the Studio** (which absorbed Neo's
> orchestrator role — see the fleet note above). pxx starts them itself via
> supervisor mode when the opt-in flag is passed; there is no `scripts/setup-*.sh`.

**1. 9router (Tier 1 — request routing)**
- Binds to `http://127.0.0.1:20128` on the Studio (localhost)
- Routes aider requests to primary (Studio Ollama) with optional fallback chains
- Opt-in: `pxx --with-router` — `NineRouterManager` launches it (`pxx/router.py`); source lives in `services/9router/`

**2. agentmemory (Tier 2+ — session memory)**
- Binds to `http://127.0.0.1:3111` on the Studio (localhost)
- Captures observations from aider tool calls; enables `/recall`, `/remember`, `/forget` slash commands
- Opt-in: `pxx --with-memory` — `AgentmemoryManager` launches it (`pxx/memory.py`); source lives in `services/agentmemory/`
- Requires: Python 3.10+

**3. Slash commands (Tier 2+ — memory interaction)**
- `/recall <query>` — search saved observations from previous sessions
- `/remember "title" "details"` — manually save an observation
- `/forget <id>` — delete an observation
- Activated automatically when `--with-memory` is used and agentmemory is running
- See `docs/PHASE5_TUNING.md` for tuning and examples

**Default behavior:** all three services are optional. pxx works without them using local Ollama only.

## Commands

### Using pxx

```bash
# Install (editable) — uses uv tool
uv tool install --editable . --python 3.12

# Pre-flight: 9router + agentmemory health and git-mirror sync.
# Exits non-zero when a configured mirror is out of sync.
pxx --doctor

# Run pxx in another project directory
cd ~/some-python-project && pxx

# Self-modes (#001 Tier 1+2+3): pxx improving pxx
pxx --self-test                      # uv run pytest -q against the pxx repo
pxx --self-lint                      # ruff check + ruff format --check against the pxx repo
pxx --self-improve                   # ask-mode aider session w/ suggest-only prompt
pxx --self-fix "<task>" --scope X    # bounded autonomous edit; commits tagged [autonomous]

# Trusted paths (#003 S3): if ~/.config/pxx/trusted-paths is populated,
# pxx --edit outside any listed prefix is hard-blocked. Override one-shot:
pxx --edit --anywhere   # bypass the gate for this session; banner annotates "untrusted path"
```

### Aider upgrade discipline

`pyproject.toml` pins `aider-chat==<exact-version>` deliberately. Aider
releases roughly weekly and can change behavior pxx depends on (chat
modes, `--read` semantics, `--model-settings-file` shape, edit format
defaults, exit codes). **Never bump on auto-pilot.**

When upgrading aider:

1. Read aider's CHANGELOG / release notes for the new version (and any
   versions skipped since the current pin).
2. Spot-check pxx's specific touch points against the new aider:
   `--chat-mode`, `--read`, `--config`, `--model-settings-file`, edit
   format, exit semantics, and the `os.execv` boundary.
3. Bump `aider-chat==<new>` in `pyproject.toml`.
4. `uv sync --extra dev` to refresh `.venv/`.
5. `uv run pytest -q` for regression.
6. `pxx --list-commands` and a real session smoke test.
7. Commit `chore(aider): bump to <new>` with a one-line summary of what
   changed in aider and which pxx touch points were verified.

Even patch releases get this treatment. The discipline is the point.

### Developing pxx

Dev deps (`pytest`, `ruff`) live in `pyproject.toml` under
`[project.optional-dependencies] dev`. Use a project-local venv managed by uv —
**not** `--with` flags on the tool install (those bypass standard packaging).

```bash
# One-time per machine: create .venv/ from pyproject.toml + uv.lock
uv sync --extra dev

# Lint
uv run ruff check --fix
uv run ruff format

# Test
uv run pytest -q
```

Tests cover the pure helper functions in `pxx/cli.py` and `pxx/endpoints.py`
(`model_for`, `_in_git_repo`, `_find_aider`, `_probe`, `detect_endpoint`).
They do **not** exercise aider or Ollama — those are integration concerns.

After editing `cli.py` or `endpoints.py`, the running pxx/aider session still
has old code in memory — the user must exit and re-launch to test interactively.
Tests will catch regressions on the pure functions automatically.

Two paired mechanisms surface this staleness (#008):

- **Post-commit hook (M1):** the installer at `scripts/install-precommit-hook.sh`
  drops a `post-commit` hook that emits a stderr notice when the just-landed
  commit touched any "core" pxx module. The core-file list lives in
  `pxx/_core_files.py` and is shared (via a `python3 -c` invocation) by the
  bash hook so it can never drift from the Python side.
- **Launch banner (M2):** `cli._emit_core_restart_banner()` runs right after
  `_self_sanity_check()` in `main()`. It reads the most recent
  `session_start` record from #004's audit log, diffs the previous HEAD
  against the current HEAD, and prints
  `pxx: loaded freshly-edited <name> (commit <sha>)` when a core file
  appears in the range. Silent outside the pxx repo and on every error.

## Architecture

`pxx` is composed of a core orchestrator that dispatches to focused sibling
modules:

- **`pxx/cli.py`**: The entry point. Handles top-level orchestration, aider 
  argument assembly, and the final process handoff.
- **`pxx/endpoints.py`**: Probes and selects Ollama instances.
- **`pxx/safety.py`**: Manages pre-session sanity checks and local git 
  safety tags (#002).
- **`pxx/scope.py`**: Handles path-prefix scoping and trusted-path 
  gates (#003).
- **`pxx/self_modes.py`**: Implements dogfooding tiers (test, lint, improve, 
  fix) (#001).
- **`pxx/audit.py`**: Records session metadata for post-mortems and 
  distillation (#004).
- **`pxx/_git.py`**: Internal git CLI wrappers shared across modules.
- **`pxx/_core_files.py`**: Registry for auto-restart notifications (#008).
- **`pxx/drift.py`**: Probes remote (Studio) HEAD over SSH to detect sync 
  drift between machines (#006).

**`pxx/cli.py`** dispatches as follows:
1. Calls `detect_endpoint()` from `pxx/endpoints.py`
2. Sets `OLLAMA_API_BASE` from the chosen endpoint
3. Picks model via `model_for(endpoint)`.
4. Executes safety and sanity checks from `pxx/safety.py`.
5. Resolves and applies scope from `pxx/scope.py`.
6. Handles dogfooding sub-commands via `pxx/self_modes.py`.
7. Records session start in `pxx/audit.py`.
8. Locates the aider binary and `os.execv`s into it.

## Modes — reviewer first

pxx defaults to **ask mode** (read-only). The user must pass `--edit` to allow
file changes. This makes it safe to run pxx against any codebase without risking
accidental edits. The startup banner prints the active mode.

When working on pxx itself: the same rule applies — type `pxx --edit` to make
changes. No special-casing for the pxx repo.

**Headless runs:** when stdin is not a TTY and no consent flag was passed,
pxx appends `--yes` for aider (with a stderr notice) — aider's interactive
confirms crash on a non-TTY stdin, and headless callers (`--loop`, cron,
one-shot `--message` runs) always want auto-confirm. Note the implication
for *edit* mode: a headless `--edit` session auto-approves aider's actions;
that is acceptable because headless edits already sit behind the #002
safety tag and `--scope`/trusted-path gates. Pass `--no` explicitly to get
decline-all semantics instead.

## Plans inventory (`plans/backlog.md`)

The `plans/` folder is governed by `plans/backlog.md` — a master inventory
where each plan has a stable numeric ID and explicit Blocks / Blocked by
columns.

**Before proposing a new plan in this repo:**
1. Read `plans/backlog.md` to make sure no existing plan covers the idea.
2. If a similar plan exists, expand it rather than creating a duplicate.
3. If a new plan is genuinely warranted, follow the "Workflow for adding a
   new plan" section in backlog.md: pick the next free ID, create
   `plans/<slug>.md`, add the `> Backlog ID: NNN` header line at the top,
   add a row, and bump the next-free-ID line.

This keeps the planning surface coherent as it grows.

### Status hygiene — non-negotiable

The backlog's status column must reflect current reality. **Update it in the
same commit as the work that motivates the change.** Never batch status
updates into a separate "housekeeping" commit; never let the backlog show
`planned` for a plan with in-flight commits.

Transitions:

- **Starting work** on a plan: `planned` → `in-progress` in the same commit
  as the first concrete code/doc change.
- **Multi-step plan**: status stays `in-progress` across all commits until
  the last verification step lands. Do not bounce back to `planned`.
- **Completing**: `in-progress` → `done` in the same commit that lands the
  last verification step.
- **Cascade unblock**: when a plan reaches `done`, scan for any plan whose
  "Blocked by" column lists this ID. Remove the now-`done` ID from that
  column. If the column becomes empty, transition `blocked` → `planned`
  (or `in-progress` if implementation starts in the same commit).
- **Newly discovered blocker**: if mid-implementation reveals a missing
  prerequisite, transition `in-progress` → `blocked` and add the new ID
  to "Blocked by". Surface this in the commit message.
- **`Next free ID`**: bump whenever a new plan is added; never let it lag.

The motivating rule: *"a backlog whose statuses lag behind the work is
worse than no backlog: it deceives."*

**Three configs feed aider, and they do different things:**
- `config/aider.conf.yml` — aider behavior (edit-format, caching, lint/test cmds, privacy)
- `config/model-settings.yml` — per-model context windows; values here are OOM-sensitive on the Studio
- `pxx/prompts/system.md` — system prompt always loaded as a read-only file into every session

`config/conventions.md` is a *template* meant to be copied into other projects
as `CONVENTIONS.md`; pxx itself does not read it. The repo-root `CONVENTIONS.md`
is the meta-rules for editing pxx with pxx.

**Slash commands** in `pxx/commands/*.md` are prompt fragments loaded inside an
aider session via `/load <path>`. They are not Python code. Editing them changes
agent behavior, not pxx behavior.

## Code review observations (`../review/`)

Above this repo, at `../review/` (i.e. `/Users/you/ai/code_pro/review/`),
three different AI CLIs — **Claude Code, Gemini, and Codex** — periodically
produce parallel-perspective code reviews of pxx. Each reviewer tends to excel
at slightly different roles, so the multi-reviewer setup is intentional.

**Layout (since 2026-05-10):** agent-namespaced. Each agent's files live at
`../review/<agent>/<agent>-*.md` — both the folder and the filename prefix
must match the agent name. `../review/inventory.md` is the authoritative
rule statement; `../review/README.md` is the landing index.

**Claude Code's writable surface is `../review/claude/` only.** This agent
may create and refresh files under `../review/claude/` whose names match
`claude-*.md`. Everything else under `../review/` (Codex's folder, Gemini's
folder, the two shared root files except for Claude's own section in
`inventory.md`) is **read-only** for Claude. Do not edit files outside the
Claude namespace, even to fix obvious typos in another agent's work — surface
the observation in `claude-followups.md` instead.

When the user cites a finding ("flagged in codex-04-observations.md..."):
1. Locate the file in `../review/` and read the cited item in full.
2. If the finding maps to a pxx change, address it by editing the relevant
   code/docs *in the pxx repo*.
3. If the finding maps to a Claude-perspective response, capture it in
   `../review/claude/claude-followups.md` (or refresh the relevant
   `claude-*.md` file).
4. Leave other agents' findings alone unless the user routes them — they may
   be intentional, queued, or under review by that agent.

The reviewers may also be **stale** — the codebase may have moved on since the
last review pass. Verify a cited finding against current code before acting.

**Stay open-minded but not credulous.** Some findings will be misguided
(reviewers don't know the user's intent or project history). Use them as
planning inputs, not commands.

**Reviewer runtime rules — do NOT run pxx in edit mode during review.**
`pxx --edit` / `pxx --self-fix` trigger #002's safety tag, which stashes
the user's uncommitted working tree. Multiple agents reviewing
concurrently can wipe each other's (and the user's) in-flight work.
Safe invocations during review: `pxx` (ask), `pxx --self-test`,
`pxx --self-lint`. Forbidden: `pxx --edit`, `pxx --self-fix`,
`pxx --edit --dry-run`. Full rules + rationale + queries against the
audit log live in [`../review/claude/claude-pxx-runtime-rules.md`](../review/claude/claude-pxx-runtime-rules.md).
The same rules apply to Gemini and Codex — the user should mirror this
paragraph into `GEMINI.md` and Codex's equivalent.

**Proactively notice the same classes of drift the review docs catch:**
- README claims ↔ code defaults (model names, version pins, command examples)
- Setup scripts ↔ what they actually install
- Comments ↔ behavior
- Env-var docs ↔ env-var reads

Surface those in replies. Do not fix silently.

## Hard guardrails (enforced by `.aiderignore`)

These files must NOT be modified without explicit user request — wrong values
break installs, OOM the Studio, or alter agent behavior subtly:

- `config/model-settings.yml` (Ollama context windows)
- `config/aider.conf.yml`, `.aider.conf.yml`
- `pyproject.toml`
- `.aiderignore`, `CONVENTIONS.md`

If a task seems to require editing one of these, stop and ask. The
`CONVENTIONS.md` rule is: refuse and ask the user to do it by hand.

## Style (from `CONVENTIONS.md` and `pxx/prompts/system.md`)

- Python 3.11+, modern syntax (`match`, `|` unions, `Self`)
- Type hints on every public signature; no `Any` without reason
- stdlib first; new third-party deps need explicit justification
- `ruff format` is run after edits — don't fight its style
- Docstrings on public functions when the WHY is non-trivial; no docstrings for simple internal helpers
- No comments unless the *why* is non-obvious
- No try/except for control flow; no defensive code for impossible inputs
- Prefer `dataclass`/`TypedDict` over dict-of-anything; `pathlib.Path` over `os.path`
- Shell scripts: `#!/usr/bin/env bash`, `set -euo pipefail`

## Environment variables that affect behavior

| Var | Effect |
|---|---|
| `PXX_OLLAMA_BASE`      | Skip detection entirely, use this URL |
| `PXX_STUDIO_LAN_URL`   | Override the primary Ollama URL (default `http://localhost:11434`) |
| `PXX_STUDIO_REMOTE_URL`| Second Ollama URL, e.g. VPN-reachable (empty by default) |
| `PXX_VLLM_URL`         | Optional vLLM endpoint(s), comma-separated, probed in order (default `http://127.0.0.1:8003`) |
| `PXX_MODEL`            | Force a specific model regardless of endpoint |
| `PXX_OLLAMA_MODEL` / `PXX_VLLM_MODEL` | Per-backend default models; `PXX_VLLM_MODEL` may be a comma list paired positionally with `PXX_VLLM_URL` |
| `PXX_DRIFT_SSH_TARGET` / `PXX_DRIFT_REMOTE_PATH` | Enable the cross-machine drift check |
| `PXX_AUTOCHECK_DRIFT`  | Set to `1` to run a drift check before every `--edit` session |
| `PXX_REVIEW_BACKEND`   | `--loop`/`--review` reviewer: `local` (default, sovereign — reviews the diff via a local model) or `claude` (frontier agent, for supervised runs) |
| `PXX_REVIEW_URL` / `PXX_REVIEW_MODEL` | Local-review endpoint (OpenAI-compatible, default `http://127.0.0.1:11434`) and model (default `qwen2.5:7b-instruct`) |
| `PXX_REVIEW_TIMEOUT`   | Ceiling (seconds) for a standalone review pass (default `900`) |
| `PXX_DEBUG`            | Set to `1` to log each failed endpoint probe to stderr during detection |

Any of these can also live in `~/.config/pxx/env` (KEY=VALUE lines, loaded at
import by `pxx/__init__.py`; real env vars win). That's where this machine's
fleet-specific values (e.g. `PXX_VLLM_MODEL` for the gpu-node-1's served model id)
belong — never in the repo.
