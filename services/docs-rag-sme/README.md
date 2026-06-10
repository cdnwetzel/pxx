# docs-rag-sme

A version-aware **docs-RAG SME** retrieval proxy that sits in front of the
local vLLM so Aider/`pxx` sessions can answer from *current, official* docs the
frozen model weights don't know. See `pxx/plans/docs-rag-sme.md` (Backlog 009)
for the full design.

## Runtime GOAL: local LLM only

At runtime this service talks **only to a local LLM**. It never reaches an
external API. The single cloud LLM in the loop is the one used to *build* these
tools — not to serve them. Any future component that needs the network goes
through the ingestion **allowlist** (docs.python.org, peps.python.org,
pypi.org), never through the request path.

## T0 — transparent forwarder (this milestone)

T0 proves the seam and nothing more: a FastAPI app that relays every request
**verbatim** to the downstream OpenAI-compatible server and streams the
response back unchanged. No retrieval yet.

```
aider ──► pxx (PXX_VLLM_URL) ──► docs-sme :8004 ──► T5810 audit-proxy :8003 ──► vLLM
                                  │
                                  └─ augment_chat_request()  ← retrieval lands here (T2+), no-op today
```

### Run it

```bash
cd ~/ai/pxx/services/docs-rag-sme
uv sync --extra dev

# Defaults: listen 127.0.0.1:8004, forward to http://127.0.0.1:8003
uv run docs-sme
# or: uv run uvicorn docs_rag_sme.app:app --port 8004
```

### Point pxx at it

```bash
# pxx already supports this override — no pxx code change needed for T0.
PXX_VLLM_URL=http://127.0.0.1:8004 pxx          # ask mode
PXX_VLLM_URL=http://127.0.0.1:8004 pxx --edit   # edit mode
```

Verify the chain end-to-end (with the SSH tunnel + audit-proxy up):

```bash
curl -s http://127.0.0.1:8004/healthz
curl -s http://127.0.0.1:8004/v1/models | head
```

### Config (env)

| Var | Default | Effect |
|---|---|---|
| `DOCS_SME_UPSTREAM` | `http://127.0.0.1:8003` | downstream OpenAI server (T5810 audit-proxy via tunnel) |
| `DOCS_SME_HOST`     | `127.0.0.1`            | bind host |
| `DOCS_SME_PORT`     | `8004`                 | bind port |
| `DOCS_SME_TIMEOUT`  | `600`                  | per-request upstream read timeout (s); `0`/`none` disables |

### Test

```bash
uv run pytest -q          # in-process fake upstream; no real vLLM needed
uv run ruff check
```

## Roadmap

T1 ingestion (allowlist crawler + pgvector) → T2 hybrid retrieval + rerank →
T3 version-aware filter → T4 systemd timer → T5 `pxx --with-docs` + model A/B.
Tracked in `pxx/plans/docs-rag-sme.md`.
