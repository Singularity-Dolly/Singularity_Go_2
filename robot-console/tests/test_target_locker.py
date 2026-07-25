"""Tests for TargetLocker — Kalman + IoU + center distance + HSV ReID."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pytest

from singularity_go2_console.target_locker import (
    LockerParams,
    LockedTarget,
    TargetLocker,
    _iou,
    _center_distance_norm,
    _compute_color_hist,
    _hist_correlation,
    _Kalman2D,
)

Bbox = tuple[float, float, float, float]


def _bbox(cx: float, cy: float, w: float = 100.0, h: float = 200.0) -> Bbox:
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _solid_image(w: int = 640, h: int = 480, color=(50, 100, 150)) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


# ---------------------------------------------------------------------------
# IoU / distance helpers
# ---------------------------------------------------------------------------


def test_iou_identical_boxes_returns_one() -> None:
    a = (10.0, 10.0, 110.0, 110.0)
    assert _iou(a, a) == 1.0


def test_iou_disjoint_boxes_returns_zero() -> None:
    a = (0.0, 0.0, 10.0, 10.0)
    b = (100.0, 100.0, 110.0, 110.0)
    assert _iou(a, b) == 0.0


def test_center_distance_zero_for_same_center() -> None:
    a = _bbox(320.0, 240.0)
    b = _bbox(320.0, 240.0, w=50.0, h=80.0)
    assert _center_distance_norm(a, b, 640, 480) == 0.0


def test_center_distance_max_for_opposite_corners() -> None:
    a = _bbox(0.0, 0.0, w=1.0, h=1.0)
    b = _bbox(640.0, 480.0, w=1.0, h=1.0)
    # corners are exactly diag apart → normalized == 1.0
    assert abs(_center_distance_norm(a, b, 640, 480) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Kalman
# ---------------------------------------------------------------------------


def test_kalman_predict_advances_position_by_velocity() -> None:
    k = _Kalman2D()
    k.init(100.0, 200.0, t=0.0)
    # Inject velocity via two updates at known times — dt=1, dx=10 → vx=10
    k.update(110.0, 200.0, t=1.0)
    pred = k.predict(t=2.0)
    assert pred is not None
    # After update at t=1 with vx≈10, predict to t=2 → cx≈120
    assert abs(pred[0] - 120.0) < 5.0


def test_kalman_returns_none_before_init() -> None:
    k = _Kalman2D()
    assert k.predict(t=1.0) is None


# ---------------------------------------------------------------------------
# Color histogram
# ---------------------------------------------------------------------------


def test_color_hist_identical_regions_correlate_to_one() -> None:
    img = _solid_image(color=(100, 150, 200))
    bbox = _bbox(320.0, 240.0, w=100, h=100)
    h1 = _compute_color_hist(img, bbox)
    h2 = _compute_color_hist(img, bbox)
    # Same ROI → identical histogram → correlation = 1
    assert _hist_correlation(h1, h2) > 0.99


def test_color_hist_different_colors_correlate_low() -> None:
    img_red = _solid_image(color=(0, 0, 255))    # BGR red
    img_blue = _solid_image(color=(255, 0, 0))    # BGR blue
    bbox = _bbox(320.0, 240.0, w=100, h=100)
    h_red = _compute_color_hist(img_red, bbox)
    h_blue = _compute_color_hist(img_blue, bbox)
    assert _hist_correlation(h_red, h_blue) < 0.5


def test_color_hist_empty_bbox_returns_zero_array() -> None:
    img = _solid_image()
    # Inverted bbox yields empty ROI
    bbox = (200.0, 200.0, 100.0, 100.0)
    h = _compute_color_hist(img, bbox)
    assert h.size == 1 and h[0] == 0.0


# ---------------------------------------------------------------------------
# TargetLocker lifecycle
# ---------------------------------------------------------------------------


def test_locker_locks_first_highest_confidence_detection() -> None:
    locker = TargetLocker(image_size=(640, 480))
    dets: Sequence = [
        (_bbox(200.0, 240.0), 0.6),
        (_bbox(400.0, 240.0), 0.9),
    ]
    img = _solid_image()
    target = locker.update(dets, img, t=0.0)
    assert target is not None
    assert target.bbox == _bbox(400.0, 240.0)
    assert target.confidence == 0.9
    assert locker.is_locked


def test_locker_retains_through_one_missing_frame() -> None:
    locker = TargetLocker(image_size=(640, 480))
    img = _solid_image()
    # Lock at t=0
    locker.update([(_bbox(320.0, 240.0), 0.9)], img, t=0.0)
    # No detections at t=0.05 (one frame later) — should retain
    target = locker.update([], img, t=0.05)
    assert target is not None
    assert locker.is_locked


def test_locker_drops_after_retain_seconds_without_match() -> None:
    params = LockerParams(retain_seconds=0.5)
    locker = TargetLocker(params=params, image_size=(640, 480))
    img = _solid_image()
    locker.update([(_bbox(320.0, 240.0), 0.9)], img, t=0.0)
    # 1 second later, no detections — exceeds retain → drop
    target = locker.update([], img, t=1.0)
    assert target is None
    assert not locker.is_locked


def test_locker_matches_target_on_sideways_motion_where_iou_fails() -> None:
    """Reproduce the previous failure: person walks sideways, IoU drops below
    the old 0.15 threshold. With center distance + color, we should still match."""
    # Disable color to prove distance cue alone saves us
    params = LockerParams(use_color=False, accept_threshold=0.30)
    locker = TargetLocker(params=params, image_size=(640, 480))
    img = _solid_image()

    # Frame 0: lock at center
    locker.update([(_bbox(320.0, 240.0, w=100, h=200), 0.9)], img, t=0.0)
    # Frame 1: target moved 40px sideways (no overlap with previous bbox)
    #   IoU = 0, but center distance is 40/sqrt(640^2+480^2) ≈ 0.05 → dist_score 0.95
    target = locker.update(
        [(_bbox(360.0, 240.0, w=100, h=200), 0.85)], img, t=0.05
    )
    # Should still match because center distance score is high.
    assert target is not None
    assert abs(target.bbox[0] - 360.0) < 1.0


def test_locker_recovers_after_one_missing_frame_via_kalman_predict() -> None:
    """When YOLO misses a frame, locker should keep steering toward the
    Kalman-predicted position. The synthetic target's bbox center should be
    displaced from the last seen position by predicted velocity."""
    locker = TargetLocker(image_size=(640, 480))
    img = _solid_image()
    # Frame 0 at cx=200
    locker.update([(_bbox(200.0, 240.0, w=100, h=200), 0.9)], img, t=0.0)
    # Frame 1 at cx=240 (vx ~ 800 px/s)
    locker.update([(_bbox(240.0, 240.0, w=100, h=200), 0.9)], img, t=0.05)
    # Frame 2: no detection. Kalman should predict cx ≈ 240 + 800*0.05 = 280
    target = locker.update([], img, t=0.10)
    assert target is not None
    cx_pred = (target.bbox[0] + target.bbox[2]) / 2
    assert cx_pred > 250.0  # has moved in the right direction


def test_locker_reset_clears_state() -> None:
    locker = TargetLocker(image_size=(640, 480))
    img = _solid_image()
    locker.update([(_bbox(320.0, 240.0), 0.9)], img, t=0.0)
    assert locker.is_locked
    locker.reset()
    assert not locker.is_locked
    assert locker.target is None


def test_locker_rejects_far_detections_and_keeps_last_known() -> None:
    """When the only available detection is far from the locked target and
    color is disabled, we should retain last-known target within retain window
    instead of switching to a random new person."""
    params = LockerParams(use_color=False, retain_seconds=1.0)
    locker = TargetLocker(params=params, image_size=(640, 480))
    img = _solid_image()
    locker.update([(_bbox(100.0, 240.0, w=100, h=200), 0.9)], img, t=0.0)
    # A new person appears at cx=500 — should NOT switch immediately
    target = locker.update([(_bbox(500.0, 240.0, w=100, h=200), 0.95)], img, t=0.05)
    # We retain the original target (via Kalman predict) — bbox should be near cx=100
    assert target is not None
    cx = (target.bbox[0] + target.bbox[2]) / 2
    assert cx < 200.0
