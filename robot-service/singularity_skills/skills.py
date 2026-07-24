"""SingularitySkillContainer — DimOS @skill methods for Go2 robot control.

Each @skill method maps to a CommandKind and provides a callable RPC
interface for the RealExecutor. Skills integrate with the SafetyController
for pre-execution safety checks.

Skill name → CommandKind mapping:
    follow_start  → follow.start   (PersonFollowSkillContainer)
    follow_hold   → follow.hold    (NavigationSkillContainer)
    scan_start    → scan.start     (WavefrontFrontierExplorer)
    mission_stop  → mission.stop   (all skills)

Usage:
    container = SingularitySkillContainer(
        safety=safety_controller,
        follow_skill=person_follow_container,
        navigation_skill=nav_container,
        explorer_skill=explorer_container,
    )
    # Each method is a callable that returns bool
    container.follow_person(target_id="t42")
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# @skill decorator (compatible with DimOS, also works standalone)
# ---------------------------------------------------------------------------

def skill(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a method as a DimOS skill.

    On real DimOS, this decorator is imported from dimos.skill.
    On standalone (without DimOS), it acts as a no-op marker.

    Args:
        name: The skill name used for RPC registration.

    Returns:
        A decorator that adds __skill_name__ to the wrapped function.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__skill_name__ = name  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# Skill protocol
# ---------------------------------------------------------------------------

class SkillProtocol:
    """Protocol for a DimOS Skill Container.

    Real implementations are injected at construction time.
    When no container is available, the skill falls back to stub behavior.
    """

    def follow_person(self, target_id: str | None = None) -> bool:
        """Start following a person. Returns True on success."""
        raise NotImplementedError

    def stop_navigation(self) -> bool:
        """Stop current navigation/following. Returns True on success."""
        raise NotImplementedError

    def start_exploration(self) -> bool:
        """Start room scanning/exploration. Returns True on success."""
        raise NotImplementedError

    def stop_all(self) -> bool:
        """Emergency stop all motors and exploration. Returns True on success."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SingularitySkillContainer
# ---------------------------------------------------------------------------

