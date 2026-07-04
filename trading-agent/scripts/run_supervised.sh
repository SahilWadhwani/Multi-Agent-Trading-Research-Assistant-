#!/usr/bin/env bash
# Restart run_agent.py on crash; warn if log/agent_health is stale.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR" "$ROOT/data_cache"

AGENT_LOG="$LOG_DIR/agent_$(date +%Y%m%d).log"
HEALTH="$ROOT/data_cache/agent_health.json"
ALERT="$ROOT/data_cache/agent_supervisor_alert.txt"
PIDFILE="$ROOT/data_cache/agent_supervised.pid"

export PYTHONUNBUFFERED=1

echo $$ >"$PIDFILE"

staleness_check() {
  local now health_ts diff
  now=$(date +%s)
  if [[ -f "$HEALTH" ]]; then
    health_ts=$(python3 -c "
import json
from datetime import datetime
p = r'''$HEALTH'''
try:
    with open(p) as f:
        d = json.load(f)
    s = d.get('updated_at', '')
    dt = datetime.fromisoformat(s)
    print(int(dt.timestamp()))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
  else
    health_ts=0
  fi
  if [[ "$health_ts" -eq 0 ]]; then
    echo "$(date -Iseconds) No agent_health.json — agent may be down" >>"$ALERT"
    return
  fi
  diff=$((now - health_ts))
  if [[ "$diff" -gt 600 ]]; then
    echo "$(date -Iseconds) Agent health stale (${diff}s) — check logs" >>"$ALERT"
  fi
}

while true; do
  staleness_check || true
  echo "$(date -Iseconds) Starting run_agent.py" | tee -a "$AGENT_LOG"
  set +e
  python3 run_agent.py >>"$AGENT_LOG" 2>&1
  code=$?
  set -e
  echo "$(date -Iseconds) run_agent.py exited code=$code" | tee -a "$AGENT_LOG"
  sleep 5
done
