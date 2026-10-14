from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, jsonify, redirect, render_template, request
from flask_limiter import Limiter
from werkzeug.exceptions import RequestEntityTooLarge

from room_errors import RoomCapacityError, RoomServiceError
from room_service import RoomService
from room_store import RoomStore

logger = logging.getLogger("pointroom")


def register_request_handlers(
    app: Flask,
    room_service: RoomService,
    store: RoomStore,
    limiter: Limiter,
    audit: Callable[[str, str | None, str], None],
    payload: Callable[[Any], dict[str, Any]],
    content_security_policy: Callable[[str], str],
) -> None:
    @app.context_processor
    def inject_app_version() -> dict[str, str]:
        return {
            "app_version": app.config["APP_VERSION"],
            "ado_work_item_base_url": app.config["ADO_WORK_ITEM_BASE_URL"],
            "public_base_url": app.config["PUBLIC_BASE_URL"],
        }

    @app.after_request
    def add_app_version_header(response):
        response.headers["X-Application-Version"] = app.config["APP_VERSION"]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers.setdefault(
            "Content-Security-Policy",
            content_security_policy(app.config["PUBLIC_BASE_URL"]),
        )
        if app.config["REQUIRE_HTTPS"] and request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.before_request
    def redirect_insecure_requests():
        if request.endpoint == "health":
            return None
        if app.config["REQUIRE_HTTPS"] and not app.config["TESTING"] and not request.is_secure:
            public_base_url = app.config["PUBLIC_BASE_URL"]
            if not public_base_url:
                return jsonify({"error": "HTTPS redirect target is not configured."}), 500
            public_url = urlsplit(public_base_url)
            target = urlunsplit(
                (
                    public_url.scheme,
                    public_url.netloc,
                    f"{public_url.path.rstrip('/')}{request.path}",
                    request.query_string.decode("latin-1"),
                    "",
                )
            )
            return redirect(target, code=308)
        return None

    @app.before_request
    def require_proxy_authentication():
        if request.endpoint == "health" or not app.config["AUTHENTICATION_REQUIRED"]:
            return None
        identity = request.headers.get(app.config["AUTH_USER_HEADER"], "").strip()
        if not identity:
            audit("authentication_failed", status="denied")
            return jsonify({"error": "Authentication required."}), 401
        return None

    @app.errorhandler(429)
    def handle_rate_limit(error):
        audit("http_request_rate_limited", status="rate_limited")
        response = jsonify({"error": "Too many requests. Please try again later."})
        response.status_code = error.code
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_entity_too_large(error):
        audit("request_rejected", status="too_large")
        response = jsonify({"error": "Request is too large."})
        response.status_code = error.code
        return response

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/health")
    def health():
        health_status = store.health_status()
        if health_status["status"] != "ok":
            logger.error(
                "Health check failed",
                extra={
                    "event": "health_check",
                    "status": health_status["status"],
                },
            )
            return jsonify(health_status), 503
        return jsonify(health_status)

    @app.get("/room/<room_id>")
    def room_page(room_id: str) -> str:
        return render_template("index.html", room_id=room_id.upper())

    @app.get("/api/rooms/<room_id>/avatar-colors")
    @limiter.limit(lambda: app.config["ROOM_COLOR_RATE_LIMIT"])
    def avatar_colors(room_id: str):
        color_state = room_service.public_avatar_color_state(
            room_id,
            participant_token=request.headers.get("X-PointRoom-Participant-Token"),
            host_token=request.headers.get("X-PointRoom-Host-Token"),
        )
        if color_state is None:
            return jsonify({"error": "That room does not exist."}), 404
        return jsonify(color_state)

    @app.post("/api/rooms")
    @limiter.limit(lambda: app.config["ROOM_CREATE_RATE_LIMIT"])
    def create_room():
        request_payload = payload(request.get_json(silent=True))
        try:
            created_room = room_service.create_room(
                request_payload.get("name"),
                request_payload.get("participant_id"),
                request_payload.get("issue"),
                request_payload.get("avatar_color"),
                request_payload.get("deck"),
            )
        except RoomCapacityError as error:
            audit("room_creation_rejected", status="capacity_exceeded")
            return jsonify({"error": str(error)}), 503
        except RoomServiceError as error:
            audit("room_creation_rejected", status="invalid_request")
            return jsonify({"error": str(error)}), 400
        audit("room_created", created_room.room_id)

        response = jsonify(
            {
                "room_id": created_room.room_id,
                "host_token": created_room.host_token,
                "participant_id": created_room.participant_id,
                "participant_token": created_room.participant_token,
            }
        )
        response.status_code = 201
        response.headers["Cache-Control"] = "no-store"
        return response
