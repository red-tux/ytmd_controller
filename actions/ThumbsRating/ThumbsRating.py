from PIL import Image

from src.backend.PluginManager.InputBases import KeyAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input
from GtkHelper.GenerativeUI.ComboRow import ComboRow

from ..common.ytmd_action_base import (
    YTMDActionMixin,
    ICON_THUMB_UP, ICON_THUMB_DOWN,
    COLOR_LIKE, COLOR_DISLIKE, COLOR_NEUTRAL,
)

# YTMD's likeStatus: a real, reliable field (unlike shuffle) - see
# https://github.com/XeroxDev/ytmdesktop-ts-companion/blob/main/src/enums/like-status.ts
LIKE_DISLIKE = 0
LIKE_INDIFFERENT = 1
LIKE_LIKE = 2

ICON_CHOICES = ["both", "up", "down"]


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
        self.add_event_assigner(EventAssigner(id="Toggle Like", ui_label="Toggle Like", callback=self._do_toggle_like))
        self.add_event_assigner(EventAssigner(id="Toggle Dislike", ui_label="Toggle Dislike", callback=self._do_toggle_dislike))

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

    def _do_toggle_like(self, data=None) -> None:
        # Unlike Like above, this is the raw toggle - if already liked, this un-likes it
        # (back to indifferent) instead of leaving it liked.
        self.send_command("toggleLike")

    def _do_toggle_dislike(self, data=None) -> None:
        self.send_command("toggleDislike")

    # --- rendering -----------------------------------------------------------

    def _render(self, like_status) -> None:
        width, height = self.get_display_size()
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        up_color_key = COLOR_LIKE if like_status == LIKE_LIKE else COLOR_NEUTRAL
        down_color_key = COLOR_DISLIKE if like_status == LIKE_DISLIKE else COLOR_NEUTRAL

        mode = self.icon_display_row.get_value(fallback="both")
        if mode == "both":
            half = height // 2
            self.paste_asset_icon(image, ICON_THUMB_UP, up_color_key, (0, 0, width, half))
            self.paste_asset_icon(image, ICON_THUMB_DOWN, down_color_key, (0, half, width, height))
        elif mode == "up":
            self.paste_asset_icon(image, ICON_THUMB_UP, up_color_key, (0, 0, width, height))
        else:
            self.paste_asset_icon(image, ICON_THUMB_DOWN, down_color_key, (0, 0, width, height))

        self.ui(self.set_media, image=image, size=1.0)
