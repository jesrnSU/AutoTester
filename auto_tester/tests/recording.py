"""Shared recording session for logging device actions and screenshots."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from auto_tester.devices.device_configs import make_device
from auto_tester.tests.ssim_validate import evaluate
from auto_tester.devices.base import BaseDevice

RECORDINGS_DIR = "recordings"
ARTIFACTS_DIR = Path("artifacts")
STEP_DELAY = 1.0
_SSIM_RETRIES = 3


class Step:
    """A single step in the recording sequence, either an action or a screenshot."""

    def __init__(self, device_id: str, kind: str, value: list[str]) -> None:
        self.device_id = device_id
        self.kind = kind  # "action" or "screenshot"
        self.value = (
            value  # for action: [action_name, arg1, ...]; for screenshot: [image_name]
        )


class Recording:
    """Shared recording session that multiple controllers can write to.

    All actions and screenshots from every attached device are logged in
    chronological order into a single sequence.txt inside the session folder.
    Each line is prefixed with the device name so entries can be told apart:

        [Polycom E100] action | send_keypress(LINE2)
        [Yealink T85]  action | send_keypress(OFFHOOK)
        [Polycom E100] screenshot | step_001_Polycom_E100.png
    """

    def __init__(self) -> None:
        self._log: list[str] = []
        self._step = 0
        self._folder: Path | None = None
        self._active = False
        self._lock = threading.Lock()

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self, session_name: str, devices: list[BaseDevice]) -> Path:
        """Create the recording folder and begin collecting entries."""
        with self._lock:
            if self._active:
                return self._folder  # already started — idempotent
            ts = int(time.time())
            self._folder = Path(RECORDINGS_DIR) / f"{session_name}_{ts}"
            self._folder.mkdir(parents=True, exist_ok=True)
            self._log = []
            self._step = 0
            self._active = True
            self._log.append("test devices")
            self._log.append(",".join(d._id for d in devices))
            self._log.append("action sequence")
        print(f"Recording started: {self._folder}")
        return self._folder

    def stop(self) -> Path | None:
        """Write sequence.txt and close the session. Returns the folder path."""
        with self._lock:
            if not self._active:
                return None
            self._active = False
            log_path = self._folder / "sequence.txt"
            log_path.write_text("\n".join(self._log), encoding="utf-8")
        print(f"Recording saved: {log_path}")
        return self._folder

    def log_action(self, device_name: str, call_str: str) -> None:
        with self._lock:
            if self._active:
                # call_str is "perform_action(ACTION, arg1, ...)" — flatten to CSV
                inner = (
                    call_str[len("perform_action(") : -1]
                    if call_str.startswith("perform_action(")
                    else call_str
                )
                inner = ",".join(p.strip() for p in inner.split(","))
                self._log.append(f"{device_name},action,{inner}")

    def log_screenshot(self, device_name: str, src_path: Path) -> None:
        with self._lock:
            if not self._active or self._folder is None:
                return
            self._step += 1
            img_name = f"step_{self._step:03d}_{device_name.replace(' ', '_')}.png"
            (self._folder / img_name).write_bytes(Path(src_path).read_bytes())
            self._log.append(f"{device_name},screenshot,{img_name}")


# Helper functions for parsing and running recordings ---------------------------------------------


def parse_recording(seq_path: Path) -> tuple[list[str], list[Step]]:
    """Parse sequence.txt into device IDs and ordered steps."""
    device_ids: list[str] = []
    steps: list[Step] = []
    section = None

    for raw in Path(seq_path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "test devices":
            section = "devices"
            continue
        if line == "action sequence":
            section = "actions"
            continue
        if section == "devices":
            device_ids.extend(line.split(","))
        elif section == "actions":
            parts = line.split(",")
            if len(parts) == 2 and parts[0] == "contract":
                # contract,<name> — a nested recording to expand
                steps.append(Step(device_id="", kind="contract", value=[parts[1]]))
            elif len(parts) >= 3:
                steps.append(Step(device_id=parts[0], kind=parts[1], value=parts[2:]))

    return device_ids, steps


def rewrite_action(seq_path: Path, old_step: Step, new_resp: dict) -> None:
    """Replace a failing action line in the recording with the LLM-chosen action."""
    new_value = [new_resp["action"]]
    if new_resp["arg"]:
        new_value.append(new_resp["arg"])
    old_line = ",".join([old_step.device_id, old_step.kind] + old_step.value)
    new_line = ",".join([new_resp["device_id"], "action"] + new_value)
    text = seq_path.read_text(encoding="utf-8")
    if old_line in text:
        seq_path.write_text(text.replace(old_line, new_line, 1), encoding="utf-8")


def run_recording(
    seq_path: Path,
    active_devices: dict[str, BaseDevice],
) -> list[str]:
    """Execute all steps in the recording. Returns failure messages; empty = all passed.

    Devices not present in active_devices are loaded from saved_configs and connected
    automatically for the duration of this run.
    """
    device_ids, steps = parse_recording(seq_path)

    devices = dict(active_devices)
    auto_connected: list[BaseDevice] = []
    for did in device_ids:
        if did not in devices:
            try:
                dev = make_device(did)
                dev.connect()
                devices[did] = dev
                auto_connected.append(dev)
                print(f"[recording] Auto-connected: {did}")
            except Exception as exc:
                print(f"[recording] Could not connect '{did}': {exc}")

    failures: list[str] = []
    try:
        for step in steps:
            if step.kind == "contract":
                sub_name = step.value[0]
                # Find the contract recording that matches the DUT
                # (the first device in the sequence is the DUT)
                dut_id = device_ids[0] if device_ids else None
                sub_seq = (
                    Path(RECORDINGS_DIR) / f"{sub_name}_{dut_id}" / "sequence.txt"
                    if dut_id
                    else None
                )
                if sub_seq and sub_seq.exists():
                    print(
                        f"[recording] Expanding contract '{sub_name}' from {sub_seq}"
                    )
                    sub_failures = run_recording(sub_seq, devices)
                    if sub_failures:
                        print(
                            f"[recording] Contract '{sub_name}' had failures — aborting parent recording"
                        )
                        failures.extend(sub_failures)
                        return failures
                    # Give phones time to settle after contract completes
                    time.sleep(STEP_DELAY * 2)
                else:
                    print(
                        f"[recording] Contract '{sub_name}' has no recording for "
                        f"'{dut_id}' — skipping"
                    )
                continue

            device = devices.get(step.device_id)
            if device is None:
                continue

            if step.kind == "action":
                device.perform_action(step.value[0], *step.value[1:])
                time.sleep(STEP_DELAY)

            elif step.kind == "screenshot":
                ref = str(seq_path.parent / step.value[0])
                for attempt in range(1, _SSIM_RETRIES + 1):
                    actual = str(
                        device.capture_screen(
                            str(ARTIFACTS_DIR / "live"), f"live_{step.device_id}.png"
                        )
                    )
                    result = evaluate(ref, actual)
                    if result.passed or attempt == _SSIM_RETRIES:
                        print(
                            f"[recording] Step {step.device_id} '{step.value[0]}': "
                            f"SSIM={result.score:.3f} (attempt {attempt})"
                        )
                        break
                    time.sleep(STEP_DELAY)
                if not result.passed:
                    failures.append(
                        f"{step.device_id}: SSIM {result.score:.3f} ({step.value[0]})"
                    )
                    print(
                        f"[recording] SSIM check failed for '{step.value[0]}' — aborting recording"
                    )
                    return failures
    finally:
        for dev in auto_connected:
            try:
                dev.disconnect()
            except Exception:
                pass

    return failures
