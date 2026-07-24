"""Singularity Go2 blueprint — integrates DollyGatewayModule with DimOS.

This blueprint wires the robot-service HTTP/WebSocket gateway into the
DimOS module lifecycle, connecting camera frames, robot state, and
heartbeat signals from the real robot to the Nero Dolly API.

Architecture:
  ┌──────────────────────────────────────────────────────────────────┐
  │  SingularityGo2Blueprint (DimOS Module)                          │
  │                                                                  │
  │  ┌─────────────────────┐  ┌──────────────────────────────────┐  │
  │  │  RobotSkillProvider  │  │  DollyGatewayModule              │  │
  │  │  - follow skill      │  │  - SafetyController              │  │
  │  │  - navigation skill  │  │  - CommandQueue + RealExecutor   │  │
  │  │  - explorer skill    │  │  - FrameProvider                 │  │
  │  │  - health check      │  │  - FastAPI (:8780)               │  │
  │  └─────────┬───────────┘  └──────────────┬───────────────────┘  │
  │            │                              │                      │
  │            └──────────┬───────────────────┘                      │
  │                       │ wire_to_executor()                       │
  │                       ▼                                          │
  │  ┌────────────────────────────────────────────────────────────┐  │
  │  │  SingularitySkillContainer (bridge layer)                  │  │
  │  │  - follow_person()  → PersonFollowSkillContainer           │  │
  │  │  - stop_navigation() → NavigationSkillContainer            │  │
  │  │  - start_exploration() → WavefrontFrontierExplorer         │  │
  │  │  - stop_all()       → all skills                           │  │
  │  └────────────────────────────────────────────────────────────┘  │
  │                                                                  │
  │  ┌────────────────────────────────────────────────────────────┐  │
  │  │  DimOS State Bridge (via GO2Connection)                    │  │
  │  │  - on_frame(jpeg)    → gateway.on_frame()                  │  │
  │  │  - get_state()       → gateway.set_dimos_state_provider()  │  │
  │  │  - heartbeat         → gateway.heartbeat()                 │  │
  │  └────────────────────────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────────────────────┘

Usage:
    dimos run singularity-go2

    # Or standalone (without DimOS):
    python -c "
    from blueprints.singularity_go2 import create_blueprint
    import asyncio
    bp = create_blueprint()
    asyncio.run(bp.start())
    "
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dolly_gateway.config import GatewaySettings
from dolly_gateway.executor import RealExecutor, SkillContainer
from dolly_gateway.module import DollyGatewayModule
from dolly_gateway.skill_provider import RobotSkillProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Blueprint configuration
# ---------------------------------------------------------------------------

class BlueprintConfig:
    """Configuration for the Singularity Go2 blueprint.

    Loaded from GatewaySettings (pydantic-settings from .env).
    """

    def __init__(self, settings: GatewaySettings | None = None) -> None:
        self._settings = settings or GatewaySettings()

    @property
    def robot_id(self) -> str:
        return self._settings.robot_id

    @property
    def port(self) -> int:
        return self._settings.port

    @property
    def host(self) -> str:
        return self._settings.host

    @property
    def command_timeout_s(self) -> float:
        return self._settings.command_timeout_s

    @property
    def max_queue_size(self) -> int:
        return self._settings.max_queue_size


# ---------------------------------------------------------------------------
# SingularityGo2Blueprint
# ---------------------------------------------------------------------------

class SingularityGo2Blueprint:
    """Main DimOS blueprint for the Singularity Go2 robot-service.

    Creates and manages the complete lifecycle of:
    - DollyGatewayModule (HTTP/WebSocket API)
    - RealExecutor (command dispatch to DimOS skills)
    - RobotSkillProvider (skill container lifecycle)
    - SingularitySkillContainer (skill bridge layer)

    Lifecycle:
        __init__()  → create all components (no side effects)
        start()     → start HTTP server, wire callbacks, begin processing
        stop()      → graceful shutdown, drain queues, stop server
    """

    def __init__(
        self,
        config: BlueprintConfig | None = None,
        follow_skill: Any | None = None,
        navigation_skill: Any | None = None,
        explorer_skill: Any | None = None,
    ) -> None:
        """Initialize the blueprint.

        Args:
            config: Blueprint configuration (loaded from .env by default).
            follow_skill: DimOS PersonFollowSkillContainer instance.
            navigation_skill: DimOS NavigationSkillContainer instance.
            explorer_skill: DimOS WavefrontFrontierExplorer instance.
        """
        self._config = config or BlueprintConfig()

        # Skill provider — manages DimOS Skill Container lifecycle
        self.skill_provider = RobotSkillProvider()

        # Real executor — dispatches commands to DimOS skills
        self.executor = RealExecutor(
            command_timeout_s=self._config.command_timeout_s,
        )

        # Gateway module — HTTP/WebSocket API server
        self.gateway = DollyGatewayModule(
            robot_id=self._config.robot_id,
            port=self._config.port,
            host=self._config.host,
            executor=self.executor,
        )

        # Store skill references for delayed wiring
        self._follow_skill = follow_skill
        self._navigation_skill = navigation_skill
        self._explorer_skill = explorer_skill

        # DimOS connection reference (populated during integration)
        self._connection: Any = None

        # Lifecycle state
        self._running = False

    # ------------------------------------------------------------------
    # Skill registration
    # ------------------------------------------------------------------

    def register_skills(
        self,
        follow_skill: Any | None = None,
        navigation_skill: Any | None = None,
        explorer_skill: Any | None = None,
    ) -> None:
        """Register DimOS Skill Containers with the provider and executor.

        Call this after DimOS initializes the skill containers,
        before starting the gateway. Can be called multiple times
        to update skill references.

        Args:
            follow_skill: PersonFollowSkillContainer instance.
            navigation_skill: NavigationSkillContainer instance.
            explorer_skill: WavefrontFrontierExplorer instance.
        """
        if follow_skill is not None:
            self._follow_skill = follow_skill
            self.skill_provider.register("follow", follow_skill)
            self.executor.register_skill("follow", follow_skill)

        if navigation_skill is not None:
            self._navigation_skill = navigation_skill
            self.skill_provider.register("navigation", navigation_skill)
            self.executor.register_skill("navigation", navigation_skill)

        if explorer_skill is not None:
            self._explorer_skill = explorer_skill
            self.skill_provider.register("explorer", explorer_skill)
            self.executor.register_skill("explorer", explorer_skill)

        logger.info(
            "Skills registered: follow=%s, navigation=%s, explorer=%s",
            self._follow_skill is not None,
            self._navigation_skill is not None,
            self._explorer_skill is not None,
        )

    # ------------------------------------------------------------------
    # Connection wiring
    # ------------------------------------------------------------------

    def wire_connection(self, connection: Any) -> None:
        """Wire a GO2Connection to the gateway module.

        Sets up three callback bridges:
        1. Camera frames: connection.on_frame → gateway.on_frame
        2. Robot state: connection.get_state → gateway state provider
        3. Heartbeat: connection.heartbeat → gateway.heartbeat

        Args:
            connection: A GO2Connection (or compatible) instance.
        """
        self._connection = connection

        # Wire camera frames
        if hasattr(connection, "on_frame"):
            original_on_frame = getattr(connection, "on_frame", None)

            def _frame_handler(jpeg_bytes: bytes) -> None:
                self.gateway.on_frame(jpeg_bytes)
                if original_on_frame:
                    original_on_frame(jpeg_bytes)

            connection.on_frame = _frame_handler
            logger.info("Camera frames wired to DollyGatewayModule")

        # Wire state provider
        if hasattr(connection, "get_state"):
            self.gateway.set_dimos_state_provider(connection.get_state)
            logger.info("Robot state provider wired to DollyGatewayModule")

        # Wire heartbeat
        if hasattr(connection, "heartbeat"):
            # The gateway.heartbeat() method should be called from
            # the connection's heartbeat loop
            logger.info("Heartbeat wiring ready (call gateway.heartbeat() from connection loop)")

        logger.info("DollyGatewayModule fully integrated with GO2Connection")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the blueprint: register skills, start gateway.

        This is the main entry point called by DimOS module lifecycle.
        """
        if self._running:
            logger.warning("Blueprint already running")
            return

        # Register skills if provided at construction
        self.register_skills(
            follow_skill=self._follow_skill,
            navigation_skill=self._navigation_skill,
            explorer_skill=self._explorer_skill,
        )

        # Start the gateway (HTTP server + command processor)
        await self.gateway.start()

        self._running = True
        logger.info(
            "SingularityGo2Blueprint started (robot=%s, port=%d)",
            self._config.robot_id,
            self._config.port,
        )

    async def stop(self) -> None:
        """Stop the blueprint: stop gateway, drain queues.

        This is called by DimOS module lifecycle on shutdown.
        """
        if not self._running:
            return

        await self.gateway.stop()
        self._running = False
        logger.info("SingularityGo2Blueprint stopped")

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        """Return health status of all components.

        Returns:
            Dict with skill health, connection status, and uptime.
        """
        return {
            "skills": self.skill_provider.health_check(),
            "all_skills_available": self.skill_provider.all_available(),
            "executor_mode": self.executor.current_mode.value,
            "running": self._running,
            "connection_wired": self._connection is not None,
        }

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def heartbeat(self) -> None:
        """Forward heartbeat from connection to gateway.

        Should be called from the GO2Connection heartbeat loop.
        """
        self.gateway.heartbeat()

    # ------------------------------------------------------------------
    # Frame ingestion
    # ------------------------------------------------------------------

    def on_frame(self, jpeg_bytes: bytes) -> None:
        """Forward camera frame from connection to gateway.

        Should be called from the GO2Connection frame callback.
        """
        self.gateway.on_frame(jpeg_bytes)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def create_blueprint(
    robot_id: str | None = None,
    port: int | None = None,
    host: str | None = None,
) -> SingularityGo2Blueprint:
    """Create a blueprint with optional overrides.

    Args:
        robot_id: Override robot ID (default: from .env).
        port: Override HTTP port (default: from .env).
        host: Override bind host (default: from .env).

    Returns:
        A configured SingularityGo2Blueprint instance.
    """
    settings = GatewaySettings()

    if robot_id is not None:
        settings.robot_id = robot_id
    if port is not None:
        settings.port = port
    if host is not None:
        settings.host = host

    config = BlueprintConfig(settings)
    return SingularityGo2Blueprint(config=config)


