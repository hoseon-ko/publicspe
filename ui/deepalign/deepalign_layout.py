"""DeepAlign UI 구성 파일.

이 파일은 DeepAlign 전용 페이지 생성 코드를 담고 있습니다.
주요 역할은 다음과 같습니다.
- 아이콘 사이드바와 스택 페이지 외형 생성
- 카메라 설정 페이지 생성
- 분석 페이지 생성
- 도킹 viewer 작업영역과 마스터 커맨드 바 생성
- 카메라 capability에 따라 보이기/숨기기 되는 UI 보조 처리
"""

from __future__ import annotations

from PyQt6.QtCore import QSize

import os
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QRadioButton,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QFileDialog,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QDockWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QToolBar,
)
from PyQt6.QtGui import QAction

from theme.styles import (
    C_BG_DEEP, C_BG_DARK, C_BG_MED, C_BORDER, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD,
)
from ui.plot_panel import PlotPanel, HistogramPanel
from ui.deepalign.proc_stats_plot import ProcStatsPlot
from ui.file_list_panel import FileListPanel
from ui.frame_grid_panel import FrameGridPanel
from ui.roi_panel import RoiPanel
from ui.viewer_v2.deepalign_adapter import DeepAlignViewerV2Adapter


class LayoutBuilderMixin:
    def _create_icon_sidebar(self):
        sidebar = QFrame()
        sidebar.setFixedWidth(65)
        sidebar.setStyleSheet(f"background-color: {C_BG_DEEP}; border-right: 1px solid #1e293b;")
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 25, 0, 25)
        lay.setSpacing(25)

        icons = [
            ("📷", "#94a3b8", "Camera Control"),
            ("🪞", "#38bdf8", "Mirror / Scan"),
            ("🔍", "#fbbf24", "Auto Focus"),
            ("🎯", "#ef4444", "6-Axis Align"),
            ("⚙", "#4ecdc4", "Motion / Hardware"),
            ("📊", "#10b981", "Data Analysis"),
        ]
        self.sidebar_btns = []
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        for i, (icon, color, name) in enumerate(icons):
            btn = QPushButton(icon)
            btn.setToolTip(name)
            btn.setFixedSize(45, 45)
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"""
                QPushButton {{ background: transparent; color: {color}; font-size: 24px;
                               border: none; border-radius: 12px; }}
                QPushButton:hover {{ background: #1e293b; color: #f8fafc; }}
                QPushButton:checked {{ background: #1e293b; color: #22d3ee;
                                       border: 1px solid #22d3ee; }}
            """
            )
            btn.clicked.connect(lambda _, idx=i: self._on_tab_changed(idx))
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
            self.sidebar_btns.append(btn)
            self.btn_group.addButton(btn, i)

        self.sidebar_btns[0].setChecked(True)
        lay.addStretch()
        return sidebar

    def _on_tab_changed(self, idx: int):
        self.central_stack.setCurrentIndex(idx)
        if hasattr(self, "master_btn_stack"):
            self.master_btn_stack.setCurrentIndex(min(idx, self.master_btn_stack.count() - 1))
        # Focus 탭(idx=2) 진입 시 KimmScanWidget 의 Center 를 현재 KIMM Z 위치로 설정
        if idx == 2:
            self._sync_kimm_scan_center_to_current_z()

    def _sync_kimm_scan_center_to_current_z(self) -> None:
        """Focus 탭 진입 시 자동 호출 — hub.kimm_get_z 의 현재값을 Center 에 반영.

        scan_requested 트리거를 피하기 위해 blockSignals 로 감쌈.
        hub 미연결 / 조회 실패 시 silent (기존 값 유지).
        """
        if not hasattr(self, "kimm_scan") or not hasattr(self.kimm_scan, "spin_center"):
            return
        hub = getattr(self, "_session_hub", None)
        if hub is None or not getattr(hub, "is_kimm_connected", lambda: False)():
            return
        try:
            z = float(hub.kimm_get_z())
        except Exception:
            return
        sp = self.kimm_scan.spin_center
        sp.blockSignals(True)
        try:
            sp.setValue(z)
        finally:
            sp.blockSignals(False)
        # Steps 표시 갱신
        if hasattr(self.kimm_scan, "_update_steps_count"):
            self.kimm_scan._update_steps_count()

    def _wrap_panel(self, panel: QWidget, extras: list[QWidget] | None = None) -> QWidget:
        """패널을 스크롤 영역에 감싼다. extras가 있으면 패널 아래에 순서대로 추가."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.addWidget(panel)
        if extras:
            for w in extras:
                c_lay.addWidget(w)
        # 위젯들을 상단 정렬 — 섹션이 접혔을 때 panel 과 extras 가
        # 서로 붙어 있도록. 빈 공간은 하단에 몰아둠.
        c_lay.addStretch(1)

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _create_align_page(self) -> QWidget:
        """Align 탭 페이지 — AcsStagePanel + AcsScanWidget + Kinematic Calc 섹션."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.addWidget(self.align_panel)
        c_lay.addWidget(self.acs_scan)              # 분리된 스캔 위젯
        c_lay.addWidget(self._create_kinem_calc_section())
        # 위젯 상단 정렬 — 카드들이 접혀도 서로 붙어있도록.
        c_lay.addStretch(1)

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _create_kinem_calc_section(self) -> QWidget:
        """KINEMATIC CALC 섹션 — 3개 볼 위치 입력 + 형상 설정 + 결과 표시."""
        _C_ACCENT    = "#aa7acc"
        _C_BG        = C_BG_DEEP
        _C_BD        = "#2a1a4a"
        _C_TEXT      = "#c0a8ff"
        _C_TEXT_DIM  = "#6a5a8a"
        _SPIN_QSS = f"""
            QDoubleSpinBox {{
                background:{_C_BG}; border:1px solid {_C_BD};
                color:{_C_TEXT}; border-radius:3px;
                font-size:11px; padding:1px 4px;
            }}
            QDoubleSpinBox:focus {{ border-color:{_C_ACCENT}; }}
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                width:12px; border:none; background:#0d1838;
            }}
        """
        _LBL = f"color:{_C_TEXT_DIM}; font-size:11px;"
        _BTN = f"""
            QPushButton {{
                background:transparent; color:{_C_ACCENT};
                border:1px solid {_C_ACCENT}; border-radius:3px;
                font-size:11px; font-weight:bold; padding:3px 10px;
            }}
            QPushButton:hover {{ background:{_C_ACCENT}22; }}
            QPushButton:disabled {{ color:#3a2a5a; border-color:#2a1a3a; }}
        """

        sec = QGroupBox("KINEMATIC CALC")
        sec.setStyleSheet(f"""
            QGroupBox {{
                color:{_C_ACCENT}; border:1px solid {_C_BD};
                border-radius:4px; margin-top:8px; font-size:11px;
                font-weight:bold; letter-spacing:2px;
            }}
            QGroupBox::title {{ subcontrol-origin:margin; left:8px; }}
        """)
        v = QVBoxLayout(sec)
        v.setContentsMargins(8, 12, 8, 8)
        v.setSpacing(6)

        # ── 1. 형상 설정 파일 ─────────────────────────────────────────
        cfg_row = QHBoxLayout()
        cfg_lbl = QLabel("Config:")
        cfg_lbl.setStyleSheet(_LBL)
        cfg_lbl.setFixedWidth(46)
        self.edit_kinem_config = QLineEdit()
        self.edit_kinem_config.setPlaceholderText("geometry_config.json 경로")
        self.edit_kinem_config.setStyleSheet(f"""
            QLineEdit {{
                background:{_C_BG}; border:1px solid {_C_BD};
                color:{_C_TEXT}; border-radius:3px; font-size:11px; padding:2px 5px;
            }}
            QLineEdit:focus {{ border-color:{_C_ACCENT}; }}
        """)
        self.btn_kinem_config_browse = QPushButton("…")
        self.btn_kinem_config_browse.setFixedWidth(28)
        self.btn_kinem_config_browse.setStyleSheet(_BTN)
        cfg_row.addWidget(cfg_lbl)
        cfg_row.addWidget(self.edit_kinem_config, 1)
        cfg_row.addWidget(self.btn_kinem_config_browse)
        v.addLayout(cfg_row)

        # ── 2. 측정 볼 위치 (3 balls × xyz) ───────────────────────────
        balls_lbl = QLabel("Measured ball positions (mm)")
        balls_lbl.setStyleSheet(_LBL)
        v.addWidget(balls_lbl)

        grid = QGridLayout()
        grid.setSpacing(3)
        grid.setContentsMargins(0, 0, 0, 0)
        for col, hdr in enumerate(("", "X", "Y", "Z")):
            h = QLabel(hdr)
            h.setStyleSheet(f"color:{_C_ACCENT}; font-size:10px; font-weight:bold;")
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(h, 0, col)

        self._kinem_ball_spins: list[list[QDoubleSpinBox]] = []
        for r in range(3):
            row_spins = []
            lbl = QLabel(f"B{r+1}")
            lbl.setStyleSheet(f"color:{_C_TEXT}; font-size:11px; font-weight:bold;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lbl, r + 1, 0)
            for c in range(3):
                sp = QDoubleSpinBox()
                sp.setRange(-9999.0, 9999.0)
                sp.setDecimals(4)
                sp.setValue(0.0)
                sp.setStyleSheet(_SPIN_QSS)
                sp.setFixedHeight(24)
                grid.addWidget(sp, r + 1, c + 1)
                row_spins.append(sp)
            self._kinem_ball_spins.append(row_spins)
        v.addLayout(grid)

        # ── 3. 결과 표시 ─────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_C_BD}; margin:2px 0;")
        v.addWidget(sep)

        res_lbl = QLabel("RESULT")
        res_lbl.setStyleSheet(f"color:{_C_TEXT_DIM}; font-size:10px; font-weight:bold; letter-spacing:2px;")
        v.addWidget(res_lbl)

        self.lbl_kinem_result = QLabel("rx=—  ry=—  rz=—  tx=—  ty=—  tz=—")
        self.lbl_kinem_result.setStyleSheet(
            f"color:{_C_ACCENT}; font-size:11px; font-family:monospace;"
        )
        self.lbl_kinem_result.setWordWrap(True)
        v.addWidget(self.lbl_kinem_result)

        return sec

    def _create_docking_workspace(self) -> QMainWindow:
        host = QMainWindow()
        host.setObjectName("deepAlignDockHost")
        host.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks |
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.AnimatedDocks
        )
        host.setStyleSheet(f"QMainWindow {{ background: {C_BG_DEEP}; }}")

        self.cam_viewer = DeepAlignViewerV2Adapter()
        self.cam_viewer.set_external_render_control(True)
        host.setCentralWidget(self.cam_viewer)

        # Analysis Docks (기본 표시)
        self.plot_panel = PlotPanel("Profile")
        self.dock_plot = self._wrap_dock(
            "dock_plot", "📈  PROFILE PLOT",
            self.plot_panel, Qt.DockWidgetArea.BottomDockWidgetArea, host
        )
        self.dock_plot.setVisible(True)

        self.hist_panel = HistogramPanel()
        self.dock_hist = self._wrap_dock(
            "dock_histogram", "📊  HISTOGRAM",
            self.hist_panel, Qt.DockWidgetArea.BottomDockWidgetArea, host
        )
        self.dock_hist.setVisible(True)

        # Proc Stats — Mode 1/2 결과 시계열 (우측 dock, 기본 숨김 / master_bar 토글로 ON)
        self.proc_stats_panel = ProcStatsPlot()
        self.dock_proc_stats = self._wrap_dock(
            "dock_proc_stats", "📉  PROC STATS",
            self.proc_stats_panel, Qt.DockWidgetArea.RightDockWidgetArea, host
        )
        self.dock_proc_stats.setMinimumWidth(280)
        self.dock_proc_stats.setVisible(False)

        # Proc Table — 연산 결과 수치 테이블 (하단 dock, 기본 숨김)
        self.dock_proc_table = self._wrap_dock(
            "dock_proc_table", "📋  METRIC TABLE",
            self.proc_stats_panel.table, Qt.DockWidgetArea.BottomDockWidgetArea, host
        )
        self.dock_proc_table.setMinimumHeight(120)
        self.dock_proc_table.setVisible(False)

        self.file_list_panel = FileListPanel()
        self.dock_files = self._wrap_dock(
            "dock_files", "📁  FILES",
            self.file_list_panel, Qt.DockWidgetArea.LeftDockWidgetArea, host
        )
        self.dock_files.setVisible(False)

        self.frame_grid_panel = FrameGridPanel()
        self.dock_frames = self._wrap_dock(
            "dock_frames", "🎞  FRAMES",
            self.frame_grid_panel, Qt.DockWidgetArea.LeftDockWidgetArea, host
        )
        self.dock_frames.setVisible(False)

        self.roi_panel = RoiPanel()
        self.dock_roi = self._wrap_dock(
            "dock_roi", "📐  ROI LIST",
            self.roi_panel, Qt.DockWidgetArea.RightDockWidgetArea, host
        )
        self.dock_roi.setVisible(False)

        # ── Mirror/Align Result Panel (Right, hidden until Mirror/Align tab) ─
        scan_result_widget = self._create_scan_result_panel()
        self.dock_scan_result = self._wrap_dock(
            "dock_scan_result", "📋  CAPTURED FRAMES",
            scan_result_widget, Qt.DockWidgetArea.RightDockWidgetArea, host
        )
        self.dock_scan_result.setMinimumWidth(300)
        self.dock_scan_result.setVisible(False)

        # ── AutoFocus Result Panel (Right, hidden until AF tab) ───────
        af_result_widget = self._create_af_result_panel()
        self.dock_af_result = self._wrap_dock(
            "dock_af_result", "📋  CAPTURED FRAMES",
            af_result_widget, Qt.DockWidgetArea.RightDockWidgetArea, host
        )
        self.dock_af_result.setMinimumWidth(300)
        self.dock_af_result.setVisible(False)

        # ── Analysis Toolbar (Hidden in Live) ─────────────────────────
        self.analysis_toolbar = QToolBar("Analysis")
        self.analysis_toolbar.setObjectName("analysis_toolbar")
        self.analysis_toolbar.setIconSize(QSize(18, 18))
        self.analysis_toolbar.setStyleSheet(f"""
            QToolBar {{ background: {C_BG_DARK}; border-bottom: 1px solid #1a4060; padding: 2px 6px; spacing: 4px; }}
            QToolButton {{ background: #0d1e38; color: #4ecdc4; border: 1px solid #1a4060; border-radius: 3px; padding: 3px 8px; font-weight: bold; font-size: 11px; }}
            QToolButton:hover {{ background: #1a3a60; }}
            QToolButton:checked {{ background: #1a3010; color: #4ecdc4; border-color: #2a6020; }}
        """)
        host.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.analysis_toolbar)
        self.analysis_toolbar.setVisible(False)

        # ── Toolbar Actions ───────────────────────────────────────────
        self.act_an_open = QAction("📂  Open SPE", self)
        self.analysis_toolbar.addAction(self.act_an_open)
        self.analysis_toolbar.addSeparator()
        
        self.dock_toggles = {}
        for text, dock in [
            ("📋  Files",     self.dock_files),
            ("🎞  Frames",    self.dock_frames),
            ("📈  Plot",      self.dock_plot),
            ("📊  Histogram", self.dock_hist),
            ("📉  Proc",      self.dock_proc_stats),
            ("🔲  ROI",       self.dock_roi),
            ("📷  Scan",      self.dock_scan_result),
            ("🔍  AF",        self.dock_af_result),
        ]:
            act = QAction(text, self)
            act.setCheckable(True)
            act.setChecked(dock.isVisible())
            act.triggered.connect(dock.setVisible)
            self.analysis_toolbar.addAction(act)
            self.dock_toggles[dock.objectName()] = act

        self.analysis_toolbar.addSeparator()
        self.act_an_roi_range = QAction("🎯  ROI Range", self)
        self.act_an_roi_range.setCheckable(True)
        self.analysis_toolbar.addAction(self.act_an_roi_range)
        
        self.act_an_fit = QAction("⟳  Reset View", self)
        self.analysis_toolbar.addAction(self.act_an_fit)

        # 기본적으로 좌우 분할 (Proc 는 우측에 별도 dock, 토글로 표시)
        host.splitDockWidget(self.dock_plot, self.dock_hist, Qt.Orientation.Horizontal)
        return host

    def _create_scan_result_panel(self) -> QWidget:
        """우측 CAPTURED FRAMES 패널: 썸네일 + Centroid 플롯 + 로그 + 결과 테이블."""
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            "QSplitter::handle:vertical { background:#1a3a60; height:4px; margin:1px 0; }"
            "QSplitter::handle:vertical:hover { background:#4ecdc4; }"
        )

        # ── 패널 1: 썸네일 리스트 ─────────────────────────────────────
        frames_widget = QWidget()
        frames_widget.setStyleSheet(f"background:{C_BG_DARK};")
        frames_layout = QVBoxLayout(frames_widget)
        frames_layout.setContentsMargins(6, 4, 6, 4)
        frames_layout.setSpacing(4)

        lbl_frames = QLabel("CAPTURED FRAMES")
        lbl_frames.setStyleSheet(
            "color:#4ecdc4; font-size:13px; font-weight:bold;"
            " letter-spacing:2px; padding:0;"
        )
        frames_layout.addWidget(lbl_frames)

        self.da_frame_list = QListWidget()
        self.da_frame_list.setIconSize(QSize(80, 60))
        self.da_frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self.da_frame_list.setWrapping(False)
        self.da_frame_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.da_frame_list.setMinimumHeight(60)
        self.da_frame_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.da_frame_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.da_frame_list.setStyleSheet(
            f"QListWidget {{ background:{C_BG_DEEP}; border:1px solid #0f3460; color:#c0d0ff; }}"
            "QListWidget::item { padding:2px; border:1px solid #0f2040; }"
            "QListWidget::item:selected { background:#1a3a60; border:1px solid #4ecdc4; }"
        )
        frames_layout.addWidget(self.da_frame_list)
        splitter.addWidget(frames_widget)

        # ── 패널 2: Centroid 플롯 ─────────────────────────────────────
        self.da_plot_panel = PlotPanel("Centroid X/Y vs Motor Position")
        self.da_plot_panel.setMinimumHeight(100)
        splitter.addWidget(self.da_plot_panel)

        # ── 패널 3: 로그 ──────────────────────────────────────────────
        self.da_log = QTextEdit()
        self.da_log.setReadOnly(True)
        self.da_log.setMinimumHeight(40)
        self.da_log.setStyleSheet(
            f"QTextEdit {{ background:{C_BG_DEEP}; border:1px solid #0f3460;"
            " color:#00cc88; font-family:'Courier New'; font-size:12px; }"
        )
        splitter.addWidget(self.da_log)

        # ── 패널 4: 결과 테이블 ───────────────────────────────────────
        self.da_table = QTableWidget()
        self.da_table.setColumnCount(10)
        self.da_table.setHorizontalHeaderLabels(
            ["Step", "M1", "M2", "M3", "M4", "CentX", "CentY", "σX", "σY", "SNR"]
        )
        self.da_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.da_table.horizontalHeader().setStretchLastSection(True)
        self.da_table.setMinimumHeight(40)
        self.da_table.setStyleSheet(
            f"QTableWidget {{ background:{C_BG_DEEP}; gridline-color:#0f3460;"
            " color:#c0d0ff; font-family:'Courier New'; font-size:12px;"
            " border:none; }"
            f"QHeaderView::section {{ background:{C_BG_MED}; color:#4ecdc4;"
            " border:1px solid #0f3460; font-weight:bold;"
            " padding:4px 2px; }"
            "QTableWidget::item:selected { background:#1a3a60; }"
        )
        self.da_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        splitter.addWidget(self.da_table)

        splitter.setSizes([120, 240, 100, 180])
        return splitter

    def _create_af_result_panel(self) -> QWidget:
        """AutoFocus 전용 우측 패널: 썸네일 + Sharpness vs Z 플롯 + 결과 테이블."""
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            "QSplitter::handle:vertical { background:#1a3a60; height:4px; margin:1px 0; }"
            "QSplitter::handle:vertical:hover { background:#4ecdc4; }"
        )

        # ── 패널 1: 썸네일 ────────────────────────────────────────────
        frames_widget = QWidget()
        frames_widget.setStyleSheet(f"background:{C_BG_DARK};")
        frames_layout = QVBoxLayout(frames_widget)
        frames_layout.setContentsMargins(6, 4, 6, 4)
        frames_layout.setSpacing(4)

        lbl_frames = QLabel("CAPTURED FRAMES")
        lbl_frames.setStyleSheet(
            "color:#4ecdc4; font-size:13px; font-weight:bold;"
            " letter-spacing:2px;"
        )
        frames_layout.addWidget(lbl_frames)

        self.af_frame_list = QListWidget()
        self.af_frame_list.setIconSize(QSize(80, 60))
        self.af_frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self.af_frame_list.setWrapping(False)
        self.af_frame_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.af_frame_list.setMinimumHeight(60)
        self.af_frame_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.af_frame_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.af_frame_list.setStyleSheet(
            f"QListWidget {{ background:{C_BG_DEEP}; border:1px solid #0f3460; color:#c0d0ff; }}"
            "QListWidget::item { padding:2px; border:1px solid #0f2040; }"
            "QListWidget::item:selected { background:#1a3a60; border:1px solid #4ecdc4; }"
        )
        frames_layout.addWidget(self.af_frame_list)
        splitter.addWidget(frames_widget)

        # ── 패널 2: Sharpness vs Z 플롯 ──────────────────────────────
        self.af_plot_panel = PlotPanel("SHARPNESS vs Z")
        self.af_plot_panel.plot_widget.setLabel("bottom", "Step", color="#8899aa")
        self.af_plot_panel.plot_widget.setLabel("left", "Sharpness (a.u.)", color="#8899aa")
        self.af_plot_panel.setMinimumHeight(100)
        splitter.addWidget(self.af_plot_panel)

        # ── 패널 3: 결과 테이블 ───────────────────────────────────────
        tbl_header = QLabel("RESULTS TABLE")
        tbl_header.setStyleSheet(
            f"color:#4ecdc4; font-size:13px; font-weight:bold;"
            f" letter-spacing:2px; background:{C_BG_DARK}; padding:4px 6px;"
        )

        self.af_table = QTableWidget()
        self.af_table.setColumnCount(3)
        self.af_table.setHorizontalHeaderLabels(["Step", "Z (µm)", "Sharpness"])
        self.af_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.af_table.setMinimumHeight(40)
        self.af_table.setStyleSheet(
            f"QTableWidget {{ background:{C_BG_DEEP}; gridline-color:#0f3460;"
            " color:#c0d0ff; font-family:'Courier New'; font-size:12px; border:none; }"
            f"QHeaderView::section {{ background:{C_BG_MED}; color:#4ecdc4;"
            " border:1px solid #0f3460; font-weight:bold; padding:4px 2px; }"
            "QTableWidget::item:selected { background:#1a3a60; }"
        )
        self.af_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        tbl_widget = QWidget()
        tbl_widget.setStyleSheet(f"background:{C_BG_DARK};")
        tbl_lay = QVBoxLayout(tbl_widget)
        tbl_lay.setContentsMargins(0, 0, 0, 0)
        tbl_lay.setSpacing(0)
        tbl_lay.addWidget(tbl_header)
        tbl_lay.addWidget(self.af_table)
        splitter.addWidget(tbl_widget)

        splitter.setSizes([120, 360, 200])
        return splitter

    def _create_cam_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        editor_combo_style = f"""
            QComboBox {{
                background: {C_BG_DEEP};
                color: #22d3ee;
                border: 1px solid {C_BORDER};
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }}
            QComboBox:hover {{
                border-color: #22d3ee;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
        """
        editor_spin_style = f"""
            QAbstractSpinBox {{
                background: {C_BG_DEEP};
                color: #22d3ee;
                border: 1px solid {C_BORDER};
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }}
            QAbstractSpinBox:hover {{
                border-color: #22d3ee;
            }}
            QAbstractSpinBox::up-button,
            QAbstractSpinBox::down-button {{
                width: 16px;
                background: {C_BG_MED};
                border-left: 1px solid #334155;
            }}
            QAbstractSpinBox::up-button:hover,
            QAbstractSpinBox::down-button:hover {{
                background: #172036;
            }}
        """
        editor_line_style = f"""
            QLineEdit {{
                background: {C_BG_DEEP};
                color: #22d3ee;
                border: 1px solid {C_BORDER};
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }}
            QLineEdit:hover {{
                border-color: #22d3ee;
            }}
        """

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        p_lay = QVBoxLayout(container)
        p_lay.setContentsMargins(10, 10, 10, 10)
        p_lay.setSpacing(8)

        conn_grp = self._make_section("CAMERA DEVICE CONNECTION", "#64748b")
        conn_grp.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        conn_grp.content_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        cl = QVBoxLayout(conn_grp.content_widget)
        cl.setSpacing(4)
        cl.setContentsMargins(6, 6, 6, 6)

        vg_frame = QFrame()
        vg_frame.setFixedHeight(28)
        vg_frame.setStyleSheet("QFrame { border: 1px solid #1e293b; }")
        vg = QHBoxLayout(vg_frame)
        vg.setContentsMargins(0, 0, 0, 0)
        vg.setSpacing(0)
        lbl_vendor = QLabel(" VENDOR:")
        lbl_vendor.setFixedWidth(80)
        lbl_vendor.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;"
            " border-right: 1px solid #1e293b; padding: 0 6px;"
            " background: rgba(30,41,59,0.2);"
        )
        vg.addWidget(lbl_vendor)
        self.cb_vendor = QComboBox()
        self.cb_vendor.addItems(["HIKVISION", "Picam", "Simulation"])
        self.cb_vendor.setStyleSheet(editor_combo_style)
        self.cb_vendor.setCurrentIndex(2)
        vg.addWidget(self.cb_vendor, 1)
        cl.addWidget(vg_frame)

        self.cam_list = QListWidget()
        self.cam_list.setFixedHeight(85)
        self.cam_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.cam_list.setStyleSheet(
            f"background: {C_BG_DEEP}; border: 1px solid #1e293b; color: {C_TEXT_DIM}; font-size: 11px;"
        )
        cl.addWidget(self.cam_list)

        self.btn_scan = self._style_btn("SCAN", "#64748b")
        self.btn_scan.setFixedHeight(24)
        cl.addWidget(self.btn_scan)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(6)
        self.btn_connect = self._style_btn("CONNECT", "#14b8a6")
        self.btn_disconnect = self._style_btn("DISCONNECT", "#ef4444")
        self.btn_connect.setFixedHeight(24)
        self.btn_disconnect.setFixedHeight(24)
        conn_row.addWidget(self.btn_connect)
        conn_row.addWidget(self.btn_disconnect)
        cl.addLayout(conn_row)
        p_lay.addWidget(conn_grp)

        # 연결 후에만 표시되는 설정 섹션 컨테이너
        self.cam_connected_settings = QWidget()
        cam_settings_lay = QVBoxLayout(self.cam_connected_settings)
        cam_settings_lay.setContentsMargins(0, 0, 0, 0)
        cam_settings_lay.setSpacing(8)
        self.cam_connected_settings.setVisible(False)
        p_lay.addWidget(self.cam_connected_settings)

        acq_grp = self._make_section("IMAGE ACQUISITION", "#22d3ee")
        al = QVBoxLayout(acq_grp.content_widget)
        al.setSpacing(8)
        al.setContentsMargins(10, 10, 10, 10)

        exp_row = QHBoxLayout()
        lbl_exp = QLabel("Exposure (ms):")
        lbl_exp.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.spin_exposure = QDoubleSpinBox()
        self.spin_exposure.setRange(0.01, 1_000_000.0)
        self.spin_exposure.setDecimals(2)
        self.spin_exposure.setValue(20.0)
        self.spin_exposure.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_exposure.setStyleSheet(editor_spin_style)
        self.btn_apply_exp = self._style_btn("APPLY", "#14b8a6")
        self.btn_apply_exp.setMinimumWidth(86)
        exp_row.addWidget(lbl_exp)
        exp_row.addWidget(self.spin_exposure, 1)
        exp_row.addWidget(self.btn_apply_exp)
        al.addLayout(exp_row)
        self.lbl_exp_range = QLabel("")
        self.lbl_exp_range.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px;")
        al.addWidget(self.lbl_exp_range)

        self.sec_fps = QFrame()
        fps_lay = QHBoxLayout(self.sec_fps)
        fps_lay.setContentsMargins(0, 0, 0, 0)
        self.check_fps_lock = QCheckBox("Lock FPS")
        self.check_fps_lock.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px;")
        self.spin_fps = QDoubleSpinBox()
        self.spin_fps.setRange(0.1, 1000.0)
        self.spin_fps.setValue(30.0)
        self.spin_fps.setSuffix(" fps")
        self.spin_fps.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_fps.setStyleSheet(editor_spin_style)
        self.btn_apply_fps = self._style_btn("APPLY", "#14b8a6")
        self.btn_apply_fps.setMinimumWidth(86)
        fps_lay.addWidget(self.check_fps_lock)
        fps_lay.addWidget(self.spin_fps, 1)
        fps_lay.addWidget(self.btn_apply_fps)
        al.addWidget(self.sec_fps)
        self.lbl_fps_range = QLabel("")
        self.lbl_fps_range.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 10px;")
        al.addWidget(self.lbl_fps_range)
        cam_settings_lay.addWidget(acq_grp)

        self.sec_adc = self._make_section("ADC SETTINGS", "#22d3ee")
        adl = QVBoxLayout(self.sec_adc.content_widget)
        adl.setSpacing(6)
        adl.setContentsMargins(10, 10, 10, 10)
        self.cb_adc_quality = QComboBox()
        self.cb_adc_speed = QComboBox()
        self.cb_adc_gain = QComboBox()
        self.cb_adc_bit = QComboBox()
        for lbl_text, cb in [
            ("Quality:", self.cb_adc_quality),
            ("Speed:", self.cb_adc_speed),
            ("Gain:", self.cb_adc_gain),
            ("Bit Depth:", self.cb_adc_bit),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
            cb.setStyleSheet(editor_combo_style)
            row.addWidget(lbl)
            row.addWidget(cb, 1)
            adl.addLayout(row)
        self.btn_apply_adc = self._style_btn("APPLY ADC", "#14b8a6")
        adl.addWidget(self.btn_apply_adc)
        cam_settings_lay.addWidget(self.sec_adc)

        self.sec_temp = self._make_section("TEMPERATURE", "#22d3ee")
        tl = QVBoxLayout(self.sec_temp.content_widget)
        tl.setSpacing(6)
        tl.setContentsMargins(10, 10, 10, 10)
        trow = QHBoxLayout()
        lbl_temp = QLabel("Setpoint (C):")
        lbl_temp.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(-100.0, 50.0)
        self.spin_temp.setValue(-70.0)
        self.spin_temp.setDecimals(2)
        self.spin_temp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_temp.setStyleSheet(editor_spin_style)
        self.btn_apply_temp = self._style_btn("SET", "#14b8a6")
        self.btn_apply_temp.setMinimumWidth(72)
        trow.addWidget(lbl_temp)
        trow.addWidget(self.spin_temp, 1)
        trow.addWidget(self.btn_apply_temp)
        tl.addLayout(trow)
        self.lbl_temp_read = QLabel("Reading: ---")
        self.lbl_temp_set = QLabel("Setpoint: ---")
        self.lbl_temp_state = QLabel("Status: ---")
        for item in (self.lbl_temp_read, self.lbl_temp_set, self.lbl_temp_state):
            item.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
            tl.addWidget(item)
        cam_settings_lay.addWidget(self.sec_temp)

        # ── BACKGROUND SUBTRACTION ────────────────────────────────────────────
        bg_grp = self._make_section("BACKGROUND", "#a855f7")
        bl = QVBoxLayout(bg_grp.content_widget)
        bl.setSpacing(7)
        bl.setContentsMargins(10, 10, 10, 10)

        _lbl_s = f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;"

        # Frames count
        bg_frames_row = QHBoxLayout()
        lbl_bgf = QLabel("Frames:")
        lbl_bgf.setStyleSheet(_lbl_s)
        self.spin_bg_frames = QSpinBox()
        self.spin_bg_frames.setRange(1, 100)
        self.spin_bg_frames.setValue(5)
        self.spin_bg_frames.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_bg_frames.setStyleSheet(editor_spin_style)
        bg_frames_row.addWidget(lbl_bgf)
        bg_frames_row.addWidget(self.spin_bg_frames, 1)
        bl.addLayout(bg_frames_row)

        # Save As filename + browse
        bg_name_row = QHBoxLayout()
        lbl_bgn = QLabel("Save As:")
        lbl_bgn.setStyleSheet(_lbl_s)
        self.edit_bg_filename = QLineEdit("background")
        self.edit_bg_filename.setStyleSheet(editor_line_style)
        self.btn_bg_browse = QPushButton("📁")
        self.btn_bg_browse.setFixedWidth(30)
        self.btn_bg_browse.setToolTip("저장 폴더 선택")
        self.btn_bg_browse.setStyleSheet(f"""
            QPushButton {{
                background: {C_BG_MED}; color: {C_TEXT};
                border: 1px solid #334155; border-radius: 4px; font-weight: 900;
            }}
            QPushButton:hover {{ border-color: #a855f7; color: #a855f7; }}
        """)
        bg_name_row.addWidget(lbl_bgn)
        bg_name_row.addWidget(self.edit_bg_filename, 1)
        bg_name_row.addWidget(self.btn_bg_browse)
        bl.addLayout(bg_name_row)

        # Action buttons
        bg_action_row = QHBoxLayout()
        self.btn_bg_capture = self._style_btn("▶  CAPTURE BG", "#a855f7")
        self.btn_bg_load    = self._style_btn("📂  LOAD SPE...", "#a855f7")
        self.btn_bg_capture.setEnabled(False)
        bg_action_row.addWidget(self.btn_bg_capture)
        bg_action_row.addWidget(self.btn_bg_load)
        bl.addLayout(bg_action_row)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #1e293b;")
        bl.addWidget(divider)

        # Status label
        self.lbl_bg_status = QLabel("No background set")
        self.lbl_bg_status.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 11px; font-weight: bold;")
        self.lbl_bg_status.setWordWrap(True)
        bl.addWidget(self.lbl_bg_status)

        # Use BG checkbox
        self.check_use_bg = QCheckBox("Use Background Subtraction")
        self.check_use_bg.setEnabled(False)
        self.check_use_bg.setStyleSheet(f"""
            QCheckBox {{ color: {C_TEXT}; font-size: 13px; font-weight: 700; spacing: 8px; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px; border-radius: 3px;
                border: 1px solid #64748b; background: {C_BG_DEEP};
            }}
            QCheckBox::indicator:hover {{ border-color: #a855f7; }}
            QCheckBox::indicator:checked {{ border-color: #a855f7; background: #a855f7; }}
            QCheckBox:disabled {{ color: {C_TEXT_DEAD}; }}
        """)
        bl.addWidget(self.check_use_bg)

        # Clear button
        self.btn_bg_clear = self._style_btn("CLEAR", "#64748b")
        self.btn_bg_clear.setEnabled(False)
        bl.addWidget(self.btn_bg_clear)

        p_lay.addWidget(bg_grp)
        # ─────────────────────────────────────────────────────────────────────

        # ── IMAGE PROCESSING ──────────────────────────────────────────────────
        ip_grp = self._make_section("IMAGE PROCESSING", "#0ea5e9")
        il = QVBoxLayout(ip_grp.content_widget)
        il.setSpacing(8)
        il.setContentsMargins(10, 10, 10, 10)

        _lbl_s2 = f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;"
        _radio_style = """
            QRadioButton {
                color: #e2e8f0; font-size: 13px; font-weight: 700; spacing: 8px;
            }
            QRadioButton::indicator {
                width: 15px; height: 15px; border-radius: 8px;
                border: 1px solid #64748b; background: #020617;
            }
            QRadioButton::indicator:hover  { border-color: #0ea5e9; }
            QRadioButton::indicator:checked {
                border-color: #0ea5e9; background: #0ea5e9;
            }
            QRadioButton:disabled { color: #2a3547; }
            QRadioButton::indicator:disabled {
                border-color: #1e293b; background: #0a0f1a;
            }
        """

        # Use checkbox — 항상 활성 (Mode 3 가 proc image 없이도 동작)
        self.check_use_proc = QCheckBox("Use Image Processing")
        self.check_use_proc.setEnabled(True)
        self.check_use_proc.setStyleSheet(f"""
            QCheckBox {{ color: {C_TEXT}; font-size: 13px; font-weight: 700; spacing: 8px; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px; border-radius: 3px;
                border: 1px solid #64748b; background: {C_BG_DEEP};
            }}
            QCheckBox::indicator:hover  {{ border-color: #0ea5e9; }}
            QCheckBox::indicator:checked {{ border-color: #0ea5e9; background: #0ea5e9; }}
            QCheckBox:disabled {{ color: {C_TEXT_DEAD}; }}
        """)
        il.addWidget(self.check_use_proc)

        # Mode selector — 1/2 는 proc image 필요, 3 은 raw 자체 통계만
        mode_row = QHBoxLayout()
        lbl_mode = QLabel("Mode:")
        lbl_mode.setStyleSheet(_lbl_s2)
        self.radio_proc_mode1 = QRadioButton("1 sub")
        self.radio_proc_mode2 = QRadioButton("2 div")
        self.radio_proc_mode3 = QRadioButton("3 analyze")
        self.radio_proc_mode1.setChecked(True)
        self.radio_proc_mode1.setEnabled(False)
        self.radio_proc_mode2.setEnabled(False)
        self.radio_proc_mode3.setEnabled(False)
        for rb in (self.radio_proc_mode1, self.radio_proc_mode2, self.radio_proc_mode3):
            rb.setStyleSheet(_radio_style)
        self._proc_mode_group = QButtonGroup()
        self._proc_mode_group.addButton(self.radio_proc_mode1, 1)
        self._proc_mode_group.addButton(self.radio_proc_mode2, 2)
        self._proc_mode_group.addButton(self.radio_proc_mode3, 3)
        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.radio_proc_mode1)
        mode_row.addWidget(self.radio_proc_mode2)
        mode_row.addWidget(self.radio_proc_mode3)
        mode_row.addStretch()
        il.addLayout(mode_row)

        # Region selector — Full image vs 첫 번째 Box ROI 영역만
        region_row = QHBoxLayout()
        lbl_region = QLabel("Region:")
        lbl_region.setStyleSheet(_lbl_s2)
        self.radio_region_full = QRadioButton("Full")
        self.radio_region_roi  = QRadioButton("Box ROI")
        self.radio_region_full.setChecked(True)
        self.radio_region_full.setEnabled(False)
        self.radio_region_roi.setEnabled(False)
        for rb in (self.radio_region_full, self.radio_region_roi):
            rb.setStyleSheet(_radio_style)
        self._proc_region_group = QButtonGroup()
        self._proc_region_group.addButton(self.radio_region_full, 0)
        self._proc_region_group.addButton(self.radio_region_roi,  1)
        region_row.addWidget(lbl_region)
        region_row.addWidget(self.radio_region_full)
        region_row.addWidget(self.radio_region_roi)
        region_row.addStretch()
        il.addLayout(region_row)

        self.btn_proc_load = self._style_btn("LOAD IMAGE", "#0ea5e9")
        il.addWidget(self.btn_proc_load)

        ip_div = QFrame()
        ip_div.setFrameShape(QFrame.Shape.HLine)
        ip_div.setStyleSheet("color: #1e293b;")
        il.addWidget(ip_div)

        self.lbl_proc_status = QLabel("No image loaded")
        self.lbl_proc_status.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 11px; font-weight: bold;")
        self.lbl_proc_status.setWordWrap(True)
        il.addWidget(self.lbl_proc_status)

        # ROI 그리기 행: SIGNAL | BG
        draw_row = QHBoxLayout()
        draw_row.setSpacing(6)
        self.btn_draw_sig_roi = self._style_btn("DRAW SIGNAL", "#e94560")
        self.btn_draw_sig_roi.setEnabled(False)
        self.btn_draw_bg_roi = self._style_btn("DRAW BG", "#38bdf8")
        self.btn_draw_bg_roi.setEnabled(False)
        draw_row.addWidget(self.btn_draw_sig_roi)
        draw_row.addWidget(self.btn_draw_bg_roi)
        il.addLayout(draw_row)

        # ROI 액션 행: AUTO-REFINE | RESET ROI
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.btn_auto_refine_roi = self._style_btn("AUTO-REFINE", "#8b5cf6")
        self.btn_auto_refine_roi.setEnabled(False)
        self.btn_clear_roi = self._style_btn("RESET ROI", "#64748b")
        self.btn_clear_roi.setEnabled(False)
        action_row.addWidget(self.btn_auto_refine_roi)
        action_row.addWidget(self.btn_clear_roi)
        il.addLayout(action_row)

        self.lbl_sig_roi_status = QLabel("Signal ROI: —")
        self.lbl_sig_roi_status.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 10px;")
        il.addWidget(self.lbl_sig_roi_status)

        # Auto-Refine 파라미터 행 (Threshold / Blur / Margin)
        _dspin_style = f"""
            QDoubleSpinBox {{
                background: #0a0f1a; color: {C_TEXT}; border: 1px solid #1e293b;
                border-radius: 3px; padding: 1px 4px; font-size: 11px;
            }}
            QDoubleSpinBox:disabled {{ color: {C_TEXT_DEAD}; border-color: #0d1829; }}
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                width: 14px; border: none; background: #1e293b;
            }}
        """
        _spin_s2 = f"""
            QSpinBox {{
                background: #0a0f1a; color: {C_TEXT}; border: 1px solid #1e293b;
                border-radius: 3px; padding: 1px 4px; font-size: 11px;
            }}
            QSpinBox:disabled {{ color: {C_TEXT_DEAD}; border-color: #0d1829; }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: 14px; border: none; background: #1e293b;
            }}
        """
        refine_row1 = QHBoxLayout()
        refine_row1.setSpacing(6)
        lbl_rthr = QLabel("Thr:")
        lbl_rthr.setStyleSheet(_lbl_s2)
        self.spin_refine_threshold = QDoubleSpinBox()
        self.spin_refine_threshold.setRange(1.0, 99.0)
        self.spin_refine_threshold.setValue(70.0)
        self.spin_refine_threshold.setSingleStep(5.0)
        self.spin_refine_threshold.setSuffix(" %")
        self.spin_refine_threshold.setFixedWidth(68)
        self.spin_refine_threshold.setEnabled(False)
        self.spin_refine_threshold.setStyleSheet(_dspin_style)
        lbl_rblur = QLabel("Blur:")
        lbl_rblur.setStyleSheet(_lbl_s2)
        self.spin_refine_blur = QDoubleSpinBox()
        self.spin_refine_blur.setRange(0.0, 10.0)
        self.spin_refine_blur.setValue(2.0)
        self.spin_refine_blur.setSingleStep(0.5)
        self.spin_refine_blur.setSuffix(" σ")
        self.spin_refine_blur.setFixedWidth(62)
        self.spin_refine_blur.setEnabled(False)
        self.spin_refine_blur.setStyleSheet(_dspin_style)
        refine_row1.addWidget(lbl_rthr)
        refine_row1.addWidget(self.spin_refine_threshold)
        refine_row1.addSpacing(6)
        refine_row1.addWidget(lbl_rblur)
        refine_row1.addWidget(self.spin_refine_blur)
        refine_row1.addStretch()
        il.addLayout(refine_row1)

        refine_row2 = QHBoxLayout()
        refine_row2.setSpacing(6)
        lbl_rmargin = QLabel("Margin:")
        lbl_rmargin.setStyleSheet(_lbl_s2)
        self.spin_refine_margin = QSpinBox()
        self.spin_refine_margin.setRange(0, 30)
        self.spin_refine_margin.setValue(5)
        self.spin_refine_margin.setSuffix(" px")
        self.spin_refine_margin.setFixedWidth(62)
        self.spin_refine_margin.setEnabled(False)
        self.spin_refine_margin.setStyleSheet(_spin_s2)
        lbl_rexp = QLabel("Expand:")
        lbl_rexp.setStyleSheet(_lbl_s2)
        self.spin_refine_expand = QSpinBox()
        self.spin_refine_expand.setRange(0, 200)
        self.spin_refine_expand.setValue(0)
        self.spin_refine_expand.setSuffix(" px")
        self.spin_refine_expand.setFixedWidth(68)
        self.spin_refine_expand.setEnabled(False)
        self.spin_refine_expand.setStyleSheet(_spin_s2)
        refine_row2.addWidget(lbl_rmargin)
        refine_row2.addWidget(self.spin_refine_margin)
        refine_row2.addSpacing(6)
        refine_row2.addWidget(lbl_rexp)
        refine_row2.addWidget(self.spin_refine_expand)
        refine_row2.addStretch()
        il.addLayout(refine_row2)

        # BG ROI 모드 행 (Ring / Manual / None)
        bg_roi_row = QHBoxLayout()
        bg_roi_row.setSpacing(6)
        lbl_bg = QLabel("BG ROI:")
        lbl_bg.setStyleSheet(_lbl_s2)
        self.radio_bg_ring   = QRadioButton("Ring")
        self.radio_bg_manual = QRadioButton("Manual")
        self.radio_bg_none   = QRadioButton("None")
        self.radio_bg_ring.setChecked(True)
        for rb in (self.radio_bg_ring, self.radio_bg_manual, self.radio_bg_none):
            rb.setEnabled(False)
            rb.setStyleSheet(_radio_style)
        self._proc_bg_group = QButtonGroup()
        self._proc_bg_group.addButton(self.radio_bg_ring,   0)
        self._proc_bg_group.addButton(self.radio_bg_manual, 1)
        self._proc_bg_group.addButton(self.radio_bg_none,   2)
        bg_roi_row.addWidget(lbl_bg)
        bg_roi_row.addWidget(self.radio_bg_ring)
        bg_roi_row.addWidget(self.radio_bg_manual)
        bg_roi_row.addWidget(self.radio_bg_none)
        bg_roi_row.addStretch()
        il.addLayout(bg_roi_row)

        # Ring 파라미터 행 (Gap / Thickness)
        _spin_style = f"""
            QSpinBox {{
                background: #0a0f1a; color: {C_TEXT}; border: 1px solid #1e293b;
                border-radius: 3px; padding: 1px 4px; font-size: 11px;
            }}
            QSpinBox:disabled {{ color: {C_TEXT_DEAD}; border-color: #0d1829; }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: 14px; border: none; background: #1e293b;
            }}
        """
        ring_row = QHBoxLayout()
        ring_row.setSpacing(6)

        lbl_gap = QLabel("Gap:")
        lbl_gap.setStyleSheet(_lbl_s2)
        self.spin_bg_gap = QSpinBox()
        self.spin_bg_gap.setRange(0, 50)
        self.spin_bg_gap.setValue(2)
        self.spin_bg_gap.setSuffix(" px")
        self.spin_bg_gap.setFixedWidth(62)
        self.spin_bg_gap.setEnabled(False)
        self.spin_bg_gap.setStyleSheet(_spin_style)

        lbl_thick = QLabel("Width:")
        lbl_thick.setStyleSheet(_lbl_s2)
        self.spin_bg_thickness = QSpinBox()
        self.spin_bg_thickness.setRange(2, 100)
        self.spin_bg_thickness.setValue(10)
        self.spin_bg_thickness.setSuffix(" px")
        self.spin_bg_thickness.setFixedWidth(62)
        self.spin_bg_thickness.setEnabled(False)
        self.spin_bg_thickness.setStyleSheet(_spin_style)

        ring_row.addWidget(lbl_gap)
        ring_row.addWidget(self.spin_bg_gap)
        ring_row.addSpacing(8)
        ring_row.addWidget(lbl_thick)
        ring_row.addWidget(self.spin_bg_thickness)
        ring_row.addStretch()
        il.addLayout(ring_row)

        pitch_row = QHBoxLayout()
        pitch_row.setSpacing(6)
        lbl_pitch = QLabel("Pitch:")
        lbl_pitch.setStyleSheet(_lbl_s2)
        self.spin_pitch_nm = QDoubleSpinBox()
        self.spin_pitch_nm.setRange(1.0, 1000.0)
        self.spin_pitch_nm.setDecimals(0)
        self.spin_pitch_nm.setValue(72.0)
        self.spin_pitch_nm.setSuffix(" nm")
        self.spin_pitch_nm.setFixedWidth(68)
        self.spin_pitch_nm.setStyleSheet(_dspin_style)
        pitch_row.addWidget(lbl_pitch)
        pitch_row.addWidget(self.spin_pitch_nm)
        pitch_row.addStretch()
        il.addLayout(pitch_row)

        # BG 상태 라벨
        self.lbl_bg_roi_status = QLabel("—")
        self.lbl_bg_roi_status.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 10px;")
        il.addWidget(self.lbl_bg_roi_status)

        # Proc Stats 옵션 1~17 체크박스 그룹 추가
        if hasattr(self, "proc_stats_panel") and hasattr(self.proc_stats_panel, "options_widget"):
            il.addWidget(self.proc_stats_panel.options_widget)

        p_lay.addWidget(ip_grp)
        # ─────────────────────────────────────────────────────────────────────

        # ── LASER HTTP CONTROL ────────────────────────────────────────────────
        laser_grp = self._make_section("LASER HTTP CONTROL", "#eab308")
        ll = QVBoxLayout(laser_grp.content_widget)
        ll.setSpacing(8)
        ll.setContentsMargins(10, 10, 10, 10)

        # IP & Port Row
        laser_ip_row = QHBoxLayout()
        lbl_lip = QLabel("IP:")
        lbl_lip.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.edit_laser_ip = QLineEdit("127.0.0.1")
        self.edit_laser_ip.setStyleSheet(editor_line_style)
        
        lbl_lport = QLabel("Port:")
        lbl_lport.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold; margin-left: 5px;")
        self.edit_laser_port = QLineEdit("5643")
        self.edit_laser_port.setFixedWidth(60)
        self.edit_laser_port.setStyleSheet(editor_line_style)
        
        laser_ip_row.addWidget(lbl_lip)
        laser_ip_row.addWidget(self.edit_laser_ip, 1)
        laser_ip_row.addWidget(lbl_lport)
        laser_ip_row.addWidget(self.edit_laser_port)
        ll.addLayout(laser_ip_row)

        # Auth Mode Selection Row
        laser_auth_mode_row = QHBoxLayout()
        lbl_lmode = QLabel("Auth Mode:")
        lbl_lmode.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.combo_laser_auth_type = QComboBox()
        self.combo_laser_auth_type.addItems(["ID / PW", "Bearer Token"])
        self.combo_laser_auth_type.setStyleSheet(f"""
            QComboBox {{
                background: {C_BG_MED}; color: {C_TEXT}; border: 1px solid #334155;
                border-radius: 4px; padding: 2px 4px; font-weight: bold;
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        laser_auth_mode_row.addWidget(lbl_lmode)
        laser_auth_mode_row.addWidget(self.combo_laser_auth_type, 1)
        ll.addLayout(laser_auth_mode_row)

        # ID / PW Row Container
        self.laser_idpw_widget = QWidget()
        idpw_lay = QHBoxLayout(self.laser_idpw_widget)
        idpw_lay.setContentsMargins(0, 0, 0, 0)
        
        lbl_lid = QLabel("ID:")
        lbl_lid.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.edit_laser_id = QLineEdit("viewer")
        self.edit_laser_id.setStyleSheet(editor_line_style)
        
        lbl_lpw = QLabel("PW:")
        lbl_lpw.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold; margin-left: 5px;")
        self.edit_laser_pw = QLineEdit()
        self.edit_laser_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_laser_pw.setPlaceholderText("Password (비어있음)")
        self.edit_laser_pw.setStyleSheet(editor_line_style)
        
        idpw_lay.addWidget(lbl_lid)
        idpw_lay.addWidget(self.edit_laser_id, 1)
        idpw_lay.addWidget(lbl_lpw)
        idpw_lay.addWidget(self.edit_laser_pw, 1)
        ll.addWidget(self.laser_idpw_widget)

        # Static Token Row Container
        self.laser_token_widget = QWidget()
        tok_lay = QHBoxLayout(self.laser_token_widget)
        tok_lay.setContentsMargins(0, 0, 0, 0)
        
        lbl_ltok = QLabel("Token:")
        lbl_ltok.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.edit_laser_token = QLineEdit()
        self.edit_laser_token.setPlaceholderText("Plain string or JSON file path (.json)")
        self.edit_laser_token.setStyleSheet(editor_line_style)
        
        self.btn_laser_token_browse = QPushButton("📁")
        self.btn_laser_token_browse.setFixedWidth(30)
        self.btn_laser_token_browse.setToolTip("토큰 JSON 파일 선택")
        self.btn_laser_token_browse.setStyleSheet(f"""
            QPushButton {{
                background: {C_BG_MED}; color: {C_TEXT};
                border: 1px solid #334155; border-radius: 4px; font-weight: 900;
            }}
            QPushButton:hover {{ border-color: #eab308; color: #eab308; }}
        """)
        
        tok_lay.addWidget(lbl_ltok)
        tok_lay.addWidget(self.edit_laser_token, 1)
        tok_lay.addWidget(self.btn_laser_token_browse)
        ll.addWidget(self.laser_token_widget)

        # Laser Temp Alarm Row
        laser_alarm_row = QHBoxLayout()
        self.chk_laser_temp_alarm = QCheckBox("Disk Temp Alarm(<=)")
        self.chk_laser_temp_alarm.setStyleSheet(f"color: {C_TEXT_DIM}; font-weight: bold; font-size: 11px;")
        self.spin_laser_temp_alarm_min = QDoubleSpinBox()
        self.spin_laser_temp_alarm_min.setRange(0, 300.0)
        self.spin_laser_temp_alarm_min.setValue(200.0)
        self.spin_laser_temp_alarm_min.setDecimals(0)
        self.spin_laser_temp_alarm_min.setStyleSheet(editor_spin_style)
        laser_alarm_row.addWidget(self.chk_laser_temp_alarm)
        laser_alarm_row.addWidget(self.spin_laser_temp_alarm_min, 1)
        ll.addLayout(laser_alarm_row)

        # Pulse Energy Setpoint Row
        laser_pe_row = QHBoxLayout()
        lbl_pe = QLabel("Pulse Energy:")
        lbl_pe.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.lbl_laser_pe_current = QLabel("N/A")
        self.lbl_laser_pe_current.setStyleSheet("color: #eab308; font-size: 11px; font-weight: bold; font-family: monospace; min-width: 60px;")
        self.spin_laser_pe = QDoubleSpinBox()
        self.spin_laser_pe.setRange(0.0, 100.0)
        self.spin_laser_pe.setDecimals(0)
        self.spin_laser_pe.setSingleStep(1.0)
        self.spin_laser_pe.setSuffix(" %")
        self.spin_laser_pe.setStyleSheet(editor_spin_style)
        self.btn_laser_pe_set = QPushButton("Set")
        self.btn_laser_pe_set.setFixedWidth(36)
        self.btn_laser_pe_set.setStyleSheet(f"""
            QPushButton {{
                background: {C_BG_MED}; color: #eab308; border: 1px solid #eab308;
                border-radius: 4px; font-weight: bold; font-size: 11px; padding: 2px 4px;
            }}
            QPushButton:hover {{ background: #eab30822; }}
        """)
        laser_pe_row.addWidget(lbl_pe)
        laser_pe_row.addWidget(self.lbl_laser_pe_current)
        laser_pe_row.addWidget(self.spin_laser_pe, 1)
        laser_pe_row.addWidget(self.btn_laser_pe_set)
        ll.addLayout(laser_pe_row)

        # Frequency Setpoint Row
        laser_freq_row = QHBoxLayout()
        lbl_freq = QLabel("Frequency:")
        lbl_freq.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.lbl_laser_freq_current = QLabel("N/A")
        self.lbl_laser_freq_current.setStyleSheet("color: #eab308; font-size: 11px; font-weight: bold; font-family: monospace; min-width: 60px;")
        self.spin_laser_freq = QDoubleSpinBox()
        self.spin_laser_freq.setRange(0.0, 100000.0)
        self.spin_laser_freq.setDecimals(0)
        self.spin_laser_freq.setSingleStep(10.0)
        self.spin_laser_freq.setSuffix(" Hz")
        self.spin_laser_freq.setStyleSheet(editor_spin_style)
        self.btn_laser_freq_set = QPushButton("Set")
        self.btn_laser_freq_set.setFixedWidth(36)
        self.btn_laser_freq_set.setStyleSheet(f"""
            QPushButton {{
                background: {C_BG_MED}; color: #eab308; border: 1px solid #eab308;
                border-radius: 4px; font-weight: bold; font-size: 11px; padding: 2px 4px;
            }}
            QPushButton:hover {{ background: #eab30822; }}
        """)
        laser_freq_row.addWidget(lbl_freq)
        laser_freq_row.addWidget(self.lbl_laser_freq_current)
        laser_freq_row.addWidget(self.spin_laser_freq, 1)
        laser_freq_row.addWidget(self.btn_laser_freq_set)
        ll.addLayout(laser_freq_row)

        # Controls Row
        laser_btn_row = QHBoxLayout()
        self.btn_laser_pulse = self._style_btn("🔴 PULSE OFF", "#ef4444")
        self.btn_laser_pulse.setCheckable(True)
        self.btn_laser_pulse.setStyleSheet("""
            QPushButton {
                background: #1e1e2f; color: #ef4444; border: 1px solid #ef4444;
                border-radius: 4px; font-weight: bold; font-size: 11px; padding: 4px;
            }
            QPushButton:hover {
                background: #ef444422;
            }
        """)
        
        self.btn_laser_hf = self._style_btn("🔴 HIGH OFF", "#ef4444")
        self.btn_laser_hf.setCheckable(True)
        self.btn_laser_hf.setStyleSheet("""
            QPushButton {
                background: #1e1e2f; color: #ef4444; border: 1px solid #ef4444;
                border-radius: 4px; font-weight: bold; font-size: 11px; padding: 4px;
            }
            QPushButton:hover {
                background: #ef444422;
            }
        """)
        
        self.btn_laser_poll = self._style_btn("START POLL", "#eab308")
        self.btn_laser_poll.setCheckable(True)
        self.btn_laser_poll.setStyleSheet("""
            QPushButton {
                background: #1e1e2f; color: #eab308; border: 1px solid #eab308;
                border-radius: 4px; font-weight: bold; font-size: 11px; padding: 4px;
            }
            QPushButton:hover {
                background: #eab30822;
            }
        """)
        laser_btn_row.addWidget(self.btn_laser_pulse, 1)
        laser_btn_row.addWidget(self.btn_laser_hf, 1)
        laser_btn_row.addWidget(self.btn_laser_poll, 1)
        ll.addLayout(laser_btn_row)

        # Status Row
        self.lbl_laser_info = QLabel("Status: Idle")
        self.lbl_laser_info.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 11px; font-weight: bold; font-family: monospace;")
        ll.addWidget(self.lbl_laser_info)

        p_lay.addWidget(laser_grp)
        # ─────────────────────────────────────────────────────────────────────

        save_grp = self._make_section("SAVE", "#22d3ee")
        sl = QVBoxLayout(save_grp.content_widget)
        sl.setSpacing(8)
        sl.setContentsMargins(10, 10, 10, 10)

        save_count_row = QHBoxLayout()
        lbl_count = QLabel("Frame To Save:")
        lbl_count.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        self.spin_frame_to_save = QSpinBox()
        self.spin_frame_to_save.setRange(1, 100)
        self.spin_frame_to_save.setValue(10)
        self.spin_frame_to_save.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_frame_to_save.setStyleSheet(editor_spin_style)
        save_count_row.addWidget(lbl_count)
        save_count_row.addWidget(self.spin_frame_to_save, 1)
        sl.addLayout(save_count_row)

        self.edit_file_base = QLineEdit("Capture")
        self.edit_folder = QLineEdit("Live_Captures")
        self.btn_browse_folder = QPushButton("...")
        self.btn_browse_folder.setFixedWidth(34)
        self.btn_browse_folder.setToolTip("저장 폴더 선택")
        self.check_inc_name = QCheckBox("Increment File Name")
        self.check_add_date = QCheckBox("Add Date")
        self.check_add_date.setChecked(True)
        self.check_add_time = QCheckBox("Add Time")
        self.check_add_time.setChecked(True)
        for chk in (self.check_inc_name, self.check_add_date, self.check_add_time):
            chk.setStyleSheet(f"""
                QCheckBox {{
                    color: {C_TEXT};
                    font-size: 13px;
                    font-weight: 700;
                    spacing: 8px;
                }}
                QCheckBox::indicator {{
                    width: 16px;
                    height: 16px;
                    border-radius: 3px;
                    border: 1px solid #64748b;
                    background: {C_BG_DEEP};
                }}
                QCheckBox::indicator:hover {{
                    border-color: #22d3ee;
                }}
                QCheckBox::indicator:checked {{
                    border-color: #22d3ee;
                    background: #22d3ee;
                }}
            """)

        self.cb_date_fmt = QComboBox(); self.cb_date_fmt.addItems(["YYYY-Month-DD", "YYYY-MM-DD"])
        self.cb_time_fmt = QComboBox(); self.cb_time_fmt.addItems(["hh:mm:ss (24h)", "hh:mm:ss (12h)"])
        self.cb_place = QComboBox(); self.cb_place.addItems(["Suffix", "Prefix"])
        for cb in (self.cb_date_fmt, self.cb_time_fmt, self.cb_place):
            cb.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")

        self.btn_browse_folder.setStyleSheet(
            f"""
            QPushButton {{
                background: {C_BG_MED};
                color: {C_TEXT};
                border: 1px solid #334155;
                border-radius: 4px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                border-color: #22d3ee;
                color: #22d3ee;
            }}
        """
        )

        self.edit_file_base.setStyleSheet(editor_line_style)
        self.edit_folder.setStyleSheet(editor_line_style)

        row_folder = QHBoxLayout()
        lbl_folder = QLabel("Save In:")
        lbl_folder.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        row_folder.addWidget(lbl_folder)
        row_folder.addWidget(self.edit_folder, 1)
        row_folder.addWidget(self.btn_browse_folder)
        sl.addLayout(row_folder)

        row_name = QHBoxLayout()
        lbl_name = QLabel("File Name:")
        lbl_name.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;")
        row_name.addWidget(lbl_name)
        row_name.addWidget(self.edit_file_base, 1)
        sl.addLayout(row_name)

        naming_box = QGroupBox("Naming Options")
        naming_box.setStyleSheet(
            f"""
            QGroupBox {{
                color: {C_TEXT};
                font-size: 13px;
                font-weight: 800;
                background: {C_BG_DEEP};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 6px;
                color: {C_TEXT};
                background: {C_BG_DEEP};
            }}
            QGroupBox QLabel {{
                color: {C_TEXT_DIM};
                font-size: 12px;
                font-weight: 700;
            }}
            QGroupBox QComboBox {{
                background: {C_BG_DEEP};
                color: #22d3ee;
                border: 1px solid {C_BORDER};
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
            }}
            QGroupBox QComboBox:disabled {{
                color: {C_TEXT_DEAD};
                border-color: #334155;
                background: {C_BG_DEEP};
            }}
        """
        )
        nl = QVBoxLayout(naming_box)
        nl.setSpacing(6)
        nl.setContentsMargins(10, 10, 10, 10)

        nl.addWidget(self.check_inc_name)
        nl.addWidget(self.check_add_date)
        row_df = QHBoxLayout(); row_df.addWidget(QLabel("Date Format:")); row_df.addWidget(self.cb_date_fmt, 1)
        row_df.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        nl.addLayout(row_df)
        nl.addWidget(self.check_add_time)
        row_tf = QHBoxLayout(); row_tf.addWidget(QLabel("Time Format:")); row_tf.addWidget(self.cb_time_fmt, 1)
        row_tf.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        nl.addLayout(row_tf)
        row_pl = QHBoxLayout(); row_pl.addWidget(QLabel("Place Date/Time:")); row_pl.addWidget(self.cb_place, 1)
        row_pl.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        nl.addLayout(row_pl)
        sl.addWidget(naming_box)

        self.lbl_save_preview = QLabel("Example File Name: Capture_2026-May-10_00_00_00.spe")
        self.lbl_save_full = QLabel("Full Path: Live_Captures/Capture_2026-May-10_00_00_00.spe")
        self.lbl_save_preview.setWordWrap(True)
        self.lbl_save_full.setWordWrap(True)
        self.lbl_save_preview.setStyleSheet("color: #4ecdc4; font-size: 12px; font-weight: 900;")
        self.lbl_save_full.setStyleSheet("color: #7dd3fc; font-size: 12px; font-weight: 900;")
        sl.addWidget(self.lbl_save_preview)
        sl.addWidget(self.lbl_save_full)

        for widget in [self.edit_file_base, self.edit_folder]:
            widget.textChanged.connect(self._update_save_preview)
        for widget in [self.check_inc_name, self.check_add_date, self.check_add_time]:
            widget.toggled.connect(self._update_save_preview)
            widget.toggled.connect(self._update_save_control_state)
        for widget in [self.cb_date_fmt, self.cb_time_fmt, self.cb_place]:
            widget.currentTextChanged.connect(self._update_save_preview)
        self.btn_browse_folder.clicked.connect(self._on_browse_save_folder)

        self._update_save_control_state()
        self._update_save_preview()
        p_lay.addWidget(save_grp)
        # ─────────────────────────────────────────────────────────────────────

        p_lay.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _on_browse_save_folder(self):
        current_dir = self.edit_folder.text().strip() or os.getcwd()
        selected = QFileDialog.getExistingDirectory(
            self, "Save In", current_dir,
            QFileDialog.Option.DontUseNativeDialog
        )
        if selected:
            self.edit_folder.setText(selected)

    def _update_save_control_state(self):
        has_date = self.check_add_date.isChecked()
        has_time = self.check_add_time.isChecked()
        self.cb_date_fmt.setEnabled(has_date)
        self.cb_time_fmt.setEnabled(has_time)
        self.cb_place.setEnabled(has_date or has_time)

    def _build_filename_stem(self, counter: str = "0001", base_override: str | None = None) -> str:
        """날짜/시간/카운터 토큰을 조합하여 파일명 stem을 반환합니다.

        Args:
            counter: 증분 카운터 문자열 (기본 "0001"). 루프에서 충돌 방지 시 증가시켜 넘깁니다.
            base_override: file_base 강제 지정 (예: "SNAP"). None 이면 SAVE FILE 의 file_base 사용.

        Returns:
            확장자 없는 파일명 stem (예: "Capture_2026-05-14_12_00_00_0001", "SNAP_2026-05-14_12_00_00").
        """
        now = datetime.now()
        tokens: list[str] = []

        if self.check_add_date.isChecked():
            fmt = "%Y-%m-%d" if self.cb_date_fmt.currentText() == "YYYY-MM-DD" else "%Y-%B-%d"
            tokens.append(now.strftime(fmt))

        if self.check_add_time.isChecked():
            fmt = "%I_%M_%S%p" if self.cb_time_fmt.currentText() == "hh:mm:ss (12h)" else "%H_%M_%S"
            tokens.append(now.strftime(fmt))

        if self.check_inc_name.isChecked():
            tokens.append(counter)

        base = base_override if base_override else (self.edit_file_base.text().strip() or "Capture")
        if not tokens:
            return base
        joined = "_".join(tokens)
        return f"{joined}_{base}" if self.cb_place.currentText() == "Prefix" else f"{base}_{joined}"

    def _update_save_preview(self):
        folder = self.edit_folder.text().strip() or "Live_Captures"
        stem = self._build_filename_stem()
        full_name = f"{stem}.spe"
        full_path = os.path.normpath(os.path.join(folder, full_name))
        self.lbl_save_preview.setText(f"Example File Name: {full_name}")
        self.lbl_save_full.setText(f"Full Path: {full_path}")

    def _apply_camera_capabilities(self, caps):
        has_fps = bool(caps and getattr(caps, "has_fps_control", False))
        has_adc = bool(caps and getattr(caps, "has_adc", False))
        has_temp = bool(caps and getattr(caps, "has_temperature", False))

        self.sec_fps.setVisible(has_fps)
        self.sec_adc.setVisible(has_adc)
        self.sec_temp.setVisible(has_temp)

        # 노출 범위 라벨 + spinbox range
        if caps and hasattr(caps, "exposure_range_ms"):
            mn, mx = caps.exposure_range_ms
            self.spin_exposure.setRange(float(mn), float(mx))
            self.lbl_exp_range.setText(f"range: {mn:.3f} ~ {mx:.3f} ms")
        else:
            self.lbl_exp_range.setText("")

        # FPS 범위 라벨 + spinbox range
        if has_fps and caps and hasattr(caps, "fps_range"):
            mn, mx = caps.fps_range
            self.spin_fps.setRange(float(mn), float(mx))
            self.lbl_fps_range.setText(f"range: {mn:.2f} ~ {mx:.2f} fps")
        else:
            self.lbl_fps_range.setText("")

        if has_temp and hasattr(caps, "temperature_range_c"):
            mn, mx = caps.temperature_range_c
            if mn is not None:
                self.spin_temp.setMinimum(float(mn))
            if mx is not None:
                self.spin_temp.setMaximum(float(mx))

        if has_adc:
            # ⚠️ clear() + addItems() 는 currentTextChanged 를 발생시킨다.
            # 이 시그널이 _save_settings 에 연결돼 있어, blockSignals 없이 호출하면
            # 사용자의 저장된 ADC 값이 콤보 첫 항목(또는 빈 문자열) 으로 덮어
            # 씌워진다. (특히 quality 가 자주 망가짐 — 카메라 후보 순서와
            # 사용자 저장값이 다를 때.) 반드시 blockSignals 로 보호.
            combo_opts = [
                (self.cb_adc_quality, getattr(caps, "adc_quality_options",  []), ["High Capacity", "Low Noise"]),
                (self.cb_adc_speed,   getattr(caps, "adc_speed_options",    []), ["100kHz", "1MHz"]),
                (self.cb_adc_gain,    getattr(caps, "adc_gain_options",     []), ["1x", "2x"]),
                (self.cb_adc_bit,     getattr(caps, "adc_bit_depth_options",[]), ["16bit", "12bit"]),
            ]
            for cb, opts, fallback in combo_opts:
                items = opts if opts else fallback
                cb.blockSignals(True)
                try:
                    cb.clear()
                    cb.addItems([str(x) for x in items])
                finally:
                    cb.blockSignals(False)

    def _create_analysis_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        p_lay = QVBoxLayout(container)
        p_lay.setContentsMargins(10, 10, 10, 10)
        p_lay.setSpacing(12)

        # 1. Metrics Section
        sum_grp = self._make_section("📊 ANALYSIS SUMMARY", "#10b981")
        sl = QVBoxLayout(sum_grp.content_widget)
        sl.setContentsMargins(8, 8, 8, 8)
        
        grid = QFrame(); grid.setStyleSheet("border: 1px solid #1e293b; border-radius: 4px;")
        gl = QGridLayout(grid); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(0)
        
        self.lbl_an_peak = QLabel("---")
        self.lbl_an_fwhm = QLabel("---")
        self.lbl_an_snr  = QLabel("---")
        
        metrics = [
            ("PEAK", self.lbl_an_peak),
            ("FWHM", self.lbl_an_fwhm),
            ("SNR",  self.lbl_an_snr)
        ]
        for i, (name, lbl) in enumerate(metrics):
            gl.addWidget(self._grid_lbl(f" {name}"), i, 0)
            lbl.setStyleSheet("color: #10b981; font-size: 13px; font-weight: 900; padding: 6px;")
            gl.addWidget(lbl, i, 1)
        sl.addWidget(grid)
        p_lay.addWidget(sum_grp)

        # 2. Frame Gallery Section
        gal_grp = self._make_section("🎞 CAPTURED GALLERY", "#38bdf8")
        glay = QVBoxLayout(gal_grp.content_widget)
        glay.setContentsMargins(5, 5, 5, 5)
        
        self.list_an_gallery = QListWidget()
        self.list_an_gallery.setFixedHeight(400)
        self.list_an_gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_an_gallery.setIconSize(QSize(100, 100))
        self.list_an_gallery.setSpacing(10)
        self.list_an_gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_an_gallery.setStyleSheet(f"""
            QListWidget {{ background: {C_BG_DEEP}; border: 1px solid #1e293b; border-radius: 4px; }}
            QListWidget::item {{ color: {C_TEXT_DIM}; font-size: 10px; font-weight: bold; }}
            QListWidget::item:selected {{ background: #1e293b; color: #38bdf8; border: 1px solid #38bdf8; }}
        """)
        glay.addWidget(self.list_an_gallery)
        
        self.btn_clear_gal = self._style_btn("CLEAR GALLERY", "#ef4444")
        glay.addWidget(self.btn_clear_gal)
        
        p_lay.addWidget(gal_grp)
        p_lay.addStretch()

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _create_master_bar(self):
        bar = QFrame()
        bar.setFixedHeight(95)
        bar.setStyleSheet(f"background-color: {C_BG_DEEP}; border-top: none;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(15, 10, 15, 5)
        lay.setSpacing(25)

        self.master_btn_stack = QStackedWidget()
        self.master_btn_stack.setFixedSize(540, 72)

        cam_w = QWidget(); cbl = QHBoxLayout(cam_w); cbl.setContentsMargins(0, 0, 0, 0); cbl.setSpacing(8)
        self.btn_snap = self._dash_btn("SNAP", "", "#3b82f6")
        self.btn_live_air = self._dash_btn("LIVE", "ON AIR", "#14b8a6")
        self.btn_acquire = self._dash_btn("ACQUIRE", "SAVE", "#e11d48")
        self.btn_stop_main = self._dash_btn("STOP", "", "#ef4444")
        for button in (self.btn_snap, self.btn_live_air, self.btn_acquire, self.btn_stop_main):
            cbl.addWidget(button)
        cbl.addStretch()
        self.master_btn_stack.addWidget(cam_w)

        mir_w = QWidget(); mbl = QHBoxLayout(mir_w); mbl.setContentsMargins(0, 0, 0, 0); mbl.setSpacing(8)
        self.btn_mirror_zero_all = self._dash_btn("ZERO ALL", "ALL AXIS", "#38bdf8")
        self.btn_mirror_reset    = self._dash_btn("RESET",    "",         "#64748b")
        self.btn_mirror_stop     = self._dash_btn("STOP",     "EMERGENCY","#ef4444")
        for button in (self.btn_mirror_zero_all, self.btn_mirror_reset, self.btn_mirror_stop):
            mbl.addWidget(button)
        mbl.addStretch()
        self.master_btn_stack.addWidget(mir_w)

        af_w = QWidget(); abl = QHBoxLayout(af_w); abl.setContentsMargins(0, 0, 0, 0); abl.setSpacing(8)
        self.btn_af_run   = self._dash_btn("RUN AF", "SEARCH", "#fbbf24")
        self.btn_af_abort = self._dash_btn("ABORT",  "",       "#ef4444")
        self.btn_af_set_z = self._dash_btn("SET Z",  "BASE",   "#3b82f6")
        for button in (self.btn_af_run, self.btn_af_abort, self.btn_af_set_z):
            abl.addWidget(button)
        abl.addStretch()
        self.master_btn_stack.addWidget(af_w)

        al_w = QWidget(); albl = QHBoxLayout(al_w); albl.setContentsMargins(0, 0, 0, 0); albl.setSpacing(8)
        self.btn_align_enable = self._dash_btn("ENABLE", "ALL",     "#4ecdc4")
        self.btn_align_calc   = self._dash_btn("CALC",   "KINEM.",  "#aa7acc")
        self.btn_align_move   = self._dash_btn("MOVE",   "EXECUTE", "#ef4444")
        self.btn_align_stop   = self._dash_btn("STOP",   "ALL",     "#64748b")
        for button in (self.btn_align_enable, self.btn_align_calc, self.btn_align_move, self.btn_align_stop):
            albl.addWidget(button)
        albl.addStretch()
        self.master_btn_stack.addWidget(al_w)

        mo_w = QWidget(); mol = QHBoxLayout(mo_w); mol.setContentsMargins(0, 0, 0, 0); mol.setSpacing(8)
        self.btn_motion_refresh   = self._dash_btn("REFRESH",   "",    "#4ecdc4")
        self.btn_motion_reconnect = self._dash_btn("RECONNECT", "ALL", "#fbbf24")
        self.btn_motion_stop      = self._dash_btn("STOP",      "ALL", "#ef4444")
        for button in (self.btn_motion_refresh, self.btn_motion_reconnect, self.btn_motion_stop):
            mol.addWidget(button)
        mol.addStretch()
        self.master_btn_stack.addWidget(mo_w)

        an_w = QWidget(); anbl = QHBoxLayout(an_w); anbl.setContentsMargins(0, 0, 0, 0); anbl.setSpacing(6)
        self.btn_an_open = self._dash_btn("OPEN", "SPE FILE", "#3b82f6")
        self.btn_an_roi_range = self._dash_btn("ROI RANGE", "SCALE", "#14b8a6")
        self.btn_an_fit = self._dash_btn("FIT VIEW", "RESET", "#64748b")
        self.btn_reset_dock = self._dash_btn("RESET", "LAYOUT", "#94a3b8")
        
        # 도킹 토글 버튼 — 3x2 그리드 (5개)
        from PyQt6.QtWidgets import QGridLayout
        dock_g = QGridLayout(); dock_g.setSpacing(2); dock_g.setContentsMargins(5, 0, 0, 0)
        self.btn_toggle_plot_sm  = self._small_toggle_btn("📈 Plot")
        self.btn_toggle_hist_sm  = self._small_toggle_btn("📊 Hist")
        self.btn_toggle_proc_sm  = self._small_toggle_btn("📉 Proc")
        self.btn_toggle_roi_sm   = self._small_toggle_btn("🎯 ROI")
        self.btn_toggle_table_sm = self._small_toggle_btn("📋 Table")
        dock_g.addWidget(self.btn_toggle_plot_sm,  0, 0)
        dock_g.addWidget(self.btn_toggle_hist_sm,  0, 1)
        dock_g.addWidget(self.btn_toggle_proc_sm,  1, 0)
        dock_g.addWidget(self.btn_toggle_roi_sm,   1, 1)
        dock_g.addWidget(self.btn_toggle_table_sm, 2, 0)

        for button in (self.btn_an_open, self.btn_an_roi_range, self.btn_an_fit, self.btn_reset_dock):
            anbl.addWidget(button)
        anbl.addLayout(dock_g)
        anbl.addStretch()
        self.master_btn_stack.addWidget(an_w)

        lay.addWidget(self.master_btn_stack)

        prog_lay = QVBoxLayout(); prog_lay.setSpacing(6); prog_lay.setContentsMargins(10, 5, 10, 5)
        top_row = QHBoxLayout()
        self.lbl_frame_info = QLabel("FRAME: <font color='#f8fafc'>— / —</font>")
        self.lbl_frame_info.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; border: none;")
        self.lbl_times = QLabel("ELAPSED: <font color='#f8fafc'>00:00:00</font> | REMAIN: <font color='#f8fafc'>00:00:00</font> | ETA: <font color='#f8fafc'>00:00:00</font>")
        self.lbl_times.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; border: none;")
        top_row.addWidget(self.lbl_frame_info); top_row.addStretch(); top_row.addWidget(self.lbl_times)
        prog_lay.addLayout(top_row)

        self.prog_container = QFrame(); self.prog_container.setFixedHeight(22)
        self.prog_container.setStyleSheet(f"background: {C_BG_MED}; border-radius: 11px; border: 1px solid #1e293b;")

        self.prog_grid = QGridLayout(self.prog_container)
        self.prog_grid.setContentsMargins(0, 0, 0, 0)

        self.prog_fill = QFrame(); self.prog_fill.setFixedHeight(22)
        self.prog_fill.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #e11d48,stop:1 #fb7185); border-radius: 11px;"
        )
        self.prog_grid.addWidget(self.prog_fill, 0, 0)

        self.lbl_prog_text = QLabel("0% COMPLETE")
        self.lbl_prog_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_prog_text.setStyleSheet("color: white; font-size: 10px; font-weight: 900; background: transparent; border: none;")
        self.prog_grid.addWidget(self.lbl_prog_text, 0, 0)

        self.prog_spacer = QWidget()
        self.prog_grid.addWidget(self.prog_spacer, 0, 1)
        self.prog_grid.setColumnStretch(0, 0)
        self.prog_grid.setColumnStretch(1, 100)

        prog_lay.addWidget(self.prog_container)
        lay.addLayout(prog_lay, 1)

        tel = QHBoxLayout(); tel.setSpacing(20)
        for label, val, attr in [
            ("DROPPED",    "0",        "lbl_tel_dropped"),
            ("WRITE RATE", "--- MB/s", "lbl_tel_write_rate"),
            ("STORAGE",    "--- Free", "lbl_tel_storage"),
            ("BUFFER",     "---",      "lbl_tel_buffer"),
        ]:
            vbox = QVBoxLayout(); vbox.setSpacing(2)
            ll = QLabel(label); ll.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 9px; font-weight: 900; border: none;")
            vv = QLabel(val);   vv.setStyleSheet("color: #14b8a6; font-size: 11px; font-weight: 900; border: none;")
            setattr(self, attr, vv)
            vbox.addWidget(ll); vbox.addWidget(vv)
            tel.addLayout(vbox)
        lay.addLayout(tel)
        return bar

