"""go2ctl CLI entry point."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from singularity_go2_console import SAFETY_WARNING, __version__
from singularity_go2_console.config import Go2CtlConfig
from singularity_go2_console.controller import Go2Controller
from singularity_go2_console.logging_config import (
    active_log_file,
    follow_logs,
    read_logs,
    setup_logging,
)
from singularity_go2_console.state import ControllerMode

app = typer.Typer(
    name="go2ctl",
    help="Standalone Unitree Go2 terminal control (independent of Dolly backend).",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _load_config(
    robot_ip: Optional[str] = None,
    mode: Optional[str] = None,
    mock: Optional[bool] = None,
    detect_only: bool = False,
    tracker_only: bool = False,
    connection_mode: Optional[str] = None,
    aes_key_file: Optional[str] = None,
    allow_normal_mode_switch: Optional[bool] = None,
) -> Go2CtlConfig:
    cfg = Go2CtlConfig.from_environ(load_aes=True, require_aes=False)
    overrides = {
        "robot_ip": robot_ip or cfg.robot_ip,
        "start_mode": mode or cfg.start_mode,
        "mock": cfg.mock if mock is None else mock,
        "detect_only": detect_only,
        "tracker_only": tracker_only,
    }
    if connection_mode:
        overrides["connection_mode"] = connection_mode
    if aes_key_file:
        overrides["aes_key_file"] = aes_key_file
        from singularity_go2_console.aes import load_aes_key
        overrides["aes_key"] = load_aes_key(key_file=aes_key_file)
    if allow_normal_mode_switch is not None:
        overrides["allow_normal_mode_switch"] = allow_normal_mode_switch
    return cfg.with_overrides(**overrides)


def _build_controller(
    cfg: Go2CtlConfig, *, enable_video: bool = True
) -> Go2Controller:
    if cfg.mock:
        from singularity_go2_console.testing.fakes import FakeGo2Adapter

        return Go2Controller(FakeGo2Adapter(), cfg, allow_mock=True)

    from singularity_go2_console.dimos_adapter import DimOSGo2Adapter

    adapter = DimOSGo2Adapter(
        detector_model=cfg.detector_model,
        detection_confidence=cfg.detection_confidence,
        connection_mode=cfg.connection_mode,
        aes_key=cfg.aes_key.value if cfg.aes_key else None,
        allow_normal_mode_switch=cfg.allow_normal_mode_switch,
        enable_video=enable_video,
    )
    if adapter.mock:
        raise RuntimeError("Refusing mock adapter in real mode")
    return Go2Controller(adapter, cfg, allow_mock=False)


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    level = "DEBUG" if verbose else os.environ.get("GO2CTL_LOG_LEVEL", "INFO")
    setup_logging(level)


@app.command()
def version() -> None:
    """Print go2ctl version."""
    console.print(f"go2ctl {__version__}")


@app.command()
def doctor() -> None:
    """Environment diagnostics. Does not move the robot."""
    console.print(SAFETY_WARNING)
    rows: list[tuple[str, str, str]] = []

    rows.append(("os", platform.platform(), "info"))
    py_ok = sys.version_info[:2] == (3, 12) or sys.version_info >= (3, 12)
    rows.append(("python", sys.version.split()[0], "ok" if py_ok else "warn"))

    try:
        import dimos

        rows.append(("dimos_import", getattr(dimos, "__file__", "ok"), "ok"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("dimos_import", str(exc), "fail"))

    dimos_cli = shutil.which("dimos")
    rows.append(("dimos_cli", dimos_cli or "not found", "ok" if dimos_cli else "warn"))

    try:
        import turbojpeg  # noqa: F401

        rows.append(("turbojpeg", "available", "ok"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("turbojpeg", str(exc), "warn"))

    try:
        from ultralytics import YOLO  # noqa: F401

        rows.append(("ultralytics", "available", "ok"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("ultralytics", str(exc), "warn"))

    try:
        import curses  # noqa: F401

        rows.append(("terminal_input", "curses available", "ok"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("terminal_input", str(exc), "fail"))

    # Network interfaces
    try:
        hostnames = socket.gethostbyname_ex(socket.gethostname())
        rows.append(("network_hosts", str(hostnames[2]), "info"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("network_hosts", str(exc), "warn"))

    robot_ip = os.environ.get("ROBOT_IP")
    rows.append(("ROBOT_IP", robot_ip or "(unset)", "ok" if robot_ip else "warn"))

    if robot_ip:
        reachable = _ping(robot_ip)
        rows.append(("robot_reachable", str(reachable), "ok" if reachable else "fail"))

    cuda = False
    try:
        import torch

        cuda = bool(torch.cuda.is_available())
        rows.append(("cuda_gpu", str(cuda), "ok" if cuda else "info"))
    except Exception:
        rows.append(("cuda_gpu", "torch not installed", "info"))
    rows.append(("cpu_mode", str(not cuda), "info"))

    log_path = active_log_file()
    rows.append(
        (
            "log_file",
            str(log_path) if log_path else "console-only (file unavailable)",
            "ok" if log_path else "warn",
        )
    )
    rows.append(
        (
            "mock_ready",
            "yes (go2ctl --mock / unit tests)",
            "ok",
        )
    )
    rows.append(
        (
            "real_robot_ready",
            "no — DimOS + ROBOT_IP + preflight required",
            "warn" if not any(r[0] == "dimos_import" and r[2] == "ok" for r in rows) else "info",
        )
    )

    table = Table(title="go2ctl doctor")
    table.add_column("check")
    table.add_column("value")
    table.add_column("status")
    for name, value, status in rows:
        table.add_row(name, value[:120], status)
    console.print(table)
    console.print("doctor does not move the robot.")


def _ping(ip: str, count: int = 2) -> bool:
    param = "-n" if platform.system().lower() == "windows" else "-c"
    try:
        r = subprocess.run(
            ["ping", param, str(count), ip],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


@app.command()
def preflight(
    connection_mode: str = typer.Option("ap", "--connection-mode", help="ap|sta"),
    robot_ip: Optional[str] = typer.Option(None, "--robot-ip", help="Required for STA"),
    aes_key_file: Optional[str] = typer.Option(None, "--aes-key-file", help="AES key file"),
) -> None:
    """Connectivity checks. Must not send non-zero movement."""
    console.print(SAFETY_WARNING)
    from singularity_go2_console.aes import AesKeyError
    from singularity_go2_console.config import DEFAULT_AP_IP

    results: dict[str, Any] = {
        "connection_mode": connection_mode,
        "robot_ip": robot_ip,
        "aes_key_file": aes_key_file,
    }
    if connection_mode.strip().lower() == "sta" and not robot_ip and not os.environ.get("ROBOT_IP"):
        results["ok"] = False
        results["error_code"] = "STA_ROBOT_IP_REQUIRED"
        results["message"] = "STA mode requires --robot-ip"
        console.print(json.dumps(results, indent=2))
        raise typer.Exit(code=2)

    try:
        cfg = _load_config(
            robot_ip=robot_ip,
            mock=False,
            connection_mode=connection_mode,
            aes_key_file=aes_key_file,
        )
        cfg.resolve_aes(require=True)
    except AesKeyError as exc:
        results["ok"] = False
        results["error_code"] = exc.code
        results["message"] = str(exc)
        console.print(json.dumps(results, indent=2))
        raise typer.Exit(code=2)

    if cfg.connection_mode == "sta" and not cfg.robot_ip:
        results["ok"] = False
        results["error_code"] = "STA_ROBOT_IP_REQUIRED"
        console.print(json.dumps(results, indent=2))
        raise typer.Exit(code=2)

    if cfg.connection_mode == "sta" and cfg.robot_ip:
        import time

        t0 = time.perf_counter()
        results["ping"] = _ping(cfg.robot_ip, count=1)
        results["ping_latency_s"] = time.perf_counter() - t0
    else:
        results["robot_ip"] = cfg.robot_ip or DEFAULT_AP_IP

    async def _run() -> dict[str, Any]:
        from singularity_go2_console.dimos_adapter import DimOSGo2Adapter

        adapter = DimOSGo2Adapter(
            connection_mode=cfg.connection_mode,
            aes_key=cfg.aes_key.value if cfg.aes_key else None,
            detector_model=cfg.detector_model,
            detection_confidence=cfg.detection_confidence,
        )
        if adapter.mock:
            raise RuntimeError("Refusing mock adapter in real preflight")
        out = await adapter.run_preflight(cfg.robot_ip)
        merged = dict(results)
        merged.update(out)
        merged.pop("aes_128_key", None)
        return merged

    out = asyncio.run(_run())
    console.print(json.dumps(out, indent=2, default=str))
    if out.get("nonzero_velocity_sent"):
        console.print("[red]preflight sent non-zero velocity[/red]")
        raise typer.Exit(code=4)
    if not out.get("ok"):
        console.print(f"[red]preflight failed: {out.get('error_code')}[/red]")
        raise typer.Exit(code=2)
    console.print("[green]preflight ok (no non-zero movement sent)[/green]")


async def _start_session(
    cfg: Go2CtlConfig,
    *,
    open_console: bool,
    line_mode: bool = False,
) -> int:
    console.print(SAFETY_WARNING)
    if not cfg.mock and cfg.connection_mode == "sta" and not cfg.robot_ip:
        console.print("[red]STA mode requires --robot-ip[/red]")
        return 2
    if not cfg.mock:
        try:
            cfg.resolve_aes(require=True)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]{exc}[/red]")
            return 2

    console.print("[yellow]Connecting… (wait for connected=True)[/yellow]")
    # Console/manual teleop does not need camera/detector downloads.
    want_video = (cfg.start_mode or "").lower() == "follow"
    controller = _build_controller(cfg, enable_video=want_video)
    ip = cfg.robot_ip
    result = await controller.connect(ip)
    if not result.ok:
        console.print(f"[red]connect failed: {result.code.value} {result.message}[/red]")
        return 2
    console.print(f"[green]connected[/green] mode={controller.mode.value}")

    mode = (cfg.start_mode or "follow").lower()
    if mode == "manual":
        man = await controller.start_manual()
        if not man.ok:
            console.print(
                f"[red]manual start failed: {man.code.value} {man.message}[/red]"
            )
    elif mode == "follow":
        console.print(SAFETY_WARNING)
        await controller.start_follow_front_person()
    elif mode == "idle":
        pass
    else:
        console.print(f"[yellow]Unknown mode {mode}; staying IDLE[/yellow]")

    if open_console:
        from singularity_go2_console.terminal_console import run_console

        return await run_console(
            controller,
            cfg,
            force_line_mode=True if line_mode else None,
        )

    await controller.shutdown()
    return 0


@app.command()
def start(
    robot_ip: Optional[str] = typer.Option(None, "--robot-ip"),
    connection_mode: str = typer.Option("ap", "--connection-mode", help="ap|sta"),
    aes_key_file: Optional[str] = typer.Option(None, "--aes-key-file"),
    mode: str = typer.Option("follow", "--mode", help="idle|manual|follow"),
    mock: bool = typer.Option(False, "--mock", help="Explicit mock mode"),
    detect_only: bool = typer.Option(False, "--detect-only"),
    tracker_only: bool = typer.Option(False, "--tracker-only"),
    no_console: bool = typer.Option(False, "--no-console"),
    allow_normal_mode_switch: bool = typer.Option(
        False,
        "--allow-normal-mode-switch",
        help="Allow explicit switch to motion mode normal (disabled by default)",
    ),
    line_mode: bool = typer.Option(
        False,
        "--line-mode",
        help="Force line-mode console (type w+Enter). Use if keyboard does not work",
    ),
) -> None:
    """Connect and enter the requested mode (default: automatic front-person follow)."""
    cfg = _load_config(
        robot_ip=robot_ip,
        mode=mode,
        mock=mock,
        detect_only=detect_only,
        tracker_only=tracker_only,
        connection_mode=connection_mode,
        aes_key_file=aes_key_file,
        allow_normal_mode_switch=allow_normal_mode_switch,
    )
    raise typer.Exit(
        asyncio.run(
            _start_session(cfg, open_console=not no_console, line_mode=line_mode)
        )
    )


@app.command(name="console")
def console_cmd(
    robot_ip: Optional[str] = typer.Option(None, "--robot-ip"),
    connection_mode: str = typer.Option("ap", "--connection-mode", help="ap|sta"),
    aes_key_file: Optional[str] = typer.Option(None, "--aes-key-file"),
    mode: str = typer.Option("manual", "--mode"),
    mock: bool = typer.Option(False, "--mock"),
    allow_normal_mode_switch: bool = typer.Option(
        False,
        "--allow-normal-mode-switch",
        help="Allow explicit switch to motion mode normal (disabled by default)",
    ),
    line_mode: bool = typer.Option(
        False,
        "--line-mode",
        help="Force line-mode console (type w+Enter). Use if keyboard does not work",
    ),
) -> None:
    """Interactive terminal console."""
    cfg = _load_config(
        robot_ip=robot_ip,
        mode=mode,
        mock=mock,
        connection_mode=connection_mode,
        aes_key_file=aes_key_file,
        allow_normal_mode_switch=allow_normal_mode_switch,
    )
    raise typer.Exit(
        asyncio.run(_start_session(cfg, open_console=True, line_mode=line_mode))
    )


@app.command()
def manual(
    robot_ip: Optional[str] = typer.Option(None, "--robot-ip"),
    connection_mode: str = typer.Option("ap", "--connection-mode", help="ap|sta"),
    aes_key_file: Optional[str] = typer.Option(None, "--aes-key-file"),
    mock: bool = typer.Option(False, "--mock"),
    allow_normal_mode_switch: bool = typer.Option(
        False,
        "--allow-normal-mode-switch",
        help="Allow explicit switch to motion mode normal (disabled by default)",
    ),
    line_mode: bool = typer.Option(
        False,
        "--line-mode",
        help="Force line-mode console (type w+Enter). Use if keyboard does not work",
    ),
) -> None:
    """Start in manual teleop mode."""
    cfg = _load_config(
        robot_ip=robot_ip,
        mode="manual",
        mock=mock,
        connection_mode=connection_mode,
        aes_key_file=aes_key_file,
        allow_normal_mode_switch=allow_normal_mode_switch,
    )
    raise typer.Exit(
        asyncio.run(_start_session(cfg, open_console=True, line_mode=line_mode))
    )

@app.command()
def follow(
    robot_ip: Optional[str] = typer.Option(None, "--robot-ip"),
    connection_mode: str = typer.Option("ap", "--connection-mode", help="ap|sta"),
    aes_key_file: Optional[str] = typer.Option(None, "--aes-key-file"),
    mock: bool = typer.Option(False, "--mock"),
    allow_normal_mode_switch: bool = typer.Option(
        False,
        "--allow-normal-mode-switch",
        help="Allow explicit switch to motion mode normal (disabled by default)",
    ),
) -> None:
    """Start automatic front-person follow."""
    cfg = _load_config(
        robot_ip=robot_ip,
        mode="follow",
        mock=mock,
        connection_mode=connection_mode,
        aes_key_file=aes_key_file,
        allow_normal_mode_switch=allow_normal_mode_switch,
    )
    raise typer.Exit(asyncio.run(_start_session(cfg, open_console=True)))


@app.command()
def wasd(
    aes_key_file: Optional[str] = typer.Option(
        str(Path.home() / ".config/go2ctl/aes_key"),
        "--aes-key-file",
    ),
) -> None:
    """Minimal WASD walker (AP). No follow/detector — just walk."""
    console.print(SAFETY_WARNING)
    console.print(
        "Launching minimal walker…\n"
        "After BalanceStand, type w/a/s/d + Enter."
    )
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "go2_wasd.py"
    )
    # Re-exec so the script owns the asyncio loop cleanly.
    os.environ["GO2CTL_AES_KEY_FILE"] = aes_key_file or ""
    raise typer.Exit(
        subprocess.call(  # noqa: S603
            [sys.executable, str(script)],
        )
    )


@app.command()
def hold() -> None:
    """Best-effort hold via local control socket is not used; use console X or API later."""
    console.print(
        "hold requires an active go2ctl session. Press X in the console, "
        "or call Go2Controller.hold() from the future API adapter."
    )


@app.command()
def reacquire() -> None:
    console.print(
        "reacquire requires an active go2ctl session. Press R in the console, "
        "or call Go2Controller.reacquire_front_person()."
    )


@app.command()
def status() -> None:
    console.print(
        "status requires an active session. Use the console I key, "
        "or Go2Controller.get_status() from the future API."
    )


@app.command()
def estop() -> None:
    """Emergency stop hint for operators."""
    console.print(SAFETY_WARNING)
    console.print(
        "If a go2ctl console is running, press SPACE.\n"
        "Also use the official Unitree remote / physical recovery method.\n"
        "A standalone estop client against a running session will be added with the API adapter."
    )


@app.command()
def stop() -> None:
    """Alias for safe shutdown guidance."""
    console.print("Press ESC in the console for safe shutdown, or send SIGTERM to the go2ctl process.")


@app.command()
def logs(
    follow: bool = typer.Option(False, "--follow", "-f"),
    lines: int = typer.Option(100, "--lines", "-n"),
) -> None:
    """Show go2ctl structured logs."""
    if follow:
        follow_logs()
        return
    for line in read_logs(lines=lines):
        console.print(line)


if __name__ == "__main__":
    app()
