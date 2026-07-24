"""Front-person selection tests."""

from __future__ import annotations

from singularity_go2_console.front_person import (
    CameraFrame,
    PersonDetection,
    TargetStabilityTracker,
    select_front_person,
)


def _frame(fid: int = 1, w: int = 100, h: int = 100) -> CameraFrame:
    return CameraFrame(image=[[0] * w for _ in range(h)], timestamp_s=0.0, frame_id=fid, width=w, height=h)


def test_no_person_found_does_not_select() -> None:
    sel = select_front_person([], _frame())
    assert not sel.ok
    assert sel.code == "NO_PERSON_FOUND"


def test_centered_person_preferred() -> None:
    frame = _frame()
    dets = [
        PersonDetection(bbox=(5, 20, 25, 80), confidence=0.9, frame_id=1),  # left
        PersonDetection(bbox=(40, 20, 60, 80), confidence=0.8, frame_id=1),  # center
    ]
    sel = select_front_person(dets, frame)
    assert sel.ok
    assert sel.person is not None
    assert sel.person.detection.bbox == (40, 20, 60, 80)


def test_larger_centered_person_preferred_over_tiny() -> None:
    frame = _frame()
    dets = [
        PersonDetection(bbox=(48, 48, 52, 52), confidence=0.99, frame_id=1),  # tiny center
        PersonDetection(bbox=(30, 10, 70, 90), confidence=0.7, frame_id=1),  # large center
    ]
    sel = select_front_person(dets, frame, min_bbox_area_ratio=0.001)
    assert sel.ok
    assert sel.person is not None
    assert sel.person.detection.bbox == (30, 10, 70, 90)


def test_ambiguous_target_does_not_move() -> None:
    frame = _frame()
    dets = [
        PersonDetection(bbox=(35, 20, 55, 80), confidence=0.9, frame_id=1),
        PersonDetection(bbox=(40, 20, 60, 80), confidence=0.9, frame_id=1),
    ]
    sel = select_front_person(dets, frame, ambiguous_score_delta=0.2)
    assert not sel.ok
    assert sel.code == "AMBIGUOUS_TARGET"


def test_stability_requires_consecutive_frames() -> None:
    tracker = TargetStabilityTracker(required_frames=3)
    for fid in (1, 2, 3):
        frame = _frame(fid)
        det = PersonDetection(bbox=(40, 20, 60, 80), confidence=0.9, frame_id=fid)
        sel = select_front_person([det], frame)
        assert sel.ok
        stable = tracker.update(sel)
        if fid < 3:
            assert not stable.ok
            assert stable.code == "STABILIZING"
        else:
            assert stable.ok
            assert stable.code == "STABLE"
            assert stable.frame is not None
            assert stable.person is not None
            assert stable.person.detection.bbox == (40, 20, 60, 80)
            assert stable.frame.frame_id == fid
