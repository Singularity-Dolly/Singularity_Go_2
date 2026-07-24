# API 契约参考（Nero ↔ Ceaser）

> 基于 Nero 的 `ceaser workflow.txt` 冻结的 API 契约，结合 DimOS 实现映射

---

## 端点总览

| 端点 | 方法 | Nero 契约 | DimOS 实现映射 |
|------|------|-----------|---------------|
| `/v1/health` | GET | 健康检查 | `GO2Connection.connected` |
| `/v1/state` | GET | 机器人状态 | 聚合多个 stream 状态 |
| `/v1/frame.jpg` | GET | 单帧 JPEG | `color_image` stream 最新帧 |
| `/v1/video.mjpeg` | GET | MJPEG 流 | `color_image` stream 持续推送 |
| `/v1/commands` | POST | 高层指令 | 映射到 @skill RPC 调用 |
| `/v1/stop` | POST | 紧急停止 | `dimos stop` + 所有运动停止 |
| `/v1/events` | WS | 实时状态推送 | 聚合状态变化事件 |

---

## 1. GET /v1/health

**响应：**
```json
{
  "status": "ok",
  "robot_connected": true,
  "uptime_seconds": 3600,
  "ts": "2026-07-24T12:00:00Z"
}
```

**DimOS 映射：**
- `robot_connected` ← `GO2Connection.connected`
- `uptime_seconds` ← 模块启动时间

---

## 2. GET /v1/state

**响应：**
```json
{
  "robot_id": "go2_62507",
  "connected": true,
  "mode": "idle",
  "target": {
    "locked": false,
    "track_id": null,
    "confidence": null
  },
  "scan": {
    "active": false,
    "path": [],
    "map_source": "unavailable"
  },
  "safety": {
    "estop": false,
    "obstacle": false,
    "heartbeat_ok": true,
    "last_command_age_ms": 0
  },
  "ts": "2026-07-24T12:00:00Z"
}
```

**DimOS 映射：**
- `mode` ← DimOS robot mode: `idle` / `navigating` / `following` / `exploring`
- `target.locked` ← `PersonTracker.has_target`
- `target.confidence` ← `PersonTracker.confidence`
- `scan.active` ← `WavefrontFrontierExplorer.is_running`
- `scan.map_source` ← `"voxel"` if SLAM has map, `"coverage"` if odometry only
- `safety.obstacle` ← `CostMapper.obstacle_nearby`
- `safety.heartbeat_ok` ← `GO2Connection.connected`

**重要规则（Nero 强调）：**
- 不发送假数据：用 `null` 或 `"unavailable"` 替代
- 不伪造 battery、pose、distance、map、coverage

---

## 3. GET /v1/frame.jpg

**响应：** `image/jpeg` 二进制

**DimOS 映射：**
- 从 `color_image: In[Image]` stream 最新帧编码为 JPEG
- 帧超过 500ms 未更新 → 返回 503

---

## 4. GET /v1/video.mjpeg

**响应：** `multipart/x-mixed-replace; boundary=frame`

**DimOS 映射：**
- 持续订阅 `color_image` stream
- 每帧编码为 JPEG 并通过 MJPEG boundary 推送

---

## 5. POST /v1/commands

**请求：**
```json
{
  "v": "1.0",
  "request_id": "uuid",
  "ttl_ms": 1000,
  "command": "follow.start",
  "target": {
    "kind": "primary_person",
    "track_id": null
  }
}
```

**允许的命令：**
| command | 说明 | DimOS 映射 |
|---------|------|-----------|
| `scan.start` | 开始扫描 | `WavefrontFrontierExplorer.start()` |
| `follow.start` | 开始跟随 | `PersonFollowSkillContainer.follow_person()` |
| `follow.hold` | 保持位置 | `NavigationSkillContainer.stop_navigation()` |
| `mission.stop` | 停止任务 | 停止所有运动 + 探索 |

**响应（receipt）：**
```json
{
  "v": "1.0",
  "request_id": "same-uuid",
  "accepted": true,
  "executed": true,
  "robot_mode": "following",
  "reason": "target_locked",
  "ts": "2026-07-24T12:00:00Z"
}
```

**安全规则：**
- TTL 过期 → `accepted: false, reason: "ttl_expired"`
- 已经在执行互斥命令 → `accepted: false, reason: "busy"`
- 紧急停止激活 → `accepted: false, reason: "estop_active"`

---

## 6. POST /v1/stop

**请求：** 无 body（或空）

**响应：**
```json
{
  "v": "1.0",
  "request_id": "emergency",
  "accepted": true,
  "executed": true,
  "robot_mode": "idle",
  "reason": "emergency_stop",
  "ts": "2026-07-24T12:00:00Z"
}
```

**DimOS 映射：**
- 调用所有运动模块的 stop
- 设置 estop 标志
- 不依赖 LLM 回路（直接停止）

---

## 7. WS /v1/events

**连接后持续推送：**

```json
{
  "type": "robot.state",
  "robot_id": "go2_62507",
  "connected": true,
  "mode": "idle",
  "target": {
    "locked": false,
    "track_id": null,
    "confidence": null
  },
  "scan": {
    "active": false,
    "path": [],
    "map_source": "unavailable"
  },
  "safety": {
    "estop": false,
    "obstacle": false,
    "heartbeat_ok": true,
    "last_command_age_ms": 0
  },
  "ts": "2026-07-24T12:00:00Z"
}
```

**推送频率：** 状态变化时立即推送，否则每 200ms 心跳

---

## 认证

- 使用 `Authorization: Bearer <token>` header
- Token 在两端 `.env` 中配置相同值
- 不放入 GitHub 或公开 Discord

---

## DimOS 模块间通信

```
┌──────────────────────────────────────────────────────┐
│  DollyGatewayModule (自定义)                          │
│  ┌────────────────────────────────────────────────┐  │
│  │  FastAPI + uvicorn on 0.0.0.0:8780             │  │
│  │  color_image: In[Image]  ← GO2Connection       │  │
│  │  robot_state: In[RobotState] ← GO2Connection   │  │
│  │  → RPC: PersonFollowSkill.follow_person()      │  │
│  │  → RPC: NavigationSkill.navigate_with_text()   │  │
│  │  → RPC: WavefrontFrontierExplorer.start()      │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```