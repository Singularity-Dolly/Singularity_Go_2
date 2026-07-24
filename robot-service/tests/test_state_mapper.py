"""Tests for state mapper — pure data transformation, no hardware.

Covers:
- build_robot_state with full DimOS state
- build_robot_state_minimal for disconnected/startup state
- RobotMode derivation priorities
- TargetState mapping from PersonTracker
- ScanState mapping from VoxelGridMapper
- Edge cases: missing keys, invalid values, disconnected robot
"""

from __future__ import annotations

import pytest

from dolly_gateway.contracts import RobotMode, SafetyState, ScanState, TargetState
from dolly_gateway.state_mapper import build_robot_state, build_robot_state_minimal


# ---------------------------------------------------------------------------
# Minimal state
# ---------------------------------------------------------------------------

class TestMinimalState:
    def test_default_values(self) -> None:
        """Minimal state has safe defaults."""
        state = build_robot_state_minimal()

        assert state.robot_id == "unknown"
        assert state.connected is False
        assert state.mode == RobotMode.IDLE
        assert state.target.locked is False
        assert state.target.track_id is None
        assert state.scan.active is False
        assert state.scan.path == []

    def test_custom_robot_id(self) -> None:
        """Custom robot_id is preserved."""
        state = build_robot_state_minimal(robot_id="go2-unit-001")
        assert state.robot_id == "go2-unit-001"

    def test_connected_propagates(self) -> None:
        """Connected flag propagates."""
        state = build_robot_state_minimal(connected=True)
        assert state.connected is True


# ---------------------------------------------------------------------------
# Full state
# ---------------------------------------------------------------------------

