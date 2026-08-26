#!/usr/bin/env python3
"""HITL decision listener — the endpoint an ntfy action / Slack button / n8n
decision node calls when the human taps Approve or Abort.

Fail-closed hardening (matches the pxx roadmap HITL item):
- HMAC over the DECISION, not the nonce alone: sig = HMAC(secret, f"{req}:{decision}").
  An `approve` signature can never be replayed as `abort`.
- Single-use nonce consumed ATOMICALLY (O_CREAT|O_EXCL): a double-tap / concurrent
  submit can't both win, and a decided request can't be re-decided.
- Bad/expired/mismatched signature -> 403, no record written.

Env: HITL_SECRET (required), HITL_DIR (decision spool), HITL_PORT (default 8479).
Binds 127.0.0.1 only.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SECRET = os.environ["HITL_SECRET"].encode()
HITL_DIR = Path(os.environ.get("HITL_DIR", "/tmp/pxx-hitl"))
HITL_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("HITL_PORT", "8479"))


def _expected(req: str, decision: str) -> str:
    return hmac.new(SECRET, f"{req}:{decision}".encode(), sha256).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, msg: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _decide(self, q: dict) -> None:
        req = (q.get("req") or [""])[0]
        decision = (q.get("decision") or [""])[0]
        sig = (q.get("sig") or [""])[0]
        if decision not in ("approve", "abort") or not req.isalnum():
            self._reply(400, "bad request")
            return
        if not hmac.compare_digest(sig, _expected(req, decision)):
            self._reply(403, "bad signature")  # forged / replayed-as-other-decision
            return
        spool = HITL_DIR / f"{req}.decision"
        try:  # atomic single-use: O_EXCL fails if the request was already decided
            fd = os.open(spool, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self._reply(409, "already decided")
            return
        with os.fdopen(fd, "w") as f:
            json.dump({"req": req, "decision": decision, "ts": time.time()}, f)
        self._reply(200, f"recorded: {decision}")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/pending":
            # Capture the outbound approval request. In production this is n8n's
            # job (route it to Slack/ntfy); here it also lets the demo read the
            # signed action URLs to simulate the human tap.
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode() if n else "{}"
            with open(HITL_DIR / "pending.jsonl", "a") as f:
                f.write(body.strip() + "\n")
            self._reply(200, "pending recorded")
            return
        self._decide(parse_qs(urlparse(self.path).query))

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._reply(200, "ok")
            return
        self._decide(parse_qs(urlparse(self.path).query))  # allow tap via GET too

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
