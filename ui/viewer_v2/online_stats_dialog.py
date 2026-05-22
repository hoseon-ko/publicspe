"""Online Statistics 다이얼로그.

LightField 식 픽셀 통계 패널. 선택 ROI(없으면 전체 이미지)의 기본 통계를
5컬럼 레이아웃으로 표시한다. 현재는 Viewer 1 만 채우고 2~5 는 N/A
(향후 멀티 뷰어 확장 대비).

- 통계 9종: 점 개수 / 무게중심 / Max·Min 위치 / Max·Min / Sum / Avg / Std
- 자동 갱신 주기 (N초) 스핀박스 + 즉시 갱신 버튼
- Save to File (CSV/TXT) / Copy (클립보드) / Close
"""
from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QSpinBox, QPushButton, QFrame, QFileDialog, QApplication, QWidget,
)
from PyQt6.QtCore import Qt, QTimer

# 통계 행 정의: (표시 라벨, dict 키)
_STAT_ROWS = [
    ("No. of Points Selected", "n_points"),
    ("Loc. of Center Mass",    "center_mass"),
    ("Loc. of Max Intensity",  "max_loc"),
    ("Loc. of Min Intensity",  "min_loc"),
    ("Maximum Intensity",      "max_val"),
    ("Minimum Intensity",      "min_val"),
    ("Sum Intensity",          "sum_val"),
    ("Average Intensity",      "avg_val"),
    ("Std. Dev. Intensity",    "std_val"),
]

N_VIEWERS = 5
_NA = "N/A"


def compute_basic_stats(sub: np.ndarray, x0: int = 0, y0: int = 0) -> dict | None:
    """2D 배열의 기본 픽셀 통계 계산.

    x0,y0: 전체 이미지 좌표 보정용 좌상단 오프셋 (위치 통계에 가산).
    NaN/inf 픽셀은 모든 계산에서 제외. 유효 픽셀이 없으면 None 반환.
    무게중심은 intensity-weighted (LightField 와 동일, 음수 가중 방지를 위해
    min 값 시프트). 위치는 (x, y) 튜플로 반환.
    """
    a = np.asarray(sub, dtype=np.float64)
    if a.ndim != 2 or a.size == 0:
        return None
    finite = np.isfinite(a)
    n = int(np.count_nonzero(finite))
    if n == 0:
        return None

    vals = a[finite]
    sum_v = float(vals.sum())
    avg_v = float(vals.mean())
    std_v = float(vals.std())
    max_v = float(vals.max())
    min_v = float(vals.min())

    # Max/Min 위치: 비유효 픽셀이 argmax/argmin 에 잡히지 않도록 치환
    a_max = np.where(finite, a, -np.inf)
    a_min = np.where(finite, a,  np.inf)
    my, mx = np.unravel_index(int(np.argmax(a_max)), a.shape)
    ny, nx = np.unravel_index(int(np.argmin(a_min)), a.shape)

    # 무게중심: min 시프트한 가중치로 계산 (음수 데이터에서도 안정)
    w = np.where(finite, a - min_v, 0.0)
    wsum = float(w.sum())
    if wsum > 0:
        ys, xs = np.indices(a.shape)
        cy = float((w * ys).sum() / wsum)
        cx = float((w * xs).sum() / wsum)
    else:
        cy = (a.shape[0] - 1) / 2.0
        cx = (a.shape[1] - 1) / 2.0

    return {
        "n_points":    n,
        "center_mass": (cx + x0, cy + y0),
        "max_loc":     (int(mx) + x0, int(my) + y0),
        "min_loc":     (int(nx) + x0, int(ny) + y0),
        "max_val":     max_v,
        "min_val":     min_v,
        "sum_val":     sum_v,
        "avg_val":     avg_v,
        "std_val":     std_v,
    }


def _fmt(key: str, stats: dict | None) -> str:
    """행 키 + 통계 dict → 표시 문자열. stats 가 None 이면 전부 N/A."""
    if stats is None:
        return _NA
    v = stats.get(key)
    if v is None:
        return _NA
    if key == "n_points":
        return f"{int(v)}"
    if key == "center_mass":
        return f"({v[0]:.6g}, {v[1]:.6g})"
    if key in ("max_loc", "min_loc"):
        return f"({v[0]}, {v[1]})"
    if key == "sum_val":
        return f"{v:.0f}"
    return f"{v:.6g}"


