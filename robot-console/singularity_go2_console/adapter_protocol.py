"""Robot adapter protocol — DimOS-specific details stay behind this boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from singularity_go2_console.front_person import CameraFrame, PersonDetection


@dataclass(slots=True)
class FollowInit:
    query: str
    bbox: list[float]
    frame: CameraFrame
    jpeg_base64: str


@runtime_checkable
class Go2Adapter(Protocol):
    """Physical robot I/O. Real and fake adapters must implement this."""

    @property
    def mock(self) -> bool: ...

    @property
    def connected(self) -> bool: ...

    @property
    def camera_ready(self) -> bool: ...

    @property
    def velocity_ready(self) -> bool: ...

    @property
    def detector_ready(self) -> bool: ...

    @property
    def follow_ready(self) -> bool: ...

    async def connect(self, robot_ip: str) -> tuple[bool, str, str]:
        """Return (ok, code, message)."""
        ...

    async def disconnect(self) -> tuple[bool, str, str]: ...

    def get_latest_frame(self) -> CameraFrame | None: ...

    def detect_persons(self, frame: CameraFrame) -> list[PersonDetection]: ...

    def publish_velocity(self, vx: float, vy: float, wz: float) -> bool: ...

    def publish_zero(self) -> bool: ...

    def start_follow(self, init: FollowInit) -> tuple[bool, str, str]:
        """Start DimOS PersonFollow (or equivalent) with paired bbox+image."""
        ...

    def stop_follow(self) -> tuple[bool, str, str]: ...

    def is_following(self) -> bool: ...

    def follow_target_visible(self) -> bool: ...

    def robot_state(self) -> dict[str, Any]: ...
