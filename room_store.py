from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from threading import RLock
from weakref import WeakValueDictionary

from domain import ROOM_SCHEMA_VERSION, Room
from room_store_adapters import (
    DEFAULT_DATABASE_PATH,
    RoomStoreAdapter,
    create_adapter,
    resolve_dsn,
)

DEFAULT_ROOM_RETENTION_DAYS = 30
ROOM_CLEANUP_BATCH_SIZE = 100
logger = logging.getLogger("pointroom.store")

__all__ = [
    "DEFAULT_DATABASE_PATH",
    "DEFAULT_ROOM_RETENTION_DAYS",
    "RoomStore",
    "resolve_dsn",
]


class RoomStore:
    def __init__(
        self,
        dsn: str | os.PathLike[str] = DEFAULT_DATABASE_PATH,
        retention_days: int = DEFAULT_ROOM_RETENTION_DAYS,
        load_rooms: bool = True,
    ) -> None:
        self.rooms: dict[str, Room] = {}
        self.room_ids: set[str] = set()
        self.connections: dict[str, tuple[str, str]] = {}
        self.lock = RLock()
        self.creation_lock = RLock()
        self._room_locks: WeakValueDictionary[str, RLock] = WeakValueDictionary()
        self.retention_days = retention_days
        self.load_errors = 0
        self.adapter: RoomStoreAdapter | None = None
        self.configure(dsn, retention_days, load_rooms=load_rooms)

    @property
    def dsn(self) -> str:
        return self.adapter.dsn

    def configure(
        self,
        dsn: str | os.PathLike[str],
        retention_days: int,
        load_rooms: bool = True,
    ) -> None:
        with self.lock:
            if self.adapter is not None:
                self.adapter.close()
            self.retention_days = max(1, int(retention_days))
            self.adapter = create_adapter(os.fspath(dsn))
            self.adapter.initialize()
            if load_rooms:
                self._load_room_ids()
            else:
                self.rooms.clear()
                self.room_ids.clear()
                self.connections.clear()
                self.load_errors = 0

    def health_check(self) -> bool:
        return self.health_status()["status"] == "ok"

    def health_status(self) -> dict[str, int | str]:
        if not self.adapter.ping():
            return {"status": "unhealthy"}
        if self.load_errors:
            return {"status": "degraded", "load_errors": self.load_errors}
        return {"status": "ok"}

    def _cutoff(self) -> float:
        return time.time() - self.retention_days * 24 * 60 * 60

    @staticmethod
    def _migrate_legacy_participants(room: Room) -> bool:
        legacy_participant_ids = [
            participant_id
            for participant_id, participant in room.participants.items()
            if not participant.credential_hash
        ]
        if not legacy_participant_ids:
            return False
        for participant_id in legacy_participant_ids:
            del room.participants[participant_id]
        logger.warning(
            "Invalidated legacy participant slots; participants must rejoin",
            extra={
                "event": "legacy_participants_migrated",
                "room_id": room.room_id,
                "status": "degraded",
            },
        )
        return True

    def _load_room_ids(self) -> None:
        cutoff = self._cutoff()
        self.rooms.clear()
        self.room_ids.clear()
        self.connections.clear()
        self.load_errors = 0
        self.adapter.delete_expired(cutoff)
        if self.adapter.has_legacy_snapshots():
            self.migrate_legacy_snapshots()

    def _parse_room_state(self, room_id: str, state_json: str) -> tuple[Room, bool]:
        payload = json.loads(state_json)
        if not isinstance(payload, dict) or str(payload.get("room_id")) != room_id:
            raise ValueError("Persisted room id does not match its database key.")
        room = Room.from_dict(payload)
        normalized = (
            self._migrate_legacy_participants(room)
            or payload.get("schema_version") != ROOM_SCHEMA_VERSION
            or "host_token" in payload
            or payload.get("host_token_hash") != room.host_token_hash
            or (
                "history" in payload
                and payload.get("history") != room.history
            )
        )
        return room, normalized

    def _load_room(self, room_id: str) -> Room | None:
        state_json = self.adapter.fetch_state(room_id)
        if state_json is None:
            with self.lock:
                self.room_ids.discard(room_id)
            return None
        try:
            room, normalized = self._parse_room_state(room_id, state_json)
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            OverflowError,
        ):
            with self.lock:
                self.load_errors += 1
            logger.error(
                "Could not load persisted room; skipping snapshot",
                extra={
                    "event": "room_load_failed",
                    "room_id": room_id,
                    "status": "degraded",
                },
            )
            return None
        if room.updated_at < self._cutoff():
            self.adapter.delete_room(room_id)
            with self.lock:
                self.room_ids.discard(room_id)
            return None
        if normalized:
            self.save(room, touch_updated_at=False)
        with self.lock:
            self.rooms[room_id] = room
            self.room_ids.add(room_id)
        return room

    def migrate_legacy_snapshots(self) -> int:
        if not self.adapter.has_legacy_snapshots():
            return 0
        migrated_count = 0
        after_room_id = None
        while True:
            page = self.adapter.fetch_state_page(after_room_id)
            if not page:
                return migrated_count
            for room_id, updated_at, state_json in page:
                after_room_id = room_id
                try:
                    room, normalized = self._parse_room_state(room_id, state_json)
                except (
                    AttributeError,
                    KeyError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                    OverflowError,
                ):
                    self.load_errors += 1
                    logger.error(
                        "Could not migrate persisted room snapshot",
                        extra={
                            "event": "room_migration_failed",
                            "room_id": room_id,
                            "status": "degraded",
                        },
                    )
                    continue
                if normalized:
                    room.updated_at = float(updated_at)
                    self.save(room, touch_updated_at=False)
                    migrated_count += 1

    @contextmanager
    def room_locks(self, room_ids: list[str]):
        unique_room_ids = sorted(set(room_ids))
        with self.lock:
            locks = []
            for room_id in unique_room_ids:
                lock = self._room_locks.get(room_id)
                if lock is None:
                    lock = RLock()
                    self._room_locks[room_id] = lock
                locks.append(lock)
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    @contextmanager
    def room_lock(self, room_id: str):
        with self.room_locks([room_id]):
            yield

    def cleanup_expired(self) -> int:
        total_deleted = 0
        cutoff = self._cutoff()
        while True:
            expired_room_ids = self.adapter.fetch_expired_room_ids(
                cutoff,
                ROOM_CLEANUP_BATCH_SIZE,
            )
            if not expired_room_ids:
                return total_deleted
            with self.room_locks(list(expired_room_ids)):
                deleted_room_ids = self.adapter.delete_expired_room_ids(
                    cutoff,
                    expired_room_ids,
                )
                with self.lock:
                    self.room_ids.difference_update(deleted_room_ids)
                    for room_id in deleted_room_ids:
                        self.rooms.pop(room_id, None)
                    self.connections = {
                        sid: connection
                        for sid, connection in self.connections.items()
                        if connection[0] not in deleted_room_ids
                    }
                total_deleted += len(deleted_room_ids)
            if not deleted_room_ids:
                return total_deleted

    def room_count(self) -> int:
        return self.adapter.count_rooms()

    def has_room_id(self, room_id: str) -> bool:
        return self.adapter.has_room_id(room_id)

    def get_room(self, room_id: str) -> Room | None:
        with self.room_lock(room_id):
            with self.lock:
                room = self.rooms.get(room_id)
                if room is not None and room.updated_at >= self._cutoff():
                    return room
                if room is not None:
                    self.rooms.pop(room_id, None)
                    self.room_ids.discard(room_id)
            if room is not None:
                self.adapter.delete_room(room_id)
            return self._load_room(room_id)

    def save(self, room: Room, touch_updated_at: bool = True) -> None:
        self.save_rooms([room], touch_updated_at=touch_updated_at)

    def save_rooms(self, rooms: list[Room], touch_updated_at: bool = True) -> None:
        unique_rooms = list({room.room_id: room for room in rooms}.values())
        with self.room_locks([room.room_id for room in unique_rooms]):
            with self.lock:
                previous_updated_at = {
                    id(room): room.updated_at for room in unique_rooms
                }
                if touch_updated_at:
                    for room in unique_rooms:
                        room.updated_at = time.time()
                snapshots = [
                    (room.room_id, room.updated_at, room.to_dict())
                    for room in unique_rooms
                ]
            records = [
                (room_id, updated_at, json.dumps(payload, separators=(",", ":")))
                for room_id, updated_at, payload in snapshots
            ]
            try:
                self.adapter.upsert_rooms(records)
                with self.lock:
                    self.room_ids.update(room_id for room_id, _, _ in records)
            except Exception:
                with self.lock:
                    for room in unique_rooms:
                        room.updated_at = previous_updated_at[id(room)]
                raise

    def delete(self, room_id: str) -> None:
        with self.room_lock(room_id):
            self.adapter.delete_room(room_id)
            with self.lock:
                self.rooms.pop(room_id, None)
                self.room_ids.discard(room_id)
                self.connections = {
                    sid: connection
                    for sid, connection in self.connections.items()
                    if connection[0] != room_id
                }
