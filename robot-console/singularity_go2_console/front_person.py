"""Local front-person selection (no cloud VLM)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

logger = logging.getLogger("go2ctl.front_person")


@dataclass(slots=True)
class PersonDetection:
    bbox: tuple[float, float, float, float]  # x1,y1,x2,y2
    confidence: float
    class_name: str = "person"
    frame_id: int | None = None


@dataclass(slots=True)
class ScoredPerson:
    detection: PersonDetection
    score: float
    center_score: float
    area_score: float
    confidence_score: float


@dataclass(slots=True)
class CameraFrame:
    """Exact frame used for detection; must stay paired with bbox."""

    image: Any  # array-like HxWxC (numpy or list); kept opaque for pairing
    timestamp_s: float
    frame_id: int
    encoding: str = "bgr"
    width: int = 0
    height: int = 0

    def __post_init__(self) -> None:
        if self.width and self.height:
            return
        shape = getattr(self.image, "shape", None)
        if shape is not None and len(shape) >= 2:
            self.height = int(shape[0])
            self.width = int(shape[1])
            return
        if isinstance(self.image, (list, tuple)) and self.image:
            self.height = len(self.image)
            row0 = self.image[0]
            self.width = len(row0) if isinstance(row0, (list, tuple)) else 0


@dataclass(slots=True)
class FrontPersonSelection:
    ok: bool
    code: str
    message: str
    person: ScoredPerson | None = None
    frame: CameraFrame | None = None
    candidates: list[ScoredPerson] | None = None


class PersonDetector(Protocol):
    def detect_persons(self, frame: CameraFrame) -> Sequence[PersonDetection]: ...

    @property
    def ready(self) -> bool: ...


def bbox_area(bbox: tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def score_person(
    detection: PersonDetection,
    *,
    image_width: int,
    image_height: int,
    center_weight: float = 0.55,
    area_weight: float = 0.25,
    confidence_weight: float = 0.20,
    min_bbox_area_ratio: float = 0.005,
) -> ScoredPerson | None:
    if detection.class_name.lower() != "person":
        return None
    x1, y1, x2, y2 = detection.bbox
    if x2 <= x1 or y2 <= y1:
        return None

    cx = (x1 + x2) / 2.0
    normalized_dx = abs(cx - (image_width / 2.0)) / max(image_width / 2.0, 1.0)
    center_score = max(0.0, 1.0 - normalized_dx)

    area = bbox_area(detection.bbox)
    image_area = max(float(image_width * image_height), 1.0)
    area_ratio = area / image_area
    if area_ratio < min_bbox_area_ratio:
        return None
    # Prefer moderately large boxes; saturate at 30% of image.
    area_score = min(1.0, area_ratio / 0.30)
    confidence_score = max(0.0, min(1.0, float(detection.confidence)))

    # Vertical sanity: person should not be only in extreme top strip.
    cy = (y1 + y2) / 2.0
    if cy < image_height * 0.05:
        return None

    total = (
        center_score * center_weight
        + area_score * area_weight
        + confidence_score * confidence_weight
    )
    return ScoredPerson(
        detection=detection,
        score=total,
        center_score=center_score,
        area_score=area_score,
        confidence_score=confidence_score,
    )


def select_front_person(
    detections: Sequence[PersonDetection],
    frame: CameraFrame,
    *,
    confidence_threshold: float = 0.50,
    center_weight: float = 0.55,
    area_weight: float = 0.25,
    confidence_weight: float = 0.20,
    min_bbox_area_ratio: float = 0.005,
    ambiguous_score_delta: float = 0.05,
) -> FrontPersonSelection:
    """Pick the best person directly in front of the robot from local detections."""
    scored: list[ScoredPerson] = []
    for det in detections:
        if det.confidence < confidence_threshold:
            continue
        if det.frame_id is not None and det.frame_id != frame.frame_id:
            return FrontPersonSelection(
                ok=False,
                code="FRAME_BBOX_MISMATCH",
                message="Detection frame_id does not match camera frame",
            )
        item = score_person(
            det,
            image_width=frame.width,
            image_height=frame.height,
            center_weight=center_weight,
            area_weight=area_weight,
            confidence_weight=confidence_weight,
            min_bbox_area_ratio=min_bbox_area_ratio,
        )
        if item is not None:
            scored.append(item)

    if not scored:
        return FrontPersonSelection(
            ok=False,
            code="NO_PERSON_FOUND",
            message="No valid person detection in front of robot",
            frame=frame,
            candidates=[],
        )

    scored.sort(key=lambda s: s.score, reverse=True)
    best = scored[0]
    if len(scored) > 1:
        second = scored[1]
        # Ambiguous if two near-center candidates have nearly identical scores
        # and both are reasonably centered.
        if (
            abs(best.score - second.score) < ambiguous_score_delta
            and best.center_score > 0.7
            and second.center_score > 0.7
        ):
            return FrontPersonSelection(
                ok=False,
                code="AMBIGUOUS_TARGET",
                message="Multiple similar front persons; refusing to move",
                frame=frame,
                candidates=scored,
            )

    return FrontPersonSelection(
        ok=True,
        code="OK",
        message="Front person selected",
        person=best,
        frame=frame,
        candidates=scored,
    )


class TargetStabilityTracker:
    """Require N consecutive compatible detections before follow begins."""

    def __init__(self, required_frames: int = 3, iou_threshold: float = 0.3) -> None:
        self.required_frames = required_frames
        self.iou_threshold = iou_threshold
        self._count = 0
        self._last_bbox: tuple[float, float, float, float] | None = None
        self._last_frame: CameraFrame | None = None
        self._last_person: ScoredPerson | None = None

    @staticmethod
    def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        if inter <= 0:
            return 0.0
        area_a = bbox_area(a)
        area_b = bbox_area(b)
        return inter / max(area_a + area_b - inter, 1e-6)

    def reset(self) -> None:
        self._count = 0
        self._last_bbox = None
        self._last_frame = None
        self._last_person = None

    def update(self, selection: FrontPersonSelection) -> FrontPersonSelection:
        if not selection.ok or selection.person is None or selection.frame is None:
            self.reset()
            return selection

        bbox = selection.person.detection.bbox
        if self._last_bbox is None:
            self._count = 1
        elif self.iou(self._last_bbox, bbox) >= self.iou_threshold:
            self._count += 1
        else:
            self._count = 1

        self._last_bbox = bbox
        self._last_frame = selection.frame
        self._last_person = selection.person

        if self._count < self.required_frames:
            return FrontPersonSelection(
                ok=False,
                code="STABILIZING",
                message=f"Stabilizing target {self._count}/{self.required_frames}",
                person=selection.person,
                frame=selection.frame,
                candidates=selection.candidates,
            )

        return FrontPersonSelection(
            ok=True,
            code="STABLE",
            message=f"Target stable for {self._count} frames",
            person=self._last_person,
            frame=self._last_frame,
            candidates=selection.candidates,
        )
