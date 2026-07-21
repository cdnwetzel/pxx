# docs-rag-sme

> **Status: experimental, source-only.** This service is not part of the
> `pxx-orchestrator` PyPI package and is not a shipped feature of the current
> release; it is developed and tested in this repository.

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
aider ──► pxx (PXX_VLLM_URL) ──► docs-sme :8004 ──► gpu-node-1 audit-proxy :8003 ──► vLLM
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

## T1 — ingestion (crawl → chunk → embed → pgvector)

Infra setup is one self-logging command (Postgres 17 + pgvector + a local
embedding model; JSON-Lines log for review):

```bash
bash scripts/setup-t1b.sh        # no sudo on macOS; brew + ollama are user-level
```

Then ingest allowlisted pages:

```bash
# Dry-run: show the structural chunks (no DB/embed needed)
uv run docs-sme-ingest https://docs.python.org/3.12/library/asyncio-task.html

# Embed + store into Postgres/pgvector (delta-skips unchanged pages)
uv run docs-sme-ingest --store https://docs.python.org/3.12/library/asyncio-task.html
uv run docs-sme-ingest --store --force <url>     # re-ingest even if unchanged
```

Allowlist is enforced **in code** (`ingest/allowlist.py`): https-only,
`docs.python.org` / `peps.python.org` / `pypi.org` JSON API only — no config
can widen it. Embeddings come from local Ollama (`nomic-embed-text`, 768-dim),
so ingestion is the *only* network step and it never leaves the allowlist.

### Config (env)

| Var | Default | Effect |
|---|---|---|
| `DOCS_SME_UPSTREAM` | `http://127.0.0.1:8003` | downstream OpenAI server (gpu-node-1 audit-proxy via tunnel) |
| `DOCS_SME_HOST`     | `127.0.0.1`            | bind host |
| `DOCS_SME_PORT`     | `8004`                 | bind port |
| `DOCS_SME_TIMEOUT`  | `600`                  | per-request upstream read timeout (s); `0`/`none` disables |
| `DOCS_SME_DSN`      | `postgresql://localhost/docs_sme` | Postgres connection |
| `DOCS_SME_EMBED_MODEL` | `nomic-embed-text`  | Ollama embedding model (768-dim) |
| `DOCS_SME_OLLAMA_URL`  | `http://127.0.0.1:11434` | local Ollama base |
| `DOCS_SME_RETRIEVAL` | `on`                | `off` = plain forwarder (no augmentation) |
| `DOCS_SME_RERANK`    | `none`              | `bge` = cross-encoder rerank (needs rerank extra) |
| `DOCS_SME_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | cross-encoder model when `RERANK=bge` |

### Optional: cross-encoder reranker (T2b)

A bge cross-encoder reranks retrieved chunks for precision. Heavy (torch +
~2GB model), so it's opt-in:

```bash
bash scripts/setup-rerank.sh      # uv sync --extra rerank + download/smoke-test
DOCS_SME_RERANK=bge uv run docs-sme
```

### Test

```bash
uv run pytest -q          # unit suite + T1b integration (skips if PG/Ollama down)
uv run ruff check
```

## T4 — perpetual refresh

`docs-sme-refresh` crawls every source in `src/docs_rag_sme/sources.toml`
(curated stdlib pages + your PyPI deps + relevant PEPs) and ingests with
delta-skip — unchanged pages cost nothing (a full no-op refresh of ~50 sources
runs in ~1.5s).

```bash
uv run docs-sme-refresh           # one delta-refresh pass (JSONL to stdout)
uv run docs-sme-refresh --force   # re-ingest everything
# Edit the source list (or point elsewhere):
$EDITOR src/docs_rag_sme/sources.toml      # or DOCS_SME_SOURCES=/path/to.toml
```

Daily timer on the Studio (launchd):

```bash
cp deploy/launchd/local.docs-sme-refresh.plist ~/Library/LaunchAgents/
# fix the absolute paths inside, then:
launchctl load ~/Library/LaunchAgents/local.docs-sme-refresh.plist
```

## Roadmap

T0 forwarder ✅ → T1 ingestion ✅ → T2 hybrid retrieval + inject ✅ →
T2b bge reranker ✅ → T3 version-aware filter ✅ → `pxx --with-docs` ✅ →
T4 refresh timer ✅. Remaining: the model A/B (§6 of the plan).
Tracked in `pxx/plans/docs-rag-sme.md`.
