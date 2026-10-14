import json
import pytest
import room_store
import time
from threading import Event, Thread
from flask_limiter import Limiter

from app import (
    EventRateLimiter,
    _audit,
    _retention_cleanup_loop,
    _validate_transport_config,
    app,
    create_app,
    limiter,
    logger as app_logger,
    socketio,
)
from flask_socketio import SocketIO
from domain import (
    DEFAULT_REVEAL_COUNTDOWN_SECONDS,
    SAFE_FIBONACCI_DECK,
    T_SHIRT_DECK,
    MAX_PARTICIPANTS_PER_ROOM,
    MAX_REVEAL_COUNTDOWN_SECONDS,
    MIN_REVEAL_COUNTDOWN_SECONDS,
    Participant,
    Room,
    hash_host_token,
)
from room_store import ROOM_CLEANUP_BATCH_SIZE, RoomStore, resolve_dsn
from room_store_adapters import (
    PostgresAdapter,
    SqliteAdapter,
    adapter_class_for_dsn,
    resolve_dsn,
)
from room_service import RoomServiceError
from maintenance import run_retention_cleanup
from support import (
    create_room,
    event_payloads,
    join,
    received_event,
    reveal_and_wait,
    reveal_service_and_wait,
    wait_for_reveal,
)
from version import __version__

class StorageFailure(Exception):
    """Stands in for any backend error raised while persisting a room."""


store = app.extensions["room_store"]
event_rate_limiter = app.extensions["event_rate_limiter"]
socket_connection_limiter = app.extensions["socket_connection_limiter"]


def test_create_room_returns_shareable_room_and_host_token():
    room = create_room()

    assert len(room["room_id"]) == 6
    assert room["host_token"]
    assert room["participant_id"] == "host-1"
    assert room["participant_token"]
    assert "participant_token" not in app.extensions["room_service"].public_state(room["room_id"])
    assert app.test_client().get("/").headers["X-Application-Version"] == __version__
    assert app.test_client().get(f"/room/{room['room_id']}").status_code == 200


@pytest.mark.parametrize(
    ("deck_name", "expected_deck"),
    [
        ("safe_fibonacci", SAFE_FIBONACCI_DECK),
        ("t_shirt", T_SHIRT_DECK),
    ],
)
def test_create_room_uses_selected_deck(deck_name, expected_deck):
    room = create_room(deck=deck_name)
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])

    state = event_payloads(host.get_received(), "room_state")[-1]

    assert state["deck"] == list(expected_deck)


