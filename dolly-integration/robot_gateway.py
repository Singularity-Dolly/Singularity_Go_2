"""RobotGatewayClient — Dolly's HTTP/WebSocket client for robot-service.

Connects to the Singularity Go2 robot-service (port 8780) and bridges:
- Robot state → Dolly StateStore (engine section)
- Robot events → Dolly EventBus (ROBOT_* event types)
- Robot commands → robot-service REST API
- Robot video → (future) frame broker

Design principles:
- Graceful degradation: if robot is unreachable, Dolly runs normally
- Auto-reconnect: exponential backoff on connection loss
- No fake data: only forward what the robot actually reports
- Follows Dolly ADX26 conventions: EventBus, StateStore, pydantic-settings
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from .contracts import EventType

logger = logging.getLogger("dolly.robot_gateway")


# ---------------------------------------------------------------------------
# Robot state shape (mirrors robot-service RobotStateResponse)
# ---------------------------------------------------------------------------

_ROBOT_STATE_TEMPLATE: dict[str, Any] = {
    "robot_id": "unknown",
    "connected": False,
    "mode": "idle",
    "target": {"locked": False, "track_id": None, "confidence": None},
    "scan": {"active": False, "path": [], "map_source": "unavailable"},
    "safety": {"estop": False, "obstacle": False, "heartbeat_ok": False, "last_command_age_ms": 0},
}


class RobotGatewayClient:
    """HTTP/WebSocket client for the robot-service API.

    Lifecycle:
        client = RobotGatewayClient(settings, bus, state)
        await client.start()   # begin polling + WebSocket
        await client.stop()    # graceful shutdown

    Public API:
        send_command(kind, target, ttl_ms) → CommandReceipt dict
        emergency_stop() → StopReceipt dict
        get_robot_state() → dict (cached snapshot)
    """

    def __init__(
        self,
        base_url: str,
        bus: Any,  # EventBus (avoid circular import)
        state: Any,  # StateStore (avoid circular import)
        auth_token: str | None = None,
        health_interval_s: float = 5.0,
        state_interval_s: float = 0.5,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 30.0,
        request_timeout_s: float = 10.0,
        enabled: bool = True,
    ) -> None:
        """Initialize the robot gateway client.

        Args:
            base_url: Robot-service base URL (e.g. "http://192.168.123.15:8780").
            bus: Dolly EventBus instance.
            state: Dolly StateStore instance.
            auth_token: Optional Bearer token for robot API authentication.
            health_interval_s: Seconds between health checks.
            state_interval_s: Seconds between state polls.
            reconnect_base_s: Base seconds for exponential backoff.
            reconnect_max_s: Maximum seconds between reconnect attempts.
            request_timeout_s: HTTP request timeout in seconds.
            enabled: If False, all operations are no-ops (graceful degradation).
        """
        self._base_url = base_url.rstrip("/")
        self._bus = bus
        self._state = state
        self._auth_token = auth_token
        self._health_interval = health_interval_s
        self._state_interval = state_interval_s
        self._reconnect_base = reconnect_base_s
        self._reconnect_max = reconnect_max_s
        self._request_timeout = request_timeout_s
        self._enabled = enabled

        # Cached robot state (thread-safe via asyncio.Lock)
        self._robot_state: dict[str, Any] = dict(_ROBOT_STATE_TEMPLATE)
        self._lock = asyncio.Lock()

        # Lifecycle
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._http: httpx.AsyncClient | None = None
        self._ws: Any = None
        self._sequence: int = 0

        # Connection tracking
        self._connected = False
        self._last_health: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start background tasks: health polling, state polling, WebSocket events."""
        if not self._enabled:
            logger.info("RobotGatewayClient is disabled — skipping start")
            return

        self._running = True
        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(self._request_timeout),
        )

        self._tasks = [
            asyncio.create_task(self._health_loop(), name="robot-health"),
            asyncio.create_task(self._state_loop(), name="robot-state"),
            asyncio.create_task(self._ws_event_loop(), name="robot-ws"),
        ]
        logger.info("RobotGatewayClient started (base_url=%s)", self._base_url)

    async def stop(self) -> None:
        """Graceful shutdown: cancel tasks, close connections."""
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

        if self._ws is not None:
            with __import__("contextlib").suppress(Exception):
                await self._ws.close()
            self._ws = None

        if self._http is not None:
            await self._http.aclose()
            self._http = None

        await self._set_connected(False)
        logger.info("RobotGatewayClient stopped")

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _health_loop(self) -> None:
        """Poll GET /v1/health every _health_interval seconds."""
        while self._running:
            try:
                assert self._http is not None
                resp = await self._http.get("/v1/health")
                if resp.status_code == 200:
                    data = resp.json()
                    self._last_health = data
                    was_connected = self._connected
                    is_connected = bool(data.get("robot_connected"))
                    await self._set_connected(is_connected)
                    if is_connected and not was_connected:
                        await self._publish(EventType.ROBOT_CONNECTED, data)
                    elif not is_connected and was_connected:
                        await self._publish(EventType.ROBOT_DISCONNECTED, data)
                    await self._publish(EventType.ROBOT_HEALTH, data)
                else:
                    await self._set_connected(False)
            except (httpx.RequestError, httpx.TimeoutException, OSError):
                await self._set_connected(False)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Health loop error")
                await self._set_connected(False)

            await asyncio.sleep(self._health_interval)

    async def _state_loop(self) -> None:
        """Poll GET /v1/state every _state_interval seconds."""
        while self._running:
            try:
                assert self._http is not None
                resp = await self._http.get("/v1/state")
                if resp.status_code == 200:
                    data = resp.json()
                    async with self._lock:
                        self._robot_state = data
                    await self._push_state_to_store(data)
                    await self._publish(EventType.ROBOT_STATE, data)
            except (httpx.RequestError, httpx.TimeoutException, OSError):
                pass  # silently skip — health loop handles connection status
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("State loop error")

            await asyncio.sleep(self._state_interval)

    async def _ws_event_loop(self) -> None:
        """Connect to WS /v1/events and forward to Dolly EventBus.

        Uses exponential backoff on disconnect.
        """
        backoff = self._reconnect_base
        ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://") + "/v1/events"

        while self._running:
            try:
                extra_headers: dict[str, str] = {}
                if self._auth_token:
                    extra_headers["Authorization"] = f"Bearer {self._auth_token}"

                self._ws = await websockets.connect(
                    ws_url,
                    extra_headers=extra_headers if extra_headers else None,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                )
                backoff = self._reconnect_base  # reset on successful connect
                logger.info("WebSocket connected to %s", ws_url)

                async for message in self._ws:
                    if not self._running:
                        break
                    try:
                        event = json.loads(message)
                        # Forward robot event to Dolly EventBus
                        event_type_str = event.get("event_type", "robot.event")
                        try:
                            event_type = EventType(event_type_str)
                        except ValueError:
                            event_type = EventType.ROBOT_EVENT
                        await self._publish(event_type, event.get("payload", event))
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Invalid WebSocket message: %s", message[:200])

            except asyncio.CancelledError:
                return
            except (ConnectionClosed, WebSocketException, OSError) as exc:
                logger.warning("WebSocket disconnected: %s — reconnecting in %.1fs", exc, backoff)
            except Exception:
                logger.exception("WebSocket loop error")

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max)

    # ------------------------------------------------------------------
    # Public API: Commands
    # ------------------------------------------------------------------

    async def send_command(
        self,
        command: str,
        target: dict[str, Any] | None = None,
        ttl_ms: int = 5000,
    ) -> dict[str, Any]:
        """Send a command to the robot via POST /v1/commands.

        Args:
            command: One of "scan.start", "follow.start", "follow.hold", "mission.stop".
            target: Optional target dict with "kind" and "track_id" fields.
            ttl_ms: Time-to-live in milliseconds (100-10000).

        Returns:
            Command receipt dict with accepted, executed, robot_mode, reason fields.
        """
        if not self._enabled or self._http is None:
            return {
                "accepted": False,
                "executed": False,
                "reason": "gateway_disabled",
                "robot_mode": "idle",
            }

        payload: dict[str, Any] = {
            "v": "1.0",
            "request_id": uuid4().hex,
            "ttl_ms": ttl_ms,
            "command": command,
        }
        if target is not None:
            payload["target"] = target

        try:
            resp = await self._http.post("/v1/commands", json=payload)
            if resp.status_code == 200:
                receipt = resp.json()
                await self._publish(EventType.ROBOT_COMMAND_RESULT, receipt)
                return receipt
            else:
                error_data: dict[str, Any] = {"code": "http_error", "message": f"HTTP {resp.status_code}"}
                try:
                    error_data = resp.json()
                except Exception:
                    pass
                return {
                    "accepted": False,
                    "executed": False,
                    "reason": error_data.get("error", {}).get("message", f"HTTP {resp.status_code}"),
                    "robot_mode": "idle",
                }
        except (httpx.RequestError, httpx.TimeoutException, OSError) as exc:
            logger.error("send_command failed: %s", exc)
            return {
                "accepted": False,
                "executed": False,
                "reason": str(exc),
                "robot_mode": "idle",
            }

    async def emergency_stop(self) -> dict[str, Any]:
        """Send emergency stop via POST /v1/stop.

        Returns:
            Stop receipt dict.
        """
        if not self._enabled or self._http is None:
            return {"accepted": False, "executed": False, "reason": "gateway_disabled"}

        try:
            resp = await self._http.post("/v1/stop")
            if resp.status_code == 200:
                return resp.json()
            return {"accepted": False, "executed": False, "reason": f"HTTP {resp.status_code}"}
        except (httpx.RequestError, httpx.TimeoutException, OSError) as exc:
            logger.error("emergency_stop failed: %s", exc)
            return {"accepted": False, "executed": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # Public API: State
    # ------------------------------------------------------------------

    async def get_robot_state(self) -> dict[str, Any]:
        """Return the cached robot state snapshot."""
        async with self._lock:
            return dict(self._robot_state)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _set_connected(self, connected: bool) -> None:
        """Update connection state and push to StateStore."""
        if self._connected == connected:
            return
        self._connected = connected
        async with self._lock:
            self._robot_state["connected"] = connected
        await self._push_state_to_store(self._robot_state)

    async def _push_state_to_store(self, robot_data: dict[str, Any]) -> None:
        """Push robot state to Dolly StateStore engine section.

        Follows project_memory rule: robot state → StateStore engine section.
        Maps robot-service fields to Dolly engine state format.
        """
        try:
            mode = robot_data.get("mode", "idle")
            target = robot_data.get("target", {})
            safety = robot_data.get("safety", {})

            await self._state.update(
                "engine",
                {
                    "robot_connected": robot_data.get("connected", False),
                    "robot_mode": mode,
                    "robot_target_locked": target.get("locked", False),
                    "robot_target_id": target.get("track_id"),
                    "robot_estop": safety.get("estop", False),
                    "robot_obstacle": safety.get("obstacle", False),
                    "robot_heartbeat_ok": safety.get("heartbeat_ok", False),
                },
            )
        except Exception:
            logger.exception("Failed to push robot state to StateStore")

    async def _publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """Publish a robot event to Dolly EventBus."""
        self._sequence += 1
        try:
            await self._bus.publish(
                event_type=event_type,
                source="robot_gateway",
                payload=payload,
            )
        except Exception:
            logger.exception("Failed to publish %s event", event_type)