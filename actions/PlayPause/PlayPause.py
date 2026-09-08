import os

from PIL import Image, ImageDraw

from ..common.ytmd_action_base import YTMDKeyAction


class PlayPause(YTMDKeyAction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_label_rows(on_change=self._on_setting_changed)
        self.setup_progress_rows(on_change=self._on_setting_changed)

        self._last_track_key = None
        self._art_image = None
        self._latest_state = None
        self._last_progress_px = None
        self._last_paused = None

    def on_ready(self) -> None:
        # Only show the placeholder before there's any art. on_ready() re-runs on every page
        # revisit; once art is cached, super().on_ready()'s state replay re-pushes it, so
        # setting the icon here again would just flash it over the real art.
        if self._art_image is None:
            icon_path = os.path.join(self.plugin_base.PATH, "assets", "info.png")
            self.push_media(media_path=icon_path, size=0.75)
        else:
            # Revisit with art already cached - re-push it now (the core cleared the key
            # image just before this call) instead of waiting for the state replay.
            self._redraw_image()
        super().on_ready()

    def _on_setting_changed(self, widget, new_value, old_value) -> None:
        if self._latest_state is not None:
            self.render_chosen_labels(self._latest_state, force=True)
        self._redraw_image()

    def on_ytmd_state(self, state: dict) -> None:
        self._latest_state = state

        # state-update fires several times a second during playback (progress ticks) - only
        # touch the hardware for track-level things (art, title/artist) when the track
        # actually changed. The progress bar and pause overlay are the things that
        # legitimately need to redraw on their own, and only do so when they actually change.
        track_key = self.video_id(state)
        track_changed = track_key != self._last_track_key
        if track_changed:
            self._last_track_key = track_key
            self.render_chosen_labels(state, force=True)
            self.request_thumbnail(state, self._on_thumbnail)

        paused = self.plugin_base.playback_state.is_paused()
        paused_changed = paused != self._last_paused
        self._last_paused = paused

        progress_changed = False
        if self.progress_enabled() and self._art_image is not None:
            # The bar is only ever a few dozen pixels wide - redrawing (and pushing a full
            # image to the deck's render queue) on every tick when the fill wouldn't even
            # move a pixel is exactly what saturates the deck's render loop and trips its
            # low-FPS warning. Only redraw when the actual filled pixel width changes.
            px = round(self._art_image.width * self.progress_fraction(state))
            progress_changed = px != self._last_progress_px
            self._last_progress_px = px

        if (paused_changed or progress_changed) and not track_changed:
            # If the track also changed, _on_thumbnail() will redraw once the new art arrives -
            # redrawing here too would just repaint the old (soon to be replaced) art.
            self._redraw_image()

    def _on_thumbnail(self, image) -> None:
        if not self.get_is_present():
            return
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

        image = self._art_image
        if self.progress_enabled():
            image = image.copy()
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            fraction = self.progress_fraction(self._latest_state) if self._latest_state else 0.0
            self.draw_progress_bar(ImageDraw.Draw(overlay), image.width, image.height, fraction)
            image = Image.alpha_composite(image, overlay)

        image = self.apply_pause_overlay(image)

        self.push_media(image=image, size=1.0)

    def on_key_down(self, data=None) -> None:
        paused = not self.plugin_base.playback_state.is_paused()
        self.plugin_base.playback_state.set_paused(paused)
        self.send_command("playPause")
        self._last_paused = paused
        self._redraw_image()
