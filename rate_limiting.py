from __future__ import annotations

from collections import OrderedDict, deque
import time
from threading import RLock

DEFAULT_EVENT_RATE_LIMITER_MAX_KEYS = 10000


class EventRateLimiter:
    def __init__(self, max_keys: int = DEFAULT_EVENT_RATE_LIMITER_MAX_KEYS) -> None:
        self.events: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = RLock()
        self.max_keys = max(1, int(max_keys))

    def _prune(self, key: str, expiries: deque[float], now: float) -> deque[float] | None:
        while expiries and expiries[0] <= now:
            expiries.popleft()
        if not expiries:
            self.events.pop(key, None)
            return None
        return expiries

    def _prune_expired(self, now: float) -> None:
        for event_key, event_expiries in list(self.events.items()):
            self._prune(event_key, event_expiries, now)

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self.lock:
            expiries = self.events.get(key)
            if expiries is not None:
                expiries = self._prune(key, expiries, now)

            if expiries is None:
                if len(self.events) >= self.max_keys:
                    self._prune_expired(now)
                while len(self.events) >= self.max_keys:
                    self.events.popitem(last=False)
                expiries = deque()
                self.events[key] = expiries

            self.events.move_to_end(key)
            if len(expiries) >= limit:
                return False
            expiries.append(now + window_seconds)
            return True

    def remove(self, key: str) -> None:
        with self.lock:
            self.events.pop(key, None)

    def clear(self) -> None:
        with self.lock:
            self.events.clear()


class SocketConnectionLimiter:
    def __init__(self) -> None:
        self.sids: set[str] = set()
        self.lock = RLock()

    def allow(self, sid: str, limit: int) -> bool:
        maximum = max(1, int(limit))
        with self.lock:
            if sid in self.sids:
                return True
            if len(self.sids) >= maximum:
                return False
            self.sids.add(sid)
            return True

    def remove(self, sid: str) -> None:
        with self.lock:
            self.sids.discard(sid)

    def clear(self) -> None:
        with self.lock:
            self.sids.clear()