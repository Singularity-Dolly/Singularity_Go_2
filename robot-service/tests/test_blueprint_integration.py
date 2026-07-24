"""Tests for SingularityGo2Blueprint — lifecycle, skill wiring, health."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from blueprints.singularity_go2 import (
    BlueprintConfig,
    SingularityGo2Blueprint,
    create_blueprint,
)
from dolly_gateway.config import GatewaySettings
from dolly_gateway.contracts import RobotMode


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
def config() -> BlueprintConfig:
    settings = GatewaySettings(robot_id="test-bp-001", port=18780)
    return BlueprintConfig(settings)


@pytest.fixture
def blueprint(config: BlueprintConfig) -> SingularityGo2Blueprint:
    return SingularityGo2Blueprint(config=config)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_blueprint_creates_with_defaults(self) -> None:
        bp = create_blueprint()
        assert bp.gateway is not None
        assert bp.executor is not None
        assert bp.skill_provider is not None
        assert bp._running is False

    def test_blueprint_creates_with_custom_config(
        self, config: BlueprintConfig
    ) -> None:
        bp = SingularityGo2Blueprint(config=config)
        assert bp.gateway.robot_id == "test-bp-001"
        assert bp.gateway.port == 18780

    def test_blueprint_creates_with_skills(
        self,
        config: BlueprintConfig,
        follow_skill: MagicMock,
        nav_skill: MagicMock,
        explorer_skill: MagicMock,
    ) -> None:
        bp = SingularityGo2Blueprint(
            config=config,
            follow_skill=follow_skill,
            navigation_skill=nav_skill,
            explorer_skill=explorer_skill,
        )
        assert bp._follow_skill is follow_skill
        assert bp._navigation_skill is nav_skill
        assert bp._explorer_skill is explorer_skill


# ---------------------------------------------------------------------------
# Skill registration
# ---------------------------------------------------------------------------

class TestSkillRegistration:
    def test_register_skills_wires_to_executor(
        self,
        blueprint: SingularityGo2Blueprint,
        follow_skill: MagicMock,
        nav_skill: MagicMock,
        explorer_skill: MagicMock,
    ) -> None:
        blueprint.register_skills(
            follow_skill=follow_skill,
            navigation_skill=nav_skill,
            explorer_skill=explorer_skill,
        )

        assert blueprint.executor.has_skill("follow") is True
        assert blueprint.executor.has_skill("navigation") is True
        assert blueprint.executor.has_skill("explorer") is True

    def test_register_skills_updates_provider(
        self,
        blueprint: SingularityGo2Blueprint,
        follow_skill: MagicMock,
    ) -> None:
        blueprint.register_skills(follow_skill=follow_skill)

        assert blueprint.skill_provider.has("follow") is True
        assert blueprint.skill_provider.get("follow") is follow_skill

    def test_register_skills_partial(
        self,
        blueprint: SingularityGo2Blueprint,
        follow_skill: MagicMock,
    ) -> None:
        blueprint.register_skills(follow_skill=follow_skill)

        assert blueprint.executor.has_skill("follow") is True
        assert blueprint.executor.has_skill("navigation") is False
        assert blueprint.executor.has_skill("explorer") is False

    def test_register_skills_none_does_nothing(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        blueprint.register_skills()  # No skills
        assert blueprint.executor.has_skill("follow") is False


# ---------------------------------------------------------------------------
# Connection wiring
# ---------------------------------------------------------------------------

class TestConnectionWiring:
    def test_wire_connection_sets_frame_callback(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        connection = MagicMock()
        connection.on_frame = None

        blueprint.wire_connection(connection)

        assert connection.on_frame is not None
        assert callable(connection.on_frame)

    def test_wire_connection_sets_state_provider(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        def get_state() -> dict:
            return {"mode": "idle"}

        connection = MagicMock()
        connection.get_state = get_state

        blueprint.wire_connection(connection)

        # State provider should be set on gateway
        assert blueprint.gateway._dimos_state_provider is not None

    def test_wire_connection_preserves_original_frame_callback(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        original_called = []

        def original_handler(jpeg_bytes: bytes) -> None:
            original_called.append(jpeg_bytes)

        connection = MagicMock()
        connection.on_frame = original_handler

        blueprint.wire_connection(connection)

        # Call the wired handler
        connection.on_frame(b"test-frame")

        # Original should still be called
        assert len(original_called) == 1
        assert original_called[0] == b"test-frame"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_check_initial_state(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        health = blueprint.health_check()

        assert health["running"] is False
        assert health["connection_wired"] is False
        assert health["executor_mode"] == RobotMode.IDLE.value
        assert "skills" in health
        assert "all_skills_available" in health

    def test_health_check_after_skill_registration(
        self,
        blueprint: SingularityGo2Blueprint,
        follow_skill: MagicMock,
        nav_skill: MagicMock,
        explorer_skill: MagicMock,
    ) -> None:
        blueprint.register_skills(
            follow_skill=follow_skill,
            navigation_skill=nav_skill,
            explorer_skill=explorer_skill,
        )

        health = blueprint.health_check()
        assert health["all_skills_available"] is True
        assert len(health["skills"]) == 3

    def test_health_check_after_connection_wiring(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        connection = MagicMock()
        blueprint.wire_connection(connection)

        health = blueprint.health_check()
        assert health["connection_wired"] is True


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        assert blueprint._running is False
        await blueprint.start()
        assert blueprint._running is True
        await blueprint.stop()
        assert blueprint._running is False

    @pytest.mark.asyncio
    async def test_start_registers_skills(
        self,
        config: BlueprintConfig,
        follow_skill: MagicMock,
    ) -> None:
        bp = SingularityGo2Blueprint(
            config=config,
            follow_skill=follow_skill,
        )
        await bp.start()

        assert bp.executor.has_skill("follow") is True
        await bp.stop()

    @pytest.mark.asyncio
    async def test_double_start_is_safe(
        self, blueprint: SingularityGo2Blueprint
    ) -> None:
        await blueprint.start()
        await blueprint.start()  # Should not raise
        await blueprint.stop()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_create_blueprint_with_overrides(self) -> None:
        bp = create_blueprint(robot_id="custom-001", port=19999, host="127.0.0.1")
        assert bp.gateway.robot_id == "custom-001"
        assert bp.gateway.port == 19999
        assert bp.gateway.host == "127.0.0.1"

    def test_create_blueprint_defaults(self) -> None:
        bp = create_blueprint()
        assert bp.gateway.robot_id == "unknown"
        assert bp.gateway.port == 8780
        assert bp.gateway.host == "0.0.0.0"