#!/usr/bin/env bash
# T1b infra setup: Postgres + pgvector + a local embedding model.
#
# Idempotent. Run it; it appends one JSON-Lines record per step to the log so
# Claude can read the outcome without copy-paste. On macOS none of this needs
# sudo (brew + ollama are user-level); the script flags anything that would.
#
#   bash scripts/setup-t1b.sh
#
# Override targets via env: PG_FORMULA (default postgresql@17), DB_NAME
# (default docs_sme), EMBED_MODEL (default nomic-embed-text).
set -euo pipefail

PG_FORMULA="${PG_FORMULA:-postgresql@17}"
DB_NAME="${DB_NAME:-docs_sme}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.setup"
LOG="$LOG_DIR/t1b-setup.jsonl"
mkdir -p "$LOG_DIR"

emit() { # step status detail
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"ts":"%s","step":"%s","status":"%s","detail":%s}\n' \
    "$ts" "$1" "$2" "$(printf '%s' "$3" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
    | tee -a "$LOG"
}

echo "T1b setup — logging to $LOG"
echo "----"

# 1. Homebrew present?
if command -v brew >/dev/null 2>&1; then
  emit brew ok "$(brew --version | head -1)"
else
  emit brew fail "homebrew not found — install from https://brew.sh first"
  exit 1
fi

# 2. Postgres
if brew list --versions "$PG_FORMULA" >/dev/null 2>&1; then
  emit postgres-install skip "already installed: $(brew list --versions "$PG_FORMULA")"
else
  brew install "$PG_FORMULA" && emit postgres-install ok "installed $PG_FORMULA" \
    || { emit postgres-install fail "brew install $PG_FORMULA failed"; exit 1; }
fi
export PATH="$(brew --prefix "$PG_FORMULA")/bin:$PATH"

# 3. pgvector
if brew list --versions pgvector >/dev/null 2>&1; then
  emit pgvector-install skip "already installed"
else
  brew install pgvector && emit pgvector-install ok "installed pgvector" \
    || { emit pgvector-install fail "brew install pgvector failed"; exit 1; }
fi

# 4. Start the service
brew services start "$PG_FORMULA" >/dev/null 2>&1 || true
for _ in $(seq 1 20); do pg_isready -q && break; sleep 0.5; done
if pg_isready -q; then
  emit postgres-start ok "$(pg_isready)"
else
  emit postgres-start fail "postgres not accepting connections after start"
  exit 1
fi

# 5. Database
if psql -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "$DB_NAME"; then
  emit createdb skip "database $DB_NAME exists"
else
  createdb "$DB_NAME" && emit createdb ok "created $DB_NAME" \
    || { emit createdb fail "createdb $DB_NAME failed"; exit 1; }
fi

# 6. Enable the vector extension
if psql -d "$DB_NAME" -tAc "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
  ver="$(psql -d "$DB_NAME" -tAc "SELECT extversion FROM pg_extension WHERE extname='vector';")"
  emit pgvector-extension ok "vector $ver enabled in $DB_NAME"
else
  emit pgvector-extension fail "CREATE EXTENSION vector failed (is pgvector on the right PG?)"
  exit 1
fi

# 7. Embedding model (local, via Ollama — keeps runtime local)
if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  ollama pull "$EMBED_MODEL" >/dev/null 2>&1 \
    && emit embed-pull ok "pulled $EMBED_MODEL" \
    || emit embed-pull fail "ollama pull $EMBED_MODEL failed"
  dim="$(curl -sf "$OLLAMA_URL/api/embeddings" -d "{\"model\":\"$EMBED_MODEL\",\"prompt\":\"ping\"}" \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("embedding",[])))' 2>/dev/null || echo 0)"
  if [ "$dim" -gt 0 ]; then
    emit embed-verify ok "$EMBED_MODEL embedding dim=$dim"
  else
    emit embed-verify fail "embedding call returned no vector"
  fi
else
  emit embed-pull fail "ollama not reachable at $OLLAMA_URL"
fi

echo "----"
echo "Done. Full log: $LOG"
echo "Connection: postgresql://localhost/$DB_NAME"
