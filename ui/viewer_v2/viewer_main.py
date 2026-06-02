from __future__ import annotations
import numpy as np
import traceback
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
    QLabel, QToolButton, QComboBox, QToolBar, QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QRectF, pyqtSignal
from PyQt6.QtGui import QPixmap

from ui.viewer_v2.viewer_state import ViewerState
from ui.viewer_v2.base_view import BaseView
from ui.viewer_v2.ruler_system import RulerSystem
from ui.viewer_v2.interaction_layer import InteractionLayer
from ui.roi_panel import RoiPanel
from ui.roi_items import LineROI, BoxROI, HistROI
from ui.viewer_v2.image_provider import ImageProvider
from ui.histogram_range_widget import HistogramRangeWidget
from ui.viewer.graphics_view import _RangePopup
from ui.deepalign._perf_probe import perf_tick  # [임시 계측]

class SpeImageViewerV2(QWidget):
    """
    고성능 계층형 이미지 뷰어 (V2) - 안정화 버전
    """
    # 전역 분석 지원용 시그널
    profile_updated = pyqtSignal(object, str)       # (data, label)
    multi_profile_updated = pyqtSignal(object, object) # (data1, data2)
    histogram_updated = pyqtSignal(object, object)  # (counts, bin_edges)
    
    # UI Toggle Requests
    toggle_analysis_requested = pyqtSignal(str)     # 'profile' or 'histogram' or 'proc'
    save_spe_requested = pyqtSignal()               # 현재 표시 raw → SPE 저장 요청

    def __init__(self, state: ViewerState | None = None, parent=None):
        super().__init__(parent)
        self._state = state if state else ViewerState()
        self._img_data: np.ndarray | None = None
        self._last_stats = (0.0, 0.0, 0.0)
        self._last_cursor = (0, 0, 0.0)
        self._fps_history = []
        self._last_fps_time = 0.0
        self._current_fps = 0.0

        # 렌더링 성능 최적화를 위한 스로틀링 타이머
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)
        self._pending_fit = False
        self._setup_ui()
        self._init_range_popup()
        self._connect_signals()
        
        # 초기 상태 동기화 (공유 상태가 이미 값을 가지고 있을 경우 대비)
        self._on_state_colormap_changed(self._state.colormap)
        self._on_state_range_changed(self._state.vmin, self._state.vmax)

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 1. 상단 툴바 (2줄 레이아웃으로 구성하여 모든 버튼을 항상 보이게 유지하며 가로폭 대폭 축소)
        self.toolbar = QFrame()
        self.toolbar.setStyleSheet("background: #0d1a2e; border-bottom: 1px solid #1a3a60;")
        
        tb_layout = QVBoxLayout(self.toolbar)
        tb_layout.setContentsMargins(6, 6, 6, 6)
        tb_layout.setSpacing(6)
        
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)

        btn_style = """
            QToolButton { color: #a0b0c0; border: 1px solid #1a3a60; border-radius: 4px; padding: 3px 8px; font-weight: bold; }
            QToolButton:hover { background: #1a3a60; color: white; }
            QToolButton:checked { background: #1a3a60; color: #4ecdc4; border: 1px solid #4ecdc4; }
        """

        def make_btn(text, tooltip, callback=None):
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setStyleSheet(btn_style)
            if callback: btn.clicked.connect(callback)
            return btn

        self.btn_select = make_btn("↖ Select", "Selection & Edit Mode")
        self.btn_select.setCheckable(True)
        self.btn_select.setChecked(True)
        
        self.btn_range = make_btn("📊 Range", "Toggle Histogram Range")
        self.btn_range.setCheckable(True)
        
        self.btn_roi_range = make_btn("🎯 ROI Range", "Set range from ROI")
        self.btn_roi_range.setCheckable(True)

        self.btn_1to1 = make_btn("1:1", "1:1 Pixel Scale")
        self.btn_fit = make_btn("FIT", "Fit to View")

        self.btn_roi_line = make_btn("📏 Line", "Line ROI")
        self.btn_roi_line.setCheckable(True)
        self.btn_roi_box = make_btn("🟦 Box", "Box ROI")
        self.btn_roi_box.setCheckable(True)
        self.btn_roi_hist = make_btn("📊 Hist", "Hist ROI")
        self.btn_roi_hist.setCheckable(True)
        self.roi_btns = [self.btn_select, self.btn_roi_line, self.btn_roi_box, self.btn_roi_hist]

        self.btn_roi_list = make_btn("📋 List", "ROI List")
        self.btn_roi_list.setCheckable(True)
        self.btn_roi_list.setChecked(True)

        self.btn_toggle_profile = make_btn("📈 Plot", "Show/Hide Profile Panel")
        self.btn_toggle_histogram = make_btn("📊 Hist", "Show/Hide Histogram Panel")
        self.btn_toggle_proc = make_btn("📉 Proc", "Show/Hide Proc Stats Plot")
        self.btn_toggle_table = make_btn("📋 Table", "Show/Hide Metric Table")
        # dock visibility 와 동기화되도록 checkable
        for b in (self.btn_toggle_profile, self.btn_toggle_histogram,
                  self.btn_toggle_proc, self.btn_toggle_table):
            b.setCheckable(True)

        # Online Statistics 팝업 (선택 ROI/전체 픽셀 통계)
        self.btn_online_stats = make_btn("Σ Stats", "Online Statistics 팝업 열기")
        self.btn_online_stats.clicked.connect(self._open_online_stats)
        self._online_stats_dlg = None

        # 현재 viewer 표시 raw 를 SPE 로 저장
        self.btn_save_spe = make_btn("💾 Save SPE", "현재 표시된 raw 를 SPE 로 저장")
        self.btn_save_spe.clicked.connect(self.save_spe_requested.emit)

        # 컬러맵 선택 드롭다운
        self.combo_cmap = QComboBox()
        self.combo_cmap.addItems(["Off", "Gray", "Jet", "Viridis", "Hot", "Plasma"])
        self.combo_cmap.setFixedWidth(100)
        self.combo_cmap.setStyleSheet("""
            QComboBox { background: #0d2038; color: #a0c0e0; border: 1px solid #1a3a60; border-radius: 4px; padding: 2px 5px; font-weight: bold; }
            QComboBox:hover { background: #1a3a60; color: white; }
            QComboBox QAbstractItemView { background: #0d2038; color: #a0c0e0; selection-background-color: #1a3a60; border: 1px solid #1a3a60; }
        """)
        self.combo_cmap.currentTextChanged.connect(self._on_cmap_changed)

        # 첫 번째 줄에 뷰어 및 이미지 기본 컨트롤 배치
        row1.addWidget(self.btn_select)
        row1.addWidget(self.btn_range)
        row1.addWidget(self.btn_roi_range)
        row1.addWidget(self.btn_1to1)
        row1.addWidget(self.btn_fit)
        
        row1.addStretch()
        
        lbl_cmap = QLabel("🎨 Colormap: ")
        lbl_cmap.setStyleSheet("color: #a0b0c0; font-weight: bold;")
        row1.addWidget(lbl_cmap)
        row1.addWidget(self.combo_cmap)

        # 두 번째 줄에 ROI 드로잉 툴 및 하단 독 토글 버튼들 배치
        row2.addWidget(self.btn_roi_line)
        row2.addWidget(self.btn_roi_box)
        row2.addWidget(self.btn_roi_hist)
        row2.addWidget(self.btn_roi_list)
        
        # 가벼운 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #1a3a60; background: #1a3a60;")
        sep.setFixedWidth(1)
        sep.setFixedHeight(14)
        row2.addWidget(sep)
        
        row2.addWidget(self.btn_toggle_profile)
        row2.addWidget(self.btn_toggle_histogram)
        row2.addWidget(self.btn_toggle_proc)
        row2.addWidget(self.btn_toggle_table)
        row2.addWidget(self.btn_online_stats)

        row2.addStretch()
        row2.addWidget(self.btn_save_spe)

        self.btn_toggle_profile.clicked.connect(lambda: self.toggle_analysis_requested.emit("profile"))
        self.btn_toggle_histogram.clicked.connect(lambda: self.toggle_analysis_requested.emit("histogram"))
        self.btn_toggle_proc.clicked.connect(lambda: self.toggle_analysis_requested.emit("proc"))
        self.btn_toggle_table.clicked.connect(lambda: self.toggle_analysis_requested.emit("table"))
        
        tb_layout.addLayout(row1)
        tb_layout.addLayout(row2)
        
        self.main_layout.addWidget(self.toolbar)

        # 2. 중앙 레이아웃 (Grid)
        center_widget = QWidget()
        center_layout = QGridLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        # 뷰어가 먼저 존재해야 룰러가 이를 참조할 수 있음
        self.view = BaseView(self._state)
        self.ruler_system = RulerSystem(self._state, self)
        
        self.corner = QWidget()
        self.corner.setStyleSheet("background: #0a1020; border-right: 1px solid #1a3a60; border-bottom: 1px solid #1a3a60;")
        
        center_layout.addWidget(self.corner, 0, 0)
        center_layout.addWidget(self.ruler_system.ruler_x, 0, 1)
        center_layout.addWidget(self.ruler_system.ruler_y, 1, 0)
        center_layout.addWidget(self.view, 1, 1)

        center_layout.setColumnStretch(1, 1)
        center_layout.setRowStretch(1, 1)
        
        # 3. 우측 ROI 패널 (Dock 형태)
        self.roi_panel = RoiPanel()
        center_layout.addWidget(self.roi_panel, 0, 2, 2, 1)
        center_layout.setColumnMinimumWidth(2, 150)

        self.main_layout.addWidget(center_widget)

        # 3. 하단 정보바
        self.info_bar = QLabel(" Ready")
        self.info_bar.setFixedHeight(25)
        self.info_bar.setStyleSheet("background: #0d1a2e; color: #a0b0c0; border-top: 1px solid #1a3a60; font-family: 'Consolas'; padding-left: 5px;")
        self.main_layout.addWidget(self.info_bar)

    def _init_range_popup(self):
        self._hist_widget = HistogramRangeWidget()
        self._range_popup = _RangePopup(self._hist_widget, parent=self)
        
        self.btn_range.toggled.connect(self._on_range_toggled)
        self.btn_roi_range.toggled.connect(self._on_roi_range_toggled)
        
        self._hist_widget.range_changed.connect(self._on_ui_range_changed)
        self._range_popup.closed.connect(lambda: self.btn_range.setChecked(False))

    def _connect_signals(self):
        self.btn_1to1.clicked.connect(self.view.set_one_to_one)
        self.btn_fit.clicked.connect(self.view.fit_in_view)
        
        self.btn_select.clicked.connect(lambda: self._set_roi_mode(None))
        self.btn_roi_line.clicked.connect(lambda: self._set_roi_mode('line'))
        self.btn_roi_box.clicked.connect(lambda: self._set_roi_mode('box'))
        self.btn_roi_hist.clicked.connect(lambda: self._set_roi_mode('histogram'))
        self.btn_roi_list.toggled.connect(self._toggle_roi_panel)

        self.view.mouse_moved.connect(self._on_mouse_moved)
        self.view.interactions.roi_added.connect(self._on_roi_added)
        self.view.interactions.roi_selected.connect(self._on_roi_selected)
        self.view.interactions.point_selected.connect(self._on_point_selected)
        self.view.interactions.reset_requested.connect(self._on_reset_requested)
        
        self._state.roi_updated.connect(self._on_basic_roi_updated)
        
        self.roi_panel.roi_selected.connect(self._on_roi_list_selected)
        self.roi_panel.roi_deleted.connect(self._on_roi_list_deleted)
        self.roi_panel.roi_goto.connect(self._on_roi_list_goto)

        self.combo_cmap.currentTextChanged.connect(self._on_cmap_changed)
        
        self._state.range_changed.connect(self._on_state_range_changed)
        self._state.colormap_changed.connect(self._on_state_colormap_changed)
        self._state.roi_updated.connect(self._update_info_text)
        self._state.crosshair_moved.connect(self._update_info_text)
        
        self.ruler_system.layout_size_changed.connect(self._update_layout_size)

    def set_image(self, image: np.ndarray):
        try:
            if image is None: return

            import time
            _ps0 = time.perf_counter()  # [임시 계측]
            now = time.time()
            if now - self._last_fps_time > 1.5:
                self._fps_history = []
            self._last_fps_time = now
            self._fps_history.append(now)
            if len(self._fps_history) > 15:
                self._fps_history.pop(0)

            if len(self._fps_history) > 1:
                dt = self._fps_history[-1] - self._fps_history[0]
                self._current_fps = (len(self._fps_history) - 1) / dt if dt > 0 else 0.0
            else:
                self._current_fps = 0.0

            self._img_data = image
            h, w = image.shape[:2]
            self._state.img_width = w
            self._state.img_height = h
            
            # 비트 깊이 감지
            max_val = np.max(image)
            if image.dtype == np.uint8:
                self._state.bit_depth = 8
            elif image.dtype == np.uint16:
                if max_val > 4095: self._state.bit_depth = 16
                else: self._state.bit_depth = 12
            else:
                self._state.bit_depth = 16 # 기본값
            
            print(f"[ViewerV2] Setting image: {w}x{h}, dtype={image.dtype}, depth={self._state.bit_depth}")
            _ps_head = time.perf_counter()  # [임시 계측] fps+maxval+print

            # 0. Vmin/Vmax 초기화 (중요: 이게 없으면 화면이 뭉개짐)
            f_data = image.astype(np.float32)
            dmin, dmax = float(np.min(f_data)), float(np.max(f_data))
            self._state.update_range(dmin, dmax)

            # ROI Range 모드 활성화 중이면 즉시 적용
            if self.btn_roi_range.isChecked():
                self._apply_roi_range()
            _ps_range = time.perf_counter()  # [임시 계측] astype+min/max+update_range

            # 1. 렌더링용 Pixmap 생성 (Vmin/Vmax 반영)
            pix = ImageProvider.get_display_pixmap(
                image, self._state.colormap, self._state.vmin, self._state.vmax
            )
            _ps_pix = time.perf_counter()  # [임시 계측] get_display_pixmap
            self.view.set_image(pix, w, h, image)
            _ps_view = time.perf_counter()  # [임시 계측] view.set_image

            # 2. 히스토그램 업데이트
            # 2. 히스토그램 위젯 업데이트 (데이터 발송은 하지 않음)
            self._hist_widget.update_image(image, self._state.colormap)
            _ps_hist = time.perf_counter()  # [임시 계측] hist update

            # 4. 분석 데이터 갱신 (선택 상태는 건드리지 않음 — 시각 강조는 선택 변경 시만)
            sel_id = self.view.interactions._selected_roi_id
            if sel_id is not None and sel_id != -1:
                # ① 마지막으로 선택된 전문 ROI(Line/Box/Hist) 기준으로 연산
                roi = self.view.interactions._rois.get(sel_id)
                if roi and self._img_data is not None:
                    self._compute_and_emit_roi(sel_id, roi)
            elif not self._state.selected_roi.isNull():
                # ② 선택된 ROI 없으면 기본 드래그 박스 기준
                self._on_basic_roi_updated(self._state.selected_roi)
            else:
                # ③ 아무것도 없으면 전체 이미지 평균을 Ruler 에만 표시
                #    외부 PlotPanel 로는 시그널을 보내지 않음
                x_prof = np.mean(self._img_data, axis=0)
                y_prof = np.mean(self._img_data, axis=1)
                self.ruler_system.set_profiles(x_prof, y_prof)

            _ps_prof = time.perf_counter()  # [임시 계측] ROI/profile 분석
            self._refresh_display()
            self._update_info_text()
            print(f"[ViewerV2] Image set successfully.")
            _ps_end = time.perf_counter()  # [임시 계측]
            perf_tick("viewer.1_head(fps+max+print)", (_ps_head - _ps0) * 1000.0)
            perf_tick("viewer.2_range(astype+mnmx)", (_ps_range - _ps_head) * 1000.0)
            perf_tick("viewer.3_get_pixmap", (_ps_pix - _ps_range) * 1000.0)
            perf_tick("viewer.4_view.set_image", (_ps_view - _ps_pix) * 1000.0)
            perf_tick("viewer.5_hist_update", (_ps_hist - _ps_view) * 1000.0)
            perf_tick("viewer.6_profile/roi", (_ps_prof - _ps_hist) * 1000.0)
            perf_tick("viewer.7_refresh+info+print", (_ps_end - _ps_prof) * 1000.0)
            perf_tick("viewer.TOTAL_set_image", (_ps_end - _ps0) * 1000.0)
        except Exception as e:
            print(f"[ViewerV2:Error] set_image failed: {e}")
            traceback.print_exc()

    def _on_mouse_moved(self, x, y, val):
        self._last_cursor = (x, y, val)
        self._update_info_text()

    def _on_range_toggled(self, checked: bool):
        if checked:
            pos = self.btn_range.mapToGlobal(self.btn_range.rect().bottomLeft())
            self._range_popup.move(pos)
            self._range_popup.show()
        else:
            self._range_popup.hide()

    def _on_roi_range_toggled(self, checked: bool):
        try:
            if checked:
                print(f"[ViewerV2] ROI Range Mode: ON")
                self._apply_roi_range()
            else:
                print(f"[ViewerV2] ROI Range Mode: OFF")
        except Exception as e:
            print(f"[ViewerV2:Error] ROI Range toggle failed: {e}")
            traceback.print_exc()

    def _apply_roi_range(self):
        """현재 선택된 ROI 영역을 기준으로 vmin/vmax 자동 스케일링 적용"""
        if self._img_data is None: return
        roi = self._state.selected_roi
        if not roi.isNull() and roi.width() > 1 and roi.height() > 1:
            ih, iw = self._img_data.shape[:2]
            ix0 = max(0, min(iw - 1, int(roi.x())))
            iy0 = max(0, min(ih - 1, int(roi.y())))
            ix1 = max(0, min(iw, int(roi.x() + roi.width())))
            iy1 = max(0, min(ih, int(roi.y() + roi.height())))
            
            sub = self._img_data[iy0:iy1, ix0:ix1]
            if sub.size > 0:
                vmin, vmax = float(np.min(sub)), float(np.max(sub))
                self._state.update_range(vmin, vmax)

    def get_active_region(self):
        """Online Statistics 용 활성 영역 반환.

        선택 ROI(전문 Line/Box/Hist → 기본 드래그) 우선, 없으면 전체 이미지.
        returns (sub_2d | None, x0, y0). x0,y0 는 전체 이미지 좌표계 기준
        좌상단 오프셋 (위치 통계 보정용).
        """
        if self._img_data is None:
            return None, 0, 0
        ih, iw = self._img_data.shape[:2]

        def _clip_box(x0, y0, x1, y1):
            ix0 = max(0, min(iw, int(min(x0, x1))))
            iy0 = max(0, min(ih, int(min(y0, y1))))
            ix1 = max(0, min(iw, int(max(x0, x1))))
            iy1 = max(0, min(ih, int(max(y0, y1))))
            sub = self._img_data[iy0:iy1, ix0:ix1]
            return (sub, ix0, iy0) if sub.size > 0 else None

        # 1. 전문 ROI 선택 우선
        sel_id = self.view.interactions._selected_roi_id
        if sel_id is not None and sel_id != -1:
            roi = self.view.interactions._rois.get(sel_id)
            if roi is not None:
                res = _clip_box(*roi.get_points())
                if res is not None:
                    return res

        # 2. 기본 드래그 ROI
        roi = self._state.selected_roi
        if not roi.isNull() and roi.width() >= 1 and roi.height() >= 1:
            res = _clip_box(roi.x(), roi.y(),
                            roi.x() + roi.width(), roi.y() + roi.height())
            if res is not None:
                return res

        # 3. 전체 이미지
        return self._img_data, 0, 0

    def _open_online_stats(self):
        """Online Statistics 팝업 열기 (이미 열려 있으면 앞으로 가져오기)."""
        from ui.viewer_v2.online_stats_dialog import OnlineStatsDialog
        if self._online_stats_dlg is None:
            self._online_stats_dlg = OnlineStatsDialog(self.get_active_region, parent=self)
            self._online_stats_dlg.finished.connect(
                lambda _=None: setattr(self, "_online_stats_dlg", None))
        self._online_stats_dlg.show()
        self._online_stats_dlg.raise_()
        self._online_stats_dlg.activateWindow()

    # ── ROI 핸들러 ───────────────────────────────────────────

    def _on_roi_added(self, roi):
        """신규 ROI 생성 시 패널 목록에 추가"""
        self.roi_panel.add_roi(roi.roi_id, roi.label(), roi.color)
        # 생성된 ROI의 수정 시그널 연결 (필요시 데이터 재계산 트리거)
        roi.modified.connect(lambda: self._on_roi_modified(roi.roi_id))

    def _on_roi_modified(self, roi_id):
        """ROI 드래그/리사이즈 후 현재 선택된 ROI면 분석 데이터 재갱신"""
        sel_id = self.view.interactions._selected_roi_id
        if roi_id == sel_id:
            roi = self.view.interactions._rois.get(roi_id)
            if roi and self._img_data is not None:
                self._compute_and_emit_roi(roi_id, roi)

    def _on_roi_list_selected(self, roi_id):
        """패널 목록에서 선택 시 뷰어 아이템 선택"""
        self.view.interactions._select_roi(roi_id)

    def _on_roi_list_deleted(self, roi_id):
        """패널에서 삭제 시 뷰어 아이템 및 리스트 행 제거"""
        self.view.interactions.delete_roi(roi_id)
        self.roi_panel.remove_roi(roi_id)

    def _on_roi_list_goto(self, roi_id):
        """패널 📍 버튼 클릭 시 해당 ROI로 뷰 이동"""
        roi = self.view.interactions._rois.get(roi_id)
        if roi:
            pts = roi.get_points()
            cx, cy = (pts[0]+pts[2])/2, (pts[1]+pts[3])/2
            self.view.centerOn(cx, cy)

    def _on_cmap_changed(self, name: str):
        print(f"[ViewerV2] UI Combo changed -> {name}")
        self._state.colormap = name.lower()
        # 상태 변경 후 즉시 강제 갱신 (시그널이 씹힐 경우를 대비한 2중 장치)
        self._refresh_display()

    def _set_roi_mode(self, mode: str | None):
        """ROI 그리기 모드 설정 및 버튼 상태 관리"""
        for btn in self.roi_btns:
            btn.blockSignals(True)
            if mode is None:
                btn.setChecked(btn == self.btn_select)
            elif mode == 'line':
                btn.setChecked(btn == self.btn_roi_line)
            elif mode == 'box':
                btn.setChecked(btn == self.btn_roi_box)
            elif mode == 'histogram':
                btn.setChecked(btn == self.btn_roi_hist)
            btn.blockSignals(False)
        
        self._state.roi_mode = mode
        self.view.interactions.set_roi_mode(mode)
        
        # 모든 ROI 아이템들의 이동/커서 상태 업데이트
        for item in self.view._scene.items():
            if hasattr(item, 'set_interaction_mode'):
                item.set_interaction_mode(mode)

    def _toggle_roi_panel(self, checked: bool):
        self.roi_panel.setVisible(checked)

    def _on_state_colormap_changed(self, name: str):
        print(f"[ViewerV2] State Colormap changed to: {name}")
        # UI 동기화 (다른 탭에서 변경된 경우 등)
        target_idx = -1
        for i in range(self.combo_cmap.count()):
            if self.combo_cmap.itemText(i).lower() == name.lower():
                target_idx = i
                break
        
        if target_idx >= 0:
            self.combo_cmap.blockSignals(True)
            self.combo_cmap.setCurrentIndex(target_idx)
            self.combo_cmap.blockSignals(False)
            
        # 히스토그램 위젯 및 화면 갱신
        if self._img_data is not None:
            try:
                self._hist_widget.update_image(self._img_data, name)
                self._refresh_display()
            except Exception as e:
                print(f"[ViewerV2:Error] Colormap sync failed: {e}")
                print(f"[ViewerV2:Error] Colormap sync failed: {e}")

    def _on_ui_range_changed(self, vmin, vmax):
        """UI(Histogram Slider)에서 직접 조절한 경우 -> 상태 업데이트"""
        self._state.update_range(vmin, vmax)

    def _on_state_range_changed(self, vmin, vmax):
        """상태(ViewerState)가 변한 경우 (ROI 계산, 타 탭 동기화 등) -> UI 동기화"""
        self._hist_widget.set_vrange(vmin, vmax)

    def _on_point_selected(self, pos):
        if self._img_data is not None:
            # ROI 제거 (포인트 모드 우선)
            self._state.update_roi(QRectF())
            
            ix, iy = int(pos.x()), int(pos.y())
            xp, yp = ImageProvider.get_point_profile(self._img_data, ix, iy)
            self.ruler_system.set_profiles(xp, yp)

    def _on_roi_selected(self, roi_id: int | None):
        """뷰어에서 ROI 선택 시 패널 하이라이트 + active 강조 + 데이터 분석"""
        if roi_id is None or roi_id == -1:
            # -1 은 기본 드래그 ROI (_on_basic_roi_updated 에서 처리)
            if roi_id is None:
                self.roi_panel.clear_selection()
            return

        self.roi_panel.select_roi(roi_id)
        roi = self.view.interactions._rois.get(roi_id)
        if roi is None or self._img_data is None:
            return

        # ── ① active 시각 강조 (ROI 아이템 색상 + 패널 배지) ──────────
        role = 'hist' if roi.roi_type == 'Hist' else 'profile'
        for rid, r in self.view.interactions._rois.items():
            if hasattr(r, 'set_active_profile'):
                r.set_active_profile(role != 'hist' and rid == roi_id)
            if hasattr(r, 'set_active_hist'):
                r.set_active_hist(role == 'hist' and rid == roi_id)
        self.roi_panel.set_active_roi(roi_id, role)

        # ── ② 분석 데이터 emit ──────────────────────────────────────
        self._compute_and_emit_roi(roi_id, roi)

    def _compute_and_emit_roi(self, roi_id: int, roi):
        """선택된 ROI 기반 분석 계산 + 시그널 emit (선택·수정 공통 경로)"""
        if self._img_data is None:
            return
        x0, y0, x1, y1 = roi.get_points()
        self._state.toggle_crosshair(False)

        if roi.roi_type == 'Line':
            prof = ImageProvider.get_line_profile(self._img_data, x0, y0, x1, y1)
            self.profile_updated.emit(prof, f"ROI #{roi_id} (Line)")
            # Ruler 는 Line 이어도 영역 X/Y 평균으로 표시
            rxp, ryp = ImageProvider.get_roi_profile(self._img_data, x0, y0, x1, y1)
            self.ruler_system.set_profiles(rxp, ryp)

        elif roi.roi_type == 'Box':
            xp, yp = ImageProvider.get_roi_profile(self._img_data, x0, y0, x1, y1)
            self.multi_profile_updated.emit(xp, yp)
            self.ruler_system.set_profiles(xp, yp)
            sub = self._img_data[int(min(y0, y1)):int(max(y0, y1)),
                                 int(min(x0, x1)):int(max(x0, x1))]
            if sub.size > 0:
                counts, bin_edges = np.histogram(sub, bins=256)
                self.histogram_updated.emit(counts, bin_edges)

        elif roi.roi_type == 'Hist':
            sub = self._img_data[int(min(y0, y1)):int(max(y0, y1)),
                                 int(min(x0, x1)):int(max(x0, x1))]
            if sub.size > 0:
                counts, bin_edges = np.histogram(sub, bins=256)
                self.histogram_updated.emit(counts, bin_edges)
            # Hist ROI 선택 시 Ruler 는 비움 (히스토그램 전용 ROI)
            self.ruler_system.set_profiles(np.array([]), np.array([]))

        if self.btn_roi_range.isChecked():
            self._apply_roi_range_with_pts(x0, y0, x1, y1)

    def _on_basic_roi_updated(self, rect: QRectF):
        """기본 드래그 ROI 변경 시 실시간 프로파일 업데이트"""
        if rect.isNull() or self._img_data is None:
            return
            
        # 기본 ROI가 활성화되면 십자선/전문ROI 선택 해제
        self._state.toggle_crosshair(False)
        
        x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
        xp, yp = ImageProvider.get_roi_profile(self._img_data, x0, y0, x1, y1)
        
        if xp.size > 0:
            self.ruler_system.set_profiles(xp, yp)
            self.multi_profile_updated.emit(xp, yp)
            
            # 기본 ROI에 대해서도 히스토그램 계산 후 전송
            ix0, ix1 = int(min(x0, x1)), int(max(x0, x1))
            iy0, iy1 = int(min(y0, y1)), int(max(y0, y1))
            sub = self._img_data[iy0:iy1, ix0:ix1]
            if sub.size > 0:
                counts, bin_edges = np.histogram(sub, bins=256)
                self.histogram_updated.emit(counts, bin_edges)
            
        if self.btn_roi_range.isChecked():
            self._apply_roi_range_with_pts(x0, y0, x1, y1)


    def _apply_roi_range_with_pts(self, x0, y0, x1, y1):
        if self._img_data is not None:
            ix0, ix1 = int(min(x0, x1)), int(max(x0, x1))
            iy0, iy1 = int(min(y0, y1)), int(max(y0, y1))
            sub = self._img_data[iy0:iy1, ix0:ix1]
            if sub.size > 0:
                vmin, vmax = float(np.min(sub)), float(np.max(sub))
                self._state.update_range(vmin, vmax)

    def _on_reset_requested(self):
        try:
            # 1. 상태 리셋
            self._state.update_roi(QRectF())
            self._state.toggle_crosshair(False)
            
            # 2. 데이터 리셋 (PlotPanel만 비움)
            self.profile_updated.emit(np.array([]), "Cleared")
            self.multi_profile_updated.emit(np.array([]), np.array([]))
            self.histogram_updated.emit(np.array([]), np.array([]))
            
            # 3. 위젯 내부(Ruler)는 센스있게 전체 이미지 X/Y 평균으로 복구
            if self._img_data is not None:
                x_prof = np.mean(self._img_data, axis=0)
                y_prof = np.mean(self._img_data, axis=1)
                self.ruler_system.set_profiles(x_prof, y_prof)
                
            self._update_info_text()
            print("[ViewerV2] Reset: Ruler restored to full X/Y average.")
        except Exception as e:
            print(f"[ViewerV2:Error] _on_reset_requested failed: {e}")

    def _update_info_text(self):
        try:
            cx, cy, cv = self._last_cursor
            stats = self._last_stats
            
            # 1. 커서 및 기본 정보
            base_txt = f" Cursor: ({cx:4d}, {cy:4d}) Val: {cv:6.1f} | {self._state.bit_depth}-bit"
            
            # 2. 도구 상태 (ROI or Point)
            roi = self._state.selected_roi
            if not roi.isNull() and roi.width() > 0:
                tool_txt = f" | ROI: [{int(roi.x())}, {int(roi.y())}, {int(roi.width())}x{int(roi.height())}]"
            elif self._state.crosshair_visible and self._state.crosshair_x >= 0:
                tool_txt = f" | Point: ({int(self._state.crosshair_x)}, {int(self._state.crosshair_y)})"
            else:
                tool_txt = " | Mode: Global"

            # 3. 통계 및 가시 범위
            stat_txt = f" | Stats: {stats[0]:.0f}~{stats[1]:.0f} (Avg:{stats[2]:.1f})"
            range_txt = f" | Range: [{self._state.vmin:.0f} ~ {self._state.vmax:.0f}]"
            
            # 4. FPS 표시
            #  - Render: set_image() 호출 빈도(화면에 실제 표시되는 프레임레이트, 드랍 후)
            #  - RX    : 카메라에서 수신 중인 프레임레이트(드랍 전). 외부(라이브 컨트롤러)가 set_rx_fps 로 주입.
            fps_txt = ""
            import time
            if getattr(self, "_current_fps", 0.0) > 0.0:
                if time.time() - getattr(self, "_last_fps_time", 0.0) < 1.5:
                    fps_txt += f" | Render {self._current_fps:.1f} fps"
                else:
                    self._current_fps = 0.0
            rx_fps = getattr(self, "_rx_fps", 0.0)
            if rx_fps > 0.0:
                fps_txt += f" | RX {rx_fps:.1f} fps"

            self.info_bar.setText(base_txt + tool_txt + stat_txt + range_txt + fps_txt)
        except Exception as e:
            print(f"[ViewerV2:Error] _update_info_text failed: {e}")

    def set_rx_fps(self, fps: float) -> None:
        """카메라에서 수신 중인 실제 프레임레이트(드랍 전)를 하단 info_bar 에 표시한다.

        뷰어 자체는 표시된 프레임(set_image)만 보므로 수신 레이트를 알 수 없다.
        DeepAlign 라이브 컨트롤러가 _on_hub_live_frame(수신마다 호출) 기준으로 측정해 주입한다.
        0 이하이면 표시하지 않는다 (라이브 종료/비수신 상태)."""
        try:
            self._rx_fps = max(0.0, float(fps))
        except (TypeError, ValueError):
            self._rx_fps = 0.0
        self._update_info_text()

    def _refresh_display(self, fit=False):
        """화면 갱신 요청 (스로틀링 적용: 약 30ms 대기 후 렌더링)"""
        self._pending_fit = self._pending_fit or fit
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(30) # 초당 최대 ~33회 렌더링으로 제한

    def _do_refresh(self):
        """실제 무거운 이미지 변환 및 렌더링 작업 수행"""
        if self._img_data is None: 
            return
            
        pixmap = ImageProvider.get_display_pixmap(
            self._img_data, 
            self._state.colormap, 
            self._state.vmin, 
            self._state.vmax
        )
        w, h = self._state.img_width, self._state.img_height
        self.view.set_image(pixmap, w, h, self._img_data)
        
        if self._pending_fit:
            self.view.fit_in_view()
            self._pending_fit = False

    def _update_layout_size(self, side_width, top_height):
        self.corner.setFixedSize(side_width, top_height)