def test_invalid_deck_is_rejected():
    response = app.test_client().post(
        "/api/rooms",
        json={
            "name": "Alex",
            "participant_id": "host-1",
            "deck": "not-a-deck",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Choose a valid card deck."


def test_room_creation_response_is_not_cacheable():
    response = app.test_client().post(
        "/api/rooms",
        json={"name": "Alex", "participant_id": "host-1"},
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"


def test_retention_cleanup_worker_is_started_when_enabled(dsn_factory, monkeypatch):
    started = []
    local_socketio = SocketIO()

    def capture_task(target, *args):
        started.append((target, *args))
        return object()

    monkeypatch.setattr(local_socketio, "start_background_task", capture_task)
    local_app = create_app(
        {
            "TESTING": False,
            "DATABASE_DSN": dsn_factory("retention-worker"),
            "RETENTION_CLEANUP_ENABLED": True,
            "RETENTION_CLEANUP_INTERVAL_SECONDS": 123,
        },
        socketio_instance=local_socketio,
    )

    try:
        assert started == [
            (
                _retention_cleanup_loop,
                local_app,
                local_app.extensions["room_store"],
                local_socketio,
            )
        ]
        assert local_app.extensions["retention_cleanup_task"] is not None
    finally:
        local_app.extensions["room_store"].adapter.close()


def test_health_endpoint_confirms_database_is_available():
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_endpoint_reports_degraded_snapshot_loads():
    original_load_errors = store.load_errors
    store.load_errors = 2
    try:
        response = app.test_client().get("/health")
    finally:
        store.load_errors = original_load_errors

    assert response.status_code == 503
    assert response.get_json() == {"status": "degraded", "load_errors": 2}


def test_cold_room_load_does_not_block_other_rooms(monkeypatch):
    slow_room = create_room(participant_id="slow-host")
    fast_room = create_room(participant_id="fast-host")
    with store.lock:
        store.rooms.clear()

    original_fetch_state = store.adapter.fetch_state
    slow_fetch_started = Event()
    release_slow_fetch = Event()
    fast_load_finished = Event()
    load_results = {}

    def fetch_state(room_id):
        if room_id == slow_room["room_id"]:
            slow_fetch_started.set()
            assert release_slow_fetch.wait(timeout=2)
        return original_fetch_state(room_id)

    monkeypatch.setattr(store.adapter, "fetch_state", fetch_state)
    slow_thread = Thread(
        target=lambda: load_results.setdefault(
            "slow", store.get_room(slow_room["room_id"])
        )
    )
    fast_thread = Thread(
        target=lambda: (
            load_results.setdefault("fast", store.get_room(fast_room["room_id"])),
            fast_load_finished.set(),
        )
    )
    try:
        slow_thread.start()
        assert slow_fetch_started.wait(timeout=1)
        fast_thread.start()
        assert fast_load_finished.wait(timeout=1)
        assert load_results["fast"] is not None
    finally:
        release_slow_fetch.set()
        slow_thread.join(timeout=2)
        fast_thread.join(timeout=2)

    assert not slow_thread.is_alive()
    assert not fast_thread.is_alive()
    assert load_results["slow"] is not None


def test_audit_records_actor_and_source_context_without_payload_data(monkeypatch):
    records = []
    monkeypatch.setattr(
        app_logger,
        "info",
        lambda message, extra: records.append((message, extra)),
    )

    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-User": "alex@example.com"},
    ):
        _audit("security_event", "ABC123", status="denied")

    assert records == [
        (
            "Audit event",
            {
                "event": "security_event",
                "status": "denied",
                "actor": "alex@example.com",
                "remote_addr": "127.0.0.1",
                "room_id": "ABC123",
            },
        )
    ]


def test_server_defaults_to_network_bind_and_exposes_public_url_config():
    assert app.config["HOST"] == "0.0.0.0"
    assert app.config["PORT"] == 5000
    assert "PUBLIC_BASE_URL" in app.config
    assert app.config["REQUIRE_HTTPS"] is False
    assert app.config["SSL_CERTFILE"] == ""
    assert app.config["SSL_KEYFILE"] == ""
    assert app.config["ROOM_RETENTION_DAYS"] == 30
    assert app.config["DATABASE_DSN"]


def test_room_creation_rejects_oversized_request():
    request_limit = app.config["MAX_CONTENT_LENGTH"]
    response = app.test_client().post(
        "/api/rooms",
        data=b"{" + (b"x" * request_limit),
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json() == {"error": "Request is too large."}


def test_socketio_message_buffer_limit_is_configured():
    assert socketio.server.eio.max_http_buffer_size == app.config["SOCKET_MAX_MESSAGE_BYTES"]


def test_room_creation_rejects_when_active_room_capacity_is_reached():
    service = app.extensions["room_service"]
    original_limit = service.max_active_rooms
    service.max_active_rooms = 1
    try:
        create_room()
        response = app.test_client().post(
            "/api/rooms",
            json={"name": "Jordan", "participant_id": "host-2"},
        )
    finally:
        service.max_active_rooms = original_limit

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "The server has reached its active room limit. Please try again later."
    }


def test_socketio_rejects_connections_above_capacity():
    original_limit = app.config["MAX_SOCKET_CONNECTIONS"]
    app.config["MAX_SOCKET_CONNECTIONS"] = 1
    socket_connection_limiter.clear()
    first_client = None
    second_client = None
    try:
        first_client = socketio.test_client(app)
        second_client = socketio.test_client(app)

        assert first_client.is_connected()
        assert not second_client.is_connected()
    finally:
        if first_client is not None and first_client.is_connected():
            first_client.disconnect()
        if second_client is not None and second_client.is_connected():
            second_client.disconnect()
        socket_connection_limiter.clear()
        app.config["MAX_SOCKET_CONNECTIONS"] = original_limit


def test_app_factory_defaults_to_http_for_local_startup(dsn_factory, monkeypatch):
    monkeypatch.delenv("POINTROOM_REQUIRE_HTTPS", raising=False)
    local_app = create_app(
        {"DATABASE_DSN": dsn_factory("local")},
    )

    assert local_app.config["REQUIRE_HTTPS"] is False
    _validate_transport_config(local_app)


def test_app_factory_uses_exported_socketio_and_limiter_by_default(dsn_factory):
    local_app = create_app(
        {
            "TESTING": True,
            "REQUIRE_HTTPS": False,
            "DATABASE_DSN": dsn_factory("factory-defaults"),
        }
    )

    try:
        assert local_app.extensions["pointroom_socketio"] is socketio
        assert limiter in local_app.extensions["limiter"]
    finally:
        local_app.extensions["room_store"].adapter.close()


def test_legacy_participants_are_invalidated_on_store_reload(dsn_factory, raw_sql):
    legacy_dsn = dsn_factory("legacy")
    legacy_store = RoomStore(legacy_dsn)
    legacy_room = Room(
        room_id="LEGACY",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Legacy story",
        participants={
            "host-1": Participant(participant_id="host-1", name="Alex"),
            "guest-1": Participant(participant_id="guest-1", name="Jordan", vote="5"),
        },
    )
    payload = legacy_room.to_dict()
    payload["schema_version"] = 1
    payload["host_token"] = "host-token"
    payload.pop("host_token_hash")
    for participant in payload["participants"]:
        participant.pop("credential_hash")

    legacy_updated_at = time.time() - 60
    raw_sql(
        legacy_store,
        "INSERT INTO rooms (room_id, updated_at, state_json) VALUES (?, ?, ?)",
        (legacy_room.room_id, legacy_updated_at, json.dumps(payload)),
    )

    legacy_store.configure(legacy_dsn, 30)

    startup_payload = json.loads(
        raw_sql(
            legacy_store,
            "SELECT state_json FROM rooms WHERE room_id = ?",
            (legacy_room.room_id,),
        )[0][0]
    )
    assert startup_payload["host_token_hash"] == hash_host_token("host-token")
    assert "host_token" not in startup_payload
    assert raw_sql(
        legacy_store,
        "SELECT updated_at FROM rooms WHERE room_id = ?",
        (legacy_room.room_id,),
    )[0][0] == legacy_updated_at

    migrated_room = legacy_store.get_room("LEGACY")
    assert migrated_room is not None
    assert migrated_room.participants == {}
    rows = raw_sql(
        legacy_store, "SELECT state_json FROM rooms WHERE room_id = ?", ("LEGACY",)
    )
    migrated_payload = json.loads(rows[0][0])
    assert migrated_payload["participants"] == []
    assert migrated_payload["host_token_hash"] == hash_host_token("host-token")
    assert "host_token" not in migrated_payload


def test_host_token_is_hashed_and_survives_store_reload(raw_sql):
    room_data = create_room()
    persisted_payload = json.loads(
        raw_sql(
            store,
            "SELECT state_json FROM rooms WHERE room_id = ?",
            (room_data["room_id"],),
        )[0][0]
    )

    assert persisted_payload["host_token_hash"] == hash_host_token(room_data["host_token"])
    assert "host_token" not in persisted_payload

    service = app.extensions["room_service"]
    service.join(
        "host-before-reload",
        room_data["room_id"],
        "Alex",
        "host-1",
        participant_token=room_data["participant_token"],
        host_token=room_data["host_token"],
    )
    store.configure(store.dsn, 30)

    rejoined = service.join(
        "host-after-reload",
        room_data["room_id"],
        "Alex",
        "host-1",
        participant_token=room_data["participant_token"],
        host_token=room_data["host_token"],
    )

    assert rejoined.participant_id == "host-1"
    service.update_issue("host-after-reload", "Reloaded story", room_data["host_token"])
    reloaded_payload = json.loads(
        raw_sql(
            store,
            "SELECT state_json FROM rooms WHERE room_id = ?",
            (room_data["room_id"],),
        )[0][0]
    )
    assert reloaded_payload["host_token_hash"] == hash_host_token(room_data["host_token"])
    assert "host_token" not in reloaded_payload


def test_host_token_recovers_from_a_stale_participant_token():
    room = create_room()
    service = app.extensions["room_service"]
    initial = service.join(
        "initial-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=room["participant_token"],
        host_token=room["host_token"],
    )
    service.join(
        "recovery-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )

    rejoined = service.join(
        "stale-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=initial.participant_token,
        host_token=room["host_token"],
    )

    assert rejoined.participant_id == "host-1"
    assert rejoined.participant_token != initial.participant_token


def test_host_recovery_uses_the_selected_avatar_color():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    joined = service.join(
        "initial-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=room["participant_token"],
        host_token=room["host_token"],
    )
    service.disconnect("initial-host-sid")

    rejoined = service.join(
        "recovered-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
        avatar_color="#00b4d8",
    )

    assert rejoined.participant_id == "host-1"
    assert rejoined.state["participants"][0]["avatar_color"] == "#00b4d8"


def test_manual_host_rejoin_updates_color_even_with_a_valid_participant_token():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    initial = service.join(
        "initial-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=room["participant_token"],
        host_token=room["host_token"],
    )
    service.disconnect("initial-host-sid")

    rejoined = service.join(
        "recovered-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=initial.participant_token,
        host_token=room["host_token"],
        avatar_color="#00b4d8",
        update_avatar_color=True,
    )

    assert rejoined.participant_id == "host-1"
    assert rejoined.state["participants"][0]["avatar_color"] == "#00b4d8"


def test_host_recovery_can_reuse_the_previous_avatar_color():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    service.join(
        "initial-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )
    service.disconnect("initial-host-sid")
    service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        avatar_color="#f3722c",
    )

    rejoined = service.join(
        "recovered-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
        avatar_color="#f94144",
    )

    assert rejoined.participant_id == "host-1"
    assert rejoined.state["participants"][0]["avatar_color"] == "#f94144"


def test_participant_credential_survives_disconnect():
    room = create_room()
    service = app.extensions["room_service"]
    joined = service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=room["participant_token"],
        host_token=room["host_token"],
    )

    service.disconnect("host-sid")

    disconnected_room = store.get_room(room["room_id"])
    assert disconnected_room is not None
    assert disconnected_room.participants["host-1"].sid is None
    assert service.public_state(room["room_id"])["participants"] == []

    rejoined = service.join(
        "reconnected-host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=joined.participant_token,
    )

    assert rejoined.participant_id == "host-1"
    assert rejoined.participant_token == joined.participant_token
    assert rejoined.state["participant_count"] == 1


def test_participant_avatar_color_survives_reconnect_and_store_reload():
    room = create_room()
    service = app.extensions["room_service"]
    joined = service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
    )
    avatar_color = joined.state["participants"][0]["avatar_color"]

    service.disconnect("guest-sid")
    rejoined = service.join(
        "reconnected-guest-sid",
        room["room_id"],
        "Jordan",
        "different-public-id",
        participant_token=joined.participant_token,
    )

    assert rejoined.state["participants"][0]["avatar_color"] == avatar_color

    database_path = store.dsn
    store.configure(database_path, 30)
    reloaded_room = store.get_room(room["room_id"])

    assert reloaded_room is not None
    assert reloaded_room.participants["guest-1"].avatar_color == avatar_color


def test_entry_colors_are_unique_and_available_colors_are_exposed():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )

    response = app.test_client().get(
        f"/api/rooms/{room['room_id']}/avatar-colors"
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "occupied_avatar_colors": ["#f94144"],
        "own_avatar_color": None,
    }

    with pytest.raises(RoomServiceError, match="already in use"):
        service.join(
            "duplicate-color-sid",
            room["room_id"],
            "Jordan",
            "guest-1",
            avatar_color="#F94144",
        )

    joined = service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        avatar_color="#f3722c",
    )
    assert joined.state["occupied_avatar_colors"] == ["#f3722c", "#f94144"]


