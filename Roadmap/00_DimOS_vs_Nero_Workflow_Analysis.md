# DimOS vs Nero Workflow：关键差异分析

## 核心发现

Nero 的工作流文档（`ceaser workflow.txt`）要求在 Linux 上从零搭建一个 `robot-service`，使用 Unitree SDK2 + CycloneDDS 直接与 Go2 通信，暴露 HTTP/WebSocket API。

**DimOS 已经提供了这一切**，而且更完整。关键差异：

| 维度 | Nero 原始方案 | DimOS 方案 |
|------|-------------|-----------|
| 机器人通信 | SDK2 + CycloneDDS（手动） | WebRTC（内置，无需 jailbreak） |
| 导航/SLAM | 需自行实现 | 已内置：VoxelGridMapper + CostMapper + A* |
| 人体检测/跟随 | 需自行实现 | 已内置：YOLO11 + PersonTracker + PersonFollowSkill |
| Agent 控制 | 需 Nero 在 Dolly 端实现 | 已内置：McpServer + McpClient + @skill |
| 视频流 | 需手动实现 MJPEG | 已内置：RerunBridge + WebSocketVis |
| 内存/记忆 | 无 | SpatialMemory + Memory2（已内置） |
| 模拟/回放 | 无 | `--replay` / `--simulation` 开箱即用 |

## 建议策略

**不重写轮子，基于 DimOS 定制**。DimOS 的架构哲学是 "80% 组合现有模块 + 20% 自定义模块 + skills"。

我们的自定义只需要：
1. 一个 HTTP/WebSocket API 桥接模块（对接 Nero 的 Dolly 契约）
2. 自定义 @skill 方法（follow.start / scan.start / mission.stop）
3. 一个自定义 Blueprint 组合所有模块