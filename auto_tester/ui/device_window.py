"""Device window — phone-like UI with buttons, screenshot, and recording controls."""

from __future__ import annotations

from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtCore import Qt, QTimer

from auto_tester.devices.base import BaseDevice
from auto_tester.devices.controller import DeviceController, Recording
from auto_tester.ui.components.screen_label import ScreenLabel
from auto_tester.ui.components.nav_wheel import build_nav_wheel
from auto_tester.ui.components.dialpad import build_dialpad
from auto_tester.ui.scenario_window import ScenarioWindow

ARTIFACTS_DIR = "artifacts"
RECORDINGS_DIR = "recordings"


def _find_matching_recordings(device_id: str) -> list[tuple[str, Path]]:
    """Return (folder_name, sequence_path) for recordings where device_id is the DUT.

    Recording folders are named <scenario>_<dut_id>, so we match by folder name suffix.
    """
    results = []
    recordings_dir = Path(RECORDINGS_DIR)
    if not recordings_dir.exists():
        return results
    for folder in sorted(recordings_dir.iterdir()):
        if not folder.is_dir():
            continue
        # Folder name ends with the DUT device_id: e.g. "basic_call_polye350"
        if not folder.name.endswith(f"_{device_id}"):
            continue
        seq = folder / "sequence.txt"
        if seq.exists():
            results.append((folder.name, seq))
    return results


