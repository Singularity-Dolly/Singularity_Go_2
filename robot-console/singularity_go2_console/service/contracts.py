"""Wire contracts matching Dolly backend robot_contracts.py (v1.0)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROBOT_CONTRACT_VERSION = "1.0"

RobotCommandName = Literal[
    "scan.start",
    "follow.start",
    "follow.hold",
    "mission.stop",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RobotCommandTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["primary_person", "track_id", "none"] = "primary_person"
    track_id: int | None = Field(None, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def track_id_matches_kind(self) -> RobotCommandTarget:
        if self.kind == "track_id" and self.track_id is None:
            raise ValueError("track_id target requires a track_id")
        if self.kind != "track_id" and self.track_id is not None:
            raise ValueError("track_id is valid only for a track_id target")
        return self


class RobotCommandOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_scope: Literal["view", "room"] | None = None
    follow_mode: Literal["rotate_only"] | None = None
    max_linear_mps: float | None = Field(None, ge=0, le=0.15, allow_inf_nan=False)
    max_yaw_rps: float | None = Field(None, ge=0, le=0.35, allow_inf_nan=False)


class RobotCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal["1.0"] = ROBOT_CONTRACT_VERSION
    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    issued_at: datetime = Field(default_factory=_utc_now)
    ttl_ms: int = Field(1000, ge=100, le=10_000)
    command: RobotCommandName
    target: RobotCommandTarget = Field(default_factory=RobotCommandTarget)
    options: RobotCommandOptions = Field(default_factory=RobotCommandOptions)

    @field_validator("issued_at")
    @classmethod
    def issued_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("issued_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        moment = now or _utc_now()
        age_ms = (moment - self.issued_at).total_seconds() * 1000.0
        return age_ms > self.ttl_ms


class RobotReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal["1.0"] = ROBOT_CONTRACT_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    accepted: bool = False
    executed: bool = False
    robot_mode: str | None = Field(
        None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    reason: str = Field("unspecified", min_length=1, max_length=500)
    ts: datetime = Field(default_factory=_utc_now)

    @field_validator("ts")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("robot receipt ts must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def execution_requires_acceptance(self) -> RobotReceipt:
        if self.executed and not self.accepted:
            raise ValueError("an executed robot receipt must also be accepted")
        return self


class RobotFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    frame_id: int | None = Field(None, ge=0)
    captured_at: datetime | None = None
    received_at: datetime | None = None
    width: int | None = Field(None, ge=1, le=7680)
    height: int | None = Field(None, ge=1, le=4320)
    fps: float | None = Field(None, ge=0, le=240, allow_inf_nan=False)
    age_ms: int | None = Field(None, ge=0, le=3_600_000)
    decode_latency_ms: int | None = Field(None, ge=0, le=60_000)
    frozen: bool = False
    dropped_frames: int | None = Field(None, ge=0)
    reconnects: int | None = Field(None, ge=0)


class RobotPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_m: float | None = Field(None, ge=-100_000, le=100_000, allow_inf_nan=False)
    y_m: float | None = Field(None, ge=-100_000, le=100_000, allow_inf_nan=False)
    yaw_rad: float | None = Field(None, ge=-6.284, le=6.284, allow_inf_nan=False)
    source: Literal[
        "unavailable", "odometry", "visual_odometry", "dimos", "gateway"
    ] = "unavailable"


class RobotTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = False
    track_id: int | None = Field(None, ge=0, le=2_147_483_647)
    class_name: str | None = Field(
        None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    confidence: float | None = Field(None, ge=0, le=1, allow_inf_nan=False)
    normalized_bbox: tuple[float, float, float, float] | None = None
    center_error_x: float | None = Field(None, ge=-1, le=1, allow_inf_nan=False)
    center_error_y: float | None = Field(None, ge=-1, le=1, allow_inf_nan=False)
    bbox_area_ratio: float | None = Field(None, ge=0, le=1, allow_inf_nan=False)
    target_state: Literal[
        "unavailable",
        "acquired",
        "lost",
        "holding",
        "reacquisition_required",
    ] = "unavailable"
    target_lost_ms: int | None = Field(None, ge=0, le=3_600_000)
    processing_latency_ms: int | None = Field(None, ge=0, le=60_000)
    distance_estimate_m: float | None = Field(
        None, ge=0, le=1_000, allow_inf_nan=False
    )


class RobotScan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool = False
    scope: Literal["unavailable", "view", "room"] = "unavailable"
    coverage_pct: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    path: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)
    obstacles: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)
    map_source: Literal[
        "unavailable", "odometry", "lidar", "dimos", "gateway"
    ] = "unavailable"


class RobotSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estop: bool = False
    obstacle: bool = False
    heartbeat_ok: bool = False
    last_command_age_ms: int | None = Field(None, ge=0, le=3_600_000)
    stop_reason: str | None = Field(None, min_length=1, max_length=500)


class RobotState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal["1.0"] = ROBOT_CONTRACT_VERSION
    type: Literal["robot.state"] = "robot.state"
    source: Literal["real", "unavailable"] = "unavailable"
    robot_id: str = Field(
        "unknown",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    connected: bool = False
    mode: Literal[
        "unavailable",
        "disabled",
        "disconnected",
        "connecting",
        "idle",
        "camera_only",
        "ready",
        "ready_unarmed",
        "armed",
        "scanning",
        "following",
        "holding",
        "target_lost",
        "estop",
        "error",
    ] = "unavailable"
    battery_pct: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    frame: RobotFrame = Field(default_factory=RobotFrame)
    pose: RobotPose = Field(default_factory=RobotPose)
    target: RobotTarget = Field(default_factory=RobotTarget)
    scan: RobotScan = Field(default_factory=RobotScan)
    safety: RobotSafety = Field(default_factory=RobotSafety)
    ts: datetime = Field(default_factory=_utc_now)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "unavailable"] = "unavailable"
    available: bool = False
    robot_connected: bool = False
    connection_mode: Literal["ap", "sta"] | None = None
    reason: str | None = None
    uptime_seconds: float = 0.0
    ts: datetime = Field(default_factory=_utc_now)
