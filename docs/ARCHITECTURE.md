# Architecture

Heaven on Earth is deliberately small: three entry points, one whitelist, one SQLite file, zero dependencies. Everything below is Python 3.8+ standard library.

## Components

| File | Role |
|---|---|
| `coordinator.py` | The hub. `ThreadingHTTPServer` + SQLite. Owns all state, serves the HTTP API, the Mission Control dashboard (`/`, port 8940), and The Good Map (`/good`). |
| `worker.py` | The volunteer node. Benchmarks itself on start (SHA-256 hashing rate × cores → capability score in kH/s), registers, then pulls work in a loop. Identity persists in `~/.heaven/<name>.json`. |
| `submit.py` | CLI client. Creates jobs, polls job status, renders a live progress bar, prints the answer and each node's share of the work on completion. |
| `tasks.py` | The complete whitelist of executable work. A worker can only ever run functions defined here — nothing is ever downloaded or executed from the network. |

The coordinator is the only stateful process. Workers and `submit.py` are plain HTTP clients; they can die and return at any time without corrupting anything.

## HTTP API surface

All state changes flow through the coordinator's API:

| Endpoint | Purpose |
|---|---|
| `POST /api/nodes/register` | A worker announces itself: name, capability score (kH/s), cores. Returns/confirms its `node_id`. |
| `GET /api/tasks/next?node_id=` | The pull loop. The coordinator cuts a chunk sized for this node (see sizing below) and assigns it, or returns "no work." |
| `POST /api/tasks/result` | A worker submits a completed chunk's result. |
| `POST /api/jobs` | `submit.py` creates a job: task type, params, redundancy. |
| `GET /api/jobs/<id>` | Job status: progress, per-node work shares, and the final answer when done. |
| `GET /api/stats` | Grid-wide stats for the dashboard: online nodes, scores, throughput, job list. |
| `GET /api/good` · `POST /api/good` | Read and add Good Map pins. |

## Chunk lifecycle

```
            (claim time)
  cut ────────► assigned ────────► done ──► accepted
                   │                          │
                   │ 180s timeout             │ redundancy mismatch
                   ▼                          ▼
               reclaimed ◄───────────────── invalid
                   │
                   └──────► reissued (back into the pool)
```

- Chunks do not exist ahead of time — they are **cut at claim time** from the job's remaining work, sized for the node that asked.
- An assigned chunk that produces no result within **180 seconds** is reclaimed and reissued. A node dying mid-chunk never stalls a job.
- `done` becomes `accepted` directly (redundancy 1) or after verification (redundancy ≥ 2, below).

## Claim-time sizing

When a node asks for work, the coordinator sizes its chunk by how good that node actually is, relative to the grid right now:

```
multiplier = clamp(node_score / mean(scores of online nodes), 0.25, 4.0)
chunk_size = base_unit × multiplier
```

- `node_score` is the worker's self-benchmark (SHA-256 kH/s × cores) from registration.
- The clamp (0.25×–4×) keeps one extreme machine from monopolizing a job or one weak machine from receiving a sliver so small the HTTP round trip dominates.
- Because scheduling is pull-based, sizing only has to be roughly right: a fast machine that finishes early simply asks again sooner. The ratio sets the slice; the pull loop does the fine-grained balancing.

## Redundancy state machine

A job submitted with `--redundancy 2` verifies every unit by having it computed twice:

```
unit needs 2 replicas
        │
        ▼
replica A assigned ──► replica B assigned (different node when possible)
        │                       │
        └───────► both done ◄───┘
                     │
        byte-for-byte compare
              │            │
           match        mismatch
              │            │
              ▼            ▼
          accepted    both discarded,
                      unit reissued
```

- Comparison is **byte-for-byte** on the serialized result. This works because every task in `tasks.py` is deterministic given its params — even the Monte Carlo tasks are seeded.
- The coordinator prefers assigning replicas to **different** nodes. On a single-node grid that's impossible, so it falls back to self-verification (the same node computes both replicas) rather than deadlocking the job.
- On mismatch there is no arbitration in v0.1 — both results are thrown away and the unit re-enters the pool. Node reputation and majority-of-3 arbitration are on the roadmap.

## SQLite schema (high level)

One database file, four core tables:

- **`nodes`** — one row per registered worker: id, name, capability score, cores, last-seen. Backs the dashboard roster and the online-average used in sizing.
- **`jobs`** — one row per submitted job: task type, params, redundancy, status, and the merged final result.
- **`chunks`** — the unit of scheduling: which job, which slice of the work, current lifecycle state, which node holds it, timestamps (for the 180s reclaim), and the submitted result for verification.
- **`good`** — Good Map pins: coordinates, category (food share, community fridge, little free library, cleanup crew, water station, act of kindness), description, and a sample-data flag for the shipped San Jose pins.

SQLite's single-writer model is a fit here: the coordinator is the only writer, `ThreadingHTTPServer` gives cheap concurrency for the read-heavy API, and the whole grid's state is one file you can back up with `cp`.

## Why pull, not push

Workers ask for work; the coordinator never pushes it. This one choice buys most of the system's simplicity:

- **Self-scheduling is natural load balancing.** Fast machines come back more often and automatically do more. There is no scheduler queue to tune, no placement algorithm to get wrong.
- **Failure handling is trivial.** A dead node just stops asking. Its outstanding chunks hit the 180s timeout and get reissued. No heartbeat protocol, no failover logic.
- **The coordinator stays dumb and stateless-per-request** — it answers questions about a SQLite file. That's the whole hub.

The cost is polling latency (a chunk can sit unclaimed for up to one poll interval), which is negligible at the chunk sizes this grid deals in.

## Security model, restated

The only code a worker executes is the code it shipped with. `tasks.py` is a closed whitelist; the API carries **parameters, never code**. New task types enter through pull requests — reviewed, deterministic, verifiable — not through the network. This is the line between a computing commons and a botnet, and it is not negotiable.