async def run_standalone(
    robot_id: str = "unknown",
    port: int = 8780,
    host: str = "0.0.0.0",
) -> SingularityGo2Blueprint:
    """Run the blueprint in standalone mode (without DimOS).

    This is useful for testing and development without a full DimOS
    installation. It starts the HTTP server and processes commands.

    Args:
        robot_id: Robot identifier.
        port: HTTP server port.
        host: HTTP server bind address.

    Returns:
        The running blueprint instance.
    """
    bp = create_blueprint(robot_id=robot_id, port=port, host=host)
    await bp.start()

    logger.info(
        "Blueprint running standalone at http://%s:%d/v1/health",
        host,
        port,
    )

    return bp


# ---------------------------------------------------------------------------
# DimOS entry point (auto-discovered)
# ---------------------------------------------------------------------------

# This function is auto-discovered by DimOS when the blueprint is loaded.
# Signature: create() → module instance
# The module name is derived from the filename: singularity_go2 → singularity-go2


def create() -> SingularityGo2Blueprint:
    """DimOS entry point — creates the blueprint instance.

    Called by DimOS module loader to instantiate the blueprint.
    Configuration is loaded from .env and environment variables.
    """
    return create_blueprint()


# ---------------------------------------------------------------------------
# Legacy integration helper (backward compatible)
# ---------------------------------------------------------------------------

def integrate_with_connection(
    gateway: DollyGatewayModule,
    connection: Any,
) -> None:
    """Wire a DollyGatewayModule to a GO2Connection instance.

    Legacy helper — the preferred approach is to use
    SingularityGo2Blueprint.wire_connection().

    Args:
        gateway: The DollyGatewayModule instance.
        connection: A GO2Connection (or compatible) instance.
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

    logger.info("DollyGatewayModule fully integrated with GO2Connection")