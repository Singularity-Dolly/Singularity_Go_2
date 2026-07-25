"""HTTP contract tests for thin robot-service (mock adapter, no motion)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from singularity_go2_console.config import Go2CtlConfig
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.service.app import create_app
from singularity_go2_console.testing.fakes import FakeGo2Adapter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROBOT_AUTH_TOKEN", "test-token-please-change")
    adapter = FakeGo2Adapter()
    cfg = Go2CtlConfig(mock=True, motion_enabled=False, robot_id="go2_62507")
    controller = Go2Controller(adapter, cfg, allow_mock=True)
    app = create_app(controller=controller, config=cfg, connect_on_startup=True)
    with TestClient(app) as test_client:
        yield test_client, adapter


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token-please-change"}


def test_rejects_unauthenticated(client) -> None:
    test_client, _ = client
    assert test_client.get("/v1/health").status_code == 401


def test_health_and_state(client) -> None:
    test_client, _ = client
    health = test_client.get("/v1/health", headers=_auth())
    assert health.status_code == 200
    body = health.json()
    assert body["available"] is True
    assert "aes" not in str(body).lower()

    state = test_client.get("/v1/state", headers=_auth())
    assert state.status_code == 200
    payload = state.json()
    assert payload["v"] == "1.0"
    assert payload["type"] == "robot.state"
    assert payload["robot_id"] == "go2_62507"
    assert payload["connected"] is True
    assert payload["battery_pct"] is None


def test_frame_jpeg_or_503(client) -> None:
    test_client, _adapter = client
    response = test_client.get("/v1/frame.jpg", headers=_auth())
    assert response.status_code in {200, 503}
    if response.status_code == 200:
        assert response.headers["content-type"].startswith("image/jpeg")


def test_stop_receipt_matches_request_id(client) -> None:
    test_client, adapter = client
    payload = {
        "v": "1.0",
        "request_id": "stop-req-1",
        "issued_at": _now(),
        "ttl_ms": 5000,
        "command": "mission.stop",
        "target": {"kind": "none"},
        "options": {},
    }
    response = test_client.post("/v1/stop", headers=_auth(), json=payload)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["request_id"] == "stop-req-1"
    assert receipt["accepted"] is True
    assert adapter.sink.last == (0.0, 0.0, 0.0) or adapter.sink.commands


def test_follow_blocked_when_motion_disabled(client) -> None:
    test_client, _ = client
    payload = {
        "v": "1.0",
        "request_id": "follow-1",
        "issued_at": _now(),
        "ttl_ms": 5000,
        "command": "follow.start",
        "target": {"kind": "primary_person"},
        "options": {},
    }
    response = test_client.post("/v1/commands", headers=_auth(), json=payload)
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["reason"] == "robot_motion_disabled"


def test_no_token_leak_in_errors(client) -> None:
    test_client, _ = client
    bad = test_client.get(
        "/v1/health", headers={"Authorization": "Bearer wrong-token-value"}
    )
    assert bad.status_code == 401
    assert "test-token" not in bad.text
    assert "wrong-token-value" not in bad.text
