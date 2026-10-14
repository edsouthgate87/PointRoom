# Deploying Point Room

This guide covers running Point Room as a long-lived service with Docker:
HTTPS, health checks, logs, updates, backups, and troubleshooting.

## At a glance

| Setting | Value |
| --- | --- |
| Container name | `pointroom` (any name works) |
| Application port | `5000` |
| Persistent volume | `pointroom-data` mounted at `/app/data` |
| Health endpoint | `GET /health` |
| Server | Gunicorn `gthread`, one worker, 100 threads |
| State model | SQLite (or PostgreSQL) plus in-process room and Socket.IO state |
| Retention cleanup | Daily background worker in the application container |

Run **one** application container with **one** Gunicorn worker. Room state and
Socket.IO coordination are process-local, so multiple workers or replicas are
not supported yet.

These limits protect that single process; raising them does not make the app
horizontally scalable:

- `POINTROOM_MAX_ACTIVE_ROOMS` (default `1000`)
- `POINTROOM_MAX_SOCKET_CONNECTIONS` (default `500`)
- `POINTROOM_SOCKET_RATE_MAX_KEYS` (default `10000`)

## Before you expose it

Point Room has no built-in user accounts. Anyone who can reach the URL and
knows a room code can join that room. Keep it on a private network or VPN, or
put it behind an authenticating proxy. Do not publish port `5000` directly to
the internet.

The recommended network path is:

```text
Browser -> HTTPS reverse proxy -> Point Room on a private port 5000
```

## Build and run

```bash
docker build --tag pointroom:1.0 .
docker volume create pointroom-data
```

Prefer an explicit version tag over `latest` so you can roll back.

### Behind a reverse proxy on the same host

Bind the app to loopback only and tell it about the external HTTPS address:

```bash
docker run --detach \
  --name pointroom \
  --restart unless-stopped \
  --publish 127.0.0.1:5000:5000 \
  --env POINTROOM_REQUIRE_HTTPS=1 \
  --env POINTROOM_TRUST_PROXY=1 \
  --env PUBLIC_BASE_URL=https://pointroom.example.com \
  --env POINTROOM_ALLOWED_ORIGINS=https://pointroom.example.com \
  --volume pointroom-data:/app/data \
  pointroom:1.0
```

The proxy must forward normal HTTP requests **and** `/socket.io` WebSocket
upgrades, and must send `X-Forwarded-Proto: https`. Only set
`POINTROOM_TRUST_PROXY=1` when the app is reachable exclusively through that
proxy.

### Plain HTTP on a trusted LAN

For a quick pilot on a private network:

```bash
docker run --detach \
  --name pointroom \
  --restart unless-stopped \
  --publish 5000:5000 \
  --env PUBLIC_BASE_URL=http://192.168.1.25:5000 \
  --volume pointroom-data:/app/data \
  pointroom:1.0
```

Replace the address with the host's reachable IP so share links work for
other people.

### With PostgreSQL

[docker-compose.yml](../docker-compose.yml) runs Point Room with a PostgreSQL
database instead of SQLite. Copy [.env.example](../.env.example) to `.env`, set
`POSTGRES_PASSWORD`, then run `docker compose up -d`. Point Room uses
PostgreSQL whenever `POINTROOM_DATABASE_URL` or `POINTROOM_DATABASE_HOST` is
set.

## HTTPS with Caddy

