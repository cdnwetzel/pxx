#!/usr/bin/env python3
"""Slack Socket Mode HITL broker (reference impl, R-036 pattern, Slack transport).

Same seam as the FastAPI broker (docs/examples/hitl/hitl_broker.py): the PreToolUse
hook POSTs {"summary","nonce"?} to /request-approval and polls the decision file. Here
the decision comes back over Slack instead of a signed URL:

  POST /request-approval {summary}
      -> post a Block Kit message (Approve / Abort / Modify buttons carrying the nonce)
         to the channel, BLOCK up to HITL_DEADLINE, return {"nonce","decision"}.
         Fail-closed: no response -> "timeout" (the pipeline treats non-approve as deny).

  Slack Socket Mode (inbound, app dials out -- no public endpoint)
      -> Approve/Abort: write the decision file atomically (O_EXCL, single-use), ack,
         edit the message to show who decided.
      -> Modify: open a modal (revised scope + note); on submit, write a "modify"
         decision carrying those fields so the caller can re-run with a tighter scope.

Env: PXX_SLACK_APP_TOKEN (xapp-), PXX_SLACK_BOT_TOKEN (xoxb-), PXX_SLACK_CHANNEL,
     HITL_DIR, HITL_PORT (default 8490), HITL_DEADLINE (default 300).
slack_sdk is imported lazily inside main() so the pure helpers below import (and
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
MODIFY_ACTION = "pxx_modify"
MODIFY_CALLBACK = "pxx_modify_submit"


def decision_for_action(action_id: str) -> str | None:
    """Map a Block Kit action_id to a terminal decision, or None (e.g. modify opens a modal)."""
    return ACTION_DECISION.get(action_id)


def write_decision(
    hitl_dir: Path, nonce: str, decision: str, who: str, extra: dict | None = None
) -> bool:
    """Atomically record a single-use decision. Returns False if already decided.

    `extra` carries structured fields for a "modify" decision (e.g. scope, note).
    """
    if decision not in ("approve", "abort", "modify") or not nonce.isalnum():
        return False
    try:
        fd = os.open(hitl_dir / f"{nonce}.decision", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    record = {"nonce": nonce, "decision": decision, "by": who, "ts": time.time()}
    if extra:
        record.update(extra)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
        f.flush()
        os.fsync(f.fileno())
    return True


def approval_blocks(nonce: str, summary: str, origin: str = "") -> list:
    """Block Kit for an approval request. nonce rides in each button's value.

    `origin` is a free-text source label (e.g. "pxx run", "n8n: governed-PR",
    "openclaw") rendered as a distinct top line, so one channel can carry approvals
    from many sources and you always see where a card came from.
    """

    def button(text, action_id, style=None):
        b = {
            "type": "button",
            "text": {"type": "plain_text", "text": text},
            "action_id": action_id,
            "value": nonce,
        }
        if style:
            b["style"] = style
        return b

    blocks = []
    if origin:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"📨 from *{origin}*"}]}
        )
    blocks += [
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {
            "type": "actions",
            "block_id": f"pxx:{nonce}",
            "elements": [
                button("Approve", "pxx_approve", "primary"),
                button("Abort", "pxx_abort", "danger"),
                button("Modify…", MODIFY_ACTION),
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Deny-by-default after the deadline."}],
        },
    ]
    return blocks


def modify_modal(nonce: str) -> dict:
    """Modal view for 'approve but re-scope'. nonce rides in private_metadata."""
    return {
        "type": "modal",
        "callback_id": MODIFY_CALLBACK,
        "private_metadata": nonce,
        "title": {"type": "plain_text", "text": "Modify request"},
        "submit": {"type": "plain_text", "text": "Send back"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "scope",
                "optional": True,
                "label": {"type": "plain_text", "text": "Revised scope (files)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "scope_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. mathlib.py"},
                },
            },
            {
                "type": "input",
                "block_id": "note",
                "optional": True,
                "label": {"type": "plain_text", "text": "Note to the agent"},
                "element": {"type": "plain_text_input", "action_id": "note_input", "multiline": True},
            },
        ],
    }


def read_modify_submission(view: dict) -> dict:
    """Pull {nonce, scope, note} out of a view_submission payload's view."""
    values = (view or {}).get("state", {}).get("values", {})

    def field(block_id, action_id):
        return (values.get(block_id, {}).get(action_id, {}) or {}).get("value") or ""

    return {
        "nonce": (view or {}).get("private_metadata", ""),
        "scope": field("scope", "scope_input"),
        "note": field("note", "note_input"),
    }


def outcome_blocks(decision: str, user_id: str, detail: str = "") -> list:
    label = {
        "approve": "✅ approved",
        "abort": "\U0001f6ab aborted",
        "modify": "✏️ modified",
        "timeout": "⏳ expired → denied",
    }.get(decision, decision)
    who = f" by <@{user_id}>" if user_id else ""
    extra = f"\n{detail}" if detail else ""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"pxx approval — *{label}*{who}{extra}"}}
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

    def finalize(nonce: str, decision: str, user: dict, detail: str = "") -> None:
        ch_ts = posted.get(nonce)
        if ch_ts:
            web.chat_update(
                channel=ch_ts[0],
                ts=ch_ts[1],
                text=f"pxx approval {decision}",
                blocks=outcome_blocks(decision, user.get("id", ""), detail),
            )

    def on_socket(client: SocketModeClient, req: SocketModeRequest) -> None:
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type != "interactive":
            return
        payload = req.payload
        ptype = payload.get("type")
        user = payload.get("user", {})
        who = user.get("username") or user.get("id", "?")

        if ptype == "block_actions":
            action = (payload.get("actions") or [{}])[0]
            action_id = action.get("action_id", "")
            nonce = action.get("value", "")
            if action_id == MODIFY_ACTION:
                # open the modal; the decision is written on submit, not here
                web.views_open(trigger_id=payload["trigger_id"], view=modify_modal(nonce))
                return
            decision = decision_for_action(action_id)
            if decision and write_decision(hitl_dir, nonce, decision, who):
                finalize(nonce, decision, user)

        elif ptype == "view_submission":
            if payload.get("view", {}).get("callback_id") == MODIFY_CALLBACK:
                sub = read_modify_submission(payload["view"])
                nonce = sub.pop("nonce", "")
                if nonce and write_decision(hitl_dir, nonce, "modify", who, extra=sub):
                    detail = " | ".join(f"{k}: {v}" for k, v in sub.items() if v)
                    finalize(nonce, "modify", user, detail)

    sm.socket_mode_request_listeners.append(on_socket)

    app = FastAPI()

    @app.get("/health")
    def health():
        return PlainTextResponse("ok")

    @app.post("/request-approval")
    def request_approval(body: dict = Body(default={})):
        nonce = token_hex(8)
        summary = body.get("summary", "(no summary)")
        origin = body.get("origin", "")  # free-text source label, shown on the card
        resp = web.chat_postMessage(
            channel=channel,
            text="pxx approval request",
            blocks=approval_blocks(nonce, summary, origin),
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
