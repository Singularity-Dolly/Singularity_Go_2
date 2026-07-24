"""Frame provider — thread-safe JPEG frame buffer with MJPEG streaming.

Responsibilities:
- Store latest JPEG frame from camera capture thread
- Generate MJPEG multipart HTTP response for GET /v1/video
- Stale frame detection via FrameGuard (returns None if >500ms old)
- Thread-safe: camera thread writes, HTTP handler reads

Usage:
    provider = FrameProvider()
    provider.update(jpeg_bytes)         # called by camera capture thread
    frame = provider.get_latest()       # called by HTTP handler
    # For MJPEG streaming:
    async for chunk in provider.stream_mjpeg():
        yield chunk
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from .safety import FrameGuard


# MJPEG boundary string
_MJPEG_BOUNDARY: bytes = b"--frame\r\n"
_MJPEG_CONTENT_TYPE: bytes = b"Content-Type: image/jpeg\r\n\r\n"


class FrameProvider:
    """Thread-safe JPEG frame buffer with MJPEG streaming support.

    Single producer (camera thread), multiple consumers (HTTP handlers).
    """

    _FRAME_TIMEOUT_MS: float = 500.0

    def __init__(self) -> None:
        self._guard = FrameGuard()

    # ---- Properties ----

    @property
    def has_fresh_frame(self) -> bool:
        """True if the latest frame is within the freshness window."""
        return self._guard.has_fresh_frame

    @property
    def frame_age_ms(self) -> float | None:
        """Age of the latest frame in milliseconds, or None if no frame."""
        return self._guard.frame_age_ms

    # ---- Producer API (camera thread) ----

    def update(self, jpeg_bytes: bytes) -> None:
        """Store a new JPEG frame. Called by the camera capture thread.

        Args:
            jpeg_bytes: Raw JPEG image bytes.
        """
        if not jpeg_bytes:
            return
        self._guard.update(jpeg_bytes)

    # ---- Consumer API (HTTP handler) ----

    def get_latest(self) -> bytes | None:
        """Return the latest frame, or None if stale/absent.

        Returns None when:
        - No frame has been captured yet
        - The latest frame is older than FRAME_TIMEOUT_MS (500ms)
        """
        return self._guard.get_latest()

    # ---- MJPEG Streaming ----

    async def stream_mjpeg(
        self,
        *,
        fps: float = 10.0,
        quality: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Async generator yielding MJPEG multipart chunks.

        Each chunk is a complete multipart segment:
            --frame\\r\\n
            Content-Type: image/jpeg\\r\\n
            \\r\\n
            <jpeg bytes>\\r\\n

        Args:
            fps: Target frame rate for streaming (default 10).
            quality: Unused; reserved for future JPEG quality adjustment.

        Yields:
            bytes: MJPEG multipart segment.
        """
        interval = 1.0 / max(fps, 1.0)

        while True:
            frame = self.get_latest()
            if frame is not None:
                yield _mjpeg_chunk(frame)
            await asyncio.sleep(interval)


def _mjpeg_chunk(jpeg_bytes: bytes) -> bytes:
    """Build a single MJPEG multipart chunk."""
    return _MJPEG_BOUNDARY + _MJPEG_CONTENT_TYPE + jpeg_bytes + b"\r\n"