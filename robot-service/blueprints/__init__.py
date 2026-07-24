"""Custom DimOS blueprints for Singularity Go2.

Blueprint: singularity-go2
  - Integrates DollyGatewayModule with GO2Connection
  - Bridges camera frames, robot state, and heartbeat to HTTP/WebSocket API
  - Manages RealExecutor, RobotSkillProvider, and SingularitySkillContainer
"""

from .singularity_go2 import (
    BlueprintConfig,
    SingularityGo2Blueprint,
    create,
    create_blueprint,
    integrate_with_connection,
    run_standalone,
)

__all__ = [
    "BlueprintConfig",
    "SingularityGo2Blueprint",
    "create",
    "create_blueprint",
    "integrate_with_connection",
    "run_standalone",
]