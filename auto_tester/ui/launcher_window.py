"""Launcher window — choose an existing saved device or create a new one."""

from __future__ import annotations

from pathlib import Path
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from auto_tester.devices.base import BaseDevice
from auto_tester.devices.controller import Recording
from auto_tester.devices.device_configs import (
    list_saved_devices,
    load_device,
    save_device,
)
from auto_tester.ui.new_device_dialog import NewDeviceDialog
from auto_tester.devices.polycom import PolycomDevice
from auto_tester.devices.yealink import YealinkDevice
from auto_tester.net.http import HttpClient


class LauncherWindow(QWidget):
    """Small startup window for selecting or creating a device."""

    def __init__(self, recording: Recording, open_windows: list) -> None:
        super().__init__()
        self._recording = recording
        self._open_windows = open_windows
        self.setWindowTitle("Auto-Tester — Select Device")

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(16, 16, 16, 16)

        root.addWidget(QLabel("<b>Select a saved device</b>"))

        self._saved_combo = QComboBox()
        self._refresh_saved_list()
        root.addWidget(self._saved_combo)

        open_btn = QPushButton("Open Device")
        open_btn.setProperty("primary", True)
        open_btn.clicked.connect(self._open_saved)
        root.addWidget(open_btn)

        root.addWidget(_divider())

        or_label = QLabel("Or")
        or_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(or_label)

        root.addWidget(_divider())

        new_btn = QPushButton("Create New Device...")
        new_btn.clicked.connect(self._create_new)
        root.addWidget(new_btn)

    def _refresh_saved_list(self) -> None:
        self._saved_combo.clear()
        for p in list_saved_devices():
            self._saved_combo.addItem(p.stem, userData=p)
        if self._saved_combo.count() == 0:
            self._saved_combo.addItem("(no saved devices)")
            self._saved_combo.setEnabled(False)
        else:
            self._saved_combo.setEnabled(True)

    def _open_saved(self) -> None:
        path: Path | None = self._saved_combo.currentData()
        if path is None:
            return
        try:
            device = load_device(path)
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return
        self._launch(device)

    def _create_new(self) -> None:
        dialog = NewDeviceDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = dialog.result_config()
        save_device(**cfg)
        auth_type = cfg["auth_type"]
        from requests.auth import HTTPBasicAuth, HTTPDigestAuth

        auth = (
            HTTPDigestAuth(cfg["username"], cfg["password"])
            if auth_type == "digest"
            else HTTPBasicAuth(cfg["username"], cfg["password"])
        )
        http = HttpClient(base_url=f"https://{cfg['ip']}", auth=auth)
        cls = PolycomDevice if cfg["vendor"] == "Polycom" else YealinkDevice
        device = cls(
            device_id=cfg["device_id"],
            ip=cfg["ip"],
            username=cfg["username"],
            password=cfg["password"],
            http_client=http,
            touchEnabled=cfg["touchscreen"],
            nr_left_linekeys=cfg["left_linekeys"],
            nr_right_linekeys=cfg["right_linekeys"],
            nr_softkeys=cfg["softkeys"],
        )
        self._refresh_saved_list()
        self._launch(device)

    def _launch(self, device: BaseDevice) -> None:
        # Import here to avoid circular import at module level
        from auto_tester.ui.device_window import DeviceWindow

        w = DeviceWindow(device, self._recording, self._open_windows)
        w.show()
        self._open_windows.append(w)


def _divider() -> QWidget:
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet("background: #393939;")
    return line
