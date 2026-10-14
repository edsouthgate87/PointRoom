from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager

from domain import Participant, Room
from room_store import RoomStore


class RoomRepository:
    def __init__(self, store: RoomStore) -> None:
        self.store = store

    @contextmanager
    def creation_lock(self) -> Iterator[None]:
        with self.store.creation_lock:
            yield

    @contextmanager
    def room_locks(self, room_ids: list[str]) -> Iterator[None]:
        with self.store.room_locks(room_ids):
            yield

    @contextmanager
    def room_lock(self, room_id: str) -> Iterator[None]:
        with self.store.room_lock(room_id):
            yield

    @staticmethod
    def _restore_room(room: Room, snapshot: Room) -> None:
        room.__dict__.clear()
        room.__dict__.update(snapshot.__dict__)

    @contextmanager
    def persist_room(self, room: Room) -> Iterator[None]:
        with self.persist_rooms([room]):
            yield

    @contextmanager
    def persist_rooms(self, rooms: list[Room]) -> Iterator[None]:
        unique_rooms = list({room.room_id: room for room in rooms}.values())
        with self.room_locks([room.room_id for room in unique_rooms]):
            snapshots = {room.room_id: copy.deepcopy(room) for room in unique_rooms}
            try:
                yield
                self.store.save_rooms(unique_rooms)
            except Exception:
                for room in unique_rooms:
                    self._restore_room(room, snapshots[room.room_id])
                raise

    @contextmanager
    def connected_room(
        self,
        sid: str,
    ) -> Iterator[tuple[Room | None, Participant | None]]:
        while True:
            connection = self.connection_for(sid)
            if connection is None:
                yield None, None
                return
            with self.room_lock(connection[0]):
                if self.connection_for(sid) != connection:
                    continue
                room = self.get_room(connection[0])
                participant = (
                    room.participants.get(connection[1])
                    if room is not None
                    else None
                )
                yield room, participant
                return

    def connection_for(self, sid: str) -> tuple[str, str] | None:
        with self.store.lock:
            return self.store.connections.get(sid)

    def set_connection(self, sid: str, connection: tuple[str, str]) -> None:
        with self.store.lock:
            self.store.connections[sid] = connection

    def remove_connection(self, sid: str) -> None:
        with self.store.lock:
            self.store.connections.pop(sid, None)

    def restore_connection(
        self,
        sid: str,
        connection: tuple[str, str] | None,
    ) -> None:
        with self.store.lock:
            if connection is None:
                self.store.connections.pop(sid, None)
            else:
                self.store.connections[sid] = connection

    def has_other_connection(
        self,
        sid: str,
        connection: tuple[str, str],
    ) -> bool:
        with self.store.lock:
            return any(
                other_sid != sid and other_connection == connection
                for other_sid, other_connection in self.store.connections.items()
            )

    def detach_connection(
        self,
        sid: str,
        connection: tuple[str, str],
    ) -> Room | None:
        room_id, participant_id = connection
        room = self.get_room(room_id)
        if room is None or self.has_other_connection(sid, connection):
            return room
        participant = room.participants.get(participant_id)
        if participant is not None:
            participant.sid = None
        return room

    def add_room(self, room: Room) -> None:
        with self.store.lock:
            self.store.rooms[room.room_id] = room

    def remove_room(self, room_id: str) -> None:
        with self.store.lock:
            self.store.rooms.pop(room_id, None)
            self.store.room_ids.discard(room_id)

    def room_count(self) -> int:
        return self.store.room_count()

    def has_room_id(self, room_id: str) -> bool:
        return self.store.has_room_id(room_id)

    def get_room(self, room_id: str) -> Room | None:
        return self.store.get_room(room_id)

    def cleanup_expired(self) -> int:
        return self.store.cleanup_expired()

    def delete(self, room_id: str) -> None:
        self.store.delete(room_id)