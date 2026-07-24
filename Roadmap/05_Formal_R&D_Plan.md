# Singularity_Go_2 正式研发计划

> 版本: 1.0 | 日期: 2026-07-24 | 作者: Ceaser
> 
> 基于 DimOS 官方文档、Nero 工作流契约、以及四份 Roadmap 文档的综合产物

---

## 1. 项目概述

### 1.1 目标

基于 DimensionalOS (DimOS) 构建 Unitree Go2 机器人的智能控制系统，通过 HTTP/WebSocket API 桥接至 Nero 的 Dolly 系统，实现：

- 机器人视频流实时传输至 OBS
- 房间自主扫描与建图
- 人体检测与智能跟随
- 语音/聊天指令远程控制
- 多层安全保护机制

### 1.2 核心指标

| 指标 | 目标值 | 验证方式 |
|------|--------|---------|
| 视频帧率 | ≥ 10 fps | `benchmark_frames.py` |
| 视频分辨率 | ≥ 640x480 | PIL 读取验证 |
| 端到端延迟 | < 500ms | frame timestamp 差值 |
| 人物跟踪成功率 | ≥ 80% | 室内多人场景测试 |
| 安全层测试覆盖率 | 100% | pytest --cov |
| API 可用性 | 99% (心跳间隔 200ms) | WebSocket 监控 |

### 1.3 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 机器人 OS | DimOS (DimensionalOS) | 开源机器人操作系统 |
| 机器人通信 | WebRTC | 通过 GO2Connection 模块 |
| 感知 | YOLO11-pose + PersonTracker | 人体检测与跟踪 |
| 导航 | VoxelGridMapper + CostMapper + A* | SLAM 建图与路径规划 |
| API 桥接 | FastAPI + uvicorn | 嵌入 DimOS Module 生命周期 |
| 实时通信 | WebSocket + MJPEG | 状态推送 + 视频流 |
| 包管理 | uv | Python 依赖管理 |

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────┐
│  Nero Dolly (Windows)                                   │
│  RobotGatewayClient → HTTP/WS → Ceaser robot-service    │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Ceaser Linux (robot-service)                           │
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

## 3. 阶段划分与里程碑

### Phase 0: 环境准备 (Day 0, 前 2 小时)

**目标：** DimOS 在 Linux 上可运行，replay 模式验证通过

| 任务 | 负责人 | 预计时间 | 验收标准 |
|------|--------|---------|---------|
| 0.1 系统依赖安装 | Ceaser | 15 min | curl, g++, git-lfs, libturbojpeg 等安装成功 |
| 0.2 uv 安装 | Ceaser | 5 min | `uv --version` 正常输出 |
| 0.3 DimOS 克隆 + 安装 | Ceaser | 30 min | `dimos --help` 正常输出 |
| 0.4 Replay 验证 | Ceaser | 15 min | Rerun 窗口显示相机画面和 SLAM 地图 |
| 0.5 FastAPI 嵌入验证 | Ceaser | 30 min | `GET /test` 返回 `{"status": "ok"}` |
| 0.6 硬件门禁测试 | Ceaser | 30 min | 输出 Nero 要求的门禁报告 |

**里程碑 M0：** `dimos --replay run unitree-go2` 成功运行，截图发送给 Nero

**门禁报告输出格式：**
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

### Phase 1: 安全层 + API 骨架 (Day 0, 后续 4 小时)

**目标：** 安全层 100% 测试覆盖，所有 API 端点可响应

| 任务 | 负责人 | 预计时间 | 验收标准 |
|------|--------|---------|---------|
| 1.1 TTL 过期拒绝 | Ceaser | 30 min | 过期命令返回 `reason: "ttl_expired"` |
| 1.2 心跳看门狗 | Ceaser | 30 min | 1.5s 无心跳触发 estop |
| 1.3 过期帧检测 | Ceaser | 20 min | 帧 > 500ms 返回 503 |
| 1.4 紧急停止优先级 | Ceaser | 20 min | estop 后所有命令被拒绝 |
| 1.5 安全层单元测试 | Ceaser | 40 min | 5 个测试全部通过，覆盖率 100% |
| 1.6 GET /v1/health | Ceaser | 15 min | 返回 `{"status": "ok", ...}` |
| 1.7 GET /v1/state | Ceaser | 30 min | 映射 DimOS 状态为 Nero 契约格式 |
| 1.8 POST /v1/stop | Ceaser | 20 min | 返回 `{"executed": true, ...}` |
| 1.9 WS /v1/events | Ceaser | 45 min | 每 200ms 推送一则状态消息 |
| 1.10 GET /v1/frame.jpg | Ceaser | 30 min | 返回有效 JPEG 图像 |
| 1.11 GET /v1/video.mjpeg | Ceaser | 30 min | 持续推送 MJPEG 流 |
| 1.12 POST /v1/commands | Ceaser | 45 min | 命令队列 + 去重 + 路由 |

