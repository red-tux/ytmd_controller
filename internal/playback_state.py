"""Single source of truth for 'is the track currently paused', derived once per state-update
from the raw `player.trackState` field. Mirrors internal/volume_state.py's role for
volume/mute - PlayPause and DialControl both need to overlay a pause icon and should agree on
when to show it, instead of each re-deriving it from the raw field independently.

trackState values, confirmed live against a real YTMD instance (see
https://github.com/XeroxDev/ytmdesktop-ts-companion for the enum this mirrors):
0 = paused, 1 = playing, 2 = buffering. Buffering is deliberately not treated as paused - it's
still trying to play.
"""

TRACK_STATE_PAUSED = 0
TRACK_STATE_PLAYING = 1
TRACK_STATE_BUFFERING = 2


class PlaybackState:
    def __init__(self):
        self._track_state = TRACK_STATE_PLAYING

    def update(self, state: dict) -> None:
        player = (state or {}).get("player") or {}
        self._track_state = player.get("trackState", self._track_state)

    def is_paused(self) -> bool:
        return self._track_state == TRACK_STATE_PAUSED

    def set_paused(self, paused: bool) -> None:
        """Optimistic local update for whichever action just sent playPause - sets it here
        (not just on that action's own instance) so every other display reflects it immediately
        instead of waiting for the round trip back through the next state-update."""
        self._track_state = TRACK_STATE_PAUSED if paused else TRACK_STATE_PLAYING
