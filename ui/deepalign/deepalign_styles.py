"""DeepAlign 공통 스타일 보조 파일.

이 파일은 DeepAlign UI 전반에서 공통으로 쓰는 가벼운 표시 보조 함수를 모아둡니다.
주요 역할은 다음과 같습니다.
- 진행률 바 시각 갱신
- 카메라 액션 버튼의 enabled/disabled 상태 표현
- 대시보드 버튼/라벨 생성 보조
- 섹션 및 그리드 라벨의 공통 스타일 보조
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QDockWidget


from theme.styles import (
    C_BG_DEEP, C_BG_MED, C_BORDER, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD, Fonts, Sizes
)

class DeepAlignStylesMixin:
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
                   area: Qt.DockWidgetArea, host) -> QDockWidget:
        wrap = QWidget()
        vbox = QVBoxLayout(wrap)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(self._make_dock_header(title))
        vbox.addWidget(content, 1)
        dock = QDockWidget(host)
        dock.setObjectName(obj_name)
        dock.setWidget(wrap)
        dock.setTitleBarWidget(QWidget()) # Hide default title bar
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        host.addDockWidget(area, dock)
        return dock
    def _set_master_progress(self, value: int):
        pct = max(0, min(100, int(value)))
        self.prog_grid.setColumnStretch(0, pct)
        self.prog_grid.setColumnStretch(1, max(0, 100 - pct))
        self.lbl_prog_text.setText(f"{pct}% COMPLETE")

    def _set_camera_action_state(self, connected: bool, busy: bool = False):
        """버튼 활성화 상태를 3-state로 관리.

        - not connected : CONNECT만 활성, 나머지 전부 비활성, 카메라 설정 섹션 숨김
        - connected + busy : STOP만 활성, 나머지 전부 비활성
        - connected + idle : SNAP/LIVE/ACQUIRE/DISCONNECT 활성, STOP 비활성
        """
        # 카메라 설정 섹션은 연결 시에만 표시
        if hasattr(self, "cam_connected_settings"):
            self.cam_connected_settings.setVisible(connected)
        if hasattr(self, "btn_bg_capture"):
            self.btn_bg_capture.setEnabled(connected and not busy)

        if not connected:
            self.btn_connect.setEnabled(True)
            self.btn_disconnect.setEnabled(False)
            self.btn_apply_exp.setEnabled(False)
            self.btn_apply_fps.setEnabled(False)
            self.btn_apply_temp.setEnabled(False)
            self.btn_apply_adc.setEnabled(False)
            self.btn_snap.setEnabled(False)
            self.btn_live_air.setEnabled(False)
            self.btn_acquire.setEnabled(False)
            self.btn_stop_main.setEnabled(False)
            self._update_dash_label(self.btn_live_air, "LIVE", "READY")
            self._update_dash_label(self.btn_acquire, "ACQUIRE", "READY")
        elif busy:
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(False)
            self.btn_apply_exp.setEnabled(False)
            self.btn_apply_fps.setEnabled(False)
            self.btn_apply_temp.setEnabled(False)
            self.btn_apply_adc.setEnabled(False)
            self.btn_snap.setEnabled(False)
            self.btn_live_air.setEnabled(False)
            self.btn_acquire.setEnabled(False)
            self.btn_stop_main.setEnabled(True)
        else:
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.btn_apply_exp.setEnabled(True)
            self.btn_apply_fps.setEnabled(True)
            self.btn_apply_temp.setEnabled(True)
            self.btn_apply_adc.setEnabled(True)
            self.btn_snap.setEnabled(True)
            self.btn_live_air.setEnabled(True)
            self.btn_acquire.setEnabled(True)
            self.btn_stop_main.setEnabled(False)

    def _update_dash_label(self, btn: QPushButton, title: str, sub: str):
        layout = btn.layout()
        if layout and layout.count() >= 2:
            sub_lbl = layout.itemAt(1).widget()
            if isinstance(sub_lbl, QLabel):
                sub_lbl.setText(sub)
                sub_lbl.setVisible(bool(sub))

    def _make_section(self, title: str, color: str, collapsed: bool = False):
        panel = QFrame()
        panel.setObjectName("subPanel")
        panel.setStyleSheet("QFrame#subPanel { background: transparent; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = QPushButton(title)
        header.setCheckable(True)
        header.setChecked(not collapsed)
        header.setFixedHeight(28)
        header.setStyleSheet(
            f"""
            QPushButton {{ background: {C_BG_MED}; color: {color}; font-weight: 900; font-size: 11px;
                           text-align: left; padding: 4px 8px; border: 1px solid #1e293b;
                           }}
            QPushButton:checked {{ border-bottom: none; }}
        """
        )
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
            f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: bold;"
            " border-right: 1px solid #1e293b; padding: 0 6px;"
            " background: rgba(30,41,59,0.2);"
        )
        return l

    def _style_btn(self, txt: str, color: str) -> QPushButton:
        btn = QPushButton(txt)
        btn.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; color: {color}; border: 1px solid {color};
                           border-radius: 4px; font-weight: bold; font-size: 11px; padding: 5px; }}
            QPushButton:hover {{ background: {color}22; }}
            QPushButton:disabled {{ color: #2a3547; border-color: #1e293b;
                                    background: transparent; }}
        """
        )
        return btn

    def _dash_btn(self, title: str, sub: str, color: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(90, 48)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"""
            QPushButton {{ background: {color}; color: white; border-radius: 6px;
                           border: none; font-weight: 900; padding: 0; }}
            QPushButton:hover {{ background: {color}cc; }}
            QPushButton:pressed {{ background: {color}aa; margin-top: 1px; }}
            QPushButton:disabled {{ background: {C_BG_MED}; border: 1px solid #1e293b; }}
        """
        )
        lay = QVBoxLayout(btn)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(0)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("font-size: 13px; font-weight: 900; background: transparent; border: none; color: white; letter-spacing: 0.5px;")
        lay.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s.setStyleSheet("font-size: 8px; font-weight: 800; background: transparent; border: none; color: rgba(255,255,255,0.9);")
            lay.addWidget(s)
        return btn

    def _small_toggle_btn(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setFixedSize(70, 20)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_BG_MED}; color: {C_TEXT_DIM}; border: 1px solid #1e293b;
                border-radius: 4px; font-size: 10px; font-weight: 800; padding: 2px;
            }}
            QPushButton:hover {{ border-color: #3b82f6; color: #3b82f6; background: #1e293b; }}
            QPushButton:checked {{ background: #1e293b; color: #3b82f6; border-color: #3b82f6; }}
        """)
        return btn

    def _apply_global_styles(self):
        self.setStyleSheet(
            f"""
            QWidget#deepAlignStack {{ background-color: {C_BG_DEEP}; }}
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{ border: none; background: {C_BG_DEEP}; width: 6px; }}
            QScrollBar::handle:vertical {{ background: #1e293b; border-radius: 3px; }}
            QWidget#deepAlignTab QLabel:disabled {{ color: {C_TEXT_DEAD}; }}
        """
        )
