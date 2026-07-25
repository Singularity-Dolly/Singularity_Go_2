# go2ctl — Singularity Go2 Console

Standalone, terminal-first Unitree Go2 control for **Singularity_Go_2**.

Independent from the Dolly backend. The CLI and the future robot-service API must both use the same `Go2Controller`.

## Safety warning

**PersonFollowSkillContainer / EdgeTAM follow does NOT guarantee obstacle avoidance.**

Initial test environment must be:

- flat and clear
- no stairs, loose cables, or chairs in the path
- low speed
- human spotter present
- official Unitree remote / physical recovery method available

This warning is shown at startup and when entering FOLLOWING mode.

## Architecture

```
go2ctl (Typer CLI / terminal console)
        │
        ▼
Go2Controller   ← reusable by future FastAPI thin adapter
        │
        ├── VelocityMux (NONE | MANUAL | FOLLOW | ESTOP)
        ├── Watchdogs (camera / manual TTL / connection / follow)
        ├── Front-person selector (local YOLO / DimOS Detection2D)
        └── Go2Adapter
              ├── DimOSGo2Adapter (real WebRTC + EdgeTAM / PersonFollow)
              └── FakeGo2Adapter (--mock / unit tests)
```

DimOS imports are isolated in `dimos_adapter.py`. Official components reused:

- `UnitreeWebRTCConnection` / Go2 WebRTC path
- `Twist` velocity messages
- `Yolo2DDetector` or Ultralytics (DimOS perception dependency)
- `PersonFollowSkillContainer.follow_person(query, initial_bbox, initial_image)` when injected
- otherwise `EdgeTAMProcessor` + `VisualServoing2D` (same stack as PersonFollowSkillContainer)

Local front-person selection **does not** require OpenAI / Qwen / cloud VLM.

## Install

Use the existing DimOS Python 3.12 venv (do not replace system Python):

```bash
cd ~/Singularity_Go_2
source dimos/.venv/bin/activate   # or your existing DimOS venv
cd robot-console
uv pip install -e ".[dev]"
# or: pip install -e ".[dev]"
```

## Discover robot

```bash
dimos go2tool discover
# typical Go2 Wi-Fi AP address:
# 192.168.123.161
```

## Doctor

```bash
go2ctl doctor
```

Does not move the robot.

## Preflight

```bash
go2ctl preflight --robot-ip 192.168.123.161
```

Checks ping, WebRTC, camera, zero-velocity path. Does not send non-zero movement.

## Manual test

```bash
go2ctl console \
  --robot-ip 192.168.123.161 \
  --mode manual
```

Or:

```bash
./scripts/run_manual.sh --robot-ip 192.168.123.161
```

## Detection-only test (no motion)

```bash
go2ctl start \
  --robot-ip 192.168.123.161 \
  --mode idle \
  --detect-only
```

## Tracker-only (init tracker, block motion)

```bash
go2ctl start \
  --robot-ip 192.168.123.161 \
  --mode follow \
  --tracker-only
```

## Auto-follow

```bash
go2ctl start \
  --robot-ip 192.168.123.161 \
  --mode follow
```

## Emergency stop

In console: **SPACE**

```bash
go2ctl estop
./scripts/emergency_stop.sh
```

Also keep the official remote ready.

## Mock test

```bash
go2ctl console --mock
```

Real mode never silently falls back to mock.

## Tests

```bash
cd robot-console
python -m pytest tests -v
```

## Key bindings

| Key | Action |
|-----|--------|
| W/S | Forward / back |
| A/D | Rotate left / right |
| Q/E | Strafe left / right |
| M | MANUAL mode |
| F | Detect front person → FOLLOWING |
| R | Reacquire front person |
| X | Hold → IDLE |
| I | Detailed status |
| L | Recent logs |
| `[` / `]` | Slow / boost toggle (curses) |
| SPACE | Emergency stop |
| ESC | Safe shutdown |

## Configuration

Priority: defaults < `.env` < environment variables < CLI args.

| Variable | Default |
|----------|---------|
| `ROBOT_IP` | unset |
| `GO2CTL_START_MODE` | follow |
| `GO2CTL_DETECTION_CONFIDENCE` | 0.50 |
| `GO2CTL_TARGET_STABLE_FRAMES` | 3 |
| `GO2CTL_CAMERA_STALE_MS` | 500 |
| `GO2CTL_MANUAL_TTL_MS` | 250 |
| `GO2CTL_MAX_FORWARD_SPEED` | 0.20 |
| `GO2CTL_MAX_REVERSE_SPEED` | 0.15 |
| `GO2CTL_MAX_STRAFE_SPEED` | 0.15 |
| `GO2CTL_MAX_ANGULAR_SPEED` | 0.35 |
| `GO2CTL_LOG_LEVEL` | INFO |
| `GO2CTL_MOCK` | false |

Logs: `~/.local/state/go2ctl/go2ctl.log`

```bash
go2ctl logs
go2ctl logs --follow
go2ctl logs --lines 100
```

## Hardware validation gates

1. `go2ctl doctor` — Python 3.12, DimOS, detector, terminal
2. `go2ctl preflight --robot-ip <IP>` — reachability + WebRTC
3. Camera frame age updates
4. Zero velocity / estop path
5. Manual console low-speed W/S/A/D/Q/E, key TTL stop, SPACE, ESC
6. `--detect-only` shows bbox, no non-zero velocity
7. `--tracker-only` initializes tracker, motion blocked
8. Slow follow with lost-target / camera-stale / SPACE stop

Do not claim a gate passed without real hardware evidence.

## Future backend integration

Do **not** put FastAPI or Dolly logic inside `Go2Controller`.

| API | Controller |
|-----|------------|
| `POST /v1/commands` follow.start | `start_follow_front_person()` |
| `POST /v1/commands` follow.hold | `hold()` |
| `POST /v1/commands` follow.reacquire | `reacquire_front_person()` |
| `POST /v1/stop` | `emergency_stop("api")` |
| `GET /v1/state` | `get_status()` |
| `WS /v1/manual` | `set_manual_velocity(...)` |
| `WS /v1/events` | `subscribe(...)` |

`robot-service/` remains untouched in this package.

## Xubuntu 25 notes

- Official DimOS docs target Ubuntu 22.04/24.04 + Python 3.12
- Use an isolated 3.12 venv (`dimos/.venv` from `install_dimos_xubuntu.sh`)
- On Ubuntu 25, turbojpeg packages may be `libturbojpeg0` / `libturbojpeg0-dev`
- Do not repeatedly reinstall the full DimOS dependency set if already present

## Known limitations

- Follow has no guaranteed obstacle avoidance
- Curses key-up is limited; manual uses TTL (250 ms) to zero velocity
- Standalone `go2ctl estop` without a running session cannot reach the robot process; use SPACE in-console or the Unitree remote
- DimOS Module RPC for PersonFollowSkillContainer may use the EdgeTAM compatibility path outside full blueprint runtime (documented warning in logs)
