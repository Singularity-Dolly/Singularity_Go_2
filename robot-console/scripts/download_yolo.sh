#!/usr/bin/env bash
# Download YOLOv8n weights into the project folder for go2ctl follow.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST_DIR="${ROOT}/robot-console/models"
DEST="${DEST_DIR}/yolov8n.pt"
URL="https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt"

mkdir -p "${DEST_DIR}"
mkdir -p "${ROOT}/weights"

echo "Downloading yolov8n.pt (~6.2 MB)…"
echo "To: ${DEST}"

if command -v curl >/dev/null 2>&1; then
  curl -L --retry 5 --retry-all-errors --continue-at - -o "${DEST}" "${URL}"
elif command -v wget >/dev/null 2>&1; then
  wget -c -O "${DEST}" "${URL}"
else
  echo "Need curl or wget"
  exit 1
fi

# Also keep Ultralytics default weights dir copy
cp -f "${DEST}" "${ROOT}/weights/yolov8n.pt"
# And CWD convenience copy (some loaders look here)
cp -f "${DEST}" "${ROOT}/yolov8n.pt"

SIZE="$(wc -c < "${DEST}" | tr -d ' ')"
echo "Saved ${DEST} (${SIZE} bytes)"
if [[ "${SIZE}" -lt 1000000 ]]; then
  echo "ERROR: file too small — download incomplete. Delete and retry."
  exit 1
fi

echo "OK. Use:"
echo "  export GO2CTL_DETECTOR_MODEL=${DEST}"
echo "  go2ctl follow --connection-mode ap --aes-key-file ~/.config/go2ctl/aes_key"

# YuNet face model for smooth yaw aim (~228 KB).
FACE_DEST="${DEST_DIR}/face_detection_yunet_2023mar.onnx"
FACE_URL="https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
if [[ ! -f "${FACE_DEST}" ]] || [[ "$(wc -c < "${FACE_DEST}" | tr -d ' ')" -lt 100000 ]]; then
  echo "Downloading YuNet face model…"
  if command -v curl >/dev/null 2>&1; then
    curl -L --retry 5 --retry-all-errors -o "${FACE_DEST}" "${FACE_URL}"
  else
    wget -O "${FACE_DEST}" "${FACE_URL}"
  fi
  echo "Saved ${FACE_DEST}"
else
  echo "Face model already present: ${FACE_DEST}"
fi

