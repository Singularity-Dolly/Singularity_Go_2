"""Map Go2Controller status into Dolly RobotState contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from singularity_go2_console.service.contracts import (
    RobotFrame,
    RobotPose,
    RobotSafety,
    RobotScan,
    RobotState,
    RobotTarget,
)
from singularity_go2_console.state import ControllerMode, RobotStatus


def _mode_from_controller(status: RobotStatus) -> str:
    mode = (status.mode or "").upper()
    if status.estop or mode == ControllerMode.ESTOP.value:
        return "estop"
    if mode == ControllerMode.CONNECTING.value:
        return "connecting"
    if mode == ControllerMode.DISCONNECTED.value:
        return "disconnected"
    if mode == ControllerMode.FOLLOWING.value:
        return "following"
    if mode == ControllerMode.ACQUIRING_TARGET.value:
        return "scanning"
    if mode == ControllerMode.ERROR.value:
        return "error"
    if mode == ControllerMode.MANUAL.value:
        return "armed" if status.connected else "disconnected"
    if mode == ControllerMode.IDLE.value:
        return "ready" if status.connected else "disconnected"
    return "idle" if status.connected else "disconnected"


def map_status_to_state(
    status: RobotStatus,
    *,
    robot_id: str,
    frame_stale_ms: int = 1000,
    scan_active: bool = False,
    holding: bool = False,
    last_command_age_ms: int | None = None,
    obstacle_reported: bool = False,
) -> RobotState:
    age = status.last_frame_age_ms
    age_i = int(age) if age is not None else None
    frame_available = bool(
        status.connected
        and status.camera_ready
        and age_i is not None
        and age_i <= frame_stale_ms
        and status.camera_watchdog_ok
    )
    frozen = bool(
        status.camera_ready
        and age_i is not None
        and age_i > frame_stale_ms
    )

    target = RobotTarget()
    if status.target_visible and status.target_confidence is not None:
        bbox = status.target_bbox
        normalized = None
        if bbox and len(bbox) == 4:
            # Best-effort; adapter may already provide normalized values.
            x1, y1, x2, y2 = (float(v) for v in bbox)
            if max(x1, y1, x2, y2) <= 1.0:
                normalized = (x1, y1, x2, y2)
        target = RobotTarget(
            locked=True,
            track_id=0,
            class_name="person",
            confidence=float(status.target_confidence),
            normalized_bbox=normalized,
            target_state="holding" if holding else "acquired",
        )
    elif status.target_lost_frames > 0:
        target = RobotTarget(
            locked=False,
            target_state="lost",
            target_lost_ms=None,
        )

    mode = _mode_from_controller(status)
    if holding and status.connected and not status.estop:
        mode = "holding"
    if scan_active and status.connected and not status.estop:
        mode = "scanning"

    heartbeat_ok = bool(
        status.connected
        and status.connection_watchdog_ok
        and status.camera_watchdog_ok
    )

    return RobotState(
        source="real" if status.connected else "unavailable",
        robot_id=robot_id,
        connected=bool(status.connected),
        mode=mode,  # type: ignore[arg-type]
        battery_pct=None,  # never fabricate battery
        frame=RobotFrame(
            available=frame_available,
            frame_id=None if not frame_available else 1,
            age_ms=age_i if frame_available or frozen else None,
            frozen=frozen,
            width=None,
            height=None,
        ),
        pose=RobotPose(source="unavailable"),
        target=target,
        scan=RobotScan(
            active=scan_active,
            scope="view" if scan_active else "unavailable",
            coverage_pct=None,
            path=[],
            obstacles=[],
            map_source="unavailable",
        ),
        safety=RobotSafety(
            estop=bool(status.estop),
            obstacle=bool(obstacle_reported),
            heartbeat_ok=heartbeat_ok,
            last_command_age_ms=last_command_age_ms,
            stop_reason=status.last_error if status.estop else None,
        ),
        ts=datetime.now(timezone.utc),
    )


def enrich_frame_meta(
    state: RobotState,
    *,
    frame_id: int | None,
    width: int | None,
    height: int | None,
    age_ms: int | None,
    frozen: bool,
) -> RobotState:
    frame = state.frame.model_copy(
        update={
            "available": bool(frame_id is not None and not frozen and age_ms is not None),
            "frame_id": frame_id,
            "width": width,
            "height": height,
            "age_ms": age_ms,
            "frozen": frozen,
            "received_at": datetime.now(timezone.utc) if frame_id is not None else None,
        }
    )
    return state.model_copy(update={"frame": frame})
