import math

from PIL import Image, ImageDraw, ImageFont

from src.backend.PluginManager.InputBases import KeyAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

from ..common.ytmd_action_base import YTMDActionMixin

# YTMD's repeatMode command: 0=off, 1=repeat whole queue, 2=repeat current track.
# See https://github.com/XeroxDev/ytmdesktop-ts-companion/blob/main/src/enums/repeat-mode.ts
REPEAT_NONE = 0
REPEAT_ALL = 1
REPEAT_ONE = 2
REPEAT_SEQUENCE = [REPEAT_NONE, REPEAT_ALL, REPEAT_ONE]

SHUFFLE_COLOR = (200, 200, 200, 255)
REPEAT_OFF_COLOR = (120, 120, 120, 255)
REPEAT_ON_COLOR = (0, 200, 83, 255)


class ShuffleRepeat(YTMDActionMixin, KeyAction):
    """Toggle Shuffle, Repeat On/Single/Off, and Cycle Repeat are separately assignable
    (Event Assigner) - assign a specific repeat function to a gesture for direct control,
    or Cycle Repeat to step through Off -> All -> One -> Off on a single gesture.

    YTMD never reports whether shuffle is on or off (and the API is a bare toggle, not a
    set-explicit-state command), so shuffle has no reliable status to show - its icon is
    static. Repeat mode IS reported (queue.repeatMode), so its icon reflects the real state.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, default_events=False, **kwargs)
        self._last_repeat_mode = None

        self.add_event_assigner(EventAssigner(
            id="Toggle Shuffle", ui_label="Toggle Shuffle",
            default_events=[Input.Key.Events.DOWN], callback=self._do_toggle_shuffle,
        ))
        self.add_event_assigner(EventAssigner(
            id="Cycle Repeat", ui_label="Cycle Repeat",
            default_events=[Input.Key.Events.HOLD_START], callback=self._do_cycle_repeat,
        ))
        self.add_event_assigner(EventAssigner(id="Repeat On", ui_label="Repeat On", callback=self._do_repeat_on))
        self.add_event_assigner(EventAssigner(id="Repeat Single", ui_label="Repeat Single", callback=self._do_repeat_single))
        self.add_event_assigner(EventAssigner(id="Repeat Off", ui_label="Repeat Off", callback=self._do_repeat_off))

    def on_ready(self) -> None:
        self._render(None)
        super().on_ready()

    def on_ytmd_state(self, state: dict) -> None:
        repeat_mode = ((state.get("player") or {}).get("queue") or {}).get("repeatMode")
        if repeat_mode == self._last_repeat_mode:
            return
        self._last_repeat_mode = repeat_mode
        self._render(repeat_mode)

    # --- assignable functions -------------------------------------------------

    def _do_toggle_shuffle(self, data=None) -> None:
        self.send_command("shuffle")

    def _do_repeat_on(self, data=None) -> None:
        self.send_command("repeatMode", REPEAT_ALL)

    def _do_repeat_single(self, data=None) -> None:
        self.send_command("repeatMode", REPEAT_ONE)

    def _do_repeat_off(self, data=None) -> None:
        self.send_command("repeatMode", REPEAT_NONE)

    def _do_cycle_repeat(self, data=None) -> None:
        state = self.plugin_base.state_store.get_latest() or {}
        current = ((state.get("player") or {}).get("queue") or {}).get("repeatMode", REPEAT_NONE)
        try:
            index = REPEAT_SEQUENCE.index(current)
        except ValueError:
            index = -1
        next_mode = REPEAT_SEQUENCE[(index + 1) % len(REPEAT_SEQUENCE)]
        self.send_command("repeatMode", next_mode)

    # --- rendering -----------------------------------------------------------

    def _render(self, repeat_mode) -> None:
        width, height = self.get_display_size()
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        half = height // 2
        self._draw_shuffle_icon(draw, width, 0, half)
        self._draw_repeat_icon(draw, width, half, height, repeat_mode)

        self.ui(self.set_media, image=image, size=1.0)

    # TODO: these are hand-drawn PIL primitives (lines/arcs), not real icon artwork - replace
    # with proper shuffle/repeat glyphs (e.g. bundled SVG/PNG assets) when available.
    @staticmethod
    def _draw_shuffle_icon(draw: ImageDraw.ImageDraw, width: int, top: int, bottom: int) -> None:
        margin_x = width * 0.22
        margin_y = (bottom - top) * 0.25
        x0, x1 = margin_x, width - margin_x
        y0, y1 = top + margin_y, bottom - margin_y
        line_width = max(2, round(width * 0.035))

        draw.line([(x0, y0), (x1, y1)], fill=SHUFFLE_COLOR, width=line_width)
        draw.line([(x0, y1), (x1, y0)], fill=SHUFFLE_COLOR, width=line_width)

    @staticmethod
    def _draw_repeat_icon(draw: ImageDraw.ImageDraw, width: int, top: int, bottom: int, repeat_mode) -> None:
        margin_x = width * 0.25
        margin_y = (bottom - top) * 0.15
        x0, x1 = margin_x, width - margin_x
        y0, y1 = top + margin_y, bottom - margin_y

        color = REPEAT_ON_COLOR if repeat_mode in (REPEAT_ALL, REPEAT_ONE) else REPEAT_OFF_COLOR
        line_width = max(2, round(width * 0.035))

        draw.arc([x0, y0, x1, y1], start=20, end=340, fill=color, width=line_width)

        # Arrowhead where the arc starts, so the loop reads as directional.
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
        angle = math.radians(20)
        tip = (cx + rx * math.cos(angle), cy + ry * math.sin(angle))
        size = width * 0.07
        draw.polygon(
            [tip, (tip[0] - size, tip[1] - size * 0.6), (tip[0] - size * 0.2, tip[1] + size * 0.6)],
            fill=color,
        )

        if repeat_mode == REPEAT_ONE:
            draw.text((cx, cy), "1", fill=color, font=ImageFont.load_default(), anchor="mm")
