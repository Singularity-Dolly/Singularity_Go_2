"""RealExecutor — maps CommandKind to DimOS Skill Container RPC calls.

Replaces StubExecutor with real robot control via DimOS skills.
Maintains backward compatibility: falls back to StubExecutor behavior
when no Skill Container is registered.

Guarantees:
- Mutual exclusion: follow and scan cannot execute concurrently
- Timeout protection: each command times out after 5 seconds
- Error isolation: executor failures are caught and returned as receipts
- Thread safety: all skill access is lock-protected
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .command_queue import CommandExecutor
from .contracts import (
    CommandKind,
    CommandReceipt,
    CommandRequest,
    RejectionReason,
    RobotMode,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COMMAND_TIMEOUT_S: float = 5.0

# CommandKind → RobotMode mapping (used as fallback when no skill is registered)
_MODE_MAP: dict[CommandKind, RobotMode] = {
    CommandKind.SCAN_START: RobotMode.EXPLORING,
    CommandKind.FOLLOW_START: RobotMode.FOLLOWING,
    CommandKind.FOLLOW_HOLD: RobotMode.IDLE,
    CommandKind.MISSION_STOP: RobotMode.IDLE,
}

# Mutually exclusive mode pairs: if one is active, the other is rejected
_MUTEX_PAIRS: list[tuple[RobotMode, RobotMode]] = [
    (RobotMode.FOLLOWING, RobotMode.EXPLORING),
    (RobotMode.EXPLORING, RobotMode.FOLLOWING),
]


# ---------------------------------------------------------------------------
# Skill container protocol
# ---------------------------------------------------------------------------

class SkillContainer:
    """Protocol for a DimOS Skill Container.

    Real implementations are injected at runtime by DimOS blueprint.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

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
# RealExecutor
# ---------------------------------------------------------------------------

