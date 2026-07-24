# DollyGatewayModule 最严苛执行计划

> 基于五个方法论的综合产物：Decomposer Protocol + Tension Mining + Agent Architecture Audit + Agent Harness Construction + Agent Browser
> 
> 执行者：Ceaser（WSL Ubuntu） | 审查者：Nero（Dolly 端）

---

# 第一部分：Decomposer Protocol —— 五步认知分解

---

## Step 1: Honesty Fuse（诚实引信）

### 我能真正参与什么

我可以参与：
- DimOS 源码阅读和模块理解（`dimos/perception/`, `dimos/robot/unitree/`, `dimos/agents/`）
- 基于 DimOS 现有 Module 模式编写自定义 `DollyGatewayModule`
- FastAPI + uvicorn 嵌入 DimOS 生命周期的技术方案
- HTTP/WebSocket API 契约的完整实现（Nero 已冻结）
- 状态映射逻辑（DimOS 内部状态 → Nero 契约 JSON）
- 安全层——TTL 过期、心跳超时、过期帧检测、紧急停止优先级

我不能参与：
- Go2 真实硬件的物理连接测试（需要 Ceaser 在 WSL 中实际操作）
- DimOS 内部的 WebRTC 连接调试（这是 DimOS 内置的，黑盒）
- 网络配置（双网卡绑定、防火墙规则、IP 分配）
- 真实人物检测/跟踪的调参（需要硬件 + 实际场景）

### 我盲在哪里

1. **我不知道 DimOS 的 `GO2Connection` 在生产环境下的稳定性**——WebRTC 连接在弱网、机器人重启、固件升级后的表现是未知的
2. **我不知道 `PersonTracker` 在真实室内场景的准确率**——DimOS 文档说它使用了 YOLO11-pose，但实际多人场景、遮挡、光照变化下的表现未经验证
3. **我不知道 FastAPI 嵌入 DimOS Module 的生命周期是否有竞争条件**——DimOS 的 Module 设计是基于 `start()/stop()` 生命周期，而 FastAPI 的 `uvicorn.run()` 是阻塞的。两者在同一进程的线程模型需要仔细设计
4. **我不知道 Nero 的 Dolly 是否已经准备好接入**——尽管契约冻结了，但 Nero 的 `RobotGatewayClient` 实现进度未知
5. **我不知道 `dimos --replay` 模式的 `color_image` stream 是否与真实硬件完全一致**——帧率、分辨率、编码格式可能有差异

### 这个项目最可能怎么死

**最可能的崩溃模式：DimOS 的 `GO2Connection` WebRTC 连接在真实硬件上不稳定，导致 `color_image` stream 频繁断连，DollyGatewayModule 无法提供持续的视频流，Nero 侧 OBS 黑屏，整个 demo 卡在视频链路。继而 Ceaser 和 Nero 被迫在 6 小时内回到原始 SDK2 方案，但时间不够。**

---

## Step 2: Uncertainty Mapping（不确定性映射）

| # | 元素 | 区域 | 理由 |
|---|------|------|------|
| 1 | DimOS 安装在 WSL Ubuntu 上可运行 | 🟢 Green | 明确的操作步骤，已知依赖 |
| 2 | `dimos --replay run unitree-go2` 能跑通 | 🟢 Green | 官方文档已验证，只需网络下载 75MB 数据 |
| 3 | `GO2Connection` WebRTC 连接真实 Go2 稳定 | 🟡 Yellow | 已知问题但未验证——取决于固件版本、网络质量 |
| 4 | FastAPI 可嵌入 DimOS Module 生命周期 | 🟡 Yellow | 技术上可行，但无先例——需要验证线程模型 |
| 5 | `color_image` stream 帧率/分辨率满足 OBS 需求 | 🟡 Yellow | DimOS 有流，但参数未知 |
| 6 | `PersonTracker` 在真实场景下准确率可接受 | 🔴 Red | 无数据，严重依赖场景 |
| 7 | DimOS `PersonFollowSkillContainer` 跟随稳定性 | 🔴 Red | 无数据，安全事故风险 |
| 8 | 双网卡配置（机器人网络 + 团队 LAN） | 🟡 Yellow | 已知操作但 WSL 网络桥接复杂 |
| 9 | Nero 的 `RobotGatewayClient` 已就绪 | 🟡 Yellow | 依赖 Nero 进度 |
| 10 | HTTP/WS API 契约实现正确性 | 🟢 Green | 契约明确，可测试 |
| 11 | 安全层（TTL/心跳/过期帧/estop）逻辑正确 | 🟢 Green | 可纯代码验证 |
| 12 | 6 小时时间线可行 | 🔴 Red | 太多未知，严重依赖硬件门禁结果 |

**区域分布：Green: 4 (33%), Yellow: 5 (42%), Red: 3 (25%). Yellow+Red = 67%. 通过 70% 规则。**

