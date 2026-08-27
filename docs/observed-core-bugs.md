# Observed StreamController core bugs

Three bugs in StreamController's own core (not this plugin) turned up while building this
plugin. None block this plugin - all are worked around or avoided on the plugin side - but all
three are real bugs worth fixing upstream in the parent `StreamController` checkout.

## 1. SVG icon assets render squashed (non-square) - fixed on our side, not in core

**File:** `src/backend/MediaManager.py`, `generate_svg_thumbnail()`

```python
def generate_svg_thumbnail(self, file_path):
    return svg_to_pil(file_path, 1024)
```

`svg_to_pil` (`src/backend/DeckManagement/HelperMethods.py`) is defined as
`svg_to_pil(svg_path: str, width: int = 96, height: int = 96)`. The call above only passes the
`width` positional argument, so `height` silently keeps its default of `96`. Every SVG-based
`Icon` asset therefore gets rasterized into a **1024x96** canvas regardless of the SVG's actual
aspect ratio, squashing anything that isn't already that shape.

**Confirmed via:**

```python
>>> svg_to_pil('some_square_icon.svg', 1024)
<PIL.Image ... size=(1024, 96) ...>
```

**Fix:** `svg_to_pil(file_path, 1024, 1024)` (or whatever the intended fixed thumbnail
resolution actually is).

**Workaround used in this plugin:** register pre-rendered, correctly-square 512x512 PNGs
instead of the `.svg` files directly - this routes through the framework's plain-raster-image
loading path (`is_image()` -> `Image.open(path)`) instead of the buggy SVG path entirely. See
`actions/common/ytmd_action_base.py`'s `ICON_ASSET_DEFAULTS` and
`assets/icons/material/NOTICE.md`.

## 2. `Observer.notify()` produces `RuntimeWarning: coroutine 'Observer._notify' was never awaited`

**File:** `src/backend/PluginManager/PluginSettings/Observer.py`

```python
def notify(self, *args, **kwargs):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(self._notify(*args, **kwargs))  # Schedule _notify as a coroutine
        else:
            loop.run_until_complete(self._notify(*args, **kwargs))
        return
    except:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._notify(*args, **kwargs))
    finally:
        loop.close()
```

`notify()` tries to detect a running asyncio event loop and schedule `self._notify(...)` onto
it via `asyncio.ensure_future(...)`. But StreamController's actual event loop is GLib's, not
asyncio's - nothing ever drives that scheduled coroutine to completion, so Python eventually
garbage-collects it unawaited and prints the RuntimeWarning.

**Triggered by:** any call to `Manager.add_asset()` / `add_override()` / `remove_asset()` - i.e.
`PluginBase.add_icon()` / `add_color()` - so any plugin registering icon/color assets hits this,
once per asset registered per plugin startup. This plugin registers 8 icons + 10 colors, so it's
loud (18 warnings every launch).

**Impact:** cosmetic only, as far as we can tell. The actual `self._assets[key] = asset` update
happens before `notify()` is called; only the "tell any currently-open Assets/Colors settings
page to refresh its view" step gets silently dropped, which doesn't matter unless that page
happens to be open at the exact moment of registration (plugin startup, before the user could
plausibly have that settings page open yet).

**Scope of a fix:** `grep -rl "PluginSettings.Observer" src/` turns up exactly one user -
`Manager.py`. Not a shared utility used elsewhere, so a fix here is narrowly scoped.

**Possible fix** - drop the asyncio scheduling entirely and just call each observer
synchronously (every actual observer in this codebase - the `IconPage`/`ColorPage` UI-refresh
callbacks - is a plain sync method, not a coroutine, so `_ensure_coroutine`'s
coroutine-vs-`asyncio.to_thread` branching isn't buying anything today):

```python
def notify(self, *args, **kwargs):
    for observer in self.observers:
        try:
            observer(*args, **kwargs)
        except Exception as e:
            log.error(f"Callback {getattr(observer, '__name__', observer)} could not be called: {e}")
```

If an async observer is ever genuinely needed later, that would need explicit handling (e.g.
`asyncio.run(...)` per call, or a real GLib-integrated event loop) rather than the current
fire-and-hope scheduling.

**Not fixed here** - deliberately left as-is per instruction, for a future session to pick up
in the main StreamController checkout (this file lives in the plugin repo only for visibility;
the actual fix belongs in `src/backend/PluginManager/PluginSettings/Observer.py` in the parent
project, not here).

## 3. Action list row markup breaks on unescaped special characters in `action_name`

**File:** `src/windows/mainWindow/elements/Sidebar/elements/ActionManager.py`

```python
self.label = Gtk.Label(label=f"<b>{self.action_name}</b> <span color=\"#979797\">({self.action_category})</span>", use_markup=True, ...)
```

`self.action_name` (an `ActionHolder`'s `action_name`, set by the plugin author) and
`self.action_category` are interpolated directly into a Pango markup string with
`use_markup=True`, without escaping (`GLib.markup_escape_text()`). Pango markup is XML, so any
of `&`, `<`, `>` in either string produces invalid markup. GTK's failure mode for invalid markup
here is to fall back to showing the raw, unrendered string - literal `<b>`, `<span ...>` tags
and all - instead of the intended bold name + gray category.

**Reproduction:** this plugin registered an action with `action_name="Shuffle & Repeat"`. The
Sidebar's "Actions for this key" row for it showed the literal text
`<b>Shuffle & Repeat</b> <span color="#979797">(YTMDesktop Controller)</span>` instead of
rendering it.

**Impact:** any plugin whose `action_name` or the enclosing category/plugin display name
contains `&`, `<`, or `>` breaks this specific row's rendering. Not data loss or a crash, just
a broken/ugly settings-sidebar label.

**Fix:** escape both interpolated values, e.g.:

```python
from gi.repository import GLib
name = GLib.markup_escape_text(self.action_name)
category = GLib.markup_escape_text(self.action_category)
self.label = Gtk.Label(label=f"<b>{name}</b> <span color=\"#979797\">({category})</span>", use_markup=True, ...)
```

**Workaround used in this plugin:** renamed the affected action from `"Shuffle & Repeat"` to
`"Shuffle/Repeat"` (see `main.py`) - avoids the character, doesn't fix the underlying bug. Any
other plugin (or a future action added to this one) with `&`/`<`/`>` in its `action_name` would
still hit this.

**Not fixed here** - same as #2, documented for a future session; the fix belongs in
`src/windows/mainWindow/elements/Sidebar/elements/ActionManager.py` in the parent project.
