"""
ui/analysis/analysis_tab.py
SPE 분석 탭 — 기존 MainWindow를 QMainWindow로 재포장.

QTabWidget 안에 QMainWindow를 임베드할 때
menuBar().setVisible(False) 처리가 필요하다.
외부에서 open_spe(path) 로 파일을 직접 열 수 있다.
"""

from __future__ import annotations

import os
import re
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QFileDialog,
    QStatusBar, QProgressBar, QLabel,
    QToolBar, QMessageBox, QWidget,
    QVBoxLayout, QHBoxLayout,
)
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QAction

from ui.image_viewer import ImageViewer
from ui.file_list_panel import FileListPanel, SpeFileItem
from ui.frame_grid_panel import FrameGridPanel
from ui.plot_panel import PlotPanel, HistogramPanel
from ui.roi_panel import RoiPanel
from core.async_worker import SpeLoadWorker
from theme.styles import Fonts, Sizes, C_ACCENT, C_TEXT_DIM, C_BORDER, C_BG_MED


class AnalysisTab(QMainWindow):
    """
    SPE 분석 전용 QMainWindow.
    QTabWidget에 임베드할 때는 menuBar().setVisible(False) 후 사용.
    """

    # 외부로 전달할 시그널 (예: 파일 로드 완료 → 상태바 갱신)
    status_message = pyqtSignal(str)

    def __init__(self, spe_class=None, parent=None):
        super().__init__(parent)
        self.spe_class = spe_class
        self._workers: list = []
        self._current_spe: SpeFileItem | None = None

        self.setWindowFlags(Qt.WindowType.Widget)   # 탭 임베드용
        self.menuBar().setVisible(False)

        self.setStyleSheet(f"""
            QToolBar {{
                background: #0a0f1e;
                border-bottom: 1px solid {C_BORDER};
                spacing: 4px;
                padding: 2px 6px;
            }}
            QToolButton {{
                background: #0d1e38;
                color: {C_ACCENT};
                border: 1px solid #1a4060;
                border-radius: 3px;
                padding: 3px 8px;
                font-family: '{Fonts.MONO}';
                font-size: {Sizes.LOG};
            }}
            QToolButton:hover {{ background: #1a3a60; }}
            QToolButton:checked {{ background: #1a3010; color: {C_ACCENT}; border-color: #2a6020; }}
        """)

        self._space_timer = QTimer()
        self._space_timer.setSingleShot(True)
        self._space_timer.setInterval(400)
        self._space_count = 0

        self._setup_ui()
        self._setup_toolbar()
        self._setup_statusbar()
        self._connect_signals()
        self._setup_shortcuts()

    # ── 독 헬퍼 ──────────────────────────────────────────────────────

    def _make_dock_header(self, title: str) -> QWidget:
        hdr = QWidget()
        hdr.setFixedHeight(22)
        hdr.setStyleSheet(
            f"background: {C_BG_MED}; border-bottom: 1px solid {C_BORDER};"
        )
        row = QHBoxLayout(hdr)
        row.setContentsMargins(8, 0, 8, 0)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
            f" font-size: {Sizes.SMALL}; font-weight: bold;"
            " letter-spacing: 2px; background: transparent; border: none;"
        )
        row.addWidget(lbl)
        return hdr

    def _wrap_dock(self, obj_name: str, title: str, content: QWidget,
                   area: Qt.DockWidgetArea) -> QDockWidget:
        wrap = QWidget()
        vbox = QVBoxLayout(wrap)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(self._make_dock_header(title))
        vbox.addWidget(content, 1)
        dock = QDockWidget(self)
        dock.setObjectName(obj_name)
        dock.setWidget(wrap)
        dock.setTitleBarWidget(QWidget())
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(area, dock)
        return dock

    # ─────────────────────────────────────────
    # UI 구성 (기존 MainWindow._setup_ui 복사)
    # ─────────────────────────────────────────

    def _setup_ui(self):
        self.image_viewer = ImageViewer()
        self.setCentralWidget(self.image_viewer)

        self.file_list_panel = FileListPanel()
        self.dock_files = self._wrap_dock(
            "dock_files", "📁  FILES",
            self.file_list_panel, Qt.DockWidgetArea.LeftDockWidgetArea,
        )

        self.frame_grid_panel = FrameGridPanel()
        self.dock_frames = self._wrap_dock(
            "dock_frames", "🎞  FRAMES",
            self.frame_grid_panel, Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.splitDockWidget(self.dock_files, self.dock_frames, Qt.Orientation.Vertical)

        self.plot_panel = PlotPanel("Profile")
        self.dock_plot = self._wrap_dock(
            "dock_plot", "📈  PROFILE PLOT",
            self.plot_panel, Qt.DockWidgetArea.BottomDockWidgetArea,
        )

        self.histogram_panel = HistogramPanel()
        self.dock_histogram = self._wrap_dock(
            "dock_histogram", "📊  HISTOGRAM",
            self.histogram_panel, Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.splitDockWidget(self.dock_plot, self.dock_histogram, Qt.Orientation.Horizontal)

        self.roi_panel = RoiPanel()
        self.dock_roi = self._wrap_dock(
            "dock_roi", "📐  ROI LIST",
            self.roi_panel, Qt.DockWidgetArea.RightDockWidgetArea,
        )

        self.resizeDocks([self.dock_files, self.dock_frames], [200, 500], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_plot], [220], Qt.Orientation.Vertical)

    def _setup_toolbar(self):
        toolbar = QToolBar("Analysis Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)

        act_open = QAction("📂  Open SPE", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self._on_open_files)
        toolbar.addAction(act_open)

        toolbar.addSeparator()

        for text, attr in [
            ("📋  Files",     "dock_files"),
            ("🎞  Frames",    "dock_frames"),
            ("📈  Plot",      "dock_plot"),
            ("📊  Histogram", "dock_histogram"),
            ("🔲  ROI",       "dock_roi"),
        ]:
            act = QAction(text, self)
            act.setCheckable(True)
            act.setChecked(True)
            dock = getattr(self, attr)
            act.triggered.connect(dock.setVisible)
            toolbar.addAction(act)

        toolbar.addSeparator()
        act_reset = QAction("⟳  Reset View", self)
        act_reset.triggered.connect(self._on_reset_view)
        toolbar.addAction(act_reset)

    def _setup_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #a0a0b0;")
        self.status_bar.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

    def _connect_signals(self):
        self.file_list_panel.file_selected.connect(self._on_file_selected)
        self.file_list_panel.frame_changed.connect(self._on_frame_changed)
        self.file_list_panel.file_removed.connect(self.frame_grid_panel.remove_file)
        self.file_list_panel.btn_open.clicked.connect(self._on_open_files)

        self.frame_grid_panel.frame_clicked.connect(self._on_grid_frame_clicked)
        self.frame_grid_panel.checked_frames_changed.connect(self._on_checked_frames_changed)

        self.roi_panel.roi_selected.connect(self._on_roi_selected)
        self.roi_panel.roi_deleted.connect(self._on_roi_deleted)
        self.roi_panel.roi_goto.connect(self._on_roi_goto)

        view = self.image_viewer._view
        view.on_roi_added    = self._on_roi_added
        view.on_roi_selected = self._on_roi_panel_select
        view.on_roi_modified = self._update_profile_from_roi

        self.image_viewer.line_profile_updated.connect(self._on_line_profile)
        self.image_viewer.box_profile_updated.connect(self._on_box_profile)
        self.image_viewer.histogram_updated.connect(self._on_histogram)

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def open_spe(self, path: str) -> None:
        """외부에서 직접 SPE 파일을 열 때 사용 (예: Acquisition 탭 저장 후 자동 오픈)."""
        self._load_spe_async(path)

    def set_spe_class(self, spe_class) -> None:
        self.spe_class = spe_class

    # ─────────────────────────────────────────
    # 파일 오픈
    # ─────────────────────────────────────────

    def _on_open_files(self):
        if self.spe_class is None:
            QMessageBox.warning(self, "Warning", "SPE 클래스가 설정되지 않았습니다.")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open SPE Files", "", "SPE Files (*.spe);;All Files (*)"
        )
        for path in sorted(paths, key=self._natural_sort_key):
            self._load_spe_async(path)

    def _load_spe_async(self, filepath: str):
        if self.spe_class is None:
            return
        self.status_label.setText(f"Loading: {filepath}")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        worker = SpeLoadWorker(filepath, self.spe_class)
        worker.progress.connect(self.progress_bar.setValue)
        worker.finished.connect(self._on_spe_loaded)
        worker.error.connect(self._on_load_error)
        worker.finished.connect(lambda *_, w=worker: self._cleanup_worker(w))
        worker.error.connect(lambda *_, w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()

    def _cleanup_worker(self, worker):
        self.progress_bar.setVisible(False)
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def _on_spe_loaded(self, filepath: str, spe_obj):
        num_frames = getattr(spe_obj, 'num_frames', 1)
        self.file_list_panel.add_file(filepath, spe_obj, num_frames)
        filename = os.path.splitext(os.path.basename(filepath))[0]
        self.frame_grid_panel.add_file(spe_obj, filepath, num_frames, filename)
        msg = f"Loaded: {filepath}  [{num_frames} frames]"
        self.status_label.setText(msg)
        self.status_message.emit(msg)

    def _on_load_error(self, msg: str):
        self.status_label.setText(f"Error: {msg}")
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "Load Error", msg)

    # ─────────────────────────────────────────
    # 파일 / 프레임 선택
    # ─────────────────────────────────────────

    def _on_file_selected(self, spe_item: SpeFileItem, frame_idx: int):
        self._current_spe = spe_item
        frame = self._extract_frame(spe_item.spe_obj, frame_idx)
        if frame is not None:
            self.image_viewer.set_image_first(frame)
            self.frame_grid_panel.set_current_frame(spe_item.filepath, frame_idx)
            self._update_status(spe_item, frame_idx, frame)

    def _on_frame_changed(self, spe_item: SpeFileItem, frame_idx: int):
        frame = self._extract_frame(spe_item.spe_obj, frame_idx)
        if frame is not None:
            self.image_viewer.set_image(frame)
            self.frame_grid_panel.set_current_frame(spe_item.filepath, frame_idx)
            self._update_status(spe_item, frame_idx, frame)

    def _on_grid_frame_clicked(self, filepath: str, frame_idx: int):
        spe_item = self.file_list_panel.find_item(filepath)
        if spe_item is None:
            return
        self._current_spe = spe_item
        frame = self._extract_frame(spe_item.spe_obj, frame_idx)
        if frame is not None:
            self.image_viewer.set_image(frame)
            self.file_list_panel.select_file(filepath)
            self.file_list_panel.set_frame(frame_idx)
            self._update_status(spe_item, frame_idx, frame)

    def _on_checked_frames_changed(self, checked_list: list):
        if not checked_list:
            self.plot_panel.clear()
            return
        self.plot_panel.clear()
        roi_mode = self.image_viewer._roi_mode

        for filepath, frame_idx in checked_list:
            spe_item = self.file_list_panel.find_item(filepath)
            if spe_item is None:
                continue
            frame = self._extract_frame(spe_item.spe_obj, frame_idx)
            if frame is None:
                continue
            fname = os.path.splitext(os.path.basename(filepath))[0]
            label = f"{fname}_{frame_idx}" if spe_item.num_frames > 1 else fname

            if roi_mode == 'line' and self.image_viewer._roi_line_pts:
                (x0, y0), (x1, y1) = self.image_viewer._roi_line_pts
                self._compute_and_plot_line(frame, x0, y0, x1, y1, label)
            elif roi_mode == 'box' and self.image_viewer._roi_box_pts:
                (x0, y0), (x1, y1) = self.image_viewer._roi_box_pts
                self._compute_and_plot_box(frame, x0, y0, x1, y1, label)
            elif roi_mode == 'xprofile':
                self.plot_panel.plot_line_overlay(frame.mean(axis=0), label)
            elif roi_mode == 'yprofile':
                self.plot_panel.plot_line_overlay(frame.mean(axis=1), label)

        self.plot_panel.set_xlabel("Pixel")
        self.plot_panel.set_ylabel("Intensity")

    # ─────────────────────────────────────────
    # 프로파일 계산
    # ─────────────────────────────────────────

    def _compute_and_plot_line(self, frame, x0, y0, x1, y1, label):
        try:
            from scipy import ndimage
            img = frame.astype(np.float64)
            h, w = img.shape[:2]
            num = max(int(np.hypot(x1 - x0, y1 - y0)), 2)
            xs = np.linspace(np.clip(x0, 0, w-1), np.clip(x1, 0, w-1), num)
            ys = np.linspace(np.clip(y0, 0, h-1), np.clip(y1, 0, h-1), num)
            profile = ndimage.map_coordinates(img, [ys, xs], order=1)
            self.plot_panel.plot_line_overlay(profile, label)
        except Exception as e:
            print(f"Line profile error: {e}")

    def _compute_and_plot_box(self, frame, x0, y0, x1, y1, label):
        try:
            h, w = frame.shape[:2]
            ix0 = int(np.clip(min(x0, x1), 0, w-1))
            ix1 = int(np.clip(max(x0, x1), 0, w-1))
            iy0 = int(np.clip(min(y0, y1), 0, h-1))
            iy1 = int(np.clip(max(y0, y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = frame[iy0:iy1, ix0:ix1].astype(np.float64)
            self.plot_panel.plot_line_overlay(region.mean(axis=0), label)
        except Exception as e:
            print(f"Box profile error: {e}")

    # ─────────────────────────────────────────
    # 이미지 뷰어 → 플롯
    # ─────────────────────────────────────────

    def _on_histogram(self, counts: np.ndarray, bin_edges: np.ndarray):
        self.histogram_panel.plot_histogram(counts, bin_edges)

    def _on_line_profile(self, data: np.ndarray, label: str):
        checked = self.frame_grid_panel.get_checked_frames()
        if checked:
            self._on_checked_frames_changed(checked)
        else:
            self.plot_panel.plot_line(data, label)
            self.plot_panel.set_xlabel("Pixel")
            self.plot_panel.set_ylabel("Intensity")

    def _on_box_profile(self, x_mean: np.ndarray, y_mean: np.ndarray, label: str):
        checked = self.frame_grid_panel.get_checked_frames()
        if checked:
            self._on_checked_frames_changed(checked)
        else:
            self.plot_panel.plot_two_lines(x_mean, y_mean, "X mean", "Y mean")
            self.plot_panel.set_xlabel("Pixel")
            self.plot_panel.set_ylabel("Mean Intensity")

    # ─────────────────────────────────────────
    # ROI 핸들러
    # ─────────────────────────────────────────

    def _on_roi_added(self, roi):
        color_map = {'Line': '#e94560', 'Box': '#e94560', 'Hist': '#4ecdc4'}
        color = color_map.get(roi.roi_type, '#e94560')
        self.roi_panel.add_roi(roi.roi_id, roi.label(), color)

    def _on_roi_panel_select(self, roi_id):
        """이미지 뷰에서 ROI 클릭 선택 시 → 패널 하이라이트 + 프로파일 갱신."""
        if roi_id is not None:
            self.roi_panel.select_roi(roi_id)
            self._activate_roi(roi_id)
            self._update_profile_from_roi(roi_id)

    def _on_roi_selected(self, roi_id: int):
        self.image_viewer._view._select_roi(roi_id)
        self._activate_roi(roi_id)
        self._update_profile_from_roi(roi_id)

    def _activate_roi(self, roi_id: int):
        """ROI 타입에 따라 profile 또는 hist active 강조 설정 (뷰어 + 패널)."""
        from ui.roi_items import HistROI
        roi = self.image_viewer._view.get_roi(roi_id)
        if roi is None:
            return
        role = 'hist' if isinstance(roi, HistROI) else 'profile'
        self.image_viewer._set_active_roi(roi_id, role)
        self.roi_panel.set_active_roi(roi_id, role)

    def _update_profile_from_roi(self, roi_id: int):
        if self._current_spe is None:
            return
        frame = self._extract_frame(
            self._current_spe.spe_obj, self._current_spe.current_frame
        )
        if frame is None:
            return

        roi = self.image_viewer._view.get_roi(roi_id)
        if roi is None:
            return

        from ui.roi_items import LineROI, BoxROI, HistROI
        if isinstance(roi, HistROI):
            (x0, y0), (x1, y1) = roi.pts
            self.image_viewer._compute_histogram_direct(frame, x0, y0, x1, y1)
        elif isinstance(roi, LineROI):
            (x0, y0), (x1, y1) = roi.pts
            self.image_viewer._compute_line_profile_direct(frame, x0, y0, x1, y1)
        elif isinstance(roi, BoxROI):
            (x0, y0), (x1, y1) = roi.pts
            self.image_viewer._compute_box_profile_direct(frame, x0, y0, x1, y1)

    def _on_roi_deleted(self, roi_id: int):
        self.image_viewer._view.delete_roi(roi_id)
        self.roi_panel.remove_roi(roi_id)

    def _on_roi_goto(self, roi_id: int):
        roi = self.image_viewer._view.get_roi(roi_id)
        if roi:
            pts = roi.pts
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            self.image_viewer._view.centerOn(cx, cy)

    def _on_reset_view(self):
        self.image_viewer.autoRange()

    # ─────────────────────────────────────────
    # 유틸
    # ─────────────────────────────────────────

    def _extract_frame(self, spe_obj, frame_idx: int):
        try:
            return spe_obj.frame(frame_idx)
        except Exception as e:
            self.status_label.setText(f"Frame error: {e}")
            return None

    def _update_status(self, spe_item, frame_idx: int, frame: np.ndarray):
        name = os.path.basename(spe_item.filepath)
        h, w = frame.shape[:2]
        msg = f"{name}  |  Frame {frame_idx + 1}/{spe_item.num_frames}  |  {w} × {h}"
        self.status_label.setText(msg)

    def _setup_shortcuts(self):
        self._space_timer.timeout.connect(self._reset_space_count)

    def keyPressEvent(self, ev):
        focused = self.focusWidget()
        in_grid = focused in (
            self.frame_grid_panel.scroll_thumb,
            self.frame_grid_panel.list_widget
        )
        key = ev.key()

        if key == Qt.Key.Key_Space:
            if in_grid:
                ev.ignore()
                return
            self._on_space_pressed()
            ev.accept()
            return

        if not in_grid:
            roi_map = {
                Qt.Key.Key_L: "Line Profile",
                Qt.Key.Key_B: "Box Profile",
                Qt.Key.Key_X: "X Profile",
                Qt.Key.Key_Y: "Y Profile",
            }
            if key in roi_map:
                idx = self.image_viewer.roi_combo.findText(roi_map[key])
                if idx >= 0:
                    self.image_viewer.roi_combo.setCurrentIndex(idx)
                ev.accept()
                return
            if key == Qt.Key.Key_C:
                btn = self.image_viewer.btn_crosshair
                btn.setChecked(not btn.isChecked())
                ev.accept()
                return
            if key == Qt.Key.Key_Escape:
                self.image_viewer.roi_combo.setCurrentIndex(0)
                ev.accept()
                return

        super().keyPressEvent(ev)

    def _on_space_pressed(self):
        self._space_count += 1
        if self._space_count == 1:
            self._space_timer.start()
        elif self._space_count >= 2:
            self._space_timer.stop()
            self._space_count = 0
            self._do_reset()

    def _reset_space_count(self):
        self._space_count = 0

    def _do_reset(self):
        self.image_viewer.autoRange()
        self.plot_panel.clear()
        self.status_label.setText("Reset")

    @staticmethod
    def _natural_sort_key(s: str) -> list:
        return [int(c) if c.isdigit() else c.lower()
                for c in re.split(r'(\d+)', s)]
