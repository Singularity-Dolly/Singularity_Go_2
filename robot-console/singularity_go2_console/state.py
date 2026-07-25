"""Modes, results, and serializable robot status."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ControllerMode(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    IDLE = "IDLE"
    MANUAL = "MANUAL"
    ACQUIRING_TARGET = "ACQUIRING_TARGET"
    FOLLOWING = "FOLLOWING"
    ESTOP = "ESTOP"
    ERROR = "ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class VelocityOwner(str, Enum):
    NONE = "NONE"
    MANUAL = "MANUAL"
    FOLLOW = "FOLLOW"
    ESTOP = "ESTOP"


# Allowed transitions for the explicit state machine.
ALLOWED_TRANSITIONS: dict[ControllerMode, set[ControllerMode]] = {
    ControllerMode.DISCONNECTED: {ControllerMode.CONNECTING, ControllerMode.SHUTTING_DOWN},
    ControllerMode.CONNECTING: {
        ControllerMode.IDLE,
        ControllerMode.ERROR,
        ControllerMode.SHUTTING_DOWN,
    },
    ControllerMode.IDLE: {
        ControllerMode.MANUAL,
        ControllerMode.ACQUIRING_TARGET,
        ControllerMode.ESTOP,
        ControllerMode.SHUTTING_DOWN,
        ControllerMode.ERROR,
    },
    ControllerMode.MANUAL: {
        ControllerMode.IDLE,
        ControllerMode.ACQUIRING_TARGET,
        ControllerMode.ESTOP,
        ControllerMode.ERROR,
        ControllerMode.SHUTTING_DOWN,
    },
    ControllerMode.ACQUIRING_TARGET: {
        ControllerMode.FOLLOWING,
        ControllerMode.IDLE,
        ControllerMode.ESTOP,
        ControllerMode.ERROR,
        ControllerMode.SHUTTING_DOWN,
    },
    ControllerMode.FOLLOWING: {
        ControllerMode.IDLE,
        ControllerMode.MANUAL,
        ControllerMode.ACQUIRING_TARGET,
        ControllerMode.ESTOP,
        ControllerMode.ERROR,
        ControllerMode.SHUTTING_DOWN,
    },
    ControllerMode.ESTOP: {
        ControllerMode.IDLE,  # only via explicit reset
        ControllerMode.SHUTTING_DOWN,
    },
    ControllerMode.ERROR: {
        ControllerMode.IDLE,  # only after successful recovery
        ControllerMode.SHUTTING_DOWN,
    },
    ControllerMode.SHUTTING_DOWN: {ControllerMode.DISCONNECTED},
}


class ErrorCode(str, Enum):
    OK = "OK"
    DIMOS_NOT_AVAILABLE = "DIMOS_NOT_AVAILABLE"
    ROBOT_UNREACHABLE = "ROBOT_UNREACHABLE"
    WEBRTC_CONNECTION_FAILED = "WEBRTC_CONNECTION_FAILED"
    CAMERA_NOT_READY = "CAMERA_NOT_READY"
    CAMERA_STALE = "CAMERA_STALE"
    DETECTOR_NOT_READY = "DETECTOR_NOT_READY"
    NO_PERSON_FOUND = "NO_PERSON_FOUND"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    TRACKER_INIT_FAILED = "TRACKER_INIT_FAILED"
    FOLLOW_SKILL_NOT_READY = "FOLLOW_SKILL_NOT_READY"
    VELOCITY_OUTPUT_NOT_READY = "VELOCITY_OUTPUT_NOT_READY"
    COMMAND_TTL_EXPIRED = "COMMAND_TTL_EXPIRED"
    ESTOP_ACTIVE = "ESTOP_ACTIVE"
    INVALID_MODE_TRANSITION = "INVALID_MODE_TRANSITION"
    CONNECTION_LOST = "CONNECTION_LOST"
    UNSUPPORTED_DIMOS_VERSION = "UNSUPPORTED_DIMOS_VERSION"
    VELOCITY_REJECTED = "VELOCITY_REJECTED"
    MOCK_REQUIRED = "MOCK_REQUIRED"
    SHUTDOWN = "SHUTDOWN"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    AES_KEY_REQUIRED = "AES_KEY_REQUIRED"
    AES_KEY_INVALID = "AES_KEY_INVALID"
    STA_ROBOT_IP_REQUIRED = "STA_ROBOT_IP_REQUIRED"
    WEBRTC_DATA_CHANNEL_FAILED = "WEBRTC_DATA_CHANNEL_FAILED"
    LOCAL_AP_SIGNALING_FAILED = "LOCAL_AP_SIGNALING_FAILED"
    LOCAL_STA_SIGNALING_FAILED = "LOCAL_STA_SIGNALING_FAILED"
    CAMERA_STREAM_UNAVAILABLE = "CAMERA_STREAM_UNAVAILABLE"
    VELOCITY_CHANNEL_UNAVAILABLE = "VELOCITY_CHANNEL_UNAVAILABLE"
    MOTION_MODE_NOT_NORMAL = "MOTION_MODE_NOT_NORMAL"
    MOTION_MODE_SWITCH_DISABLED = "MOTION_MODE_SWITCH_DISABLED"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ControllerResult:
    ok: bool
    code: ErrorCode = ErrorCode.OK
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now_iso)

    @classmethod
    def success(cls, message: str = "ok", **details: Any) -> ControllerResult:
        return cls(ok=True, code=ErrorCode.OK, message=message, details=details)

    @classmethod
    def failure(
        cls,
        code: ErrorCode,
        message: str,
        **details: Any,
    ) -> ControllerResult:
        return cls(ok=False, code=code, message=message, details=details)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["code"] = self.code.value
        return data


@dataclass(slots=True)
class RobotStatus:
    robot_id: str = "go2"
    robot_ip: str | None = None
    connected: bool = False
    mode: str = ControllerMode.DISCONNECTED.value
    estop: bool = False

    camera_ready: bool = False
    last_frame_age_ms: float | None = None

    detector_ready: bool = False
    target_visible: bool = False
    target_confidence: float | None = None
    target_bbox: list[float] | None = None
    target_lost_frames: int = 0

    velocity_owner: str = VelocityOwner.NONE.value
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0

    manual_watchdog_ok: bool = True
    camera_watchdog_ok: bool = True
    connection_watchdog_ok: bool = True

    last_command: str | None = None
    last_error: str | None = None
    last_error_code: str | None = None
    updated_at: str = field(default_factory=utc_now_iso)
    mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
