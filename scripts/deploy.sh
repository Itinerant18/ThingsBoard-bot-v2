#!/usr/bin/env bash
# Production deploy, run ON the EC2 host by CI (see .github/workflows/ci.yml).
#
# The SSH key CI holds is locked to ~/deploy.sh via an authorized_keys forced command,
# so a leaked deploy key can only trigger this script — it cannot get a shell. ~/deploy.sh
# updates the checkout and then execs THIS file, so the deploy logic stays version
# controlled and a change here ships with the commit that makes it.
#
# Exits non-zero if the new containers do not come up healthy, after rolling back to the
# previous image — a red CI run should mean "production is still on the last good build",
# never "production is broken and CI went green anyway".
set -euo pipefail

cd /home/ubuntu/ThingsBoard-Bot-v2
COMPOSE="docker compose -f docker-compose.prod.yml"

echo "==> deploying $(git log --oneline -1)"

# Keep a way back. On the very first run there is no image yet, hence the || true.
docker tag thingsboard-bot-v2:latest thingsboard-bot-v2:rollback 2>/dev/null || true

$COMPOSE up -d --build

# Health gate. The app needs a moment to bind, so poll rather than checking once.
healthy() {
  docker exec chatbot-v2 python -c "
import sys, urllib.request
try:
    sys.exit(0 if urllib.request.urlopen('http://localhost:8083/health', timeout=3).status == 200 else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# The image now has a node build stage, so every deploy adds build-cache layers on a
# host with ~2.5 GB free. Trim the cache but keep a working set, otherwise a deploy
# eventually fails on a full disk rather than on anything to do with the code.
docker builder prune -f --keep-storage 2GB >/dev/null 2>&1 || true

for attempt in $(seq 1 30); do
  if healthy; then
    echo "==> healthy after ${attempt} attempt(s)"
    # Surface the scheduler's own summary; silence here would mean the sync never ran.
    sleep 10
    docker logs chatbot-v2 2>&1 | grep '\[LIVE-SYNC\]' | tail -5 || true
    echo "==> deploy OK"
    exit 0
  fi
  sleep 2
done

echo "!!! health check failed after 60s — rolling back" >&2
docker logs chatbot-v2 2>&1 | tail -40 >&2
if docker image inspect thingsboard-bot-v2:rollback >/dev/null 2>&1; then
  docker tag thingsboard-bot-v2:rollback thingsboard-bot-v2:latest
  $COMPOSE up -d --no-build
  echo "!!! rolled back to the previous image" >&2
fi
exit 1
