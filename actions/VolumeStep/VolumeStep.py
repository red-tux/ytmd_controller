from PIL import Image

from src.backend.PluginManager.InputBases import KeyAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.ComboRow import ComboRow

from ..common.ytmd_action_base import (
    YTMDActionMixin,
    ICON_VOLUME_UP, ICON_VOLUME_DOWN,
    COLOR_VOLUME_UP, COLOR_VOLUME_DOWN,
)

ICON_CHOICES = ["both", "up", "down"]


class VolumeStep(YTMDActionMixin, KeyAction):
    """Volume Up and Volume Down are separately assignable functions (Event Assigner), so
    one key can do both - e.g. short press = up, hold = down - instead of needing one key
    per direction. 'Icon Display' picks whether this key shows both direction icons, or just
    one - place it twice for dedicated up/down keys, same pattern as Thumbs Up/Down."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, default_events=False, **kwargs)
        self.step_row = SpinRow(
            self, "step", 10, min=1, max=100, step=1, digits=0, title="Step"
        )
        self.icon_display_row = ComboRow(
            self, "icon_display", "both", items=ICON_CHOICES, title="Icon Display",
            on_change=self._on_icon_setting_changed,
        )
        self._last_displayed = None

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

    def on_ready(self) -> None:
        self._render()
        super().on_ready()

    def _on_icon_setting_changed(self, widget, new_value, old_value) -> None:
        self._render()

    def on_ytmd_state(self, state: dict) -> None:
        # state-update fires several times a second during playback (progress ticks) - only
        # touch the hardware when the displayed value actually changed.
        self._update_label()

    def _update_label(self) -> None:
        # Reads from the shared VolumeState (updated centrally in main.py before this fires)
        # rather than raw `player.volume`, so this always agrees with DialControl and any other
        # volume display - including reflecting mute, which `player.volume` alone doesn't.
        volume = self.plugin_base.volume_state.get_volume()
        muted = self.plugin_base.volume_state.get_muted()
        if (volume, muted) == self._last_displayed:
            return
        self._last_displayed = (volume, muted)
        self.ui(self.set_center_label, "Muted" if muted else f"{volume}%")

    def _do_volume_up(self, data=None) -> None:
        self._step_volume(int(self.step_row.get_value(fallback=10)))

    def _do_volume_down(self, data=None) -> None:
        self._step_volume(-int(self.step_row.get_value(fallback=10)))

    def _do_mute_toggle(self, data=None) -> None:
        muted = not self.plugin_base.volume_state.get_muted()
        self.plugin_base.volume_state.set_muted(muted)
        self.send_command("mute" if muted else "unmute")
        self._update_label()

    def _step_volume(self, delta: int) -> None:
        state = self.plugin_base.state_store.get_latest()
        if state is None:
            try:
                state = self.plugin_base.client.get_state_once()
            except Exception:
                state = {}

        current_volume = (state.get("player") or {}).get("volume", 50)
        new_volume = max(0, min(100, current_volume + delta))
        self.send_command("setVolume", new_volume)

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
        else:
            self.paste_asset_icon(image, ICON_VOLUME_DOWN, COLOR_VOLUME_DOWN, (0, 0, width, height))

        self.ui(self.set_media, image=image, size=1.0)
