"""Pydantic contracts for DollyGatewayModule robot-service API.

All request/response models follow Dolly ADX26 conventions:
- Strict validation (extra="forbid")
- Literal version strings ("v": "1.0")
- ISO 8601 UTC timestamps
- Error responses with code + message pairs
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Python 3.10 compatible StrEnum
class _StrEnum(str, Enum):
    """String enum base class for Python 3.10 compatibility."""

    def __str__(self) -> str:
        return self.value


SCHEMA_VERSION: Literal["1.0"] = "1.0"


# ---- Enums ----

class RobotMode(_StrEnum):
    IDLE = "idle"
    NAVIGATING = "navigating"
    FOLLOWING = "following"
    EXPLORING = "exploring"


class CommandKind(_StrEnum):
    SCAN_START = "scan.start"
    FOLLOW_START = "follow.start"
    FOLLOW_HOLD = "follow.hold"
    MISSION_STOP = "mission.stop"


class TargetKind(_StrEnum):
    PRIMARY_PERSON = "primary_person"


class RejectionReason(_StrEnum):
    TTL_EXPIRED = "ttl_expired"
    BUSY = "busy"
    ESTOP_ACTIVE = "estop_active"
    QUEUE_FULL = "queue_full"
    UNKNOWN_COMMAND = "unknown_command"


class EventType(_StrEnum):
    ROBOT_STATE = "robot.state"
    ROBOT_CONNECTED = "robot.connected"
    ROBOT_DISCONNECTED = "robot.disconnected"
    ROBOT_SAFETY = "robot.safety"
    ROBOT_COMMAND = "robot.command"
    ROBOT_FRAME = "robot.frame"


# ---- Health ----

class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    robot_connected: bool = False
    uptime_seconds: float = 0.0
    ts_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ts_utc")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts_utc must be timezone-aware")
        return value.astimezone(timezone.utc)


# ---- Robot State ----

class TargetState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = False
    track_id: str | None = None
    confidence: float | None = None


class ScanState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool = False
    path: list[dict[str, float]] = Field(default_factory=list)
    map_source: Literal["voxel", "coverage", "unavailable"] = "unavailable"


class SafetyState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estop: bool = False
    obstacle: bool = False
    heartbeat_ok: bool = True
    last_command_age_ms: float = 0.0


class RobotStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal["1.0"] = SCHEMA_VERSION
    robot_id: str = "unknown"
    connected: bool = False
    mode: RobotMode = RobotMode.IDLE
    target: TargetState = Field(default_factory=TargetState)
    scan: ScanState = Field(default_factory=ScanState)
    safety: SafetyState = Field(default_factory=SafetyState)
    ts_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ts_utc")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts_utc must be timezone-aware")
        return value.astimezone(timezone.utc)


# ---- Commands ----

class CommandTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TargetKind = TargetKind.PRIMARY_PERSON
    track_id: str | None = None


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal["1.0"] = SCHEMA_VERSION
    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    ttl_ms: int = Field(ge=100, le=10_000, default=1_000)
    command: CommandKind
    target: CommandTarget = Field(default_factory=CommandTarget)


class CommandReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal["1.0"] = SCHEMA_VERSION
    request_id: str
    accepted: bool
    executed: bool = False
    robot_mode: RobotMode = RobotMode.IDLE
    reason: str = "ok"
    ts_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ts_utc")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts_utc must be timezone-aware")
        return value.astimezone(timezone.utc)


# ---- Stop ----

class StopReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal["1.0"] = SCHEMA_VERSION
    request_id: str = "emergency"
    accepted: bool = True
    executed: bool = True
    robot_mode: RobotMode = RobotMode.IDLE
    reason: str = "emergency_stop"
    ts_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ts_utc")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts_utc must be timezone-aware")
        return value.astimezone(timezone.utc)


# ---- Events (WebSocket) ----

class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str | None = None
    source: str = Field(default="robot-service", min_length=1, max_length=64)
    sequence: int = Field(ge=1)
    payload: dict[str, Any]

    @field_validator("timestamp_utc")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        return value.astimezone(timezone.utc)


# ---- Error ----

class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail