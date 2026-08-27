"""General-purpose image cache: memory LRU backed by an on-disk cache, keyed by any
caller-chosen string (not tied to "the currently playing track" or any YTMD-specific shape).

Owned once by the plugin (see main.py's `self.thumbnail_cache`) and shared by anything with
access to it - actions, settings UI, future widgets - to fetch/cache any image by
(key, url): queue art, playlist art, or anything else, not just now-playing art.
"""
import hashlib
import os
import threading
from collections import OrderedDict
from io import BytesIO
from typing import Callable

import requests
from loguru import logger as log
from PIL import Image

DEFAULT_MAX_ENTRIES = 30


class ThumbnailCache:
    def __init__(self, cache_dir: str, max_entries: int = DEFAULT_MAX_ENTRIES):
        self._cache_dir = cache_dir
        self._max_entries = max_entries
        self._memory: "OrderedDict[str, Image.Image]" = OrderedDict()
        self._inflight: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._disk_pruned = False

    def request(self, key: str, url: str, callback: Callable[[Image.Image | None], None]) -> None:
        """Resolve an image for `key` without blocking the caller.

        A memory-cache hit calls back immediately (no I/O); otherwise resolution (disk read,
        or network fetch as a last resort) happens in a background thread. Concurrent requests
        for the same `key` that arrive before the first one resolves are coalesced into that
        single resolution instead of triggering their own redundant fetch.
        """
        with self._lock:
            cached = self._memory.get(key)
            if cached is not None:
                self._memory.move_to_end(key)
                image = cached
            else:
                waiters = self._inflight.get(key)
                if waiters is not None:
                    waiters.append(callback)
                    log.info(f"[thumbnail] {key} - coalesced onto an in-flight resolution")
                    return
                self._inflight[key] = [callback]
                image = None

        if image is not None:
            log.info(f"[thumbnail] {key} - memory cache hit")
            callback(image)
            return

        threading.Thread(target=lambda: self._resolve(key, url), daemon=True).start()

    def _resolve(self, key: str, url: str) -> None:
        cache_dir = self._ensure_cache_dir()
        disk_path = self._disk_path(cache_dir, key)

        image = self._read_disk(disk_path)
        if image is not None:
            log.info(f"[thumbnail] {key} - disk cache hit ({disk_path})")
        else:
            log.info(f"[thumbnail] {key} - cache miss, fetching {url}")
            image = self._fetch_uncached(url)
            if image is not None:
                self._write_disk(cache_dir, disk_path, image, self._max_entries)
                log.info(f"[thumbnail] {key} - fetched and cached to disk ({disk_path})")
            else:
                log.info(f"[thumbnail] {key} - fetch failed, nothing cached")

        if image is not None:
            with self._lock:
                self._memory[key] = image
                self._memory.move_to_end(key)
                while len(self._memory) > self._max_entries:
                    self._memory.popitem(last=False)

        with self._lock:
            waiters = self._inflight.pop(key, [])
        for callback in waiters:
            callback(image)

    def _ensure_cache_dir(self) -> str:
        os.makedirs(self._cache_dir, exist_ok=True)
        if not self._disk_pruned:
            self._disk_pruned = True
            self._prune_disk(self._cache_dir, max_entries=self._max_entries)
        return self._cache_dir

    @staticmethod
    def _disk_path(cache_dir: str, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(cache_dir, f"{digest}.jpg")

    @staticmethod
    def _read_disk(disk_path: str) -> Image.Image | None:
        if not os.path.exists(disk_path):
            return None
        try:
            return Image.open(disk_path).convert("RGB")
        except Exception as e:
            log.error(f"Failed to read cached thumbnail {disk_path}: {e}")
            return None

    @staticmethod
    def _write_disk(cache_dir: str, disk_path: str, image: Image.Image, max_entries: int) -> None:
        try:
            image.save(disk_path, format="JPEG", quality=85)
        except Exception as e:
            log.error(f"Failed to write thumbnail cache file {disk_path}: {e}")
            return
        ThumbnailCache._prune_disk(cache_dir, max_entries=max_entries)

    @staticmethod
    def _prune_disk(cache_dir: str, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        """Local storage is preferred over re-fetching, kept bounded by recency (mtime) rather
        than needing any active invalidation - a given URL's image never changes."""
        try:
            entries = [os.path.join(cache_dir, name) for name in os.listdir(cache_dir)]
            entries = [p for p in entries if os.path.isfile(p)]
            entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for stale in entries[max_entries:]:
                os.remove(stale)
        except OSError as e:
            log.error(f"Failed to prune thumbnail cache dir {cache_dir}: {e}")

    def get_stats(self) -> tuple[int, int]:
        """Returns (file_count, total_bytes) for the on-disk cache - for a settings UI, not
        anything performance-sensitive, so no caching of the result."""
        try:
            entries = [os.path.join(self._cache_dir, name) for name in os.listdir(self._cache_dir)]
        except OSError:
            return 0, 0
        entries = [p for p in entries if os.path.isfile(p)]
        return len(entries), sum(os.path.getsize(p) for p in entries)

    def purge(self) -> None:
        """Deletes every cached thumbnail, in memory and on disk."""
        with self._lock:
            self._memory.clear()
        if not os.path.isdir(self._cache_dir):
            return
        try:
            for name in os.listdir(self._cache_dir):
                path = os.path.join(self._cache_dir, name)
                if os.path.isfile(path):
                    os.remove(path)
        except OSError as e:
            log.error(f"Failed to purge thumbnail cache dir {self._cache_dir}: {e}")

    def get_max_entries(self) -> int:
        return self._max_entries

    def set_max_entries(self, max_entries: int) -> None:
        """Live-updates the cache size limit and immediately prunes down to it - no restart
        needed, unlike settings actions read once at plugin startup."""
        self._max_entries = max_entries
        with self._lock:
            while len(self._memory) > self._max_entries:
                self._memory.popitem(last=False)
        if os.path.isdir(self._cache_dir):
            self._prune_disk(self._cache_dir, max_entries=self._max_entries)

    @staticmethod
    def _fetch_uncached(url: str) -> Image.Image | None:
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return Image.open(BytesIO(response.content)).convert("RGB")
        except Exception as e:
            log.error(f"Failed to fetch thumbnail {url}: {e}")
            return None
