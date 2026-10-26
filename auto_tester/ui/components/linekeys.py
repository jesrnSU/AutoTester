from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# Shared dimensions
_KEY_W = 38
_KEY_H = 28

_LINEKEY_STYLE = (
    f"QPushButton {{ "
    f"border-radius: 5px; border: none; "
    f"background: #393939; color: #f4f4f4; font-size: 11px; "
    f"padding: 0px; }}"
    " QPushButton:hover { background: #525252; }"
    " QPushButton:pressed { background: #0f62fe; }"
)

_SOFTKEY_STYLE = (
    f"QPushButton {{ "
    f"border-radius: 5px; border: none; "
    f"background: #393939; color: #f4f4f4; font-size: 11px; "
    f"padding: 0px; }}"
    " QPushButton:hover { background: #525252; }"
    " QPushButton:pressed { background: #0f62fe; }"
)


def build_linekey_column(indices: list[int], action) -> QWidget:
    col = QWidget()
    layout = QVBoxLayout(col)
    layout.setContentsMargins(2, 0, 2, 0)
    layout.setSpacing(6)
    for i in indices:
        btn = QPushButton(f"L{i}")
        btn.setFixedSize(_KEY_W, _KEY_H)
        btn.setStyleSheet(_LINEKEY_STYLE)
        btn.clicked.connect(action("KEYPRESS", f"LINE{i}"))
        layout.addWidget(btn)
    return col


def build_softkey_row(action, count: int) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for i in range(1, count + 1):
        btn = QPushButton(f"SK{i}")
        btn.setFixedHeight(_KEY_H)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(_SOFTKEY_STYLE)
        btn.clicked.connect(action("KEYPRESS", f"SOFTKEY{i}"))
        row.addWidget(btn)
    return row
