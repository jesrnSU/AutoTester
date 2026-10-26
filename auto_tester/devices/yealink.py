from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

import urllib3
from PIL import Image

from auto_tester.devices.base import BaseDevice, DeviceCapabilities
from auto_tester.net.http import HttpClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LATEST_SCREENSHOT_NAME = "latest_screen.png"


class YealinkDevice(BaseDevice):
    # Maps generic key name -> Yealink API key value
    SUPPORTED_KEYS: dict[str, str] = {
        "UP": "UP",
        "DOWN": "DOWN",
        "LEFT": "LEFT",
        "RIGHT": "RIGHT",
        "OK": "OK",
        "CANCEL": "X",
        "SPEAKER": "SPEAKER",
        "MUTE": "MUTE",
        "OFFHOOK": "OFFHOOK",
        "ONHOOK": "ONHOOK",
        **{f"SOFTKEY{i}": f"F{i}" for i in range(1, 5)},
        **{f"LINE{i}": f"L{i}" for i in range(1, 5)},
        **{d: d for d in "0123456789*#"},
    }

    # Maps generic action name -> Yealink API endpoint
    SUPPORTED_ACTIONS: dict[str, str] = {
        "OFFHOOK": "/servlet?key=OFFHOOK",
        "ONHOOK": "/servlet?key=ONHOOK",
        "KEYPRESS": "/servlet?key=",  # key appended at call time
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
        self._last_screenshot: Optional[Path] = None

    def capture_screen(
        self, output_dir: str = "artifacts/live", filename: Optional[str] = None
    ) -> Path:
        base_url = f"https://{self._username}:{self._password}@{self._ip}"
        response = self.http_client.get(f"{base_url}/screencapture", timeout=10)
        if not response.content:
            raise RuntimeError("screencapture returned an empty payload")
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if filename is None:
            filename = f"{self._id}_{LATEST_SCREENSHOT_NAME}"
        out_path = out_dir / filename
        temp_path = out_path.with_suffix(".tmp")
        Image.open(BytesIO(response.content)).save(temp_path, format="PNG")
        temp_path.replace(out_path)
        self._last_screenshot = out_path
        return out_path

    def perform_action(self, action: str, *args) -> None:
        if action not in self.SUPPORTED_ACTIONS:
            raise ValueError(
                f"Invalid action: {action!r}. Must be one of: {list(self.SUPPORTED_ACTIONS)}"
            )
        base_url = f"https://{self._username}:{self._password}@{self._ip}"
        if action == "KEYPRESS":
            api_key = self.SUPPORTED_KEYS.get(args[0])
            if api_key is None:
                raise ValueError(
                    f"Invalid key: {args[0]!r}. Must be one of: {list(self.SUPPORTED_KEYS)}"
                )
            self.http_client.get(f"{base_url}{self.SUPPORTED_ACTIONS[action]}{api_key}")
        else:
            self.http_client.get(f"{base_url}{self.SUPPORTED_ACTIONS[action]}")
        self.capture_screen()

    def get_screen_capture(self) -> Path | None:
        return self._last_screenshot
