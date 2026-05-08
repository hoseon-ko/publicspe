"""
ui/kinematic/kinematic_tab.py
ACS 6축 키네마틱 스테이지 스캔 탭.

AutoFocusTab과 동일한 레이아웃 — 이동 주체만 ACS 키네마틱 스테이지로 변경.
Dry Run은 AcsStageController.dry_run 플래그로 제어됨.

레이아웃:
  ┌─ 좌측 설정 패널 ────────┬─ 우측 뷰 영역 ──────────────────────┐
  │  📷 CAMERA              │  ImageViewer (스캔 프리뷰)           │
  │  ⬡  ACS STAGE          ├─────────────────────────────────────┤
  │  📐 SCAN AXIS / RANGE  │  Sharpness vs Position 플롯          │
  │  📊 METRIC             │  + 결과 테이블                        │
  │  ─────────              └─────────────────────────────────────┘
  │  [ ▶ RUN ] [ ■ STOP ]
  │  ████████░  60%
  │  Best Pos: +1.2500 mm [GO]
  └─────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QScrollArea, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QComboBox,
    QProgressBar, QTextEdit, QCheckBox,
    QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter,
    QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QSettings, QSize
from PyQt6.QtGui import QIcon, QPixmap, QImage

import pyqtgraph as pg

from ui.image_viewer import ImageViewer
from ui.widgets.collapsible_section import CollapsibleSection
from ui.widgets.auto_splitter import AutoSplitter

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

from core.motor.kinematic_calc import KinematicCalc
from theme.styles import (
    Fonts, Sizes,
    C_ACCENT, C_DANGER, C_BORDER,
    C_BG_DARK, C_BG_MED, C_TEXT_DIM, C_TEXT_DEAD,
    SPIN_STYLE, COMBO_STYLE, TEXTEDIT_LOG,
)

_FC  = Fonts.UI
_FS  = Sizes.CTRL
_FSS = Sizes.CTRL

# 스캔 가능한 DOF 목록
_DOF_AXES = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
_DOF_UNITS = {"Tx": "mm", "Ty": "mm", "Tz": "mm",
               "Rx": "mrad", "Ry": "mrad", "Rz": "mrad"}


def _lbl(text: str, color: str = C_TEXT_DIM) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(f"color: {color}; font-family: '{_FC}'; font-size: {_FSS};")
    return w


def _sep_h() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {C_BORDER}; margin: 3px 0;")
    return f


def _btn(text: str, color: str) -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(f"""
        QPushButton {{
            background: transparent; color: {color};
            border: 1px solid {color}; border-radius: 3px;
            font-family: '{_FC}'; font-size: {_FS};
            font-weight: bold; padding: 5px 12px;
        }}
        QPushButton:hover  {{ background: {color}22; }}
        QPushButton:pressed {{ background: {color}44; }}
        QPushButton:disabled {{ color: #304060; border-color: #1a2840; }}
    """)
    return b


class KinematicTab(QWidget):
    """
    ACS 6축 키네마틱 스테이지 스캔 탭.

    외부 연결:
        set_shared_camera(cam)   — Live 탭 카메라 공유
        clear_shared_camera()
        set_acs_ctrl(ctrl)       — Live 탭 ACS 스테이지 공유
        clear_acs_ctrl()
    """

    kin_starting = pyqtSignal()
    kin_done     = pyqtSignal()
    log_message  = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cam    = None
        self._ctrl   = None
        self._calc   = KinematicCalc()
        self._running = False
        self._worker  = None
        self._goto_worker: QThread | None = None

        self._pos_pts: list[float] = []
        self._sh_pts:  list[float] = []
        self._best_pos: Optional[float] = None
        self._image_list: list = []

        self._auto_disable_timer = QTimer(self)
        self._auto_disable_timer.setSingleShot(True)
        self._auto_disable_timer.setInterval(5 * 60 * 1000)
        self._auto_disable_timer.timeout.connect(self._on_auto_disable)

        self._build_ui()
        self._restore_settings()

    # ── Public API ────────────────────────────────────────────────────

    def set_shared_camera(self, cam):
        self._cam = cam
        name = type(cam).__name__.replace("Camera", "")
        self._lbl_cam.setText(f"● {name}  CONNECTED")
        self._lbl_cam.setStyleSheet(
            f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._update_run_btn()

    def clear_shared_camera(self):
        self._cam = None
        self._lbl_cam.setText("● 카메라 없음")
        self._lbl_cam.setStyleSheet(
            f"color: {C_DANGER}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._update_run_btn()

    def set_acs_ctrl(self, ctrl):
        self._ctrl = ctrl
        dry = getattr(ctrl, "dry_run", False)
        dry_tag = "  [DRY RUN]" if dry else ""
        self._lbl_stage.setText(f"● ACS STAGE  CONNECTED{dry_tag}")
        self._lbl_stage.setStyleSheet(
            f"color: {'#ffe66d' if dry else '#4ecdc4'}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._pos_timer.start()
        self._update_run_btn()

    def clear_acs_ctrl(self):
        self._ctrl = None
        self._pos_timer.stop()
        self._lbl_stage.setText("● 스테이지 미연결")
        self._lbl_stage.setStyleSheet(
            f"color: {C_TEXT_DEAD}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        for lbl in self._pos_labels:
            lbl.setText("—")
        self._update_run_btn()

    def on_tab_activated(self):
        if self._ctrl and self._ctrl.is_connected:
            if not self._pos_timer.isActive():
                self._pos_timer.start()

    def cleanup(self):
        self._save_settings()
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(2000)

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._splitter = AutoSplitter(Qt.Orientation.Horizontal)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: #1a3a60; width: 3px; }"
        )
        root.addWidget(self._splitter)
        self._splitter.addWidget(self._build_left_panel())
        self._splitter.addWidget(self._build_right_panel())
        self._splitter.setSizes([300, 1200])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

    # ── 좌측 패널 ─────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(320)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #0a0f1e; }"
            "QScrollBar:vertical { width: 6px; background: #0a1020; }"
            "QScrollBar::handle:vertical { background: #1a3060; border-radius: 3px; }"
        )

        inner = QWidget()
        inner.setStyleSheet("background: #0a0f1e;")
        v = QVBoxLayout(inner)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        def _row(label: str, widget: QWidget) -> QHBoxLayout:
            h = QHBoxLayout()
            h.setSpacing(10)
            lb = QLabel(label)
            lb.setFixedWidth(100)
            lb.setStyleSheet(f"color:{C_TEXT_DIM}; font-family:'{_FC}'; font-size:{_FSS};")
            lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(lb)
            h.addWidget(widget, 1)
            return h

        # ── 1. 카메라 ────────────────────────────────────────────────
        sec_cam = CollapsibleSection("📷  CAMERA", accent=C_ACCENT)
        self._lbl_cam = QLabel("● 카메라 없음")
        self._lbl_cam.setStyleSheet(f"color:{C_DANGER}; font-family:'{_FC}'; font-size:{_FSS};")
        sec_cam.add_widget(self._lbl_cam)
        v.addWidget(sec_cam)

        # ── 2. ACS 스테이지 상태 ──────────────────────────────────────
        sec_stage = CollapsibleSection("⬡  ACS STAGE", accent="#aa7acc")
        self._lbl_stage = QLabel("● 스테이지 미연결")
        self._lbl_stage.setStyleSheet(
            f"color:{C_TEXT_DEAD}; font-family:'{_FC}'; font-size:{_FSS};"
        )
        sec_stage.add_widget(self._lbl_stage)

        # 6축 현재 위치 표시
        pos_grid = QWidget()
        pg_lay = QGridLayout(pos_grid)
        pg_lay.setContentsMargins(4, 4, 4, 4)
        pg_lay.setSpacing(2)
        axis_labels = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
        self._pos_labels: list[QLabel] = []
        for i, name in enumerate(axis_labels):
            r, c = divmod(i, 3)
            lbl_name = QLabel(name)
            lbl_name.setStyleSheet(
                f"color:#aa7acc; font-family:'Courier New'; font-size:13px; font-weight:bold;"
            )
            pg_lay.addWidget(lbl_name, r * 2, c)
            lbl_val = QLabel("—")
            lbl_val.setStyleSheet(
                f"color:#c0d0ff; font-family:'Courier New'; font-size:13px;"
            )
            pg_lay.addWidget(lbl_val, r * 2 + 1, c)
            self._pos_labels.append(lbl_val)

        sec_stage.add_widget(pos_grid)

        # Dry Run 표시
        self._lbl_dry = QLabel("")
        self._lbl_dry.setStyleSheet(
            f"color:#ffe66d; font-family:'{_FC}'; font-size:{_FSS}; font-weight:bold;"
        )
        sec_stage.add_widget(self._lbl_dry)

        # 위치 폴링 타이머
        self._pos_timer = QTimer()
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._poll_stage)

        v.addWidget(sec_stage)

        # ── 3. 스캔 축 & 범위 ─────────────────────────────────────────
        sec_range = CollapsibleSection("📐  SCAN AXIS / RANGE", accent="#4a9a7a")
        rl = sec_range.content_layout()
        rl.setSpacing(5)

        self.combo_axis = QComboBox()
        self.combo_axis.addItems(_DOF_AXES)
        self.combo_axis.setCurrentText("Tz")
        self.combo_axis.setStyleSheet(COMBO_STYLE)
        self.combo_axis.currentTextChanged.connect(self._on_axis_changed)
        rl.addLayout(_row("Scan Axis", self.combo_axis))

        self.spin_center = QDoubleSpinBox()
        self.spin_center.setRange(-500.0, 500.0)
        self.spin_center.setDecimals(4)
        self.spin_center.setValue(0.0)
        self.spin_center.setStyleSheet(SPIN_STYLE)
        rl.addLayout(_row("Center", self.spin_center))

        self.spin_range = QDoubleSpinBox()
        self.spin_range.setRange(0.001, 500.0)
        self.spin_range.setDecimals(4)
        self.spin_range.setValue(1.0)
        self.spin_range.setStyleSheet(SPIN_STYLE)
        rl.addLayout(_row("± Range", self.spin_range))

        self.spin_step = QDoubleSpinBox()
        self.spin_step.setRange(0.0001, 100.0)
        self.spin_step.setDecimals(4)
        self.spin_step.setValue(0.1)
        self.spin_step.setStyleSheet(SPIN_STYLE)
        rl.addLayout(_row("Step", self.spin_step))

        self._lbl_steps = QLabel("Steps: 21")
        self._lbl_steps.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lbl_steps.setStyleSheet(f"color:#4a7a6a; font-family:'{_FC}'; font-size:{_FSS};")
        rl.addWidget(self._lbl_steps)
        self._lbl_unit = QLabel("단위: mm")
        self._lbl_unit.setStyleSheet(f"color:#6a5a8a; font-family:'{_FC}'; font-size:{_FSS};")
        rl.addWidget(self._lbl_unit)

        for s in (self.spin_range, self.spin_step):
            s.valueChanged.connect(self._update_step_count)
        self._update_step_count()
        v.addWidget(sec_range)

        # ── 4. 선명도 지표 ────────────────────────────────────────────
        sec_metric = CollapsibleSection("📊  SHARPNESS METRIC", accent="#6a6aaa")
        ml = sec_metric.content_layout()
        self.combo_metric = QComboBox()
        self.combo_metric.addItems([
            "Laplacian Variance",
            "Contrast (Std Dev)",
            "Tenengrad (Sobel²)",
            "Brenner",
        ])
        self.combo_metric.setStyleSheet(COMBO_STYLE)
        ml.addWidget(self.combo_metric)
        v.addWidget(sec_metric)

        # ── 5. 옵션 ──────────────────────────────────────────────────
        sec_opt = CollapsibleSection("⚙  OPTIONS", accent="#7a6a4a", collapsed=True)
        ol = sec_opt.content_layout()
        ol.setSpacing(5)

        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 10000)
        self.spin_settle.setSuffix("  ms")
        self.spin_settle.setValue(300)
        self.spin_settle.setStyleSheet(SPIN_STYLE)
        ol.addLayout(_row("Settle", self.spin_settle))

        self.spin_avg = QSpinBox()
        self.spin_avg.setRange(1, 32)
        self.spin_avg.setValue(1)
        self.spin_avg.setStyleSheet(SPIN_STYLE)
        ol.addLayout(_row("Avg frames", self.spin_avg))

        self.chk_goto_best = QCheckBox("완료 후 Best Position으로 자동 이동")
        self.chk_goto_best.setChecked(True)
        self.chk_goto_best.setStyleSheet(
            f"color:{C_TEXT_DIM}; font-family:'{_FC}'; font-size:{_FSS};"
        )
        ol.addWidget(self.chk_goto_best)
        v.addWidget(sec_opt)

        v.addWidget(_sep_h())

        # ── 6. RUN / STOP ─────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_run  = _btn("▶  RUN",  "#4ecdc4")
        self.btn_stop = _btn("■  STOP", "#e94560")
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_run.clicked.connect(self._on_run)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(self.btn_run,  1)
        btn_row.addWidget(self.btn_stop, 1)
        v.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(14)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background: #080e1e; border: 1px solid #1a3060;
                border-radius: 3px; font-family: '{_FC}'; font-size: {_FSS};
                text-align: center;
            }}
            QProgressBar::chunk {{ background: #aa7acc; border-radius: 2px; }}
        """)
        v.addWidget(self.progress)

        self._lbl_status = QLabel("Ready")
        self._lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_status.setStyleSheet(
            f"color:#4a6a8a; font-family:'{_FC}'; font-size:{_FSS};"
        )
        v.addWidget(self._lbl_status)
        v.addWidget(_sep_h())

        # ── 7. 결과 ──────────────────────────────────────────────────
        v.addWidget(_lbl("RESULT", "#2a4a6a"))
        res_row = QHBoxLayout()
        self._lbl_best = QLabel("Best:  —")
        self._lbl_best.setStyleSheet(
            f"color:#e94560; font-family:'{_FC}'; font-size:{Sizes.BTN}; font-weight:bold;"
        )
        self.btn_goto = _btn("GO", "#e94560")
        self.btn_goto.setFixedWidth(60)
        self.btn_goto.setEnabled(False)
        self.btn_goto.clicked.connect(self._on_goto)
        res_row.addWidget(self._lbl_best, 1)
        res_row.addWidget(self.btn_goto)
        v.addLayout(res_row)

        self._lbl_best_sh = QLabel("Sharpness:  —")
        self._lbl_best_sh.setStyleSheet(
            f"color:#4a9a7a; font-family:'{_FC}'; font-size:{_FSS};"
        )
        v.addWidget(self._lbl_best_sh)
        v.addWidget(_sep_h())

        # ── 8. 로그 ──────────────────────────────────────────────────
        v.addWidget(_lbl("LOG", "#2a4a6a"))
        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(120)
        self._log_box.setStyleSheet(TEXTEDIT_LOG)
        v.addWidget(self._log_box)

        v.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    # ── 우측 패널 ─────────────────────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet("background: #080e1e;")
        root_h = QHBoxLayout(container)
        root_h.setContentsMargins(0, 0, 0, 0)
        root_h.setSpacing(0)

        self._main_splitter = AutoSplitter(Qt.Orientation.Horizontal)
        root_h.addWidget(self._main_splitter)

        # 이미지 뷰어
        viewer_wrap = QWidget()
        viewer_wrap.setStyleSheet("background: #080e1e;")
        vv = QVBoxLayout(viewer_wrap)
        vv.setContentsMargins(0, 0, 0, 0)
        vv.setSpacing(0)

        vhdr = QWidget()
        vhdr.setFixedHeight(22)
        vhdr.setStyleSheet(f"background: {C_BG_MED}; border-bottom: 1px solid {C_BORDER};")
        vhdr_h = QHBoxLayout(vhdr)
        vhdr_h.setContentsMargins(8, 0, 8, 0)
        lbl_view = QLabel("PREVIEW")
        lbl_view.setStyleSheet(
            f"color:#3a5878; font-family:'{_FC}'; font-size:{_FSS}; font-weight:bold; letter-spacing:2px;"
        )
        self._lbl_step_info = QLabel("—")
        self._lbl_step_info.setStyleSheet(f"color:#4a6a8a; font-family:'{_FC}'; font-size:{_FSS};")
        self._lbl_step_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vhdr_h.addWidget(lbl_view)
        vhdr_h.addWidget(self._lbl_step_info, 1)
        vv.addWidget(vhdr)

        self.image_viewer = ImageViewer()
        vv.addWidget(self.image_viewer, 1)
        self._main_splitter.addWidget(viewer_wrap)

        # 데이터 사이드
        self._side_splitter = QSplitter(Qt.Orientation.Vertical)
        self._side_splitter.setStyleSheet(
            "QSplitter::handle { background: #1a3a60; height: 3px; }"
        )

        # 캡처 프레임 리스트
        frames_widget = QWidget()
        frames_widget.setStyleSheet(f"background:{C_BG_DARK};")
        fv = QVBoxLayout(frames_widget)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(0)
        fhdr = QWidget()
        fhdr.setFixedHeight(22)
        fhdr.setStyleSheet(f"background:{C_BG_MED}; border-bottom:1px solid {C_BORDER};")
        fh_h = QHBoxLayout(fhdr)
        fh_h.setContentsMargins(8, 0, 8, 0)
        lbl_f = QLabel("CAPTURED FRAMES")
        lbl_f.setStyleSheet(
            f"color:#aa7acc; font-family:'{_FC}'; font-size:{Sizes.SMALL}; font-weight:bold;"
        )
        fh_h.addWidget(lbl_f)
        fv.addWidget(fhdr)
        self._frame_list = QListWidget()
        self._frame_list.setIconSize(QSize(80, 60))
        self._frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self._frame_list.setWrapping(False)
        self._frame_list.setFixedHeight(100)
        self._frame_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._frame_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frame_list.setStyleSheet(f"""
            QListWidget {{ background:#080e1e; border:none; color:#c0d0ff; }}
            QListWidget::item {{ padding:2px; border:1px solid #0f2040; }}
            QListWidget::item:selected {{ background:#1a3a60; border:1px solid #aa7acc; }}
        """)
        self._frame_list.currentRowChanged.connect(self._on_frame_select)
        fv.addWidget(self._frame_list)
        self._side_splitter.addWidget(frames_widget)

        # Sharpness vs Position 플롯
        plot_wrap = QWidget()
        plot_wrap.setStyleSheet("background:#080e1e;")
        pv = QVBoxLayout(plot_wrap)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        phdr = QWidget()
        phdr.setFixedHeight(22)
        phdr.setStyleSheet(f"background:{C_BG_MED}; border-bottom:1px solid {C_BORDER};")
        ph_h = QHBoxLayout(phdr)
        ph_h.setContentsMargins(8, 0, 8, 0)
        self._lbl_plot_title = QLabel("SHARPNESS vs Tz")
        self._lbl_plot_title.setStyleSheet(
            f"color:#3a5878; font-family:'{_FC}'; font-size:{Sizes.SMALL}; font-weight:bold;"
        )
        ph_h.addWidget(self._lbl_plot_title)
        pv.addWidget(phdr)
        pv.addWidget(self._build_plot(), 1)
        self._side_splitter.addWidget(plot_wrap)

        # 결과 테이블
        table_wrap = QWidget()
        table_wrap.setStyleSheet("background:#080e1e;")
        tv = QVBoxLayout(table_wrap)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        thdr = QWidget()
        thdr.setFixedHeight(22)
        thdr.setStyleSheet(f"background:{C_BG_MED}; border-bottom:1px solid {C_BORDER};")
        th_h = QHBoxLayout(thdr)
        th_h.setContentsMargins(8, 0, 8, 0)
        lbl_t = QLabel("RESULTS TABLE")
        lbl_t.setStyleSheet(
            f"color:#3a5878; font-family:'{_FC}'; font-size:{Sizes.SMALL}; font-weight:bold;"
        )
        th_h.addWidget(lbl_t)
        tv.addWidget(thdr)
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Step", "Position", "Sharpness"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setStyleSheet(f"""
            QTableWidget {{ background:#080e1e; color:#c0d0ff; gridline-color:#1a3a60; border:none; }}
            QHeaderView::section {{ background:{C_BG_MED}; color:#4a6a8a; border:1px solid #1a3a60; padding:2px; }}
        """)
        tv.addWidget(self._table)
        self._side_splitter.addWidget(table_wrap)

        self._side_splitter.setSizes([100, 300, 200])
        self._main_splitter.addWidget(self._side_splitter)
        self._main_splitter.setSizes([900, 400])
        return container

    def _build_plot(self) -> pg.PlotWidget:
        pg.setConfigOptions(antialias=True)
        pw = pg.PlotWidget()
        pw.setBackground("#080e1e")
        for ax in ("bottom", "left"):
            axis = pw.getAxis(ax)
            axis.setPen(pg.mkPen("#2a3a52"))
            axis.setTextPen(pg.mkPen(C_TEXT_DIM))
            axis.setStyle(tickFont=pg.QtGui.QFont(_FC, 8))
        pw.getAxis("bottom").setLabel("Position", **{"color": C_TEXT_DIM, "font-size": "9px"})
        pw.getAxis("left").setLabel("Sharpness (a.u.)", **{"color": C_TEXT_DIM, "font-size": "9px"})
        pw.showGrid(x=True, y=True, alpha=0.2)
        pw.getPlotItem().getViewBox().setBackgroundColor("#080e1e")
        self._curve = pw.plot(
            pen=pg.mkPen("#aa7acc", width=2),
            symbol="o", symbolSize=5,
            symbolBrush="#aa7acc", symbolPen=None,
        )
        self._best_vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#e94560", width=1.5, style=Qt.PenStyle.DashLine),
        )
        pw.addItem(self._best_vline)
        self._best_vline.hide()
        self._plot_widget = pw
        return pw

    # ── 슬롯 ─────────────────────────────────────────────────────────

    def _on_axis_changed(self, axis: str):
        unit = _DOF_UNITS.get(axis, "")
        self.spin_center.setSuffix(f"  {unit}")
        self.spin_range.setSuffix(f"  {unit}")
        self.spin_step.setSuffix(f"  {unit}")
        self._lbl_unit.setText(f"단위: {unit}")
        self._lbl_plot_title.setText(f"SHARPNESS vs {axis}")
        self._update_step_count()

    def _update_step_count(self):
        half = self.spin_range.value()
        step = max(self.spin_step.value(), 1e-9)
        n = int(2 * half / step) + 1
        self._lbl_steps.setText(f"Steps: {n}")

    def _poll_stage(self):
        if not self._ctrl or not self._ctrl.is_connected:
            self._pos_timer.stop()
            return
        try:
            positions = self._ctrl.get_all_positions()
            for i, lbl in enumerate(self._pos_labels):
                lbl.setText(f"{positions[i]:+.3f}")
            dry = getattr(self._ctrl, "dry_run", False)
            self._lbl_dry.setText("[DRY RUN 활성]" if dry else "")
        except Exception:
            pass

    def _update_run_btn(self):
        # 카메라 + 스테이지 둘 다 있어야 RUN 가능
        ready = (self._cam is not None) and (self._ctrl is not None)
        if not self._running:
            self.btn_run.setEnabled(ready)

    # ── RUN / STOP ────────────────────────────────────────────────────

    def _on_run(self):
        if self._cam is None:
            self._log("❌ 카메라 없음"); return
        if self._ctrl is None:
            self._log("❌ ACS 스테이지 미연결"); return

        axis  = self.combo_axis.currentText()
        center = self.spin_center.value()
        half   = self.spin_range.value()
        step   = self.spin_step.value()

        positions = []
        p = center - half
        while p <= center + half + step * 0.01:
            positions.append(round(p, 8))
            p += step

        metric_map = {
            "Laplacian Variance": "laplacian",
            "Contrast (Std Dev)": "contrast",
            "Tenengrad (Sobel²)": "tenengrad",
            "Brenner":            "brenner",
        }
        metric = metric_map.get(self.combo_metric.currentText(), "laplacian")

        # 고정 DOF는 모두 0 (현재 버전)
        fixed = {k: 0.0 for k in _DOF_AXES}

        self._log(
            f"SCAN START — axis={axis}  center={center:+.4f}  "
            f"±{half:.4f}  step={step:.4f}  {len(positions)}steps"
        )
        if getattr(self._ctrl, "dry_run", False):
            self._log("⚠ DRY RUN 모드 — 실제 스테이지 이동 없음")

        # 초기화
        self._pos_pts.clear(); self._sh_pts.clear()
        self._image_list.clear(); self._frame_list.clear()
        self._table.setRowCount(0)
        self._best_pos = None
        self._curve.setData([], [])
        self._best_vline.hide()
        self._lbl_best.setText("Best:  —")
        self._lbl_best_sh.setText("Sharpness:  —")
        self.btn_goto.setEnabled(False)
        self.progress.setValue(0)
        self._lbl_status.setText("Running…")

        self._set_running(True)
        self.kin_starting.emit()

        from ui.kinematic.kinematic_scan_worker import KinematicScanWorker
        self._worker = KinematicScanWorker(
            camera    = self._cam,
            acs_ctrl  = self._ctrl,
            scan_axis = axis,
            positions = positions,
            fixed_dof = fixed,
            calc      = self._calc,
            metric    = metric,
            settle_ms = self.spin_settle.value(),
            avg_frames= self.spin_avg.value(),
        )
        self._worker.step_done.connect(self._on_step_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.log.connect(self._log)
        self._worker.start()

    def _on_stop(self):
        self._log("SCAN STOP 요청")
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
        self._lbl_status.setText("중단됨")
        self._set_running(False)

    def _on_goto(self):
        if self._best_pos is None or self._ctrl is None:
            return
        if self._goto_worker and self._goto_worker.isRunning():
            self._log("⚠ GOTO 진행 중 — 중복 명령 무시")
            return

        axis  = self.combo_axis.currentText()
        fixed = {k: 0.0 for k in _DOF_AXES}

        from ui.kinematic.kinematic_scan_worker import _AXIS_INFO
        ax_idx, ax_type, _ = _AXIS_INFO[axis]
        trans  = [fixed.get(k, 0.0) for k in ("Tx", "Ty", "Tz")]
        rotate = [fixed.get(k, 0.0) for k in ("Rx", "Ry", "Rz")]
        if ax_type == "trans":
            trans[ax_idx] = self._best_pos
        else:
            rotate[ax_idx] = self._best_pos

        cal_pos, _, ok, violations = self._calc.calculate(trans, rotate)
        if not ok or cal_pos is None:
            self._log(f"❌ GOTO 인터락 위반: {violations}")
            return

        dry = getattr(self._ctrl, "dry_run", False)
        axis_txt = self.combo_axis.currentText()
        unit = _DOF_UNITS.get(axis_txt, "")
        self._log(f"GOTO 시작: {axis_txt}={self._best_pos:+.4f}{unit}")

        from ui.live.acs_stage_panel import _KinematicMoveWorker
        self._auto_disable_timer.stop()
        self.btn_goto.setEnabled(False)

        self._goto_worker = _KinematicMoveWorker(self._ctrl, cal_pos, dry)
        self._goto_worker.log.connect(self._log)
        self._goto_worker.finished.connect(self._on_goto_done)
        self._goto_worker.error.connect(self._on_goto_error)
        self._goto_worker.start()

    def _on_goto_done(self):
        axis_txt = self.combo_axis.currentText()
        unit = _DOF_UNITS.get(axis_txt, "")
        self._log(f"✅ GOTO 완료: {axis_txt}={self._best_pos:+.4f}{unit} — 5분 후 자동 서보 OFF 예약")
        self.btn_goto.setEnabled(True)
        self._auto_disable_timer.start()

    def _on_goto_error(self, msg: str):
        self._log(f"❌ GOTO 오류: {msg}")
        self.btn_goto.setEnabled(True)

    def _on_auto_disable(self):
        if self._ctrl and self._ctrl.is_connected:
            self._ctrl.disable_all()
            self._log("⏱ 자동 서보 OFF (5분 대기 타임아웃)")

    # ── Worker 콜백 ───────────────────────────────────────────────────

    def _on_step_done(self, step: int, total: int,
                      pos: float, sh: float, frame):
        pct = int(step / max(total, 1) * 100)
        self.progress.setValue(pct)
        axis = self.combo_axis.currentText()
        unit = _DOF_UNITS.get(axis, "")
        self._lbl_status.setText(f"Step {step}/{total}  {axis}={pos:+.4f}{unit}  S={sh:.2f}")
        self._lbl_step_info.setText(f"Step {step}/{total}  •  {axis} = {pos:+.4f} {unit}")

        self._pos_pts.append(pos)
        self._sh_pts.append(sh)
        self._image_list.append((step, frame, pos, sh))
        self._curve.setData(self._pos_pts, self._sh_pts)

        self._append_thumbnail(frame, step - 1)
        self._update_table(step, pos, sh)
        self.image_viewer.set_image(frame)

    def _on_finished(self, best_pos: float, best_sh: float):
        self._best_pos = best_pos
        axis = self.combo_axis.currentText()
        unit = _DOF_UNITS.get(axis, "")
        self._lbl_best.setText(f"Best:  {best_pos:+.4f}  {unit}")
        self._lbl_best_sh.setText(f"Sharpness:  {best_sh:.2f}")
        self._best_vline.setPos(best_pos)
        self._best_vline.show()
        self.btn_goto.setEnabled(True)
        self.progress.setValue(100)
        self._lbl_status.setText(f"완료 — Best: {best_pos:+.4f} {unit}")
        self._log(f"✅ 스캔 완료 — Best {axis}: {best_pos:+.4f}{unit}  S={best_sh:.2f}")

        # 테이블 하이라이트
        for r in range(self._table.rowCount()):
            try:
                p = float(self._table.item(r, 1).text())
                if abs(p - best_pos) < 1e-9:
                    self._table.selectRow(r)
                    self._frame_list.setCurrentRow(r)
                    break
            except Exception:
                pass

        self._set_running(False)
        if self.chk_goto_best.isChecked():
            self._on_goto()
        self.kin_done.emit()

    def _on_error(self, msg: str):
        self._log(f"❌ 오류: {msg}")
        self._lbl_status.setText(f"오류: {msg}")
        self._set_running(False)
        self.kin_done.emit()

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        ready = (self._cam is not None) and (self._ctrl is not None)
        self.btn_run.setEnabled(not running and ready)
        self.btn_stop.setEnabled(running)
        for w in (self.combo_axis, self.spin_center, self.spin_range,
                  self.spin_step, self.combo_metric,
                  self.spin_settle, self.spin_avg):
            w.setEnabled(not running)

    def _append_thumbnail(self, frame, idx: int):
        disp = np.asarray(frame)
        if disp.ndim == 2:
            if disp.dtype != np.uint8:
                mi, ma = disp.min(), disp.max()
                if ma > mi:
                    disp = ((disp - mi) / (ma - mi) * 255).astype(np.uint8)
                else:
                    disp = disp.astype(np.uint8)
            disp = np.stack([disp, disp, disp], axis=-1)
        h, w = disp.shape[:2]
        tw, th = 80, 60
        scale = min(tw / w, th / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        try:
            if _CV2_OK:
                small = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
            else:
                raise ImportError
        except (ImportError, NameError):
            small = disp[::max(1, h // nh), ::max(1, w // nw)][:nh, :nw]
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        y0, x0 = (th - nh) // 2, (tw - nw) // 2
        canvas[y0:y0 + nh, x0:x0 + nw] = small[:, :, :3]
        qimg = QImage(canvas.tobytes(), tw, th, tw * 3, QImage.Format.Format_RGB888)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(qimg)), f"#{idx + 1}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._frame_list.addItem(item)
        self._frame_list.scrollToItem(item)

    def _update_table(self, step: int, pos: float, sh: float):
        row = self._table.rowCount()
        self._table.insertRow(row)
        unit = _DOF_UNITS.get(self.combo_axis.currentText(), "")
        for col, txt in enumerate([str(step), f"{pos:+.4f} {unit}", f"{sh:.2f}"]):
            item = QTableWidgetItem(txt)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, col, item)
        self._table.scrollToBottom()

    def _on_frame_select(self, row: int):
        if 0 <= row < len(self._image_list):
            _, frame, pos, sh = self._image_list[row]
            self.image_viewer.set_image(frame)
            axis = self.combo_axis.currentText()
            unit = _DOF_UNITS.get(axis, "")
            self._lbl_step_info.setText(
                f"Step {row + 1}/{len(self._image_list)}  •  {axis} = {pos:+.4f} {unit}"
            )

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.append(
            f'<span style="color:#4a6a8a">[{ts}]</span>'
            f' <span style="color:#c0c0e0">{msg}</span>'
        )
        self.log_message.emit(msg)

    # ── 설정 저장/복원 ────────────────────────────────────────────────

    def _save_settings(self):
        s = QSettings("SpeAnalyze", "KinematicTab")
        s.setValue("axis",        self.combo_axis.currentText())
        s.setValue("center",      self.spin_center.value())
        s.setValue("range",       self.spin_range.value())
        s.setValue("step",        self.spin_step.value())
        s.setValue("metric",      self.combo_metric.currentIndex())
        s.setValue("settle",      self.spin_settle.value())
        s.setValue("avg",         self.spin_avg.value())
        s.setValue("goto_best",   self.chk_goto_best.isChecked())
        if hasattr(self, "_splitter"):
            s.setValue("splitter",      self._splitter.saveState())
        if hasattr(self, "_main_splitter"):
            s.setValue("main_splitter", self._main_splitter.saveState())
        if hasattr(self, "_side_splitter"):
            s.setValue("side_splitter", self._side_splitter.saveState())

    def _restore_settings(self):
        s = QSettings("SpeAnalyze", "KinematicTab")
        try:
            self.combo_axis.setCurrentText(s.value("axis", "Tz"))
            self.spin_center.setValue(float(s.value("center", 0.0)))
            self.spin_range.setValue(float(s.value("range", 1.0)))
            self.spin_step.setValue(float(s.value("step", 0.1)))
            self.combo_metric.setCurrentIndex(int(s.value("metric", 0)))
            self.spin_settle.setValue(int(s.value("settle", 300)))
            self.spin_avg.setValue(int(s.value("avg", 1)))
            self.chk_goto_best.setChecked(s.value("goto_best", True, type=bool))
        except Exception:
            pass
