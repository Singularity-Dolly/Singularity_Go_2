"""Watchdog tests."""

from __future__ import annotations

from singularity_go2_console.watchdog import Watchdogs
from tests.fakes import FakeClock


def test_manual_ttl_expiry() -> None:
    clock = FakeClock()
    wd = Watchdogs(manual_ttl_ms=250, clock=clock)
    wd.mark_manual_command()
    assert not wd.check_manual_ttl_expired()
    clock.advance(0.3)
    assert wd.check_manual_ttl_expired()


def test_camera_stale() -> None:
    clock = FakeClock()
    wd = Watchdogs(camera_stale_ms=500, clock=clock)
    wd.mark_frame()
    assert not wd.check_camera_stale()
    clock.advance(0.6)
    assert wd.check_camera_stale()


def test_connection_lost() -> None:
    wd = Watchdogs()
    wd.mark_connected(True)
    assert not wd.check_connection_lost()
    wd.mark_connected(False)
    assert wd.check_connection_lost()
