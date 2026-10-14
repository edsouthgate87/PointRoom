from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

FIBONACCI_DECK = ("0", "0.5", "1", "2", "3", "5", "8", "13", "21", "?", "break")
SAFE_FIBONACCI_DECK = (
    "0",
    "0.5",
    "1",
    "2",
    "3",
    "5",
    "8",
    "13",
    "20",
    "40",
    "100",
    "?",
    "break",
)
T_SHIRT_DECK = ("XS", "S", "M", "L", "XL", "XXL", "?", "break")
DEFAULT_DECK = FIBONACCI_DECK
CARD_DECKS = {
    "fibonacci": FIBONACCI_DECK,
    "safe_fibonacci": SAFE_FIBONACCI_DECK,
    "t_shirt": T_SHIRT_DECK,
}
AVATAR_COLORS = ("yellow", "mint", "blue", "coral")
AVATAR_COLOR_HEX = {
    "yellow": "#f1c963",
    "mint": "#7bd09f",
    "blue": "#4f7184",
    "coral": "#ed725e",
}
AVATAR_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_REVEAL_COUNTDOWN_SECONDS = 5
MIN_REVEAL_COUNTDOWN_SECONDS = 1
MAX_REVEAL_COUNTDOWN_SECONDS = 60
ROOM_SCHEMA_VERSION = 8
MAX_PARTICIPANTS_PER_ROOM = 50
MAX_NAME_LENGTH = 32
MAX_ISSUE_LENGTH = 500
MAX_HISTORY_ENTRIES = 25
MAX_HISTORY_BYTES = 8 * 1024
MAX_STORY_QUEUE_ENTRIES = 25
MAX_STORY_QUEUE_ID_LENGTH = 80


def story_key(issue: str) -> str:
    return issue.casefold()


def deck_for_name(value: Any) -> tuple[str, ...] | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_DECK
    if not isinstance(value, str):
        return None
    return CARD_DECKS.get(value.strip().lower())


def deck_name_for(deck: Any) -> str | None:
    if not isinstance(deck, (list, tuple)):
        return None
    normalized_deck = tuple(deck)
    return next(
        (
            deck_name
            for deck_name, supported_deck in CARD_DECKS.items()
            if supported_deck == normalized_deck
        ),
        None,
    )


def numeric_vote(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def clean_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:maximum]


def is_valid_story(value: Any) -> bool:
    return bool(clean_text(value, MAX_ISSUE_LENGTH))


def clean_participant_id(value: Any) -> str:
    return clean_text(value, 80)


def hash_host_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def avatar_color_for(participant_id: str) -> str:
    digest = hashlib.sha256(participant_id.encode("utf-8")).digest()
    return AVATAR_COLORS[int.from_bytes(digest[:2], "big") % len(AVATAR_COLORS)]