[Caddy](https://caddyserver.com/) is a simple option because it obtains and
renews certificates automatically. Put Caddy and Point Room on a shared Docker
network so the app does not need a published port:

```bash
docker network create pointroom-net
docker run --detach --name pointroom --restart unless-stopped \
  --network pointroom-net \
  --env POINTROOM_REQUIRE_HTTPS=1 \
  --env POINTROOM_TRUST_PROXY=1 \
  --env PUBLIC_BASE_URL=https://pointroom.example.com \
  --env POINTROOM_ALLOWED_ORIGINS=https://pointroom.example.com \
  --volume pointroom-data:/app/data \
  pointroom:1.0

docker run --detach --name pointroom-caddy --restart unless-stopped \
  --network pointroom-net \
  --publish 80:80 --publish 443:443 \
  --volume "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" \
  --volume pointroom-caddy-data:/data \
  --volume pointroom-caddy-config:/config \
  caddy:2
```

For a public DNS name, this `Caddyfile` is enough; Caddy gets a Let's Encrypt
certificate and handles WebSockets automatically:

```caddyfile
pointroom.example.com {
    reverse_proxy pointroom:5000
}
```

For an internal-only host name that a public certificate authority cannot
validate, use Caddy's built-in certificate authority instead:

```caddyfile
{
    skip_install_trust
}

pointroom.internal.example {
    tls internal
    reverse_proxy pointroom:5000
}
```

Browsers will only trust that certificate after Caddy's root certificate is
installed on each client. Export it with:

```bash
docker cp pointroom-caddy:/data/caddy/pki/authorities/local/root.crt ./pointroom-root-ca.crt
```

On Windows, import it into the current user's trusted roots with
`Import-Certificate -FilePath pointroom-root-ca.crt -CertStoreLocation Cert:\CurrentUser\Root`.
Keep the `pointroom-caddy-data` volume: deleting it creates a new root
certificate that every client would need to trust again.

Point Room sends a `Strict-Transport-Security` header when HTTPS is required.
If other HTTP-only services share the same host name, strip that header at the
proxy with `header_down -Strict-Transport-Security` inside `reverse_proxy`.

Any other proxy (Nginx, Traefik, IIS) works as long as it forwards WebSocket
upgrades and `X-Forwarded-Proto`.

### Direct HTTPS without a proxy

When running from source with `python app.py`, Point Room can serve HTTPS
itself using `POINTROOM_SSL_CERTFILE` and `POINTROOM_SSL_KEYFILE`. The Docker
image's Gunicorn command does not use these settings, so use a reverse proxy
for container deployments.

## Health and status

```bash
docker ps --filter name=pointroom
docker inspect pointroom --format 'status={{.State.Status}} health={{.State.Health.Status}}'
docker exec pointroom python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health').read())"
```

A healthy response is `{"status":"ok"}`. The endpoint returns HTTP `503` with
`{"status":"unhealthy"}` when the database is unreachable, or
`{"status":"degraded","load_errors":N}` when room snapshots fail to load.

The container runs retention cleanup once a day. To run it immediately:

```bash
docker exec pointroom python maintenance.py
```

A healthy container does not prove that DNS, the proxy, firewalls, or
certificates are correct. Test the external URL from another device.

## Logs

Application and audit logs are structured JSON on container stdout:

```bash
docker logs --timestamps --tail 100 pointroom
docker logs --timestamps --follow pointroom
```

Set `POINTROOM_LOG_LEVEL=WARNING` to reduce volume; keep `INFO` when audit
events are needed.

## Updating

Build and smoke-test the new image before replacing the running container:

```bash
docker build --tag pointroom:1.1 .
docker run --rm pointroom:1.1 python -c "import wsgi; print(wsgi.app.config['APP_VERSION'])"
```

Replace only the container and reuse the same volume and settings:

```bash
docker stop pointroom
docker rename pointroom pointroom-previous
docker run --detach --name pointroom ... pointroom:1.1   # same options as before
```

Once the new version is healthy, remove the old container with
`docker rm pointroom-previous`. To roll back, stop and remove the new
container, then rename `pointroom-previous` back and start it.

Never remove the `pointroom-data` volume during routine maintenance.

## Backup and restore

The database holds room codes, host-token hashes, participant names, stories,
votes, and history. Treat backups as sensitive.

Back up the SQLite database while the app is stopped:

```bash
mkdir -p /srv/pointroom-backups
docker stop pointroom
docker run --rm \
  --volume pointroom-data:/data:ro \
  --volume /srv/pointroom-backups:/backup \
  alpine:3.20 \
  sh -c 'cp /data/pointroom.sqlite3 /backup/pointroom-$(date -u +%Y%m%dT%H%M%SZ).sqlite3'
docker start pointroom
```

Restore:

```bash
docker stop pointroom
docker run --rm \
  --volume pointroom-data:/data \
  --volume /srv/pointroom-backups:/backup:ro \
  alpine:3.20 \
  sh -c 'cp /backup/<backup-file>.sqlite3 /data/pointroom.sqlite3'
docker start pointroom
```

Check `/health` and confirm an expected room is present. Test a restore at
least once before relying on your backups.

## Troubleshooting

If the page does not load:

1. Check `docker ps` and the container's health state.
2. Check `docker logs --tail 100 pointroom`.
3. Check `/health` from inside the container.
4. Confirm the proxy is running and forwarding `/socket.io` upgrades.
5. Check firewalls, DNS, and the certificate.
6. Test from a second device.

If the page loads but live updates do not work, check the browser console and
the proxy's WebSocket configuration.

If the container is unhealthy, do not delete the data volume. Capture the logs
and inspect the volume before restarting or restoring.
