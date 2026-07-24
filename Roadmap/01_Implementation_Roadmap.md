# Singularity_Go_2 实现路线图

> 基于 DimOS 官方文档 + Nero 工作流契约，为 Ceaser 的 WSL Ubuntu 环境制定

---

## 架构全景

```
┌─────────────────────────────────────────────────────────┐
│  Nero Dolly (Windows)                                   │
│  RobotGatewayClient → HTTP/WS → Ceaser robot-service    │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Ceaser WSL Ubuntu (robot-service)                      │
│  ┌───────────────────────────────────────────────────┐  │
│  │  Custom Blueprint: singularity-go2                │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │  DimOS 内置模块 (80%)                        │  │  │
│  │  │  • GO2Connection (WebRTC)                    │  │  │
│  │  │  • VoxelGridMapper + CostMapper + A*         │  │  │
│  │  │  • Detection2D + PersonTracker               │  │  │
│  │  │  • PersonFollowSkillContainer                │  │  │
│  │  │  • NavigationSkillContainer                  │  │  │
│  │  │  • SpatialMemory                             │  │  │
│  │  │  • McpServer + McpClient                     │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │  自定义模块 (20%)                             │  │  │
│  │  │  • DollyGatewayModule (HTTP/WS on :8780)    │  │  │
│  │  │  • SingularitySkillContainer (@skill)        │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Unitree Go2 (WebRTC, 无需 jailbreak)                   │
└─────────────────────────────────────────────────────────┘
```

---

## 阶段 0：环境准备（Day 0）

### 0.1 WSL Ubuntu 初始化
- [ ] 确认 WSL Ubuntu 版本 ≥ 22.04
- [ ] 确认 Python 3.12 可用
- [ ] 确认 CUDA GPU 可用（可选，感知功能需要）
- [ ] 配置双网卡：eth0 → 团队 LAN，eth1 → 机器人网络

### 0.2 DimOS 安装
```bash
git clone https://github.com/dimensionalOS/dimos.git
cd dimos
uv venv --python 3.12
source .venv/bin/activate
uv pip install --pre -e '.[base,unitree]'
```

### 0.3 无硬件验证（回放模式）
```bash
dimos --replay run unitree-go2
```
预期：Rerun 窗口显示机器人相机、SLAM 地图、规划路径。

### 0.4 硬件门禁测试（30 分钟）
按照 Nero 要求，逐一验证：
```bash
export ROBOT_IP=<机器人IP>
export UNITREE_AES_128_KEY=<AES密钥>  # 如需要

dimos go2tool discover          # 确认型号
dimos run unitree-go2-basic     # 基础连接
dimos run unitree-go2           # 完整导航栈
```

输出 Nero 要求的门禁报告：
```
MODEL:        [Air/Pro/EDU]
SDK INIT:     [WebRTC 连接成功/失败]
FRONT CAMERA: [Rerun 中可见/不可见]
STATE:        [odometry 可读/不可读]
MOVE:         [navigation goal 成功/失败]
STOP:         [dimos stop 生效/不生效]
OBSTACLE DATA:[LiDAR 点云可用/不可用]
BLOCKER:      [阻塞项]
```

---

## 阶段 1：基础服务（对应 Nero 00:30–01:30）

### 1.1 创建自定义 Blueprint

在 `dimos/robot/unitree/go2/blueprints/` 下创建 `singularity/` 目录：

```
singularity/
├── __init__.py
├── blueprint.py           # singularity-go2 blueprint
├── dolly_gateway.py       # DollyGatewayModule
└── singularity_skills.py  # 自定义 @skill 方法
```

### 1.2 DollyGatewayModule（核心定制）

实现 Nero 要求的 API 契约，端口 8780：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/health` | GET | 健康检查 |
| `/v1/state` | GET | 机器人状态（映射 DimOS 内部状态） |
| `/v1/frame.jpg` | GET | 单帧画面（从 DimOS camera stream 取） |
| `/v1/video.mjpeg` | GET | MJPEG 视频流 |
| `/v1/commands` | POST | 高层指令（映射到 @skill 调用） |
| `/v1/stop` | POST | 紧急停止 |
| `/v1/events` | WS | 实时状态推送 |

**实现方式：** 使用 FastAPI + uvicorn 嵌入 DimOS Module，从 `color_image: In[Image]` stream 取帧，从 `robot_state` stream 取状态，通过 RPC 调用其他模块的 skills。

### 1.3 状态映射

DimOS 内部状态 → Nero 契约状态：

```python
# DimOS robot mode → Nero mode
{
    "idle": "idle",
    "navigating": "following",  # 或 scanning
    "following": "following",
    "exploring": "scanning",
}

# DimOS safety → Nero safety
{
    "estop": robot.emergency_stop_active,
    "obstacle": costmap.has_obstacle_nearby,
    "heartbeat_ok": connection.connected,
    "last_command_age_ms": time_since_last_command,
}
```

---

## 阶段 2：视频端点（对应 Nero 01:30–02:30）

### 2.1 视频流实现

DimOS 已经通过 `GO2Connection` 提供了 `color_image` stream。DollyGatewayModule 订阅该 stream：

```python
class DollyGatewayModule(Module):
    color_image: In[Image]  # 从 GO2Connection 自动连接

    def _on_start(self):
        self.color_image.subscribe(self._on_frame)

    def _on_frame(self, img: Image):
        # 缓存最新帧供 /v1/frame.jpg 和 /v1/video.mjpeg 使用
        self._latest_frame = img