---

## Step 3: Hierarchical Decomposition（认知依赖树）

```
ROOT: GO2Connection WebRTC 连接真实 Go2 稳定 ← 🟡
  ├── color_image stream 帧率/分辨率 ← 🟡 (死则视频链路断)
  │   └── HTTP/WS API 契约实现 ← 🟢 (独立，可先基于 replay 实现)
  │       └── Nero RobotGatewayClient 接入 ← 🟡 (依赖 API 实现)
  ├── PersonTracker 准确率 ← 🔴 (死则 follow 功能死)
  │   └── PersonFollowSkillContainer 跟随稳定 ← 🔴 (死则 demo 核心功能死)
  └── 双网卡配置 ← 🟡 (死则 Nero 无法访问 :8780)

INDEPENDENT LEAVES:
  ├── DimOS 安装 + replay 验证 ← 🟢 (可立即执行)
  ├── FastAPI 嵌入 DimOS Module 生命周期 ← 🟡 (可先验证)
  └── 安全层逻辑 ← 🟢 (可独立开发测试)
```

**三个节点状态：**
- **Executable**：DimOS 安装、安全层、API 契约实现、replay 验证
- **Pending Validation**：WebRTC 连接、网络配置、PersonTracker 准确率
- **Blind Zone**：6 小时时间线可行性（依赖所有硬件门禁结果）

---

## Step 4: Error Budget Assignment（误差预算）

| 节点 | 置信度 | 生存条件 | 致命性 |
|------|--------|----------|--------|
| GO2 WebRTC 连接 | **Low** | Alive if: `dimos run unitree-go2-basic` 在真实硬件上持续运行 5 分钟不掉线，Rerun 中可见相机和 LiDAR。Dead if: 连接在 2 分钟内断开，或相机/LiDAR 任一不可用 | 杀死：整个 DimOS 方案。必须回退到 SDK2 |
| FastAPI 嵌入 Module | **Medium** | Alive if: 一个最简单的 FastAPI endpoint 在 DimOS Module 的 `start()` 中启动，`stop()` 中关闭，uvicorn 不与 DimOS 的事件循环冲突。Dead if: 启动后 DimOS 挂起或 uvicorn 阻塞 Module 生命周期 | 杀死：HTTP/WS API 桥接方案 |
| color_image 帧率 | **Medium** | Alive if: replay 模式下帧率 ≥ 10fps，分辨率 ≥ 640x480。Dead if: < 5fps 或 < 320x240 | 杀死：OBS 视频源 |
| PersonTracker 准确率 | **Zero** | Alive if: 在室内 3-5 人场景中，主要人物跟踪成功率 ≥ 80%，误跟率 < 10%。Dead if: 成功率 < 50% 或频繁跳到错误目标 | 杀死：follow 功能 |
| 安全层逻辑 | **High** | Alive if: 所有安全条件（TTL/心跳/过期帧/estop）在单元测试中 100% 覆盖，每个条件至少一个集成测试。Dead if: 任一安全条件无测试覆盖 | 无（独立模块） |

---

## Step 5: Anti-Shell Self-Check（反壳自检）

### Check 1: Survival Condition
✅ 所有节点都有 Concrete 生存条件。每个条件都有可量化的阈值。

### Check 2: Jargon
✅ 扫描全文，无 "leverage"、"robust"、"optimize"、"scalable" 等空洞词汇。

### Check 3: Uncertainty Ratio
✅ Yellow + Red = 67% > 30%. 通过。

### Check 4: Honesty
✅ **"I don't know" 声明**：我不知道 DimOS 的 `GO2Connection` 在生产环境下的稳定性。我不知道 `PersonTracker` 在真实室内场景的准确率。我不知道 FastAPI 嵌入 DimOS Module 是否有竞争条件。

### Check 5: Actionability
✅ **第一个可执行步骤**：在 WSL Ubuntu 中克隆 DimOS 仓库，执行 `uv venv --python 3.12 && uv pip install --pre -e '.[base,unitree]'`，然后运行 `dimos --replay run unitree-go2`，确认 Rerun 窗口出现。

---

# 第二部分：Tension Mining —— 系统张力分析

## Phase 1: Phenomenon Mining（现象挖掘）

从 3+ 个领域收集 5-10 个现象：

