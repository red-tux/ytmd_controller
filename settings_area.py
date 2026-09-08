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

        # Status text goes in the row subtitle (not a suffix Gtk.Label) so a long
        # pairing error wraps to multiple lines instead of forcing the whole
        # settings dialog to overflow horizontally and hide the controls.
        self.status_row = Adw.ActionRow(title="Status")
        self.status_row.set_use_markup(False)
        self.status_row.set_subtitle_lines(0)
        self.status_row.set_subtitle_selectable(True)
        self.add(self.status_row)

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

        self.add(Gtk.Separator(margin_top=12, margin_bottom=12))

        self.cache_stats_label = Gtk.Label(label="")
        cache_stats_row = Adw.ActionRow(title="Cached Thumbnails")
        cache_stats_row.add_suffix(self.cache_stats_label)
        self.add(cache_stats_row)

        self.purge_button = Gtk.Button(
            label="Purge Cache", valign=Gtk.Align.CENTER, css_classes=["destructive-action"]
        )
        self.purge_button.connect("clicked", self.on_purge_clicked)
        purge_row = Adw.ActionRow(
            title="Purge Thumbnail Cache",
            subtitle="Deletes every cached thumbnail. They're re-downloaded from YTMD as needed.",
        )
        purge_row.add_suffix(self.purge_button)
        self.add(purge_row)

        max_entries = plugin_base.thumbnail_cache.get_max_entries()
        self._max_entries_adjustment = Gtk.Adjustment.new(max_entries, 1, 1000, 1, 10, 0)
        self.max_entries_row = Adw.SpinRow(
            title="Max Cached Thumbnails",
            subtitle="Applies immediately - no restart needed.",
            adjustment=self._max_entries_adjustment,
            value=max_entries,
        )
        self.max_entries_row.set_digits(0)
        self.max_entries_row.connect("changed", self.on_max_entries_changed)
        self.add(self.max_entries_row)

        self.update_status_label()
        self.update_cache_stats_label()

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

    def update_cache_stats_label(self) -> None:
        count, total_bytes = self.plugin_base.thumbnail_cache.get_stats()
        plural = "" if count == 1 else "s"
        self.cache_stats_label.set_label(f"{count} thumbnail{plural} · {total_bytes / (1024 * 1024):.1f} MB")

    def on_purge_clicked(self, button: Gtk.Button) -> None:
        self.plugin_base.thumbnail_cache.purge()
        self.update_cache_stats_label()

    def on_max_entries_changed(self, spin_row: Adw.SpinRow) -> None:
        self.plugin_base.on_thumbnail_cache_max_entries_changed(int(spin_row.get_value()))
        self.update_cache_stats_label()

    def update_status_label(self) -> None:
        settings = self.plugin_base.get_settings()
        self.status_row.set_subtitle("Paired" if settings.get("token") else "Not paired")

    def on_pair_clicked(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        self.status_row.set_subtitle("Requesting pairing code…")
        threading.Thread(target=self._pair_thread, daemon=True).start()

    def _pair_thread(self) -> None:
        settings = self.plugin_base.get_settings()
        host = settings.get("host", DEFAULT_HOST)
        port = settings.get("port", DEFAULT_PORT)
        app_id = sanitize_app_id(settings.get("app_id") or default_app_id())
        client = YTMDClient(host, port)
        try:
            code = client.request_pair_code(app_id, DEFAULT_APP_NAME)
            GLib.idle_add(self.status_row.set_subtitle, f"Confirm code {code} in YTMD…")
            token = client.exchange_code(app_id, code)
        except YTMDError as e:
            GLib.idle_add(self._on_pair_failed, str(e))
            return
        GLib.idle_add(self._on_pair_succeeded, token)

    def _on_pair_succeeded(self, token: str) -> None:
        settings = self.plugin_base.get_settings()
        settings["token"] = token
        self.plugin_base.set_settings(settings)
        self.status_row.set_subtitle("Paired")
        self.pair_button.set_sensitive(True)
        self.plugin_base.on_connection_settings_changed()

    def _on_pair_failed(self, error: str) -> None:
        self.status_row.set_subtitle(f"Pairing failed: {error}")
        self.pair_button.set_sensitive(True)
