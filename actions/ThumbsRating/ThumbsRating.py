from PIL import Image, ImageDraw

from src.backend.PluginManager.InputBases import KeyAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input
from GtkHelper.GenerativeUI.ComboRow import ComboRow

from ..common.ytmd_action_base import YTMDActionMixin

# YTMD's likeStatus: a real, reliable field (unlike shuffle) - see
# https://github.com/XeroxDev/ytmdesktop-ts-companion/blob/main/src/enums/like-status.ts
LIKE_DISLIKE = 0
LIKE_INDIFFERENT = 1
LIKE_LIKE = 2

ICON_CHOICES = ["both", "up", "down"]

NEUTRAL_COLOR = (120, 120, 120, 255)
LIKE_ACTIVE_COLOR = (0, 200, 83, 255)
DISLIKE_ACTIVE_COLOR = (220, 53, 69, 255)


class ThumbsRating(YTMDActionMixin, KeyAction):
    """Like and Dislike are separately assignable (Event Assigner) - e.g. press = like, hold
    = dislike, on one key. Unlike shuffle, YTMD does report the current like status, so these
    are real set-to-this-state functions (only toggling if not already in that state, since
    the underlying API commands are toggles) and the icon reflects the true current rating.

    'Icon Display' controls whether this key shows both icons stacked, or just one - so the
    same action can be placed twice, once configured for a dedicated like key and once for a
    dedicated dislike key.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, default_events=False, **kwargs)
        self._latest_state = None
        self._last_like_status = None

        self.icon_display_row = ComboRow(
            self, "icon_display", "both", items=ICON_CHOICES, title="Icon Display",
            on_change=self._on_setting_changed,
        )

        self.add_event_assigner(EventAssigner(
            id="Like", ui_label="Like",
            default_events=[Input.Key.Events.DOWN], callback=self._do_like,
        ))
        self.add_event_assigner(EventAssigner(
            id="Dislike", ui_label="Dislike",
            default_events=[Input.Key.Events.HOLD_START], callback=self._do_dislike,
        ))

    def on_ready(self) -> None:
        self._render(None)
        super().on_ready()

    def _on_setting_changed(self, widget, new_value, old_value) -> None:
        self._render(self._last_like_status)

    def on_ytmd_state(self, state: dict) -> None:
        self._latest_state = state
        like_status = self.get_video(state).get("likeStatus")
        if like_status == self._last_like_status:
            return
        self._last_like_status = like_status
        self._render(like_status)

    # --- assignable functions -------------------------------------------------

    def _do_like(self, data=None) -> None:
        # toggleLike is a toggle, not a set-to-liked command - only fire it if we're not
        # already liked, so this is idempotent rather than flipping back to indifferent.
        if self._last_like_status != LIKE_LIKE:
            self.send_command("toggleLike")

    def _do_dislike(self, data=None) -> None:
        if self._last_like_status != LIKE_DISLIKE:
            self.send_command("toggleDislike")

    # --- rendering -----------------------------------------------------------

    def _render(self, like_status) -> None:
        width, height = self.get_display_size()
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        up_color = LIKE_ACTIVE_COLOR if like_status == LIKE_LIKE else NEUTRAL_COLOR
        down_color = DISLIKE_ACTIVE_COLOR if like_status == LIKE_DISLIKE else NEUTRAL_COLOR

        mode = self.icon_display_row.get_value(fallback="both")
        if mode == "both":
            half = height // 2
            self._draw_thumb(draw, 0, 0, width, half, pointing_up=True, color=up_color)
            self._draw_thumb(draw, 0, half, width, height, pointing_up=False, color=down_color)
        elif mode == "up":
            self._draw_thumb(draw, 0, 0, width, height, pointing_up=True, color=up_color)
        else:
            self._draw_thumb(draw, 0, 0, width, height, pointing_up=False, color=down_color)

        self.ui(self.set_media, image=image, size=1.0)

    # TODO: this is a hand-drawn PIL approximation (two rounded rectangles), not real icon
    # artwork - replace with a proper thumbs-up/down glyph (e.g. bundled SVG/PNG assets)
    # when available.
    @staticmethod
    def _draw_thumb(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, pointing_up: bool, color) -> None:
        margin_x = (x1 - x0) * 0.22
        margin_y = (y1 - y0) * 0.12
        x0, x1 = x0 + margin_x, x1 - margin_x
        y0, y1 = y0 + margin_y, y1 - margin_y

        width = x1 - x0
        height = y1 - y0
        fist_height = height * 0.4
        thumb_width = width * 0.42
        thumb_x0 = x0 + (width - thumb_width) * 0.35  # slightly left of center, like a real thumb
        fist_radius = min(width, fist_height) * 0.35
        thumb_radius = thumb_width * 0.5

        if pointing_up:
            fist_box = [x0, y1 - fist_height, x1, y1]
            thumb_box = [thumb_x0, y0, thumb_x0 + thumb_width, y1 - fist_height * 0.35]
        else:
            fist_box = [x0, y0, x1, y0 + fist_height]
            thumb_box = [thumb_x0, y0 + fist_height * 0.35, thumb_x0 + thumb_width, y1]

        draw.rounded_rectangle(thumb_box, radius=thumb_radius, fill=color)
        draw.rounded_rectangle(fist_box, radius=fist_radius, fill=color)
