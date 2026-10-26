from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DeviceCapabilities:
    touch_nav_enabled: bool = False
    nr_left_linekeys: int = 0
    nr_right_linekeys: int = 0
    nr_softkeys: int = 0


class BaseDevice(ABC):
    def __init__(
        self,
        device_id: str,
        ip: str,
        username: str,
        password: str,
        touchEnabled: bool,
        nr_left_linekeys: int = 0,
        nr_right_linekeys: int = 0,
        nr_softkeys: int = 0,
    ) -> None:
        self._id = device_id
        self._ip = ip
        self._username = username
        self._password = password
        self._capabilities = DeviceCapabilities(
            touch_nav_enabled=touchEnabled,
            nr_left_linekeys=nr_left_linekeys,
            nr_right_linekeys=nr_right_linekeys,
            nr_softkeys=nr_softkeys,
        )

    # Subclasses define their own supported keys/actions.
    SUPPORTED_KEYS: dict[str, str] = {}
    SUPPORTED_ACTIONS: dict[str, object] = {}

    # Optional lifecycle hooks. Stateful devices can override.
    def connect(self) -> None:
        return

    def disconnect(self) -> None:
        return

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> DeviceCapabilities:
        return self._capabilities

    # Common actions
    @abstractmethod
    def capture_screen(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    def perform_action(self, action: str, *args) -> None:
        raise NotImplementedError()

    @abstractmethod
    def get_screen_capture(self) -> Path | None:
        """Fetch the latest screenshot as a file path. Used for live view and test parsing."""
        raise NotImplementedError()
