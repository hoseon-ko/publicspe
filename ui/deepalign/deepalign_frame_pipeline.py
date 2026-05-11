"""DeepAlign 프레임 표시 파이프라인 파일.

이 파일은 들어오는 프레임을 DeepAlign viewer에 맞게 변환하고 밀어넣는 역할을 합니다.
주요 역할은 다음과 같습니다.
- raw 프레임을 표시용 RGB로 변환
- live/snap/acquire 프레임을 viewer에 반영
- LiveTab과 공유될 때 colormap/range 변경 동기화
- viewer 상태와 ROI dock/list 동기화
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from ui.image_viewer import apply_colormap


class FramePipelineMixin:
    def _on_live_frame_ready(self, rgb, raw):
        if not hasattr(self, "cam_viewer"):
            return
        if raw is not None:
            self.cam_viewer.set_source_image(raw)
        else:
            self.cam_viewer.set_source_image(rgb)
        self.cam_viewer.set_live_frame(rgb, fit=self._viewer_first_frame)
        self._viewer_first_frame = False

    def _push_frame(self, raw) -> None:
        rgb = self._to_display_rgb(raw)
        self._on_live_frame_ready(rgb, raw)

    def _to_display_rgb(self, raw: np.ndarray) -> np.ndarray:
        arr = np.asarray(raw)

        if arr.ndim == 3 and arr.shape[2] == 3:
            if arr.dtype == np.uint8:
                return arr
            return np.clip(arr, 0, 255).astype(np.uint8)

        if arr.ndim != 2:
            arr = np.asarray(arr).squeeze()
            if arr.ndim != 2:
                arr = np.zeros((32, 32), dtype=np.uint8)

        cmap = getattr(self.cam_viewer, "_current_cmap", "off")
        if cmap and str(cmap).lower() != "off":
            vmin = getattr(self.cam_viewer, "_display_vmin", None)
            vmax = getattr(self.cam_viewer, "_display_vmax", None)
            rgba = apply_colormap(arr.astype(np.float64), str(cmap), vmin=vmin, vmax=vmax)
            return np.ascontiguousarray(rgba[:, :, :3]).astype(np.uint8)

        vmin = float(np.min(arr))
        vmax = float(np.max(arr))
        if vmax <= vmin:
            gray = np.zeros_like(arr, dtype=np.uint8)
        else:
            gray = ((arr.astype(np.float64) - vmin) * (255.0 / (vmax - vmin))).clip(0, 255).astype(np.uint8)
        return np.ascontiguousarray(np.stack([gray, gray, gray], axis=-1))

    def _on_cmap_changed_sync(self, cmap_name: str):
        if self._live_tab and hasattr(self._live_tab, "image_viewer"):
            cb = self._live_tab.image_viewer.cmap_combo
            idx = cb.findText(cmap_name, Qt.MatchFlag.MatchFixedString | Qt.MatchFlag.MatchContains)
            if idx < 0:
                idx = cb.findText(cmap_name, Qt.MatchFlag.MatchFixedString)

            if idx >= 0:
                cb.setCurrentIndex(idx)
            elif cmap_name.lower() == "off":
                cb.setCurrentText("Off")
            else:
                cb.setCurrentText(cmap_name)

    def _update_roi_list_from_viewer(self):
        if not hasattr(self, "roi_list") or not self.cam_viewer:
            return
        self.roi_list.clear()
        internal_list = self.cam_viewer._roi_list_widget
        for i in range(internal_list.count()):
            src_item = internal_list.item(i)
            new_item = QListWidgetItem(src_item.text())
            new_item.setData(Qt.ItemDataRole.UserRole, src_item.data(Qt.ItemDataRole.UserRole))
            self.roi_list.addItem(new_item)

    def _on_roi_item_clicked(self, item):
        roi_id = item.data(Qt.ItemDataRole.UserRole)
        if roi_id is not None:
            self.cam_viewer._set_active_roi(roi_id, "profile")

    def _on_roi_del_clicked(self):
        item = self.roi_list.currentItem()
        if item:
            roi_id = item.data(Qt.ItemDataRole.UserRole)
            self.cam_viewer._view.delete_roi(roi_id)
            self.cam_viewer._refresh_roi_list()

    def _on_roi_clear_clicked(self):
        self.cam_viewer._delete_all_rois_ui()
