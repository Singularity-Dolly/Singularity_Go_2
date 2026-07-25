"""Unitree WebRTC connection helpers for AP/STA + AES (no secret logging).

Exact verified constructors (no AP↔STA fallback, no silent DimOS LocalSTA swap):

  LocalAP:
    UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP, aes_128_key=key)

  LocalSTA:
    UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA, ip=robot_ip, aes_128_key=key
    )
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Literal

from singularity_go2_console.aes import redact_secrets

logger = logging.getLogger("go2ctl.webrtc")

ConnectionMode = Literal["ap", "sta"]
LOCAL_AP_IP = "192.168.12.1"


def build_unitree_connection(
    *,
    connection_mode: ConnectionMode,
    robot_ip: str | None,
    aes_key: str | None,
    connection_cls: Any | None = None,
    method_enum: Any | None = None,
) -> tuple[Any | None, str, str]:
    """Construct a Unitree WebRTC connection object without starting it."""
    secrets = [aes_key] if aes_key else []
    if not aes_key:
        return None, "AES_KEY_REQUIRED", "AES-128 key is required"

    mode = (connection_mode or "").strip().lower()
    if mode not in {"ap", "sta"}:
        return None, "INTERNAL_ERROR", "connection mode must be ap or sta"

    if connection_cls is None or method_enum is None:
        try:
            from unitree_webrtc_connect.constants import WebRTCConnectionMethod
            from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection
        except Exception as exc:  # noqa: BLE001
            return (
                None,
                "UNSUPPORTED_DIMOS_VERSION",
                f"unitree_webrtc_connect unavailable: {redact_secrets(str(exc), secrets)}",
            )
        connection_cls = connection_cls or UnitreeWebRTCConnection
        method_enum = method_enum or WebRTCConnectionMethod

    try:
        if mode == "ap":
            # Do not pass robot_ip for LocalAP.
            connection = connection_cls(method_enum.LocalAP, aes_128_key=aes_key)
            return connection, "OK", "LocalAP signaling object created"

        if not robot_ip or not str(robot_ip).strip():
            return None, "STA_ROBOT_IP_REQUIRED", "STA mode requires --robot-ip"
        connection = connection_cls(
            method_enum.LocalSTA,
            ip=str(robot_ip).strip(),
            aes_128_key=aes_key,
        )
        return connection, "OK", "LocalSTA signaling object created"
    except Exception as exc:  # noqa: BLE001
        code = (
            "LOCAL_AP_SIGNALING_FAILED" if mode == "ap" else "LOCAL_STA_SIGNALING_FAILED"
        )
        return None, code, redact_secrets(str(exc), secrets)


class Go2WebRTCSession:
    """Background event-loop session around unitree_webrtc_connect."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self._cmd_vel_timeout = 0.2
        self.stop_timer: threading.Timer | None = None
        self.datachannel_ok = False
        self.velocity_channel_ok = False

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _run(self, coro: Any, timeout: float = 60.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    def connect_and_verify(self) -> None:
        async def _async_connect() -> None:
            await self.conn.connect()
            dc = getattr(self.conn, "datachannel", None)
            if dc is None:
                raise RuntimeError("datachannel missing after connect")
            try:
                await dc.disableTrafficSaving(True)
            except Exception:  # noqa: BLE001
                logger.debug("disableTrafficSaving failed", exc_info=True)
            if hasattr(dc, "set_decoder"):
                try:
                    dc.set_decoder(decoder_type="native")
                except Exception:  # noqa: BLE001
                    logger.debug("set_decoder failed", exc_info=True)
            self.datachannel_ok = True
            pub_sub = getattr(dc, "pub_sub", None)
            self.velocity_channel_ok = pub_sub is not None and hasattr(
                pub_sub, "publish_without_callback"
            )
            if not self.velocity_channel_ok:
                raise RuntimeError("velocity channel unavailable")

        try:
            self._run(_async_connect(), timeout=60.0)
        except Exception:
            self.close()
            raise

    def publish_wireless(self, lx: float, ly: float, rx: float, ry: float = 0.0) -> bool:
        if not self.velocity_channel_ok:
            return False
        try:
            from unitree_webrtc_connect.constants import RTC_TOPIC

            def _pub() -> None:
                self.conn.datachannel.pub_sub.publish_without_callback(
                    RTC_TOPIC["WIRELESS_CONTROLLER"],
                    data={"lx": lx, "ly": ly, "rx": rx, "ry": ry},
                )

            self.loop.call_soon_threadsafe(_pub)
            return True
        except Exception:
            logger.exception("wireless publish failed")
            return False

    def move(self, vx: float, vy: float, wz: float) -> bool:
        ok = self.publish_wireless(lx=-vy, ly=vx, rx=-wz, ry=0.0)
        if not ok:
            return False
        if self.stop_timer:
            self.stop_timer.cancel()
        self.stop_timer = threading.Timer(self._cmd_vel_timeout, self.stop_movement)
        self.stop_timer.daemon = True
        self.stop_timer.start()
        return True

    def stop_movement(self) -> bool:
        if self.stop_timer:
            self.stop_timer.cancel()
            self.stop_timer = None
        return self.publish_wireless(0.0, 0.0, 0.0, 0.0)

    def enable_video(self, on_frame: Callable[[Any], None]) -> bool:
        try:
            from aiortc import MediaStreamTrack

            async def accept_track(track: MediaStreamTrack) -> None:
                while True:
                    frame = await track.recv()
                    on_frame(frame)

            self.conn.video.add_track_callback(accept_track)

            def switch_on() -> None:
                self.conn.video.switchVideoChannel(True)

            self.loop.call_soon_threadsafe(switch_on)
            return True
        except Exception:
            logger.exception("enable_video failed")
            return False

    def close(self) -> None:
        if self.stop_timer:
            self.stop_timer.cancel()
            self.stop_timer = None
        try:
            self.stop_movement()
        except Exception:  # noqa: BLE001
            pass

        async def _disconnect() -> None:
            try:
                await self.conn.disconnect()
            except Exception:  # noqa: BLE001
                pass

        try:
            if self.loop.is_running():
                asyncio.run_coroutine_threadsafe(_disconnect(), self.loop).result(
                    timeout=10.0
                )
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:  # noqa: BLE001
            logger.debug("session close failed", exc_info=True)
        if self.thread.is_alive():
            self.thread.join(timeout=5.0)


async def start_connection(
    connection: Any, *, connection_mode: ConnectionMode
) -> tuple[bool, str, Go2WebRTCSession | None, str]:
    """Start connection inside a Go2WebRTCSession and verify data/velocity channels."""
    secrets: list[str] = []
    session = Go2WebRTCSession(connection)
    try:
        session.connect_and_verify()
        if not session.datachannel_ok:
            session.close()
            return False, "WEBRTC_DATA_CHANNEL_FAILED", None, "data channel not open"
        if not session.velocity_channel_ok:
            session.close()
            return (
                False,
                "VELOCITY_CHANNEL_UNAVAILABLE",
                None,
                "velocity channel unavailable",
            )
        return True, "OK", session, f"{connection_mode} connected"
    except Exception as exc:  # noqa: BLE001
        session.close()
        msg = redact_secrets(str(exc), secrets)
        lower = msg.lower()
        if "aes" in lower and ("reject" in lower or "required" in lower):
            code = "AES_KEY_INVALID" if "reject" in lower else "AES_KEY_REQUIRED"
            return False, code, None, msg
        if "datachannel" in lower:
            return False, "WEBRTC_DATA_CHANNEL_FAILED", None, msg
        if "velocity" in lower:
            return False, "VELOCITY_CHANNEL_UNAVAILABLE", None, msg
        code = (
            "LOCAL_AP_SIGNALING_FAILED"
            if connection_mode == "ap"
            else "LOCAL_STA_SIGNALING_FAILED"
        )
        return False, code, None, msg