**里程碑 M1：** 所有 7 个 API 端点返回正确响应，安全层 5 个测试全部通过

---

### Phase 2: 硬件集成 (Day 1, 前 2 小时)

**目标：** 连接真实 Go2，验证完整视频链路和控制链路

| 任务 | 负责人 | 预计时间 | 验收标准 |
|------|--------|---------|---------|
| 2.1 双网卡配置 | Ceaser | 20 min | eth0 → 团队 LAN, eth1 → 机器人 |
| 2.2 连接真实 Go2 | Ceaser | 15 min | `dimos run unitree-go2` 成功启动 |
| 2.3 视频链路验证 | Ceaser | 30 min | Nero 端可看到真实 Go2 画面 |
| 2.4 控制链路验证 | Ceaser | 15 min | `POST /v1/stop` 机器人实际停止 |
| 2.5 状态推送验证 | Ceaser | 20 min | WebSocket 推送真实机器人状态 |
| 2.6 网络稳定性测试 | Ceaser | 20 min | 持续运行 5 分钟不掉线 |

**里程碑 M2：** Nero 从 Windows 端可访问所有 API 端点，看到真实 Go2 画面

---

### Phase 3: 智能跟随 + 房间扫描 (Day 1, 后续 3 小时)

**目标：** 人物跟随和房间扫描功能可正常工作

| 任务 | 负责人 | 预计时间 | 验收标准 |
|------|--------|---------|---------|
| 3.1 PersonTracker 验证 | Ceaser | 30 min | 单人场景跟踪成功率 ≥ 80% |
| 3.2 follow.start 实现 | Ceaser | 30 min | 机器人开始跟随主要目标 |
| 3.3 follow.hold 实现 | Ceaser | 15 min | 机器人保持当前位置 |
| 3.4 scan.start 实现 | Ceaser | 30 min | 机器人开始自主探索 |
| 3.5 mission.stop 实现 | Ceaser | 15 min | 停止所有任务 |
| 3.6 多人场景测试 | Ceaser | 30 min | 3-5 人场景中正确锁定主要目标 |
| 3.7 障碍物停止测试 | Ceaser | 15 min | 障碍物出现时自动停止 |
| 3.8 丢目标停止测试 | Ceaser | 15 min | 目标消失 5 秒内自动停止 |

**里程碑 M3：** 完整流程走通：开机 → 连接 → 扫描 → 跟随 → 停止

---

### Phase 4: 集成测试 + 排练 (Day 1, 最后 2 小时)

**目标：** 端到端测试通过，准备排练

| 任务 | 负责人 | 预计时间 | 验收标准 |
|------|--------|---------|---------|
| 4.1 端到端测试 (10 项) | Ceaser | 45 min | 全部通过 |
| 4.2 OBS 集成验证 | Ceaser + Nero | 20 min | CAM_ROBOT 画面正常 |
| 4.3 语音/聊天指令测试 | Ceaser + Nero | 20 min | 指令正常路由 |
| 4.4 断连重连测试 | Ceaser | 15 min | 自动重连，心跳恢复 |
| 4.5 紧急停止压力测试 | Ceaser | 10 min | 连续 stop 不失效 |
| 4.6 完整排练 | Ceaser + Nero | 30 min | 无新增功能，仅修复 bug |

**里程碑 M4：** 所有 10 项端到端测试通过，排练完成

---

## 4. API 契约

### 4.1 端点清单

| 端点 | 方法 | 说明 | 优先级 |
|------|------|------|--------|
| `/v1/health` | GET | 健康检查 | P0 |
| `/v1/state` | GET | 机器人状态 | P0 |
| `/v1/frame.jpg` | GET | 单帧 JPEG | P0 |
| `/v1/video.mjpeg` | GET | MJPEG 流 | P1 |
| `/v1/commands` | POST | 高层指令 | P1 |
| `/v1/stop` | POST | 紧急停止 | P0 |
| `/v1/events` | WS | 实时状态推送 | P1 |

### 4.2 允许的命令

