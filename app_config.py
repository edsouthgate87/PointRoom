from __future__ import annotations

import os
from urllib.parse import urlsplit

from flask import Flask

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5000
DEFAULT_MAX_REQUEST_BYTES = 16 * 1024
DEFAULT_MAX_SOCKET_MESSAGE_BYTES = 16 * 1024
DEFAULT_MAX_SOCKET_CONNECTIONS = 500
DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_ADO_WORK_ITEM_BASE_URL = ""


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def parse_allowed_origins(value: str | None) -> list[str] | None:
    if not value:
        return None
    origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
    return origins or None


def validate_transport_config(application: Flask) -> None:
    if application.config["AUTHENTICATION_REQUIRED"]:
        if not application.config["REQUIRE_HTTPS"]:
            raise RuntimeError("Proxy authentication requires HTTPS to be enabled.")
        if not application.config["TRUST_PROXY"]:
            raise RuntimeError(
                "Proxy authentication requires POINTROOM_TRUST_PROXY=1 behind a trusted proxy."
            )
        if not application.config["AUTH_USER_HEADER"]:
            raise RuntimeError("AUTH_USER_HEADER must be configured when proxy authentication is required.")

    if not application.config["REQUIRE_HTTPS"]:
        return

    ssl_certfile = application.config["SSL_CERTFILE"]
    ssl_keyfile = application.config["SSL_KEYFILE"]
    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise RuntimeError(
            "HTTPS is required. Set both POINTROOM_SSL_CERTFILE and "
            "POINTROOM_SSL_KEYFILE to start PointRoom."
        )

    if ssl_certfile and (
        not os.path.isfile(ssl_certfile) or not os.path.isfile(ssl_keyfile)
    ):
        raise RuntimeError("The configured PointRoom HTTPS certificate or key file does not exist.")

    if not ssl_certfile and not application.config["TRUST_PROXY"]:
        raise RuntimeError(
            "HTTPS is required. Provide a certificate and key, or set "
            "POINTROOM_TRUST_PROXY=1 behind a trusted HTTPS reverse proxy."
        )

    public_base_url = application.config["PUBLIC_BASE_URL"]
    public_url = urlsplit(public_base_url)
    if public_url.scheme.lower() != "https" or not public_url.netloc:
        raise RuntimeError("PUBLIC_BASE_URL must use https:// when HTTPS is required.")


def content_security_policy(public_base_url: str) -> str:
    connect_sources = ["'self'"]
    public_url = urlsplit(public_base_url) if public_base_url else None
    if public_url and public_url.scheme and public_url.netloc:
        websocket_scheme = "wss" if public_url.scheme.lower() == "https" else "ws"
        connect_sources.extend(
            [
                f"{public_url.scheme}://{public_url.netloc}",
                f"{websocket_scheme}://{public_url.netloc}",
            ]
        )

    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "img-src 'self' data:",
        "font-src 'self' https://fonts.gstatic.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "script-src 'self' https://cdn.socket.io https://unpkg.com",
        f"connect-src {' '.join(connect_sources)}",
    ]
    return "; ".join(directives)
