from __future__ import annotations

import logging
import json
import re
import threading
import urllib.parse
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

from services.config_service import ConfigService
from services.cookie_service import load_browser_cookie_header, load_cookie_header


logger = logging.getLogger("tube_player.instagram_browser")
_HOME_URL = "https://www.instagram.com/"
# The discovery page runs in QtWebEngine (Chromium). Keep its UA coherent with
# the engine while importing the user's authenticated Firefox cookies.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_CAPTURE_SCRIPT = r"""
(() => {
  if (window.__tubePlayerInstagramCapture) return;
  const state = window.__tubePlayerInstagramCapture = {items: {}, error: ''};
  const text = value => String(value == null ? '' : value).trim();
  const scan = (value, depth = 0) => {
    if (depth > 8 || !value || typeof value !== 'object') return;
    if (Array.isArray(value)) { for (const item of value.slice(0, 300)) scan(item, depth + 1); return; }
    const permalink = text(value.permalink || value.url || value.webpage_url);
    const match = permalink.match(/instagram\.com\/(?:reel|p|tv)\/([A-Za-z0-9_-]+)/i);
    const shortcode = text(value.shortcode || value.code || (match && match[1]));
    const isVideo = value.is_video === true || Number(value.media_type) === 2 ||
      value.video_url || value.video_versions || match;
    if (shortcode && isVideo) {
      const caption = value.caption && typeof value.caption === 'object' ? value.caption.text : value.caption;
      const user = value.user || value.owner || {};
      const images = value.image_versions2 && value.image_versions2.candidates;
      state.items[shortcode] = {
        id: shortcode,
        url: permalink || `https://www.instagram.com/reel/${shortcode}/`,
        title: text(value.title || caption || value.accessibility_caption || shortcode).slice(0, 240),
        thumbnail: text(value.display_url || value.thumbnail_src || value.thumbnail_url || (Array.isArray(images) && images[0] && images[0].url)),
        uploader: text(user.full_name || user.username || '')
      };
    }
    for (const child of Object.values(value)) scan(child, depth + 1);
  };
  const consume = payload => { try { scan(payload); } catch (error) { state.error = text(error); } };
  const fetch0 = window.fetch;
  if (fetch0) window.fetch = async function(input) {
    const response = await fetch0.apply(this, arguments);
    response.clone().json().then(consume).catch(() => {});
    return response;
  };
  const open0 = XMLHttpRequest.prototype.open;
  const send0 = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) { this.__tubePlayerInstagramUrl = text(url); return open0.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener('loadend', () => { try { consume(JSON.parse(this.responseText)); } catch (_) {} }, {once: true});
    return send0.apply(this, arguments);
  };
})();
"""


class _InstagramPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
        if is_main_frame:
            host = url.host().strip().lower()
            if not (host == "instagram.com" or host.endswith(".instagram.com") or host.endswith(".cdninstagram.com")):
                logger.warning("blocked external Instagram navigation host=%s", host)
                return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)

    def createWindow(self, _window_type):  # noqa: N802
        return None


@dataclass(slots=True)
class _Request:
    operation: str
    keyword: str = ""
    page: int = 1
    page_size: int = 20
    timeout: float = 45.0
    event: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: BaseException | None = None
    cancelled: bool = False
    collect_attempts: int = 0


class InstagramBrowserService(QObject):
    request_enqueued = Signal(object)

    def __init__(self, config: ConfigService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._queue: deque[_Request] = deque()
        self._current: _Request | None = None
        self._profile: QWebEngineProfile | None = None
        self._page: QWebEnginePage | None = None
        self._view: QWebEngineView | None = None
        self._loaded = False
        self._shutdown = False
        self._cookie_source = ""
        self.request_enqueued.connect(self._enqueue, Qt.ConnectionType.QueuedConnection)

    def request_home(self, page: int = 1, page_size: int = 20, *, timeout: float = 45.0) -> dict:
        return self._submit("home", page=max(1, int(page)), page_size=page_size, timeout=timeout)

    def request_search(self, keyword: str, page: int = 1, page_size: int = 20, *, timeout: float = 45.0) -> dict:
        return self._submit("search", keyword=str(keyword or "").strip(), page=max(1, int(page)), page_size=page_size, timeout=timeout)

    def _submit(self, operation: str, *, keyword: str = "", page: int = 1, page_size: int, timeout: float) -> dict:
        if QThread.currentThread() is self.thread():
            raise RuntimeError("Instagram 浏览器请求不能阻塞 Qt 主线程")
        request = _Request(operation, keyword, max(1, int(page)), max(1, min(40, int(page_size))), max(10.0, float(timeout)))
        self.request_enqueued.emit(request)
        if not request.event.wait(request.timeout + 5.0):
            request.cancelled = True
            raise RuntimeError("Instagram 浏览器请求等待超时")
        if request.error is not None:
            raise request.error
        return request.result if isinstance(request.result, dict) else {"items": [], "has_more": False}

    @Slot(object)
    def _enqueue(self, request: _Request) -> None:
        if self._shutdown:
            request.error = RuntimeError("应用正在退出，Instagram 浏览器请求已取消")
            request.event.set()
            return
        self._queue.append(request)
        self._start_next()

    def _start_next(self) -> None:
        if self._current is not None or not self._queue or self._shutdown:
            return
        request = self._queue.popleft()
        if request.cancelled:
            request.error = RuntimeError("Instagram 浏览器请求已取消")
            request.event.set()
            return self._start_next()
        self._current = request
        self._ensure_page()
        self._sync_cookies()
        self._loaded = False
        self._reset_capture_state()
        # QWebEngine cookieStore writes asynchronously. Navigating immediately
        # after setCookie can send the first request without sessionid.
        QTimer.singleShot(220, lambda req=request: self._navigate_request(req))

    def _ensure_page(self) -> None:
        if self._page is not None:
            return
        self._profile = QWebEngineProfile(self)
        self._profile.setHttpUserAgent(_BROWSER_USER_AGENT)
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        capture = QWebEngineScript()
        capture.setName("tube-player-instagram-capture")
        capture.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        capture.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        capture.setRunsOnSubFrames(False)
        capture.setSourceCode(_CAPTURE_SCRIPT)
        self._profile.scripts().insert(capture)
        self._page = _InstagramPage(self._profile, self)
        self._page.loadFinished.connect(self._load_finished)
        self._view = QWebEngineView()
        self._view.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self._view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._view.resize(1280, 900)
        self._view.move(-20000, -20000)
        self._view.setPage(self._page)
        self._view.show()

    def _sync_cookies(self) -> None:
        if self._profile is None:
            return
        browser = str(self.config.cookie_browser_for_site("instagram") or "")
        cookie_file = str(self.config.cookie_file("instagram") or "")
        source = f"{browser}|{cookie_file}"
        if source == self._cookie_source:
            return
        self._profile.cookieStore().deleteAllCookies()
        header = load_cookie_header(cookie_file, _HOME_URL) if cookie_file else ""
        if not header and browser:
            header = load_browser_cookie_header(browser, _HOME_URL)
        logger.info(
            "instagram cookie sync browser=%s cookie_file=%s cookie_names=%s",
            browser,
            bool(cookie_file),
            [part.split("=", 1)[0].strip() for part in header.split(";") if "=" in part],
        )
        store = self._profile.cookieStore()
        origin = QUrl("https://www.instagram.com/")
        for part in header.split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            cookie = QNetworkCookie(name.encode(), value.encode())
            cookie.setDomain(".instagram.com")
            cookie.setPath("/")
            cookie.setSecure(True)
            store.setCookie(cookie, origin)
        self._cookie_source = source

    def _navigate_request(self, request: _Request) -> None:
        if self._current is not request or self._page is None:
            return
        target = _HOME_URL if request.operation == "home" else (
            "https://www.instagram.com/explore/search/keyword/?q=" + urllib.parse.quote(request.keyword)
        )
        self._page.setUrl(QUrl(target))
        QTimer.singleShot(int(request.timeout * 1000), lambda req=request: self._timeout_request(req))

    def _reset_capture_state(self) -> None:
        if self._page is not None:
            self._page.runJavaScript(
                "window.__tubePlayerInstagramCapture && (window.__tubePlayerInstagramCapture.items = {}, window.__tubePlayerInstagramCapture.error = '');"
            )

    def _load_finished(self, ok: bool) -> None:
        if self._current is None:
            return
        if not ok:
            return self._finish(error=RuntimeError("Instagram 页面加载失败，请检查网络、代理和 Cookie"))
        self._loaded = True
        QTimer.singleShot(1200, self._collect)

    def _collect(self) -> None:
        if self._current is None or self._page is None:
            return
        self._current.collect_attempts += 1
        # ``document.innerHTML`` is large and Instagram's Relay payload is
        # embedded as HTML text.  Reading it through QWebEngine's HTML
        # serializer is more reliable than evaluating a large regex-heavy JS
        # expression (which can silently return undefined on Chromium builds).
        self._page.toHtml(self._collect_html)

    def _collect_html(self, html_text: str) -> None:
        if self._current is None or self._page is None:
            return
        self._page.runJavaScript(
            "JSON.stringify({captured:(window.__tubePlayerInstagramCapture && Object.values(window.__tubePlayerInstagramCapture.items || {})) || [], url:String(location.href || ''), title:String(document.title || ''), pathname:String(location.pathname || ''), body:String(document.body && document.body.innerText || '').slice(0,3000)})",
            lambda payload, html=html_text: self._collected_html(html, payload),
        )

    def _collected_html(self, html_text: str, browser_state) -> None:
        if self._current is None:
            return
        try:
            state = json.loads(browser_state) if isinstance(browser_state, str) else (browser_state or {})
        except (TypeError, json.JSONDecodeError):
            state = {}
        source = str(html_text or "").replace("\\/", "/").replace("&quot;", '"').replace("&#x27;", "'")
        rows: list[dict] = []
        seen: set[str] = set()

        def add(code: str, url: str = "", title: str = "", thumbnail: str = "", uploader: str = "") -> None:
            code = str(code or "").strip()
            if not code or code in seen or code in {"en_US", "undefined"}:
                return
            seen.add(code)
            rows.append({"id": code, "url": url or f"https://www.instagram.com/reel/{code}/", "title": (title or code).strip()[:240], "thumbnail": thumbnail, "uploader": uploader})

        for match in re.finditer(r"(?:https?://(?:www\.)?instagram\.com)?/(?:reel|p|tv)/([A-Za-z0-9_-]+)", source, re.I):
            add(match.group(1), f"https://www.instagram.com/reel/{match.group(1)}/")
        code_matches = list(re.finditer(r"(?:\"code\"|&quot;code&quot;)\s*:\s*(?:\"|&quot;)([A-Za-z0-9_-]{5,})(?:\"|&quot;)", source))
        for match in code_matches:
            context = source[max(0, match.start() - 1000): min(len(source), match.end() + 50000)]
            if not re.search(r"(?:\"media_type\"|&quot;media_type&quot;)\s*:\s*2|(?:\"product_type\"|&quot;product_type&quot;)\s*:\s*(?:\"|&quot;)clips", context):
                continue
            code = match.group(1)
            user = (re.search(r"\"full_name\"\s*:\s*\"([^\"\\]{1,160})", context) or re.search(r"\"username\"\s*:\s*\"([^\"\\]{1,160})", context))
            caption = re.search(r"\"(?:caption|text)\"\s*:\s*\"([^\"\\]{1,240})", context)
            image = re.search(r"\"(?:thumbnail_url|display_url|image_url)\"\s*:\s*\"([^\"\\]{20,})", context)
            add(code, title=caption.group(1) if caption else code, thumbnail=image.group(1) if image else "", uploader=user.group(1) if user else "")
        for item in state.get("captured") or []:
            if isinstance(item, dict):
                add(item.get("id"), item.get("url", ""), item.get("title", ""), item.get("thumbnail", ""), item.get("uploader", ""))
        pathname = str(state.get("pathname") or "")
        body = str(state.get("body") or "").lower()
        self._collected(
            {
                "rows": rows,
                "url": state.get("url", ""),
                "title": state.get("title", ""),
                "pathname": pathname,
                "loginRequired": "/accounts/login" in pathname or "/challenge/" in pathname,
                "challenge": "/challenge/" in pathname
                or "/checkpoint/" in pathname
                or any(
                    marker in body
                    for marker in (
                        "suspicious login attempt",
                        "confirm it's you",
                        "security check",
                        "验证身份",
                        "安全验证",
                    )
                ),
            }
        )

    def _collected(self, payload) -> None:
        if self._current is None:
            return
        payload = payload if isinstance(payload, dict) else {"rows": payload or []}
        if payload.get("challenge"):
            self._finish(error=RuntimeError("Instagram 页面触发安全验证，请先在 Firefox 中完成验证后重试"))
            return
        if payload.get("loginRequired"):
            self._finish(error=RuntimeError("Instagram 页面未识别到登录状态，请重新检测或导入 Instagram Cookie"))
            return
        rows = payload.get("rows") or []
        items = [item for item in (rows or []) if isinstance(item, dict) and item.get("id")]
        required = self._current.page * self._current.page_size
        if len(items) < required and self._current.collect_attempts < 25:
            if self._page is not None:
                self._page.runJavaScript(
                    "window.scrollTo(0, document.body.scrollHeight);"
                    "for (const el of Array.from(document.querySelectorAll('div')).slice(0, 600)) "
                    "if (el.scrollHeight > el.clientHeight + 300) el.scrollTop = el.scrollHeight;"
                    "document.dispatchEvent(new Event('scroll'));"
                )
            QTimer.singleShot(1300, self._collect)
            return
        if not items:
            page_url = str(payload.get("url") or "")
            logger.warning(
                "instagram page returned no video cards url=%s title=%s pathname=%s",
                page_url,
                str(payload.get("title") or "")[:120],
                str(payload.get("pathname") or ""),
            )
        count = self._current.page_size
        start = (self._current.page - 1) * count
        self._finish(
            result={
                "items": items[start : start + count],
                "has_more": len(items) > start + count,
                "page_url": str(payload.get("url") or ""),
            }
        )

    def _timeout_request(self, request: _Request) -> None:
        if self._current is request:
            self._finish(error=RuntimeError("Instagram 页面加载超时，请确认已登录并完成安全验证"))

    def _finish(self, *, result: dict | None = None, error: BaseException | None = None) -> None:
        request = self._current
        self._current = None
        if request is None:
            return
        request.result = result
        request.error = error
        request.event.set()
        logger.info(
            "instagram browser request completed operation=%s count=%s page_url=%s",
            request.operation,
            len((result or {}).get("items", [])),
            (result or {}).get("page_url", ""),
        )
        QTimer.singleShot(0, self._start_next)

    def cancel_home_requests(self) -> None:
        """Cancel queued/current discovery requests when playback takes focus."""
        for request in self._queue:
            request.cancelled = True
        if self._current is not None:
            self._current.cancelled = True
            self._finish(error=RuntimeError("Instagram 首页请求已取消"))

    def shutdown(self) -> None:
        self._shutdown = True
        for request in list(self._queue):
            request.error = RuntimeError("应用正在退出，Instagram 浏览器请求已取消")
            request.event.set()
        self._queue.clear()
        if self._current is not None:
            self._current.cancelled = True
            self._finish(error=RuntimeError("应用正在退出，Instagram 浏览器请求已取消"))
        if self._view is not None:
            self._view.close()
            self._view.deleteLater()
            self._view = None
        self._page = None
        self._profile = None
