from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QPushButton, QWidget


def build_nav_wheel(action) -> QWidget:
    SIZE = 44
    style = (
        f"QPushButton {{ border-radius: {SIZE // 2}px; "
        "border: none; background: #393939; color: #f4f4f4; font-size: 11px; }"
        " QPushButton:hover { background: #525252; }"
        " QPushButton:pressed { background: #0f62fe; }"
    )
    cancel_style = (
        "QPushButton { border-radius: 4px; border: none; background: #393939;"
        " color: #ff8389; font-size: 11px; }"
        " QPushButton:hover { background: #525252; }"
        " QPushButton:pressed { background: #da1e28; color: #f4f4f4; }"
    )

    def _btn(label: str, key: str) -> QPushButton:
        b = QPushButton(label)
        b.setFixedSize(SIZE, SIZE)
        b.setStyleSheet(style)
        b.clicked.connect(action("KEYPRESS", key))
        return b

    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(4)
    grid.addWidget(_btn("▲", "UP"), 0, 1)
    grid.addWidget(_btn("◀", "LEFT"), 1, 0)
    grid.addWidget(_btn("OK", "OK"), 1, 1)
    grid.addWidget(_btn("▶", "RIGHT"), 1, 2)
    grid.addWidget(_btn("▼", "DOWN"), 2, 1)

    cancel_btn = QPushButton("Cancel")
    cancel_btn.setFixedHeight(SIZE - 8)
    cancel_btn.setStyleSheet(cancel_style)
    cancel_btn.clicked.connect(action("KEYPRESS", "CANCEL"))
    grid.addWidget(cancel_btn, 3, 0, 1, 3)

    return widget
