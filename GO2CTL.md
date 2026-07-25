# go2ctl

Standalone Unitree Go2 terminal control lives in [`robot-console/`](robot-console/README.md).

```bash
source dimos/.venv/bin/activate
cd robot-console && uv pip install -e ".[dev]"
go2ctl doctor
```

The Dolly `robot-service/` gateway is unchanged. Future API integration should call `Go2Controller` only.
