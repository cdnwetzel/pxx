# Docs-RAG SME: version-aware "perpetual SME" retrieval proxy
> Backlog ID: 006

> **Status:** `done` (2026-07-17) — §6 model A/B run and recorded (the last
> open item): SME retrieval lifts the fallback 14b +1 to 100%, net-neutral for
> the priority Qwen3-Coder. No prod swap indicated. Earlier:
> T0–T4 + T2b + `pxx --with-docs` all landed and
> validated live. Through the SME, qwen2.5-coder corrected its own incomplete
> `asyncio.TaskGroup.create_task` signature (added the `context=` param it
> didn't know) from the real 3.12 docs. Store holds 1715 chunks across stdlib +
> deps + PEPs. **Only the model A/B (§6) remains.** Code lives at
> `services/docs-rag-sme/`.
>
> **Depends on:** nothing in-repo. Sits *beside* the existing gpu-node-1 vLLM +
> audit-proxy stack; pxx changes are limited to one endpoint override and
> one model-settings entry.
>
> **Origin:** a 2026-06-10 design conversation about giving local Aider
> sessions access to *current* official Python/library docs that no local
> model knows from its frozen weights. This plan captures that architecture
> grounded in the actual pxx fleet.

---

## Problem

Every local model's knowledge is frozen at its training cutoff. The gpu-node-1
serves a coding model (today `qwen2.5-coder-14b`; candidates under A/B
are Qwen3-Coder-Next and Gemma 4 26B A4B — see §6) that cannot know yesterday's
`asyncio` change or a library's just-released API. Aider's manual `/web <url>`
works but is per-call and unstructured.

**Goal:** a *perpetual SME* — a retrieval layer in front of vLLM that, for any
request, injects the most relevant chunks of **current, official** docs, with
**version-aware** filtering (a project pinned to Python 3.12 should answer from
3.12 docs, not blindly "latest"). Aider points at it unchanged.

**Non-goals:** crawling the open web; replacing Aider's repo-map; competing
with the agentmemory subsystem (Phase 8.x). This is *external reference docs*,
not *session memory* — orthogonal systems.

---

## Where it sits in the pxx fleet

Today's request path (from `pxx/endpoints.py`):

```
aider ──► SSH tunnel (127.0.0.1:8003) ──► gpu-node-1 audit-proxy :8003 ──► vLLM
```

The SME is another OpenAI-compatible shim. The open design decision is
**ordering vs. the audit-proxy** (§5, Decision A). Target path:

```
aider ──► SSH tunnel ──► SME proxy ──► audit-proxy ──► vLLM
                          │
                          ├─ hybrid retrieval (pgvector + BM25)
                          ├─ reranker
                          └─ version filter (from request hint / repo pin)
```

**pxx integration is deliberately thin:**
- `PXX_VLLM_URL` already overrides the vLLM endpoint (`endpoints.py:102`,
  `DEFAULT_VLLM = "http://127.0.0.1:8003"`). Point it at the SME proxy's port
  and pxx needs *no code change* to route through it.
- One opt-in flag to make it discoverable/explicit, mirroring `--with-router`
  / `--with-memory`: **`pxx --with-docs`** sets the endpoint override + a
  banner line. (Optional; env override alone is enough to start.)
- A model-settings entry only changes if the backing model changes (§6).

This keeps the SME a separable service that pxx *consumes*, not *owns* —
consistent with how 9router and agentmemory are treated.

---

## Architecture (4 components)

### 1. Ingestion — the "perpetual" part
- Python crawler on a **systemd timer** (the gpu-node-1/RHEL node already runs
  systemd tunnels — natural home), daily or weekly.
- **Allowlist only:** `docs.python.org` (versioned), `peps.python.org`,
  CPython release notes/changelog, and `pypi.org/pypi/<pkg>/json` for the
  libraries actually used. No open-web crawl.
- **Delta-only refresh:** store a content hash per page; reprocess only
  changed pages. Most doc pages never change → cheap refresh runs.
- **Metadata per chunk:** source URL, python_version, package, package_version,
  last-modified. This metadata is the whole point (see §4).

### 2. Processing + storage
- Parse with `trafilatura` or `docling`. Chunk on **structural boundaries** —
  never split a function signature from its description.
- Embed locally: `bge-m3` or `nomic-embed` (~1–2 GB, coexists with the LLM).
- **Store: Postgres + pgvector.** Vector search *and* plain SQL filtering
  (`WHERE python_version = '3.12'`) in one system the user already
  administers comfortably (MSSQL background). Add a **BM25 / full-text index**
  alongside — hybrid search matters for code: exact identifiers like
  `asyncio.TaskGroup` are keyword matches, not semantic ones.

### 3. Retrieval + serving
- **FastAPI proxy speaking the OpenAI Chat Completions API.** Intercepts the
  request, runs hybrid retrieval on the last user turn, prepends top-K chunks
  (with source + version so the model can cite freshness), forwards downstream.
- **Reranker** (`bge-reranker`, ~scraps of VRAM) between retrieval and prompt
  sharpens what actually lands in context.

### 4. Version-aware retrieval (the differentiator)
"Latest" isn't always right. If the project pins Python 3.12, answer from 3.12
docs, with deprecation notes pulled from newer ones. Version comes from (in
priority): explicit request hint → repo pin (`pyproject.toml` /
`.python-version`) → default "stable". **This metadata filtering is where the
SQL instinct becomes the actual edge over a naive embed-everything RAG.**

---

## Hardware fit (full fleet)

Available accelerators:
- **Mac Studio** — 36 GB unified memory (runs pxx + Ollama).
- **gpu-node-1** — 2× RTX A4500 20 GB, NVLink, **ECC** = 40 GB.
- **RTX 5080** — 16 GB (consumer, no ECC).
- **RTX A1000** — 8 GB, **ECC**.

Placement that the extra cards unlock (the key win: **stop stealing LLM VRAM
for the retrieval stack**):

- **LLM:** Q8 ≈ 28 GB / Q4 ≈ 18 GB for a 26B-class MoE → both A4500s via vLLM
  **`--tensor-parallel-size 2`**, where NVLink all-reduce traffic pays off. Q8
  fits with ~12 GB left for KV cache; Q4 fits one card and frees the other for
  context/batching. (Interactive-only → llama.cpp/Ollama layer-split is fine;
  NVLink barely used.)
- **Embedder + reranker (~2–3 GB):** dedicate the **A1000 8 GB** to them. ECC
  is a nice fit for an always-on ingestion/retrieval service, and it leaves
  *both* A4500s entirely for the LLM + a longer KV cache. This is the cleanest
  split.
- **RTX 5080 16 GB:** spare capacity — options: (a) host a **draft model for
  speculative decoding** if the chosen LLM ships one (Gemma 4 does), (b) run
  the **A/B challenger model** concurrently so both arms serve at once, or
  (c) hold the Postgres+pgvector working set / a second small model.
- **Studio (36 GB unified):** stays the orchestrator + Tier-1 local Ollama;
  not part of the SME serving path unless you want a Mac-hosted fallback.

Net: the SME's embed+rerank tax moves *off* the A4500s onto the A1000, so the
40 GB NVLink pair is reserved end-to-end for the coding model and its context —
exactly what the 256K-context candidates want.

---

## §5 Open design decisions (resolve before building)

**Decision A — SME proxy vs. audit-proxy ordering.** Putting the SME *in front
of* the audit-proxy means the audit log records the *augmented* request
(retrieved chunks visible in the audit) — good for post-mortems, but bloats
the log. Putting it *behind* keeps audits clean but hides what context the
model actually saw. **Lean:** SME in front, but log only chunk *IDs/sources*,
not full chunk text, into a separate retrieval-audit table.

**Decision B — prompt-cache tension.** `config/aider.conf.yml` enables prompt
caching. Injecting variable retrieved context **near the top** of the message
list busts the cached prefix every turn, killing the caching win. **Mitigation:**
inject retrieved docs as a *late* user/system turn (after the stable
system+repo-map prefix), or gate injection to only requests that look like they
need docs (heuristic: mentions an import / stdlib symbol / "latest"). Measure
cache hit-rate before/after.

**Decision C — does Aider tolerate injected turns?** Aider builds its own
message array (system prompt, repo map, files, chat). A transparent proxy that
*adds* a message is usually fine, but verify Aider doesn't choke on an
unexpected role/length and that token budget math still fits the model's window
(the 14b model has a hard 16k in+out; a 26B MoE candidate has 256K headroom,
which is partly *why* it's the better SME host).

**Decision D — where does the proxy run?** On the gpu-node-1 (next to vLLM, lowest
latency, but adds a service to the SSH-only box) or on the Studio (where pxx
runs, simpler to iterate, but every request crosses the tunnel twice). **Lean:**
gpu-node-1, co-located with vLLM and the existing audit-proxy.

---

## §6 Model A/B — RUN 2026-07-17 (RESULTS BELOW)

**Result (fleet incumbent vs priority, both through the SME on a fresh
1,715-chunk store — 49/49 sources, 12 stdlib-API questions, temp 0):**

| Model | docs-off | docs-on | retrieval lift |
|---|---|---|---|
| `qwen2.5-coder-14b-coder-lora` (incumbent, gpu-node-1) | 11/12 (92%) | **12/12 (100%)** | **+1** |
| `Qwen3-Coder` (priority, vllm-host-1) | 11/12 (92%) | 11/12 (92%) | +0 (one gain, one loss) |

**Findings:**
- Both models already answer 3.12-era stdlib questions at 92% from frozen
  weights — the base bar is high, so retrieval has little room.
- Retrieval **helps the 14b** (closes its one gap: `TaskGroup.create_task`
  signature) and is **net-neutral for Qwen3-Coder** — it gained the same
  TaskGroup case but *lost* `subprocess_capture`, where injected docs
  distracted a correct base answer. Classic RAG failure mode (Decision-B
  cache/relevance risk in §7), visible on the fleet's own primary model.
- **Decision:** the SME earns its place in front of the *fallback* 14b, not
  the *priority* Qwen3-Coder, which needs no augmentation to hit 92% and is
  occasionally hurt by it. No prod model swap — the 2026-07-15 A/B already
  settled Qwen3-Coder as priority on edit quality; this §6 pass settles the
  narrower "does the SME help each model" question, which was its actual
  scope. The candidate models below (Qwen3-Coder-Next, Gemma 4) were never
  deployed and remain deferred until a reason to deploy them appears.
- Raw reports: `eval/run_ab.py` output for each arm (scratchpad, 2026-07-17).

**Infra note:** the eval store is a machine-local Postgres 17 + pgvector
(`docs_sme` DB) built for this A/B; query embeddings via local Ollama
`nomic-embed-text`. Not promoted to a running service — that's a separate
infra decision, out of this plan's scope.

## §6 (original framing, left open per 2026-06-10 decision)

The SME proxy is **model-agnostic** — it augments whatever vLLM serves. But the
backing coding model is worth an explicit A/B *independent of* this plan:

| Candidate | Why | Notes |
|---|---|---|
| **Qwen3-Coder-Next** | Strongest pure-code: **70.6–71.3% SWE-bench Verified** (scaffold-dependent); beats DeepSeek-V3.2, trails GLM-4.7. MoE, fast/token, 256K ctx. | Top pick for Aider/agentic editing. |
| **Gemma 4 26B A4B** | Fast generalist, strong coding, native function calling, configurable thinking mode, 256K ctx. #6 open on text Arena. | Verbose thinking mode costs local tokens. |
| **qwen2.5-coder-14b** (incumbent) | Fine-tuned + `coder-prod` LoRA already in prod on :8003. | Baseline to beat; keep until A/B is conclusive. |

**A/B method:** run candidates behind the *same* SME proxy on the same task set
(real pxx/repo edit tasks + a few T-SQL/PowerShell/Python prompts), compare
edit-acceptance and latency. The proxy makes this clean: only `PXX_MODEL` /
the vLLM `--served-model-name` changes between arms. Capture results in a
follow-up doc; do **not** swap the prod model until an arm wins.

Both context windows (256K) are a *reason* this RAG matters: room to stuff
several official doc pages alongside the repo map.

---

## §7 Risks / things that bite

- **Cache busting** (Decision B) — the single biggest latency/cost risk.
- **Audit-log bloat** (Decision A) — mitigate by logging chunk refs, not text.
- **Stale-version answers** — a version filter bug is worse than no RAG; it
  injects *confidently wrong* docs. Default to "no chunk" over "wrong-version
  chunk" when the version can't be resolved.
- **Allowlist drift** — the crawler must be allowlist-enforced in code, not
  config-by-convention, so it can never wander off official sources.
- **Scope creep vs. agentmemory** — keep this strictly *external reference
  docs*. Session observations stay in the Phase 8.x system.

---

## §8 Phased build (suggested)

- **T0 — Spike:** ✅ FastAPI OpenAI-shim that forwards verbatim (no retrieval),
  pointed at by `PXX_VLLM_URL`. Proves the seam + Decisions A/C cheaply.
- **T1 — Ingestion + store:** ✅ allowlist crawler (delta-hash) → lxml
  structural chunking → embed (local Ollama nomic-embed-text, 768-dim) →
  pgvector + generated tsvector. Verified live on `docs.python.org/3.12`.
- **T2 — Hybrid retrieval + inject:** ✅ RRF (vector + tsvector, identifier-
  aware) → gate → late injection with graceful degradation. Reranker is a
  pluggable stage (identity default). **T2b** (optional bge cross-encoder)
  staged — needs a torch model download.
- **T3 — Version-aware filter:** ✅ session version via /control/context
  (single active project assumed); retrieval filters by it; default-safe
  (no match → inject nothing, never wrong-version). Verified live.
- **T4 — Timer + delta refresh:** ✅ `docs-sme-refresh` over a curated
  sources.toml (stdlib + deps + PEPs); launchd daily timer on the Studio.
  Verified live: 48 ingested / 1666 chunks first run, full 49-skip in ~1.5s
  on re-run.
- **T5 — `pxx --with-docs`:** ✅ flag routes aider through the SME + posts the
  resolved Python version. **Model A/B (§6) is the last open item.**

---

## §9 pxx-repo touch points (minimal, for reference)

- `pxx/endpoints.py` — none required if using `PXX_VLLM_URL`; optional new
  candidate if `--with-docs` gets a dedicated default URL.
- `pxx/cli.py` — optional `--with-docs` flag + banner line (mirror
  `--with-router` / `--with-memory`).
- `config/model-settings.yml` — only if the backing model changes (§6);
  **guardrailed file — ask before editing.**
- `docs/` — a short "docs-RAG SME" usage note once T0/T1 land.

---

## §10 Backlog bookkeeping note

`plans/backlog.md` (referenced by `CLAUDE.md`) does not yet exist; plans
currently self-describe via inline `> Backlog ID: NNN` headers (008, 8.5).
This plan claims **ID 009**. If/when the master inventory is created, add a row
for 009 and bump the next-free-ID line.
