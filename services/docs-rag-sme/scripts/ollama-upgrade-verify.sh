#!/usr/bin/env bash
# Run AFTER upgrading Ollama.app. Verifies the pxx fleet survived the engine
# swap and that nomic-embed-text still produces vectors compatible with the
# 1,715 already-stored chunks; then pulls the §6 A/B candidate. Self-logs JSONL.
#
#   bash scripts/ollama-upgrade-verify.sh
set -euo pipefail

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.setup"; LOG="$LOG_DIR/ollama-upgrade.jsonl"
mkdir -p "$LOG_DIR"; cd "$ROOT"
export PATH="/opt/homebrew/bin:$PATH"

emit() {
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"ts":"%s","step":"%s","status":"%s","detail":%s}\n' \
    "$ts" "$1" "$2" "$(printf '%s' "$3" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" \
    | tee -a "$LOG"
}

echo "Post-upgrade verify — logging to $LOG"
curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1 || { emit ollama fail "not reachable at $OLLAMA_URL"; exit 1; }
emit version ok "$(ollama --version 2>&1 | head -1)"

# 1) Embedding-drift check: cosine of the re-embedded probe vs the baseline.
#    <0.999 means stored vectors are stale -> must re-embed (refresh --force).
uv run python - "$OLLAMA_URL" <<'PY'
import json, sys, pathlib, httpx
import numpy as np
base = json.loads(pathlib.Path(".setup/ollama-probe.json").read_text())
r = httpx.post(f"{sys.argv[1]}/api/embed",
               json={"model":"nomic-embed-text","input":[base["probe"]]}, timeout=60)
v = np.array(r.json()["embeddings"][0]); b = np.array(base["vec"])
cos = float(v @ b / (np.linalg.norm(v)*np.linalg.norm(b)))
verdict = "ok" if cos >= 0.999 else "DRIFT"
print(json.dumps({"cosine_vs_baseline": round(cos,6), "verdict": verdict,
                  "dim_now": len(v), "dim_before": base["dim"]}))
sys.exit(0 if cos >= 0.999 else 3)
PY
if [ $? -eq 0 ]; then
  emit embed-drift ok "nomic-embed-text vectors unchanged — stored chunks valid"
else
  emit embed-drift DRIFT "nomic embeddings changed — run: uv run docs-sme-refresh --force"
fi

# 2) Smoke each pxx fleet model (1-token gen) so we know generation still works.
for m in qwen2.5-coder:7b devstral:24b qwen2.5:32b-instruct-q4_K_M; do
  ok=$(curl -sf "$OLLAMA_URL/v1/chat/completions" -H 'Content-Type: application/json' \
        -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}],\"stream\":false,\"max_tokens\":3}" \
        | python3 -c 'import json,sys;print("ok" if json.load(sys.stdin).get("choices") else "no")' 2>/dev/null || echo "no")
  emit "smoke:$m" "$([ "$ok" = ok ] && echo ok || echo fail)" "chat smoke=$ok"
done

# 3) Pull the §6 candidate now that the engine is new enough.
if ollama pull gemma4:26b >/dev/null 2>&1; then
  emit pull-gemma4 ok "gemma4:26b pulled"
else
  emit pull-gemma4 fail "ollama pull gemma4:26b still failing"
fi

echo "----"
echo "If embed-drift=DRIFT, re-embed first:  uv run docs-sme-refresh --force"
echo "Then the candidate A/B:  uv run python eval/run_ab.py --models 'qwen2.5-coder:7b,gemma4:26b'"
echo "Full log: $LOG"
