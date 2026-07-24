"""RobotGatewayClient configuration additions for Dolly Settings.

Add these fields to backend/app/config.py's Settings class.
"""

# ---------------------------------------------------------------------------
# Add these fields to the Settings class in Dolly's config.py:
# ---------------------------------------------------------------------------

# ---- Robot Gateway ----
# robot_gateway_url: str = Field(
#     "http://192.168.123.15:8780",
#     validation_alias="ROBOT_GATEWAY_URL",
# )
# robot_gateway_enabled: bool = Field(
#     False,
#     validation_alias="ROBOT_GATEWAY_ENABLED",
# )
# robot_auth_token: str = Field(
#     "",
#     validation_alias="ROBOT_AUTH_TOKEN",
# )
# robot_health_interval_s: float = Field(
#     5.0, ge=1.0, le=60.0,
#     validation_alias="ROBOT_HEALTH_INTERVAL_S",
# )
# robot_state_interval_s: float = Field(
#     0.5, ge=0.1, le=10.0,
#     validation_alias="ROBOT_STATE_INTERVAL_S",
# )