"""Scenario window — pick a scenario file, watch live log output, run with Claude."""

from __future__ import annotations

import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QTextCursor

from auto_tester.devices.base import BaseDevice


class _LogStream:
    """File-like object that emits each completed line as a Qt signal."""

    def __init__(self, signal: Signal) -> None:
        self._signal = signal
        self._buf = ""

    def write(self, text: str) -> None:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._signal.emit(line)

    def flush(self) -> None:
        pass


class _ScenarioWorker(QThread):
    log = Signal(str)
    finished = Signal(str)  # recording path on success
    failed = Signal(str)  # reason on failure

    def __init__(
        self,
        scenario_path: Path,
        devices: dict[str, BaseDevice],
        use_ocr: bool = True,
        use_screenshots: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._scenario_path = scenario_path
        self._devices = devices
        self._use_ocr = use_ocr
        self._use_screenshots = use_screenshots

    def run(self) -> None:
        from auto_tester.tests.scenario import run_scenario

        stream = _LogStream(self.log)
        old_stdout = sys.stdout
        sys.stdout = stream  # type: ignore[assignment]
        try:
            success = run_scenario(
                self._scenario_path,
                self._devices,
                use_ocr=self._use_ocr,
                use_screenshots=self._use_screenshots,
            )
            if success:
                self.finished.emit("Scenario completed successfully.")
            else:
                self.failed.emit("Scenario did not complete — see log above.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            sys.stdout = old_stdout


class _NullCheck:
    """Placeholder for a device not in the checkboxes dict — always checked."""

    def isChecked(self) -> bool:
        return True


class ScenarioWindow(QMainWindow):
    """Window for selecting and running a scenario against the currently open devices."""

    def __init__(self, devices: dict[str, BaseDevice], parent=None) -> None:
        super().__init__(parent)
        self._devices = devices
        self._worker: _ScenarioWorker | None = None

        self.setWindowTitle("Run Scenario")
        self.resize(600, 420)

        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        # ── File picker row ───────────────────────────────────────────────
        file_row = QHBoxLayout()
        self._path_label = QLabel("No scenario selected")
        self._path_label.setWordWrap(False)
        file_row.addWidget(self._path_label, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(browse_btn)
        root.addLayout(file_row)

        # ── Devices — checkboxes to include/exclude from scenario ─────────
        devices_label = QLabel("Devices:")
        root.addWidget(devices_label)

        _checkbox_style = (
            "QCheckBox::indicator {"
            "  width: 14px; height: 14px;"
            "  border: 1px solid #aaa;"
            "  border-radius: 2px;"
            "  background-color: #d0d0d0;"
            "}"
            "QCheckBox::indicator:checked {"
            "  background-color: #3a9bdc;"
            "  border-color: #2a7ab0;"
            "}"
        )

        self._device_checks: dict[str, QCheckBox] = {}
        devices_row = QHBoxLayout()
        devices_row.setSpacing(12)
        for did in devices:
            cb = QCheckBox(did)
            cb.setStyleSheet(_checkbox_style)
            cb.setChecked(True)
            devices_row.addWidget(cb)
            self._device_checks[did] = cb
        devices_row.addStretch()
        root.addLayout(devices_row)

        # ── Phone 1 (DUT) selector ────────────────────────────────────────
        dut_row = QHBoxLayout()
        dut_row.setSpacing(8)
        dut_row.addWidget(QLabel("Phone 1 (DUT):"))
        self._dut_combo = QComboBox()
        for did in devices:
            self._dut_combo.addItem(did)
        dut_row.addWidget(self._dut_combo)
        dut_row.addStretch()
        root.addLayout(dut_row)

        # ── OCR mode toggle ───────────────────────────────────────────────
        context_row = QHBoxLayout()
        context_row.addWidget(QLabel("Send to Claude:"))
        self._ocr_check = QCheckBox("OCR")
        self._ocr_check.setStyleSheet(_checkbox_style)
        self._ocr_check.setChecked(True)
        context_row.addWidget(self._ocr_check)
        self._screenshots_check = QCheckBox("Screenshots")
        self._screenshots_check.setStyleSheet(_checkbox_style)
        self._screenshots_check.setChecked(False)
        context_row.addWidget(self._screenshots_check)
        context_row.addStretch()
        context_row.setSpacing(12)
        root.addLayout(context_row)

        # ── Log view ──────────────────────────────────────────────────────
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self._log, stretch=1)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._run_btn = QPushButton("Run")
        self._run_btn.setEnabled(False)
        self._run_btn.setProperty("primary", True)
        self._run_btn.clicked.connect(self._run)
        btn_row.addWidget(self._run_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._scenario_path: Path | None = None

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Scenario", "scenarios", "Text files (*.txt)"
        )
        if not path:
            return
        self._scenario_path = Path(path)
        self._path_label.setText(self._scenario_path.name)
        self._run_btn.setEnabled(True)

    def _run(self) -> None:
        if not self._scenario_path:
            return

        # Collect only devices whose checkbox is checked, DUT first
        dut_id = self._dut_combo.currentText()
        checked = {
            did: dev
            for did, dev in self._devices.items()
            if self._device_checks.get(did, _NullCheck()).isChecked()
        }
        # Reorder so the chosen DUT is Phone1
        selected_devices = {}
        if dut_id in checked:
            selected_devices[dut_id] = checked.pop(dut_id)
        selected_devices.update(checked)
        if not selected_devices:
            self._append_log("No devices selected — cannot run.")
            self._run_btn.setEnabled(True)
            return

        self._log.clear()
        self._run_btn.setEnabled(False)
        self._append_log(f"Starting: {self._scenario_path.name}")

        self._worker = _ScenarioWorker(
            self._scenario_path,
            selected_devices,
            use_ocr=self._ocr_check.isChecked(),
            use_screenshots=self._screenshots_check.isChecked(),
            parent=self,
        )
        self._worker.log.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _on_finished(self, rec_path: str) -> None:
        self._append_log(f"\nDone. Recording: {rec_path}")
        self._run_btn.setEnabled(True)

    def _on_failed(self, reason: str) -> None:
        self._append_log(f"\nFAILED: {reason}")
        self._run_btn.setEnabled(True)
