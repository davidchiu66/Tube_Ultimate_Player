from __future__ import annotations

import logging
from collections import OrderedDict

from PySide6.QtCore import QByteArray, QObject, QUrl, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QLabel


logger = logging.getLogger("tube_player.ui.thumbnail")


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
        data, failure = read_image_reply(reply)
        pixmap = QPixmap()
        success = not failure and pixmap.loadFromData(data)
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
        elif failure:
            logger.debug("thumbnail request ignored url=%s reason=%s", key[0], failure)
        else:
            logger.debug("thumbnail decode failed url=%s bytes=%s", key[0], data.size())

        for label in waiters:
            if success:
                label.setPixmap(cached)
                label.setText("")
            else:
                label.setPixmap(QPixmap())
                label.setText(error_text)
        reply.deleteLater()


def read_image_reply(reply: QNetworkReply) -> tuple[QByteArray, str]:
    url = reply.url().toString()
    if reply.error() != QNetworkReply.NetworkError.NoError:
        reply.readAll()
        return QByteArray(), f"network error {reply.error()}: {reply.errorString()}"

    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    if status is not None:
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            status_code = 0
        if status_code and not (200 <= status_code < 300):
            reply.readAll()
            return QByteArray(), f"http status {status_code}"

    content_type = str(reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader) or "").lower()
    if content_type and "image/" not in content_type:
        reply.readAll()
        return QByteArray(), f"unexpected content type {content_type}"

    data = reply.readAll()
    if data.isEmpty():
        return QByteArray(), "empty response"
    if not _looks_like_complete_image(data):
        return QByteArray(), f"response does not look like an image url={url}"
    return data, ""


def _looks_like_complete_image(data: QByteArray) -> bool:
    raw = bytes(data)
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    if raw.startswith(b"\xff\xd8\xff"):
        return raw.endswith(b"\xff\xd9")
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return raw.endswith(b";")
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP" and len(raw) >= 12:
        riff_size = int.from_bytes(raw[4:8], "little") + 8
        return len(raw) >= riff_size
    if raw.startswith(b"BM") and len(raw) >= 6:
        bmp_size = int.from_bytes(raw[2:6], "little")
        return len(raw) >= bmp_size
    if raw.startswith(b"\x00\x00\x01\x00") and len(raw) >= 6:
        return True
    return False
