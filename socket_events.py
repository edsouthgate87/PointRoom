from __future__ import annotations

import logging
import time
from typing import Any

from flask import Flask, current_app, request
from flask_socketio import SocketIO, emit, join_room as socket_join_room, leave_room

from room_service import RoomService, RoomServiceError

SID_SCOPED_RATE_LIMIT_EVENTS = (
    "vote",
    "reveal",
    "reset_round",
    "start_new_story",
    "remove_history_story",
    "delete_session",
    "update_issue",
    "set_reveal_countdown",
    "update_profile",
    "set_suggestions_enabled",
    "set_deck",
    "suggest_story",
    "accept_story_suggestion",
    "backlog_story_suggestion",
    "dismiss_story_suggestion",
    "use_backlog_story",
    "remove_backlog_story",
)

logger = logging.getLogger("pointroom")


def _socketio_instance() -> SocketIO:
    return current_app.extensions["pointroom_socketio"]


def _room_service() -> RoomService:
    return current_app.extensions["room_service"]


def _broadcast_state(room_id: str) -> None:
    state = _room_service().public_state(room_id)
    if state is None:
        return
    _socketio_instance().emit("room_state", state, to=room_id)


def _broadcast_history(room_id: str) -> None:
    history = _room_service().public_history(room_id)
    if history is None:
        return
    _socketio_instance().emit("room_history", {"history": history}, to=room_id)


def _complete_reveal_after_delay(
    application: Flask,
    room_service: RoomService,
    socketio_instance: SocketIO,
    room_id: str,
    ends_at: float,
) -> None:
    while True:
        remaining = ends_at - time.time()
        if remaining <= 0:
            break
        socketio_instance.sleep(remaining)

    with application.app_context():
        completed_room_id = room_service.complete_reveal(room_id, ends_at)
        if completed_room_id is None:
            return
        logger.info(
            "Audit event",
            extra={
                "event": "round_revealed",
                "room_id": completed_room_id,
                "status": "success",
                "actor": "system",
                "remote_addr": "internal",
            },
        )
        _broadcast_state(completed_room_id)
        _broadcast_history(completed_room_id)


def _error(message: str) -> None:
    emit("room_error", {"message": message})


def _payload(data: Any) -> dict[str, Any]:
    return data if isinstance(data, dict) else {}


def _allow_socket_event(event_name: str, limit: int, by_ip: bool = False) -> bool:
    if not current_app.config["RATELIMIT_ENABLED"]:
        return True

    identity = request.remote_addr if by_ip else request.sid
    identity = identity or "unknown"
    event_limiter = current_app.extensions["event_rate_limiter"]
    if event_limiter.allow(
        f"{event_name}:{identity}",
        limit,
        current_app.config["SOCKET_RATE_WINDOW_SECONDS"],
    ):
        return True

    _audit(f"{event_name}_rate_limited", status="rate_limited")
    _error("Too many requests. Please try again later.")
    return False


def _audit(event_name: str, room_id: str | None = None, status: str = "success") -> None:
    current_app.extensions["pointroom_audit"](
        event_name,
        room_id=room_id,
        status=status,
    )


def handle_connect(auth: dict[str, Any] | None = None) -> bool | None:
    if not _allow_socket_event(
        "connect",
        current_app.config["SOCKET_CONNECT_RATE_LIMIT"],
        by_ip=True,
    ):
        return False
    connection_limiter = current_app.extensions["socket_connection_limiter"]
    if not connection_limiter.allow(
        request.sid,
        current_app.config["MAX_SOCKET_CONNECTIONS"],
    ):
        _audit("socket_connection_rejected", status="capacity_exceeded")
        return False
    if not current_app.config["AUTHENTICATION_REQUIRED"]:
        return None
    identity = request.headers.get(current_app.config["AUTH_USER_HEADER"], "").strip()
    if not identity:
        _audit("socket_authentication_failed", status="denied")
        connection_limiter.remove(request.sid)
        return False
    return None


def handle_join(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "join_room",
        current_app.config["SOCKET_JOIN_RATE_LIMIT"],
        by_ip=True,
    ):
        return
    data = _payload(data)
    try:
        joined_room = _room_service().join(
            request.sid,
            data.get("room_id"),
            data.get("name"),
            data.get("participant_id"),
            data.get("participant_token"),
            data.get("host_token"),
            data.get("avatar_color"),
            data.get("update_avatar_color"),
        )
    except RoomServiceError as error:
        _audit("participant_join_rejected", status="denied")
        _error(str(error))
        return

    _audit("participant_joined", joined_room.room_id)
    if joined_room.previous_room_id:
        leave_room(joined_room.previous_room_id)
        _broadcast_state(joined_room.previous_room_id)
    socket_join_room(joined_room.room_id)
    emit(
        "your_identity",
        {
            "participant_id": joined_room.participant_id,
            "participant_token": joined_room.participant_token,
        },
    )
    emit("room_history", {"history": joined_room.history})
    emit("your_vote", {"value": joined_room.own_vote})
    _broadcast_state(joined_room.room_id)


