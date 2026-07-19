#!/usr/bin/env bash
# Pull the §6 A/B candidate model(s) into Studio Ollama. Self-logging JSONL.
# No sudo. Gemma 4 26B A4B at Q4 is ~18GB and fits the Studio's 36GB.
#
#   bash scripts/setup-models.sh
#
# Qwen3-Coder-Next is the gpu-node-1 candidate (ultra-sparse MoE, larger than the
# Studio comfortably serves) — deploy it on the gpu-node-1 vLLM, not here.
set -euo pipefail

MODELS=("${@:-gemma4:26b}")
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.setup"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/models-setup.jsonl"

emit() {
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"ts":"%s","step":"%s","status":"%s","detail":%s}\n' \
    "$ts" "$1" "$2" "$(printf '%s' "$3" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" \
    | tee -a "$LOG"
}

echo "Model setup — logging to $LOG"
curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1 || { emit ollama fail "ollama not reachable at $OLLAMA_URL"; exit 1; }

for m in "${MODELS[@]}"; do
  echo "pulling $m ..."
  if ollama pull "$m" >/dev/null 2>&1; then
    # smoke-test a one-token generation through the OpenAI-compatible endpoint
    ok=$(curl -sf "$OLLAMA_URL/v1/chat/completions" -H 'Content-Type: application/json' \
          -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}],\"stream\":false,\"max_tokens\":5}" \
          | python3 -c 'import json,sys;print("ok" if json.load(sys.stdin).get("choices") else "no")' 2>/dev/null || echo "no")
    emit "$m" "$([ "$ok" = ok ] && echo ok || echo fail)" "pulled; chat smoke=$ok"
  else
    emit "$m" fail "ollama pull $m failed"
  fi
done

echo "----"
echo "Run the A/B including a new model, e.g.:"
echo "  uv run python eval/run_ab.py --models 'qwen2.5-coder:7b,gemma4:26b'"
echo "Full log: $LOG"
