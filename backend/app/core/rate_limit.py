"""Limitador de intentos en memoria (ventana deslizante).

Es por instancia de Cloud Run: con max-instances bajo (2–3) es suficiente para
frenar fuerza bruta en el login del panel. Si se necesita un límite global, mover
el contador a Firestore.
"""

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_s: int):
        self.max_events = max_events
        self.window_s = window_s
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _purge(self, key: str, now: float) -> deque:
        """Eventos vigentes de la clave. Las claves sin eventos no se guardan, para que
        la memoria no crezca con cada IP distinta (la demo es pública)."""
        q = self._events.get(key)
        if q is None:
            return deque()
        while q and now - q[0] > self.window_s:
            q.popleft()
        if not q:
            del self._events[key]
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
            q = self._purge(key, now)
            q.append(now)
            self._events[key] = q
            if len(self._events) > self.SWEEP_AT:
                for k in list(self._events):
                    self._purge(k, now)

    # Con más claves que esto se limpian todas las vencidas (IP que no volvieron)
    SWEEP_AT = 5000

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
