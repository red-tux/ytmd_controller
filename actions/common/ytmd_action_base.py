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

from ...internal import profiling as _profiling

LABEL_CHOICES = ["none", "title", "artist"]
DEFAULT_PROGRESS_COLOR = (255, 0, 0, 255)

# Icon/Color asset keys, registered once in main.py via PluginBase.add_icon()/add_color() and
# read by the rendering helpers below. Centralized here (rather than as string literals at
# each call site) so the registration site and every render call site are guaranteed to agree
# on the exact key, and so main.py doesn't need to duplicate them.
#
# Each is independently user-overridable through this plugin's own Settings dialog: the Assets
# tab replaces an icon's shape/file, the Colors tab replaces a tint - see
# src/backend/PluginManager/PluginSettings/ in the parent StreamController checkout for how
# that system works. We still apply the Color ourselves on render (see paste_asset_icon below);
# the framework has no concept of "tint this icon based on live state" built in.
ICON_SHUFFLE = "shuffle_icon"
ICON_REPEAT = "repeat_icon"
ICON_REPEAT_ONE = "repeat_one_icon"
ICON_THUMB_UP = "thumb_up_icon"
ICON_THUMB_DOWN = "thumb_down_icon"
ICON_VOLUME_UP = "volume_up_icon"
ICON_VOLUME_DOWN = "volume_down_icon"
ICON_PAUSE = "pause_icon"

COLOR_SHUFFLE = "shuffle_color"
COLOR_REPEAT_ON = "repeat_on_color"
COLOR_REPEAT_OFF = "repeat_off_color"
COLOR_LIKE = "like_color"
COLOR_DISLIKE = "dislike_color"
COLOR_NEUTRAL = "neutral_color"
COLOR_VOLUME_UP = "volume_up_color"
COLOR_VOLUME_DOWN = "volume_down_color"
COLOR_PAUSE_ICON = "pause_icon_color"
COLOR_PAUSE_DIM = "pause_dim_color"

# Filenames are relative to assets/icons/material/ (the plugin's bundled Material Icons - see
# assets/icons/material/NOTICE.md for attribution). main.py registers these as the default
# Icon asset for each key above via PluginBase.add_icon().
#
# Pre-rendered PNGs, not the .svg sources directly: StreamController's own SVG loading path
# (MediaManager.generate_svg_thumbnail -> HelperMethods.svg_to_pil) calls
# `svg_to_pil(path, 1024)` - only the width positional arg, so height stays at that function's
# default of 96, rasterizing every SVG icon asset into a squashed 1024x96 canvas. Registering
# our own correctly-square 512x512 PNGs (rendered once from the same .svg files, kept alongside
# them) sidesteps that core bug entirely via the framework's plain-raster-image path instead.
ICON_ASSET_DEFAULTS = {
    ICON_SHUFFLE: "shuffle.png",
    ICON_REPEAT: "repeat.png",
    ICON_REPEAT_ONE: "repeat_one.png",
    ICON_THUMB_UP: "thumb_up.png",
    ICON_THUMB_DOWN: "thumb_down.png",
    ICON_VOLUME_UP: "volume_up.png",
    ICON_VOLUME_DOWN: "volume_down.png",
    ICON_PAUSE: "pause.png",
}

# main.py registers these as the default Color asset for each key above via PluginBase.add_color().
COLOR_ASSET_DEFAULTS = {
    COLOR_SHUFFLE: (200, 200, 200, 255),
    COLOR_REPEAT_ON: (0, 200, 83, 255),
    COLOR_REPEAT_OFF: (120, 120, 120, 255),
    COLOR_LIKE: (0, 200, 83, 255),
    COLOR_DISLIKE: (220, 53, 69, 255),
    COLOR_NEUTRAL: (120, 120, 120, 255),
    COLOR_VOLUME_UP: (0, 200, 83, 255),
    COLOR_VOLUME_DOWN: (220, 53, 69, 255),
    COLOR_PAUSE_ICON: (255, 255, 255, 255),
    COLOR_PAUSE_DIM: (0, 0, 0, 140),
}