def handle_vote(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event("vote", current_app.config["SOCKET_VOTE_RATE_LIMIT"]):
        return
    data = _payload(data)
    value = data.get("value")
    try:
        room_id = _room_service().vote(request.sid, value)
    except RoomServiceError as error:
        _audit("vote_rejected", status="denied")
        _error(str(error))
        return
    if value is not None:
        _audit("vote_submitted", room_id)
    emit("your_vote", {"value": value})
    _broadcast_state(room_id)


def handle_update_profile(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "update_profile", current_app.config["SOCKET_VOTE_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id, name, avatar_color = _room_service().update_profile(
            request.sid,
            data.get("name"),
            data.get("avatar_color"),
        )
    except RoomServiceError as error:
        _audit("profile_update_rejected", status="denied")
        _error(str(error))
        return
    _audit("profile_updated", room_id)
    emit(
        "profile_updated",
        {
            "name": name,
            "avatar_color": avatar_color,
        },
    )
    _broadcast_state(room_id)
    _broadcast_history(room_id)


def handle_set_suggestions_enabled(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "set_suggestions_enabled", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().set_suggestions_enabled(
            request.sid,
            data.get("enabled"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("suggestions_setting_rejected", status="denied")
        _error(str(error))
        return
    _audit("suggestions_setting_updated", room_id)
    _broadcast_state(room_id)


def handle_set_deck(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "set_deck", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().set_deck(
            request.sid,
            data.get("deck"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("deck_update_rejected", status="denied")
        _error(str(error))
        return
    _audit("deck_updated", room_id)
    _broadcast_state(room_id)


def handle_suggest_story(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "suggest_story", current_app.config["SOCKET_SUGGESTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id, suggestion = _room_service().submit_story_suggestion(
            request.sid,
            data.get("issue"),
        )
    except RoomServiceError as error:
        _audit("story_suggestion_rejected", status="denied")
        _error(str(error))
        return
    _audit("story_suggested", room_id)
    emit("story_suggestion_submitted", {"suggestion": suggestion})
    _broadcast_state(room_id)


def handle_reveal(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "reveal", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_service = _room_service()
        countdown = room_service.begin_reveal(request.sid, data.get("host_token"))
    except RoomServiceError as error:
        _audit("reveal_rejected", status="denied")
        _error(str(error))
        return
    if countdown is None:
        return
    _audit("reveal_countdown_started", countdown.room_id)
    _broadcast_state(countdown.room_id)
    _socketio_instance().emit(
        "reveal_countdown",
        {
            "seconds": countdown.seconds,
            "ends_at": countdown.ends_at,
        },
        to=countdown.room_id,
    )
    if countdown.started:
        application = current_app._get_current_object()
        _socketio_instance().start_background_task(
            _complete_reveal_after_delay,
            application,
            room_service,
            _socketio_instance(),
            countdown.room_id,
            countdown.ends_at,
        )


def handle_set_reveal_countdown(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "set_reveal_countdown", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().set_reveal_countdown(
            request.sid,
            data.get("seconds"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("reveal_countdown_update_rejected", status="denied")
        _error(str(error))
        return
    _audit("reveal_countdown_updated", room_id)
    _broadcast_state(room_id)


def handle_reset(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "reset_round", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().reset_round(request.sid, data.get("host_token"))
    except RoomServiceError as error:
        _audit("round_reset_rejected", status="denied")
        _error(str(error))
        return
    _audit("round_reset", room_id)
    _broadcast_state(room_id)
    _broadcast_history(room_id)
    _socketio_instance().emit("your_vote", {"value": None}, to=room_id)


def handle_start_new_story(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "start_new_story", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().start_new_story(
            request.sid,
            data.get("issue"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("story_start_rejected", status="denied")
        _error(str(error))
        return
    _audit("story_started", room_id)
    _broadcast_state(room_id)
    _broadcast_history(room_id)
    _socketio_instance().emit("your_vote", {"value": None}, to=room_id)


def handle_accept_story_suggestion(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "accept_story_suggestion", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().accept_story_suggestion(
            request.sid,
            data.get("suggestion_id"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("story_suggestion_accept_rejected", status="denied")
        _error(str(error))
        return
    _audit("story_suggestion_accepted", room_id)
    _broadcast_state(room_id)
    _broadcast_history(room_id)
    _socketio_instance().emit("your_vote", {"value": None}, to=room_id)


def handle_backlog_story_suggestion(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "backlog_story_suggestion", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().backlog_story_suggestion(
            request.sid,
            data.get("suggestion_id"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("story_suggestion_backlog_rejected", status="denied")
        _error(str(error))
        return
    _audit("story_suggestion_backlogged", room_id)
    _broadcast_state(room_id)


def handle_dismiss_story_suggestion(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "dismiss_story_suggestion", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().dismiss_story_suggestion(
            request.sid,
            data.get("suggestion_id"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("story_suggestion_dismiss_rejected", status="denied")
        _error(str(error))
        return
    _audit("story_suggestion_dismissed", room_id)
    _broadcast_state(room_id)


def handle_use_backlog_story(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "use_backlog_story", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().use_backlog_story(
            request.sid,
            data.get("backlog_id"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("backlog_story_use_rejected", status="denied")
        _error(str(error))
        return
    _audit("backlog_story_used", room_id)
    _broadcast_state(room_id)
    _broadcast_history(room_id)
    _socketio_instance().emit("your_vote", {"value": None}, to=room_id)


def handle_remove_backlog_story(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "remove_backlog_story", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().remove_backlog_story(
            request.sid,
            data.get("backlog_id"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("backlog_story_removal_rejected", status="denied")
        _error(str(error))
        return
    _audit("backlog_story_removed", room_id)
    _broadcast_state(room_id)


def handle_remove_history_story(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "remove_history_story", current_app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().remove_history_story(
            request.sid,
            data.get("round"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("history_removal_rejected", status="denied")
        _error(str(error))
        return
    _audit("history_story_removed", room_id)
    _broadcast_state(room_id)
    _broadcast_history(room_id)


def handle_delete_session(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "delete_session", current_app.config["SOCKET_DELETE_SESSION_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().delete_session(
            request.sid,
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("session_deletion_rejected", status="denied")
        _error(str(error))
        return
    _socketio_instance().emit(
        "session_deleted",
        {"message": "The session was deleted by the host."},
        to=room_id,
    )
    _audit("session_deleted", room_id)


def handle_update_issue(data: dict[str, Any] | None) -> None:
    if not _allow_socket_event(
        "update_issue", current_app.config["SOCKET_UPDATE_ISSUE_RATE_LIMIT"]
    ):
        return
    data = _payload(data)
    try:
        room_id = _room_service().update_issue(
            request.sid,
            data.get("issue"),
            data.get("host_token"),
        )
    except RoomServiceError as error:
        _audit("story_update_rejected", status="denied")
        _error(str(error))
        return
    _audit("story_updated", room_id)
    _broadcast_state(room_id)


def handle_disconnect() -> None:
    connection_limiter = current_app.extensions["socket_connection_limiter"]
    connection_limiter.remove(request.sid)
    room_id = _room_service().disconnect(request.sid)
    event_limiter = current_app.extensions["event_rate_limiter"]
    for event_name in SID_SCOPED_RATE_LIMIT_EVENTS:
        event_limiter.remove(f"{event_name}:{request.sid}")
    if room_id is None:
        return
    leave_room(room_id)
    _broadcast_state(room_id)


def register_socket_events(socketio: SocketIO) -> None:
    handlers = {
        "connect": handle_connect,
        "join_room": handle_join,
        "vote": handle_vote,
        "update_profile": handle_update_profile,
        "set_suggestions_enabled": handle_set_suggestions_enabled,
        "set_deck": handle_set_deck,
        "suggest_story": handle_suggest_story,
        "reveal": handle_reveal,
        "set_reveal_countdown": handle_set_reveal_countdown,
        "reset_round": handle_reset,
        "start_new_story": handle_start_new_story,
        "accept_story_suggestion": handle_accept_story_suggestion,
        "backlog_story_suggestion": handle_backlog_story_suggestion,
        "dismiss_story_suggestion": handle_dismiss_story_suggestion,
        "use_backlog_story": handle_use_backlog_story,
        "remove_backlog_story": handle_remove_backlog_story,
        "remove_history_story": handle_remove_history_story,
        "delete_session": handle_delete_session,
        "update_issue": handle_update_issue,
        "disconnect": handle_disconnect,
    }
    for event_name, handler in handlers.items():
        socketio.on_event(event_name, handler)