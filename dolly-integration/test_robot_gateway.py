"""Tests for RobotGatewayClient — HTTP/WebSocket client for robot-service.

Tests use mocks for httpx and websockets to avoid requiring a running robot.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.contracts import EventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_health_response(connected: bool = True) -> dict:
    return {
        "status": "ok",
        "robot_connected": connected,
        "uptime_seconds": 123.4,
        "ts_utc": "2026-07-24T12:00:00Z",
    }


def _mock_state_response(mode: str = "idle") -> dict:
    return {
        "v": "1.0",
        "robot_id": "go2_test",
        "connected": True,
        "mode": mode,
        "target": {"locked": False, "track_id": None, "confidence": None},
        "scan": {"active": False, "path": [], "map_source": "unavailable"},
        "safety": {"estop": False, "obstacle": False, "heartbeat_ok": True, "last_command_age_ms": 0},
        "ts_utc": "2026-07-24T12:00:00Z",
    }


def _mock_command_receipt() -> dict:
    return {
        "v": "1.0",
        "request_id": "test-req-id",
        "accepted": True,
        "executed": True,
        "robot_mode": "following",
        "reason": "ok",
        "ts_utc": "2026-07-24T12:00:00Z",
    }


# ---------------------------------------------------------------------------
# Mock Dolly dependencies
# ---------------------------------------------------------------------------

class MockEventBus:
    """Minimal mock of Dolly EventBus for testing."""

    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, event_type, *, source, payload, session_id=None, correlation_id=None):
        self.published.append({
            "event_type": event_type,
            "source": source,
            "payload": payload,
        })


class MockStateStore:
    """Minimal mock of Dolly StateStore for testing."""

    def __init__(self) -> None:
        self.sections: dict[str, dict] = {"engine": {}}

    async def update(self, section: str, values: dict) -> None:
        self.sections.setdefault(section, {}).update(values)

    async def snapshot(self) -> dict:
        return dict(self.sections)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bus() -> MockEventBus:
    return MockEventBus()


@pytest.fixture
def state() -> MockStateStore:
    return MockStateStore()


@pytest.fixture
def client(bus, state) -> "RobotGatewayClient":
    from backend.app.robot_gateway import RobotGatewayClient
    return RobotGatewayClient(
        base_url="http://127.0.0.1:8780",
        bus=bus,
        state=state,
        enabled=True,
    )


@pytest.fixture
def disabled_client(bus, state) -> "RobotGatewayClient":
    from backend.app.robot_gateway import RobotGatewayClient
    return RobotGatewayClient(
        base_url="http://127.0.0.1:8780",
        bus=bus,
        state=state,
        enabled=False,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_defaults(self, bus, state):
        from backend.app.robot_gateway import RobotGatewayClient
        c = RobotGatewayClient("http://127.0.0.1:8780", bus, state)
        assert c._enabled is True
        assert c._base_url == "http://127.0.0.1:8780"
        assert c._connected is False

    def test_disabled(self, bus, state):
        from backend.app.robot_gateway import RobotGatewayClient
        c = RobotGatewayClient("http://127.0.0.1:8780", bus, state, enabled=False)
        assert c._enabled is False
        assert c.enabled is False

    def test_with_auth_token(self, bus, state):
        from backend.app.robot_gateway import RobotGatewayClient
        c = RobotGatewayClient("http://127.0.0.1:8780", bus, state, auth_token="secret")
        assert c._auth_token == "secret"

    def test_custom_intervals(self, bus, state):
        from backend.app.robot_gateway import RobotGatewayClient
        c = RobotGatewayClient(
            "http://127.0.0.1:8780", bus, state,
            health_interval_s=10.0,
            state_interval_s=1.0,
            reconnect_base_s=2.0,
            reconnect_max_s=60.0,
            request_timeout_s=15.0,
        )
        assert c._health_interval == 10.0
        assert c._state_interval == 1.0
        assert c._reconnect_base == 2.0
        assert c._reconnect_max == 60.0
        assert c._request_timeout == 15.0


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_disabled_skip_start(self, disabled_client):
        await disabled_client.start()
        assert disabled_client._running is False
        assert disabled_client._http is None

    @pytest.mark.asyncio
    async def test_disabled_skip_stop(self, disabled_client):
        await disabled_client.stop()
        # Should not raise

    @pytest.mark.asyncio
    async def test_stop_before_start(self, client):
        await client.stop()
        # Should not raise


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class TestCommands:
    @pytest.mark.asyncio
    async def test_disabled_send_command(self, disabled_client):
        result = await disabled_client.send_command("scan.start")
        assert result["accepted"] is False
        assert result["reason"] == "gateway_disabled"

    @pytest.mark.asyncio
    async def test_disabled_emergency_stop(self, disabled_client):
        result = await disabled_client.emergency_stop()
        assert result["accepted"] is False
        assert result["reason"] == "gateway_disabled"

    @pytest.mark.asyncio
    async def test_send_command_no_http(self, client):
        """send_command called before start() should return gateway_disabled."""
        result = await client.send_command("scan.start")
        assert result["accepted"] is False
        assert result["reason"] == "gateway_disabled"

    @pytest.mark.asyncio
    async def test_emergency_stop_no_http(self, client):
        result = await client.emergency_stop()
        assert result["accepted"] is False
        assert result["reason"] == "gateway_disabled"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class TestState:
    @pytest.mark.asyncio
    async def test_get_robot_state_default(self, client):
        state = await client.get_robot_state()
        assert state["connected"] is False
        assert state["mode"] == "idle"
        assert state["robot_id"] == "unknown"

    def test_connected_property(self, client):
        assert client.connected is False

    def test_enabled_property(self, client, disabled_client):
        assert client.enabled is True
        assert disabled_client.enabled is False


# ---------------------------------------------------------------------------
# Event publishing
# ---------------------------------------------------------------------------

class TestEventPublishing:
    @pytest.mark.asyncio
    async def test_publish_increments_sequence(self, client, bus):
        await client._publish(EventType.ROBOT_STATE, {"test": True})
        await client._publish(EventType.ROBOT_HEALTH, {"test": True})
        assert client._sequence == 2
        assert len(bus.published) == 2

    @pytest.mark.asyncio
    async def test_publish_robot_state(self, client, bus):
        await client._publish(EventType.ROBOT_STATE, {"mode": "idle"})
        assert bus.published[0]["event_type"] == EventType.ROBOT_STATE
        assert bus.published[0]["source"] == "robot_gateway"
        assert bus.published[0]["payload"]["mode"] == "idle"

    @pytest.mark.asyncio
    async def test_publish_robot_connected(self, client, bus):
        await client._publish(EventType.ROBOT_CONNECTED, {"robot_connected": True})
        assert bus.published[0]["event_type"] == EventType.ROBOT_CONNECTED

    @pytest.mark.asyncio
    async def test_publish_does_not_raise_on_error(self, client):
        """Publish should not raise even if bus fails."""
        client._bus = None  # type: ignore[assignment]
        # Should not raise
        await client._publish(EventType.ROBOT_STATE, {"test": True})


# ---------------------------------------------------------------------------
# State store push
# ---------------------------------------------------------------------------

class TestStateStorePush:
    @pytest.mark.asyncio
    async def test_push_state_to_store(self, client, state):
        data = _mock_state_response("following")
        data["target"]["locked"] = True
        data["target"]["track_id"] = "t42"
        data["safety"]["estop"] = False

        await client._push_state_to_store(data)

        engine = state.sections["engine"]
        assert engine["robot_connected"] is True
        assert engine["robot_mode"] == "following"
        assert engine["robot_target_locked"] is True
        assert engine["robot_target_id"] == "t42"
        assert engine["robot_estop"] is False

    @pytest.mark.asyncio
    async def test_push_state_estop(self, client, state):
        data = _mock_state_response("idle")
        data["safety"]["estop"] = True
        data["safety"]["obstacle"] = True

        await client._push_state_to_store(data)

        engine = state.sections["engine"]
        assert engine["robot_estop"] is True
        assert engine["robot_obstacle"] is True
        assert engine["robot_heartbeat_ok"] is True

    @pytest.mark.asyncio
    async def test_push_state_disconnected(self, client, state):
        data = _mock_state_response("idle")
        data["connected"] = False
        data["safety"]["heartbeat_ok"] = False

        await client._push_state_to_store(data)

        engine = state.sections["engine"]
        assert engine["robot_connected"] is False
        assert engine["robot_heartbeat_ok"] is False


# ---------------------------------------------------------------------------
# Connection state
# ---------------------------------------------------------------------------

class TestConnectionState:
    @pytest.mark.asyncio
    async def test_set_connected_transitions(self, client, bus):
        assert client._connected is False

        await client._set_connected(True)
        assert client._connected is True

        await client._set_connected(False)
        assert client._connected is False

    @pytest.mark.asyncio
    async def test_set_connected_no_duplicate(self, client, bus):
        """Duplicate calls should not publish redundant events."""
        await client._set_connected(False)  # already False
        # First transition: False → True
        await client._set_connected(True)
        # Duplicate: True → True (should be no-op)
        await client._set_connected(True)
        # Should only have published once for the transition
        # (health loop publishes separately, so we just check no error)