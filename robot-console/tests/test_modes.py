"""Unit tests for mode transitions."""

from __future__ import annotations

from singularity_go2_console.modes import can_transition, require_transition
from singularity_go2_console.state import ControllerMode, ErrorCode


def test_allowed_basic_transitions() -> None:
    assert can_transition(ControllerMode.DISCONNECTED, ControllerMode.CONNECTING)
    assert can_transition(ControllerMode.CONNECTING, ControllerMode.IDLE)
    assert can_transition(ControllerMode.IDLE, ControllerMode.MANUAL)
    assert can_transition(ControllerMode.IDLE, ControllerMode.ACQUIRING_TARGET)
    assert can_transition(ControllerMode.ACQUIRING_TARGET, ControllerMode.FOLLOWING)
    assert can_transition(ControllerMode.FOLLOWING, ControllerMode.MANUAL)
    assert can_transition(ControllerMode.ESTOP, ControllerMode.IDLE)
    assert can_transition(ControllerMode.ERROR, ControllerMode.IDLE)
    assert can_transition(ControllerMode.SHUTTING_DOWN, ControllerMode.DISCONNECTED)


def test_rejects_invalid_transitions() -> None:
    assert not can_transition(ControllerMode.DISCONNECTED, ControllerMode.FOLLOWING)
    assert not can_transition(ControllerMode.ESTOP, ControllerMode.MANUAL)
    assert not can_transition(ControllerMode.ESTOP, ControllerMode.FOLLOWING)
    assert not can_transition(ControllerMode.ERROR, ControllerMode.FOLLOWING)
    result = require_transition(ControllerMode.ESTOP, ControllerMode.MANUAL)
    assert not result.ok
    assert result.code == ErrorCode.INVALID_MODE_TRANSITION
