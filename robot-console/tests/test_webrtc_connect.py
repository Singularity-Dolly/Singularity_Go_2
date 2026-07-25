"""AP/STA constructor selection tests (no robot motion)."""

from __future__ import annotations

from enum import Enum
from typing import Any

import pytest
from typer.testing import CliRunner

from singularity_go2_console.cli import app
from singularity_go2_console.webrtc_connect import build_unitree_connection

runner = CliRunner()


class FakeMethod(Enum):
    LocalAP = 1
    LocalSTA = 2
    Remote = 3


class FakeConn:
    def __init__(self, method: FakeMethod, **kwargs: Any) -> None:
        self.method = method
        self.kwargs = kwargs


def test_ap_constructor_selection() -> None:
    conn, code, _ = build_unitree_connection(
        connection_mode="ap",
        robot_ip=None,
        aes_key="0123456789abcdef0123456789abcdef",
        connection_cls=FakeConn,
        method_enum=FakeMethod,
    )
    assert code == "OK"
    assert conn is not None
    assert conn.method is FakeMethod.LocalAP
    assert "ip" not in conn.kwargs


def test_sta_constructor_selection() -> None:
    conn, code, _ = build_unitree_connection(
        connection_mode="sta",
        robot_ip="192.168.123.161",
        aes_key="0123456789abcdef0123456789abcdef",
        connection_cls=FakeConn,
        method_enum=FakeMethod,
    )
    assert code == "OK"
    assert conn is not None
    assert conn.method is FakeMethod.LocalSTA
    assert conn.kwargs["ip"] == "192.168.123.161"


def test_sta_requires_ip() -> None:
    conn, code, _ = build_unitree_connection(
        connection_mode="sta",
        robot_ip=None,
        aes_key="0123456789abcdef0123456789abcdef",
        connection_cls=FakeConn,
        method_enum=FakeMethod,
    )
    assert conn is None
    assert code == "STA_ROBOT_IP_REQUIRED"


def test_missing_key_rejected() -> None:
    conn, code, _ = build_unitree_connection(
        connection_mode="ap",
        robot_ip=None,
        aes_key=None,
        connection_cls=FakeConn,
        method_enum=FakeMethod,
    )
    assert conn is None
    assert code == "AES_KEY_REQUIRED"


def test_ap_does_not_accept_sta_ip_kw() -> None:
    """LocalAP constructor must not receive ip= (no silent STA-shaped call)."""
    conn, code, _ = build_unitree_connection(
        connection_mode="ap",
        robot_ip="192.168.123.161",
        aes_key="0123456789abcdef0123456789abcdef",
        connection_cls=FakeConn,
        method_enum=FakeMethod,
    )
    assert code == "OK"
    assert conn is not None
    assert conn.method is FakeMethod.LocalAP
    assert "ip" not in conn.kwargs
    assert set(conn.kwargs.keys()) == {"aes_128_key"}


def test_no_ap_sta_mode_flip() -> None:
    conn, code, _ = build_unitree_connection(
        connection_mode="sta",
        robot_ip="10.0.0.5",
        aes_key="0123456789abcdef0123456789abcdef",
        connection_cls=FakeConn,
        method_enum=FakeMethod,
    )
    assert code == "OK"
    assert conn is not None
    assert conn.method is FakeMethod.LocalSTA
    assert conn.method is not FakeMethod.LocalAP



def test_cli_sta_requires_robot_ip(tmp_path) -> None:
    key = tmp_path / "aes_key"
    key.write_text("0123456789abcdef0123456789abcdef", encoding="utf-8")
    result = runner.invoke(
        app,
        ["preflight", "--connection-mode", "sta", "--aes-key-file", str(key)],
    )
    assert result.exit_code != 0
    assert "STA_ROBOT_IP_REQUIRED" in result.stdout
