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

from ui.plot_panel import PlotPanel, HistogramPanel
from ui.file_list_panel import FileListPanel
from ui.frame_grid_panel import FrameGridPanel
from ui.roi_panel import RoiPanel
from ui.viewer_v2.deepalign_adapter import DeepAlignViewerV2Adapter


class LayoutBuilderMixin:
    def _create_icon_sidebar(self):
        sidebar = QFrame()
        sidebar.setFixedWidth(65)
        sidebar.setStyleSheet("background-color: #020617; border-right: 1px solid #1e293b;")
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 25, 0, 25)
        lay.setSpacing(25)

        icons = [
            ("📷", "#94a3b8"),
            ("🪞", "#38bdf8"),
            ("🔍", "#fbbf24"),
            ("🎯", "#ef4444"),
            ("⚙", "#4ecdc4"),
            ("📊", "#10b981"),
        ]
        self.sidebar_btns = []
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        for i, (icon, color) in enumerate(icons):
            btn = QPushButton(icon)
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

    def _wrap_panel(self, panel: QWidget) -> QWidget:
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

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _create_align_page(self) -> QWidget:
        """Align 탭 페이지 — AcsStagePanel + Kinematic Calc 섹션."""
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
        c_lay.addWidget(self._create_kinem_calc_section())

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _create_kinem_calc_section(self) -> QWidget:
        """KINEMATIC CALC 섹션 — 3개 볼 위치 입력 + 형상 설정 + 결과 표시."""
        _C_ACCENT    = "#aa7acc"
        _C_BG        = "#080e1e"
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
        host.setStyleSheet("QMainWindow { background: #060d19; }")

        self.cam_viewer = DeepAlignViewerV2Adapter()
        self.cam_viewer.set_external_render_control(True)
        host.setCentralWidget(self.cam_viewer)

        # Analysis Docks
        self.plot_panel = PlotPanel("PROFILE")
        self.plot_panel = PlotPanel("Profile")
        self.dock_plot = self._wrap_dock(
            "dock_plot", "📈  PROFILE PLOT",
            self.plot_panel, Qt.DockWidgetArea.BottomDockWidgetArea, host
        )

        self.hist_panel = HistogramPanel()
        self.dock_hist = self._wrap_dock(
            "dock_histogram", "📊  HISTOGRAM",
            self.hist_panel, Qt.DockWidgetArea.BottomDockWidgetArea, host
        )

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
        self.analysis_toolbar.setStyleSheet("""
            QToolBar { background: #0a0f1e; border-bottom: 1px solid #1a4060; padding: 2px 6px; spacing: 4px; }
            QToolButton { background: #0d1e38; color: #4ecdc4; border: 1px solid #1a4060; border-radius: 3px; padding: 3px 8px; font-weight: bold; font-size: 11px; }
            QToolButton:hover { background: #1a3a60; }
            QToolButton:checked { background: #1a3010; color: #4ecdc4; border-color: #2a6020; }
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
            ("🔲  ROI",       self.dock_roi),
            ("📷  Scan",      self.dock_scan_result),
            ("🔍  AF",        self.dock_af_result),
        ]:
            act = QAction(text, self)
            act.setCheckable(True)
            act.setChecked(True)
            act.triggered.connect(dock.setVisible)
            self.analysis_toolbar.addAction(act)
            self.dock_toggles[dock.objectName()] = act

        self.analysis_toolbar.addSeparator()
        self.act_an_roi_range = QAction("🎯  ROI Range", self)
        self.act_an_roi_range.setCheckable(True)
        self.analysis_toolbar.addAction(self.act_an_roi_range)
        
        self.act_an_fit = QAction("⟳  Reset View", self)
        self.analysis_toolbar.addAction(self.act_an_fit)

        # 기본적으로 좌우 분할
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
        frames_widget.setStyleSheet("background:#0a0f1e;")
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
            "QListWidget { background:#080e1e; border:1px solid #0f3460; color:#c0d0ff; }"
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
            "QTextEdit { background:#080e1e; border:1px solid #0f3460;"
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
            "QTableWidget { background:#080e1e; gridline-color:#0f3460;"
            " color:#c0d0ff; font-family:'Courier New'; font-size:12px;"
            " border:none; }"
            "QHeaderView::section { background:#0f1729; color:#4ecdc4;"
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
        frames_widget.setStyleSheet("background:#0a0f1e;")
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
            "QListWidget { background:#080e1e; border:1px solid #0f3460; color:#c0d0ff; }"
            "QListWidget::item { padding:2px; border:1px solid #0f2040; }"
            "QListWidget::item:selected { background:#1a3a60; border:1px solid #4ecdc4; }"
        )
        frames_layout.addWidget(self.af_frame_list)
        splitter.addWidget(frames_widget)

        # ── 패널 2: Sharpness vs Z 플롯 ──────────────────────────────
        self.af_plot_panel = PlotPanel("SHARPNESS vs Z")
        self.af_plot_panel.plot_widget.setLabel("bottom", "Z position (µm)", color="#8899aa")
        self.af_plot_panel.plot_widget.setLabel("left", "Sharpness (a.u.)", color="#8899aa")
        self.af_plot_panel.setMinimumHeight(100)
        splitter.addWidget(self.af_plot_panel)

        # ── 패널 3: 결과 테이블 ───────────────────────────────────────
        tbl_header = QLabel("RESULTS TABLE")
        tbl_header.setStyleSheet(
            "color:#4ecdc4; font-size:13px; font-weight:bold;"
            " letter-spacing:2px; background:#0a0f1e; padding:4px 6px;"
        )

        self.af_table = QTableWidget()
        self.af_table.setColumnCount(3)
        self.af_table.setHorizontalHeaderLabels(["Step", "Z (µm)", "Sharpness"])
        self.af_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.af_table.setMinimumHeight(40)
        self.af_table.setStyleSheet(
            "QTableWidget { background:#080e1e; gridline-color:#0f3460;"
            " color:#c0d0ff; font-family:'Courier New'; font-size:12px; border:none; }"
            "QHeaderView::section { background:#0f1729; color:#4ecdc4;"
            " border:1px solid #0f3460; font-weight:bold; padding:4px 2px; }"
            "QTableWidget::item:selected { background:#1a3a60; }"
        )
        self.af_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        tbl_widget = QWidget()
        tbl_widget.setStyleSheet("background:#0a0f1e;")
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

        editor_combo_style = """
            QComboBox {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }
            QComboBox:hover {
                border-color: #22d3ee;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
        """
        editor_spin_style = """
            QAbstractSpinBox {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }
            QAbstractSpinBox:hover {
                border-color: #22d3ee;
            }
            QAbstractSpinBox::up-button,
            QAbstractSpinBox::down-button {
                width: 16px;
                background: #0f172a;
                border-left: 1px solid #334155;
            }
            QAbstractSpinBox::up-button:hover,
            QAbstractSpinBox::down-button:hover {
                background: #172036;
            }
        """
        editor_line_style = """
            QLineEdit {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }
            QLineEdit:hover {
                border-color: #22d3ee;
            }
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
            "color: #94a3b8; font-size: 12px; font-weight: bold;"
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
            "background: #020617; border: 1px solid #1e293b; color: #94a3b8; font-size: 11px;"
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

        acq_grp = self._make_section("IMAGE ACQUISITION", "#22d3ee")
        al = QVBoxLayout(acq_grp.content_widget)
        al.setSpacing(8)
        al.setContentsMargins(10, 10, 10, 10)

        exp_row = QHBoxLayout()
        lbl_exp = QLabel("Exposure (ms):")
        lbl_exp.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
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

        self.sec_fps = QFrame()
        fps_lay = QHBoxLayout(self.sec_fps)
        fps_lay.setContentsMargins(0, 0, 0, 0)
        self.check_fps_lock = QCheckBox("Lock FPS")
        self.check_fps_lock.setStyleSheet("color: #94a3b8; font-size: 12px;")
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
        p_lay.addWidget(acq_grp)

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
            lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
            cb.setStyleSheet(editor_combo_style)
            row.addWidget(lbl)
            row.addWidget(cb, 1)
            adl.addLayout(row)
        self.btn_apply_adc = self._style_btn("APPLY ADC", "#14b8a6")
        adl.addWidget(self.btn_apply_adc)
        p_lay.addWidget(self.sec_adc)

        self.sec_temp = self._make_section("TEMPERATURE", "#22d3ee")
        tl = QVBoxLayout(self.sec_temp.content_widget)
        tl.setSpacing(6)
        tl.setContentsMargins(10, 10, 10, 10)
        trow = QHBoxLayout()
        lbl_temp = QLabel("Setpoint (C):")
        lbl_temp.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
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
            item.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
            tl.addWidget(item)
        p_lay.addWidget(self.sec_temp)

        save_grp = self._make_section("SAVE", "#22d3ee")
        sl = QVBoxLayout(save_grp.content_widget)
        sl.setSpacing(8)
        sl.setContentsMargins(10, 10, 10, 10)

        save_count_row = QHBoxLayout()
        lbl_count = QLabel("Frame To Save:")
        lbl_count.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
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
            chk.setStyleSheet("""
                QCheckBox {
                    color: #e2e8f0;
                    font-size: 13px;
                    font-weight: 700;
                    spacing: 8px;
                }
                QCheckBox::indicator {
                    width: 16px;
                    height: 16px;
                    border-radius: 3px;
                    border: 1px solid #64748b;
                    background: #020617;
                }
                QCheckBox::indicator:hover {
                    border-color: #22d3ee;
                }
                QCheckBox::indicator:checked {
                    border-color: #22d3ee;
                    background: #22d3ee;
                }
            """)

        self.cb_date_fmt = QComboBox(); self.cb_date_fmt.addItems(["YYYY-Month-DD", "YYYY-MM-DD"])
        self.cb_time_fmt = QComboBox(); self.cb_time_fmt.addItems(["hh:mm:ss (24h)", "hh:mm:ss (12h)"])
        self.cb_place = QComboBox(); self.cb_place.addItems(["Suffix", "Prefix"])
        for cb in (self.cb_date_fmt, self.cb_time_fmt, self.cb_place):
            cb.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")

        self.btn_browse_folder.setStyleSheet(
            """
            QPushButton {
                background: #0f172a;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 4px;
                font-weight: 900;
            }
            QPushButton:hover {
                border-color: #22d3ee;
                color: #22d3ee;
            }
        """
        )

        self.edit_file_base.setStyleSheet(editor_line_style)
        self.edit_folder.setStyleSheet(editor_line_style)

        row_folder = QHBoxLayout()
        lbl_folder = QLabel("Save In:")
        lbl_folder.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        row_folder.addWidget(lbl_folder)
        row_folder.addWidget(self.edit_folder, 1)
        row_folder.addWidget(self.btn_browse_folder)
        sl.addLayout(row_folder)

        row_name = QHBoxLayout()
        lbl_name = QLabel("File Name:")
        lbl_name.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        row_name.addWidget(lbl_name)
        row_name.addWidget(self.edit_file_base, 1)
        sl.addLayout(row_name)

        naming_box = QGroupBox("Naming Options")
        naming_box.setStyleSheet(
            """
            QGroupBox {
                color: #e2e8f0;
                font-size: 13px;
                font-weight: 800;
                background: #020817;
                border: 1px solid #475569;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 6px;
                color: #e2e8f0;
                background: #020817;
            }
            QGroupBox QLabel {
                color: #cbd5e1;
                font-size: 12px;
                font-weight: 700;
            }
            QGroupBox QComboBox {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
            }
            QGroupBox QComboBox:disabled {
                color: #64748b;
                border-color: #334155;
                background: #0a0f1a;
            }
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

        p_lay.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _on_browse_save_folder(self):
        current_dir = self.edit_folder.text().strip() or os.getcwd()
        selected = QFileDialog.getExistingDirectory(self, "Save In", current_dir)
        if selected:
            self.edit_folder.setText(selected)

    def _update_save_control_state(self):
        has_date = self.check_add_date.isChecked()
        has_time = self.check_add_time.isChecked()
        self.cb_date_fmt.setEnabled(has_date)
        self.cb_time_fmt.setEnabled(has_time)
        self.cb_place.setEnabled(has_date or has_time)

    def _build_filename_stem(self, counter: str = "0001") -> str:
        """날짜/시간/카운터 토큰을 조합하여 파일명 stem을 반환합니다.

        Args:
            counter: 증분 카운터 문자열 (기본 "0001"). 루프에서 충돌 방지 시 증가시켜 넘깁니다.

        Returns:
            확장자 없는 파일명 stem (예: "Capture_2026-05-14_12_00_00_0001").
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

        base = self.edit_file_base.text().strip() or "Capture"
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

        if has_temp and hasattr(caps, "temperature_range_c"):
            mn, mx = caps.temperature_range_c
            if mn is not None:
                self.spin_temp.setMinimum(float(mn))
            if mx is not None:
                self.spin_temp.setMaximum(float(mx))

        if has_adc:
            self.cb_adc_quality.clear()
            self.cb_adc_speed.clear()
            self.cb_adc_gain.clear()
            self.cb_adc_bit.clear()

            # Use capabilities options if available, otherwise use defaults
            qual_opts = getattr(caps, "adc_quality_options", [])
            if not qual_opts: qual_opts = ["High Capacity", "Low Noise"]
            self.cb_adc_quality.addItems([str(x) for x in qual_opts])

            speed_opts = getattr(caps, "adc_speed_options", [])
            if not speed_opts: speed_opts = ["100kHz", "1MHz"]
            self.cb_adc_speed.addItems([str(x) for x in speed_opts])

            gain_opts = getattr(caps, "adc_gain_options", [])
            if not gain_opts: gain_opts = ["1x", "2x"]
            self.cb_adc_gain.addItems([str(x) for x in gain_opts])

            bit_opts = getattr(caps, "adc_bit_depth_options", [])
            if not bit_opts: bit_opts = ["16bit", "12bit"]
            self.cb_adc_bit.addItems([str(x) for x in bit_opts])

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
        self.list_an_gallery.setStyleSheet("""
            QListWidget { background: #080e1e; border: 1px solid #1e293b; border-radius: 4px; }
            QListWidget::item { color: #94a3b8; font-size: 10px; font-weight: bold; }
            QListWidget::item:selected { background: #1e293b; color: #38bdf8; border: 1px solid #38bdf8; }
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
        bar.setFixedHeight(75)
        bar.setStyleSheet("background-color: #020617; border-top: none;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(15, 10, 15, 5)
        lay.setSpacing(25)

        self.master_btn_stack = QStackedWidget()
        self.master_btn_stack.setFixedSize(380, 45)

        cam_w = QWidget(); cbl = QHBoxLayout(cam_w); cbl.setContentsMargins(0, 0, 0, 0); cbl.setSpacing(8)
        self.btn_snap = self._dash_btn("SNAP", "", "#3b82f6")
        self.btn_live_air = self._dash_btn("LIVE", "ON AIR", "#14b8a6")
        self.btn_acquire = self._dash_btn("ACQUIRE", "SAVE", "#e11d48")
        self.btn_stop_main = self._dash_btn("STOP", "", "#ef4444")
        for button in (self.btn_snap, self.btn_live_air, self.btn_acquire, self.btn_stop_main):
            cbl.addWidget(button)
        self.master_btn_stack.addWidget(cam_w)

        mir_w = QWidget(); mbl = QHBoxLayout(mir_w); mbl.setContentsMargins(0, 0, 0, 0); mbl.setSpacing(8)
        self.btn_mirror_zero_all = self._dash_btn("ZERO ALL", "ALL AXIS", "#38bdf8")
        self.btn_mirror_reset    = self._dash_btn("RESET",    "",         "#64748b")
        self.btn_mirror_stop     = self._dash_btn("STOP",     "EMERGENCY","#ef4444")
        for button in (self.btn_mirror_zero_all, self.btn_mirror_reset, self.btn_mirror_stop):
            mbl.addWidget(button)
        self.master_btn_stack.addWidget(mir_w)

        af_w = QWidget(); abl = QHBoxLayout(af_w); abl.setContentsMargins(0, 0, 0, 0); abl.setSpacing(8)
        self.btn_af_run   = self._dash_btn("RUN AF", "SEARCH", "#fbbf24")
        self.btn_af_abort = self._dash_btn("ABORT",  "",       "#ef4444")
        self.btn_af_set_z = self._dash_btn("SET Z",  "BASE",   "#3b82f6")
        for button in (self.btn_af_run, self.btn_af_abort, self.btn_af_set_z):
            abl.addWidget(button)
        self.master_btn_stack.addWidget(af_w)

        al_w = QWidget(); albl = QHBoxLayout(al_w); albl.setContentsMargins(0, 0, 0, 0); albl.setSpacing(8)
        self.btn_align_enable = self._dash_btn("ENABLE", "ALL",     "#4ecdc4")
        self.btn_align_calc   = self._dash_btn("CALC",   "KINEM.",  "#aa7acc")
        self.btn_align_move   = self._dash_btn("MOVE",   "EXECUTE", "#ef4444")
        self.btn_align_stop   = self._dash_btn("STOP",   "ALL",     "#64748b")
        for button in (self.btn_align_enable, self.btn_align_calc, self.btn_align_move, self.btn_align_stop):
            albl.addWidget(button)
        self.master_btn_stack.addWidget(al_w)

        mo_w = QWidget(); mol = QHBoxLayout(mo_w); mol.setContentsMargins(0, 0, 0, 0); mol.setSpacing(8)
        self.btn_motion_refresh   = self._dash_btn("REFRESH",   "",    "#4ecdc4")
        self.btn_motion_reconnect = self._dash_btn("RECONNECT", "ALL", "#fbbf24")
        self.btn_motion_stop      = self._dash_btn("STOP",      "ALL", "#ef4444")
        for button in (self.btn_motion_refresh, self.btn_motion_reconnect, self.btn_motion_stop):
            mol.addWidget(button)
        self.master_btn_stack.addWidget(mo_w)

        an_w = QWidget(); anbl = QHBoxLayout(an_w); anbl.setContentsMargins(0, 0, 0, 0); anbl.setSpacing(6)
        self.btn_an_open = self._dash_btn("OPEN", "SPE FILE", "#3b82f6")
        self.btn_an_roi_range = self._dash_btn("ROI RANGE", "SCALE", "#14b8a6")
        self.btn_an_fit = self._dash_btn("FIT VIEW", "RESET", "#64748b")
        self.btn_reset_dock = self._dash_btn("RESET", "LAYOUT", "#94a3b8")
        
        # 도킹 토글 버튼들을 위한 작은 수직 레이아웃
        dock_v = QVBoxLayout(); dock_v.setSpacing(2); dock_v.setContentsMargins(5, 0, 0, 0)
        self.btn_toggle_plot_sm = self._small_toggle_btn("📈 Plot")
        self.btn_toggle_hist_sm = self._small_toggle_btn("📊 Hist")
        self.btn_toggle_roi_sm  = self._small_toggle_btn("🎯 ROI")
        dock_v.addWidget(self.btn_toggle_plot_sm)
        dock_v.addWidget(self.btn_toggle_hist_sm)
        dock_v.addWidget(self.btn_toggle_roi_sm)
        
        for button in (self.btn_an_open, self.btn_an_roi_range, self.btn_an_fit, self.btn_reset_dock):
            anbl.addWidget(button)
        anbl.addLayout(dock_v)
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
        self.prog_container.setStyleSheet("background: #0f172a; border-radius: 11px; border: 1px solid #1e293b;")

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
            ll = QLabel(label); ll.setStyleSheet("color: #64748b; font-size: 9px; font-weight: 900; border: none;")
            vv = QLabel(val);   vv.setStyleSheet("color: #14b8a6; font-size: 11px; font-weight: 900; border: none;")
            setattr(self, attr, vv)
            vbox.addWidget(ll); vbox.addWidget(vv)
            tel.addLayout(vbox)
        lay.addLayout(tel)
        return bar

