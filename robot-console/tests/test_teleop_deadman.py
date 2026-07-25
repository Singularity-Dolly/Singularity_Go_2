"""Deadman WASD teleop tests (no hardware)."""

from __future__ import annotations

import pytest

from singularity_go2_console.config import (
    MAX_LINEAR_MPS,
    MAX_YAW_RPS,
    Go2CtlConfig,
    clamp_key_hold_ms,
)
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.state import ControllerMode, VelocityOwner
from singularity_go2_console.terminal_console import TerminalConsole
from tests.fakes import FakeClock, FakeGo2Adapter


def _console(
    *,
    key_hold_ms: int = 400,
    manual_ttl_ms: int = 250,
) -> tuple[TerminalConsole, Go2Controller, FakeGo2Adapter, FakeClock]:
    clock = FakeClock()
    adapter = FakeGo2Adapter(clock=clock)
    cfg = Go2CtlConfig(
        mock=True,
        key_hold_ms=key_hold_ms,
        manual_ttl_ms=manual_ttl_ms,
        max_forward_speed=MAX_LINEAR_MPS,
        max_reverse_speed=MAX_LINEAR_MPS,
        max_strafe_speed=MAX_LINEAR_MPS,
        max_angular_speed=MAX_YAW_RPS,
        mode_settle_ms=0,
    )
    ctl = Go2Controller(adapter, cfg, allow_mock=True, clock=clock)
    ui = TerminalConsole(ctl, cfg, clock=clock)
    return ui, ctl, adapter, clock


@pytest.mark.asyncio
async def test_one_w_keypress_repeats_during_hold_window() -> None:
    ui, ctl, adapter, clock = _console(key_hold_ms=400)
    assert (await ctl.connect("1.2.3.4")).ok
    assert (await ctl.start_manual()).ok

    ui.note_motion_key("w")
    nonzero: list[tuple[float, float, float]] = []
    # 20 Hz for ~300 ms => multiple publishes, still inside 400 ms hold window.
    for _ in range(6):
        await ui.teleop_tick()
        await ctl.tick_watchdogs()
        if adapter.sink.last and adapter.sink.last != (0.0, 0.0, 0.0):
            nonzero.append(adapter.sink.last)
        clock.advance(0.05)

    assert len(nonzero) >= 3
    assert all(abs(cmd[0] - MAX_LINEAR_MPS) < 1e-9 for cmd in nonzero)
    assert all(cmd[1] == 0.0 and cmd[2] == 0.0 for cmd in nonzero)
    assert ui.is_active
    # Watchdog refreshed by repeated publishes.
    status = await ctl.get_status()
    assert status.manual_watchdog_ok is True
    assert status.velocity_owner == VelocityOwner.MANUAL.value


@pytest.mark.asyncio
async def test_command_zeros_after_deadline_once() -> None:
    ui, ctl, adapter, clock = _console(key_hold_ms=200)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui.note_motion_key("w")
    await ui.teleop_tick()
    assert adapter.sink.last == (MAX_LINEAR_MPS, 0.0, 0.0)

    clock.advance(0.25)  # past hold window
    before = len(adapter.sink.commands)
    await ui.teleop_tick()
    zeros = [c for c in adapter.sink.commands[before:] if c == (0.0, 0.0, 0.0)]
    assert len(zeros) == 1
    assert adapter.sink.last == (0.0, 0.0, 0.0)
    assert not ui.is_active

    # No continuous zero spam after expiry.
    before2 = len(adapter.sink.commands)
    await ui.teleop_tick()
    await ui.teleop_tick()
    assert adapter.sink.commands[before2:] == []


