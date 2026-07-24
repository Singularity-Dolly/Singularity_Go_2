# Dolly ↔ Robot-Service 兼容性分析

> 基于 Dolly ADX26 源码 (v0.2.0) 的实地审计
> 日期: 2026-07-24

---

## 核心发现

**Dolly 目前没有机器人集成。** `Robotics/` 目录是空的，没有 `RobotGatewayClient`，没有 `ROBOT_*` 环境变量。旧版 Ceaser v1 使用的是完全不同的协议（引擎推送模式）。这意味着我们不是在"适配"现有接口，而是在"设计"新接口——这反而是优势。

---

## 1. 旧版 Ceaser v1 协议 vs 新版 API 契约

| 维度 | 旧版 Ceaser v1 | 新版 API 契约 |
|------|---------------|-------------|
| 通信方向 | 引擎 → Dolly（推送） | Dolly → robot-service（拉取） |
| 传输协议 | WebSocket only | HTTP + WebSocket |
| 端点 | `/ws/v1/engine` | `GET/POST /v1/*` + `WS /v1/events` |
| 消息格式 | `engine.hello`, `engine.heartbeat`, `director.decision` | REST JSON + EventEnvelope |
| 机器人控制 | 无（引擎单向推送决策） | 有（POST /v1/commands, POST /v1/stop） |
| 视频流 | 无（旧版处理的是 OBS 导播） | 有（/v1/frame.jpg, /v1/video.mjpeg） |

**结论：旧版和新版是完全不同的架构。新版是更完整的设计。**

---

## 2. Dolly 代码风格分析（必须遵循的约定）

### 2.1 数据模型
- 所有请求/响应使用 **Pydantic BaseModel**，`extra="forbid"`
- 版本标识使用 `v: Literal["1.0"]`
- 时间戳使用 ISO 8601 UTC: `datetime.now(timezone.utc)`
- 状态枚举使用 `StrEnum`
- 错误响应格式: `{"error": {"code": "ERROR_CODE", "message": "..."}}`

### 2.2 事件系统
- 所有事件通过 `EventBus` 发布，使用 `EventEnvelope`:
  ```python
  EventEnvelope(
      event_type=EventType.XXX,
      source="source_name",
      payload={...},
      session_id=...,
      correlation_id=...,
      sequence=...,
  )
  ```
- 事件类型在 `EventType(StrEnum)` 中预定义
- payload 禁止超过 512KB

### 2.3 配置管理
- 使用 `pydantic-settings` 的 `BaseSettings`
- 环境变量大写 + 前缀（如 `DOLLY_*`, `CAM_*`, `OBS_*`）
- `model_validator(mode="after")` 做运行时验证
- 支持 `env_file=".env"` 自动加载

### 2.4 状态管理
- `StateStore` 使用 `asyncio.Lock` 保护
- 按 section 组织（`cameras`, `audio`, `obs`, `director`, `engine`...）
- 已有 `engine` section 存放引擎状态
- 支持 `set_degraded()` 优雅降级

### 2.5 运行时模式
- `Runtime` 类是组合根，持有所有服务
- `initialize()` 按依赖顺序启动
- `shutdown()` 按逆序清理
- `readiness()` 返回所有检查项的 ready 状态

---

## 3. 我们 API 契约需要调整的地方

### 3.1 需要立即对齐的

| 我们的设计 | Dolly 的实际约定 | 调整 |
|-----------|-----------------|------|
| `/v1/health` | Dolly 用 `/healthz` 返回 `HealthResponse` | 改为 `/v1/health` 但返回格式对齐 Dolly 风格 |
| 状态 JSON 格式 | Dolly 用 `StateStore.snapshot()` 返回嵌套 dict | 保持当前格式，但加 `schema_version` 字段 |
| 时间戳 `ts` | Dolly 用 ISO 8601 `timestamp_utc` | 统一为 `ts_utc: datetime` |
| 错误格式 | Dolly 用 `{"error": {"code": "...", "message": "..."}}` | 已对齐，ok |
| 命令响应 `accepted` | Dolly 的 `_execute_command` 也用 `accepted: bool` | 已对齐，ok |

### 3.2 需要新增到 Dolly 的事件类型

我们的 robot-service 状态需要映射到 Dolly 的 `EventType`:

