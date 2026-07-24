"""Terminal UI for go2ctl (curses primary, line-mode fallback). No Pygame window."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from singularity_go2_console import SAFETY_WARNING
from singularity_go2_console.config import Go2CtlConfig
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.state import ControllerMode

logger = logging.getLogger("go2ctl.console")

HELP = (
    "W/S forward/back  A/D turn  Q/E strafe | M manual  F follow  R reacquire  "
    "X hold  I status  L logs | SPACE estop  ESC quit | Ctrl=slow Shift=boost"
)


def _try_import_curses() -> Any | None:
    try:
        import curses

        return curses
    except Exception:
        return None


class TerminalConsole:
    def __init__(self, controller: Go2Controller, config: Go2CtlConfig) -> None:
        self.controller = controller
        self.config = config
        self._show_logs = False
        self._keys_down: set[str] = set()
        self._slow = False
        self._boost = False
        self._running = True
        self._recent: list[str] = []

        def _on_event(event: Any) -> None:
            line = f"{event.type.value} {event.payload}"
            self._recent.append(line)
            self._recent = self._recent[-30:]

        controller.subscribe(_on_event)

    async def run(self) -> int:
        curses = _try_import_curses()
        if curses is None:
            logger.warning("curses unavailable; using line mode")
            return await self._line_mode()
        try:
            return await asyncio.to_thread(self._curses_main, curses)
        except Exception:
            logger.exception("curses UI failed; falling back to line mode")
            return await self._line_mode()

    def _curses_main(self, curses: Any) -> int:
        return curses.wrapper(lambda stdscr: self._curses_loop(stdscr, curses))

    def _curses_loop(self, stdscr: Any, curses: Any) -> int:
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(50)
        loop = asyncio.new_event_loop()
        try:
            while self._running:
                self._handle_keys(stdscr, loop)
                loop.run_until_complete(self.controller.tick_watchdogs())
                if self.controller.mode == ControllerMode.MANUAL:
                    loop.run_until_complete(self._publish_held_keys())
                self._draw(stdscr, loop, curses)
        finally:
            loop.run_until_complete(self.controller.shutdown())
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
                return
            if key == ord(" "):
                loop.run_until_complete(self.controller.emergency_stop("space"))
                self._keys_down.clear()
                continue

            char = chr(key) if 0 <= key < 256 else ""

            if char.lower() == "m":
                loop.run_until_complete(self.controller.start_manual())
            elif char.lower() == "f":
                loop.run_until_complete(self.controller.start_follow_front_person())
            elif char.lower() == "r":
                loop.run_until_complete(self.controller.reacquire_front_person())
            elif char.lower() == "x":
                loop.run_until_complete(self.controller.hold())
            elif char.lower() == "i":
                status = loop.run_until_complete(self.controller.get_status())
                self._recent.append(str(status.to_dict()))
            elif char.lower() == "l":
                self._show_logs = not self._show_logs
            elif char == "[":
                self._slow = not self._slow
            elif char == "]":
                self._boost = not self._boost
            elif char.lower() in {"w", "a", "s", "d", "q", "e"}:
                self._boost = char.isupper()
                self._keys_down.add(char.lower())

    async def _publish_held_keys(self) -> None:
        if not self._keys_down:
            return
        vx = vy = wz = 0.0
        cfg = self.config
        if "w" in self._keys_down:
            vx += cfg.max_forward_speed
        if "s" in self._keys_down:
            vx -= cfg.max_reverse_speed
        if "q" in self._keys_down:
            vy += cfg.max_strafe_speed
        if "e" in self._keys_down:
            vy -= cfg.max_strafe_speed
        if "a" in self._keys_down:
            wz += cfg.max_angular_speed
        if "d" in self._keys_down:
            wz -= cfg.max_angular_speed

        mult = 1.0
        if self._slow:
            mult = cfg.slow_multiplier
        elif self._boost:
            mult = cfg.boost_multiplier

        vx, vy, wz = cfg.clamp_velocity(vx * mult, vy * mult, wz * mult)
        vx, vy, wz = cfg.clamp_velocity(vx, vy, wz)
        await self.controller.set_manual_velocity(vx, vy, wz, cfg.manual_ttl_ms)
        self._keys_down.clear()
        self._boost = False

    def _draw(self, stdscr: Any, loop: asyncio.AbstractEventLoop, curses: Any) -> None:
        stdscr.erase()
        status = loop.run_until_complete(self.controller.get_status())
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
            f"slow={self._slow} boost={self._boost} mock={status.mock}",
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
        print("Line mode: type commands: w/a/s/d/q/e, m, f, r, x, i, l, space, quit")
        while self._running:
            await self.controller.tick_watchdogs()
            status = await self.controller.get_status()
            print(
                f"[{status.mode}] owner={status.velocity_owner} "
                f"vx={status.vx:.2f} wz={status.wz:.2f} err={status.last_error}"
            )
            try:
                line = await asyncio.to_thread(input, "> ")
            except EOFError:
                break
            cmd = line.strip().lower()
            if cmd in {"quit", "esc", "exit"}:
                break
            if cmd in {"space", "estop"}:
                await self.controller.emergency_stop("line")
            elif cmd == "m":
                await self.controller.start_manual()
            elif cmd == "f":
                await self.controller.start_follow_front_person()
            elif cmd == "r":
                await self.controller.reacquire_front_person()
            elif cmd == "x":
                await self.controller.hold()
            elif cmd == "i":
                print((await self.controller.get_status()).to_dict())
            elif cmd in {"w", "a", "s", "d", "q", "e"}:
                await self.controller.start_manual()
                self._keys_down = {cmd}
                await self._publish_held_keys()
            else:
                print("unknown command")
            await asyncio.sleep(0.05)
        await self.controller.shutdown()
        return 0


async def run_console(controller: Go2Controller, config: Go2CtlConfig) -> int:
    ui = TerminalConsole(controller, config)
    return await ui.run()
