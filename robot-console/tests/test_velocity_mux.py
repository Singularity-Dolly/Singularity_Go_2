"""Velocity mux ownership tests."""

from __future__ import annotations

from singularity_go2_console.state import ErrorCode, VelocityOwner
from singularity_go2_console.velocity_mux import VelocityMux
from tests.fakes import FakeVelocitySink


def test_only_owner_can_publish() -> None:
    sink = FakeVelocitySink()
    mux = VelocityMux(sink)
    mux.set_owner(VelocityOwner.MANUAL)
    ok = mux.publish(VelocityOwner.MANUAL, 0.1, 0.0, 0.0)
    assert ok.ok
    bad = mux.publish(VelocityOwner.FOLLOW, 0.2, 0.0, 0.0)
    assert not bad.ok
    assert bad.code == ErrorCode.VELOCITY_REJECTED
    assert mux.rejected_count >= 1


def test_manual_and_follow_never_simultaneous() -> None:
    sink = FakeVelocitySink()
    mux = VelocityMux(sink)
    mux.set_owner(VelocityOwner.MANUAL)
    mux.publish(VelocityOwner.MANUAL, 0.1, 0.0, 0.0)
    mux.set_owner(VelocityOwner.FOLLOW)
    # After ownership transfer, manual is rejected
    rejected = mux.publish(VelocityOwner.MANUAL, 0.2, 0.0, 0.0)
    assert not rejected.ok
    follow = mux.publish(VelocityOwner.FOLLOW, 0.05, 0.0, 0.0)
    assert follow.ok
    assert mux.owner == VelocityOwner.FOLLOW


def test_estop_rejects_later_movement() -> None:
    sink = FakeVelocitySink()
    mux = VelocityMux(sink)
    mux.set_owner(VelocityOwner.ESTOP)
    mux.force_zero()
    rejected = mux.publish(VelocityOwner.MANUAL, 0.1, 0.0, 0.0)
    assert not rejected.ok
    assert rejected.code == ErrorCode.ESTOP_ACTIVE
