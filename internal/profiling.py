"""Opt-in lightweight counters for the YTMD plugin.

A complete no-op unless YTMD_PROFILE is set to something truthy in the environment before
StreamController launches. When enabled, a background thread logs a summary every
YTMD_PROFILE_INTERVAL seconds (default 10) to the plugin log:

  - state_update.recv  - how often the backend relays a YTMD state-update
  - dispatch           - action callbacks that fans out to (recv x placed actions)
  - on_ytmd_state      - wall time spent in those callbacks, on the GTK main thread
  - ui_push            - set_media()/set_label()-style hardware pushes marshalled via ui()

The point is to answer "is the plugin itself hot" without a full profiler; pair it with
`py-spy` against the running process for a function-level / per-thread breakdown.
"""
import os
import threading
import time
from collections import defaultdict

from loguru import logger as log

ENABLED = os.environ.get("YTMD_PROFILE", "").strip().lower() not in ("", "0", "false", "no")
REPORT_INTERVAL = float(os.environ.get("YTMD_PROFILE_INTERVAL", "10"))

_lock = threading.Lock()
_counters: "defaultdict[str, float]" = defaultdict(float)
_started = False


def incr(name: str, by: float = 1.0) -> None:
    if not ENABLED:
        return
    with _lock:
        _counters[name] += by


def _record_time(name: str, seconds: float) -> None:
    with _lock:
        _counters[name + ".calls"] += 1
        _counters[name + ".seconds"] += seconds
        if seconds > _counters[name + ".max_seconds"]:
            _counters[name + ".max_seconds"] = seconds


def wrap_callback(cb):
    """Return `cb` unchanged unless profiling is on, in which case time every invocation
    under `on_ytmd_state`."""
    if not ENABLED:
        return cb

    def _timed(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return cb(*args, **kwargs)
        finally:
            _record_time("on_ytmd_state", time.perf_counter() - t0)

    return _timed


def ensure_reporter() -> None:
    global _started
    if not ENABLED or _started:
        return
    _started = True
    threading.Thread(target=_report_loop, name="ytmd_profile_reporter", daemon=True).start()
    log.info(f"[ytmd-profile] enabled - reporting every {REPORT_INTERVAL:g}s")


def _report_loop() -> None:
    prev: "dict[str, float]" = {}
    while True:
        time.sleep(REPORT_INTERVAL)
        with _lock:
            snap = dict(_counters)

        lines = []
        for key in sorted(snap):
            if key.endswith((".calls", ".max_seconds")):
                continue
            if key.endswith(".seconds"):
                base = key[: -len(".seconds")]
                total = snap[key] - prev.get(key, 0.0)
                calls = snap.get(base + ".calls", 0.0) - prev.get(base + ".calls", 0.0)
                mean_ms = (total / calls * 1000) if calls else 0.0
                max_ms = snap.get(base + ".max_seconds", 0.0) * 1000
                pct = 100 * total / REPORT_INTERVAL
                lines.append(
                    f"  {base}: {calls:.0f} calls, {total * 1000:.1f}ms total "
                    f"(mean {mean_ms:.2f}ms, max {max_ms:.2f}ms) = {pct:.1f}% of one core"
                )
            else:
                delta = snap[key] - prev.get(key, 0.0)
                lines.append(f"  {key}: {delta:.0f} ({delta / REPORT_INTERVAL:.1f}/s)")
        prev = snap
        log.info("[ytmd-profile] last %gs:\n%s" % (REPORT_INTERVAL, "\n".join(lines) or "  (idle)"))
