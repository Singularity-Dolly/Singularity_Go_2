"""Safety layer for DollyGatewayModule.

Multi-layer safety guarantees independent of the robot control loop:
1. CommandGuard — TTL expiration rejection
2. HeartbeatWatchdog — 1.5s heartbeat timeout triggers emergency stop
3. FrameGuard — stale frame detection (>500ms returns None)
4. SafetyController — emergency stop priority, bypasses all queues

All components are pure Python, tested without hardware.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .contracts import CommandRequest, RejectionReason


# ---------------------------------------------------------------------------
# CommandGuard — TTL validation
# ---------------------------------------------------------------------------

@dataclass
class TtlResult:
    valid: bool
    reason: str = "ok"
    age_ms: float = 0.0


class CommandGuard:
    """Validates that a command's TTL has not expired."""

    def validate(self, command: CommandRequest) -> TtlResult:
        age_ms = _age_ms_since(command.created_at_ms)
        if age_ms > command.ttl_ms:
            return TtlResult(
                valid=False,
                reason=RejectionReason.TTL_EXPIRED.value,
                age_ms=age_ms,
            )
        return TtlResult(valid=True, age_ms=age_ms)


# ---------------------------------------------------------------------------
# HeartbeatWatchdog — timeout-triggered emergency stop
# ---------------------------------------------------------------------------

class HeartbeatWatchdog:
    """Monitors heartbeat from robot connection.

    If no heartbeat is received within HEARTBEAT_TIMEOUT_MS,
    triggers the emergency stop callback.
    """

    HEARTBEAT_TIMEOUT_MS: float = 1500.0
    CHECK_INTERVAL_S: float = 0.1

    def __init__(self, estop_callback: Callable[[], None] | None = None) -> None:
        self._last_heartbeat: float = time.monotonic()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._estop_callback = estop_callback
        self._estop_triggered = False

    @property
    def last_heartbeat_age_ms(self) -> float:
        with self._lock:
            return (time.monotonic() - self._last_heartbeat) * 1000

    @property
    def estop_triggered(self) -> bool:
        return self._estop_triggered

    def heartbeat(self) -> None:
        """Record a heartbeat (called by robot connection layer)."""
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def start(self) -> None:
        """Start the watchdog thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._watch, daemon=True, name="heartbeat-watchdog")
        self._thread.start()

    def stop(self) -> None:
        """Stop the watchdog thread."""
        self._running = False

    def _watch(self) -> None:
        while self._running:
            age_ms = self.last_heartbeat_age_ms
            if age_ms > self.HEARTBEAT_TIMEOUT_MS and not self._estop_triggered:
                self._estop_triggered = True
                if self._estop_callback:
                    self._estop_callback()
            time.sleep(self.CHECK_INTERVAL_S)


# ---------------------------------------------------------------------------
# FrameGuard — stale frame detection
# ---------------------------------------------------------------------------

class FrameGuard:
    """Detects stale frames.

    If the latest frame is older than MAX_FRAME_AGE_MS (500ms),
    get_latest_frame() returns None to signal STALE_FRAME.
    """

    MAX_FRAME_AGE_MS: float = 500.0

    def __init__(self) -> None:
        self._latest_frame: bytes | None = None
        self._latest_frame_ts: float = 0.0
        self._lock = threading.Lock()

    @property
    def has_fresh_frame(self) -> bool:
        with self._lock:
            if self._latest_frame is None:
                return False
            return (time.monotonic() - self._latest_frame_ts) * 1000 <= self.MAX_FRAME_AGE_MS

    @property
    def frame_age_ms(self) -> float | None:
        with self._lock:
            if self._latest_frame is None:
                return None
            return (time.monotonic() - self._latest_frame_ts) * 1000

    def update(self, jpeg_bytes: bytes) -> None:
        """Store a new frame with current timestamp. Ignores empty bytes."""
        if not jpeg_bytes:
            return
        with self._lock:
            self._latest_frame = jpeg_bytes
            self._latest_frame_ts = time.monotonic()

    def get_latest_frame(self) -> bytes | None:
        """Return the latest frame, or None if stale."""
        with self._lock:
            if self._latest_frame is None:
                return None
            age_ms = (time.monotonic() - self._latest_frame_ts) * 1000
            if age_ms > self.MAX_FRAME_AGE_MS:
                return None
            return self._latest_frame


# ---------------------------------------------------------------------------
# SafetyController — emergency stop state machine
# ---------------------------------------------------------------------------

@dataclass
class SafetyState:
    estop: bool = False
    obstacle: bool = False
    heartbeat_ok: bool = True
    last_command_age_ms: float = 0.0


class SafetyController:
    """Central safety state machine.

    Rules:
    - POST /v1/stop bypasses all queues and TTL checks
    - When estop is active, all commands are rejected
    - estop can only be cleared by explicit reset
    """

    def __init__(self) -> None:
        self._estop = False
        self._obstacle = False
        self._heartbeat_ok = True
        self._last_command_ts: float = 0.0
        self._lock = threading.Lock()

    @property
    def estop_active(self) -> bool:
        with self._lock:
            return self._estop

    @property
    def last_command_age_ms(self) -> float:
        with self._lock:
            if self._last_command_ts == 0.0:
                return 0.0
            return (time.monotonic() - self._last_command_ts) * 1000

    def trigger_estop(self) -> None:
        """Activate emergency stop. Blocks all subsequent commands."""
        with self._lock:
            self._estop = True

    def reset_estop(self) -> None:
        """Clear emergency stop. Only call after operator confirms safety."""
        with self._lock:
            self._estop = False

    def update_obstacle(self, detected: bool) -> None:
        with self._lock:
            self._obstacle = detected

    def update_heartbeat(self, ok: bool) -> None:
        with self._lock:
            self._heartbeat_ok = ok

    def record_command(self) -> None:
        with self._lock:
            self._last_command_ts = time.monotonic()

    def can_accept_command(self) -> tuple[bool, str]:
        """Check if a command can be accepted under current safety state."""
        with self._lock:
            if self._estop:
                return False, RejectionReason.ESTOP_ACTIVE.value
            return True, "ok"

    def snapshot(self) -> SafetyState:
        with self._lock:
            age_ms = 0.0
            if self._last_command_ts > 0.0:
                age_ms = (time.monotonic() - self._last_command_ts) * 1000
            return SafetyState(
                estop=self._estop,
                obstacle=self._obstacle,
                heartbeat_ok=self._heartbeat_ok,
                last_command_age_ms=age_ms,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _age_ms_since(monotonic_ts: float) -> float:
    return (time.monotonic() - monotonic_ts) * 1000


# ---------------------------------------------------------------------------
# CommandRequest extension for TTL tracking
# ---------------------------------------------------------------------------

def attach_timestamp(command: CommandRequest) -> CommandRequest:
    """Attach a monotonic creation timestamp to a command for TTL validation.

    Uses object.__setattr__ to record the timestamp on the Pydantic model
    without modifying the schema.
    """
    object.__setattr__(command, "created_at_ms", time.monotonic())
    return command


def get_timestamp(command: CommandRequest) -> float:
    """Retrieve the monotonic creation timestamp."""
    return getattr(command, "created_at_ms", time.monotonic())