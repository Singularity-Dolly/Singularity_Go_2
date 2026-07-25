"""Reusable Go2Controller — UI/backend-agnostic robot control API."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
from typing import Any

from singularity_go2_console.adapter_protocol import FollowInit, Go2Adapter
from singularity_go2_console.config import Go2CtlConfig
from singularity_go2_console.events import EventBus, EventCallback, EventType
from singularity_go2_console.front_person import (
    TargetStabilityTracker,
    select_front_person,
)
from singularity_go2_console.logging_config import log_event
from singularity_go2_console.modes import require_transition
from singularity_go2_console.state import (
    ControllerMode,
    ControllerResult,
    ErrorCode,
    RobotStatus,
    VelocityOwner,
    utc_now_iso,
)
from singularity_go2_console.velocity_mux import VelocityMux
from singularity_go2_console.watchdog import Watchdogs

logger = logging.getLogger("go2ctl.controller")


class Go2Controller:
    """Single reusable controller for CLI and future robot-service API."""

    def __init__(
        self,
        adapter: Go2Adapter,
        config: Go2CtlConfig | None = None,
        *,
        clock: Any | None = None,
        allow_mock: bool = False,
    ) -> None:
        if adapter.mock and not allow_mock and not (config and config.mock):
            raise RuntimeError(
                "Refusing mock adapter in real mode. Pass allow_mock=True or config.mock=True."
            )
        self.config = config or Go2CtlConfig()
        self.adapter = adapter
        self._clock = clock or time.monotonic
        self._events = EventBus()
        self._lock = threading.RLock()
        self._mode = ControllerMode.DISCONNECTED
        self._estop = False
        self._robot_ip: str | None = None
        self._last_command: str | None = None
        self._last_error: str | None = None
        self._last_error_code: str | None = None
        self._target_bbox: list[float] | None = None
        self._target_confidence: float | None = None
        self._target_visible = False
        self._stability = TargetStabilityTracker(self.config.target_stable_frames)
        self._watchdogs = Watchdogs(
            camera_stale_ms=self.config.camera_stale_ms,
            manual_ttl_ms=self.config.manual_ttl_ms,
            clock=self._clock,
        )
        self._mux = VelocityMux(adapter)
        self._bg_task: asyncio.Task[None] | None = None
        self._stop_bg = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def subscribe(self, callback: EventCallback) -> None:
        self._events.subscribe(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        self._events.unsubscribe(callback)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _emit(self, event_type: EventType, **payload: Any) -> None:
        self._events.emit(event_type, **payload)

    def _set_error(self, code: ErrorCode, message: str) -> None:
        self._last_error = message
        self._last_error_code = code.value

    async def _settle(self) -> None:
        await asyncio.sleep(self.config.mode_settle_ms / 1000.0)

    async def _transition(
        self,
        target: ControllerMode,
        *,
        transfer_owner: VelocityOwner | None = None,
        stop_follow: bool = False,
    ) -> ControllerResult:
        with self._lock:
            current = self._mode
        check = require_transition(current, target)
        if not check.ok:
            return check

        # Movement ownership change: zero first
        zero = self._mux.force_zero()
        if not zero.ok:
            self._set_error(zero.code, zero.message)
            return zero

        if stop_follow and self.adapter.is_following():
            self.adapter.stop_follow()

        await self._settle()

        with self._lock:
            self._mode = target
            if transfer_owner is not None:
                self._mux.set_owner(transfer_owner)
            elif target in {ControllerMode.IDLE, ControllerMode.DISCONNECTED}:
                self._mux.set_owner(VelocityOwner.NONE)
            elif target == ControllerMode.ESTOP:
                self._mux.set_owner(VelocityOwner.ESTOP)

        log_event(
            "mode_transition",
            mode=target.value,
            robot_ip=self._robot_ip,
            result="ok",
            from_mode=current.value,
            to_mode=target.value,
        )
        self._emit(
            EventType.MODE_CHANGED,
            from_mode=current.value,
            to_mode=target.value,
        )
        return ControllerResult.success(
            message=f"{current.value} -> {target.value}",
            from_mode=current.value,
            to_mode=target.value,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self, robot_ip: str | None = None) -> ControllerResult:
        self._last_command = "connect"
        if not self.adapter.mock and self.config.mock is False:
            # Real mode must never silently use a mock adapter
            if getattr(self.adapter, "mock", False):
                return ControllerResult.failure(
                    ErrorCode.INTERNAL_ERROR,
                    "Real mode refused mock adapter",
                )

        mode = (getattr(self.config, "connection_mode", None) or "ap")
        mode = str(mode).lower()
        if mode == "sta" and not (robot_ip or self.config.robot_ip):
            return ControllerResult.failure(
                ErrorCode.STA_ROBOT_IP_REQUIRED,
                "STA mode requires --robot-ip",
            )

        tr = await self._transition(ControllerMode.CONNECTING)
        if not tr.ok and self._mode != ControllerMode.DISCONNECTED:
            # Allow DISCONNECTED -> CONNECTING only
            if self._mode != ControllerMode.CONNECTING:
                return tr

        # Force mode if coming from DISCONNECTED
        if self._mode == ControllerMode.DISCONNECTED:
            with self._lock:
                self._mode = ControllerMode.CONNECTING

        resolved_ip = robot_ip if robot_ip is not None else self.config.robot_ip
        if mode == "ap" and not resolved_ip:
            resolved_ip = "192.168.12.1"
        self._robot_ip = resolved_ip
        self._emit(EventType.ROBOT_CONNECTING, robot_ip=resolved_ip)
        log_event(
            "robot_connect_start",
            robot_ip=resolved_ip,
            mode=self._mode.value,
            connection_mode=mode,
        )

        try:
            ok, code, message = await self.adapter.connect(
                None if mode == "ap" else resolved_ip
            )
        except Exception as exc:  # noqa: BLE001 — boundary conversion
            await self._transition(ControllerMode.ERROR)
            self._set_error(ErrorCode.WEBRTC_CONNECTION_FAILED, str(exc))
            self._mux.force_zero()
            self._emit(EventType.ROBOT_ERROR, error=str(exc))
            return ControllerResult.failure(
                ErrorCode.WEBRTC_CONNECTION_FAILED,
                f"Connection exception: {exc}",
            )

        if not ok:
            err = ErrorCode[code] if code in ErrorCode.__members__ else ErrorCode.WEBRTC_CONNECTION_FAILED
            with self._lock:
                self._mode = ControllerMode.ERROR
            self._set_error(err, message)
            self._watchdogs.mark_connected(False)
            self._emit(EventType.ROBOT_ERROR, error=message, code=err.value)
            return ControllerResult.failure(err, message)

        self._watchdogs.mark_connected(True)
        frame = self.adapter.get_latest_frame()
        if frame is not None:
            self._watchdogs.mark_frame()
            self._emit(EventType.CAMERA_READY, frame_id=frame.frame_id)
            log_event("camera_first_frame", robot_ip=resolved_ip, mode="IDLE")

        with self._lock:
            self._mode = ControllerMode.IDLE
            self._mux.set_owner(VelocityOwner.NONE)
        self._emit(EventType.ROBOT_CONNECTED, robot_ip=resolved_ip)
        log_event("robot_connected", robot_ip=resolved_ip, mode="IDLE", result="ok")
        return ControllerResult.success(message=message, robot_ip=resolved_ip)

    async def disconnect(self) -> ControllerResult:
        self._last_command = "disconnect"
        self._mux.force_zero()
        if self.adapter.is_following():
            self.adapter.stop_follow()
        ok, code, message = await self.adapter.disconnect()
        self._watchdogs.mark_connected(False)
        with self._lock:
            self._mode = ControllerMode.DISCONNECTED
            self._mux.set_owner(VelocityOwner.NONE)
        self._emit(EventType.ROBOT_DISCONNECTED)
        if not ok:
            err = ErrorCode[code] if code in ErrorCode.__members__ else ErrorCode.CONNECTION_LOST
            return ControllerResult.failure(err, message)
        return ControllerResult.success(message=message)

    async def start_manual(self) -> ControllerResult:
        self._last_command = "manual.start"
        if self._estop:
            return ControllerResult.failure(ErrorCode.ESTOP_ACTIVE, "E-stop active")

        # Optional operator-gated switch to motion mode "normal" (never automatic).
        if getattr(self.config, "allow_normal_mode_switch", False):
            ensure = getattr(self.adapter, "ensure_normal_mode", None)
            if callable(ensure):
                ok, code, message = ensure()
                if not ok:
                    err = (
                        ErrorCode[code]
                        if code in ErrorCode.__members__
                        else ErrorCode.MOTION_MODE_NOT_NORMAL
                    )
                    self._set_error(err, message)
                    self._mux.force_zero()
                    return ControllerResult.failure(err, message)

        result = await self._transition(
            ControllerMode.MANUAL,
            transfer_owner=VelocityOwner.MANUAL,
            stop_follow=True,
        )
        if result.ok:
            self._watchdogs.clear_manual()
        return result

    async def set_manual_velocity(
        self,
        vx: float,
        vy: float,
        wz: float,
        ttl_ms: int | None = None,
    ) -> ControllerResult:
        self._last_command = "manual.velocity"
        if self._estop:
            return ControllerResult.failure(ErrorCode.ESTOP_ACTIVE, "E-stop active")
        if self._mode != ControllerMode.MANUAL:
            return ControllerResult.failure(
                ErrorCode.INVALID_MODE_TRANSITION,
                f"Manual velocity requires MANUAL mode (current={self._mode.value})",
            )
        if not self.adapter.velocity_ready:
            return ControllerResult.failure(
                ErrorCode.VELOCITY_OUTPUT_NOT_READY,
                "Velocity output not ready",
            )

        ttl = ttl_ms if ttl_ms is not None else self.config.manual_ttl_ms
        vx, vy, wz = self.config.clamp_velocity(vx, vy, wz)
        pub = self._mux.publish(VelocityOwner.MANUAL, vx, vy, wz)
        if pub.ok:
            self._watchdogs.mark_manual_command()
            # Store ttl for watchdog (already configured); mark time
            self._watchdogs.manual_ttl_ms = ttl
            self._emit(
                EventType.VELOCITY_COMMAND,
                owner="MANUAL",
                vx=vx,
                vy=vy,
                wz=wz,
                ttl_ms=ttl,
            )
            log_event(
                "manual_command",
                mode=self._mode.value,
                robot_ip=self._robot_ip,
                result="ok",
                vx=vx,
                vy=vy,
                wz=wz,
            )
        return pub

    async def start_follow_front_person(self) -> ControllerResult:
        self._last_command = "follow.start"
        if self._estop:
            return ControllerResult.failure(ErrorCode.ESTOP_ACTIVE, "E-stop active")
        if self.config.detect_only or self.config.tracker_only:
            # Motion blocked in debug modes after acquisition
            pass

        result = await self._transition(
            ControllerMode.ACQUIRING_TARGET,
            transfer_owner=VelocityOwner.NONE,
            stop_follow=True,
        )
        if not result.ok:
            return result

        self._stability.reset()
        self._emit(EventType.TARGET_ACQUIRING)
        log_event("target_acquiring", mode=self._mode.value, robot_ip=self._robot_ip)

        if not self.adapter.detector_ready:
            await self._transition(ControllerMode.IDLE)
            return ControllerResult.failure(
                ErrorCode.DETECTOR_NOT_READY,
                "Local person detector not ready",
            )

        deadline = time.monotonic() + self.config.acquire_timeout_s
        last_message = "No person found"

        while time.monotonic() < deadline and self._mode == ControllerMode.ACQUIRING_TARGET:
            frame = self.adapter.get_latest_frame()
            if frame is None:
                await asyncio.sleep(0.05)
                continue
            self._watchdogs.mark_frame()

            detections = self.adapter.detect_persons(frame)
            selection = select_front_person(
                detections,
                frame,
                confidence_threshold=self.config.detection_confidence,
                center_weight=self.config.center_weight,
                area_weight=self.config.area_weight,
                confidence_weight=self.config.confidence_weight,
                min_bbox_area_ratio=self.config.min_bbox_area_ratio,
            )
            log_event(
                "target_candidate",
                mode=self._mode.value,
                robot_ip=self._robot_ip,
                result=selection.code,
            )

            if selection.code == "AMBIGUOUS_TARGET":
                self._mux.force_zero()
                last_message = selection.message
                await asyncio.sleep(0.05)
                continue

            if not selection.ok and selection.code != "STABILIZING":
                last_message = selection.message
                self._stability.reset()
                await asyncio.sleep(0.05)
                continue

            stable = self._stability.update(selection)
            if not stable.ok:
                last_message = stable.message
                await asyncio.sleep(0.05)
                continue

            assert stable.person is not None and stable.frame is not None
            # Pair exact detection frame with bbox — never mix frames
            try:
                jpeg_b64 = self._encode_frame(stable.frame)
            except Exception as exc:  # noqa: BLE001
                self._mux.force_zero()
                await self._transition(ControllerMode.IDLE)
                self._set_error(ErrorCode.TRACKER_INIT_FAILED, str(exc))
                return ControllerResult.failure(
                    ErrorCode.TRACKER_INIT_FAILED,
                    f"Detection frame encode failed: {exc}",
                )
            init = FollowInit(
                query=self.config.follow_query,
                bbox=list(stable.person.detection.bbox),
                frame=stable.frame,
                jpeg_base64=jpeg_b64,
            )

            if self.config.detect_only:
                self._target_bbox = init.bbox
                self._target_confidence = stable.person.detection.confidence
                self._target_visible = True
                await self._transition(ControllerMode.IDLE)
                return ControllerResult.success(
                    message="detect-only: target selected, no motion",
                    bbox=init.bbox,
                    confidence=self._target_confidence,
                )

            if not self.adapter.follow_ready:
                await self._transition(ControllerMode.IDLE)
                return ControllerResult.failure(
                    ErrorCode.FOLLOW_SKILL_NOT_READY,
                    "Follow skill not ready",
                )

            ok, code, message = self.adapter.start_follow(init)
            if not ok:
                self._mux.force_zero()
                await self._transition(ControllerMode.IDLE)
                err = (
                    ErrorCode[code]
                    if code in ErrorCode.__members__
                    else ErrorCode.TRACKER_INIT_FAILED
                )
                self._set_error(err, message)
                return ControllerResult.failure(err, message)

            self._target_bbox = init.bbox
            self._target_confidence = stable.person.detection.confidence
            self._target_visible = True
            self._watchdogs.mark_target_ok()

            if self.config.tracker_only:
                # Tracker initialized but motion blocked
                self._mux.set_owner(VelocityOwner.NONE)
                self._mux.force_zero()
                with self._lock:
                    self._mode = ControllerMode.FOLLOWING
                self._emit(EventType.TARGET_ACQUIRED, bbox=init.bbox)
                return ControllerResult.success(
                    message="tracker-only: tracking without motion",
                    bbox=init.bbox,
                )

            follow_tr = await self._transition(
                ControllerMode.FOLLOWING,
                transfer_owner=VelocityOwner.FOLLOW,
            )
            if not follow_tr.ok:
                self.adapter.stop_follow()
                self._mux.force_zero()
                return follow_tr

            self._emit(EventType.TARGET_ACQUIRED, bbox=init.bbox)
            log_event(
                "target_acquired",
                mode="FOLLOWING",
                robot_ip=self._robot_ip,
                result="ok",
            )
            log_event("tracker_started", mode="FOLLOWING", robot_ip=self._robot_ip)
            return ControllerResult.success(message="following front person", bbox=init.bbox)

        await self._transition(ControllerMode.IDLE)
        return ControllerResult.failure(ErrorCode.NO_PERSON_FOUND, last_message)

    async def reacquire_front_person(self) -> ControllerResult:
        self._last_command = "follow.reacquire"
        if self._estop:
            return ControllerResult.failure(ErrorCode.ESTOP_ACTIVE, "E-stop active")
        self._mux.force_zero()
        if self.adapter.is_following():
            self.adapter.stop_follow()
        if self._mode == ControllerMode.FOLLOWING:
            # FOLLOWING -> ACQUIRING_TARGET allowed
            pass
        return await self.start_follow_front_person()

    async def stop_following(self) -> ControllerResult:
        self._last_command = "follow.stop"
        self._mux.force_zero()
        self.adapter.stop_follow()
        self._target_visible = False
        if self._mode in {
            ControllerMode.FOLLOWING,
            ControllerMode.ACQUIRING_TARGET,
        }:
            return await self._transition(
                ControllerMode.IDLE,
                transfer_owner=VelocityOwner.NONE,
            )
        return ControllerResult.success(message="follow already stopped")

    async def hold(self) -> ControllerResult:
        self._last_command = "hold"
        if self._estop:
            return ControllerResult.failure(ErrorCode.ESTOP_ACTIVE, "E-stop active; reset first")
        self._mux.force_zero()
        if self.adapter.is_following():
            self.adapter.stop_follow()
        self._watchdogs.clear_manual()
        if self._mode == ControllerMode.IDLE:
            return ControllerResult.success(message="already idle")
        if self._mode in {
            ControllerMode.MANUAL,
            ControllerMode.FOLLOWING,
            ControllerMode.ACQUIRING_TARGET,
        }:
            return await self._transition(ControllerMode.IDLE, transfer_owner=VelocityOwner.NONE)
        return ControllerResult.failure(
            ErrorCode.INVALID_MODE_TRANSITION,
            f"Cannot hold from {self._mode.value}",
        )

    async def emergency_stop(self, reason: str = "operator") -> ControllerResult:
        self._last_command = "estop"
        # Bypass queues, mode restrictions, and settle delays
        zero = self._mux.force_zero()
        self._mux.set_owner(VelocityOwner.ESTOP)
        if self.adapter.is_following():
            self.adapter.stop_follow()
        self._watchdogs.clear_manual()
        self._estop = True
        with self._lock:
            self._mode = ControllerMode.ESTOP
        self._emit(EventType.ESTOP_ACTIVATED, reason=reason)
        log_event(
            "estop",
            mode="ESTOP",
            robot_ip=self._robot_ip,
            result="ok" if zero.ok else "zero_failed",
            error_code=None if zero.ok else zero.code.value,
            reason=reason,
        )
        if not zero.ok:
            self._set_error(zero.code, zero.message)
            return ControllerResult.failure(
                zero.code,
                f"E-stop set but zero publish failed: {zero.message}",
                reason=reason,
            )
        return ControllerResult.success(message=f"emergency stop: {reason}")

    async def reset_estop(self) -> ControllerResult:
        self._last_command = "estop.reset"
        if not self._estop and self._mode != ControllerMode.ESTOP:
            return ControllerResult.failure(
                ErrorCode.INVALID_MODE_TRANSITION,
                "E-stop is not active",
            )
        self._mux.force_zero()
        self._estop = False
        result = await self._transition(ControllerMode.IDLE, transfer_owner=VelocityOwner.NONE)
        if result.ok:
            self._emit(EventType.ESTOP_RESET)
            log_event("estop_reset", mode="IDLE", robot_ip=self._robot_ip, result="ok")
        return result

    async def get_status(self) -> RobotStatus:
        frame_age = self._watchdogs.frame_age_ms()
        wd = self._watchdogs.status()
        last = self._mux.last
        with self._lock:
            mode = self._mode
        return RobotStatus(
            robot_id="go2",
            robot_ip=self._robot_ip,
            connected=self.adapter.connected,
            mode=mode.value,
            estop=self._estop,
            camera_ready=self.adapter.camera_ready,
            last_frame_age_ms=frame_age,
            detector_ready=self.adapter.detector_ready,
            target_visible=self._target_visible and self.adapter.follow_target_visible(),
            target_confidence=self._target_confidence,
            target_bbox=list(self._target_bbox) if self._target_bbox else None,
            target_lost_frames=self._watchdogs.target_lost_frames,
            velocity_owner=self._mux.owner.value,
            vx=last.vx,
            vy=last.vy,
            wz=last.wz,
            manual_watchdog_ok=wd.manual_ok,
            camera_watchdog_ok=wd.camera_ok,
            connection_watchdog_ok=wd.connection_ok,
            last_command=self._last_command,
            last_error=self._last_error,
            last_error_code=self._last_error_code,
            updated_at=utc_now_iso(),
            mock=bool(getattr(self.adapter, "mock", False)),
        )

    async def tick_watchdogs(self) -> None:
        """Periodic safety checks — call from console loop or background task."""
        if self._mode == ControllerMode.SHUTTING_DOWN:
            return

        # Keep connection watchdog aligned with adapter truth.
        self._watchdogs.mark_connected(bool(self.adapter.connected))

        if self._watchdogs.check_connection_lost() and self._mode not in {
            ControllerMode.DISCONNECTED,
            ControllerMode.CONNECTING,
            ControllerMode.ERROR,
            ControllerMode.ESTOP,
        }:
            self._mux.force_zero()
            with self._lock:
                self._mode = ControllerMode.ERROR
            self._set_error(ErrorCode.CONNECTION_LOST, "Connection lost")
            self._emit(EventType.ROBOT_ERROR, code="CONNECTION_LOST")
            return

        if self._mode == ControllerMode.MANUAL and self._watchdogs.check_manual_ttl_expired():
            self._mux.zero(VelocityOwner.MANUAL)
            self._watchdogs.clear_manual()
            self._emit(EventType.MANUAL_TTL_EXPIRED)
            self._emit(EventType.WATCHDOG_TIMEOUT, kind="manual_ttl")
            log_event(
                "manual_ttl_expired",
                mode=self._mode.value,
                robot_ip=self._robot_ip,
                result="zeroed",
            )

        if self._mode == ControllerMode.FOLLOWING:
            frame = self.adapter.get_latest_frame()
            # Only refresh camera watchdog when a newer frame arrives.
            if frame is not None:
                age_from_frame = (self._clock() - float(frame.timestamp_s)) * 1000.0
                if age_from_frame <= self.config.camera_stale_ms:
                    self._watchdogs.mark_frame()
                # else: leave last mark so check_camera_stale can trip

            if self._watchdogs.check_camera_stale():
                self._mux.force_zero()
                self.adapter.stop_follow()
                self._target_visible = False
                with self._lock:
                    self._mode = ControllerMode.IDLE
                self._mux.set_owner(VelocityOwner.NONE)
                self._set_error(ErrorCode.CAMERA_STALE, "Camera frame stale")
                self._emit(EventType.CAMERA_STALE)
                self._emit(EventType.WATCHDOG_TIMEOUT, kind="camera_stale")
                log_event(
                    "camera_stale",
                    mode="IDLE",
                    robot_ip=self._robot_ip,
                    result="follow_stopped",
                    error_code="CAMERA_STALE",
                )
                return

            if not self.adapter.follow_target_visible():
                lost = self._watchdogs.mark_target_lost_frame()
                self._mux.zero(VelocityOwner.FOLLOW)
                if lost > 15 or self._watchdogs.check_follow_lost():
                    self._mux.force_zero()
                    self.adapter.stop_follow()
                    self._target_visible = False
                    with self._lock:
                        self._mode = ControllerMode.IDLE
                    self._mux.set_owner(VelocityOwner.NONE)
                    self._emit(EventType.TARGET_LOST)
                    log_event(
                        "target_lost",
                        mode="IDLE",
                        robot_ip=self._robot_ip,
                        result="follow_stopped",
                    )
                return

            self._watchdogs.mark_target_ok()
            self._target_visible = True
            # Forward DimOS visual-servo command through the mux (FOLLOW owner only).
            if not self.config.tracker_only and not self.config.detect_only:
                pop = getattr(self.adapter, "pop_follow_velocity", None)
                if callable(pop):
                    cmd = pop()
                    if cmd is not None:
                        vx, vy, wz = self.config.clamp_velocity(*cmd)
                        self._mux.publish(VelocityOwner.FOLLOW, vx, vy, wz)

    async def shutdown(self) -> ControllerResult:
        self._last_command = "shutdown"
        self._mux.force_zero()
        if self.adapter.is_following():
            self.adapter.stop_follow()
        self._watchdogs.clear_manual()
        with self._lock:
            self._mode = ControllerMode.SHUTTING_DOWN
        self._emit(EventType.ROBOT_SHUTDOWN)
        log_event("shutdown", mode="SHUTTING_DOWN", robot_ip=self._robot_ip, result="ok")
        try:
            await self.adapter.disconnect()
        except Exception as exc:  # noqa: BLE001
            self._mux.force_zero()
            logger.exception("disconnect during shutdown failed: %s", exc)
        with self._lock:
            self._mode = ControllerMode.DISCONNECTED
            self._mux.set_owner(VelocityOwner.NONE)
        return ControllerResult.success(message="shutdown complete")

    def _encode_frame(self, frame: Any) -> str:
        encode = getattr(self.adapter, "encode_frame_jpeg_b64", None)
        if callable(encode):
            return str(encode(frame))
        # Minimal fallback: deterministic pairing token (DimOS adapter overrides)
        payload = f"frame:{frame.frame_id}:{frame.timestamp_s}".encode()
        return base64.b64encode(payload).decode("ascii")

    @property
    def mode(self) -> ControllerMode:
        return self._mode

    @property
    def mux(self) -> VelocityMux:
        return self._mux
