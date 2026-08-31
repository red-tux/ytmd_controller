# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A StreamController plugin providing Stream Deck control of [YouTube Music Desktop](https://github.com/ytmdesktop/ytmdesktop) (YTMD) via its Companion Server API. This repo (`net_red-tux_ytmd_controller`, remote `git@github.com:red-tux/ytmd_controller.git`) is developed as a nested git checkout inside a StreamController dev tree, at `<StreamController>/data/plugins/net_red-tux_ytmd_controller/`. StreamController discovers plugins by folder name under `<data>/plugins/`, so this location doubles as the plugin's install path for local testing — no separate install step is needed during development.

The plugin ships six actions:

| Action | Input | Purpose |
| --- | --- | --- |
| `PlayPause` | key | Now-playing art + labels; toggles play/pause; dimmed pause overlay while paused. |
| `TrackStep` | key | Next / Previous as separately assignable functions; optional preview of the adjacent queue item's art+title. |
| `VolumeControl` | key | Volume Up / Down / Mute Toggle / Set Volume as separately assignable functions; shows current volume % or "Muted". The "Set Volume Level" row is hidden unless Set Volume is bound. |
| `DialControl` | dial | Every dial gesture bindable to any function via the Event Assigner; album art + optional volume bar; like/dislike flash. |
| `ShuffleRepeat` | key | Toggle Shuffle, explicit Repeat modes, Cycle Repeat; repeat-status icon (shuffle state isn't reported by YTMD). |
| `ThumbsRating` | key | Like / Dislike / Toggle Like / Toggle Dislike; icon reflects the real reported rating. |

## Running / testing this plugin

There is no standalone build or test harness in this repo — a plugin only runs inside a StreamController instance. From the parent StreamController checkout:

```sh
python3 main.py --devel --data data --close-running
```

or, from that checkout, `bash run_dev.sh` — a convenience wrapper that clears the previous run's logs, closes any running instance, and tees console output (GTK warnings, uncaught tracebacks) to `data/logs/run-console.log` alongside the structured loguru log at `data/logs/logs.log`.

(`--devel` and `--data data` are documented in the parent repo's `CLAUDE.md`.) Plugins are imported once at startup — there is no hot reload, so restart the app after code changes.

The plugin id and folder name must stay in sync: `manifest.json`'s `id` field (falling back to the folder name if blank) is the plugin id, used as the prefix for every action id (`<plugin_id>::<ActionName>`).

Ad-hoc test scripts with an entry point outside this directory are fine (e.g. importing `internal/` modules directly against a GLib main loop) — that's the practical way to exercise the concurrency-sensitive parts without a full app launch.

**Profiling.** Launch with `YTMD_PROFILE=1` (optionally `YTMD_PROFILE_INTERVAL=<seconds>`, default 10) and `internal/profiling.py` logs a periodic `[ytmd-profile]` summary — YTMD state-update rate, callback fan-out, wall time in `on_ytmd_state` (main thread), and `ui()` hardware pushes. It's a hard no-op with the env var unset (no thread, guard-checks only), so it stays in the tree. Hooked in `main.py` (`ensure_reporter`, `state_update.recv`), `state_store.py` (`dispatch` + `wrap_callback` timing), and `YTMDActionMixin.ui()` (`ui_push`). For a function-level / per-thread picture of the whole app, `py-spy record --pid <main.py pid> --format raw` and grep the folded stacks — as of the last profiling pass the plugin was ~2% of on-CPU samples; the bulk is core `add_labels_to_image` → PIL `getmask2` glyph rasterization on the deck render loop. The `README.md` "Performance" section is the user-facing version of this.

### Fixed: dev-container bug where text labels didn't render (PIL/GTK FreeType conflict)

In this devcontainer, `set_top_label`/`set_center_label`/`set_bottom_label` calls (both from this plugin and from StreamController's own manual Label Editor) used to silently fail to show any visible text, and raising a label's outline width could crash with `PIL.Image.DecompressionBombError`. **This is now fixed** at the devcontainer level (see below) — no plugin-side workaround is needed any more.

Root cause, confirmed independent of any StreamController or plugin code: the pip-installed Pillow wheel bundles its own private FreeType (`pillow.libs/libfreetype-*.so`), distinct from the system FreeType GTK/Pango load. It's not merely "GTK imported before PIL" — the corruption specifically needs a real `Gtk`/`Adw.Application` to actually activate against a live display (this devcontainer forwards the host's X11/Wayland sockets in, so GTK gets a real display connection, not a headless fallback). Once that happens, PIL's own text-measurement calls come back corrupted — `ImageDraw.textbbox()` on `"Hello World"` at 15pt, which is ~86px wide with PIL alone, comes back with garbage coordinates (e.g. `-2326586`) after GTK activates in the same process. That garbage is what pushed text off-canvas (invisible) and, with a larger stroke/outline width, over PIL's decompression-bomb limit.

- Verified independent of StreamController/plugin code via isolated repro scripts (PIL font load + `Adw.Application.run()` alone, no GTK/plugin code involved).
- Also verified NOT present in a real Flatpak install (1.5.0-beta.15, fake deck) — the GNOME SDK runtime version-locks GTK and its dependents together, avoiding this. That's still true and remains a good verification path for anything this fix doesn't cover.
- **Fix**: rebuild Pillow from source against the *system* FreeType instead of installing the prebuilt manylinux wheel, so there's only one FreeType in the process:
  ```sh
  uv pip install --no-binary pillow --reinstall --no-deps pillow==12.3.0
  ```
  This is now wired into the parent repo's `.devcontainer/devcontainer.json` `postCreateCommand`, so a fresh container rebuild picks it up automatically. If Pillow's version pin in the parent repo's `requirements.txt` ever changes, that `postCreateCommand` line needs its version bumped to match (it's a plain string pin, not auto-derived).
  - `LD_PRELOAD`-ing the system libfreetype over PIL's bundled one does *not* work (produces different garbage) — the fix has to be an actual rebuild/relink, not a runtime override.

### Observed StreamController core bugs (documented, not fixed)

See `docs/observed-core-bugs.md` for three bugs found in the parent app's core while building this plugin: SVG icon assets rasterize squashed (non-square) due to a missing argument in `MediaManager.generate_svg_thumbnail()`; `Observer.notify()` produces a harmless-but-noisy `RuntimeWarning` on every asset registration because it schedules a coroutine onto an asyncio loop that GLib never drives; and the Sidebar's action-list row breaks (shows raw markup text) if an action's `action_name` contains `&`/`<`/`>`, because `ActionManager.py` interpolates it into a Pango markup string unescaped. All three are worked around or avoided on the plugin side, none fixed in core — that file has the reproductions and proposed fixes for a future session.

## Architecture

### Entry points

- `main.py` — `YTMDControllerPlugin(PluginBase)`. Constructs the shared singletons (below), registers icon/color assets, adds the six `ActionHolder`s, `register()`s, then launches the backend on a daemon thread (`launch_backend()` can block building the venv on first run — keep it off the UI thread). `register_backend()` is overridden to push the current host/port/token to the backend once it connects.
- `actions/<Name>/<Name>.py` — one class per action, each `class X(YTMDActionMixin, KeyAction)` or `(YTMDActionMixin, DialAction)`. Subclass the current `InputBases.py` mixins, never the deprecated `ActionBase`.
- `actions/common/ytmd_action_base.py` — `YTMDActionMixin`: the shared plumbing every action mixes in ahead of `KeyAction`/`DialAction` (plain mixin, no `__init__`, so it doesn't disturb the MRO). Provides state subscribe/unsubscribe, the thumbnail request wrapper, and all the rendering helpers (label rows, progress bar, icon/colour asset compositing, pause overlay, `get_display_size()`).
- `backend/backend.py` — `YTMDBackend(BackendBase)`, runs in this plugin's own venv. Owns the YTMD Socket.IO realtime connection and relays `state-update` / connect / disconnect events to the foreground over RPyC. `__install__.py` + `backend_requirements.txt` build the venv (`python-socketio[client]`, which the shared app env doesn't and shouldn't ship).
- `settings_area.py` — `YTMDSettingsGroup(Adw.PreferencesGroup)`, returned from `PluginBase.get_settings_area()`. Host/port/client-id entry, the pairing button (blocking companion-server handshake on a thread), and thumbnail-cache stats / purge / max-entries.
- `internal/ytmd_client.py` — `YTMDClient`, the REST client for the Companion Server API v1 (`/auth/*`, `/state`, `/command`). Used by the foreground for pairing and for sending commands.

### Shared singletons (owned by the plugin, one instance, read by every action)

| Attribute | Type | Role |
| --- | --- | --- |
| `plugin_base.state_store` | `internal/state_store.py` `StateStore` | Holds the latest raw `/state` dict; pub/sub for actions. Fed by the backend relay and by `on_state_update()`. |
| `plugin_base.volume_state` | `internal/volume_state.py` `VolumeState` | Single source of truth for volume % + mute. Derived once per state-update (in `on_state_update()`, before actions see the state) so every volume display agrees. |
| `plugin_base.playback_state` | `internal/playback_state.py` `PlaybackState` | Same, for paused/playing/buffering — so `PlayPause` and `DialControl` always agree on the pause overlay. |
| `plugin_base.thumbnail_cache` | `internal/thumbnail_cache.py` `ThumbnailCache` | General-purpose image cache keyed by any caller-chosen string: memory LRU over an on-disk cache under `cache/thumbnails/`. Not YTMD-specific — takes `(key, url, callback)`. |
| `plugin_base.client` | `internal/ytmd_client.py` `YTMDClient` | REST client, shared by the actions' `send_command()` and the settings UI. |

`on_state_update()` in `main.py` is the one ingestion point: the backend calls it (JSON-encoded — see below) and it updates `volume_state`, then `playback_state`, then `state_store`, in that order, so actions always read already-current derived values.

**Why the backend JSON-encodes the state dict:** rpyc proxies plain `dict`/`list` arguments *by reference* across the RPyC boundary rather than copying them, so every `.get()`/`[]` on the frontend would silently round-trip back to the backend process and eventually recurse. `backend.py` does `json.dumps(data)`; `on_state_update()` does `json.loads(state)` to force a real local copy.

### Threading model

The invariant that keeps the plugin free of per-action locks: **`on_ytmd_state()` for every action runs single-threaded on the GTK main thread.** `StateStore.update()` / `set_connected()` fan out to subscribers via `GLib.idle_add`, and the `on_ready()` state replay is already on the main thread — so an action's `on_ytmd_state`, its `_last_*` dedup bookkeeping, and its `_art_image` writes never race.

Everything else:

- **`VolumeState` / `PlaybackState`** — writer-only `threading.Lock` (a compound / read-modify-write in `update()` must not interleave with an event thread's `set_muted()` / `set_volume()` / `set_paused()`). `get_*()` / `is_paused()` stay lock-free: a single attribute read is atomic under the GIL, and the brief cross-field skew that allows is invisible for a display value. A lock here does *not* fix the "stale state-update snapshot clobbers an optimistic local write" reorder — that's handled by the in-flight guards in `DialControl` (`_pending_send_timer`) and `VolumeControl` (`_local_change_until` grace window).
- **`ThumbnailCache`** — one long-lived `ytmd_thumbnail_worker` daemon drains a `queue.Queue`; no thread-per-request, so a burst of track changes can't spawn an unbounded pile of downloads. Same-key requests arriving before resolution are coalesced onto one job. **Every callback — memory hit, disk hit, network, or failure — is delivered via `GLib.idle_add`**, so consumers always run on the main thread. `internal/thumbnail_cache.py` is the single choke point for that guarantee; callers just pass a plain callback.
- **`YTMDClient`** — `configure(host, port, token)` applies the triple atomically under a lock; `base_url` / `_headers()` read under it. `send_command()` is synchronous HTTP, but it runs on the throwaway per-event thread StreamController spawns for each input (`own_actions_event_callback_threaded`), never on the main thread or a tick thread, so a slow/hung request delays only that one command. Routing commands through the backend's existing connection would be the "clean" fix but is a larger change — deliberately not done.
- **`DialControl` timers** — `threading.Timer` callbacks (`_hide_bar`, `_clear_thumb_flash`) marshal their bodies onto the main thread via `self.ui(...)` and carry a timer-identity guard so a superseding `_flash_*` isn't clobbered by a stale deferred callback. `_send_volume` stays on its timer thread (it does network I/O and only touches the single `_pending_send_timer` attr).
- **Event-thread `_do_*` handlers** doing single-attribute optimistic writes (`_volume`, `_show_bar`, `_thumb_flash`) are left unlocked — GIL-atomic, self-correcting on the next state-update. Documented rather than serialized, to keep the diff small.
- **GTK/Adw calls must be on the main thread** (parent-repo hard constraint). `YTMDActionMixin.ui(fn, *args)` = `GLib.idle_add(lambda: fn(*args))` is the marshalling helper; `set_media`/`set_label` calls from any off-thread context go through it.

### Lifecycle handling

`on_ready()` is the framework's **redraw entry point**, not one-time init — `ActionCore.on_update()` calls it, and it re-fires on every page (re)load / state load, on the *same* action instance (instances are reused across reloads). The core clears the input's image just before that call. So:

- `YTMDActionMixin.on_ready()` subscribes to `state_store` **idempotently** (`getattr(self, "_state_token", None) is None` guard) — an unconditional subscribe would leak a subscription per page revisit. `on_disconnect()` unsubscribes and nulls the token, so a genuine teardown/re-ready still re-subscribes.
- `on_ready()` then calls `_reset_render_cache()` (nulls the fields listed in `_RENDER_CACHE_ATTRS`) before replaying the latest state, so the replayed `on_ytmd_state` falls through its change checks and does a **full repaint** instead of short-circuiting on stale `_last_*` values. Cached art (`_art_image` / `_raw_art_image`) is deliberately *not* reset — the replay re-pushes it via a thumbnail-cache hit rather than re-fetching.
- Actions that show a static placeholder icon (`PlayPause`, `TrackStep`) only set it when there is no art yet, so a revisit doesn't flash the icon over real art.
- `on_disconnect()` overrides must `super().on_disconnect()` — the base does the RPyC/backend-process teardown. `DialControl` also cancels its timers there.

### Actions / Event Assigner pattern

Actions that expose multiple functions on one input pass `default_events=False` to the base and register explicit `EventAssigner`s (`add_event_assigner(EventAssigner(id=..., ui_label=..., default_events=[...], callback=...))`), so a user can bind e.g. press→Next, hold→Previous on a single key, and rebind any of it in the UI. `YTMDActionMixin` overrides the unused `on_key_*` / `on_dial_*` no-ops with a `data=None` signature, because `EventAssigner.call(*args)` always forwards the hardware callback's data (even `None`) and the base classes' zero-arg defaults would crash.

**Reacting to assignment changes.** The framework gives no "an assignment changed" callback, but `ActionCore.load_event_overrides()` is called both on load and after every Event Assigner dropdown change, so overriding it (call `super()` first) is the seam. `VolumeControl` uses this to show its "Set Volume Level" `SpinRow` only while the `Set Volume` assigner is bound to a gesture: `event_manager.get_event_map().values()` contains the assigner iff it's bound (it has no default gesture), and the row's `.widget.set_visible(...)` is toggled from there. The config panel doesn't rebuild on assignment changes, so `set_visible` on the live widget is what makes it appear/disappear in place.

Per-action settings use `GtkHelper/GenerativeUI/` widgets (`ComboRow`, `SwitchRow`, `SpinRow`, `ColorButtonRow`), bound to a settings key on the action; they persist themselves into the page JSON. Read them with `row.get_value(fallback=...)` — `get_value()` returns the literal `fallback` when the key is unset, ignoring the row's own `default_value`, so pass the same default the row was constructed with (this is why `render_labels()` takes no hardcoded `fallback=`).

### Assets

Icons/colours are registered in `main.py` via `PluginBase.add_icon()` / `add_color()` from the maps in `actions/common/ytmd_action_base.py` (`ICON_ASSET_DEFAULTS`, `COLOR_ASSET_DEFAULTS`). They're user-overridable in this plugin's Settings → Assets / Colors tabs; the framework applies them as defaults only (a user override loaded from settings.json wins). The plugin still applies the colour itself on render — `paste_asset_icon()` uses the icon's alpha as a mask and tints it with the colour asset, because the framework has no "tint this icon by live state" concept.

Icons are shipped as **pre-rendered 512×512 PNGs**, not the `.svg` sources, to route around core bug #1 (`generate_svg_thumbnail()` rasterizes every SVG to a squashed 1024×96). The `.svg` files are kept alongside for regeneration; see `assets/icons/material/NOTICE.md` for attribution.

### Rendering discipline

`state-update` fires several times a second during playback (progress ticks). Actions must only touch the hardware when something displayed actually changed — compare against `_last_*` and bail early otherwise. Pushing a full image to the deck's render queue on every tick saturates its render loop and trips the low-FPS warning. Size art to `get_display_size()` (the real key/dial pixel size) before compositing; YTMD thumbnails run up to 544×544.

## Settings scopes

Two distinct, non-interchangeable persistence layers, both provided by the parent app:
- **Plugin-level settings** (`PluginBase.get_settings()`/`set_settings()`) — one JSON file per plugin, for state shared across all actions (YTMD host/port, client id, pairing token, `thumbnail_cache_max_entries`). There's no `GenerativeUI` widget set for this scope; the UI is built manually in `settings_area.py` via `PluginBase.get_settings_area()`.
- **Per-action settings** (`ActionCore.get_settings()`/`set_settings()`) — stored inside the page JSON, one copy per placed instance of an action. This is what `GenerativeUI` widgets read and write automatically when bound to an action.

## External dependencies

Plugin foreground code runs inside StreamController's own Python environment — don't assume any package beyond what the parent app's `requirements.txt` provides (see the parent `CLAUDE.md`'s "Hard constraints" section for the full list, and why removing an "unused" one can silently break other plugins). The foreground already relies on `requests` and `Pillow` from there.

Anything the app doesn't ship goes in the **backend venv** instead: `backend_requirements.txt` (currently `streamcontroller-plugin-tools`, `python-socketio[client]`), built by `__install__.py` via `create_venv`, recreated automatically by `PluginBase.launch_backend()` when the system Python version changes. The realtime feed lives in the backend for exactly this reason (Socket.IO isn't an app dep). The thumbnail cache stays in the *foreground* despite doing network I/O — its consumer is foreground PIL compositing, and RPyC can't cheaply pass images across.

## Framework source

Lives in the parent StreamController checkout, not this repo — chiefly `src/backend/PluginManager/` (`PluginBase.py`, `ActionHolder.py`, `ActionCore.py`, `InputBases.py`, `EventAssigner.py`/`EventManager.py`) and `GtkHelper/GenerativeUI/`, reachable at `../../../src/...` and `../../../GtkHelper/...` from this repo's root. `ActionBase` there is deprecated (kept only for old plugins, pre-wires legacy `on_key_down`-style assigners); this plugin uses `KeyAction`/`DialAction` on the current `ActionCore`.
