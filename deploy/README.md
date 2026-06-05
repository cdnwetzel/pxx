# deploy/ — host service artifacts

OS-level service definitions that keep the inference fleet reachable. These
are machine-specific (absolute paths, host aliases) and live here so they are
reproducible and reviewable, the same way `inference-node` keeps its
`psagent-coder-tunnel.service` unit in source control.

## The fleet (as of 2026-06-05)

| Node | Role | Inference |
|---|---|---|
| **Mac Studio** (`workstation`, M4 Max, 36GB) | orchestrator — runs `pxx` locally | Ollama `:11434` — `devstral:24b`, `qwen2.5:32b`, `qwen2.5-coder:7b` |
| **T5810** (`gpu-node-1`, 2× RTX A4500 20GB NVLink) | remote vLLM, **SSH-only** (office NAT forwards only port 22) | vLLM `qwen2.5-coder-14b-coder-lora` (+`coder-lora-prod` LoRA), TP-2, behind audit-proxy `:8003` |
| **inference-node** (RHEL 10) | separate inference node | vLLM `:8000` (legal LoRAs), Ollama `:11434` |

The Asrock RTX 3060Ti is not part of the fleet and pxx never referenced it.

## `launchd/local.pxx.gpu-node-1-vllm-tunnel.plist`

Persistent SSH local-forward from the Studio to the T5810 audit-proxy:
`http://127.0.0.1:8003` on the Studio → T5810 vLLM. The audit-proxy has no
auth, so **the SSH tunnel is the security boundary** — same posture as the
RHEL `psagent-coder-tunnel.service` it mirrors.

### Install (Mac Studio)

```bash
# 1. Confirm the T5810 ssh alias works
ssh T5810 true

# 2. Install + load the agent
cp deploy/launchd/local.pxx.gpu-node-1-vllm-tunnel.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/local.pxx.gpu-node-1-vllm-tunnel.plist

# 3. Verify the A4500 vLLM answers through the tunnel
curl -s http://127.0.0.1:8003/v1/models | python3 -m json.tool
```

pxx then picks it up automatically: `DEFAULT_VLLM = http://127.0.0.1:8003`
(`pxx/endpoints.py`), overridable with `PXX_VLLM_URL`. Tier-2/3 sessions
route to the A4500s; tier-1 stays on local Ollama (`qwen2.5-coder:7b`).

To stop: `launchctl unload ~/Library/LaunchAgents/local.pxx.gpu-node-1-vllm-tunnel.plist`.
Logs: `~/Library/Logs/pxx-gpu-node-1-tunnel.log`.

### Reference: the RHEL systemd equivalent (already deployed)

`inference-node:/etc/systemd/system/psagent-coder-tunnel.service` does the
same forward (`ssh -N -L 8003:127.0.0.1:8003 T5810`, `Restart=always`). This
LaunchAgent is its macOS translation.
