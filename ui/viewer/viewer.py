"""ui/viewer/viewer.py — ImageViewer: main composite widget."""
from __future__ import annotations

import numpy as np
from ui.viewer.ruler import RulerWidget
from ui.viewer.graphics_view import ImageGraphicsView, _RangePopup
from ui.colormap_utils import apply_colormap, ndarray_to_qpixmap
from ui.histogram_range_widget import HistogramRangeWidget
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsLineItem, QGraphicsRectItem,
    QSizePolicy, QToolButton, QPushButton,
    QListWidget, QListWidgetItem, QApplication, QMenu,
)
from PyQt6.QtCore import pyqtSignal, Qt, QRectF, QTimer
from PyQt6.QtGui import (
    QPixmap, QImage, QPen, QColor, QBrush, QPainter,
    QWheelEvent, QMouseEvent, QFont, QTransform
)
from typing import Optional, Union


# ─────────────────────────────────────────────────────────────────────────────
# 메인 ImageViewer 위젯
# ─────────────────────────────────────────────────────────────────────────────

class ImageViewer(QWidget):
    def closeEvent(self, event):
        # 진행 중인 worker 시그널 끊기 — finished.connect(deleteLater)가 자율 정리
        if self._colormap_worker is not None:
            try:
                self._colormap_worker.colormap_applied.disconnect()
            except Exception:
                pass
            self._colormap_worker = None
        super().closeEvent(event)
    # 시그널
    line_profile_updated = pyqtSignal(object, str)
    box_profile_updated  = pyqtSignal(object, object, str)
    histogram_updated    = pyqtSignal(object, object)
    pixel_info_updated   = pyqtSignal(int, int, float)
    range_changed        = pyqtSignal(object, object)   # (vmin|None, vmax|None)
    colormap_changed     = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_image: np.ndarray | None = None
        self._current_cmap = 'off'
        self._crosshair_color = '#ff0000'
        self._last_click_info = None  # (ix, iy, val)
        self._sel_box_rect: tuple | None = None   # (x0,y0,x1,y1) 이미지 좌표
        self._roi_line_pts = None
        self._roi_box_pts  = None
        self._roi_hist_pts = None
        self._roi_mode = None
        # 패널에 연결된 ROI id 추적
        self._active_profile_roi_id: int | None = None
        self._active_hist_roi_id:    int | None = None
        self._last_profile_t: float = 0.0   # profile throttle용 타임스탬프
        self._colormap_worker = None
        self._pending_workers: set = set()   # GC 방지용 실행중 워커 참조
        self._display_vmin: float | None = None
        self._display_vmax: float | None = None
        self._external_render_control = False  # True면 외부(예: LiveTab) 렌더 파이프라인 사용
        self._roi_range_mode  = False            # ROI 영역 기준 컬러맵 범위 자동 설정
        self._pre_roi_range   = (None, None)     # ROI Range 진입 전 저장값
        self._rotation_k: int = 0              # np.rot90 k 값 (0/1/2/3 → 0°/90°/180°/270°)
        self._range_debounce = QTimer()
        self._range_debounce.setSingleShot(True)
        self._range_debounce.setInterval(50)
        self._range_debounce.timeout.connect(lambda: self._refresh_pixmap(fit=False))
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 툴바 ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 4, 4, 4)
        toolbar.setSpacing(6)

        # ROI 콤보
        roi_label = QLabel("ROI:")
        roi_label.setStyleSheet("color: #a0a0b0; font-size: 11px;")
        toolbar.addWidget(roi_label)

        self.roi_combo = QComboBox()
        self.roi_combo.addItems(["None", "Line Profile", "Box Profile",
                                  "X Profile", "Y Profile", "Histogram"])
        self.roi_combo.setFixedWidth(130)
        self.roi_combo.currentTextChanged.connect(self._on_roi_mode_changed)
        toolbar.addWidget(self.roi_combo)

        # 컬러맵
        cmap_label = QLabel("  Colormap:")
        cmap_label.setStyleSheet("color: #a0a0b0; font-size: 11px;")
        toolbar.addWidget(cmap_label)

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["Off", "jet", "viridis", "plasma", "hot", "grey"])
        self.cmap_combo.setFixedWidth(90)
        self.cmap_combo.currentTextChanged.connect(self._on_cmap_changed)
        toolbar.addWidget(self.cmap_combo)

        # 크로스헤어 토글
        self.btn_crosshair = QToolButton()
        self.btn_crosshair.setText("✛ Crosshair")
        self.btn_crosshair.setCheckable(True)
        self.btn_crosshair.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:checked {
                background: #e94560; color: #fff; border-color: #e94560;
            }
            QToolButton:hover { border-color: #e94560; }
        """)
        self.btn_crosshair.toggled.connect(self._on_crosshair_toggled)
        toolbar.addWidget(self.btn_crosshair)

        # 크로스헤어 색상 콤보
        self.crosshair_color_combo = QComboBox()
        self.crosshair_color_combo.setFixedWidth(85)
        self._crosshair_colors = {
            'Red':    '#ff0000',
            'White':  '#ffffff',
            'Yellow': '#ffff00',
            'Cyan':   '#00ffff',
            'Black':  '#000000',
            'Green':  '#00ff00',
        }
        for name in self._crosshair_colors:
            self.crosshair_color_combo.addItem(name)
        self.crosshair_color_combo.currentTextChanged.connect(self._on_crosshair_color_changed)
        toolbar.addWidget(self.crosshair_color_combo)

        # 이미지 내보내기 버튼 (P3-2)
        self.btn_export_img = QToolButton()
        self.btn_export_img.setText("📤")
        self.btn_export_img.setToolTip("처리된 이미지 PNG/TIFF 저장")
        self.btn_export_img.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:hover { border-color: #4ecdc4; color: #4ecdc4; }
        """)
        self.btn_export_img.clicked.connect(self._export_image)
        toolbar.addWidget(self.btn_export_img)

        # Range 슬라이더 패널 토글
        self.btn_range = QToolButton()
        self.btn_range.setText("📊 Range")
        self.btn_range.setCheckable(True)
        self.btn_range.setToolTip("컬러맵 Min/Max 히스토그램 슬라이더 표시")
        self.btn_range.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:checked {
                background: #1a3a20; color: #4ecdc4; border-color: #4ecdc4;
            }
            QToolButton:hover { border-color: #4ecdc4; }
        """)
        self.btn_range.toggled.connect(self._on_range_panel_toggled)
        toolbar.addWidget(self.btn_range)

        # ROI Range 토글 — 선택 사각형 내 min/max → 컬러맵 범위
        self.btn_roi_range = QToolButton()
        self.btn_roi_range.setText("🎯 ROI Range")
        self.btn_roi_range.setCheckable(True)
        self.btn_roi_range.setToolTip(
            "선택 영역(드래그) 내 픽셀 min/max를 컬러맵 범위로 자동 설정\n"
            "해제 시 이전 범위로 복원"
        )
        self.btn_roi_range.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:checked {
                background: #1a2a10; color: #ffe66d; border-color: #ffe66d;
            }
            QToolButton:hover { border-color: #ffe66d; }
        """)
        self.btn_roi_range.toggled.connect(self._on_roi_range_toggled)
        toolbar.addWidget(self.btn_roi_range)

        # ROI 목록 토글
        self.btn_roi_list_toggle = QToolButton()
        self.btn_roi_list_toggle.setText("📋 ROI 목록")
        self.btn_roi_list_toggle.setCheckable(True)
        self.btn_roi_list_toggle.setToolTip("그려진 ROI 목록 표시 / 삭제")
        self.btn_roi_list_toggle.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:checked { background: #1a2840; color: #ffe66d; border-color: #ffe66d; }
            QToolButton:hover { border-color: #ffe66d; }
        """)
        self.btn_roi_list_toggle.toggled.connect(self._on_roi_list_toggled)
        toolbar.addWidget(self.btn_roi_list_toggle)

        toolbar.addStretch()

        # 회전
        rotate_label = QLabel("↻")
        rotate_label.setStyleSheet("color: #a0a0b0; font-size: 13px; margin-right:0px;")
        toolbar.addWidget(rotate_label)
        self.rotate_combo = QComboBox()
        self.rotate_combo.addItems(["0°", "90°", "180°", "270°"])
        self.rotate_combo.setFixedWidth(58)
        self.rotate_combo.setStyleSheet("""
            QComboBox {
                background:#080e1e; border:1px solid #1a3a60; color:#d0deff;
                border-radius:3px; font-size:11px; padding:1px 4px; min-height:20px;
            }
            QComboBox::drop-down { border:none; width:14px; }
            QComboBox QAbstractItemView {
                background:#0a1428; color:#d0deff;
                selection-background-color:#1a3a60;
            }
        """)
        self.rotate_combo.currentIndexChanged.connect(self._on_rotation_changed)
        toolbar.addWidget(self.rotate_combo)

        # 포화 경고 레이블 (P3-3)
        self.lbl_saturated = QLabel("⚠ SATURATED")
        self.lbl_saturated.setStyleSheet(
            "color: #e94560; font-size: 11px; font-weight: bold; "
            "background: #3a0010; border-radius: 3px; padding: 1px 6px;"
        )
        self.lbl_saturated.setVisible(False)
        toolbar.addWidget(self.lbl_saturated)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar)
        toolbar_widget.setStyleSheet("background: #16213e;")
        layout.addWidget(toolbar_widget)

        # ── 정보 바 (툴바 아래 두 번째 줄) ──────────────────────────
        info_bar = QWidget()
        info_bar.setFixedHeight(22)
        info_bar.setStyleSheet(
            "background: #0d1422; border-bottom: 1px solid #0f3460;"
        )
        info_row = QHBoxLayout(info_bar)
        info_row.setContentsMargins(6, 0, 6, 0)
        info_row.setSpacing(0)

        _ib = (
            "color: #4a7a9a; font-family: 'Courier New';"
            " font-size: 10px; padding: 0 6px;"
        )
        _ib_val = (
            "color: #a0c8e0; font-family: 'Courier New';"
            " font-size: 10px; padding: 0 6px;"
        )
        _btn_zoom = (
            "QPushButton { background: transparent; color: #3a6a8a;"
            " border: 1px solid #1a3a55; border-radius: 2px;"
            " font-family: 'Courier New'; font-size: 10px; padding: 0 5px; }"
            "QPushButton:hover { color: #4ecdc4; border-color: #4ecdc4; }"
        )

        # 줌 % 표시
        lbl_zoom_icon = QLabel("🔍")
        lbl_zoom_icon.setStyleSheet(_ib)
        info_row.addWidget(lbl_zoom_icon)

        self._lbl_zoom_pct = QLabel("100%")
        self._lbl_zoom_pct.setFixedWidth(46)
        self._lbl_zoom_pct.setStyleSheet(_ib_val)
        info_row.addWidget(self._lbl_zoom_pct)

        # FIT / 1:1 버튼
        btn_fit = QPushButton("FIT")
        btn_fit.setFixedSize(34, 16)
        btn_fit.setStyleSheet(_btn_zoom)
        btn_fit.clicked.connect(self.autoRange)
        info_row.addWidget(btn_fit)

        btn_1x = QPushButton("1:1")
        btn_1x.setFixedSize(34, 16)
        btn_1x.setStyleSheet(_btn_zoom)
        btn_1x.clicked.connect(self._zoom_actual)
        info_row.addWidget(btn_1x)

        # 구분선
        def _vsep():
            sep = QLabel("|")
            sep.setStyleSheet("color: #1a3a55; padding: 0 4px;")
            return sep
        info_row.addWidget(_vsep())

        # 마우스 커서 좌표·값
        lbl_cur_icon = QLabel("✛")
        lbl_cur_icon.setStyleSheet(_ib)
        info_row.addWidget(lbl_cur_icon)

        self._lbl_cursor = QLabel("X:—  Y:—  Val:—")
        self._lbl_cursor.setFixedWidth(200)
        self._lbl_cursor.setStyleSheet(_ib_val)
        info_row.addWidget(self._lbl_cursor)

        info_row.addWidget(_vsep())

        # 클릭 핀 좌표·값
        lbl_pin_icon = QLabel("📍")
        lbl_pin_icon.setStyleSheet(_ib)
        info_row.addWidget(lbl_pin_icon)

        self._lbl_pin = QLabel("X:—  Y:—  Val:—")
        self._lbl_pin.setFixedWidth(200)
        self._lbl_pin.setStyleSheet(_ib_val)
        info_row.addWidget(self._lbl_pin)

        info_row.addWidget(_vsep())

        # 선택 박스 크기
        lbl_sel_icon = QLabel("▣")
        lbl_sel_icon.setStyleSheet(_ib)
        info_row.addWidget(lbl_sel_icon)

        self._lbl_sel = QLabel("—")
        self._lbl_sel.setFixedWidth(140)
        self._lbl_sel.setStyleSheet(_ib_val)
        info_row.addWidget(self._lbl_sel)

        info_row.addStretch()

        # 이미지 해상도 표시
        self._lbl_img_size = QLabel("—×—")
        self._lbl_img_size.setStyleSheet(_ib)
        info_row.addWidget(self._lbl_img_size)

        layout.addWidget(info_bar)

        # 기존 pixel_label (하위호환 — 숨김 처리, 코드에서 setText 호출은 유지)
        self.pixel_label = QLabel()
        self.pixel_label.setVisible(False)

        # ── ROI 목록 패널 (토글) ──
        self._roi_list_panel = QWidget()
        self._roi_list_panel.setVisible(False)
        self._roi_list_panel.setFixedHeight(90)
        self._roi_list_panel.setStyleSheet(
            "background:#0d1828; border-bottom:1px solid #0f3460;"
        )
        roi_panel_row = QHBoxLayout(self._roi_list_panel)
        roi_panel_row.setContentsMargins(6, 4, 6, 4)
        roi_panel_row.setSpacing(6)

        self._roi_list_widget = QListWidget()
        self._roi_list_widget.setStyleSheet("""
            QListWidget {
                background:#080e1e; color:#c0d0ff;
                border:1px solid #0f3460; font-family:'Courier New'; font-size:11px;
            }
            QListWidget::item { padding:2px 4px; }
            QListWidget::item:selected { background:#1a3a60; color:#ffe66d; }
        """)
        roi_panel_row.addWidget(self._roi_list_widget, 1)

        _roi_del_btn = (
            "QPushButton { background:#0d1a28; color:#e94560;"
            "border:1px solid #e94560; border-radius:3px;"
            "font-size:11px; padding:3px 8px; }"
            "QPushButton:hover { background:#2a1020; }"
        )
        roi_btn_col = QVBoxLayout()
        roi_btn_col.setSpacing(4)
        roi_btn_col.setContentsMargins(0, 0, 0, 0)
        self._btn_del_roi = QPushButton("선택 삭제")
        self._btn_del_all_roi = QPushButton("전체 삭제")
        self._btn_del_roi.setFixedWidth(80)
        self._btn_del_all_roi.setFixedWidth(80)
        self._btn_del_roi.setStyleSheet(_roi_del_btn)
        self._btn_del_all_roi.setStyleSheet(_roi_del_btn)
        self._btn_del_roi.clicked.connect(self._delete_selected_roi)
        self._btn_del_all_roi.clicked.connect(self._delete_all_rois_ui)
        roi_btn_col.addWidget(self._btn_del_roi)
        roi_btn_col.addWidget(self._btn_del_all_roi)
        roi_btn_col.addStretch()
        roi_panel_row.addLayout(roi_btn_col)
        layout.addWidget(self._roi_list_panel)

        # ── 눈금자 + 뷰어 영역 ──
        viewer_area = QWidget()
        viewer_layout = QVBoxLayout(viewer_area)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(0)

        # 상단 행: 코너 + X 눈금자
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)

        self._corner = QWidget()
        self._corner.setFixedSize(48, 24)
        self._corner.setStyleSheet("background: #16213e;")
        top_row.addWidget(self._corner)

        self._ruler_x = RulerWidget('horizontal')
        top_row.addWidget(self._ruler_x)
        viewer_layout.addLayout(top_row)

        # 하단 행: Y 눈금자 + 뷰어
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(0)

        self._ruler_y = RulerWidget('vertical')
        bottom_row.addWidget(self._ruler_y)

        self._view = ImageGraphicsView()
        self._view.mouse_moved.connect(self._on_mouse_moved)
        self._view.mouse_clicked.connect(self._on_mouse_clicked)
        self._view.sel_box_changed.connect(self._on_sel_box_changed)
        self._view.roi_drawn.connect(self._on_roi_drawn)
        self._view.scale_changed.connect(self._on_scale_changed)
        # ROI 선택/추가 콜백
        self._view.on_roi_selected = self._on_roi_selected
        self._view.on_roi_added    = self._on_roi_added
        self._view.horizontalScrollBar().valueChanged.connect(
            lambda _: self._on_scale_changed(
                self._view._scale,
                self._view.horizontalScrollBar().value(),
                self._view.verticalScrollBar().value()
            )
        )
        self._view.verticalScrollBar().valueChanged.connect(
            lambda _: self._on_scale_changed(
                self._view._scale,
                self._view.horizontalScrollBar().value(),
                self._view.verticalScrollBar().value()
            )
        )
        bottom_row.addWidget(self._view)
        viewer_layout.addLayout(bottom_row)

        layout.addWidget(viewer_area)

        # ── 히스토그램 Range 슬라이더 팝업 ──
        self._hist_range_widget = HistogramRangeWidget()
        self._hist_range_widget.range_changed.connect(self._on_range_changed)
        self._hist_range_widget.range_changed.connect(
            lambda vmin, vmax: self.range_changed.emit(vmin, vmax)
        )
        self._range_popup = _RangePopup(self._hist_range_widget, parent=self)
        self._range_popup.closed.connect(lambda: self.btn_range.setChecked(False))

        # ── 우클릭 컨텍스트 메뉴 ──
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_context_menu)

        # ── 키보드 단축키 ──────────────────────────────────────────
        from PyQt6.QtGui import QShortcut, QKeySequence
        _ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        QShortcut(QKeySequence("F"),      self, activated=self.autoRange,          context=_ctx)
        QShortcut(QKeySequence("1"),      self, activated=self._zoom_actual,        context=_ctx)
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self._copy_to_clipboard,  context=_ctx)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._export_image,       context=_ctx)

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def _update_img_size_label(self):
        if self._current_image is not None:
            h, w = self._current_image.shape[:2]
            self._lbl_img_size.setText(f"{w}×{h}px")
        else:
            self._lbl_img_size.setText("—×—")

    def set_image(self, image: np.ndarray):
        """프레임 전환 (뷰 유지)"""
        self._current_image = image
        self._update_img_size_label()
        self._refresh_pixmap(fit=False)
        self._recompute_profile()
        self._update_ruler_profiles()
        if self._hist_range_widget.isVisible():
            self._hist_range_widget.update_image(image, self._current_cmap)

    def set_image_first(self, image: np.ndarray):
        """첫 로드 (뷰 fit) — range 리셋"""
        self._current_image = image
        self._update_img_size_label()
        self._display_vmin = None
        self._display_vmax = None
        if self._hist_range_widget.isVisible():
            self._hist_range_widget.update_image(image, self._current_cmap,
                                                 reset_range=True)
        self._refresh_pixmap(fit=True)
        self._update_ruler_profiles()

    def set_live_frame(self, rgb: np.ndarray, fit: bool = False):
        """라이브 스트리밍 전용 — ColorMapWorker 우회, GUI 스레드에서 직접 변환.
        rgb: uint8 H×W×3 (또는 H×W grayscale)
        프로파일/히스토그램은 최대 10fps로 throttle — scipy/numpy 과부하 방지.
        """
        import time
        from PyQt6.QtGui import QImage, QPixmap
        # 라이브에서는 set_source_image(raw)가 먼저 들어오면 그 값을 유지한다.
        # (프로파일/룰러/픽셀값은 raw 기준)
        if self._current_image is None:
            self._current_image = rgb
            self._update_img_size_label()
        disp = np.rot90(rgb, k=self._rotation_k) if self._rotation_k else rgb
        h, w = disp.shape[:2]
        if disp.ndim == 3 and disp.shape[2] == 3:
            disp_c = np.ascontiguousarray(disp)
            qimg = QImage(disp_c.data, w, h, w * 3, QImage.Format.Format_RGB888)
        else:
            disp_c = np.ascontiguousarray(disp)
            qimg = QImage(disp_c.data, w, h, w, QImage.Format.Format_Grayscale8)
        self._view.set_pixmap(QPixmap.fromImage(qimg.copy()), w, h, fit=fit)
        # ROI 프로파일/히스토그램 + 룰러: 최대 10fps (0.1초 간격)
        now = time.monotonic()
        if now - self._last_profile_t >= 0.1:
            self._last_profile_t = now
            self._recompute_profile()
            self._update_ruler_profiles()

    def set_source_image(self, img: np.ndarray) -> None:
        """라이브 모드에서 colormap/export 기준이 될 원본 grayscale 이미지를 갱신.
        set_live_frame은 display용 RGB를 받으므로, 원본은 별도로 저장해야
        _refresh_pixmap/_export_image에서 이중 colormap 적용이 발생하지 않는다."""
        self._current_image = img

    def set_centroid_overlay(self, cx: float, cy: float) -> None:
        """센트로이드 위치에 십자 마커를 씬 오버레이로 표시 (픽셀에 굽지 않음)."""
        self.clear_centroid_overlay()
        from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsSimpleTextItem
        pen = QPen(QColor('#4ecdc4'), 2)
        pen.setCosmetic(True)
        arm = 20
        h = QGraphicsLineItem(cx - arm, cy, cx + arm, cy)
        v = QGraphicsLineItem(cx, cy - arm, cx, cy + arm)
        h.setPen(pen); h.setZValue(10)
        v.setPen(pen); v.setZValue(10)
        txt = QGraphicsSimpleTextItem(f"({cx:.1f}, {cy:.1f})")
        txt.setBrush(QBrush(QColor('#4ecdc4')))
        txt.setZValue(10)
        txt.setFlag(txt.GraphicsItemFlag.ItemIgnoresTransformations)
        txt.setPos(cx + 6, cy - 6)
        for item in (h, v, txt):
            self._view._scene.addItem(item)
        self._centroid_overlay_items = [h, v, txt]

    def clear_centroid_overlay(self) -> None:
        """센트로이드 오버레이 제거."""
        for item in getattr(self, '_centroid_overlay_items', []):
            if item.scene():
                self._view._scene.removeItem(item)
        self._centroid_overlay_items = []

    def set_external_render_control(self, enabled: bool) -> None:
        """외부 렌더 파이프라인 사용 여부를 설정한다.

        True  : range/cmap 변경 시 내부 _refresh_pixmap 호출을 생략.
        False : 기존 동작(내부 ColorMapWorker 렌더) 사용.
        """
        self._external_render_control = bool(enabled)

    def set_saturated(self, saturated: bool, sat_ratio: float = 0.0) -> None:
        """포화 경고 오버레이 표시/숨김 (P3-3)."""
        if saturated:
            self.lbl_saturated.setText(f"⚠ SAT {sat_ratio*100:.2f}%")
            self.lbl_saturated.setVisible(True)
        else:
            self.lbl_saturated.setVisible(False)

    def _export_image(self) -> None:
        """현재 display 이미지를 PNG/TIFF로 저장 (P3-2)."""
        if self._current_image is None:
            return
        from PyQt6.QtWidgets import QFileDialog
        from datetime import datetime
        default = f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "이미지 저장", default,
            "PNG (*.png);;TIFF (*.tif *.tiff);;모든 파일 (*)"
        )
        if not path:
            return
        try:
            img = self._current_image
            cmap = self._current_cmap
            if cmap and cmap != 'off':
                rgba = apply_colormap(img, cmap)
            else:
                from core.async_worker import ColorMapWorker
                rgba = ColorMapWorker._to_grayscale_rgba(img)
            pixmap = ndarray_to_qpixmap(rgba)
            pixmap.save(path)
        except Exception as e:
            print(f"[ImageViewer] 이미지 저장 오류: {e}")

    def _refresh_pixmap(self, fit: bool = False):
        if self._current_image is None:
            return
        # 이전 워커: 결과 시그널만 끊어 화면 업데이트 방지.
        # 스레드 자체는 계속 실행되며 finished → _pending_workers 제거 → deleteLater 순으로 자율 정리.
        if self._colormap_worker is not None:
            try:
                self._colormap_worker.colormap_applied.disconnect()
            except Exception:
                pass

        img = self._current_image
        # 회전 적용 (k=1 → 90°CCW, k=2 → 180°, k=3 → 270°CCW)
        if self._rotation_k:
            img = np.rot90(img, k=self._rotation_k)
        cmap = self._current_cmap

        from core.async_worker import ColorMapWorker
        worker = ColorMapWorker(img, cmap,
                                vmin=self._display_vmin, vmax=self._display_vmax)
        self._colormap_worker = worker
        self._pending_workers.add(worker)   # Python 레퍼런스 유지 → GC 방지

        def _cleanup():
            self._pending_workers.discard(worker)

        worker.finished.connect(_cleanup)
        worker.finished.connect(worker.deleteLater)

        def on_colormap_ready(rgba):
            try:
                pixmap = ndarray_to_qpixmap(rgba)
                h, w = img.shape[:2]
                self._view.set_pixmap(pixmap, w, h, fit=fit)
            except Exception as e:
                print(f"[ImageViewer] QPixmap 변환 오류: {e}")
            finally:
                if self._colormap_worker is worker:
                    self._colormap_worker = None

        worker.colormap_applied.connect(on_colormap_ready)
        worker.start()

    def hide_range_popup(self):
        """프로그램적으로 Range 팝업을 닫는다 (탭 전환 시 등)."""
        if hasattr(self, '_range_popup'):
            self._range_popup.hide()
            self.btn_range.setChecked(False)

    # ─────────────────────────────────────────
    # 눈금자 업데이트
    # ─────────────────────────────────────────

    def _on_scale_changed(self, scale: float, x_offset: float, y_offset: float):
        # 줌 % 업데이트
        self._lbl_zoom_pct.setText(f"{scale * 100:.0f}%")

        if self._current_image is None:
            return
        img = self._current_image
        if self._rotation_k:
            img = np.rot90(img, k=self._rotation_k)
        h, w = img.shape[:2]
        x_img_offset = x_offset / max(scale, 0.001)
        y_img_offset = y_offset / max(scale, 0.001)
        self._ruler_x.update_transform(scale, x_img_offset, w)
        self._ruler_y.update_transform(scale, y_img_offset, h)

    def _update_ruler_profiles(self):
        """선택 박스 > 마지막 클릭 위치 > 전체 평균 순으로 룰러 프로파일 전달."""
        if self._current_image is None:
            self._ruler_x.set_profile(None)
            self._ruler_y.set_profile(None)
            return
        img = self._current_image
        H0, W0 = img.shape[:2]
        if self._rotation_k:
            img = np.rot90(img, k=self._rotation_k)
        rH, rW = img.shape[:2]

        # 1순위: 선택 박스
        if self._sel_box_rect is not None:
            if self._update_ruler_profiles_sel(img, rH, rW):
                return

        if self._last_click_info is not None:
            ix0, iy0, _ = self._last_click_info  # 원본 이미지 좌표
            k = self._rotation_k
            if k == 0:
                rix, riy = ix0, iy0
            elif k == 1:   # 90° CCW
                riy, rix = W0 - 1 - ix0, iy0
            elif k == 2:   # 180°
                riy, rix = H0 - 1 - iy0, W0 - 1 - ix0
            else:          # 270° CCW
                riy, rix = ix0, H0 - 1 - iy0
            rix = max(0, min(rW - 1, rix))
            riy = max(0, min(rH - 1, riy))
            if img.ndim == 2:
                x_prof = img[riy, :].astype(np.float32)
                y_prof = img[:, rix].astype(np.float32)
            else:
                x_prof = img[riy, :].mean(axis=-1).astype(np.float32)
                y_prof = img[:, rix].mean(axis=-1).astype(np.float32)
        else:
            if img.ndim == 2:
                x_prof = img.mean(axis=0).astype(np.float32)
                y_prof = img.mean(axis=1).astype(np.float32)
            elif img.ndim == 3:
                x_prof = img.mean(axis=(0, 2)).astype(np.float32)
                y_prof = img.mean(axis=(1, 2)).astype(np.float32)
            else:
                return
        self._ruler_x.set_profile(x_prof, 0)
        self._ruler_y.set_profile(y_prof, 0)

    def _update_ruler_profiles_sel(self, img: np.ndarray, rH: int, rW: int):
        """선택 박스 기준 룰러 프로파일 계산.

        X 룰러: 박스 Y범위(H)만 평균 → 전체 폭 프로파일
        Y 룰러: 박스 X범위(W)만 평균 → 전체 높이 프로파일
        """
        x0, y0, x1, y1 = self._sel_box_rect
        ix0 = max(0, min(int(x0), rW - 1))
        iy0 = max(0, min(int(y0), rH - 1))
        ix1 = max(ix0 + 1, min(int(x1), rW))
        iy1 = max(iy0 + 1, min(int(y1), rH))
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        if img.ndim == 2:
            x_prof = img[iy0:iy1, :].mean(axis=0).astype(np.float32)   # 전체 폭, 박스 H 평균
            y_prof = img[:, ix0:ix1].mean(axis=1).astype(np.float32)   # 전체 높이, 박스 W 평균
        else:
            x_prof = img[iy0:iy1, :].mean(axis=(0, 2)).astype(np.float32)
            y_prof = img[:, ix0:ix1].mean(axis=(1, 2)).astype(np.float32)
        self._ruler_x.set_profile(x_prof, 0)
        self._ruler_y.set_profile(y_prof, 0)
        return True

    # ─────────────────────────────────────────
    # ROI 선택 → 패널 갱신
    # ─────────────────────────────────────────

    def _on_roi_selected(self, roi_id):
        """클릭 또는 신규 드로우로 ROI가 선택됐을 때 해당 타입 패널을 갱신한다."""
        # 목록이 열려있으면 동기화
        if self.btn_roi_list_toggle.isChecked():
            self._refresh_roi_list()
        if roi_id is None:
            return
        roi = self._view.get_roi(roi_id)
        if roi is None or self._current_image is None:
            return
        pts = roi.pts
        roi_type = roi.roi_type   # 'Line' | 'Box' | 'Hist'
        try:
            (x0, y0), (x1, y1) = pts[0], pts[1]
        except (IndexError, TypeError):
            return
        if roi_type == 'Line':
            self._roi_line_pts = pts
            self._roi_box_pts  = None
            self._set_active_roi(roi_id, 'profile')
            self._compute_line_profile(x0, y0, x1, y1)
        elif roi_type == 'Box':
            self._roi_box_pts  = pts
            self._roi_line_pts = None
            self._set_active_roi(roi_id, 'profile')
            self._compute_box_profile(x0, y0, x1, y1)
        elif roi_type == 'Hist':
            self._roi_hist_pts = pts
            self._set_active_roi(roi_id, 'hist')
            self._compute_histogram(x0, y0, x1, y1)

    # ─────────────────────────────────────────
    # ROI 모드 (드로우 타입 선택)
    # ─────────────────────────────────────────

    def _on_roi_mode_changed(self, mode_text: str):
        mode_map = {
            "Line Profile": 'line',
            "Box Profile":  'box',
            "X Profile":    'xprofile',
            "Y Profile":    'yprofile',
            "Histogram":    'histogram',
        }
        self._roi_mode = mode_map.get(mode_text, None)
        # ★ pts 초기화 제거 — 드로우 모드만 변경, 기존 출력 패널 유지
        self._view.set_roi_mode(self._roi_mode)

        # X/Y 프로파일: ROI 없이 전체 이미지로 즉시 계산
        if self._roi_mode == 'xprofile':
            self._compute_x_profile()
        elif self._roi_mode == 'yprofile':
            self._compute_y_profile()

    def _on_roi_drawn(self, mode: str, pts: list):
        if mode == 'line':
            self._roi_line_pts = pts
            (x0,y0),(x1,y1) = pts
            self._compute_line_profile(x0, y0, x1, y1)
        elif mode == 'box':
            self._roi_box_pts = pts
            (x0,y0),(x1,y1) = pts
            self._compute_box_profile(x0, y0, x1, y1)
        elif mode == 'histogram':
            self._roi_hist_pts = pts
            (x0,y0),(x1,y1) = pts
            self._compute_histogram(x0, y0, x1, y1)

    def _set_active_roi(self, roi_id: Optional[int], role: str) -> None:
        """
        지정 role의 active ROI를 업데이트하고 강조 시각을 갱신한다.

        role: 'profile' | 'hist'
        roi_id: None 이면 해당 role의 active 해제.
        외부(analysis_tab 등)에서도 직접 호출 가능.
        """
        if roi_id is None:
            self._view.clear_active_roi(role)
            if role == 'profile':
                self._active_profile_roi_id = None
            else:
                self._active_hist_roi_id = None
            return

        self._view.set_active_roi(roi_id, role)
        if role == 'profile':
            self._active_profile_roi_id = roi_id
        else:
            self._active_hist_roi_id = roi_id

    def _recompute_profile(self):
        """프레임 갱신 시 프로파일·히스토그램 독립 재계산.

        드로우 모드와 무관하게 마지막으로 선택된 ROI 기준으로 표시한다.
        프로파일(line/box)과 히스토그램은 서로 독립적으로 유지된다.
        """
        if self._current_image is None:
            return
        # ── 프로파일 패널: 마지막 선택된 프로파일 ROI 기준 ──
        if self._roi_line_pts:
            (x0, y0), (x1, y1) = self._roi_line_pts
            self._compute_line_profile(x0, y0, x1, y1)
        elif self._roi_box_pts:
            (x0, y0), (x1, y1) = self._roi_box_pts
            self._compute_box_profile(x0, y0, x1, y1)
        elif self._roi_mode == 'xprofile':
            self._compute_x_profile()
        elif self._roi_mode == 'yprofile':
            self._compute_y_profile()
        # ── 히스토그램 패널: 마지막 선택된 히스토그램 ROI 기준 (독립) ──
        if self._roi_hist_pts:
            (x0, y0), (x1, y1) = self._roi_hist_pts
            self._compute_histogram(x0, y0, x1, y1)

    # ─────────────────────────────────────────
    # 프로파일 계산
    # ─────────────────────────────────────────

    def _compute_line_profile(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            from scipy import ndimage
            img = self._current_image.astype(np.float64)
            h, w = img.shape[:2]
            num = max(int(np.hypot(x1-x0, y1-y0)), 2)
            xs = np.linspace(np.clip(x0, 0, w-1), np.clip(x1, 0, w-1), num)
            ys = np.linspace(np.clip(y0, 0, h-1), np.clip(y1, 0, h-1), num)
            profile = ndimage.map_coordinates(img, [ys, xs], order=1)
            self.line_profile_updated.emit(profile, "Line")
        except Exception as e:
            print(f"Line profile error: {e}")

    def _compute_box_profile(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            img = self._current_image
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].astype(np.float64)
            self.box_profile_updated.emit(region.mean(axis=0), region.mean(axis=1), "Box")
        except Exception as e:
            print(f"Box profile error: {e}")

    def _compute_histogram(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            img = self._current_image
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].flatten().astype(np.float64)
            num_bins = min(256, int(np.sqrt(len(region))) + 1)
            counts, bin_edges = np.histogram(region, bins=num_bins)
            self.histogram_updated.emit(counts, bin_edges)
        except Exception as e:
            print(f"Histogram error: {e}")

    def _compute_x_profile(self):
        if self._current_image is None:
            return
        self.line_profile_updated.emit(self._current_image.mean(axis=0), "X Profile")

    def _compute_y_profile(self):
        if self._current_image is None:
            return
        self.line_profile_updated.emit(self._current_image.mean(axis=1), "Y Profile")

    def _compute_line_profile_direct(self, frame: np.ndarray, x0, y0, x1, y1):
        """외부에서 직접 프레임 지정해서 라인 프로파일 계산"""
        try:
            from scipy import ndimage
            img = frame.astype(np.float64)
            h, w = img.shape[:2]
            num = max(int(np.hypot(x1-x0, y1-y0)), 2)
            xs = np.linspace(np.clip(x0, 0, w-1), np.clip(x1, 0, w-1), num)
            ys = np.linspace(np.clip(y0, 0, h-1), np.clip(y1, 0, h-1), num)
            profile = ndimage.map_coordinates(img, [ys, xs], order=1)
            self.line_profile_updated.emit(profile, "Line")
        except Exception as e:
            print(f"Line profile error: {e}")

    def _compute_box_profile_direct(self, frame: np.ndarray, x0, y0, x1, y1):
        try:
            img = frame
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].astype(np.float64)
            self.box_profile_updated.emit(region.mean(axis=0), region.mean(axis=1), "Box")
        except Exception as e:
            print(f"Box profile error: {e}")

    def _compute_histogram_direct(self, frame: np.ndarray, x0, y0, x1, y1):
        try:
            img = frame
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].flatten().astype(np.float64)
            num_bins = min(256, int(np.sqrt(len(region))) + 1)
            counts, bin_edges = np.histogram(region, bins=num_bins)
            self.histogram_updated.emit(counts, bin_edges)
        except Exception as e:
            print(f"Histogram error: {e}")

    # ─────────────────────────────────────────
    # ROI 목록 패널
    # ─────────────────────────────────────────

    def _on_roi_list_toggled(self, checked: bool):
        self._roi_list_panel.setVisible(checked)
        if checked:
            self._refresh_roi_list()

    def _on_roi_added(self, roi):
        if self.btn_roi_list_toggle.isChecked():
            self._refresh_roi_list()

    def _refresh_roi_list(self):
        self._roi_list_widget.clear()
        _icon = {'Line': '━', 'Box': '▭', 'Hist': '▒'}
        for roi_id, roi in self._view._rois.items():
            icon = _icon.get(roi.roi_type, '?')
            try:
                (x0, y0), (x1, y1) = roi.pts[0], roi.pts[1]
                coord = f"  ({int(x0)},{int(y0)})→({int(x1)},{int(y1)})"
            except Exception:
                coord = ""
            item = QListWidgetItem(f"{icon} {roi.roi_type} #{roi_id}{coord}")
            item.setData(Qt.ItemDataRole.UserRole, roi_id)
            self._roi_list_widget.addItem(item)
            if roi_id == self._view._selected_roi_id:
                self._roi_list_widget.setCurrentItem(item)

    def _delete_selected_roi(self):
        item = self._roi_list_widget.currentItem()
        if item is None:
            return
        roi_id = item.data(Qt.ItemDataRole.UserRole)
        if roi_id is None:
            return
        self._view.delete_roi(roi_id)
        if self._active_profile_roi_id == roi_id:
            self._active_profile_roi_id = None
            self._roi_line_pts = None
            self._roi_box_pts  = None
        if self._active_hist_roi_id == roi_id:
            self._active_hist_roi_id = None
            self._roi_hist_pts = None
        self._refresh_roi_list()

    def _delete_all_rois_ui(self):
        self._view.delete_all_rois()
        self._active_profile_roi_id = None
        self._active_hist_roi_id    = None
        self._roi_line_pts = None
        self._roi_box_pts  = None
        self._roi_hist_pts = None
        self._refresh_roi_list()

    # ─────────────────────────────────────────
    # 회전
    # ─────────────────────────────────────────

    def _on_rotation_changed(self, index: int):
        self._rotation_k = index   # 0/1/2/3 → 0°/90°/180°/270°
        if self._current_image is not None:
            self._refresh_pixmap(fit=False)
            self._update_ruler_profiles()

    def set_rotation(self, degrees: int):
        """외부에서 회전 각도를 설정 (0/90/180/270)."""
        k = (degrees // 90) % 4
        self._rotation_k = k
        self.rotate_combo.setCurrentIndex(k)

    # ─────────────────────────────────────────
    # 크로스헤어 / 컬러맵
    # ─────────────────────────────────────────

    def _on_crosshair_toggled(self, on: bool):
        self._view.set_crosshair(on)

    def _on_crosshair_color_changed(self, name: str):
        color = self._crosshair_colors.get(name, '#ff0000')
        self._crosshair_color = color
        self._view.set_crosshair_color(color)


    def _on_range_panel_toggled(self, checked: bool):
        if checked:
            btn_global = self.btn_range.mapToGlobal(
                self.btn_range.rect().bottomLeft()
            )
            self._range_popup.move(btn_global.x(), btn_global.y() + 4)
            if self._current_image is not None:
                self._hist_range_widget.update_image(
                    self._current_image, self._current_cmap, reset_range=True
                )
            self._range_popup.show()
            self._range_popup.raise_()
        else:
            self._range_popup.hide()

    def _on_range_changed(self, vmin, vmax):
        self._display_vmin = vmin   # None = auto
        self._display_vmax = vmax
        if not self._external_render_control:
            self._range_debounce.start()  # 50ms 후 렌더 — 빠른 드래그 시 중간 프레임 스킵

    def _on_cmap_changed(self, name: str):
        # 'Off'는 내부적으로 'off'로 처리
        self._current_cmap = name.lower() if name.lower() == 'off' else name
        is_off = (self._current_cmap == 'off')

        # Colormap Off에서는 Range UI를 자동으로 닫아 혼란을 줄인다.
        if is_off:
            if self.btn_range.isChecked():
                self.btn_range.setChecked(False)
            self.btn_range.setEnabled(False)
        else:
            self.btn_range.setEnabled(True)

        self.colormap_changed.emit(self._current_cmap)
        if self._hist_range_widget.isVisible() and self._current_image is not None:
            self._hist_range_widget.update_image(self._current_image, self._current_cmap)
        if not self._external_render_control:
            self._refresh_pixmap(fit=False)

    # ─────────────────────────────────────────
    # 마우스 정보
    # ─────────────────────────────────────────

    def _on_mouse_moved(self, x: float, y: float):
        if self._current_image is None:
            return
        ix, iy = int(x), int(y)
        h, w = self._current_image.shape[:2]
        if 0 <= ix < w and 0 <= iy < h:
            val = self._current_image[iy, ix]
            import numpy as np
            # 칼라(RGB) 픽셀 처리
            if isinstance(val, (np.ndarray, list, tuple)) and len(val) == 3:
                r, g, b = val
                # RGB를 문자열로 emit (시그널 시그니처가 float 하나라면)
                self.pixel_info_updated.emit(ix, iy, float(r))  # R값 emit (필요시 시그널/슬롯 수정)
                # pixel_label에 RGB 모두 표시
                self._update_pixel_label(ix, iy, f"R:{r} G:{g} B:{b}")
            else:
                self.pixel_info_updated.emit(ix, iy, float(val))
                self._update_pixel_label(ix, iy, val)

    def _update_pixel_label(self, ix: int, iy: int, val):
        # 정보 바 커서 위치 업데이트
        if isinstance(val, str) and val.startswith("R:"):
            self._lbl_cursor.setText(f"X:{ix}  Y:{iy}  {val}")
        else:
            self._lbl_cursor.setText(f"X:{ix:4d}  Y:{iy:4d}  Val:{val:.0f}")

        # 클릭 핀
        if self._last_click_info is not None:
            lx, ly, lv = self._last_click_info
            if isinstance(lv, str) and lv.startswith("R:"):
                self._lbl_pin.setText(f"X:{lx}  Y:{ly}  {lv}")
            else:
                self._lbl_pin.setText(f"X:{lx:4d}  Y:{ly:4d}  Val:{lv:.0f}")
        else:
            self._lbl_pin.setText("X:—  Y:—  Val:—")

        # 하위호환 pixel_label (숨겨져 있지만 setValue 유지)
        self.pixel_label.setText(f"X:{ix} Y:{iy}")


    def _on_sel_box_changed(self, x0: float, y0: float, x1: float, y1: float):
        if x0 < 0:
            self._sel_box_rect = None
            self._lbl_sel.setText("—")
            if self._roi_range_mode:
                self._restore_pre_roi_range()
        else:
            self._sel_box_rect = (x0, y0, x1, y1)
            w = abs(x1 - x0)
            h = abs(y1 - y0)
            self._lbl_sel.setText(f"{w:.0f}×{h:.0f}  @({min(x0,x1):.0f},{min(y0,y1):.0f})")
            if self._roi_range_mode:
                self._apply_roi_range(self._sel_box_rect)
        self._update_ruler_profiles()

    # ─────────────────────────────────────────
    # ROI Range
    # ─────────────────────────────────────────

    def _on_roi_range_toggled(self, checked: bool):
        """ROI Range 모드 ON/OFF."""
        self._roi_range_mode = checked
        if checked:
            # 현재 범위 저장
            self._pre_roi_range = (self._display_vmin, self._display_vmax)
            # 이미 선택 박스가 있으면 즉시 적용
            if self._sel_box_rect is not None:
                self._apply_roi_range(self._sel_box_rect)
        else:
            self._restore_pre_roi_range()

    def _apply_roi_range(self, rect: tuple):
        """선택 영역 내 픽셀 min/max → 컬러맵 범위로 설정."""
        if self._current_image is None:
            return
        x0, y0, x1, y1 = rect
        img = self._current_image
        h, w = img.shape[:2]
        ix0 = max(0, min(int(min(x0, x1)), w - 1))
        iy0 = max(0, min(int(min(y0, y1)), h - 1))
        ix1 = max(ix0 + 1, min(int(max(x0, x1)), w))
        iy1 = max(iy0 + 1, min(int(max(y0, y1)), h))
        region = img[iy0:iy1, ix0:ix1].astype(np.float64)
        vmin = float(region.min())
        vmax = float(region.max())
        if vmax <= vmin:
            vmax = vmin + 1.0
        self._on_range_changed(vmin, vmax)
        self.range_changed.emit(vmin, vmax)
        # 히스토그램 Range 팝업 핸들도 동기화
        if self._hist_range_widget.isVisible():
            self._hist_range_widget.set_range(vmin, vmax)

    def _restore_pre_roi_range(self):
        """ROI Range 진입 전 범위로 복원."""
        vmin, vmax = self._pre_roi_range
        self._on_range_changed(vmin, vmax)
        self.range_changed.emit(vmin, vmax)
        if self._hist_range_widget.isVisible():
            if vmin is not None and vmax is not None:
                self._hist_range_widget.set_range(vmin, vmax)

    def _on_mouse_clicked(self, x: float, y: float):
        if x < 0 or y < 0:
            self._last_click_info = None
            self._update_ruler_profiles()   # 전체 평균으로 복귀
            return
        if self._current_image is None:
            return
        ix, iy = int(x), int(y)
        h, w = self._current_image.shape[:2]
        if 0 <= ix < w and 0 <= iy < h:
            val = self._current_image[iy, ix]
            import numpy as np
            # 칼라(RGB) 픽셀 처리: 클릭 시에도 문자열로 저장
            if isinstance(val, (np.ndarray, list, tuple)) and len(val) == 3:
                r, g, b = val
                lv = f"R:{r} G:{g} B:{b}"
            else:
                lv = val
            self._last_click_info = (ix, iy, lv)
            self._update_ruler_profiles()

    # ─────────────────────────────────────────
    # 우클릭 컨텍스트 메뉴
    # ─────────────────────────────────────────

    def _show_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtCore import QPoint

        _QSS = """
            QMenu {
                background: #0d1829;
                border: 1px solid #1a3a60;
                color: #c0d0e0;
                font-family: 'Courier New';
                font-size: 11px;
                padding: 4px 0;
            }
            QMenu::item {
                padding: 5px 28px 5px 16px;
                min-width: 180px;
            }
            QMenu::item:selected {
                background: #1a3a60;
                color: #4ecdc4;
            }
            QMenu::item:disabled {
                color: #3a5070;
            }
            QMenu::separator {
                height: 1px;
                background: #1a3a60;
                margin: 3px 8px;
            }
            QMenu::indicator {
                width: 12px;
                height: 12px;
                margin-left: 4px;
            }
        """

        menu = QMenu(self)
        menu.setStyleSheet(_QSS)

        # ── View ──────────────────────────────────────────────────
        act_fit = menu.addAction("⊡  Fit to View\tF")
        act_fit.triggered.connect(self.autoRange)

        act_1x = menu.addAction("1:1  Actual Size\t1")
        act_1x.triggered.connect(self._zoom_actual)

        menu.addSeparator()

        # ── Rotate ────────────────────────────────────────────────
        rot_menu = menu.addMenu("↻  Rotate")
        rot_menu.setStyleSheet(_QSS)
        for label, k in [("0°  (no rotation)", 0), ("90°  CCW", 1),
                         ("180°", 2), ("270°  CCW", 3)]:
            act = rot_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._rotation_k == k)
            act.triggered.connect(lambda _, _k=k: self.set_rotation(_k * 90))

        menu.addSeparator()

        # ── Colormap ──────────────────────────────────────────────
        cmap_menu = menu.addMenu("🎨  Colormap")
        cmap_menu.setStyleSheet(_QSS)
        _cmaps = ["Off", "jet", "viridis", "plasma", "hot", "grey",
                  "inferno", "magma", "coolwarm", "RdBu"]
        for name in _cmaps:
            act = cmap_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(self._current_cmap.lower() == name.lower())
            act.triggered.connect(lambda _, n=name: self._set_cmap_by_name(n))

        menu.addSeparator()

        # ── Clipboard / Save ──────────────────────────────────────
        has_img = self._current_image is not None

        act_copy = menu.addAction("📋  Copy to Clipboard\tCtrl+C")
        act_copy.setEnabled(has_img)
        act_copy.triggered.connect(self._copy_to_clipboard)

        act_save = menu.addAction("💾  Save As...\tCtrl+S")
        act_save.setEnabled(has_img)
        act_save.triggered.connect(self._export_image)

        menu.exec(self._view.mapToGlobal(pos))

    def _zoom_actual(self):
        """1:1 스케일 (픽셀 = 픽셀)."""
        self._view._scale = 1.0
        self._view._apply_scale()
        self._view._emit_scale()

    def _set_cmap_by_name(self, name: str):
        """컨텍스트 메뉴에서 컬러맵 선택 — combo와 동기화."""
        idx = self.cmap_combo.findText(name, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.cmap_combo.setCurrentIndex(idx)
        else:
            # combo에 없는 colormap (inferno 등) — 직접 적용
            self._current_cmap = name.lower()
            self.colormap_changed.emit(self._current_cmap)
            if not self._external_render_control:
                self._refresh_pixmap(fit=False)

    def _copy_to_clipboard(self):
        """현재 화면 이미지를 클립보드에 복사."""
        if self._current_image is None:
            return
        try:
            img = self._current_image
            if self._rotation_k:
                img = np.rot90(img, k=self._rotation_k)
            cmap = self._current_cmap
            if cmap and cmap != 'off':
                rgba = apply_colormap(img, cmap,
                                      vmin=self._display_vmin,
                                      vmax=self._display_vmax)
            else:
                from core.async_worker import ColorMapWorker
                rgba = ColorMapWorker._to_grayscale_rgba(img)
            pixmap = ndarray_to_qpixmap(rgba)
            QApplication.clipboard().setPixmap(pixmap)
        except Exception as e:
            print(f"[ImageViewer] 클립보드 복사 오류: {e}")

    # ─────────────────────────────────────────
    # 하위 호환 (main_window 에서 사용하는 속성들)
    # ─────────────────────────────────────────

    def autoRange(self):
        self._view.fit_to_view()

    @property
    def image_view(self):
        """하위 호환용 - main_window 에서 image_viewer.image_view.autoRange() 호출"""
        return self
