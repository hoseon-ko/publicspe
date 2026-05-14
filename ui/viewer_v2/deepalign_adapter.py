from __future__ import annotations

from types import MethodType

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ui.viewer_v2.viewer_main import SpeImageViewerV2
from ui.viewer_v2.viewer_state import ViewerState


class DeepAlignViewerV2Adapter(QWidget):
    """Compatibility wrapper that lets DeepAlign use SpeImageViewerV2."""

    range_changed = pyqtSignal(object, object)
    colormap_changed = pyqtSignal(str)
    roi_list_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = ViewerState()
        self.viewer = SpeImageViewerV2(state=self._state, parent=self)
        self._view = self.viewer.view
        self._view.delete_roi = MethodType(
            lambda view, roi_id: self._delete_roi_from_view(roi_id),
            self._view,
        )

        self._external_render_control = False
        self._source_image: np.ndarray | None = None
        self._current_cmap = self._state.colormap
        self._display_vmin = self._state.vmin
        self._display_vmax = self._state.vmax
        self._roi_list_widget = QListWidget(self)
        self._roi_list_widget.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.viewer)

        self._state.colormap_changed.connect(self._on_state_colormap_changed)
        self._state.range_changed.connect(self._on_state_range_changed)
        self.viewer.view.interactions.roi_added.connect(self._on_v2_roi_added)
        self.viewer.view.interactions.roi_selected.connect(self._on_v2_roi_selected)
        self.viewer.roi_panel.roi_deleted.connect(lambda _roi_id: self._refresh_roi_list())
        self.viewer.roi_panel.roi_selected.connect(lambda _roi_id: self._refresh_roi_list())

        self._refresh_roi_list(emit_signal=False)

    def set_external_render_control(self, enabled: bool) -> None:
        self._external_render_control = bool(enabled)

    def set_source_image(self, img: np.ndarray) -> None:
        self._source_image = None if img is None else np.asarray(img)

    def set_image_first(self, image: np.ndarray) -> None:
        self.set_source_image(image)
        self.set_live_frame(image, fit=True)

    def set_image(self, image: np.ndarray) -> None:
        self.set_source_image(image)
        self.set_live_frame(image, fit=False)

    def set_live_frame(self, rgb: np.ndarray, fit: bool = False) -> None:
        image = self._source_image if self._source_image is not None else rgb
        image = self._coerce_viewer_image(image)
        if image is None:
            return

        self.viewer.set_image(image)
        if fit:
            self.viewer.view.fit_in_view()

    # ------------------------------------------------------------------
    # Public API (FramePipelineMixin 등 외부에서 접근할 공개 인터페이스)
    # ------------------------------------------------------------------

    @property
    def current_cmap(self) -> str:
        """현재 적용된 컬러맵 이름을 반환합니다."""
        return self._current_cmap

    @property
    def display_vmin(self):
        """현재 표시 범위의 최솟값을 반환합니다."""
        return self._display_vmin

    @property
    def display_vmax(self):
        """현재 표시 범위의 최댓값을 반환합니다."""
        return self._display_vmax

    def get_roi_list_widget(self) -> QListWidget:
        """내부 ROI 목록 위젯을 반환합니다."""
        return self._roi_list_widget

    def set_active_roi(self, roi_id: int, mode: str = "profile") -> None:
        """지정한 ROI를 활성 상태로 설정합니다."""
        self._set_active_roi(roi_id, mode)

    def delete_roi(self, roi_id: int) -> None:
        """지정한 ROI를 삭제하고 목록을 갱신합니다."""
        self._delete_roi_from_view(roi_id)

    def delete_all_rois(self) -> None:
        """모든 ROI를 삭제합니다."""
        self._delete_all_rois_ui()

    # ------------------------------------------------------------------

    def hide_range_popup(self) -> None:
        popup = getattr(self.viewer, "_range_popup", None)
        if popup is not None:
            popup.hide()
        btn = getattr(self.viewer, "btn_range", None)
        if btn is not None:
            btn.setChecked(False)

    def autoRange(self) -> None:
        if self._source_image is not None:
            arr = np.asarray(self._source_image)
            if arr.size:
                self._state.update_range(float(np.min(arr)), float(np.max(arr)))
        self.viewer.view.fit_in_view()

    def _refresh_roi_list(self, emit_signal: bool = True) -> None:
        self._roi_list_widget.clear()
        selected_id = self.viewer.view.interactions._selected_roi_id
        for roi_id, roi in sorted(self._rois().items()):
            text = roi.label() if hasattr(roi, "label") else f"ROI #{roi_id}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, roi_id)
            self._roi_list_widget.addItem(item)
            if roi_id == selected_id:
                self._roi_list_widget.setCurrentItem(item)

        if emit_signal:
            self.roi_list_changed.emit()

    def _set_active_roi(self, roi_id: int, role: str) -> None:
        rois = self._rois()
        if roi_id not in rois:
            return

        for rid, roi in rois.items():
            if hasattr(roi, "set_active_profile"):
                roi.set_active_profile(role != "hist" and rid == roi_id)
            if hasattr(roi, "set_active_hist"):
                roi.set_active_hist(role == "hist" and rid == roi_id)

        self.viewer.view.interactions._select_roi(roi_id)
        if hasattr(self.viewer.roi_panel, "set_active_roi"):
            self.viewer.roi_panel.set_active_roi(roi_id, role)
        self._refresh_roi_list()

    def _delete_all_rois_ui(self) -> None:
        for roi_id in list(self._rois().keys()):
            self.viewer.view.interactions.delete_roi(roi_id)
        self.viewer.roi_panel.clear_all()
        self._refresh_roi_list()

    def _delete_roi_from_view(self, roi_id: int) -> None:
        self.viewer.view.interactions.delete_roi(roi_id)
        self.viewer.roi_panel.remove_roi(roi_id)
        self._refresh_roi_list()

    def _on_state_colormap_changed(self, name: str) -> None:
        self._current_cmap = str(name)
        self.colormap_changed.emit(self._current_cmap)

    def _on_state_range_changed(self, vmin, vmax) -> None:
        self._display_vmin = vmin
        self._display_vmax = vmax
        self.range_changed.emit(vmin, vmax)

    def _on_v2_roi_added(self, roi) -> None:
        if hasattr(roi, "modified"):
            roi.modified.connect(self._refresh_roi_list)
        self._refresh_roi_list()

    def _on_v2_roi_selected(self, _roi_id) -> None:
        self._refresh_roi_list()

    def _rois(self) -> dict[int, object]:
        return self.viewer.view.interactions._rois

    def _coerce_viewer_image(self, image) -> np.ndarray | None:
        if image is None:
            return None
        arr = np.asarray(image)
        if arr.ndim == 2:
            return np.ascontiguousarray(arr)
        if arr.ndim == 3 and arr.shape[2] >= 3:
            rgb = arr[..., :3].astype(np.float32)
            gray = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
            return np.ascontiguousarray(np.clip(gray, 0, 255).astype(np.uint8))
        arr = np.squeeze(arr)
        if arr.ndim == 2:
            return np.ascontiguousarray(arr)
        return None