def test_avatar_colors_exclude_the_authenticated_participant_color():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    host = service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )
    guest = service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        avatar_color="#f3722c",
    )
    client = app.test_client()
    colors_url = f"/api/rooms/{room['room_id']}/avatar-colors"

    assert client.get(colors_url).get_json() == {
        "occupied_avatar_colors": ["#f3722c", "#f94144"],
        "own_avatar_color": None,
    }
    assert client.get(
        colors_url,
        headers={"X-PointRoom-Host-Token": room["host_token"]},
    ).get_json() == {
        "occupied_avatar_colors": ["#f3722c"],
        "own_avatar_color": "#f94144",
    }
    assert client.get(
        colors_url,
        headers={"X-PointRoom-Participant-Token": guest.participant_token},
    ).get_json() == {
        "occupied_avatar_colors": ["#f94144"],
        "own_avatar_color": "#f3722c",
    }
    assert client.get(
        colors_url,
        headers={"X-PointRoom-Host-Token": "wrong-token"},
    ).get_json() == {
        "occupied_avatar_colors": ["#f3722c", "#f94144"],
        "own_avatar_color": None,
    }
    assert host.participant_id == "host-1"


def test_departed_host_color_is_returned_as_the_authenticated_default():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )
    service.disconnect("host-sid")

    response = app.test_client().get(
        f"/api/rooms/{room['room_id']}/avatar-colors",
        headers={"X-PointRoom-Host-Token": room["host_token"]},
    )

    assert response.get_json() == {
        "occupied_avatar_colors": [],
        "own_avatar_color": "#f94144",
    }


def test_host_color_remains_reserved_after_the_host_disconnects():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )
    service.disconnect("host-sid")

    assert "#f94144" in service.public_avatar_colors(room["room_id"])
    with pytest.raises(RoomServiceError, match="already in use"):
        service.join(
            "replacement-sid",
            room["room_id"],
            "Jordan",
            "guest-1",
            avatar_color="#f94144",
        )


def test_departed_participant_can_reuse_their_reserved_color():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    joined = service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        avatar_color="#f3722c",
    )
    service.disconnect("guest-sid")

    assert "#f3722c" in service.public_avatar_colors(room["room_id"])
    with pytest.raises(RoomServiceError, match="already in use"):
        service.join(
            "replacement-sid",
            room["room_id"],
            "Taylor",
            "guest-2",
            avatar_color="#f3722c",
        )

    rejoined = service.join(
        "reconnected-guest-sid",
        room["room_id"],
        "Jordan",
        "different-public-id",
        participant_token=joined.participant_token,
        avatar_color="#f3722c",
    )

    assert rejoined.participant_id == "guest-1"
    assert rejoined.state["participants"][-1]["avatar_color"] == "#f3722c"
    assert "#f3722c" not in service.public_avatar_colors(
        room["room_id"],
        participant_token=rejoined.participant_token,
    )


def test_duplicate_legacy_avatar_colors_are_normalized():
    room = Room(
        room_id="ABC123",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Legacy colors",
        participants={
            "host-1": Participant(
                participant_id="host-1",
                name="Alex",
                avatar_color="#f94144",
            ),
            "guest-1": Participant(
                participant_id="guest-1",
                name="Jordan",
                avatar_color="#F94144",
            ),
        },
    )

    assert len(room.occupied_avatar_colors()) == 2
    assert room.participants["host-1"].avatar_color == "#f94144"
    assert room.participants["guest-1"].avatar_color != "#f94144"


def test_profile_color_cannot_be_changed_to_another_participants_color():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )
    guest = service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        avatar_color="#f3722c",
    )

    with pytest.raises(RoomServiceError, match="already in use"):
        service.update_profile("guest-sid", "Jordan", "#F94144")

    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    assert persisted_room.participants[guest.participant_id].avatar_color == "#f3722c"


def test_reconnect_keeps_assigned_color_even_when_entry_selects_another_color():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    joined = service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        avatar_color="#f3722c",
    )
    service.disconnect("guest-sid")

    rejoined = service.join(
        "reconnected-guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        participant_token=joined.participant_token,
        avatar_color="#00b4d8",
    )

    assert rejoined.state["participants"][-1]["avatar_color"] == "#f3722c"


def test_manual_participant_rejoin_uses_the_selected_avatar_color():
    room = create_room(avatar_color="#f94144")
    service = app.extensions["room_service"]
    joined = service.join(
        "guest-sid",
        room["room_id"],
        "Jordan",
        "guest-1",
        avatar_color="#f3722c",
    )
    service.disconnect("guest-sid")

    rejoined = service.join(
        "reconnected-guest-sid",
        room["room_id"],
        "Jordan",
        "different-public-id",
        participant_token=joined.participant_token,
        avatar_color="#00b4d8",
        update_avatar_color=True,
    )

    assert rejoined.participant_id == "guest-1"
    assert rejoined.state["participants"][-1]["avatar_color"] == "#00b4d8"


def test_participant_can_update_name_and_avatar_color():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit(
        "update_profile",
        {
            "name": "Alex Updated",
            "avatar_color": "coral",
        },
    )

    events = host.get_received()
    profile = event_payloads(events, "profile_updated")[-1]
    state = event_payloads(events, "room_state")[-1]
    assert profile == {"name": "Alex Updated", "avatar_color": "coral"}
    assert state["participants"][0]["name"] == "Alex Updated"
    assert state["participants"][0]["avatar_color"] == "coral"

    database_path = store.dsn
    store.configure(database_path, 30)
    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.participants["host-1"].name == "Alex Updated"
    assert reloaded_room.participants["host-1"].avatar_color == "coral"


def test_participant_can_save_a_custom_avatar_color():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit(
        "update_profile",
        {
            "name": "Alex",
            "avatar_color": "#12AbEf",
        },
    )

    events = host.get_received()
    profile = event_payloads(events, "profile_updated")[-1]
    state = event_payloads(events, "room_state")[-1]
    assert profile == {"name": "Alex", "avatar_color": "#12abef"}
    assert state["participants"][0]["avatar_color"] == "#12abef"

    host.emit(
        "update_profile",
        {
            "name": "Should Not Save",
            "avatar_color": "not-a-color",
        },
    )

    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Choose a valid avatar color."
    )
    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    assert persisted_room.participants["host-1"].name == "Alex"
    assert persisted_room.participants["host-1"].avatar_color == "#12abef"


def test_history_preserves_each_participant_avatar_color():
    room = create_room()
    service = app.extensions["room_service"]
    service.join("host-sid", room["room_id"], "Alex", "host-1", host_token=room["host_token"])
    service.join("guest-sid", room["room_id"], "Jordan", "guest-1")
    service.update_profile("host-sid", "Alex", "#123456")
    service.vote("host-sid", "5")
    service.vote("guest-sid", "?")
    reveal_service_and_wait(service, "host-sid", room)

    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    assert [vote["avatar_color"] for vote in persisted_room.history[-1]["votes"]] == [
        "#123456",
        persisted_room.participants["guest-1"].avatar_color,
    ]

    database_path = store.dsn
    store.configure(database_path, 30)
    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.history[-1]["votes"][0]["avatar_color"] == "#123456"


def test_history_uses_current_participant_profile_after_profile_change():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()
    host.emit("vote", {"value": "5"})
    host.get_received()
    reveal_and_wait(host, room)
    host.get_received()

    host.emit(
        "update_profile",
        {
            "name": "Alex Updated",
            "avatar_color": "#123456",
        },
    )

    history_events = event_payloads(host.get_received(), "room_history")
    vote = history_events[-1]["history"][0]["votes"][0]
    assert vote["name"] == "Alex Updated"
    assert vote["avatar_color"] == "#123456"


def test_participant_vote_survives_disconnect_and_reconnect():
    room = create_room()
    service = app.extensions["room_service"]
    joined = service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        participant_token=room["participant_token"],
        host_token=room["host_token"],
    )

    service.vote("host-sid", "5")
    service.disconnect("host-sid")

    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    assert persisted_room.participants["host-1"].vote == "5"

    rejoined = service.join(
        "reconnected-host-sid",
        room["room_id"],
        "Alex",
        "different-public-id",
        participant_token=joined.participant_token,
    )

    assert rejoined.own_vote == "5"
    assert rejoined.state["voted_count"] == 1
    assert rejoined.state["participants"][0]["has_voted"] is True


def test_event_rate_limiter_is_bounded_and_evicts_least_recently_used_key():
    event_limiter = EventRateLimiter(max_keys=2)

    assert event_limiter.allow("vote:sid-1", 1, 60)
    assert event_limiter.allow("vote:sid-2", 1, 60)
    assert event_limiter.allow("vote:sid-3", 1, 60)
    assert len(event_limiter.events) == 2
    assert "vote:sid-1" not in event_limiter.events

    event_limiter.remove("vote:sid-2")
    assert len(event_limiter.events) == 1


