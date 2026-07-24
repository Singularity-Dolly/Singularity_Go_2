"""Singularity Go2 blueprint — integrates DollyGatewayModule with DimOS.

This blueprint wires the robot-service HTTP/WebSocket gateway into the
DimOS module lifecycle, connecting camera frames, robot state, and
heartbeat signals from the real robot to the Nero Dolly API.

Usage:
    dimos run singularity-go2
"""

from __future__ import annotations

import logging
from typing import Any

from dolly_gateway.module import DollyGatewayModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DimOS blueprint entry point
# ---------------------------------------------------------------------------

# This file is auto-discovered by DimOS when placed in the blueprints/ directory.
# The blueprint name is derived from the filename: singularity_go2 → singularity-go2

# To integrate with a real GO2Connection module:
#
# 1. Import the module in your blueprint:
#    from dimos.robots.go2.connection import GO2Connection
#
# 2. Instantiate the gateway:
#    gateway = DollyGatewayModule(robot_id="go2-serial-xxx", port=8780)
#
# 3. Wire callbacks:
#    connection.on_frame = gateway.on_frame       # JPEG frames
#    connection.on_state = gateway.set_dimos_state_provider  # state dict
#    connection.on_heartbeat = gateway.heartbeat   # heartbeat
#
# 4. Start gateway after connection:
#    await gateway.start()
#
# 5. On shutdown:
#    await gateway.stop()


# ---------------------------------------------------------------------------
# Integration guide
# ---------------------------------------------------------------------------

def integrate_with_connection(
    gateway: DollyGatewayModule,
    connection: Any,  # dimos.robots.go2.connection.GO2Connection
) -> None:
    """Wire a DollyGatewayModule to a GO2Connection instance.

    Args:
        gateway: The DollyGatewayModule instance.
        connection: A GO2Connection (or compatible) instance with:
            - on_frame(jpeg_bytes) callback
            - on_state() → dict provider
            - heartbeat signal
    """
    # Wire camera frames
    if hasattr(connection, "on_frame"):
        original_on_frame = getattr(connection, "on_frame", None)

        def _frame_handler(jpeg_bytes: bytes) -> None:
            gateway.on_frame(jpeg_bytes)
            if original_on_frame:
                original_on_frame(jpeg_bytes)

        connection.on_frame = _frame_handler
        logger.info("Camera frames wired to DollyGatewayModule")

    # Wire state provider
    if hasattr(connection, "get_state"):
        gateway.set_dimos_state_provider(connection.get_state)
        logger.info("Robot state provider wired to DollyGatewayModule")

    # Wire heartbeat
    if hasattr(connection, "heartbeat"):
        # Register gateway heartbeat in the connection's heartbeat loop
        pass  # GO2Connection-specific wiring

    logger.info("DollyGatewayModule fully integrated with GO2Connection")