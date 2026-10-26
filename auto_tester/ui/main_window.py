"""Main application window — single window, device list on the left, screen on the right."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from auto_tester.devices.base import BaseDevice
from auto_tester.devices.controller import DeviceController, Recording
from auto_tester.devices.device_configs import (
    list_saved_devices,
    load_device,
    save_device,
)
from auto_tester.ui.components.dialpad import build_dialpad
from auto_tester.ui.components.nav_wheel import build_nav_wheel
from auto_tester.ui.components.screen_label import ScreenLabel
from auto_tester.ui.new_device_dialog import NewDeviceDialog

ARTIFACTS_DIR = "artifacts"
RECORDINGS_DIR = "recordings"
_CONNECTED_DOT = "● "
_DISCONNECTED_DOT = "○ "
_DOT_CONNECTED_COLOR = "#42be65"  # Carbon Green 40
_DOT_DISCONNECTED_COLOR = "#6f6f6f"  # Carbon Gray 50


def _find_matching_recordings(device_id: str) -> list[tuple[str, Path]]:
    """Return (folder_name, sequence_path) for recordings where device_id is the DUT."""
    results = []
    recordings_dir = Path(RECORDINGS_DIR)
    if not recordings_dir.exists():
        return results
    for folder in sorted(recordings_dir.iterdir()):
        if not folder.is_dir():
            continue
        if not folder.name.endswith(f"_{device_id}"):
            continue
        seq = folder / "sequence.txt"
        if seq.exists():
            results.append((folder.name, seq))
    return results


# ── Device row widget ─────────────────────────────────────────────────────────


class _DeviceRow(QWidget):
    """A row in the device list: coloured dot + device ID label."""

    def __init__(self, device_id: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._dot = QLabel(_DISCONNECTED_DOT)
        self._dot.setFixedWidth(16)
        self._label = QLabel(device_id)

        layout.addWidget(self._dot)
        layout.addWidget(self._label, stretch=1)

        self.set_connected(False)

    def set_connected(self, connected: bool) -> None:
        color = _DOT_CONNECTED_COLOR if connected else _DOT_DISCONNECTED_COLOR
        dot = _CONNECTED_DOT if connected else _DISCONNECTED_DOT
        self._dot.setText(dot)
        self._dot.setStyleSheet(f"color: {color}; background: transparent;")


# ── Main window ───────────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    """Single application window with a device list and screen view."""

    def __init__(self, recording: Recording) -> None:
        super().__init__()
        self._recording = recording

        # device_id → (DeviceController, _DeviceRow, QListWidgetItem)
        self._devices: dict[
            str, tuple[DeviceController, _DeviceRow, QListWidgetItem]
        ] = {}
        self._selected_did: str | None = None

        self.setWindowTitle("Auto-Tester")
        self.resize(980, 680)

        # ── Central splitter ──────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #393939; }")

        # Left panel
        left_panel = QWidget()
        left_panel.setFixedWidth(200)
        left_panel.setStyleSheet("background: #1c1c1c;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        devices_label = QLabel("  Devices")
        devices_label.setStyleSheet(
            "color: #8d8d8d; font-size: 11px; padding: 8px 6px 4px 6px; background: #1c1c1c;"
        )
        left_layout.addWidget(devices_label)

        self._device_list = QListWidget()
        self._device_list.setStyleSheet(
            "QListWidget {"
            "  background: #1c1c1c; border: none; outline: none;"
            "}"
            "QListWidget::item {"
            "  padding: 0px; border: none;"
            "}"
            "QListWidget::item:selected {"
            "  background: #262626;"
            "}"
            "QListWidget::item:hover:!selected {"
            "  background: #212121;"
            "}"
        )
        self._device_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._device_list.currentItemChanged.connect(self._on_list_selection_changed)
        left_layout.addWidget(self._device_list, stretch=1)

        add_btn = QPushButton("+ Add Device…")
        add_btn.setStyleSheet(
            "QPushButton { background: #262626; color: #c6c6c6; border: none;"
            " padding: 8px; font-size: 12px; }"
            " QPushButton:hover { background: #393939; }"
        )
        add_btn.clicked.connect(self._add_device)
        left_layout.addWidget(add_btn)

        splitter.addWidget(left_panel)

        # Right panel — screen view + controls strip
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._screen = ScreenLabel()
        self._screen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._screen.setText("Select a device to view its screen.")
        self._screen.setStyleSheet("color: #6f6f6f; font-size: 13px;")
        right_layout.addWidget(self._screen, stretch=1)

        # Controls strip: nav wheel + dialpad
        controls = QWidget()
        controls.setStyleSheet("background: #161616; border-top: 1px solid #393939;")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(24, 12, 24, 12)
        controls_layout.setSpacing(0)
        controls_layout.addStretch()
        controls_layout.addWidget(build_nav_wheel(self._action))
        controls_layout.addStretch()
        controls_layout.addWidget(build_dialpad(self._action))
        controls_layout.addStretch()
        right_layout.addWidget(controls)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

        # ── Timers ────────────────────────────────────────────────────────
        # UI refresh: re-read the PNG from disk for the selected device
        self._last_mtime: float | None = None
        self._ui_refresh_timer = QTimer()
        self._ui_refresh_timer.timeout.connect(self._refresh_screen_image)
        self._ui_refresh_timer.start(500)

        # Network capture: ask the selected device to fetch a new screenshot
        self._capture_timer = QTimer()
        self._capture_timer.timeout.connect(self._poll_selected_device)
        self._capture_timer.start(4000)

        # ── Populate device list ──────────────────────────────────────────
        self._load_device_list()
        self._rebuild_menubar()

    # ── Device list ───────────────────────────────────────────────────────

    def _load_device_list(self) -> None:
        """Add all saved devices to the list (disconnected state)."""
        for path in list_saved_devices():
            self._add_row(path.stem)

    def _add_row(self, device_id: str) -> QListWidgetItem:
        row_widget = _DeviceRow(device_id)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, device_id)
        item.setSizeHint(row_widget.sizeHint())
        self._device_list.addItem(item)
        self._device_list.setItemWidget(item, row_widget)
        # Store a placeholder — controller filled in when connected
        self._devices[device_id] = (None, row_widget, item)  # type: ignore[assignment]
        return item

    def _on_list_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        device_id: str = current.data(Qt.ItemDataRole.UserRole)
        controller, row_widget, _ = self._devices[device_id]

        if controller is None:
            # First click → connect
            self._connect_device(device_id)
        else:
            self._select_device(device_id)

    def _connect_device(self, device_id: str) -> None:
        _, row_widget, item = self._devices[device_id]
        try:
            device = load_device(
                Path("auto_tester/devices/saved_configs") / f"{device_id}.json"
            )
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return

        controller = DeviceController(device)
        controller.attach_recording(self._recording)
        connected = controller.connect()
        if not connected:
            print(
                f"[MainWindow] Warning: could not connect to {device_id} — offline mode"
            )

        row_widget.set_connected(connected)
        self._devices[device_id] = (controller, row_widget, item)
        self._select_device(device_id)

    def _select_device(self, device_id: str) -> None:
        self._selected_did = device_id
        controller, _, _ = self._devices[device_id]
        caps = controller.device.capabilities if controller else None

        # Update touch handler
        if caps and caps.touch_nav_enabled:
            self._screen.set_on_touch(
                lambda x, y, c=controller: c.execute_action("TOUCH", x, y)
            )
            self._screen.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._screen.set_on_touch(None)
            self._screen.setCursor(Qt.CursorShape.ArrowCursor)

        self._last_mtime = None  # force image reload
        self._refresh_screen_image()
        self._rebuild_menubar()

    # ── Add device ────────────────────────────────────────────────────────

    def _add_device(self) -> None:
        dialog = NewDeviceDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = dialog.result_config()
        save_device(**cfg)

        device_id = cfg["device_id"]
        # Add row (may already exist if user re-created a saved device)
        if device_id not in self._devices:
            item = self._add_row(device_id)
        else:
            _, _, item = self._devices[device_id]

        # Select it — triggers connection on the next click cycle
        self._device_list.setCurrentItem(item)

    # ── Screen image ──────────────────────────────────────────────────────

    def _image_path(self, device_id: str) -> Path | None:
        p = Path(ARTIFACTS_DIR, "live", f"{device_id}_latest_screen.png").resolve()
        return p if p.exists() else None

    def _refresh_screen_image(self) -> None:
        if self._selected_did is None:
            return
        path = self._image_path(self._selected_did)
        if path is None:
            self._screen.setText(f"Waiting for screen…\n({self._selected_did})")
            return
        mtime = path.stat().st_mtime
        if mtime == self._last_mtime:
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._screen.setText(f"Cannot load image:\n{path}")
            return
        self._last_mtime = mtime
        self._screen.setPixmap(pixmap)

    def _poll_selected_device(self) -> None:
        if self._selected_did is None:
            return
        controller, _, _ = self._devices[self._selected_did]
        if controller is not None:
            controller.refresh_screen()

    # ── Menubar ───────────────────────────────────────────────────────────

    def _action(self, action: str, *args):
        """Return a callable that executes *action* on the currently selected device."""

        def _call():
            if self._selected_did is None:
                return
            controller, _, _ = self._devices[self._selected_did]
            if controller:
                controller.execute_action(action, *args)

        return _call

    def _rebuild_menubar(self) -> None:
        self.menuBar().clear()

        caps = None
        if self._selected_did:
            controller, _, _ = self._devices[self._selected_did]
            if controller:
                caps = controller.device.capabilities

        # ── Line Keys ─────────────────────────────────────────────────────
        if caps:
            nr_total = caps.nr_left_linekeys + caps.nr_right_linekeys
            if nr_total > 0:
                lk_menu = self.menuBar().addMenu("Line Keys")
                for i in range(1, nr_total + 1):
                    act = QAction(f"LINE{i}", self)
                    act.triggered.connect(self._action("KEYPRESS", f"LINE{i}"))
                    lk_menu.addAction(act)

        # ── Softkeys ──────────────────────────────────────────────────────
        if caps and caps.nr_softkeys > 0:
            sk_menu = self.menuBar().addMenu("Softkeys")
            for i in range(1, caps.nr_softkeys + 1):
                act = QAction(f"SOFTKEY{i}", self)
                act.triggered.connect(self._action("KEYPRESS", f"SOFTKEY{i}"))
                sk_menu.addAction(act)

        # ── Navigation ────────────────────────────────────────────────────
        nav_menu = self.menuBar().addMenu("Navigation")
        for label, key in [
            ("▲  Up", "UP"),
            ("▼  Down", "DOWN"),
            ("◀  Left", "LEFT"),
            ("▶  Right", "RIGHT"),
            ("OK", "OK"),
        ]:
            act = QAction(label, self)
            act.triggered.connect(self._action("KEYPRESS", key))
            nav_menu.addAction(act)

        # ── Dialpad ───────────────────────────────────────────────────────
        dial_menu = self.menuBar().addMenu("Dialpad")
        for digit in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]:
            act = QAction(digit, self)
            act.triggered.connect(self._action("KEYPRESS", digit))
            dial_menu.addAction(act)

        # ── Actions ───────────────────────────────────────────────────────
        actions_menu = self.menuBar().addMenu("Actions")
        actions_menu.addAction(
            QAction("Cancel", self, triggered=self._action("KEYPRESS", "CANCEL"))
        )
        actions_menu.addSeparator()
        speaker_act = QAction("Speaker", self, checkable=True)
        speaker_act.triggered.connect(self._action("KEYPRESS", "SPEAKER"))
        actions_menu.addAction(speaker_act)
        mute_act = QAction("Mute", self, checkable=True)
        mute_act.triggered.connect(self._action("KEYPRESS", "MUTE"))
        actions_menu.addAction(mute_act)

        # ── Record ────────────────────────────────────────────────────────
        record_menu = self.menuBar().addMenu("Record")
        record_act = QAction("Start Recording", self, checkable=True)
        capture_act = QAction("Capture Screen", self)
        capture_act.setEnabled(False)
        capture_act.triggered.connect(self._capture_screen)

        def _toggle(checked: bool) -> None:
            if checked:
                record_act.setText("Stop Recording")
                capture_act.setEnabled(True)
                all_devices = [
                    ctrl.device
                    for ctrl, _, _ in self._devices.values()
                    if ctrl is not None
                ]
                if all_devices:
                    dut_id = all_devices[0].id
                    self._recording.start(dut_id, all_devices)
            else:
                record_act.setText("Start Recording")
                capture_act.setEnabled(False)
                self._recording.stop()

        record_act.toggled.connect(_toggle)
        record_menu.addAction(record_act)
        record_menu.addAction(capture_act)

        # ── Run Tests ─────────────────────────────────────────────────────
        tests_menu = self.menuBar().addMenu("Run Tests")

        # Recordings for the currently selected device
        if self._selected_did:
            controller, _, _ = self._devices[self._selected_did]
            if controller:
                recordings = _find_matching_recordings(self._selected_did)
                if recordings:
                    for rec_name, seq_path in recordings:
                        act = QAction(rec_name, self)
                        act.triggered.connect(
                            lambda _checked=False, p=seq_path: self._run_recording(p)
                        )
                        tests_menu.addAction(act)
                    tests_menu.addSeparator()

        scenario_act = QAction("Run Scenario…", self)
        scenario_act.triggered.connect(self._open_scenario)
        tests_menu.addAction(scenario_act)

        # ── Settings ──────────────────────────────────────────────────────
        settings_menu = self.menuBar().addMenu("Settings")
        refresh_menu = settings_menu.addMenu("Refresh Rate")
        rate_group: list[QAction] = []
        default_rate = 4.0
        for rate in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
            act = QAction(f"{rate:g}s", self, checkable=True)
            act.setChecked(rate == default_rate)
            act.triggered.connect(
                lambda _checked=False, r=rate: self._capture_timer.setInterval(
                    int(r * 1000)
                )
            )
            rate_group.append(act)
            refresh_menu.addAction(act)

        def _uncheck_others(triggered_act: QAction) -> None:
            for a in rate_group:
                a.setChecked(a is triggered_act)

        for act in rate_group:
            act.triggered.connect(lambda _checked=False, a=act: _uncheck_others(a))

    def _capture_screen(self) -> None:
        if self._selected_did is None:
            return
        controller, _, _ = self._devices[self._selected_did]
        if controller:
            controller.capture_screen()

    def _run_recording(self, seq_path: Path) -> None:
        if self._selected_did is None:
            return
        controller, _, _ = self._devices[self._selected_did]
        if controller is None:
            return
        all_connected = {
            did: ctrl.device
            for did, (ctrl, _, _) in self._devices.items()
            if ctrl is not None
        }
        controller.run_recording(seq_path, all_connected)

    def _open_scenario(self) -> None:
        from auto_tester.ui.scenario_window import ScenarioWindow

        connected = {
            did: ctrl.device
            for did, (ctrl, _, _) in self._devices.items()
            if ctrl is not None
        }
        if not connected:
            QMessageBox.information(
                self, "No devices", "Connect at least one device first."
            )
            return
        self._scenario_window = ScenarioWindow(connected, parent=self)
        self._scenario_window.show()

    # ── Cleanup ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._ui_refresh_timer.stop()
        self._capture_timer.stop()
        for controller, _, _ in self._devices.values():
            if controller is not None:
                controller.disconnect()
        event.accept()
