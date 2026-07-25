"""Terminal UI for go2ctl (curses primary, line-mode fallback). No Pygame window.

WASD teleop uses a deadman hold window (GO2CTL_KEY_HOLD_MS) so a single key
event keeps publishing at ~20 Hz until the deadline, then sends exactly one zero.

On WSL / Cursor / VS Code terminals, curses often fails to capture keys — use
--line-mode or GO2CTL_LINE_MODE=1 (type w + Enter).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Callable

from singularity_go2_console import SAFETY_WARNING
from singularity_go2_console.config import (
    TELEOP_PUBLISH_HZ,
    Go2CtlConfig,
    clamp_key_hold_ms,
)
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.state import ControllerMode

logger = logging.getLogger("go2ctl.console")

HELP = (
    "W/S forward/back  A/D turn  Q/E strafe | M manual  F follow  R reacquire  "
    "X hold  I status  L logs | SPACE estop  ESC quit | Ctrl=slow Shift=boost"
)

MOTION_KEYS = frozenset({"w", "a", "s", "d", "q", "e"})
Clock = Callable[[], float]


def _try_import_curses() -> Any | None:
    try:
        import curses

        return curses
    except Exception:
        return None


def prefer_line_mode(*, force: bool | None = None) -> bool:
    """Prefer line-mode when curses cannot reliably capture keyboard input."""
    if force is not None:
        return bool(force)
    env = os.environ.get("GO2CTL_LINE_MODE", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    term = (os.environ.get("TERM") or "").strip().lower()
    if term in {"", "dumb", "unknown"}:
        return True
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return True
    return False


class TerminalConsole:
    def __init__(
        self,
        controller: Go2Controller,
        config: Go2CtlConfig,
        *,
        clock: Clock | None = None,
        force_line_mode: bool | None = None,
    ) -> None:
        self.controller = controller
        self.config = config
        self._clock: Clock = clock or time.monotonic
        self._force_line_mode = force_line_mode
        self._show_logs = False
        self._active_keys: set[str] = set()
        self._active_vx = 0.0
        self._active_vy = 0.0
        self._active_wz = 0.0
        self._active_until: float | None = None
        self._need_zero = False
        self._slow = False
        self._boost = False
        self._running = True
        self._recent: list[str] = []
        self._teleop_period_s = 1.0 / TELEOP_PUBLISH_HZ
        self._last_teleop_publish_at: float | None = None

        def _on_event(event: Any) -> None:
            line = f"{event.type.value} {event.payload}"
            self._recent.append(line)
            self._recent = self._recent[-30:]

        controller.subscribe(_on_event)

    @property
    def key_hold_ms(self) -> int:
        return clamp_key_hold_ms(self.config.key_hold_ms)

    @property
    def is_active(self) -> bool:
        return self._active_until is not None and self._clock() < self._active_until

    def clear_active_vector(self) -> None:
        """Drop the deadman vector without publishing (caller publishes zero)."""
        self._active_keys.clear()
        self._active_vx = 0.0
        self._active_vy = 0.0
        self._active_wz = 0.0
        self._active_until = None
        self._need_zero = False
        self._last_teleop_publish_at = None
        self._boost = False

    def note_motion_key(self, key: str, *, boost: bool = False) -> None:
        """Activate / extend deadman window for a motion key."""
        k = key.lower()
        if k not in MOTION_KEYS:
            return
        if boost:
            self._boost = True
        self._active_keys.add(k)
        self._recompute_active_vector()
        self._active_until = self._clock() + (self.key_hold_ms / 1000.0)
        self._need_zero = False

    def _recompute_active_vector(self) -> None:
        cfg = self.config
        vx = vy = wz = 0.0
        if "w" in self._active_keys:
            vx += cfg.max_forward_speed
        if "s" in self._active_keys:
            vx -= cfg.max_reverse_speed
        if "q" in self._active_keys:
            vy += cfg.max_strafe_speed
        if "e" in self._active_keys:
            vy -= cfg.max_strafe_speed
        if "a" in self._active_keys:
            wz += cfg.max_angular_speed
        if "d" in self._active_keys:
            wz -= cfg.max_angular_speed

        mult = 1.0
        if self._slow:
            mult = cfg.slow_multiplier
        elif self._boost:
            mult = cfg.boost_multiplier

        self._active_vx, self._active_vy, self._active_wz = cfg.clamp_velocity(
            vx * mult, vy * mult, wz * mult
        )

    async def teleop_tick(self) -> None:
        """20 Hz deadman publisher for MANUAL mode."""
        mode = self.controller.mode
        if mode != ControllerMode.MANUAL:
            if self._active_until is not None or self._need_zero:
                self.clear_active_vector()
            return

        now = self._clock()
        if self._active_until is not None and now < self._active_until:
            # Pace publishes at ~TELEOP_PUBLISH_HZ when the outer loop is faster.
            if (
                self._last_teleop_publish_at is not None
                and (now - self._last_teleop_publish_at) < (self._teleop_period_s * 0.5)
            ):
                return
            ttl = max(self.config.manual_ttl_ms, int(self._teleop_period_s * 1000) * 2)
            result = await self.controller.set_manual_velocity(
                self._active_vx,
                self._active_vy,
                self._active_wz,
                ttl_ms=ttl,
            )
            if result.ok:
                self._last_teleop_publish_at = now
                self._need_zero = True
            return

        if self._need_zero or self._active_until is not None:
            # Deadline expired (or force-clear path): exactly one zero.
            await self.controller.set_manual_velocity(0.0, 0.0, 0.0, ttl_ms=self.config.manual_ttl_ms)
            self.clear_active_vector()

    async def handle_estop(self, reason: str = "space") -> None:
        self.clear_active_vector()
        await self.controller.emergency_stop(reason)

    async def handle_hold(self) -> None:
        self.clear_active_vector()
        await self.controller.set_manual_velocity(0.0, 0.0, 0.0, ttl_ms=self.config.manual_ttl_ms)
        await self.controller.hold()

    async def handle_shutdown(self) -> None:
        self.clear_active_vector()
        await self.controller.set_manual_velocity(0.0, 0.0, 0.0, ttl_ms=self.config.manual_ttl_ms)
        await self.controller.shutdown()

    async def run(self) -> int:
        if prefer_line_mode(force=self._force_line_mode):
            logger.warning(
                "Using line-mode console (curses keyboard capture unreliable). "
                "Type commands then Enter: m, w, a, s, d, x, quit"
            )
            return await self._line_mode()
        curses = _try_import_curses()
        if curses is None:
            logger.warning("curses unavailable; using line mode")
            return await self._line_mode()
        try:
            return await asyncio.to_thread(self._curses_main, curses)
        except Exception:
            logger.exception("curses UI failed; falling back to line mode")
            self.clear_active_vector()
            try:
                await self.controller.set_manual_velocity(0.0, 0.0, 0.0)
            except Exception:  # noqa: BLE001
                logger.exception("zero during curses fallback failed")
            return await self._line_mode()

    def _curses_main(self, curses: Any) -> int:
        return curses.wrapper(lambda stdscr: self._curses_loop(stdscr, curses))

    def _curses_loop(self, stdscr: Any, curses: Any) -> int:
        curses.curs_set(0)
        stdscr.nodelay(True)
        # ~20 Hz teleop cadence
        stdscr.timeout(int(1000 / TELEOP_PUBLISH_HZ))
        loop = asyncio.new_event_loop()
        try:
            while self._running:
                self._handle_keys(stdscr, loop)
                loop.run_until_complete(self.controller.tick_watchdogs())
                loop.run_until_complete(self.teleop_tick())
                self._draw(stdscr, loop, curses)
        except Exception:
            logger.exception("console loop crashed; forcing zero")
            try:
                loop.run_until_complete(self.handle_shutdown())
            except Exception:  # noqa: BLE001
                logger.exception("shutdown after crash failed")
            raise
        finally:
            try:
                loop.run_until_complete(self.handle_shutdown())
            except Exception:  # noqa: BLE001
                logger.exception("final shutdown failed")
            loop.close()
        return 0

    def _handle_keys(self, stdscr: Any, loop: asyncio.AbstractEventLoop) -> None:
        while True:
            try:
                ch = stdscr.getch()
            except Exception:
                break
            if ch == -1:
                break
            key = ch
            if key == 27:  # ESC
                self._running = False
                loop.run_until_complete(self.handle_shutdown())
                return
            if key == ord(" "):
                loop.run_until_complete(self.handle_estop("space"))
                continue

            char = chr(key) if 0 <= key < 256 else ""

            if char.lower() == "m":
                loop.run_until_complete(self.controller.start_manual())
            elif char.lower() == "f":
                loop.run_until_complete(self.controller.start_follow_front_person())
            elif char.lower() == "r":
                loop.run_until_complete(self.controller.reacquire_front_person())
            elif char.lower() == "x":
                loop.run_until_complete(self.handle_hold())
            elif char.lower() == "i":
                status = loop.run_until_complete(self.controller.get_status())
                self._recent.append(str(status.to_dict()))
            elif char.lower() == "l":
                self._show_logs = not self._show_logs
            elif char == "[":
                self._slow = not self._slow
                if self._active_keys:
                    self._recompute_active_vector()
            elif char == "]":
                self._boost = not self._boost
                if self._active_keys:
                    self._recompute_active_vector()
            elif char.lower() in MOTION_KEYS:
                if self.controller.mode != ControllerMode.MANUAL:
                    loop.run_until_complete(self.controller.start_manual())
                self.note_motion_key(char.lower(), boost=char.isupper())

    def _draw(self, stdscr: Any, loop: asyncio.AbstractEventLoop, curses: Any) -> None:
        stdscr.erase()
        status = loop.run_until_complete(self.controller.get_status())
        hold_left_ms = 0
        if self._active_until is not None:
            hold_left_ms = max(0, int((self._active_until - self._clock()) * 1000))
        rows = [
            "go2ctl — Unitree Go2 Console",
            SAFETY_WARNING,
            "",
            f"IP={status.robot_ip}  connected={status.connected}  mode={status.mode}  estop={status.estop}",
            f"camera_ready={status.camera_ready}  frame_age_ms={status.last_frame_age_ms}",
            f"detector_ready={status.detector_ready}  target_visible={status.target_visible}  "
            f"conf={status.target_confidence}  bbox={status.target_bbox}",
            f"lost_frames={status.target_lost_frames}  owner={status.velocity_owner}  "
            f"vx={status.vx:.3f} vy={status.vy:.3f} wz={status.wz:.3f}",
            f"wdog manual={status.manual_watchdog_ok} camera={status.camera_watchdog_ok} "
            f"conn={status.connection_watchdog_ok}",
            f"last_command={status.last_command}  last_error={status.last_error}",
            f"slow={self._slow} boost={self._boost} mock={status.mock} "
            f"hold_ms={self.key_hold_ms} hold_left_ms={hold_left_ms} keys={sorted(self._active_keys)}",
            "",
            HELP,
            "",
            "Recent events:",
        ]
        cols = 80
        try:
            cols = max(curses.COLS - 1, 1)
        except Exception:
            pass
        for i, line in enumerate(rows):
            try:
                stdscr.addstr(i, 0, line[:cols])
            except Exception:
                pass
        base = len(rows)
        for j, ev in enumerate(self._recent[-8:]):
            try:
                stdscr.addstr(base + j, 0, ev[:cols])
            except Exception:
                pass
        stdscr.refresh()

    async def _line_mode(self) -> int:
        print(SAFETY_WARNING)
        print(HELP)
        print(
            "\n=== LINE MODE (keyboard works here) ===\n"
            "Type a command, then press Enter:\n"
            "  m       = MANUAL (start driving)\n"
            "  w/a/s/d = move (hold window ~400ms)\n"
            "  q/e     = strafe\n"
            "  x       = IDLE / stop\n"
            "  space   = E-stop\n"
            "  i       = status\n"
            "  quit    = exit\n"
            f"Deadman hold: {self.key_hold_ms} ms @ {TELEOP_PUBLISH_HZ:.0f} Hz\n"
        )
        try:
            while self._running:
                await self.controller.tick_watchdogs()
                await self.teleop_tick()
                status = await self.controller.get_status()
                print(
                    f"[{status.mode}] connected={status.connected} "
                    f"owner={status.velocity_owner} "
                    f"vx={status.vx:.2f} wz={status.wz:.2f} "
                    f"err={status.last_error}"
                )
                try:
                    line = await asyncio.to_thread(input, "> ")
                except EOFError:
                    break
                cmd = line.strip().lower()
                if cmd in {"quit", "esc", "exit", "q!"}:
                    break
                if cmd in {"space", "estop"}:
                    await self.handle_estop("line")
                elif cmd == "m":
                    result = await self.controller.start_manual()
                    print(f"manual: ok={result.ok} code={result.code.value} {result.message}")
                elif cmd == "f":
                    await self.controller.start_follow_front_person()
                elif cmd == "r":
                    await self.controller.reacquire_front_person()
                elif cmd == "x":
                    await self.handle_hold()
                    print("hold -> IDLE")
                elif cmd == "i":
                    print((await self.controller.get_status()).to_dict())
                elif cmd in MOTION_KEYS:
                    if self.controller.mode != ControllerMode.MANUAL:
                        result = await self.controller.start_manual()
                        print(
                            f"auto manual: ok={result.ok} "
                            f"code={result.code.value} {result.message}"
                        )
                        if not result.ok:
                            continue
                    self.note_motion_key(cmd)
                    # Drain the hold window with teleop ticks (mock/tests/line mode).
                    while self.is_active:
                        await self.teleop_tick()
                        await self.controller.tick_watchdogs()
                        await asyncio.sleep(self._teleop_period_s)
                    await self.teleop_tick()
                    status = await self.controller.get_status()
                    print(
                        f"moved {cmd}: vx={status.vx:.3f} vy={status.vy:.3f} "
                        f"wz={status.wz:.3f} err={status.last_error}"
                    )
                elif cmd == "l":
                    self._show_logs = not self._show_logs
                else:
                    print("unknown command — try: m, w, a, s, d, x, space, quit")
                await asyncio.sleep(0.05)
        finally:
            await self.handle_shutdown()
        return 0


async def run_console(
    controller: Go2Controller,
    config: Go2CtlConfig,
    *,
    force_line_mode: bool | None = None,
) -> int:
    ui = TerminalConsole(
        controller, config, force_line_mode=force_line_mode
    )
    return await ui.run()