def test_room_rejects_a_51st_participant():
    room = create_room()
    service = app.extensions["room_service"]
    for participant_number in range(1, MAX_PARTICIPANTS_PER_ROOM):
        service.join(
            f"guest-sid-{participant_number}",
            room["room_id"],
            f"Guest {participant_number}",
            f"guest-{participant_number}",
        )

    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    assert len(persisted_room.participants) == MAX_PARTICIPANTS_PER_ROOM
    with pytest.raises(RoomServiceError, match="limit is 50"):
        service.join("guest-sid-51", room["room_id"], "Guest 51", "guest-51")


def test_socketio_rejects_untrusted_origins_by_default():
    response = app.test_client().get(
        "/socket.io/?EIO=4&transport=polling",
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 400
    assert response.get_data(as_text=True) == '"Not an accepted origin."'


def test_voting_requires_a_current_story():
    room = create_room(issue="   ")
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("vote", {"value": "5"})

    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Add a valid story before voting."
    )
    assert store.rooms[room["room_id"]].participants["host-1"].vote is None


def test_reveal_requires_a_valid_story():
    room = create_room(issue="   ")
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("reveal", {"host_token": room["host_token"]})

    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Add a valid story before revealing."
    )
    assert store.rooms[room["room_id"]].revealed is False


def test_reveal_requires_at_least_one_vote():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("reveal", {"host_token": room["host_token"]})

    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Submit at least one vote before revealing."
    )
    assert store.rooms[room["room_id"]].revealed is False


def test_host_can_configure_and_persist_reveal_countdown():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit(
        "set_reveal_countdown",
        {
            "seconds": 12,
            "host_token": room["host_token"],
        },
    )

    host_state = event_payloads(host.get_received(), "room_state")[-1]
    assert host_state["reveal_countdown_seconds"] == 12
    assert store.get_room(room["room_id"]).reveal_countdown_seconds == 12

    guest.emit(
        "set_reveal_countdown",
        {
            "seconds": 20,
            "host_token": room["host_token"],
        },
    )
    assert received_event(guest, "room_error")[-1]["args"][0]["message"] == (
        "Only the room host can do that."
    )

    host.emit(
        "set_reveal_countdown",
        {
            "seconds": MAX_REVEAL_COUNTDOWN_SECONDS + 1,
            "host_token": room["host_token"],
        },
    )
    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Set the reveal timer to a whole number between "
        f"{MIN_REVEAL_COUNTDOWN_SECONDS} and {MAX_REVEAL_COUNTDOWN_SECONDS} seconds."
    )

    database_path = store.dsn
    store.configure(database_path, 30)
    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.reveal_countdown_seconds == 12


def test_reveal_countdown_allows_a_late_vote_before_revealing():
    room = create_room()
    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    persisted_room.reveal_countdown_seconds = 1
    store.save(persisted_room)

    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit("vote", {"value": "5"})
    host.get_received()
    guest.get_received()
    host.emit("reveal", {"host_token": room["host_token"]})

    reveal_events = guest.get_received()
    countdown_events = [
        event for event in reveal_events if event["name"] == "reveal_countdown"
    ]
    assert countdown_events
    countdown = countdown_events[-1]["args"][0]
    pending_state = [
        event for event in reveal_events if event["name"] == "room_state"
    ][-1]["args"][0]
    assert pending_state["revealed"] is False
    assert pending_state["reveal_countdown_ends_at"] == countdown["ends_at"]

    guest.emit("vote", {"value": "8"})
    socketio.sleep(0.03)
    assert store.get_room(room["room_id"]).revealed is False
    wait_for_reveal(room["room_id"])
    socketio.sleep(0.03)

    revealed_state = event_payloads(guest.get_received(), "room_state")[-1]
    assert revealed_state["revealed"] is True
    assert revealed_state["average"] == 6.5


def test_new_rooms_default_to_five_second_reveal_countdown():
    room = create_room()
    service = app.extensions["room_service"]
    joined = service.join(
        "host-sid",
        room["room_id"],
        "Alex",
        "host-1",
        host_token=room["host_token"],
    )

    assert DEFAULT_REVEAL_COUNTDOWN_SECONDS == 5
    assert joined.state["reveal_countdown_seconds"] == DEFAULT_REVEAL_COUNTDOWN_SECONDS


def test_participant_id_reuse_cannot_hijack_identity_or_private_vote():
    room = create_room()
    owner = socketio.test_client(app)
    join(owner, room, "Alex", "host-1", room["host_token"])
    owner.get_received()

    owner.emit("vote", {"value": "5"})
    owner.get_received()

    attacker = socketio.test_client(app)
    join(attacker, room, "Mallory", "host-1")

    attacker_events = attacker.get_received()
    identity_events = [
        event for event in attacker_events if event["name"] == "your_identity"
    ]
    attacker_state = [
        event for event in attacker_events if event["name"] == "room_state"
    ][-1]["args"][0]

    assert identity_events[-1]["args"][0]["participant_id"] != "host-1"
    assert [participant["name"] for participant in attacker_state["participants"]] == [
        "Alex",
        "Mallory",
    ]
    assert [event for event in attacker_events if event["name"] == "your_vote"][-1][
        "args"
    ][0]["value"] is None

    attacker.emit("vote", {"value": "8"})

    assert store.rooms[room["room_id"]].participants["host-1"].vote == "5"


def test_plain_room_join_cannot_recover_the_host():
    room = create_room()
    service = app.extensions["room_service"]

    joined = service.join(
        "untrusted-sid",
        room["room_id"],
        "Mallory",
        room["participant_id"],
    )

    assert joined.participant_id != room["participant_id"]
    assert joined.state["participants"][0]["is_host"] is False
    assert store.get_room(room["room_id"]).host_participant_id == room["participant_id"]


def test_joining_another_room_leaves_the_previous_socket_room():
    first_room = create_room(name="Host one", participant_id="host-1")
    second_room = create_room(name="Host two", participant_id="host-2")
    client = socketio.test_client(app)

    join(client, first_room, "Alex", "guest-1")
    client.get_received()
    join(client, second_room, "Alex", "guest-1")
    client.get_received()

    assert store.rooms[first_room["room_id"]].participants["guest-1"].sid is None
    assert store.rooms[first_room["room_id"]].public_state()["participants"] == []
    assert "guest-1" in store.rooms[second_room["room_id"]].participants

    observer = socketio.test_client(app)
    join(observer, first_room, "Jordan", "guest-2")
    observer.get_received()

    assert not received_event(client, "room_state")

    client.disconnect()
    assert store.rooms[second_room["room_id"]].participants["guest-1"].sid is None
    assert store.rooms[second_room["room_id"]].public_state()["participants"] == []


def test_room_creation_is_rate_limited():
    original_enabled = app.config["RATELIMIT_ENABLED"]
    original_limit = app.config["ROOM_CREATE_RATE_LIMIT"]
    app.config["RATELIMIT_ENABLED"] = True
    app.config["ROOM_CREATE_RATE_LIMIT"] = "2 per minute"
    limiter.enabled = True
    try:
        client = app.test_client()
        request_options = {"environ_base": {"REMOTE_ADDR": "198.51.100.10"}}
        for participant_id in ("host-1", "host-2"):
            response = client.post(
                "/api/rooms",
                json={"name": "Alex", "participant_id": participant_id},
                **request_options,
            )
            assert response.status_code == 201

        response = client.post(
            "/api/rooms",
            json={"name": "Alex", "participant_id": "host-3"},
            **request_options,
        )
        assert response.status_code == 429
        assert response.get_json() == {"error": "Too many requests. Please try again later."}
    finally:
        app.config["RATELIMIT_ENABLED"] = original_enabled
        app.config["ROOM_CREATE_RATE_LIMIT"] = original_limit
        limiter.enabled = False


def test_join_events_are_rate_limited_per_client():
    original_enabled = app.config["RATELIMIT_ENABLED"]
    original_limit = app.config["SOCKET_JOIN_RATE_LIMIT"]
    app.config["RATELIMIT_ENABLED"] = True
    app.config["SOCKET_JOIN_RATE_LIMIT"] = 1
    event_rate_limiter.clear()
    try:
        client = socketio.test_client(app)
        payload = {
            "room_id": "MISSING",
            "name": "Guest",
            "participant_id": "guest-1",
        }
        client.emit("join_room", payload)
        assert received_event(client, "room_error")[-1]["args"][0]["message"] == (
            "That room does not exist."
        )
        client.emit("join_room", payload)
        assert received_event(client, "room_error")[-1]["args"][0]["message"] == (
            "Too many requests. Please try again later."
        )
    finally:
        event_rate_limiter.clear()
        app.config["RATELIMIT_ENABLED"] = original_enabled
        app.config["SOCKET_JOIN_RATE_LIMIT"] = original_limit


