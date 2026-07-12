from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, QUrl, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QLabel


class ThumbnailCache(QObject):
    def __init__(self, parent: QObject | None = None, max_items: int = 300) -> None:
        super().__init__(parent)
        self._max_items = max(50, int(max_items))
        self._pixmaps: OrderedDict[tuple[str, int, int], QPixmap] = OrderedDict()
        self._in_flight: dict[tuple[str, int, int], list[QLabel]] = {}

    def load(
        self,
        network: QNetworkAccessManager,
        url: str,
        size,
        label: QLabel,
        *,
        empty_text: str,
        error_text: str,
    ) -> None:
        normalized = str(url or "").strip()
        if not normalized:
            label.setPixmap(QPixmap())
            label.setText(empty_text)
            return

        key = (normalized, max(1, size.width()), max(1, size.height()))
        cached = self._pixmaps.get(key)
        if cached is not None:
            self._pixmaps.move_to_end(key)
            label.setPixmap(cached)
            label.setText("")
            return

        waiters = self._in_flight.setdefault(key, [])
        if label not in waiters:
            waiters.append(label)
        if len(waiters) > 1:
            return

        reply = network.get(QNetworkRequest(QUrl(normalized)))
        reply.finished.connect(lambda: self._handle_finished(reply, key, error_text))

    def _handle_finished(
        self,
        reply: QNetworkReply,
        key: tuple[str, int, int],
        error_text: str,
    ) -> None:
        waiters = self._in_flight.pop(key, [])
        data = reply.readAll()
        pixmap = QPixmap()
        success = pixmap.loadFromData(data)
        cached = QPixmap()
        if success:
            cached = pixmap.scaled(
                key[1],
                key[2],
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._pixmaps[key] = cached
            self._pixmaps.move_to_end(key)
            while len(self._pixmaps) > self._max_items:
                self._pixmaps.popitem(last=False)

        for label in waiters:
            if success:
                label.setPixmap(cached)
                label.setText("")
            else:
                label.setPixmap(QPixmap())
                label.setText(error_text)
        reply.deleteLater()
