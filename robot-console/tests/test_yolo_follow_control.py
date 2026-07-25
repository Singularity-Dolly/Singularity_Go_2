"""Tests for stabilized YOLO/face follow velocity law."""

from __future__ import annotations

from singularity_go2_console.face_tracker import bbox_center, head_proxy_bbox
from singularity_go2_console.yolo_follow_control import (
    YoloFollowParams,
    YoloFollowState,
    compute_yolo_follow_cmd,
    estimate_distance_m,
)


def _bbox_for_distance(
    distance_m: float,
    *,
    image_width: int = 640,
    image_height: int = 480,
    assumed_width_m: float = 0.45,
    fx_scale: float = 0.90,
    cx: float | None = None,
) -> tuple[float, float, float, float]:
    fx = fx_scale * image_width
    pixel_w = (assumed_width_m * fx) / distance_m
    center_x = float(image_width) * 0.5 if cx is None else cx
    x1 = center_x - pixel_w / 2.0
    x2 = center_x + pixel_w / 2.0
    y1 = image_height * 0.15
    y2 = image_height * 0.95
    return (x1, y1, x2, y2)


def _sharp_params(**kwargs: float) -> YoloFollowParams:
    """Disable smoothing so unit tests see the instantaneous law."""
    base = dict(
        ema_alpha_aim=1.0,
        ema_alpha_dist=1.0,
        ema_alpha_rate=1.0,
        ema_alpha_cmd=1.0,
        max_dvx_mps2=100.0,
        max_dwz_rps2=100.0,
        soft_band_m=0.01,
        soft_yaw=0.01,
    )
    base.update(kwargs)
    return YoloFollowParams(**base)  # type: ignore[arg-type]


def test_estimate_distance_roundtrip() -> None:
    bbox = _bbox_for_distance(1.5)
    d = estimate_distance_m(bbox, 640)
    assert d is not None
    assert abs(d - 1.5) < 0.05


def test_stop_when_person_holds_at_standoff() -> None:
    params = _sharp_params()
    state = YoloFollowState()
    bbox = _bbox_for_distance(1.55)
    compute_yolo_follow_cmd(bbox, 640, 480, 0.0, state, params)
    vx, vy, wz = compute_yolo_follow_cmd(bbox, 640, 480, 0.1, state, params)
    assert vy == 0.0
    assert abs(vx) < 1e-6
    assert abs(wz) < 1e-6


def test_forward_when_person_far() -> None:
    params = _sharp_params()
    state = YoloFollowState()
    bbox = _bbox_for_distance(2.8)
    compute_yolo_follow_cmd(bbox, 640, 480, 0.0, state, params)
    vx, _, _ = compute_yolo_follow_cmd(bbox, 640, 480, 0.1, state, params)
    assert vx > 0.05


def test_backup_when_too_close() -> None:
    params = _sharp_params()
    state = YoloFollowState()
    bbox = _bbox_for_distance(0.7)
    compute_yolo_follow_cmd(bbox, 640, 480, 0.0, state, params)
    vx, _, _ = compute_yolo_follow_cmd(bbox, 640, 480, 0.1, state, params)
    assert vx < 0.0


def test_feedforward_matches_person_walking_away() -> None:
    params = _sharp_params(distance_deadband_m=0.05)
    state = YoloFollowState()
    near = _bbox_for_distance(1.6)
    far = _bbox_for_distance(2.0)
    compute_yolo_follow_cmd(near, 640, 480, 0.0, state, params)
    vx_static, _, _ = compute_yolo_follow_cmd(near, 640, 480, 0.2, state, params)
    state2 = YoloFollowState()
    compute_yolo_follow_cmd(near, 640, 480, 0.0, state2, params)
    vx_moving, _, _ = compute_yolo_follow_cmd(far, 640, 480, 0.2, state2, params)
    assert vx_moving > vx_static


def test_yaw_uses_aim_bbox_not_body() -> None:
    params = _sharp_params()
    state = YoloFollowState()
    body = _bbox_for_distance(1.55)  # centered body
    aim = _bbox_for_distance(1.55, cx=500)  # face to the right
    compute_yolo_follow_cmd(body, 640, 480, 0.0, state, params, aim_bbox=aim)
    _, _, wz = compute_yolo_follow_cmd(
        body, 640, 480, 0.1, state, params, aim_bbox=aim
    )
    assert wz < 0.0


def test_slew_limits_prevent_step_jumps() -> None:
    params = YoloFollowParams(
        ema_alpha_aim=1.0,
        ema_alpha_dist=1.0,
        ema_alpha_rate=1.0,
        ema_alpha_cmd=1.0,
        max_dvx_mps2=0.4,
        max_dwz_rps2=0.4,
        soft_band_m=0.01,
        soft_yaw=0.01,
        distance_deadband_m=0.05,
    )
    state = YoloFollowState()
    near = _bbox_for_distance(1.55)
    far = _bbox_for_distance(3.0)
    compute_yolo_follow_cmd(near, 640, 480, 0.0, state, params)
    vx1, _, _ = compute_yolo_follow_cmd(far, 640, 480, 0.05, state, params)
    # 0.05s * 0.4 m/s^2 = 0.02 max step from 0
    assert abs(vx1) <= 0.025 + 1e-6


def test_head_proxy_is_upper_person() -> None:
    person = (100.0, 100.0, 200.0, 400.0)
    head = head_proxy_bbox(person)
    cx, cy = bbox_center(head)
    assert head[1] == 100.0
    assert head[3] < 100.0 + 0.35 * 300.0
    assert 100.0 < cx < 200.0
    assert cy < 200.0
