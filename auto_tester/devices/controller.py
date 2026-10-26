"""Device controller for managing device lifecycle and actions."""

from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Callable, Optional

import cv2

from auto_tester.devices.base import BaseDevice
from auto_tester.tests.recording import Recording

# Re-export so existing imports from this module keep working
__all__ = ["DeviceController", "Recording"]


class DeviceController:
    """Wraps a device and provides two modes:

    Normal mode  — key presses execute immediately; the live screenshot refreshes
                   automatically after each action and every 4 seconds from the UI timer.

    Recording mode — same as normal, but every action and every "Capture Screen"
                     button press are logged to the attached Recording session.
                     Attach a Recording with attach_recording(); multiple controllers
                     can share the same Recording to log to one sequence.txt.
    """

    def __init__(self, device: BaseDevice) -> None:
        self.device = device
        self._on_error_callback: Optional[Callable[[str], None]] = None
        self._recording: Optional[Recording] = None

    def set_error_callback(self, callback: Callable[[str], None]) -> None:
        self._on_error_callback = callback

    def attach_recording(self, recording: Recording) -> None:
        """Share a Recording session with this controller."""
        self._recording = recording

    def detach_recording(self) -> None:
        self._recording = None

    # ── Device lifecycle ───────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            self.device.connect()
            return True
        except Exception as e:
            self._notify_error(f"Failed to connect: {e}")
            return False

    def disconnect(self) -> None:
        try:
            self.device.disconnect()
        except Exception as e:
            self._notify_error(f"Error disconnecting: {e}")

    # ── actions ────────────────────────────────────────────────

    def execute_action(self, action: str, *args) -> None:
        """Send an action to the device and refresh the live view.
        If a Recording is attached, also logs the action."""

        def _run():
            try:
                self.device.perform_action(action, *args)
                if self._recording:
                    call_str = (
                        f"perform_action({', '.join(str(a) for a in (action, *args))})"
                    )
                    self._recording.log_action(self.device.id, call_str)
            except Exception as e:
                self._notify_error(f"Error in {action}: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def refresh_screen(self) -> None:
        """Fetch the latest screenshot and update the live view. Never logged."""

        def _run():
            try:
                self.device.capture_screen()
            except Exception as e:
                self._notify_error(f"Error refreshing screen: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def capture_screen(self) -> None:
        """Save a screenshot and log it to the Recording (if attached and active)."""

        def _run():
            try:
                out_path = self.device.capture_screen()
                if self._recording:
                    self._recording.log_screenshot(self.device.id, out_path)
            except Exception as e:
                self._notify_error(f"Error capturing screen: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def run_recording(self, seq_path: Path, active_devices: dict) -> None:
        """Run a test recording in a background thread and print failures."""
        from auto_tester.tests.recording import run_recording

        def _run():
            failures = run_recording(seq_path, active_devices)
            if failures:
                print("[test] FAILURES:")
                for f in failures:
                    print(f"  {f}")
            else:
                print("[test] All checks passed")

        threading.Thread(target=_run, daemon=True).start()

    def get_latest_screenshot(self) -> cv2.typing.MatLike | None:
        return self.device.get_screen_capture()

    # ── Internals ──────────────────────────────────────────────────────────

    def _notify_error(self, message: str) -> None:
        if self._on_error_callback:
            self._on_error_callback(message)
        else:
            print(message)