@pytest.mark.asyncio
async def test_no_command_continues_after_x() -> None:
    ui, ctl, adapter, clock = _console(key_hold_ms=500)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui.note_motion_key("w")
    await ui.teleop_tick()
    assert adapter.sink.last != (0.0, 0.0, 0.0)

    await ui.handle_hold()
    assert ctl.mode == ControllerMode.IDLE
    assert adapter.sink.last == (0.0, 0.0, 0.0)
    assert not ui.is_active

    clock.advance(0.05)
    before = len(adapter.sink.commands)
    await ui.teleop_tick()
    assert adapter.sink.commands[before:] == []


@pytest.mark.asyncio
async def test_no_command_continues_after_space_estop() -> None:
    ui, ctl, adapter, clock = _console(key_hold_ms=500)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui.note_motion_key("d")
    await ui.teleop_tick()

    await ui.handle_estop("space")
    assert ctl.mode == ControllerMode.ESTOP
    assert ctl.mux.owner == VelocityOwner.ESTOP
    assert adapter.sink.last == (0.0, 0.0, 0.0)
    assert not ui.is_active

    # Teleop must not republish motion under E-stop.
    ui.note_motion_key("w")
    clock.advance(0.05)
    await ui.teleop_tick()
    rejected = await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    assert not rejected.ok
    assert adapter.sink.last == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_no_command_continues_after_esc_shutdown() -> None:
    ui, ctl, adapter, clock = _console(key_hold_ms=500)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui.note_motion_key("w")
    await ui.teleop_tick()

    await ui.handle_shutdown()
    assert ctl.mode == ControllerMode.DISCONNECTED
    assert adapter.sink.last == (0.0, 0.0, 0.0)
    assert not ui.is_active
    assert not adapter.connected

    before = len(adapter.sink.commands)
    await ui.teleop_tick()
    assert adapter.sink.commands[before:] == []


@pytest.mark.asyncio
async def test_estop_has_priority_over_deadman() -> None:
    ui, ctl, adapter, _ = _console()
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    await ui.handle_estop("test")
    ui.note_motion_key("w")
    await ui.teleop_tick()
    assert ctl.mode == ControllerMode.ESTOP
    assert adapter.sink.last == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_speeds_remain_clamped() -> None:
    ui, ctl, adapter, _ = _console()
    # Attempt to request uncapped speeds via config override path.
    ui.config = ui.config.with_overrides(
        max_forward_speed=5.0,
        max_angular_speed=9.0,
        boost_multiplier=10.0,
    )
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui._boost = True
    ui.note_motion_key("w")
    ui.note_motion_key("d")
    await ui.teleop_tick()
    assert adapter.sink.last is not None
    vx, vy, wz = adapter.sink.last
    assert abs(vx) <= MAX_LINEAR_MPS + 1e-9
    assert abs(vy) <= MAX_LINEAR_MPS + 1e-9
    assert abs(wz) <= MAX_YAW_RPS + 1e-9


@pytest.mark.asyncio
async def test_manual_watchdog_remains_active_during_hold() -> None:
    ui, ctl, adapter, clock = _console(key_hold_ms=400, manual_ttl_ms=120)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    ui.note_motion_key("w")
    for _ in range(6):
        await ui.teleop_tick()
        await ctl.tick_watchdogs()
        status = await ctl.get_status()
        assert status.manual_watchdog_ok is True
        clock.advance(0.05)
    assert adapter.sink.non_zero_active()


def test_key_hold_ms_clamped() -> None:
    assert clamp_key_hold_ms(50) == 150
    assert clamp_key_hold_ms(400) == 400
    assert clamp_key_hold_ms(5000) == 1000


def test_env_cannot_raise_speed_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GO2CTL_MAX_FORWARD_SPEED", "9.9")
    monkeypatch.setenv("GO2CTL_MAX_ANGULAR_SPEED", "9.9")
    monkeypatch.setenv("GO2CTL_KEY_HOLD_MS", "50")
    cfg = Go2CtlConfig.from_environ(load_aes=False)
    assert cfg.max_forward_speed == MAX_LINEAR_MPS
    assert cfg.max_angular_speed == MAX_YAW_RPS
    assert cfg.key_hold_ms == 150
