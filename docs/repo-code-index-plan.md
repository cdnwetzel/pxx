# Repo code index — design & build plan

Status: **planned** (foundation shipped in 2.5.1). This is the remainder of a
coordination request from a downstream governed-OS consumer ("ask 1"): a
repo-aware code index a solo dev with one GPU wants most and is least likely to
build themselves.

## Goal

Symbol-aware retrieval over a repository's source: chunk code by AST symbol
(functions, classes, methods), embed each chunk, and retrieve the top-k chunks
relevant to a query — an **extension** of the existing memory embeddings/store, a
**distinct namespace** from observation memory, not a new subsystem.

## Non-negotiable constraints (from the coord thread + pxx values)

1. **Pluggable vector backend, numpy floor.** A `VectorBackend` interface with a
   zero-dependency **numpy brute-force** default (always works) and an
   **auto-detected sqlite-vec** accelerator; Qdrant/other are opt-in adapters,
   never a hard dep. Receipt driving this: macOS *system* python3 lacks
   `sqlite3.Connection.enable_load_extension`, so sqlite-vec cannot be the
   committed default — `pip install pxx` on a stock Mac must still index.
2. **Embedding-space versioning (already shipped, 2.5.1).** Reuse the `meta`
   identity stamp + `EmbeddingSpaceError` fail-closed. The index is stamped with
   its `roles.embed` identity; repointing the embedder without reindexing fails
   closed, not silent garbage. Dimension mismatch already throws; the same-dim /
   different-model case is the one the stamp catches.
3. **Observer, not authority.** The index is **pure retrieval** — it returns
   ranked context and never gates, judges, or emits a terminal. Any block-on-
   judgment decision is policy and lives on the consumer's policy layer, not here.
4. **Consumer-agnostic.** No downstream/governance-layer identifiers appear in
   installed pxx (the seam stays a generic `[[hooks]]` + openai-compatible
   `base_url`). pxx owns role→model NAME (`roles.embed`); endpoint/node placement
   stays a pluggable adapter.
5. **Graceful degradation.** Like numpy↔sqlite-vec, AST chunking degrades: when a
   tree-sitter grammar for a language is unavailable, fall back to a heuristic
   line/definition chunker rather than failing the index.

## Components (build order)

### 1. `VectorBackend` interface + numpy default + sqlite-vec accelerator
- `pxx/index/backends.py`: `VectorBackend` protocol — `upsert(ids, vectors, meta)`,
  `query(vector, k) -> [(id, score)]`, `delete(ids)`, `count()`.
- `NumpyBackend`: brute-force cosine over an in-memory / mmap'd float32 matrix.
  Single repo ≈ 1e4–1e5 chunks; 50k×768 f32 ≈ 150 MB, dot product single-digit ms
  — the correct default at the solo-dev-one-repo target.
- `SqliteVecBackend`: auto-detected (probe `enable_load_extension`); silent debug
  fallback to numpy when the extension can't load (a stock-Mac user did nothing
  wrong). Qdrant left as a documented adapter seam.

### 2. AST symbol chunker
- `pxx/index/chunker.py`: tree-sitter over source files → one chunk per symbol
  (function/class/method) carrying `{path, symbol, kind, signature, docstring,
  span, text}`. tree-sitter grammars are an **optional extra**; heuristic fallback
  when absent.
- Deterministic chunk IDs (`sha256(path + symbol + span)` or content hash) so
  re-indexing is idempotent and diffs are cheap.

### 3. Code index store
- `pxx/index/store.py`: a `CodeIndex` bound to a repo + the `roles.embed` embedder,
  **reusing the 2.5.1 embedding-space stamp** (own collection/namespace, separate
  from observation memory). Incremental: re-index only changed files (mtime/hash).
- `embed` resolved via `Settings.effective_role("embed")` (shipped in 2.5.0).

### 4. Retrieval API
- `search(query, *, k=8) -> [CodeChunk]` (k=8 matches the memory default). Vector-
  first; optional lexical/symbol-name boost. Returns chunks with provenance
  (path:span) so callers can cite `file:line`.

### 5. CLI + wiring
- `pxx index build [path]` / `pxx index query <q>` (thin CLI over the store).
- Integration into context assembly is a **separate, later** step — ship the index
  + retrieval first, wire consumers incrementally (same discipline as the role
  lanes).

## Tests & receipts

- Backend parity: numpy vs sqlite-vec return the same top-k on a fixture corpus.
- Fallback: with `enable_load_extension` absent, sqlite-vec path degrades to numpy
  (negative control — the accelerator's absence must not break indexing).
- Embedding-space guard reuse: reindex-required on an embed identity change (the
  2.5.1 negative control, exercised through the code index).
- Chunker: symbol boundaries correct on a fixture; heuristic fallback when a
  grammar is missing.
- **Receipt**: build the index over a real repo and retrieve a known symbol
  (RECEIPTS.md line), on the ladder hardware where feasible.

## Open questions (pending)

- **Ollama identity granularity** (name vs name+digest) — raised in the
  coordination thread, awaiting reply. 2.5.1 ships name-level; digest needs an async
  `/api/show` probe the sync `identity` property can't make.
- **`roles.embed_code` split** — carded, not reserved; a code-tuned embedder is
  *likely* worth its own lane once the index proves it (coord seq-28). Add a second
  closed lane name additively when proven.
- **`rerank` lane** — deferred until the index shows it earns one.

## Sequencing

Backend (1) → chunker (2) can proceed in parallel; store (3) depends on both; API
(4) then CLI (5). Land as focused PRs (CodeRabbit-gated), each with tests, on the
2.5.1 versioning foundation. Reference each PR in the coord thread.
