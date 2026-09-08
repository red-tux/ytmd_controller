"""Thread-safe cache of the latest YTMD `/state` payload, with pub/sub for actions.

Fed by the backend process's Socket.IO relay (see backend/backend.py) and by one-shot
REST fetches; read by every action so they don't each maintain their own copy.
"""
import threading
from typing import Callable

from gi.repository import GLib

from . import profiling

StateCallback = Callable[[dict], None]
ConnectionCallback = Callable[[bool], None]


class StateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict | None = None
        self._connected = False
        # True until something proves the stored pairing token is rejected by YTMD
        # (startup check, settings-dialog check, or a live 401). Actions watch this to
        # replace their normal display with a "Check YTMD Settings" error.
        self._auth_ok = True
        self._state_subscribers: dict[int, StateCallback] = {}
        self._connection_subscribers: dict[int, ConnectionCallback] = {}
        self._auth_subscribers: dict[int, ConnectionCallback] = {}
        self._next_token = 1

    def update(self, state: dict) -> None:
        with self._lock:
            self._state = state
            subscribers = list(self._state_subscribers.values())
        # Fan out on the GTK main thread: the app feeds this from the backend's RPyC
        # callback thread, and actions also replay the latest state from on_ready() on
        # the main thread. Marshalling every delivery here keeps each action's
        # on_ytmd_state() single-threaded, so the per-action dedup bookkeeping
        # (_last_track_key etc.) needs no locking of its own.
        for callback in subscribers:
            profiling.incr("dispatch")
            GLib.idle_add(profiling.wrap_callback(callback), state)

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            subscribers = list(self._connection_subscribers.values())
        for callback in subscribers:
            GLib.idle_add(callback, connected)

    def set_auth_ok(self, ok: bool) -> None:
        """Flip the token-valid flag and notify auth subscribers if it actually changed."""
        with self._lock:
            if ok == self._auth_ok:
                return
            self._auth_ok = ok
            subscribers = list(self._auth_subscribers.values())
        for callback in subscribers:
            GLib.idle_add(callback, ok)

    def get_latest(self) -> dict | None:
        with self._lock:
            return self._state

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def is_auth_ok(self) -> bool:
        with self._lock:
            return self._auth_ok

    def subscribe_state(self, callback: StateCallback) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._state_subscribers[token] = callback
        return token

    def unsubscribe_state(self, token: int) -> None:
        with self._lock:
            self._state_subscribers.pop(token, None)

    def subscribe_connection(self, callback: ConnectionCallback) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._connection_subscribers[token] = callback
        return token

    def unsubscribe_connection(self, token: int) -> None:
        with self._lock:
            self._connection_subscribers.pop(token, None)

    def subscribe_auth(self, callback: ConnectionCallback) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._auth_subscribers[token] = callback
        return token

    def unsubscribe_auth(self, token: int) -> None:
        with self._lock:
            self._auth_subscribers.pop(token, None)
