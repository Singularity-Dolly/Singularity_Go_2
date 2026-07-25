"""Strict single-owner velocity multiplexer."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from singularity_go2_console.state import ControllerResult, ErrorCode, VelocityOwner

logger = logging.getLogger("go2ctl.velocity_mux")


class VelocitySink(Protocol):
    def publish_velocity(self, vx: float, vy: float, wz: float) -> bool: ...

    def publish_zero(self) -> bool: ...


@dataclass(slots=True)
class VelocityCommand:
    vx: float
    vy: float
    wz: float
    owner: VelocityOwner


class VelocityMux:
    """Only one owner may publish non-zero velocity. ESTOP has highest priority."""

    def __init__(self, sink: VelocitySink) -> None:
        self._sink = sink
        self._lock = threading.RLock()
        self._owner = VelocityOwner.NONE
        self._last = VelocityCommand(0.0, 0.0, 0.0, VelocityOwner.NONE)
        self._rejected = 0

    @property
    def owner(self) -> VelocityOwner:
        with self._lock:
            return self._owner

    @property
    def last(self) -> VelocityCommand:
        with self._lock:
            return self._last

    @property
    def rejected_count(self) -> int:
        with self._lock:
            return self._rejected

    def set_owner(self, owner: VelocityOwner) -> None:
        with self._lock:
            self._owner = owner
            logger.info("velocity_owner_changed", extra={"owner": owner.value})

    def publish(
        self,
        owner: VelocityOwner,
        vx: float,
        vy: float,
        wz: float,
    ) -> ControllerResult:
        with self._lock:
            if self._owner == VelocityOwner.ESTOP and owner != VelocityOwner.ESTOP:
                self._rejected += 1
                logger.warning(
                    "velocity_rejected estop_active owner=%s attempted=%s",
                    self._owner.value,
                    owner.value,
                )
                return ControllerResult.failure(
                    ErrorCode.ESTOP_ACTIVE,
                    "E-stop active; velocity rejected",
                    owner=self._owner.value,
                    attempted_owner=owner.value,
                )

            if owner != self._owner and owner != VelocityOwner.ESTOP:
                self._rejected += 1
                logger.warning(
                    "velocity_rejected owner_mismatch active=%s attempted=%s",
                    self._owner.value,
                    owner.value,
                )
                return ControllerResult.failure(
                    ErrorCode.VELOCITY_REJECTED,
                    "Velocity rejected: producer is not active owner",
                    owner=self._owner.value,
                    attempted_owner=owner.value,
                )

            if owner == VelocityOwner.ESTOP:
                self._owner = VelocityOwner.ESTOP

            ok = self._sink.publish_velocity(vx, vy, wz)
            if not ok:
                code_name = getattr(self._sink, "last_velocity_error_code", None)
                message = getattr(
                    self._sink,
                    "last_velocity_error_message",
                    "Velocity sink failed to publish",
                )
                if code_name == ErrorCode.MOTION_MODE_NOT_NORMAL.value:
                    return ControllerResult.failure(
                        ErrorCode.MOTION_MODE_NOT_NORMAL,
                        str(message) or "Motion mode is not normal",
                    )
                if code_name == ErrorCode.MOTION_MODE_SWITCH_DISABLED.value:
                    return ControllerResult.failure(
                        ErrorCode.MOTION_MODE_SWITCH_DISABLED,
                        str(message) or "Normal mode switch disabled",
                    )
                return ControllerResult.failure(
                    ErrorCode.VELOCITY_OUTPUT_NOT_READY,
                    str(message) or "Velocity sink failed to publish",
                )
            self._last = VelocityCommand(vx, vy, wz, owner)
            return ControllerResult.success(
                message="velocity published",
                vx=vx,
                vy=vy,
                wz=wz,
                owner=owner.value,
            )

    def zero(self, owner: VelocityOwner | None = None) -> ControllerResult:
        """Publish zero. ESTOP or current owner may call; owner=None forces zero."""
        with self._lock:
            if owner is not None and owner not in {self._owner, VelocityOwner.ESTOP}:
                if self._owner != VelocityOwner.NONE:
                    self._rejected += 1
                    return ControllerResult.failure(
                        ErrorCode.VELOCITY_REJECTED,
                        "Zero rejected: producer is not active owner",
                        owner=self._owner.value,
                        attempted_owner=owner.value,
                    )
            ok = self._sink.publish_zero()
            if not ok:
                return ControllerResult.failure(
                    ErrorCode.VELOCITY_OUTPUT_NOT_READY,
                    "Velocity sink failed to publish zero",
                )
            active = owner or self._owner
            self._last = VelocityCommand(0.0, 0.0, 0.0, active)
            return ControllerResult.success(message="zero velocity", owner=active.value)

    def force_zero(self) -> ControllerResult:
        """Bypass ownership for emergency paths."""
        with self._lock:
            ok = self._sink.publish_zero()
            self._last = VelocityCommand(0.0, 0.0, 0.0, self._owner)
            if not ok:
                return ControllerResult.failure(
                    ErrorCode.VELOCITY_OUTPUT_NOT_READY,
                    "Emergency zero failed",
                )
            return ControllerResult.success(message="emergency zero")
