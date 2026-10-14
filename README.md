# Point Room

Point Room is a small, real-time story point poker room for internal team estimation. A host creates a room, shares the six-character code or link, and everyone votes privately before the cards are revealed together.

Current application version: `26.10.4`, defined in [version.py](version.py). Versions use the `YY.M.BUILD` format: the first two segments identify the year and month, and the final segment must be incremented for every build in that month. Reset the build segment to `1` when the month changes.
CI enforces this rule with [scripts/check_version_bump.py](scripts/check_version_bump.py) and fails when code changes do not include a higher version.
The check compares the PR head against its base, or a pushed head against the
previous branch tip. On a newly pushed branch, GitHub supplies an all-zero previous
SHA; the check falls back to the default branch (`origin/HEAD`, then `master` or
`main`) unless it points at the commit being checked, then to the head commit's
parent. The repository's first commit has nothing to compare against, so the check
is skipped.

For the company deployment, support procedures, backups, updates, rollback, and incident checks, see [OPERATIONS.md](OPERATIONS.md).

## Run locally

The project uses a project-local virtual environment so the interpreter is consistent across VS Code, tests, and the server.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

HTTP is the default for local development and LAN testing when no certificate is available. HTTPS can be enabled later by setting `POINTROOM_REQUIRE_HTTPS=1` and providing a certificate, or by placing the app behind a trusted HTTPS reverse proxy.

## Run with Docker

Build the image from the project root:

```powershell
docker build --tag storypointpoker:local .
```

Start the app with a named volume so room sessions survive container recreation:

```powershell
docker volume create pointroom-data
docker run --detach --name storypointpoker --publish 5000:5000 --volume pointroom-data:/app/data storypointpoker:local
```

Open `http://localhost:5000`. The image uses a single threaded Gunicorn worker with `simple-websocket` for Socket.IO connections, uses HTTP, and binds to all container interfaces by default. The single worker is intentional because room state and event rate limits are currently process-local. For a production deployment, terminate HTTPS at a trusted reverse proxy and set `POINTROOM_TRUST_PROXY=1` and `PUBLIC_BASE_URL` as described below.

The container exposes `GET /health` for liveness checks. It returns HTTP `200` only when the application can access its SQLite database, and Docker uses the same endpoint for its health status. Application and audit logs are written as structured JSON to container stdout. Set `POINTROOM_LOG_LEVEL=WARNING` to reduce application log volume; keep the default `INFO` level when audit events are required.

### CORS and rate limiting

Socket.IO accepts same-origin browser connections by default. To allow a specific internal frontend origin or reverse-proxy hostname, set a comma-separated allowlist:

```powershell
$env:POINTROOM_ALLOWED_ORIGINS = "https://pointroom.company.example"
```

Rate limiting is enabled by default: room creation allows 10 requests per minute per client address, room joins allow 30 events per minute per client address, votes allow 60 events per minute per connection, and story suggestions allow 20 events per minute per connection. The in-memory limiters are appropriate for one application process. The current room state and Socket.IO event limiter are process-local, so use one application process until shared room state and event coordination are added. Container deployments run retention cleanup once per day; configure `POINTROOM_RETENTION_CLEANUP_INTERVAL_SECONDS` to change the interval.

### Runtime scaling and limit configuration

Point Room currently runs as a single process design. Keep one application container and one Gunicorn worker until shared room state and cross-process Socket.IO coordination are introduced.

- `POINTROOM_MAX_ACTIVE_ROOMS` (default `1000`) caps active rooms across the process.
- `POINTROOM_MAX_SOCKET_CONNECTIONS` (default `500`) caps concurrent Socket.IO connections in-process.
- `POINTROOM_SOCKET_RATE_MAX_KEYS` (default `10000`) caps tracked rate-limit keys for socket events.

These limits protect one process. Increasing them does not make the app horizontally scalable.

### Run a local load test

The load-test script supports two workloads:

- `separate-rooms` creates one room per simulated client and sends concurrent
  story updates, exercising independent room writes.
- `shared-room` joins all simulated clients to one room and sends concurrent
  votes, exercising broadcast fan-out. The shared-room workload is capped at
  50 clients to stay within the application's participant limit.

Both workloads report connection, join, and operation latency percentiles,
plus setup, workload, cleanup, and whole-run durations. Operation throughput
is calculated only over the synchronized workload phase; setup and cleanup
time are excluded. Created rooms are deleted when the run finishes; any room
that could not be deleted is reported for manual cleanup.

In one PowerShell window, start a local server with rate limiting disabled so
the configured safety limits do not obscure the performance measurements:

```powershell
$env:POINTROOM_RATE_LIMITING_ENABLED = "0"
python app.py
```

In another window, run a modest test first:

```powershell
python scripts\load_test.py --url http://127.0.0.1:5000 --clients 20 --iterations 10
```

To measure shared-room broadcast fan-out:

```powershell
python scripts\load_test.py --url http://127.0.0.1:5000 --workload shared-room --clients 20 --iterations 10
```

Increase `--clients` and `--iterations` gradually. The client count must not
exceed the server's configured socket connection limit, and the default limit
is 500. Run this against a local or dedicated test instance only, never a
production service. The reported operation latency covers the complete
Socket.IO handler, including room persistence and state broadcast; it is an
end-to-end indicator rather than a direct database-pool wait measurement.

## Share with coworkers on the same network

`127.0.0.1` is local to the computer that opens the link. Start Point Room with a LAN address as the public base URL:

```powershell
$env:POINTROOM_HOST = "0.0.0.0"
$env:POINTROOM_REQUIRE_HTTPS = "0"
$env:PUBLIC_BASE_URL = "http://192.168.1.25:5000"
& ".\.venv\Scripts\python.exe" app.py
```

