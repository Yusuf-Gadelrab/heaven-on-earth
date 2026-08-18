"""Whitelisted task implementations — the ONLY code a worker will ever run.

Heaven on Earth never downloads or executes arbitrary code from the network;
a worker can only run what ships in this file. New task types arrive by pull
request and must be deterministic given their params, so redundant results
from two different machines can be compared byte-for-byte.
"""
import math
import multiprocessing
import os
import random
import re
import urllib.request

# fork is much cheaper than spawn per-chunk; our worker is single-threaded so it's safe
CTX = multiprocessing.get_context("fork") if hasattr(os, "fork") else multiprocessing.get_context()

# fixed sub-split so the result is identical no matter how many cores a machine has
SUBPARTS = 8


def _pi_part(arg):
    n, seed = arg
    rnd = random.Random(seed)
    inside = 0
    for _ in range(n):
        x = rnd.random()
        y = rnd.random()
        if x * x + y * y <= 1.0:
            inside += 1
    return inside


def monte_carlo_pi(params, cores=1):
    n = int(params["samples"])
    seed = int(params.get("seed", 0))
    sizes = [n // SUBPARTS] * SUBPARTS
    sizes[0] += n - sum(sizes)
    args = [(s, seed * SUBPARTS + i) for i, s in enumerate(sizes)]
    procs = max(1, min(cores, SUBPARTS))
    if procs == 1:
        inside = sum(_pi_part(a) for a in args)
    else:
        with CTX.Pool(procs) as pool:
            inside = sum(pool.map(_pi_part, args))
    return {"inside": inside, "samples": n}


def _small_primes(limit):
    if limit < 2:
        return []
    sieve = bytearray(limit + 1)
    primes = []
    for p in range(2, limit + 1):
        if not sieve[p]:
            primes.append(p)
            for m in range(p * p, limit + 1, p):
                sieve[m] = 1
    return primes


def _seg_count(arg):
    lo, hi, base = arg
    size = hi - lo
    mark = bytearray(size)
    for p in base:
        start = max(p * p, ((lo + p - 1) // p) * p)
        for m in range(start, hi, p):
            mark[m - lo] = 1
    count = 0
    largest = None
    for i in range(size):
        if not mark[i] and lo + i >= 2:
            count += 1
            largest = lo + i
    return count, largest


def prime_count(params, cores=1):
    start = max(int(params["start"]), 2)
    end = int(params["end"])
    if start >= end:
        return {"count": 0, "largest": None}
    base = _small_primes(math.isqrt(end - 1))
    step = max(1, (end - start + SUBPARTS - 1) // SUBPARTS)
    segs = [(lo, min(lo + step, end), base) for lo in range(start, end, step)]
    procs = max(1, min(cores, len(segs)))
    if procs == 1:
        parts = [_seg_count(s) for s in segs]
    else:
        with CTX.Pool(procs) as pool:
            parts = pool.map(_seg_count, segs)
    count = sum(c for c, _ in parts)
    largest = max((l for _, l in parts if l is not None), default=None)
    return {"count": count, "largest": largest}


_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def research_fetch(params, cores=1):
    import html as html_mod

    url = params["url"]
    query = params.get("query", "")
    if not url.lower().startswith(("http://", "https://")):
        return {"url": url, "status": "error", "error": "only http(s) urls allowed"}
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "HeavenOnEarth/0.1 (+volunteer research grid)"}
        )
        raw = urllib.request.urlopen(req, timeout=20).read(800_000)
    except Exception as e:  # any network failure becomes a reportable result, not a crash
        return {"url": url, "status": "error", "error": str(e)[:200]}
    text = raw.decode("utf-8", "replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    title = html_mod.unescape(re.sub(r"\s+", " ", m.group(1)).strip())[:200] if m else ""
    body = _TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", text))
    body = re.sub(r"\s+", " ", html_mod.unescape(body))
    sentences = re.split(r"(?<=[.!?]) +", body)
    terms = [t for t in query.casefold().split() if len(t) > 2]
    matches = []
    if terms:
        matches = [s.strip()[:300] for s in sentences if any(t in s.casefold() for t in terms)][:5]
    return {"url": url, "status": "ok", "title": title, "matches": matches, "chars_scanned": len(body)}


TASKS = {
    "monte_carlo_pi": monte_carlo_pi,
    "prime_count": prime_count,
    "research_fetch": research_fetch,
}


def execute(task_type, params, cores=1):
    fn = TASKS.get(task_type)
    if fn is None:
        raise ValueError(f"refusing unknown task type: {task_type}")
    return fn(params, cores=cores)
