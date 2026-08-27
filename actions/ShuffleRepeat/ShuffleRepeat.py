from PIL import Image

from src.backend.PluginManager.InputBases import KeyAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input

from ..common.ytmd_action_base import YTMDActionMixin, paste_material_icon

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

        half = height // 2
        paste_material_icon(image, "shuffle", (0, 0, width, half), SHUFFLE_COLOR)

        repeat_color = REPEAT_ON_COLOR if repeat_mode in (REPEAT_ALL, REPEAT_ONE) else REPEAT_OFF_COLOR
        repeat_icon = "repeat_one" if repeat_mode == REPEAT_ONE else "repeat"
        paste_material_icon(image, repeat_icon, (0, half, width, height), repeat_color)

        self.ui(self.set_media, image=image, size=1.0)
