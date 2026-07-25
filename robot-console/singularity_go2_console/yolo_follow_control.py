"""Stabilized person-follow velocity law (face aim + body range).

WHY: Raw YOLO body boxes + high gains feel violent. We:
- Aim yaw at face/head (steady)
- Estimate range from body width (less noisy than face size)
- Multi-stage EMA + slew-rate limits so motion feels soft
- Wide deadband so a stopped person → robot fully stops
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class YoloFollowParams:
    """Soft walk-follow tunables (deliberately gentle)."""

    target_distance_m: float = 1.55
    min_distance_m: float = 1.05
    distance_deadband_m: float = 0.22
    range_rate_stop_mps: float = 0.12
    assumed_person_width_m: float = 0.45
    fx_scale: float = 0.90
    max_vx: float = 0.38
    max_wz: float = 0.35
    kp_x: float = 0.32
    k_ff: float = 0.22
    kp_yaw: float = 0.85
    yaw_deadband: float = 0.055
    turn_slow_start: float = 0.16
    backup_vx: float = 0.12
    # Lower alpha = heavier smoothing (less twitch).
    ema_alpha_aim: float = 0.18
    ema_alpha_dist: float = 0.12
    ema_alpha_rate: float = 0.15
    ema_alpha_cmd: float = 0.16
    # Max command change per second (butter-smooth).
    max_dvx_mps2: float = 0.55
    max_dwz_rps2: float = 0.70
    # Soft taper outside deadband (meters / normalized yaw).
    soft_band_m: float = 0.35
    soft_yaw: float = 0.12


@dataclass(slots=True)
class YoloFollowState:
    ema_distance_m: float | None = None
    ema_aim_x: float | None = None
    ema_range_rate: float = 0.0
    ema_vx: float = 0.0
    ema_wz: float = 0.0
    last_distance_m: float | None = None
    last_t: float | None = None
    last_vx: float = 0.0
    last_wz: float = 0.0


def estimate_distance_m(
    bbox: tuple[float, float, float, float],
    image_width: int,
    *,
    assumed_width_m: float = 0.45,
    fx_scale: float = 0.90,
) -> float | None:
    """Pinhole distance from body bbox width (shoulders)."""
    x1, _, x2, _ = bbox
    pixel_w = max(1.0, float(x2) - float(x1))
    fx = max(1.0, float(fx_scale) * float(max(1, image_width)))
    return (assumed_width_m * fx) / pixel_w


def _soft_scale(error: float, deadband: float, soft: float) -> float:
    """0 inside deadband, ease in through soft band, 1 beyond."""
    a = abs(error)
    if a <= deadband:
        return 0.0
    if soft <= 1e-6 or a >= deadband + soft:
        return 1.0
    t = (a - deadband) / soft
    # Smoothstep for gentle onset.
    return t * t * (3.0 - 2.0 * t)


def _slew(prev: float, target: float, max_delta: float) -> float:
    d = target - prev
    if d > max_delta:
        return prev + max_delta
    if d < -max_delta:
        return prev - max_delta
    return target


def compute_yolo_follow_cmd(
    body_bbox: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
    now_s: float,
    state: YoloFollowState,
    params: YoloFollowParams | None = None,
    *,
    aim_bbox: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float]:
    """Return smoothed (vx, vy, wz). Updates ``state`` in place.

    ``aim_bbox`` should be face/head for yaw; ``body_bbox`` for range.
    """
    p = params or YoloFollowParams()
    _ = image_height

    dist = estimate_distance_m(
        body_bbox,
        image_width,
        assumed_width_m=p.assumed_person_width_m,
        fx_scale=p.fx_scale,
    )
    if dist is None or dist <= 0.05:
        state.ema_vx = 0.0
        state.ema_wz = 0.0
        state.last_vx = 0.0
        state.last_wz = 0.0
        return 0.0, 0.0, 0.0

    aim = aim_bbox if aim_bbox is not None else body_bbox
    ax1, _, ax2, _ = aim
    raw_aim_x = 0.5 * (float(ax1) + float(ax2)) / max(1.0, float(image_width))

    a_aim = max(0.02, min(1.0, p.ema_alpha_aim))
    a_dist = max(0.02, min(1.0, p.ema_alpha_dist))
    a_rate = max(0.02, min(1.0, p.ema_alpha_rate))
    a_cmd = max(0.02, min(1.0, p.ema_alpha_cmd))

    if state.ema_aim_x is None:
        state.ema_aim_x = raw_aim_x
    else:
        state.ema_aim_x = (1.0 - a_aim) * state.ema_aim_x + a_aim * raw_aim_x

    if state.ema_distance_m is None:
        state.ema_distance_m = dist
    else:
        state.ema_distance_m = (1.0 - a_dist) * state.ema_distance_m + a_dist * dist
    distance = state.ema_distance_m

    dt = 0.05
    raw_rate = 0.0
    if state.last_distance_m is not None and state.last_t is not None:
        dt = max(1e-3, now_s - state.last_t)
        raw_rate = (distance - state.last_distance_m) / dt
    state.ema_range_rate = (1.0 - a_rate) * state.ema_range_rate + a_rate * raw_rate
    range_rate = state.ema_range_rate
    state.last_distance_m = distance
    state.last_t = now_s

    err_yaw = state.ema_aim_x - 0.5
    yaw_scale = _soft_scale(err_yaw, p.yaw_deadband, p.soft_yaw)
    if yaw_scale <= 1e-6:
        wz_raw = 0.0
    else:
        wz_raw = max(-p.max_wz, min(p.max_wz, -p.kp_yaw * err_yaw * yaw_scale))

    dist_err = distance - p.target_distance_m
    hold = (
        abs(dist_err) <= p.distance_deadband_m
        and abs(range_rate) <= p.range_rate_stop_mps
    )
    if hold:
        vx_raw = 0.0
    elif distance < p.min_distance_m:
        vx_raw = -min(p.backup_vx, p.max_vx)
    else:
        x_scale = _soft_scale(dist_err, p.distance_deadband_m, p.soft_band_m)
        vx_raw = (p.kp_x * dist_err + p.k_ff * range_rate) * max(x_scale, 0.15 if abs(dist_err) > p.distance_deadband_m else 0.0)
        if abs(err_yaw) > p.turn_slow_start:
            slow = max(0.30, 1.0 - (abs(err_yaw) - p.turn_slow_start) * 2.0)
            vx_raw *= slow
        vx_raw = max(-p.max_vx, min(p.max_vx, vx_raw))

    # EMA on command then slew-limit for butter motion.
    state.ema_vx = (1.0 - a_cmd) * state.ema_vx + a_cmd * vx_raw
    state.ema_wz = (1.0 - a_cmd) * state.ema_wz + a_cmd * wz_raw
    if hold:
        # Drain quickly to true stop when person is still at standoff.
        state.ema_vx *= 0.55
        if abs(state.ema_vx) < 0.025:
            state.ema_vx = 0.0
        if abs(state.ema_wz) < 0.025 and yaw_scale <= 1e-6:
            state.ema_wz = 0.0

    max_dvx = p.max_dvx_mps2 * dt
    max_dwz = p.max_dwz_rps2 * dt
    vx = _slew(state.last_vx, float(state.ema_vx), max_dvx)
    wz = _slew(state.last_wz, float(state.ema_wz), max_dwz)
    state.last_vx = vx
    state.last_wz = wz

    if hold and abs(vx) < 0.02:
        vx = 0.0
        state.last_vx = 0.0
        state.ema_vx = 0.0
    if yaw_scale <= 1e-6 and abs(wz) < 0.02:
        wz = 0.0
        state.last_wz = 0.0
        state.ema_wz = 0.0

    return float(vx), 0.0, float(wz)


# Back-compat alias used by older tests/call sites.
def compute_follow_cmd(*args, **kwargs):  # type: ignore[no-untyped-def]
    return compute_yolo_follow_cmd(*args, **kwargs)
