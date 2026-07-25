"""Tests for safety layer — pure Python, no hardware required.

Covers:
- CommandGuard TTL validation
- HeartbeatWatchdog timeout behavior
- FrameGuard stale frame detection
- SafetyController estop state machine
- Multi-layer integration (estop blocks commands)
"""

from __future__ import annotations

import time

import pytest

from dolly_gateway.contracts import CommandKind, CommandRequest, RejectionReason
from dolly_gateway.safety import (
    CommandGuard,
    FrameGuard,
    HeartbeatWatchdog,
    SafetyController,
    attach_timestamp,
    get_timestamp,
)


# ---------------------------------------------------------------------------
# CommandGuard
# ---------------------------------------------------------------------------

class TestCommandGuard:
    """TTL validation tests."""

    def test_valid_within_ttl(self) -> None:
        """Command within TTL is accepted."""
        guard = CommandGuard()
        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=1000,
            command=CommandKind.SCAN_START,
        )
        attach_timestamp(cmd)

        result = guard.validate(cmd)
        assert result.valid is True
        assert result.age_ms >= 0.0

    def test_expired_ttl(self) -> None:
        """Command past TTL is rejected."""
        guard = CommandGuard()
        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=100,  # Minimum allowed TTL
            command=CommandKind.SCAN_START,
        )
        attach_timestamp(cmd)

        # Wait for TTL to expire (150ms ensures Windows timer resolution)
        time.sleep(0.15)

        result = guard.validate(cmd)
        assert result.valid is False
        assert result.reason == RejectionReason.TTL_EXPIRED.value

    def test_ttl_boundary(self) -> None:
        """Command at exactly TTL boundary."""
        guard = CommandGuard()
        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,  # 5s TTL
            command=CommandKind.SCAN_START,
        )
        attach_timestamp(cmd)

        result = guard.validate(cmd)
        assert result.valid is True


# ---------------------------------------------------------------------------
# HeartbeatWatchdog
# ---------------------------------------------------------------------------

class TestHeartbeatWatchdog:
    """Heartbeat timeout tests."""

    def test_initial_state(self) -> None:
        """Watchdog starts with estop not triggered."""
        wd = HeartbeatWatchdog()
        assert wd.estop_triggered is False

    def test_heartbeat_keeps_alive(self) -> None:
        """Regular heartbeats prevent timeout."""
        triggered = []

        wd = HeartbeatWatchdog(estop_callback=lambda: triggered.append(True))
        wd.start()

        # Send heartbeats for 1 second
        for _ in range(10):
            wd.heartbeat()
            time.sleep(0.1)

        wd.stop()
        assert not triggered, "estop should not trigger with regular heartbeats"

    def test_timeout_triggers_estop(self) -> None:
        """Missing heartbeat triggers estop after timeout."""
        triggered = []

        wd = HeartbeatWatchdog(estop_callback=lambda: triggered.append(True))
        # Override timeout for fast test
        wd.HEARTBEAT_TIMEOUT_MS = 200.0
        wd.CHECK_INTERVAL_S = 0.05
        wd.start()

        # Don't send any heartbeat — wait for timeout
        time.sleep(0.5)

        wd.stop()
        assert triggered, "estop should trigger on heartbeat timeout"

    def test_heartbeat_age_increases(self) -> None:
        """Heartbeat age increases without updates."""
        wd = HeartbeatWatchdog()
        wd.heartbeat()

        age1 = wd.last_heartbeat_age_ms
        time.sleep(0.05)
        age2 = wd.last_heartbeat_age_ms

        assert age2 > age1


# ---------------------------------------------------------------------------
# FrameGuard
# ---------------------------------------------------------------------------

class TestFrameGuard:
    """Stale frame detection tests."""

    def test_initial_no_frame(self) -> None:
        """No frame initially — returns None."""
        guard = FrameGuard()
        assert guard.get_latest_frame() is None
        assert guard.has_fresh_frame is False
        assert guard.frame_age_ms is None

    def test_fresh_frame_available(self) -> None:
        """Fresh frame is returned."""
        guard = FrameGuard()
        guard.update(b"fake-jpeg-data")

        frame = guard.get_latest_frame()
        assert frame == b"fake-jpeg-data"
        assert guard.has_fresh_frame is True
        assert guard.frame_age_ms is not None

    def test_stale_frame_returns_none(self) -> None:
        """Stale frame returns None."""
        guard = FrameGuard()
        guard.MAX_FRAME_AGE_MS = 50.0  # 50ms timeout
        guard.update(b"fake-jpeg-data")

        time.sleep(0.1)  # Wait for staleness

        assert guard.get_latest_frame() is None
        assert guard.has_fresh_frame is False

    def test_empty_bytes_ignored(self) -> None:
        """Empty bytes are not stored."""
        guard = FrameGuard()
        guard.update(b"")

        assert guard.get_latest_frame() is None


