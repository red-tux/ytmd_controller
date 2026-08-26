# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A StreamController plugin providing Stream Deck control of [YouTube Music Desktop](https://github.com/ytmdesktop/ytmdesktop) (YTMD) via its Companion Server API. This repo (`net_red-tux_ytmd_controller`, remote `git@github.com:red-tux/ytmd_controller.git`) is developed as a nested git checkout inside a StreamController dev tree, at `<StreamController>/data/plugins/net_red-tux_ytmd_controller/`. StreamController discovers plugins by folder name under `<data>/plugins/`, so this location doubles as the plugin's install path for local testing — no separate install step is needed during development.

## Running / testing this plugin

There is no standalone build or test harness in this repo — a plugin only runs inside a StreamController instance. From the parent StreamController checkout:

```sh
python3 main.py --devel --data data --close-running
```

(`--devel` and `--data data` are documented in the parent repo's `CLAUDE.md`.) Plugins are imported once at startup — there is no hot reload, so restart the app after code changes.

The plugin id and folder name must stay in sync: `manifest.json`'s `id` field (falling back to the folder name if blank) is the plugin id, used as the prefix for every action id (`<plugin_id>::<ActionName>`).

### Known dev-container bug: text labels don't render (PIL/GTK FreeType conflict)

In this devcontainer specifically, `set_top_label`/`set_center_label`/`set_bottom_label` calls (both from this plugin and from StreamController's own manual Label Editor) silently fail to show any visible text, and raising a label's outline width can crash with `PIL.Image.DecompressionBombError`.

Root cause, confirmed independent of any StreamController or plugin code: this venv's pip-installed Pillow bundles its own FreeType (`pillow.libs/libfreetype-*.so`), distinct from the system FreeType GTK/Pango loads. Importing GTK before touching PIL is enough to corrupt PIL's own text-measurement calls afterward — `ImageDraw.textbbox()` on the string `"Hello World"` at 15pt comes back ~86px wide with PIL alone, but literally millions of pixels wide (in either direction) once GTK has been imported in the same process. That garbage width is what pushes text off-canvas (invisible) and, with a larger stroke/outline width, over PIL's decompression-bomb limit.

- Verified this is devcontainer-specific: a separate real Flatpak install (1.5.0-beta.15, fake deck) renders plain labels correctly. Flatpak's GNOME SDK runtime version-locks GTK and its dependents together, avoiding the mismatch a raw pip venv can hit.
- Not a quick fix: `LD_PRELOAD`-ing the system libfreetype over PIL's bundled one did not resolve it (produced different garbage) — this needs an actual environment/packaging fix (e.g. rebuilding Pillow against the system FreeType), not an application-level workaround.
- Practical implication: don't try to visually verify anything that depends on framework text-label rendering (title/artist labels, any `set_*_label` output) in this devcontainer — test on a real install instead. Pure image rendering (album art, the hand-drawn volume/progress bars) is unaffected, since it never goes through PIL's font/text measurement.

## Current state

This repo is still the stock `PluginTemplate` scaffold (StreamController's plugin starter), not yet YTMD-specific:
- `main.py` — `PluginTemplate(PluginBase)`, registers one action, `SimpleAction`.
- `actions/SimpleAction/SimpleAction.py` — a placeholder action using the **deprecated** `ActionBase` class.
- `manifest.json` — mostly empty (`id`/`name`/`version`/`thumbnail` all blank).

When building the real actions, prefer the parent app's current action pattern over what the template shows: subclass `ActionCore` directly, or one of its `InputBases.py` mixins (`KeyAction`, `DialAction`, `TouchScreenAction`), instead of the deprecated `ActionBase`. `ActionBase` is kept only for backward compatibility with older plugins and pre-wires legacy `on_key_down`/`on_key_up`-style event assigners; `KeyAction` etc. do the same thing on the current base class.

Framework source lives in the parent StreamController checkout, not in this repo — chiefly `src/backend/PluginManager/` (`PluginBase.py`, `ActionHolder.py`, `ActionCore.py`, `InputBases.py`, `EventAssigner.py`/`EventManager.py`) and `GtkHelper/GenerativeUI/` (per-action settings widgets: `SwitchRow`, `ComboRow`, `EntryRow`, `ScaleRow`, etc.), reachable at `../../../src/...` and `../../../GtkHelper/...` from this repo's root.

## Settings scopes

Two distinct, non-interchangeable persistence layers, both provided by the parent app:
- **Plugin-level settings** (`PluginBase.get_settings()`/`set_settings()`) — one JSON file per plugin, for state shared across all actions (e.g. a YTMD host/port and companion-server pairing token). There's no `GenerativeUI` widget set for this scope; the UI is built manually by overriding `PluginBase.get_settings_area()`.
- **Per-action settings** (`ActionCore.get_settings()`/`set_settings()`) — stored inside the page JSON, one copy per placed instance of an action. This is what `GenerativeUI` widgets read and write automatically when bound to an action.

## External dependencies

Plugin code runs inside StreamController's own Python environment by default — don't assume any package beyond what the parent app's `requirements.txt` provides (see the parent `CLAUDE.md`'s "Hard constraints" section for the full list, and why removing an "unused" one can silently break other plugins). If YTMD integration ends up needing a dependency the app doesn't ship, isolate it via this plugin's own `__install__.py` + venv + RPyC backend process (`PluginBase.launch_backend()` / `recreate_venv()`) rather than assuming it can be added to the main app's requirements.
