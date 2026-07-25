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

import asyncio
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
        connection_mode: str = "ap",
        aes_key: str | None = None,
        allow_normal_mode_switch: bool = False,
        enable_video: bool = True,
    ) -> None:
        self._detector_model = detector_model
        self._detection_confidence = detection_confidence
        self._injected_follow = follow_skill
        self._max_lost_frames = max_lost_frames
        self._control_hz = control_hz
        mode = (connection_mode or "ap").strip().lower()
        if mode not in {"ap", "sta"}:
            raise ValueError("connection_mode must be ap or sta")
        self._connection_mode = mode
        self._aes_key = aes_key
        self._allow_normal_mode_switch = bool(allow_normal_mode_switch)
        self._enable_video = bool(enable_video)

        self._connected = False
        self._robot_ip: str | None = None
        self._session: Any | None = None
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
        self._published_commands: list[tuple[float, float, float]] = []
        self.last_velocity_error_code: str | None = None
        self.last_velocity_error_message: str = ""

    @property
    def mock(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return self._connected and self._session is not None

    @property
    def camera_ready(self) -> bool:
        return self._latest_frame is not None

    @property
    def velocity_ready(self) -> bool:
        return self.connected and bool(
            self._session and getattr(self._session, "velocity_channel_ok", False)
        )

    @property
    def detector_ready(self) -> bool:
        return self._detector_ready

    @property
    def follow_ready(self) -> bool:
        ok, _ = dimos_available()
        return self.connected and ok

    async def connect(self, robot_ip: str | None = None) -> tuple[bool, str, str]:
        if not self._aes_key:
            return False, "AES_KEY_REQUIRED", "AES-128 key required for Go2 firmware auth"

        if self._connection_mode == "sta" and not (robot_ip and str(robot_ip).strip()):
            return False, "STA_ROBOT_IP_REQUIRED", "STA mode requires --robot-ip"

        from singularity_go2_console.aes import redact_secrets
        from singularity_go2_console.webrtc_connect import (
            LOCAL_AP_IP,
            build_unitree_connection,
            start_connection,
        )

        secrets = [self._aes_key]
        connection, code, message = build_unitree_connection(
            connection_mode=self._connection_mode,  # type: ignore[arg-type]
            robot_ip=robot_ip,
            aes_key=self._aes_key,
        )
        if connection is None:
            return False, code, redact_secrets(message, secrets)

        ok, code, session, message = await start_connection(
            connection,
            connection_mode=self._connection_mode,  # type: ignore[arg-type]
            allow_normal_mode_switch=self._allow_normal_mode_switch,
        )
        if not ok or session is None:
            return False, code, redact_secrets(message, secrets)

        self._session = session
        self._connection = session
        self._robot_ip = (
            LOCAL_AP_IP
            if self._connection_mode == "ap"
            else (str(robot_ip).strip() if robot_ip else None)
        )
        self._connected = True
        self._published_commands = []

        if self._enable_video:
            if not session.enable_video(self._on_av_frame):
                logger.warning("Video channel enable failed at connect")
        else:
            logger.info("Video disabled for this session (teleop/console)")

        if self._enable_video:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and self._latest_frame is None:
                await asyncio.sleep(0.05)

            if self._latest_frame is None:
                logger.warning("Connected but no camera frame yet")
        else:
            # Synthetic frame so preflight/status paths that expect a frame stay calm.
            self._frame_counter += 1
            self._latest_frame = CameraFrame(
                image=[[[0, 0, 0]]],
                timestamp_s=time.monotonic(),
                frame_id=self._frame_counter,
                encoding="rgb",
                width=1,
                height=1,
            )

        # Never auto-standup here — preflight and connect must remain no-motion.
        if self._enable_video:
            det_ok = self._init_detector()
            if not det_ok:
                logger.warning(
                    "Detector not ready; follow acquisition will fail until available"
                )
        else:
            logger.info("Skipping detector init (teleop session without video)")

        mode_label = "LocalAP" if self._connection_mode == "ap" else "LocalSTA"
        return True, "OK", f"Connected via {mode_label} (AES authenticated)"

    def _on_av_frame(self, frame: Any) -> None:
        try:
            array = frame.to_ndarray(format="rgb24")
            height, width = int(array.shape[0]), int(array.shape[1])
        except Exception:  # noqa: BLE001
            logger.debug("frame convert failed", exc_info=True)
            return
        with self._lock:
            self._frame_counter += 1
            self._latest_frame = CameraFrame(
                image=array,
                timestamp_s=time.monotonic(),
                frame_id=self._frame_counter,
                encoding="rgb",
                width=width,
                height=height,
            )

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
        session = self._session
        self._session = None
        self._connection = None
        self._connected = False
        if session is not None:
            try:
                session.close()
            except Exception as exc:  # noqa: BLE001
                return False, "CONNECTION_LOST", f"Disconnect error: {exc}"
        return True, "OK", "disconnected"

    async def run_preflight(
        self,
        robot_ip: str | None = None,
        *,
        frame_advance_s: float = 5.0,
    ) -> dict[str, Any]:
        """NO-MOTION preflight. Never publishes non-zero velocity."""
        from singularity_go2_console.webrtc_connect import LOCAL_AP_IP

        target_ip = (
            LOCAL_AP_IP
            if self._connection_mode == "ap"
            else (robot_ip or self._robot_ip)
        )
        out: dict[str, Any] = {
            "connection_mode": self._connection_mode,
            "robot_ip": target_ip,
            "nonzero_velocity_sent": False,
            "commands": [],
        }
        disconnected = False
        try:
            ok, code, message = await self.connect(
                None if self._connection_mode == "ap" else target_ip
            )
            out["connect"] = {"ok": ok, "code": code, "message": message}
            out["aes_authenticated"] = bool(ok)
            if not ok:
                out["ok"] = False
                out["error_code"] = code
                return out

            out["data_channel"] = {
                "ok": bool(self._session and self._session.datachannel_ok)
            }
            out["velocity_channel"] = {
                "ok": bool(self._session and self._session.velocity_channel_ok)
            }
            if not out["data_channel"]["ok"]:
                out["ok"] = False
                out["error_code"] = "WEBRTC_DATA_CHANNEL_FAILED"
                return out
            if not out["velocity_channel"]["ok"]:
                out["ok"] = False
                out["error_code"] = "VELOCITY_CHANNEL_UNAVAILABLE"
                return out

            start_id = self._latest_frame.frame_id if self._latest_frame else None
            deadline = time.monotonic() + frame_advance_s
            advanced = False
            while time.monotonic() < deadline:
                frame = self.get_latest_frame()
                if frame is not None and start_id is not None and frame.frame_id > start_id:
                    advanced = True
                    break
                if frame is not None and start_id is None:
                    start_id = frame.frame_id
                await asyncio.sleep(0.05)
            end_frame = self.get_latest_frame()
            out["camera"] = {
                "ready": end_frame is not None,
                "start_frame_id": start_id,
                "end_frame_id": getattr(end_frame, "frame_id", None),
                "frames_advanced": advanced,
            }
            if not advanced:
                out["ok"] = False
                out["error_code"] = "CAMERA_STREAM_UNAVAILABLE"
                return out

            zero_ok = self.publish_zero()
            stop_ok = bool(self._session and self._session.stop_movement())
            sport_requests = list(getattr(self._session, "sport_requests", []) or [])
            out["zero_velocity"] = zero_ok
            out["stop_path"] = stop_ok
            out["commands"] = list(self._published_commands)
            out["sport_requests"] = sport_requests
            out["move_sent"] = any(
                str(item.get("api")) == "Move" for item in sport_requests
            )
            out["nonzero_velocity_sent"] = any(
                abs(vx) > 1e-12 or abs(vy) > 1e-12 or abs(wz) > 1e-12
                for vx, vy, wz in self._published_commands
            )
            if out["move_sent"]:
                out["ok"] = False
                out["error_code"] = "INTERNAL_ERROR"
                out["message"] = "preflight sent sport Move — abort"
                return out
            if out["nonzero_velocity_sent"]:
                out["ok"] = False
                out["error_code"] = "INTERNAL_ERROR"
                out["message"] = "preflight sent non-zero velocity — abort"
                return out
            if not zero_ok or not stop_ok:
                out["ok"] = False
                out["error_code"] = "VELOCITY_CHANNEL_UNAVAILABLE"
                return out
            out["ok"] = True
            return out
        finally:
            try:
                await self.disconnect()
                disconnected = True
            except Exception as exc:  # noqa: BLE001
                out["disconnect_error"] = str(exc)
            out["disconnected"] = disconnected

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
        if self._session is None:
            self.last_velocity_error_code = "VELOCITY_OUTPUT_NOT_READY"
            self.last_velocity_error_message = "session not connected"
            return False
        ok = bool(self._session.move(vx, vy, wz))
        if ok:
            self._published_commands.append((float(vx), float(vy), float(wz)))
            self.last_velocity_error_code = None
            self.last_velocity_error_message = ""
            return True
        self.last_velocity_error_code = getattr(
            self._session, "last_error_code", "VELOCITY_OUTPUT_NOT_READY"
        )
        self.last_velocity_error_message = getattr(
            self._session, "last_error_message", "Move failed"
        )
        return False

    def publish_zero(self) -> bool:
        if self._session is None:
            self.last_velocity_error_code = "VELOCITY_OUTPUT_NOT_READY"
            self.last_velocity_error_message = "session not connected"
            return False
        ok = bool(self._session.stop_movement())
        self._published_commands.append((0.0, 0.0, 0.0))
        if ok:
            self.last_velocity_error_code = None
            self.last_velocity_error_message = ""
        else:
            self.last_velocity_error_code = getattr(
                self._session, "last_error_code", "VELOCITY_OUTPUT_NOT_READY"
            )
            self.last_velocity_error_message = getattr(
                self._session, "last_error_message", "StopMove failed"
            )
        return ok

    def ensure_normal_mode(self) -> tuple[bool, str, str]:
        """Operator-gated switch to motion mode normal (disabled unless configured)."""
        if not self._allow_normal_mode_switch:
            return (
                False,
                "MOTION_MODE_SWITCH_DISABLED",
                "Pass --allow-normal-mode-switch to enable operator mode switch",
            )
        if self._session is None:
            return False, "VELOCITY_OUTPUT_NOT_READY", "session not connected"
        return self._session.ensure_normal_mode()

    def get_motion_mode(self) -> tuple[str | None, str, str]:
        if self._session is None:
            return None, "VELOCITY_OUTPUT_NOT_READY", "session not connected"
        return self._session.get_motion_mode(use_cache=False)

    def encode_frame_jpeg_bytes(self, frame: CameraFrame) -> bytes | None:
        try:
            from turbojpeg import TurboJPEG
            return TurboJPEG().encode(frame.image)
        except Exception:
            pass
        try:
            import cv2
            ok, buf = cv2.imencode(".jpg", frame.image)
            if not ok:
                return None
            return buf.tobytes()
        except Exception:
            return None

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

        edge_err: str | None = None
        try:
            from dimos.models.segmentation.edge_tam import EdgeTAMProcessor
            from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
            from dimos.navigation.visual_servoing.visual_servoing_2d import VisualServoing2D

            self.stop_follow()
            import numpy as np

            tracker = EdgeTAMProcessor()
            image_obj = self._as_dimos_image(init.frame)
            box = np.array(init.bbox, dtype=np.float32)
            detections = tracker.init_track(image=image_obj, box=box, obj_id=1)
            if detections is None or len(detections) == 0:
                raise RuntimeError("EdgeTAM failed to segment front person")

            camera_info = CameraInfo.from_yaml(
                str(
                    __import__("importlib.resources", fromlist=["files"]).files(
                        "dimos.robot.unitree.go2"
                    ).joinpath("front_camera_720.yaml")
                )
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
            edge_err = str(exc)
            logger.warning(
                "EdgeTAM follow unavailable (%s) — using YOLO bbox follow fallback",
                edge_err,
            )

        # CPU-friendly fallback: re-detect person each frame and servo on bbox.
        return self._start_yolo_bbox_follow(init, edge_err=edge_err)

    def _start_yolo_bbox_follow(
        self, init: FollowInit, *, edge_err: str | None = None
    ) -> tuple[bool, str, str]:
        if not self.detector_ready:
            self.publish_zero()
            return (
                False,
                "FOLLOW_SKILL_NOT_READY",
                f"EdgeTAM failed ({edge_err}) and detector not ready for YOLO fallback",
            )
        self.stop_follow()
        self._follow_stop.clear()
        self._following = True
        self._target_visible = True
        self._visual_servo = None
        self._tracker = None
        width = int(init.frame.width or 640)
        height = int(init.frame.height or 480)
        self._follow_thread = threading.Thread(
            target=self._yolo_follow_loop,
            args=(width, height),
            daemon=True,
        )
        self._follow_thread.start()
        msg = "YOLO bbox follow started"
        if edge_err:
            msg += f" (EdgeTAM fallback: {edge_err})"
        return True, "OK", msg

    def _yolo_follow_loop(self, image_width: int, image_height: int) -> None:
        """Simple person follow using YOLO detections (CPU OK)."""
        period = 1.0 / self._control_hz
        lost = 0
        # Keep person roughly centered and at a comfortable size.
        target_area = 0.12
        kp_yaw = 1.2
        kp_x = 0.8
        while not self._follow_stop.is_set():
            t0 = time.monotonic()
            frame = self.get_latest_frame()
            if frame is None or not self.detector_ready:
                self._last_follow_cmd = (0.0, 0.0, 0.0)
                lost += 1
            else:
                try:
                    dets = self.detect_persons(frame)
                    if not dets:
                        self._last_follow_cmd = (0.0, 0.0, 0.0)
                        self._target_visible = False
                        lost += 1
                        if lost > self._max_lost_frames:
                            break
                    else:
                        lost = 0
                        self._target_visible = True
                        best = max(
                            dets,
                            key=lambda d: max(0.0, (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1])),
                        )
                        x1, y1, x2, y2 = best.bbox
                        cx = 0.5 * (x1 + x2)
                        area = max(0.0, (x2 - x1) * (y2 - y1)) / max(
                            1.0, float(image_width * image_height)
                        )
                        err_x = (cx / max(1.0, float(image_width))) - 0.5
                        wz = max(-0.35, min(0.35, -kp_yaw * err_x))
                        # Closer (large bbox) → slow/back; far → forward.
                        vx = max(-0.15, min(0.45, kp_x * (target_area - area)))
                        if abs(err_x) > 0.25:
                            vx *= 0.35  # turn first if far off-center
                        self._last_follow_cmd = (float(vx), 0.0, float(wz))
                except Exception:
                    logger.exception("yolo follow loop error")
                    self._last_follow_cmd = (0.0, 0.0, 0.0)
                    lost += 1
                    if lost > self._max_lost_frames:
                        break
            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, period - elapsed))

        self._last_follow_cmd = (0.0, 0.0, 0.0)
        self._following = False
        self._target_visible = False

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


