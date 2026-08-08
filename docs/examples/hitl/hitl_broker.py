#!/usr/bin/env python3
"""HITL approval + PR broker on FastAPI/uvicorn (robust; same stack as pxx serve).

- POST /request-approval {summary}  -> mint signed nonce, record pending, BLOCK up
  to HITL_DEADLINE, return {"decision": approve|abort|timeout} (sync endpoint runs
  in the threadpool, so it doesn't stall /decision).
- GET|POST /decision?req=&decision=&sig=  -> signed (HMAC over nonce:decision),
  single-use (O_EXCL) callback the Slack button / ntfy action / demo curl hits.
- POST /open-pr {title,body}  -> server-side git commit + push + gh pr create in the
  sandbox (n8n's executeCommand node is disabled by default), return {pr_url}.
"""
from __future__ import annotations

import hmac
import json
import os
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from secrets import token_hex

import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

SECRET = os.environ["HITL_SECRET"].encode()
HITL_DIR = Path(os.environ.get("HITL_DIR", "/tmp/pxx-hitl"))
HITL_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("HITL_PORT", "8480"))
DEADLINE = float(os.environ.get("HITL_DEADLINE", "60"))
BASE = os.environ.get("HITL_LISTENER", f"http://127.0.0.1:{PORT}")
SANDBOX = os.environ.get("SANDBOX_DIR", "")
SANDBOX_REPO = os.environ.get("SANDBOX_REPO", "")

app = FastAPI()


def _hmac(nonce: str, decision: str) -> str:
    return hmac.new(SECRET, f"{nonce}:{decision}".encode(), sha256).hexdigest()


@app.get("/health")
def health():
    return PlainTextResponse("ok")


@app.post("/request-approval")
def request_approval(body: dict = Body(default={})):
    nonce = token_hex(8)
    approve = f"{BASE}/decision?req={nonce}&decision=approve&sig={_hmac(nonce, 'approve')}"
    abort = f"{BASE}/decision?req={nonce}&decision=abort&sig={_hmac(nonce, 'abort')}"
    with open(HITL_DIR / "pending.jsonl", "a") as f:
        f.write(json.dumps({"nonce": nonce, "summary": body.get("summary", ""),
                            "approve_url": approve, "abort_url": abort}) + "\n")
    spool = HITL_DIR / f"{nonce}.decision"
    end = time.time() + DEADLINE
    while time.time() < end:
        if spool.exists():
            try:
                d = json.loads(spool.read_text()).get("decision")
            except Exception:
                d = "unreadable"
            return {"nonce": nonce, "decision": d}
        time.sleep(0.5)
    return {"nonce": nonce, "decision": "timeout"}  # fail-closed


def _decide(req: str, decision: str, sig: str):
    if decision not in ("approve", "abort") or not req.isalnum():
        return PlainTextResponse("bad request", status_code=400)
    if not hmac.compare_digest(sig, _hmac(req, decision)):
        return PlainTextResponse("bad signature", status_code=403)
    try:
        fd = os.open(HITL_DIR / f"{req}.decision", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return PlainTextResponse("already decided", status_code=409)
    with os.fdopen(fd, "w") as f:
        json.dump({"req": req, "decision": decision, "ts": time.time()}, f)
    return PlainTextResponse(f"recorded: {decision}")


@app.get("/decision")
def decision_get(req: str = "", decision: str = "", sig: str = ""):
    return _decide(req, decision, sig)


@app.post("/decision")
def decision_post(req: str = "", decision: str = "", sig: str = ""):
    return _decide(req, decision, sig)


@app.post("/open-pr")
def open_pr(body: dict = Body(default={})):
    title = body.get("title", "pxx: change")
    prbody = body.get("body", "opened by the n8n governed pipeline")
    br = "pxx/demo-" + token_hex(4)
    try:
        def run(*c):
            return subprocess.run(c, cwd=SANDBOX, check=True, capture_output=True,
                                  text=True, timeout=90)
        run("git", "checkout", "-q", "-b", br)
        run("git", "-c", "user.email=demo@x", "-c", "user.name=pxx demo", "commit", "-qam", title)
        run("git", "push", "-q", "-u", "origin", br)
        pr = run("gh", "pr", "create", "--repo", SANDBOX_REPO, "--head", br, "--base", "main",
                 "--title", title, "--body", prbody)
        return {"branch": br, "pr_url": pr.stdout.strip()}
    except subprocess.CalledProcessError as e:
        return JSONResponse({"error": (e.stderr or str(e))[:400], "branch": br}, status_code=500)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
