import os

from PIL import Image, ImageDraw

from src.backend.PluginManager.InputBases import KeyAction

from ..common.ytmd_action_base import YTMDActionMixin


class PlayPause(YTMDActionMixin, KeyAction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_label_rows(on_change=self._on_setting_changed)
        self.setup_progress_rows(on_change=self._on_setting_changed)

        self._last_track_key = None
        self._art_image = None
        self._latest_state = None
        self._last_progress_px = None

    def on_ready(self) -> None:
        icon_path = os.path.join(self.plugin_base.PATH, "assets", "info.png")
        self.set_media(media_path=icon_path, size=0.75)
        super().on_ready()

    def _on_setting_changed(self, widget, new_value, old_value) -> None:
        if self._latest_state is not None:
            self.render_chosen_labels(self._latest_state, force=True)
        self._redraw_image()

    def on_ytmd_state(self, state: dict) -> None:
        self._latest_state = state

        # state-update fires several times a second during playback (progress ticks) - only
        # touch the hardware for track-level things (art, title/artist) when the track
        # actually changed. The progress bar is the one thing that legitimately needs to
        # redraw every tick, and only does so when the user has actually enabled it.
        track_key = self.video_id(state)
        track_changed = track_key != self._last_track_key
        if track_changed:
            self._last_track_key = track_key
            self.render_chosen_labels(state, force=True)
            self.request_thumbnail(state, self._on_thumbnail)

        if self.progress_enabled() and self._art_image is not None:
            # The bar is only ever a few dozen pixels wide - redrawing (and pushing a full
            # image to the deck's render queue) on every tick when the fill wouldn't even
            # move a pixel is exactly what saturates the deck's render loop and trips its
            # low-FPS warning. Only redraw when the actual filled pixel width changes.
            px = round(self._art_image.width * self.progress_fraction(state))
            if px != self._last_progress_px:
                self._last_progress_px = px
                self._redraw_image()

    def _on_thumbnail(self, image) -> None:
        if image is not None:
            width, height = self.get_display_size()
            self._art_image = image.resize((width, height)).convert("RGBA")
        else:
            self._art_image = None
        self._last_progress_px = None
        self._redraw_image()

    def _redraw_image(self) -> None:
        if self._art_image is None:
            return

        if not self.progress_enabled():
            self.ui(self.set_media, image=self._art_image, size=1.0)
            return

        image = self._art_image.copy()
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        fraction = self.progress_fraction(self._latest_state) if self._latest_state else 0.0
        self.draw_progress_bar(ImageDraw.Draw(overlay), image.width, image.height, fraction)
        image = Image.alpha_composite(image, overlay)

        self.ui(self.set_media, image=image, size=1.0)

    def on_key_down(self, data=None) -> None:
        self.send_command("playPause")
