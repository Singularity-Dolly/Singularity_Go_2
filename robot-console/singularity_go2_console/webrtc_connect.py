"""Unitree WebRTC connection helpers for AP/STA + AES (no secret logging).

Exact verified constructors (no AP↔STA fallback, no silent DimOS LocalSTA swap):

  LocalAP:
    UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP, aes_128_key=key)

  LocalSTA:
    UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA, ip=robot_ip, aes_128_key=key
    )

Locomotion transport uses sport-mode request API (SPORT_MOD Move / StopMove).
WIRELESS_CONTROLLER is diagnostics-only and is not the default movement path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from typing import Any, Callable, Literal

from singularity_go2_console.aes import redact_secrets

logger = logging.getLogger("go2ctl.webrtc")

ConnectionMode = Literal["ap", "sta"]
LOCAL_AP_IP = "192.168.12.1"

# Motion switcher API IDs (Unitree motion_switcher service; not SPORT_CMD).
MOTION_SWITCHER_CHECK_MODE = 1001
MOTION_SWITCHER_SELECT_MODE = 1002
MOTION_SWITCHER_RELEASE_MODE = 1003
REQUIRED_MOTION_MODE = "normal"


def _sport_constants() -> tuple[dict[str, Any], dict[str, int]]:
    from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD

    return RTC_TOPIC, SPORT_CMD


def parse_motion_mode_name(response: Any) -> str | None:
    """Extract motion-switcher mode name from a publish_request_new response."""

    def _walk(node: Any, depth: int = 0) -> str | None:
        if depth > 8 or node is None:
            return None
        if isinstance(node, str):
            text = node.strip()
            if not text:
                return None
            if text.startswith("{") or text.startswith("["):
                try:
                    return _walk(json.loads(text), depth + 1)
                except json.JSONDecodeError:
                    lower = text.lower()
                    if lower in {"normal", "ai", "advanced", "sport", "mcf"}:
                        return lower
                    return None
            lower = text.lower()
            if lower in {"normal", "ai", "advanced", "sport", "mcf"}:
                return lower
            return None
        if isinstance(node, dict):
            for key in ("name", "mode", "Name", "Mode"):
                val = node.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip().lower()
            for key in ("data", "parameter", "body", "info", "result"):
                if key in node:
                    found = _walk(node.get(key), depth + 1)
                    if found:
                        return found
            for val in node.values():
                found = _walk(val, depth + 1)
                if found:
                    return found
            return None
        if isinstance(node, (list, tuple)):
            for item in node:
                found = _walk(item, depth + 1)
                if found:
                    return found
        return None

    return _walk(response)


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

    def __init__(
        self,
        conn: Any,
        *,
        allow_normal_mode_switch: bool = False,
        motion_mode_cache_s: float = 2.0,
        mode_switch_timeout_s: float = 5.0,
    ) -> None:
        self.conn = conn
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.allow_normal_mode_switch = bool(allow_normal_mode_switch)
        self.motion_mode_cache_s = float(motion_mode_cache_s)
        self.mode_switch_timeout_s = float(mode_switch_timeout_s)
        self.datachannel_ok = False
        self.velocity_channel_ok = False
        self.last_error_code: str | None = None
        self.last_error_message: str = ""
        self.sport_requests: list[dict[str, Any]] = []
        self.wireless_diagnostics: list[dict[str, float]] = []
        self._cached_motion_mode: str | None = None
        self._cached_motion_mode_at: float | None = None
        self._move_active = False
        self._operator_selected_normal = False
        self.last_motion_raw: Any | None = None
        self.last_motion_mode_seen: str | None = None

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
                pub_sub, "publish_request_new"
            )
            if not self.velocity_channel_ok:
                raise RuntimeError("sport-mode request channel unavailable")

        try:
            self._run(_async_connect(), timeout=60.0)
        except Exception:
            self.close()
            raise

    def _record_sport(self, name: str, **payload: Any) -> None:
        RTC_TOPIC, SPORT_CMD = _sport_constants()
        entry = {
            "topic": RTC_TOPIC["SPORT_MOD"],
            "api": name,
            "api_id": SPORT_CMD[name],
            **payload,
        }
        self.sport_requests.append(entry)

    def publish_wireless(self, lx: float, ly: float, rx: float, ry: float = 0.0) -> bool:
        """Diagnostics-only helper. Not used for default locomotion."""
        try:
            from unitree_webrtc_connect.constants import RTC_TOPIC

            def _pub() -> None:
                self.conn.datachannel.pub_sub.publish_without_callback(
                    RTC_TOPIC["WIRELESS_CONTROLLER"],
                    data={"lx": lx, "ly": ly, "rx": rx, "ry": ry},
                )

            self.loop.call_soon_threadsafe(_pub)
            self.wireless_diagnostics.append(
                {"lx": float(lx), "ly": float(ly), "rx": float(rx), "ry": float(ry)}
            )
            return True
        except Exception:
            logger.exception("wireless diagnostic publish failed")
            return False

    async def _async_get_motion_mode(self, *, use_cache: bool = True) -> str | None:
        now = time.monotonic()
        if (
            use_cache
            and self._cached_motion_mode is not None
            and self._cached_motion_mode_at is not None
            and (now - self._cached_motion_mode_at) < self.motion_mode_cache_s
        ):
            return self._cached_motion_mode

        from unitree_webrtc_connect.constants import RTC_TOPIC

        # Short timeout — a hung mode query must not freeze the console keyboard.
        response = await asyncio.wait_for(
            self.conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"],
                {"api_id": MOTION_SWITCHER_CHECK_MODE},
            ),
            timeout=2.0,
        )
        self.last_motion_raw = response
        name = parse_motion_mode_name(response)
        self.last_motion_mode_seen = name
        self._cached_motion_mode = name
        self._cached_motion_mode_at = time.monotonic()
        return name

    def _motion_allows_move(self, mode: str | None) -> bool:
        if mode == REQUIRED_MOTION_MODE:
            return True
        # After an explicit operator SelectMode("normal") ACK, allow Move even if
        # CheckMode payloads are opaque or briefly stale (common on some firmwares).
        if self._operator_selected_normal:
            return True
        return False

    def _publish_sport_request_nowait(
        self, api_name: str, parameter: dict[str, Any] | None = None
    ) -> None:
        """Fire-and-forget sport request (Move/StopMove). Avoids UI freezes."""
        from unitree_webrtc_connect.constants import DATA_CHANNEL_TYPE, RTC_TOPIC, SPORT_CMD

        generated_id = int(time.time() * 1000) % 2147483648 + random.randint(0, 1000)
        request_payload: dict[str, Any] = {
            "header": {
                "identity": {
                    "id": generated_id,
                    "api_id": SPORT_CMD[api_name],
                }
            },
            "parameter": "",
        }
        if parameter is not None:
            request_payload["parameter"] = (
                parameter
                if isinstance(parameter, str)
                else json.dumps(parameter)
            )
        self.conn.datachannel.pub_sub.publish_without_callback(
            RTC_TOPIC["SPORT_MOD"],
            data=request_payload,
            msg_type=DATA_CHANNEL_TYPE["REQUEST"],
        )
        self._record_sport(
            api_name,
            **(
                {
                    "x": float(parameter.get("x", 0.0)),
                    "y": float(parameter.get("y", 0.0)),
                    "z": float(parameter.get("z", 0.0)),
                }
                if isinstance(parameter, dict)
                else {}
            ),
        )

    def get_motion_mode(self, *, use_cache: bool = True) -> tuple[str | None, str, str]:
        """Read-only motion mode query (MOTION_SWITCHER api_id 1001)."""
        try:
            name = self._run(
                self._async_get_motion_mode(use_cache=use_cache), timeout=5.0
            )
            return name, "OK", f"motion mode={name}"
        except Exception as exc:  # noqa: BLE001
            self.last_error_code = "INTERNAL_ERROR"
            self.last_error_message = str(exc)
            return None, "INTERNAL_ERROR", str(exc)

    async def _async_switch_to_normal(self) -> tuple[bool, str, str]:
        if not self.allow_normal_mode_switch:
            return (
                False,
                "MOTION_MODE_SWITCH_DISABLED",
                "Pass --allow-normal-mode-switch to enable operator mode switch",
            )
        from unitree_webrtc_connect.constants import RTC_TOPIC

        # Unitree MotionSwitcher: release active mode, then select normal.
        try:
            await asyncio.wait_for(
                self.conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["MOTION_SWITCHER"],
                    {"api_id": MOTION_SWITCHER_RELEASE_MODE},
                ),
                timeout=2.0,
            )
        except Exception:  # noqa: BLE001
            logger.debug("motion ReleaseMode failed/ignored", exc_info=True)

        await asyncio.wait_for(
            self.conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"],
                {
                    "api_id": MOTION_SWITCHER_SELECT_MODE,
                    "parameter": {"name": REQUIRED_MOTION_MODE},
                },
            ),
            timeout=3.0,
        )
        self._operator_selected_normal = True
        self._cached_motion_mode = None
        self._cached_motion_mode_at = None

        deadline = time.monotonic() + self.mode_switch_timeout_s
        last_seen: str | None = None
        while time.monotonic() < deadline:
            try:
                name = await self._async_get_motion_mode(use_cache=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("motion CheckMode during switch: %s", exc)
                await asyncio.sleep(0.15)
                continue
            last_seen = name
            if name == REQUIRED_MOTION_MODE:
                return True, "OK", "motion mode is normal"
            # Opaque CheckMode after explicit SelectMode — treat as ready.
            if name is None and self._operator_selected_normal:
                return (
                    True,
                    "OK",
                    "SelectMode(normal) sent; CheckMode opaque — allowing Move",
                )
            await asyncio.sleep(0.15)
        return (
            False,
            "MOTION_MODE_NOT_NORMAL",
            "timed out waiting for motion mode normal confirmation "
            f"(last_seen={last_seen!r}, raw={self.last_motion_raw!r})",
        )

    def ensure_normal_mode(self) -> tuple[bool, str, str]:
        """Operator-gated switch to normal mode (disabled by default)."""
        try:
            ok, code, message = self._run(
                self._async_switch_to_normal(), timeout=12.0
            )
            self.last_error_code = None if ok else code
            self.last_error_message = "" if ok else message
            return ok, code, message
        except Exception as exc:  # noqa: BLE001
            self.last_error_code = "INTERNAL_ERROR"
            self.last_error_message = str(exc)
            return False, "INTERNAL_ERROR", str(exc)

    async def _async_stop_move(self) -> bool:
        self._publish_sport_request_nowait("StopMove")
        self._move_active = False
        return True

    async def _async_move(self, vx: float, vy: float, wz: float) -> tuple[bool, str, str]:
        try:
            mode = await self._async_get_motion_mode(use_cache=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("motion mode query failed: %s", exc)
            mode = self._cached_motion_mode
            if not self._motion_allows_move(mode):
                try:
                    await self._async_stop_move()
                except Exception:  # noqa: BLE001
                    logger.exception("StopMove after mode-query failure failed")
                return (
                    False,
                    "MOTION_MODE_NOT_NORMAL",
                    f"motion mode query failed: {exc} "
                    f"(last_seen={self.last_motion_mode_seen!r})",
                )

        if not self._motion_allows_move(mode):
            try:
                await self._async_stop_move()
            except Exception:  # noqa: BLE001
                logger.exception("StopMove after non-normal mode failed")
            return (
                False,
                "MOTION_MODE_NOT_NORMAL",
                f"motion mode is {mode!r}; require '{REQUIRED_MOTION_MODE}' "
                f"(raw={self.last_motion_raw!r})",
            )

        self._publish_sport_request_nowait(
            "Move",
            {"x": float(vx), "y": float(vy), "z": float(wz)},
        )
        self._move_active = True
        return True, "OK", "sport Move published"

    def move(self, vx: float, vy: float, wz: float) -> bool:
        """Publish sport-mode Move (x=vx, y=vy, z=wz). Not wireless joystick."""
        try:
            # Fire-and-forget Move + cached mode check — must stay fast for teleop UI.
            ok, code, message = self._run(
                self._async_move(float(vx), float(vy), float(wz)), timeout=2.0
            )
            self.last_error_code = None if ok else code
            self.last_error_message = "" if ok else message
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.exception("sport Move failed")
            self.last_error_code = "VELOCITY_CHANNEL_UNAVAILABLE"
            self.last_error_message = str(exc)
            return False

    def stop_movement(self) -> bool:
        """Publish sport-mode StopMove once for this stop call."""
        try:
            ok = self._run(self._async_stop_move(), timeout=2.0)
            self.last_error_code = None if ok else "VELOCITY_CHANNEL_UNAVAILABLE"
            self.last_error_message = "" if ok else "StopMove failed"
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.exception("sport StopMove failed")
            self.last_error_code = "VELOCITY_CHANNEL_UNAVAILABLE"
            self.last_error_message = str(exc)
            return False

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
    connection: Any,
    *,
    connection_mode: ConnectionMode,
    allow_normal_mode_switch: bool = False,
) -> tuple[bool, str, Go2WebRTCSession | None, str]:
    """Start connection inside a Go2WebRTCSession and verify data/sport channels."""
    secrets: list[str] = []
    session = Go2WebRTCSession(
        connection, allow_normal_mode_switch=allow_normal_mode_switch
    )
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
                "sport-mode request channel unavailable",
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
        if "sport-mode" in lower or "velocity" in lower:
            return False, "VELOCITY_CHANNEL_UNAVAILABLE", None, msg
        code = (
            "LOCAL_AP_SIGNALING_FAILED"
            if connection_mode == "ap"
            else "LOCAL_STA_SIGNALING_FAILED"
        )
        return False, code, None, msg
