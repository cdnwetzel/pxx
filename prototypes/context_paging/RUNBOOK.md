# Runbook — earn the Context Paging v0 live receipt on Neo

Goal: prove a real 4B/8K local model completes a real single-file fix through the paging
runtime, host-verified, and capture the receipt. The mechanism is already proven offline
(`tests/test_context_paging.py`); this is the **live arm**.

Time: ~15 min. Where: the 8 GB MacBook (Neo). Fallback: the M4 Mac mini (16 GB) is the gentler
first pass — run there first if Neo struggles.

---

## 0. Prerequisites (once)

```bash
# on Neo
brew install ollama            # or the official installer
ollama serve &                 # leave running (default: http://localhost:11434)
ollama pull qwen3:4b           # the 4B/8K model (~2.5 GB)

# get the pxx repo (prototypes/ is in the repo, NOT the pip wheel)
git clone git@github.com:cdnwetzel/pxx.git   # or: git -C ~/ai/pxx pull origin v2
cd pxx && git checkout v2

# a Python env with httpx + pytest (RUN_TEST shells out to `python -m pytest`):
uv sync --extra dev            # if uv is installed (brings httpx + pytest)
#   ...or:  python -m venv .venv && . .venv/bin/activate && pip install httpx pytest
```

Sanity check the model endpoint is up **and** serving the model you'll request:

```bash
curl -fsS http://localhost:11434/v1/models | grep -q 'qwen3:4b' \
  && echo "endpoint OK, qwen3:4b present" || echo "MODEL NOT FOUND — pull it / check the endpoint"
```

---

## 1. Run it

From the **repo root** (the module is imported from `prototypes/`, so run from there):

```bash
uv run --extra dev python -m prototypes.context_paging.run_neo \
    --base-url http://localhost:11434/v1 \
    --model qwen3:4b \
    --cap 5500 \
    --max-actions 40 \
    --workdir ~/paging-neo-run --keep
```

- `--base-url` is the OpenAI-compatible root **including `/v1`** (Ollama's is `.../v1`).
- `--cap` is the hard input-token cap per capsule. Start 5500; if the model is confused by big
  capsules, lower it (e.g. 4000). If it never has enough context, raise it.
- `--workdir ... --keep` writes the scratch repo + `state/receipt.json` to a fixed dir you keep
  (omit both to use a temp dir that's cleaned up).
- **Real token count (recommended for the receipt):** add `--hf-tokenizer Qwen/Qwen3-4B` to the
  command above. It needs `transformers` **in the same interpreter**, so launch with
  `uv run --with transformers --extra dev python -m prototypes.context_paging.run_neo <the flags
  above> --hf-tokenizer Qwen/Qwen3-4B` (a bare `pip install transformers` may target a different
  Python, and `run_neo` then silently falls back). Without it the cap uses a documented char/4
  approximation, and the receipt's `tokenizer_id` records which was used — it never overstates fidelity.

---

## 2. What success looks like

Console tail:

```text
=== TERMINAL: COMPLETED
=== actions: <n>  capsules: <n>
=== negative_controls observed: {...}
=== verification: {'command': '... pytest ... test_bug.py', 'host_run': True, 'passed': True}
=== receipt written: <workdir>/state/receipt.json
```

Exit code `0` == the model reached a **host-verified** COMPLETE. The receipt:

```bash
cat ~/paging-neo-run/state/receipt.json
```

A valid receipt has:
- `"terminal": "COMPLETED"`
- `"verification": {"host_run": true, "passed": true}` — the host ran the ledger's test, not the model
- `"capsules": [...]` each `"under_cap": true` — every capsule stayed within the hard cap
- `"actions": [...]` — the trace **ends** with an applied `PATCH` (carrying `expected_sha`), a
  `RUN_TEST`, then the accepted `COMPLETE`. It may also contain **earlier retries** — `INVALID`
  actions, stale/rejected patches — before the model gets there; that is normal, not a failure.
- `"model_id"` / `"tokenizer_id"` — provenance

---

## 3. If it does NOT complete (expected with a 4B — iterate, don't force)

A 4B model may not one-shot the typed-action protocol. This is a real result, not a failure of
the mechanism — the receipt still records an **honest** terminal (`BLOCKED:...`), which is the
point. Read `state/receipt.json` `actions` to see where it got stuck, then:

| Symptom (in the receipt / log) | Try |
|---|---|
| `BLOCKED: model_returned_no_json_action` / invalid JSON | model isn't emitting clean JSON — lower `--cap`, or try the Mac mini 16 GB first; consider a more instruction-tuned 4B |
| `PATCH REJECTED: old_string matched N times` | the model quoted a non-unique anchor — it usually self-corrects next round; raise `--max-actions` |
| `PATCH REJECTED (stale expected_sha)` repeatedly | model isn't using the sha shown in the capsule — a prompt/model-capability limit; note it |
| `BLOCKED: target_source_exceeds_capsule` | `--cap` too small for the file — raise it |
| loops to `action_budget_exhausted` | raise `--max-actions`, or lower `--cap` so capsules are crisper |
| `BLOCKED: model_endpoint_error:*` | Ollama not reachable / model not pulled — recheck step 0 |

Start on the **Mac mini (16 GB)** if Neo's 8 GB is too tight for `qwen3:4b` + headroom:
same command, run on the mini.

---

## 4. Capture the receipt (make it a receipt, not a claim)

Once you get a `COMPLETED` receipt, archive it **inside the checked-out repo** (derive the root so
this works wherever you cloned) with a filename that labels the **hardware + model**:

```bash
# run this from inside your pxx checkout (any location)
REPO_ROOT="$(git rev-parse --show-toplevel)"
DEST="$REPO_ROOT/prototypes/context_paging/receipts"
mkdir -p "$DEST"
# name it <hardware>-<model>-<date>.json so the box under test is unambiguous
cp ~/paging-neo-run/state/receipt.json \
   "$DEST/neo-a18pro-8gb-qwen3-4b-$(date +%Y%m%d).json"
```

Then it goes through the normal gate (do NOT skip — this is the evidence):
1. `git checkout -b receipt/context-paging-neo-v0`
2. `git add prototypes/context_paging/receipts/*.json`
3. commit + push, open a PR
4. **CodeRabbit must pass** before merge; then merge to `v2`
5. add a `docs/RECEIPTS.md` line (R-0xx) pointing at the receipt file + the model/tokenizer/hardware

Scrub check before committing (the receipt is machine-written and already secret-scrubbed, but the
gate confirms it): `uv run --extra dev pxx check --all-files` must be clean.

---

## 5. What this does and does NOT prove

- **Proves:** the paging mechanism drives a real small model to a host-verified fix on real
  hardware, under a hard capsule cap, with an honest terminal and a durable receipt.
- **Does NOT prove:** multi-file work, symbol-level paging, or a specific success *rate* — those
  are v1 and a later eval. One green receipt = the mechanism works end-to-end on Neo. If the 4B
  can't get there, that's a documented capability finding, and the honest `BLOCKED` receipt is
  itself the result.

Next after a green receipt: the **build-native vs. compose-on-Camelid** decision (revive the
parked timtoole02 RFC), and v1 (symbol cards + repo map + multi-file).
