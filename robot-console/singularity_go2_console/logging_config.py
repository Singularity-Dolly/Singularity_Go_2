"""Structured logging for go2ctl."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG_DIR = Path.home() / ".local" / "state" / "go2ctl"
LOG_FILE = LOG_DIR / "go2ctl.log"
_ACTIVE_LOG_FILE: Path | None = None


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "event",
            "mode",
            "robot_ip",
            "result",
            "error_code",
            "owner",
            "vx",
            "vy",
            "wz",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def _candidate_log_dirs(log_dir: Path | None = None) -> list[Path]:
    dirs: list[Path] = []
    if log_dir is not None:
        dirs.append(Path(log_dir))
    env_dir = os.environ.get("GO2CTL_LOG_DIR")
    if env_dir:
        dirs.append(Path(env_dir))
    dirs.append(LOG_DIR)
    dirs.append(Path(tempfile.gettempdir()) / "go2ctl")
    # Preserve order while dropping duplicates.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in dirs:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def active_log_file() -> Path | None:
    """Return the file currently used by setup_logging, if any."""
    return _ACTIVE_LOG_FILE


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> Path | None:
    """Configure go2ctl logging.

    Tries writable file destinations in order. If none are writable, continues
    with console-only logging so diagnostic commands like ``doctor`` still run.
    """
    global LOG_FILE, _ACTIVE_LOG_FILE

    root = logging.getLogger("go2ctl")
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(console)

    log_path: Path | None = None
    errors: list[str] = []
    for directory in _candidate_log_dirs(log_dir):
        candidate = directory / "go2ctl.log"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(candidate, encoding="utf-8")
            file_handler.setFormatter(JsonLineFormatter())
            root.addHandler(file_handler)
            log_path = candidate
            break
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")

    _ACTIVE_LOG_FILE = log_path
    if log_path is not None:
        LOG_FILE = log_path
    else:
        root.warning(
            "file logging unavailable; console-only (%s)",
            "; ".join(errors) if errors else "no candidates",
        )

    return log_path


def log_event(
    event: str,
    *,
    mode: str | None = None,
    robot_ip: str | None = None,
    result: str | None = None,
    error_code: str | None = None,
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    logger = logging.getLogger("go2ctl")
    # Never log secrets
    for banned in ("password", "token", "api_key", "authorization"):
        extra.pop(banned, None)
        if robot_ip and banned in str(robot_ip).lower():
            robot_ip = "<redacted>"

    logger.log(
        level,
        event,
        extra={
            "event": event,
            "mode": mode,
            "robot_ip": robot_ip,
            "result": result,
            "error_code": error_code,
            **extra,
        },
    )


def read_logs(lines: int = 100, log_path: Path | None = None) -> list[str]:
    path = log_path or _ACTIVE_LOG_FILE or LOG_FILE
    try:
        if not path.is_file():
            return []
        content = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if lines <= 0:
        return content
    return content[-lines:]


def follow_logs(log_path: Path | None = None) -> None:
    """Blocking tail -f style log reader for CLI."""
    import time

    path = log_path or _ACTIVE_LOG_FILE or LOG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    except OSError as exc:
        print(f"log file unavailable: {path} ({exc})")
        return
    with path.open("r", encoding="utf-8") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if line:
                print(line, end="")
            else:
                time.sleep(0.2)
