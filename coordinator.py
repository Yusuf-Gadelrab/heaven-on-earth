#!/usr/bin/env python3
"""Heaven on Earth — coordinator.

Registers volunteer nodes, cuts work into chunks sized to each node's measured
capability, verifies redundant results, aggregates answers, and serves the
Mission Control dashboard plus The Good Map. Zero dependencies — stdlib only.

Run:  python3 coordinator.py [--port 8940] [--db heaven.db]
"""
import argparse
import json
import socket
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DB_PATH = ROOT / "heaven.db"

CHUNK_TIMEOUT_S = 180   # a node that goes dark forfeits its chunk; work is reissued
OFFLINE_AFTER_S = 30
TARGET_CHUNKS = 16      # ~16 base units per job so a slow node can never stall a job
MIN_UNIT = {"monte_carlo_pi": 50_000, "prime_count": 20_000}
DIVISIBLE = ("monte_carlo_pi", "prime_count")

_lock = threading.Lock()  # serializes claim/accept so two polls can't grab the same unit

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  id TEXT PRIMARY KEY, name TEXT, os TEXT, arch TEXT, cores INT, ram_gb REAL,
  score REAL, chunks_done INT DEFAULT 0, work_units REAL DEFAULT 0,
  last_seen REAL, created REAL);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, task_type TEXT, params TEXT, redundancy INT DEFAULT 1,
  status TEXT DEFAULT 'running', cursor REAL DEFAULT 0, total REAL,
  done_work REAL DEFAULT 0, created REAL, finished REAL, result TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY, job_id TEXT, unit_key TEXT, params TEXT, size REAL,
  status TEXT DEFAULT 'assigned', node_id TEXT, assigned_at REAL,
  result TEXT, elapsed REAL);
CREATE TABLE IF NOT EXISTS good(
  id TEXT PRIMARY KEY, lat REAL, lng REAL, category TEXT, title TEXT,
  note TEXT, created REAL);
