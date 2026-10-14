from __future__ import annotations

import json
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any

from flask import Flask, current_app, jsonify, request
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from rate_limiting import (
    DEFAULT_EVENT_RATE_LIMITER_MAX_KEYS,
    EventRateLimiter,
    SocketConnectionLimiter,
)
from room_store import (
    DEFAULT_ROOM_RETENTION_DAYS,
    RoomStore,
    resolve_dsn,
)
from room_service import (
    DEFAULT_MAX_ACTIVE_ROOMS,
    RoomService,
)
from socket_events import register_socket_events
from version import __version__
from app_config import (
    DEFAULT_ADO_WORK_ITEM_BASE_URL,
    DEFAULT_HOST,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_MAX_SOCKET_CONNECTIONS,
    DEFAULT_MAX_SOCKET_MESSAGE_BYTES,
    DEFAULT_PORT,
    DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS,
    content_security_policy,
    env_flag,
    env_int,
    parse_allowed_origins,
    validate_transport_config,
)
from app_request_handlers import register_request_handlers

socketio = SocketIO()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
logger = logging.getLogger("pointroom")


class StructuredLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in ("event", "room_id", "status", "actor", "remote_addr"):
            field_value = getattr(record, field_name, None)
            if field_value is not None:
                payload[field_name] = field_value
        return json.dumps(payload, separators=(",", ":"))