| # | 现象 | 领域 | 观察到的行为 | 与目标系统的关系 |
|---|------|------|-------------|----------------|
| 1 | **ROS 节点的网络断连** | 机器人学 | ROS 节点在弱网下频繁断连重连，导致 topic 消息丢失，系统状态不一致 | DimOS 的 WebRTC 连接可能面临同样问题 |
| 2 | **微服务网关的级联故障** | 分布式系统 | 一个服务超时 → 网关重试 → 下游服务雪崩。Hystrix 断路器模式解决 | DollyGateway 是微服务网关，Nero 的超时重试可能触发级联故障 |
| 3 | **自动驾驶的安全层冗余** | 自动驾驶 | 感知→规划→控制三层架构，每层都有独立的安全看门狗，任意一层触发则停止 | Go2 的 safety 层需要同样的多层冗余设计 |
| 4 | **视频流 CDN 的缓冲策略** | 流媒体 | 客户端缓冲 2-5 秒以平滑网络抖动，但缓冲过多导致延迟不可接受 | MJPEG 流需要平衡延迟和稳定性 |
| 5 | **浏览器 WebSocket 的重连风暴** | Web 前端 | 客户端断连后立即重连，导致服务器端 SYN flood。Exponential backoff 解决 | Nero 的 WebSocket 客户端需要合理的重连策略 |
| 6 | **多智能体系统的命令冲突** | 多智能体 | 两个 agent 同时发送互斥命令，系统状态不确定。需要分布式锁或命令队列 | 语音命令和 UI 命令可能同时到达 |
| 7 | **ROS2 DDS 的发现协议开销** | 机器人中间件 | DDS 的 Simple Discovery Protocol 在大规模节点下广播风暴，导致网络拥塞 | DimOS 的模块间 stream 连接可能产生类似开销 |

## Phase 2: Tension Mining（张力挖掘）

| # | 张力 | Force A | Force B | 为何不可消除 | 过度优化 A 的后果 | 过度优化 B 的后果 |
|---|------|---------|---------|-------------|-----------------|-----------------|
| **T1** | **Safety vs Autonomy** | 最大化机器人自主性 | 最大化安全约束 | 完全自主则不可控，完全安全则无动作 | 机器人做出危险动作，人身伤害 | 机器人永远不动作，demo 无意义 |
| **T2** | **Freshness vs Stability** | 始终推送最新状态 | 仅推送已验证的稳定状态 | 最新状态可能错误（传感器噪声），稳定状态可能过期 | 频繁抖动的状态变化导致 Nero UI 闪烁、Dolly 决策错误 | 延迟的状态导致 Dolly 基于过期信息决策 |
| **T3** | **Throughput vs Latency** | 最大化视频帧率/分辨率 | 最小化端到端延迟 | 高帧率消耗带宽增加延迟，低延迟需要降低帧率 | 视频流畅但 OBS 延迟 2-3 秒，操作不同步 | 延迟低但画面卡顿，无法判断场景 |
| **T4** | **Completeness vs Honesty** | 填充所有字段让 API 看起来完整 | 只发送真实数据，未知用 null | 假数据导致 Nero 误判，空数据导致 Nero 缺失信息 | 伪造 battery/pose 值 → Dolly 显示虚假信息 → 操作员误判 | API 返回大量 null → Nero 的 UI 空白 → 体验差 |
| **T5** | **Reactivity vs Idempotency** | 每个命令立即执行 | 相同命令不重复执行 | 立即执行可能重复，幂等可能延迟 | 连续发送 stop → 机器人反复停止-恢复-停止 | 连续发送 stop → 第二个被忽略 → 操作员以为失效 |
| **T6** | **Local Control vs Remote Proxy** | 机器人本地决策（低延迟） | 远程 LLM 决策（高智能） | 本地快但笨，远程聪明但慢 | 本地跟随逻辑简单，复杂场景处理不了 | OpenRouter 延迟 2-5 秒，跟随目标已走远 |
| **T7** | **Connection Persistence vs Resource Reclamation** | 保持 WebSocket 长连接 | 超时释放资源 | 长连接占资源，短连接重建开销大 | 僵尸连接占满文件描述符，服务崩溃 | 频繁重连消耗 CPU，状态丢失 |

**最根本的张力：T1 Safety vs Autonomy**——如果这个张力处理不好，其他所有张力都无关紧要。

## Phase 3: Invariant Mining（不变量挖掘）

| # | 不变量 | 支撑现象 | 边界条件 |
|---|--------|---------|---------|
| **I1** | **任何远程控制链路的可靠性 ≤ 最弱链路** | 现象 1, 2, 5 | 仅当链路是串行的；如果有多条冗余链路，可靠性可超越单链路 |
| **I2** | **安全约束必须独立于智能控制回路** | 现象 3, T1 | 当安全约束本身需要智能判断时（如"是否真的危险"），独立回路可能过度保守 |
| **I3** | **状态推送的延迟 × 频率 = 恒定的信息过时量** | T2, T3, 现象 4 | 仅在稳态下成立；突发状态变化时过时量可能远超此乘积 |
| **I4** | **任何 API 的假数据最终会被发现，且代价远大于 null** | T4, Nero 强调的"不要假数据" | 当 null 会导致系统崩溃而假数据不会时，假数据短期内更优（但长期仍有害） |

## Phase 4: Mechanism Mining（机制挖掘）