| command | 说明 | DimOS 映射 |
|---------|------|-----------|
| `scan.start` | 开始扫描 | `WavefrontFrontierExplorer.start()` |
| `follow.start` | 开始跟随 | `PersonFollowSkillContainer.follow_person()` |
| `follow.hold` | 保持位置 | `NavigationSkillContainer.stop_navigation()` |
| `mission.stop` | 停止任务 | 停止所有运动 + 探索 |

### 4.3 安全规则

- TTL 过期 → `accepted: false, reason: "ttl_expired"`
- 互斥命令冲突 → `accepted: false, reason: "busy"`
- 紧急停止激活 → `accepted: false, reason: "estop_active"`
- 命令队列满 → `accepted: false, reason: "queue_full"`
- 过期帧 → 503 + `error: "stale_frame"`

### 4.4 数据真实性原则

**不发送假数据。** 用 `null` 或 `"unavailable"` 替代不可用的值。不伪造 battery、pose、distance、map、coverage。

---

## 5. 角色与职责

| 角色 | 负责人 | 职责 |
|------|--------|------|
| Ceaser | 机器人端 | DimOS 安装、DollyGatewayModule 开发、硬件连接、API 实现 |
| Nero | Dolly 端 | RobotGatewayClient 实现、OBS 集成、语音/聊天指令路由、UI 开发 |

---

## 6. 风险管理

### 6.1 风险评估矩阵

| 风险 | 概率 | 影响 | 等级 | 缓解措施 |
|------|------|------|------|---------|
| Go2 是 Air 且无 SDK 权限 | 中 | 致命 | 🔴 | 联系 Dimensional 工作人员要 EDU/X 或临时权限 |
| WebRTC 连接不稳定 | 高 | 致命 | 🔴 | 回退 SDK2 方案（需额外 2-3 小时） |
| PersonTracker 准确率低 | 中 | 高 | 🟡 | 降级为 LiDAR 跟踪或纯手动控制 |
| 双网卡配置失败 | 中 | 高 | 🟡 | 单网卡 + 端口转发 |
| 相机不可用 | 低 | 高 | 🟡 | 外部摄像头 + demo-camera 蓝图 |
| FastAPI 嵌入 DimOS 冲突 | 中 | 高 | 🟡 | 提前验证线程模型，必要时独立进程 |
| Nero 端未就绪 | 中 | 中 | 🟡 | 提前同步进度，Ceaser 侧独立可测试 |

### 6.2 降级方案

| 功能 | 完整方案 | 降级方案 A | 降级方案 B |
|------|---------|-----------|-----------|
| 人物跟随 | PersonTracker + 自动跟随 | LiDAR 跟踪最近移动物体 | 纯手动控制 |
| 视频流 | MJPEG 流 | 单帧轮询 | 外部摄像头 |
| 房间扫描 | WavefrontFrontierExplorer | 手动路径点 | 跳过 |
| 通信 | DimOS WebRTC | SDK2 + CycloneDDS | 手动遥控 |

### 6.3 最可能的崩溃模式

**DimOS 的 GO2Connection WebRTC 连接在真实硬件上不稳定，导致 color_image stream 频繁断连，DollyGatewayModule 无法提供持续的视频流，Nero 侧 OBS 黑屏，整个 demo 卡在视频链路。**

---

## 7. 时间线

```
Day 0 (前 6 小时)
├── [0:00-0:30]  Phase 0.1-0.4: 环境安装 + Replay 验证
├── [0:30-1:00]  Phase 0.5: FastAPI 嵌入验证
├── [1:00-1:30]  Phase 0.6: 硬件门禁测试 ← 关键决策点
├── [1:30-4:00]  Phase 1: 安全层 + API 骨架
│   ├── [1:30-3:10]  安全层实现 (100% 测试覆盖)
│   └── [3:10-4:00]  API 端点实现 (7 个端点)
└── [4:00-4:30]  同步 Nero → 30 分钟同步

Day 1 (后 6 小时)
├── [0:00-2:00]  Phase 2: 硬件集成
│   ├── [0:00-0:30]  双网卡 + 连接 Go2
│   ├── [0:30-1:30]  视频/控制链路验证
│   └── [1:30-2:00]  网络稳定性测试
├── [2:00-5:00]  Phase 3: 智能跟随 + 房间扫描
│   ├── [2:00-3:00]  PersonTracker + follow 功能
│   ├── [3:00-4:00]  scan + mission.stop
│   └── [4:00-5:00]  多人场景 + 障碍物测试
└── [5:00-6:00]  Phase 4: 集成测试 + 排练
    ├── [5:00-5:30]  端到端测试 (10 项)
    ├── [5:30-5:45]  OBS + 语音/聊天集成
    └── [5:45-6:00]  完整排练
```

