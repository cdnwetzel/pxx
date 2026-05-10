#!/usr/bin/env bash
# Pre-flight check before a coding session.
# Run anytime: bash scripts/doctor.sh

set -uo pipefail

probe() {
  local url="$1"
  if [[ -z "$url" ]]; then
    echo "(not set)"
    return
  fi
  if curl -sS --max-time 1 "$url/api/tags" >/dev/null 2>&1; then
    local models
    models=$(curl -sS --max-time 1 "$url/api/tags" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models', [])) or 'none loaded')" 2>/dev/null || echo "?")
    echo "OK — models: $models"
  else
    echo "unreachable"
  fi
}

echo "=== pxx doctor ==="
echo

echo "Endpoints (priority order):"
printf "  1. Studio LAN  (%s): " "${PXX_STUDIO_LAN_URL:-http://mac-studio.local:11434}"
probe "${PXX_STUDIO_LAN_URL:-http://mac-studio.local:11434}"

printf "  2. Studio over VPN (%s): " "${PXX_STUDIO_REMOTE_URL:-not set}"
probe "${PXX_STUDIO_REMOTE_URL:-}"

printf "  3. Neo localhost (offline fallback): "
probe "http://localhost:11434"

echo
echo "Memory pressure:"
if command -v memory_pressure >/dev/null; then
  memory_pressure 2>/dev/null | grep -E "(percentage|state)" || echo "  (no output)"
else
  vm_stat | head -5
fi

echo
echo "Currently loaded models (local Ollama):"
ollama ps 2>/dev/null || echo "  (no local Ollama running)"

echo
echo "CPU temp (Mac):"
if command -v osx-cpu-temp >/dev/null; then
  osx-cpu-temp
else
  echo "  install with: brew install osx-cpu-temp"
fi
