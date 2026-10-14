# Point Room Operations Runbook

This runbook describes the current single-container deployment of Point Room.
It is intended for the service owner and the IS team supporting the host.
The commands below use the SQLite-backed single-container deployment; the
PostgreSQL Docker Compose deployment has its own database and environment
settings.

## Current deployment

| Setting | Current value |
| --- | --- |
| Container | `storypointpoker` |
| Image | `storypointpoker:<approved-version>` |
| Application port | `5000` |
| Persistent volume | `pointroom-data` mounted at `/app/data` |
| Health endpoint | `GET /health` |
| Server | Gunicorn `gthread`, one worker, 100 threads |
| State model | SQLite plus process-local room and Socket.IO state |
| Retention cleanup | Daily background worker in the application container |

Run one application container and one Gunicorn worker. Do not run multiple workers or replicas until room state is moved out of process memory and Socket.IO coordination is added.

## Runtime scaling and limit configuration

Point Room is intentionally constrained to one process today. Keep one application container and one Gunicorn worker until shared state and Socket.IO coordination are added.

- `POINTROOM_MAX_ACTIVE_ROOMS` (default `1000`) limits active rooms per process.
- `POINTROOM_MAX_SOCKET_CONNECTIONS` (default `500`) limits concurrent Socket.IO connections per process.
- `POINTROOM_SOCKET_RATE_MAX_KEYS` (default `10000`) limits in-memory socket rate-limit key tracking per process.

Treat these as protective limits, not horizontal scaling controls. Raising them does not enable safe multi-worker or multi-replica operation.

## Production prerequisites

Before making the service available to a team, confirm these values with IS:

- VM hostname: `<approved-vm-hostname>`
- Internal DNS name: `<pointroom.company.example>`
- Backup destination: `<approved-backup-location>`
- Monitoring destination: `<approved-monitoring-service>`
- Service owner and escalation contact: `<team-or-contact>`

The production network should use this path:

```text
Users -> company HTTPS reverse proxy -> Point Room on private port 5000
```

Do not expose port `5000` directly to the public internet. The current application does not provide company SSO, so access must remain restricted to an approved internal network or VPN until authentication is added.

## First deployment

Build or obtain an approved image on the Ubuntu VM. Prefer an immutable version tag rather than `latest`:

```bash
docker build --tag storypointpoker:<approved-version> .
```

Create the data volume once:

```bash
docker volume create pointroom-data
```

For a reverse proxy running on the same VM, bind the application only to loopback:

```bash
docker run --detach \
  --name storypointpoker \
  --restart unless-stopped \
  --publish 127.0.0.1:5000:5000 \
  --env POINTROOM_REQUIRE_HTTPS=1 \
  --env POINTROOM_TRUST_PROXY=1 \
  --env POINTROOM_RETENTION_CLEANUP_ENABLED=1 \
  --env POINTROOM_RETENTION_CLEANUP_INTERVAL_SECONDS=86400 \
  --env PUBLIC_BASE_URL=https://<pointroom.company.example> \
  --env POINTROOM_ALLOWED_ORIGINS=https://<pointroom.company.example> \
  --volume pointroom-data:/app/data \
  storypointpoker:<approved-version>
```

The reverse proxy must forward normal HTTP requests and `/socket.io` WebSocket traffic to `127.0.0.1:5000`. It must send `X-Forwarded-Proto: https` when the external request uses HTTPS.

For a temporary internal pilot without a reverse proxy, use HTTP only on an approved private network:

```bash
docker run --detach \
  --name storypointpoker \
  --restart unless-stopped \
  --publish 5000:5000 \
  --env POINTROOM_REQUIRE_HTTPS=0 \
  --volume pointroom-data:/app/data \
  storypointpoker:<approved-version>
```

## Health and status

Check the container and health state:

```bash
docker ps --filter name=storypointpoker
docker inspect storypointpoker \
  --format 'status={{.State.Status}} health={{.State.Health.Status}}'
curl --fail http://127.0.0.1:5000/health
```

A healthy response is:

```json
{"status":"ok"}
```

If the database cannot be reached, the endpoint returns HTTP `503` with
`{"status":"unhealthy"}`. If room snapshots fail to load, it returns HTTP
`503` with `{"status":"degraded","load_errors":N}`. Treat either response
as an operational incident and investigate the logs and data volume.

Check the published port if the service is not reachable:

```bash
ss -ltnp | grep ':5000'
```

The container runs retention cleanup once per day. The worker also migrates legacy room snapshots that still contain cleartext host tokens while preserving their original activity timestamps. Run an immediate one-shot pass after deployment or before the first protected backup:

```bash
docker exec storypointpoker python maintenance.py
```