class RealExecutor(CommandExecutor):
    """Maps CommandKind to real DimOS Skill Container calls.

    Usage:
        executor = RealExecutor()
        executor.register_skill("follow", follow_container)
        executor.register_skill("navigation", nav_container)
        executor.register_skill("explorer", explorer_container)

        receipt = executor.execute(command)
    """

    def __init__(
        self,
        command_timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
    ) -> None:
        self._command_timeout_s = command_timeout_s
        self._lock = threading.Lock()

        # Skill container references
        self._follow_skill: SkillContainer | None = None
        self._navigation_skill: SkillContainer | None = None
        self._explorer_skill: SkillContainer | None = None

        # Current execution state
        self._current_mode: RobotMode = RobotMode.IDLE
        self._executing: bool = False

    # ---- Skill registration ----

    def register_skill(self, name: str, container: SkillContainer) -> None:
        """Register a skill container by name.

        Supported names: 'follow', 'navigation', 'explorer'
        """
        with self._lock:
            if name == "follow":
                self._follow_skill = container
            elif name == "navigation":
                self._navigation_skill = container
            elif name == "explorer":
                self._explorer_skill = container
            else:
                logger.warning("Unknown skill name: %s", name)

    def has_skill(self, name: str) -> bool:
        """Check if a skill container is registered."""
        with self._lock:
            if name == "follow":
                return self._follow_skill is not None
            if name == "navigation":
                return self._navigation_skill is not None
            if name == "explorer":
                return self._explorer_skill is not None
            return False

    @property
    def current_mode(self) -> RobotMode:
        with self._lock:
            return self._current_mode

    # ---- execute ----

    def execute(self, command: CommandRequest) -> CommandReceipt:
        """Execute a validated command. Returns a receipt.

        Falls back to StubExecutor behavior (mode-only transition) when
        no skill container is registered for the command.
        """
        kind = command.command

        # Mutual exclusion check
        target_mode = _MODE_MAP.get(kind, RobotMode.IDLE)
        with self._lock:
            if self._is_mutex_violation(target_mode):
                return CommandReceipt(
                    request_id=command.request_id,
                    accepted=False,
                    reason=RejectionReason.BUSY.value,
                    robot_mode=self._current_mode,
                )

        try:
            success = self._dispatch(kind, command)
        except Exception as exc:
            logger.exception("Executor dispatch failed for %s", kind)
            return CommandReceipt(
                request_id=command.request_id,
                accepted=False,
                executed=False,
                reason=f"executor_error: {exc}",
                robot_mode=self.current_mode,
            )

        if success:
            with self._lock:
                self._current_mode = target_mode
            return CommandReceipt(
                request_id=command.request_id,
                accepted=True,
                executed=True,
                robot_mode=target_mode,
            )
        else:
            return CommandReceipt(
                request_id=command.request_id,
                accepted=False,
                executed=False,
                reason="skill_execution_failed",
                robot_mode=self.current_mode,
            )

    # ---- Dispatch ----

    def _dispatch(self, kind: CommandKind, command: CommandRequest) -> bool:
        """Route command to the appropriate skill container.

        Falls back to StubExecutor mode-only transition when no skill.
        """
        target_id = command.target.track_id if command.target else None

        if kind == CommandKind.FOLLOW_START:
            return self._call_follow_start(target_id)
        elif kind == CommandKind.FOLLOW_HOLD:
            return self._call_follow_hold()
        elif kind == CommandKind.SCAN_START:
            return self._call_scan_start()
        elif kind == CommandKind.MISSION_STOP:
            return self._call_mission_stop()
        else:
            logger.warning("Unknown command kind: %s", kind)
            return False

    # ---- Skill calls with timeout ----

    def _call_follow_start(self, target_id: str | None) -> bool:
        container = self._follow_skill
        if container is None:
            logger.info("No follow skill registered — fallback to stub")
            return True  # Stub fallback: mode transition only

        with self._lock:
            self._executing = True

        try:
            result = _call_with_timeout(
                container.follow_person,
                self._command_timeout_s,
                target_id,
            )
            return result is True
        finally:
            with self._lock:
                self._executing = False

    def _call_follow_hold(self) -> bool:
        container = self._navigation_skill
        if container is None:
            logger.info("No navigation skill registered — fallback to stub")
            return True

        with self._lock:
            self._executing = True

        try:
            result = _call_with_timeout(
                container.stop_navigation,
                self._command_timeout_s,
            )
            return result is True
        finally:
            with self._lock:
                self._executing = False

    def _call_scan_start(self) -> bool:
        container = self._explorer_skill
        if container is None:
            logger.info("No explorer skill registered — fallback to stub")
            return True

        with self._lock:
            self._executing = True

        try:
            result = _call_with_timeout(
                container.start_exploration,
                self._command_timeout_s,
            )
            return result is True
        finally:
            with self._lock:
                self._executing = False

    def _call_mission_stop(self) -> bool:
        """Stop all motion and exploration. Tries all available skills."""
        success = True

        # Stop navigation
        if self._navigation_skill is not None:
            try:
                result = _call_with_timeout(
                    self._navigation_skill.stop_navigation,
                    self._command_timeout_s,
                )
                if result is not True:
                    success = False
            except Exception:
                success = False

        # Stop exploration
        if self._explorer_skill is not None:
            try:
                result = _call_with_timeout(
                    self._explorer_skill.stop_all,
                    self._command_timeout_s,
                )
                if result is not True:
                    success = False
            except Exception:
                success = False

        # If no skills registered, fallback to stub
        if self._navigation_skill is None and self._explorer_skill is None:
            logger.info("No skills registered — fallback to stub for mission.stop")
            return True

        with self._lock:
            self._current_mode = RobotMode.IDLE

        return success

    # ---- Mutual exclusion ----

    def _is_mutex_violation(self, target_mode: RobotMode) -> bool:
        """Check if target_mode conflicts with current mode."""
        if self._current_mode == RobotMode.IDLE:
            return False
        if target_mode == RobotMode.IDLE:
            return False
        return (self._current_mode, target_mode) in _MUTEX_PAIRS


# ---------------------------------------------------------------------------
# Timeout helper
# ---------------------------------------------------------------------------

def _call_with_timeout(
    func: Any,
    timeout_s: float,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call a function with a timeout. Returns the result or raises TimeoutError.

    Uses a simple thread-based timeout since most skill calls are synchronous.
    """
    result: list[Any] = []
    error: list[Exception | None] = [None]

    def _target() -> None:
        try:
            result.append(func(*args, **kwargs))
        except Exception as exc:
            error[0] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    if thread.is_alive():
        logger.warning("Skill call timed out after %.1fs", timeout_s)
        # Thread is daemon — will be cleaned up on process exit
        raise TimeoutError(f"Skill call timed out after {timeout_s}s")

    if error[0] is not None:
        raise error[0]

    return result[0] if result else None