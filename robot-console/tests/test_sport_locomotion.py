"""Sport-mode locomotion transport tests (no hardware)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

from singularity_go2_console.config import (
    MAX_LINEAR_MPS,
    MAX_YAW_RPS,
    Go2CtlConfig,
)
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.dimos_adapter import DimOSGo2Adapter
from singularity_go2_console.front_person import CameraFrame
from singularity_go2_console.state import ErrorCode
from singularity_go2_console.terminal_console import TerminalConsole
from singularity_go2_console.webrtc_connect import (
    MOTION_SWITCHER_CHECK_MODE,
    MOTION_SWITCHER_SELECT_MODE,
    Go2WebRTCSession,
    parse_motion_mode_name,
)
from tests.fakes import FakeClock, FakeGo2Adapter


class FakePubSub:
    def __init__(self, *, motion_mode: str = "normal") -> None:
        self.motion_mode = motion_mode
        self.requests: list[dict[str, Any]] = []
        self.select_calls = 0

    async def publish_request_new(self, topic: str, options: dict[str, Any] | None = None):
        options = options or {}
        api_id = options.get("api_id")
        entry = {"topic": topic, "options": options}
        self.requests.append(entry)
        if topic == RTC_TOPIC["MOTION_SWITCHER"] and api_id == MOTION_SWITCHER_CHECK_MODE:
            return {"data": {"data": json.dumps({"name": self.motion_mode, "form": "0"})}}
        if topic == RTC_TOPIC["MOTION_SWITCHER"] and api_id == MOTION_SWITCHER_SELECT_MODE:
            self.select_calls += 1
            param = options.get("parameter") or {}
            if isinstance(param, str):
                param = json.loads(param)
            if param.get("name") == "normal":
                self.motion_mode = "normal"
            return {"data": {"data": json.dumps({"name": self.motion_mode})}}
        if topic == RTC_TOPIC["SPORT_MOD"]:
            return {"ok": True}
        return {"ok": True}

    def publish_without_callback(self, topic: str, data: Any = None, msg_type: Any = None) -> None:
        entry: dict[str, Any] = {"topic": topic, "data": data, "type": msg_type}
        # Only mark wireless diagnostic publishes; sport Move/StopMove use this too.
        if topic == RTC_TOPIC["WIRELESS_CONTROLLER"]:
            entry["wireless"] = data
            entry["diagnostic"] = True
        self.requests.append(entry)


class FakeDataChannel:
    def __init__(self, pub_sub: FakePubSub) -> None:
        self.pub_sub = pub_sub


class FakeConn:
    def __init__(self, pub_sub: FakePubSub) -> None:
        self.datachannel = FakeDataChannel(pub_sub)

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None


def _session(
    *,
    motion_mode: str = "normal",
    allow_normal_mode_switch: bool = False,
) -> tuple[Go2WebRTCSession, FakePubSub]:
    pub = FakePubSub(motion_mode=motion_mode)
    session = Go2WebRTCSession(
        FakeConn(pub),
        allow_normal_mode_switch=allow_normal_mode_switch,
        motion_mode_cache_s=0.0,
        mode_switch_timeout_s=0.5,
    )
    session.datachannel_ok = True
    session.velocity_channel_ok = True
    return session, pub


def test_parse_motion_mode_name() -> None:
    assert parse_motion_mode_name({"data": {"data": '{"name":"ai"}'}}) == "ai"
    assert parse_motion_mode_name({"name": "normal"}) == "normal"


def test_w_produces_sport_move_positive_x() -> None:
    session, _ = _session()
    assert session.move(0.15, 0.0, 0.0) is True
    moves = [r for r in session.sport_requests if r["api"] == "Move"]
    assert len(moves) == 1
    assert moves[0]["api_id"] == SPORT_CMD["Move"]
    assert moves[0]["topic"] == RTC_TOPIC["SPORT_MOD"]
    assert moves[0]["x"] > 0
    assert moves[0]["y"] == 0.0
    assert moves[0]["z"] == 0.0
    session.close()


def test_s_produces_negative_x() -> None:
    session, _ = _session()
    assert session.move(-0.15, 0.0, 0.0) is True
    assert session.sport_requests[-1]["x"] < 0
    session.close()


def test_ad_produce_bounded_z() -> None:
    session, _ = _session()
    assert session.move(0.0, 0.0, 0.35) is True
    assert session.move(0.0, 0.0, -0.35) is True
    zs = [r["z"] for r in session.sport_requests if r["api"] == "Move"]
    assert zs[0] > 0
    assert zs[1] < 0
    assert all(abs(z) <= MAX_YAW_RPS + 1e-9 for z in zs)
    session.close()


def test_qe_produce_bounded_y() -> None:
    session, _ = _session()
    assert session.move(0.0, 0.15, 0.0) is True
    assert session.move(0.0, -0.15, 0.0) is True
    ys = [r["y"] for r in session.sport_requests if r["api"] == "Move"]
    assert ys[0] > 0
    assert ys[1] < 0
    assert all(abs(y) <= MAX_LINEAR_MPS + 1e-9 for y in ys)
    session.close()


def test_wireless_not_used_for_default_move() -> None:
    session, pub = _session()
    assert session.move(0.1, 0.0, 0.0) is True
    assert session.wireless_diagnostics == []
    assert not any(r.get("diagnostic") for r in pub.requests)
    assert any(r["topic"] == RTC_TOPIC["SPORT_MOD"] for r in pub.requests)
    session.close()


def test_non_normal_mode_rejects_move_and_sends_stop() -> None:
    session, _ = _session(motion_mode="ai")
    assert session.move(0.1, 0.0, 0.0) is False
    assert session.last_error_code == "MOTION_MODE_NOT_NORMAL"
    assert any(r["api"] == "StopMove" for r in session.sport_requests)
    assert not any(r["api"] == "Move" for r in session.sport_requests)
    session.close()


def test_normal_mode_enables_move() -> None:
    session, _ = _session(motion_mode="normal")
    assert session.move(0.05, 0.0, 0.0) is True
    assert any(r["api"] == "Move" for r in session.sport_requests)
    session.close()


def test_automatic_mode_switch_disabled_by_default() -> None:
    session, pub = _session(motion_mode="ai", allow_normal_mode_switch=False)
    ok, code, _ = session.ensure_normal_mode()
    assert ok is False
    assert code == "MOTION_MODE_SWITCH_DISABLED"
    assert pub.select_calls == 0
    assert session.move(0.1, 0.0, 0.0) is False
    session.close()


def test_explicit_mode_switch_flag_required() -> None:
    session, pub = _session(motion_mode="ai", allow_normal_mode_switch=True)
    ok, code, _ = session.ensure_normal_mode()
    assert ok is True
    assert code == "OK"
    assert pub.select_calls == 1
    assert session.move(0.1, 0.0, 0.0) is True
    session.close()


@pytest.mark.asyncio
async def test_stopmove_after_deadman_expiry() -> None:
    clock = FakeClock()
    adapter = FakeGo2Adapter(clock=clock)
    cfg = Go2CtlConfig(mock=True, key_hold_ms=200, mode_settle_ms=0)
    ctl = Go2Controller(adapter, cfg, allow_mock=True, clock=clock)
    ui = TerminalConsole(ctl, cfg, clock=clock)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui.note_motion_key("w")
    await ui.teleop_tick()
    assert any(r["api"] == "Move" and r["x"] > 0 for r in adapter.sport_requests)
    clock.advance(0.25)
    await ui.teleop_tick()
    stops = [r for r in adapter.sport_requests if r["api"] == "StopMove"]
    assert len(stops) >= 1
    assert adapter.sink.last == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_stopmove_on_hold_estop_shutdown() -> None:
    clock = FakeClock()
    adapter = FakeGo2Adapter(clock=clock)
    cfg = Go2CtlConfig(mock=True, key_hold_ms=400, mode_settle_ms=0)
    ctl = Go2Controller(adapter, cfg, allow_mock=True, clock=clock)
    ui = TerminalConsole(ctl, cfg, clock=clock)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui.note_motion_key("w")
    await ui.teleop_tick()

    await ui.handle_hold()
    assert any(r["api"] == "StopMove" for r in adapter.sport_requests)

    adapter2 = FakeGo2Adapter(clock=FakeClock())
    ctl2 = Go2Controller(adapter2, cfg, allow_mock=True, clock=FakeClock())
    ui2 = TerminalConsole(ctl2, cfg, clock=FakeClock())
    await ctl2.connect("1.2.3.4")
    await ctl2.start_manual()
    ui2.note_motion_key("a")
    await ui2.teleop_tick()
    await ui2.handle_estop("space")
    assert any(r["api"] == "StopMove" for r in adapter2.sport_requests)

    adapter3 = FakeGo2Adapter(clock=FakeClock())
    ctl3 = Go2Controller(adapter3, cfg, allow_mock=True, clock=FakeClock())
    ui3 = TerminalConsole(ctl3, cfg, clock=FakeClock())
    await ctl3.connect("1.2.3.4")
    await ctl3.start_manual()
    ui3.note_motion_key("d")
    await ui3.teleop_tick()
    await ui3.handle_shutdown()
    assert any(r["api"] == "StopMove" for r in adapter3.sport_requests)


@pytest.mark.asyncio
async def test_controller_rejects_move_when_not_normal() -> None:
    adapter = FakeGo2Adapter(motion_mode="ai")
    cfg = Go2CtlConfig(mock=True, mode_settle_ms=0)
    ctl = Go2Controller(adapter, cfg, allow_mock=True)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    result = await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    assert result.ok is False
    assert result.code == ErrorCode.MOTION_MODE_NOT_NORMAL
    assert not any(r["api"] == "Move" for r in adapter.sport_requests)


@pytest.mark.asyncio
async def test_start_manual_switch_requires_flag() -> None:
    adapter = FakeGo2Adapter(motion_mode="ai", allow_normal_mode_switch=False)
    cfg = Go2CtlConfig(mock=True, mode_settle_ms=0, allow_normal_mode_switch=True)
    # Config allows, but adapter gate still respects its own flag unless wired.
    adapter.allow_normal_mode_switch = False
    ctl = Go2Controller(adapter, cfg, allow_mock=True)
    await ctl.connect("1.2.3.4")
    result = await ctl.start_manual()
    assert result.ok is False
    assert result.code == ErrorCode.MOTION_MODE_SWITCH_DISABLED
    assert adapter.mode_switch_calls == 1


@pytest.mark.asyncio
async def test_start_manual_with_flag_enables_normal() -> None:
    adapter = FakeGo2Adapter(motion_mode="ai", allow_normal_mode_switch=True)
    cfg = Go2CtlConfig(mock=True, mode_settle_ms=0, allow_normal_mode_switch=True)
    ctl = Go2Controller(adapter, cfg, allow_mock=True)
    await ctl.connect("1.2.3.4")
    result = await ctl.start_manual()
    assert result.ok is True
    assert adapter.motion_mode == "normal"
    move = await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    assert move.ok is True


@pytest.mark.asyncio
async def test_preflight_never_sends_move() -> None:
    adapter = DimOSGo2Adapter(
        connection_mode="ap",
        aes_key="0123456789abcdef0123456789abcdef",
    )

    class _FakeSession:
        def __init__(self) -> None:
            self.datachannel_ok = True
            self.velocity_channel_ok = True
            self.sport_requests: list[dict[str, Any]] = []
            self.closed = False

        def move(self, vx: float, vy: float, wz: float) -> bool:
            self.sport_requests.append(
                {"api": "Move", "x": vx, "y": vy, "z": wz, "api_id": SPORT_CMD["Move"]}
            )
            return True

        def stop_movement(self) -> bool:
            self.sport_requests.append(
                {"api": "StopMove", "api_id": SPORT_CMD["StopMove"]}
            )
            return True

        def close(self) -> None:
            self.closed = True

        def enable_video(self, _cb: Any) -> bool:
            return True

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
    out = await adapter.run_preflight(frame_advance_s=0.05)
    assert out["ok"] is True
    assert out["move_sent"] is False
    assert not any(r["api"] == "Move" for r in out["sport_requests"])
    assert any(r["api"] == "StopMove" for r in out["sport_requests"])


def test_installed_sport_cmd_keys() -> None:
    assert SPORT_CMD["Move"] == 1008
    assert SPORT_CMD["StopMove"] == 1003
