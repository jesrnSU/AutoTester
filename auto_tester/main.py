from __future__ import annotations

import sys
from PySide6.QtWidgets import QApplication
from auto_tester.devices.controller import Recording
from auto_tester.ui.main_window import MainWindow
from auto_tester.ui import carbon_theme


def main() -> None:
    app = QApplication(sys.argv)
    carbon_theme.apply(app)
    recording = Recording()
    window = MainWindow(recording)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
