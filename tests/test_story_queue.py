import pytest

from app import app, socketio
from domain import Participant, Room, hash_host_token
from room_service import RoomServiceError
from story_queue_service import StoryQueueService
from support import create_room, event_payloads, join, received_event, store


def test_story_queue_service_can_submit_and_move_a_suggestion_to_backlog():
    room = Room(
        room_id="ABC123",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Current story",
        suggestions_enabled=True,
        participants={
            "host-1": Participant(participant_id="host-1", name="Alex"),
            "guest-1": Participant(participant_id="guest-1", name="Jordan"),
        },
    )
    participant = room.participants["guest-1"]
    queue = StoryQueueService()

    suggestion = queue.submit(room, participant, "Queued story")
    queue.move_to_backlog(room, suggestion["id"])

    assert room.suggestions == []
    assert room.backlog == [suggestion]


def test_story_queue_service_accepts_a_suggestion_as_the_next_story():
    room = Room(
        room_id="ABC123",
        host_token_hash=hash_host_token("host-token"),
        host_participant_id="host-1",
        issue="Current story",
        suggestions_enabled=True,
        participants={
            "host-1": Participant(participant_id="host-1", name="Alex"),
            "guest-1": Participant(participant_id="guest-1", name="Jordan"),
        },
    )
    queue = StoryQueueService()
    suggestion = queue.submit(room, room.participants["guest-1"], "Next story")

    queue.accept(room, suggestion["id"])

    assert room.issue == "Next story"
    assert room.round_number == 2
    assert room.suggestions == []
    assert room.history[-1]["status"] == "skipped"


def test_guest_story_suggestions_are_opt_in_and_host_controlled():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    guest.emit("suggest_story", {"issue": "Guest story"})
    assert received_event(guest, "room_error")[-1]["args"][0]["message"] == (
        "Story suggestions are turned off by the host."
    )

    host.emit(
        "set_suggestions_enabled",
        {"enabled": True, "host_token": room["host_token"]},
    )
    host.get_received()
    guest.get_received()

    guest.emit("suggest_story", {"issue": "Guest story"})
    guest_events = guest.get_received()
    suggestion = event_payloads(guest_events, "story_suggestion_submitted")[-1][
        "suggestion"
    ]
    guest_state = event_payloads(guest_events, "room_state")[-1]
    assert guest_state["suggestions_enabled"] is True
    assert guest_state["suggestions"] == [suggestion]
    assert suggestion["issue"] == "Guest story"
    assert suggestion["suggested_by"] == "Jordan"

    guest.emit(
        "accept_story_suggestion",
        {"suggestion_id": suggestion["id"], "host_token": room["host_token"]},
    )
    assert received_event(guest, "room_error")[-1]["args"][0]["message"] == (
        "Only the room host can do that."
    )

    host.emit(
        "accept_story_suggestion",
        {"suggestion_id": suggestion["id"], "host_token": room["host_token"]},
    )
    host_events = host.get_received()
    host_state = event_payloads(host_events, "room_state")[-1]
    assert host_state["issue"] == "Guest story"
    assert host_state["round_number"] == 2
    assert host_state["suggestions"] == []
    assert event_payloads(host_events, "room_history")[-1]["history"][0]["issue"] == "Test story"


def test_host_can_move_a_story_suggestion_to_backlog_and_use_it_later():
    room = create_room()
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    join(host, room, "Alex", "host-1", room["host_token"])
    join(guest, room, "Jordan", "guest-1")
    host.get_received()
    guest.get_received()

    host.emit(
        "set_suggestions_enabled",
        {"enabled": True, "host_token": room["host_token"]},
    )
    host.get_received()
    guest.get_received()
    guest.emit("suggest_story", {"issue": "Backlog story"})
    suggestion = event_payloads(
        guest.get_received(), "story_suggestion_submitted"
    )[-1]["suggestion"]

    host.emit(
        "backlog_story_suggestion",
        {"suggestion_id": suggestion["id"], "host_token": room["host_token"]},
    )
    backlog_state = event_payloads(host.get_received(), "room_state")[-1]
    assert backlog_state["issue"] == "Test story"
    assert backlog_state["suggestions"] == []
    assert backlog_state["backlog"] == [suggestion]

    database_path = store.dsn
    store.configure(database_path, 30)
    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.suggestions_enabled is True
    assert reloaded_room.backlog == [suggestion]


def test_host_can_disable_story_suggestions_after_enabling_them():
    room = create_room()
    service = app.extensions["room_service"]
    service.join("host-sid", room["room_id"], "Alex", "host-1", host_token=room["host_token"])
    service.join("guest-sid", room["room_id"], "Jordan", "guest-1")

    service.set_suggestions_enabled("host-sid", True, room["host_token"])
    service.set_suggestions_enabled("host-sid", False, room["host_token"])

    with pytest.raises(RoomServiceError, match="turned off"):
        service.submit_story_suggestion("guest-sid", "No longer accepted")

    database_path = store.dsn
    store.configure(database_path, 30)
    reloaded_room = store.get_room(room["room_id"])
    assert reloaded_room is not None
    assert reloaded_room.suggestions_enabled is False