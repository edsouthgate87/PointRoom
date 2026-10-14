from __future__ import annotations

import hashlib
import secrets
import string
import time
from dataclasses import dataclass
from hmac import compare_digest
from typing import Any

from domain import (
    AVATAR_COLORS,
    MAX_ISSUE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_PARTICIPANTS_PER_ROOM,
    MAX_REVEAL_COUNTDOWN_SECONDS,
    MIN_REVEAL_COUNTDOWN_SECONDS,
    Participant,
    Room,
    avatar_color_key,
    avatar_color_for,
    clean_participant_id,
    clean_text,
    deck_for_name,
    hash_host_token,
    is_valid_story,
    normalize_avatar_color,
    story_key,
)
from room_store import RoomStore
from room_repository import RoomRepository
from room_errors import RoomCapacityError, RoomServiceError
from story_queue_service import StoryQueueService

ROOM_ID_ALPHABET = string.ascii_uppercase + string.digits
PARTICIPANT_TOKEN_MAX_LENGTH = 128
DEFAULT_MAX_ACTIVE_ROOMS = 1000


@dataclass(frozen=True)
class CreatedRoom:
    room_id: str
    host_token: str
    participant_id: str
    participant_token: str


@dataclass(frozen=True)
class JoinedRoom:
    room_id: str
    participant_id: str
    participant_token: str
    state: dict[str, Any]
    history: list[dict[str, Any]]
    own_vote: str | None
    previous_room_id: str | None = None


@dataclass(frozen=True)
class RevealCountdown:
    room_id: str
    seconds: int
    ends_at: float
    started: bool


