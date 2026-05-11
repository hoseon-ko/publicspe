"""Session event names and payload envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SessionEventType(str, Enum):
    CAMERA_CONNECTED = "camera_connected"
    CAMERA_DISCONNECTED = "camera_disconnected"
    FRAME_READY = "frame_ready"
    STREAM_STARTED = "stream_started"
    STREAM_STOPPED = "stream_stopped"
    EXPOSURE_CHANGED = "exposure_changed"
    ACQUISITION_STARTED = "acquisition_started"
    ACQUISITION_FINISHED = "acquisition_finished"
    ERROR_RAISED = "error_raised"
    STATUS_MESSAGE = "status_message"


@dataclass(slots=True)
class SessionEvent:
    event_type: SessionEventType
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def make_event(event_type: SessionEventType, source: str, **payload: Any) -> SessionEvent:
    return SessionEvent(event_type=event_type, source=source, payload=payload)