def test_host_socket_events_are_rate_limited_per_client():
    original_enabled = app.config["RATELIMIT_ENABLED"]
    original_limit = app.config["SOCKET_HOST_ACTION_RATE_LIMIT"]
    app.config["RATELIMIT_ENABLED"] = True
    app.config["SOCKET_HOST_ACTION_RATE_LIMIT"] = 1
    event_rate_limiter.clear()
    try:
        room = create_room()
        host = socketio.test_client(app)
        join(host, room, "Alex", "host-1", room["host_token"])
        host.get_received()

        host.emit("reveal", {"host_token": room["host_token"]})
        assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
            "Submit at least one vote before revealing."
        )

        host.emit("reveal", {"host_token": room["host_token"]})
        assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
            "Too many requests. Please try again later."
        )
    finally:
        event_rate_limiter.clear()
        app.config["RATELIMIT_ENABLED"] = original_enabled
        app.config["SOCKET_HOST_ACTION_RATE_LIMIT"] = original_limit


def test_joining_a_missing_room_returns_explicit_error():
    guest = socketio.test_client(app)
    guest.emit(
        "join_room",
        {
            "room_id": "MISSING",
            "name": "Guest",
            "participant_id": "guest-1",
        },
    )

    errors = [event for event in guest.get_received() if event["name"] == "room_error"]
    assert errors[-1]["args"][0]["message"] == "That room does not exist."


def test_insecure_requests_redirect_when_https_is_required():
    original_require_https = app.config["REQUIRE_HTTPS"]
    original_testing = app.config["TESTING"]
    original_public_base_url = app.config["PUBLIC_BASE_URL"]
    app.config["REQUIRE_HTTPS"] = True
    app.config["TESTING"] = False
    app.config["PUBLIC_BASE_URL"] = "https://pointroom.test"
    try:
        response = app.test_client().get("/")
    finally:
        app.config["REQUIRE_HTTPS"] = original_require_https
        app.config["TESTING"] = original_testing
        app.config["PUBLIC_BASE_URL"] = original_public_base_url

    assert response.status_code == 308
    assert response.headers["Location"] == "https://pointroom.test/"


def test_insecure_redirect_ignores_untrusted_host_header():
    original_require_https = app.config["REQUIRE_HTTPS"]
    original_testing = app.config["TESTING"]
    original_public_base_url = app.config["PUBLIC_BASE_URL"]
    app.config["REQUIRE_HTTPS"] = True
    app.config["TESTING"] = False
    app.config["PUBLIC_BASE_URL"] = "https://pointroom.test"
    try:
        response = app.test_client().get("/room/ABC123?source=test", headers={"Host": "evil.example"})
    finally:
        app.config["REQUIRE_HTTPS"] = original_require_https
        app.config["TESTING"] = original_testing
        app.config["PUBLIC_BASE_URL"] = original_public_base_url

    assert response.status_code == 308
    assert response.headers["Location"] == "https://pointroom.test/room/ABC123?source=test"


def test_https_startup_requires_certificate_or_trusted_proxy():
    original_require_https = app.config["REQUIRE_HTTPS"]
    app.config["REQUIRE_HTTPS"] = True
    try:
        with pytest.raises(RuntimeError, match="HTTPS is required"):
            _validate_transport_config(app)
    finally:
        app.config["REQUIRE_HTTPS"] = original_require_https


def test_proxy_authentication_requires_secure_trusted_proxy_configuration():
    original_require_https = app.config["REQUIRE_HTTPS"]
    original_trust_proxy = app.config["TRUST_PROXY"]
    original_authentication_required = app.config["AUTHENTICATION_REQUIRED"]
    app.config["REQUIRE_HTTPS"] = True
    app.config["TRUST_PROXY"] = False
    app.config["AUTHENTICATION_REQUIRED"] = True
    try:
        with pytest.raises(RuntimeError, match="Proxy authentication requires"):
            _validate_transport_config(app)
    finally:
        app.config["REQUIRE_HTTPS"] = original_require_https
        app.config["TRUST_PROXY"] = original_trust_proxy
        app.config["AUTHENTICATION_REQUIRED"] = original_authentication_required


def test_proxy_authentication_requires_identity_header():
    original_require_https = app.config["REQUIRE_HTTPS"]
    original_testing = app.config["TESTING"]
    original_trust_proxy = app.config["TRUST_PROXY"]
    original_authentication_required = app.config["AUTHENTICATION_REQUIRED"]
    app.config["REQUIRE_HTTPS"] = True
    app.config["TESTING"] = False
    app.config["TRUST_PROXY"] = True
    app.config["AUTHENTICATION_REQUIRED"] = True
    try:
        client = app.test_client()
        response = client.get("/", base_url="https://pointroom.test")
        assert response.status_code == 401
        assert response.get_json() == {"error": "Authentication required."}

        response = client.get(
            "/",
            base_url="https://pointroom.test",
            headers={"X-Forwarded-User": "alex@example.com"},
        )
        assert response.status_code == 200
        assert client.get("/health").status_code == 200
    finally:
        app.config["REQUIRE_HTTPS"] = original_require_https
        app.config["TESTING"] = original_testing
        app.config["TRUST_PROXY"] = original_trust_proxy
        app.config["AUTHENTICATION_REQUIRED"] = original_authentication_required


def test_socketio_requires_proxy_identity_header():
    original_require_https = app.config["REQUIRE_HTTPS"]
    original_testing = app.config["TESTING"]
    original_trust_proxy = app.config["TRUST_PROXY"]
    original_authentication_required = app.config["AUTHENTICATION_REQUIRED"]
    app.config["REQUIRE_HTTPS"] = True
    app.config["TESTING"] = False
    app.config["TRUST_PROXY"] = True
    app.config["AUTHENTICATION_REQUIRED"] = True
    try:
        unauthenticated_client = socketio.test_client(app)
        assert not unauthenticated_client.is_connected()

        authenticated_client = socketio.test_client(
            app,
            headers={"X-Forwarded-User": "alex@example.com"},
        )
        assert authenticated_client.is_connected()
    finally:
        app.config["REQUIRE_HTTPS"] = original_require_https
        app.config["TESTING"] = original_testing
        app.config["TRUST_PROXY"] = original_trust_proxy
        app.config["AUTHENTICATION_REQUIRED"] = original_authentication_required


def test_secure_responses_include_hsts_header():
    original_require_https = app.config["REQUIRE_HTTPS"]
    original_testing = app.config["TESTING"]
    app.config["REQUIRE_HTTPS"] = True
    app.config["TESTING"] = False
    try:
        response = app.test_client().get("/", base_url="https://pointroom.test")
    finally:
        app.config["REQUIRE_HTTPS"] = original_require_https
        app.config["TESTING"] = original_testing

    assert response.status_code == 200
    assert response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_responses_include_security_headers_and_content_security_policy():
    response = app.test_client().get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    content_security_policy = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in content_security_policy
    assert "object-src 'none'" in content_security_policy
    assert "script-src 'self' https://cdn.socket.io https://unpkg.com" in content_security_policy
    assert "connect-src 'self'" in content_security_policy


def test_votes_are_private_until_host_reveals():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit("vote", {"value": "5"})

    host_states = received_event(host, "room_state")
    guest_states = received_event(guest, "room_state")
    assert host_states[-1]["args"][0]["voted_count"] == 1
    assert guest_states[-1]["args"][0]["participants"][0]["vote"] is None
    assert guest_states[-1]["args"][0]["participants"][0]["has_voted"] is True

    reveal_and_wait(host, room)

    revealed_state = received_event(guest, "room_state")[-1]["args"][0]
    assert revealed_state["revealed"] is True
    assert revealed_state["participants"][0]["vote"] == "5"
    assert revealed_state["average"] == 5.0


