#!/usr/bin/env python3
"""Task-library tests. Run:  python3 tests/test_tasks.py  (no test framework needed)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import tasks  # noqa: E402


def main():
    # π must be bit-identical regardless of core count — redundancy verification depends on it
    r1 = tasks.execute("monte_carlo_pi", {"samples": 200_000, "seed": 7}, cores=1)
    r2 = tasks.execute("monte_carlo_pi", {"samples": 200_000, "seed": 7}, cores=4)
    assert r1 == r2, f"π not deterministic across core counts: {r1} vs {r2}"
    est = 4 * r1["inside"] / r1["samples"]
    assert abs(est - 3.14159) < 0.02, f"π estimate off: {est}"

    r = tasks.execute("prime_count", {"start": 2, "end": 1_000_000}, cores=4)
    assert r["count"] == 78_498, f"π(10^6) should be 78,498, got {r['count']}"
    assert r["largest"] == 999_983, f"largest prime below 10^6 is 999,983, got {r['largest']}"

    r = tasks.execute("prime_count", {"start": 10, "end": 30}, cores=1)
    assert r["count"] == 6 and r["largest"] == 29, f"primes in [10,30): {r}"

    r = tasks.execute("prime_count", {"start": 5, "end": 5}, cores=1)
    assert r == {"count": 0, "largest": None}, f"empty range: {r}"

    try:
        tasks.execute("curl_evil_payload", {}, cores=1)
        raise SystemExit("FAIL: unknown task type must be refused")
    except ValueError:
        pass

    bad = tasks.execute("research_fetch", {"url": "file:///etc/passwd", "query": "x"}, cores=1)
    assert bad["status"] == "error", "non-http(s) URLs must be refused"

    print("all task tests pass ✓")


# the guard matters: on Windows multiprocessing re-imports this module in child processes
if __name__ == "__main__":
    main()
