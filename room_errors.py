from __future__ import annotations


class RoomServiceError(ValueError):
    pass


class RoomCapacityError(RoomServiceError):
    pass