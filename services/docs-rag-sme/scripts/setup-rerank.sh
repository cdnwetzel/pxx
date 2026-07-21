#!/usr/bin/env bash
# T2b: install the cross-encoder reranker deps and pre-download the model.
# Heavy (torch + a ~2GB model) so it's opt-in. No sudo needed on macOS.
#
#   bash scripts/setup-rerank.sh
#
# Override the model with DOCS_SME_RERANK_MODEL (default BAAI/bge-reranker-v2-m3;
# use BAAI/bge-reranker-base for a smaller/faster option).
set -euo pipefail

MODEL="${DOCS_SME_RERANK_MODEL:-BAAI/bge-reranker-v2-m3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.setup"
LOG="$LOG_DIR/rerank-setup.jsonl"
mkdir -p "$LOG_DIR"

emit() {
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"ts":"%s","step":"%s","status":"%s","detail":%s}\n' \
    "$ts" "$1" "$2" "$(printf '%s' "$3" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
    | tee -a "$LOG"
}

echo "T2b rerank setup — logging to $LOG"
cd "$ROOT"

if uv sync --extra rerank >/tmp/rerank-sync.log 2>&1; then
  emit deps ok "uv sync --extra rerank"
else
  emit deps fail "$(tail -3 /tmp/rerank-sync.log)"; exit 1
fi

# Pre-download + smoke-test the model so first request isn't a cold download.
if uv run python - "$MODEL" <<'PY'
import sys
from sentence_transformers import CrossEncoder
import torch
dev = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
m = CrossEncoder(sys.argv[1], device=dev, max_length=512)
s = m.predict([("asyncio.gather", "asyncio.gather runs awaitables concurrently"),
               ("asyncio.gather", "json.dumps serializes to a string")])
print(f"device={dev} scores={[round(float(x),3) for x in s]}")
assert s[0] > s[1], "reranker should score the relevant passage higher"
PY
then
  emit model ok "$MODEL downloaded + smoke-tested"
else
  emit model fail "model load/smoke-test failed for $MODEL"; exit 1
fi

echo "----"
echo "Done. Enable with: DOCS_SME_RERANK=bge"
echo "Full log: $LOG"
