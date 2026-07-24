"""Tests for SingularitySkillContainer — @skill methods, fallback, safety."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dolly_gateway.safety import SafetyController
from singularity_skills.skills import SingularitySkillContainer, skill


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def follow_skill() -> MagicMock:
    skill_mock = MagicMock()
    skill_mock.follow_person = MagicMock(return_value=True)
    return skill_mock


@pytest.fixture
def nav_skill() -> MagicMock:
    skill_mock = MagicMock()
    skill_mock.stop_navigation = MagicMock(return_value=True)
    return skill_mock


@pytest.fixture
def explorer_skill() -> MagicMock:
    skill_mock = MagicMock()
    skill_mock.start_exploration = MagicMock(return_value=True)
    skill_mock.stop_all = MagicMock(return_value=True)
    return skill_mock


@pytest.fixture
def safety() -> SafetyController:
    return SafetyController()


@pytest.fixture
def container(
    follow_skill: MagicMock,
    nav_skill: MagicMock,
    explorer_skill: MagicMock,
    safety: SafetyController,
) -> SingularitySkillContainer:
    return SingularitySkillContainer(
        follow_skill=follow_skill,
        navigation_skill=nav_skill,
        explorer_skill=explorer_skill,
        safety=safety,
    )


# ---------------------------------------------------------------------------
# @skill decorator
# ---------------------------------------------------------------------------

class TestSkillDecorator:
    def test_decorator_adds_skill_name(self) -> None:
        @skill(name="test_skill")
        def my_func() -> bool:
            return True

        assert hasattr(my_func, "__skill_name__")
        assert my_func.__skill_name__ == "test_skill"

    def test_decorated_function_still_callable(self) -> None:
        @skill(name="test_skill")
        def my_func() -> bool:
            return True

        assert my_func() is True

    def test_decorator_preserves_arguments(self) -> None:
        @skill(name="test_skill")
        def my_func(a: int, b: str = "default") -> str:
            return f"{a}:{b}"

        assert my_func(42, b="hello") == "42:hello"


# ---------------------------------------------------------------------------
# Stub fallback (no skills registered)
# ---------------------------------------------------------------------------

class TestStubFallback:
    def test_follow_person_fallback(self) -> None:
        container = SingularitySkillContainer()
        assert container.follow_person("t42") is True

    def test_stop_navigation_fallback(self) -> None:
        container = SingularitySkillContainer()
        assert container.stop_navigation() is True

    def test_start_exploration_fallback(self) -> None:
        container = SingularitySkillContainer()
        assert container.start_exploration() is True

    def test_stop_all_fallback(self) -> None:
        container = SingularitySkillContainer()
        assert container.stop_all() is True


# ---------------------------------------------------------------------------
# Real skill dispatch
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_follow_person_calls_skill(
        self, container: SingularitySkillContainer, follow_skill: MagicMock
    ) -> None:
        result = container.follow_person("t42")
        assert result is True
        follow_skill.follow_person.assert_called_once_with("t42")

    def test_stop_navigation_calls_skill(
        self, container: SingularitySkillContainer, nav_skill: MagicMock
    ) -> None:
        result = container.stop_navigation()
        assert result is True
        nav_skill.stop_navigation.assert_called_once()

    def test_start_exploration_calls_skill(
        self, container: SingularitySkillContainer, explorer_skill: MagicMock
    ) -> None:
        result = container.start_exploration()
        assert result is True
        explorer_skill.start_exploration.assert_called_once()

    def test_stop_all_calls_all_skills(
        self,
        container: SingularitySkillContainer,
        nav_skill: MagicMock,
        explorer_skill: MagicMock,
    ) -> None:
        result = container.stop_all()
        assert result is True
        nav_skill.stop_navigation.assert_called_once()
        explorer_skill.stop_all.assert_called_once()


# ---------------------------------------------------------------------------
# Skill failure
# ---------------------------------------------------------------------------

class TestSkillFailure:
    def test_follow_person_returns_false_on_failure(
        self, follow_skill: MagicMock, safety: SafetyController
    ) -> None:
        follow_skill.follow_person = MagicMock(return_value=False)
        container = SingularitySkillContainer(
            follow_skill=follow_skill,
            safety=safety,
        )
        assert container.follow_person("t42") is False

    def test_follow_person_returns_false_on_exception(
        self, follow_skill: MagicMock, safety: SafetyController
    ) -> None:
        follow_skill.follow_person = MagicMock(side_effect=RuntimeError("motor failure"))
        container = SingularitySkillContainer(
            follow_skill=follow_skill,
            safety=safety,
        )
        assert container.follow_person("t42") is False

    def test_stop_all_partial_failure(
        self, nav_skill: MagicMock, explorer_skill: MagicMock
    ) -> None:
        nav_skill.stop_navigation = MagicMock(side_effect=RuntimeError("nav down"))
        explorer_skill.stop_all = MagicMock(return_value=True)
        container = SingularitySkillContainer(
            navigation_skill=nav_skill,
            explorer_skill=explorer_skill,
        )
        # One skill failed, but stop_all continues
        assert container.stop_all() is False
        explorer_skill.stop_all.assert_called_once()


# ---------------------------------------------------------------------------
# Safety integration
# ---------------------------------------------------------------------------

class TestSafetyIntegration:
    def test_follow_person_rejected_when_estop_active(
        self, follow_skill: MagicMock
    ) -> None:
        safety = SafetyController()
        safety.trigger_estop()
        container = SingularitySkillContainer(
            follow_skill=follow_skill,
            safety=safety,
        )
        assert container.follow_person("t42") is False
        follow_skill.follow_person.assert_not_called()

    def test_start_exploration_rejected_when_estop_active(
        self, explorer_skill: MagicMock
    ) -> None:
        safety = SafetyController()
        safety.trigger_estop()
        container = SingularitySkillContainer(
            explorer_skill=explorer_skill,
            safety=safety,
        )
        assert container.start_exploration() is False
        explorer_skill.start_exploration.assert_not_called()

    def test_skills_work_when_safety_ok(
        self, follow_skill: MagicMock, explorer_skill: MagicMock
    ) -> None:
        safety = SafetyController()
        container = SingularitySkillContainer(
            follow_skill=follow_skill,
            explorer_skill=explorer_skill,
            safety=safety,
        )
        assert container.follow_person("t42") is True
        assert container.start_exploration() is True


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

class TestIntrospection:
    def test_list_skills(self, container: SingularitySkillContainer) -> None:
        skills = container.list_skills()
        assert "follow_start" in skills
        assert "follow_hold" in skills
        assert "scan_start" in skills
        assert "mission_stop" in skills
        assert len(skills) == 4

    def test_has_skill_container_true(
        self, container: SingularitySkillContainer
    ) -> None:
        assert container.has_skill_container("follow") is True
        assert container.has_skill_container("navigation") is True
        assert container.has_skill_container("explorer") is True

    def test_has_skill_container_false(self) -> None:
        container = SingularitySkillContainer()
        assert container.has_skill_container("follow") is False
        assert container.has_skill_container("navigation") is False
        assert container.has_skill_container("explorer") is False

    def test_has_skill_container_unknown_name(
        self, container: SingularitySkillContainer
    ) -> None:
        assert container.has_skill_container("unknown") is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_skill_without_expected_method(
        self, safety: SafetyController
    ) -> None:
        """Skill container without follow_person method."""
        bad_skill = MagicMock()
        del bad_skill.follow_person  # Remove the auto-created magic method
        container = SingularitySkillContainer(
            follow_skill=bad_skill,
            safety=safety,
        )
        assert container.follow_person("t42") is False

    def test_explorer_uses_start_fallback(
        self, safety: SafetyController
    ) -> None:
        """Explorer that has start() but not start_exploration()."""
        explorer = MagicMock()
        del explorer.start_exploration
        explorer.start = MagicMock(return_value=True)
        container = SingularitySkillContainer(
            explorer_skill=explorer,
            safety=safety,
        )
        assert container.start_exploration() is True
        explorer.start.assert_called_once()

    def test_empty_skill_list(self) -> None:
        container = SingularitySkillContainer()
        skills = container.list_skills()
        assert len(skills) == 4  # Methods exist even without containers