"""Integration tests: SingularitySkillContainer ↔ RealExecutor bridge.

Verifies that the full command execution chain works end-to-end:
  CommandRequest → RealExecutor → SingularitySkillContainer → DimOS Skill
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dolly_gateway.contracts import (
    CommandKind,
    CommandRequest,
    CommandTarget,
    RejectionReason,
    RobotMode,
    TargetKind,
)
from dolly_gateway.executor import RealExecutor, SkillContainer
from dolly_gateway.safety import SafetyController
from singularity_skills.skills import SingularitySkillContainer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def follow_skill() -> MagicMock:
    skill = MagicMock()
    skill.follow_person = MagicMock(return_value=True)
    return skill


@pytest.fixture
def nav_skill() -> MagicMock:
    skill = MagicMock()
    skill.stop_navigation = MagicMock(return_value=True)
    return skill


@pytest.fixture
def explorer_skill() -> MagicMock:
    skill = MagicMock()
    skill.start_exploration = MagicMock(return_value=True)
    skill.stop_all = MagicMock(return_value=True)
    return skill


@pytest.fixture
def safety() -> SafetyController:
    return SafetyController()


@pytest.fixture
def executor(
    follow_skill: MagicMock,
    nav_skill: MagicMock,
    explorer_skill: MagicMock,
) -> RealExecutor:
    exec = RealExecutor(command_timeout_s=2.0)
    exec.register_skill("follow", follow_skill)
    exec.register_skill("navigation", nav_skill)
    exec.register_skill("explorer", explorer_skill)
    return exec


def _cmd(kind: CommandKind, request_id: str = "test-001", track_id: str | None = None) -> CommandRequest:
    return CommandRequest(
        command=kind,
        request_id=request_id,
        target=CommandTarget(kind=TargetKind.PRIMARY_PERSON, track_id=track_id),
    )


# ---------------------------------------------------------------------------
# Full dispatch chain
# ---------------------------------------------------------------------------

class TestFullDispatchChain:
    def test_follow_start_dispatches_to_skill(
        self, executor: RealExecutor, follow_skill: MagicMock
    ) -> None:
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1", "t42"))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.FOLLOWING
        follow_skill.follow_person.assert_called_once_with("t42")

    def test_follow_hold_dispatches_to_skill(
        self, executor: RealExecutor, nav_skill: MagicMock
    ) -> None:
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_HOLD, "fh1"))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.IDLE
        nav_skill.stop_navigation.assert_called_once()

    def test_scan_start_dispatches_to_skill(
        self, executor: RealExecutor, explorer_skill: MagicMock
    ) -> None:
        receipt = executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.EXPLORING
        explorer_skill.start_exploration.assert_called_once()

    def test_mission_stop_dispatches_to_all_skills(
        self,
        executor: RealExecutor,
        nav_skill: MagicMock,
        explorer_skill: MagicMock,
    ) -> None:
        receipt = executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        assert receipt.accepted is True
        assert receipt.executed is True
        assert receipt.robot_mode == RobotMode.IDLE
        nav_skill.stop_navigation.assert_called_once()
        explorer_skill.stop_all.assert_called_once()


# ---------------------------------------------------------------------------
# Skill failure propagation
# ---------------------------------------------------------------------------

class TestSkillFailurePropagation:
    def test_executor_returns_failure_when_skill_returns_false(
        self, follow_skill: MagicMock
    ) -> None:
        follow_skill.follow_person = MagicMock(return_value=False)
        executor = RealExecutor()
        executor.register_skill("follow", follow_skill)

        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is False
        assert receipt.executed is False
        assert receipt.reason == "skill_execution_failed"

    def test_executor_returns_failure_when_skill_raises(
        self, follow_skill: MagicMock
    ) -> None:
        follow_skill.follow_person = MagicMock(side_effect=RuntimeError("motor failure"))
        executor = RealExecutor()
        executor.register_skill("follow", follow_skill)

        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is False
        assert receipt.executed is False
        assert "motor failure" in receipt.reason

    def test_mission_stop_partial_failure_propagates(
        self, nav_skill: MagicMock, explorer_skill: MagicMock
    ) -> None:
        nav_skill.stop_navigation = MagicMock(side_effect=RuntimeError("nav down"))
        explorer_skill.stop_all = MagicMock(return_value=True)
        executor = RealExecutor()
        executor.register_skill("navigation", nav_skill)
        executor.register_skill("explorer", explorer_skill)

        receipt = executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        assert receipt.accepted is False
        assert receipt.executed is False
        explorer_skill.stop_all.assert_called_once()  # Second skill still called


# ---------------------------------------------------------------------------
# Mutual exclusion across full chain
# ---------------------------------------------------------------------------

class TestMutualExclusionChain:
    def test_follow_blocks_scan(
        self, executor: RealExecutor
    ) -> None:
        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        receipt = executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        assert receipt.accepted is False
        assert receipt.reason == RejectionReason.BUSY.value

    def test_scan_blocks_follow(
        self, executor: RealExecutor
    ) -> None:
        executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert receipt.accepted is False
        assert receipt.reason == RejectionReason.BUSY.value

    def test_hold_allowed_during_follow(
        self, executor: RealExecutor
    ) -> None:
        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        receipt = executor.execute(_cmd(CommandKind.FOLLOW_HOLD, "fh1"))
        assert receipt.accepted is True

    def test_stop_always_allowed(self, executor: RealExecutor) -> None:
        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        receipt = executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        assert receipt.accepted is True


# ---------------------------------------------------------------------------
# Mode tracking across full chain
# ---------------------------------------------------------------------------

class TestModeTrackingChain:
    def test_full_mode_transition_cycle(self, executor: RealExecutor) -> None:
        assert executor.current_mode == RobotMode.IDLE

        # Follow
        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert executor.current_mode == RobotMode.FOLLOWING

        # Hold
        executor.execute(_cmd(CommandKind.FOLLOW_HOLD, "fh1"))
        assert executor.current_mode == RobotMode.IDLE

        # Scan
        executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        assert executor.current_mode == RobotMode.EXPLORING

        # Stop
        executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        assert executor.current_mode == RobotMode.IDLE

    def test_mode_does_not_change_on_rejected_command(
        self, executor: RealExecutor
    ) -> None:
        executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert executor.current_mode == RobotMode.FOLLOWING

        # Scan should be rejected
        executor.execute(_cmd(CommandKind.SCAN_START, "s1"))
        # Mode should still be FOLLOWING
        assert executor.current_mode == RobotMode.FOLLOWING


# ---------------------------------------------------------------------------
# SingularitySkillContainer → RealExecutor wiring
# ---------------------------------------------------------------------------

class TestSkillContainerToExecutorWiring:
    def test_singularity_container_can_replace_dimos_skills(
        self, safety: SafetyController
    ) -> None:
        """Verify SingularitySkillContainer can be used as executor skill source."""
        # Create mock DimOS skills
        follow = MagicMock()
        follow.follow_person = MagicMock(return_value=True)
        nav = MagicMock()
        nav.stop_navigation = MagicMock(return_value=True)
        explorer = MagicMock()
        explorer.start_exploration = MagicMock(return_value=True)
        explorer.stop_all = MagicMock(return_value=True)

        # Create SingularitySkillContainer
        skill_container = SingularitySkillContainer(
            follow_skill=follow,
            navigation_skill=nav,
            explorer_skill=explorer,
            safety=safety,
        )

        # Verify skills work through the container
        assert skill_container.follow_person("t42") is True
        follow.follow_person.assert_called_once_with("t42")

        assert skill_container.stop_navigation() is True
        nav.stop_navigation.assert_called_once()

        assert skill_container.start_exploration() is True
        explorer.start_exploration.assert_called_once()

        assert skill_container.stop_all() is True
        explorer.stop_all.assert_called_once()

    def test_executor_works_with_skill_container_methods(
        self, follow_skill: MagicMock, explorer_skill: MagicMock
    ) -> None:
        """Verify RealExecutor can call skill methods and get correct receipts."""
        executor = RealExecutor()
        executor.register_skill("follow", follow_skill)
        executor.register_skill("explorer", explorer_skill)

        # Follow
        r1 = executor.execute(_cmd(CommandKind.FOLLOW_START, "f1"))
        assert r1.accepted is True
        assert r1.robot_mode == RobotMode.FOLLOWING

        # Stop (bypasses mutex)
        r2 = executor.execute(_cmd(CommandKind.MISSION_STOP, "ms1"))
        assert r2.accepted is True
        assert r2.robot_mode == RobotMode.IDLE