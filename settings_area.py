"""Plugin-level settings screen: host/port + pairing button.

Built manually because plugin-level settings have no GenerativeUI widget set (that's
only for per-action settings stored in page JSON) - see this plugin's own CLAUDE.md.
"""
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from .internal.ytmd_client import (
    YTMDClient,
    YTMDError,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_APP_NAME,
    default_app_id,
    sanitize_app_id,
)


class YTMDSettingsGroup(Adw.PreferencesGroup):
    def __init__(self, plugin_base):
        super().__init__(
            title="YTMD Connection",
            description="Connect to YouTube Music Desktop's Companion Server",
        )
        self.plugin_base = plugin_base

        settings = plugin_base.get_settings()

        self.host_row = Adw.EntryRow(title="Host", text=settings.get("host", DEFAULT_HOST))
        self.host_row.connect("notify::text", self.on_host_port_changed)
        self.add(self.host_row)

        self.port_row = Adw.EntryRow(title="Port", text=str(settings.get("port", DEFAULT_PORT)))
        self.port_row.connect("notify::text", self.on_host_port_changed)
        self.add(self.port_row)

        if not settings.get("app_id"):
            settings["app_id"] = default_app_id()
            plugin_base.set_settings(settings)

        self.app_id_row = Adw.EntryRow(title="Client Identifier", text=settings["app_id"])
        self.app_id_row.connect("notify::text", self.on_app_id_changed)
        self.add(self.app_id_row)

        self.status_label = Gtk.Label(label="")
        status_row = Adw.ActionRow(title="Status")
        status_row.add_suffix(self.status_label)
        self.add(status_row)

        self.pair_button = Gtk.Button(
            label="Pair with YTMD", valign=Gtk.Align.CENTER, css_classes=["suggested-action"]
        )
        self.pair_button.connect("clicked", self.on_pair_clicked)
        pair_row = Adw.ActionRow(
            title="Pairing",
            subtitle="Requires confirmation inside YTMD. Re-pairing with the same "
            "Client Identifier replaces the existing authorization for it.",
        )
        pair_row.add_suffix(self.pair_button)
        self.add(pair_row)

        self.update_status_label()

    def on_host_port_changed(self, *args) -> None:
        settings = self.plugin_base.get_settings()
        settings["host"] = self.host_row.get_text().strip() or DEFAULT_HOST
        try:
            settings["port"] = int(self.port_row.get_text().strip())
        except ValueError:
            pass
        self.plugin_base.set_settings(settings)
        self.plugin_base.on_connection_settings_changed()

    def on_app_id_changed(self, *args) -> None:
        settings = self.plugin_base.get_settings()
        settings["app_id"] = sanitize_app_id(self.app_id_row.get_text())
        self.plugin_base.set_settings(settings)

    def update_status_label(self) -> None:
        settings = self.plugin_base.get_settings()
        self.status_label.set_label("Paired" if settings.get("token") else "Not paired")

    def on_pair_clicked(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        self.status_label.set_label("Requesting pairing code…")
        threading.Thread(target=self._pair_thread, daemon=True).start()

    def _pair_thread(self) -> None:
        settings = self.plugin_base.get_settings()
        host = settings.get("host", DEFAULT_HOST)
        port = settings.get("port", DEFAULT_PORT)
        app_id = sanitize_app_id(settings.get("app_id") or default_app_id())
        client = YTMDClient(host, port)
        try:
            code = client.request_pair_code(app_id, DEFAULT_APP_NAME)
            GLib.idle_add(self.status_label.set_label, f"Confirm code {code} in YTMD…")
            token = client.exchange_code(app_id, code)
        except YTMDError as e:
            GLib.idle_add(self._on_pair_failed, str(e))
            return
        GLib.idle_add(self._on_pair_succeeded, token)

    def _on_pair_succeeded(self, token: str) -> None:
        settings = self.plugin_base.get_settings()
        settings["token"] = token
        self.plugin_base.set_settings(settings)
        self.status_label.set_label("Paired")
        self.pair_button.set_sensitive(True)
        self.plugin_base.on_connection_settings_changed()

    def _on_pair_failed(self, error: str) -> None:
        self.status_label.set_label(f"Pairing failed: {error}")
        self.pair_button.set_sensitive(True)
