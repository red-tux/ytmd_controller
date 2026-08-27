"""Shared plumbing for every YTMD action: subscribe/unsubscribe to live state, thumbnail fetch+cache.

Mixed in ahead of KeyAction/DialAction, e.g. `class PlayPause(YTMDActionMixin, KeyAction): ...`
Plain mixin (no __init__) so it doesn't disturb the KeyAction/DialAction/ActionCore MRO.
"""
import functools
import io
import os
from typing import Callable

import cairosvg
from loguru import logger as log
from PIL import Image
from gi.repository import GLib

from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.ColorButtonRow import ColorButtonRow

LABEL_CHOICES = ["none", "title", "artist"]
DEFAULT_PROGRESS_COLOR = (255, 0, 0, 255)

PAUSE_OVERLAY_DIM_COLOR = (0, 0, 0, 140)
PAUSE_ICON_COLOR = (255, 255, 255, 255)

# Bundled Material Icons glyphs (Google, Apache-2.0 - see attribution.json) as source SVGs, so
# they rasterize crisply at whatever pixel size the actual deck hardware needs.
_MATERIAL_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "icons", "material",
)


@functools.lru_cache(maxsize=64)
def _rasterize_material_icon(name: str, size: int) -> Image.Image:
    svg_path = os.path.join(_MATERIAL_ICONS_DIR, f"{name}.svg")
    png_bytes = cairosvg.svg2png(url=svg_path, output_width=size, output_height=size)
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def load_material_icon(name: str, size: int, color: tuple[int, int, int, int]) -> Image.Image:
    """Loads a bundled Material Icons glyph (assets/icons/material/<name>.svg), rasterized at
    `size` px and recolored to `color` - the glyph's own alpha is used as a mask, so the same
    rasterized shape can be recolored for any on/off/active state without re-rendering the SVG."""
    base = _rasterize_material_icon(name, size)
    r, g, b, a = color
    colored = Image.new("RGBA", base.size, (r, g, b, 0))
    alpha = base.getchannel("A")
    if a != 255:
        alpha = alpha.point(lambda v: v * a // 255)
    colored.putalpha(alpha)
    return colored


def paste_material_icon(
    canvas: Image.Image, name: str, box: tuple[float, float, float, float],
    color: tuple[int, int, int, int], margin_fraction: float = 0.15,
) -> None:
    """Pastes a bundled Material Icons glyph, square and centered with a margin, into `box`
    (x0, y0, x1, y1) on `canvas`."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    size = round(min(w, h) * (1 - margin_fraction * 2))
    if size <= 0:
        return
    icon = load_material_icon(name, size, color)
    px, py = round(x0 + (w - size) / 2), round(y0 + (h - size) / 2)
    canvas.paste(icon, (px, py), icon)


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
        self.render_labels(title, artist, force=force)

    def render_labels(self, title: str, artist: str, force: bool = False) -> None:
        """Same as render_chosen_labels, but for callers that already have a title/artist pair
        not sourced from the live state's `video` object - e.g. a queue item being previewed."""
        # No hardcoded fallback= here - GenerativeUI.get_value() returns that literal whenever
        # the setting is unset, ignoring the row's own configured default_value. Callers set up
        # these rows with different defaults (setup_label_rows()'s top/middle/bottom_default),
        # so a hardcoded fallback here would silently override whichever caller didn't happen
        # to match it (e.g. TrackStep defaults to none/none/title, not PlayPause's title/artist/none).
        values = {"title": title, "artist": artist, "none": ""}
        labels = (
            values.get(self.top_label_row.get_value(), ""),
            values.get(self.middle_label_row.get_value(), ""),
            values.get(self.bottom_label_row.get_value(), ""),
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

    def apply_pause_overlay(self, image: Image.Image) -> Image.Image:
        """If playback is currently paused (per the shared PlaybackState singleton - see
        internal/playback_state.py), returns a dimmed copy of `image` with a centered pause
        icon; otherwise returns `image` unchanged. The dim is needed for the icon to stay
        legible over arbitrary album art, including light/white covers."""
        if not self.plugin_base.playback_state.is_paused():
            return image
        width, height = image.size
        dimmed = Image.alpha_composite(image, Image.new("RGBA", (width, height), PAUSE_OVERLAY_DIM_COLOR))
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        paste_material_icon(overlay, "pause", (0, 0, width, height), PAUSE_ICON_COLOR, margin_fraction=0.3)
        return Image.alpha_composite(dimmed, overlay)

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