```python
# 需要在 Dolly 的 contracts.py 中新增:
ROBOT_STATE = "robot.state"        # 机器人状态变化
ROBOT_CONNECTED = "robot.connected" # 机器人连接/断开
ROBOT_FRAME = "robot.frame"         # 新帧可用
ROBOT_SAFETY = "robot.safety"       # 安全事件
ROBOT_COMMAND = "robot.command"     # 命令执行结果
```

### 3.3 需要新增到 Dolly 配置的环境变量

```env
# 在 Dolly 的 .env.example 中新增:
ROBOT_ENABLED=false
ROBOT_BASE_URL=http://192.168.x.x:8780
ROBOT_WS_URL=ws://192.168.x.x:8780/v1/events
ROBOT_AUTH_TOKEN=
ROBOT_ID=go2_62507
```

---

## 4. 集成架构：Dolly 侧需要新建的模块

```
Dolly_adx26/
├── backend/app/
│   ├── robot/                     ← 新建
│   │   ├── __init__.py
│   │   ├── client.py              # RobotGatewayClient (httpx + websockets)
│   │   ├── adapter.py             # robot-service 状态 → Dolly EventBus
│   │   └── contracts.py           # 机器人侧 Pydantic 模型
│   ├── config.py                  ← 修改：新增 ROBOT_* 配置
│   ├── contracts.py               ← 修改：新增 EventType
│   └── runtime.py                 ← 修改：初始化 RobotGatewayClient
```

### RobotGatewayClient 设计

```python
class RobotGatewayClient:
    """Dolly 侧机器人网关客户端。
    
    连接 robot-service 的 HTTP/WS 端点，将机器人状态映射到 Dolly EventBus。
    """
    def __init__(self, settings: RobotSettings, bus: EventBus):
        self._http = httpx.AsyncClient(base_url=settings.base_url)
        self._ws = None
        self._bus = bus
    
    async def connect(self) -> bool:
        """连接 robot-service WebSocket，开始接收事件"""
        
    async def health(self) -> RobotHealth:
        """GET /v1/health"""
        
    async def state(self) -> RobotState:
        """GET /v1/state"""
        
    async def frame(self) -> bytes:
        """GET /v1/frame.jpg"""
        
    async def send_command(self, cmd: RobotCommand) -> CommandReceipt:
        """POST /v1/commands"""
        
    async def stop(self) -> CommandReceipt:
        """POST /v1/stop"""
        
    async def disconnect(self):
        """断开连接"""
```

---

## 5. 兼容性风险矩阵（更新版）

| 风险 | 旧评估 | 实地审计后 | 原因 |
|------|--------|-----------|------|
| API 契约不匹配 | 未知 | 🟢 低风险 | Dolly 侧需新建，可按我们的契约实现 |
| 数据格式不兼容 | 未知 | 🟢 低风险 | Dolly 用 Pydantic，高度一致 |
| 事件系统不兼容 | 未知 | 🟡 中风险 | 需要新增 EventType 到 Dolly |
| 配置管理不兼容 | 未知 | 🟢 低风险 | Dolly 用 pydantic-settings，与我们的设计一致 |
| 生命周期不兼容 | 未知 | 🟢 低风险 | Dolly 的 Runtime.initialize/shutdown 模式清晰 |
| 旧版 Ceaser 协议冲突 | 未知 | 🟢 低风险 | 旧版 `/ws/v1/engine` 可共存，新版用独立路径 |
| 视频流格式不兼容 | 未知 | 🟡 中风险 | Dolly 目前处理 DirectShow 帧，MJPEG 需要验证 |

---

## 6. 结论

**Dolly 和 robot-service 不会不兼容，因为 Dolly 侧还没有机器人集成代码。** 我们不是在适配现有接口，而是在设计新接口。这给了我们完全的控制权——只要两边遵循相同的技术约定（Pydantic、FastAPI、EventBus、Settings），兼容性就是设计出来的，不是碰运气。

**唯一真正的风险：** 如果 Dolly 的维护者（Nero）想要一个不同于我们设计的 API 契约。但这个风险很小，因为：
1. 我们的契约基于 Nero 的 `ceaser workflow.txt` 冻结要求
2. 我们的契约遵循 Dolly 现有的代码风格
3. 我们的契约比旧版 Ceaser v1 协议更完整（支持双向通信）