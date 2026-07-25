"""Face tracking for smooth Go2 follow yaw.

WHY: Full-body YOLO boxes bounce with gait/arms → jittery turning.
Face (or head ROI fallback) is a much steadier aiming point.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("go2ctl.face_tracker")

DEFAULT_YUNET = (
    Path(__file__).resolve().parents[1] / "models" / "face_detection_yunet_2023mar.onnx"
)


def head_proxy_bbox(
    person_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Upper ~28% of person box — fallback when face detector misses."""
    x1, y1, x2, y2 = person_bbox
    h = max(1.0, float(y2) - float(y1))
    w = max(1.0, float(x2) - float(x1))
    # Slightly inset horizontally (shoulders wider than head).
    inset = 0.18 * w
    top = float(y1)
    bottom = float(y1) + 0.28 * h
    return (float(x1) + inset, top, float(x2) - inset, bottom)


def bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return 0.5 * (float(x1) + float(x2)), 0.5 * (float(y1) + float(y2))


class FaceTracker:
    """YuNet face detector with head-ROI fallback."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        score_threshold: float = 0.55,
        nms_threshold: float = 0.3,
    ) -> None:
        self._score_threshold = score_threshold
        self._nms_threshold = nms_threshold
        self._detector: Any | None = None
        self._input_size: tuple[int, int] | None = None
        path = Path(model_path) if model_path else DEFAULT_YUNET
        self._model_path = path
        self._init_detector(path)

    @property
    def ready(self) -> bool:
        return self._detector is not None

    def _init_detector(self, path: Path) -> None:
        if not path.is_file():
            logger.warning("YuNet face model missing at %s — head-proxy only", path)
            return
        try:
            import cv2

            self._detector = cv2.FaceDetectorYN_create(
                str(path),
                "",
                (320, 320),
                float(self._score_threshold),
                float(self._nms_threshold),
                5000,
            )
            logger.info("face_tracker_loaded model=%s", path)
        except Exception:  # noqa: BLE001
            logger.warning("FaceDetectorYN init failed — head-proxy only", exc_info=True)
            self._detector = None

    def detect_face_bbox(
        self,
        image_bgr: Any,
        person_bbox: tuple[float, float, float, float] | None = None,
    ) -> tuple[float, float, float, float] | None:
        """Return best face bbox in image coords, or None."""
        if self._detector is None or image_bgr is None:
            return None
        try:
            import cv2

            arr = np.asarray(image_bgr)
            if arr.ndim != 3 or arr.shape[2] < 3:
                return None
            h, w = int(arr.shape[0]), int(arr.shape[1])
            if self._input_size != (w, h):
                self._detector.setInputSize((w, h))
                self._input_size = (w, h)

            _retval, faces = self._detector.detect(arr)
            if faces is None or len(faces) == 0:
                return None

            best: tuple[float, float, float, float] | None = None
            best_score = -1.0
            px1 = py1 = px2 = py2 = 0.0
            use_person = person_bbox is not None
            if use_person:
                px1, py1, px2, py2 = person_bbox  # type: ignore[misc]

            for face in faces:
                fx, fy, fw, fh = float(face[0]), float(face[1]), float(face[2]), float(face[3])
                score = float(face[-1]) if len(face) >= 15 else 1.0
                fbox = (fx, fy, fx + fw, fy + fh)
                fcx, fcy = bbox_center(fbox)
                if use_person:
                    # Prefer faces inside / near the locked person.
                    if not (px1 - 20 <= fcx <= px2 + 20 and py1 - 20 <= fcy <= py2 + 20):
                        continue
                if score > best_score:
                    best_score = score
                    best = fbox
            return best
        except Exception:  # noqa: BLE001
            logger.debug("face detect failed", exc_info=True)
            return None

    def aim_bbox(
        self,
        image_bgr: Any,
        person_bbox: tuple[float, float, float, float],
    ) -> tuple[tuple[float, float, float, float], str]:
        """Face bbox if found else head proxy. Returns (bbox, source)."""
        face = self.detect_face_bbox(image_bgr, person_bbox)
        if face is not None:
            return face, "face"
        return head_proxy_bbox(person_bbox), "head_proxy"
