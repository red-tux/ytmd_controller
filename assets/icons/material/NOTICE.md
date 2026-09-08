These icons (`shuffle`, `repeat`, `repeat_one`, `thumb_up`, `thumb_down`, `volume_up`,
`volume_down`, `pause`, `report_problem` - both the `.svg` sources and the `.png` renders of
them) are from Google's Material Icons set:

https://github.com/google/material-design-icons

Copyright Google LLC, licensed under the Apache License, Version 2.0 (see `LICENSE` in this
directory, or https://www.apache.org/licenses/LICENSE-2.0).

The `.svg` files are unmodified. The `.png` files are plain rasterizations of the same SVGs at
512x512 (via `cairosvg`), generated once and committed rather than rendered at runtime, and are
what's actually registered as this plugin's default icon assets - see
`actions/common/ytmd_action_base.py`'s `ICON_ASSET_DEFAULTS` for why (a bug in
StreamController's own SVG-to-image loading path squashes SVG icon assets to a non-square
aspect ratio; registering pre-rendered square PNGs instead sidesteps it). Both the icon's shape
and its tint color are recolored/re-tinted at render time
(`actions/common/ytmd_action_base.py`'s `paste_asset_icon`) and independently user-overridable
through this plugin's own Settings dialog (Assets/Colors tabs).
