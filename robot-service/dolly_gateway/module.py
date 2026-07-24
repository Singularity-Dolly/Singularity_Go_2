"""DollyGatewayModule — DimOS module bridging robot state to Nero Dolly.

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │  DollyGatewayModule (DimOS Module)                      │
  │  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐ │
  │  │ Safety   │  │ Command  │  │  FastAPI (:8780)       │ │
  │  │ Controller│  │ Queue    │  │  GET /v1/health        │ │
  │  │          │  │          │  │  GET /v1/state         │ │
  │  │ Heartbeat│  │ Executor │  │  GET /v1/video         │ │
  │  │ Watchdog │  │          │  │  POST /v1/commands     │ │
  │  │          │  │          │  │  POST /v1/stop         │ │
  │  │ Frame    │  │          │  │  WS  /v1/events        │ │
  │  │ Guard    │  │          │  │                       │ │
  │  └──────────┘  └──────────┘  └───────────────────────┘ │
  │                                                         │
  │  ┌──────────────────────────────────────────────────┐   │
  │  │  DimOS State Bridge                              │   │
  │  │  state_mapper.build_robot_state()                │   │
  │  │  ← GO2Connection, PersonTracker, VoxelGridMapper │   │
  │  └──────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────┘

Lifecycle:
  __init__()  → create all components (no side effects)
  start()     → start watchdog, HTTP server, camera loop
  stop()      → graceful shutdown, drain queue
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .api import GatewayContext, get_context, http_exception_handler, router
from .command_queue import CommandExecutor, CommandQueue
from .contracts import (
    CommandKind,
    CommandReceipt,
    CommandRequest,
    RobotMode,
)
from .frame_provider import FrameProvider
from .safety import HeartbeatWatchdog, SafetyController

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default executor (stub — replaced by DimOS integration)
# ---------------------------------------------------------------------------

class StubExecutor(CommandExecutor):
    """Stub executor for testing without hardware.

    Maps commands to robot mode transitions without actually
    controlling the robot. Replaced by a real executor when
    DimOS skills are available.
    """

    def execute(self, command: CommandRequest) -> CommandReceipt:
        mode_map: dict[CommandKind, RobotMode] = {
            CommandKind.SCAN_START: RobotMode.EXPLORING,
            CommandKind.FOLLOW_START: RobotMode.FOLLOWING,
            CommandKind.FOLLOW_HOLD: RobotMode.IDLE,
            CommandKind.MISSION_STOP: RobotMode.IDLE,
        }
        new_mode = mode_map.get(command.command, RobotMode.IDLE)
        return CommandReceipt(
            request_id=command.request_id,
            accepted=True,
            executed=True,
            robot_mode=new_mode,
        )


# ---------------------------------------------------------------------------
# DollyGatewayModule
# ---------------------------------------------------------------------------

class DollyGatewayModule:
    """Main DimOS module for the robot-service HTTP/WebSocket gateway.

    Usage:
        module = DollyGatewayModule(robot_id="go2-001", port=8780)
        await module.start()
        # ... service running ...
        await module.stop()
    """

    def __init__(
        self,
        robot_id: str = "unknown",
        port: int = 8780,
        host: str = "0.0.0.0",
        executor: CommandExecutor | None = None,
    ) -> None:
        self.robot_id = robot_id
        self.port = port
        self.host = host

        # Safety layer
        self.safety = SafetyController()
        self.watchdog = HeartbeatWatchdog(
            estop_callback=self._on_heartbeat_timeout,
        )

        # Frame provider
        self.frames = FrameProvider()

        # Command queue
        self.queue = CommandQueue(
            executor=executor or StubExecutor(),
            safety=self.safety,
        )

        # HTTP server
        self._server: asyncio.AbstractEventLoop | None = None
        self._server_task: asyncio.Task | None = None
        self._running = False

        # DimOS state bridge (populated during DimOS integration)
        self._dimos_state_provider: Any = None

        # Populate gateway context
        ctx = get_context()
        ctx.robot_id = self.robot_id
        ctx.safety = self.safety
        ctx.frames = self.frames
        ctx.queue = self.queue
        ctx.dimos_state = self._get_dimos_state
        ctx.start_time = time.monotonic()

    # ---- Lifecycle ----

    async def start(self) -> None:
        """Start all services: watchdog, HTTP server, command processor."""
        if self._running:
            logger.warning("DollyGatewayModule already running")
            return

        self._running = True
        self.watchdog.start()

        # Start HTTP server in a background task
        self._server_task = asyncio.create_task(self._run_server())

        # Start command processor loop
        asyncio.create_task(self._process_commands())

        logger.info(
            "DollyGatewayModule started on %s:%d (robot=%s)",
            self.host,
            self.port,
            self.robot_id,
        )

    async def stop(self) -> None:
        """Graceful shutdown: stop processing, drain queue, close server."""
        if not self._running:
            return

        self._running = False
        self.watchdog.stop()

        # Drain pending commands
        self.queue.drain()

        # Cancel server task
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await self._server_task

        logger.info("DollyGatewayModule stopped")

    # ---- DimOS state bridge ----

    def set_dimos_state_provider(self, provider: Any) -> None:
        """Register a callable that returns the current DimOS state dict.

        Called by DimOS integration layer to connect module state
        to the HTTP API.
        """
        self._dimos_state_provider = provider

    def _get_dimos_state(self) -> dict[str, Any]:
        """Return the current DimOS module state dict."""
        if self._dimos_state_provider is None:
            return {}
        try:
            return self._dimos_state_provider()
        except Exception:
            return {}

    # ---- Heartbeat ----

    def heartbeat(self) -> None:
        """Record a heartbeat from the robot connection layer."""
        self.watchdog.heartbeat()
        self.safety.update_heartbeat(True)

    def _on_heartbeat_timeout(self) -> None:
        """Called when heartbeat watchdog detects a timeout."""
        self.safety.trigger_estop()
        self.safety.update_heartbeat(False)
        self.queue.drain()
        logger.critical(
            "Heartbeat timeout — emergency stop activated (robot=%s)",
            self.robot_id,
        )

    # ---- Frame ingestion ----

    def on_frame(self, jpeg_bytes: bytes) -> None:
        """Ingest a new camera frame. Called by the camera capture thread."""
        self.frames.update(jpeg_bytes)

    # ---- Internal ----

    async def _run_server(self) -> None:
        """Start the FastAPI/Uvicorn server."""
        import uvicorn

        from fastapi import FastAPI
        from fastapi.exceptions import HTTPException
        from fastapi.middleware.cors import CORSMiddleware

        app = FastAPI(
            title="robot-service",
            version="1.0.0",
            docs_url=None,
            redoc_url=None,
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        app.add_exception_handler(HTTPException, http_exception_handler)
        app.include_router(router)

        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)

        try:
            await server.serve()
        except asyncio.CancelledError:
            await server.shutdown()

    async def _process_commands(self) -> None:
        """Background loop that processes the command queue."""
        while self._running:
            try:
                receipts = self.queue.process()
                for receipt in receipts:
                    if receipt.executed:
                        logger.info(
                            "Command executed: %s → %s",
                            receipt.request_id,
                            receipt.robot_mode.value,
                        )
                    else:
                        logger.warning(
                            "Command rejected: %s (%s)",
                            receipt.request_id,
                            receipt.reason,
                        )
            except Exception:
                logger.exception("Command processor error")

            await asyncio.sleep(0.05)  # ~20Hz processing


# ---------------------------------------------------------------------------
# DimOS Module entry point
# ---------------------------------------------------------------------------

def create_module(
    robot_id: str = "unknown",
    port: int = 8780,
    host: str = "0.0.0.0",
) -> DollyGatewayModule:
    """Factory function for DimOS module discovery.

    Called by DimOS module loader to instantiate the gateway.
    """
    return DollyGatewayModule(robot_id=robot_id, port=port, host=host)