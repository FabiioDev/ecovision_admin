"""Limitador de intentos en memoria (ventana deslizante).

Es por instancia de Cloud Run: con max-instances bajo (2–3) es suficiente para
frenar fuerza bruta en el login del panel. Si se necesita un límite global, mover
el contador a Firestore.
"""

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_s: int):
        self.max_events = max_events
        self.window_s = window_s
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _purge(self, key: str, now: float) -> deque:
        q = self._events[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if not q:
            self._events.pop(key, None)
            q = self._events[key]
        return q

    def blocked_for(self, key: str) -> int:
        """Segundos que faltan para desbloquear la clave (0 si no está bloqueada)."""
        now = time.monotonic()
        with self._lock:
            q = self._purge(key, now)
            if len(q) < self.max_events:
                return 0
            return max(1, int(self.window_s - (now - q[0])))

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._purge(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
