# deploy/ — host service artifacts

OS-level service definitions that keep the inference fleet reachable. These
are machine-specific (absolute paths, host aliases) and live here so they are
reproducible and reviewable, the same way `inference-node` keeps its
`coder-tunnel.service` unit in source control.

## The fleet (as of 2026-07-16)

| Node | Role | Inference |
|---|---|---|
| **vllm-host-1** (GB10, office LAN) | **priority endpoint** — work-owned | vLLM `Qwen3-Coder` (30B-A3B-Instruct FP8, 32k ctx) on `:8001` |
| **Mac Studio** (`workstation`, M4 Max, 36GB) | runs `pxx` locally | Ollama `:11434` — `devstral:24b`, `qwen2.5:32b`, `qwen2.5-coder:7b` |
| **Mac Mini** (M4 16GB, home LAN) | OpenClaw → pxx autonomous bridge | none local; consumes vllm-host-1 + gpu-node-1 over tunnels (see below) |
| **gpu-node-1** (`gpu-node-1`, 2× RTX A4500 20GB NVLink) | remote vLLM + **public SSH rendezvous** (`<rendezvous-host>:22`) | vLLM `qwen2.5-coder-14b` (+`coder-prod` LoRA), TP-2, behind audit-proxy `:8003` |
| **inference-node** (RHEL 10) | separate inference node | vLLM `:8000` (legal LoRAs), Ollama `:11434` |

## The Qwen3-Coder reverse tunnel chain (2026-07-16)

vllm-host-1 is on the **office** LAN; the Mini is at **home**. Neither can reach the
other directly, and the office NATs no inbound ports. The gpu-node-1 resolves this
because it is publicly reachable on `:22` — so the office dials *out* to it and
the Mini reaches *in* from the same home LAN:

```
vllm-host-1 :8001 ──(ssh -R, dials out)──▶ gpu-node-1 loopback:8001 ──(ssh -L)──▶ Mini 127.0.0.1:8001
                                      gpu-node-1 loopback:8003 ──(ssh -L)──▶ Mini 127.0.0.1:8003
```

Round-trip Mini→office→Mini measured at **0.21s** — the detour is not the
bottleneck. Both Mini forwards ride one ssh connection
(`launchd/local.pxx.mini-vllm-tunnel.plist`); the vllm-host-1 side is
`systemd/qwen3-reverse-tunnel.service`.

**No inbound port is opened anywhere.** Every hop is SSH-key authenticated and
the vLLM is never bound beyond loopback — verified: `<gpu-node-1-lan-ip>:8001` refuses
while `127.0.0.1:8001` answers.

### Two things that will bite you

**`restrict` rejects the forward.** The gpu-node-1's `authorized_keys` entry must be:

```
restrict,port-forwarding,permitlisten="127.0.0.1:8001" ssh-ed25519 AAAA... vllm-host-1-qwen3-reverse-tunnel
```

`permitlisten` only *constrains* a forward that is already permitted — it does
not grant one. `restrict` alone disables port-forwarding and the tunnel dies
with `remote port forwarding failed for listen port 8001`. `port-forwarding`
must be re-enabled explicitly. The key can then do nothing else: no pty, no
exec, no agent forwarding.

**The systemd user unit needs lingering.** Without it the unit dies at logout
and never returns after a reboot:

```bash
sudo loginctl enable-linger <tunnel-user>      # on vllm-host-1
```

Recommended on the gpu-node-1 so a dropped tunnel's stale forward is reaped in ~90s
instead of the ~2h TCP-keepalive default (otherwise reconnects fail):

```bash
sudo sh -c 'printf "\nClientAliveInterval 30\nClientAliveCountMax 3\n" >> /etc/ssh/sshd_config'
```

### Why loopback is mandatory on the Mini, not merely tidy

macOS **Local Network privacy (TCC)** blocks pxx's Python from reaching LAN
addresses when it is launched from **launchd** — the context the WhatsApp
bridge actually runs in. It presents as an intermittent "No Ollama or vLLM
endpoint reachable" that never reproduces from a terminal, because an
interactive session already holds the grant. The grant is per-binary, and
`/usr/bin/ssh` is not subject to it — proven by a pre-existing
launchd ssh-tunnel agent, which holds an established connection
to a LAN host from launchd. So the tunnel performs the LAN hop and pxx only
ever touches localhost, which is exempt.

Verified from launchd context (not an ssh session):

```
LAUNCHD CONTEXT -> vllm_127_0_0_1 http://127.0.0.1:8001 openai/Qwen3-Coder
```

## `launchd/local.pxx.gpu-node-1-vllm-tunnel.plist`

Persistent SSH local-forward from the Studio to the gpu-node-1 audit-proxy:
`http://127.0.0.1:8003` on the Studio → gpu-node-1 vLLM. The audit-proxy has no
auth, so **the SSH tunnel is the security boundary** — same posture as the
RHEL `coder-tunnel.service` it mirrors.

### Install (Mac Studio)

```bash
# 1. Confirm the gpu-node-1 ssh alias works
ssh gpu-node-1 true

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

## `launchd/local.pxx.mini-vllm-tunnel.plist`

The Mac Mini variant of the above: same gpu-node-1 ssh alias, but **two** forwards
over one connection (`:8001` Qwen3-Coder via vllm-host-1, `:8003` the 14B fallback).
Pair it with:

```
PXX_VLLM_URL=http://127.0.0.1:8001,http://127.0.0.1:8003
PXX_VLLM_MODEL=openai/Qwen3-Coder,openai/qwen2.5-coder-14b-coder-lora
```

pxx probes in order, so Qwen3-Coder is primary and the 14B is automatic
fallback with no new code. `ExitOnForwardFailure` guards only the *local* bind:
if vllm-host-1's reverse tunnel is down, `:8001` still binds, the probe fails at
request time, and pxx falls through to `:8003` — the intended degradation.

`PXX_VLLM_URL` comma-splitting requires pxx ≥ the multi-endpoint support; older
builds treat the whole comma string as one malformed URL and report *no
endpoint reachable* while both ports answer HTTP 200. If detection fails but
`curl` succeeds, check the pxx version first.

### Reference: the RHEL systemd equivalent (already deployed)

`inference-node:/etc/systemd/system/coder-tunnel.service` does the
same forward (`ssh -N -L 8003:127.0.0.1:8003 gpu-node-1`, `Restart=always`). This
LaunchAgent is its macOS translation.
