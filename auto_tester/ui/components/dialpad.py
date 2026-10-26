from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QPushButton,
    QWidget,
)

DIALPAD_DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]


def build_dialpad(action) -> QWidget:
    SIZE = 48
    style = (
        f"QPushButton {{ border-radius: {SIZE // 2}px; "
        "border: none; background: #393939; color: #f4f4f4; font-size: 16px; }"
        " QPushButton:hover { background: #525252; }"
        " QPushButton:pressed { background: #0f62fe; }"
    )
    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(6)
    for idx, digit in enumerate(DIALPAD_DIGITS):
        btn = QPushButton(digit)
        btn.setFixedSize(SIZE, SIZE)
        btn.setStyleSheet(style)
        btn.clicked.connect(action("KEYPRESS", digit))
        grid.addWidget(btn, idx // 3, idx % 3)
    return widget
