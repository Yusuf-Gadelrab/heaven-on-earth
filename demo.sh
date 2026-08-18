#!/usr/bin/env bash
# One-machine demo: a coordinator plus two workers — "atlas" at full power and
# "pebble" throttled to 25% to play the old laptop — so you can watch the grid
# split work by capability and verify results twice. Uses its own demo.db;
# your real heaven.db (and any Good Map pins in it) is never touched.
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8940}"
HUB="http://127.0.0.1:$PORT"

rm -f demo.db demo.db-wal demo.db-shm demo-*.log
python3 coordinator.py --port "$PORT" --db demo.db >demo-coordinator.log 2>&1 &
PIDS=($!)
trap 'kill "${PIDS[@]}" 2>/dev/null; wait 2>/dev/null' EXIT
sleep 1

python3 worker.py --join "$HUB" --name atlas >demo-atlas.log 2>&1 &
PIDS+=($!)
python3 worker.py --join "$HUB" --name pebble --throttle 0.75 >demo-pebble.log 2>&1 &
PIDS+=($!)
sleep 2

echo "— Estimating π across the grid —"
python3 submit.py --hub "$HUB" pi --samples 4000000 --watch
echo
echo "— Counting primes below 1,500,000 · every unit verified by two nodes —"
python3 submit.py --hub "$HUB" primes --end 1500000 --redundancy 2 --watch
echo
echo "Demo done. Bring the grid up for real with:  python3 coordinator.py"
echo "Then visit  $HUB  (Mission Control)  and  $HUB/good  (The Good Map)."
