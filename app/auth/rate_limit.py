from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Single-instance fixed-window limiter. Put a distributed gateway/Redis in front when API replicas are added."""
    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, client_id: str, limit: int) -> tuple[bool, int, int]:
        now = time.time()
        cutoff = now - 60
        with self._lock:
            q = self._events[client_id]
            while q and q[0] <= cutoff:
                q.popleft()
            allowed = len(q) < max(1, limit)
            if allowed:
                q.append(now)
            remaining = max(0, limit - len(q))
            reset = int((q[0] + 60) if q else (now + 60))
            return allowed, remaining, reset
