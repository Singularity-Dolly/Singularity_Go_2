"""Command queue with TTL validation, safety gating, and priority bypass.

Guarantees:
- FIFO ordering within same priority
- Every command passes TTL check before enqueue AND before execution
- STOP command bypasses queue and TTL — always executed immediately
- SafetyController rejections are returned as rejection receipts
- Queue-full commands are rejected with QUEUE_FULL reason
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .contracts import (
    CommandKind,
    CommandReceipt,
    CommandRequest,
    RejectionReason,
    RobotMode,
)
from .safety import CommandGuard, SafetyController, attach_timestamp, get_timestamp


# ---------------------------------------------------------------------------
# Executor protocol
# ---------------------------------------------------------------------------

class CommandExecutor(Protocol):
    """Protocol for the robot command executor.

    The executor is injected so the queue is testable without hardware.
    """

    def execute(self, command: CommandRequest) -> CommandReceipt:
        """Execute a validated command on the robot.

        Returns a CommandReceipt with executed=True on success.
        """
        ...


# ---------------------------------------------------------------------------
# Enqueued command
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _Enqueued:
    command: CommandRequest
    enqueued_at_ms: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# CommandQueue
# ---------------------------------------------------------------------------

class CommandQueue:
    """Bounded command queue with safety gating.

    Lifecycle:
    1. enqueue(command) -> CommandReceipt
       - Validates TTL, safety, and queue capacity
       - Returns rejection receipt on failure
    2. process() iterates over pending commands
       - Re-checks TTL and safety before each execution
       - Calls executor.execute() for each valid command
    """

    DEFAULT_MAX_QUEUE: int = 8

    def __init__(
        self,
        executor: CommandExecutor,
        safety: SafetyController,
        max_queue: int = DEFAULT_MAX_QUEUE,
    ) -> None:
        self._executor = executor
        self._safety = safety
        self._max_queue = max_queue
        self._guard = CommandGuard()
        self._queue: deque[_Enqueued] = deque()
        self._lock = threading.Lock()
        self._current_mode: RobotMode = RobotMode.IDLE
        self._busy: bool = False

    # ---- Properties ----

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def current_mode(self) -> RobotMode:
        with self._lock:
            return self._current_mode

    @current_mode.setter
    def current_mode(self, mode: RobotMode) -> None:
        with self._lock:
            self._current_mode = mode

    # ---- Enqueue ----

    def enqueue(self, command: CommandRequest) -> CommandReceipt:
        """Enqueue a command for execution. Returns receipt immediately.

        STOP commands are executed synchronously, bypassing the queue.
        """
        # Only attach timestamp if not already set (allows tests to pre-set)
        if not hasattr(command, "created_at_ms"):
            attach_timestamp(command)

        # STOP is special — bypass queue, TTL, and safety check
        if command.command == CommandKind.MISSION_STOP:
            return self._execute_stop(command)

        # TTL check
        ttl_result = self._guard.validate(command)
        if not ttl_result.valid:
            return CommandReceipt(
                request_id=command.request_id,
                accepted=False,
                reason=ttl_result.reason,
                robot_mode=self.current_mode,
            )

        # Safety check
        can_accept, reason = self._safety.can_accept_command()
        if not can_accept:
            return CommandReceipt(
                request_id=command.request_id,
                accepted=False,
                reason=reason,
                robot_mode=self.current_mode,
            )

        # Queue capacity check
        with self._lock:
            count = len(self._queue)
            if count >= self._max_queue:
                return CommandReceipt(
                    request_id=command.request_id,
                    accepted=False,
                    reason=RejectionReason.QUEUE_FULL.value,
                    robot_mode=self._current_mode,
                )
            self._queue.append(_Enqueued(command=command))

        return CommandReceipt(
            request_id=command.request_id,
            accepted=True,
            robot_mode=self.current_mode,
        )

    # ---- Process ----

    def process(self) -> list[CommandReceipt]:
        """Process all pending commands in the queue.

        Returns a list of receipts, one per processed command.
        """
        receipts: list[CommandReceipt] = []

        while True:
            with self._lock:
                if not self._queue:
                    break
                if self._busy:
                    break
                item = self._queue.popleft()
                self._busy = True

            receipt = self._process_one(item)
            receipts.append(receipt)

            with self._lock:
                self._busy = False
                if receipt.executed:
                    self._current_mode = receipt.robot_mode

        return receipts

    def _process_one(self, item: _Enqueued) -> CommandReceipt:
        """Process a single enqueued command."""
        command = item.command

        # Re-check TTL (may have expired while waiting in queue)
        ttl_result = self._guard.validate(command)
        if not ttl_result.valid:
            return CommandReceipt(
                request_id=command.request_id,
                accepted=False,
                reason=ttl_result.reason,
                robot_mode=self.current_mode,
            )

        # Re-check safety
        can_accept, reason = self._safety.can_accept_command()
        if not can_accept:
            return CommandReceipt(
                request_id=command.request_id,
                accepted=False,
                reason=reason,
                robot_mode=self.current_mode,
            )

        # Execute
        try:
            receipt = self._executor.execute(command)
        except Exception as exc:
            return CommandReceipt(
                request_id=command.request_id,
                accepted=False,
                executed=False,
                reason=f"executor_error: {exc}",
                robot_mode=self.current_mode,
            )

        return receipt

    # ---- Stop bypass ----

    def _execute_stop(self, command: CommandRequest) -> CommandReceipt:
        """Execute STOP immediately. Bypasses all queues and TTL checks."""
        # Activate emergency stop
        self._safety.trigger_estop()

        # Clear all pending commands
        with self._lock:
            self._queue.clear()
            self._busy = False
            self._current_mode = RobotMode.IDLE

        # Execute stop on robot
        try:
            receipt = self._executor.execute(command)
        except Exception:
            receipt = CommandReceipt(
                request_id=command.request_id,
                accepted=True,
                executed=True,
                robot_mode=RobotMode.IDLE,
                reason="emergency_stop",
            )

        return receipt

    # ---- Drain ----

    def drain(self) -> list[CommandReceipt]:
        """Drain the queue, returning rejection receipts for all pending commands.

        Called during shutdown or emergency stop.
        """
        receipts: list[CommandReceipt] = []
        with self._lock:
            while self._queue:
                item = self._queue.popleft()
                receipts.append(
                    CommandReceipt(
                        request_id=item.command.request_id,
                        accepted=False,
                        reason=RejectionReason.ESTOP_ACTIVE.value,
                        robot_mode=self._current_mode,
                    )
                )
        return receipts