class YTMDActionMixin:
    # Per-action "last rendered X" bookkeeping that on_ytmd_state() uses to skip redundant
    # hardware writes. on_ready() is the framework's redraw entry point (on_update() calls
    # it) and runs again on every page (re)load - and the core clears the input's image
    # just before that call, so a re-entry has to repaint from scratch. Nulling these lets
    # the replayed state pass fall through its change checks and do a full redraw. Cached
    # art (_art_image/_raw_art_image) is deliberately kept - the replay re-pushes it via a
    # cache hit rather than re-fetching.
    _RENDER_CACHE_ATTRS = (
        "_last_rendered_labels", "_last_track_key", "_last_video_id", "_last_preview_key",
        "_last_like_status", "_last_repeat_mode", "_last_displayed", "_last_progress_px",
        "_last_paused",
    )

    def _reset_render_cache(self) -> None:
        for attr in self._RENDER_CACHE_ATTRS:
            if hasattr(self, attr):
                setattr(self, attr, None)

    def on_ready(self) -> None:
        # Idempotent: subscribing unconditionally would leak a StateStore subscription on
        # every page revisit. on_disconnect() nulls the token so a genuine teardown/re-ready
        # still re-subscribes.
        if getattr(self, "_state_token", None) is None:
            self._state_token = self.plugin_base.state_store.subscribe_state(self.on_ytmd_state)
        self._reset_render_cache()
        latest = self.plugin_base.state_store.get_latest()
        if latest is not None:
            self.on_ytmd_state(latest)

    def on_disconnect(self) -> None:
        token = getattr(self, "_state_token", None)
        if token is not None:
            self.plugin_base.state_store.unsubscribe_state(token)
            self._state_token = None

    def on_ytmd_state(self, state: dict) -> None:
        """Override in the concrete action. Always invoked on the GTK main thread - StateStore
        marshals its fan-out through GLib.idle_add and the on_ready() replay is already
        main-thread - so the per-action bookkeeping here needs no locking. set_media()/
        set_label() may be called directly; self.ui() stays harmless if used."""

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
        _profiling.incr("ui_push")
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

    # --- icon/color assets (Settings > Assets/Colors - see the constants above) ---------------

    def get_asset_icon_image(self, icon_key: str, size: int) -> Image.Image | None:
        """Rasterized image for an Icon asset, resized to `size`x`size`. Only its alpha channel
        matters to callers (see paste_asset_icon) - whatever shape the user has picked for this
        key, via this plugin's Settings > Assets tab, gets recolored the same way our bundled
        default does."""
        values = self.plugin_base.asset_manager.icons.get_asset_values(icon_key)
        if not values:
            return None
        _, rendered = values
        if rendered is None:
            return None
        return rendered.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")

    def get_asset_color(self, color_key: str, fallback: tuple[int, int, int, int] = (255, 255, 255, 255)) -> tuple[int, int, int, int]:
        """Color for a Color asset, user-overridable through this plugin's Settings > Colors
        tab. `fallback` is only a last resort - every key above is always registered with a
        default in main.py, so this should never actually be hit in practice."""
        color = self.plugin_base.asset_manager.colors.get_asset_values(color_key)
        return color if color is not None else fallback

    def paste_asset_icon(
        self, canvas: Image.Image, icon_key: str, color_key: str,
        box: tuple[float, float, float, float], margin_fraction: float = 0.15,
    ) -> None:
        """Pastes an Icon asset, square and centered with a margin, into `box` (x0, y0, x1, y1)
        on `canvas`, tinted with a Color asset - the icon's own alpha is used as a mask, so any
        shape the user swaps in gets recolored the same way our bundled Material Icons do."""
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        size = round(min(w, h) * (1 - margin_fraction * 2))
        if size <= 0:
            return
        base = self.get_asset_icon_image(icon_key, size)
        if base is None:
            return
        r, g, b, a = self.get_asset_color(color_key)
        colored = Image.new("RGBA", base.size, (r, g, b, 0))
        alpha = base.getchannel("A")
        if a != 255:
            alpha = alpha.point(lambda v: v * a // 255)
        colored.putalpha(alpha)
        px, py = round(x0 + (w - size) / 2), round(y0 + (h - size) / 2)
        canvas.paste(colored, (px, py), colored)

    def apply_pause_overlay(self, image: Image.Image) -> Image.Image:
        """If playback is currently paused (per the shared PlaybackState singleton - see
        internal/playback_state.py), returns a dimmed copy of `image` with a centered pause
        icon; otherwise returns `image` unchanged. The dim is needed for the icon to stay
        legible over arbitrary album art, including light/white covers."""
        if not self.plugin_base.playback_state.is_paused():
            return image
        width, height = image.size
        dim_color = self.get_asset_color(COLOR_PAUSE_DIM, COLOR_ASSET_DEFAULTS[COLOR_PAUSE_DIM])
        dimmed = Image.alpha_composite(image, Image.new("RGBA", (width, height), dim_color))
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        self.paste_asset_icon(overlay, ICON_PAUSE, COLOR_PAUSE_ICON, (0, 0, width, height), margin_fraction=0.3)
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
