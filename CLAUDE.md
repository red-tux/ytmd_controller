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
