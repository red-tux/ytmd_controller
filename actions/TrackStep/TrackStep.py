import os

from src.backend.PluginManager.InputBases import KeyAction
from src.backend.PluginManager.EventAssigner import EventAssigner
from src.backend.DeckManagement.InputIdentifier import Input
from GtkHelper.GenerativeUI.ComboRow import ComboRow

from ..common.ytmd_action_base import YTMDActionMixin

PREVIEW_CHOICES = ["none", "next", "previous"]


class TrackStep(YTMDActionMixin, KeyAction):
    """Next Track and Previous Track are separately assignable functions (Event Assigner), so
    one key can do both - e.g. short press = next, hold = previous - instead of needing one
    key per direction. Optionally previews the upcoming/previous track's art+title instead of
    the current track's, fetched via the plugin's shared thumbnail cache."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, default_events=False, **kwargs)

        self._latest_state = None
        self._last_label = None
        self._last_preview_key = None

        self.preview_row = ComboRow(
            self, "preview", "none", items=PREVIEW_CHOICES, title="Thumbnail Preview",
            subtitle="Show the upcoming/previous track's art instead of the current track's",
            on_change=self._on_preview_setting_changed,
        )

        self.add_event_assigner(EventAssigner(
            id="Next Track", ui_label="Next Track",
            default_events=[Input.Key.Events.DOWN], callback=self._do_next,
        ))
        self.add_event_assigner(EventAssigner(
            id="Previous Track", ui_label="Previous Track",
            default_events=[Input.Key.Events.HOLD_START], callback=self._do_previous,
        ))

    def on_ready(self) -> None:
        icon_path = os.path.join(self.plugin_base.PATH, "assets", "info.png")
        self.set_media(media_path=icon_path, size=0.75)
        super().on_ready()

    def _on_preview_setting_changed(self, widget, new_value, old_value) -> None:
        self._last_label = None
        self._last_preview_key = None
        if self._latest_state is not None:
            self._render(self._latest_state)

    def on_ytmd_state(self, state: dict) -> None:
        self._latest_state = state
        self._render(state)

    def _render(self, state: dict) -> None:
        preview = self.preview_row.get_value(fallback="none")

        if preview == "none":
            title, _ = self.format_title_artist(state)
            if title == self._last_label:
                return
            was_previewing = self._last_preview_key is not None or self._last_label is None
            self._last_label = title
            self._last_preview_key = None
            self.ui(self.set_bottom_label, title)
            if was_previewing:
                # Coming back from preview mode - the key image is still whatever track was
                # last previewed, reset it to the static icon rather than leaving it stuck.
                icon_path = os.path.join(self.plugin_base.PATH, "assets", "info.png")
                self.ui(self.set_media, media_path=icon_path, size=0.75)
            return

        item = self._get_adjacent_queue_item(state, preview)
        key = (item or {}).get("videoId")
        title = (item or {}).get("title", "")

        if key == self._last_preview_key:
            return
        self._last_preview_key = key
        self._last_label = None

        self.ui(self.set_bottom_label, title)

        if item is None:
            icon_path = os.path.join(self.plugin_base.PATH, "assets", "info.png")
            self.ui(self.set_media, media_path=icon_path, size=0.75)
            return

        thumbnails = item.get("thumbnails") or []
        if not thumbnails:
            return
        url = max(thumbnails, key=lambda t: t.get("width", 0))["url"]
        self.plugin_base.thumbnail_cache.request(key, url, self._on_preview_thumbnail)

    def _on_preview_thumbnail(self, image) -> None:
        if image is None:
            return
        width, height = self.get_display_size()
        image = image.resize((width, height)).convert("RGBA")
        self.ui(self.set_media, image=image, size=1.0)

    @staticmethod
    def _get_adjacent_queue_item(state: dict, direction: str) -> dict | None:
        queue = ((state or {}).get("player") or {}).get("queue") or {}
        items = queue.get("items") or []
        index = queue.get("selectedItemIndex")
        if index is None:
            return None

        offset = 1 if direction == "next" else -1
        adjacent_index = index + offset
        if 0 <= adjacent_index < len(items):
            return items[adjacent_index]
        return None

    def _do_next(self, data=None) -> None:
        self.send_command("next")

    def _do_previous(self, data=None) -> None:
        self.send_command("previous")
