import time

from PIL import Image

from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.ComboRow import ComboRow

from ..common.ytmd_action_base import (
    YTMDKeyAction,
    ICON_VOLUME_UP, ICON_VOLUME_DOWN,
    COLOR_VOLUME_UP, COLOR_VOLUME_DOWN,
)

ICON_CHOICES = ["both", "up", "down", "none"]

# YTMD's `setVolume` command takes an integer percentage. Range per the Companion Server
# API v1 (POST /command, "setVolume"):
# https://github.com/ytmdesktop/ytmdesktop/wiki/v2-%E2%80%90-Companion-Server-API-v1
# Kept as named constants so that if YTMD ever raises the ceiling (goes to eleven), this is
# the only line to touch - the Set Volume spin row and the clamps below both read it.
VOLUME_MIN = 0
VOLUME_MAX = 100
DEFAULT_SET_VOLUME = 50

# After a local volume change, ignore the volume reported by state-updates for this long:
# YTMD's echo of the change lags the command, so adopting it would stomp a fast sequence of
# presses back to a stale level. Mute is unaffected (its echo is effectively immediate).
LOCAL_GRACE_SECONDS = 0.5

SET_VOLUME_ID = "Set Volume"


class VolumeControl(YTMDKeyAction):
    """Volume Up, Volume Down, Mute Toggle and Set Volume are separately assignable functions
    (Event Assigner), so one key can do several - e.g. short press = up, hold = down. 'Icon
    Display' picks whether this key shows both direction icons, or just one - place it twice
    for dedicated up/down keys, same pattern as Thumbs Up/Down.

    Set Volume jumps straight to the level configured in 'Set Volume Level' (bounded to what
    YTMD accepts). That row is only shown while Set Volume is bound to a gesture.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, default_events=False, **kwargs)
        self.step_row = SpinRow(
            self, "step", 10, min=1, max=100, step=1, digits=0, title="Step"
        )
        self.icon_display_row = ComboRow(
            self, "icon_display", "both", items=ICON_CHOICES, title="Icon Display",
            on_change=self._on_icon_setting_changed,
        )
        self.set_volume_target_row = SpinRow(
            self, "set_volume_target", DEFAULT_SET_VOLUME,
            min=VOLUME_MIN, max=VOLUME_MAX, step=1, digits=0,
            title="Set Volume Level",
            subtitle="Level the 'Set Volume' function jumps to",
        )
        # Hidden until the user binds Set Volume to a gesture - see _refresh_set_volume_row().
        self.set_volume_target_row.widget.set_visible(False)

        self._last_displayed = None
        # Local authoritative level so rapid presses accumulate instead of every press
        # re-reading the same not-yet-updated shared value. Kept in step with the shared
        # VolumeState by on_ytmd_state() outside the post-change grace window.
        self._volume = self.plugin_base.volume_state.get_volume()
        self._local_change_until = 0.0

        self.add_event_assigner(EventAssigner(
            id="Volume Up", ui_label="Volume Up",
            default_events=[Input.Key.Events.DOWN], callback=self._do_volume_up,
        ))
        self.add_event_assigner(EventAssigner(
            id="Volume Down", ui_label="Volume Down",
            default_events=[Input.Key.Events.HOLD_START], callback=self._do_volume_down,
        ))
        self.add_event_assigner(EventAssigner(
            id="Mute Toggle", ui_label="Mute Toggle", callback=self._do_mute_toggle,
        ))
        self.add_event_assigner(EventAssigner(
            id=SET_VOLUME_ID, ui_label="Set Volume", callback=self._do_set_volume,
        ))

    def load_event_overrides(self):
        # Framework calls this once on load and again on every Event Assigner dropdown change,
        # so it doubles as our "assignments changed" hook for showing/hiding the level row.
        super().load_event_overrides()
        self._refresh_set_volume_row()

    def on_ready(self) -> None:
        self._render()
        self._refresh_set_volume_row()
        super().on_ready()

    def _refresh_set_volume_row(self) -> None:
        row = getattr(self, "set_volume_target_row", None)
        if row is None or row.widget is None:
            return
        row.widget.set_visible(self._is_set_volume_assigned())

    def _is_set_volume_assigned(self) -> bool:
        try:
            assigners = self.event_manager.get_event_map().values()
        except Exception:
            return False
        return any(a is not None and a.id == SET_VOLUME_ID for a in assigners)

    def _on_icon_setting_changed(self, widget, new_value, old_value) -> None:
        self._render()

    def on_ytmd_state(self, state: dict) -> None:
        # state-update fires several times a second during playback (progress ticks) - only
        # touch the hardware when the displayed value actually changed. Adopt the shared
        # volume unless a local change is still settling (see LOCAL_GRACE_SECONDS).
        if time.monotonic() >= self._local_change_until:
            self._volume = self.plugin_base.volume_state.get_volume()
        self._update_label()

    def _update_label(self) -> None:
        # self._volume is the local authoritative level (kept in step with the shared
        # VolumeState by on_ytmd_state); mute comes straight from the shared state. Using both
        # keeps this label in agreement with DialControl and any other volume display -
        # including reflecting mute, which `player.volume` alone doesn't.
        volume = self._volume
        muted = self.plugin_base.volume_state.get_muted()
        if (volume, muted) == self._last_displayed:
            return
        self._last_displayed = (volume, muted)
        self.push_center_label("Muted" if muted else f"{volume}%")

    def _do_volume_up(self, data=None) -> None:
        self._step_volume(int(self.step_row.get_value(fallback=10)))

    def _do_volume_down(self, data=None) -> None:
        self._step_volume(-int(self.step_row.get_value(fallback=10)))

    def _do_mute_toggle(self, data=None) -> None:
        muted = not self.plugin_base.volume_state.get_muted()
        self.plugin_base.volume_state.set_muted(muted)
        self.send_command("mute" if muted else "unmute")
        self._update_label()

    def _do_set_volume(self, data=None) -> None:
        self._apply_volume(self.set_volume_target_row.get_value(fallback=DEFAULT_SET_VOLUME))

    def _step_volume(self, delta: int) -> None:
        self._apply_volume(self._volume + delta)

    def _apply_volume(self, volume) -> None:
        # Single clamp point for every path - the SpinRow already bounds the UI, this also
        # covers a hand-edited page json and keeps a future caller from sending YTMD a value
        # outside what it accepts.
        volume = max(VOLUME_MIN, min(VOLUME_MAX, int(volume)))
        self._volume = volume
        self.plugin_base.volume_state.set_volume(volume)
        self._local_change_until = time.monotonic() + LOCAL_GRACE_SECONDS
        self.send_command("setVolume", volume)
        self._update_label()

    # --- rendering -----------------------------------------------------------

    def _render(self) -> None:
        width, height = self.get_display_size()
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        mode = self.icon_display_row.get_value(fallback="both")
        if mode == "both":
            half = height // 2
            self.paste_asset_icon(image, ICON_VOLUME_UP, COLOR_VOLUME_UP, (0, 0, width, half))
            self.paste_asset_icon(image, ICON_VOLUME_DOWN, COLOR_VOLUME_DOWN, (0, half, width, height))
        elif mode == "up":
            self.paste_asset_icon(image, ICON_VOLUME_UP, COLOR_VOLUME_UP, (0, 0, width, height))
        elif mode == "down":
            self.paste_asset_icon(image, ICON_VOLUME_DOWN, COLOR_VOLUME_DOWN, (0, 0, width, height))
        # mode == "none": leave the image fully transparent - just the volume % label shows

        self.push_media(image=image, size=1.0)
