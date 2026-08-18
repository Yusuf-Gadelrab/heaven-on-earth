#!/usr/bin/env python3
"""Heaven on Earth — volunteer worker. Zero dependencies.

Turn this computer into part of one big machine:

    python3 worker.py --join http://COORDINATOR-IP:8940

It benchmarks itself, joins the grid, and pulls work sized to what it can
actually handle. Only the whitelisted tasks in tasks.py ever run here.
"""
import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import tasks


def http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def ram_gb():
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (ValueError, OSError, AttributeError):
        pass
    try:  # macOS fallback
        out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                             capture_output=True, text=True, timeout=5)
        return round(int(out.stdout.strip()) / 2**30, 1)
    except Exception:
        return 0.0


def benchmark(cores, throttle):
    """Capability score in kH/s: measured single-core SHA-256 rate × cores."""
    h = b"heaven-on-earth"
    t0 = time.perf_counter()
    for _ in range(300_000):
        h = hashlib.sha256(h).digest()
    khs = 300.0 / (time.perf_counter() - t0)
    return round(khs * cores * (1.0 - throttle), 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--join", required=True, help="coordinator URL, e.g. http://192.168.1.10:8940")
    ap.add_argument("--name", default=socket.gethostname().split(".")[0])
    ap.add_argument("--cores", type=int, default=multiprocessing.cpu_count())
    ap.add_argument("--throttle", type=float, default=0.0,
                    help="0..0.9 — give up this fraction of speed (politeness, or demo a weak machine)")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between asking for work")
    args = ap.parse_args()
    hub = args.join.rstrip("/")
    throttle = max(0.0, min(0.9, args.throttle))
    cores = max(1, args.cores)

    state_dir = Path.home() / ".heaven"
    state_dir.mkdir(exist_ok=True)
    state_file = state_dir / f"{args.name}.json"
    saved = json.loads(state_file.read_text()) if state_file.exists() else {}

    print(f"[{args.name}] benchmarking…")
    score = benchmark(cores, throttle)
    profile = {"node_id": saved.get("node_id"), "name": args.name,
               "os": platform.system(), "arch": platform.machine(),
               "cores": cores, "ram_gb": ram_gb(), "score": score}

    def join_grid():
        reg = http_json(f"{hub}/api/nodes/register", profile)
        profile["node_id"] = reg["node_id"]
        state_file.write_text(json.dumps({"node_id": reg["node_id"]}))
        return reg["node_id"]

    while True:  # keep trying until the coordinator exists
        try:
            node_id = join_grid()
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            print(f"[{args.name}] can't reach {hub} yet — retrying in 5s")
            time.sleep(5)

    print(f"[{args.name}] joined the grid — score {score} kH/s · {cores} cores · node {node_id}")

    while True:
        try:
            resp = http_json(f"{hub}/api/tasks/next?node_id={node_id}")
            if resp.get("reregister"):  # coordinator lost us (fresh db) — introduce ourselves again
                node_id = join_grid()
                continue
            chunk = resp.get("chunk")
            if not chunk:
                time.sleep(args.poll)
                continue
            t0 = time.perf_counter()
            result = tasks.execute(chunk["task_type"], chunk["params"], cores=cores)
            dt = time.perf_counter() - t0
            if throttle:
                time.sleep(dt * throttle / (1.0 - throttle))
                dt = time.perf_counter() - t0
            http_json(f"{hub}/api/tasks/result",
                      {"node_id": node_id, "chunk_id": chunk["chunk_id"],
                       "result": result, "elapsed": round(dt, 3)})
            print(f"[{args.name}] {chunk['task_type']} chunk done in {dt:.2f}s")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            print(f"[{args.name}] hub unreachable ({e}) — retrying in 5s")
            time.sleep(5)
        except KeyboardInterrupt:
            print(f"\n[{args.name}] leaving the grid. thanks for the cycles.")
            return


if __name__ == "__main__":
    main()
