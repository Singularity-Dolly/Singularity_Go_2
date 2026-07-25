"""Mock preflight: zero-velocity only, no hardware."""

from __future__ import annotations

from typing import Any

import pytest

from singularity_go2_console.dimos_adapter import DimOSGo2Adapter
from singularity_go2_console.front_person import CameraFrame


class _FakeSession:
    def __init__(self) -> None:
        self.datachannel_ok = True
        self.velocity_channel_ok = True
        self.closed = False
        self.moves: list[tuple[float, float, float]] = []
        self.sport_requests: list[dict[str, Any]] = []

    def move(self, vx: float, vy: float, wz: float) -> bool:
        self.moves.append((vx, vy, wz))
        self.sport_requests.append({"api": "Move", "x": vx, "y": vy, "z": wz})
        return True

    def stop_movement(self) -> bool:
        self.moves.append((0.0, 0.0, 0.0))
        self.sport_requests.append({"api": "StopMove"})
        return True

    def close(self) -> None:
        self.closed = True

    def enable_video(self, _cb: Any) -> bool:
        return True


async def _wire_fake_connect(
    adapter: DimOSGo2Adapter,
    *,
    inject_nonzero: bool = False,
) -> None:
    session = _FakeSession()

    async def fake_connect(robot_ip: str | None = None) -> tuple[bool, str, str]:
        adapter._session = session
        adapter._connection = session
        adapter._connected = True
        adapter._robot_ip = "192.168.12.1"
        adapter._published_commands = []
        adapter._latest_frame = CameraFrame(
            image=[[[0, 0, 0]]],
            timestamp_s=1.0,
            frame_id=1,
            width=1,
            height=1,
        )
        if inject_nonzero:
            # Poison command log as if a non-zero publish happened.
            adapter._published_commands.append((0.1, 0.0, 0.0))
        return True, "OK", "mock connected"

    frame_id = {"n": 1}

    def advancing_frame() -> CameraFrame:
        frame_id["n"] += 1
        frame = CameraFrame(
            image=[[[0, 0, 0]]],
            timestamp_s=1.0 + frame_id["n"] * 0.01,
            frame_id=frame_id["n"],
            width=1,
            height=1,
        )
        adapter._latest_frame = frame
        return frame

    adapter.connect = fake_connect  # type: ignore[method-assign]
    adapter.get_latest_frame = advancing_frame  # type: ignore[method-assign]



@pytest.mark.asyncio
async def test_preflight_zero_only_passes_mock() -> None:
    adapter = DimOSGo2Adapter(
        connection_mode="ap",
        aes_key="0123456789abcdef0123456789abcdef",
    )
    await _wire_fake_connect(adapter, inject_nonzero=False)
    out = await adapter.run_preflight(frame_advance_s=0.05)
    assert out["ok"] is True
    assert out["nonzero_velocity_sent"] is False
    assert out["disconnected"] is True
    assert all(
        abs(vx) < 1e-12 and abs(vy) < 1e-12 and abs(wz) < 1e-12
        for vx, vy, wz in out["commands"]
    )


@pytest.mark.asyncio
async def test_preflight_fails_if_nonzero_velocity_sent() -> None:
    adapter = DimOSGo2Adapter(
        connection_mode="ap",
        aes_key="0123456789abcdef0123456789abcdef",
    )
    await _wire_fake_connect(adapter, inject_nonzero=True)
    out = await adapter.run_preflight(frame_advance_s=0.05)
    assert out["ok"] is False
    assert out["nonzero_velocity_sent"] is True
    assert out["error_code"] == "INTERNAL_ERROR"
    assert out["disconnected"] is True
    # Hard fail if any non-zero remains unmarked.
    assert any(
        abs(vx) > 1e-12 or abs(vy) > 1e-12 or abs(wz) > 1e-12
        for vx, vy, wz in out["commands"]
    )


@pytest.mark.asyncio
async def test_preflight_no_mock_fallback_flag() -> None:
    adapter = DimOSGo2Adapter(
        connection_mode="ap",
        aes_key="0123456789abcdef0123456789abcdef",
    )
    assert adapter.mock is False


@pytest.mark.asyncio
async def test_preflight_disconnect_on_connect_failure() -> None:
    adapter = DimOSGo2Adapter(
        connection_mode="ap",
        aes_key="0123456789abcdef0123456789abcdef",
    )

    async def fail_connect(robot_ip: str | None = None) -> tuple[bool, str, str]:
        return False, "LOCAL_AP_SIGNALING_FAILED", "mock signaling fail"

    adapter.connect = fail_connect  # type: ignore[method-assign]
    out = await adapter.run_preflight(frame_advance_s=0.05)
    assert out["ok"] is False
    assert out["error_code"] == "LOCAL_AP_SIGNALING_FAILED"
    assert out["nonzero_velocity_sent"] is False
    assert out["disconnected"] is True
