from PIL import Image, ImageDraw

from src.backend.PluginManager.InputBases import KeyAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.ComboRow import ComboRow

from ..common.ytmd_action_base import YTMDActionMixin

ICON_CHOICES = ["both", "up", "down"]

UP_COLOR = (0, 200, 83, 255)
DOWN_COLOR = (220, 53, 69, 255)


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
        self._last_volume = None

        self.add_event_assigner(EventAssigner(
            id="Volume Up", ui_label="Volume Up",
            default_events=[Input.Key.Events.DOWN], callback=self._do_volume_up,
        ))
        self.add_event_assigner(EventAssigner(
            id="Volume Down", ui_label="Volume Down",
            default_events=[Input.Key.Events.HOLD_START], callback=self._do_volume_down,
        ))

    def on_ready(self) -> None:
        self._render()
        super().on_ready()

    def _on_icon_setting_changed(self, widget, new_value, old_value) -> None:
        self._render()

    def on_ytmd_state(self, state: dict) -> None:
        # state-update fires several times a second during playback (progress ticks) - only
        # touch the hardware when the volume actually changed.
        volume = (state.get("player") or {}).get("volume")
        if volume is None or volume == self._last_volume:
            return
        self._last_volume = volume
        self.ui(self.set_center_label, f"{volume}%")

    def _do_volume_up(self, data=None) -> None:
        self._step_volume(int(self.step_row.get_value(fallback=10)))

    def _do_volume_down(self, data=None) -> None:
        self._step_volume(-int(self.step_row.get_value(fallback=10)))

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
        draw = ImageDraw.Draw(image)

        mode = self.icon_display_row.get_value(fallback="both")
        if mode == "both":
            half = height // 2
            self._draw_volume_icon(draw, 0, 0, width, half, pointing_up=True, color=UP_COLOR)
            self._draw_volume_icon(draw, 0, half, width, height, pointing_up=False, color=DOWN_COLOR)
        elif mode == "up":
            self._draw_volume_icon(draw, 0, 0, width, height, pointing_up=True, color=UP_COLOR)
        else:
            self._draw_volume_icon(draw, 0, 0, width, height, pointing_up=False, color=DOWN_COLOR)

        self.ui(self.set_media, image=image, size=1.0)

    # TODO: this is a hand-drawn PIL approximation (speaker box + cone + chevron), not real
    # icon artwork - replace with a proper volume-up/down glyph (e.g. bundled SVG/PNG assets)
    # when available.
    @staticmethod
    def _draw_volume_icon(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, pointing_up: bool, color) -> None:
        margin_x = (x1 - x0) * 0.15
        margin_y = (y1 - y0) * 0.22
        x0, x1 = x0 + margin_x, x1 - margin_x
        y0, y1 = y0 + margin_y, y1 - margin_y
        width = x1 - x0
        height = y1 - y0
        cy = (y0 + y1) / 2

        speaker_w = width * 0.5
        rect_w = speaker_w * 0.32
        rect_h = height * 0.36
        draw.rectangle([x0, cy - rect_h / 2, x0 + rect_w, cy + rect_h / 2], fill=color)
        # Trapezoid cone: narrow at the box, wide at the open end.
        draw.polygon(
            [
                (x0 + rect_w, cy - rect_h / 2),
                (x0 + rect_w, cy + rect_h / 2),
                (x0 + speaker_w, cy + height * 0.42),
                (x0 + speaker_w, cy - height * 0.42),
            ],
            fill=color,
        )

        chevron_x0 = x0 + width * 0.68
        chevron_w = x1 - chevron_x0
        thickness = height * 0.16
        if pointing_up:
            draw.polygon(
                [
                    (chevron_x0, cy + height * 0.15),
                    (chevron_x0 + chevron_w / 2, cy - height * 0.32),
                    (chevron_x0 + chevron_w, cy + height * 0.15),
                    (chevron_x0 + chevron_w, cy + height * 0.15 - thickness * 0.3),
                    (chevron_x0 + chevron_w / 2, cy - height * 0.32 + thickness),
                    (chevron_x0, cy + height * 0.15 - thickness * 0.3),
                ],
                fill=color,
            )
        else:
            draw.polygon(
                [
                    (chevron_x0, cy - height * 0.15),
                    (chevron_x0 + chevron_w / 2, cy + height * 0.32),
                    (chevron_x0 + chevron_w, cy - height * 0.15),
                    (chevron_x0 + chevron_w, cy - height * 0.15 + thickness * 0.3),
                    (chevron_x0 + chevron_w / 2, cy + height * 0.32 - thickness),
                    (chevron_x0, cy - height * 0.15 + thickness * 0.3),
                ],
                fill=color,
            )
