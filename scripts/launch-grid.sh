#!/usr/bin/env bash
# Bring up a real local grid: coordinator + 4 workers with varied capability
# (one machine playing four). Logs go to ~/.heaven/logs/.
set -e
cd "$(dirname "$0")/.."
PORT="${PORT:-8940}"
HUB="http://127.0.0.1:$PORT"
LOGS="$HOME/.heaven/logs"
mkdir -p "$LOGS"

if ! curl -s -o /dev/null --max-time 2 "$HUB/api/stats"; then
  nohup python3 coordinator.py --port "$PORT" >"$LOGS/coordinator.log" 2>&1 &
  sleep 1
fi
pgrep -f "worker.py --join $HUB --name atlas"  >/dev/null || nohup python3 worker.py --join "$HUB" --name atlas                            >"$LOGS/atlas.log"  2>&1 &
pgrep -f "worker.py --join $HUB --name breeze" >/dev/null || nohup python3 worker.py --join "$HUB" --name breeze --cores 4                 >"$LOGS/breeze.log" 2>&1 &
pgrep -f "worker.py --join $HUB --name pebble" >/dev/null || nohup python3 worker.py --join "$HUB" --name pebble --cores 2 --throttle 0.6  >"$LOGS/pebble.log" 2>&1 &
pgrep -f "worker.py --join $HUB --name mote"   >/dev/null || nohup python3 worker.py --join "$HUB" --name mote   --cores 1 --throttle 0.85 >"$LOGS/mote.log"   2>&1 &
sleep 3
curl -s "$HUB/api/stats" | python3 -c "import json,sys; s=json.load(sys.stdin); print('grid up:', s['grid']['nodes_online'], 'nodes online ·', s['grid']['grid_score'], 'kH/s total')"
