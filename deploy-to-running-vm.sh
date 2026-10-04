#!/bin/sh
set -eu
HOST="${WB_HOST:-127.0.0.1}"
PORT="${WB_PORT:-2222}"
KEY="${WB_KEY:-$HOME/.ssh/whiteboard_pi}"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ ! -f "$KEY" ]; then
  echo "SSH key not found: $KEY" >&2
  exit 1
fi

echo "Deploying WhiteBoardOS Retrieval/Sync Agent v2.7 to ${HOST}:${PORT}..."
tar -C "$HERE" -cf - whiteboardos benchmarks scripts README.md WHITEBOARDOS-QUICKSTART.md 2>/dev/null | \
ssh -p "$PORT" -i "$KEY" -o StrictHostKeyChecking=accept-new "root@$HOST" '
set -eu
STAGE=/tmp/whiteboard-retrieval-agent-v27
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar -xf - -C "$STAGE"

systemctl stop whiteboardos || true

BACKUP=/opt/whiteboardos/pre-retrieval-agent-v27
if [ ! -d "$BACKUP" ]; then
  mkdir -p "$BACKUP"
  cp -a /opt/whiteboardos/whiteboardos "$BACKUP/whiteboardos"
  cp -a /opt/whiteboardos/benchmarks "$BACKUP/benchmarks" 2>/dev/null || true
  [ -x /usr/bin/wb ] && cp -a /usr/bin/wb "$BACKUP/wb" || true
fi

mkdir -p /opt/whiteboardos/whiteboardos/retrieval /opt/whiteboardos/benchmarks
cp "$STAGE/whiteboardos/catalog.py" /opt/whiteboardos/whiteboardos/catalog.py
cp "$STAGE/whiteboardos/server.py" /opt/whiteboardos/whiteboardos/server.py
cp -a "$STAGE/whiteboardos/retrieval/." /opt/whiteboardos/whiteboardos/retrieval/
cp "$STAGE/benchmarks/gemini_retrieval_benchmark.py" /opt/whiteboardos/benchmarks/gemini_retrieval_benchmark.py
cp "$STAGE/benchmarks/gemini_organize_benchmark.py" /opt/whiteboardos/benchmarks/gemini_organize_benchmark.py
install -m 755 "$STAGE/scripts/wb" /usr/bin/wb
install -m 755 "$STAGE/scripts/wb-stress-corpus" /usr/bin/wb-stress-corpus
install -m 755 "$STAGE/scripts/wb-organize-corpus" /usr/bin/wb-organize-corpus
install -m 755 "$STAGE/scripts/wb-full-test" /usr/bin/wb-full-test
cp "$STAGE/WHITEBOARDOS-QUICKSTART.md" /opt/whiteboardos/WHITEBOARDOS-QUICKSTART.md
find /opt/whiteboardos/whiteboardos/retrieval -type f -name "*.py" -exec chmod 644 {} \;
chmod 644 /opt/whiteboardos/whiteboardos/catalog.py /opt/whiteboardos/whiteboardos/server.py /opt/whiteboardos/benchmarks/gemini_retrieval_benchmark.py /opt/whiteboardos/benchmarks/gemini_organize_benchmark.py

# Persist API credential location without overwriting any existing key.
install -d -m 700 -o 1000 -g 1000 /data/whiteboardos/.secrets
if [ -f /data/whiteboardos/.secrets/gemini_api_key ]; then
  chown 1000:1000 /data/whiteboardos/.secrets/gemini_api_key
  chmod 600 /data/whiteboardos/.secrets/gemini_api_key
fi

# Keep both the metadata model and embedding model resident.
mkdir -p /etc/systemd/system/whiteboard-ollama.service.d
cat > /etc/systemd/system/whiteboard-ollama.service.d/retrieval-agent-v27.conf <<UNIT
[Service]
Environment=OLLAMA_MAX_LOADED_MODELS=2
Environment=OLLAMA_KEEP_ALIVE=-1
UNIT
systemctl daemon-reload
systemctl restart whiteboard-ollama

i=0
until /usr/bin/python3 - <<PY >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:11434/api/tags",timeout=2).read()
PY
do
  i=$((i+1))
  [ "$i" -ge 20 ] && { echo "Ollama did not become ready" >&2; exit 1; }
  sleep 1
done

systemctl start whiteboardos
i=0
until systemctl is-active --quiet whiteboardos; do
  i=$((i+1))
  [ "$i" -ge 20 ] && { echo "whiteboardos did not become active" >&2; exit 1; }
  sleep 1
done
sleep 1

echo "Refreshing pending semantic state and rebuilding sparse graph..."
/usr/bin/wb rebuild-retrieval || true
echo "Embedding model warmup is handled by the running service; wb-full-test only retries warmup if a query falls back."
printf "\nRetrieval/sync status:\n"
/usr/bin/wb sync-status --limit 5
'

echo
echo "Retrieval/Sync Agent v2.7 deployed."
echo "Adds folder-aware retrieval, safe natural-language file actions, and warm-on-fallback full-stack tests."
echo "Inside WhiteBoardOS run:"
echo "  wb-full-test"
echo "  wb retrieval-status"
echo "  wb sync-status"
