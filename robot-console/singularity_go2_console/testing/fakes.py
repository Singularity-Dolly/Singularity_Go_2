"""Fake adapters for offline unit tests (no real robot)."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

from singularity_go2_console.adapter_protocol import FollowInit
from singularity_go2_console.front_person import CameraFrame, PersonDetection


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class FakeVelocitySink:
    def __init__(self) -> None:
        self.commands: list[tuple[float, float, float]] = []
        self.sport_requests: list[dict[str, Any]] = []
        self.fail = False

    def publish_velocity(self, vx: float, vy: float, wz: float) -> bool:
        if self.fail:
            return False
        self.commands.append((vx, vy, wz))
        self.sport_requests.append(
            {"api": "Move", "api_id": 1008, "x": float(vx), "y": float(vy), "z": float(wz)}
        )
        return True

    def publish_zero(self) -> bool:
        if self.fail:
            return False
        self.commands.append((0.0, 0.0, 0.0))
        self.sport_requests.append({"api": "StopMove", "api_id": 1003})
        return True

    @property
    def last(self) -> tuple[float, float, float] | None:
        return self.commands[-1] if self.commands else None

    def non_zero_active(self) -> bool:
        if not self.commands:
            return False
        vx, vy, wz = self.commands[-1]
        return abs(vx) > 1e-9 or abs(vy) > 1e-9 or abs(wz) > 1e-9


class FakeCamera:
    def __init__(self, width: int = 640, height: int = 480, clock: FakeClock | None = None) -> None:
        self.width = width
        self.height = height
        self.clock = clock or FakeClock()
        self.frame_id = 0
        self._frame: CameraFrame | None = None

    def push(self, image: Any | None = None) -> CameraFrame:
        self.frame_id += 1
        if image is None:
            image = [[[0, 0, 0] for _ in range(self.width)] for _ in range(self.height)]
        frame = CameraFrame(
            image=image,
            timestamp_s=self.clock(),
            frame_id=self.frame_id,
            width=self.width,
            height=self.height,
        )
        self._frame = frame
        return frame

    def latest(self) -> CameraFrame | None:
        return self._frame


class FakePersonDetector:
    def __init__(self) -> None:
        self._ready = True
        self._detections: list[PersonDetection] = []
        self.fail_ready = False

    @property
    def ready(self) -> bool:
        return self._ready and not self.fail_ready

    def set_detections(self, detections: list[PersonDetection]) -> None:
        self._detections = list(detections)

    def detect_persons(self, frame: CameraFrame) -> list[PersonDetection]:
        out: list[PersonDetection] = []
        for d in self._detections:
            out.append(
                PersonDetection(
                    bbox=d.bbox,
                    confidence=d.confidence,
                    class_name=d.class_name,
                    frame_id=frame.frame_id,
                )
            )
        return out


class FakeFollowSkill:
    def __init__(self) -> None:
        self.started = False
        self.fail_init = False
        self.target_visible = True
        self.last_init: FollowInit | None = None
        self.stop_count = 0

    def start(self, init: FollowInit) -> tuple[bool, str, str]:
        if self.fail_init:
            return False, "TRACKER_INIT_FAILED", "EdgeTAM failed to segment"
        # Enforce same-frame pairing
        if init.frame is None or not init.bbox:
            return False, "TRACKER_INIT_FAILED", "missing bbox/frame"
        self.started = True
        self.last_init = init
        self.target_visible = True
        return True, "OK", "follow started"

    def stop(self) -> tuple[bool, str, str]:
        self.started = False
        self.stop_count += 1
        return True, "OK", "follow stopped"


@dataclass
class FakeGo2Adapter:
    camera: FakeCamera | None = None
    detector: FakePersonDetector = field(default_factory=FakePersonDetector)
    follow: FakeFollowSkill = field(default_factory=FakeFollowSkill)
    sink: FakeVelocitySink = field(default_factory=FakeVelocitySink)
    clock: FakeClock = field(default_factory=FakeClock)
    fail_connect: bool = False
    drop_connection: bool = False
    motion_mode: str = "normal"
    allow_normal_mode_switch: bool = False
    mode_switch_calls: int = 0
    last_velocity_error_code: str | None = None
    last_velocity_error_message: str = ""
    _connected: bool = False
    _robot_ip: str | None = None

    def __post_init__(self) -> None:
        if self.camera is None:
            self.camera = FakeCamera(clock=self.clock)
        else:
            self.camera.clock = self.clock

    @property
    def mock(self) -> bool:
        return True

    @property
    def connected(self) -> bool:
        return self._connected and not self.drop_connection

    @property
    def camera_ready(self) -> bool:
        return self.connected and self.camera.latest() is not None

    @property
    def velocity_ready(self) -> bool:
        return self.connected and not self.sink.fail

    @property
    def detector_ready(self) -> bool:
        return self.detector.ready

    @property
    def follow_ready(self) -> bool:
        return self.connected

    @property
    def sport_requests(self) -> list[dict[str, Any]]:
        return self.sink.sport_requests

    async def connect(self, robot_ip: str | None = None) -> tuple[bool, str, str]:
        if self.fail_connect:
            return False, "WEBRTC_CONNECTION_FAILED", "fake connect failed"
        self._robot_ip = robot_ip or "127.0.0.1"
        self._connected = True
        self.camera.push()
        return True, "OK", f"connected to {robot_ip}"

    async def disconnect(self) -> tuple[bool, str, str]:
        self._connected = False
        self.follow.started = False
        return True, "OK", "disconnected"

    def encode_frame_jpeg_bytes(self, frame: CameraFrame) -> bytes | None:
        return b"\xff\xd8\xff\xd9"

    def get_latest_frame(self) -> CameraFrame | None:
        if not self.connected:
            return None
        return self.camera.latest()

    def detect_persons(self, frame: CameraFrame) -> list[PersonDetection]:
        return list(self.detector.detect_persons(frame))

    def publish_velocity(self, vx: float, vy: float, wz: float) -> bool:
        if not self.connected:
            self.last_velocity_error_code = "VELOCITY_OUTPUT_NOT_READY"
            self.last_velocity_error_message = "not connected"
            return False
        if self.motion_mode != "normal":
            self.sink.sport_requests.append({"api": "StopMove", "api_id": 1003})
            self.last_velocity_error_code = "MOTION_MODE_NOT_NORMAL"
            self.last_velocity_error_message = (
                f"motion mode is {self.motion_mode!r}; require 'normal'"
            )
            return False
        self.last_velocity_error_code = None
        self.last_velocity_error_message = ""
        return self.sink.publish_velocity(vx, vy, wz)

    def publish_zero(self) -> bool:
        # Still attempt record for tests of shutdown paths
        self.last_velocity_error_code = None
        self.last_velocity_error_message = ""
        return self.sink.publish_zero()

    def ensure_normal_mode(self) -> tuple[bool, str, str]:
        self.mode_switch_calls += 1
        if not self.allow_normal_mode_switch:
            return (
                False,
                "MOTION_MODE_SWITCH_DISABLED",
                "Pass --allow-normal-mode-switch to enable operator mode switch",
            )
        self.motion_mode = "normal"
        return True, "OK", "motion mode is normal"

    def get_motion_mode(self) -> tuple[str | None, str, str]:
        return self.motion_mode, "OK", f"motion mode={self.motion_mode}"

    def start_follow(self, init: FollowInit) -> tuple[bool, str, str]:
        if not self.connected:
            return False, "CONNECTION_LOST", "not connected"
        return self.follow.start(init)

    def stop_follow(self) -> tuple[bool, str, str]:
        return self.follow.stop()

    def is_following(self) -> bool:
        return self.follow.started

    def follow_target_visible(self) -> bool:
        return self.follow.target_visible

    def robot_state(self) -> dict[str, Any]:
        return {
            "robot_ip": self._robot_ip,
            "connected": self.connected,
            "mock": True,
            "motion_mode": self.motion_mode,
        }

    @staticmethod
    def encode_frame_jpeg_b64(frame: CameraFrame) -> str:
        # Deterministic fake payload for pairing tests
        payload = f"frame:{frame.frame_id}:{frame.timestamp_s}".encode()
        return base64.b64encode(payload).decode("ascii")
