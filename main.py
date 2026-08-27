# Import StreamController modules
from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input

# Import python modules
import json
import os
import threading

from loguru import logger as log

# Import plugin internals
from .internal.ytmd_client import YTMDClient, DEFAULT_HOST, DEFAULT_PORT
from .internal.state_store import StateStore
from .internal.thumbnail_cache import ThumbnailCache, DEFAULT_MAX_ENTRIES as DEFAULT_THUMBNAIL_CACHE_ENTRIES
from .internal.volume_state import VolumeState
from .internal.playback_state import PlaybackState
from .settings_area import YTMDSettingsGroup

# Import actions
from .actions.PlayPause.PlayPause import PlayPause
from .actions.TrackStep.TrackStep import TrackStep
from .actions.VolumeStep.VolumeStep import VolumeStep
from .actions.DialControl.DialControl import DialControl
from .actions.ShuffleRepeat.ShuffleRepeat import ShuffleRepeat
from .actions.ThumbsRating.ThumbsRating import ThumbsRating
from .actions.common.ytmd_action_base import ICON_ASSET_DEFAULTS, COLOR_ASSET_DEFAULTS

KEY_ONLY_SUPPORT = {
    Input.Key: ActionInputSupport.SUPPORTED,
    Input.Dial: ActionInputSupport.UNSUPPORTED,
    Input.Touchscreen: ActionInputSupport.UNSUPPORTED,
    Input.TouchKey: ActionInputSupport.UNSUPPORTED,
    Input.Screen: ActionInputSupport.UNSUPPORTED,
}

DIAL_ONLY_SUPPORT = {
    Input.Key: ActionInputSupport.UNSUPPORTED,
    Input.Dial: ActionInputSupport.SUPPORTED,
    Input.Touchscreen: ActionInputSupport.UNSUPPORTED,
    Input.TouchKey: ActionInputSupport.UNSUPPORTED,
    Input.Screen: ActionInputSupport.UNSUPPORTED,
}


