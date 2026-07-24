"""Go2Controller integration tests with fake adapter (no robot)."""

from __future__ import annotations

import pytest

from singularity_go2_console.config import Go2CtlConfig
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.front_person import PersonDetection
from singularity_go2_console.state import ControllerMode, ErrorCode, VelocityOwner
from tests.fakes import FakeClock, FakeGo2Adapter


def _controller(
    *,
    clock: FakeClock | None = None,
    adapter: FakeGo2Adapter | None = None,
    **cfg_overrides,
) -> tuple[Go2Controller, FakeGo2Adapter, FakeClock]:
    clock = clock or FakeClock()
    adapter = adapter or FakeGo2Adapter(clock=clock)
    adapter.camera.clock = clock
    defaults = {"mock": True, "target_stable_frames": 1, "acquire_timeout_s": 2.0}
    defaults.update(cfg_overrides)
    cfg = Go2CtlConfig(**defaults)
    ctl = Go2Controller(adapter, cfg, allow_mock=True, clock=clock)
    return ctl, adapter, clock


@pytest.mark.asyncio
async def test_manual_owns_velocity() -> None:
    ctl, adapter, _ = _controller()
    assert (await ctl.connect("1.2.3.4")).ok
    assert (await ctl.start_manual()).ok
    assert ctl.mux.owner == VelocityOwner.MANUAL
    assert (await ctl.set_manual_velocity(0.1, 0.0, 0.0)).ok
    assert adapter.sink.last == (0.1, 0.0, 0.0)


@pytest.mark.asyncio
async def test_follow_owns_velocity() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    result = await ctl.start_follow_front_person()
    assert result.ok, result.message
    assert ctl.mode == ControllerMode.FOLLOWING
    assert ctl.mux.owner == VelocityOwner.FOLLOW


@pytest.mark.asyncio
async def test_manual_and_follow_exclusive() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    await ctl.start_follow_front_person()
    assert ctl.mux.owner == VelocityOwner.FOLLOW
    rejected = await ctl.set_manual_velocity(0.2, 0.0, 0.0)
    assert not rejected.ok


@pytest.mark.asyncio
async def test_switch_manual_to_follow_publishes_zero_first() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    before = len(adapter.sink.commands)
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    await ctl.start_follow_front_person()
    # zero must appear during transition
    zeros = [c for c in adapter.sink.commands[before:] if c == (0.0, 0.0, 0.0)]
    assert zeros, "expected zero velocity before ownership transfer"


@pytest.mark.asyncio
async def test_switch_follow_to_manual_stops_follow_and_zeros() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    await ctl.start_follow_front_person()
    assert adapter.follow.started
    before = len(adapter.sink.commands)
    await ctl.start_manual()
    assert not adapter.follow.started
    assert (0.0, 0.0, 0.0) in adapter.sink.commands[before:]
    assert ctl.mux.owner == VelocityOwner.MANUAL


@pytest.mark.asyncio
async def test_estop_zeros_and_rejects_movement() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    estop = await ctl.emergency_stop("test")
    assert estop.ok
    assert adapter.sink.last == (0.0, 0.0, 0.0)
    assert ctl.mode == ControllerMode.ESTOP
    rejected = await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    assert not rejected.ok
    assert rejected.code == ErrorCode.ESTOP_ACTIVE


@pytest.mark.asyncio
async def test_estop_requires_explicit_reset() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    await ctl.emergency_stop("test")
    # Cannot go to manual without reset
    bad = await ctl.start_manual()
    assert not bad.ok
    reset = await ctl.reset_estop()
    assert reset.ok
    assert ctl.mode == ControllerMode.IDLE
    assert (await ctl.start_manual()).ok


@pytest.mark.asyncio
async def test_manual_ttl_publishes_zero() -> None:
    clock = FakeClock()
    ctl, adapter, clock = _controller(clock=clock, manual_ttl_ms=250)
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    clock.advance(0.3)
    await ctl.tick_watchdogs()
    assert adapter.sink.last == (0.0, 0.0, 0.0)
    assert ctl.mode == ControllerMode.MANUAL


