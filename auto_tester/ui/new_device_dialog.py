"""Dialog for creating a new device configuration."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QWidget,
)


class NewDeviceDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Device")
        form = QFormLayout(self)

        self._device_id = QLineEdit()
        self._ip = QLineEdit()
        self._username = QLineEdit()
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._vendor = QComboBox()
        self._vendor.addItems(["Polycom", "Yealink"])
        self._auth_type = QComboBox()
        self._auth_type.addItems(["Digest", "Basic"])
        self._touchscreen = QComboBox()
        self._touchscreen.addItems(["No", "Yes"])
        self._softkeys = QLineEdit()
        self._softkeys.setInputMask("9;_")  # up to 4
        self._left_linekeys = QLineEdit()
        self._left_linekeys.setInputMask("9;_")  # up to 4 digits, no validation
        self._right_linekeys = QLineEdit()
        self._right_linekeys.setInputMask("9;_")  # up to 4 digits, no validation

        form.addRow("Device ID:", self._device_id)
        form.addRow("IP Address:", self._ip)
        form.addRow("Username:", self._username)
        form.addRow("Password:", self._password)
        form.addRow("Vendor:", self._vendor)
        form.addRow("Auth Type:", self._auth_type)
        form.addRow("Touchscreen:", self._touchscreen)
        form.addRow("Softkeys:", self._softkeys)
        form.addRow("Left Linekeys:", self._left_linekeys)
        form.addRow("Right Linekeys:", self._right_linekeys)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate(self) -> None:
        if not all(
            [
                self._device_id.text().strip(),
                self._ip.text().strip(),
                self._username.text().strip(),
            ]
        ):
            QMessageBox.warning(
                self, "Missing fields", "Device ID, IP and Username are required."
            )
            return
        self.accept()

    def result_config(self) -> dict:
        return {
            "device_id": self._device_id.text().strip(),
            "ip": self._ip.text().strip(),
            "username": self._username.text().strip(),
            "password": self._password.text(),
            "vendor": self._vendor.currentText(),
            "auth_type": self._auth_type.currentText().lower(),
            "left_linekeys": (
                int(self._left_linekeys.text().strip())
                if self._left_linekeys.text().strip()
                else 0
            ),
            "right_linekeys": (
                int(self._right_linekeys.text().strip())
                if self._right_linekeys.text().strip()
                else 0
            ),
            "touchscreen": self._touchscreen.currentText() == "Yes",
            "softkeys": (
                int(self._softkeys.text().strip())
                if self._softkeys.text().strip()
                else 0
            ),
        }
