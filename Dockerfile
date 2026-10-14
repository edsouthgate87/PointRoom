FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POINTROOM_HOST=0.0.0.0 \
    POINTROOM_PORT=5000 \
    POINTROOM_REQUIRE_HTTPS=0 \
    POINTROOM_RETENTION_CLEANUP_ENABLED=1 \
    POINTROOM_RETENTION_CLEANUP_INTERVAL_SECONDS=86400 \
    POINTROOM_DATABASE_PATH=/app/data/pointroom.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py app_config.py app_request_handlers.py domain.py maintenance.py rate_limiting.py room_errors.py room_projection.py room_repository.py room_service.py room_snapshot.py room_store.py room_store_adapters.py socket_events.py story_queue_service.py version.py wsgi.py ./
COPY static ./static
COPY templates ./templates

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /app/data \
    && chown -R app:app /app

RUN POINTROOM_DATABASE_PATH=/tmp/pointroom-smoke.sqlite3 \
    python -c "import wsgi; print(wsgi.app.config['APP_VERSION'])" \
    && rm -f /tmp/pointroom-smoke.sqlite3

USER app

VOLUME ["/app/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; response = urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3); raise SystemExit(0 if response.status == 200 else 1)"]

CMD ["gunicorn", "--worker-class", "gthread", "--workers", "1", "--threads", "100", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]