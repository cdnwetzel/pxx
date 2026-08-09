#!/usr/bin/env python3
"""Slack Socket Mode HITL broker (reference impl, R-036 pattern, Slack transport).

Same seam as the FastAPI broker (docs/examples/hitl/hitl_broker.py): the PreToolUse
hook POSTs {"summary","nonce"?} to /request-approval and polls the decision file. Here
the decision comes back over Slack instead of a signed URL:

  POST /request-approval {summary}
      -> post a Block Kit message (Approve / Abort buttons carrying the nonce) to the
         channel, BLOCK up to HITL_DEADLINE, return {"nonce","decision"}.
         Fail-closed: no click -> "timeout" (the pipeline treats non-approve as deny).

  Slack Socket Mode (inbound, app dials out -- no public endpoint)
      -> a block_actions click writes the decision file atomically (O_EXCL, single-use),
         acks Slack, and edits the original message to show who decided.

Env: PXX_SLACK_APP_TOKEN (xapp-), PXX_SLACK_BOT_TOKEN (xoxb-), PXX_SLACK_CHANNEL,
     HITL_DIR, HITL_PORT (default 8490), HITL_DEADLINE (default 300).
slack_sdk is imported lazily inside build()/main so the pure helpers below import (and
unit-test) without the dependency. Run: uv run --with slack_sdk --with fastapi --with uvicorn python3 slack_hitl_broker.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from secrets import token_hex

# ---- pure, dependency-free helpers (unit-tested without slack_sdk) ----

ACTION_DECISION = {"pxx_approve": "approve", "pxx_abort": "abort"}


def decision_for_action(action_id: str) -> str | None:
    """Map a Block Kit action_id to a decision, or None if not a decision button."""
    return ACTION_DECISION.get(action_id)


def write_decision(hitl_dir: Path, nonce: str, decision: str, who: str) -> bool:
    """Atomically record a single-use decision. Returns False if already decided."""
    if decision not in ("approve", "abort") or not nonce.isalnum():
        return False
    try:
        fd = os.open(hitl_dir / f"{nonce}.decision", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        json.dump({"nonce": nonce, "decision": decision, "by": who, "ts": time.time()}, f)
        f.flush()
        os.fsync(f.fileno())
    return True


def approval_blocks(nonce: str, summary: str) -> list:
    """Block Kit for an approval request. nonce rides in each button's value."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {
            "type": "actions",
            "block_id": f"pxx:{nonce}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "pxx_approve",
                    "value": nonce,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Abort"},
                    "style": "danger",
                    "action_id": "pxx_abort",
                    "value": nonce,
                },
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Deny-by-default after the deadline."}],
        },
    ]


def outcome_blocks(decision: str, user_id: str) -> list:
    label = {
        "approve": "✅ approved",
        "abort": "\U0001f6ab aborted",
        "timeout": "⏳ expired → denied",
    }.get(decision, decision)
    who = f" by <@{user_id}>" if user_id else ""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"pxx approval — *{label}*{who}"}}
    ]


# ---- runtime wiring (slack_sdk + fastapi imported here so the module imports clean) ----


def main() -> None:
    from fastapi import Body, FastAPI
    from fastapi.responses import PlainTextResponse
    import uvicorn
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    hitl_dir = Path(os.environ.get("HITL_DIR", "/tmp/pxx-hitl-slack"))
    hitl_dir.mkdir(parents=True, exist_ok=True)
    deadline = float(os.environ.get("HITL_DEADLINE", "300"))
    port = int(os.environ.get("HITL_PORT", "8490"))
    channel = os.environ["PXX_SLACK_CHANNEL"]
    web = WebClient(token=os.environ["PXX_SLACK_BOT_TOKEN"])
    sm = SocketModeClient(app_token=os.environ["PXX_SLACK_APP_TOKEN"], web_client=web)
    posted: dict[str, tuple[str, str]] = {}  # nonce -> (channel, ts)

    def on_socket(client: SocketModeClient, req: SocketModeRequest) -> None:
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type != "interactive" or req.payload.get("type") != "block_actions":
            return
        action = (req.payload.get("actions") or [{}])[0]
        decision = decision_for_action(action.get("action_id", ""))
        nonce = action.get("value", "")
        user = req.payload.get("user", {})
        who = user.get("username") or user.get("id", "?")
        if decision and write_decision(hitl_dir, nonce, decision, who):
            ch_ts = posted.get(nonce)
            if ch_ts:
                web.chat_update(
                    channel=ch_ts[0],
                    ts=ch_ts[1],
                    text=f"pxx approval {decision} by {who}",
                    blocks=outcome_blocks(decision, user.get("id", "")),
                )

    sm.socket_mode_request_listeners.append(on_socket)

    app = FastAPI()

    @app.get("/health")
    def health():
        return PlainTextResponse("ok")

    @app.post("/request-approval")
    def request_approval(body: dict = Body(default={})):
        nonce = token_hex(8)
        summary = body.get("summary", "(no summary)")
        resp = web.chat_postMessage(
            channel=channel, text="pxx approval request", blocks=approval_blocks(nonce, summary)
        )
        posted[nonce] = (resp["channel"], resp["ts"])
        spool = hitl_dir / f"{nonce}.decision"
        end = time.time() + deadline
        while time.time() < end:
            if spool.exists():
                try:
                    d = json.loads(spool.read_text()).get("decision")
                except Exception:
                    d = "unreadable"
                return {"nonce": nonce, "decision": d}
            time.sleep(0.5)
        web.chat_update(
            channel=posted[nonce][0],
            ts=posted[nonce][1],
            text="pxx approval expired (denied)",
            blocks=outcome_blocks("timeout", ""),
        )
        return {"nonce": nonce, "decision": "timeout"}  # fail-closed

    sm.connect()  # Socket Mode listener runs in a background thread
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
