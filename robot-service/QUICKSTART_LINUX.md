# Linux 快速启动指南

## 前置条件

- Xubuntu / Ubuntu 22.04+
- Python 3.10+
- 网络连接（首次安装需下载约 3GB 依赖）

## 第一步：安装 DimOS（约 10-30 分钟）

```bash
cd ~/Singularity_Go_2
chmod +x install_dimos_xubuntu.sh
./install_dimos_xubuntu.sh
```

国内网络慢则编辑脚本，取消第 83 行注释启用清华镜像。

安装完成后验证：
```bash
cd ~/Singularity_Go_2/dimos
source .venv/bin/activate
dimos --help               # 应该显示 CLI 帮助
dimos --replay run unitree-go2   # 回放模式（无需硬件）
```

## 第二步：安装 robot-service 模块

```bash
cd ~/Singularity_Go_2/dimos
source .venv/bin/activate
uv pip install -e ../robot-service
```

验证安装：
```bash
python -c "from dolly_gateway import DollyGatewayModule; print('OK')"
```

## 第三步：启动服务

### 方式 A：独立启动（推荐首次测试）

```bash
cd ~/Singularity_Go_2/dimos
source .venv/bin/activate
python -m uvicorn dolly_gateway.api:app --host 0.0.0.0 --port 8780
```

另一个终端测试：
```bash
bash ~/Singularity_Go_2/scripts/test_endpoints.sh
```

### 方式 B：Python 脚本启动（完整功能）

```bash
cd ~/Singularity_Go_2/dimos
source .venv/bin/activate
```

创建 `start_gateway.py`：
```python
import asyncio
from dolly_gateway import DollyGatewayModule

async def main():
    module = DollyGatewayModule(robot_id="go2-test-001", port=8780)
    await module.start()
    # 服务运行中... Ctrl+C 停止
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await module.stop()

asyncio.run(main())
```

## 第四步：操控机器人

### curl 命令

```bash
# 健康检查
curl http://localhost:8780/v1/health

# 获取状态
curl http://localhost:8780/v1/state

# 开始扫描
curl -X POST http://localhost:8780/v1/commands \
  -H "Content-Type: application/json" \
  -d '{"command":"scan.start","ttl_ms":5000}'

# 开始跟随
curl -X POST http://localhost:8780/v1/commands \
  -H "Content-Type: application/json" \
  -d '{"command":"follow.start","ttl_ms":5000}'

# 紧急停止
curl -X POST http://localhost:8780/v1/stop

# 视频流（浏览器打开）
# http://localhost:8780/v1/video?fps=10
```

### Nero/Dolly 端连接

Nero 的 Windows 机器通过局域网访问：
```
http://<Linux_IP>:8780/v1/health
http://<Linux_IP>:8780/v1/state
http://<Linux_IP>:8780/v1/video?fps=10
ws://<Linux_IP>:8780/v1/events
```

## 安全机制

| 机制 | 说明 |
|------|------|
| TTL 过期 | 命令超过 TTL 自动拒绝 |
| 心跳看门狗 | 1.5s 无心跳触发紧急停止 |
| 过期帧检测 | 500ms 无新帧返回 503 |
| 紧急停止 | `mission.stop` 绕过所有队列立即执行 |
| 队列上限 | 最多 8 个待处理命令 |

## 运行测试

```bash
cd ~/Singularity_Go_2/robot-service
python -m pytest tests/ -v    # 58 个测试，全绿
```

## 与真实 Go2 集成

连接真实硬件后，在 DimOS blueprint 中注册回调：

```python
from blueprints.singularity_go2 import integrate_with_connection
from dolly_gateway import DollyGatewayModule

gateway = DollyGatewayModule(robot_id="go2-xxx", port=8780)
integrate_with_connection(gateway, go2_connection_module)
await gateway.start()
```

三个数据流自动桥接：
- `connection.on_frame` → `gateway.on_frame` → HTTP 视频流
- `connection.get_state` → `gateway.set_dimos_state_provider` → `/v1/state`
- `connection.heartbeat` → `gateway.heartbeat` → 心跳看门狗