@pytest.mark.asyncio
async def test_camera_stale_stops_following() -> None:
    clock = FakeClock()
    ctl, adapter, clock = _controller(clock=clock, camera_stale_ms=500)
    await ctl.connect("1.2.3.4")
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    await ctl.start_follow_front_person()
    assert ctl.mode == ControllerMode.FOLLOWING
    # Do not push new frames; advance clock past stale threshold
    clock.advance(0.6)
    await ctl.tick_watchdogs()
    assert ctl.mode == ControllerMode.IDLE
    assert not adapter.follow.started
    assert adapter.sink.last == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_target_loss_stops_following() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    await ctl.start_follow_front_person()
    adapter.follow.target_visible = False
    for _ in range(20):
        adapter.camera.push()
        await ctl.tick_watchdogs()
    assert ctl.mode == ControllerMode.IDLE
    assert not adapter.follow.started


@pytest.mark.asyncio
async def test_no_person_found_does_not_move() -> None:
    ctl, adapter, _ = _controller(acquire_timeout_s=0.2)
    await ctl.connect("1.2.3.4")
    adapter.camera.push()
    adapter.detector.set_detections([])
    before = list(adapter.sink.commands)
    result = await ctl.start_follow_front_person()
    assert not result.ok
    assert result.code == ErrorCode.NO_PERSON_FOUND
    # Only zeros allowed after connect/transitions
    non_zero = [c for c in adapter.sink.commands[len(before) :] if c != (0.0, 0.0, 0.0)]
    assert non_zero == []


@pytest.mark.asyncio
async def test_ambiguous_target_does_not_move() -> None:
    ctl, adapter, clock = _controller(acquire_timeout_s=0.25)
    await ctl.connect("1.2.3.4")
    frame = adapter.camera.push()
    # Two nearly identical center persons
    adapter.detector.set_detections(
        [
            PersonDetection(bbox=(220, 50, 380, 400), confidence=0.9, frame_id=frame.frame_id),
            PersonDetection(bbox=(230, 50, 390, 400), confidence=0.9, frame_id=frame.frame_id),
        ]
    )
    result = await ctl.start_follow_front_person()
    assert not result.ok
    assert not adapter.follow.started


@pytest.mark.asyncio
async def test_bbox_and_image_same_frame() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    await ctl.start_follow_front_person()
    assert adapter.follow.last_init is not None
    assert adapter.follow.last_init.frame.frame_id == frame.frame_id
    assert adapter.follow.last_init.bbox == [200, 50, 400, 400]


@pytest.mark.asyncio
async def test_tracker_init_failure() -> None:
    ctl, adapter, _ = _controller()
    adapter.follow.fail_init = True
    await ctl.connect("1.2.3.4")
    frame = adapter.camera.push()
    adapter.detector.set_detections(
        [PersonDetection(bbox=(200, 50, 400, 400), confidence=0.9, frame_id=frame.frame_id)]
    )
    result = await ctl.start_follow_front_person()
    assert not result.ok
    assert result.code == ErrorCode.TRACKER_INIT_FAILED
    assert ctl.mode == ControllerMode.IDLE


@pytest.mark.asyncio
async def test_real_mode_refuses_mock_adapter() -> None:
    adapter = FakeGo2Adapter()
    cfg = Go2CtlConfig(mock=False)
    with pytest.raises(RuntimeError, match="mock"):
        Go2Controller(adapter, cfg, allow_mock=False)


@pytest.mark.asyncio
async def test_shutdown_zeros_and_closes() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    result = await ctl.shutdown()
    assert result.ok
    assert adapter.sink.last == (0.0, 0.0, 0.0)
    assert not adapter.connected
    assert ctl.mode == ControllerMode.DISCONNECTED


@pytest.mark.asyncio
async def test_status_model_accurate() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("192.168.123.161")
    status = await ctl.get_status()
    assert status.connected is True
    assert status.robot_ip == "192.168.123.161"
    assert status.mode == ControllerMode.IDLE.value
    assert status.mock is True
    d = status.to_dict()
    assert "velocity_owner" in d
    assert "camera_ready" in d


@pytest.mark.asyncio
async def test_exception_cleanup_zeros() -> None:
    ctl, adapter, _ = _controller()
    await ctl.connect("1.2.3.4")
    await ctl.start_manual()
    await ctl.set_manual_velocity(0.1, 0.0, 0.0)
    adapter.drop_connection = True
    await ctl.tick_watchdogs()
    assert ctl.mode == ControllerMode.ERROR
    assert adapter.sink.last == (0.0, 0.0, 0.0)
