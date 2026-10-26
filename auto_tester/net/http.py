from __future__ import annotations

from typing import Any, Optional

import requests


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        verify: bool = False,
        timeout: int = 5,
        auth: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
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
        response = self._session.request(method, url, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response

    def get(self, path_or_url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path_or_url, **kwargs)

    def post(self, path_or_url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path_or_url, **kwargs)

    def _build_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"
