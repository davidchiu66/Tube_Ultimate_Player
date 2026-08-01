from __future__ import annotations

import logging
import weakref
from collections import OrderedDict

from PySide6.QtCore import QByteArray, QObject, QUrl, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QLabel


logger = logging.getLogger("tube_player.ui.thumbnail")

# 一屏封面会同时发出几十个请求。Qt6 默认对 HTTPS 启用 HTTP/2，这些请求会被复用到
# 同一条连接的多个 stream 上，图片 CDN 往回打 RST_STREAM(INTERNAL_ERROR) 时，Qt 就在
# 控制台刷 `qt.network.http2: stream N error: "Internal server error"`。
# 封面是一堆小文件，HTTP/2 的多路复用收益很小，改用 HTTP/1.1（每主机 6 连接）
# 既能消掉这些报错，也更稳。
def build_image_request(url: str) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setAttribute(QNetworkRequest.Attribute.Http2AllowedAttribute, False)
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    return request


class ThumbnailCache(QObject):
    def __init__(self, parent: QObject | None = None, max_items: int = 300) -> None:
        super().__init__(parent)
        self._max_items = max(50, int(max_items))
        self._pixmaps: OrderedDict[tuple[str, int, int], QPixmap] = OrderedDict()
        # 等待者只持弱引用：页面切换后卡片可以正常回收，回调也不会让已销毁的控件复活。
        self._in_flight: dict[tuple[str, int, int], list[weakref.ReferenceType[QLabel]]] = {}

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

        waiters = self._in_flight.get(key)
        if waiters is not None:
            # 同一张图已在请求中，只登记等待者，不重复发起网络请求。
            if not any(ref() is label for ref in waiters):
                waiters.append(weakref.ref(label))
            return

        self._in_flight[key] = [weakref.ref(label)]
        reply = network.get(build_image_request(normalized))
        reply.finished.connect(lambda: self._handle_finished(reply, key, error_text))

    def cancel_for(self, label: QLabel) -> None:
        """注销某个控件的等待登记，供页面清空卡片前调用。"""
        for key in list(self._in_flight):
            waiters = self._in_flight[key]
            remaining = [ref for ref in waiters if ref() is not None and ref() is not label]
            if len(remaining) != len(waiters):
                self._in_flight[key] = remaining

    def pending_count(self) -> int:
        """在途请求数，供测试与诊断使用。"""
        return len(self._in_flight)

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

        for ref in waiters:
            label = ref()
            if label is None:
                continue
            # 控件的 C++ 对象可能已被销毁（页面切换、卡片重建），此时逐个吞掉
            # RuntimeError，避免一个失效控件中断同批次其余等待者的回填。
            try:
                if success:
                    label.setPixmap(cached)
                    label.setText("")
                else:
                    label.setPixmap(QPixmap())
                    label.setText(error_text)
            except RuntimeError:
                logger.debug("thumbnail target already destroyed url=%s", key[0])
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
