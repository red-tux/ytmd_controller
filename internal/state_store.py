"""Thread-safe cache of the latest YTMD `/state` payload, with pub/sub for actions.

Fed by the backend process's Socket.IO relay (see backend/backend.py) and by one-shot
REST fetches; read by every action so they don't each maintain their own copy.
"""
import threading
from typing import Callable

StateCallback = Callable[[dict], None]
ConnectionCallback = Callable[[bool], None]


class StateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict | None = None
        self._connected = False
        self._state_subscribers: dict[int, StateCallback] = {}
        self._connection_subscribers: dict[int, ConnectionCallback] = {}
        self._next_token = 1

    def update(self, state: dict) -> None:
        with self._lock:
            self._state = state
            subscribers = list(self._state_subscribers.values())
        for callback in subscribers:
            callback(state)

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            subscribers = list(self._connection_subscribers.values())
        for callback in subscribers:
            callback(connected)

    def get_latest(self) -> dict | None:
        with self._lock:
            return self._state

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

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
