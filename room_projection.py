from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domain import Room


class RoomPublicProjector:
    @staticmethod
    def state(room: Room) -> dict[str, Any]:
        from domain import deck_name_for, numeric_vote

        participants = []
        votes = []
        distribution = {card: 0 for card in room.deck}

        revealed_round = next(
            (
                entry
                for entry in reversed(room.history)
                if room.revealed
                and entry.get("round") == room.round_number
                and entry.get("status") == "revealed"
            ),
            None,
        )
        recorded_votes = revealed_round.get("votes") if revealed_round else None
        recorded_distribution = (
            revealed_round.get("distribution") if revealed_round else None
        )
        has_recorded_result = isinstance(recorded_votes, list) and isinstance(
            recorded_distribution, dict
        )
        participants_to_show = (
            [
                room.participants[participant_id]
                for participant_id in room.revealed_participant_ids
                if participant_id in room.participants
            ]
            if room.revealed
            else room.active_participants()
        )

        for participant in sorted(participants_to_show, key=lambda item: item.name.casefold()):
            if not has_recorded_result and participant.vote is not None and participant.sid is not None:
                votes.append(participant.vote)
                distribution[participant.vote] += 1

            participant_state = {
                "id": participant.participant_id,
                "name": participant.name,
                "has_voted": participant.vote is not None,
                "is_host": participant.participant_id == room.host_participant_id,
                "avatar_color": participant.avatar_color,
                "vote": participant.vote if room.revealed else None,
            }
            participants.append(participant_state)

        if has_recorded_result:
            votes = [
                vote["vote"]
                for vote in recorded_votes
                if isinstance(vote, dict) and vote.get("vote") in room.deck
            ]
            distribution = {
                card: recorded_distribution.get(card, 0)
                for card in room.deck
            }

        numeric_votes = [
            numeric_value
            for vote in votes
            if (numeric_value := numeric_vote(vote)) is not None
        ]
        average = (
            revealed_round.get("average")
            if has_recorded_result and revealed_round is not None
            else round(sum(numeric_votes) / len(numeric_votes), 1)
            if numeric_votes
            else None
        )

        return {
            "room_id": room.room_id,
            "issue": room.issue,
            "suggestions_enabled": room.suggestions_enabled,
            "suggestions": copy.deepcopy(room.suggestions),
            "backlog": copy.deepcopy(room.backlog),
            "round_number": room.round_number,
            "deck": list(room.deck),
            "deck_name": deck_name_for(room.deck),
            "reveal_countdown_seconds": room.reveal_countdown_seconds,
            "reveal_countdown_ends_at": (
                room.reveal_countdown_ends_at if not room.revealed else None
            ),
            "revealed": room.revealed,
            "participants": participants,
            "participant_count": len(participants),
            "occupied_avatar_colors": room.occupied_avatar_colors(),
            "voted_count": len(votes),
            "average": average if room.revealed else None,
            "distribution": distribution if room.revealed else {},
        }

    @staticmethod
    def history(room: Room) -> list[dict[str, Any]]:
        history = copy.deepcopy(room.history)
        for entry in history:
            votes = entry.get("votes")
            if not isinstance(votes, list):
                continue
            for vote in votes:
                if not isinstance(vote, dict):
                    continue
                participant = room.participants.get(str(vote.get("participant_id", "")))
                if participant is not None:
                    vote["name"] = participant.name
                    vote["avatar_color"] = participant.avatar_color
        return history