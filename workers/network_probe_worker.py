from __future__ import annotations

import time
import urllib.request

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class NetworkProbeSignals(QObject):
    success = Signal(float)
    error = Signal(str)
    finished = Signal()


class NetworkProbeWorker(QRunnable):
    def __init__(self, url: str, headers: dict[str, str] | None = None, proxy: str = "") -> None:
        super().__init__()
        self.url = str(url or "")
        self.headers = dict(headers or {})
        self.proxy = str(proxy or "")
        self.signals = NetworkProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            if not self.url.lower().startswith(("http://", "https://")):
                raise ValueError("probe URL is not HTTP(S)")
            request_headers = {k: v for k, v in self.headers.items() if k.lower() not in {"content-length", "host"}}
            request_headers["Range"] = "bytes=0-1048575"
            request_headers.setdefault("User-Agent", "Mozilla/5.0")
            request = urllib.request.Request(self.url, headers=request_headers)
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}) if self.proxy
                else urllib.request.ProxyHandler({})
            )
            started = time.monotonic()
            total = 0
            with opener.open(request, timeout=3.0) as response:
                while total < 1024 * 1024 and time.monotonic() - started < 5.0:
                    chunk = response.read(min(64 * 1024, 1024 * 1024 - total))
                    if not chunk:
                        break
                    total += len(chunk)
            elapsed = max(0.001, time.monotonic() - started)
            if total < 128 * 1024:
                raise ValueError("probe sample too small")
            self.signals.success.emit(total * 8.0 / elapsed / 1000.0)
        except Exception as exc:
            # 签名 CDN URL 常含临时令牌，错误对象可能把完整 URL 串进文本，不能向日志透传。
            self.signals.error.emit(type(exc).__name__ or "network probe failed")
        finally:
            self.signals.finished.emit()
