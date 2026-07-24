"""Configuration for go2ctl. Priority: defaults < .env < env vars < CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(slots=True)
class Go2CtlConfig:
    robot_ip: str | None = None
    start_mode: str = "follow"
    detector_model: str = "yolov8n.pt"
    detection_confidence: float = 0.50
    target_stable_frames: int = 3
    acquire_timeout_s: float = 15.0
    camera_stale_ms: int = 500
    manual_ttl_ms: int = 250
    max_forward_speed: float = 0.20
    max_reverse_speed: float = 0.15
    max_strafe_speed: float = 0.15
    max_angular_speed: float = 0.35
    slow_multiplier: float = 0.5
    boost_multiplier: float = 1.5
    mode_settle_ms: int = 80
    log_level: str = "INFO"
    mock: bool = False
    detect_only: bool = False
    tracker_only: bool = False
    center_weight: float = 0.55
    area_weight: float = 0.25
    confidence_weight: float = 0.20
    min_bbox_area_ratio: float = 0.005
    log_dir: Path = field(
        default_factory=lambda: Path.home() / ".local" / "state" / "go2ctl"
    )
    follow_query: str = "front person"

    @classmethod
    def from_environ(cls, dotenv_path: Path | None = None) -> Go2CtlConfig:
        if dotenv_path is not None:
            _load_dotenv(dotenv_path)
        else:
            _load_dotenv(Path.cwd() / ".env")
            _load_dotenv(Path.cwd().parent / ".env")

        return cls(
            robot_ip=os.environ.get("ROBOT_IP") or None,
            start_mode=os.environ.get("GO2CTL_START_MODE", "follow"),
            detector_model=os.environ.get("GO2CTL_DETECTOR_MODEL", "yolov8n.pt"),
            detection_confidence=_env_float("GO2CTL_DETECTION_CONFIDENCE", 0.50),
            target_stable_frames=_env_int("GO2CTL_TARGET_STABLE_FRAMES", 3),
            acquire_timeout_s=_env_float("GO2CTL_ACQUIRE_TIMEOUT_S", 15.0),
            camera_stale_ms=_env_int("GO2CTL_CAMERA_STALE_MS", 500),
            manual_ttl_ms=_env_int("GO2CTL_MANUAL_TTL_MS", 250),
            max_forward_speed=_env_float("GO2CTL_MAX_FORWARD_SPEED", 0.20),
            max_reverse_speed=_env_float("GO2CTL_MAX_REVERSE_SPEED", 0.15),
            max_strafe_speed=_env_float("GO2CTL_MAX_STRAFE_SPEED", 0.15),
            max_angular_speed=_env_float("GO2CTL_MAX_ANGULAR_SPEED", 0.35),
            log_level=os.environ.get("GO2CTL_LOG_LEVEL", "INFO"),
            mock=_env_bool("GO2CTL_MOCK", False),
        )

    def with_overrides(self, **kwargs: Any) -> Go2CtlConfig:
        cleaned = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **cleaned)

    def clamp_velocity(self, vx: float, vy: float, wz: float) -> tuple[float, float, float]:
        if vx >= 0:
            vx = max(-self.max_reverse_speed, min(self.max_forward_speed, vx))
        else:
            vx = max(-self.max_reverse_speed, min(self.max_forward_speed, vx))
        vy = max(-self.max_strafe_speed, min(self.max_strafe_speed, vy))
        wz = max(-self.max_angular_speed, min(self.max_angular_speed, wz))
        return vx, vy, wz
