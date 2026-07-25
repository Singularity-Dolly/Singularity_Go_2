#!/usr/bin/env python3
"""Minimal Go2 WASD walker — no Dolly, no follow, no detector.

Usage:
  source /home/nero/Singularity_Go_2/.venv/bin/activate
  python robot-console/scripts/go2_wasd.py

Keys (type letter + Enter):
  w/a/s/d  walk
  q/e      strafe
  b        BalanceStand (stand up)
  x        StopMove
  quit     exit
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running as a file without install.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from singularity_go2_console.aes import load_aes_key
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD, WebRTCConnectionMethod
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection

# Ordinary walking speed (not the ultra-slow 0.15 demo crawl).
MAX_X = 0.45
MAX_Y = 0.30
MAX_Z = 0.80
HOLD_S = 1.0
RATE_HZ = 15.0


async def sport(conn: UnitreeWebRTCConnection, api_id: int, parameter: dict | None = None) -> None:
    opts: dict = {"api_id": api_id}
    if parameter is not None:
        payload = dict(parameter)
        if "z" in payload and "yaw" not in payload:
            payload["yaw"] = payload["z"]
        opts["parameter"] = payload
    try:
        await asyncio.wait_for(
            conn.datachannel.pub_sub.publish_request_new(RTC_TOPIC["SPORT_MOD"], opts),
            timeout=0.6,
        )
    except asyncio.TimeoutError:
        pass  # command was still sent


async def select_normal(conn: UnitreeWebRTCConnection) -> None:
    await asyncio.wait_for(
        conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"],
            {"api_id": 1002, "parameter": {"name": "normal"}},
        ),
        timeout=3.0,
    )


async def publish_move(conn: UnitreeWebRTCConnection, x: float, y: float, z: float) -> None:
    await sport(
        conn,
        SPORT_CMD["Move"],
        {"x": float(x), "y": float(y), "z": float(z)},
    )


async def stop(conn: UnitreeWebRTCConnection) -> None:
    await sport(conn, SPORT_CMD["StopMove"])


async def balance(conn: UnitreeWebRTCConnection) -> None:
    await sport(conn, SPORT_CMD["BalanceStand"])
    await asyncio.sleep(1.0)


async def hold_move(
    conn: UnitreeWebRTCConnection, x: float, y: float, z: float, seconds: float = HOLD_S
) -> None:
    period = 1.0 / RATE_HZ
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        await publish_move(conn, x, y, z)
        await asyncio.sleep(period)
    await stop(conn)


async def main() -> int:
    key = load_aes_key(key_file=str(Path.home() / ".config/go2ctl/aes_key")).value
    print("Connecting LocalAP…")
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP, aes_128_key=key)
    await conn.connect()
    try:
        await conn.datachannel.disableTrafficSaving(True)
    except Exception:
        pass

    print("SelectMode(normal)…")
    await select_normal(conn)
    print("BalanceStand… (robot should stand)")
    await balance(conn)
    print(
        "READY — type command + Enter:\n"
        "  w forward | s back | a left turn | d right turn\n"
        "  q/e strafe | b stand | x stop | quit\n"
    )

    try:
        while True:
            try:
                cmd = (await asyncio.to_thread(input, "> ")).strip().lower()
            except EOFError:
                break
            if cmd in {"quit", "exit", "esc"}:
                break
            if cmd == "b":
                print("BalanceStand…")
                await balance(conn)
                print("ok")
                continue
            if cmd in {"x", "stop"}:
                await stop(conn)
                print("StopMove")
                continue
            mapping = {
                "w": (MAX_X, 0.0, 0.0),
                "s": (-MAX_X, 0.0, 0.0),
                "a": (0.0, 0.0, MAX_Z),
                "d": (0.0, 0.0, -MAX_Z),
                "q": (0.0, MAX_Y, 0.0),
                "e": (0.0, -MAX_Y, 0.0),
            }
            if cmd not in mapping:
                print("unknown — use w/a/s/d/q/e/b/x/quit")
                continue
            x, y, z = mapping[cmd]
            print(f"Move x={x} y={y} z={z} for {HOLD_S}s")
            await hold_move(conn, x, y, z)
            print("stopped")
    finally:
        try:
            await stop(conn)
        except Exception:
            pass
        try:
            await conn.disconnect()
        except Exception:
            pass
        print("disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
