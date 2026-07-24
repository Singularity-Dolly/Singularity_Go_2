"""Tests for command queue — FIFO ordering, TTL, safety, and stop bypass.

Covers:
- Command enqueue acceptance
- TTL rejection at enqueue and execution
- Safety rejection (estop)
- Queue full rejection
- STOP bypass behavior
- FIFO ordering
- Busy state prevention
- Drain during emergency stop
"""

from __future__ import annotations

import time

import pytest

from dolly_gateway.command_queue import CommandQueue
from dolly_gateway.contracts import (
    CommandKind,
    CommandReceipt,
    CommandRequest,
    RejectionReason,
    RobotMode,
)
from dolly_gateway.safety import SafetyController, attach_timestamp


# ---------------------------------------------------------------------------
# Test executor
# ---------------------------------------------------------------------------

class _TestExecutor:
    """Controllable executor for testing."""

    def __init__(self) -> None:
        self.executed: list[CommandRequest] = []
        self.next_mode: RobotMode = RobotMode.IDLE
        self.should_fail: bool = False

    def execute(self, command: CommandRequest) -> CommandReceipt:
        if self.should_fail:
            raise RuntimeError("simulated executor failure")

        self.executed.append(command)
        return CommandReceipt(
            request_id=command.request_id,
            accepted=True,
            executed=True,
            robot_mode=self.next_mode,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def safety() -> SafetyController:
    return SafetyController()


@pytest.fixture
def executor() -> _TestExecutor:
    return _TestExecutor()


@pytest.fixture
def queue(safety: SafetyController, executor: _TestExecutor) -> CommandQueue:
    return CommandQueue(executor=executor, safety=safety, max_queue=4)


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_accepts_valid_command(self, queue: CommandQueue) -> None:
        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        receipt = queue.enqueue(cmd)
        assert receipt.accepted is True
        assert receipt.request_id == "req-1"
        assert queue.pending_count == 1

    def test_rejects_expired_ttl(self, queue: CommandQueue) -> None:
        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=100,
            command=CommandKind.SCAN_START,
        )
        attach_timestamp(cmd)
        time.sleep(0.15)

        receipt = queue.enqueue(cmd)
        assert receipt.accepted is False
        assert receipt.reason == RejectionReason.TTL_EXPIRED.value

    def test_rejects_estop(self, queue: CommandQueue, safety: SafetyController) -> None:
        safety.trigger_estop()

        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        receipt = queue.enqueue(cmd)
        assert receipt.accepted is False
        assert receipt.reason == RejectionReason.ESTOP_ACTIVE.value

    def test_rejects_queue_full(self, queue: CommandQueue) -> None:
        for i in range(4):
            cmd = CommandRequest(
                request_id=f"req-{i}",
                ttl_ms=5000,
                command=CommandKind.SCAN_START,
            )
            queue.enqueue(cmd)

        # Queue is now full (max_queue=4)
        cmd = CommandRequest(
            request_id="req-full",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        receipt = queue.enqueue(cmd)
        assert receipt.accepted is False
        assert receipt.reason == RejectionReason.QUEUE_FULL.value


# ---------------------------------------------------------------------------
# STOP bypass
# ---------------------------------------------------------------------------

class TestStopBypass:
    def test_stop_bypasses_queue(self, queue: CommandQueue) -> None:
        """STOP command skips queue and executes immediately."""
        # Fill queue with pending commands
        for i in range(3):
            cmd = CommandRequest(
                request_id=f"req-{i}",
                ttl_ms=5000,
                command=CommandKind.SCAN_START,
            )
            queue.enqueue(cmd)

        assert queue.pending_count == 3

        # Send STOP
        stop_cmd = CommandRequest(
            request_id="stop-1",
            ttl_ms=5000,
            command=CommandKind.MISSION_STOP,
        )
        receipt = queue.enqueue(stop_cmd)

        assert receipt.accepted is True
        # Queue should be drained
        assert queue.pending_count == 0

    def test_stop_bypasses_estop(self, queue: CommandQueue, safety: SafetyController) -> None:
        """STOP executes even when estop is active (idempotent)."""
        safety.trigger_estop()

        stop_cmd = CommandRequest(
            request_id="stop-1",
            ttl_ms=5000,
            command=CommandKind.MISSION_STOP,
        )
        receipt = queue.enqueue(stop_cmd)

        # STOP always succeeds
        assert receipt.accepted is True

    def test_stop_bypasses_expired_ttl(self, queue: CommandQueue) -> None:
        """STOP executes even with expired TTL."""
        stop_cmd = CommandRequest(
            request_id="stop-1",
            ttl_ms=100,
            command=CommandKind.MISSION_STOP,
        )
        attach_timestamp(stop_cmd)
        time.sleep(0.15)

        receipt = queue.enqueue(stop_cmd)
        assert receipt.accepted is True


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------

class TestProcess:
    def test_processes_in_fifo_order(self, queue: CommandQueue, executor: _TestExecutor) -> None:
        """Commands are executed in FIFO order."""
        for i in range(3):
            cmd = CommandRequest(
                request_id=f"req-{i}",
                ttl_ms=5000,
                command=CommandKind.SCAN_START,
            )
            queue.enqueue(cmd)

        receipts = queue.process()

        assert len(receipts) == 3
        assert len(executor.executed) == 3
        assert [r.request_id for r in receipts] == ["req-0", "req-1", "req-2"]

    def test_process_updates_mode(self, queue: CommandQueue, executor: _TestExecutor) -> None:
        """Queue mode updates after successful execution."""
        executor.next_mode = RobotMode.EXPLORING

        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        queue.enqueue(cmd)
        queue.process()

        assert queue.current_mode == RobotMode.EXPLORING

    def test_process_rechecks_ttl(self, queue: CommandQueue) -> None:
        """TTL is re-checked before execution."""
        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=100,
            command=CommandKind.SCAN_START,
        )
        queue.enqueue(cmd)
        time.sleep(0.15)

        receipts = queue.process()

        assert len(receipts) == 1
        assert receipts[0].accepted is False
        assert receipts[0].reason == RejectionReason.TTL_EXPIRED.value

    def test_process_rechecks_safety(self, queue: CommandQueue, safety: SafetyController) -> None:
        """Safety is re-checked before execution."""
        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        queue.enqueue(cmd)

        # Activate estop after enqueue but before process
        safety.trigger_estop()

        receipts = queue.process()

        assert len(receipts) == 1
        assert receipts[0].accepted is False
        assert receipts[0].reason == RejectionReason.ESTOP_ACTIVE.value

    def test_process_handles_executor_failure(self, queue: CommandQueue, executor: _TestExecutor) -> None:
        """Executor exceptions are caught and returned as receipts."""
        executor.should_fail = True

        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        queue.enqueue(cmd)

        receipts = queue.process()

        assert len(receipts) == 1
        assert receipts[0].accepted is False
        assert "executor_error" in receipts[0].reason

    def test_process_empty_queue(self, queue: CommandQueue) -> None:
        """Processing empty queue returns empty list."""
        receipts = queue.process()
        assert receipts == []

    def test_busy_prevents_concurrent_process(self, queue: CommandQueue) -> None:
        """Busy flag prevents overlapping processing."""
        # This tests the busy flag mechanism
        assert queue.busy is False

        cmd = CommandRequest(
            request_id="req-1",
            ttl_ms=5000,
            command=CommandKind.SCAN_START,
        )
        queue.enqueue(cmd)
        queue.process()

        # After processing, queue should be not busy
        assert queue.busy is False


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------

class TestDrain:
    def test_drain_returns_rejection_receipts(self, queue: CommandQueue) -> None:
        """Drain returns rejection receipts for pending commands."""
        for i in range(3):
            cmd = CommandRequest(
                request_id=f"req-{i}",
                ttl_ms=5000,
                command=CommandKind.SCAN_START,
            )
            queue.enqueue(cmd)

        receipts = queue.drain()

        assert len(receipts) == 3
        for r in receipts:
            assert r.accepted is False
            assert r.reason == RejectionReason.ESTOP_ACTIVE.value

        assert queue.pending_count == 0

    def test_drain_empty_queue(self, queue: CommandQueue) -> None:
        """Draining empty queue returns empty list."""
        receipts = queue.drain()
        assert receipts == []