import math
import threading
import time
from collections import deque, defaultdict

class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._max_window = 0
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now # monotonic чтобы не ломалось при смене времени

        with self._lock:
            self._max_window = max(self._max_window, window)
            q = self._hits[key]

            while q and q[0] <= now - window:
                q.popleft()

            if len(q) >= limit:
                return False, max(1, math.ceil(q[0] + window - now))

            q.append(now)
            return True, 0

    def purge(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            stale = [
                k for k, q in self._hits.items()
                if not q or q[-1] <= now - self._max_window
            ]
            for k in stale:
                del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()