def normalize_avatar_color(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    if normalized in AVATAR_COLORS or AVATAR_COLOR_PATTERN.fullmatch(normalized):
        return normalized
    return ""


def avatar_color_key(value: Any) -> str:
    normalized = normalize_avatar_color(value)
    return AVATAR_COLOR_HEX.get(normalized, normalized)


def normalize_reveal_countdown(value: Any) -> int:
    if isinstance(value, bool):
        return DEFAULT_REVEAL_COUNTDOWN_SECONDS
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_REVEAL_COUNTDOWN_SECONDS
    return min(
        max(seconds, MIN_REVEAL_COUNTDOWN_SECONDS),
        MAX_REVEAL_COUNTDOWN_SECONDS,
    )


def history_size_bytes(history: list[dict[str, Any]]) -> int:
    return len(
        json.dumps(history, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def trim_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retained_history = [entry for entry in history if isinstance(entry, dict)]
    while (
        len(retained_history) > MAX_HISTORY_ENTRIES
        or history_size_bytes(retained_history) > MAX_HISTORY_BYTES
    ):
        retained_history.pop(0)
    return retained_history


def normalize_story_queue(entries: Any) -> list[dict[str, str]]:
    if not isinstance(entries, list):
        return []

    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        story_id = clean_text(entry.get("id"), MAX_STORY_QUEUE_ID_LENGTH)
        issue = clean_text(entry.get("issue"), MAX_ISSUE_LENGTH)
        suggested_by = clean_text(entry.get("suggested_by"), MAX_NAME_LENGTH)
        if not story_id or not issue or not suggested_by or story_id in seen_ids:
            continue
        seen_ids.add(story_id)
        normalized.append(
            {
                "id": story_id,
                "issue": issue,
                "suggested_by": suggested_by,
                "avatar_color": normalize_avatar_color(entry.get("avatar_color"))
                or avatar_color_for(story_id),
            }
        )
        if len(normalized) >= MAX_STORY_QUEUE_ENTRIES:
            break
    return normalized


@dataclass
class Participant:
    participant_id: str
    name: str
    vote: str | None = None
    sid: str | None = None
    credential_hash: str = ""
    avatar_color: str = ""

    def __post_init__(self) -> None:
        self.avatar_color = normalize_avatar_color(self.avatar_color)
        if not self.avatar_color:
            self.avatar_color = avatar_color_for(self.participant_id)


@dataclass
class Room:
    room_id: str
    host_token_hash: str
    host_participant_id: str
    issue: str
    participants: dict[str, Participant] = field(default_factory=dict)
    revealed: bool = False
    revealed_participant_ids: tuple[str, ...] = field(default_factory=tuple)
    round_number: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)
    deck: tuple[str, ...] = DEFAULT_DECK
    reveal_countdown_seconds: int = DEFAULT_REVEAL_COUNTDOWN_SECONDS
    suggestions_enabled: bool = False
    suggestions: list[dict[str, str]] = field(default_factory=list)
    backlog: list[dict[str, str]] = field(default_factory=list)
    reveal_countdown_ends_at: float | None = field(default=None, repr=False, compare=False)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.history = trim_history(self.history)
        occupied_colors = set()
        for participant in self.participants.values():
            color_key = avatar_color_key(participant.avatar_color)
            if color_key in occupied_colors:
                for attempt in range(256):
                    digest = hashlib.sha256(
                        f"{participant.participant_id}:{attempt}".encode("utf-8")
                    ).hexdigest()
                    replacement_color = f"#{digest[:6]}"
                    if replacement_color not in occupied_colors:
                        participant.avatar_color = replacement_color
                        color_key = replacement_color
                        break
                else:
                    raise ValueError("Could not allocate a unique avatar color.")
            occupied_colors.add(color_key)
        raw_revealed_participant_ids = (
            self.revealed_participant_ids
            if isinstance(self.revealed_participant_ids, (list, tuple))
            else []
        )
        revealed_participant_ids = []
        seen_participant_ids = set()
        for raw_participant_id in raw_revealed_participant_ids:
            participant_id = clean_participant_id(raw_participant_id)
            if (
                participant_id
                and participant_id in self.participants
                and participant_id not in seen_participant_ids
            ):
                revealed_participant_ids.append(participant_id)
                seen_participant_ids.add(participant_id)
        if self.revealed and not revealed_participant_ids:
            for entry in reversed(self.history):
                if (
                    entry.get("round") == self.round_number
                    and entry.get("status") == "revealed"
                ):
                    votes = entry.get("votes")
                    if isinstance(votes, list):
                        for vote in votes:
                            if not isinstance(vote, dict):
                                continue
                            participant_id = clean_participant_id(
                                vote.get("participant_id")
                            )
                            if (
                                participant_id
                                and participant_id in self.participants
                                and participant_id not in seen_participant_ids
                            ):
                                revealed_participant_ids.append(participant_id)
                                seen_participant_ids.add(participant_id)
                    break
        self.revealed_participant_ids = tuple(revealed_participant_ids)
        self.suggestions_enabled = bool(self.suggestions_enabled)
        self.suggestions = normalize_story_queue(self.suggestions)
        self.backlog = normalize_story_queue(self.backlog)
        self.reveal_countdown_seconds = normalize_reveal_countdown(
            self.reveal_countdown_seconds
        )
        for entry in self.history:
            votes = entry.get("votes")
            if not isinstance(votes, list):
                continue
            for vote in votes:
                if not isinstance(vote, dict):
                    continue
                participant_id = str(vote.get("participant_id", ""))
                participant = self.participants.get(participant_id)
                stored_color = normalize_avatar_color(vote.get("avatar_color"))
                vote["avatar_color"] = stored_color or (
                    participant.avatar_color
                    if participant is not None
                    else avatar_color_for(participant_id)
                )

    def active_participants(self) -> list[Participant]:
        return [
            participant
            for participant in self.participants.values()
            if participant.sid is not None
        ]

    def occupied_avatar_colors(self) -> list[str]:
        return sorted(
            {
                avatar_color_key(participant.avatar_color)
                for participant in self.participants.values()
                if avatar_color_key(participant.avatar_color)
            }
        )

    def to_dict(self) -> dict[str, Any]:
        from room_snapshot import RoomSnapshotMapper

        return RoomSnapshotMapper.to_dict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Room:
        from room_snapshot import RoomSnapshotMapper

        return RoomSnapshotMapper.from_dict(payload, cls)

    def has_story(self, issue: str) -> bool:
        story_key_value = story_key(issue)
        return story_key_value == story_key(self.issue) or any(
            story_key_value == story_key(entry.get("issue", ""))
            for entry in self.history
        )

    def has_queued_story(self, issue: str) -> bool:
        story_key_value = story_key(issue)
        return any(
            story_key_value == story_key(entry["issue"])
            for entry in [*self.suggestions, *self.backlog]
        )

    def archive_current_round(self, status: str) -> None:
        if not self.issue:
            return

        vote_records = [
            {
                "participant_id": participant.participant_id,
                "name": participant.name,
                "vote": participant.vote,
                "avatar_color": participant.avatar_color,
            }
            for participant in sorted(self.active_participants(), key=lambda item: item.name.casefold())
            if participant.vote is not None
        ]
        numeric_votes = [
            numeric_value
            for record in vote_records
            if (numeric_value := numeric_vote(record["vote"])) is not None
        ]
        distribution = {card: 0 for card in self.deck}
        for record in vote_records:
            distribution[record["vote"]] += 1

        self.history.append(
            {
                "round": self.round_number,
                "issue": self.issue,
                "status": status,
                "votes": vote_records,
                "voter_count": len(vote_records),
                "average": round(sum(numeric_votes) / len(numeric_votes), 1) if numeric_votes else None,
                "distribution": distribution,
            }
        )
        self.history = trim_history(self.history)

    def has_votes(self) -> bool:
        return any(participant.vote is not None for participant in self.active_participants())

    def reveal(self) -> None:
        if self.revealed:
            return
        self.revealed_participant_ids = tuple(
            participant.participant_id for participant in self.active_participants()
        )
        self.revealed = True
        self.reveal_countdown_ends_at = None
        self.archive_current_round("revealed")

    def reset_round(self) -> None:
        if not self.revealed:
            self.archive_current_round("skipped")
        self.revealed = False
        self.revealed_participant_ids = ()
        self.reveal_countdown_ends_at = None
        self.issue = ""
        self.round_number += 1
        for participant in self.participants.values():
            participant.vote = None

    def start_new_story(self, issue: str) -> None:
        if not self.revealed:
            self.archive_current_round("skipped")
        self.issue = issue
        self.revealed = False
        self.revealed_participant_ids = ()
        self.reveal_countdown_ends_at = None
        self.round_number += 1
        for participant in self.participants.values():
            participant.vote = None

    def update_issue(self, issue: str) -> None:
        self.issue = issue

    def remove_history_round(self, round_number: int) -> bool:
        remaining_history = []
        removed = False
        for entry in self.history:
            try:
                entry_round = int(entry.get("round"))
            except (AttributeError, TypeError, ValueError):
                entry_round = None
            if not removed and entry_round == round_number:
                removed = True
                continue
            remaining_history.append(entry)
        if removed:
            self.history = remaining_history
        return removed

    def public_state(self) -> dict[str, Any]:
        from room_projection import RoomPublicProjector

        return RoomPublicProjector.state(self)

    def public_history(self) -> list[dict[str, Any]]:
        from room_projection import RoomPublicProjector

        return RoomPublicProjector.history(self)