class RoomService:
    def __init__(
        self,
        store: RoomStore,
        max_active_rooms: int = DEFAULT_MAX_ACTIVE_ROOMS,
    ) -> None:
        self.store = store
        self.repository = RoomRepository(store)
        self.story_queue = StoryQueueService()
        self.max_active_rooms = max(1, int(max_active_rooms))

    @staticmethod
    def _new_participant_id() -> str:
        return secrets.token_urlsafe(12)

    @staticmethod
    def _new_participant_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _participant_credential_hash(participant_token: str) -> str:
        return hashlib.sha256(participant_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _tokens_match(candidate: Any, expected: Any) -> bool:
        if not isinstance(candidate, str) or not isinstance(expected, str):
            return False
        try:
            return compare_digest(
                candidate.encode("utf-8"),
                expected.encode("utf-8"),
            )
        except UnicodeEncodeError:
            return False

    @staticmethod
    def _clean_participant_token(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        token = value.strip()
        if not token or len(token) > PARTICIPANT_TOKEN_MAX_LENGTH:
            return ""
        return token

    @staticmethod
    def _clean_reveal_countdown(value: Any) -> int:
        if isinstance(value, bool):
            raise RoomServiceError(
                "Set the reveal timer to a whole number between "
                f"{MIN_REVEAL_COUNTDOWN_SECONDS} and {MAX_REVEAL_COUNTDOWN_SECONDS} seconds."
            )
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            raise RoomServiceError(
                "Set the reveal timer to a whole number between "
                f"{MIN_REVEAL_COUNTDOWN_SECONDS} and {MAX_REVEAL_COUNTDOWN_SECONDS} seconds."
            ) from None
        if not MIN_REVEAL_COUNTDOWN_SECONDS <= seconds <= MAX_REVEAL_COUNTDOWN_SECONDS:
            raise RoomServiceError(
                "Set the reveal timer to a whole number between "
                f"{MIN_REVEAL_COUNTDOWN_SECONDS} and {MAX_REVEAL_COUNTDOWN_SECONDS} seconds."
            )
        return seconds

    @staticmethod
    def _avatar_color_is_available(
        room: Room | None,
        avatar_color: Any,
        participant_id: str | None = None,
    ) -> bool:
        color_key = avatar_color_key(avatar_color)
        if not color_key:
            return False
        if room is None:
            return True
        return all(
            participant.participant_id == participant_id
            or avatar_color_key(participant.avatar_color) != color_key
            for participant in room.participants.values()
        )

    @classmethod
    def _resolve_avatar_color(
        cls,
        room: Room | None,
        participant_id: str,
        requested_avatar_color: Any,
        excluded_participant_id: str | None = None,
    ) -> str:
        requested = (
            requested_avatar_color is not None
            and (
                not isinstance(requested_avatar_color, str)
                or bool(requested_avatar_color.strip())
            )
        )
        if requested:
            cleaned_avatar_color = normalize_avatar_color(requested_avatar_color)
            if not cleaned_avatar_color:
                raise RoomServiceError("Choose a valid avatar color.")
            if not cls._avatar_color_is_available(
                room,
                cleaned_avatar_color,
                excluded_participant_id,
            ):
                raise RoomServiceError(
                    "That avatar color is already in use. Choose another color."
                )
            return cleaned_avatar_color

        preferred_avatar_color = normalize_avatar_color(
            avatar_color_for(participant_id)
        )
        if cls._avatar_color_is_available(
            room,
            preferred_avatar_color,
            excluded_participant_id,
        ):
            return preferred_avatar_color
        for avatar_color in AVATAR_COLORS:
            if cls._avatar_color_is_available(
                room,
                avatar_color,
                excluded_participant_id,
            ):
                return avatar_color

        for _ in range(32):
            generated_avatar_color = f"#{secrets.randbelow(0x1000000):06x}"
            if cls._avatar_color_is_available(
                room,
                generated_avatar_color,
                excluded_participant_id,
            ):
                return generated_avatar_color
        raise RoomServiceError("No unique avatar colors are available.")

    @classmethod
    def _find_participant_by_token_locked(
        cls,
        room: Room,
        participant_token: str,
    ) -> Participant | None:
        credential_hash = cls._participant_credential_hash(participant_token)
        for participant in room.participants.values():
            if participant.credential_hash and cls._tokens_match(
                participant.credential_hash,
                credential_hash,
            ):
                return participant
        return None

    @staticmethod
    def _matches_host_token(room: Room, host_token: Any) -> bool:
        if not isinstance(host_token, str) or not room.host_token_hash:
            return False
        return RoomService._tokens_match(
            room.host_token_hash,
            hash_host_token(host_token),
        )

    @staticmethod
    def _reclaim_inactive_participant_locked(room: Room) -> bool:
        if len(room.participants) < MAX_PARTICIPANTS_PER_ROOM:
            return True
        for participant_id, participant in room.participants.items():
            if participant.sid is None and participant_id != room.host_participant_id:
                del room.participants[participant_id]
                return True
        return False

    def _new_room_id(self) -> str:
        while True:
            room_id = "".join(secrets.choice(ROOM_ID_ALPHABET) for _ in range(6))
            if not self.repository.has_room_id(room_id):
                return room_id

    @classmethod
    def _require_host_locked(
        cls,
        room: Room | None,
        participant: Participant | None,
        host_token: Any,
    ) -> Room:
        if room is None or participant is None:
            raise RoomServiceError("Join a room before using host controls.")
        if (
            participant.participant_id != room.host_participant_id
            or not cls._matches_host_token(room, host_token)
        ):
            raise RoomServiceError("Only the room host can do that.")
        return room

    def create_room(
        self,
        name: Any,
        participant_id: Any,
        issue: Any,
        avatar_color: Any = None,
        deck_name: Any = None,
    ) -> CreatedRoom:
        cleaned_name = clean_text(name, MAX_NAME_LENGTH)
        cleaned_participant_id = clean_participant_id(participant_id)
        cleaned_issue = clean_text(issue, MAX_ISSUE_LENGTH)
        selected_deck = deck_for_name(deck_name)
        if not cleaned_name:
            raise RoomServiceError("Enter your name to create a room.")
        if not cleaned_participant_id:
            raise RoomServiceError("A participant id is required.")
        if selected_deck is None:
            raise RoomServiceError("Choose a valid card deck.")

        host_token = secrets.token_urlsafe(24)
        participant_token = self._new_participant_token()
        self.repository.cleanup_expired()
        with self.repository.creation_lock():
            if self.repository.room_count() >= self.max_active_rooms:
                raise RoomCapacityError(
                    "The server has reached its active room limit. Please try again later."
                )
            room_id = self._new_room_id()
            participant_avatar_color = self._resolve_avatar_color(
                None,
                cleaned_participant_id,
                avatar_color,
            )
            room = Room(
                room_id=room_id,
                host_token_hash=hash_host_token(host_token),
                host_participant_id=cleaned_participant_id,
                issue=cleaned_issue,
                deck=selected_deck,
                participants={
                    cleaned_participant_id: Participant(
                        participant_id=cleaned_participant_id,
                        name=cleaned_name,
                        credential_hash=self._participant_credential_hash(participant_token),
                        avatar_color=participant_avatar_color,
                    )
                },
            )
            self.repository.add_room(room)
            try:
                with self.repository.persist_room(room):
                    pass
            except Exception:
                self.repository.remove_room(room_id)
                raise
        return CreatedRoom(
            room_id=room_id,
            host_token=host_token,
            participant_id=cleaned_participant_id,
            participant_token=participant_token,
        )

    def join(
        self,
        sid: str,
        room_id: Any,
        name: Any,
        participant_id: Any,
        participant_token: Any = None,
        host_token: Any = None,
        avatar_color: Any = None,
        update_avatar_color: Any = False,
    ) -> JoinedRoom:
        cleaned_room_id = clean_text(room_id, 6).upper()
        cleaned_participant_id = clean_participant_id(participant_id)
        cleaned_name = clean_text(name, MAX_NAME_LENGTH)
        if not cleaned_room_id or not cleaned_participant_id or not cleaned_name:
            raise RoomServiceError("A room, name, and participant id are required.")

        supplied_participant_token = bool(participant_token)
        cleaned_participant_token = self._clean_participant_token(participant_token)
        if supplied_participant_token and not cleaned_participant_token:
            raise RoomServiceError("That participant credential is invalid.")

        while True:
            previous_connection = self.repository.connection_for(sid)
            room_ids = [cleaned_room_id]
            if previous_connection is not None:
                room_ids.append(previous_connection[0])
            with self.repository.room_locks(room_ids):
                if self.repository.connection_for(sid) != previous_connection:
                    continue
                room = self.repository.get_room(cleaned_room_id)
                if room is None:
                    raise RoomServiceError("That room does not exist.")
                previous_room = None
                if previous_connection and previous_connection[0] != cleaned_room_id:
                    previous_room = self.repository.get_room(previous_connection[0])

                participant = None
                participant_token_for_client = cleaned_participant_token
                host_recovery = False
                participant_is_new = False
                host_recovery_avatar_color = None
                participant_avatar_color_update = None
                requested_avatar_color = (
                    avatar_color is not None
                    and (
                        not isinstance(avatar_color, str)
                        or bool(avatar_color.strip())
                    )
                )

                host_token_valid = self._matches_host_token(room, host_token)
                if supplied_participant_token:
                    participant = self._find_participant_by_token_locked(
                        room,
                        cleaned_participant_token,
                    )
                    if participant is None and not host_token_valid:
                        raise RoomServiceError("That participant credential is invalid.")
                    if participant is None:
                        participant_token_for_client = self._new_participant_token()
                        host_recovery = True
                elif host_token_valid:
                    participant = room.participants.get(room.host_participant_id)
                    participant_token_for_client = self._new_participant_token()
                    host_recovery = True

                if participant is None:
                    if (
                        not host_recovery
                        and len(room.active_participants()) >= MAX_PARTICIPANTS_PER_ROOM
                    ):
                        raise RoomServiceError(
                            f"This room is full. The limit is {MAX_PARTICIPANTS_PER_ROOM} participants."
                        )
                    if host_recovery and room.host_participant_id:
                        effective_participant_id = room.host_participant_id
                    else:
                        effective_participant_id = cleaned_participant_id
                        if (
                            effective_participant_id in room.participants
                            or (
                                not host_recovery
                                and effective_participant_id == room.host_participant_id
                            )
                        ):
                            effective_participant_id = self._new_participant_id()
                            while effective_participant_id in room.participants:
                                effective_participant_id = self._new_participant_id()
                    participant_token_for_client = self._new_participant_token()
                    participant_avatar_color = self._resolve_avatar_color(
                        room,
                        effective_participant_id,
                        avatar_color,
                    )
                    participant = Participant(
                        participant_id=effective_participant_id,
                        name=cleaned_name,
                        credential_hash=self._participant_credential_hash(
                            participant_token_for_client
                        ),
                        avatar_color=participant_avatar_color,
                    )
                    participant_is_new = True
                    if host_recovery:
                        room.host_participant_id = effective_participant_id

                if host_recovery and not participant_is_new and requested_avatar_color:
                    host_recovery_avatar_color = self._resolve_avatar_color(
                        room,
                        participant.participant_id,
                        avatar_color,
                        excluded_participant_id=participant.participant_id,
                    )
                elif (
                    participant is not None
                    and update_avatar_color is True
                    and requested_avatar_color
                ):
                    participant_avatar_color_update = self._resolve_avatar_color(
                        room,
                        participant.participant_id,
                        avatar_color,
                        excluded_participant_id=participant.participant_id,
                    )

                target_connection = (cleaned_room_id, participant.participant_id)
                previous_room_id = (
                    previous_connection[0]
                    if previous_connection and previous_connection[0] != cleaned_room_id
                    else None
                )
                rooms_to_persist = [room]
                if previous_room is not None and previous_connection is not None:
                    has_other_connection = self.repository.has_other_connection(
                        sid,
                        previous_connection,
                    )
                    if (
                        previous_room is not room
                        and previous_connection[1] in previous_room.participants
                        and not has_other_connection
                    ):
                        rooms_to_persist.append(previous_room)

                try:
                    with self.repository.persist_rooms(rooms_to_persist):
                        if previous_connection and previous_connection != target_connection:
                            self.repository.detach_connection(sid, previous_connection)
                        if participant_is_new:
                            if (
                                not host_recovery
                                and not self._reclaim_inactive_participant_locked(room)
                            ):
                                raise RoomServiceError(
                                    f"This room is full. The limit is {MAX_PARTICIPANTS_PER_ROOM} participants."
                                )
                            room.participants[participant.participant_id] = participant
                        participant.name = cleaned_name
                        participant.sid = sid
                        if host_recovery:
                            participant.credential_hash = self._participant_credential_hash(
                                participant_token_for_client
                            )
                            if host_recovery_avatar_color is not None:
                                participant.avatar_color = host_recovery_avatar_color
                        if participant_avatar_color_update is not None:
                            participant.avatar_color = participant_avatar_color_update
                        self.repository.set_connection(sid, target_connection)
                        state = room.public_state()
                        history = room.public_history()
                        own_vote = participant.vote
                except Exception:
                    self.repository.restore_connection(sid, previous_connection)
                    raise
                return JoinedRoom(
                    room_id=cleaned_room_id,
                    participant_id=participant.participant_id,
                    participant_token=participant_token_for_client,
                    state=state,
                    history=history,
                    own_vote=own_vote,
                    previous_room_id=previous_room_id,
                )

    def public_state(self, room_id: str) -> dict[str, Any] | None:
        with self.repository.room_lock(room_id):
            room = self.repository.get_room(room_id)
            return room.public_state() if room is not None else None

    def public_history(self, room_id: str) -> list[dict[str, Any]] | None:
        with self.repository.room_lock(room_id):
            room = self.repository.get_room(room_id)
            return room.public_history() if room is not None else None

    def vote(self, sid: str, value: Any) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            if room is None or participant is None:
                raise RoomServiceError("Join a room before voting.")
            if room.revealed:
                raise RoomServiceError("The cards are already revealed. Reset the round to vote again.")
            if not is_valid_story(room.issue):
                raise RoomServiceError("Add a valid story before voting.")
            if value is not None and value not in room.deck:
                raise RoomServiceError("That card is not in this deck.")
            with self.repository.persist_room(room):
                participant.vote = value
            return room.room_id

    def update_profile(
        self,
        sid: str,
        name: Any,
        avatar_color: Any,
    ) -> tuple[str, str, str]:
        with self.repository.connected_room(sid) as (room, participant):
            if room is None or participant is None:
                raise RoomServiceError("Join a room before updating your profile.")
            cleaned_name = clean_text(name, MAX_NAME_LENGTH)
            if not cleaned_name:
                raise RoomServiceError("Enter a name for your profile.")
            cleaned_avatar_color = normalize_avatar_color(avatar_color)
            if not cleaned_avatar_color:
                raise RoomServiceError("Choose a valid avatar color.")
            if not self._avatar_color_is_available(
                room,
                cleaned_avatar_color,
                participant.participant_id,
            ):
                raise RoomServiceError(
                    "That avatar color is already in use. Choose another color."
                )
            with self.repository.persist_room(room):
                participant.name = cleaned_name
                participant.avatar_color = cleaned_avatar_color
            return room.room_id, participant.name, participant.avatar_color

    def set_suggestions_enabled(
        self,
        sid: str,
        enabled: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            self.story_queue.validate_suggestions_enabled(enabled)
            if room.suggestions_enabled == enabled:
                return room.room_id
            with self.repository.persist_room(room):
                self.story_queue.set_suggestions_enabled(room, enabled)
            return room.room_id

    def set_deck(
        self,
        sid: str,
        deck_name: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            if not isinstance(deck_name, str) or not deck_name.strip():
                raise RoomServiceError("Choose a valid card deck.")
            selected_deck = deck_for_name(deck_name)
            if selected_deck is None:
                raise RoomServiceError("Choose a valid card deck.")
            if room.deck == selected_deck:
                return room.room_id
            if room.revealed or any(
                participant.vote is not None
                for participant in room.participants.values()
            ):
                raise RoomServiceError(
                    "Start a new round before changing the card deck."
                )
            with self.repository.persist_room(room):
                room.deck = selected_deck
            return room.room_id

    def submit_story_suggestion(
        self,
        sid: str,
        issue: Any,
    ) -> tuple[str, dict[str, str]]:
        with self.repository.connected_room(sid) as (room, participant):
            if room is None or participant is None:
                raise RoomServiceError("Join a room before suggesting a story.")
            with self.repository.persist_room(room):
                suggestion = self.story_queue.submit(room, participant, issue)
            return room.room_id, suggestion

    def accept_story_suggestion(
        self,
        sid: str,
        suggestion_id: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            with self.repository.persist_room(room):
                self.story_queue.accept(room, suggestion_id)
            return room.room_id

    def backlog_story_suggestion(
        self,
        sid: str,
        suggestion_id: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            with self.repository.persist_room(room):
                self.story_queue.move_to_backlog(room, suggestion_id)
            return room.room_id

    def dismiss_story_suggestion(
        self,
        sid: str,
        suggestion_id: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            with self.repository.persist_room(room):
                self.story_queue.dismiss(room, suggestion_id)
            return room.room_id

    def use_backlog_story(
        self,
        sid: str,
        backlog_id: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            with self.repository.persist_room(room):
                self.story_queue.use_backlog(room, backlog_id)
            return room.room_id

    def remove_backlog_story(
        self,
        sid: str,
        backlog_id: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            with self.repository.persist_room(room):
                self.story_queue.remove_backlog(room, backlog_id)
            return room.room_id

    def public_avatar_colors(
        self,
        room_id: Any,
        participant_token: Any = None,
        host_token: Any = None,
    ) -> list[str] | None:
        color_state = self.public_avatar_color_state(
            room_id,
            participant_token=participant_token,
            host_token=host_token,
        )
        return color_state["occupied_avatar_colors"] if color_state is not None else None

    def public_avatar_color_state(
        self,
        room_id: Any,
        participant_token: Any = None,
        host_token: Any = None,
    ) -> dict[str, Any] | None:
        cleaned_room_id = clean_text(room_id, 6).upper()
        with self.repository.room_lock(cleaned_room_id):
            room = self.repository.get_room(cleaned_room_id)
            if room is None:
                return None

            participant_id = None
            if self._matches_host_token(room, host_token):
                participant_id = room.host_participant_id
            else:
                cleaned_participant_token = self._clean_participant_token(participant_token)
                if cleaned_participant_token:
                    participant = self._find_participant_by_token_locked(
                        room,
                        cleaned_participant_token,
                    )
                    participant_id = participant.participant_id if participant else None

            occupied_avatar_colors = room.occupied_avatar_colors()
            participant = room.participants.get(participant_id) if participant_id else None
            if participant is not None:
                participant_color = avatar_color_key(participant.avatar_color)
                occupied_avatar_colors = [
                    color
                    for color in occupied_avatar_colors
                    if color != participant_color
                ]
            return {
                "occupied_avatar_colors": occupied_avatar_colors,
                "own_avatar_color": participant.avatar_color if participant else None,
            }

    def begin_reveal(
        self,
        sid: str,
        host_token: Any,
    ) -> RevealCountdown | None:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            if room.revealed:
                return None
            if room.reveal_countdown_ends_at is not None:
                return RevealCountdown(
                    room_id=room.room_id,
                    seconds=room.reveal_countdown_seconds,
                    ends_at=room.reveal_countdown_ends_at,
                    started=False,
                )
            if not is_valid_story(room.issue):
                raise RoomServiceError("Add a valid story before revealing.")
            if not room.has_votes():
                raise RoomServiceError("Submit at least one vote before revealing.")
            ends_at = time.time() + room.reveal_countdown_seconds
            with self.repository.persist_room(room):
                room.reveal_countdown_ends_at = ends_at
            return RevealCountdown(
                room_id=room.room_id,
                seconds=room.reveal_countdown_seconds,
                ends_at=ends_at,
                started=True,
            )

    def complete_reveal(self, room_id: str, ends_at: float) -> str | None:
        with self.repository.room_lock(room_id):
            room = self.repository.get_room(room_id)
            if room is None or room.revealed:
                return None
            if room.reveal_countdown_ends_at != ends_at or time.time() < ends_at:
                return None
            with self.repository.persist_room(room):
                room.reveal()
            return room.room_id

    def set_reveal_countdown(
        self,
        sid: str,
        seconds: Any,
        host_token: Any,
    ) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            if room.reveal_countdown_ends_at is not None:
                raise RoomServiceError("Wait for the reveal countdown to finish.")
            cleaned_seconds = self._clean_reveal_countdown(seconds)
            if room.reveal_countdown_seconds == cleaned_seconds:
                return room.room_id
            with self.repository.persist_room(room):
                room.reveal_countdown_seconds = cleaned_seconds
            return room.room_id

    def reset_round(self, sid: str, host_token: Any) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            if not room.revealed and room.has_votes():
                raise RoomServiceError("Reveal the current round before starting a new round.")
            with self.repository.persist_room(room):
                room.reset_round()
            return room.room_id

    def start_new_story(self, sid: str, issue: Any, host_token: Any) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            cleaned_issue = clean_text(issue, MAX_ISSUE_LENGTH)
            if not cleaned_issue:
                raise RoomServiceError("Add a story before starting a new story.")
            self.story_queue.validate_story_start(room, cleaned_issue)
            with self.repository.persist_room(room):
                room.start_new_story(cleaned_issue)
            return room.room_id

    def remove_history_story(self, sid: str, round_value: Any, host_token: Any) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            try:
                round_number = int(round_value)
            except (TypeError, ValueError):
                raise RoomServiceError("That history entry could not be found.") from None
            with self.repository.persist_room(room):
                if not room.remove_history_round(round_number):
                    raise RoomServiceError("That history entry could not be found.")
            return room.room_id

    def delete_session(self, sid: str, host_token: Any) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            room_id = room.room_id
            self.repository.delete(room_id)
            return room_id

    def update_issue(self, sid: str, issue: Any, host_token: Any) -> str:
        with self.repository.connected_room(sid) as (room, participant):
            room = self._require_host_locked(room, participant, host_token)
            if room.revealed:
                raise RoomServiceError("Start a new round before changing the story.")
            if room.reveal_countdown_ends_at is not None:
                raise RoomServiceError("Wait for the reveal countdown to finish.")
            cleaned_issue = clean_text(issue, MAX_ISSUE_LENGTH)
            if not cleaned_issue:
                raise RoomServiceError("Add an issue before saving.")
            if story_key(cleaned_issue) != story_key(room.issue) and any(
                story_key(cleaned_issue) == story_key(entry["issue"])
                for entry in room.history
            ):
                raise RoomServiceError("That story has already been added in this session.")
            with self.repository.persist_room(room):
                room.update_issue(cleaned_issue)
            return room.room_id

    def disconnect(self, sid: str) -> str | None:
        while True:
            connection = self.repository.connection_for(sid)
            if connection is None:
                return None
            room_id, participant_id = connection
            with self.repository.room_lock(room_id):
                if self.repository.connection_for(sid) != connection:
                    continue
                self.repository.remove_connection(sid)
                room = self.repository.get_room(room_id)
                if room is None:
                    return None
                participant = room.participants.get(participant_id)
                has_other_connection = self.repository.has_other_connection(
                    sid,
                    connection,
                )
                if participant is not None and not has_other_connection:
                    try:
                        with self.repository.persist_room(room):
                            participant.sid = None
                    except Exception:
                        self.repository.restore_connection(sid, connection)
                        raise
                return room_id