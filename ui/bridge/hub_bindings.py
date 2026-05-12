"""MainWindow <-> DeviceSessionHub binding helpers.

Phase-1 note:
- This module does not mutate existing wiring automatically.
- It provides opt-in helpers so migration can be done incrementally.
"""

from __future__ import annotations

from typing import Any

from core.session.device_session_hub import DeviceSessionHub


def bind_status_to_main_window(hub: DeviceSessionHub, main_window: Any) -> None:
    if hasattr(main_window, "_on_status"):
        hub.status_message.connect(main_window._on_status)
    if hasattr(main_window, "_log"):
        hub.status_message.connect(lambda text: main_window._log(f"[HUB] {text}", "camera"))


def bind_live_signals_to_hub(hub: DeviceSessionHub, live_tab: Any) -> None:
    """Optional bridge used during migration while LiveTab still exists."""
    if hasattr(live_tab, "status_message"):
        live_tab.status_message.connect(lambda text: hub.publish_status(text, source="live_tab"))

    if hasattr(live_tab, "frame_ready"):
        live_tab.frame_ready.connect(lambda rgb, raw: hub.publish_frame(rgb, raw, source="live_tab"))

    if hasattr(live_tab, "camera_connected"):
        live_tab.camera_connected.connect(lambda _cam: hub.mark_camera_connected(source="live_tab"))

    if hasattr(live_tab, "camera_disconnected"):
        live_tab.camera_disconnected.connect(lambda: hub.mark_camera_disconnected(source="live_tab"))

    if hasattr(live_tab, "exposure_applied"):
        live_tab.exposure_applied.connect(lambda ms: hub.set_exposure_ms(ms, source="live_tab"))
