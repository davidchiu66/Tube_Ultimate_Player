from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript

from services.config_service import ConfigService
from services.cookie_service import load_browser_cookie_header


logger = logging.getLogger("tube_player.douyin_browser")


@dataclass(slots=True)
class _BrowserRequest:
    request_id: int
    endpoint: str
    params: dict[str, str]
    referer: str
    timeout: float
    operation: str = "json"
    page: int = 1
    target_count: int = 0
    event: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: BaseException | None = None
    deadline: float = 0.0


class DouyinBrowserService(QObject):
    """Run signed Douyin API requests inside the application's Chromium runtime.

    Douyin validates the page-generated X-Bogus value together with the browser
    transport fingerprint. Resolver workers therefore enqueue work here and
    wait while the GUI thread signs and fetches the request in QWebEngine.
    """

    request_enqueued = Signal(object)

    def __init__(self, config: ConfigService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._queue: deque[_BrowserRequest] = deque()
        self._current: _BrowserRequest | None = None
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._profile: QWebEngineProfile | None = None
        self._page: QWebEnginePage | None = None
        self._ready = False
        self._loading = False
        self._shutdown = False
        self._ready_attempts = 0
        self._cookie_source = ""
        self._cookie_loaded_at = 0.0
        self.request_enqueued.connect(self._enqueue, Qt.ConnectionType.QueuedConnection)

    def request_json(
        self,
        endpoint: str,
        params: dict[str, str],
        referer: str,
        *,
        timeout: float = 35.0,
    ) -> dict:
        return self._submit_request(
            endpoint,
            params,
            referer,
            timeout=timeout,
        )

    def request_home_json(
        self,
        endpoint: str,
        params: dict[str, str],
        referer: str,
        *,
        page: int,
        target_count: int,
        timeout: float = 45.0,
    ) -> dict:
        return self._submit_request(
            endpoint,
            params,
            referer,
            timeout=timeout,
            operation="home",
            page=max(1, int(page)),
            target_count=max(1, int(target_count)),
        )

    def _submit_request(
        self,
        endpoint: str,
        params: dict[str, str],
        referer: str,
        *,
        timeout: float,
        operation: str = "json",
        page: int = 1,
        target_count: int = 0,
    ) -> dict:
        if QThread.currentThread() is self.thread():
            raise RuntimeError("抖音浏览器请求不能阻塞 Qt 主线程")
        with self._sequence_lock:
            self._sequence += 1
            request_id = self._sequence
        request = _BrowserRequest(
            request_id=request_id,
            endpoint=str(endpoint),
            params={str(key): str(value) for key, value in params.items()},
            referer=str(referer or "https://www.douyin.com/jingxuan"),
            timeout=max(5.0, float(timeout)),
            operation=operation,
            page=page,
            target_count=target_count,
        )
        self.request_enqueued.emit(request)
        if not request.event.wait(request.timeout + 5.0):
            raise RuntimeError("抖音浏览器请求等待超时")
        if request.error is not None:
            raise RuntimeError(str(request.error)) from request.error
        if not isinstance(request.result, dict):
            raise RuntimeError("抖音浏览器返回数据格式无效")
        return request.result

    @Slot(object)
    def _enqueue(self, request: _BrowserRequest) -> None:
        if self._shutdown:
            request.error = RuntimeError("应用正在退出，抖音浏览器请求已取消")
            request.event.set()
            return
        self._queue.append(request)
        self._start_next()

    def _start_next(self) -> None:
        if self._current is not None or not self._queue or self._shutdown:
            return
        self._current = self._queue.popleft()
        self._current.deadline = time.monotonic() + self._current.timeout
        self._ensure_page()
        if self._ready:
            self._execute_current()

    def _ensure_page(self) -> None:
        if self._page is not None:
            if not self._ready and not self._loading:
                self._load_runtime_page()
            return
        self._profile = QWebEngineProfile("tube-player-douyin", self)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        capture_script = QWebEngineScript()
        capture_script.setName("tube-player-douyin-capture")
        capture_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        capture_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        capture_script.setRunsOnSubFrames(False)
        capture_script.setSourceCode(
            """
            (() => {
                window.__tubePlayerDouyinCaptured = [];
                const save = (url, body) => {
                    if (!/aweme\\/v1.*(tab\\/feed|multi\\/aweme\\/detail)/.test(String(url))) return;
                    window.__tubePlayerDouyinCaptured.push({url: String(url), body});
                    if (window.__tubePlayerDouyinCaptured.length > 12) {
                        window.__tubePlayerDouyinCaptured.shift();
                    }
                };
                const nativeFetch = window.fetch;
                window.fetch = async (...args) => {
                    const response = await nativeFetch(...args);
                    try {
                        response.clone().text().then(body => save(args[0] && args[0].url || args[0], body));
                    } catch (_) {}
                    return response;
                };
                const nativeOpen = XMLHttpRequest.prototype.open;
                const nativeSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    this.__tubePlayerUrl = url;
                    return nativeOpen.call(this, method, url, ...rest);
                };
                XMLHttpRequest.prototype.send = function(...args) {
                    this.addEventListener('load', () => {
                        try { save(this.__tubePlayerUrl, this.responseText); } catch (_) {}
                    });
                    return nativeSend.apply(this, args);
                };
            })()
            """
        )
        self._profile.scripts().insert(capture_script)
        self._page = QWebEnginePage(self._profile, self)
        self._page.loadFinished.connect(self._runtime_loaded)
        self._load_runtime_page()

    def _load_runtime_page(self) -> None:
        if self._page is None or self._current is None:
            return
        self._loading = True
        self._ready = False
        self._ready_attempts = 0
        self._sync_browser_cookies(self._current.referer)
        QTimer.singleShot(
            150,
            lambda: self._page and self._page.setUrl(QUrl("https://www.douyin.com/jingxuan")),
        )

    @Slot(bool)
    def _runtime_loaded(self, ok: bool) -> None:
        self._loading = False
        if not ok:
            self._finish_current(error=RuntimeError("抖音浏览器运行页加载失败"))
            return
        self._poll_runtime_ready()

    def _poll_runtime_ready(self) -> None:
        if self._page is None or self._current is None:
            return
        self._ready_attempts += 1
        self._page.runJavaScript(
            "Boolean(window.byted_acrawler && window.byted_acrawler.frontierSign)",
            self._runtime_ready_result,
        )

    def _runtime_ready_result(self, ready) -> None:
        if self._current is None:
            return
        if bool(ready):
            self._ready = True
            logger.info("douyin browser signing runtime ready")
            self._execute_current()
            return
        if self._ready_attempts >= 40 or time.monotonic() >= self._current.deadline:
            self._finish_current(error=RuntimeError("抖音页面签名组件加载超时"))
            return
        QTimer.singleShot(250, self._poll_runtime_ready)

    def _execute_current(self) -> None:
        request = self._current
        if request is None or self._page is None:
            return
        self._sync_browser_cookies(request.referer)
        if request.operation == "home":
            self._execute_home_request(request)
            return
        unsigned_url = request.endpoint
        query = urllib.parse.urlencode(request.params)
        if query:
            unsigned_url += ("&" if "?" in unsigned_url else "?") + query
        script = f"""
            (async () => {{
                const requestId = {request.request_id};
                window.__tubePlayerDouyinResult = null;
                try {{
                    const referer = new URL({json.dumps(request.referer)});
                    history.replaceState(null, '', referer.pathname + referer.search);
                    let signature = window.byted_acrawler.frontierSign({{url: {json.dumps(unsigned_url)}}});
                    if (signature && typeof signature.then === 'function') signature = await signature;
                    const signed = new URL({json.dumps(unsigned_url)});
                    for (const [key, value] of Object.entries(signature || {{}})) {{
                        signed.searchParams.set(key, String(value));
                    }}
                    const response = await fetch(signed.toString(), {{
                        credentials: 'include',
                        headers: {{'Accept': 'application/json, text/plain, */*'}}
                    }});
                    const body = await response.text();
                    window.__tubePlayerDouyinResult = JSON.stringify({{
                        requestId,
                        status: response.status,
                        body
                    }});
                }} catch (error) {{
                    window.__tubePlayerDouyinResult = JSON.stringify({{
                        requestId,
                        error: String(error)
                    }});
                }}
            }})()
        """
        self._page.runJavaScript(script)
        QTimer.singleShot(100, self._poll_current_result)

    def _execute_home_request(self, request: _BrowserRequest) -> None:
        if self._page is None:
            return
        # 精选页首屏已经自动请求了低位游标。推荐流的相邻 cursor 高度重复，
        # 因此每个 UI 页取 9 个分散窗口，提升去重后的内容覆盖率。
        cursor_start = (request.page - 1) * 180 + 20
        script = f"""
            (async () => {{
                const requestId = {request.request_id};
                window.__tubePlayerDouyinResult = null;
                try {{
                    const referer = new URL({json.dumps(request.referer)});
                    history.replaceState(null, '', referer.pathname + referer.search);
                    const endpoint = {json.dumps(request.endpoint)};
                    const baseParams = {json.dumps(request.params, ensure_ascii=False)};
                    const collected = [];
                    const seen = new Set();
                    const addItems = items => {{
                        for (const item of items || []) {{
                            const id = String(item && item.aweme_id || '');
                            if (id && !seen.has(id)) {{ seen.add(id); collected.push(item); }}
                        }}
                    }};
                    if ({request.page} === 1) {{
                        for (const capture of window.__tubePlayerDouyinCaptured || []) {{
                            try {{
                                const payload = JSON.parse(capture.body || '{{}}');
                                addItems(payload.aweme_details || payload.aweme_list || []);
                            }} catch (_) {{}}
                        }}
                    }}
                    const jobs = [];
                    for (let cursor = {cursor_start}; cursor < {cursor_start + 180}; cursor += 20) {{
                        const unsigned = new URL(endpoint);
                        for (const [key, value] of Object.entries(baseParams)) {{
                            unsigned.searchParams.set(key, String(value));
                        }}
                        unsigned.searchParams.set('cursor', String(cursor));
                        let signature = window.byted_acrawler.frontierSign({{url: unsigned.toString()}});
                        if (signature && typeof signature.then === 'function') signature = await signature;
                        for (const [key, value] of Object.entries(signature || {{}})) {{
                            unsigned.searchParams.set(key, String(value));
                        }}
                        jobs.push(
                            fetch(unsigned.toString(), {{credentials: 'include'}})
                                .then(response => response.json())
                                .catch(() => null)
                        );
                    }}
                    const payloads = await Promise.all(jobs);
                    for (const payload of payloads) {{
                        if (payload) addItems(payload.aweme_list || payload.aweme_details || []);
                    }}
                    window.__tubePlayerDouyinResult = JSON.stringify({{
                        requestId,
                        status: 200,
                        body: JSON.stringify({{
                            status_code: 0,
                            aweme_list: collected.slice(0, {request.target_count}),
                            cursor: {cursor_start + 180},
                            has_more: 1
                        }})
                    }});
                }} catch (error) {{
                    window.__tubePlayerDouyinResult = JSON.stringify({{
                        requestId,
                        error: String(error)
                    }});
                }}
            }})()
        """
        self._page.runJavaScript(script)
        QTimer.singleShot(100, self._poll_current_result)

    def _poll_current_result(self) -> None:
        request = self._current
        if request is None or self._page is None:
            return
        if time.monotonic() >= request.deadline:
            self._finish_current(error=RuntimeError("抖音浏览器 API 请求超时"))
            return
        self._page.runJavaScript(
            "window.__tubePlayerDouyinResult || ''",
            self._current_result_received,
        )

    def _current_result_received(self, raw) -> None:
        request = self._current
        if request is None:
            return
        text = str(raw or "")
        if not text:
            QTimer.singleShot(100, self._poll_current_result)
            return
        try:
            envelope = json.loads(text)
            if int(envelope.get("requestId") or 0) != request.request_id:
                QTimer.singleShot(100, self._poll_current_result)
                return
            if envelope.get("error"):
                raise RuntimeError(str(envelope["error"]))
            status = int(envelope.get("status") or 0)
            if status < 200 or status >= 300:
                raise RuntimeError(f"抖音浏览器 API 返回 HTTP {status}")
            payload = json.loads(str(envelope.get("body") or "{}"))
            if not isinstance(payload, dict):
                raise RuntimeError("抖音浏览器 API 返回的 JSON 不是对象")
        except Exception as exc:
            self._finish_current(error=exc)
            return
        logger.info(
            "douyin browser request completed request=%s endpoint=%s bytes=%s",
            request.request_id,
            urllib.parse.urlparse(request.endpoint).path,
            len(str(envelope.get("body") or "")),
        )
        self._finish_current(result=payload)

    def _sync_browser_cookies(self, referer: str) -> None:
        if self._profile is None:
            return
        browser = self.config.cookie_browser_for_site("douyin")
        now = time.monotonic()
        if browser == self._cookie_source and now - self._cookie_loaded_at < 60.0:
            return
        cookie_header = load_browser_cookie_header(browser, referer) if browser else ""
        origin = QUrl("https://www.douyin.com/")
        store = self._profile.cookieStore()
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            if not name:
                continue
            cookie = QNetworkCookie(name.encode("utf-8"), value.encode("utf-8"))
            cookie.setDomain(".douyin.com")
            cookie.setPath("/")
            cookie.setSecure(True)
            store.setCookie(cookie, origin)
        self._cookie_source = browser
        self._cookie_loaded_at = now

    def _finish_current(self, *, result: dict | None = None, error: BaseException | None = None) -> None:
        request = self._current
        if request is None:
            return
        request.result = result
        request.error = error
        request.event.set()
        self._current = None
        QTimer.singleShot(0, self._start_next)

    def shutdown(self) -> None:
        self._shutdown = True
        error = RuntimeError("应用正在退出，抖音浏览器请求已取消")
        if self._current is not None:
            self._current.error = error
            self._current.event.set()
            self._current = None
        while self._queue:
            request = self._queue.popleft()
            request.error = error
            request.event.set()
        if self._page is not None:
            self._page.deleteLater()
            self._page = None
        if self._profile is not None:
            self._profile.deleteLater()
            self._profile = None