def test_selecting_the_same_card_again_clears_vote_for_the_room():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit("vote", {"value": "5"})
    host.get_received()
    guest.get_received()
    host.emit("vote", {"value": None})

    host_events = host.get_received()
    guest_events = guest.get_received()
    host_state = [event for event in host_events if event["name"] == "room_state"][-1]["args"][0]
    guest_state = [event for event in guest_events if event["name"] == "room_state"][-1]["args"][0]
    assert host_state["voted_count"] == 0
    assert host_state["participants"][0]["has_voted"] is False
    assert guest_state["voted_count"] == 0
    assert guest_state["participants"][0]["has_voted"] is False
    vote_events = [event for event in host_events if event["name"] == "your_vote"]
    assert vote_events[-1]["args"][0]["value"] is None


def test_only_host_can_reveal_and_reset():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    guest.emit("reveal", {"host_token": "wrong-token"})
    assert received_event(guest, "room_error")[-1]["args"][0]["message"] == "Only the room host can do that."

    host.emit("vote", {"value": "8"})
    reveal_and_wait(host, room)
    host.emit("reset_round", {"host_token": room["host_token"]})

    host_events = host.get_received()
    reset_state = [event for event in host_events if event["name"] == "room_state"][-1]["args"][0]
    assert reset_state["revealed"] is False
    assert reset_state["voted_count"] == 0
    vote_events = [event for event in host_events if event["name"] == "your_vote"]
    assert vote_events[-1]["args"][0]["value"] is None


def test_non_ascii_host_token_is_rejected_without_socket_error():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("reveal", {"host_token": "é"})

    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Only the room host can do that."
    )


def test_invalid_cards_are_rejected():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("vote", {"value": "999"})

    assert received_event(host, "room_error")[-1]["args"][0]["message"] == "That card is not in this deck."


def test_host_story_updates_are_broadcast_to_guests():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    story = (
        "[User Story 1234](https://dev.azure.com/example-org/Example%20Project/"
        "_workitems/edit/1234): Add CSV export for estimation history"
    )
    host.emit("update_issue", {"issue": story, "host_token": room["host_token"]})

    host_state = received_event(host, "room_state")[-1]["args"][0]
    guest_events = guest.get_received()
    guest_state = event_payloads(guest_events, "room_state")[-1]
    assert host_state["issue"] == story
    assert guest_state["issue"] == story


def test_failed_room_save_rolls_back_in_memory_state(monkeypatch):
    room_data = create_room()
    room = store.get_room(room_data["room_id"])
    assert room is not None
    service = app.extensions["room_service"]
    sid = "save-failure-sid"
    service.join(
        sid,
        room.room_id,
        "Alex",
        "host-1",
        participant_token=room_data["participant_token"],
        host_token=room_data["host_token"],
    )

    def fail_upsert(records):
        raise StorageFailure("database unavailable")

    monkeypatch.setattr(store.adapter, "upsert_rooms", fail_upsert)
    with pytest.raises(StorageFailure):
        service.update_issue(sid, "Unsaved story", room_data["host_token"])

    assert room.issue == "Test story"
    assert store.connections[sid] == (room.room_id, "host-1")


def test_room_save_does_not_hold_global_lock_during_database_write(monkeypatch):
    room_data = create_room()
    room = store.get_room(room_data["room_id"])
    assert room is not None
    save_started = Event()
    release_save = Event()
    save_errors = []
    original_upsert = store.adapter.upsert_rooms

    def blocked_upsert(records):
        save_started.set()
        if not release_save.wait(2):
            raise StorageFailure("timed out waiting for test release")
        original_upsert(records)

    monkeypatch.setattr(store.adapter, "upsert_rooms", blocked_upsert)

    def save_room():
        try:
            store.save(room)
        except Exception as error:
            save_errors.append(error)

    save_thread = Thread(target=save_room, daemon=True)
    save_thread.start()
    try:
        assert save_started.wait(2)
        assert store.lock.acquire(timeout=0.5)
        store.lock.release()
    finally:
        release_save.set()
        save_thread.join(2)

    assert not save_thread.is_alive()
    assert save_errors == []


def test_expired_room_cleanup_does_not_hold_global_lock_during_database_io(monkeypatch):
    expired_at = time.time() - (31 * 24 * 60 * 60)
    room_ids = {"CLEAN1"}
    expired_room = Room(
        room_id="CLEAN1",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Expired story",
        updated_at=expired_at,
    )
    with store.lock:
        store.rooms[expired_room.room_id] = expired_room
        store.room_ids.add(expired_room.room_id)
    store.save(expired_room, touch_updated_at=False)

    fetch_started = Event()
    release_fetch = Event()
    cleanup_errors = []
    original_fetch = store.adapter.fetch_expired_room_ids

    def blocked_fetch(cutoff, limit=ROOM_CLEANUP_BATCH_SIZE):
        fetch_started.set()
        if not release_fetch.wait(2):
            raise StorageFailure("timed out waiting for test release")
        return original_fetch(cutoff, limit)

    monkeypatch.setattr(store.adapter, "fetch_expired_room_ids", blocked_fetch)

    def cleanup():
        try:
            store.cleanup_expired()
        except Exception as error:
            cleanup_errors.append(error)

    cleanup_thread = Thread(target=cleanup, daemon=True)
    cleanup_thread.start()
    try:
        assert fetch_started.wait(2)
        assert store.lock.acquire(timeout=0.5)
        store.lock.release()
    finally:
        release_fetch.set()
        cleanup_thread.join(2)

    assert not cleanup_thread.is_alive()
    assert cleanup_errors == []
    assert store.get_room(expired_room.room_id) is None


def test_room_delete_does_not_hold_global_lock_during_database_io(monkeypatch):
    room_data = create_room()
    delete_started = Event()
    release_delete = Event()
    delete_errors = []
    original_delete = store.adapter.delete_room

    def blocked_delete(room_id):
        delete_started.set()
        if not release_delete.wait(2):
            raise StorageFailure("timed out waiting for test release")
        original_delete(room_id)

    monkeypatch.setattr(store.adapter, "delete_room", blocked_delete)

    def delete_room():
        try:
            store.delete(room_data["room_id"])
        except Exception as error:
            delete_errors.append(error)

    delete_thread = Thread(target=delete_room, daemon=True)
    delete_thread.start()
    try:
        assert delete_started.wait(2)
        assert store.lock.acquire(timeout=0.5)
        store.lock.release()
    finally:
        release_delete.set()
        delete_thread.join(2)

    assert not delete_thread.is_alive()
    assert delete_errors == []
    assert store.get_room(room_data["room_id"]) is None


def test_failed_history_removal_rolls_back_in_memory_and_database(monkeypatch):
    room_data = create_room()
    room = store.get_room(room_data["room_id"])
    assert room is not None
    service = app.extensions["room_service"]
    sid = "history-failure-sid"
    service.join(
        sid,
        room.room_id,
        "Alex",
        "host-1",
        participant_token=room_data["participant_token"],
        host_token=room_data["host_token"],
    )
    service.vote(sid, "5")
    reveal_service_and_wait(service, sid, room_data)
    assert len(room.history) == 1

    def fail_upsert(records):
        raise StorageFailure("database unavailable")

    monkeypatch.setattr(store.adapter, "upsert_rooms", fail_upsert)
    with pytest.raises(StorageFailure):
        service.remove_history_story(sid, 1, room_data["host_token"])

    assert len(room.history) == 1
    monkeypatch.undo()
    store.configure(store.dsn, 30)
    reloaded_room = store.get_room(room.room_id)
    assert reloaded_room is not None
    assert len(reloaded_room.history) == 1


def test_corrupt_persisted_room_is_logged_and_counted(dsn_factory, raw_sql, monkeypatch):
    corrupt_dsn = dsn_factory("corrupt")
    corrupt_store = RoomStore(corrupt_dsn)
    raw_sql(
        corrupt_store,
        "INSERT INTO rooms (room_id, updated_at, state_json) VALUES (?, ?, ?)",
        ("BROKEN", time.time(), "{not valid json"),
    )

    logged_events = []
    def fail_fetch_states(self):
        raise AssertionError("startup must not fetch every room snapshot")

    monkeypatch.setattr(
        room_store.logger,
        "error",
        lambda message, **kwargs: logged_events.append((message, kwargs)),
    )
    monkeypatch.setattr(SqliteAdapter, "fetch_states", fail_fetch_states)
    corrupt_store.configure(corrupt_dsn, 30)

    assert corrupt_store.rooms == {}
    assert corrupt_store.room_count() == 1
    assert corrupt_store.room_ids == set()
    assert corrupt_store.load_errors == 0
    assert corrupt_store.get_room("BROKEN") is None
    assert corrupt_store.load_errors == 1
    assert logged_events[0][1]["extra"] == {
        "event": "room_load_failed",
        "room_id": "BROKEN",
        "status": "degraded",
    }


