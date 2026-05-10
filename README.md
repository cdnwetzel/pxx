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

## Daily use

```bash
# At the office, on the LAN:
cd ~/some-python-project
pxx                                      # auto-detects Studio via mDNS

# Remote: bring up the SSL VPN, then:
pxx                                      # auto-detects Studio over VPN

# Offline (no VPN, off-LAN):
pxx                                      # falls through to local qwen3:4b
```

`pxx` prints which endpoint and model it picked before launching aider, so you always know which brain you're talking to.

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

Reports: which endpoints are reachable, memory pressure, loaded models, CPU temp.

## Environment overrides

| Var | Purpose |
|---|---|
| `PXX_OLLAMA_BASE` | Force a specific endpoint URL, skip detection. Uses Studio default model unless `PXX_MODEL` is also set — set both when overriding to a small-memory host |
| `PXX_STUDIO_LAN_URL` | Override default `http://mac-studio.local:11434` |
| `PXX_STUDIO_REMOTE_URL` | Studio's VPN-reachable URL (work-internal DNS or IP) |
| `PXX_MODEL` | Force a specific model regardless of endpoint |
