from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from auto_tester.devices.base import BaseDevice, DeviceCapabilities
from auto_tester.net.http import HttpClient


LATEST_SCREENSHOT_NAME = "latest_screen.png"

DEVICE_MGMT_API = "/api/v1/mgmt"
DEVICE_SCREEN_CAPTURE_API = "/captureScreen/mainScreen/1"


class PolycomDevice(BaseDevice):
    # Maps generic key name -> Polycom API KeyName value
    SUPPORTED_KEYS: dict[str, str] = {
        "UP": "Up",
        "DOWN": "Down",
        "LEFT": "Left",
        "RIGHT": "Right",
        "OK": "Select",
        "CANCEL": "Delete",
        "SPEAKER": "Handsfree",
        "MUTE": "Mute",
        **{f"SOFTKEY{i}": f"SoftKey{i}" for i in range(1, 5)},
        **{f"LINE{i}": f"Line{i}" for i in range(1, 5)},
        **{str(i): f"Dialpad{i}" for i in range(10)},
        "*": "DialpadStar",
        "#": "DialpadPound",
        "Menu": "Menu",
    }

    # Maps generic action name -> (API endpoint, API Type value)
    SUPPORTED_ACTIONS: dict[str, tuple[str, str]] = {
        "OFFHOOK": ("/simulateHook", "Up"),
        "ONHOOK": ("/simulateHook", "Down"),
        "TOUCH": ("/simulateTouch", "Tap"),  # requires x, y args
        "KEYPRESS": ("/simulateKeyEvent", "Tap"),  # requires key arg
    }

    def __init__(
        self,
        device_id: str,
        ip: str,
        username: str,
        password: str,
        http_client: HttpClient,
        touchEnabled: bool = False,
        nr_left_linekeys: int = 0,
        nr_right_linekeys: int = 0,
        nr_softkeys: int = 0,
    ) -> None:
        super().__init__(
            device_id,
            ip,
            username,
            password,
            touchEnabled,
            nr_left_linekeys,
            nr_right_linekeys,
            nr_softkeys,
        )
        self.http_client = http_client
        self._session_id: Optional[str] = None
        self._last_screenshot: Optional[Path] = None

    def connect(self) -> None:
        # Digest auth uses a session; Basic auth sends credentials per-request
        if not isinstance(self.http_client._session.auth, HTTPBasicAuth):
            response = self.http_client.get(f"{DEVICE_MGMT_API}/session/create")
            self._session_id = response.json()["data"]["sessionId"]
        self.http_client.post(
            f"{DEVICE_MGMT_API}/config/set",
            json={
                "data": {
                    "up.screenCapture.enabled": "1",
                    "up.screenCapture.value": "1",
                    "up.screenCapture.allowed": "1",
                }
            },
        )

    def disconnect(self) -> None:
        if self._session_id:
            self.http_client.clear_cookies()
            self._session_id = None

    def capture_screen(
        self, output_dir: str = "artifacts/live", filename: Optional[str] = None
    ) -> Path:
        # The screen-capture endpoint uses Digest Auth directly, not the management session.
        response = self.http_client.get(
            DEVICE_SCREEN_CAPTURE_API,
            timeout=10,
            auth=HTTPDigestAuth(self._username, self._password),
        )
        image_bytes = response.content
        if not image_bytes:
            raise AssertionError("captureScreen returned an empty payload")

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if filename is None:
            filename = f"{self._id}_{LATEST_SCREENSHOT_NAME}"
        out_path = out_dir / filename
        temp_path = out_path.with_suffix(".tmp")
        Image.open(BytesIO(image_bytes)).save(temp_path, format="PNG")
        temp_path.replace(out_path)
        self._last_screenshot = out_path
        return out_path

    def perform_action(self, action: str, *args) -> None:
        if action not in self.SUPPORTED_ACTIONS:
            raise ValueError(
                f"Invalid action: {action!r}. Must be one of: {list(self.SUPPORTED_ACTIONS)}"
            )
        endpoint, api_type = self.SUPPORTED_ACTIONS[action]
        if action in ("OFFHOOK", "ONHOOK"):
            self.http_client.post(
                f"{DEVICE_MGMT_API}{endpoint}",
                json={"data": {"Type": api_type}},
            )
        elif action == "TOUCH":
            x, y = args
            self.http_client.post(
                f"{DEVICE_MGMT_API}{endpoint}",
                json={
                    "data": {
                        "Type": api_type,
                        "Positions": [{"X": str(x), "Y": str(y)}],
                    }
                },
            )
        elif action == "KEYPRESS":
            api_key = self.SUPPORTED_KEYS.get(args[0])
            if api_key is None:
                raise ValueError(
                    f"Invalid key: {args[0]!r}. Must be one of: {list(self.SUPPORTED_KEYS)}"
                )
            self.http_client.post(
                f"{DEVICE_MGMT_API}{endpoint}",
                json={"data": {"Type": api_type, "KeyName": api_key}},
            )
        self.capture_screen()

    def get_screen_capture(self) -> Path | None:
        return self._last_screenshot
