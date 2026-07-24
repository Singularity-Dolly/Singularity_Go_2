"""Tests for RobotSkillProvider — registration, lookup, health, wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dolly_gateway.skill_provider import RobotSkillProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> RobotSkillProvider:
    return RobotSkillProvider()


@pytest.fixture
def mock_skill() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_and_has(self, provider: RobotSkillProvider, mock_skill: MagicMock) -> None:
        assert provider.has("follow") is False
        provider.register("follow", mock_skill)
        assert provider.has("follow") is True

    def test_register_multiple(self, provider: RobotSkillProvider) -> None:
        provider.register("follow", MagicMock())
        provider.register("navigation", MagicMock())
        provider.register("explorer", MagicMock())
        assert provider.has("follow") is True
        assert provider.has("navigation") is True
        assert provider.has("explorer") is True
        assert provider.count == 3

    def test_unregister(self, provider: RobotSkillProvider, mock_skill: MagicMock) -> None:
        provider.register("follow", mock_skill)
        assert provider.has("follow") is True
        provider.unregister("follow")
        assert provider.has("follow") is False

    def test_unregister_nonexistent(self, provider: RobotSkillProvider) -> None:
        provider.unregister("nonexistent")  # Should not raise

    def test_list_registered(self, provider: RobotSkillProvider) -> None:
        provider.register("follow", MagicMock())
        provider.register("navigation", MagicMock())
        names = provider.list_registered()
        assert "follow" in names
        assert "navigation" in names
        assert len(names) == 2


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

class TestLookup:
    def test_get_returns_container(self, provider: RobotSkillProvider, mock_skill: MagicMock) -> None:
        provider.register("follow", mock_skill)
        assert provider.get("follow") is mock_skill

    def test_get_returns_none_for_missing(self, provider: RobotSkillProvider) -> None:
        assert provider.get("nonexistent") is None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_check_after_register(self, provider: RobotSkillProvider) -> None:
        provider.register("follow", MagicMock())
        provider.register("navigation", MagicMock())
        health = provider.health_check()
        assert health["follow"]["available"] is True
        assert health["follow"]["last_error"] is None
        assert health["navigation"]["available"] is True
        assert len(health) == 2

    def test_mark_error(self, provider: RobotSkillProvider) -> None:
        provider.register("follow", MagicMock())
        provider.mark_error("follow", "connection lost")
        health = provider.health_check()
        assert health["follow"]["last_error"] == "connection lost"

    def test_mark_unavailable(self, provider: RobotSkillProvider) -> None:
        provider.register("follow", MagicMock())
        provider.mark_unavailable("follow", "crashed")
        health = provider.health_check()
        assert health["follow"]["available"] is False
        assert health["follow"]["last_error"] == "crashed"

    def test_mark_available_recovery(self, provider: RobotSkillProvider) -> None:
        provider.register("follow", MagicMock())
        provider.mark_unavailable("follow", "crashed")
        provider.mark_available("follow")
        health = provider.health_check()
        assert health["follow"]["available"] is True
        assert health["follow"]["last_error"] is None

    def test_all_available(self, provider: RobotSkillProvider) -> None:
        assert provider.all_available() is False  # No skills registered
        provider.register("follow", MagicMock())
        provider.register("navigation", MagicMock())
        assert provider.all_available() is True
        provider.mark_unavailable("follow", "down")
        assert provider.all_available() is False

    def test_empty_health_check(self, provider: RobotSkillProvider) -> None:
        health = provider.health_check()
        assert health == {}


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

class TestWiring:
    def test_wire_to_executor(self, provider: RobotSkillProvider) -> None:
        from dolly_gateway.executor import RealExecutor

        executor = RealExecutor()
        provider.register("follow", MagicMock())
        provider.register("navigation", MagicMock())
        provider.register("explorer", MagicMock())
        provider.wire_to_executor(executor)

        assert executor.has_skill("follow") is True
        assert executor.has_skill("navigation") is True
        assert executor.has_skill("explorer") is True

    def test_wire_empty_provider(self, provider: RobotSkillProvider) -> None:
        from dolly_gateway.executor import RealExecutor

        executor = RealExecutor()
        provider.wire_to_executor(executor)
        # Should not raise, just no skills wired
        assert executor.has_skill("follow") is False