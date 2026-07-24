"""RobotGatewayClient integration into Dolly Runtime.

Instructions for integrating into backend/app/runtime.py.
"""

# ============================================================================
# Step 1: Add import at top of runtime.py
# ============================================================================
# from .robot_gateway import RobotGatewayClient

# ============================================================================
# Step 2: Add to Runtime.__init__ (after self.editor = None)
# ============================================================================
# self.robot_gateway = RobotGatewayClient(
#     base_url=settings.robot_gateway_url,
#     bus=self.bus,
#     state=self.state,
#     auth_token=settings.robot_auth_token or None,
#     health_interval_s=settings.robot_health_interval_s,
#     state_interval_s=settings.robot_state_interval_s,
#     enabled=settings.robot_gateway_enabled,
# )

# ============================================================================
# Step 3: Add to Runtime.initialize() (after engine state update)
# ============================================================================
# if self.robot_gateway.enabled:
#     await self.robot_gateway.start()

# ============================================================================
# Step 4: Add to Runtime.shutdown() (before engine state update)
# ============================================================================
# if self.robot_gateway.enabled:
#     await self.robot_gateway.stop()

# ============================================================================
# Step 5: Add to Runtime.capabilities() (in the return dict)
# ============================================================================
# "robot": {
#     "connected": self.robot_gateway.connected,
#     "enabled": self.robot_gateway.enabled,
# },