def test_rooms_older_than_retention_window_are_removed(raw_sql, monkeypatch):
    room = create_room()
    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    expired_at = time.time() - (31 * 24 * 60 * 60)
    persisted_room.updated_at = expired_at
    raw_sql(
        store,
        "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
        (expired_at, room["room_id"]),
    )
    monkeypatch.setattr(
        store.adapter,
        "fetch_room_ids",
        lambda: pytest.fail("cleanup must not fetch every room id"),
    )

    removed_count = store.cleanup_expired()

    assert removed_count == 1
    assert store.get_room(room["room_id"]) is None
    assert room["room_id"] not in store.rooms


def test_expired_room_cleanup_detaches_connections(raw_sql):
    room = create_room()
    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    store.connections["expired-sid"] = (room["room_id"], "host-1")
    expired_at = time.time() - (31 * 24 * 60 * 60)
    persisted_room.updated_at = expired_at
    raw_sql(
        store,
        "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
        (expired_at, room["room_id"]),
    )

    store.cleanup_expired()

    assert "expired-sid" not in store.connections


def test_expired_room_cleanup_uses_bounded_batches(raw_sql, monkeypatch):
    expired_at = time.time() - (31 * 24 * 60 * 60)
    records = [
        (f"EX{i:04d}", expired_at, "{}")
        for i in range(ROOM_CLEANUP_BATCH_SIZE * 2 + 5)
    ]
    with store.adapter.connection() as connection:
        connection.cursor().executemany(
            store.adapter.sql(
                "INSERT INTO rooms (room_id, updated_at, state_json) "
                "VALUES (?, ?, ?)"
            ),
            records,
        )

    batch_sizes = []
    original_fetch = store.adapter.fetch_expired_room_ids

    def record_batch_size(cutoff, limit=ROOM_CLEANUP_BATCH_SIZE):
        room_ids = original_fetch(cutoff, limit)
        batch_sizes.append(len(room_ids))
        return room_ids

    monkeypatch.setattr(
        store.adapter,
        "fetch_expired_room_ids",
        record_batch_size,
    )

    assert store.cleanup_expired() == len(records)
    assert len(batch_sizes) >= 3
    assert max(batch_sizes) <= ROOM_CLEANUP_BATCH_SIZE
    assert raw_sql(
        store,
        "SELECT room_id FROM rooms WHERE updated_at < ?",
        (time.time(),),
    ) == []


def test_scheduled_retention_cleanup_removes_expired_rooms(dsn_factory, raw_sql, monkeypatch):
    cleanup_dsn = dsn_factory("scheduled-cleanup")
    cleanup_store = RoomStore(cleanup_dsn)
    cleanup_room = Room(
        room_id="CLEAN1",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Expired story",
    )
    cleanup_store.rooms[cleanup_room.room_id] = cleanup_room
    cleanup_store.save(cleanup_room)
    expired_at = time.time() - (31 * 24 * 60 * 60)
    raw_sql(
        cleanup_store,
        "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
        (expired_at, cleanup_room.room_id),
    )
    monkeypatch.setenv("POINTROOM_DATABASE_URL", cleanup_dsn)
    monkeypatch.setenv("POINTROOM_RETENTION_DAYS", "30")

    assert run_retention_cleanup() == 1
    assert raw_sql(
        cleanup_store,
        "SELECT room_id FROM rooms WHERE room_id = ?",
        (cleanup_room.room_id,),
    ) == []


def test_story_cannot_change_after_reveal():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("vote", {"value": "3"})
    host.get_received()
    reveal_and_wait(host, room)
    host.get_received()
    host.emit(
        "update_issue",
        {"issue": "This should wait", "host_token": room["host_token"]},
    )

    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Start a new round before changing the story."
    )
    assert store.rooms[room["room_id"]].issue == "Test story"


def test_host_can_start_new_story_and_keep_revealed_history():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit(
        "update_issue",
        {"issue": "First user story", "host_token": room["host_token"]},
    )
    host.get_received()
    guest.get_received()
    host.emit("vote", {"value": "5"})
    guest.emit("vote", {"value": "8"})
    host.get_received()
    guest.get_received()
    reveal_and_wait(host, room)
    host.get_received()
    guest.get_received()

    host.emit(
        "start_new_story",
        {"issue": "Second user story", "host_token": room["host_token"]},
    )

    host_events = host.get_received()
    guest_events = guest.get_received()
    host_state = event_payloads(host_events, "room_state")[-1]
    guest_state = event_payloads(guest_events, "room_state")[-1]
    guest_history = event_payloads(guest_events, "room_history")[-1]["history"]
    assert host_state["issue"] == "Second user story"
    assert guest_state["issue"] == "Second user story"
    assert guest_state["round_number"] == 2
    assert guest_state["revealed"] is False
    assert guest_state["voted_count"] == 0
    assert guest_history[0]["issue"] == "First user story"
    assert [(vote["name"], vote["vote"]) for vote in guest_history[0]["votes"]] == [
        ("Alex", "5"),
        ("Jordan", "8"),
    ]


def test_host_can_change_deck_before_voting_and_persist_it():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit(
        "set_deck",
        {"deck": "t_shirt", "host_token": room["host_token"]},
    )

    host_state = event_payloads(host.get_received(), "room_state")[-1]
    guest_state = event_payloads(guest.get_received(), "room_state")[-1]
    assert host_state["deck_name"] == "t_shirt"
    assert host_state["deck"] == list(T_SHIRT_DECK)
    assert guest_state["deck_name"] == "t_shirt"

    database_path = store.dsn
    store.configure(database_path, 30)
    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.deck == T_SHIRT_DECK


def test_deck_change_requires_host_and_a_fresh_round():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    guest.emit(
        "set_deck",
        {"deck": "t_shirt", "host_token": room["host_token"]},
    )
    assert received_event(guest, "room_error")[-1]["args"][0]["message"] == (
        "Only the room host can do that."
    )

    host.emit("vote", {"value": "5"})
    host.get_received()
    host.emit(
        "set_deck",
        {"deck": "t_shirt", "host_token": room["host_token"]},
    )
    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "Start a new round before changing the card deck."
    )


def test_host_can_remove_a_past_story_from_history():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit("vote", {"value": "5"})
    guest.emit("vote", {"value": "8"})
    host.get_received()
    guest.get_received()
    reveal_and_wait(host, room)
    host.get_received()
    guest.get_received()
    host.emit(
        "start_new_story",
        {"issue": "Second story", "host_token": room["host_token"]},
    )
    host.get_received()
    guest.get_received()

    host.emit(
        "remove_history_story",
        {"round": 1, "host_token": room["host_token"]},
    )

    host_events = host.get_received()
    guest_events = guest.get_received()
    host_state = event_payloads(host_events, "room_state")[-1]
    guest_state = event_payloads(guest_events, "room_state")[-1]
    assert event_payloads(host_events, "room_history")[-1]["history"] == []
    assert event_payloads(guest_events, "room_history")[-1]["history"] == []
    assert store.get_room(room["room_id"]).history == []


def test_only_host_can_remove_history_or_delete_session():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    guest.emit("remove_history_story", {"round": 1, "host_token": "wrong"})
    assert received_event(guest, "room_error")[-1]["args"][0]["message"] == (
        "Only the room host can do that."
    )
    guest.emit("delete_session", {"host_token": "wrong"})
    assert received_event(guest, "room_error")[-1]["args"][0]["message"] == (
        "Only the room host can do that."
    )
    assert store.get_room(room["room_id"]) is not None


def test_host_can_delete_the_session_for_everyone(raw_sql):
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit("delete_session", {"host_token": room["host_token"]})

    assert received_event(host, "session_deleted")[-1]["args"][0]["message"] == (
        "The session was deleted by the host."
    )
    assert received_event(guest, "session_deleted")[-1]["args"][0]["message"] == (
        "The session was deleted by the host."
    )
    assert store.get_room(room["room_id"]) is None
    assert raw_sql(
        store, "SELECT room_id FROM rooms WHERE room_id = ?", (room["room_id"],)
    ) == []


