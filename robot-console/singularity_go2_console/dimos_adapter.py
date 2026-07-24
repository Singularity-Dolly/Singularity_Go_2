"""DimOS-specific Go2 adapter.

All direct DimOS imports live here. Compatible with dimensionalOS/dimos
PersonFollowSkillContainer.follow_person(query, initial_bbox, initial_image)
where initial_image is base64 JPEG of the exact detection frame.

Compatibility note (DimOS main / 2026):
- GO2Connection: dimos.robot.unitree.go2.connection.GO2Connection
- Transport: UnitreeWebRTCConnection via make_connection / connection_type=webrtc
- Follow: PersonFollowSkillContainer with EdgeTAM + VisualServoing2D
- Outside full blueprint runtime we wire WebRTC + EdgeTAM similarly to
  PersonFollowSkillContainer._follow_person / _follow_loop, reusing the same
  DimOS classes (not a second tracker). If Module stream wiring is injected,
  prefer the real PersonFollowSkillContainer instance.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Any

from singularity_go2_console.adapter_protocol import FollowInit
from singularity_go2_console.front_person import CameraFrame, PersonDetection

logger = logging.getLogger("go2ctl.dimos_adapter")

DIMOS_COMPAT_DOC = (
    "DimOSGo2Adapter targets dimensionalOS/dimos PersonFollowSkillContainer "
    "follow_person(initial_bbox, initial_image=base64-jpeg). "
    "If full Module RPC is unavailable, EdgeTAMProcessor + VisualServoing2D "
    "are reused (same components as PersonFollowSkillContainer)."
)


def dimos_available() -> tuple[bool, str]:
    try:
        import dimos  # noqa: F401

        return True, getattr(dimos, "__file__", "unknown")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


class DimOSGo2Adapter:
    """One physical robot connection per running application."""

    def __init__(
        self,
        *,
        detector_model: str = "yolov8n.pt",
        detection_confidence: float = 0.50,
        follow_skill: Any | None = None,
        max_lost_frames: int = 15,
        control_hz: float = 20.0,
    ) -> None:
        self._detector_model = detector_model
        self._detection_confidence = detection_confidence
        self._injected_follow = follow_skill
        self._max_lost_frames = max_lost_frames
        self._control_hz = control_hz

        self._connected = False
        self._robot_ip: str | None = None
        self._connection: Any | None = None
        self._video_sub: Any | None = None
        self._latest_frame: CameraFrame | None = None
        self._frame_counter = 0
        self._lock = threading.RLock()
        self._detector: Any | None = None
        self._detector_ready = False
        self._tracker: Any | None = None
        self._visual_servo: Any | None = None
        self._follow_thread: threading.Thread | None = None
        self._follow_stop = threading.Event()
        self._following = False
        self._target_visible = False
        self._cmd_bridge: Any | None = None  # optional intercept for injected skill
        self._warned_compat = False

    @property
    def mock(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return self._connected and self._connection is not None

    @property
    def camera_ready(self) -> bool:
        return self._latest_frame is not None

    @property
    def velocity_ready(self) -> bool:
        return self.connected

    @property
    def detector_ready(self) -> bool:
        return self._detector_ready

    @property
    def follow_ready(self) -> bool:
        ok, _ = dimos_available()
        return self.connected and ok

    async def connect(self, robot_ip: str) -> tuple[bool, str, str]:
        ok, detail = dimos_available()
        if not ok:
            return False, "DIMOS_NOT_AVAILABLE", f"DimOS import failed: {detail}"

        try:
            from dimos.msgs.geometry_msgs.Twist import Twist  # noqa: F401
            from dimos.robot.unitree.connection import UnitreeWebRTCConnection
        except Exception as exc:  # noqa: BLE001
            return False, "UNSUPPORTED_DIMOS_VERSION", f"DimOS Go2 imports failed: {exc}"

        try:
            connection = UnitreeWebRTCConnection(robot_ip)
            connection.start()
        except Exception as exc:  # noqa: BLE001
            return False, "WEBRTC_CONNECTION_FAILED", f"WebRTC connect failed: {exc}"

        self._connection = connection
        self._robot_ip = robot_ip
        self._connected = True

        # Subscribe to video stream
        try:
            self._video_sub = connection.video_stream().subscribe(self._on_video)
        except Exception as exc:  # noqa: BLE001
            await self.disconnect()
            return False, "CAMERA_NOT_READY", f"Video subscribe failed: {exc}"

        # Wait briefly for first frame
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and self._latest_frame is None:
            time.sleep(0.05)

        if self._latest_frame is None:
            logger.warning("Connected but no camera frame yet")

        # Best-effort standup / balance (matches GO2Connection.start behavior)
        try:
            if hasattr(connection, "standup"):
                connection.standup()
            if hasattr(connection, "balance_stand"):
                connection.balance_stand()
        except Exception:  # noqa: BLE001
            logger.warning("standup/balance_stand failed", exc_info=True)

        det_ok = self._init_detector()
        if not det_ok:
            logger.warning("Detector not ready; follow acquisition will fail until available")

        return True, "OK", f"Connected to {robot_ip} via DimOS WebRTC"

    def _init_detector(self) -> bool:
        # Prefer DimOS Yolo2DDetector; fall back to ultralytics directly
        try:
            from dimos.perception.detection.detectors.yolo import Yolo2DDetector

            self._detector = Yolo2DDetector()
            self._detector_ready = True
            logger.info("detector_loaded backend=dimos.Yolo2DDetector")
            return True
        except Exception:
            logger.warning("DimOS Yolo2DDetector unavailable; trying ultralytics", exc_info=True)

        try:
            from ultralytics import YOLO

            self._detector = YOLO(self._detector_model)
            self._detector_ready = True
            logger.info("detector_loaded backend=ultralytics model=%s", self._detector_model)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Detector init failed: %s", exc)
            self._detector_ready = False
            return False

    def _on_video(self, image: Any) -> None:
        with self._lock:
            self._frame_counter += 1
            data = getattr(image, "data", image)
            width = int(getattr(image, "width", 0) or getattr(data, "shape", [0, 0])[1])
            height = int(getattr(image, "height", 0) or getattr(data, "shape", [0])[0])
            self._latest_frame = CameraFrame(
                image=data,
                timestamp_s=time.monotonic(),
                frame_id=self._frame_counter,
                encoding="bgr",
                width=width,
                height=height,
            )

    async def disconnect(self) -> tuple[bool, str, str]:
        self.stop_follow()
        self.publish_zero()
        sub = self._video_sub
        self._video_sub = None
        if sub is not None:
            try:
                sub.dispose()
            except Exception:  # noqa: BLE001
                pass
        conn = self._connection
        self._connection = None
        self._connected = False
        if conn is not None:
            try:
                if hasattr(conn, "stop_movement"):
                    conn.stop_movement()
                conn.stop()
            except Exception as exc:  # noqa: BLE001
                return False, "CONNECTION_LOST", f"Disconnect error: {exc}"
        return True, "OK", "disconnected"

    def get_latest_frame(self) -> CameraFrame | None:
        with self._lock:
            return self._latest_frame

    def detect_persons(self, frame: CameraFrame) -> list[PersonDetection]:
        if not self._detector_ready or self._detector is None:
            return []

        # DimOS detector path
        if hasattr(self._detector, "process_image"):
            try:
                # Build a minimal Image-like object if needed
                image = frame.image
                result = self._detector.process_image(image)
                return self._parse_dimos_detections(result, frame)
            except Exception:
                logger.exception("DimOS detector failed")
                return []

        # Ultralytics path
        try:
            results = self._detector.predict(
                source=frame.image,
                conf=self._detection_confidence,
                classes=[0],  # COCO person
                verbose=False,
            )
            out: list[PersonDetection] = []
            for r in results:
                boxes = getattr(r, "boxes", None)
                if boxes is None:
                    continue
                for box in boxes:
                    xyxy = box.xyxy[0].tolist()
                    conf = float(box.conf[0])
                    out.append(
                        PersonDetection(
                            bbox=(float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])),
                            confidence=conf,
                            class_name="person",
                            frame_id=frame.frame_id,
                        )
                    )
            return out
        except Exception:
            logger.exception("Ultralytics detect failed")
            return []

    def _parse_dimos_detections(self, result: Any, frame: CameraFrame) -> list[PersonDetection]:
        out: list[PersonDetection] = []
        detections = getattr(result, "detections", result)
        for det in detections or []:
            name = str(
                getattr(det, "class_name", None)
                or getattr(det, "label", None)
                or getattr(getattr(det, "classification", None), "label", "")
                or "person"
            ).lower()
            if "person" not in name and name not in {"0", "person"}:
                # Some detectors use class id 0
                class_id = getattr(det, "class_id", None)
                if class_id not in (0, "0", None):
                    if name and name != "person":
                        continue
            bbox = getattr(det, "bbox", None) or getattr(det, "bbox_2d", None)
            if bbox is None:
                continue
            if hasattr(bbox, "__iter__"):
                vals = list(bbox)
            else:
                continue
            if len(vals) < 4:
                continue
            conf = float(getattr(det, "confidence", getattr(det, "score", 0.0)))
            out.append(
                PersonDetection(
                    bbox=(float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])),
                    confidence=conf,
                    class_name="person",
                    frame_id=frame.frame_id,
                )
            )
        return out

    def publish_velocity(self, vx: float, vy: float, wz: float) -> bool:
        if self._connection is None:
            return False
        try:
            from dimos.msgs.geometry_msgs.Twist import Twist
            from dimos.msgs.geometry_msgs.Vector3 import Vector3

            twist = Twist()
            twist.linear = Vector3(vx, vy, 0.0)
            twist.angular = Vector3(0.0, 0.0, wz)
            return bool(self._connection.move(twist))
        except Exception:
            logger.exception("publish_velocity failed")
            return False

    def publish_zero(self) -> bool:
        if self._connection is None:
            return False
        try:
            if hasattr(self._connection, "stop_movement"):
                self._connection.stop_movement()
            return self.publish_velocity(0.0, 0.0, 0.0)
        except Exception:
            logger.exception("publish_zero failed")
            try:
                return self.publish_velocity(0.0, 0.0, 0.0)
            except Exception:
                return False

    def encode_frame_jpeg_b64(self, frame: CameraFrame) -> str:
        """Encode the exact detection frame as base64 JPEG for follow_person.

        Raises RuntimeError on failure — never returns a fake/token payload.
        """
        errors: list[str] = []
        try:
            from turbojpeg import TurboJPEG

            jpeg = TurboJPEG().encode(frame.image)
            return base64.b64encode(jpeg).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"turbojpeg: {exc}")
        try:
            import cv2

            ok, buf = cv2.imencode(".jpg", frame.image)
            if not ok:
                raise RuntimeError("cv2.imencode failed")
            return base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cv2: {exc}")
        raise RuntimeError(
            "Failed to encode detection frame as JPEG (" + "; ".join(errors) + ")"
        )

    def start_follow(self, init: FollowInit) -> tuple[bool, str, str]:
        if not self.connected:
            return False, "CONNECTION_LOST", "not connected"

        # Prefer injected PersonFollowSkillContainer
        if self._injected_follow is not None and hasattr(self._injected_follow, "follow_person"):
            try:
                msg = self._injected_follow.follow_person(
                    init.query,
                    initial_bbox=init.bbox,
                    initial_image=init.jpeg_base64,
                )
                text = str(msg).lower()
                if "fail" in text or "could not" in text or "no image" in text:
                    return False, "TRACKER_INIT_FAILED", str(msg)
                self._following = True
                self._target_visible = True
                return True, "OK", str(msg)
            except Exception as exc:  # noqa: BLE001
                return False, "TRACKER_INIT_FAILED", str(exc)

        # Reuse EdgeTAM + VisualServoing2D (same as PersonFollowSkillContainer)
        if not self._warned_compat:
            logger.warning(
                "Using EdgeTAMProcessor compatibility path outside DimOS Module RPC. %s",
                DIMOS_COMPAT_DOC,
            )
            self._warned_compat = True

        try:
            from dimos.models.segmentation.edge_tam import EdgeTAMProcessor
            from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
            from dimos.navigation.visual_servoing.visual_servoing_2d import VisualServoing2D
        except Exception as exc:  # noqa: BLE001
            return False, "FOLLOW_SKILL_NOT_READY", f"Cannot import DimOS follow stack: {exc}"

        self.stop_follow()
        try:
            import numpy as np

            tracker = EdgeTAMProcessor()
            # Build Image for EdgeTAM
            image_obj = self._as_dimos_image(init.frame)
            box = np.array(init.bbox, dtype=np.float32)
            detections = tracker.init_track(image=image_obj, box=box, obj_id=1)
            if detections is None or len(detections) == 0:
                self.publish_zero()
                return False, "TRACKER_INIT_FAILED", "EdgeTAM failed to segment front person"

            try:
                camera_info = CameraInfo.from_yaml(
                    str(
                        __import__("importlib.resources", fromlist=["files"]).files(
                            "dimos.robot.unitree.go2"
                        ).joinpath("front_camera_720.yaml")
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self.publish_zero()
                return (
                    False,
                    "FOLLOW_SKILL_NOT_READY",
                    f"CameraInfo unavailable for VisualServoing2D: {exc}",
                )

            self._visual_servo = VisualServoing2D(camera_info, False)
            self._tracker = tracker
            self._follow_stop.clear()
            self._following = True
            self._target_visible = True
            self._follow_thread = threading.Thread(
                target=self._follow_loop,
                args=(tracker, init.frame.width),
                daemon=True,
            )
            self._follow_thread.start()
            return True, "OK", "EdgeTAM follow started"
        except Exception as exc:  # noqa: BLE001
            self.publish_zero()
            return False, "TRACKER_INIT_FAILED", str(exc)

    def _as_dimos_image(self, frame: CameraFrame) -> Any:
        try:
            from dimos.msgs.sensor_msgs.Image import Image, ImageFormat

            return Image(data=frame.image, format=ImageFormat.BGR)
        except Exception:
            return frame.image

    def _follow_loop(self, tracker: Any, image_width: int) -> None:
        period = 1.0 / self._control_hz
        lost = 0
        while not self._follow_stop.is_set():
            t0 = time.monotonic()
            frame = self.get_latest_frame()
            if frame is None:
                self._last_follow_cmd = (0.0, 0.0, 0.0)
                lost += 1
            else:
                try:
                    image_obj = self._as_dimos_image(frame)
                    detections = tracker.process_image(image_obj)
                    if detections is None or len(detections) == 0:
                        self._last_follow_cmd = (0.0, 0.0, 0.0)
                        self._target_visible = False
                        lost += 1
                        if lost > self._max_lost_frames:
                            break
                    else:
                        lost = 0
                        self._target_visible = True
                        dets = getattr(detections, "detections", detections)
                        best = max(
                            dets,
                            key=lambda d: getattr(d, "bbox_2d_volume", lambda: 0)()
                            if callable(getattr(d, "bbox_2d_volume", None))
                            else 0,
                        )
                        bbox = getattr(best, "bbox", None)
                        if self._visual_servo is not None and bbox is not None:
                            twist = self._visual_servo.compute_twist(bbox, image_width)
                            if twist is not None:
                                lx = float(getattr(twist.linear, "x", 0.0))
                                ly = float(getattr(twist.linear, "y", 0.0))
                                az = float(getattr(twist.angular, "z", 0.0))
                                # Controller VelocityMux (FOLLOW owner) publishes this.
                                self._last_follow_cmd = (lx, ly, az)
                except Exception:
                    logger.exception("follow loop error")
                    self._last_follow_cmd = (0.0, 0.0, 0.0)
                    lost += 1
                    if lost > self._max_lost_frames:
                        break
            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, period - elapsed))

        self._last_follow_cmd = (0.0, 0.0, 0.0)
        self._following = False
        self._target_visible = False

    def pop_follow_velocity(self) -> tuple[float, float, float] | None:
        cmd = getattr(self, "_last_follow_cmd", None)
        self._last_follow_cmd = None
        return cmd

    def stop_follow(self) -> tuple[bool, str, str]:
        self._follow_stop.set()
        if self._injected_follow is not None and hasattr(self._injected_follow, "stop_following"):
            try:
                self._injected_follow.stop_following()
            except Exception:  # noqa: BLE001
                logger.exception("injected stop_following failed")
        thread = self._follow_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._follow_thread = None
        if self._tracker is not None:
            try:
                self._tracker.stop()
            except Exception:  # noqa: BLE001
                pass
        self._tracker = None
        self._following = False
        self._target_visible = False
        self.publish_zero()
        return True, "OK", "follow stopped"

    def is_following(self) -> bool:
        return self._following

    def follow_target_visible(self) -> bool:
        return self._target_visible

    def robot_state(self) -> dict[str, Any]:
        return {
            "robot_ip": self._robot_ip,
            "connected": self.connected,
            "camera_ready": self.camera_ready,
            "detector_ready": self.detector_ready,
            "following": self._following,
            "mock": False,
            "compat": DIMOS_COMPAT_DOC,
        }