| # | 机制 | 功能 | 解决哪些张力 | 失败模式 |
|---|------|------|-------------|---------|
| **M1** | **断路器 (Circuit Breaker)** | 检测下游故障，快速失败而非持续重试 | T1, I2 | 断路器误触发（假阳性）→ 所有请求被拒绝 |
| **M2** | **心跳看门狗 (Heartbeat Watchdog)** | 独立进程监控主进程心跳，超时强制停止 | T1, I2, I3 | 看门狗自身崩溃 → 无保护。需双看门狗互相监控 |
| **M3** | **指数退避重连 (Exponential Backoff)** | 断连后延迟递增重试，避免重连风暴 | T7, 现象 5 | 退避上限过高 → 重连太慢。退避上限过低 → 仍可能风暴 |
| **M4** | **命令队列 + 去重 (Command Queue + Dedup)** | 所有命令入队，基于 request_id 去重，按序执行 | T5, T6 | 队列满 → 拒绝新命令。需有界队列 + 溢出策略 |
| **M5** | **自适应帧率 (Adaptive Frame Rate)** | 根据网络带宽动态调整视频帧率和质量 | T3, T2 | 自适应算法震荡 → 帧率忽高忽低 → 比固定帧率更差 |
| **M6** | **优雅降级 (Graceful Degradation)** | 当 PersonTracker 失联时，降级为纯手动控制而非崩溃 | T4, I4 | 降级后操作员不知道已降级 → 以为系统正常工作 |

## Phase 5-7: System Synthesis → Algorithm Synthesis → Destruction

（见下方第三部分的架构审计和第四部分的实施计划）

---

# 第三部分：Agent Architecture Audit —— 12 层栈审计

## 审计范围

| 维度 | 值 |
|------|-----|
| 目标系统 | DollyGatewayModule（DimOS Module → FastAPI → Nero Dolly） |
| 入口点 | HTTP `GET/POST /v1/*` + WebSocket `/v1/events` |
| 模型栈 | DimOS McpClient (gpt-4o, 可选) + OpenRouter (Nero 侧) |
| 待审计层 | 全部 12 层 |

## 诊断问题快速扫描

| # | 问题 | 答案 | 诊断 |
|---|------|------|------|
| 1 | 模型能否跳过必需工具仍然回答？ | N/A（DollyGateway 不直接调用 LLM） | ⚠️ 但 Nero 的 OpenRouter 可以，Nero 必须在代码层 enforce 命令格式 |
| 2 | 旧对话内容会出现在新回合？ | 不适用 | DimOS 的 SpatialMemory 可能有跨 session 污染 |
| 3 | 同一信息在 system prompt + memory + history 中重复？ | ⚠️ 可能 | DimOS system prompt + SpatialMemory + Nero 的 Dolly 上下文可能重复 |
| 4 | 平台在交付前运行第二次 LLM？ | ⚠️ 可能 | Nero 的 Dolly 可能有 hidden repair loop |
| 5 | 内部生成和用户交付的输出不同？ | ⚠️ 可能 | DimOS 状态 → DollyGateway 序列化 → Dolly 渲染，三层可能突变 |
| 6 | "必须使用 tool X" 只在 prompt 文本中？ | ⚠️ 是 | Nero 的契约在 markdown 中，不在代码中强制 |
| 7 | Agent 的独白能变成持久记忆？ | ⚠️ 可能 | DimOS Memory2 的 observation store 可能记录 agent 推理 |

## 严重性排序的发现

### [CRITICAL] F1: WebRTC 连接断连时 DollyGateway 无优雅降级
- **机制**：`GO2Connection` 断连 → `color_image` stream 停止 → `/v1/frame.jpg` 返回陈旧帧 → Nero UI 显示冻结画面 → 操作员以为机器人还在工作
- **源层**：Layer 10 (Platform Rendering) + Layer 12 (Persistence)
- **根因**：DollyGateway 没有区分 "无新帧" 和 "连接断开"，两者都返回最后缓存帧
- **置信度**：0.85
- **修复**：帧 timestamp 超过 500ms → 返回 503 + `{"error": "stale_frame", "last_frame_age_ms": 1200}`。WebSocket 推送 `{"type": "robot.disconnected"}`

### [CRITICAL] F2: 安全停止依赖 LLM 回路
- **机制**：如果 Nero 的 OpenRouter 发出 stop 命令有延迟（2-5 秒），机器人可能已经撞到障碍物
- **源层**：Layer 6 (Tool Selection) + Layer 11 (Hidden Repair Loops)
- **根因**：Nero 的契约中 POST /v1/stop 是独立端点，但 Nero 的 UI/语音可能通过 OpenRouter 路由 stop 命令
- **置信度**：0.75
- **修复**：强制 Nero 的 stop 按钮直接调用 POST /v1/stop，不经过 OpenRouter。在 DollyGateway 侧，stop 端点不接受 TTL 限制，立即执行

