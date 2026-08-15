from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from urllib.parse import urlparse

from resolver.models import VideoQuality


@dataclass(frozen=True)
class NetworkMeasurement:
    site: str
    host: str
    proxy: str
    kbps: float
    measured_at: float


class NetworkMeasurementCache:
    def __init__(self, *, ttl_seconds: float = 300.0, max_items: int = 16) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_items = max(1, int(max_items))
        self._items: OrderedDict[tuple[str, str, str, int, str], NetworkMeasurement] = OrderedDict()
        self._lock = RLock()

    def key(self, site: str, url: str, proxy: str = "") -> tuple[str, str, str, int, str]:
        parsed = urlparse(str(url or ""))
        scheme = parsed.scheme.lower()
        port = parsed.port or (443 if scheme == "https" else 80 if scheme == "http" else 0)
        return (str(site or "").lower(), scheme, (parsed.hostname or "").lower(), int(port), str(proxy or ""))

    def get(self, site: str, url: str, proxy: str = "") -> NetworkMeasurement | None:
        key = self.key(site, url, proxy)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if time.monotonic() - item.measured_at > self.ttl_seconds:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return item

    def put(self, measurement: NetworkMeasurement, url: str) -> None:
        key = self.key(measurement.site, url, measurement.proxy)
        with self._lock:
            self._items[key] = measurement
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


def select_quality_for_bandwidth(
    qualities: dict[str, VideoQuality],
    kbps: float | None,
    *,
    safety_factor: float = 0.65,
) -> VideoQuality | None:
    """Select the highest stream whose estimated bitrate fits the measured budget."""
    if not qualities:
        return None
    ordered = sorted(qualities.values(), key=lambda q: (int(q.height or 0), int(q.fps or 0), float(q.tbr or 0)))
    if kbps is None or kbps <= 0:
        return _tier_by_height(ordered, 720)
    budget = float(kbps) * float(safety_factor)
    fitting = [quality for quality in ordered if _required_kbps(quality) <= budget]
    return fitting[-1] if fitting else ordered[0]


def _tier_by_height(qualities: list[VideoQuality], target: int) -> VideoQuality:
    below = [q for q in qualities if int(q.height or 0) <= target]
    if below:
        return below[-1]
    return qualities[0]


def _required_kbps(quality: VideoQuality) -> float:
    if (quality.tbr or 0) > 0:
        video_kbps = float(quality.tbr or 0)
    else:
        height = int(quality.height or 0)
        if height <= 480:
            video_kbps = 1500.0
        elif height <= 720:
            video_kbps = 3000.0
        elif height <= 1080:
            video_kbps = 6000.0
        elif height <= 1440:
            video_kbps = 12000.0
        else:
            video_kbps = 25000.0
    return video_kbps + float(quality.audio_tbr or 192)
