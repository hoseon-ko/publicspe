DARK_THEME_QSS = """
QMainWindow {
    background-color: #1a1a2e;
}
QWidget {
    background-color: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Segoe UI', sans-serif;
    font-size: 16px;
}

/* 도킹 인디케이터 - QSS 간섭 방지 */
QMainWindow::separator {
    background-color: #0f3460;
    width: 3px;
    height: 3px;
}

QRubberBand {
    background-color: rgba(233, 69, 96, 80);
    border: 2px solid #e94560;
}

QMenuBar {
    background-color: #16213e;
    color: #e0e0e0;
    border-bottom: 1px solid #0f3460;
}
QMenuBar::item:selected {
    background-color: #0f3460;
}
QMenu {
    background-color: #16213e;
    color: #e0e0e0;
    border: 1px solid #0f3460;
}
QMenu::item:selected {
    background-color: #0f3460;
}

QToolBar {
    background-color: #16213e;
    border-bottom: 1px solid #0f3460;
    spacing: 4px;
    padding: 4px;
}
QToolBar QToolButton {
    background-color: transparent;
    color: #e0e0e0;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 8px;
}
QToolBar QToolButton:hover {
    background-color: #0f3460;
    border: 1px solid #e94560;
}
QToolBar QToolButton:checked {
    background-color: #e94560;
    color: #ffffff;
}

QDockWidget {
    color: #e0e0e0;
}
QDockWidget::title {
    background-color: #16213e;
    padding: 6px;
    border-bottom: 2px solid #e94560;
    font-weight: bold;
    letter-spacing: 1px;
    text-transform: uppercase;
    font-size: 14px;
}
QDockWidget::close-button, QDockWidget::float-button {
    background-color: transparent;
    border: none;
    padding: 2px;
}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {
    background-color: #e94560;
    border-radius: 2px;
}

QListWidget {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 4px;
    outline: none;
}
QListWidget::item {
    padding: 6px 8px;
    border-bottom: 1px solid #1a1a2e;
}
QListWidget::item:selected {
    background-color: #0f3460;
    color: #e94560;
}
QListWidget::item:hover {
    background-color: #0f3460;
}

QSlider::groove:horizontal {
    height: 4px;
    background-color: #0f3460;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background-color: #e94560;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background-color: #e94560;
    border-radius: 2px;
}

QLabel {
    color: #e0e0e0;
}
QLabel#status_label {
    color: #a0a0b0;
    font-size: 14px;
}
QLabel#frame_label {
    color: #e94560;
    font-weight: bold;
}

QStatusBar {
    background-color: #16213e;
    color: #a0a0b0;
    border-top: 1px solid #0f3460;
    font-size: 14px;
}

QScrollBar:vertical {
    background-color: #16213e;
    width: 8px;
}
QScrollBar::handle:vertical {
    background-color: #0f3460;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #e94560;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QSplitter::handle {
    background-color: #0f3460;
    width: 2px;
    height: 2px;
}

QPushButton {
    background-color: #0f3460;
    color: #e0e0e0;
    border: 1px solid #e94560;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover {
    background-color: #e94560;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #c73652;
}

QComboBox {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 4px;
    padding: 4px 8px;
    color: #e0e0e0;
}
QComboBox::drop-down {
    border: none;
}
QComboBox QAbstractItemView {
    background-color: #16213e;
    border: 1px solid #0f3460;
    selection-background-color: #0f3460;
}

QProgressBar {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 4px;
    text-align: center;
    color: #e0e0e0;
    height: 16px;
}
QProgressBar::chunk {
    background-color: #e94560;
    border-radius: 3px;
}
"""