### [HIGH] F3: 状态推送的序列化-反序列化损失
- **机制**：DimOS 内部状态（Python 对象）→ DollyGateway JSON 序列化 → HTTP/WS 传输 → Nero Dolly JSON 反序列化 → UI 渲染。5 层转换，每层可能丢失精度或引入误差
- **源层**：Layer 9 (Answer Shaping) + Layer 10 (Platform Rendering)
- **根因**：没有定义从 DimOS 内部状态到 Nero 契约 JSON 的精确映射规范
- **置信度**：0.90
- **修复**：在 `Roadmap/02_Api_Contract_Reference.md` 中已有映射，但需要用 JSON Schema 验证每个端点

### [HIGH] F4: 命令队列无溢出保护
- **机制**：如果 Nero 连续发送大量命令（如 UI 按钮快速点击），命令队列可能溢出或排队延迟
- **源层**：Layer 7 (Tool Execution)
- **根因**：Nero 契约中没有定义命令速率限制
- **置信度**：0.70
- **修复**：DollyGateway 实现命令队列 + 去重 + 速率限制（最多 5 个排队命令，TTL 过期自动丢弃）

### [MEDIUM] F5: WebSocket 重连时的状态同步
- **机制**：Nero 的 WebSocket 断连重连后，DollyGateway 不知道 Nero 的状态，必须全量推送，但 Nero 可能已经错过了中间的状态变化
- **源层**：Layer 2 (Session History) + Layer 12 (Persistence)
- **根因**：WebSocket 协议本身无状态恢复机制
- **置信度**：0.80
- **修复**：WebSocket 连接时发送全量 state snapshot，后续仅推送增量变化

---

# 第四部分：Agent Harness Construction —— 动作空间设计

## 动作空间设计

### 工具粒度矩阵

| 操作类型 | 粒度 | 工具名 | 输入 Schema | 输出 Shape |
|---------|------|--------|------------|-----------|
| 高风险（停止） | **Micro** | `stop()` | 无（立即执行） | `{"executed": bool, "ts": ISO}` |
| 高风险（跟随） | **Micro** | `follow_start(target_kind)` | `{"target_kind": "primary_person"}` | `{"accepted": bool, "reason": str}` |
| 中风险（扫描） | **Medium** | `scan_start(bounds?)` | `{"bounds": BBox \| null}` | `{"accepted": bool, "scan_id": uuid}` |
| 中风险（保持） | **Micro** | `follow_hold()` | 无 | `{"executed": bool}` |
| 低风险（查询） | **Medium** | `get_state()` | 无 | `RobotState` (完整 JSON) |
| 低风险（查询） | **Medium** | `get_frame()` | 无 | JPEG 二进制 |

### 关键设计决策：不暴露 Macro 工具

**理由**：DimOS 的 McpClient LLM 不应该有 "go explore the room" 这种模糊命令。所有命令必须通过 Nero 的 Dolly 翻译为精确的高层指令（`follow.start`, `scan.start`, `mission.stop`）。这就是 Nero 契约中 "OpenRouter must never send raw vx, vy, vyaw" 的原因。

## 观察设计

每个工具响应必须包含：

```python
@dataclass
class ToolResponse:
    status: Literal["success", "warning", "error"]
    summary: str          # 一行结果
    next_actions: list[str]  # 可操作的后续步骤
    artifacts: dict       # 文件路径/ID
```

**反模式避免**：
- ❌ 返回 `{"status": "ok"}` 而没有 `summary`
- ❌ 返回错误但没有 `next_actions`（让调用者猜测下一步）
- ❌ 返回大量内部状态而没有 `artifacts` 引用

## 错误恢复契约

每个错误路径必须包含：

```python
class ErrorResponse:
    error_code: str       # 机器可读
    root_cause_hint: str  # 人类可读
    safe_retry: bool      # 是否可以安全重试
    retry_after_ms: int | None  # 建议等待时间
    stop_condition: str   # 何时应停止重试
```

示例：
```json
{
  "error_code": "STALE_FRAME",
  "root_cause_hint": "Camera stream has not produced a new frame in 500ms. Robot may be disconnected or camera may be obstructed.",
  "safe_retry": true,
  "retry_after_ms": 1000,
  "stop_condition": "Stop retrying if error persists for more than 10 seconds. Check robot connection."
}
```

## 上下文预算

| 层级 | 内容 | 预算 |
|------|------|------|
| System Prompt (DimOS McpClient) | 仅包含机器人身份 + 可用 skill 列表 | < 500 tokens |
| 工具定义 | 每个 @skill 的 docstring 即为 schema | < 200 tokens/工具 |
| 状态推送 | 仅推送变化的字段，非全量 | < 1KB/推送 |
| 视频帧 | 不推送给 LLM，仅推送给 OBS | 0 tokens |

---

