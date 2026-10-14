from app import app, socketio
from domain import (
    MAX_HISTORY_BYTES,
    MAX_HISTORY_ENTRIES,
    Participant,
    Room,
    T_SHIRT_DECK,
    hash_host_token,
    history_size_bytes,
)
from room_projection import RoomPublicProjector
from room_snapshot import RoomSnapshotMapper
from support import (
    create_room,
    event_payloads,
    join,
    reveal_and_wait,
    received_event,
    store,
)


def test_room_snapshot_mapper_round_trips_room_state():
    room = Room(
        room_id="ABC123",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Mapped story",
        participants={
            "host-1": Participant(
                participant_id="host-1",
                name="Alex",
                credential_hash="credential-hash",
            )
        },
        suggestions_enabled=True,
        suggestions=[
            {
                "id": "suggestion-1",
                "issue": "Suggested story",
                "suggested_by": "Jordan",
            }
        ],
        backlog=[
            {
                "id": "backlog-1",
                "issue": "Backlog story",
                "suggested_by": "Taylor",
            }
        ],
    )

    snapshot = RoomSnapshotMapper.to_dict(room)
    restored_room = RoomSnapshotMapper.from_dict(snapshot)

    assert RoomSnapshotMapper.to_dict(restored_room) == snapshot


def test_room_snapshot_mapper_migrates_legacy_host_token_without_persisting_it():
    room = RoomSnapshotMapper.from_dict(
        {
            "room_id": "ABC123",
            "host_participant_id": "host-1",
            "host_token": "legacy-token",
            "participants": [],
        }
    )

    snapshot = RoomSnapshotMapper.to_dict(room)
    assert room.host_token_hash == hash_host_token("legacy-token")
    assert "host_token" not in snapshot


def test_room_public_projector_returns_defensive_state_copies():
    room = Room(
        room_id="ABC123",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Projected story",
        suggestions=[
            {
                "id": "suggestion-1",
                "issue": "Suggested story",
                "suggested_by": "Jordan",
            }
        ],
    )

    state = RoomPublicProjector.state(room)
    state["suggestions"][0]["issue"] = "Changed outside the room"

    assert room.suggestions[0]["issue"] == "Suggested story"


def test_room_history_is_bounded_by_entry_and_byte_limits():
    history = [
        {
            "round": round_number,
            "issue": "x" * 500,
            "status": "skipped",
            "votes": [],
            "voter_count": 0,
            "average": None,
            "distribution": {},
        }
        for round_number in range(MAX_HISTORY_ENTRIES + 10)
    ]
    room = Room(
        room_id="ABC123",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Current story",
        history=history,
    )

    assert len(room.history) <= MAX_HISTORY_ENTRIES
    assert history_size_bytes(room.history) <= MAX_HISTORY_BYTES
    assert room.history[-1]["round"] == MAX_HISTORY_ENTRIES + 9


def test_live_room_state_excludes_history_from_repeated_state_payloads():
    room = create_room()
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join_events = host.get_received()

    state_events = event_payloads(join_events, "room_state")
    state = state_events[-1]
    history = event_payloads(join_events, "room_history")[-1]["history"]
    assert len(state_events) == 1
    assert "history" not in state
    assert history == []

    host.emit("update_issue", {"issue": "Updated story", "host_token": room["host_token"]})
    state_events = host.get_received()
    assert event_payloads(state_events, "room_state")[-1]["issue"] == "Updated story"
    assert not event_payloads(state_events, "room_history")


def test_t_shirt_cards_reveal_without_numeric_average():
    room = create_room(deck="t_shirt")
    host = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    host.get_received()

    host.emit("vote", {"value": "M"})
    host.get_received()
    reveal_and_wait(host, room)

    events = host.get_received()
    state = event_payloads(events, "room_state")[-1]
    history_entry = event_payloads(events, "room_history")[-1]["history"][-1]

    assert state["average"] is None
    assert state["distribution"]["M"] == 1
    assert history_entry["average"] is None
    assert history_entry["votes"][0]["vote"] == "M"

    database_path = store.dsn
    store.configure(database_path, 30)

    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.deck == T_SHIRT_DECK
    assert reloaded_room.history[-1]["average"] is None


