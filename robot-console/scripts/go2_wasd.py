#!/usr/bin/env python3
"""Minimal Go2 WASD walker — continuous hold like a game.

Hold W/A/S/D to walk (no Enter). Release keys → StopMove.
  b = BalanceStand
  x / space = stop
  ESC / q = quit

Usage:
  source /home/nero/Singularity_Go_2/.venv/bin/activate
  go2ctl wasd
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from singularity_go2_console.aes import load_aes_key
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD, WebRTCConnectionMethod
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection

# Ordinary walk speed
MAX_X = 0.45
MAX_Y = 0.30
MAX_Z = 0.80
RATE_HZ = 20.0
# Cover OS key-repeat delay so hold feels continuous (no stutter).
KEY_HOLD_S = 0.55


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
            timeout=0.4,
        )
    except asyncio.TimeoutError:
        pass


async def select_normal(conn: UnitreeWebRTCConnection) -> None:
    await asyncio.wait_for(
        conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"],
            {"api_id": 1002, "parameter": {"name": "normal"}},
        ),
        timeout=3.0,
    )


async def publish_move(conn: UnitreeWebRTCConnection, x: float, y: float, z: float) -> None:
    await sport(conn, SPORT_CMD["Move"], {"x": float(x), "y": float(y), "z": float(z)})


async def stop(conn: UnitreeWebRTCConnection) -> None:
    await sport(conn, SPORT_CMD["StopMove"])


async def balance(conn: UnitreeWebRTCConnection) -> None:
    await sport(conn, SPORT_CMD["BalanceStand"])
    await asyncio.sleep(1.0)


def _vector_from_keys(keys: set[str]) -> tuple[float, float, float]:
    x = y = z = 0.0
    if "w" in keys:
        x += MAX_X
    if "s" in keys:
        x -= MAX_X
    if "q" in keys:
        y += MAX_Y
    if "e" in keys:
        y -= MAX_Y
    if "a" in keys:
        z += MAX_Z
    if "d" in keys:
        z -= MAX_Z
    return (
        max(-MAX_X, min(MAX_X, x)),
        max(-MAX_Y, min(MAX_Y, y)),
        max(-MAX_Z, min(MAX_Z, z)),
    )


async def run_continuous(conn: UnitreeWebRTCConnection) -> None:
    """Hold-to-move teleop (curses). No Enter required."""
    import curses

    active: set[str] = set()
    active_until = 0.0
    need_stop = False
    period = 1.0 / RATE_HZ
    running = True

    def _ui(stdscr: object) -> None:
        nonlocal active, active_until, need_stop, running
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(int(period * 1000))
        stdscr.clear()
        stdscr.addstr(0, 0, "Go2 WASD — HOLD keys to walk (game style)")
        stdscr.addstr(1, 0, "W/S move  A/D turn  Q/E strafe | B stand | X/SPACE stop | ESC quit")
        stdscr.refresh()

        loop = asyncio.new_event_loop()
        try:
            while running:
                # Drain all pending key events this frame.
                while True:
                    ch = stdscr.getch()
                    if ch == -1:
                        break
                    if ch in (27, ord("q")):  # ESC / q
                        running = False
                        break
                    if ch in (ord(" "), ord("x"), ord("X")):
                        active.clear()
                        active_until = 0.0
                        need_stop = True
                        loop.run_until_complete(stop(conn))
                        need_stop = False
                        continue
                    if ch in (ord("b"), ord("B")):
                        stdscr.addstr(3, 0, "BalanceStand...          ")
                        stdscr.refresh()
                        loop.run_until_complete(balance(conn))
                        stdscr.addstr(3, 0, "BalanceStand OK          ")
                        stdscr.refresh()
                        continue
                    char = chr(ch).lower() if 0 <= ch < 256 else ""
                    if char in {"w", "a", "s", "d", "q", "e"}:
                        active.add(char)
                        # Opposites cancel for clean game feel.
                        if char == "w":
                            active.discard("s")
                        elif char == "s":
                            active.discard("w")
                        elif char == "a":
                            active.discard("d")
                        elif char == "d":
                            active.discard("a")
                        elif char == "q":
                            active.discard("e")
                        elif char == "e":
                            active.discard("q")
                        active_until = time.monotonic() + KEY_HOLD_S

                now = time.monotonic()
                if active and now < active_until:
                    vx, vy, wz = _vector_from_keys(active)
                    loop.run_until_complete(publish_move(conn, vx, vy, wz))
                    need_stop = True
                    stdscr.addstr(
                        3,
                        0,
                        f"WALK keys={sorted(active)}  vx={vx:.2f} vy={vy:.2f} wz={wz:.2f}   ",
                    )
                elif need_stop:
                    loop.run_until_complete(stop(conn))
                    need_stop = False
                    active.clear()
                    stdscr.addstr(3, 0, "STOP                                          ")
                else:
                    stdscr.addstr(3, 0, "idle — hold W/A/S/D                           ")
                stdscr.refresh()
        finally:
            try:
                loop.run_until_complete(stop(conn))
            except Exception:
                pass
            loop.close()

    curses.wrapper(_ui)


async def run_line_fallback(conn: UnitreeWebRTCConnection) -> None:
    print("curses unavailable — line mode (type w + Enter, repeats while you spam keys)")
    print("Commands: w/a/s/d/q/e, b, x, quit")
    while True:
        try:
            cmd = (await asyncio.to_thread(input, "> ")).strip().lower()
        except EOFError:
            break
        if cmd in {"quit", "exit", "esc"}:
            break
        if cmd == "b":
            await balance(conn)
            continue
        if cmd in {"x", "stop", "space"}:
            await stop(conn)
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
            print("unknown")
            continue
        x, y, z = mapping[cmd]
        # Spam-friendly: keep moving until user hits x (short burst chain).
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            await publish_move(conn, x, y, z)
            await asyncio.sleep(1.0 / RATE_HZ)
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
    print("BalanceStand… (wait for stand)")
    await balance(conn)
    print("READY — HOLD W/A/S/D to walk (like a game). ESC quit.")

    try:
        try:
            import curses  # noqa: F401

            await run_continuous(conn)
        except Exception as exc:
            print(f"continuous UI failed ({exc}); line fallback")
            await run_line_fallback(conn)
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