# 第五部分：实施计划 —— 分层执行

## 前置阶段：环境验证（Day 0, 30 分钟）

### 任务 0.1：DimOS 安装 + Replay 验证
**生存条件：** `dimos --replay run unitree-go2` 在 Rerun 窗口中显示相机画面和 SLAM 地图

```bash
# 在 WSL Ubuntu 中执行
git clone https://github.com/dimensionalOS/dimos.git
cd dimos
uv venv --python 3.12
source .venv/bin/activate
uv pip install --pre -e '.[base,unitree]'
dimos --replay run unitree-go2
```

**验证方式：** Agent Browser 截图 Rerun 窗口保存为证据

### 任务 0.2：FastAPI 嵌入验证
**生存条件：** 一个最简单的 DimOS Module 能在 `start()` 中启动 FastAPI，`stop()` 中关闭

```python
# 验证脚本：test_fastapi_embed.py
from dimos.core.module import Module
from fastapi import FastAPI
import uvicorn
import threading

class TestGatewayModule(Module):
    def _on_start(self):
        self.app = FastAPI()
        @self.app.get("/test")
        def test():
            return {"status": "ok"}
        self._server_thread = threading.Thread(
            target=uvicorn.run,
            args=(self.app,),
            kwargs={"host": "0.0.0.0", "port": 8780},
            daemon=True
        )
        self._server_thread.start()
    
    def _on_stop(self):
        # uvicorn 在 daemon 线程中，主进程退出时自动清理
        pass
```

---

## 第 1 层：安全层（Day 0, 1 小时）—— 最优先，独立于硬件

### 为什么安全层最先实现

**安全层不依赖任何硬件。** 它可以在没有 Go2 的情况下完全开发和测试。而且安全层是所有其他功能的基础——如果安全层有 bug，其他功能都不可信。

### 1.1 TTL 过期拒绝

```python
class CommandGuard:
    def validate(self, command: Command) -> bool:
        age_ms = (time_now() - command.created_at).total_seconds() * 1000
        if age_ms > command.ttl_ms:
            return False, "ttl_expired"
        return True, "ok"
```

### 1.2 心跳看门狗

```python
class HeartbeatWatchdog:
    HEARTBEAT_TIMEOUT_MS = 1500
    
    def __init__(self):
        self._last_heartbeat = time_now()
        self._watchdog_thread = threading.Thread(target=self._watch, daemon=True)
    
    def _watch(self):
        while True:
            if (time_now() - self._last_heartbeat).total_seconds() * 1000 > self.HEARTBEAT_TIMEOUT_MS:
                self._trigger_estop()
            time.sleep(0.1)
```

### 1.3 过期帧检测

```python
class FrameGuard:
    MAX_FRAME_AGE_MS = 500
    
    def get_latest_frame(self) -> bytes | None:
        if self._latest_frame is None:
            return None
        age_ms = (time_now() - self._latest_frame_ts).total_seconds() * 1000
        if age_ms > self.MAX_FRAME_AGE_MS:
            return None  # 触发 STALE_FRAME 错误
        return self._latest_frame
```

### 1.4 紧急停止优先级

```python
# POST /v1/stop 的处理逻辑
# 1. 不接受 TTL 限制
# 2. 不经过命令队列（直接执行）
# 3. 绕过所有其他模块，直接调用机器人停止
# 4. 设置 estop 标志，阻止所有后续命令
```

### 安全层测试清单

| 测试 | 输入 | 预期输出 |
|------|------|---------|
| TTL 过期 | `ttl_ms=100`, 等待 200ms 后发送 | `accepted: false, reason: "ttl_expired"` |
| 心跳超时 | 模拟 1.5s 无心跳 | estop 触发，所有命令被拒绝 |
| 过期帧 | 帧超过 500ms 未更新 | 503 + `error: "stale_frame"` |
| 紧急停止优先级 | 发送 stop + 同时发送 follow.start | stop 先执行，follow.start 被拒绝 |
| estop 后拒绝命令 | estop=true 时发送 follow.start | `accepted: false, reason: "estop_active"` |

---

## 第 2 层：API 骨架（Day 0, 1.5 小时）—— 基于 replay 模式

### 2.1 实现顺序

1. `GET /v1/health` — 最简单的端点，验证 FastAPI 嵌入成功
2. `GET /v1/state` — 聚合 DimOS 内部状态，注意映射为 Nero 契约格式
3. `POST /v1/stop` — 紧急停止，最高优先级
4. `WS /v1/events` — 实时状态推送，需验证 Nero 能连接
5. `GET /v1/frame.jpg` — 从 `color_image` stream 取帧
6. `GET /v1/video.mjpeg` — 持续推流
7. `POST /v1/commands` — 命令队列 + 去重 + 路由

### 2.2 状态映射规范

