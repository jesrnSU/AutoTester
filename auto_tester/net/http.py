from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

import requests


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        verify: bool = True,
        timeout: int = 5,
        auth: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Device URL must use HTTPS without embedded credentials")
        self._origin = (parsed.scheme, parsed.hostname, parsed.port)
        self.verify = verify
        self.timeout = timeout
        self._session = requests.Session()
        self._session.verify = verify
        self._session.auth = auth

    def set_auth(self, auth: Optional[Any]) -> None:
        self._session.auth = auth

    def clear_cookies(self) -> None:
        self._session.cookies.clear()

    def request(
        self, method: str, path_or_url: str, **kwargs: Any
    ) -> requests.Response:
        url = self._build_url(path_or_url)
        timeout = kwargs.pop("timeout", self.timeout)
        kwargs["allow_redirects"] = False
        response = self._session.request(method, url, timeout=timeout, **kwargs)
        if 300 <= response.status_code < 400:
            raise requests.exceptions.HTTPError("Device redirects are disabled")
        response.raise_for_status()
        return response

    def get(self, path_or_url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path_or_url, **kwargs)

    def post(self, path_or_url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path_or_url, **kwargs)

    def _build_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            parsed = urlsplit(path_or_url)
            if (parsed.scheme, parsed.hostname, parsed.port) != self._origin or parsed.username is not None or parsed.password is not None:
                raise ValueError("Requests must stay on the configured device origin")
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"
