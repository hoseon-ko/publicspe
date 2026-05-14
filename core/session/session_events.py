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
    ACS_CONNECTED = "acs_connected"
    ACS_DISCONNECTED = "acs_disconnected"
    KIMM_CONNECTED = "kimm_connected"
    KIMM_DISCONNECTED = "kimm_disconnected"
    PICO_CONNECTED = "pico_connected"
    PICO_DISCONNECTED = "pico_disconnected"
    MOTION_STATE_CHANGED = "motion_state_changed"
    MOTION_COORDS_UPDATED = "motion_coords_updated"
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
