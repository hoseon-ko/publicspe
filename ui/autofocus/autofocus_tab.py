"""
ui/autofocus/autofocus_tab.py
KIMM Z 오토포커스 탭 — Z 스캔 + Contrast 최대 포커스 탐색.

레이아웃:
  ┌─ 좌측 설정 패널 (280px) ─┬─ 우측 뷰 영역 (QSplitter) ─────────┐
  │  📷 CAMERA               │  ImageViewer (스캔 중 프리뷰)        │
  │  🔍 SCAN RANGE           ├─────────────────────────────────────┤
  │  📊 METRIC               │  Sharpness vs Z 플롯                 │
  │  ⚙ OPTIONS              │  + 결과 테이블                        │
  │  ─────────               └─────────────────────────────────────┘
  │  [ ▶ RUN ] [ ■ STOP ]
  │  ████████░  60%
  │  ─────────
  │  Best Z: +12.50 µm [GO]
  └──────────────────────────
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QScrollArea, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QComboBox,
    QProgressBar, QTextEdit, QCheckBox, QRadioButton,
    QButtonGroup, QFileDialog, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QSizePolicy, QLineEdit, QToolButton,
)
from ui.widgets.auto_splitter import AutoSplitter
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSettings, QSize
from PyQt6.QtGui import QFont, QIcon, QPixmap, QImage

import pyqtgraph as pg

from ui.image_viewer import ImageViewer
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    Fonts, Sizes,
    C_ACCENT, C_DANGER, C_BORDER,
    C_BG_DARK, C_BG_MED, C_TEXT_DIM, C_TEXT_DEAD,
    SPIN_STYLE, COMBO_STYLE, TEXTEDIT_LOG,
    BTN_PRIMARY, BTN_DANGER,
)

_FC  = Fonts.UI  # Changed from MONO to UI to match other tabs
_FS  = Sizes.CTRL
_FSS = Sizes.CTRL
_FSB = Sizes.BTN


# ── 버튼 / 레이블 헬퍼 ────────────────────────────────────────────────
def _lbl(text: str, color: str = C_TEXT_DIM) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-family: '{_FC}'; font-size: {_FSS};"
    )
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


# ─────────────────────────────────────────────────────────────────────
class AutoFocusTab(QWidget):
    """
    독립 오토포커스 탭.

    외부 시그널:
      af_starting()          — RUN 시작 → Live 스트림 정지 요청
      af_done()              — 완료   → Live 스트림 재개 요청
      log_message(str)       — 상태바 전달
    """

    af_starting  = pyqtSignal()
    af_done      = pyqtSignal()
    log_message  = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cam  = None          # Live 탭에서 공유받는 카메라
        self._kimm = None          # KIMMZController (공유)
        self._running = False
        self._worker = None        # AutoFocusWorker

        # SIM 상태
        self._sim_active  = False
        self._sim_images: list = []   # 로드된 이미지 (image 모드)
        self._real_cam    = None      # SIM 진입 전 실제 카메라 백업

        # 결과 데이터
        self._z_pts:  list[float] = []
        self._sh_pts: list[float] = []
        self._best_z: Optional[float] = None
        self._image_list: list = []  # (step, raw, z, sh)

        self._build_ui()
        self._restore_settings()

    # ── Public API (MainWindow에서 연결) ──────────────────────────────

    def set_shared_camera(self, cam):
        if self._sim_active:
            self._real_cam = cam   # SIM 중: 나중에 복원용으로만 보관
            return
        self._cam = cam
        name = type(cam).__name__.replace("Camera", "")
        self._lbl_cam.setText(f"● {name}  CONNECTED")
        self._lbl_cam.setStyleSheet(
            f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self.btn_run.setEnabled(True)

    def clear_shared_camera(self):
        if self._sim_active:
            self._real_cam = None
            return
        self._cam = None
        self._lbl_cam.setText("● 카메라 없음")
        self._lbl_cam.setStyleSheet(
            f"color: {C_DANGER}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self.btn_run.setEnabled(False)

    def set_kimm_ctrl(self, ctrl):
        """Live 탭의 KIMMZController 공유."""
        self._kimm = ctrl
        if ctrl and ctrl.is_connected:
            self._lbl_kimm.setText("● KIMM STAGE  CONNECTED")
            self._lbl_kimm.setStyleSheet(
                f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FSS};"
            )
            self._z_timer.start()
        else:
            self.clear_kimm_ctrl()

    def clear_kimm_ctrl(self):
        """KIMM 연결 해제 알림."""
        self._z_timer.stop()
        self._kimm = None
        self._lbl_kimm.setText("● 스테이지 미연결")
        self._lbl_kimm.setStyleSheet(
            f"color: {C_DANGER}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._lbl_z_cur.setText("Z: —  µm")

    def on_tab_activated(self):
        """탭이 활성화될 때 호출 (MainWindow에서 호출)."""
        if self._kimm and self._kimm.is_connected:
            # 현재 위치를 명시적으로 요청하고 spin_center에 반영
            self._kimm.request_position()
            z = self._kimm.current_z
            self.spin_center.setValue(z)
            self._log(f"현재 Z축 위치 동기화: {z:+.2f} µm")
            if not self._z_timer.isActive():
                self._z_timer.start()

    def _poll_z(self):
        """150ms마다 현재 Z 위치 갱신 (UI 표시용)."""
        if not self._kimm or not self._kimm.is_connected:
            self._z_timer.stop()
            return
        z = self._kimm.current_z
        self._lbl_z_cur.setText(f"Z: {z:+.2f} µm")

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
        self._splitter.setSizes([290, 1200])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        # Connect ImageViewer ROI signals
        if hasattr(self, 'image_viewer'):
            self.image_viewer._view.roi_drawn.connect(self._on_roi_drawn)

    # ── 좌측 설정 패널 ────────────────────────────────────────────────

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

        # ── 1. 카메라 상태 + SIM MODE ──────────────────────────────────
        sec_cam = CollapsibleSection("📷  CAMERA", accent=C_ACCENT)
        self._lbl_cam = QLabel("● 카메라 없음")
        self._lbl_cam.setStyleSheet(
            f"color: {C_DANGER}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        sec_cam.add_widget(self._lbl_cam)

        # SIM MODE 토글
        self.btn_sim = QPushButton("▷  SIM MODE")
        self.btn_sim.setCheckable(True)
        self.btn_sim.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: #ffe66d;
                border: 1px solid #8a7a30; border-radius: 3px;
                font-family: '{_FC}'; font-size: {_FSS};
                font-weight: bold; padding: 4px 10px;
            }}
            QPushButton:hover  {{ background: #ffe66d22; }}
            QPushButton:checked {{ background: #ffe66d33; border-color: #ffe66d; color: #ffe66d; }}
        """)
        self.btn_sim.clicked.connect(self._toggle_sim_mode)
        sec_cam.add_widget(self.btn_sim)

        # SIM 세부 옵션 (기본 숨김)
        self._sim_options = QWidget()
        # Connect ImageViewer ROI signals
        self._roi_rect = None  # (x0, y0, x1, y1) in image coords

        sim_v = QVBoxLayout(self._sim_options)
        sim_v.setContentsMargins(4, 4, 4, 4)
        sim_v.setSpacing(6)

        _rb_style = (f"color: {C_TEXT_DIM}; font-family: '{_FC}';"
                     f" font-size: {_FSS};")

        self._sim_grp = QButtonGroup(self)
        self.rb_sim_math = QRadioButton("수학 모델 (Gaussian + defocus)")
        self.rb_sim_imgs = QRadioButton("이미지 시퀀스 (파일 로드)")
        for rb in (self.rb_sim_math, self.rb_sim_imgs):
            rb.setStyleSheet(_rb_style)
            self._sim_grp.addButton(rb)
        self.rb_sim_math.setChecked(True)
        sim_v.addWidget(self.rb_sim_math)
        sim_v.addWidget(self.rb_sim_imgs)

        # 이미지 로드 영역 (rb_sim_imgs 선택 시만 활성)
        self._img_load_widget = QWidget()
        self._img_load_widget.setVisible(False)
        il = QVBoxLayout(self._img_load_widget)
        il.setContentsMargins(0, 2, 0, 2)
        il.setSpacing(4)

        btn_load_imgs = QPushButton("📂  이미지 폴더 선택")
        btn_load_imgs.setStyleSheet(f"""
            QPushButton {{
                background: #0f1e38; color: #8090c0;
                border: 1px solid #1a3060; border-radius: 3px;
                font-family: '{_FC}'; font-size: {_FSS}; padding: 3px 8px;
            }}
            QPushButton:hover {{ background: #1a3060; color: #a0c0e0; }}
        """)
        btn_load_imgs.clicked.connect(self._browse_sim_images)
        il.addWidget(btn_load_imgs)

        self._lbl_img_count = QLabel("파일 없음")
        self._lbl_img_count.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        il.addWidget(self._lbl_img_count)

        self._lst_sim_imgs = QListWidget()
        self._lst_sim_imgs.setFixedHeight(80)
        self._lst_sim_imgs.setStyleSheet(f"""
            QListWidget {{ background:#080e1e; border:1px solid #1a3060;
                color:#8090b0; font-family:'{_FC}'; font-size:{_FSS}; }}
            QListWidget::item {{ padding: 1px 4px; }}
        """)
        il.addWidget(self._lst_sim_imgs)
        sim_v.addWidget(self._img_load_widget)

        self.rb_sim_imgs.toggled.connect(
            lambda chk: self._img_load_widget.setVisible(chk)
        )
        sec_cam.add_widget(self._sim_options)
        v.addWidget(sec_cam)

        # ── 2. KIMM Z 상태 ─────────────────────────────────────────────
        sec_kimm = CollapsibleSection("🎯  KIMM Z", accent="#9a6a4a")
        self._lbl_kimm = QLabel("● 연결 없음")
        self._lbl_kimm.setStyleSheet(
            f"color: {C_TEXT_DEAD}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._lbl_z_cur = QLabel("Z: —  µm")
        self._lbl_z_cur.setStyleSheet(
            f"color: #8090b0; font-family: '{_FC}'; font-size: {_FSS};"
        )
        # 100ms 폴링 타이머 (KIMM 연결 시 활성화)
        self._z_timer = QTimer()
        self._z_timer.setInterval(150)
        self._z_timer.timeout.connect(self._poll_z)
        sec_kimm.add_widget(self._lbl_kimm)
        sec_kimm.add_widget(self._lbl_z_cur)
        v.addWidget(sec_kimm)

        # ── 3. Z 스캔 범위 ─────────────────────────────────────────────
        sec_range = CollapsibleSection("🔍  Z SCAN RANGE", accent="#4a9a7a")
        rl = sec_range.content_layout()
        rl.setSpacing(5)

        def _row(label: str, widget: QWidget) -> QHBoxLayout:
            h = QHBoxLayout()
            h.setSpacing(10)
            lb = QLabel(label)
            lb.setFixedWidth(110)
            lb.setStyleSheet(
                f"color: {C_TEXT_DIM}; font-family: '{_FC}'; font-size: {_FSS};"
            )
            lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(lb)
            h.addWidget(widget, 1)
            return h

        self.spin_center = QDoubleSpinBox()
        self.spin_center.setRange(-1e6, 1e6)
        self.spin_center.setDecimals(2)
        self.spin_center.setSuffix("  µm")
        self.spin_center.setValue(0.0)
        self.spin_center.setStyleSheet(SPIN_STYLE)

        self.spin_range = QDoubleSpinBox()
        self.spin_range.setRange(0.1, 1e5)
        self.spin_range.setDecimals(2)
        self.spin_range.setSuffix("  µm")
        self.spin_range.setValue(50.0)
        self.spin_range.setStyleSheet(SPIN_STYLE)

        self.spin_step = QDoubleSpinBox()
        self.spin_step.setRange(0.1, 1e4)
        self.spin_step.setDecimals(2)
        self.spin_step.setSuffix("  µm")
        self.spin_step.setValue(5.0)
        self.spin_step.setStyleSheet(SPIN_STYLE)

        rl.addLayout(_row("Center", self.spin_center))
        rl.addLayout(_row("± Range", self.spin_range))
        rl.addLayout(_row("Step", self.spin_step))

        self._lbl_steps = QLabel("Steps: 21")
        self._lbl_steps.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lbl_steps.setStyleSheet(
            f"color: #4a7a6a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        rl.addWidget(self._lbl_steps)

        for s in (self.spin_range, self.spin_step):
            s.valueChanged.connect(self._update_step_count)
        self._update_step_count()
        v.addWidget(sec_range)

        # ── 4. 선명도 지표 ─────────────────────────────────────────────
        sec_metric = CollapsibleSection("📊  SHARPNESS METRIC", accent="#6a6aaa")
        ml = sec_metric.content_layout()
        ml.setSpacing(5)

        self.combo_metric = QComboBox()
        self.combo_metric.addItems([
            "Laplacian Variance",
            "Contrast (Std Dev)",
            "Tenengrad (Sobel²)",
            "Brenner",
        ])
        self.combo_metric.setStyleSheet(COMBO_STYLE)
        ml.addWidget(self.combo_metric)
        ml.addWidget(_lbl("Laplacian: 가장 일반적, 고주파 성분을 통한 빠른 선명도 측정"));
        ml.addWidget(_lbl("Contrast: 이미지 전체의 대비와 명암 분포를 기반으로 분석"));
        ml.addWidget(_lbl("Tenengrad: Sobel 필터 기반, 노이즈가 많은 환경에서도 높은 신뢰도"));
        ml.addWidget(_lbl("Brenner: 인접 픽셀 차이 분석, 초점 변화에 대한 높은 민감도"));
        v.addWidget(sec_metric)

        # ── 5. ROI 설정 ────────────────────────────────────────────────
        sec_roi = CollapsibleSection("📐  ROI SELECTION", accent="#e67e22")
        rl = sec_roi.content_layout()
        rl.setSpacing(5)

        self.cb_use_roi = QCheckBox("ROI 사용 (연산 영역 제한)")
        self.cb_use_roi.setChecked(False)
        self.cb_use_roi.setStyleSheet(_rb_style)
        self.cb_use_roi.toggled.connect(self._toggle_roi_mode)
        rl.addWidget(self.cb_use_roi)

        self._lbl_roi_info = _lbl("이미지 뷰어에서 드래그하여 영역 지정", color="#8090b0")
        self._lbl_roi_info.setWordWrap(True)
        rl.addWidget(self._lbl_roi_info)

        self._lbl_roi_rect = _lbl("영역: 전체 이미지", color="#4ecdc4")
        rl.addWidget(self._lbl_roi_rect)
        v.addWidget(sec_roi)

        sec_opt = CollapsibleSection("⚙  OPTIONS", accent="#7a6a4a", collapsed=True)
        ol = sec_opt.content_layout()
        ol.setSpacing(5)

        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 5000)
        self.spin_settle.setSuffix("  ms")
        self.spin_settle.setValue(200)
        self.spin_settle.setToolTip("이동 후 진동 안정 대기 시간")
        self.spin_settle.setStyleSheet(SPIN_STYLE)
        ol.addLayout(_row("Settle", self.spin_settle))

        self.spin_avg = QSpinBox()
        self.spin_avg.setRange(1, 32)
        self.spin_avg.setValue(1)
        self.spin_avg.setToolTip("각 위치에서 평균낼 프레임 수")
        self.spin_avg.setStyleSheet(SPIN_STYLE)
        ol.addLayout(_row("Avg frames", self.spin_avg))

        self.chk_goto_best = QCheckBox("완료 후 Best Z로 자동 이동")
        self.chk_goto_best.setChecked(True)
        self.chk_goto_best.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        ol.addWidget(self.chk_goto_best)

        ol.addWidget(_sep_h())
        # ── Capture 저장 옵션 ──────────────────────────────────────
        self.chk_save_frames = QCheckBox("스캔 결과 SPE 저장")
        self.chk_save_frames.setStyleSheet(
            f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FSS}; font-weight: bold;"
        )
        ol.addWidget(self.chk_save_frames)

        row_dir = QHBoxLayout()
        self.edit_save_dir = QLineEdit()
        self.edit_save_dir.setPlaceholderText("SPE 저장 폴더 선택...")
        self.edit_save_dir.setStyleSheet(f"""
            QLineEdit {{
                background: #081220; border: 1px solid #1a3060;
                color: #a0b0d0; font-family: '{_FC}'; font-size: 11px; padding: 3px 6px;
            }}
        """)
        self.btn_save_dir = QPushButton("📂")
        self.btn_save_dir.setFixedWidth(28)
        self.btn_save_dir.setStyleSheet(f"""
            QPushButton {{
                background: #1a3060; color: white; border-radius: 3px; font-size: 10px;
            }}
            QPushButton:hover {{ background: #2a4a8a; }}
        """)
        self.btn_save_dir.clicked.connect(self._browse_save_dir)
        row_dir.addWidget(self.edit_save_dir, 1)
        row_dir.addWidget(self.btn_save_dir)
        ol.addLayout(row_dir)

        v.addWidget(sec_opt)
        v.addWidget(_sep_h())

        # ── 6. RUN / STOP ──────────────────────────────────────────────
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

        # 진행 상태
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(14)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background: #080e1e; border: 1px solid #1a3060;
                border-radius: 3px; color: #4a6a8a;
                font-family: '{_FC}'; font-size: {_FSS}; text-align: center;
            }}
            QProgressBar::chunk {{ background: {C_ACCENT}; border-radius: 2px; }}
        """)
        v.addWidget(self.progress)

        self._lbl_status = QLabel("Ready")
        self._lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_status.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        v.addWidget(self._lbl_status)
        v.addWidget(_sep_h())

        # ── 7. 결과 ────────────────────────────────────────────────────
        v.addWidget(_lbl("RESULT", "#2a4a6a"))

        res_row = QHBoxLayout()
        v.addLayout(res_row)
        self._lbl_best_z = QLabel("Best Z:  —")
        self._lbl_best_z.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: {Sizes.BTN}; font-weight: bold;"
        )
        self.btn_goto = _btn("GO", "#e94560")
        self.btn_goto.setFixedWidth(60)
        self.btn_goto.setEnabled(False)
        self.btn_goto.setToolTip("Best Z 위치로 이동")
        self.btn_goto.clicked.connect(self._on_goto)

        self.btn_manual_save = _btn("💾 SPE", "#4ecdc4")
        self.btn_manual_save.setFixedWidth(85)
        self.btn_manual_save.setEnabled(False)
        self.btn_manual_save.setToolTip("현재 스캔 결과 SPE로 저장")
        self.btn_manual_save.clicked.connect(self._save_af_result_spe)

        res_row.addWidget(self._lbl_best_z, 1)
        res_row.addWidget(self.btn_goto)
        res_row.addWidget(self.btn_manual_save)

        self._lbl_best_sh = QLabel("Sharpness:  —")
        self._lbl_best_sh.setStyleSheet(
            f"color: #4a9a7a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        v.addWidget(self._lbl_best_sh)
        v.addWidget(_sep_h())

        # ── 8. 로그 ────────────────────────────────────────────────────
        v.addWidget(_lbl("LOG", "#2a4a6a"))
        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(120)
        self._log_box.setStyleSheet(TEXTEDIT_LOG)
        v.addWidget(self._log_box)

        v.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    # ── 우측 뷰 패널 ──────────────────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet("background: #080e1e;")
        root_h = QHBoxLayout(container)
        root_h.setContentsMargins(0, 0, 0, 0)
        root_h.setSpacing(0)

        # 전체를 가로로 나누는 스플리터 (메인 뷰 vs 사이드 패널)
        self._main_splitter = AutoSplitter(Qt.Orientation.Horizontal)
        root_h.addWidget(self._main_splitter)

        # ── 1. 왼쪽: 이미지 뷰어 (각 스텝 프리뷰) ───────────────────────
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
            f"color: #3a5878; font-family: '{_FC}'; font-size: {_FSS};"
            " font-weight: bold; letter-spacing: 2px;"
        )
        self._lbl_step_info = QLabel("—")
        self._lbl_step_info.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._lbl_step_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._btn_roi_range = QToolButton()
        self._btn_roi_range.setText("🎯 ROI Range")
        self._btn_roi_range.setCheckable(True)
        self._btn_roi_range.setStyleSheet("""
            QToolButton { background: transparent; color: #a0a0b0; border: 1px solid #1a3a60;
                border-radius: 3px; font-size: 10px; padding: 1px 4px; }
            QToolButton:checked { background: #1a2a10; color: #ffe66d; border-color: #ffe66d; }
        """)
        self._btn_roi_range.toggled.connect(self._on_roi_range_toggled)

        vhdr_h.addWidget(lbl_view)
        vhdr_h.addWidget(self._btn_roi_range)
        vhdr_h.addWidget(self._lbl_step_info, 1)
        vv.addWidget(vhdr)

        self.image_viewer = ImageViewer()
        vv.addWidget(self.image_viewer, 1)
        self._main_splitter.addWidget(viewer_wrap)

        # ── 2. 오른쪽: 데이터 대시보드 (세로 스플리터) ──────────────────
        self._side_splitter = QSplitter(Qt.Orientation.Vertical)
        self._side_splitter.setStyleSheet("""
            QSplitter::handle { background: #1a3a60; height: 3px; }
            QSplitter::handle:hover { background: #4ecdc4; }
        """)

        # (A) CAPTURED FRAMES (썸네일 리스트)
        frames_widget = QWidget()
        frames_widget.setStyleSheet(f"background: {C_BG_DARK};")
        fv = QVBoxLayout(frames_widget)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(0)

        fhdr = QWidget()
        fhdr.setFixedHeight(22)
        fhdr.setStyleSheet(f"background: {C_BG_MED}; border-bottom: 1px solid {C_BORDER};")
        fh_h = QHBoxLayout(fhdr)
        fh_h.setContentsMargins(8, 0, 8, 0)
        lbl_f = QLabel("CAPTURED FRAMES")
        lbl_f.setStyleSheet(
            f"color: #4ecdc4; font-family: '{_FC}'; font-size: {Sizes.SMALL}; font-weight: bold;"
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
            QListWidget {{ background: #080e1e; border: none; color: #c0d0ff; }}
            QListWidget::item {{ padding: 2px; border: 1px solid #0f2040; }}
            QListWidget::item:selected {{ background: #1a3a60; border: 1px solid #4ecdc4; }}
        """)
        self._frame_list.currentRowChanged.connect(self._on_frame_list_select)
        fv.addWidget(self._frame_list)
        self._side_splitter.addWidget(frames_widget)

        # (B) Sharpness vs Z Plot
        plot_wrap = QWidget()
        plot_wrap.setStyleSheet("background: #080e1e;")
        pv = QVBoxLayout(plot_wrap)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        phdr = QWidget()
        phdr.setFixedHeight(22)
        phdr.setStyleSheet(f"background: {C_BG_MED}; border-bottom: 1px solid {C_BORDER};")
        ph_h = QHBoxLayout(phdr)
        ph_h.setContentsMargins(8, 0, 8, 0)
        lbl_p = QLabel("SHARPNESS vs Z")
        lbl_p.setStyleSheet(
            f"color: #3a5878; font-family: '{_FC}'; font-size: {Sizes.SMALL}; font-weight: bold;"
        )
        ph_h.addWidget(lbl_p)
        pv.addWidget(phdr)
        pv.addWidget(self._build_plot(), 1)
        self._side_splitter.addWidget(plot_wrap)

        # (C) Result Table
        table_wrap = QWidget()
        table_wrap.setStyleSheet("background: #080e1e;")
        tv = QVBoxLayout(table_wrap)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)

        thdr = QWidget()
        thdr.setFixedHeight(22)
        thdr.setStyleSheet(f"background: {C_BG_MED}; border-bottom: 1px solid {C_BORDER};")
        th_h = QHBoxLayout(thdr)
        th_h.setContentsMargins(8, 0, 8, 0)
        lbl_t = QLabel("RESULTS TABLE")
        lbl_t.setStyleSheet(
            f"color: #3a5878; font-family: '{_FC}'; font-size: {Sizes.SMALL}; font-weight: bold;"
        )
        th_h.addWidget(lbl_t)
        tv.addWidget(thdr)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Step", "Z (µm)", "Sharpness"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setStyleSheet(f"""
            QTableWidget {{ background: #080e1e; color: #c0d0ff; gridline-color: #1a3a60; border: none; }}
            QHeaderView::section {{ background: {C_BG_MED}; color: #4a6a8a; border: 1px solid #1a3a60; padding: 2px; }}
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

        pw.getAxis("bottom").setLabel(
            "Z position (µm)", **{"color": C_TEXT_DIM, "font-size": "9px"}
        )
        pw.getAxis("left").setLabel(
            "Sharpness (a.u.)", **{"color": C_TEXT_DIM, "font-size": "9px"}
        )
        pw.showGrid(x=True, y=True, alpha=0.2)
        pw.getPlotItem().getViewBox().setBackgroundColor("#080e1e")

        # 선명도 곡선
        self._curve = pw.plot(
            pen=pg.mkPen("#4ecdc4", width=2),
            symbol="o", symbolSize=5,
            symbolBrush="#4ecdc4", symbolPen=None,
            name="Sharpness",
        )
        # 최적 Z 수직선 (빨간 점선)
        self._best_vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#e94560", width=1.5,
                         style=Qt.PenStyle.DashLine),
        )
        pw.addItem(self._best_vline)
        self._best_vline.hide()

        self._plot_widget = pw
        return pw

    # ── 슬롯 ─────────────────────────────────────────────────────────

    def _update_step_count(self):
        half = self.spin_range.value()
        step = max(self.spin_step.value(), 0.001)
        n = int(2 * half / step) + 1
        self._lbl_steps.setText(f"Steps: {n}")

    def _update_kimm_status(self):
        if self._kimm and self._kimm.is_connected:
            self._lbl_kimm.setText("● CONNECTED")
            self._lbl_kimm.setStyleSheet(
                f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FSS};"
            )
            self._z_timer.start()
        else:
            self._lbl_kimm.setText("● 연결 없음")
            self._lbl_kimm.setStyleSheet(
                f"color: {C_TEXT_DEAD}; font-family: '{_FC}'; font-size: {_FSS};"
            )
            self._z_timer.stop()

    def _poll_z(self):
        if self._kimm and self._kimm.is_connected:
            self._kimm.request_position()
            z = self._kimm.current_z
            self._lbl_z_cur.setText(f"Z: {z:+.3f}  µm")
        else:
            self._z_timer.stop()

    # ── SIM MODE ──────────────────────────────────────────────────────

    def _toggle_sim_mode(self, checked: bool):
        if self._running:
            self._log("❌ 동작 중 SIM 모드 전환 불가")
            self.btn_sim.setChecked(not checked)
            return

        self._sim_active = checked
        self._sim_options.setVisible(checked)

        if checked:
            self._real_cam = self._cam
            self._build_sim_camera()
            self.btn_sim.setText("■  SIM OFF")
            self._log("🟡 SIM MODE 활성")
        else:
            self._sim_active = False
            self.btn_sim.setText("▷  SIM MODE")
            real = self._real_cam
            self._real_cam = None
            if real is not None:
                self.set_shared_camera(real)
            else:
                self.clear_shared_camera()
            self._log("⬛ SIM MODE 해제")

    def _build_sim_camera(self):
        """SIM 카메라 인스턴스 생성 후 self._cam에 설정."""
        from core.simulator import SimAFCamera
        center = self.spin_center.value()
        step   = self.spin_step.value()
        half   = self.spin_range.value()

        if self.rb_sim_imgs.isChecked() and self._sim_images:
            n = int(2 * half / max(step, 0.001)) + 1
            z_seq = [center - half + i * step for i in range(n)]
            cam = SimAFCamera(mode="images", images=self._sim_images)
            cam.set_z_sequence(z_seq)
        else:
            # 수학 모델: defocus sigma = range / 4 (포커스 곡선이 선명하게)
            cam = SimAFCamera(
                mode="math",
                best_z=center,
                z_sigma=max(half / 4.0, 1.0),
            )

        self._cam = cam
        self._lbl_cam.setText("🟡 SIM  ● ACTIVE")
        self._lbl_cam.setStyleSheet(
            f"color: #ffe66d; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self.btn_run.setEnabled(True)

    def _browse_save_dir(self):
        """AF 스캔 프레임 저장 폴더 선택."""
        folder = QFileDialog.getExistingDirectory(self, "저장 폴더 선택")
        if folder:
            self.edit_save_dir.setText(folder)

    def _browse_sim_images(self):
        """파일 선택 → 지원 포맷 로드.
        SPE: 파일 하나에 여러 프레임 → 각 프레임을 독립 이미지로 분리.
        기타: 폴더 안 이미지 파일들을 Z 순서대로 로드.
        """
        # SPE 파일 or 일반 이미지 폴더 — 어느 쪽이든 선택 가능
        path, _ = QFileDialog.getOpenFileName(
            self, "SPE 파일 또는 이미지 파일 선택",
            "",
            "SPE 파일 (*.spe);;이미지 파일 (*.png *.tif *.tiff *.bmp "
            "*.jpg *.npy *.npz);;모든 파일 (*)"
        )
        if not path:
            # 파일 선택 취소 → 폴더 선택 fallback
            folder = QFileDialog.getExistingDirectory(self, "이미지 폴더 선택")
            if not folder:
                return
            self._load_from_folder(folder)
            return

        import os
        if path.lower().endswith(".spe"):
            self._load_from_spe(path)
        else:
            # 단일 파일 선택 → 해당 폴더 전체 로드
            self._load_from_folder(os.path.dirname(path))

    def _load_from_spe(self, path: str):
        """SPE 파일 → 각 프레임을 독립 이미지로 분리."""
        import os
        try:
            from core.spe_reader import SpeFile
            spe = SpeFile(path)
            frames = spe.data   # (num_frames, H, W)
            if frames.ndim == 2:
                frames = frames[np.newaxis, ...]   # 단일 프레임 처리
        except Exception as e:
            self._log(f"SPE 로드 실패: {e}")
            self._lbl_img_count.setText("SPE 로드 실패")
            return

        n = frames.shape[0]
        if n == 0:
            self._lbl_img_count.setText("프레임 없음")
            return

        self._sim_images = [frames[i] for i in range(n)]
        self._lst_sim_imgs.clear()
        fname = os.path.basename(path)
        for i in range(n):
            self._lst_sim_imgs.addItem(f"{fname}  [frame {i}]")

        self._lbl_img_count.setText(f"{n}프레임 (SPE)")
        self._log(f"SPE 로드: {fname}  {n}프레임  {frames.shape[1]}×{frames.shape[2]}")

        if self._sim_active:
            self._build_sim_camera()

    def _load_from_folder(self, folder: str):
        """폴더 내 이미지 파일들을 Z 순서대로 로드."""
        import os
        supported = (".spe", ".png", ".tif", ".tiff", ".bmp",
                     ".jpg", ".jpeg", ".npy", ".npz")
        files = sorted([
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(supported)
        ])
        if not files:
            self._lbl_img_count.setText("지원 파일 없음")
            return

        self._sim_images = []
        self._lst_sim_imgs.clear()
        for fpath in files:
            try:
                if fpath.lower().endswith(".spe"):
                    from core.spe_reader import SpeFile
                    spe = SpeFile(fpath)
                    data = spe.data
                    # SPE 하나 = 여러 프레임 → 첫 프레임만 대표로 사용
                    img = data[0] if data.ndim == 3 else data
                else:
                    img = self._load_image(fpath)
                if img is not None:
                    self._sim_images.append(img)
                    self._lst_sim_imgs.addItem(os.path.basename(fpath))
            except Exception as e:
                self._log(f"로드 실패: {os.path.basename(fpath)} — {e}")

        n = len(self._sim_images)
        self._lbl_img_count.setText(f"{n}개 로드됨")
        self._log(f"{n}개 로드 완료: {folder}")

        if self._sim_active:
            self._build_sim_camera()

    @staticmethod
    def _load_image(path: str):
        import os
        ext = os.path.splitext(path)[1].lower()
        if ext == ".npy":
            return np.load(path)
        if ext == ".npz":
            d = np.load(path)
            k = list(d.keys())[0]
            return d[k]
        try:
            import cv2
            img = cv2.imread(path, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_GRAYSCALE)
            if img is not None:
                return img
        except ImportError:
            pass
        from PIL import Image  # type: ignore
        return np.array(Image.open(path).convert("L"))

    # ── RUN / STOP ────────────────────────────────────────────────────

    def _toggle_roi_mode(self, checked: bool):
        """Enable box ROI drawing mode when ROI mode is active."""
        if checked:
            # Switch ImageViewer to box draw mode
            self.image_viewer._view.set_single_roi_mode(True)
            self.image_viewer._view.set_roi_mode('box')
            self._log("🟢 ROI 모드 활성 – 박스 그리기를 진행하세요")
            self._lbl_roi_rect.setText("영역: 드래그하여 지정하세요")
        else:
            # Return to no-ROI mode
            self.image_viewer._view.set_single_roi_mode(False)
            self.image_viewer._view.set_roi_mode(None)
            # Clear all existing ROIs in the viewer to remove the box
            self.image_viewer._view.delete_all_rois()
            self._roi_rect = None
            self._log("⚪ ROI 모드 비활성")
            self._lbl_roi_rect.setText("영역: 전체 이미지")

    def _on_roi_range_toggled(self, checked: bool):
        if self.image_viewer.btn_roi_range.isChecked() != checked:
            self.image_viewer.btn_roi_range.setChecked(checked)
        if hasattr(self.image_viewer, '_on_roi_range_toggled'):
            self.image_viewer._on_roi_range_toggled(checked)

    def _on_roi_drawn(self, mode: str, pts: list):
        """Capture drawn ROI rectangle for cropping.
        mode: 'box' for rectangular ROI.
        pts: [(x0, y0), (x1, y1)]
        """
        if mode == 'box' and self.cb_use_roi.isChecked():
            (x0, y0), (x1, y1) = pts
            self._roi_rect = (int(min(x0, x1)), int(min(y0, y1)),
                              int(max(x0, x1)), int(max(y0, y1)))
            self._log(f"📐 ROI 영역 설정: {self._roi_rect}")
            x0, y0, x1, y1 = self._roi_rect
            self._lbl_roi_rect.setText(f"영역: ({x0}, {y0}) ~ ({x1}, {y1})")

    def _on_run(self):
        if self._cam is None:
            self._log("❌ 카메라 없음")
            return

        center = self.spin_center.value()
        half   = self.spin_range.value()
        step   = self.spin_step.value()

        z_positions = []
        z = center - half
        while z <= center + half + step * 0.01:
            z_positions.append(round(z, 6))
            z += step
        n = len(z_positions)

        metric_map = {
            "Laplacian Variance":  "laplacian",
            "Contrast (Std Dev)":  "contrast",
            "Tenengrad (Sobel²)":  "tenengrad",
            "Brenner":             "brenner",
        }
        metric = metric_map.get(self.combo_metric.currentText(), "laplacian")

        # SIM 이미지 모드: Z 시퀀스를 카메라에도 전달
        if self._sim_active and hasattr(self._cam, "set_z_sequence"):
            self._cam.set_z_sequence(z_positions)

        roi_info = ""
        if self.cb_use_roi.isChecked() and self._roi_rect:
            roi_info = f"  [ROI {self._roi_rect}]"
        self._log(
            f"AF START — center={center:+.1f}µm  ±{half:.1f}µm  "
            f"step={step:.2f}µm  {n}steps  [{metric}]"
            + ("  [SIM]" if self._sim_active else "")
            + roi_info
        )

        # 플롯 초기화
        self._z_pts.clear()
        self._sh_pts.clear()
        self._image_list.clear()
        self._frame_list.clear()
        self._table.setRowCount(0)
        self._best_z = None
        self._curve.setData([], [])
        self._best_vline.hide()
        self._lbl_best_z.setText("Best Z:  —")
        self._lbl_best_sh.setText("Sharpness:  —")
        self.btn_goto.setEnabled(False)
        self.btn_manual_save.setEnabled(False)
        self.progress.setValue(0)
        self._lbl_status.setText("Running…")
        self._lbl_step_info.setText("—")

        self._set_running(True)
        self.af_starting.emit()

        self._set_running(True)
        self.af_starting.emit()

        from ui.autofocus.af_worker import AutoFocusWorker
        self._worker = AutoFocusWorker(
            camera       = self._cam,
            kimm_ctrl    = None if self._sim_active else self._kimm,
            z_positions  = z_positions,
            metric       = metric,
            settle_ms    = self.spin_settle.value(),
            avg_frames   = self.spin_avg.value(),
            sim_mode     = self._sim_active,
            roi_rect     = self._roi_rect if self.cb_use_roi.isChecked() else None,
            rotation_k   = self.image_viewer._rotation_k,
        )
        self._worker.step_done.connect(self.on_step_done)
        self._worker.finished.connect(self.on_af_finished)
        self._worker.error.connect(self.on_af_error)
        self._worker.log.connect(self._log)
        self._worker.start()

    def _on_stop(self):
        self._log("AF STOP 요청")
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
        self._lbl_status.setText("중단됨")
        self._set_running(False)

    def _on_goto(self):
        if self._best_z is None:
            return
        if self._kimm is None or not self._kimm.is_connected:
            self._log("KIMM 미연결 — 이동 불가")
            return
        self._log(f"Best Z로 이동: {self._best_z:+.2f} µm")
        try:
            # KIMMZController에 구현된 move_to_z 호출 (속도는 설정된 기본값 사용)
            ok = self._kimm.move_to_z(self._best_z)
            if ok:
                self._log("✅ Best Z 이동 완료")
            else:
                self._log("❌ Best Z 이동 실패 (안전 리밋 또는 통신 오류)")
        except Exception as e:
            self._log(f"❌ 이동 중 예외 발생: {e}")

    # ── Worker 콜백 (Worker 구현 후 연결) ────────────────────────────

    def on_step_done(self, step: int, total: int,
                     z: float, sharpness: float,
                     frame: np.ndarray):
        """각 스텝 완료 시 Worker에서 호출."""
        pct = int(step / max(total, 1) * 100)
        self.progress.setValue(pct)
        self._lbl_status.setText(
            f"Step {step}/{total}  Z={z:+.2f}µm  S={sharpness:.1f}"
        )
        self._lbl_step_info.setText(
            f"Step {step}/{total}  •  Z = {z:+.2f} µm"
        )

        self._z_pts.append(z)
        self._sh_pts.append(sharpness)
        self._image_list.append((step, frame, z, sharpness))
        self._curve.setData(self._z_pts, self._sh_pts)

        # UI 업데이트
        self._append_thumbnail(frame, step-1)
        self._update_table(step, z, sharpness)
        self.image_viewer.set_image(frame)

    def _append_thumbnail(self, frame: np.ndarray, idx: int):
        """frame(ndarray)를 썸네일로 만들어 리스트에 추가."""
        disp = frame
        if disp.ndim == 2:
            # 8-bit or 16-bit grayscale
            if disp.dtype != np.uint8:
                # 간단한 정규화
                mi, ma = disp.min(), disp.max()
                if ma > mi:
                    disp = ((disp - mi) / (ma - mi) * 255).astype(np.uint8)
                else:
                    disp = disp.astype(np.uint8)
            disp = np.stack([disp, disp, disp], axis=-1)
        
        h, w = disp.shape[:2]
        tw, th = 80, 60
        scale = min(tw/w, th/h)
        nw, nh = max(1, int(w*scale)), max(1, int(h*scale))
        
        try:
            import cv2
            small = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
        except ImportError:
            small = disp[::max(1, h//nh), ::max(1, w//nw)][:nh, :nw]
            
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        y0 = (th - nh) // 2
        x0 = (tw - nw) // 2
        canvas[y0:y0+nh, x0:x0+nw] = small[:, :, :3]
        
        qimg = QImage(canvas.tobytes(), tw, th, tw*3, QImage.Format.Format_RGB888)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(qimg)), f"#{idx+1}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._frame_list.addItem(item)
        self._frame_list.scrollToItem(item)

    def _update_table(self, step: int, z: float, sh: float):
        row = self._table.rowCount()
        self._table.insertRow(row)
        
        items = [
            QTableWidgetItem(str(step)),
            QTableWidgetItem(f"{z:+.2f}"),
            QTableWidgetItem(f"{sh:.1f}"),
        ]
        for col, item in enumerate(items):
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, col, item)
        self._table.scrollToBottom()

    def _on_frame_list_select(self, row: int):
        if row < 0 or row >= len(self._image_list):
            return
        step_i, frame, z, sh = self._image_list[row]
        self.image_viewer.set_image(frame)
        self._lbl_step_info.setText(f"Step {step_i}/{len(self._image_list)}  •  Z = {z:+.2f} µm")

    def on_af_finished(self, best_z: float, best_sh: float):
        """스캔 완료 시 Worker에서 호출."""
        self._best_z = best_z
        self._lbl_best_z.setText(f"Best Z:  {best_z:+.2f}  µm")
        self._lbl_best_sh.setText(f"Sharpness:  {best_sh:.1f}")
        self._best_vline.setPos(best_z)
        self._best_vline.show()
        self.btn_goto.setEnabled(True)
        self.btn_manual_save.setEnabled(True)
        self.progress.setValue(100)
        self._lbl_status.setText(f"완료 — Best Z: {best_z:+.2f} µm")
        self._log(f"AF 완료 — Best Z: {best_z:+.2f} µm  Sharpness: {best_sh:.1f}")

        # SPE 저장 처리
        if self.chk_save_frames.isChecked() and self._image_list:
            self._save_af_result_spe()

        self._set_running(False)

    def _save_af_result_spe(self):
        """현재 스캔된 모든 프레임을 하나의 SPE 파일로 저장."""
        base_path = self.edit_save_dir.text().strip()
        if not base_path:
            # 수동 저장 시 경로가 없으면 폴더 브라우저 열기
            self._browse_save_dir()
            base_path = self.edit_save_dir.text().strip()
            if not base_path:
                self._log("⚠️ 저장 경로가 지정되지 않아 SPE 저장을 취소합니다.")
                return

        try:
            import os
            from datetime import datetime
            from core.spe_writer import save_spe

            if not os.path.exists(base_path):
                os.makedirs(base_path, exist_ok=True)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"AF_Scan_{ts}.spe"
            fpath = os.path.join(base_path, fname)

            # _image_list: (step, frame, z, sh)
            frames = [item[1] for item in self._image_list]
            z_list = [item[2] for item in self._image_list]
            sh_list = [item[3] for item in self._image_list]

            # 메타데이터 구성
            extra = {
                "AutoFocus": {
                    "Metric": self.combo_metric.currentText(),
                    "BestZ": self._best_z,
                    "Z_Positions": z_list,
                    "Sharpness_Values": sh_list
                }
            }

            save_spe(
                fpath, frames,
                exposure_ms = 0.0, # AF 스냅 시 노출 정보가 필요하면 worker에서 전달받아야 함
                extra_metadata = extra
            )
            self._log(f"✅ 스캔 결과 저장 완료 (SPE): {fname}")
        except Exception as e:
            self._log(f"❌ SPE 저장 실패: {e}")
        self.af_done.emit()

        # 테이블 및 리스트에서 Best 결과 하이라이트
        for r in range(self._table.rowCount()):
            z_val = float(self._table.item(r, 1).text())
            if abs(z_val - best_z) < 0.001:
                self._table.selectRow(r)
                self._frame_list.setCurrentRow(r)
                break

        if self.chk_goto_best.isChecked():
            self._on_goto()

    def on_af_error(self, msg: str):
        self._log(f"AF ERROR: {msg}")
        self._lbl_status.setText(f"오류: {msg}")
        self._set_running(False)
        self.af_done.emit()

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        self.btn_run.setEnabled(not running and self._cam is not None)
        self.btn_stop.setEnabled(running)
        self.btn_sim.setEnabled(not running)
        for w in (self.spin_center, self.spin_range,
                  self.spin_step, self.combo_metric,
                  self.spin_settle, self.spin_avg):
            w.setEnabled(not running)

    def _log(self, msg: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.append(
            f'<span style="color:#4a6a8a">[{ts}]</span>'
            f' <span style="color:#a0c0e0">{msg}</span>'
        )
        self.log_message.emit(msg)

    # ── 설정 저장 및 복원 ────────────────────────────────────────────────

    def _save_settings(self):
        s = QSettings("SpeAnalyze", "AutoFocusTab")
        s.setValue("spin_center", self.spin_center.value())
        s.setValue("spin_range", self.spin_range.value())
        s.setValue("spin_step", self.spin_step.value())
        s.setValue("combo_metric", self.combo_metric.currentIndex())
        s.setValue("spin_settle", self.spin_settle.value())
        s.setValue("spin_avg", self.spin_avg.value())
        s.setValue("chk_goto_best", self.chk_goto_best.isChecked())
        s.setValue("chk_save_frames", self.chk_save_frames.isChecked())
        s.setValue("save_dir", self.edit_save_dir.text())
        if hasattr(self, "_splitter"):
            s.setValue("splitter", self._splitter.saveState())
        if hasattr(self, "_main_splitter"):
            s.setValue("main_splitter", self._main_splitter.saveState())
        if hasattr(self, "_side_splitter"):
            s.setValue("side_splitter", self._side_splitter.saveState())

    def _restore_settings(self):
        s = QSettings("SpeAnalyze", "AutoFocusTab")
        try:
            self.spin_center.setValue(float(s.value("spin_center", 0.0)))
            self.spin_range.setValue(float(s.value("spin_range", 50.0)))
            self.spin_step.setValue(float(s.value("spin_step", 5.0)))
            self.combo_metric.setCurrentIndex(int(s.value("combo_metric", 0)))
            self.spin_settle.setValue(int(s.value("spin_settle", 200)))
            self.spin_avg.setValue(int(s.value("spin_avg", 1)))
            self.chk_goto_best.setChecked(s.value("chk_goto_best", True, type=bool))
            self.chk_save_frames.setChecked(s.value("chk_save_frames", False, type=bool))
            self.edit_save_dir.setText(s.value("save_dir", ""))
            if hasattr(self, "_splitter") and s.value("splitter"):
                self._splitter.restoreState(s.value("splitter"))
            if hasattr(self, "_main_splitter") and s.value("main_splitter"):
                self._main_splitter.restoreState(s.value("main_splitter"))
            if hasattr(self, "_side_splitter") and s.value("side_splitter"):
                self._side_splitter.restoreState(s.value("side_splitter"))
        except Exception as e:
            print(f"AutoFocusTab settings restore error: {e}")