def test_room_and_history_survive_a_store_reload():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit("update_issue", {"issue": "Persisted story", "host_token": room["host_token"]})
    host.get_received()
    guest.get_received()
    host.emit("vote", {"value": "5"})
    guest.emit("vote", {"value": "8"})
    host.get_received()
    guest.get_received()
    reveal_and_wait(host, room)
    guest_events = guest.get_received()
    guest_history = event_payloads(guest_events, "room_history")[-1]["history"]
    database_path = store.dsn

    store.configure(database_path, 30)

    assert store.rooms == {}
    assert store.room_count() == 1
    assert store.room_ids == set()
    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.issue == "Persisted story"
    assert reloaded_room.revealed is True
    assert reloaded_room.history == guest_history
    assert reloaded_room.revealed_participant_ids == ("host-1", "guest-1")
    assert reloaded_room.participants["host-1"].vote == "5"
    assert reloaded_room.participants["guest-1"].vote == "8"


def test_participant_credential_survives_store_reload():
    room = create_room()
    service = app.extensions["room_service"]
    joined = service.join("guest-sid", room["room_id"], "Jordan", "guest-1")
    guest_token = joined.participant_token
    database_path = store.dsn

    store.configure(database_path, 30)

    rejoined = service.join(
        "reconnected-sid",
        room["room_id"],
        "Jordan",
        "different-public-id",
        participant_token=guest_token,
    )

    assert rejoined.participant_id == "guest-1"
    assert rejoined.participant_token == guest_token
    assert rejoined.own_vote is None
    assert "credential_hash" not in rejoined.state["participants"][0]


def test_revealed_round_history_survives_reset():
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

    revealed_events = guest.get_received()
    revealed_state = event_payloads(revealed_events, "room_state")[-1]
    history_entry = event_payloads(revealed_events, "room_history")[-1]["history"][0]
    assert history_entry["round"] == 1
    assert history_entry["issue"] == "Test story"
    assert history_entry["status"] == "revealed"
    assert [(vote["name"], vote["vote"]) for vote in history_entry["votes"]] == [
        ("Alex", "5"),
        ("Jordan", "8"),
    ]
    assert [vote["avatar_color"] for vote in history_entry["votes"]] == [
        store.get_room(room["room_id"]).participants["host-1"].avatar_color,
        store.get_room(room["room_id"]).participants["guest-1"].avatar_color,
    ]
    assert history_entry["average"] == 6.5

    host.emit("reset_round", {"host_token": room["host_token"]})

    reset_events = guest.get_received()
    reset_state = event_payloads(reset_events, "room_state")[-1]
    assert reset_state["round_number"] == 2
    assert reset_state["voted_count"] == 0
    assert event_payloads(reset_events, "room_history")[-1]["history"] == [history_entry]


def test_revealed_result_survives_participant_disconnect():
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

    revealed_state = event_payloads(host.get_received(), "room_state")[-1]
    assert revealed_state["average"] == 6.5
    assert revealed_state["distribution"]["5"] == 1
    assert revealed_state["distribution"]["8"] == 1

    guest.disconnect()

    disconnected_state = event_payloads(host.get_received(), "room_state")[-1]
    assert disconnected_state["revealed"] is True
    assert disconnected_state["participant_count"] == revealed_state["participant_count"]
    assert disconnected_state["voted_count"] == revealed_state["voted_count"]
    assert disconnected_state["average"] == revealed_state["average"]
    assert disconnected_state["distribution"] == revealed_state["distribution"]
    assert {participant["name"] for participant in disconnected_state["participants"]} == {
        "Alex",
        "Jordan",
    }


def test_revealed_result_excludes_participant_disconnected_before_reveal():
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
    guest.disconnect()
    reveal_and_wait(host, room)

    state = event_payloads(host.get_received(), "room_state")[-1]
    assert state["participant_count"] == 1
    assert state["voted_count"] == 1
    assert state["average"] == 5.0
    assert state["distribution"]["5"] == 1
    assert state["distribution"]["8"] == 0
    assert [participant["name"] for participant in state["participants"]] == ["Alex"]