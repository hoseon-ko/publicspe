"""Typed session state model for shared hardware hub."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.session.ownership import OWNER_NONE


class CameraConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class StreamState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    STREAMING = "streaming"
    STOPPING = "stopping"
    ERROR = "error"


class ActivityState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHING = "finishing"
    PAUSED = "paused"
    ERROR = "error"


@dataclass(slots=True)
class CameraSessionState:
    vendor: str = ""
    device_id: str = ""
    connection: CameraConnectionState = CameraConnectionState.DISCONNECTED
    stream: StreamState = StreamState.STOPPED
    exposure_ms: float = 0.0
    last_error: str = ""


@dataclass(slots=True)
class RuntimeActivityState:
    acquisition: ActivityState = ActivityState.IDLE
    scan: ActivityState = ActivityState.IDLE
    autofocus: ActivityState = ActivityState.IDLE
    kinematic: ActivityState = ActivityState.IDLE


@dataclass(slots=True)
class SessionState:
    camera: CameraSessionState = field(default_factory=CameraSessionState)
    activity: RuntimeActivityState = field(default_factory=RuntimeActivityState)
    exclusive_owner: str = OWNER_NONE


def create_default_state() -> SessionState:
    return SessionState()
