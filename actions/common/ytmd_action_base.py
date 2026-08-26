"""Shared plumbing for every YTMD action: subscribe/unsubscribe to live state, thumbnail fetch+cache.

Mixed in ahead of KeyAction/DialAction, e.g. `class PlayPause(YTMDActionMixin, KeyAction): ...`
Plain mixin (no __init__) so it doesn't disturb the KeyAction/DialAction/ActionCore MRO.
"""
from typing import Callable

from loguru import logger as log
from PIL import Image
from gi.repository import GLib

from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.ColorButtonRow import ColorButtonRow

LABEL_CHOICES = ["none", "title", "artist"]
DEFAULT_PROGRESS_COLOR = (255, 0, 0, 255)


class YTMDActionMixin:
    def on_ready(self) -> None:
        self._state_token = self.plugin_base.state_store.subscribe_state(self.on_ytmd_state)
        latest = self.plugin_base.state_store.get_latest()
        if latest is not None:
            self.on_ytmd_state(latest)

    def on_disconnect(self) -> None:
        token = getattr(self, "_state_token", None)
        if token is not None:
            self.plugin_base.state_store.unsubscribe_state(token)
            self._state_token = None

    def on_ytmd_state(self, state: dict) -> None:
        """Override in the concrete action. May be called from the backend's RPyC callback
        thread - route any set_media()/set_label() calls through self.ui() (GLib.idle_add)."""

    # EventAssigner always forwards whatever data the hardware callback produced (see
    # ActionCore._raw_event_callback -> EventAssigner.call(*args, **kwargs)), even when it's
    # None. KeyAction/DialAction's own default no-op hooks don't accept that extra argument
    # and crash on every key-up/most dial events; override the ones this plugin doesn't use
    # itself with a correct signature so they stay harmless no-ops instead of raising.
    def on_key_up(self, data=None) -> None:
        pass

    def on_key_short_up(self, data=None) -> None:
        pass

    def on_key_hold_start(self, data=None) -> None:
        pass

    def on_key_hold_stop(self, data=None) -> None:
        pass

    def on_dial_up(self, data=None) -> None:
        pass

    def on_dial_short_up(self, data=None) -> None:
        pass

    def on_dial_hold_start(self, data=None) -> None:
        pass

    def on_dial_hold_stop(self, data=None) -> None:
        pass

    def on_dial_short_touch_press(self, data=None) -> None:
        pass

    def on_dial_long_touch_press(self, data=None) -> None:
        pass

    def ui(self, fn, *args, **kwargs) -> None:
        """Marshal a set_media()/set_label()-style call onto the GTK main thread.
        Event/tick callbacks and the backend's RPyC callback all run off-thread."""
        GLib.idle_add(lambda: fn(*args, **kwargs))

    def get_display_size(self, fallback: tuple[int, int] = (200, 100)) -> tuple[int, int]:
        """The actual pixel size this action renders to (key or dial touchscreen slot).

        Resize art/overlays to this before compositing - pushing a much larger image (YTMD's
        thumbnails run up to 544x544) than the hardware will ever display wastes memory, makes
        every composite/alpha-blend operation slower than it needs to be, and means the
        framework has to downscale it itself on every single update.
        """
        try:
            width, height = self.get_state().controller_input.get_image_size()
        except Exception:
            return fallback
        if width <= 0 or height <= 0:
            return fallback
        return width, height

    def send_command(self, command: str, data=None) -> None:
        try:
            self.plugin_base.client.send_command(command, data)
            log.debug(f"{self.action_id} - Sent command {command!r} data={data!r}")
        except Exception as e:
            log.error(f"{self.action_id} - Failed to send command {command!r} data={data!r}: {e}")

    @staticmethod
    def get_video(state: dict) -> dict:
        return (state or {}).get("video") or {}

    @staticmethod
    def format_title_artist(state: dict) -> tuple[str, str]:
        video = YTMDActionMixin.get_video(state)
        return video.get("title", ""), video.get("author", "")

    @staticmethod
    def video_id(state: dict) -> str | None:
        """YTMD's actual YouTube video ID - a real stable identifier for the track, unlike the
        thumbnail URL (which is only meant for fetching the image, not for identifying which
        track it belongs to). Use this for track-change detection and as the cache key."""
        return YTMDActionMixin.get_video(state).get("id")

    @staticmethod
    def progress_fraction(state: dict) -> float:
        video = YTMDActionMixin.get_video(state)
        duration = video.get("durationSeconds") or 0
        if not duration:
            return 0.0
        position = ((state or {}).get("player") or {}).get("videoProgress", 0)
        return max(0.0, min(1.0, position / duration))

    # --- shared settings: which text goes on which label ------------------------

    def setup_label_rows(self, on_change, top_default="title", middle_default="artist", bottom_default="none") -> None:
        self._last_rendered_labels = None
        self.top_label_row = ComboRow(
            self, "top_label", top_default, items=LABEL_CHOICES, title="Top Label", on_change=on_change
        )
        self.middle_label_row = ComboRow(
            self, "middle_label", middle_default, items=LABEL_CHOICES, title="Middle Label", on_change=on_change
        )
        self.bottom_label_row = ComboRow(
            self, "bottom_label", bottom_default, items=LABEL_CHOICES, title="Bottom Label", on_change=on_change
        )

    def render_chosen_labels(self, state: dict, force: bool = False) -> None:
        title, artist = self.format_title_artist(state)
        values = {"title": title, "artist": artist, "none": ""}
        labels = (
            values.get(self.top_label_row.get_value(fallback="title"), ""),
            values.get(self.middle_label_row.get_value(fallback="artist"), ""),
            values.get(self.bottom_label_row.get_value(fallback="none"), ""),
        )
        if not force and labels == self._last_rendered_labels:
            return
        self._last_rendered_labels = labels

        top, middle, bottom = labels
        log.info(f"{self.action_id} - rendering labels top={top!r} middle={middle!r} bottom={bottom!r}")
        self.ui(self.set_top_label, top)
        self.ui(self.set_center_label, middle)
        self.ui(self.set_bottom_label, bottom)

    # --- shared settings: progress bar --------------------------------------------

    def setup_progress_rows(self, on_change, default_color: tuple = DEFAULT_PROGRESS_COLOR) -> None:
        self.progress_enabled_row = SwitchRow(
            self, "progress_enabled", False, title="Show Progress Bar", on_change=on_change
        )
        self.progress_width_row = SpinRow(
            self, "progress_width", 15, min=5, max=50, step=5, digits=0,
            title="Progress Bar Width (%)", on_change=on_change,
        )
        self.progress_opacity_row = SpinRow(
            self, "progress_opacity", 100, min=10, max=100, step=5, digits=0,
            title="Progress Bar Opacity (%)", on_change=on_change,
        )
        self.progress_color_row = ColorButtonRow(
            self, "progress_color", default_color, title="Progress Bar Color", on_change=on_change
        )

    def progress_enabled(self) -> bool:
        return self.progress_enabled_row.get_value(fallback=False)

    def draw_progress_bar(self, draw, width: int, height: int, fraction: float) -> int:
        """Draws the progress bar into `draw` (an ImageDraw on a transparent overlay) at the
        bottom of a `width`x`height` canvas. Returns the bar's pixel height, so callers that
        also render something else above it (e.g. DialControl's volume bar) know how much
        vertical space it reserved."""
        bar_height = round(height * (self.progress_width_row.get_value(fallback=15) / 100))
        opacity = round(255 * (self.progress_opacity_row.get_value(fallback=100) / 100))
        rgb = tuple(self.progress_color_row.get_value(fallback=DEFAULT_PROGRESS_COLOR))[:3]
        fill_width = round(width * fraction)

        draw.rectangle([0, height - bar_height, fill_width, height], fill=(*rgb, opacity))
        return bar_height

    @staticmethod
    def thumbnail_url(state: dict) -> str | None:
        """Cheap, I/O-free lookup - safe to call on every state-update for change detection."""
        thumbnails = YTMDActionMixin.get_video(state).get("thumbnails") or []
        if not thumbnails:
            return None
        return max(thumbnails, key=lambda t: t.get("width", 0))["url"]

    def request_thumbnail(self, state: dict, callback: Callable[[Image.Image | None], None]) -> None:
        """Convenience wrapper around `plugin_base.thumbnail_cache` for the currently-playing
        track specifically. For any other image (queue art, etc.), call
        `self.plugin_base.thumbnail_cache.request(key, url, callback)` directly with whatever
        key/url makes sense for that image - it's a general-purpose cache, not tied to this
        "now playing" shape.
        """
        key = self.video_id(state)
        url = self.thumbnail_url(state)
        if key is None or url is None:
            callback(None)
            return
        self.plugin_base.thumbnail_cache.request(key, url, callback)
