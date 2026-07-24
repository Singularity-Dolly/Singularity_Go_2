"""Custom DimOS blueprints for Singularity Go2.

Blueprint: singularity-go2
  - Integrates DollyGatewayModule with GO2Connection
  - Bridges camera frames, robot state, and heartbeat to HTTP/WebSocket API
"""

from .singularity_go2 import integrate_with_connection

__all__ = ["integrate_with_connection"]