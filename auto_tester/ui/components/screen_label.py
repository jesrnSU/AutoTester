from __future__ import annotations

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt


class ScreenLabel(QLabel):
    """QLabel that forwards mouse clicks as TOUCH actions when touch is enabled."""

    def __init__(self, on_touch=None) -> None:
        super().__init__()
        self._on_touch = on_touch  # callable(x, y) or None

    def set_on_touch(self, fn) -> None:
        """Replace the touch callback (called when the selected device changes)."""
        self._on_touch = fn

    def mousePressEvent(self, event) -> None:
        if self._on_touch and event.button() == Qt.MouseButton.LeftButton:
            pm = self.pixmap()
            if pm and not pm.isNull():
                x_off = (self.width() - pm.width()) // 2
                y_off = (self.height() - pm.height()) // 2
                x = event.position().x() - x_off
                y = event.position().y() - y_off
                if 0 <= x <= pm.width() and 0 <= y <= pm.height():
                    self._on_touch(int(x), int(y))
        super().mousePressEvent(event)
