"""DollyGatewayModule — HTTP/WebSocket bridge between DimOS and Nero Dolly."""

from .contracts import (
    CommandKind,
    CommandReceipt,
    CommandRequest,
    CommandTarget,
    ErrorDetail,
    ErrorResponse,
    EventEnvelope,
    EventType,
    HealthResponse,
    RejectionReason,
    RobotMode,
    RobotStateResponse,
    SafetyState,
    ScanState,
    StopReceipt,
    TargetKind,
    TargetState,
)
from .safety import (
    CommandGuard,
    FrameGuard,
    HeartbeatWatchdog,
    SafetyController,
    SafetyState as SafetyStateInternal,
    TtlResult,
    attach_timestamp,
    get_timestamp,
)
from .state_mapper import build_robot_state, build_robot_state_minimal
from .frame_provider import FrameProvider
from .command_queue import CommandExecutor, CommandQueue
from .module import DollyGatewayModule, create_module

__all__ = [
    # Contracts
    "CommandKind",
    "CommandReceipt",
    "CommandRequest",
    "CommandTarget",
    "ErrorDetail",
    "ErrorResponse",
    "EventEnvelope",
    "EventType",
    "HealthResponse",
    "RejectionReason",
    "RobotMode",
    "RobotStateResponse",
    "SafetyState",
    "ScanState",
    "StopReceipt",
    "TargetKind",
    "TargetState",
    # Safety
    "CommandGuard",
    "FrameGuard",
    "HeartbeatWatchdog",
    "SafetyController",
    "SafetyStateInternal",
    "TtlResult",
    "attach_timestamp",
    "get_timestamp",
    # State mapper
    "build_robot_state",
    "build_robot_state_minimal",
    # Frame provider
    "FrameProvider",
    # Command queue
    "CommandExecutor",
    "CommandQueue",
    # Module
    "DollyGatewayModule",
    "create_module",
]