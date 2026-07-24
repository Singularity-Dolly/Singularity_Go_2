"""RobotSkillProvider — manages DimOS Skill Container lifecycle.

Provides lazy registration, lookup, and health checking for
robot skill containers (follow, navigation, explorer).

Thread safety: all operations are lock-protected.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skill status
# ---------------------------------------------------------------------------

class SkillStatus:
    """Health status of a registered skill."""

    def __init__(self) -> None:
        self.available: bool = False
        self.last_error: str | None = None


# ---------------------------------------------------------------------------
# RobotSkillProvider
# ---------------------------------------------------------------------------

class RobotSkillProvider:
    """Registers and manages DimOS Skill Container references.

    Usage:
        provider = RobotSkillProvider()
        provider.register("follow", follow_skill_container)
        provider.register("navigation", nav_skill_container)
        provider.register("explorer", explorer_skill_container)

        status = provider.health_check()
        # {"follow": {"available": True, "last_error": None}, ...}

        container = provider.get("follow")
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._containers: dict[str, Any] = {}
        self._statuses: dict[str, SkillStatus] = {}

    # ---- Registration ----

    def register(self, name: str, container: Any) -> None:
        """Register a skill container by name.

        Supported names: 'follow', 'navigation', 'explorer'

        Args:
            name: Skill name (one of the supported names).
            container: A DimOS Skill Container instance.
        """
        with self._lock:
            self._containers[name] = container
            self._statuses[name] = SkillStatus()
            self._statuses[name].available = True
            logger.info("Skill registered: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a skill container."""
        with self._lock:
            self._containers.pop(name, None)
            self._statuses.pop(name, None)
            logger.info("Skill unregistered: %s", name)

    # ---- Lookup ----

    def get(self, name: str) -> Any | None:
        """Get a registered skill container, or None."""
        with self._lock:
            return self._containers.get(name)

    def has(self, name: str) -> bool:
        """Check if a skill is registered."""
        with self._lock:
            return name in self._containers

    def list_registered(self) -> list[str]:
        """Return names of all registered skills."""
        with self._lock:
            return list(self._containers.keys())

    # ---- Health ----

    def health_check(self) -> dict[str, dict[str, Any]]:
        """Return health status of all registered skills.

        Returns a dict mapping skill name to status info:
        {
            "follow": {"available": True, "last_error": None},
            ...
        }
        """
        with self._lock:
            result: dict[str, dict[str, Any]] = {}
            for name, status in self._statuses.items():
                result[name] = {
                    "available": status.available,
                    "last_error": status.last_error,
                }
            return result

    def mark_error(self, name: str, error: str) -> None:
        """Mark a skill as having experienced an error."""
        with self._lock:
            if name in self._statuses:
                self._statuses[name].last_error = error
                logger.warning("Skill error [%s]: %s", name, error)

    def mark_available(self, name: str) -> None:
        """Mark a skill as available (after recovery)."""
        with self._lock:
            if name in self._statuses:
                self._statuses[name].available = True
                self._statuses[name].last_error = None

    def mark_unavailable(self, name: str, reason: str = "unknown") -> None:
        """Mark a skill as unavailable."""
        with self._lock:
            if name in self._statuses:
                self._statuses[name].available = False
                self._statuses[name].last_error = reason

    def all_available(self) -> bool:
        """Check if all registered skills are available."""
        with self._lock:
            if not self._statuses:
                return False
            return all(s.available for s in self._statuses.values())

    # ---- Integration with RealExecutor ----

    def wire_to_executor(self, executor: Any) -> None:
        """Wire all registered skills to a RealExecutor instance.

        Args:
            executor: A RealExecutor instance with register_skill() method.
        """
        with self._lock:
            for name, container in self._containers.items():
                executor.register_skill(name, container)
                logger.info("Wired skill '%s' to executor", name)

    # ---- Count ----

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._containers)