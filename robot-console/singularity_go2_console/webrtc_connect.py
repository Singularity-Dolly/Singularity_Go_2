"""Unitree WebRTC connection helpers for AP/STA + AES (no secret logging)."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Literal

from singularity_go2_console.aes import redact_secrets

logger = logging.getLogger("go2ctl.webrtc")

ConnectionMode = Literal["ap", "sta"]


def _try_import_unitree_webrtc() -> tuple[Any | None, Any | None, str | None]:
    """Return (ConnectionClass, MethodEnum, error)."""
    try:
        from unitree_webrtc_connect import (  # type: ignore
            UnitreeWebRTCConnection,
            WebRTCConnectionMethod,
        )

        return UnitreeWebRTCConnection, WebRTCConnectionMethod, None
    except Exception as exc:  # noqa: BLE001
        return None, None, str(exc)


def build_unitree_connection(
    *,
    connection_mode: ConnectionMode,
    robot_ip: str,
    aes_key: str | None,
) -> tuple[Any | None, str, str]:
    """Construct a Unitree WebRTC connection object without starting it.

    Prefers unitree_webrtc_connect LocalAP / LocalSTA. Falls back to DimOS
    UnitreeWebRTCConnection when the dedicated package is unavailable, but
    still refuses to silently drop the AES requirement when a key is provided
    and the constructor cannot accept it.
    """
    secrets = [aes_key] if aes_key else []
    Conn, Method, err = _try_import_unitree_webrtc()
    if Conn is not None and Method is not None:
        try:
            if connection_mode == "ap":
                method = Method.LocalAP
                kwargs: dict[str, Any] = {"aes_128_key": aes_key} if aes_key else {}
                # LocalAP must not require an arbitrary client IP.
                connection = Conn(method, **{k: v for k, v in kwargs.items() if v})
            else:
                method = Method.LocalSTA
                kwargs = {"ip": robot_ip}
                if aes_key:
                    kwargs["aes_128_key"] = aes_key
                connection = Conn(method, **kwargs)
            return connection, "OK", f"{connection_mode.upper()} signaling object created"
        except TypeError as exc:
            # Older constructors may use different kw names.
            message = redact_secrets(str(exc), secrets)
            return None, "AES_KEY_INVALID", f"Unitree WebRTC constructor rejected args: {message}"
        except Exception as exc:  # noqa: BLE001
            code = (
                "LOCAL_AP_SIGNALING_FAILED"
                if connection_mode == "ap"
                else "LOCAL_STA_SIGNALING_FAILED"
            )
            return None, code, redact_secrets(str(exc), secrets)

    # DimOS fallback path
    try:
        from dimos.robot.unitree.connection import UnitreeWebRTCConnection
    except Exception as exc:  # noqa: BLE001
        detail = redact_secrets(f"{err}; dimos={exc}", secrets)
        return None, "UNSUPPORTED_DIMOS_VERSION", detail

    try:
        signature = inspect.signature(UnitreeWebRTCConnection.__init__)
        params = signature.parameters
        kwargs = {}
        # Common positional: robot IP
        if aes_key:
            for name in ("aes_128_key", "aes_key", "key"):
                if name in params:
                    kwargs[name] = aes_key
                    break
            else:
                return (
                    None,
                    "AES_KEY_REQUIRED",
                    "DimOS UnitreeWebRTCConnection cannot accept AES key; "
                    "install unitree_webrtc_connect for firmware 1.1.15+",
                )
        for name in ("connection_type", "connection_method", "method"):
            if name in params:
                kwargs[name] = "localap" if connection_mode == "ap" else "localsta"
                break
        connection = UnitreeWebRTCConnection(robot_ip, **kwargs)
        return connection, "OK", "DimOS UnitreeWebRTCConnection created"
    except Exception as exc:  # noqa: BLE001
        code = (
            "LOCAL_AP_SIGNALING_FAILED"
            if connection_mode == "ap"
            else "LOCAL_STA_SIGNALING_FAILED"
        )
        return None, code, redact_secrets(str(exc), secrets)


async def start_connection(connection: Any, *, connection_mode: ConnectionMode) -> tuple[bool, str, str]:
    """Start an already-constructed connection object."""
    try:
        if hasattr(connection, "connect") and inspect.iscoroutinefunction(connection.connect):
            await connection.connect()
        elif hasattr(connection, "connect"):
            result = connection.connect()
            if inspect.isawaitable(result):
                await result
        elif hasattr(connection, "start"):
            connection.start()
        else:
            return False, "WEBRTC_CONNECTION_FAILED", "No connect/start method on connection"
        return True, "OK", f"{connection_mode} connected"
    except Exception as exc:  # noqa: BLE001
        code = (
            "LOCAL_AP_SIGNALING_FAILED"
            if connection_mode == "ap"
            else "LOCAL_STA_SIGNALING_FAILED"
        )
        return False, code, str(exc)
