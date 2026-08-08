#!/usr/bin/env python3
"""pxx PreToolUse HITL gate hook — pause a gated tool call, route an approval
request out (n8n -> Slack/ntfy), block on the human's decision, FAIL CLOSED.

pxx calls this with {"tool","args"} on stdin; exit 0 = allow, non-zero = deny.
Configure it from a TRUSTED source (user config / env, never repo-local — A0b),
scoped with a `matcher` to the tool(s) you want gated (e.g. "edit_file").

Fail-closed contract (roadmap HITL item):
- server-minted nonce; the approve/abort action URLs carry sig = HMAC(secret,
  f"{nonce}:{decision}") so an approve link can't be replayed as abort;
- BLOCK on the listener's single-use decision spool up to HITL_DEADLINE seconds;
- no answer / abort / unreadable / receipt-write-failure  ->  DENY (exit 2);
- a STRICT receipt (append + fsync) must persist BEFORE an allow is released.

Env: HITL_SECRET, HITL_DIR, HITL_LISTENER (e.g. http://127.0.0.1:8479),
     HITL_NOTIFY (n8n webhook URL; best-effort), HITL_DEADLINE (default 60).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
import urllib.request
from hashlib import sha256
from pathlib import Path
from secrets import token_hex

SECRET = os.environ["HITL_SECRET"].encode()
HITL_DIR = Path(os.environ.get("HITL_DIR", "/tmp/pxx-hitl"))
HITL_DIR.mkdir(parents=True, exist_ok=True)
LISTENER = os.environ.get("HITL_LISTENER", "http://127.0.0.1:8479")
NOTIFY = os.environ.get("HITL_NOTIFY", "")  # n8n webhook; blank = skip routing
DEADLINE = float(os.environ.get("HITL_DEADLINE", "60"))

DENY, ALLOW = 2, 0


def _sig(nonce: str, decision: str) -> str:
    return hmac.new(SECRET, f"{nonce}:{decision}".encode(), sha256).hexdigest()


def _receipt(rec: dict) -> bool:
    """Strict: the decision must durably persist before an allow is released.
    Returns False if it could not be written (-> caller denies, fail-closed)."""
    try:
        path = HITL_DIR / "decisions.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return DENY  # can't read the request -> deny
    tool = str(payload.get("tool") or "tool")
    args = payload.get("args") or {}
    summary = f"{tool}: " + (str(args.get("path") or args.get("command") or args))[:120]

    nonce = token_hex(8)
    approve_url = f"{LISTENER}/decision?req={nonce}&decision=approve&sig={_sig(nonce, 'approve')}"
    abort_url = f"{LISTENER}/decision?req={nonce}&decision=abort&sig={_sig(nonce, 'abort')}"
    args_hash = sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]

    # Route the approval request OUT (n8n -> Slack/ntfy). Best-effort: a routing
    # failure must not fail OPEN — we still block on the decision spool below, so
    # no decision -> deny. (The operator just won't get the ping; safe default.)
    if NOTIFY:
        try:
            body = json.dumps(
                {
                    "nonce": nonce,
                    "tool": tool,
                    "summary": summary,
                    "args_hash": args_hash,
                    "approve_url": approve_url,
                    "abort_url": abort_url,
                }
            ).encode()
            req = urllib.request.Request(
                NOTIFY, data=body, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=8).read()
        except Exception:
            pass  # deliberately fail-closed via the wait below, not fail-open

    # BLOCK on the human's decision (single-use spool the listener writes).
    spool = HITL_DIR / f"{nonce}.decision"
    deadline = time.time() + DEADLINE
    while time.time() < deadline:
        if spool.exists():
            try:
                decision = json.loads(spool.read_text()).get("decision")
            except Exception:
                decision = None
            rec = {
                "nonce": nonce,
                "tool": tool,
                "args_hash": args_hash,
                "decision": decision,
                "ts": time.time(),
            }
            if decision == "approve" and _receipt(rec):
                return ALLOW  # receipt persisted THEN allow
            _receipt({**rec, "decision": decision or "unreadable", "result": "deny"})
            return DENY  # abort / unreadable / receipt-failed -> deny
        time.sleep(1)

    _receipt(
        {"nonce": nonce, "tool": tool, "args_hash": args_hash, "decision": "timeout", "result": "deny"}
    )
    return DENY  # no answer within the deadline -> fail closed


if __name__ == "__main__":
    sys.exit(main())
