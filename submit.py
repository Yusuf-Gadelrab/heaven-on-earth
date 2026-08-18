#!/usr/bin/env python3
"""Give the grid work and watch it finish. Zero dependencies.

    python3 submit.py pi --samples 10000000 --watch
    python3 submit.py primes --end 2000000 --redundancy 2 --watch
    python3 submit.py research --query "community fridges" --urls https://a.com,https://b.org --watch
    python3 submit.py watch <job-id>
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _call(hub, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(hub + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"error: {e.read().decode()[:300]}")
    except (urllib.error.URLError, OSError):
        sys.exit(f"error: no coordinator at {hub} — start one with: python3 coordinator.py")


def watch(hub, jid):
    while True:
        j = _call(hub, f"/api/jobs/{jid}")
        bar = "█" * int(j["progress"] * 28)
        sys.stdout.write(f"\r  [{bar:<28}] {j['progress'] * 100:5.1f}%  {j['status']}   ")
        sys.stdout.flush()
        if j["status"] == "done":
            print()
            r = j["result"]
            if "pi_estimate" in r:
                print(f"  π ≈ {r['pi_estimate']}   ({r['samples']:,} samples, {j['elapsed_s']}s)")
            elif "prime_count" in r:
                print(f"  {r['prime_count']:,} primes · largest {r['largest_prime']:,}"
                      f"   ({j['elapsed_s']}s)")
            else:
                for p in r["pages"]:
                    print(f"  · {p.get('title') or p['url']}")
                    for m in p.get("matches", [])[:3]:
                        print(f"      – {m}")
                    if p.get("status") == "error":
                        print(f"      (unreachable: {p.get('error')})")
            if j.get("by_node"):
                print("  — who did the work —")
                for n in j["by_node"]:
                    print(f"  {n['name']:<14} {n['chunks']:>3} chunks   {n['share'] * 100:5.1f}%")
            return
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hub", default="http://127.0.0.1:8940")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("pi", help="estimate π by seeded Monte Carlo")
    pi.add_argument("--samples", type=int, default=4_000_000)
    pi.add_argument("--redundancy", type=int, default=1)
    pi.add_argument("--watch", action="store_true")

    pr = sub.add_parser("primes", help="count primes in a range")
    pr.add_argument("--start", type=int, default=2)
    pr.add_argument("--end", type=int, default=2_000_000)
    pr.add_argument("--redundancy", type=int, default=1)
    pr.add_argument("--watch", action="store_true")

    rs = sub.add_parser("research", help="read many pages in parallel across the grid")
    rs.add_argument("--query", required=True)
    rs.add_argument("--urls", required=True, help="comma-separated URLs")
    rs.add_argument("--watch", action="store_true")

    w = sub.add_parser("watch", help="watch an existing job")
    w.add_argument("job_id")

    args = ap.parse_args()
    if args.cmd == "watch":
        return watch(args.hub, args.job_id)
    if args.cmd == "pi":
        body = {"task_type": "monte_carlo_pi", "params": {"samples": args.samples},
                "redundancy": args.redundancy}
    elif args.cmd == "primes":
        body = {"task_type": "prime_count",
                "params": {"start": args.start, "end": args.end},
                "redundancy": args.redundancy}
    else:
        body = {"task_type": "research_fetch",
                "params": {"query": args.query,
                           "urls": [u.strip() for u in args.urls.split(",") if u.strip()]}}
    job = _call(args.hub, "/api/jobs", body)
    print(f"  job {job['id']} submitted")
    if getattr(args, "watch", False):
        watch(args.hub, job["id"])


if __name__ == "__main__":
    main()