"""

GOOD_CATEGORIES = {
    "food-share", "community-fridge", "little-library",
    "cleanup", "water-station", "kindness",
}

SAMPLE_DEEDS = [
    (37.3352, -121.8811, "food-share", "Canned food shelf outside the library (sample)",
     "Someone leaves a row of cans on the ledge every Sunday — take what you need."),
    (37.3318, -121.8863, "community-fridge", "Community fridge on 4th St (sample)",
     "Plugged in, stocked by neighbors, open 24/7."),
    (37.3230, -121.9152, "little-library", "Little Free Library at the Rose Garden (sample)",
     "Take a book, leave a book."),
    (37.3387, -121.8853, "water-station", "Water jug and cups outside the corner store (sample)",
     "The owner refills it on hot days."),
    (37.3163, -121.8846, "cleanup", "Saturday creek cleanup crew (sample)",
     "Shows up with trash grabbers every other weekend."),
]


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------- nodes

def register(p):
    now = time.time()
    nid = p.get("node_id") or uuid.uuid4().hex[:12]
    conn = connect()
    with conn:
        conn.execute(
            """INSERT INTO nodes(id,name,os,arch,cores,ram_gb,score,last_seen,created)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, os=excluded.os,
                 arch=excluded.arch, cores=excluded.cores, ram_gb=excluded.ram_gb,
                 score=excluded.score, last_seen=excluded.last_seen""",
            (nid, str(p.get("name", "node"))[:40], str(p.get("os", ""))[:20],
             str(p.get("arch", ""))[:20], int(p.get("cores") or 1),
             float(p.get("ram_gb") or 0), max(1.0, float(p.get("score") or 1)), now, now),
        )
    return {"node_id": nid}


# ---------------------------------------------------------------- jobs

def create_job(p):
    task_type = p.get("task_type")
    params = p.get("params") or {}
    redundancy = max(1, min(3, int(p.get("redundancy") or 1)))
    now = time.time()
    jid = uuid.uuid4().hex[:12]
    if task_type == "monte_carlo_pi":
        total = float(int(params["samples"]))
        if total <= 0:
            raise ValueError("samples must be > 0")
    elif task_type == "prime_count":
        params = {"start": int(params.get("start", 2)), "end": int(params["end"])}
        total = float(params["end"] - params["start"])
        if total <= 0:
            raise ValueError("end must be > start")
    elif task_type == "research_fetch":
        urls = [u.strip() for u in params.get("urls", []) if u.strip()][:50]
        if not urls:
            raise ValueError("urls required")
        params = {"urls": urls, "query": str(params.get("query", ""))[:300]}
        total = float(len(urls))
        redundancy = 1  # live pages aren't deterministic; refetch equality would false-fail
    else:
        raise ValueError(f"unknown task_type: {task_type}")
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO jobs(id,task_type,params,redundancy,total,created) VALUES(?,?,?,?,?,?)",
            (jid, task_type, json.dumps(params), redundancy, total, now),
        )
        if task_type == "research_fetch":
            for i, url in enumerate(params["urls"]):
                conn.execute(
                    "INSERT INTO chunks(id,job_id,unit_key,params,size,status) VALUES(?,?,?,?,1,'pending')",
                    (uuid.uuid4().hex[:12], jid, f"url{i:04d}",
                     json.dumps({"url": url, "query": params["query"]})),
                )
    return {"id": jid}


def claim_chunk(node):
    now = time.time()
    conn = connect()
    with _lock, conn:
        conn.execute(
            "UPDATE chunks SET status='pending', node_id=NULL WHERE status='assigned' AND assigned_at < ?",
            (now - CHUNK_TIMEOUT_S,),
        )
        # 1) pending unit whose sibling wasn't computed by this same node (redundancy
        #    twins should land on different machines) …
        row = conn.execute(
            """SELECT c.*, j.task_type FROM chunks c JOIN jobs j ON j.id = c.job_id
               WHERE c.status='pending' AND j.status='running'
                 AND NOT EXISTS (SELECT 1 FROM chunks c2 WHERE c2.job_id = c.job_id
                                 AND c2.unit_key = c.unit_key AND c2.id != c.id AND c2.node_id = ?)
               ORDER BY j.created, c.rowid LIMIT 1""",
            (node["id"],),
        ).fetchone()
        # … 2) but a 1-node grid self-verifies rather than deadlocking
        if not row:
            row = conn.execute(
                """SELECT c.*, j.task_type FROM chunks c JOIN jobs j ON j.id = c.job_id
                   WHERE c.status='pending' AND j.status='running'
                   ORDER BY j.created, c.rowid LIMIT 1"""
            ).fetchone()
        if row:
            conn.execute(
                "UPDATE chunks SET status='assigned', node_id=?, assigned_at=? WHERE id=?",
                (node["id"], now, row["id"]),
            )
            return {"chunk_id": row["id"], "job_id": row["job_id"],
                    "task_type": row["task_type"], "params": json.loads(row["params"])}
        # 3) cut a fresh unit from a divisible job, sized to this node's capability
        job = conn.execute(
            "SELECT * FROM jobs WHERE status='running' AND cursor < total "
            "AND task_type IN (?,?) ORDER BY created LIMIT 1", DIVISIBLE,
        ).fetchone()
        if not job:
            return None
        avg = conn.execute(
            "SELECT AVG(score) a FROM nodes WHERE last_seen > ?", (now - OFFLINE_AFTER_S,)
        ).fetchone()["a"] or node["score"] or 1.0
        factor = max(0.25, min(4.0, (node["score"] or avg) / avg))
        size = max(MIN_UNIT.get(job["task_type"], 1), int(job["total"] / TARGET_CHUNKS * factor))
        size = min(size, int(job["total"] - job["cursor"]))
        jparams = json.loads(job["params"])
        if job["task_type"] == "monte_carlo_pi":
            cparams = {"samples": size, "seed": int(job["cursor"])}
        else:
            lo = int(jparams["start"] + job["cursor"])
            cparams = {"start": lo, "end": lo + size}
        unit_key = f"{int(job['cursor'])}+{size}"
        conn.execute("UPDATE jobs SET cursor = cursor + ? WHERE id=?", (size, job["id"]))
        cid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO chunks(id,job_id,unit_key,params,size,status,node_id,assigned_at) "
            "VALUES(?,?,?,?,?,'assigned',?,?)",
            (cid, job["id"], unit_key, json.dumps(cparams), size, node["id"], now),
        )
        for _ in range(job["redundancy"] - 1):
            conn.execute(
                "INSERT INTO chunks(id,job_id,unit_key,params,size,status) VALUES(?,?,?,?,?,'pending')",
                (uuid.uuid4().hex[:12], job["id"], unit_key, json.dumps(cparams), size),
            )
        return {"chunk_id": cid, "job_id": job["id"],
                "task_type": job["task_type"], "params": cparams}


def next_task(node_id):
    now = time.time()
    conn = connect()
    with conn:
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            return {"chunk": None, "reregister": True}
        conn.execute("UPDATE nodes SET last_seen=? WHERE id=?", (now, node_id))
    return {"chunk": claim_chunk(dict(node))}


def submit_result(p):
    node_id = p["node_id"]
    chunk_id = p["chunk_id"]
    result = p["result"]
    elapsed = float(p.get("elapsed") or 0)
    now = time.time()
    conn = connect()
    with _lock, conn:
        c = conn.execute(
            "SELECT c.*, j.redundancy, j.total AS job_total FROM chunks c "
            "JOIN jobs j ON j.id = c.job_id WHERE c.id=?", (chunk_id,),
        ).fetchone()
        if not c or c["status"] not in ("assigned", "pending"):
            return {"ok": False, "reason": "stale"}  # late duplicate after a timeout reissue
        conn.execute(
            "UPDATE chunks SET status='done', result=?, elapsed=?, node_id=? WHERE id=?",
            (json.dumps(result, sort_keys=True), elapsed, node_id, chunk_id),
        )
        conn.execute(
            "UPDATE nodes SET chunks_done = chunks_done + 1, last_seen=? WHERE id=?",
            (now, node_id),
        )
        twins = conn.execute(
            "SELECT * FROM chunks WHERE job_id=? AND unit_key=?", (c["job_id"], c["unit_key"]),
        ).fetchall()
        done_rows = [t for t in twins if t["status"] == "done"]
        if len(done_rows) >= c["redundancy"]:
            if len({t["result"] for t in done_rows}) == 1:
                frac = (c["size"] or 1) / c["job_total"]
                for t in done_rows:
                    conn.execute("UPDATE chunks SET status='accepted' WHERE id=?", (t["id"],))
                    conn.execute(
                        "UPDATE nodes SET work_units = work_units + ? WHERE id=?",
                        (frac, t["node_id"]),
                    )
                conn.execute(
                    "UPDATE jobs SET done_work = done_work + ? WHERE id=?",
                    (c["size"], c["job_id"]),
                )
            else:
                # disagreement: throw away both answers, put the unit back in the pool
                for t in done_rows:
                    conn.execute("UPDATE chunks SET status='invalid' WHERE id=?", (t["id"],))
                for _ in range(c["redundancy"]):
                    conn.execute(
                        "INSERT INTO chunks(id,job_id,unit_key,params,size,status) VALUES(?,?,?,?,?,'pending')",
                        (uuid.uuid4().hex[:12], c["job_id"], c["unit_key"], c["params"], c["size"]),
                    )
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (c["job_id"],)).fetchone()
        if job["status"] == "running" and job["done_work"] >= job["total"]:
            _finish_job(conn, job)
    return {"ok": True}


def _finish_job(conn, job):
    rows = conn.execute(
        "SELECT * FROM chunks WHERE job_id=? AND status='accepted' ORDER BY unit_key", (job["id"],)
    ).fetchall()
    units = {}
    for r in rows:  # redundancy twins hold identical results; count each unit once
        units.setdefault(r["unit_key"], r)
    results = [json.loads(u["result"]) for u in units.values()]
    t = job["task_type"]
    if t == "monte_carlo_pi":
        inside = sum(r["inside"] for r in results)
        samples = sum(r["samples"] for r in results)
        agg = {"pi_estimate": round(4.0 * inside / samples, 6), "samples": samples}
    elif t == "prime_count":
        agg = {"prime_count": sum(r["count"] for r in results),
               "largest_prime": max((r["largest"] for r in results if r.get("largest")), default=None)}
    else:
        agg = {"pages": results}
    conn.execute(
        "UPDATE jobs SET status='done', finished=?, result=? WHERE id=?",
        (time.time(), json.dumps(agg), job["id"]),
    )


# ---------------------------------------------------------------- read views

def _job_row(j, now):
    return {
        "id": j["id"], "task_type": j["task_type"], "status": j["status"],
        "progress": min(1.0, (j["done_work"] or 0) / j["total"]) if j["total"] else 0,
        "created": j["created"], "redundancy": j["redundancy"],
        "elapsed_s": round((j["finished"] or now) - j["created"], 1),
        "result": json.loads(j["result"]) if j["result"] else None,
    }


def stats():
    now = time.time()
    conn = connect()
    total_work = conn.execute("SELECT SUM(work_units) w FROM nodes").fetchone()["w"] or 0
    nodes, grid_score, online = [], 0.0, 0
    for n in conn.execute("SELECT * FROM nodes ORDER BY score DESC"):
        is_on = (now - (n["last_seen"] or 0)) < OFFLINE_AFTER_S
        if is_on:
            grid_score += n["score"] or 0
            online += 1
        nodes.append({
            "id": n["id"], "name": n["name"], "os": n["os"], "arch": n["arch"],
            "cores": n["cores"], "ram_gb": n["ram_gb"], "score": round(n["score"] or 0, 1),
            "status": "online" if is_on else "offline", "chunks_done": n["chunks_done"],
            "work_share": round((n["work_units"] or 0) / total_work, 4) if total_work else 0,
            "last_seen_s": int(now - (n["last_seen"] or now)),
        })
    jobs = [_job_row(j, now) for j in
            conn.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 20")]
    chunks_done = conn.execute(
        "SELECT COUNT(*) c FROM chunks WHERE status IN ('done','accepted')").fetchone()["c"]
    deeds = conn.execute("SELECT COUNT(*) c FROM good").fetchone()["c"]
    return {
        "grid": {"nodes_online": online, "nodes_total": len(nodes),
                 "grid_score": round(grid_score, 1), "chunks_done": chunks_done,
                 "good_deeds": deeds},
        "nodes": nodes, "jobs": jobs,
    }


def job_detail(jid):
    now = time.time()
    conn = connect()
    j = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not j:
        return None
    out = _job_row(j, now)
    out["params"] = json.loads(j["params"])
    rows = conn.execute(
        """SELECT n.name, COUNT(*) c, SUM(ch.size) w FROM chunks ch
           JOIN nodes n ON n.id = ch.node_id
           WHERE ch.job_id=? AND ch.status IN ('done','accepted')
           GROUP BY n.name ORDER BY w DESC""", (jid,),
    ).fetchall()
    total_w = sum(r["w"] or 0 for r in rows) or 1
    out["by_node"] = [{"name": r["name"], "chunks": r["c"],
                       "share": round((r["w"] or 0) / total_w, 4)} for r in rows]
    return out


# ---------------------------------------------------------------- good map

def list_good():
    conn = connect()
    return [dict(r) for r in conn.execute("SELECT * FROM good ORDER BY created DESC")]


def good_add(p):
    lat, lng = float(p["lat"]), float(p["lng"])
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("bad coordinates")
    cat = p.get("category", "kindness")
    if cat not in GOOD_CATEGORIES:
        raise ValueError("bad category")
    title = str(p.get("title", "")).strip()[:120]
    if not title:
        raise ValueError("title required")
    note = str(p.get("note", "")).strip()[:500]
    gid = uuid.uuid4().hex[:12]
    conn = connect()
    with conn:
        conn.execute("INSERT INTO good VALUES(?,?,?,?,?,?,?)",
                     (gid, lat, lng, cat, title, note, time.time()))
    return {"id": gid}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "HeavenOnEarth/0.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name):
        p = WEB / name
        if not p.exists():
            return self._json({"error": f"web/{name} missing"}, 404)
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        try:
            path, _, query = self.path.partition("?")
            if path == "/":
                return self._file("index.html")
            if path == "/good":
                return self._file("good.html")
            if path == "/api/stats":
                return self._json(stats())
            if path == "/api/good":
                return self._json({"deeds": list_good()})
            if path == "/api/tasks/next":
                node_id = (parse_qs(query).get("node_id") or [""])[0]
                return self._json(next_task(node_id))
            if path.startswith("/api/jobs/"):
                detail = job_detail(path.rsplit("/", 1)[1])
                return self._json(detail or {"error": "not found"}, 200 if detail else 404)
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)[:300]}, 400)

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/api/nodes/register":
                return self._json(register(body))
            if self.path == "/api/tasks/result":
                return self._json(submit_result(body))
            if self.path == "/api/jobs":
                return self._json(create_job(body))
            if self.path == "/api/good":
                return self._json(good_add(body))
            self._json({"error": "not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self._json({"error": str(e)[:300]}, 400)
        except Exception as e:
            self._json({"error": str(e)[:300]}, 500)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    global DB_PATH
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8940)
    ap.add_argument("--db", default=str(ROOT / "heaven.db"))
    args = ap.parse_args()
    DB_PATH = Path(args.db)
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) c FROM good").fetchone()["c"] == 0:
        now = time.time()
        with conn:
            for lat, lng, cat, title, note in SAMPLE_DEEDS:
                conn.execute("INSERT INTO good VALUES(?,?,?,?,?,?,?)",
                             (uuid.uuid4().hex[:12], lat, lng, cat, title, note, now))
    conn.close()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    ip = lan_ip()
    print(f"""
  ☁  HEAVEN ON EARTH — coordinator up
     Mission Control   http://localhost:{args.port}
     The Good Map      http://localhost:{args.port}/good
     Join from any machine:
       python3 worker.py --join http://{ip}:{args.port}
""")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  coordinator down — the grid rests.")


if __name__ == "__main__":
    main()
