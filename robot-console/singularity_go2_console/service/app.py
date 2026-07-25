"""Authenticated FastAPI service exposing Go2Controller to Dolly HttpRobotGateway.

Endpoints:
  GET  /v1/health
  GET  /v1/state
  GET  /v1/frame.jpg
  POST /v1/commands
  POST /v1/stop

Browser clients must never call this service; Dolly FastAPI is the only caller.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse

from singularity_go2_console.aes import AesKeyError, redact_secrets
from singularity_go2_console.config import DEFAULT_AP_IP, Go2CtlConfig
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.service.auth import AuthDep, expected_token
from singularity_go2_console.service.contracts import (
    HealthResponse,
    RobotCommand,
    RobotReceipt,
    RobotState,
)
from singularity_go2_console.service.mapper import enrich_frame_meta, map_status_to_state
from singularity_go2_console.state import ControllerMode

logger = logging.getLogger("go2ctl.service")


class ServiceRuntime:
    def __init__(self, controller: Go2Controller, config: Go2CtlConfig) -> None:
        self.controller = controller
        self.config = config
        self.started_at = time.monotonic()
        self._scan_active = False
        self._holding = False
        self._last_command_at: float | None = None
        self._lock = asyncio.Lock()
        self._bg: asyncio.Task[None] | None = None
        self._stop_bg = asyncio.Event()

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.started_at

    async def start(self) -> None:
        self._stop_bg.clear()
        self._bg = asyncio.create_task(self._watchdog_loop(), name="go2-watchdogs")

    async def stop(self) -> None:
        self._stop_bg.set()
        if self._bg is not None:
            self._bg.cancel()
            try:
                await self._bg
            except asyncio.CancelledError:
                pass
        await self.controller.shutdown()

    async def _watchdog_loop(self) -> None:
        while not self._stop_bg.is_set():
            try:
                await self.controller.tick_watchdogs()
            except Exception:  # noqa: BLE001
                logger.exception("watchdog tick failed")
            await asyncio.sleep(0.05)

    async def build_state(self) -> RobotState:
        status = await self.controller.get_status()
        age = status.last_frame_age_ms
        frame = self.controller.adapter.get_latest_frame()
        frame_id = getattr(frame, "frame_id", None) if frame is not None else None
        width = getattr(frame, "width", None) if frame is not None else None
        height = getattr(frame, "height", None) if frame is not None else None
        stale_ms = self.config.camera_stale_ms
        frozen = bool(age is not None and age > stale_ms)
        last_age = None
        if self._last_command_at is not None:
            last_age = int((time.monotonic() - self._last_command_at) * 1000)
        state = map_status_to_state(
            status,
            robot_id=self.config.robot_id,
            frame_stale_ms=stale_ms,
            scan_active=self._scan_active,
            holding=self._holding,
            last_command_age_ms=last_age,
            obstacle_reported=False,  # never invent obstacles
        )
        return enrich_frame_meta(
            state,
            frame_id=int(frame_id) if frame_id is not None and not frozen else (
                int(frame_id) if frame_id is not None and frozen else None
            ),
            width=int(width) if width else None,
            height=int(height) if height else None,
            age_ms=int(age) if age is not None else None,
            frozen=frozen,
        )

    async def encode_jpeg(self) -> bytes | None:
        frame = self.controller.adapter.get_latest_frame()
        if frame is None:
            return None
        status = await self.controller.get_status()
        age = status.last_frame_age_ms
        if age is None or age > self.config.camera_stale_ms:
            return None
        # Prefer adapter helper when present.
        encode = getattr(self.controller.adapter, "encode_frame_jpeg_bytes", None)
        if callable(encode):
            data = encode(frame)
            if data:
                return data
        # Fallback via base64 helper then decode
        import base64

        b64 = getattr(self.controller.adapter, "encode_frame_jpeg_b64", None)
        if callable(b64):
            try:
                return base64.b64decode(b64(frame))
            except Exception:  # noqa: BLE001
                return None
        return None

    async def handle_command(self, command: RobotCommand) -> RobotReceipt:
        async with self._lock:
            self._last_command_at = time.monotonic()
            if command.is_expired():
                return RobotReceipt(
                    request_id=command.request_id,
                    accepted=False,
                    executed=False,
                    reason="ttl_expired",
                    robot_mode=self.controller.mode.value.lower(),
                )

            if command.command == "mission.stop":
                result = await self.controller.emergency_stop(reason="gateway_stop")
                self._scan_active = False
                self._holding = False
                return RobotReceipt(
                    request_id=command.request_id,
                    accepted=True,
                    executed=bool(result.ok),
                    reason=result.message or result.code.value,
                    robot_mode="estop",
                )

            if self.controller.mode == ControllerMode.ESTOP or self.controller._estop:  # noqa: SLF001
                return RobotReceipt(
                    request_id=command.request_id,
                    accepted=False,
                    executed=False,
                    reason="estop_active",
                    robot_mode="estop",
                )

            if command.command == "scan.start":
                # Detection-only scan: acquire front person without starting follow motion.
                previous_detect = self.config.detect_only
                self.config.detect_only = True
                try:
                    result = await self.controller.start_follow_front_person()
                finally:
                    self.config.detect_only = previous_detect
                # Immediately hold to avoid physical follow after scan.
                await self.controller.hold()
                self._scan_active = bool(result.ok)
                self._holding = True
                return RobotReceipt(
                    request_id=command.request_id,
                    accepted=bool(result.ok),
                    executed=bool(result.ok),
                    reason=result.message or result.code.value,
                    robot_mode="scanning" if result.ok else self.controller.mode.value.lower(),
                )

            if command.command == "follow.start":
                if not self.config.motion_enabled:
                    return RobotReceipt(
                        request_id=command.request_id,
                        accepted=False,
                        executed=False,
                        reason="robot_motion_disabled",
                        robot_mode=self.controller.mode.value.lower(),
                    )
                # Clamp any requested speeds into safety caps.
                if command.options.max_linear_mps is not None:
                    self.config.max_forward_speed = min(
                        0.15, float(command.options.max_linear_mps)
                    )
                if command.options.max_yaw_rps is not None:
                    self.config.max_angular_speed = min(
                        0.35, float(command.options.max_yaw_rps)
                    )
                result = await self.controller.start_follow_front_person()
                self._scan_active = False
                self._holding = False
                return RobotReceipt(
                    request_id=command.request_id,
                    accepted=bool(result.ok),
                    executed=bool(result.ok),
                    reason=result.message or result.code.value,
                    robot_mode="following" if result.ok else self.controller.mode.value.lower(),
                )

            if command.command == "follow.hold":
                result = await self.controller.hold()
                self._holding = bool(result.ok)
                self._scan_active = False
                return RobotReceipt(
                    request_id=command.request_id,
                    accepted=bool(result.ok),
                    executed=bool(result.ok),
                    reason=result.message or result.code.value,
                    robot_mode="holding" if result.ok else self.controller.mode.value.lower(),
                )

            return RobotReceipt(
                request_id=command.request_id,
                accepted=False,
                executed=False,
                reason="unknown_command",
                robot_mode=self.controller.mode.value.lower(),
            )


def _build_controller(config: Go2CtlConfig) -> Go2Controller:
    if config.mock:
        from singularity_go2_console.testing.fakes import FakeGo2Adapter

        return Go2Controller(FakeGo2Adapter(), config, allow_mock=True)

    from singularity_go2_console.dimos_adapter import DimOSGo2Adapter

    adapter = DimOSGo2Adapter(
        detector_model=config.detector_model,
        detection_confidence=config.detection_confidence,
        connection_mode=config.connection_mode,
        aes_key=config.aes_key.value if config.aes_key else None,
        allow_normal_mode_switch=config.allow_normal_mode_switch,
    )
    return Go2Controller(adapter, config, allow_mock=False)


def create_app(
    *,
    controller: Go2Controller | None = None,
    config: Go2CtlConfig | None = None,
    connect_on_startup: bool = True,
) -> FastAPI:
    cfg = config or Go2CtlConfig.from_environ(load_aes=True, require_aes=False)
    ctrl = controller
    runtime_holder: dict[str, ServiceRuntime] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal ctrl
        secrets: list[str] = []
        try:
            if cfg.aes_key is None and not cfg.mock:
                material = cfg.resolve_aes(require=True)
                if material is not None:
                    secrets.append(material.value)
            elif cfg.aes_key is not None:
                secrets.append(cfg.aes_key.value)
        except AesKeyError as exc:
            logger.error("AES key unavailable: %s", exc.code)
            raise

        if ctrl is None:
            ctrl = _build_controller(cfg)
        runtime = ServiceRuntime(ctrl, cfg)
        runtime_holder["runtime"] = runtime
        app.state.runtime = runtime

        if connect_on_startup and not cfg.mock:
            ip = cfg.robot_ip or (DEFAULT_AP_IP if cfg.connection_mode == "ap" else None)
            if not ip:
                raise RuntimeError("ROBOT_IP is required for STA mode")
            result = await ctrl.connect(ip)
            if not result.ok:
                msg = redact_secrets(result.message, secrets)
                logger.error("robot connect failed: %s %s", result.code.value, msg)
        elif connect_on_startup and cfg.mock:
            await ctrl.connect(cfg.robot_ip or "127.0.0.1")

        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="Singularity Go2 robot-service", lifespan=lifespan)

    def _runtime() -> ServiceRuntime:
        runtime = runtime_holder.get("runtime") or getattr(app.state, "runtime", None)
        if runtime is None:
            raise HTTPException(status_code=503, detail="service not ready")
        return runtime

    @app.get("/v1/health", response_model=HealthResponse)
    async def health(_: AuthDep) -> HealthResponse:
        runtime = _runtime()
        status = await runtime.controller.get_status()
        return HealthResponse(
            status="ok" if status.connected else "degraded",
            available=True,
            robot_connected=bool(status.connected),
            connection_mode=runtime.config.connection_mode,
            reason=None if status.connected else (status.last_error or "disconnected"),
            uptime_seconds=runtime.uptime,
        )

    @app.get("/v1/state", response_model=RobotState)
    async def state(_: AuthDep) -> RobotState:
        return await _runtime().build_state()

    @app.get("/v1/frame.jpg")
    async def frame(_: AuthDep) -> Response:
        data = await _runtime().encode_jpeg()
        if not data:
            raise HTTPException(status_code=503, detail="fresh robot frame unavailable")
        return Response(content=data, media_type="image/jpeg")

    @app.post("/v1/commands", response_model=RobotReceipt)
    async def commands(payload: RobotCommand, _: AuthDep) -> RobotReceipt:
        return await _runtime().handle_command(payload)

    @app.post("/v1/stop", response_model=RobotReceipt)
    async def stop(payload: RobotCommand | None = None, _auth: AuthDep = None) -> RobotReceipt:
        # Dedicated stop path — independent of planner / command queue.
        command = payload or RobotCommand(
            command="mission.stop",
            target={"kind": "none"},  # type: ignore[arg-type]
        )
        if command.command != "mission.stop":
            command = command.model_copy(update={"command": "mission.stop"})
        return await _runtime().handle_command(command)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        # Unauthenticated liveness only — no robot secrets or telemetry.
        return {"ok": True, "auth_configured": bool(expected_token())}

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("ROBOT_SERVICE_HOST", os.environ.get("GATEWAY_HOST", "127.0.0.1"))
    port = int(os.environ.get("ROBOT_SERVICE_PORT", os.environ.get("GATEWAY_PORT", "8780")))
    uvicorn.run(
        "singularity_go2_console.service.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=os.environ.get("GO2CTL_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
