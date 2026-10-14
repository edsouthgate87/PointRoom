from __future__ import annotations

import copy
import secrets

from domain import (
    MAX_ISSUE_LENGTH,
    MAX_STORY_QUEUE_ENTRIES,
    MAX_STORY_QUEUE_ID_LENGTH,
    Participant,
    Room,
    clean_text,
)
from room_errors import RoomServiceError


class StoryQueueService:
    @staticmethod
    def validate_suggestions_enabled(enabled: object) -> None:
        if not isinstance(enabled, bool):
            raise RoomServiceError("Choose whether story suggestions are on or off.")

    @staticmethod
    def set_suggestions_enabled(room: Room, enabled: bool) -> None:
        room.suggestions_enabled = enabled

    @staticmethod
    def _clean_queue_id(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return clean_text(value, MAX_STORY_QUEUE_ID_LENGTH)

    @staticmethod
    def _queue_index(
        queue: list[dict[str, str]],
        story_id: str,
    ) -> int | None:
        for index, entry in enumerate(queue):
            if entry.get("id") == story_id:
                return index
        return None

    @staticmethod
    def _new_queue_id(room: Room) -> str:
        queued_ids = {
            entry.get("id")
            for entry in [*room.suggestions, *room.backlog]
        }
        while True:
            story_id = secrets.token_urlsafe(9)
            if story_id not in queued_ids:
                return story_id

    @staticmethod
    def validate_story_start(room: Room, issue: str) -> None:
        if room.has_story(issue):
            raise RoomServiceError("That story has already been added in this session.")
        if not room.revealed and room.has_votes():
            raise RoomServiceError("Reveal the current round before starting a new story.")

    def submit(
        self,
        room: Room,
        participant: Participant,
        issue: object,
    ) -> dict[str, str]:
        if participant.participant_id == room.host_participant_id:
            raise RoomServiceError("Only non-host participants can suggest a story.")
        if not room.suggestions_enabled:
            raise RoomServiceError("Story suggestions are turned off by the host.")
        cleaned_issue = clean_text(issue, MAX_ISSUE_LENGTH)
        if not cleaned_issue:
            raise RoomServiceError("Add a story before suggesting it.")
        if room.has_story(cleaned_issue) or room.has_queued_story(cleaned_issue):
            raise RoomServiceError("That story has already been added in this session.")
        if len(room.suggestions) >= MAX_STORY_QUEUE_ENTRIES:
            raise RoomServiceError("The story suggestion queue is full.")
        suggestion = {
            "id": self._new_queue_id(room),
            "issue": cleaned_issue,
            "suggested_by": participant.name,
            "avatar_color": participant.avatar_color,
        }
        room.suggestions.append(suggestion)
        return copy.deepcopy(suggestion)

    def accept(self, room: Room, suggestion_id: object) -> None:
        cleaned_id = self._clean_queue_id(suggestion_id)
        suggestion_index = self._queue_index(room.suggestions, cleaned_id)
        if suggestion_index is None:
            raise RoomServiceError("That story suggestion could not be found.")
        suggestion = room.suggestions[suggestion_index]
        self.validate_story_start(room, suggestion["issue"])
        room.start_new_story(suggestion["issue"])
        room.suggestions.pop(suggestion_index)

    def move_to_backlog(self, room: Room, suggestion_id: object) -> None:
        cleaned_id = self._clean_queue_id(suggestion_id)
        suggestion_index = self._queue_index(room.suggestions, cleaned_id)
        if suggestion_index is None:
            raise RoomServiceError("That story suggestion could not be found.")
        if len(room.backlog) >= MAX_STORY_QUEUE_ENTRIES:
            raise RoomServiceError("The story backlog is full.")
        suggestion = room.suggestions[suggestion_index]
        if room.has_story(suggestion["issue"]):
            raise RoomServiceError("That story has already been added in this session.")
        room.suggestions.pop(suggestion_index)
        room.backlog.append(suggestion)

    def dismiss(self, room: Room, suggestion_id: object) -> None:
        cleaned_id = self._clean_queue_id(suggestion_id)
        suggestion_index = self._queue_index(room.suggestions, cleaned_id)
        if suggestion_index is None:
            raise RoomServiceError("That story suggestion could not be found.")
        room.suggestions.pop(suggestion_index)

    def use_backlog(self, room: Room, backlog_id: object) -> None:
        cleaned_id = self._clean_queue_id(backlog_id)
        backlog_index = self._queue_index(room.backlog, cleaned_id)
        if backlog_index is None:
            raise RoomServiceError("That backlog story could not be found.")
        backlog_story = room.backlog[backlog_index]
        self.validate_story_start(room, backlog_story["issue"])
        room.start_new_story(backlog_story["issue"])
        room.backlog.pop(backlog_index)

    def remove_backlog(self, room: Room, backlog_id: object) -> None:
        cleaned_id = self._clean_queue_id(backlog_id)
        backlog_index = self._queue_index(room.backlog, cleaned_id)
        if backlog_index is None:
            raise RoomServiceError("That backlog story could not be found.")
        room.backlog.pop(backlog_index)