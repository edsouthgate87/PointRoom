from __future__ import annotations

import time

from app import app, socketio

store = app.extensions["room_store"]


def create_room(
    name="Alex",
    participant_id="host-1",
    issue="Test story",
    avatar_color=None,
    deck=None,
):
    payload = {"name": name, "participant_id": participant_id, "issue": issue}
    if avatar_color is not None:
        payload["avatar_color"] = avatar_color
    if deck is not None:
        payload["deck"] = deck
    response = app.test_client().post(
        "/api/rooms",
        json=payload,
    )
    assert response.status_code == 201
    return response.get_json()


def received_event(client, event_name):
    return [event for event in client.get_received() if event["name"] == event_name]


def event_payloads(events, event_name):
    return [event["args"][0] for event in events if event["name"] == event_name]


def wait_for_reveal(room_id, timeout=2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        room = store.get_room(room_id)
        if room is not None and room.revealed:
            return
        socketio.sleep(0.01)
    raise AssertionError("The room did not reveal before the test timeout.")


def reveal_and_wait(client, room, countdown_seconds=0.05):
    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    persisted_room.reveal_countdown_seconds = countdown_seconds
    store.save(persisted_room)
    client.emit("reveal", {"host_token": room["host_token"]})
    wait_for_reveal(room["room_id"])
    socketio.sleep(0.03)


def reveal_service_and_wait(service, sid, room, countdown_seconds=0.05):
    persisted_room = store.get_room(room["room_id"])
    assert persisted_room is not None
    persisted_room.reveal_countdown_seconds = countdown_seconds
    store.save(persisted_room)
    countdown = service.begin_reveal(sid, room["host_token"])
    assert countdown is not None
    socketio.sleep(countdown_seconds + 0.05)
    assert service.complete_reveal(room["room_id"], countdown.ends_at) == room["room_id"]


def join(
    client,
    room,
    name,
    participant_id,
    host_token=None,
    participant_token=None,
    avatar_color=None,
):
    payload = {
        "room_id": room["room_id"],
        "name": name,
        "participant_id": participant_id,
    }
    if (
        participant_token is None
        and host_token
        and participant_id == room.get("participant_id")
    ):
        participant_token = room.get("participant_token")
    if participant_token:
        payload["participant_token"] = participant_token
    if host_token:
        payload["host_token"] = host_token
    if avatar_color is not None:
        payload["avatar_color"] = avatar_color
    client.emit("join_room", payload)