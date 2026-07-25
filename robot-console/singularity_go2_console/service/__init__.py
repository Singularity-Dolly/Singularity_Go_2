"""Thin authenticated FastAPI robot-service around Go2Controller."""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # lazy import avoids FastAPI at import time for CLI
    from singularity_go2_console.service.app import create_app as _create_app

    return _create_app(*args, **kwargs)
