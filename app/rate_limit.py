from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable


class SlidingWindowLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        limit = max(1, int(limit))
        window = max(1, int(window_seconds))
        now = float(self.clock())
        cutoff = now - window
        normalized = str(key)
        with self._lock:
            events = self._events[normalized]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True
