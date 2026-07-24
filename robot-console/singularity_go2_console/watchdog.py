"""Safety watchdogs for camera, manual TTL, connection, and follow."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


Clock = Callable[[], float]


@dataclass(slots=True)
class WatchdogStatus:
    manual_ok: bool = True
    camera_ok: bool = True
    connection_ok: bool = True
    follow_ok: bool = True
    reason: str | None = None


class Watchdogs:
    """Tracks freshness of camera/manual/connection and follow target loss."""

    def __init__(
        self,
        *,
        camera_stale_ms: int = 500,
        manual_ttl_ms: int = 250,
        clock: Clock | None = None,
    ) -> None:
        self.camera_stale_ms = camera_stale_ms
        self.manual_ttl_ms = manual_ttl_ms
        self._clock: Clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._last_frame_ts: float | None = None
        self._last_manual_ts: float | None = None
        self._connected = False
        self._target_lost_frames = 0
        self._max_lost_frames = 15
        self._manual_active = False

    def mark_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected

    def mark_frame(self) -> None:
        with self._lock:
            self._last_frame_ts = self._clock()

    def mark_manual_command(self) -> None:
        with self._lock:
            self._last_manual_ts = self._clock()
            self._manual_active = True

    def clear_manual(self) -> None:
        with self._lock:
            self._manual_active = False
            self._last_manual_ts = None

    def mark_target_ok(self) -> None:
        with self._lock:
            self._target_lost_frames = 0

    def mark_target_lost_frame(self) -> int:
        with self._lock:
            self._target_lost_frames += 1
            return self._target_lost_frames

    def set_max_lost_frames(self, n: int) -> None:
        with self._lock:
            self._max_lost_frames = n

    @property
    def target_lost_frames(self) -> int:
        with self._lock:
            return self._target_lost_frames

    def frame_age_ms(self) -> float | None:
        with self._lock:
            if self._last_frame_ts is None:
                return None
            return (self._clock() - self._last_frame_ts) * 1000.0

    def check_camera_stale(self) -> bool:
        age = self.frame_age_ms()
        if age is None:
            return True
        return age > self.camera_stale_ms

    def check_manual_ttl_expired(self) -> bool:
        with self._lock:
            if not self._manual_active or self._last_manual_ts is None:
                return False
            age_ms = (self._clock() - self._last_manual_ts) * 1000.0
            return age_ms > self.manual_ttl_ms

    def check_connection_lost(self) -> bool:
        with self._lock:
            return not self._connected

    def check_follow_lost(self) -> bool:
        with self._lock:
            return self._target_lost_frames > self._max_lost_frames

    def status(self) -> WatchdogStatus:
        camera_stale = self.check_camera_stale()
        manual_expired = self.check_manual_ttl_expired()
        conn_lost = self.check_connection_lost()
        follow_lost = self.check_follow_lost()
        reason = None
        if conn_lost:
            reason = "connection_lost"
        elif camera_stale and self._last_frame_ts is not None:
            reason = "camera_stale"
        elif manual_expired:
            reason = "manual_ttl_expired"
        elif follow_lost:
            reason = "target_lost"
        return WatchdogStatus(
            manual_ok=not manual_expired,
            camera_ok=not camera_stale if self._last_frame_ts is not None else True,
            connection_ok=not conn_lost,
            follow_ok=not follow_lost,
            reason=reason,
        )
