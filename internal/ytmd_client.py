"""REST client for the YouTube Music Desktop Companion Server API v1.

Reference: https://github.com/ytmdesktop/ytmdesktop/wiki/v2-%E2%80%90-Companion-Server-API-v1
"""
import re
import socket
import threading

import requests

# YTMD dedupes/overwrites authorized clients by appId (see ytmdesktop's createAuthToken),
# so this must be unique per install - it's user-configurable in the settings UI, defaulting
# to the machine's hostname.
APP_ID_PATTERN = re.compile(r"^[a-z0-9_-]{2,32}$")
FALLBACK_APP_ID = "ytmd-controller"

DEFAULT_APP_NAME = "YTMD Controller for StreamDeck"
APP_VERSION = "0.1.0"

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9863

REQUEST_TIMEOUT = 5
# The companion server holds the /auth/request call open until the user confirms
# (or 30s pass) in the YTMD app itself.
PAIRING_TIMEOUT = 35


class YTMDError(Exception):
    """Base error for all YTMD companion server failures."""


class YTMDConnectionError(YTMDError):
    """Could not reach the companion server at all (host/port wrong, app not running)."""


class YTMDAuthError(YTMDError):
    """The companion server rejected the request (missing/invalid/expired token)."""


def sanitize_app_id(raw: str) -> str:
    """Coerce arbitrary text (e.g. a hostname) into YTMD's appId format: ^[a-z0-9_-]{2,32}$."""
    candidate = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower())
    candidate = re.sub(r"-{2,}", "-", candidate).strip("-_")
    candidate = candidate[:32]
    if len(candidate) < 2:
        return FALLBACK_APP_ID
    return candidate


def default_app_id() -> str:
    try:
        return sanitize_app_id(socket.gethostname())
    except OSError:
        return FALLBACK_APP_ID


class YTMDClient:
    def __init__(self, host: str, port: int, token: str | None = None):
        self.host = host
        self.port = port
        self.token = token
        # host/port/token are rewritten by the settings UI (main thread) while request
        # threads read them - guard both sides so a request can't pick up a half-applied
        # host/port/token triple.
        self._lock = threading.Lock()

    def configure(self, host: str, port: int, token: str | None) -> None:
        """Apply a new host/port/token atomically. Called from the settings UI whenever any
        of them change (including after pairing)."""
        with self._lock:
            self.host = host
            self.port = port
            self.token = token

    @property
    def base_url(self) -> str:
        with self._lock:
            return f"http://{self.host}:{self.port}/api/v1"

    def _headers(self) -> dict:
        with self._lock:
            if self.token:
                return {"Authorization": self.token}
            return {}

    def _request(self, method: str, path: str, timeout: float, **kwargs) -> requests.Response:
        try:
            response = requests.request(
                method, f"{self.base_url}{path}", headers=self._headers(), timeout=timeout, **kwargs
            )
        except requests.RequestException as e:
            raise YTMDConnectionError(f"Could not reach YTMD at {self.host}:{self.port}: {e}") from e

        if response.status_code == 401:
            raise YTMDAuthError("YTMD rejected the request (missing/invalid/expired token)")
        if not response.ok:
            raise YTMDError(f"YTMD returned {response.status_code} for {method} {path}: {response.text}")
        return response

    def request_pair_code(self, app_id: str, app_name: str = DEFAULT_APP_NAME) -> str:
        """Step 1 of pairing: ask YTMD to show a code the user confirms in-app."""
        if not APP_ID_PATTERN.match(app_id):
            raise YTMDError(f"Invalid client identifier {app_id!r}: must match {APP_ID_PATTERN.pattern}")
        response = self._request(
            "POST",
            "/auth/requestcode",
            REQUEST_TIMEOUT,
            json={"appId": app_id, "appName": app_name, "appVersion": APP_VERSION},
        )
        return response.json()["code"]

    def exchange_code(self, app_id: str, code: str) -> str:
        """Step 2 of pairing: blocks (up to PAIRING_TIMEOUT) until the user confirms in YTMD."""
        response = self._request(
            "POST",
            "/auth/request",
            PAIRING_TIMEOUT,
            json={"appId": app_id, "code": code},
        )
        token = response.json()["token"]
        self.token = token
        return token

    def check_auth(self) -> None:
        """Probe whether the configured token is currently accepted by YTMD.

        Returns normally if the token works, raises YTMDAuthError if YTMD rejects it
        (missing/invalid/expired), or YTMDConnectionError if YTMD can't be reached.
        GET /state is authenticated and side-effect free, so it doubles as a token test."""
        self._request("GET", "/state", REQUEST_TIMEOUT)

    def send_command(self, command: str, data=None) -> None:
        body = {"command": command}
        if data is not None:
            body["data"] = data
        self._request("POST", "/command", REQUEST_TIMEOUT, json=body)