class YTMDControllerPlugin(PluginBase):
    def __init__(self):
        super().__init__()

        self.state_store = StateStore()

        settings = self.get_settings()
        # Shared by any code with access to the plugin - actions, settings UI, future widgets -
        # to fetch/cache any image by (key, url), not just the currently-playing track's art.
        self.thumbnail_cache = ThumbnailCache(
            cache_dir=os.path.join(self.PATH, "cache", "thumbnails"),
            max_entries=settings.get("thumbnail_cache_max_entries", DEFAULT_THUMBNAIL_CACHE_ENTRIES),
        )
        # Single source of truth for volume/mute, updated in on_state_update() below before
        # actions see the state - see internal/volume_state.py for why this needs to be shared.
        self.volume_state = VolumeState()
        # Same idea, for pause status - see internal/playback_state.py.
        self.playback_state = PlaybackState()

        # Registers this plugin's icons/colors as user-customizable assets (Settings > Assets /
        # Colors tabs). Safe to call every startup - PluginBase only applies these as the
        # default; a user override (loaded from settings.json before this runs) always wins.
        # See actions/common/ytmd_action_base.py for the keys and how they're rendered.
        for key, filename in ICON_ASSET_DEFAULTS.items():
            self.add_icon(key, os.path.join(self.PATH, "assets", "icons", "material", filename))
        for key, rgba in COLOR_ASSET_DEFAULTS.items():
            self.add_color(key, rgba)

        self.client = YTMDClient(
            host=settings.get("host", DEFAULT_HOST),
            port=settings.get("port", DEFAULT_PORT),
            token=settings.get("token"),
        )

        self.add_action_holders([
            ActionHolder(
                plugin_base=self,
                action_base=PlayPause,
                action_id_suffix="PlayPause",
                action_name="Play/Pause",
                action_support=KEY_ONLY_SUPPORT,
                description="Shows the currently playing track's art/title/artist; toggles play/pause when pressed. Overlays a dimmed pause icon while paused, shared with Dial Control so both always agree.",
                settings_schema={
                    "top_label": {"type": "string", "values": ["none", "title", "artist"], "default": "title"},
                    "middle_label": {"type": "string", "values": ["none", "title", "artist"], "default": "artist"},
                    "bottom_label": {"type": "string", "values": ["none", "title", "artist"], "default": "none"},
                    "progress_enabled": {"type": "bool", "default": False},
                    "progress_width": {"type": "int", "description": "Progress bar height as % of the key's height", "default": 15},
                    "progress_opacity": {"type": "int", "default": 100},
                    "progress_color": {"type": "rgba tuple", "default": (255, 0, 0, 255)},
                },
            ),
            ActionHolder(
                plugin_base=self,
                action_base=TrackStep,
                action_id_suffix="TrackStep",
                action_name="Track Step",
                action_support=KEY_ONLY_SUPPORT,
                description=(
                    "Next Track and Previous Track are separately assignable via the Event "
                    "Assigner (e.g. press = next, hold = previous, on one key). Optionally "
                    "previews the upcoming/previous track's art and title instead of the "
                    "current track's."
                ),
                settings_schema={
                    "preview": {"type": "string", "values": ["none", "next", "previous"], "default": "none"},
                    "top_label": {"type": "string", "values": ["none", "title", "artist"], "default": "none"},
                    "middle_label": {"type": "string", "values": ["none", "title", "artist"], "default": "none"},
                    "bottom_label": {"type": "string", "values": ["none", "title", "artist"], "default": "title"},
                },
            ),
            ActionHolder(
                plugin_base=self,
                action_base=VolumeStep,
                action_id_suffix="VolumeStep",
                action_name="Volume Step",
                action_support=KEY_ONLY_SUPPORT,
                description=(
                    "Volume Up, Volume Down, and Mute Toggle are separately assignable via the "
                    "Event Assigner (e.g. press = up, hold = down, on one key). 'Icon Display' "
                    "picks whether this key shows both direction icons, or just one - place it "
                    "twice for dedicated up/down keys. Shows the current volume % (or 'Muted'), "
                    "shared with Dial Control so both always agree."
                ),
                settings_schema={
                    "step": {"type": "int", "default": 10},
                    "icon_display": {"type": "string", "values": ["both", "up", "down"], "default": "both"},
                },
            ),
            ActionHolder(
                plugin_base=self,
                action_base=DialControl,
                action_id_suffix="DialControl",
                action_name="Dial Control",
                action_support=DIAL_ONLY_SUPPORT,
                description=(
                    "Configurable dial: every gesture (press, hold, touchscreen tap, turn) can be "
                    "bound to any function - Play/Pause, Mute Toggle, Next/Previous Track, Volume "
                    "Up/Down, Like/Dislike/Toggle Like/Toggle Dislike - via the Event Assigner. "
                    "Shows album art with an optional volume bar; firing a like/dislike function "
                    "also briefly flashes a matching thumb icon. Overlays a dimmed pause icon "
                    "while paused, shared with Play/Pause so both always agree."
                ),
                settings_schema={
                    "bar_mode": {"type": "string", "values": ["auto", "always"], "default": "auto"},
                    "bar_width": {"type": "int", "description": "Bar width as % of the dial's width", "default": 25},
                    "bar_opacity": {"type": "int", "description": "Bar opacity %, 100 = solid", "default": 100},
                    "bar_color": {"type": "rgba tuple", "default": (0, 200, 83, 255)},
                    "art_maintain_aspect": {"type": "bool", "description": "Shrink art to fit instead of stretching", "default": False},
                    "art_h_align": {"type": "string", "values": ["left", "center", "right"], "default": "center"},
                    "art_v_align": {"type": "string", "values": ["top", "center", "bottom"], "default": "center"},
                    "top_label": {"type": "string", "values": ["none", "title", "artist"], "default": "title"},
                    "middle_label": {"type": "string", "values": ["none", "title", "artist"], "default": "artist"},
                    "bottom_label": {"type": "string", "values": ["none", "title", "artist"], "default": "none"},
                    "progress_enabled": {"type": "bool", "default": False},
                    "progress_width": {"type": "int", "description": "Progress bar height as % of the dial's height", "default": 15},
                    "progress_opacity": {"type": "int", "default": 100},
                    "progress_color": {"type": "rgba tuple", "default": (255, 0, 0, 255)},
                },
            ),
            ActionHolder(
                plugin_base=self,
                action_base=ShuffleRepeat,
                action_id_suffix="ShuffleRepeat",
                action_name="Shuffle/Repeat",
                action_support=KEY_ONLY_SUPPORT,
                description=(
                    "Toggle Shuffle, Repeat On/Single/Off, and Cycle Repeat are separately "
                    "assignable via the Event Assigner - assign a specific repeat mode to a "
                    "gesture, or Cycle Repeat to step through modes on one gesture. Shows a "
                    "repeat-status icon (YTMD doesn't report shuffle state, so that icon is "
                    "static, not a toggle indicator)."
                ),
            ),
            ActionHolder(
                plugin_base=self,
                action_base=ThumbsRating,
                action_id_suffix="ThumbsRating",
                action_name="Thumbs Up/Down",
                action_support=KEY_ONLY_SUPPORT,
                description=(
                    "Like, Dislike, Toggle Like, and Toggle Dislike are separately assignable via "
                    "the Event Assigner (e.g. press = like, hold = dislike, on one key). Like/"
                    "Dislike only ever move you into that state (idempotent); Toggle Like/Toggle "
                    "Dislike are the raw toggle - pressing again undoes it. Shows the real current "
                    "rating (YTMD does report like status). 'Icon Display' picks whether this key "
                    "shows both icons, or just one - place it twice for dedicated like/dislike keys."
                ),
                settings_schema={
                    "icon_display": {"type": "string", "values": ["both", "up", "down"], "default": "both"},
                },
            ),
        ])

        # Register plugin
        # name/github/version/app-version all come from manifest.json - no need to duplicate
        # them here (and risk the two drifting out of sync).
        self.register()

        # launch_backend() can block on first run (building the venv), so keep it off the
        # main/UI thread - see the hard constraint on not blocking GTK startup.
        threading.Thread(target=self._launch_backend, name="ytmd_launch_backend", daemon=True).start()

    def get_settings_area(self):
        return YTMDSettingsGroup(self)

    def _launch_backend(self) -> None:
        self.launch_backend(
            backend_path=os.path.join(self.PATH, "backend", "backend.py"),
            venv_path=os.path.join(self.PATH, ".venv"),
        )

    def register_backend(self, port: int) -> None:
        super().register_backend(port)
        self._push_backend_config()

    def _push_backend_config(self) -> None:
        if self.backend is None:
            return
        settings = self.get_settings()
        try:
            self.backend.configure(
                settings.get("host", DEFAULT_HOST),
                settings.get("port", DEFAULT_PORT),
                settings.get("token"),
            )
        except Exception as e:
            log.error(f"Failed to push connection settings to backend: {e}")

    def on_connection_settings_changed(self) -> None:
        """Called by the settings UI whenever host/port/token change (including after pairing)."""
        settings = self.get_settings()
        self.client.host = settings.get("host", DEFAULT_HOST)
        self.client.port = settings.get("port", DEFAULT_PORT)
        self.client.token = settings.get("token")
        self._push_backend_config()

    def on_thumbnail_cache_max_entries_changed(self, max_entries: int) -> None:
        """Called by the settings UI when 'Max Cached Thumbnails' changes. Applies immediately -
        no restart needed, since the cache reads its limit fresh on every write/prune."""
        settings = self.get_settings()
        settings["thumbnail_cache_max_entries"] = max_entries
        self.set_settings(settings)
        self.thumbnail_cache.set_max_entries(max_entries)

    def on_state_update(self, state: str) -> None:
        """Called by the backend process (over RPyC) whenever YTMD pushes a state-update event.

        `state` arrives JSON-encoded rather than as a dict - see backend.py's _on_state_update
        for why (rpyc proxies plain dicts by reference across the RPyC boundary instead of
        copying them, which causes every field access here to silently round-trip back to the
        backend process and eventually recurse)."""
        parsed = json.loads(state)
        self.volume_state.update(parsed)
        self.playback_state.update(parsed)
        self.state_store.update(parsed)

    def on_connection_status(self, connected: bool) -> None:
        """Called by the backend process (over RPyC) when the realtime socket connects/drops."""
        self.state_store.set_connected(connected)