```python
# dimos_state → nero_contract
STATE_MAPPING = {
    # Mode 映射
    "idle": "idle",
    "navigating": "following",
    "following": "following",
    "exploring": "scanning",
    
    # Safety 映射
    "connected": "heartbeat_ok",
    "obstacle_detected": "obstacle",
    "emergency_stop": "estop",
}
```

### 2.3 测试策略

使用 Agent Browser 测试每个端点：

```bash
# 在 Windows 端（Nero 视角）
agent-browser open http://<WSL_IP>:8780/v1/health
# 预期：{"status": "ok", "robot_connected": false, ...}

# 测试 WebSocket
wscat -c ws://<WSL_IP>:8780/v1/events
# 预期：每 200ms 收到一条状态推送

# 测试 stop
curl -X POST http://<WSL_IP>:8780/v1/stop
# 预期：{"accepted": true, "executed": true, "robot_mode": "idle", ...}
```

---

## 第 3 层：视频链路（Day 0, 1 小时）

### 3.1 验证 replay 模式下的帧流

```bash
# 运行 replay 模式
dimos --replay run unitree-go2

# 在另一个终端验证帧
curl http://localhost:8780/v1/frame.jpg -o test_frame.jpg
# 预期：test_frame.jpg 是有效的 JPEG 文件

# 验证 MJPEG 流
curl http://localhost:8780/v1/video.mjpeg --output - | ffmpeg -i - -f null -
# 预期：持续输出帧信息
```

### 3.2 帧率/分辨率验证

```python
# 验证脚本
import time
import requests
from PIL import Image
from io import BytesIO

frame_times = []
for _ in range(30):
    t0 = time.time()
    resp = requests.get("http://localhost:8780/v1/frame.jpg")
    img = Image.open(BytesIO(resp.content))
    frame_times.append(time.time() - t0)
    print(f"Frame: {img.size}, FPS: {1/(time.time()-t0):.1f}")

print(f"Avg FPS: {len(frame_times)/sum(frame_times):.1f}")
print(f"Resolution: {img.size}")
```

---

## 第 4 层：命令路由（Day 1, 1.5 小时）

### 4.1 命令队列实现

```python
from collections import OrderedDict
import asyncio

class CommandQueue:
    MAX_QUEUE_SIZE = 5
    
    def __init__(self):
        self._queue: OrderedDict[str, Command] = OrderedDict()
        self._lock = asyncio.Lock()
    
    async def enqueue(self, cmd: Command) -> Receipt:
        async with self._lock:
            # 去重：相同 request_id → 返回已有 receipt
            if cmd.request_id in self._queue:
                return self._receipts[cmd.request_id]
            
            # TTL 检查
            if cmd.is_expired():
                return Receipt(accepted=False, reason="ttl_expired")
            
            # 溢出检查
            if len(self._queue) >= self.MAX_QUEUE_SIZE:
                return Receipt(accepted=False, reason="queue_full")
            
            self._queue[cmd.request_id] = cmd
            return Receipt(accepted=True, reason="queued")
```

### 4.2 命令路由表

```python
COMMAND_ROUTERS = {
    "scan.start": {
        "dimos_module": "WavefrontFrontierExplorer",
        "dimos_method": "start",
        "exclusive": True,  # 互斥：执行时不能执行其他命令
    },
    "follow.start": {
        "dimos_module": "PersonFollowSkillContainer",
        "dimos_method": "follow_person",
        "exclusive": True,
    },
    "follow.hold": {
        "dimos_module": "NavigationSkillContainer",
        "dimos_method": "stop_navigation",
        "exclusive": False,
    },
    "mission.stop": {
        "dimos_module": "SafetyController",
        "dimos_method": "estop",
        "exclusive": True,
        "bypass_queue": True,  # 不排队，立即执行
    },
}
```

---

## 第 5 层：硬件集成（Day 1, 2 小时）—— 依赖硬件门禁

### 5.1 硬件门禁测试（必须最先完成）

按照 Nero 的 30 分钟门禁清单执行，使用 `dimos go2tool` 和 `dimos run unitree-go2-basic`。

### 5.2 连接到真实 Go2

```bash
export ROBOT_IP=<Go2_IP>
export UNITREE_AES_128_KEY=<AES_KEY>  # 如果需要
dimos run unitree-go2
```

### 5.3 验证完整链路

```
Go2 相机 → GO2Connection WebRTC → color_image stream
  → DollyGatewayModule → /v1/frame.jpg
  → Nero Dolly (curl) → 验证收到真实画面
```

---

## 第 6 层：Person 跟随（Day 1, 1.5 小时）—— 依赖硬件

### 6.1 使用 DimOS 内置 PersonFollow

```bash
dimos run unitree-go2-agentic
# 然后通过 humancli 测试
humancli > follow the person in front of you
```

### 6.2 验证 PersonTracker 准确率

