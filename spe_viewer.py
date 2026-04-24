"""
spe_viewer.py
PyQt6 + matplotlib 기반 SPE 파일 뷰어

기능
----
- SPE 파일 드래그&드롭 또는 파일 열기 다이얼로그
- cmap 실시간 변경
- brightness / contrast 슬라이더
- 멀티프레임 전환 슬라이더 + ◀▶ 버튼 + 키보드 ←→
- 마우스 커서 위치 픽셀값 실시간 표시
- 메타데이터 패널 (우측)
- XML footer 추출 / PGM 저장 버튼

설치
----
    pip install PyQt6 matplotlib numpy

사용 예시
---------
    # 단독 실행
    python spe_viewer.py
    python spe_viewer.py "D:/data/32nm.spe"

    # 다른 스크립트에서 호출
    from spe_viewer import show_spe
    show_spe(r"D:\data\32nm.spe")
"""

import sys
from pathlib import Path

import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QSlider, QComboBox, QPushButton,
    QStatusBar, QFileDialog, QSizePolicy,
    QTextEdit, QGroupBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from spe_reader import SpeFile, extract_xml, save_pgm


# ── cmap 목록 ────────────────────────────────────────────────────────────────
CMAP_LIST = [
    "gray", "gray_r",
    "inferno", "magma", "plasma", "viridis",
    "hot", "afmhot",
    "coolwarm", "bwr", "seismic",
    "jet", "turbo", "rainbow",
    "cividis", "twilight",
]


