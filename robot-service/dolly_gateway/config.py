"""Gateway configuration via pydantic-settings.

Loads configuration from environment variables and .env file.
All settings are prefixed with GATEWAY_ to avoid collisions.

Usage:
    from dolly_gateway.config import GatewaySettings

    settings = GatewaySettings()
    # settings.robot_id, settings.port, settings.host, ...

    # With custom .env path:
    settings = GatewaySettings(_env_file="/path/to/.env")
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Configuration for DollyGatewayModule.

    Loaded from .env file and environment variables.
    All config keys are prefixed with GATEWAY_.

    Environment variables:
        GATEWAY_ROBOT_ID: Unique robot identifier (default: "unknown")
        GATEWAY_PORT: HTTP server port (default: 8780)
        GATEWAY_HOST: HTTP server bind address (default: "0.0.0.0")
        GATEWAY_AUTH_TOKEN: Optional Bearer token for API auth (default: None)
        GATEWAY_ROBOT_IP: Robot IP address for direct connection (default: None)
        GATEWAY_LOG_LEVEL: Logging level (default: "INFO")
        GATEWAY_COMMAND_TIMEOUT_S: Command execution timeout in seconds (default: 5.0)
        GATEWAY_HEARTBEAT_INTERVAL_S: Heartbeat check interval (default: 0.5)
        GATEWAY_HEARTBEAT_TIMEOUT_S: Heartbeat timeout threshold (default: 1.5)
        GATEWAY_MAX_QUEUE_SIZE: Maximum command queue size (default: 8)
        GATEWAY_FRAME_MAX_AGE_S: Maximum frame age before considered stale (default: 0.5)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="GATEWAY_",
        extra="ignore",
    )

    # ---- Identity ----
    robot_id: str = "unknown"

    # ---- Network ----
    port: int = 8780
    host: str = "0.0.0.0"
    auth_token: str | None = None
    robot_ip: str | None = None

    # ---- Logging ----
    log_level: str = "INFO"

    # ---- Timing ----
    command_timeout_s: float = 5.0
    heartbeat_interval_s: float = 0.5
    heartbeat_timeout_s: float = 1.5

    # ---- Capacity ----
    max_queue_size: int = 8
    frame_max_age_s: float = 0.5