# ---------------------------------------------------------------------------
# SafetyController
# ---------------------------------------------------------------------------

class TestSafetyController:
    """Emergency stop state machine tests."""

    def test_initial_state_accepts_commands(self) -> None:
        """Initial state allows commands."""
        sc = SafetyController()
        can_accept, reason = sc.can_accept_command()
        assert can_accept is True
        assert reason == "ok"

    def test_estop_blocks_commands(self) -> None:
        """After estop, all commands are rejected."""
        sc = SafetyController()
        sc.trigger_estop()

        can_accept, reason = sc.can_accept_command()
        assert can_accept is False
        assert reason == RejectionReason.ESTOP_ACTIVE.value

    def test_estop_reset_allows_commands(self) -> None:
        """After reset, commands are accepted again."""
        sc = SafetyController()
        sc.trigger_estop()
        sc.reset_estop()

        can_accept, reason = sc.can_accept_command()
        assert can_accept is True

    def test_obstacle_blocks_motion_commands(self) -> None:
        """Obstacle detection blocks motion commands (follow/scan/velocity)."""
        sc = SafetyController()
        sc.update_obstacle(True)

        for cmd_type in ("follow", "scan", "velocity", "move"):
            can_accept, reason = sc.can_accept_command(cmd_type)
            assert can_accept is False, f"{cmd_type} should be blocked"
            assert reason == RejectionReason.OBSTACLE_BLOCKING.value

    def test_obstacle_allows_stop_and_query_commands(self) -> None:
        """STOP / status / query commands always pass even with obstacle active.

        Note: None (unknown command type) is NOT in the allowed set — when
        obstacle is active, unknown commands are conservatively blocked.
        Callers must pass an explicit command_type to bypass the obstacle gate.
        """
        sc = SafetyController()
        sc.update_obstacle(True)

        for cmd_type in ("stop", "stop_all", "status", "query"):
            can_accept, reason = sc.can_accept_command(cmd_type)
            assert can_accept is True, f"{cmd_type} should pass"
            assert reason == "ok"

    def test_obstacle_blocks_unknown_command_type(self) -> None:
        """Unknown / None command type is blocked when obstacle is active —
        conservative default since we can't prove it's a non-motion command."""
        sc = SafetyController()
        sc.update_obstacle(True)

        can_accept, reason = sc.can_accept_command(None)
        assert can_accept is False
        assert reason == RejectionReason.OBSTACLE_BLOCKING.value

    def test_obstacle_distance_hard_stop_overrides_boolean(self) -> None:
        """Distance < OBSTACLE_HARD_STOP_M forces obstacle=True even if
        the boolean detector reported False. This protects against the
        higher-level detector failing to flag a near obstacle."""
        sc = SafetyController()
        # Boolean False but distance below threshold → should still block
        sc.update_obstacle(detected=False, distance_m=0.20)

        can_accept, reason = sc.can_accept_command("follow")
        assert can_accept is False
        assert reason == RejectionReason.OBSTACLE_BLOCKING.value

    def test_obstacle_clears_when_distance_above_threshold(self) -> None:
        """When obstacle moves away beyond hard-stop distance, motion commands
        are accepted again."""
        sc = SafetyController()
        sc.update_obstacle(detected=True, distance_m=0.20)
        assert sc.can_accept_command("follow")[0] is False

        sc.update_obstacle(detected=False, distance_m=0.50)
        assert sc.can_accept_command("follow")[0] is True

    def test_snapshot_reflects_state(self) -> None:
        """Snapshot includes all safety fields."""
        sc = SafetyController()
        sc.trigger_estop()
        sc.update_obstacle(True)

        snap = sc.snapshot()
        assert snap.estop is True
        assert snap.obstacle is True

    def test_snapshot_idempotent(self) -> None:
        """Multiple snapshots are consistent."""
        sc = SafetyController()
        snap1 = sc.snapshot()
        snap2 = sc.snapshot()

        assert snap1.estop == snap2.estop
        assert snap1.obstacle == snap2.obstacle


# ---------------------------------------------------------------------------
# Integration: estop + command flow
# ---------------------------------------------------------------------------

class TestSafetyIntegration:
    """Multi-component safety integration tests."""

    def test_estop_rejects_raw_command(self) -> None:
        """SafetyController rejections happen before CommandGuard."""
        sc = SafetyController()
        sc.trigger_estop()

        can_accept, reason = sc.can_accept_command()
        assert can_accept is False
        assert reason == RejectionReason.ESTOP_ACTIVE.value

    def test_ttl_passed_but_estop_active(self) -> None:
        """Even with valid TTL, estop blocks the command."""
        sc = SafetyController()
        guard = CommandGuard()

        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        attach_timestamp(cmd)

        ttl_result = guard.validate(cmd)
        assert ttl_result.valid is True

        sc.trigger_estop()
        can_accept, _ = sc.can_accept_command()
        assert can_accept is False