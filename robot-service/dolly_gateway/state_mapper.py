"""State mapper — translates DimOS internal state into Nero contract models.

Pure data transformation layer. No side effects, no I/O, no threading.
Independently testable with synthetic DimOS state dicts.

Mapping rules:
- RobotMode is derived from the highest-priority active skill
- TargetState comes from PersonTracker output
- ScanState comes from VoxelGridMapper / SpatialMemory
- SafetyState is a snapshot from SafetyController + watchdog
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .contracts import (
    RobotMode,
    RobotStateResponse,
    SafetyState,
    ScanState,
    TargetState,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_robot_state(
    *,
    robot_id: str,
    dimos_state: dict[str, Any],
    safety_snapshot: SafetyState,
    start_time: float,
) -> RobotStateResponse:
    """Build a full RobotStateResponse from DimOS internal state + safety.

    Args:
        robot_id: Unique robot identifier (serial number or hostname).
        dimos_state: Dict of DimOS module outputs keyed by module name.
                     Expected keys:
                       - "go2_connection": connection status, robot mode
                       - "person_tracker": target tracking state
                       - "voxel_mapper": scan/map state
        safety_snapshot: SafetyController snapshot.
        start_time: monotonic timestamp of service start (for uptime).

    Returns:
        RobotStateResponse ready for serialization.
    """
    connection = dimos_state.get("go2_connection", {})
    tracker = dimos_state.get("person_tracker", {})
    mapper = dimos_state.get("voxel_mapper", {})

    mode = _derive_mode(connection, tracker)
    target = _derive_target(tracker)
    scan = _derive_scan(mapper)

    return RobotStateResponse(
        robot_id=robot_id,
        connected=connection.get("connected", False),
        mode=mode,
        target=target,
        scan=scan,
        safety=safety_snapshot,
        ts_utc=datetime.now(timezone.utc),
    )


def build_robot_state_minimal(
    robot_id: str = "unknown",
    connected: bool = False,
    safety_snapshot: SafetyState | None = None,
) -> RobotStateResponse:
    """Build a minimal RobotStateResponse when no DimOS state is available.

    Used during startup, shutdown, or when the robot is disconnected.
    """
    return RobotStateResponse(
        robot_id=robot_id,
        connected=connected,
        mode=RobotMode.IDLE,
        target=TargetState(),
        scan=ScanState(),
        safety=safety_snapshot or SafetyState(),
        ts_utc=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Mode derivation
# ---------------------------------------------------------------------------

# Priority order: higher index = higher priority (wins when multiple active)
_MODE_PRIORITY: dict[RobotMode, int] = {
    RobotMode.IDLE: 0,
    RobotMode.EXPLORING: 1,
    RobotMode.NAVIGATING: 2,
    RobotMode.FOLLOWING: 3,
}

# Mapping from DimOS skill/state keys to RobotMode
_MODE_SIGNALS: dict[str, RobotMode] = {
    "following_active": RobotMode.FOLLOWING,
    "navigating_active": RobotMode.NAVIGATING,
    "exploring_active": RobotMode.EXPLORING,
}


def _derive_mode(connection: dict[str, Any], tracker: dict[str, Any]) -> RobotMode:
    """Derive RobotMode from DimOS module states.

    Priorities: FOLLOWING > NAVIGATING > EXPLORING > IDLE
    """
    if not connection.get("connected", False):
        return RobotMode.IDLE

    best_mode = RobotMode.IDLE
    best_priority = 0

    # Check connection-level signals
    for signal_key, mode in _MODE_SIGNALS.items():
        if connection.get(signal_key, False):
            priority = _MODE_PRIORITY.get(mode, 0)
            if priority > best_priority:
                best_mode = mode
                best_priority = priority

    # Check tracker-level signals
    if tracker.get("following", False):
        priority = _MODE_PRIORITY[RobotMode.FOLLOWING]
        if priority > best_priority:
            best_mode = RobotMode.FOLLOWING

    return best_mode


# ---------------------------------------------------------------------------
# Target derivation
# ---------------------------------------------------------------------------

def _derive_target(tracker: dict[str, Any]) -> TargetState:
    """Derive TargetState from PersonTracker output."""
    locked = tracker.get("target_locked", False)
    track_id = tracker.get("track_id")
    confidence = tracker.get("confidence")

    # Validate confidence range
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        confidence = None

    return TargetState(
        locked=bool(locked),
        track_id=str(track_id) if track_id is not None else None,
        confidence=float(confidence) if confidence is not None else None,
    )


# ---------------------------------------------------------------------------
# Scan derivation
# ---------------------------------------------------------------------------

def _derive_scan(mapper: dict[str, Any]) -> ScanState:
    """Derive ScanState from VoxelGridMapper / SpatialMemory output."""
    active = mapper.get("scan_active", False)
    path = mapper.get("scan_path", [])
    map_source = mapper.get("map_source", "unavailable")

    # Validate map_source
    valid_sources = {"voxel", "coverage", "unavailable"}
    if map_source not in valid_sources:
        map_source = "unavailable"

    # Validate path entries
    validated_path: list[dict[str, float]] = []
    for point in path:
        if isinstance(point, dict) and all(k in point for k in ("x", "y")):
            validated_path.append({
                "x": float(point["x"]),
                "y": float(point["y"]),
            })

    return ScanState(
        active=bool(active),
        path=validated_path,
        map_source=map_source,
    )