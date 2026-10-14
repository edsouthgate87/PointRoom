# Contributing to Point Room

## Development setup

Point Room needs Python 3.12 or later.

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

## Tests

```bash
python -m pytest -q
```

PostgreSQL-backed tests are skipped unless `POINTROOM_TEST_DATABASE_URL` points
at a disposable PostgreSQL database.

## Versioning

The application version lives in [version.py](version.py) and uses the
`YY.M.BUILD` format. The first two segments are the year and month; the build
number increases with every build in that month and resets to `1` when the
month changes. Keep the version line in [README.md](README.md) in sync.

CI enforces this with
[scripts/check_version_bump.py](scripts/check_version_bump.py): any change to
code must raise the version. Markdown, text, `.github/`, and `docs/` changes
are exempt.

The check compares a pull request's head against its base, or a pushed head
against the previous branch tip. For a newly pushed branch, GitHub supplies an
all-zero previous SHA; the check then falls back to the default branch
(`origin/HEAD`, then `master` or `main`) unless it points at the commit being
checked, and finally to the head commit's parent. The repository's first
commit has nothing to compare against, so the check is skipped.

## Load testing

[scripts/load_test.py](scripts/load_test.py) supports two workloads:

- `separate-rooms` creates one room per simulated client and sends concurrent
  story updates, exercising independent room writes.
- `shared-room` joins all simulated clients to one room and sends concurrent
  votes, exercising broadcast fan-out. It is capped at 50 clients, the room's
  participant limit.

Both report connection, join, and operation latency percentiles plus phase
durations. Throughput covers only the synchronized workload phase. Rooms
created by the test are deleted at the end; any that could not be deleted are
reported.

Start a local server with rate limiting disabled so the limits do not skew the
results:

```bash
POINTROOM_RATE_LIMITING_ENABLED=0 python app.py
```

On Windows PowerShell, set `$env:POINTROOM_RATE_LIMITING_ENABLED = "0"` first.
Then, in another terminal:

```bash
python scripts/load_test.py --url http://127.0.0.1:5000 --clients 20 --iterations 10
python scripts/load_test.py --url http://127.0.0.1:5000 --workload shared-room --clients 20 --iterations 10
```

Increase `--clients` and `--iterations` gradually; the client count must stay
within `POINTROOM_MAX_SOCKET_CONNECTIONS` (default `500`). Only run load tests
against a local or dedicated test instance. Reported operation latency covers
the full Socket.IO handler, including persistence and the state broadcast.