Replace `192.168.1.25` with the host or VM's reachable IPv4 address. Then allow TCP port 5000 through the Windows firewall from an elevated PowerShell prompt:

```powershell
New-NetFirewallRule -DisplayName "PointRoom 5000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000 -Profile Private
```

Your coworker can then open `http://192.168.1.25:5000/room/CZNCEY`. Both computers must be on the same LAN or connected to the same VPN. For a VM, use its reachable VM IP and configure the hypervisor or cloud firewall to forward or allow TCP port 5000.

## Enable HTTPS later

When a certificate is available, enable HTTPS explicitly. The preferred direct mode uses a certificate and key issued by your company's trusted certificate authority:

```powershell
$env:POINTROOM_HOST = "0.0.0.0"
$env:POINTROOM_PORT = "5000"
$env:PUBLIC_BASE_URL = "https://pointroom.company.example:5000"
$env:POINTROOM_SSL_CERTFILE = "C:\PointRoom\certs\pointroom-cert.pem"
$env:POINTROOM_SSL_KEYFILE = "C:\PointRoom\certs\pointroom-key.pem"
& ".\.venv\Scripts\python.exe" app.py
```

Share links will use the configured `https://` address. The certificate must match the hostname in the URL and be trusted by coworkers' devices. The certificate and private key are excluded from source control.

For production, terminate TLS in IIS, Caddy, or Nginx and forward WebSocket traffic to PointRoom. Set `POINTROOM_TRUST_PROXY=1` only when the app is reachable through that trusted proxy, and set `PUBLIC_BASE_URL` to the external HTTPS address. The proxy must forward `X-Forwarded-Proto: https` so PointRoom can reject insecure requests.

If you start PointRoom without certificate files while HTTPS is enabled, it will stop with a configuration error. A self-signed certificate encrypts traffic but still shows a browser warning until its issuing certificate authority is trusted on every device. Keep the certificate and private key outside source control; `certs/`, `*.pem`, and `*.key` are ignored by Git.

## Persistent room sessions

Room snapshots are stored in `data/pointroom.sqlite3` and automatically reloaded when PointRoom restarts. The default retention is 30 days after the room's last activity. Activity includes joining, voting, revealing, resetting, changing the story, and managing the story queue. Expired rooms are removed during startup, as rooms are accessed, and by the daily container cleanup worker. The worker also migrates legacy snapshots that still contain cleartext host tokens.

You can configure the location and retention period before starting the server:

```powershell
$env:POINTROOM_DATABASE_PATH = "C:\PointRoom\data\pointroom.sqlite3"
$env:POINTROOM_RETENTION_DAYS = "30"
$env:POINTROOM_RETENTION_CLEANUP_ENABLED = "1"
$env:POINTROOM_RETENTION_CLEANUP_INTERVAL_SECONDS = "86400"
```

Back up the SQLite file while PointRoom is stopped or with a SQLite-aware backup process. Protect its file permissions and backups because it contains room codes, host tokens, participant names, stories, votes, and history. Run one PointRoom process for this deployment; multiple workers need coordinated shared storage and Socket.IO support such as Redis.

Participant credentials are kept in browser session storage so they are cleared when the tab session ends. The host credential is kept in browser profile storage with a 12-hour expiry so the host can recover across new tabs and short restarts while reducing long-lived token exposure. Host credentials are bearer tokens and are not included in the room link; clearing site data or closing an incognito session removes them, and a plain room link cannot safely restore host status across that boundary.

Run the one-shot maintenance command manually when needed:

```powershell
& ".\.venv\Scripts\python.exe" maintenance.py
```

It removes expired rooms and reports how many legacy snapshots were migrated. Container deployments run the same cleanup loop automatically when `POINTROOM_RETENTION_CLEANUP_ENABLED=1`.

## Azure DevOps work item links

Stories can reference work items such as `User Story 1234` or `Bug 42`. To turn those references into links, set the base URL of your Azure DevOps work item editor; the work item number is appended:

```powershell
$env:ADO_WORK_ITEM_BASE_URL = "https://dev.azure.com/<organization>/<project>/_workitems/edit/"
```

When it is not set, references are shown as plain text. Full Markdown links and plain URLs in stories are always clickable.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## How the room works

- The room host creates the room and can edit the current story, reveal cards, and start a new round.
- When creating a room, the host chooses Fibonacci, SAFe modified Fibonacci, or T-Shirt sizing cards; everyone in the room uses that deck. The host can change the deck before voting or after starting a fresh round.
- Teammates join with the room code or share link and choose a display name.
- Participants choose an avatar colour when they enter; colours already reserved in the room are hidden and the join form automatically chooses an available colour when needed. Returning participants default to their previous color but may choose another available color. In-room colour pickers hide colours reserved by other participants while keeping the current participant's colour available.
- The host can opt in to guest story suggestions. Guests can submit stories for review; the host can use one immediately, move it to the backlog, or dismiss it.
- Backlog stories stay available to the host until they are used or removed.
- Cards remain unavailable until the host shares a nonblank story.
- Before reveal, everyone sees only whether each person has submitted a card.
- After reveal, the selected cards, numeric average, and distribution are visible to everyone.
- Every story is kept in the session history when it is replaced. Revealed stories include each submitted vote and score; stories skipped before voting are recorded with no votes.
- Stories can only be added once per session. Starting a new round clears the current story so the host can add the next one; the previous story remains in history.
- Clicking your selected card again clears your vote; the table updates your status back to thinking.
- The host can remove an individual past story or delete the entire session. Both actions are persisted to SQLite and require confirmation.

Room sessions are persisted locally in SQLite for 30 days by default. Use a shared database and Socket.IO coordination such as Redis before running multiple workers.
