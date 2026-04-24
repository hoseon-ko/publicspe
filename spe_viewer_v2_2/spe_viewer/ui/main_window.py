"""
main_window.py
메인 윈도우
"""

import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QFileDialog,
    QStatusBar, QProgressBar, QLabel,
    QToolBar, QMessageBox
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QAction, QShortcut, QKeySequence

from ui.image_viewer import ImageViewer
from ui.file_list_panel import FileListPanel, SpeFileItem
from ui.frame_grid_panel import FrameGridPanel
from ui.plot_panel import PlotPanel
from core.async_worker import SpeLoadWorker


class MainWindow(QMainWindow):
    def __init__(self, spe_class=None):
        super().__init__()
        self.spe_class = spe_class
        self._workers = []
        self._current_spe = None
        self._current_roi_mode = None

        self.setWindowTitle("SPE Viewer")
        self.setMinimumSize(1200, 800)
        self.resize(1500, 950)
        self._space_timer = QTimer()
        self._space_timer.setSingleShot(True)
        self._space_timer.setInterval(400)  # 400ms 안에 두 번
        self._space_count = 0

        self._setup_ui()
        self._setup_toolbar()
        self._setup_statusbar()
        self._connect_signals()
        self._setup_shortcuts()

    # ─────────────────────────────────────────
    # UI 구성
    # ─────────────────────────────────────────

    def _setup_ui(self):
        # 중앙: 이미지 뷰어
        self.image_viewer = ImageViewer()
        self.setCentralWidget(self.image_viewer)

        # ── 좌측 Dock: 파일 리스트 ──
        self.file_list_panel = FileListPanel()
        self.dock_files = QDockWidget("Files", self)
        self.dock_files.setWidget(self.file_list_panel)
        self.dock_files.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_files)

        # ── 프레임 그리드: 파일 리스트 바로 오른쪽에 나란히 ──
        self.frame_grid_panel = FrameGridPanel()
        self.dock_frames = QDockWidget("Frames", self)
        self.dock_frames.setWidget(self.frame_grid_panel)
        self.dock_frames.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_frames)
        # 파일 리스트 오른쪽에 나란히 배치
        self.splitDockWidget(self.dock_files, self.dock_frames, Qt.Orientation.Horizontal)

        # ── 하단 Dock: 프로파일 플롯 ──
        self.plot_panel = PlotPanel("Profile")
        self.dock_plot = QDockWidget("Profile Plot", self)
        self.dock_plot.setWidget(self.plot_panel)
        self.dock_plot.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea |
            Qt.DockWidgetArea.TopDockWidgetArea |
            Qt.DockWidgetArea.LeftDockWidgetArea |
            Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_plot)

        # 초기 크기
        self.resizeDocks(
            [self.dock_files, self.dock_frames],
            [180, 320],
            Qt.Orientation.Horizontal
        )
        self.resizeDocks(
            [self.dock_plot],
            [220],
            Qt.Orientation.Vertical
        )

    def _setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)

        act_open = QAction("📂  Open SPE", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self._on_open_files)
        toolbar.addAction(act_open)

        toolbar.addSeparator()

        act_toggle_files = QAction("📋  Files", self)
        act_toggle_files.setCheckable(True)
        act_toggle_files.setChecked(True)
        act_toggle_files.triggered.connect(self.dock_files.setVisible)
        toolbar.addAction(act_toggle_files)

        act_toggle_frames = QAction("🎞  Frames", self)
        act_toggle_frames.setCheckable(True)
        act_toggle_frames.setChecked(True)
        act_toggle_frames.triggered.connect(self.dock_frames.setVisible)
        toolbar.addAction(act_toggle_frames)

        act_toggle_plot = QAction("📈  Plot", self)
        act_toggle_plot.setCheckable(True)
        act_toggle_plot.setChecked(True)
        act_toggle_plot.triggered.connect(self.dock_plot.setVisible)
        toolbar.addAction(act_toggle_plot)

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
        # 파일 리스트
        self.file_list_panel.file_selected.connect(self._on_file_selected)
        self.file_list_panel.frame_changed.connect(self._on_frame_changed)
        self.file_list_panel.file_removed.connect(self.frame_grid_panel.remove_file)

        # 프레임 그리드
        self.frame_grid_panel.frame_clicked.connect(self._on_grid_frame_clicked)
        self.frame_grid_panel.checked_frames_changed.connect(self._on_checked_frames_changed)

        # 이미지 뷰어 → 플롯
        self.image_viewer.line_profile_updated.connect(self._on_line_profile)
        self.image_viewer.box_profile_updated.connect(self._on_box_profile)

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
        for path in paths:
            self._load_spe_async(path)

    def _load_spe_async(self, filepath: str):
        self.status_label.setText(f"Loading: {filepath}")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        worker = SpeLoadWorker(filepath, self.spe_class)
        worker.progress.connect(self.progress_bar.setValue)
        worker.finished.connect(self._on_spe_loaded)
        worker.error.connect(self._on_load_error)
        worker.finished.connect(lambda *_: self.progress_bar.setVisible(False))
        self._workers.append(worker)
        worker.start()

    def _on_spe_loaded(self, filepath: str, spe_obj):
        import os
        num_frames = getattr(spe_obj, 'num_frames', 1)
        self.file_list_panel.add_file(filepath, spe_obj, num_frames)
        filename = os.path.splitext(os.path.basename(filepath))[0]
        # 그리드에 이 파일의 프레임들 추가
        self.frame_grid_panel.add_file(spe_obj, filepath, num_frames, filename)
        self.status_label.setText(f"Loaded: {filepath}  [{num_frames} frames]")

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
        """파일 리스트 슬라이더"""
        frame = self._extract_frame(spe_item.spe_obj, frame_idx)
        if frame is not None:
            self.image_viewer.set_image(frame)
            self.frame_grid_panel.set_current_frame(spe_item.filepath, frame_idx)
            self._update_status(spe_item, frame_idx, frame)

    def _on_grid_frame_clicked(self, filepath: str, frame_idx: int):
        """프레임 그리드 썸네일 클릭"""
        # filepath로 spe_item 찾기
        spe_item = self.file_list_panel.find_item(filepath)
        if spe_item is None:
            return
        self._current_spe = spe_item
        frame = self._extract_frame(spe_item.spe_obj, frame_idx)
        if frame is not None:
            self.image_viewer.set_image_first(frame)
            self.file_list_panel.select_file(filepath)
            self.file_list_panel.set_frame(frame_idx)
            self._update_status(spe_item, frame_idx, frame)

    def _on_checked_frames_changed(self, checked_list: list):
        """체크박스 변경 → 프로파일 다중 그래프. checked_list = [(filepath, frame_idx), ...]"""
        if not checked_list:
            self.plot_panel.clear()
            return

        self.plot_panel.clear()
        import os
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

    def _compute_and_plot_line(self, frame, x0, y0, x1, y1, label):
        try:
            from scipy import ndimage
            img = frame.astype(np.float64)
            h, w = img.shape[:2]
            num = max(int(np.hypot(x1 - x0, y1 - y0)), 2)
            xs = np.linspace(np.clip(x0, 0, w - 1), np.clip(x1, 0, w - 1), num)
            ys = np.linspace(np.clip(y0, 0, h - 1), np.clip(y1, 0, h - 1), num)
            profile = ndimage.map_coordinates(img, [ys, xs], order=1)
            self.plot_panel.plot_line_overlay(profile, label)
        except Exception as e:
            print(f"Multi-frame line profile error: {e}")

    def _compute_and_plot_box(self, frame, x0, y0, x1, y1, label):
        try:
            img = frame
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0, x1), 0, w - 1))
            ix1 = int(np.clip(max(x0, x1), 0, w - 1))
            iy0 = int(np.clip(min(y0, y1), 0, h - 1))
            iy1 = int(np.clip(max(y0, y1), 0, h - 1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].astype(np.float64)
            x_mean = region.mean(axis=0)
            self.plot_panel.plot_line_overlay(x_mean, label)
        except Exception as e:
            print(f"Multi-frame box profile error: {e}")

    # ─────────────────────────────────────────
    # 이미지 뷰어 → 플롯 (현재 프레임)
    # ─────────────────────────────────────────

    def _on_line_profile(self, data: np.ndarray, label: str):
        checked = self.frame_grid_panel.get_checked_frames()
        if not checked:
            self.plot_panel.plot_line(data, label)
            self.plot_panel.set_xlabel("Pixel")
            self.plot_panel.set_ylabel("Intensity")

    def _on_box_profile(self, x_mean: np.ndarray, y_mean: np.ndarray, label: str):
        checked = self.frame_grid_panel.get_checked_frames()
        if not checked:
            self.plot_panel.plot_two_lines(x_mean, y_mean, "X mean", "Y mean")
            self.plot_panel.set_xlabel("Pixel")
            self.plot_panel.set_ylabel("Mean Intensity")

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
        import os
        name = os.path.basename(spe_item.filepath)
        h, w = frame.shape[:2]
        self.status_label.setText(
            f"{name}  |  Frame {frame_idx + 1}/{spe_item.num_frames}  |  {w} × {h}"
        )

    def _setup_shortcuts(self):
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        shortcut.activated.connect(self._on_space_pressed)
        self._space_timer.timeout.connect(self._reset_space_count)

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
        """뷰어 + 플롯 동시 리셋"""
        self.image_viewer.image_view.autoRange()
        self.plot_panel.clear()
        self.status_label.setText("Reset")

    def _on_reset_view(self):
        self.image_viewer.image_view.autoRange()