def _configure_logging(log_level: str) -> None:
    handler = next(
        (existing_handler for existing_handler in logger.handlers if getattr(existing_handler, "_pointroom_handler", False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        handler._pointroom_handler = True
        handler.setFormatter(StructuredLogFormatter())
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False


def _audit(event_name: str, room_id: str | None = None, status: str = "success") -> None:
    extra = {
        "event": event_name,
        "status": status,
        "actor": request.headers.get(
            current_app.config["AUTH_USER_HEADER"], ""
        ).strip()
        or "anonymous",
        "remote_addr": request.remote_addr or "unknown",
    }
    if room_id:
        extra["room_id"] = room_id
    logger.info("Audit event", extra=extra)


def _retention_cleanup_loop(
    application: Flask,
    room_store: RoomStore,
    socketio_instance: SocketIO,
) -> None:
    interval_seconds = application.config["RETENTION_CLEANUP_INTERVAL_SECONDS"]
    while True:
        socketio_instance.sleep(interval_seconds)
        with application.app_context():
            try:
                room_store.cleanup_expired()
                room_store.migrate_legacy_snapshots()
            except Exception:
                logger.exception(
                    "Scheduled retention cleanup failed",
                    extra={
                        "event": "retention_cleanup",
                        "status": "failed",
                    },
                )
            else:
                logger.info(
                    "Scheduled retention cleanup completed",
                    extra={
                        "event": "retention_cleanup",
                        "status": "success",
                    },
                )


def _payload(data: Any) -> dict[str, Any]:
    return data if isinstance(data, dict) else {}


def create_app(
    test_config: dict[str, Any] | None = None,
    room_store: RoomStore | None = None,
    event_limiter: EventRateLimiter | None = None,
    socketio_instance: SocketIO | None = None,
    limiter_instance: Limiter | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=secrets.token_hex(16),
        TESTING=False,
        APP_VERSION=__version__,
        HOST=os.environ.get("POINTROOM_HOST", DEFAULT_HOST),
        PORT=int(os.environ.get("POINTROOM_PORT", DEFAULT_PORT)),
        MAX_CONTENT_LENGTH=env_int(
            "POINTROOM_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES
        ),
        SOCKET_MAX_MESSAGE_BYTES=env_int(
            "POINTROOM_SOCKET_MAX_MESSAGE_BYTES", DEFAULT_MAX_SOCKET_MESSAGE_BYTES
        ),
        MAX_ACTIVE_ROOMS=env_int(
            "POINTROOM_MAX_ACTIVE_ROOMS", DEFAULT_MAX_ACTIVE_ROOMS
        ),
        MAX_SOCKET_CONNECTIONS=env_int(
            "POINTROOM_MAX_SOCKET_CONNECTIONS", DEFAULT_MAX_SOCKET_CONNECTIONS
        ),
        DEBUG=env_flag("POINTROOM_DEBUG", False),
        REQUIRE_HTTPS=env_flag("POINTROOM_REQUIRE_HTTPS", False),
        TRUST_PROXY=env_flag("POINTROOM_TRUST_PROXY", False),
        AUTHENTICATION_REQUIRED=env_flag("POINTROOM_AUTHENTICATION_REQUIRED", False),
        AUTH_USER_HEADER=os.environ.get("POINTROOM_AUTH_USER_HEADER", "X-Forwarded-User"),
        ALLOWED_ORIGINS=parse_allowed_origins(os.environ.get("POINTROOM_ALLOWED_ORIGINS")),
        RATELIMIT_ENABLED=env_flag("POINTROOM_RATE_LIMITING_ENABLED", True),
        RATELIMIT_STORAGE_URI=os.environ.get(
            "POINTROOM_RATE_LIMIT_STORAGE_URI", "memory://"
        ),
        RATELIMIT_HEADERS_ENABLED=True,
        ROOM_CREATE_RATE_LIMIT=os.environ.get(
            "POINTROOM_ROOM_CREATE_RATE_LIMIT", "10 per minute"
        ),
        ROOM_COLOR_RATE_LIMIT=os.environ.get(
            "POINTROOM_ROOM_COLOR_RATE_LIMIT", "60 per minute"
        ),
        SOCKET_JOIN_RATE_LIMIT=env_int("POINTROOM_SOCKET_JOIN_RATE_LIMIT", 30),
        SOCKET_VOTE_RATE_LIMIT=env_int("POINTROOM_SOCKET_VOTE_RATE_LIMIT", 60),
        SOCKET_SUGGESTION_RATE_LIMIT=env_int(
            "POINTROOM_SOCKET_SUGGESTION_RATE_LIMIT", 20
        ),
        SOCKET_CONNECT_RATE_LIMIT=env_int("POINTROOM_SOCKET_CONNECT_RATE_LIMIT", 60),
        SOCKET_HOST_ACTION_RATE_LIMIT=env_int(
            "POINTROOM_SOCKET_HOST_ACTION_RATE_LIMIT", 60
        ),
        SOCKET_UPDATE_ISSUE_RATE_LIMIT=env_int(
            "POINTROOM_SOCKET_UPDATE_ISSUE_RATE_LIMIT", 300
        ),
        SOCKET_DELETE_SESSION_RATE_LIMIT=env_int(
            "POINTROOM_SOCKET_DELETE_SESSION_RATE_LIMIT", 5
        ),
        SOCKET_RATE_WINDOW_SECONDS=env_int("POINTROOM_SOCKET_RATE_WINDOW_SECONDS", 60),
        SOCKET_RATE_MAX_KEYS=env_int(
            "POINTROOM_SOCKET_RATE_MAX_KEYS", DEFAULT_EVENT_RATE_LIMITER_MAX_KEYS
        ),
        RETENTION_CLEANUP_ENABLED=env_flag(
            "POINTROOM_RETENTION_CLEANUP_ENABLED", False
        ),
        RETENTION_CLEANUP_INTERVAL_SECONDS=env_int(
            "POINTROOM_RETENTION_CLEANUP_INTERVAL_SECONDS",
            DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS,
        ),
        DATABASE_DSN=resolve_dsn(),
        ROOM_RETENTION_DAYS=int(
            os.environ.get("POINTROOM_RETENTION_DAYS", DEFAULT_ROOM_RETENTION_DAYS)
        ),
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        SSL_CERTFILE=os.environ.get("POINTROOM_SSL_CERTFILE", ""),
        SSL_KEYFILE=os.environ.get("POINTROOM_SSL_KEYFILE", ""),
        ADO_WORK_ITEM_BASE_URL=os.environ.get(
            "ADO_WORK_ITEM_BASE_URL", DEFAULT_ADO_WORK_ITEM_BASE_URL
        ),
        LOG_LEVEL=os.environ.get("POINTROOM_LOG_LEVEL", "INFO"),
    )
    if test_config:
        app.config.update(test_config)

    _configure_logging(app.config["LOG_LEVEL"])
    store = room_store or RoomStore(
        app.config["DATABASE_DSN"],
        app.config["ROOM_RETENTION_DAYS"],
    )
    room_service = RoomService(
        store,
        max_active_rooms=app.config["MAX_ACTIVE_ROOMS"],
    )
    event_limiter = event_limiter or EventRateLimiter(app.config["SOCKET_RATE_MAX_KEYS"])
    connection_limiter = SocketConnectionLimiter()
    if socketio_instance is None:
        socketio_instance = socketio
    if limiter_instance is None:
        limiter_instance = limiter

    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.extensions["storypoint_store"] = store
    app.extensions["room_store"] = store
    app.extensions["room_service"] = room_service
    app.extensions["event_rate_limiter"] = event_limiter
    app.extensions["socket_connection_limiter"] = connection_limiter
    app.extensions["pointroom_socketio"] = socketio_instance
    app.extensions["pointroom_audit"] = _audit
    socketio_instance.init_app(
        app,
        cors_allowed_origins=app.config["ALLOWED_ORIGINS"],
        max_http_buffer_size=app.config["SOCKET_MAX_MESSAGE_BYTES"],
    )
    limiter_instance.init_app(app)
    if app.config["RETENTION_CLEANUP_ENABLED"] and not app.config["TESTING"]:
        app.extensions["retention_cleanup_task"] = socketio_instance.start_background_task(
            _retention_cleanup_loop,
            app,
            store,
            socketio_instance,
        )
    register_socket_events(socketio_instance)

    register_request_handlers(
        app,
        room_service,
        store,
        limiter_instance,
        _audit,
        _payload,
        content_security_policy,
    )
    return app


_validate_transport_config = validate_transport_config
app = create_app()


if __name__ == "__main__":
    _validate_transport_config(app)
    ssl_certfile = app.config["SSL_CERTFILE"]
    ssl_keyfile = app.config["SSL_KEYFILE"]

    socketio.run(
        app,
        host=app.config["HOST"],
        port=app.config["PORT"],
        debug=app.config["DEBUG"],
        ssl_context=(ssl_certfile, ssl_keyfile)
        if app.config["REQUIRE_HTTPS"] and ssl_certfile
        else None,
    )
