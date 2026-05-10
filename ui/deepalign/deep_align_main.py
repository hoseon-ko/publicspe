"""
ui/deepalign/deep_align_main.py
DeepAlign Industrial Dashboard

전략: 기존에 완성된 패널(MotorPanel, AutoFocusPanel, AcsStagePanel)을
      각 탭 페이지에 ScrollArea로 감싸서 직접 임베드.
      중복 UI 없음 — 100% 기존 패널 재사용.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QComboBox, QCheckBox, QListWidget,
    QGridLayout, QStackedWidget, QScrollArea, QButtonGroup
)
from PyQt6.QtCore import Qt, pyqtSignal

from ui.live.motor_panel import MotorPanel
from ui.live.autofocus_panel import AutoFocusPanel
from ui.live.acs_stage_panel import AcsStagePanel


class DeepAlignMainTab(QWidget):
    """
    DeepAlign Industrial Dashboard
    - 5-탭 아이콘 사이드바
    - 각 탭은 기존 완성된 패널을 ScrollArea로 감싸서 직접 임베드
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deepAlignTab")

        # ── 기존 패널 인스턴스 생성 (단 1회) ──────────────────────────
        self.mirror_panel = MotorPanel()
        self.af_panel     = AutoFocusPanel()
        self.align_panel  = AcsStagePanel()

        self._init_ui()
        self._apply_global_styles()

    # ─────────────────────────────────────────────────────────────────
    # UI 초기화
    # ─────────────────────────────────────────────────────────────────

    def _init_ui(self):
        main_lay = QHBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # 1. 아이콘 사이드바 (좌측)
        sidebar = self._create_icon_sidebar()
        main_lay.addWidget(sidebar)

        # 2. 중앙 패널 스택
        self.central_stack = QStackedWidget()
        self.central_stack.setObjectName("deepAlignStack")
        self.central_stack.setFixedWidth(380)
        self.central_stack.setStyleSheet(
            "background-color: #0d121d; border-right: 1px solid #1e293b;"
        )

        self.central_stack.addWidget(self._create_cam_page())       # 0
        self.central_stack.addWidget(self._wrap_panel(self.mirror_panel))  # 1
        self.central_stack.addWidget(self._wrap_panel(self.af_panel))      # 2
        self.central_stack.addWidget(self._wrap_panel(self.align_panel))   # 3
        self.central_stack.addWidget(self._create_analysis_page())  # 4
        main_lay.addWidget(self.central_stack)

        # 3. 우측 영역 (카메라 뷰 + 마스터 바)
        right_widget = QWidget()
        right_lay    = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(10, 10, 10, 10)
        right_lay.setSpacing(10)

        self.cam_view_area = QFrame()
        self.cam_view_area.setStyleSheet(
            "background-color: #000; border: 2px solid #1e293b; border-radius: 8px;"
        )
        v_cam = QVBoxLayout(self.cam_view_area)
        lbl_cam = QLabel("LIVE CAMERA STREAM")
        lbl_cam.setStyleSheet("color: #1e293b; font-size: 48px; font-weight: 900;")
        v_cam.addWidget(lbl_cam, 0, Qt.AlignmentFlag.AlignCenter)
        right_lay.addWidget(self.cam_view_area, 1)

        self.master_bar = self._create_master_bar()
        right_lay.addWidget(self.master_bar)

        main_lay.addWidget(right_widget, 1)

        # 시그널 연결
        self.mirror_panel.log_message.connect(self._on_sub_panel_log)
        self.align_panel.log_message.connect(self._on_sub_panel_log)

    # ─────────────────────────────────────────────────────────────────
    # 아이콘 사이드바
    # ─────────────────────────────────────────────────────────────────

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
            ("📊", "#10b981"),
        ]
        self.sidebar_btns = []
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        for i, (icon, color) in enumerate(icons):
            btn = QPushButton(icon)
            btn.setFixedSize(45, 45)
            btn.setCheckable(True)
            btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {color}; font-size: 24px;
                               border: none; border-radius: 12px; }}
                QPushButton:hover {{ background: #1e293b; color: #f8fafc; }}
                QPushButton:checked {{ background: #1e293b; color: #22d3ee;
                                       border: 1px solid #22d3ee; }}
            """)
            btn.clicked.connect(lambda _, idx=i: self._on_tab_changed(idx))
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
            self.sidebar_btns.append(btn)
            self.btn_group.addButton(btn, i)

        self.sidebar_btns[0].setChecked(True)
        lay.addStretch()
        return sidebar

    def _on_tab_changed(self, idx: int):
        self.central_stack.setCurrentIndex(idx)
        if hasattr(self, 'master_btn_stack'):
            self.master_btn_stack.setCurrentIndex(min(idx, self.master_btn_stack.count() - 1))

    # ─────────────────────────────────────────────────────────────────
    # 패널 래퍼 (기존 패널 → ScrollArea)
    # ─────────────────────────────────────────────────────────────────

    def _wrap_panel(self, panel: QWidget) -> QWidget:
        """기존 패널을 ScrollArea로 감싸서 페이지 위젯으로 반환."""
        page   = QWidget()
        lay    = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        lay.addWidget(scroll)
        return page

    # ─────────────────────────────────────────────────────────────────
    # 카메라 페이지 (탭 0 — 자체 UI)
    # ─────────────────────────────────────────────────────────────────

    def _create_cam_page(self):
        page   = QWidget()
        lay    = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        p_lay = QVBoxLayout(container)
        p_lay.setContentsMargins(10, 10, 10, 10)
        p_lay.setSpacing(12)

        # DEVICE CONNECTION
        conn_grp = self._make_section("🔌 DEVICE CONNECTION", "#64748b")
        cl = QVBoxLayout(conn_grp.content_widget)
        cl.setSpacing(8); cl.setContentsMargins(10, 10, 10, 10)

        vg_frame = QFrame()
        vg_frame.setFixedHeight(36)
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
        self.cb_vendor.setStyleSheet(
            "color: #14b8a6; font-size: 12px; font-weight: bold; border: none; padding: 6px;"
        )
        vg.addWidget(self.cb_vendor, 1)
        cl.addWidget(vg_frame)

        self.cam_list = QListWidget()
        self.cam_list.setFixedHeight(60)
        self.cam_list.setStyleSheet(
            "background: #020617; border: 1px solid #1e293b; color: #94a3b8; font-size: 11px;"
        )
        cl.addWidget(self.cam_list)

        self.btn_scan = self._style_btn("SCAN", "#64748b")
        self.btn_scan.setFixedHeight(35)
        cl.addWidget(self.btn_scan)

        conn_row = QHBoxLayout()
        self.btn_connect    = self._style_btn("CONNECT",    "#14b8a6")
        self.btn_disconnect = self._style_btn("DISCONNECT", "#ef4444")
        conn_row.addWidget(self.btn_connect)
        conn_row.addWidget(self.btn_disconnect)
        cl.addLayout(conn_row)
        p_lay.addWidget(conn_grp)

        # GRAB STATISTICS
        stat_grp = self._make_section("📊 GRAB STATISTICS", "#22d3ee")
        stl = QVBoxLayout(stat_grp.content_widget)
        stl.setSpacing(6); stl.setContentsMargins(10, 10, 10, 10)
        self.stat_labels = {}
        for s in ["Bit Depth", "Sensor Size", "Active ROI", "Buffer Status"]:
            row = QHBoxLayout()
            ll  = QLabel(s + ":")
            ll.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
            vv  = QLabel("---")
            vv.setStyleSheet("color: #22d3ee; font-size: 12px; font-weight: 900;")
            row.addWidget(ll); row.addWidget(vv); row.addStretch()
            stl.addLayout(row)
            self.stat_labels[s] = vv
        p_lay.addWidget(stat_grp)

        p_lay.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    # ─────────────────────────────────────────────────────────────────
    # 분석 페이지 (탭 4 — 자체 UI)
    # ─────────────────────────────────────────────────────────────────

    def _create_analysis_page(self):
        page  = QWidget()
        lay   = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        p_lay = QVBoxLayout(container)
        p_lay.setContentsMargins(10, 10, 10, 10)
        p_lay.setSpacing(12)

        sum_grp = self._make_section("📊 ANALYSIS SUMMARY", "#10b981")
        sl = QVBoxLayout(sum_grp.content_widget)
        sl.setContentsMargins(10, 10, 10, 10); sl.setSpacing(8)
        metrics = [("PEAK INTENSITY", "---"), ("FWHM (px)", "---"), ("SNR", "---")]
        grid = QFrame(); grid.setStyleSheet("border: 1px solid #1e293b;")
        gl = QGridLayout(grid); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(0)
        for i, (m, v) in enumerate(metrics):
            gl.addWidget(self._grid_lbl(f" {m}"), i, 0)
            vv = QLabel(v)
            vv.setStyleSheet("color: #10b981; font-size: 12px; font-weight: 900; padding: 6px;")
            gl.addWidget(vv, i, 1)
        sl.addWidget(grid)
        p_lay.addWidget(sum_grp)
        p_lay.addStretch()

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    # ─────────────────────────────────────────────────────────────────
    # 마스터 바
    # ─────────────────────────────────────────────────────────────────

    def _create_master_bar(self):
        bar = QFrame()
        bar.setFixedHeight(65)
        bar.setStyleSheet(
            "background-color: #020617; border-top: 1px solid #991b1b;"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(15, 10, 15, 5)
        lay.setSpacing(25)

        self.master_btn_stack = QStackedWidget()
        self.master_btn_stack.setFixedSize(380, 45)

        # 0: Camera
        cam_w = QWidget(); cbl = QHBoxLayout(cam_w); cbl.setContentsMargins(0,0,0,0); cbl.setSpacing(8)
        self.btn_snap     = self._dash_btn("SNAP",    "",          "#3b82f6")
        self.btn_live_air = self._dash_btn("LIVE",    "ON AIR",    "#14b8a6")
        self.btn_acquire  = self._dash_btn("ACQUIRE", "RECORDING", "#e11d48")
        self.btn_stop_main = self._dash_btn("STOP",   "",          "#ef4444")
        for b in (self.btn_snap, self.btn_live_air, self.btn_acquire, self.btn_stop_main):
            cbl.addWidget(b)
        self.master_btn_stack.addWidget(cam_w)

        # 1: Mirror
        mir_w = QWidget(); mbl = QHBoxLayout(mir_w); mbl.setContentsMargins(0,0,0,0); mbl.setSpacing(8)
        for t, s, c in [("ZERO ALL","ALL AXIS","#38bdf8"),("RESET","","#64748b"),("STOP","EMERGENCY","#ef4444")]:
            mbl.addWidget(self._dash_btn(t, s, c))
        self.master_btn_stack.addWidget(mir_w)

        # 2: AF
        af_w = QWidget(); abl = QHBoxLayout(af_w); abl.setContentsMargins(0,0,0,0); abl.setSpacing(8)
        for t, s, c in [("RUN AF","SEARCH","#fbbf24"),("ABORT","","#ef4444"),("SET Z","BASE","#3b82f6")]:
            abl.addWidget(self._dash_btn(t, s, c))
        self.master_btn_stack.addWidget(af_w)

        # 3: Align
        al_w = QWidget(); albl = QHBoxLayout(al_w); albl.setContentsMargins(0,0,0,0); albl.setSpacing(8)
        for t, s, c in [("ENABLE","ALL","#4ecdc4"),("CALC","KINEM.","#aa7acc"),("MOVE","EXECUTE","#ef4444"),("STOP","ALL","#64748b")]:
            albl.addWidget(self._dash_btn(t, s, c))
        self.master_btn_stack.addWidget(al_w)

        # 4: Analysis
        an_w = QWidget()
        lbl_an = QLabel("ANALYSIS COMMANDS")
        lbl_an.setStyleSheet("color: #10b981; font-weight: 900;")
        QHBoxLayout(an_w).addWidget(lbl_an)
        self.master_btn_stack.addWidget(an_w)

        lay.addWidget(self.master_btn_stack)

        # 진행 영역
        prog_lay = QVBoxLayout(); prog_lay.setSpacing(4); prog_lay.setContentsMargins(10, 8, 10, 8)
        top_row = QHBoxLayout()
        self.lbl_frame_info = QLabel("FRAME: <font color='#f8fafc'>— / —</font>")
        self.lbl_frame_info.setStyleSheet("font-size: 10px; font-weight: 800; color: #64748b; border: none;")
        self.lbl_times = QLabel("ELAPSED: <font color='#f8fafc'>--:--:--</font>")
        self.lbl_times.setStyleSheet("font-size: 10px; font-weight: 800; color: #64748b; border: none;")
        top_row.addWidget(self.lbl_frame_info); top_row.addStretch(); top_row.addWidget(self.lbl_times)
        prog_lay.addLayout(top_row)

        self.prog_bar = QFrame(); self.prog_bar.setFixedHeight(12)
        self.prog_bar.setStyleSheet("background: #0f172a; border-radius: 6px; border: 1px solid #1e293b;")
        pb_lay = QHBoxLayout(self.prog_bar); pb_lay.setContentsMargins(0, 0, 0, 0)
        self.prog_fill = QFrame(); self.prog_fill.setFixedHeight(12)
        self.prog_fill.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #e11d48,stop:1 #fb7185); border-radius: 6px;"
        )
        pb_lay.addWidget(self.prog_fill, 0); pb_lay.addStretch(100)
        prog_lay.addWidget(self.prog_bar)
        lay.addLayout(prog_lay, 1)

        # 텔레메트리
        tel = QHBoxLayout(); tel.setSpacing(20)
        for label, val in [("DROPPED","0"),("WRITE RATE","--- MB/s"),("STORAGE","--- Free"),("BUFFER","---")]:
            vbox = QVBoxLayout(); vbox.setSpacing(2)
            ll = QLabel(label); ll.setStyleSheet("color: #64748b; font-size: 9px; font-weight: 900; border: none;")
            vv = QLabel(val);   vv.setStyleSheet("color: #14b8a6; font-size: 11px; font-weight: 900; border: none;")
            vbox.addWidget(ll); vbox.addWidget(vv)
            tel.addLayout(vbox)
        lay.addLayout(tel)
        return bar

    # ─────────────────────────────────────────────────────────────────
    # 헬퍼
    # ─────────────────────────────────────────────────────────────────

    def _make_section(self, title: str, color: str, collapsed: bool = False):
        panel = QFrame()
        panel.setObjectName("subPanel")
        panel.setStyleSheet("QFrame#subPanel { background: transparent; }")
        lay = QVBoxLayout(panel); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        header = QPushButton(title)
        header.setCheckable(True); header.setChecked(not collapsed)
        header.setStyleSheet(f"""
            QPushButton {{ background: #0f172a; color: {color}; font-weight: 900; font-size: 11px;
                           text-align: left; padding: 10px; border: 1px solid #1e293b;
                           border-top: 4px solid {color}; }}
            QPushButton:checked {{ border-bottom: none; }}
        """)
        lay.addWidget(header)
        panel.content_widget = QWidget()
        panel.content_widget.setVisible(not collapsed)
        lay.addWidget(panel.content_widget)
        header.toggled.connect(panel.content_widget.setVisible)
        return panel

    def _grid_lbl(self, txt: str) -> QLabel:
        l = QLabel(txt)
        l.setFixedWidth(90)
        l.setStyleSheet(
            "color: #94a3b8; font-size: 12px; font-weight: bold;"
            " border-right: 1px solid #1e293b; padding: 0 6px;"
            " background: rgba(30,41,59,0.2);"
        )
        return l

    def _style_btn(self, txt: str, color: str) -> QPushButton:
        btn = QPushButton(txt)
        btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {color}; border: 1px solid {color};
                           border-radius: 4px; font-weight: bold; font-size: 11px; padding: 5px; }}
            QPushButton:hover {{ background: {color}22; }}
        """)
        return btn

    def _dash_btn(self, title: str, sub: str, color: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(85, 45)
        btn.setStyleSheet(f"""
            QPushButton {{ background: {color}; color: white; border-radius: 4px;
                           border: none; font-weight: 900; padding: 0; }}
            QPushButton:hover {{ background: {color}dd; }}
        """)
        lay = QVBoxLayout(btn); lay.setContentsMargins(0,5,0,5); lay.setSpacing(0)
        t = QLabel(title); t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("font-size: 13px; background: transparent; border: none; color: white;")
        lay.addWidget(t)
        if sub:
            s = QLabel(sub); s.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s.setStyleSheet("font-size: 8px; background: transparent; border: none; color: rgba(255,255,255,0.8);")
            lay.addWidget(s)
        return btn

    def _apply_global_styles(self):
        self.setStyleSheet("""
            QWidget#deepAlignStack { background-color: #05080c; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { border: none; background: #05080c; width: 6px; }
            QScrollBar::handle:vertical { background: #1e293b; border-radius: 3px; }
        """)

    # ─────────────────────────────────────────────────────────────────
    # 공개 API (main_window 에서 호출)
    # ─────────────────────────────────────────────────────────────────

    def set_shared_cameraera(self, camera):
        self._camera = camera

    def clear_shared_cameraera(self):
        self._camera = None

    def set_kimm_ctrl(self, ctrl):
        self._kimm = ctrl

    def clear_kimm_ctrl(self):
        self._kimm = None

    def set_acs_ctrl(self, ctrl):
        self._acs = ctrl
        if hasattr(self, 'align_panel') and ctrl is not None:
            # 컨트롤러를 align_panel에 직접 주입
            self.align_panel._ctrl_ref[0] = ctrl
            if ctrl.is_connected:
                from theme.styles import C_ACCENT
                from theme.styles import lbl as lbl_style
                self.align_panel.lbl_status.setText(f"● CONNECTED")
                self.align_panel.lbl_status.setStyleSheet(lbl_style(C_ACCENT, mono=True, bold=True))
                self.align_panel.btn_connect.setEnabled(False)
                self.align_panel.btn_disconnect.setEnabled(True)
                for b in self.align_panel._move_btns:
                    b.setEnabled(True)

    def clear_acs_ctrl(self):
        self._acs = None

    def set_picos_ctrl(self, ctrl):
        self._picos = ctrl
        if hasattr(self, 'mirror_panel') and ctrl is not None:
            self.mirror_panel.set_controller(ctrl)

    def _on_sub_panel_log(self, msg: str):
        print(f"[DeepAlign] {msg}")