The command reports both removed and migrated rooms. Review the output and confirm that the database and backups no longer contain a `host_token` field.

A successful local health check does not prove that DNS, the reverse proxy, the corporate firewall, or VPN access is configured correctly. Test the public internal URL from a separate user device.

## Logs

Application and audit logs are structured JSON. Gunicorn access logs are also written to the container output:

```bash
docker logs --timestamps --follow storypointpoker
```

Recent logs:

```bash
docker logs --timestamps --tail 100 storypointpoker
```

Do not put host tokens, participant names, story text, or votes into operational logs. Forward logs to the approved company logging service if required.

## Stop and start

Stop the service without deleting its data:

```bash
docker stop storypointpoker
```

Start the existing container again:

```bash
docker start storypointpoker
```

Do not remove the `pointroom-data` volume during routine maintenance.

## Updating the image

Build the new version and verify it before replacing the running container:

```bash
docker build --tag storypointpoker:<new-version> .
docker run --rm storypointpoker:<new-version> python -c "import app; print(app.__version__)"
```

Stop and replace only the container. Reuse the existing data volume:

```bash
docker stop storypointpoker
docker rm storypointpoker

docker run --detach \
  --name storypointpoker \
  --restart unless-stopped \
  --publish 127.0.0.1:5000:5000 \
  --env POINTROOM_REQUIRE_HTTPS=1 \
  --env POINTROOM_TRUST_PROXY=1 \
  --env PUBLIC_BASE_URL=https://<pointroom.company.example> \
  --env POINTROOM_ALLOWED_ORIGINS=https://<pointroom.company.example> \
  --volume pointroom-data:/app/data \
  storypointpoker:<new-version>
```

Confirm the new version and health state:

```bash
curl --fail -I http://127.0.0.1:5000/health
docker inspect storypointpoker \
  --format 'status={{.State.Status}} health={{.State.Health.Status}}'
```

Keep the previous image tag until the new version has passed its smoke test. To roll back, stop and remove the container, then run the previous image tag with the same volume and environment settings.

## Backup

The SQLite database contains room codes, host-token hashes, participant names, stories, votes, and history. Protect backups as sensitive internal data.

For a simple consistent file backup, stop Point Room first:

```bash
mkdir -p /srv/pointroom-backups

docker stop storypointpoker
docker run --rm \
  --volume pointroom-data:/data:ro \
  --volume /srv/pointroom-backups:/backup \
  alpine:3.20 \
  sh -c 'cp /data/pointroom.sqlite3 /backup/pointroom-$(date -u +%Y%m%dT%H%M%SZ).sqlite3'
docker start storypointpoker
```

Verify that the backup file exists and is readable. Configure the approved company backup system to copy backups to protected storage with an appropriate retention period. Test a restore at least once before relying on the backup.

## Restore

Restore only when Point Room is stopped:

```bash
docker stop storypointpoker

docker run --rm \
  --volume pointroom-data:/data \
  --volume /srv/pointroom-backups:/backup:ro \
  alpine:3.20 \
  sh -c 'cp /backup/<backup-file>.sqlite3 /data/pointroom.sqlite3'
docker start storypointpoker
```

Run the health check, open the application, and verify that an expected room is present. Keep the original volume or a copy until the restore has been confirmed.

## Incident checks

If users cannot open the service:

1. Check `docker ps` and the Docker health state.
2. Check `docker logs --tail 100 storypointpoker`.
3. Run `curl --fail http://127.0.0.1:5000/health` on the VM.
4. Confirm the reverse proxy is running and forwarding `/socket.io` upgrades.
5. Confirm the VM firewall, corporate firewall, VPN route, DNS, and certificate.
6. Test from a second user device.

If the page opens but live updates do not work, inspect browser errors and verify WebSocket forwarding. Polling fallback may still allow partial connectivity, but the deployment should support WebSockets.

If the container is unhealthy, do not delete the data volume. Capture logs and inspect the mounted volume before restarting or restoring.

## Pre-launch checklist

- [ ] IS has approved the Ubuntu VM and Docker installation.
- [ ] Internal DNS points to the VM.
- [ ] HTTPS certificate is trusted by company devices.
- [ ] Reverse proxy forwards `/socket.io` WebSocket traffic.
- [ ] Port `5000` is private or restricted to the reverse proxy.
- [ ] `POINTROOM_ALLOWED_ORIGINS` matches the HTTPS URL.
- [ ] Automated backups are configured.
- [ ] A restore test has succeeded.
- [ ] Health monitoring is configured.
- [ ] Logs are forwarded to the approved destination.
- [ ] Users are informed that company SSO is not yet available.
- [ ] A service owner and escalation path are documented.
