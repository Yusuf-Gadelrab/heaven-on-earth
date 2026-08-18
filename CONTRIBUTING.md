# Contributing

Thanks for helping build the commons. This project is deliberately tiny and deliberately strict about staying that way.

## Dev setup

```bash
git clone https://github.com/Yusuf-Gadelrab/heaven-on-earth && cd heaven-on-earth
python3 coordinator.py
```

That's the whole setup. No virtualenv, no `pip install`, no build step. Python 3.8+ and the standard library.

## The one hard rule: zero dependencies

**PRs adding dependencies to the core will be declined.** Not because dependencies are bad, but because "any computer running Python 3.8+ joins with one command" is the entire promise of this project. Every dependency is a machine that can't join.

## Adding a task type

Task types are the whitelist — the only code a worker will ever run — so they get the most scrutiny.

1. **Implement it in `tasks.py`.** Pure stdlib, pure function of its params.
2. **Register it in the `TASKS` dict** so the coordinator and workers know it exists.
3. **It must be deterministic given its params, and verifiable.** Same params → byte-identical result on every machine, every run. If it involves randomness, seed it from the params (see `monte_carlo_pi`). Redundancy verification compares results byte-for-byte; a nondeterministic task breaks the trust model.
4. **Add a test in `tests/test_tasks.py`** covering correctness and determinism.
5. **Document the params** — what each one means, its valid range, how the work splits into chunks.

## Before you submit a PR

```bash
python3 tests/test_tasks.py   # all tests pass
bash demo.sh                  # the end-to-end demo still runs clean
```

The demo is the integration test: coordinator + two workers (one throttled), π estimate, prime count with ×2 verification. If it runs and the shares print, the grid still works.

## Code style

- Standard library only.
- Comments only where the **why** isn't obvious from the code. No narrating the what.

## Not just code

Two other ways to contribute that matter as much:

- **Run a node** and report what breaks on your hardware/OS.
- **Ideas for task packs** — deterministic, verifiable, public-interest workloads the grid should learn to run. Open an issue.
