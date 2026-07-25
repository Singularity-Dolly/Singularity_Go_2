"""Controller event types and subscription helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    ROBOT_CONNECTING = "robot.connecting"
    ROBOT_CONNECTED = "robot.connected"
    ROBOT_DISCONNECTED = "robot.disconnected"
    CAMERA_READY = "camera.ready"
    CAMERA_STALE = "camera.stale"
    TARGET_ACQUIRING = "target.acquiring"
    TARGET_ACQUIRED = "target.acquired"
    TARGET_LOST = "target.lost"
    MODE_CHANGED = "mode.changed"
    VELOCITY_COMMAND = "velocity.command"
    WATCHDOG_TIMEOUT = "watchdog.timeout"
    ESTOP_ACTIVATED = "estop.activated"
    ESTOP_RESET = "estop.reset"
    ROBOT_ERROR = "robot.error"
    ROBOT_SHUTDOWN = "robot.shutdown"
    MANUAL_TTL_EXPIRED = "manual.ttl_expired"
    LOG = "log"


@dataclass(slots=True)
class ControllerEvent:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


EventCallback = Callable[[ControllerEvent], None]


class EventBus:
    """Simple synchronous pub/sub used by Go2Controller."""

    def __init__(self) -> None:
        self._callbacks: list[EventCallback] = []

    def subscribe(self, callback: EventCallback) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def emit(self, event_type: EventType, **payload: Any) -> ControllerEvent:
        event = ControllerEvent(type=event_type, payload=payload)
        for callback in list(self._callbacks):
            callback(event)
        return event
