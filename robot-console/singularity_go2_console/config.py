"""Configuration for go2ctl. Priority: defaults < .env < env vars < CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from singularity_go2_console.aes import AesKeyError, AesKeyMaterial, load_aes_key

ConnectionMode = Literal["ap", "sta"]

# Hard safety caps for AdventureX physical demo.
MAX_LINEAR_MPS = 0.15
MAX_YAW_RPS = 0.35
DEFAULT_AP_IP = "192.168.12.1"


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


def _parse_connection_mode(
    raw: str | None, default: ConnectionMode = "ap"
) -> ConnectionMode:
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value not in {"ap", "sta"}:
        raise ValueError("connection mode must be ap or sta")
    return value  # type: ignore[return-value]


@dataclass(slots=True)
class Go2CtlConfig:
    robot_ip: str | None = None
    connection_mode: ConnectionMode = "ap"
    aes_key_file: str | None = None
    aes_key: AesKeyMaterial | None = None
    start_mode: str = "follow"
    detector_model: str = "yolov8n.pt"
    detection_confidence: float = 0.50
    target_stable_frames: int = 3
    acquire_timeout_s: float = 15.0
    camera_stale_ms: int = 500
    manual_ttl_ms: int = 250
    max_forward_speed: float = MAX_LINEAR_MPS
    max_reverse_speed: float = MAX_LINEAR_MPS
    max_strafe_speed: float = MAX_LINEAR_MPS
    max_angular_speed: float = MAX_YAW_RPS
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
    robot_id: str = "go2_62507"
    motion_enabled: bool = False

    @classmethod
    def from_environ(
        cls,
        dotenv_path: Path | None = None,
        *,
        load_aes: bool = True,
        require_aes: bool = False,
    ) -> Go2CtlConfig:
        if dotenv_path is not None:
            _load_dotenv(dotenv_path)
        else:
            _load_dotenv(Path.cwd() / ".env")
            _load_dotenv(Path.cwd().parent / ".env")

        mode = _parse_connection_mode(
            os.environ.get("GO2CTL_CONNECTION_MODE")
            or os.environ.get("CONNECTION_MODE")
        )
        default_ip = DEFAULT_AP_IP if mode == "ap" else None
        robot_ip = os.environ.get("ROBOT_IP") or default_ip
        aes_key_file = os.environ.get("GO2CTL_AES_KEY_FILE") or None
        aes: AesKeyMaterial | None = None
        if load_aes:
            try:
                aes = load_aes_key(key_file=aes_key_file)
            except AesKeyError:
                if require_aes:
                    raise
                aes = None

        forward = min(
            MAX_LINEAR_MPS, _env_float("GO2CTL_MAX_FORWARD_SPEED", MAX_LINEAR_MPS)
        )
        reverse = min(
            MAX_LINEAR_MPS, _env_float("GO2CTL_MAX_REVERSE_SPEED", MAX_LINEAR_MPS)
        )
        strafe = min(
            MAX_LINEAR_MPS, _env_float("GO2CTL_MAX_STRAFE_SPEED", MAX_LINEAR_MPS)
        )
        yaw = min(MAX_YAW_RPS, _env_float("GO2CTL_MAX_ANGULAR_SPEED", MAX_YAW_RPS))

        return cls(
            robot_ip=robot_ip,
            connection_mode=mode,
            aes_key_file=aes_key_file,
            aes_key=aes,
            start_mode=os.environ.get("GO2CTL_START_MODE", "follow"),
            detector_model=os.environ.get("GO2CTL_DETECTOR_MODEL", "yolov8n.pt"),
            detection_confidence=_env_float("GO2CTL_DETECTION_CONFIDENCE", 0.50),
            target_stable_frames=_env_int("GO2CTL_TARGET_STABLE_FRAMES", 3),
            acquire_timeout_s=_env_float("GO2CTL_ACQUIRE_TIMEOUT_S", 15.0),
            camera_stale_ms=_env_int("GO2CTL_CAMERA_STALE_MS", 500),
            manual_ttl_ms=_env_int("GO2CTL_MANUAL_TTL_MS", 250),
            max_forward_speed=forward,
            max_reverse_speed=reverse,
            max_strafe_speed=strafe,
            max_angular_speed=yaw,
            log_level=os.environ.get("GO2CTL_LOG_LEVEL", "INFO"),
            mock=_env_bool("GO2CTL_MOCK", False),
            robot_id=os.environ.get("ROBOT_ID")
            or os.environ.get("GO2CTL_ROBOT_ID")
            or "go2_62507",
            motion_enabled=_env_bool("GO2CTL_MOTION_ENABLED", False),
        )

    def with_overrides(self, **kwargs: Any) -> Go2CtlConfig:
        cleaned = {k: v for k, v in kwargs.items() if v is not None}
        if "connection_mode" in cleaned:
            cleaned["connection_mode"] = _parse_connection_mode(
                str(cleaned["connection_mode"])
            )
        for key in ("max_forward_speed", "max_reverse_speed", "max_strafe_speed"):
            if key in cleaned:
                cleaned[key] = min(MAX_LINEAR_MPS, float(cleaned[key]))
        if "max_angular_speed" in cleaned:
            cleaned["max_angular_speed"] = min(
                MAX_YAW_RPS, float(cleaned["max_angular_speed"])
            )
        return replace(self, **cleaned)

    def resolve_aes(self, *, require: bool = True) -> AesKeyMaterial | None:
        if self.aes_key is not None:
            return self.aes_key
        try:
            material = load_aes_key(key_file=self.aes_key_file)
        except AesKeyError:
            if require:
                raise
            return None
        object.__setattr__(self, "aes_key", material)
        return material

    def clamp_velocity(
        self, vx: float, vy: float, wz: float
    ) -> tuple[float, float, float]:
        vx = max(-self.max_reverse_speed, min(self.max_forward_speed, vx))
        vy = max(-self.max_strafe_speed, min(self.max_strafe_speed, vy))
        wz = max(-self.max_angular_speed, min(self.max_angular_speed, wz))
        return vx, vy, wz
