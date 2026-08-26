import threading

from PIL import Image, ImageDraw

from src.backend.PluginManager.InputBases import DialAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.ColorButtonRow import ColorButtonRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow

from ..common.ytmd_action_base import YTMDActionMixin

STEP = 2
# YTMD rate-limits /command; a fast spin of the dial fires one turn event per detent, so the
# actual setVolume call is debounced - only sent once turning pauses for this long - while the
# bar still redraws instantly on every detent for responsive visual feedback.
DEBOUNCE_SECONDS = 0.25
# How long the volume bar stays visible after a change, in "auto" bar mode.
AUTO_HIDE_SECONDS = 2.0

BACKGROUND_COLOR = (20, 20, 20, 255)
MUTED_BAR_COLOR = (120, 120, 120)
DEFAULT_BAR_COLOR = (0, 200, 83, 255)
FALLBACK_SIZE = (200, 100)


class DialControl(YTMDActionMixin, DialAction):
    """A single dial action exposing every YTMD function as an assignable event, so any
    physical gesture (press, hold, touchscreen tap, turn) can be bound to any function via
    the Event Assigner UI - e.g. knob press -> Play/Pause, screen press -> Mute."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, default_events=False, **kwargs)

        self._volume = 50
        self._muted = False
        self._show_bar = False
        self._pending_send_timer = None
        self._hide_bar_timer = None
        self._art_image = None
        self._raw_art_image = None
        self._last_video_id = None
        self._latest_state = None
        self._last_progress_px = None

        self.setup_label_rows(on_change=self._on_bar_setting_changed)
        self.setup_progress_rows(on_change=self._on_bar_setting_changed)

        self.bar_mode_row = ComboRow(
            self, "bar_mode", "auto", items=["auto", "always"], title="Volume Bar",
            subtitle="auto = show briefly after a change, always = always overpainted on the art",
            on_change=self._on_bar_mode_changed,
        )
        self.bar_width_row = SpinRow(
            self, "bar_width", 25, min=5, max=75, step=5, digits=0, title="Bar Width (%)",
            on_change=self._on_bar_setting_changed,
        )
        self.bar_opacity_row = SpinRow(
            self, "bar_opacity", 100, min=10, max=100, step=5, digits=0, title="Bar Opacity (%)",
            on_change=self._on_bar_setting_changed,
        )
        self.bar_color_row = ColorButtonRow(
            self, "bar_color", DEFAULT_BAR_COLOR, title="Bar Color",
            on_change=self._on_bar_setting_changed,
        )

        self.art_aspect_row = SwitchRow(
            self, "art_maintain_aspect", False, title="Maintain Aspect Ratio",
            subtitle="Off stretches the art to fill the dial; on shrinks it to fit instead",
            on_change=self._on_art_setting_changed,
        )
        self.art_h_align_row = ComboRow(
            self, "art_h_align", "center", items=["left", "center", "right"],
            title="Art Horizontal Position", on_change=self._on_art_setting_changed,
        )
        self.art_v_align_row = ComboRow(
            self, "art_v_align", "center", items=["top", "center", "bottom"],
            title="Art Vertical Position", on_change=self._on_art_setting_changed,
        )

        self.add_event_assigner(EventAssigner(
            id="Play/Pause", ui_label="Play/Pause",
            default_events=[Input.Dial.Events.DOWN], callback=self._do_play_pause,
        ))
        self.add_event_assigner(EventAssigner(
            id="Mute Toggle", ui_label="Mute Toggle",
            default_events=[Input.Dial.Events.SHORT_TOUCH_PRESS], callback=self._do_mute_toggle,
        ))
        self.add_event_assigner(EventAssigner(
            id="Next Track", ui_label="Next Track",
            default_events=[Input.Dial.Events.SHORT_UP], callback=self._do_next,
        ))
        self.add_event_assigner(EventAssigner(
            id="Previous Track", ui_label="Previous Track",
            default_events=[Input.Dial.Events.HOLD_START], callback=self._do_previous,
        ))
        self.add_event_assigner(EventAssigner(
            id="Volume Up", ui_label="Volume Up",
            default_events=[Input.Dial.Events.TURN_CW], callback=self._do_volume_up,
        ))
        self.add_event_assigner(EventAssigner(
            id="Volume Down", ui_label="Volume Down",
            default_events=[Input.Dial.Events.TURN_CCW], callback=self._do_volume_down,
        ))

    def on_ready(self) -> None:
        self._redraw()
        super().on_ready()

    def _on_bar_setting_changed(self, widget, new_value, old_value) -> None:
        """GenerativeUI's on_change - redraw immediately so a settings change (width/opacity/
        color/art fit/position) is visible without waiting for a volume event."""
        self._redraw()

    def _on_bar_mode_changed(self, widget, new_value, old_value) -> None:
        if new_value == "auto":
            # _show_bar is sticky (only auto-hide's timer clears it), and while in "always"
            # mode that timer is never scheduled - so switching to "auto" could otherwise
            # leave a stale True in place, and the bar wouldn't disappear until the next
            # actual volume/mute event happened to schedule the hide timer for the first time.
            self._cancel_hide_bar()
            self._show_bar = False
        self._redraw()

    def _on_art_setting_changed(self, widget, new_value, old_value) -> None:
        # Re-fit from the already-downloaded raw image - no need to re-fetch over the network.
        self._apply_art_fit()

    def on_disconnect(self) -> None:
        self._cancel_pending_send()
        self._cancel_hide_bar()
        super().on_disconnect()

    def on_ytmd_state(self, state: dict) -> None:
        # state-update fires several times a second during playback (progress ticks) - only
        # touch the hardware (and never block this thread on a thumbnail download) when
        # something actually displayed here changed. The progress bar is the one thing that
        # legitimately needs to redraw every tick, and only does so when actually enabled.
        self._latest_state = state

        player = state.get("player") or {}
        muted = player.get("muted", self._muted)
        muted_changed = muted != self._muted

        volume_changed = False
        # Two reasons to not blindly adopt the server-reported volume:
        # 1. YTMD reports volume as 0 while muted, which would clobber the pre-mute level
        #    we need to restore to once the dial is turned again.
        # 2. While a local turn is still debounced (not sent yet), an update reflecting an
        #    older value would stomp the in-progress local change out from under the user.
        if not muted and self._pending_send_timer is None:
            new_volume = player.get("volume", self._volume)
            volume_changed = new_volume != self._volume
            self._volume = new_volume
        self._muted = muted

        track_id = self.video_id(state)
        track_changed = track_id != self._last_video_id
        if track_changed:
            self._last_video_id = track_id
            self.render_chosen_labels(state, force=True)
            self.request_thumbnail(state, self._on_thumbnail)

        progress_changed = False
        if self.progress_enabled():
            # The bar is only ever a few dozen pixels wide - redrawing (and pushing a full
            # image to the deck's render queue) on every tick when the fill wouldn't even
            # move a pixel is exactly what saturates the deck's render loop and trips its
            # low-FPS warning. Only redraw when the actual filled pixel width changes.
            width, _ = self.get_display_size(fallback=FALLBACK_SIZE)
            px = round(width * self.progress_fraction(state))
            progress_changed = px != self._last_progress_px
            self._last_progress_px = px

        if muted_changed or volume_changed or progress_changed:
            self._redraw()

    def _on_thumbnail(self, image) -> None:
        self._raw_art_image = image
        self._last_progress_px = None
        self._apply_art_fit()

    def _apply_art_fit(self) -> None:
        if self._raw_art_image is None:
            self._art_image = None
            self._redraw()
            return

        width, height = self.get_display_size(fallback=FALLBACK_SIZE)
        source = self._raw_art_image.convert("RGBA")

        if not self.art_aspect_row.get_value(fallback=False):
            self._art_image = source.resize((width, height))
            self._redraw()
            return

        src_w, src_h = source.size
        scale = min(width / src_w, height / src_h)
        fitted = source.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))))

        h_align = self.art_h_align_row.get_value(fallback="center")
        v_align = self.art_v_align_row.get_value(fallback="center")
        x = {"left": 0, "center": (width - fitted.width) // 2, "right": width - fitted.width}[h_align]
        y = {"top": 0, "center": (height - fitted.height) // 2, "bottom": height - fitted.height}[v_align]

        # Transparent padding around the shrunk art - the framework composites this action's
        # image over the dial's own background, so this reads as normal letterboxing.
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(fitted, (x, y), fitted)
        self._art_image = canvas
        self._redraw()

    # --- assignable functions -------------------------------------------------

    def _do_play_pause(self, data=None) -> None:
        self.send_command("playPause")

    def _do_mute_toggle(self, data=None) -> None:
        self._muted = not self._muted
        self.send_command("mute" if self._muted else "unmute")
        self._flash_bar()

    def _do_next(self, data=None) -> None:
        self.send_command("next")

    def _do_previous(self, data=None) -> None:
        self.send_command("previous")

    def _do_volume_up(self, data=None) -> None:
        self._adjust_volume(STEP)

    def _do_volume_down(self, data=None) -> None:
        self._adjust_volume(-STEP)

    def _adjust_volume(self, delta: int) -> None:
        # Adjusting volume always means "I want sound" - unmute rather than silently
        # adjusting a level the user can't hear.
        if self._muted:
            self._muted = False
            self.send_command("unmute")

        self._volume = max(0, min(100, self._volume + delta))
        self._flash_bar()
        self._schedule_send_volume()

    # --- debounced volume send --------------------------------------------------

    def _schedule_send_volume(self) -> None:
        self._cancel_pending_send()
        self._pending_send_timer = threading.Timer(DEBOUNCE_SECONDS, self._send_volume)
        self._pending_send_timer.daemon = True
        self._pending_send_timer.start()

    def _send_volume(self) -> None:
        self._pending_send_timer = None
        self.send_command("setVolume", self._volume)

    def _cancel_pending_send(self) -> None:
        if self._pending_send_timer is not None:
            self._pending_send_timer.cancel()
            self._pending_send_timer = None

    # --- volume bar visibility ---------------------------------------------------

    def _flash_bar(self) -> None:
        self._show_bar = True
        self._redraw()

        if self.bar_mode_row.get_value(fallback="auto") != "auto":
            self._cancel_hide_bar()
            return

        self._cancel_hide_bar()
        self._hide_bar_timer = threading.Timer(AUTO_HIDE_SECONDS, self._hide_bar)
        self._hide_bar_timer.daemon = True
        self._hide_bar_timer.start()

    def _hide_bar(self) -> None:
        self._hide_bar_timer = None
        self._show_bar = False
        self._redraw()

    def _cancel_hide_bar(self) -> None:
        if self._hide_bar_timer is not None:
            self._hide_bar_timer.cancel()
            self._hide_bar_timer = None

    # --- rendering -----------------------------------------------------------

    def _redraw(self) -> None:
        # No I/O here - the art is only ever (re)fetched from _on_thumbnail() when the track
        # changes; this just composites whatever's already cached plus the volume bar.
        width, height = self.get_display_size(fallback=FALLBACK_SIZE)

        if self._art_image is not None:
            image = self._art_image.copy()
        else:
            image = Image.new("RGBA", (width, height), BACKGROUND_COLOR)

        # Draw onto a fully transparent overlay and alpha-composite it in, rather than
        # ImageDraw.rectangle() straight onto `image` - that would overwrite pixels (including
        # alpha) instead of blending, and would need an opaque backing behind the unfilled
        # portion to look right. This way the unfilled portion stays exactly the art
        # underneath, with no backing box at all.
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # The progress bar spans the full width at the bottom; the volume bar (on the right)
        # only gets whatever vertical space is left above it, so the two never overlap.
        volume_area_height = height
        if self.progress_enabled():
            fraction = self.progress_fraction(self._latest_state) if self._latest_state else 0.0
            progress_height = self.draw_progress_bar(draw, width, height, fraction)
            volume_area_height = max(0, height - progress_height)

        bar_mode = self.bar_mode_row.get_value(fallback="auto")
        if self._show_bar or bar_mode == "always":
            bar_width = int(width * (self.bar_width_row.get_value(fallback=25) / 100))
            opacity = round(255 * (self.bar_opacity_row.get_value(fallback=100) / 100))
            rgb = MUTED_BAR_COLOR if self._muted else tuple(self.bar_color_row.get_value(fallback=DEFAULT_BAR_COLOR))[:3]
            bar_color = (*rgb, opacity)
            bar_left = width - bar_width
            fill_height = int(volume_area_height * (self._volume / 100))

            draw.rectangle([bar_left, volume_area_height - fill_height, width, volume_area_height], fill=bar_color)

        image = Image.alpha_composite(image, overlay)
        self.ui(self.set_media, image=image, size=1.0)
