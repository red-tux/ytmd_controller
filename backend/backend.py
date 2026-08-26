"""Isolated backend process: owns the YTMD Socket.IO real-time connection.

Runs in the plugin's own venv (see __install__.py) so python-socketio never has to be
added to the shared app requirements.txt. Talks to the foreground PluginBase over RPyC
via streamcontroller_plugin_tools.BackendBase - see that class for the connection
handshake (it connects to the frontend's RPyC server, starts its own, then registers).
"""
import json

from loguru import logger as log
from streamcontroller_plugin_tools import BackendBase
import socketio

# YTMD exposes its realtime feed as a Socket.IO *namespace* (not a transport path) -
# see fastify.io.of("/api/v1/realtime") in ytmdesktop's own source. The engine.io
# handshake itself stays on the default "/socket.io" path.
REALTIME_NAMESPACE = "/api/v1/realtime"


class YTMDBackend(BackendBase):
    def __init__(self):
        self.sio = socketio.Client(reconnection=True, reconnection_delay=1, reconnection_delay_max=10)
        self.sio.on("state-update", self._on_state_update, namespace=REALTIME_NAMESPACE)
        self.sio.on("connect", self._on_connect, namespace=REALTIME_NAMESPACE)
        self.sio.on("disconnect", self._on_disconnect, namespace=REALTIME_NAMESPACE)
        super().__init__()

    def _on_state_update(self, data) -> None:
        try:
            # rpyc proxies plain dict/list arguments by reference rather than copying them,
            # so every .get()/[] access on the frontend would silently round-trip back here
            # (and recurse). Round-tripping through JSON forces a real, local copy instead.
            self.frontend.on_state_update(json.dumps(data))
        except Exception as e:
            log.error(f"Failed relaying state-update to frontend: {e}")

    def _on_connect(self) -> None:
        log.success("Connected to YTMD realtime endpoint")
        try:
            self.frontend.on_connection_status(True)
        except Exception as e:
            log.error(f"Failed relaying connection status to frontend: {e}")

    def _on_disconnect(self) -> None:
        log.warning("Disconnected from YTMD realtime endpoint")
        try:
            self.frontend.on_connection_status(False)
        except Exception as e:
            log.error(f"Failed relaying connection status to frontend: {e}")

    def configure(self, host: str, port: int, token: str) -> None:
        """Called by the foreground whenever host/port/token change (including after pairing)."""
        if self.sio.connected:
            self.sio.disconnect()

        if not token:
            log.info("No token configured yet, not connecting to YTMD realtime endpoint")
            return

        url = f"http://{host}:{port}"
        try:
            self.sio.connect(
                url,
                namespaces=[REALTIME_NAMESPACE],
                transports=["websocket"],
                auth={"token": token},
                wait_timeout=10,
            )
        except Exception as e:
            log.error(f"Could not connect to YTMD realtime endpoint at {url}: {e}")


if __name__ == "__main__":
    YTMDBackend()
