"""Explicit mode transition helper."""

from __future__ import annotations

from singularity_go2_console.state import (
    ALLOWED_TRANSITIONS,
    ControllerMode,
    ControllerResult,
    ErrorCode,
)


def can_transition(current: ControllerMode, target: ControllerMode) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def require_transition(
    current: ControllerMode,
    target: ControllerMode,
) -> ControllerResult:
    if current == target:
        return ControllerResult.success(
            message=f"already in {current.value}",
            mode=current.value,
        )
    if not can_transition(current, target):
        return ControllerResult.failure(
            ErrorCode.INVALID_MODE_TRANSITION,
            f"Invalid transition {current.value} -> {target.value}",
            from_mode=current.value,
            to_mode=target.value,
        )
    return ControllerResult.success(
        message=f"{current.value} -> {target.value}",
        from_mode=current.value,
        to_mode=target.value,
    )