def test_duplicate_story_is_rejected_for_new_story_and_story_update():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("update_issue", {"issue": "First user story", "host_token": room["host_token"]})
    host.get_received()
    host.emit("vote", {"value": "5"})
    host.get_received()
    reveal_and_wait(host, room)
    host.get_received()

    host.emit(
        "start_new_story",
        {"issue": "Second user story", "host_token": room["host_token"]},
    )
    host.get_received()
    host.emit(
        "start_new_story",
        {"issue": "FIRST USER STORY", "host_token": room["host_token"]},
    )
    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "That story has already been added in this session."
    )

    host.emit("update_issue", {"issue": "First user story", "host_token": room["host_token"]})
    assert received_event(host, "room_error")[-1]["args"][0]["message"] == (
        "That story has already been added in this session."
    )


def test_start_new_round_clears_current_story():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("update_issue", {"issue": "Story to clear", "host_token": room["host_token"]})
    host.get_received()
    host.emit("vote", {"value": "3"})
    host.get_received()
    reveal_and_wait(host, room)
    host.get_received()
    host.emit("reset_round", {"host_token": room["host_token"]})

    reset_events = host.get_received()
    state = event_payloads(reset_events, "room_state")[-1]
    assert state["issue"] == ""
    assert state["round_number"] == 2
    assert state["revealed"] is False
    assert state["voted_count"] == 0
    assert event_payloads(reset_events, "room_history")[-1]["history"][0]["issue"] == "Story to clear"


def test_skipped_story_is_kept_in_session_history():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit(
        "start_new_story",
        {"issue": "Next story", "host_token": room["host_token"]},
    )

    events = guest.get_received()
    state = event_payloads(events, "room_state")[-1]
    assert state["issue"] == "Next story"
    assert state["round_number"] == 2
    assert event_payloads(events, "room_history")[-1]["history"] == [
        {
            "round": 1,
            "issue": "Test story",
            "status": "skipped",
            "votes": [],
            "voter_count": 0,
            "average": None,
            "distribution": {card: 0 for card in state["deck"]},
        }
    ]


def test_app_factory_keeps_injected_dependencies_isolated(dsn_factory):
    first_store = RoomStore(dsn_factory("first"))
    second_store = RoomStore(dsn_factory("second"))
    first_socketio = SocketIO()
    second_socketio = SocketIO()
    first_limiter = Limiter(key_func=lambda: "first", default_limits=[])
    second_limiter = Limiter(key_func=lambda: "second", default_limits=[])
    first_app = create_app(
        {"TESTING": True, "REQUIRE_HTTPS": False},
        room_store=first_store,
        socketio_instance=first_socketio,
        limiter_instance=first_limiter,
    )
    second_app = create_app(
        {"TESTING": True, "REQUIRE_HTTPS": False},
        room_store=second_store,
        socketio_instance=second_socketio,
        limiter_instance=second_limiter,
    )

    assert first_app.extensions["room_store"] is first_store
    assert second_app.extensions["room_store"] is second_store
    assert first_app.extensions["room_store"] is not second_app.extensions["room_store"]
    assert first_app.extensions["pointroom_socketio"] is first_socketio
    assert second_app.extensions["pointroom_socketio"] is second_socketio
    assert first_app.extensions["pointroom_socketio"] is not second_app.extensions["pointroom_socketio"]
    assert first_limiter in first_app.extensions["limiter"]
    assert second_limiter in second_app.extensions["limiter"]
    assert first_limiter not in second_app.extensions["limiter"]
    assert second_limiter not in first_app.extensions["limiter"]

    response = first_app.test_client().post(
        "/api/rooms",
        json={"name": "Alex", "participant_id": "host-1"},
    )

    assert response.status_code == 201
    assert len(first_store.rooms) == 1
    assert second_store.rooms == {}

    first_client = first_app.extensions["pointroom_socketio"].test_client(first_app)
    second_client = second_app.extensions["pointroom_socketio"].test_client(second_app)
    first_client.emit(
        "join_room",
        {
            "room_id": response.get_json()["room_id"],
            "name": "Alex",
            "participant_id": "host-1",
            "participant_token": response.get_json()["participant_token"],
            "host_token": response.get_json()["host_token"],
        },
    )
    second_client.emit(
        "join_room",
        {
            "room_id": response.get_json()["room_id"],
            "name": "Jordan",
            "participant_id": "guest-1",
        },
    )
    assert any(event["name"] == "room_state" for event in first_client.get_received())
    second_events = second_client.get_received()
    assert any(event["name"] == "room_error" for event in second_events)
    first_client.disconnect()
    second_client.disconnect()


def test_dsn_defaults_to_the_bundled_sqlite_file(monkeypatch):
    monkeypatch.delenv("POINTROOM_DATABASE_URL", raising=False)
    monkeypatch.delenv("POINTROOM_DATABASE_PATH", raising=False)

    assert resolve_dsn().endswith("pointroom.sqlite3")

    monkeypatch.setenv("POINTROOM_DATABASE_URL", "postgresql://db/pointroom")
    assert resolve_dsn() == "postgresql://db/pointroom"


def test_dsn_selects_the_matching_storage_adapter(tmp_path):
    assert adapter_class_for_dsn(str(tmp_path / "rooms.sqlite3")) is SqliteAdapter
    assert adapter_class_for_dsn("sqlite:///var/lib/pointroom/rooms.sqlite3") is SqliteAdapter
    assert adapter_class_for_dsn("postgresql://user:pass@db:5432/pointroom") is PostgresAdapter
    assert adapter_class_for_dsn("postgres://user:pass@db:5432/pointroom") is PostgresAdapter


def test_component_database_config_preserves_reserved_password_characters(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    monkeypatch.delenv("POINTROOM_DATABASE_URL", raising=False)
    monkeypatch.setenv("POINTROOM_DATABASE_HOST", "postgres")
    monkeypatch.setenv("POINTROOM_DATABASE_PORT", "5432")
    monkeypatch.setenv("POINTROOM_DATABASE_NAME", "pointroom")
    monkeypatch.setenv("POINTROOM_DATABASE_USER", "pointroom")
    monkeypatch.setenv("POINTROOM_DATABASE_PASSWORD", "review@password:42/with#reserved")

    dsn = resolve_dsn()
    parsed = psycopg.conninfo.conninfo_to_dict(dsn)

    assert adapter_class_for_dsn(dsn) is PostgresAdapter
    assert parsed["host"] == "postgres"
    assert parsed["password"] == "review@password:42/with#reserved"


def test_sqlite_adapter_creates_its_parent_directory(tmp_path):
    adapter = SqliteAdapter(str(tmp_path / "nested" / "rooms.sqlite3"))

    assert (tmp_path / "nested").is_dir()
    adapter.initialize()
    assert adapter.ping() is True


def test_sqlite_adapter_indexes_room_expiration_timestamp(tmp_path):
    adapter = SqliteAdapter(str(tmp_path / "rooms.sqlite3"))
    adapter.initialize()

    with adapter.connection() as connection:
        indexes = connection.execute("PRAGMA index_list('rooms')").fetchall()

    assert any(index[1] == "idx_rooms_updated_at" for index in indexes)


def test_index_loads_frontend_as_module_script():
    response = app.test_client().get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'script type="module" src="/static/app.js"' in page


def test_room_lifecycle_flow_create_join_vote_reveal_new_story_delete():
    room = create_room(issue="Initial story")
    host = socketio.test_client(app)
    guest = socketio.test_client(app)

    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit("vote", {"value": "5"})
    guest.emit("vote", {"value": "8"})
    host.get_received()
    guest.get_received()

    reveal_and_wait(host, room)
    host_events = host.get_received()
    host_state = event_payloads(host_events, "room_state")[-1]
    assert host_state["revealed"] is True
    assert host_state["distribution"]["5"] == 1
    assert host_state["distribution"]["8"] == 1

    host.emit(
        "start_new_story",
        {"issue": "Follow-up story", "host_token": room["host_token"]},
    )
    state_after_new_story = event_payloads(host.get_received(), "room_state")[-1]
    assert state_after_new_story["issue"] == "Follow-up story"
    assert state_after_new_story["revealed"] is False

    host.emit("delete_session", {"host_token": room["host_token"]})
    guest_events = guest.get_received()
    deleted_event = event_payloads(guest_events, "session_deleted")
    assert deleted_event