class DeviceWindow(QMainWindow):
    """Full phone-control window for a single device."""

    def __init__(
        self, device: BaseDevice, recording: Recording, open_windows: list
    ) -> None:
        super().__init__()
        self._recording = recording
        self._open_windows = open_windows
        self._controller = DeviceController(device)
        self._controller.attach_recording(recording)

        connected = self._controller.connect()
        if not connected:
            print(
                f"Warning: failed to connect to {device.id} — running in view-only mode"
            )

        self.setWindowTitle(
            f"Auto-Tester — {device.id}" + ("" if connected else " (offline)")
        )
        self.closeEvent = lambda event: (self._controller.disconnect(), event.accept())

        root = QVBoxLayout()
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        caps = device.capabilities
        nr_left = caps.nr_left_linekeys
        nr_right = caps.nr_right_linekeys
        nr_softkeys = caps.nr_softkeys
        touchscreen = caps.touch_nav_enabled
        self._nr_left = nr_left
        self._nr_right = nr_right
        self._nr_softkeys = nr_softkeys

        # Screenshot area
        on_touch = (
            (lambda x, y: self._controller.execute_action("TOUCH", x, y))
            if touchscreen
            else None
        )
        self._image_label = ScreenLabel(on_touch)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if touchscreen:
            self._image_label.setCursor(Qt.CursorShape.CrossCursor)

        # Center column: screen → nav → dialpad
        center_col = QVBoxLayout()
        center_col.setContentsMargins(0, 0, 0, 0)
        center_col.setSpacing(2)
        center_col.addWidget(self._image_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        center_col.addWidget(
            build_nav_wheel(self._action), alignment=Qt.AlignmentFlag.AlignHCenter
        )
        center_col.addWidget(
            build_dialpad(self._action), alignment=Qt.AlignmentFlag.AlignHCenter
        )
        center_col.addStretch()
        center_widget = QWidget()
        center_widget.setLayout(center_col)
        root.addWidget(center_widget)

        # Live image refresh (polls file mtime)
        image_file = Path(
            ARTIFACTS_DIR, "live", f"{device.id}_latest_screen.png"
        ).resolve()
        last_mtime: list[float | None] = [None]

        def refresh_image() -> None:
            if not image_file.exists():
                self._image_label.setText(f"Waiting for screen...\n{image_file}")
                return
            mtime = image_file.stat().st_mtime
            if last_mtime[0] == mtime:
                return
            pixmap = QPixmap(str(image_file))
            if pixmap.isNull():
                self._image_label.setText(f"Unable to load image:\n{image_file}")
                return
            last_mtime[0] = mtime
            self._image_label.setPixmap(pixmap)
            self.adjustSize()

        refresh_image()

        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(refresh_image)
        self._refresh_timer.start(500)

        self._capture_timer = QTimer()
        self._capture_timer.timeout.connect(self._controller.refresh_screen)
        self._capture_timer.start(4000)

        self._build_menu_bar()

    def _build_menu_bar(self) -> None:
        # ── Line Keys ──────────────────────────────────────────────────────
        nr_total = self._nr_left + self._nr_right
        if nr_total > 0:
            linekeys_menu = self.menuBar().addMenu("Line Keys")
            for i in range(1, nr_total + 1):
                act = QAction(f"LINE{i}", self)
                act.triggered.connect(self._action("KEYPRESS", f"LINE{i}"))
                linekeys_menu.addAction(act)

        # ── Softkeys ───────────────────────────────────────────────────────
        if self._nr_softkeys > 0:
            softkeys_menu = self.menuBar().addMenu("Softkeys")
            for i in range(1, self._nr_softkeys + 1):
                act = QAction(f"SOFTKEY{i}", self)
                act.triggered.connect(self._action("KEYPRESS", f"SOFTKEY{i}"))
                softkeys_menu.addAction(act)

        # ── Phone ──────────────────────────────────────────────────────────
        phone_menu = self.menuBar().addMenu("Actions")
        phone_menu.addAction("Cancel", lambda: self._action("KEYPRESS", "CANCEL")())
        phone_menu.addAction("Offhook", lambda: self._action("OFFHOOK")())
        phone_menu.addAction("Onhook", lambda: self._action("ONHOOK")())
        phone_menu.addSeparator()
        speaker_act = QAction("Speaker", self, checkable=True)
        speaker_act.triggered.connect(self._action("KEYPRESS", "SPEAKER"))
        phone_menu.addAction(speaker_act)
        mute_act = QAction("Mute", self, checkable=True)
        mute_act.triggered.connect(self._action("KEYPRESS", "MUTE"))
        phone_menu.addAction(mute_act)

        # ── Record ─────────────────────────────────────────────────────────
        record_menu = self.menuBar().addMenu("Record")
        record_act = QAction("Start Recording", self, checkable=True)
        capture_act = QAction("Capture Screen", self)
        capture_act.setEnabled(False)
        capture_act.triggered.connect(self._controller.capture_screen)

        def _toggle(checked: bool) -> None:
            if checked:
                record_act.setText("Stop Recording")
                capture_act.setEnabled(True)
                all_devices = [
                    w._controller.device
                    for w in self._open_windows
                    if isinstance(w, DeviceWindow)
                ]
                self._recording.start(self._controller.device.id, all_devices)
            else:
                record_act.setText("Start Recording")
                capture_act.setEnabled(False)
                self._recording.stop()

        record_act.toggled.connect(_toggle)
        record_menu.addAction(record_act)
        record_menu.addAction(capture_act)

        # ── Run Tests ──────────────────────────────────────────────────────
        tests_menu = self.menuBar().addMenu("Run Tests")
        recordings = _find_matching_recordings(self._controller.device.id)
        if recordings:
            for name, seq_path in recordings:
                act = QAction(name, self)
                act.triggered.connect(
                    lambda checked=False, p=seq_path: self._controller.run_recording(
                        p,
                        {
                            w._controller.device.id: w._controller.device
                            for w in self._open_windows
                            if isinstance(w, DeviceWindow)
                        },
                    )
                )
                tests_menu.addAction(act)
        else:
            no_act = QAction("(no recordings)", self)
            no_act.setEnabled(False)
            tests_menu.addAction(no_act)

        # ── Run Scenario ───────────────────────────────────────────────────
        scenario_act = QAction("Run Scenario…", self)
        scenario_act.triggered.connect(self._open_scenario)
        tests_menu.addSeparator()
        tests_menu.addAction(scenario_act)

        # ── View ───────────────────────────────────────────────────────────
        settings_menu = self.menuBar().addMenu("Settings")
        refresh_menu = settings_menu.addMenu("Refresh Rate")
        rate_group: list[QAction] = []
        default_rate = 4.0
        for rate in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
            act = QAction(f"{rate:g}s", self, checkable=True)
            act.setChecked(rate == default_rate)
            act.triggered.connect(
                lambda checked=False, r=rate: self._capture_timer.setInterval(
                    int(r * 1000)
                )
            )
            rate_group.append(act)
            refresh_menu.addAction(act)

        def _uncheck_others(triggered_act: QAction) -> None:
            for a in rate_group:
                a.setChecked(a is triggered_act)

        for act in rate_group:
            act.triggered.connect(lambda checked=False, a=act: _uncheck_others(a))

    def _open_scenario(self) -> None:
        all_devices = {
            w._controller.device.id: w._controller.device
            for w in self._open_windows
            if isinstance(w, DeviceWindow)
        }
        self._scenario_window = ScenarioWindow(all_devices, parent=self)
        self._scenario_window.show()

    def _action(self, action: str, *args):
        return lambda: self._controller.execute_action(action, *args)