# ─────────────────────────────────────────────────────────────────────────────
class SpeViewer(QMainWindow):

    def __init__(self, spe_path: "str | Path | None" = None):
        super().__init__()
        self._spe: SpeFile | None = None
        self._cbar = None
        self._im   = None
        self._frame_idx  = 0
        self._cmap = "gray"
        self._vmin = 0.0
        self._vmax = 1.0
        self._global_min = 0.0
        self._global_max = 1.0

        self._build_ui()
        self.setAcceptDrops(True)

        if spe_path:
            self._load(spe_path)

    # ── UI 구성 ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.setWindowTitle("SPE Viewer")
        self.resize(1100, 720)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 4)
        root.setSpacing(6)

        # ── 좌측: 이미지 + 컨트롤 ─────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)

        # matplotlib canvas
        self._fig = Figure(tight_layout=True)
        self._ax  = self._fig.add_subplot(111)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        left_layout.addWidget(self._canvas)

        # 파일 열기
        row_file = QHBoxLayout()
        btn_open = QPushButton("📂  SPE 파일 열기")
        btn_open.clicked.connect(self._open_dialog)
        row_file.addWidget(btn_open)
        row_file.addStretch()
        left_layout.addLayout(row_file)

        # cmap
        row_cmap = QHBoxLayout()
        row_cmap.addWidget(QLabel("Colormap"))
        self._cmap_box = QComboBox()
        self._cmap_box.addItems(CMAP_LIST)
        self._cmap_box.setCurrentText("gray")
        self._cmap_box.currentTextChanged.connect(self._on_cmap_change)
        self._cmap_box.setFixedWidth(150)
        row_cmap.addWidget(self._cmap_box)
        row_cmap.addStretch()
        left_layout.addLayout(row_cmap)

        # Min 슬라이더
        row_min = QHBoxLayout()
        row_min.addWidget(QLabel("Min"))
        self._sld_min = QSlider(Qt.Orientation.Horizontal)
        self._sld_min.setRange(0, 1000)
        self._sld_min.setValue(10)
        self._sld_min.valueChanged.connect(self._on_range_change)
        self._lbl_min = QLabel("—")
        self._lbl_min.setFixedWidth(80)
        row_min.addWidget(self._sld_min)
        row_min.addWidget(self._lbl_min)
        left_layout.addLayout(row_min)

        # Max 슬라이더
        row_max = QHBoxLayout()
        row_max.addWidget(QLabel("Max"))
        self._sld_max = QSlider(Qt.Orientation.Horizontal)
        self._sld_max.setRange(0, 1000)
        self._sld_max.setValue(990)
        self._sld_max.valueChanged.connect(self._on_range_change)
        self._lbl_max = QLabel("—")
        self._lbl_max.setFixedWidth(80)
        row_max.addWidget(self._sld_max)
        row_max.addWidget(self._lbl_max)
        left_layout.addLayout(row_max)

        # 프레임 슬라이더
        row_frame = QHBoxLayout()
        row_frame.addWidget(QLabel("Frame"))
        self._btn_prev = QPushButton("◀")
        self._btn_prev.setFixedWidth(32)
        self._btn_prev.clicked.connect(self._prev_frame)
        self._btn_next = QPushButton("▶")
        self._btn_next.setFixedWidth(32)
        self._btn_next.clicked.connect(self._next_frame)
        self._sld_frame = QSlider(Qt.Orientation.Horizontal)
        self._sld_frame.setRange(0, 0)
        self._sld_frame.setValue(0)
        self._sld_frame.valueChanged.connect(self._on_frame_change)
        self._lbl_frame = QLabel("— / —")
        self._lbl_frame.setFixedWidth(70)
        row_frame.addWidget(self._btn_prev)
        row_frame.addWidget(self._sld_frame)
        row_frame.addWidget(self._btn_next)
        row_frame.addWidget(self._lbl_frame)
        left_layout.addLayout(row_frame)

        # ── 우측: 메타 + 저장 버튼 ────────────────────────────────────────
        right = QWidget()
        right.setFixedWidth(280)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        grp_meta = QGroupBox("메타데이터")
        grp_meta_layout = QVBoxLayout(grp_meta)
        self._meta_box = QTextEdit()
        self._meta_box.setReadOnly(True)
        self._meta_box.setFontFamily("Courier New")
        self._meta_box.setFontPointSize(9)
        self._meta_box.setText("파일을 열어주세요.")
        grp_meta_layout.addWidget(self._meta_box)
        right_layout.addWidget(grp_meta)

        grp_save = QGroupBox("저장")
        grp_save_layout = QVBoxLayout(grp_save)
        btn_png = QPushButton("PNG 저장")
        btn_png.clicked.connect(self._save_png)
        btn_xml = QPushButton("XML footer 추출")
        btn_xml.clicked.connect(self._save_xml)
        btn_pgm = QPushButton("PGM P5 저장 (전체 프레임)")
        btn_pgm.clicked.connect(self._save_pgm)
        grp_save_layout.addWidget(btn_png)
        grp_save_layout.addWidget(btn_xml)
        grp_save_layout.addWidget(btn_pgm)
        right_layout.addWidget(grp_save)
        right_layout.addStretch()

        # 좌우 합치기
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        root.addWidget(splitter)

        # 상태바
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("SPE 파일을 드래그하거나 '파일 열기'를 클릭하세요.")

        self._set_controls_enabled(False)

    # ── 파일 로드 ─────────────────────────────────────────────────────────────
    def _load(self, path: "str | Path"):
        try:
            self._spe = SpeFile(path)
        except Exception as e:
            self._statusbar.showMessage(f"[오류] {e}")
            return

        spe = self._spe
        data = spe.data  # (frames, H, W)

        self._frame_idx = 0
        flat = data.astype(np.float64).ravel()
        self._global_min = float(np.percentile(flat, 1))
        self._global_max = float(np.percentile(flat, 99))
        self._vmin = self._global_min
        self._vmax = self._global_max

        # 슬라이더 초기화
        self._sld_min.blockSignals(True)
        self._sld_max.blockSignals(True)
        self._sld_frame.blockSignals(True)
        self._sld_min.setValue(10)
        self._sld_max.setValue(990)
        self._sld_frame.setRange(0, max(spe.num_frames - 1, 0))
        self._sld_frame.setValue(0)
        self._sld_min.blockSignals(False)
        self._sld_max.blockSignals(False)
        self._sld_frame.blockSignals(False)

        self._lbl_min.setText(f"{self._vmin:.1f}")
        self._lbl_max.setText(f"{self._vmax:.1f}")
        self._lbl_frame.setText(f"0 / {spe.num_frames - 1}")

        single = spe.num_frames == 1
        self._sld_frame.setEnabled(not single)
        self._btn_prev.setEnabled(not single)
        self._btn_next.setEnabled(not single)

        self._update_meta()
        self._draw()
        self._set_controls_enabled(True)
        self.setWindowTitle(f"SPE Viewer — {Path(path).name}")
        self._statusbar.showMessage(
            f"{Path(path).name}  |  {spe.shape}  |  {data.dtype}"
        )

    def _update_meta(self):
        if self._spe is None:
            return
        lines = []
        skip = {"file", "rois", "wavelengths_nm", "wavelength_poly_coeffs"}
        for k, v in self._spe.meta.items():
            if k in skip:
                continue
            if isinstance(v, float):
                lines.append(f"{k}: {v:.4g}")
            else:
                lines.append(f"{k}: {v}")
        self._meta_box.setText("\n".join(lines))

    # ── 그리기 ────────────────────────────────────────────────────────────────
    def _draw(self):
        if self._spe is None:
            return
        self._ax.clear()
        if self._cbar is not None:
            self._cbar.remove()
            self._cbar = None

        img = self._spe.data[self._frame_idx].astype(np.float64)
        self._im = self._ax.imshow(
            img,
            cmap=self._cmap,
            vmin=self._vmin,
            vmax=self._vmax,
            origin="upper",
            aspect="equal",
            interpolation="nearest",
        )
        self._cbar = self._fig.colorbar(self._im, ax=self._ax, fraction=0.046, pad=0.04)
        n = self._spe.num_frames
        h, w = self._spe.data.shape[1], self._spe.data.shape[2]
        self._ax.set_title(
            f"Frame {self._frame_idx} / {n - 1}   |   {w}×{h} px   |   {self._spe.data.dtype}",
            fontsize=9,
        )
        self._ax.set_xlabel("Column (px)", fontsize=8)
        self._ax.set_ylabel("Row (px)", fontsize=8)
        self._canvas.draw()

    def _update_image(self):
        if self._spe is None or self._im is None:
            return
        self._im.set_data(self._spe.data[self._frame_idx].astype(np.float64))
        self._im.set_cmap(self._cmap)
        self._im.set_clim(self._vmin, self._vmax)
        if self._cbar is not None:
            self._cbar.update_normal(self._im)
        n = self._spe.num_frames
        h, w = self._spe.data.shape[1], self._spe.data.shape[2]
        self._ax.set_title(
            f"Frame {self._frame_idx} / {n - 1}   |   {w}×{h} px   |   {self._spe.data.dtype}",
            fontsize=9,
        )
        self._canvas.draw_idle()

    # ── 이벤트 핸들러 ─────────────────────────────────────────────────────────
    def _on_cmap_change(self, name: str):
        self._cmap = name
        self._update_image()

    def _on_range_change(self):
        total = self._global_max - self._global_min
        self._vmin = self._global_min + (self._sld_min.value() / 1000) * total
        self._vmax = self._global_min + (self._sld_max.value() / 1000) * total
        if self._vmin >= self._vmax:
            self._vmax = self._vmin + 1e-6
        self._lbl_min.setText(f"{self._vmin:.1f}")
        self._lbl_max.setText(f"{self._vmax:.1f}")
        self._update_image()

    def _on_frame_change(self, val: int):
        self._frame_idx = val
        n = self._spe.num_frames if self._spe else 0
        self._lbl_frame.setText(f"{val} / {n - 1}")
        self._update_image()

    def _prev_frame(self):
        self._sld_frame.setValue(max(0, self._frame_idx - 1))

    def _next_frame(self):
        n = self._spe.num_frames if self._spe else 1
        self._sld_frame.setValue(min(n - 1, self._frame_idx + 1))

    def _on_mouse_move(self, event):
        if self._spe is None or event.xdata is None or event.ydata is None:
            return
        col = int(round(event.xdata))
        row = int(round(event.ydata))
        h, w = self._spe.data.shape[1], self._spe.data.shape[2]
        if 0 <= row < h and 0 <= col < w:
            val = self._spe.data[self._frame_idx, row, col]
            self._statusbar.showMessage(
                f"x={col}  y={row}  |  value={val}"
                f"   |   {self._spe.path.name}  {self._spe.data.shape}  {self._spe.data.dtype}"
            )

    # ── 파일 열기 ─────────────────────────────────────────────────────────────
    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "SPE 파일 열기", "", "SPE Files (*.spe);;All Files (*)"
        )
        if path:
            self._load(path)

    # ── 드래그&드롭 ───────────────────────────────────────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            self._load(urls[0].toLocalFile())

    # ── 저장 ─────────────────────────────────────────────────────────────────
    def _save_png(self):
        if self._spe is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "PNG 저장",
            str(self._spe.path.with_name(f"{self._spe.path.stem}_frame{self._frame_idx}.png")),
            "PNG (*.png)",
        )
        if path:
            self._fig.savefig(path, dpi=150, bbox_inches="tight")
            self._statusbar.showMessage(f"저장 완료: {path}")

    def _save_xml(self):
        if self._spe is None:
            return
        result = extract_xml(self._spe)
        if result:
            self._statusbar.showMessage(f"XML 저장 완료: {result}")
        else:
            self._statusbar.showMessage("XML footer 없음 (SPE 2.x)")

    def _save_pgm(self):
        if self._spe is None:
            return
        saved = save_pgm(self._spe)
        self._statusbar.showMessage(f"PGM 저장 완료: {len(saved)}개 파일")

    # ── 유틸 ─────────────────────────────────────────────────────────────────
    def _set_controls_enabled(self, enabled: bool):
        for w in (self._sld_min, self._sld_max, self._cmap_box):
            w.setEnabled(enabled)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Right:
            self._next_frame()
        elif event.key() == Qt.Key.Key_Left:
            self._prev_frame()
        else:
            super().keyPressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# 편의 함수
# ─────────────────────────────────────────────────────────────────────────────

def show_spe(path: "str | Path | None" = None):
    """
    SPE 뷰어를 열고 파일을 표시한다.

    Parameters
    ----------
    path : str | Path | None
        SPE 파일 경로. None이면 빈 뷰어로 시작.
    """
    app = QApplication.instance() or QApplication(sys.argv)
    win = SpeViewer(path)
    win.show()
    app.exec()


# ─────────────────────────────────────────────────────────────────────────────
# 단독 실행
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    show_spe(path)