class SingularitySkillContainer:
    """Skill container for Singularity Go2 robot commands.

    Bridges the DollyGatewayModule command system to DimOS Skill Containers.
    Each method is decorated with @skill for DimOS RPC registration.

    When no DimOS Skill Container is available (standalone mode), methods
    fall back to stub behavior (returning True for mode-only transitions).

    Thread safety: all skill access is lock-protected via the internal
    skill containers (which are managed by RobotSkillProvider).
    """

    def __init__(
        self,
        follow_skill: Any | None = None,
        navigation_skill: Any | None = None,
        explorer_skill: Any | None = None,
        safety: Any | None = None,
    ) -> None:
        """Initialize the skill container.

        Args:
            follow_skill: DimOS PersonFollowSkillContainer instance.
            navigation_skill: DimOS NavigationSkillContainer instance.
            explorer_skill: DimOS WavefrontFrontierExplorer instance.
            safety: SafetyController instance for pre-execution checks.
        """
        self._follow_skill = follow_skill
        self._navigation_skill = navigation_skill
        self._explorer_skill = explorer_skill
        self._safety = safety

    # ------------------------------------------------------------------
    # @skill(name="follow_start")
    # ------------------------------------------------------------------

    @skill(name="follow_start")
    def follow_person(self, target_id: str | None = None) -> bool:
        """Start following a person.

        Delegates to PersonFollowSkillContainer.follow_person().
        Falls back to stub (mode-only transition) when no container.

        Args:
            target_id: Optional track ID of the person to follow.

        Returns:
            True if the command was accepted and executed successfully.
        """
        if self._safety is not None:
            can_accept, _reason = self._safety.can_accept_command()
            if not can_accept:
                logger.warning("follow_person rejected by safety: %s", _reason)
                return False

        if self._follow_skill is None:
            logger.info("No follow skill registered — fallback to stub")
            return True

        try:
            if hasattr(self._follow_skill, "follow_person"):
                return self._follow_skill.follow_person(target_id)
            else:
                logger.warning("follow_skill has no follow_person method")
                return False
        except Exception as exc:
            logger.exception("follow_person failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # @skill(name="follow_hold")
    # ------------------------------------------------------------------

    @skill(name="follow_hold")
    def stop_navigation(self) -> bool:
        """Stop current navigation/following.

        Delegates to NavigationSkillContainer.stop_navigation().
        Falls back to stub when no container.

        Returns:
            True if the navigation was stopped successfully.
        """
        if self._navigation_skill is None:
            logger.info("No navigation skill registered — fallback to stub")
            return True

        try:
            if hasattr(self._navigation_skill, "stop_navigation"):
                return self._navigation_skill.stop_navigation()
            else:
                logger.warning("navigation_skill has no stop_navigation method")
                return False
        except Exception as exc:
            logger.exception("stop_navigation failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # @skill(name="scan_start")
    # ------------------------------------------------------------------

    @skill(name="scan_start")
    def start_exploration(self) -> bool:
        """Start room scanning/exploration.

        Delegates to WavefrontFrontierExplorer.start().
        Falls back to stub when no container.

        Safety check: verifies the safety controller allows command execution.

        Returns:
            True if exploration was started successfully.
        """
        if self._safety is not None:
            can_accept, _reason = self._safety.can_accept_command()
            if not can_accept:
                logger.warning("start_exploration rejected by safety: %s", _reason)
                return False

        if self._explorer_skill is None:
            logger.info("No explorer skill registered — fallback to stub")
            return True

        try:
            if hasattr(self._explorer_skill, "start_exploration"):
                return self._explorer_skill.start_exploration()
            elif hasattr(self._explorer_skill, "start"):
                return self._explorer_skill.start()
            else:
                logger.warning("explorer_skill has no start_exploration/start method")
                return False
        except Exception as exc:
            logger.exception("start_exploration failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # @skill(name="mission_stop")
    # ------------------------------------------------------------------

    @skill(name="mission_stop")
    def stop_all(self) -> bool:
        """Emergency stop all motors and exploration.

        Tries to stop all registered skill containers. Even if one fails,
        continues to attempt stopping the others.

        Returns:
            True if all skills were stopped successfully.
        """
        success = True

        # Stop navigation
        if self._navigation_skill is not None:
            try:
                if hasattr(self._navigation_skill, "stop_navigation"):
                    result = self._navigation_skill.stop_navigation()
                    if result is not True:
                        success = False
                elif hasattr(self._navigation_skill, "stop_all"):
                    result = self._navigation_skill.stop_all()
                    if result is not True:
                        success = False
            except Exception as exc:
                logger.exception("stop_navigation failed: %s", exc)
                success = False

        # Stop exploration
        if self._explorer_skill is not None:
            try:
                if hasattr(self._explorer_skill, "stop_all"):
                    result = self._explorer_skill.stop_all()
                    if result is not True:
                        success = False
                elif hasattr(self._explorer_skill, "stop"):
                    result = self._explorer_skill.stop()
                    if result is not True:
                        success = False
            except Exception as exc:
                logger.exception("stop_exploration failed: %s", exc)
                success = False

        # If no skills registered, fallback to stub
        if self._navigation_skill is None and self._explorer_skill is None:
            logger.info("No skills registered — fallback to stub for mission.stop")
            return True

        return success

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_skills(self) -> list[str]:
        """Return names of all registered @skill methods."""
        skills: list[str] = []
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            attr = getattr(self, attr_name, None)
            if callable(attr) and hasattr(attr, "__skill_name__"):
                skills.append(getattr(attr, "__skill_name__"))
        return skills

    def has_skill_container(self, name: str) -> bool:
        """Check if a DimOS Skill Container is registered."""
        if name == "follow":
            return self._follow_skill is not None
        if name == "navigation":
            return self._navigation_skill is not None
        if name == "explorer":
            return self._explorer_skill is not None
        return False