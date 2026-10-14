from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domain import Room


class RoomSnapshotMapper:
    @staticmethod
    def to_dict(room: Room) -> dict[str, Any]:
        from domain import ROOM_SCHEMA_VERSION

        return {
            "schema_version": ROOM_SCHEMA_VERSION,
            "room_id": room.room_id,
            "host_token_hash": room.host_token_hash,
            "host_participant_id": room.host_participant_id,
            "issue": room.issue,
            "participants": [
                {
                    "participant_id": participant.participant_id,
                    "name": participant.name,
                    "vote": participant.vote,
                    "credential_hash": participant.credential_hash,
                    "avatar_color": participant.avatar_color,
                }
                for participant in room.participants.values()
            ],
            "revealed": room.revealed,
            "revealed_participant_ids": list(room.revealed_participant_ids),
            "round_number": room.round_number,
            "history": room.history,
            "deck": list(room.deck),
            "reveal_countdown_seconds": room.reveal_countdown_seconds,
            "suggestions_enabled": room.suggestions_enabled,
            "suggestions": room.suggestions,
            "backlog": room.backlog,
            "created_at": room.created_at,
            "updated_at": room.updated_at,
        }

    @staticmethod
    def from_dict(
        payload: dict[str, Any],
        room_type: type[Room] | None = None,
    ) -> Room:
        from domain import (
            DEFAULT_DECK,
            DEFAULT_REVEAL_COUNTDOWN_SECONDS,
            Participant,
            Room,
            hash_host_token,
            trim_history,
        )

        legacy_host_token = payload.get("host_token")
        host_token_hash = payload.get("host_token_hash")
        if not isinstance(host_token_hash, str) or not host_token_hash:
            host_token_hash = hash_host_token(legacy_host_token)
        if not host_token_hash:
            raise KeyError("host_token_hash")

        participants = {}
        for raw_participant in payload.get("participants", []):
            if not isinstance(raw_participant, dict):
                continue
            participant_id = str(raw_participant.get("participant_id", ""))
            if not participant_id:
                continue
            participants[participant_id] = Participant(
                participant_id=participant_id,
                name=str(raw_participant.get("name", "")),
                vote=raw_participant.get("vote"),
                credential_hash=str(raw_participant.get("credential_hash", "")),
                avatar_color=raw_participant.get("avatar_color", ""),
            )

        deck = tuple(payload.get("deck") or DEFAULT_DECK)
        room_class = room_type or Room
        return room_class(
            room_id=str(payload["room_id"]),
            host_token_hash=host_token_hash,
            host_participant_id=str(payload["host_participant_id"]),
            issue=str(payload.get("issue", "")),
            participants=participants,
            revealed=bool(payload.get("revealed", False)),
            revealed_participant_ids=payload.get("revealed_participant_ids") or [],
            round_number=int(payload.get("round_number", 1)),
            history=trim_history(payload.get("history") or []),
            deck=deck,
            reveal_countdown_seconds=payload.get(
                "reveal_countdown_seconds",
                DEFAULT_REVEAL_COUNTDOWN_SECONDS,
            ),
            suggestions_enabled=payload.get("suggestions_enabled", False),
            suggestions=payload.get("suggestions") or [],
            backlog=payload.get("backlog") or [],
            created_at=float(payload.get("created_at", time.time())),
            updated_at=float(payload.get("updated_at", time.time())),
        )