在真实场景中测试：
- 单人场景：跟踪成功率
- 多人场景（3-5 人）：主要目标锁定率
- 遮挡场景：重新锁定时间
- 光照变化：跟踪稳定性

---

## 第 7 层：集成测试 + 排练（Day 1, 2 小时）

### 7.1 端到端测试清单

| # | 测试 | 验证方式 | 通过标准 |
|---|------|---------|---------|
| 1 | Nero 能访问 `/v1/health` | Agent Browser 从 Windows 访问 | 200 OK |
| 2 | `/v1/frame.jpg` 显示真实画面 | 保存为 JPEG 对比 | 有效图像，非黑屏 |
| 3 | `/v1/stop` 返回真实 receipt | curl POST | `executed: true` |
| 4 | `WS /v1/events` 推送状态 | wscat 连接 | 每 200ms 一条消息 |
| 5 | `follow.start` 开始跟随 | 观察机器人行为 | 机器人朝向并跟随目标 |
| 6 | `mission.stop` 停止 | 观察机器人停止 | 立即停止所有运动 |
| 7 | 断连重连 | 拔掉网线 5 秒后恢复 | 自动重连，心跳恢复 |
| 8 | 障碍物停止 | 在人前放置障碍物 | 机器人自动停止 |
| 9 | 丢目标停止 | 目标走出视野 | 5 秒内停止 |
| 10 | 紧急停止优先级 | 在 follow 中按 stop | 立即停止，无视 TTL |

### 7.2 每 30 分钟同步

```
DONE:
WORKING NOW:
ENDPOINT/IP:
REAL EVIDENCE:
BLOCKER:
NEED FROM NERO/CEASER:
```

---

# 第六部分：失败预案

## 如果硬件门禁失败

| 失败场景 | 应对方案 |
|---------|---------|
| Go2 是 Air 且无 SDK 权限 | 立即联系 Dimensional 工作人员要 EDU/X 或临时 SDK 权限 |
| WebRTC 连接不稳定 | 回退到 SDK2 方案（Nero 原始方案），但需要额外 2-3 小时 |
| 相机不可用 | 使用外部摄像头通过 DimOS 的 `demo-camera` 蓝图替代 |
| 双网卡无法配置 | 使用单网卡 + 端口转发，或使用热点 |

## 如果 PersonTracker 不可用

| 降级方案 | 影响 |
|---------|------|
| 仅使用 LiDAR 跟踪（最近移动物体） | 精度降低，但 demo 仍可运行 |
| 纯手动控制（Nero 通过 UI 直接发送运动指令） | 失去自动跟随，但扫描和视频流仍可用 |
| 使用 DimOS 的 YOLO-E open-vocabulary 检测替代 | 可能更慢，但不需要训练数据 |

---

# 第七部分：文件结构

```
d:\github projects\Singularity_Go_2\
├── Roadmap/
│   ├── 00_DimOS_vs_Nero_Workflow_Analysis.md
│   ├── 01_Implementation_Roadmap.md
│   ├── 02_Api_Contract_Reference.md
│   └── 03_DollyGatewayModule_Execution_Plan.md  ← 本文件
├── robot-service/
│   ├── dolly_gateway/
│   │   ├── __init__.py
│   │   ├── module.py           # DollyGatewayModule (DimOS Module)
│   │   ├── api.py              # FastAPI 路由定义
│   │   ├── safety.py           # 安全层：TTL/心跳/过期帧/estop
│   │   ├── command_queue.py    # 命令队列 + 去重 + 路由
│   │   ├── state_mapper.py     # DimOS 状态 → Nero 契约映射
│   │   └── frame_provider.py   # 从 color_image stream 提供帧
│   ├── singularity_skills/
│   │   ├── __init__.py
│   │   └── skills.py           # 自定义 @skill 方法
│   ├── blueprints/
│   │   ├── __init__.py
│   │   └── singularity_go2.py  # singularity-go2 blueprint
│   └── tests/
│       ├── test_safety.py      # 安全层单元测试
│       ├── test_command_queue.py
│       ├── test_state_mapper.py
│       ├── test_api.py         # API 集成测试
│       └── test_integration.py # 端到端测试（需要 replay）
├── scripts/
│   ├── verify_replay.sh        # 验证 replay 模式
│   ├── test_endpoints.sh       # 用 curl 测试所有端点
│   └── benchmark_frames.py     # 帧率/分辨率基准测试
└── README.md
```

---

# 第八部分：第一个可执行步骤

**在 WSL Ubuntu 中，克隆 DimOS 仓库，执行 `uv venv --python 3.12 && uv pip install --pre -e '.[base,unitree]'`，然后运行 `dimos --replay run unitree-go2`，确认 Rerun 窗口出现相机画面和 SLAM 地图。截图作为证据发送给 Nero。**

这验证了最基础的前提：DimOS 在你的环境中可以运行。如果这一步失败，后续所有计划都需要重新评估。