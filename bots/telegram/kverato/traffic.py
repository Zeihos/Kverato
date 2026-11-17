"""Bounded per-player user request limiter, independent of chat moderation.

Only controls traffic directed to this bot. No group chat moderation.
SQLite monetary safeguards remain authoritative; this is a load-shedding layer.
"""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from typing import Callable


class UserRateLimiter:
    def __init__(self, *, limit: int = 20, window: float = 10.0,
                 max_players: int = 4096, clock: Callable[[], float] = time.monotonic):
        if limit < 1 or window <= 0 or max_players < 1:
            raise ValueError('Invalid rate-limit configuration')
        self.limit, self.window, self.max_players, self.clock = limit, window, max_players, clock
        self._users: OrderedDict[int, deque[float]] = OrderedDict()
        self._notified: dict[int, float] = {}

    @property
    def tracked_players(self) -> int:
        return len(self._users)

    def allow(self, user_id: int) -> tuple[bool, bool]:
        """Return (allowed, should_notify). Notify at most once per window."""
        now = self.clock()
        if user_id in self._users:
            requests = self._users[user_id]
            self._users.move_to_end(user_id)
        else:
            if len(self._users) >= self.max_players:
                evicted, _ = self._users.popitem(last=False)
                self._notified.pop(evicted, None)
            requests = deque()
            self._users[user_id] = requests
        while requests and now - requests[0] >= self.window:
            requests.popleft()
        if len(requests) < self.limit:
            requests.append(now)
            return True, False
        previous = self._notified.get(user_id, float('-inf'))
        notify = now - previous >= self.window
        if notify:
            self._notified[user_id] = now
        return False, notify


class BoundedCache(OrderedDict):
    """Small FIFO/LRU-style in-memory cache with an upper bound for long uptimes."""

    def __init__(self, max_items: int = 4096):
        if max_items < 1:
            raise ValueError('Cache capacity must be positive')
        super().__init__()
        self.max_items = max_items

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.max_items:
            self.popitem(last=False)
