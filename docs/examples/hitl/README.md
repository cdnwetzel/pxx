# pxx human-in-the-loop (HITL) approvals

Reference implementations for pausing an action on a human decision, fail-closed. These
are examples, not core pxx code: you run the pieces yourself and wire them from trusted
config. The security contract they follow is receipted in
[docs/RECEIPTS.md](../../RECEIPTS.md): R-036 (the gate), R-044 and R-045 (Slack),
R-035 / R-037 / R-038 (n8n orchestration).

## The one invariant

An action-bearing approval is a bearer capability, so every design here is **fail-closed**:
no answer, an abort, an unreadable decision, or a failed receipt write all resolve to
**deny**. A decision is single-use (consumed atomically with `O_EXCL`), and a signed
decision cannot be replayed as its opposite (HMAC is over `nonce:decision`, not the nonce
alone). Approvals only ride an authenticated, non-public transport.

## Two ways to use it

There are two independent stacks here. Pick by what you are gating. These are two
*triggers*, not two Slack channels or two roles: both can post to the same
`#pxx-approvals` channel. To keep sources distinguishable in one channel, `POST
/request-approval` takes an optional **`origin`** label (for example `"pxx run"`,
`"n8n: governed-PR"`, or `"openclaw"`) that renders as a distinct top line on the card, so
you always see where an approval came from without needing a channel per source. Use
separate channels only if you also want them physically split, which is a preference, not
a requirement.

### A. Supervise your own `pxx run` (the PreToolUse gate)

Pause a gated tool call inside a running `pxx` session until you approve it.

- **`hitl_gate.py`** is a **PreToolUse hook**. pxx calls it with `{"tool","args"}` on
  stdin; exit 0 allows, non-zero denies. It mints a nonce, routes an approval request out
  (best-effort), then **blocks** on the decision spool up to `HITL_DEADLINE`, fail-closed.
- **`hitl_listener.py`** is the loopback endpoint the human's tap lands on. It validates
  the signed `approve`/`abort` link and writes the single-use decision the gate is waiting
  on. Delivery to the human is via a signed link (an ntfy action, a Slack link button, or
  an n8n decision node).

Wire the gate from **trusted config only** (`~/.config/pxx/config.toml`, never repo-local):

```toml
[[hooks]]
event = "PreToolUse"
command = "python3 /path/to/docs/examples/hitl/hitl_gate.py"
matcher = "edit_file"   # substring-scope to the tool(s) you want gated
timeout = 120           # seconds; must exceed HITL_DEADLINE
```

Env for the gate and listener (both read these):

```bash
export HITL_SECRET="$(openssl rand -hex 32)"   # shared by gate + listener
export HITL_DIR=/tmp/pxx-hitl                   # decision spool
export HITL_LISTENER=http://127.0.0.1:8479
export HITL_DEADLINE=120                         # deny after this
export HITL_NOTIFY=""                            # optional: webhook to deliver the ask
```

Run the listener: `python3 hitl_listener.py` (binds 127.0.0.1:8479). This stack is R-036.

### B. Approvals inside an n8n pipeline (Slack buttons + modal)

When n8n orchestrates a governed `pxx serve` run and you want a human to approve the next
step from Slack, use the Slack Socket Mode broker.

- **`slack_hitl_broker.py`** posts a Block Kit card (Approve / Abort / **Modify**) to a
  private Slack channel, receives the decision over **Socket Mode** (the app dials out, so
  there is no inbound public endpoint), writes a single-use decision, and edits the card to
  show the outcome. Modify opens a modal for a revised scope and a note, handed back as a
  structured `modify` decision. This is R-044 (approve/abort) and R-045 (modify).
- **`hitl_broker.py`** is the earlier FastAPI variant used in the n8n governed-PR pipeline:
  signed approve/abort URLs plus a server-side `/open-pr`. This is R-038.
- The n8n workflows that drive these: `n8n-hitl-approval-workflow.json`,
  `n8n-governed-pr-pipeline.json`.

n8n calls `POST /request-approval {summary}` and receives `{nonce, decision}` when the
human responds (or `timeout` on the deadline).

## Transport: Slack primary, ntfy sovereign fallback

The roadmap decision (2026-08-09) is that richer feedback is preferred, so **Slack via
Socket Mode is the primary transport**: it gives buttons and a modal, and Socket Mode
means no inbound endpoint to expose. **Self-hosted `ntfy` remains the sovereign default**
for air-gapped or regulated setups where nothing may leave your infrastructure; it is
button-only (approve/abort) but keeps everything on your own box. Public `ntfy.sh` is only
acceptable for notify-only alerts, never for an action-bearing approval.

## Slack setup (for the Socket Mode broker)

The workspace does not need the next-gen Slack platform; this is a classic Socket Mode app.

1. api.slack.com/apps → **Create New App → From a manifest** → your workspace → paste:

   ```yaml
   display_information:
     name: pxx-hitl
     description: Human-in-the-loop approvals for pxx governed runs
   features:
     bot_user:
       display_name: pxx
       always_online: true
   oauth_config:
     scopes:
       bot:
         - chat:write
   settings:
     interactivity:
       is_enabled: true
     socket_mode_enabled: true
   ```

2. **Basic Information → App-Level Tokens**: the manifest generates one with
   `connections:write`. Copy the `xapp-...` value.
3. **Install App → Install to Workspace**: copy the Bot User OAuth Token (`xoxb-...`).
4. Create a private channel (for example `#pxx-approvals`), `/invite @pxx`, and copy the
   channel ID.
5. Put the three values in trusted env (never in a repo), `chmod 600`:

   ```bash
   export PXX_SLACK_APP_TOKEN="xapp-..."   # Socket Mode
   export PXX_SLACK_BOT_TOKEN="xoxb-..."   # chat:write
   export PXX_SLACK_CHANNEL="C0123456789"  # not secret
   export HITL_DEADLINE=300
   ```

Run the broker (loopback only):

```bash
uv run --with slack_sdk --with fastapi --with uvicorn python3 slack_hitl_broker.py
```

Verify without a click: `curl -s -XPOST http://127.0.0.1:8490/request-approval -d '{"summary":"test"}'`
posts a card to the channel and blocks until you tap a button (or the deadline denies).

## What is proven, and the next bridge

- Proven live: the pxx gate (R-036), the Slack approve/abort round-trip (R-044), the Slack
  Modify modal (R-045), and the n8n orchestration patterns (R-035, R-037, R-038).
- Not yet built: a direct bridge so `pxx run`'s PreToolUse gate drives the Slack Socket
  Mode **buttons and modal** (rather than a signed link). Today the gate uses the
  signed-link path (stack A) and the Socket Mode buttons serve n8n (stack B). Unifying
  them means the gate and the Slack broker sharing one nonce, so a paused `pxx run` shows
  the richer Slack card and resumes on a tap. That is the natural next increment.

## Security notes (all stacks)

Hooks and their commands are honored only from trusted config, never repo-local. Run every
listener and broker on loopback. The approval message describes what the agent is about to
do, so treat the channel or topic as sensitive and keep it private and authenticated. The
host still enforces any scope a human sets; a proposed scope is a proposal into pxx's gate
(R-014), not a way around it.
