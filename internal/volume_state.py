"""Single source of truth for 'current volume level + mute status', derived once per
state-update from the raw `player.volume`/`player.muted` fields.

VolumeStep and DialControl each used to derive this independently from the raw state, which is
how they could disagree - e.g. VolumeStep showed a stale volume % after muting via DialControl,
since it never looked at `player.muted` at all. Updated centrally in main.py's
on_state_update(), before the state fans out to actions, so every display reads the same
already-current values instead of re-deriving (and possibly re-interpreting) them itself.
"""


import threading


class VolumeState:
    def __init__(self):
        self._volume = 50
        self._muted = False
        # Writers only: update() (backend thread) does a compound two-field write that
        # must not interleave with an event thread's set_muted()/set_volume(). Readers
        # (get_*) stay lock-free - a single attribute read is atomic under the GIL, and
        # the brief cross-field skew that allows is invisible for a display value.
        self._lock = threading.Lock()

    def update(self, state: dict) -> None:
        player = (state or {}).get("player") or {}
        with self._lock:
            self._volume = player.get("volume", self._volume)
            self._muted = player.get("muted", self._muted)

    def get_volume(self) -> int:
        return self._volume

    def get_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        """Optimistic local update for whichever action just sent mute/unmute - sets it here
        (not just on that action's own instance) so every other display reflects it immediately
        instead of waiting for the round trip back through the next state-update."""
        with self._lock:
            self._muted = muted

    def set_volume(self, volume: int) -> None:
        """Optimistic local update for whichever action just sent setVolume - same rationale
        as set_muted(): every other volume display reflects it immediately instead of waiting
        for the next state-update round trip."""
        with self._lock:
            self._volume = volume
