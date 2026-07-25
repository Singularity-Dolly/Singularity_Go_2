"""Connection mode and config priority tests (no robot motion)."""

from __future__ import annotations

from pathlib import Path

import pytest

from singularity_go2_console.aes import AesKeyMaterial
from singularity_go2_console.config import (
    DEFAULT_AP_IP,
    Go2CtlConfig,
    MAX_LINEAR_MPS,
    MAX_YAW_RPS,
)


def test_ap_mode_default_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GO2CTL_CONNECTION_MODE", "ap")
    monkeypatch.delenv("ROBOT_IP", raising=False)
    monkeypatch.delenv("UNITREE_AES_128_KEY", raising=False)
    cfg = Go2CtlConfig.from_environ(load_aes=False)
    assert cfg.connection_mode == "ap"
    assert cfg.robot_ip == DEFAULT_AP_IP


def test_sta_mode_requires_explicit_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GO2CTL_CONNECTION_MODE", "sta")
    monkeypatch.setenv("ROBOT_IP", "192.168.1.50")
    monkeypatch.delenv("UNITREE_AES_128_KEY", raising=False)
    cfg = Go2CtlConfig.from_environ(load_aes=False)
    assert cfg.connection_mode == "sta"
    assert cfg.robot_ip == "192.168.1.50"


def test_invalid_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GO2CTL_CONNECTION_MODE", "wifi")
    with pytest.raises(ValueError):
        Go2CtlConfig.from_environ(load_aes=False)


def test_speed_caps_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env values above the hard caps are clamped to the hard caps.

    Verifies the safety invariant: no env var can raise speeds above the
    compile-time MAX_LINEAR_MPS / MAX_YAW_RPS ceiling, regardless of what
    the operator puts in GO2CTL_MAX_FORWARD_SPEED / GO2CTL_MAX_ANGULAR_SPEED.
    """
    # Set env values ABOVE the hard caps — they must be clamped down.
    monkeypatch.setenv("GO2CTL_MAX_FORWARD_SPEED", str(MAX_LINEAR_MPS + 5.0))
    monkeypatch.setenv("GO2CTL_MAX_ANGULAR_SPEED", str(MAX_YAW_RPS + 5.0))
    cfg = Go2CtlConfig.from_environ(load_aes=False)
    assert cfg.max_forward_speed == MAX_LINEAR_MPS
    assert cfg.max_angular_speed == MAX_YAW_RPS
    # clamp_velocity must not exceed the hard caps.
    vx, vy, wz = cfg.clamp_velocity(MAX_LINEAR_MPS + 10, MAX_LINEAR_MPS + 10, MAX_YAW_RPS + 10)
    assert vx == MAX_LINEAR_MPS
    assert abs(vy) <= MAX_LINEAR_MPS
    assert abs(wz) <= MAX_YAW_RPS


def test_speed_caps_below_hard_cap_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env values below the hard caps are honored (operator can slow the robot)."""
    monkeypatch.setenv("GO2CTL_MAX_FORWARD_SPEED", "0.5")
    monkeypatch.setenv("GO2CTL_MAX_ANGULAR_SPEED", "0.3")
    cfg = Go2CtlConfig.from_environ(load_aes=False)
    assert cfg.max_forward_speed == 0.5
    assert cfg.max_angular_speed == 0.3
    vx, vy, wz = cfg.clamp_velocity(1.0, 1.0, 2.0)
    assert vx == 0.5
    assert abs(wz) <= 0.3


def test_no_silent_mock_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GO2CTL_MOCK", raising=False)
    cfg = Go2CtlConfig.from_environ(load_aes=False)
    assert cfg.mock is False


def test_aes_attached_when_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNITREE_AES_128_KEY", "0123456789abcdef0123456789abcdef")
    cfg = Go2CtlConfig.from_environ(load_aes=True)
    assert isinstance(cfg.aes_key, AesKeyMaterial)
    assert "0123" not in repr(cfg.aes_key)