class TestFullState:
    def _dimos_state(self, **overrides: object) -> dict:
        """Build a synthetic DimOS state dict."""
        base: dict = {
            "go2_connection": {
                "connected": True,
                "following_active": False,
                "navigating_active": False,
                "exploring_active": False,
            },
            "person_tracker": {
                "target_locked": False,
                "track_id": None,
                "confidence": None,
                "following": False,
            },
            "voxel_mapper": {
                "scan_active": False,
                "scan_path": [],
                "map_source": "unavailable",
            },
        }
        # Deep merge overrides
        for section, values in overrides.items():
            if isinstance(values, dict):
                base.setdefault(section, {}).update(values)
        return base

    def _safety(self) -> SafetyState:
        return SafetyState()

    def test_idle_mode_when_no_skills_active(self) -> None:
        """No active skills → IDLE mode."""
        state = build_robot_state(
            robot_id="test",
            dimos_state=self._dimos_state(),
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.IDLE

    def test_disconnected_robot_returns_idle(self) -> None:
        """Disconnected robot always returns IDLE regardless of skills."""
        dimos = self._dimos_state()
        dimos["go2_connection"]["connected"] = False
        dimos["go2_connection"]["following_active"] = True  # Should be ignored

        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.IDLE

    def test_following_mode(self) -> None:
        """Following active → FOLLOWING mode."""
        dimos = self._dimos_state(
            go2_connection={"following_active": True},
        )
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.FOLLOWING

    def test_tracker_following_signal(self) -> None:
        """PersonTracker following=True → FOLLOWING mode."""
        dimos = self._dimos_state(
            person_tracker={"following": True},
        )
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.FOLLOWING

    def test_navigating_mode(self) -> None:
        """Navigating active → NAVIGATING mode."""
        dimos = self._dimos_state(
            go2_connection={"navigating_active": True},
        )
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.NAVIGATING

    def test_exploring_mode(self) -> None:
        """Exploring active → EXPLORING mode."""
        dimos = self._dimos_state(
            go2_connection={"exploring_active": True},
        )
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.EXPLORING

    def test_mode_priority_following_wins(self) -> None:
        """FOLLOWING has highest priority."""
        dimos = self._dimos_state(
            go2_connection={
                "following_active": True,
                "navigating_active": True,
                "exploring_active": True,
            },
        )
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.FOLLOWING

    def test_mode_priority_navigating_over_exploring(self) -> None:
        """NAVIGATING > EXPLORING."""
        dimos = self._dimos_state(
            go2_connection={
                "navigating_active": True,
                "exploring_active": True,
            },
        )
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.mode == RobotMode.NAVIGATING


# ---------------------------------------------------------------------------
# Target state mapping
# ---------------------------------------------------------------------------

class TestTargetMapping:
    def _dimos_state(self, tracker: dict | None = None) -> dict:
        base: dict = {
            "go2_connection": {"connected": True},
            "person_tracker": tracker or {},
            "voxel_mapper": {},
        }
        return base

    def _safety(self) -> SafetyState:
        return SafetyState()

    def test_target_locked(self) -> None:
        dimos = self._dimos_state(tracker={
            "target_locked": True,
            "track_id": "person-42",
            "confidence": 0.95,
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.target.locked is True
        assert state.target.track_id == "person-42"
        assert state.target.confidence == 0.95

    def test_target_not_locked(self) -> None:
        dimos = self._dimos_state(tracker={
            "target_locked": False,
            "track_id": None,
            "confidence": None,
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.target.locked is False
        assert state.target.track_id is None
        assert state.target.confidence is None

    def test_confidence_out_of_range_clamped(self) -> None:
        """Confidence outside [0,1] is set to None."""
        dimos = self._dimos_state(tracker={
            "target_locked": True,
            "track_id": "person-1",
            "confidence": 1.5,  # Invalid
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.target.confidence is None

    def test_track_id_string_conversion(self) -> None:
        """Numeric track_id is converted to string."""
        dimos = self._dimos_state(tracker={
            "target_locked": True,
            "track_id": 42,
            "confidence": 0.8,
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.target.track_id == "42"
        assert isinstance(state.target.track_id, str)


# ---------------------------------------------------------------------------
# Scan state mapping
# ---------------------------------------------------------------------------

class TestScanMapping:
    def _dimos_state(self, mapper: dict | None = None) -> dict:
        base: dict = {
            "go2_connection": {"connected": True},
            "person_tracker": {},
            "voxel_mapper": mapper or {},
        }
        return base

    def _safety(self) -> SafetyState:
        return SafetyState()

    def test_scan_active(self) -> None:
        dimos = self._dimos_state(mapper={
            "scan_active": True,
            "scan_path": [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}],
            "map_source": "voxel",
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.scan.active is True
        assert state.scan.map_source == "voxel"
        assert len(state.scan.path) == 2
        assert state.scan.path[0] == {"x": 1.0, "y": 2.0}

    def test_scan_inactive(self) -> None:
        dimos = self._dimos_state(mapper={
            "scan_active": False,
            "scan_path": [],
            "map_source": "unavailable",
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.scan.active is False
        assert state.scan.path == []

    def test_invalid_map_source_defaults(self) -> None:
        """Invalid map_source is coerced to 'unavailable'."""
        dimos = self._dimos_state(mapper={
            "scan_active": True,
            "map_source": "invalid_source",
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.scan.map_source == "unavailable"

    def test_malformed_path_entries_filtered(self) -> None:
        """Path entries without x,y keys are filtered out."""
        dimos = self._dimos_state(mapper={
            "scan_active": True,
            "scan_path": [
                {"x": 1.0, "y": 2.0},
                {"z": 3.0},  # Malformed — missing x,y
                {"x": 5.0, "y": 6.0},
            ],
        })
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert len(state.scan.path) == 2
        assert state.scan.path[0] == {"x": 1.0, "y": 2.0}
        assert state.scan.path[1] == {"x": 5.0, "y": 6.0}


# ---------------------------------------------------------------------------
# Safety propagation
# ---------------------------------------------------------------------------

class TestSafetyPropagation:
    def test_safety_snapshot_preserved(self) -> None:
        """Safety snapshot fields are preserved in robot state."""
        safety = SafetyState(
            estop=True,
            obstacle=True,
            heartbeat_ok=False,
            last_command_age_ms=1500.0,
        )
        dimos = {
            "go2_connection": {"connected": True},
            "person_tracker": {},
            "voxel_mapper": {},
        }
        state = build_robot_state(
            robot_id="test",
            dimos_state=dimos,
            safety_snapshot=safety,
            start_time=0.0,
        )
        assert state.safety.estop is True
        assert state.safety.obstacle is True
        assert state.safety.heartbeat_ok is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def _safety(self) -> SafetyState:
        return SafetyState()

    def test_empty_dimos_state(self) -> None:
        """Empty dict → safe defaults."""
        state = build_robot_state(
            robot_id="test",
            dimos_state={},
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.connected is False
        assert state.mode == RobotMode.IDLE

    def test_missing_sections(self) -> None:
        """Missing DimOS sections → safe defaults."""
        state = build_robot_state(
            robot_id="test",
            dimos_state={"go2_connection": {"connected": True}},
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.connected is True
        assert state.mode == RobotMode.IDLE
        assert state.target.locked is False

    def test_robot_id_propagated(self) -> None:
        """robot_id is preserved through mapping."""
        state = build_robot_state(
            robot_id="go2-serial-abc123",
            dimos_state={},
            safety_snapshot=self._safety(),
            start_time=0.0,
        )
        assert state.robot_id == "go2-serial-abc123"