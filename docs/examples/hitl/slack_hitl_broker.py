#!/usr/bin/env python3
"""Slack Socket Mode HITL broker (reference impl, R-036 pattern, Slack transport).

Same seam as the FastAPI broker (docs/examples/hitl/hitl_broker.py): the caller POSTs
{"summary","nonce"?} and the decision comes back over Slack instead of a signed URL.

Two endpoints, because the two callers need opposite things:

  POST /post-approval {nonce, summary, origin?}     <- the pxx PreToolUse gate (P4)
      -> post the card carrying THE CALLER'S nonce and return IMMEDIATELY.
         The gate runs its own fail-closed wait on {nonce}.decision; the broker must
         not also hold the connection. This is the gate<->Slack bridge.

  POST /request-approval {summary, nonce?}          <- n8n pipelines (R-044/045)
      -> post the card, BLOCK up to HITL_DEADLINE, return {"nonce","decision"}.
         Fail-closed: no response -> "timeout" (the pipeline treats non-approve as deny).

HITL_DIR MUST MATCH the caller's. The gate waits on ITS OWN {nonce}.decision path, so a
broker writing to a different directory can never release it: every gated call would run
to its deadline and deny. That is fail-closed but permanently shut, and silent — so the
resolved directory is printed at startup. The gate's default is /tmp/pxx-hitl.

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

#: Bounds on a caller-supplied nonce. The gate mints `token_hex(8)` = 16 chars; allow a
#: little room either side without allowing anything long enough to be interesting.
NONCE_MIN, NONCE_MAX = 8, 64


def decision_for_action(action_id: str) -> str | None:
    """Map a Block Kit action_id to a terminal decision, or None (e.g. modify opens a modal)."""
    return ACTION_DECISION.get(action_id)


def resolve_nonce(body: dict, mint) -> tuple[str | None, str | None]:
    """Decide which nonce a card is posted under. Returns (nonce, error).

    This is the bridge in one function, so keep it at module level where it can be tested
    without slack_sdk:

    - caller supplied a valid nonce -> USE IT. The pxx gate mints the nonce and blocks on
      `{nonce}.decision`; a card posted under any other name writes a file the gate never
      looks at, so it would wait out its deadline and deny. Every gated call, always.
    - caller supplied nothing       -> mint one (the n8n path, R-044/045, unchanged).
    - caller supplied garbage       -> REJECT. Never mint a replacement: the caller is
      waiting on the value it sent, so a substitute could not release it either, and
      answering 200 would hide the misconfiguration behind a deny that looks like a
      human choosing "no".
    """
    supplied = (body or {}).get("nonce")
    if supplied is None:
        return mint(), None
    nonce = sanitize_nonce(supplied)
    if nonce is None:
        return None, "invalid nonce"
    return nonce, None


def sanitize_nonce(value: object) -> str | None:
    """Validate a CALLER-SUPPLIED nonce, or return None to reject it.

    Security-critical. Before the gate bridge the broker minted every nonce itself, so
    it was trusted by construction; now a caller sends one and it reaches the filesystem
    as ``hitl_dir / f"{nonce}.decision"``. An unvalidated value there is a path-traversal
    primitive — ``../../etc/cron.d/x`` would let a caller choose where the broker writes.
    ASCII-alphanumeric only (which excludes ``/``, ``.``, NUL, and every separator) plus a
    length bound. Rejects rather than sanitizes: a nonce that needed cleaning is a caller
    bug or an attack, and silently rewriting it would break the gate's poll path anyway
    (it waits on the nonce it sent, so a rewritten one could never be released).
    """
    if not isinstance(value, str):
        return None
    if not (NONCE_MIN <= len(value) <= NONCE_MAX):
        return None
    # `str.isalnum()` is True for non-ASCII digits/letters (e.g. "١٢٣", "ⅷ"); restrict to
    # ASCII so the value is exactly what it looks like on the wire and in a filename.
    if not value.isascii() or not value.isalnum():
        return None
    return value


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
    from fastapi import Body, FastAPI, HTTPException
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

    def post_card(body: dict):
        """Post the approval card and register it. Returns (nonce, error).

        The nonce is the CALLER'S when it sends a valid one — that is the whole bridge:
        the pxx PreToolUse gate mints the nonce, blocks on `{nonce}.decision`, and can
        only be released by a decision written under the name it is waiting on. A broker
        that minted its own (as this did before) would post a card whose buttons write a
        file the gate never looks at, so every gated call would hang to its deadline and
        deny. Fail-closed, but permanently — the gate could never be approved at all.
        """
        nonce, err = resolve_nonce(body, lambda: token_hex(8))
        if err:
            return None, err
        resp = web.chat_postMessage(
            channel=channel,
            text="pxx approval request",
            blocks=approval_blocks(
                nonce,
                body.get("summary", "(no summary)"),
                body.get("origin", ""),  # free-text source label, shown on the card
            ),
        )
        posted[nonce] = (resp["channel"], resp["ts"])
        return nonce, None

    @app.post("/post-approval")
    def post_approval(body: dict = Body(default={})):
        """NON-BLOCKING post — the pxx gate bridge (roadmap P4).

        Posts the card and returns immediately. The caller does its OWN fail-closed wait
        on `{nonce}.decision`, so the broker must not hold the connection: the gate POSTs
        with an 8s timeout and deliberately ignores the result, which means a blocking
        endpoint here would be abandoned mid-request every time and the card's fate would
        depend on whether uvicorn noticed the hangup. Returning at once keeps exactly one
        component responsible for the deadline — the gate.
        """
        nonce, err = post_card(body)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return {"nonce": nonce, "posted": True}

    @app.post("/request-approval")
    def request_approval(body: dict = Body(default={})):
        """BLOCKING post — the original n8n path (R-044/045), unchanged in behaviour."""
        nonce, err = post_card(body)
        if err:
            raise HTTPException(status_code=400, detail=err)
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

    # A HITL_DIR that does not match the caller's is a permanent, silent deny (see the
    # module docstring), so state it plainly rather than leaving it to be discovered.
    print(f"pxx slack hitl broker: HITL_DIR={hitl_dir}  (must match the caller's)")
    print(f"  gate bridge (non-blocking): POST http://127.0.0.1:{port}/post-approval")
    print(f"  n8n (blocking, {deadline:.0f}s):     POST http://127.0.0.1:{port}/request-approval")
    sm.connect()  # Socket Mode listener runs in a background thread
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