class OnlineStatsDialog(QDialog):
    """선택 ROI/전체 이미지의 픽셀 통계를 5컬럼으로 표시하는 팝업."""

    def __init__(self, region_provider, parent=None):
        """region_provider: () -> (sub_2d | None, x0, y0) 콜백."""
        super().__init__(parent)
        self._provider = region_provider
        self._stats: list[dict | None] = [None] * N_VIEWERS
        self._cells: list[list[QLabel]] = []  # [row][viewer]

        self.setWindowTitle("Online Statistics")
        self.setMinimumWidth(560)
        self._build_ui()
        self._apply_style()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._on_interval_changed(self.spin_interval.value())

        self.refresh()

    # ── UI 구성 ────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)

        # 헤더 행: "Viewer:" + 1~5
        hdr = QLabel("Viewer:")
        hdr.setObjectName("rowLabel")
        hdr.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(hdr, 0, 0)
        for c in range(N_VIEWERS):
            h = QLabel(str(c + 1))
            h.setObjectName("colHeaderActive" if c == 0 else "colHeader")
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(h, 0, c + 1)

        # 통계 행
        for r, (label, _key) in enumerate(_STAT_ROWS, start=1):
            lbl = QLabel(label)
            lbl.setObjectName("rowLabel")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(lbl, r, 0)

            row_cells = []
            for c in range(N_VIEWERS):
                cell = QLabel(_NA)
                cell.setObjectName("cellActive" if c == 0 else "cell")
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(cell, r, c + 1)
                row_cells.append(cell)
            self._cells.append(row_cells)

        grid.setColumnStretch(1, 1)
        for c in range(2, N_VIEWERS + 1):
            grid.setColumnStretch(c, 1)
        root.addLayout(grid)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("sep")
        root.addWidget(sep)

        # 자동 갱신 주기
        interval_row = QHBoxLayout()
        interval_row.addStretch()
        interval_row.addWidget(QLabel("Update statistics every:"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 3600)
        self.spin_interval.setValue(1)
        self.spin_interval.setFixedWidth(60)
        self.spin_interval.valueChanged.connect(self._on_interval_changed)
        interval_row.addWidget(self.spin_interval)
        interval_row.addWidget(QLabel("seconds"))
        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setObjectName("iconBtn")
        self.btn_refresh.setFixedWidth(36)
        self.btn_refresh.setToolTip("지금 갱신")
        self.btn_refresh.clicked.connect(self.refresh)
        interval_row.addWidget(self.btn_refresh)
        interval_row.addStretch()
        root.addLayout(interval_row)

        # 하단 버튼
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Save to File...")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.clicked.connect(self._on_copy)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_copy)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog { background: #0a1020; }
            QLabel { color: #a0b0c0; font-family: 'Consolas'; }
            QLabel#rowLabel { color: #7f93ad; padding: 2px 8px; }
            QLabel#colHeader, QLabel#colHeaderActive {
                color: #a0b0c0; font-weight: bold; padding: 4px;
                border: 1px solid #1a3a60; background: #0d1a2e;
            }
            QLabel#colHeaderActive { color: #4ecdc4; border: 1px solid #4ecdc4; }
            QLabel#cell {
                color: #5a6b80; padding: 3px 6px; background: #0a1525;
                border: 1px solid #11233d;
            }
            QLabel#cellActive {
                color: #e0e8f0; padding: 3px 6px; background: #0d2038;
                border: 1px solid #1a3a60;
            }
            QFrame#sep { color: #1a3a60; background: #1a3a60; max-height: 1px; }
            QSpinBox {
                background: #0d2038; color: #a0c0e0; border: 1px solid #1a3a60;
                border-radius: 3px; padding: 2px;
            }
            QPushButton {
                color: #a0b0c0; background: #0d1a2e; border: 1px solid #1a3a60;
                border-radius: 4px; padding: 5px 14px; font-weight: bold;
            }
            QPushButton:hover { background: #1a3a60; color: white; }
            QPushButton#iconBtn { padding: 2px; font-size: 14px; }
        """)

    # ── 동작 ──────────────────────────────────────────────────
    def _on_interval_changed(self, seconds: int):
        self._timer.start(max(1, int(seconds)) * 1000)

    def refresh(self):
        """provider 로부터 활성 영역을 받아 Viewer 1 통계를 재계산·표시."""
        sub, x0, y0 = (None, 0, 0)
        if self._provider is not None:
            try:
                sub, x0, y0 = self._provider()
            except Exception as e:
                print(f"[OnlineStats] provider 호출 실패: {e}")
                sub = None
        self._stats[0] = compute_basic_stats(sub, x0, y0) if sub is not None else None
        self._render()

    def _render(self):
        for r, (_label, key) in enumerate(_STAT_ROWS):
            for c in range(N_VIEWERS):
                self._cells[r][c].setText(_fmt(key, self._stats[c]))

    def _as_text(self, sep: str = "\t") -> str:
        lines = ["Statistic" + sep + sep.join(f"Viewer {c + 1}" for c in range(N_VIEWERS))]
        for r, (label, key) in enumerate(_STAT_ROWS):
            cells = [_fmt(key, self._stats[c]) for c in range(N_VIEWERS)]
            lines.append(label + sep + sep.join(cells))
        return "\n".join(lines)

    def _on_copy(self):
        QApplication.clipboard().setText(self._as_text(sep="\t"))

    def _on_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Statistics", "statistics.csv",
            "CSV Files (*.csv);;Text Files (*.txt)",
        )
        if not path:
            return
        sep = "," if path.lower().endswith(".csv") else "\t"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._as_text(sep=sep))
        except Exception as e:
            print(f"[OnlineStats] 저장 실패: {e}")

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
