"""Logging setup must never crash CLI diagnostics."""

from __future__ import annotations

from pathlib import Path

from singularity_go2_console.logging_config import active_log_file, setup_logging


def test_setup_logging_uses_writable_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GO2CTL_LOG_DIR", raising=False)
    path = setup_logging("INFO", log_dir=tmp_path)
    assert path == tmp_path / "go2ctl.log"
    assert path is not None and path.is_file()
    assert active_log_file() == path


def test_setup_logging_falls_back_when_primary_unwritable(
    tmp_path: Path, monkeypatch
) -> None:
    # Use a regular file as the "log dir" so mkdir/FileHandler fail for any uid.
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("x", encoding="utf-8")
    fallback = tmp_path / "fallback"
    monkeypatch.setenv("GO2CTL_LOG_DIR", str(fallback))
    path = setup_logging("INFO", log_dir=blocked)
    assert path == fallback / "go2ctl.log"
    assert path is not None and path.is_file()


def test_setup_logging_console_only_when_all_unwritable(
    tmp_path: Path, monkeypatch
) -> None:
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GO2CTL_LOG_DIR", str(blocked))
    # Force only unwritable candidates (skip home/tmp by pointing env + arg to file).
    from singularity_go2_console import logging_config as lc

    monkeypatch.setattr(lc, "_candidate_log_dirs", lambda log_dir=None: [blocked, blocked])
    path = setup_logging("INFO", log_dir=blocked)
    assert path is None
    assert active_log_file() is None