---

## 8. 沟通协议

### 8.1 每 30 分钟同步

```
DONE:
WORKING NOW:
ENDPOINT/IP:
REAL EVIDENCE:
BLOCKER:
NEED FROM NERO/CEASER:
```

### 8.2 决策点

| 时间点 | 决策 | 决策者 |
|--------|------|--------|
| Day 0, 1:30 | 硬件门禁结果：继续 DimOS 方案还是回退 SDK2 | Ceaser + Nero |
| Day 0, 4:00 | API 骨架验证：Nero 能否连接所有端点 | Ceaser + Nero |
| Day 1, 2:00 | 硬件集成完成：视频/控制链路是否稳定 | Ceaser + Nero |
| Day 1, 5:00 | 功能完成：是否需要降级某些功能 | Ceaser + Nero |

---

## 9. 质量门禁

### 9.1 代码质量

- [ ] 安全层 100% 单元测试覆盖
- [ ] 所有 API 端点有集成测试
- [ ] 命令队列有去重和溢出测试
- [ ] 状态映射有 Schema 验证

### 9.2 功能质量

- [ ] 10 项端到端测试全部通过
- [ ] 视频帧率 ≥ 10 fps，分辨率 ≥ 640x480
- [ ] 人物跟踪成功率 ≥ 80%
- [ ] 紧急停止响应时间 < 100ms

### 9.3 安全质量

- [ ] TTL 过期命令 100% 被拒绝
- [ ] 1.5s 心跳超时触发 estop
- [ ] 过期帧返回 503 而非假数据
- [ ] estop 后所有命令被拒绝
- [ ] stop 端点不经过 LLM 回路

---

## 10. 交付物

| 交付物 | 格式 | 负责人 | 截止时间 |
|--------|------|--------|---------|
| DimOS 安装脚本 | `.sh` | Ceaser | Day 0, 0:30 |
| 硬件门禁报告 | Markdown | Ceaser | Day 0, 1:30 |
| DollyGatewayModule 源码 | Python | Ceaser | Day 0, 4:00 |
| SingularitySkillContainer 源码 | Python | Ceaser | Day 1, 5:00 |
| 安全层测试报告 | pytest 输出 | Ceaser | Day 0, 4:00 |
| 端到端测试报告 | Markdown | Ceaser | Day 1, 5:30 |
| 排练记录 | 视频 | Ceaser + Nero | Day 1, 6:00 |

---

## 11. 文件结构

```
d:\github projects\Singularity_Go_2\
├── Roadmap/
│   ├── 00_DimOS_vs_Nero_Workflow_Analysis.md   ← 差异分析
│   ├── 01_Implementation_Roadmap.md             ← 实现路线图
│   ├── 02_Api_Contract_Reference.md             ← API 契约参考
│   ├── 03_DollyGatewayModule_Execution_Plan.md  ← 执行计划
│   └── 05_Formal_R&D_Plan.md                    ← 本文件
├── robot-service/                               ← 自定义模块
│   ├── dolly_gateway/
│   │   ├── __init__.py
│   │   ├── module.py           # DollyGatewayModule
│   │   ├── api.py              # FastAPI 路由
│   │   ├── safety.py           # 安全层
│   │   ├── command_queue.py    # 命令队列
│   │   ├── state_mapper.py     # 状态映射
│   │   └── frame_provider.py   # 帧提供
│   ├── singularity_skills/
│   │   ├── __init__.py
│   │   └── skills.py           # 自定义 @skill
│   ├── blueprints/
│   │   ├── __init__.py
│   │   └── singularity_go2.py  # singularity-go2 blueprint
│   └── tests/
│       ├── test_safety.py
│       ├── test_command_queue.py
│       ├── test_state_mapper.py
│       ├── test_api.py
│       └── test_integration.py
├── scripts/
│   ├── verify_replay.sh
│   ├── test_endpoints.sh
│   └── benchmark_frames.py
├── dimos/                       ← DimOS 源码（.gitignore 排除）
├── .gitignore
├── install_dimos_xubuntu.sh
└── README.md
```

---

## 12. 下一步行动

1. **Ceaser**：在 Linux 设备上运行 `install_dimos_xubuntu.sh`，完成 DimOS 安装
2. **Ceaser**：运行 `dimos --replay run unitree-go2`，截图发送 Nero
3. **Ceaser**：完成硬件门禁测试，输出门禁报告
4. **Nero**：确认 DimOS 方案是否可接受
5. **共同**：确认后进入 Phase 1 开发