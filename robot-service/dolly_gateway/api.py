"""FastAPI router for robot-service HTTP/WebSocket API.

Endpoints:
  GET  /v1/health    — Health check with robot connection status
  GET  /v1/state     — Robot state snapshot (mode, target, scan, safety)
  GET  /v1/video     — MJPEG video stream
  POST /v1/commands  — Send a command (scan, follow, stop)
  POST /v1/stop      — Emergency stop (bypasses all queues)
  WS   /v1/events    — WebSocket event stream

All endpoints follow Nero Dolly ADX26 conventions:
- Strict JSON validation (Pydantic extra="forbid")
- Literal version strings ("v": "1.0")
- ISO 8601 UTC timestamps
- Error responses with {error: {code, message}}
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .command_queue import CommandQueue
from .contracts import (
    CommandKind,
    CommandReceipt,
    CommandRequest,
    ErrorDetail,
    ErrorResponse,
    EventEnvelope,
    EventType,
    HealthResponse,
    RejectionReason,
    RobotStateResponse,
    StopReceipt,
)
from .frame_provider import FrameProvider
from .safety import SafetyController
from .state_mapper import build_robot_state, build_robot_state_minimal


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/v1", tags=["robot-service"])


# ---------------------------------------------------------------------------
# Dependency: shared context
# ---------------------------------------------------------------------------

class GatewayContext:
    """Shared state injected into all route handlers.

    Populated by DollyGatewayModule during initialization.
    """

    def __init__(self) -> None:
        self.robot_id: str = "unknown"
        self.start_time: float = time.monotonic()
        self.safety: SafetyController | None = None
        self.frames: FrameProvider | None = None
        self.queue: CommandQueue | None = None
        # Callable that returns the latest DimOS state dict
        self.dimos_state: Any = None  # callable: () -> dict

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.start_time


# Module-level context singleton
_context = GatewayContext()


def get_context() -> GatewayContext:
    return _context


# ---------------------------------------------------------------------------
# GET /v1/health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
def health(ctx: GatewayContext = Depends(get_context)) -> HealthResponse:
    """Health check endpoint.

    Returns service status and robot connection state.
    Used by Nero Dolly for liveliness checks.
    """
    safety = ctx.safety
    safety_snapshot = safety.snapshot() if safety else None

    return HealthResponse(
        status="ok",
        robot_connected=safety_snapshot.heartbeat_ok if safety_snapshot else False,
        uptime_seconds=ctx.uptime_seconds,
    )


# ---------------------------------------------------------------------------
# GET /v1/state
# ---------------------------------------------------------------------------

@router.get("/state", response_model=RobotStateResponse)
def state(ctx: GatewayContext = Depends(get_context)) -> RobotStateResponse:
    """Robot state snapshot.

    Returns current mode, target tracking, scan progress, and safety status.
    """
    safety = ctx.safety
    safety_snapshot = safety.snapshot() if safety else None

    # Get DimOS state if available
    dimos_raw: dict[str, Any] = {}
    if ctx.dimos_state is not None:
        try:
            dimos_raw = ctx.dimos_state()
        except Exception:
            dimos_raw = {}

    from .contracts import SafetyState as SafetyStateModel

    ss = safety_snapshot or SafetyStateModel()

    if not dimos_raw:
        return build_robot_state_minimal(
            robot_id=ctx.robot_id,
            connected=ss.heartbeat_ok,
            safety_snapshot=ss,
        )

    return build_robot_state(
        robot_id=ctx.robot_id,
        dimos_state=dimos_raw,
        safety_snapshot=ss,
        start_time=ctx.start_time,
    )


# ---------------------------------------------------------------------------
# GET /v1/video
# ---------------------------------------------------------------------------

@router.get("/video")
async def video(
    fps: float = 10.0,
    ctx: GatewayContext = Depends(get_context),
) -> StreamingResponse:
    """MJPEG video stream.

    Returns a multipart/x-mixed-replace stream of JPEG frames.
    Clients (Nero Dolly, browsers) can consume this as an <img> src
    or via fetch/XMLHttpRequest.
    """
    if ctx.frames is None:
        raise HTTPException(status_code=503, detail="frame provider not initialized")

    async def generate() -> Any:
        async for chunk in ctx.frames.stream_mjpeg(fps=fps):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Robot-Id": ctx.robot_id,
        },
    )


# ---------------------------------------------------------------------------
# POST /v1/commands
# ---------------------------------------------------------------------------

@router.post("/commands", response_model=CommandReceipt)
def command(
    cmd: CommandRequest,
    ctx: GatewayContext = Depends(get_context),
) -> CommandReceipt:
    """Send a command to the robot.

    Supported commands:
    - scan.start: Begin room scanning
    - follow.start: Start following a person
    - follow.hold: Pause following (hold position)
    - mission.stop: Emergency stop (bypasses queue)

    Commands are validated for TTL, safety state, and queue capacity.
    Returns a receipt immediately; execution is asynchronous.
    """
    if ctx.queue is None:
        raise HTTPException(status_code=503, detail="command queue not initialized")

    receipt = ctx.queue.enqueue(cmd)

    if not receipt.accepted:
        raise HTTPException(
            status_code=409,
            detail=receipt.reason,
        )

    return receipt


# ---------------------------------------------------------------------------
# POST /v1/stop
# ---------------------------------------------------------------------------

@router.post("/stop", response_model=StopReceipt)
def stop(
    ctx: GatewayContext = Depends(get_context),
) -> StopReceipt:
    """Emergency stop. Bypasses all queues and TTL checks.

    This endpoint:
    - Activates the safety controller emergency stop
    - Drains the command queue
    - Returns immediately with a StopReceipt
    """
    if ctx.safety is None:
        raise HTTPException(status_code=503, detail="safety controller not initialized")

    ctx.safety.trigger_estop()

    if ctx.queue is not None:
        ctx.queue.drain()

    return StopReceipt(
        request_id=uuid.uuid4().hex[:8],
    )


# ---------------------------------------------------------------------------
# WS /v1/events
# ---------------------------------------------------------------------------

@router.websocket("/events")
async def events(
    websocket: WebSocket,
    ctx: GatewayContext = Depends(get_context),
) -> None:
    """WebSocket event stream.

    Pushes robot state changes, safety events, and command receipts
    to connected clients (Nero Dolly). Events are JSON-encoded
    EventEnvelope messages.

    The client receives a welcome event on connect, then state
    snapshots at ~5Hz.
    """
    await websocket.accept()

    # Send welcome event
    welcome = EventEnvelope(
        event_type=EventType.ROBOT_CONNECTED,
        source="robot-service",
        sequence=1,
        payload={"robot_id": ctx.robot_id, "message": "connected"},
    )
    await websocket.send_json(welcome.model_dump(mode="json"))

    sequence = 1
    try:
        while True:
            sequence += 1

            # Build state snapshot
            safety = ctx.safety
            safety_snapshot = safety.snapshot() if safety else None

            dimos_raw: dict[str, Any] = {}
            if ctx.dimos_state is not None:
                try:
                    dimos_raw = ctx.dimos_state()
                except Exception:
                    pass

            if dimos_raw and safety_snapshot:
                state_response = build_robot_state(
                    robot_id=ctx.robot_id,
                    dimos_state=dimos_raw,
                    safety_snapshot=safety_snapshot,
                    start_time=ctx.start_time,
                )
            else:
                state_response = build_robot_state_minimal(
                    robot_id=ctx.robot_id,
                    connected=safety_snapshot.heartbeat_ok if safety_snapshot else False,
                    safety_snapshot=safety_snapshot,
                )

            envelope = EventEnvelope(
                event_type=EventType.ROBOT_STATE,
                source="robot-service",
                sequence=sequence,
                payload=state_response.model_dump(mode="json"),
            )

            await websocket.send_json(envelope.model_dump(mode="json"))

            # Push at ~5Hz
            await asyncio.sleep(0.2)

    except WebSocketDisconnect:
        pass
    except Exception:
        # Client disconnected or other error — clean exit
        pass


# ---------------------------------------------------------------------------
# Error handler (registered on FastAPI app, not router)
# ---------------------------------------------------------------------------

async def http_exception_handler(request: Request, exc: HTTPException) -> Any:
    """Convert HTTPException to Nero-compatible error response."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=f"HTTP_{exc.status_code}",
                message=exc.detail,
            )
        ).model_dump(mode="json"),
    )