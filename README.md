# YTMDesktop Controller

A StreamController plugin for controlling [YouTube Music Desktop](https://github.com/ytmdesktop/ytmdesktop) (YTMD) from a Stream Deck, via YTMD's own Companion Server API.

![Overview of the plugin's actions placed on a deck](docs/screenshots/keygrid-overview.png)

## Connecting to YTMD

Every action below shares one connection, configured once in the plugin's own settings.

1. In **YTMD**: Settings → Integration → enable **Companion Server**, and turn on **Authorized companions**.
2. In **StreamController**: open this plugin's settings (Plugins → YTMDesktop Controller → the settings/gear icon).
3. Set **Host** / **Port** if YTMD isn't running on `localhost:9863`.
4. Optionally change **Client Identifier** — it defaults to your machine's hostname. YTMD's own "authorized companions" list is keyed on this value, so if you run this plugin from more than one machine (or want to re-pair cleanly), give each one a distinct identifier.
5. Click **Pair with YTMD** and confirm the prompt in YTMD right away — the pairing code is only valid for about a minute.
6. The status changes to **Paired** once confirmed.

That's the only setup step. Every action communicates through this single paired connection.

## The actions

| Action | Input | What it does |
|---|---|---|
| **Play/Pause** | Key | Shows the current track's art and, optionally, title/artist labels and a progress bar. Press to toggle playback. |
| **Track Step** | Key | Skip forward/back. Optionally previews the *upcoming* or *previous* track's art/title instead of the current one. |
| **Volume Step** | Key | Nudge the volume up/down by a configurable amount. |
| **Dial Control** | Dial | An all-in-one dial: album art, an optional volume bar and progress bar, optional title/artist labels, and every function (play/pause, mute, next/previous, volume) bindable to any gesture. |
| **Shuffle & Repeat** | Key | Toggle shuffle and control repeat mode, with a status icon. |
| **Thumbs Up/Down** | Key | Like/dislike the current track, with a status icon. |

Add any of these the normal StreamController way — drag an empty key or dial's **Add Action** button, pick it from *YTMDesktop Controller*, then configure it in the sidebar.

![The Actions panel for a key with an action assigned](docs/screenshots/actions-panel.png)

## The Event Assigner

Every action except Play/Pause exposes more than one function (e.g. Volume Step has *Volume Up* and *Volume Down*), and lets you decide which physical gesture triggers which one, instead of hard-coding one function per key. This is StreamController's own **Event Assigner** panel, in each action's configuration:

![The Event Assigner, showing gestures on the left and assignable functions on the right](docs/screenshots/event-assigner.png)

- The **left column** is the fixed physical gesture for that input (Key Down/Up/Short Up/Hold Start/Hold Stop for keys; those plus Turn CW/CCW and two touchscreen presses for dials). This list never changes.
- The **right column** is a dropdown of every function *that action* registers. Pick which function fires for each gesture, or leave it **None**.
- Every action below ships with sensible defaults already assigned — you only need to open this panel if you want to remap something (e.g. swap which gesture triggers Next vs. Previous), or to bind a function that has no default (like Dial Control's *Repeat On/Single/Off*, or Shuffle & Repeat's individual repeat-mode functions).
- Because gestures and functions are independent, one physical key or dial can do multiple things — short press for one function, hold for another, and so on.

## Per-action reference

### Play/Pause

The only action here *without* an Event Assigner — it just toggles play/pause on press. Everything else is display configuration:

| Setting | Values | Default |
|---|---|---|
| Top / Middle / Bottom Label | `none`, `title`, `artist` | `title` / `artist` / `none` |
| Show Progress Bar | on/off | off |
| Progress Bar Width (%) | 5–50 | 15 |
| Progress Bar Opacity (%) | 10–100 | 100 |
| Progress Bar Color | color picker | red |

### Track Step

| Function | Default gesture |
|---|---|
| Next Track | Key Down |
| Previous Track | Key Hold Start |

| Setting | Values | Default |
|---|---|---|
| Thumbnail Preview | `none`, `next`, `previous` | `none` |

With Thumbnail Preview set, the key shows the art/title of the *adjacent* track from YTMD's queue instead of the currently-playing one — handy for a "here's what's coming up next" key. With it off, the key shows the current track's title, like a normal transport button.

### Volume Step

| Function | Default gesture |
|---|---|
| Volume Up | Key Down |
| Volume Down | Key Hold Start |

| Setting | Values | Default |
|---|---|---|
| Step | 1–100 | 10 |
| Icon Display | `both`, `up`, `down` | `both` |

The current volume is also shown as a `NN%` label. Set Icon Display to `up` or `down` (and remap the Event Assigner if you want) to make a pair of dedicated volume-up / volume-down keys instead of one key that does both.

### Dial Control

The most configurable action — a full now-playing display with every function bindable to any dial gesture.

| Function | Default gesture |
|---|---|
| Play/Pause | Dial Down (press) |
| Mute Toggle | Dial Touchscreen Short Press |
| Next Track | Dial Short Up |
| Previous Track | Dial Hold Start |
| Volume Up | Dial Turn CW |
| Volume Down | Dial Turn CCW |

*(Dial Up, Dial Hold Stop, and Dial Touchscreen Long Press have no default — bind them to whatever you like.)*

![Dial Control's settings panel](docs/screenshots/dial-control-settings.png)

| Setting | Values | Default |
|---|---|---|
| Top / Middle / Bottom Label | `none`, `title`, `artist` | `none` / `title` / `artist` |
| Show Progress Bar | on/off | off |
| Progress Bar Width (%) | 5–50 | 15 |
| Progress Bar Opacity (%) | 10–100 | 100 |
| Progress Bar Color | color picker | red |
| Volume Bar | `auto` (briefly shows after a change), `always` | `auto` |
| Bar Width (%) | 5–75 | 25 |
| Bar Opacity (%) | 10–100 | 100 |
| Bar Color | color picker | green |
| Maintain Aspect Ratio | on/off | off (stretches art to fill) |
| Art Horizontal / Vertical Position | left/center/right, top/center/bottom | center / center |

Two things worth knowing:
- If both the progress bar and volume bar are enabled, the progress bar reserves its height off the bottom of the dial first, and the volume bar's fill range is scaled to whatever space is left above it, so they never overlap.
- Turning the dial always unmutes first (a muted knob that silently changes an inaudible volume isn't useful), and the mute icon dims the volume bar rather than showing a separate indicator.

### Shuffle & Repeat

| Function | Default gesture |
|---|---|
| Toggle Shuffle | Key Down |
| Cycle Repeat (steps Off → All → One → Off) | Key Hold Start |
| Repeat On / Repeat Single / Repeat Off | *(no default — assign directly if you want one gesture per mode instead of cycling)* |

No additional settings. The key shows a static shuffle glyph (crossed lines) above a repeat-status loop icon: gray = off, green = repeat-all, green with a "1" = repeat-one.

> YTMD doesn't report whether shuffle is currently on or off, and its API only exposes a toggle — not a set-explicit-state command. Because of that, there's no reliable way to show shuffle's actual status or offer separate "Shuffle On"/"Shuffle Off" functions; the shuffle icon is always the same, and the only shuffle function is a toggle.

### Thumbs Up/Down

| Function | Default gesture |
|---|---|
| Like | Key Down |
| Dislike | Key Hold Start |

| Setting | Values | Default |
|---|---|---|
| Icon Display | `both`, `up`, `down` | `both` |

Unlike shuffle, YTMD does report the current rating, so Like/Dislike are true "set to this state" functions (pressing Like when already liked does nothing, rather than un-liking it), and the icon reflects the real rating: gray = neutral, green thumb = liked, red thumb = disliked. Set Icon Display to `up` or `down` to use this action as a dedicated like-only or dislike-only key.

## Notes

- Album art and any other track art (e.g. Track Step's upcoming-track preview) is cached to disk under the plugin's own folder, so returning to a recently-seen track doesn't re-download its artwork.
- All of the shuffle/repeat and thumbs icons are simple hand-drawn shapes, not polished artwork — see the `# TODO` markers in each action's source for where nicer icon assets would slot in.
