#!/usr/bin/env python
"""Frame rate benchmark for robot-service video endpoint.

Usage:
    python benchmark_frames.py [--url URL] [--duration 10] [--fps-target 10]
"""

from __future__ import annotations

import argparse
import time
from urllib.request import urlopen


def benchmark(url: str, duration: float, fps_target: float) -> None:
    """Benchmark MJPEG stream frame rate."""
    print(f"Benchmarking {url} for {duration}s (target: {fps_target} fps)...")
    print(f"  {'Time':<8} {'Frames':<8} {'FPS':<8} {'Status':<8}")
    print(f"  {'-' * 32}")

    start = time.monotonic()
    frame_count = 0
    last_report = start
    boundary = b"--frame"

    try:
        with urlopen(url, timeout=5) as resp:
            buffer = b""
            while time.monotonic() - start < duration:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                # Count frame boundaries
                frame_count += buffer.count(boundary)
                buffer = buffer[-len(boundary):]  # Keep tail

                # Report every second
                now = time.monotonic()
                if now - last_report >= 1.0:
                    elapsed = now - start
                    fps = frame_count / elapsed if elapsed > 0 else 0
                    status = "OK" if fps >= fps_target else "LOW"
                    print(f"  {elapsed:<8.1f} {frame_count:<8} {fps:<8.1f} {status:<8}")
                    last_report = now

    except Exception as e:
        print(f"Error: {e}")
        return

    elapsed = time.monotonic() - start
    fps = frame_count / elapsed if elapsed > 0 else 0
    print(f"\n  Result: {frame_count} frames in {elapsed:.1f}s = {fps:.1f} fps")
    print(f"  Target: {fps_target} fps")
    print(f"  {'PASS' if fps >= fps_target else 'FAIL'}: "
          f"{'Meeting target' if fps >= fps_target else 'Below target'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark robot-service video")
    parser.add_argument("--url", default="http://localhost:8780/v1/video?fps=10",
                        help="MJPEG stream URL")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Benchmark duration in seconds")
    parser.add_argument("--fps-target", type=float, default=10.0,
                        help="Target frame rate")
    args = parser.parse_args()
    benchmark(args.url, args.duration, args.fps_target)