```

### 2.2 验证清单
- [ ] `GET /v1/frame.jpg` 返回 Go2 真实画面
- [ ] `GET /v1/video.mjpeg` 持续推流
- [ ] Nero 在 OBS 中能添加 `CAM_ROBOT` 源

---

## 阶段 3：目标跟踪 + 跟随（对应 Nero 02:30–03:30）

### 3.1 使用 DimOS 内置 PersonFollow

DimOS 已有 `PersonFollowSkillContainer`：
- `follow_person` — 开始跟随
- `stop_following` — 停止跟随

### 3.2 自定义 SingularitySkillContainer

在 `singularity_skills.py` 中封装 Nero 的高层指令：

```python
class SingularitySkillContainer(Module):
    @skill
    def follow_start(self) -> str:
        """Start person following. Maps to Nero's follow.start."""
        # 调用 DimOS PersonFollowSkillContainer.follow_person
        ...

    @skill
    def follow_hold(self) -> str:
        """Hold current position. Maps to Nero's follow.hold."""
        ...

    @skill
    def scan_start(self) -> str:
        """Start bounded room scan. Maps to Nero's scan.start."""
        # 调用 DimOS WavefrontFrontierExplorer
        ...

    @skill
    def mission_stop(self) -> str:
        """Stop all missions. Maps to Nero's mission.stop."""
        ...
```

### 3.3 安全层

DimOS 已有的安全机制：
- 障碍物检测 → CostMapper 自动避障
- 丢失目标 → PersonTracker 返回 confidence=0 → stop
- 心跳 → GO2Connection 连接状态

需要额外实现的：
- [ ] 命令 TTL 到期自动拒绝
- [ ] 1.5s 心跳超时紧急停止
- [ ] 过期帧检测（frame timestamp > 500ms → stop）

---

## 阶段 4：房间扫描（对应 Nero 03:30–04:15）

### 4.1 使用 DimOS 内置探索

DimOS 的 `WavefrontFrontierExplorer` 已实现自主探索未映射区域。我们只需：

1. 添加边界约束（限制扫描范围）
2. 返回扫描路径和 receipts
3. 在 WebSocket events 中包含 scan 状态

### 4.2 扫描状态映射

```python
scan_state = {
    "active": explorer.is_running,
    "path": explorer.visited_waypoints,
    "map_source": "voxel" if slam.has_map else "coverage",
}
```

---

## 阶段 5：集成测试（对应 Nero 04:15–05:00）

### 5.1 测试清单

- [ ] Nero 从 Windows 能访问 `GET /v1/health`
- [ ] `GET /v1/frame.jpg` 显示真实 Go2 画面
- [ ] `POST /v1/stop` 返回真实 receipt
- [ ] `WS /v1/events` 推送真实机器人状态
- [ ] 命令 `follow.start` → 机器人开始跟随
- [ ] 命令 `mission.stop` → 机器人停止
- [ ] 断连重连 → 心跳恢复
- [ ] 障碍物出现 → 自动停止
- [ ] 丢失目标 → 自动停止
- [ ] 紧急停止 → 始终可用

### 5.2 环境变量配置

```env
# Ceaser WSL 端
ROBOT_IP=192.168.x.x
UNITREE_AES_128_KEY=xxx
DOLLY_AUTH_TOKEN=shared_local_token
ROBOT_ID=go2_62507

# Nero Dolly 端
ROBOT_ENABLED=true
ROBOT_BASE_URL=http://<CEASER_LAN_IP>:8780
ROBOT_WS_URL=ws://<CEASER_LAN_IP>:8780/v1/events
ROBOT_AUTH_TOKEN=shared_local_token
ROBOT_ID=go2_62507
```

---

## 阶段 6：物理排练（对应 Nero 05:00–06:00）

- [ ] 完整流程走通：开机 → 连接 → 扫描 → 跟随 → 停止
- [ ] OBS 中 CAM_ROBOT 画面正常
- [ ] 语音/聊天指令正常路由
- [ ] 不再添加新功能

---

## 每 30 分钟同步模板

```
DONE:
WORKING NOW:
ENDPOINT/IP:
REAL EVIDENCE:
BLOCKER:
NEED FROM NERO/CEASER:
```

---

## 关键文件结构

```
d:\github projects\Singularity_Go_2\
├── Roadmap/
│   ├── 00_DimOS_vs_Nero_Workflow_Analysis.md  ← 差异分析
│   ├── 01_Implementation_Roadmap.md            ← 本文件
│   └── 02_Api_Contract_Reference.md            ← API 契约参考
├── robot-service/                              ← 自定义模块代码（待创建）
│   ├── dolly_gateway/
│   │   ├── __init__.py
│   │   ├── module.py          # DollyGatewayModule
│   │   └── api.py             # FastAPI 路由
│   ├── singularity_skills/
│   │   ├── __init__.py
│   │   └── skills.py          # 自定义 @skill
│   └── blueprints/
│       ├── __init__.py
│       └── singularity_go2.py # singularity-go2 blueprint
├── tests/
│   └── test_dolly_gateway.py
└── README.md
```

---

## 下一步行动

1. **Ceaser**：在 WSL Ubuntu 中安装 DimOS，跑通 `dimos --replay run unitree-go2`
2. **Ceaser**：完成硬件门禁测试，输出报告给 Nero
3. **Nero**：确认 DimOS 方案是否可接受，还是坚持原始 SDK2 方案
4. **共同**：确认后进入阶段 1 开发