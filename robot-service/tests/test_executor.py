"""Tests for RealExecutor — skill dispatch, mutual exclusion, timeout, fallback."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from dolly_gateway.contracts import (
    CommandKind,
    CommandReceipt,
    CommandRequest,
    CommandTarget,
    RejectionReason,
    RobotMode,
    TargetKind,
)
from dolly_gateway.executor import RealExecutor, SkillContainer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def executor() -> RealExecutor:
    return RealExecutor(command_timeout_s=2.0)


@pytest.fixture
def follow_skill() -> SkillContainer:
    skill = SkillContainer()
    skill.follow_person = MagicMock(return_value=True)
    skill.stop_navigation = MagicMock(return_value=True)
    return skill


@pytest.fixture
def nav_skill() -> SkillContainer:
    skill = SkillContainer()
    skill.stop_navigation = MagicMock(return_value=True)
    return skill


@pytest.fixture
def explorer_skill() -> SkillContainer:
    skill = SkillContainer()
    skill.start_exploration = MagicMock(return_value=True)
    skill.stop_all = MagicMock(return_value=True)
    return skill


def _cmd(kind: CommandKind, request_id: str = "test-001") -> CommandRequest:
    return CommandRequest(command=kind, request_id=request_id)


# ---------------------------------------------------------------------------
# Fallback: no skills registered → stub behavior
# ---------------------------------------------------------------------------

class TestNoSkillsFallback:
    """When no skills are registered, RealExecutor falls back to stub mode."""

    def test_scan_start_fallback(self, executor: RealExecutor) -> None:
        receipt = executor.execute(_cmd(CommandKind.SCAN_START))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.EXPLORING

    def test_follow_start_fallback(self, executor: RealExecutor) -> None:
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.FOLLOWING

    def test_follow_hold_fallback(self, executor: RealExecutor) -> None:
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_HOLD))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.IDLE

    def test_mission_stop_fallback(self, executor: RealExecutor) -> None:
        receipt = executor.execute(_cmd(CommandKind.MISSION_STOP))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.IDLE


# ---------------------------------------------------------------------------
# Skill registration
# ---------------------------------------------------------------------------

class TestSkillRegistration:
    def test_register_and_has_skill(self, executor: RealExecutor) -> None:
        skill = SkillContainer()
        assert executor.has_skill("follow") is False
        executor.register_skill("follow", skill)
        assert executor.has_skill("follow") is True

    def test_register_unknown_skill_name(self, executor: RealExecutor) -> None:
        executor.register_skill("unknown", SkillContainer())
        assert executor.has_skill("unknown") is False

    def test_register_multiple_skills(self, executor: RealExecutor) -> None:
        executor.register_skill("follow", SkillContainer())
        executor.register_skill("navigation", SkillContainer())
        executor.register_skill("explorer", SkillContainer())
        assert executor.has_skill("follow") is True
        assert executor.has_skill("navigation") is True
        assert executor.has_skill("explorer") is True


# ---------------------------------------------------------------------------
# Real skill dispatch
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_follow_start_calls_skill(
        self, executor: RealExecutor, follow_skill: SkillContainer
    ) -> None:
        executor.register_skill("follow", follow_skill)
        cmd = CommandRequest(
            command=CommandKind.FOLLOW_START,
            request_id="f1",
            target=CommandTarget(kind=TargetKind.PRIMARY_PERSON, track_id="t42"),
        )
        receipt = executor.execute(cmd)
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.FOLLOWING
        follow_skill.follow_person.assert_called_once_with("t42")  # type: ignore[union-attr]

    def test_follow_hold_calls_nav_skill(
        self, executor: RealExecutor, nav_skill: SkillContainer
    ) -> None:
        executor.register_skill("navigation", nav_skill)
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_HOLD, "fh1"))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.IDLE
        nav_skill.stop_navigation.assert_called_once()  # type: ignore[union-attr]

    def test_scan_start_calls_explorer(
        self, executor: RealExecutor, explorer_skill: SkillContainer
    ) -> None:
        executor.register_skill("explorer", explorer_skill)
        receipt = executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.EXPLORING
        explorer_skill.start_exploration.assert_called_once()  # type: ignore[union-attr]

    def test_mission_stop_calls_both_skills(
        self,
        executor: RealExecutor,
        nav_skill: SkillContainer,
        explorer_skill: SkillContainer,
    ) -> None:
        executor.register_skill("navigation", nav_skill)
        executor.register_skill("explorer", explorer_skill)
        receipt = executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.IDLE
        nav_skill.stop_navigation.assert_called_once()  # type: ignore[union-attr]
        explorer_skill.stop_all.assert_called_once()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Mutual exclusion
# ---------------------------------------------------------------------------

class TestMutualExclusion:
    def test_follow_rejects_scan_when_following(
        self, executor: RealExecutor, follow_skill: SkillContainer
    ) -> None:
        executor.register_skill("follow", follow_skill)
        # Start following
        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        # Try to scan — should be rejected
        receipt = executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        assert receipt.accepted is False
        assert receipt.reason == RejectionReason.BUSY.value

    def test_scan_rejects_follow_when_exploring(
        self, executor: RealExecutor, explorer_skill: SkillContainer
    ) -> None:
        executor.register_skill("explorer", explorer_skill)
        executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is False
        assert receipt.reason == RejectionReason.BUSY.value

    def test_hold_is_allowed_during_following(
        self, executor: RealExecutor, follow_skill: SkillContainer
    ) -> None:
        executor.register_skill("follow", follow_skill)
        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_HOLD, "fh1"))
        assert receipt.accepted is True

    def test_idle_allows_all(self) -> None:
        # Use separate executors to avoid mode contamination
        e1 = RealExecutor()
        receipt = e1.execute(_cmd(CommandKind.SCAN_START, "s1"))
        assert receipt.accepted is True

        e2 = RealExecutor()
        receipt = e2.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is True


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_skill_raises_exception(
        self, executor: RealExecutor, follow_skill: SkillContainer
    ) -> None:
        follow_skill.follow_person = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("motor failure")
        )
        executor.register_skill("follow", follow_skill)
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is False
        assert receipt.executed is False
        assert "motor failure" in receipt.reason

    def test_skill_returns_false(
        self, executor: RealExecutor, follow_skill: SkillContainer
    ) -> None:
        follow_skill.follow_person = MagicMock(return_value=False)  # type: ignore[method-assign]
        executor.register_skill("follow", follow_skill)
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is False
        assert receipt.executed is False
        assert receipt.reason == "skill_execution_failed"

    def test_mission_stop_partial_failure(
        self,
        executor: RealExecutor,
        nav_skill: SkillContainer,
        explorer_skill: SkillContainer,
    ) -> None:
        nav_skill.stop_navigation = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("nav down")
        )
        explorer_skill.stop_all = MagicMock(return_value=True)  # type: ignore[method-assign]
        executor.register_skill("navigation", nav_skill)
        executor.register_skill("explorer", explorer_skill)
        receipt = executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        # One skill failed, but stop still proceeds
        assert receipt.accepted is False
        assert receipt.executed is False


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_skill_timeout(
        self, executor: RealExecutor, follow_skill: SkillContainer
    ) -> None:
        def slow(target_id: str | None = None) -> bool:
            time.sleep(3.0)  # > 2.0s timeout
            return True

        follow_skill.follow_person = slow  # type: ignore[method-assign]
        executor.register_skill("follow", follow_skill)
        # Use a short timeout
        executor = RealExecutor(command_timeout_s=0.1)
        executor.register_skill("follow", follow_skill)
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is False
        assert receipt.executed is False
        assert "timed out" in receipt.reason


# ---------------------------------------------------------------------------
# Mode tracking
# ---------------------------------------------------------------------------

class TestModeTracking:
    def test_mode_transitions(
        self, executor: RealExecutor, follow_skill: SkillContainer,
        explorer_skill: SkillContainer,
    ) -> None:
        executor.register_skill("follow", follow_skill)
        executor.register_skill("explorer", explorer_skill)

        assert executor.current_mode == RobotMode.IDLE

        executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        assert executor.current_mode == RobotMode.EXPLORING

        # hold resets to idle
        executor.execute(_cmd(CommandKind.FOLLOW_HOLD, "fh1"))
        assert executor.current_mode == RobotMode.IDLE

        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert executor.current_mode == RobotMode.FOLLOWING

        executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        assert executor.current_mode == RobotMode.IDLE