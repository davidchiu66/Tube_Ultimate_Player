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
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from services.config_service import ConfigService
from services.cookie_service import load_browser_cookie_header


logger = logging.getLogger("tube_player.douyin_browser")

_ALLOWED_DOUYIN_NAVIGATION_SUFFIXES = (
    "douyin.com",
    "zijieapi.com",
    "byteverify.com",
    "bytedance.com",
    "snssdk.com",
    "amemv.com",
)


def _is_allowed_douyin_navigation(url: QUrl) -> bool:
    if url.scheme().lower() not in {"http", "https"}:
        return False
    host = url.host().strip().lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_DOUYIN_NAVIGATION_SUFFIXES)


def _safe_douyin_url(value: str) -> str:
    url = QUrl(str(value or ""))
    return url.toString() if _is_allowed_douyin_navigation(url) else "https://www.douyin.com/jingxuan"


class _DouyinRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        if info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia:
            info.block(True)


class _DouyinPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
        if is_main_frame and not _is_allowed_douyin_navigation(url):
            logger.warning("blocked external navigation from douyin verification host=%s", url.host())
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)

    def createWindow(self, _window_type):  # noqa: N802
        return None


class DouyinVerificationRequired(RuntimeError):
    def __init__(self, message: str, url: str) -> None:
        super().__init__(message)
        self.url = _safe_douyin_url(url)


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
    refresh_index: int = 1
    event: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: BaseException | None = None
    deadline: float = 0.0
    cancelled: bool = False


class DouyinBrowserService(QObject):
    """Run signed Douyin API requests inside the application's Chromium runtime.

    Douyin validates the page-generated X-Bogus value together with the browser
    transport fingerprint. Resolver workers therefore enqueue work here and
    wait while the GUI thread signs and fetches the request in QWebEngine.
    """

    request_enqueued = Signal(object)
    verification_required = Signal(str, str)

    def __init__(self, config: ConfigService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._queue: deque[_BrowserRequest] = deque()
        self._current: _BrowserRequest | None = None
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._profile: QWebEngineProfile | None = None
        self._page: QWebEnginePage | None = None
        self._view: QWebEngineView | None = None
        self._request_interceptor: _DouyinRequestInterceptor | None = None
        self._ready = False
        self._loading = False
        self._shutdown = False
        self._runtime_probe_inflight = False
        self._runtime_probe_request_id = 0
        self._runtime_ready_timer = QTimer(self)
        self._runtime_ready_timer.setSingleShot(True)
        self._runtime_ready_timer.setInterval(250)
        self._runtime_ready_timer.timeout.connect(self._poll_runtime_ready)
        self._cookie_source = ""
        self._cookie_loaded_at = 0.0
        self._verification_pending = False
        self._verification_url = "https://www.douyin.com/jingxuan"
        self._collection_request: _BrowserRequest | None = None
        self._collection_page_ready_id = 0
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
        refresh_index: int = 1,
        timeout: float = 80.0,
    ) -> dict:
        return self._submit_request(
            endpoint,
            params,
            referer,
            timeout=timeout,
            operation="home",
            page=max(1, int(page)),
            target_count=max(1, int(target_count)),
            refresh_index=max(1, int(refresh_index)),
        )

    def request_collection_json(
        self,
        collection_url: str,
        *,
        target_count: int = 50,
        timeout: float = 80.0,
    ) -> dict:
        return self._submit_request(
            "__collection_page__",
            {},
            _safe_douyin_url(collection_url),
            timeout=timeout,
            operation="collection_page",
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
        refresh_index: int = 1,
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
            refresh_index=refresh_index,
        )
        self.request_enqueued.emit(request)
        if not request.event.wait(request.timeout + 5.0):
            request.cancelled = True
            raise RuntimeError("抖音浏览器请求等待超时")
        if request.error is not None:
            raise request.error
        if not isinstance(request.result, dict):
            raise RuntimeError("抖音浏览器返回数据格式无效")
        return request.result

    @Slot(object)
    def _enqueue(self, request: _BrowserRequest) -> None:
        if self._shutdown:
            request.error = RuntimeError("应用正在退出，抖音浏览器请求已取消")
            request.event.set()
            return
        if request.operation == "home":
            self._queue.append(request)
        else:
            # 用户主动搜索/解析优先于尚未开始的首页补水。
            self._queue.appendleft(request)
            if self._current is not None and self._current.operation == "home":
                self._cancel_current_home_for_priority()
        self._start_next()

    def _cancel_current_home_for_priority(self) -> None:
        request = self._current
        if request is None or request.operation != "home":
            return
        request.cancelled = True
        if self._page is not None:
            self._page.runJavaScript(
                """
                for (const controller of window.__tubePlayerDouyinControllers || []) {
                    try { controller.abort(); } catch (_) {}
                }
                window.__tubePlayerDouyinControllers = [];
                """
            )
        logger.info("douyin home request preempted by foreground request=%s", request.request_id)
        self._finish_current(error=RuntimeError("抖音首页补水已让位于用户请求"))

    def _start_next(self) -> None:
        if self._current is not None or not self._queue or self._shutdown:
            return
        while self._queue:
            candidate = self._queue.popleft()
            if candidate.cancelled:
                candidate.error = RuntimeError("抖音浏览器请求已取消")
                candidate.event.set()
                continue
            self._current = candidate
            break
        if self._current is None:
            return
        self._current.deadline = time.monotonic() + self._current.timeout
        self._ensure_page()
        if self._ready:
            self._execute_current()

    def _ensure_page(self) -> None:
        if self._page is not None:
            if not self._ready and not self._loading:
                self._load_runtime_page()
            return
        self._create_profile_and_page()

    def _create_profile_and_page(self) -> None:
        # 无 storageName 的 Profile 是 off-the-record：Cookie、Local Storage 和
        # HTTP 缓存均只存在于当前进程，不在运行目录留下另一套登录数据。
        self._profile = QWebEngineProfile(self)
        self._request_interceptor = _DouyinRequestInterceptor(self._profile)
        self._profile.setUrlRequestInterceptor(self._request_interceptor)
        capture_script = QWebEngineScript()
        capture_script.setName("tube-player-douyin-capture")
        capture_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        capture_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        capture_script.setRunsOnSubFrames(False)
        capture_script.setSourceCode(
            """
            (() => {
                window.__tubePlayerDouyinCapturedItems = [];
                window.__tubePlayerDouyinCapturedIds = new Set();
                const save = (url, body) => {
                    if (!/aweme\\/v1.*(tab\\/feed|multi\\/aweme\\/detail)/.test(String(url))) return;
                    try {
                        const payload = JSON.parse(body || '{}');
                        const items = payload.aweme_details || payload.aweme_list || [];
                        for (const item of items) {
                            const id = String(item && item.aweme_id || '');
                            if (!id || window.__tubePlayerDouyinCapturedIds.has(id)) continue;
                            window.__tubePlayerDouyinCapturedIds.add(id);
                            window.__tubePlayerDouyinCapturedItems.push(item);
                        }
                        while (window.__tubePlayerDouyinCapturedItems.length > 80) {
                            const removed = window.__tubePlayerDouyinCapturedItems.shift();
                            window.__tubePlayerDouyinCapturedIds.delete(String(removed && removed.aweme_id || ''));
                        }
                    } catch (_) {
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
        self._page = _DouyinPage(self._profile, self)
        # 给隐藏签名页提供真实 viewport，避免 Chromium 后台降频导致 frontierSign/fetch
        # 长时间不回调；视图位于屏幕外，不会覆盖主界面。
        view = QWebEngineView()
        view.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        view.resize(1280, 900)
        view.move(-20000, -20000)
        view.setPage(self._page)
        view.show()
        self._view = view
        self._page.setAudioMuted(True)
        self._page.loadFinished.connect(self._runtime_loaded)
        self._page.permissionRequested.connect(lambda permission: permission.deny())
        self._profile.downloadRequested.connect(lambda item: item.cancel())
        self._load_runtime_page()

    def _load_runtime_page(self) -> None:
        if self._page is None or self._current is None:
            return
        self._loading = True
        self._ready = False
        self._runtime_probe_inflight = False
        self._runtime_probe_request_id = 0
        self._runtime_ready_timer.stop()
        self._sync_browser_cookies(self._current.referer)
        QTimer.singleShot(
            150,
            lambda: self._page and self._page.setUrl(QUrl("https://www.douyin.com/jingxuan")),
        )

    @Slot(bool)
    def _runtime_loaded(self, ok: bool) -> None:
        self._loading = False
        if self._current is None:
            return
        if self._collection_request is not None:
            self._collection_page_loaded(ok)
            return
        if not ok:
            self._finish_current(error=RuntimeError("抖音浏览器运行页加载失败"))
            return
        self._runtime_ready_timer.start(50)

    def _poll_runtime_ready(self) -> None:
        if self._page is None or self._current is None or self._runtime_probe_inflight:
            return
        self._runtime_probe_inflight = True
        request_id = self._current.request_id
        self._runtime_probe_request_id = request_id
        self._page.runJavaScript(
            """
            JSON.stringify({
                ready: Boolean(window.byted_acrawler && window.byted_acrawler.frontierSign),
                url: location.href,
                challenge: /captcha|verify|challenge|risk/i.test(location.href)
                    || /安全验证|验证码/.test((document.body && document.body.innerText || '').slice(0, 2000))
            })
            """,
            lambda result, rid=request_id: self._runtime_ready_result(rid, result),
        )

    def _runtime_ready_result(self, request_id: int, result) -> None:
        if self._runtime_probe_request_id != request_id:
            return
        self._runtime_probe_inflight = False
        self._runtime_probe_request_id = 0
        if self._current is None or self._current.request_id != request_id:
            return
        probe = self._parse_runtime_probe(result)
        if bool(probe.get("challenge")):
            url = str(probe.get("url") or self._current.referer)
            error = DouyinVerificationRequired("抖音页面需要完成安全验证", url)
            self._finish_current(error=error)
            self._notify_verification(error.url, str(error))
            return
        ready = bool(probe.get("ready"))
        if bool(ready):
            self._ready = True
            logger.info("douyin browser signing runtime ready")
            self._execute_current()
            return
        if time.monotonic() >= self._current.deadline:
            self._finish_current(error=RuntimeError("抖音页面签名组件加载超时"))
            return
        self._runtime_ready_timer.start()

    @staticmethod
    def _parse_runtime_probe(result) -> dict:
        if isinstance(result, dict):
            return result
        if isinstance(result, str) and result:
            try:
                parsed = json.loads(result)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {"ready": bool(result), "challenge": False, "url": ""}

    def _execute_current(self) -> None:
        request = self._current
        if request is None or self._page is None:
            return
        self._sync_browser_cookies(request.referer)
        if request.operation == "home":
            self._execute_home_request(request)
            return
        if request.operation == "collection_page" or "/series/list/" in request.endpoint:
            if self._collection_page_ready_id != request.request_id:
                self._execute_collection_request(request)
                return
            self._collection_page_ready_id = 0
            if request.operation == "collection_page":
                self._execute_collection_capture(request)
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
                    const signPromise = Promise.resolve().then(() =>
                        window.byted_acrawler.frontierSign({{url: {json.dumps(unsigned_url)}}})
                    );
                    let signature = await Promise.race([
                        signPromise,
                        new Promise((_, reject) => setTimeout(() => reject(new Error('signature timeout')), 12000))
                    ]);
                    if (signature && typeof signature.then === 'function') signature = await Promise.race([
                        signature,
                        new Promise((_, reject) => setTimeout(() => reject(new Error('signature timeout')), 12000))
                    ]);
                    const signed = new URL({json.dumps(unsigned_url)});
                    for (const [key, value] of Object.entries(signature || {{}})) {{
                        signed.searchParams.set(key, String(value));
                    }}
                    const response = await fetch(signed.toString(), {{
                        credentials: 'include',
                        headers: {{'Accept': 'application/json, text/plain, */*'}},
                        signal: (() => {{
                            const controller = new AbortController();
                            window.__tubePlayerDouyinControllers = window.__tubePlayerDouyinControllers || [];
                            window.__tubePlayerDouyinControllers.push(controller);
                            setTimeout(() => controller.abort(), {max(5000, int(request.timeout * 1000) - 1000)});
                            return controller.signal;
                        }})()
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

    def _execute_collection_request(self, request: _BrowserRequest) -> None:
        if self._page is None:
            return
        target = _safe_douyin_url(request.referer)
        self._collection_request = request
        self._collection_page_ready_id = 0
        self._page.setUrl(QUrl(target))

    def _execute_collection_capture(self, request: _BrowserRequest) -> None:
        if self._page is None:
            return
        script = f"""
            (() => {{
                const requestId = {request.request_id};
                const items = (window.__tubePlayerDouyinCapturedItems || []).slice(0, {request.target_count});
                window.__tubePlayerDouyinResult = JSON.stringify({{
                    requestId,
                    status: 200,
                    body: JSON.stringify({{status_code: 0, aweme_list: items, has_more: false}})
                }});
            }})()
        """
        self._page.runJavaScript(script)
        QTimer.singleShot(100, self._poll_current_result)

    def _collection_page_loaded(self, ok: bool) -> None:
        request = getattr(self, "_collection_request", None)
        if request is None or self._page is None:
            return
        if not ok:
            self._collection_request = None
            self._finish_current(error=RuntimeError("抖音用户页加载失败，无法获取合集"))
            return
        self._collection_request = None
        self._collection_page_ready_id = request.request_id
        self._ready = False
        self._loading = False
        self._runtime_probe_inflight = False
        self._runtime_probe_request_id = 0
        self._runtime_ready_timer.stop()
        self._runtime_ready_timer.start(50)

    def _execute_home_request(self, request: _BrowserRequest) -> None:
        if self._page is None:
            return
        refresh_start = max(1, int(request.refresh_index))
        refresh_end = refresh_start + 6
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
                    window.__tubePlayerDouyinControllers = [];
                    const addItems = items => {{
                        for (const item of items || []) {{
                            const id = String(item && item.aweme_id || '');
                            if (id && !seen.has(id)) {{ seen.add(id); collected.push(item); }}
                        }}
                    }};
                    if ({refresh_start} === 1) {{
                        addItems(window.__tubePlayerDouyinCapturedItems || []);
                    }}
                    let verificationRequired = false;
                    let nextRefreshIndex = {refresh_start};
                    const fetchRefresh = async refreshIndex => {{
                        const unsigned = new URL(endpoint);
                        for (const [key, value] of Object.entries(baseParams)) {{
                            unsigned.searchParams.set(key, String(value));
                        }}
                        unsigned.searchParams.delete('cursor');
                        unsigned.searchParams.set('refresh_index', String(refreshIndex));
                        let signature = window.byted_acrawler.frontierSign({{url: unsigned.toString()}});
                        if (signature && typeof signature.then === 'function') signature = await signature;
                        for (const [key, value] of Object.entries(signature || {{}})) {{
                            unsigned.searchParams.set(key, String(value));
                        }}
                        const controller = new AbortController();
                        window.__tubePlayerDouyinControllers.push(controller);
                        const timeoutId = setTimeout(() => controller.abort(), 30000);
                        return fetch(unsigned.toString(), {{
                            credentials: 'include',
                            signal: controller.signal
                        }})
                            .then(async response => {{
                                let payload = null;
                                try {{ payload = await response.json(); }} catch (_) {{}}
                                return {{status: response.status, payload}};
                            }})
                            .catch(() => null)
                            .finally(() => {{
                                clearTimeout(timeoutId);
                                window.__tubePlayerDouyinControllers =
                                    (window.__tubePlayerDouyinControllers || []).filter(item => item !== controller);
                            }});
                    }};
                    const jobs = [];
                    for (let refreshIndex = {refresh_start}; refreshIndex < {refresh_end}; refreshIndex += 1) {{
                        jobs.push(fetchRefresh(refreshIndex));
                    }}
                    const responses = await Promise.all(jobs);
                    nextRefreshIndex = {refresh_end};
                    for (const result of responses) {{
                        if (!result) continue;
                        const payload = result.payload;
                        const nilInfo = payload && payload.search_nil_info || {{}};
                        if (result.status === 403 || result.status === 429
                            || nilInfo.search_nil_type === 'verify_check'
                            || nilInfo.search_nil_item === 'verify_check') {{
                            verificationRequired = true;
                            continue;
                        }}
                        if (payload) addItems(payload.aweme_list || payload.aweme_details || []);
                    }}
                    window.__tubePlayerDouyinResult = JSON.stringify({{
                        requestId,
                        status: 200,
                        body: JSON.stringify({{
                            status_code: 0,
                            aweme_list: collected,
                            next_refresh_index: nextRefreshIndex,
                            has_more: 1,
                            _tube_player_verification_required: verificationRequired
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
            self._abort_current_script()
            self._ready = False
            self._loading = False
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
            if status in {403, 429}:
                raise DouyinVerificationRequired(
                    f"抖音返回 HTTP {status}，需要完成安全验证",
                    request.referer,
                )
            if status < 200 or status >= 300:
                raise RuntimeError(f"抖音浏览器 API 返回 HTTP {status}")
            body_text = str(envelope.get("body") or "{}")
            if self._body_requires_verification(body_text):
                raise DouyinVerificationRequired("抖音页面需要完成安全验证", request.referer)
            payload = json.loads(body_text)
            if not isinstance(payload, dict):
                raise RuntimeError("抖音浏览器 API 返回的 JSON 不是对象")
            verification_reason = self._verification_reason(payload, status)
            if verification_reason:
                raise DouyinVerificationRequired(verification_reason, request.referer)
        except Exception as exc:
            self._finish_current(error=exc)
            if isinstance(exc, DouyinVerificationRequired):
                self._notify_verification(exc.url, str(exc))
            return
        logger.info(
            "douyin browser request completed request=%s endpoint=%s bytes=%s",
            request.request_id,
            urllib.parse.urlparse(request.endpoint).path,
            len(str(envelope.get("body") or "")),
        )
        self._finish_current(result=payload)

    def _abort_current_script(self) -> None:
        if self._page is None:
            return
        self._page.runJavaScript(
            """
            (() => {
              for (const controller of window.__tubePlayerDouyinControllers || []) {
                try { controller.abort(); } catch (_) {}
              }
              window.__tubePlayerDouyinControllers = [];
              window.__tubePlayerDouyinResult = null;
            })();
            """
        )

    @staticmethod
    def _verification_reason(payload: dict, status: int = 200) -> str:
        if status in {403, 429}:
            return f"抖音返回 HTTP {status}，需要完成安全验证"
        if bool(payload.get("_tube_player_verification_required")):
            return "抖音推荐流需要完成安全验证"
        nil_info = payload.get("search_nil_info")
        nil_info = nil_info if isinstance(nil_info, dict) else {}
        nil_values = {
            str(nil_info.get("search_nil_type") or "").strip().lower(),
            str(nil_info.get("search_nil_item") or "").strip().lower(),
        }
        if "verify_check" in nil_values:
            return "抖音搜索需要完成安全验证"
        return ""

    @staticmethod
    def _body_requires_verification(body: str) -> bool:
        text = str(body or "").strip().lower()
        if not text or text.startswith("{"):
            return False
        return any(token in text for token in (
            "verify_check",
            "risk-captcha",
            "captcha",
            "安全验证",
            "验证码",
        ))

    def _notify_verification(self, url: str, reason: str) -> None:
        self._verification_url = _safe_douyin_url(url)
        if self._verification_pending:
            return
        self._verification_pending = True
        QTimer.singleShot(
            0,
            lambda: self.verification_required.emit(self._verification_url, reason),
        )

    def verification_pending(self) -> bool:
        return self._verification_pending

    def attach_verification_view(self, view, url: str = "") -> None:
        self._ensure_verification_page()
        if self._page is None:
            raise RuntimeError("抖音验证页面尚未初始化")
        target = _safe_douyin_url(url or self._verification_url)
        self._verification_url = target
        self._ready = False
        self._loading = False
        self._runtime_probe_inflight = False
        self._runtime_probe_request_id = 0
        self._runtime_ready_timer.stop()
        old_page = view.page()
        if self._view is not None and self._view.page() is self._page:
            self._view.setPage(QWebEnginePage(self._view))
        view.setPage(self._page)
        if old_page is not self._page:
            old_page.deleteLater()
        self._page.setUrl(QUrl(target))

    def reload_verification_page(self) -> None:
        if self._page is None:
            return
        self._page.setUrl(QUrl(self._verification_url))

    def detach_verification_view(self, view) -> None:
        if self._page is None or view.page() is not self._page:
            return
        view.setPage(QWebEnginePage(view))
        if self._view is not None:
            placeholder = self._view.page()
            self._view.setPage(self._page)
            if placeholder is not self._page:
                placeholder.deleteLater()

    def complete_verification(self) -> None:
        self._verification_pending = False
        self._ready = False
        self._loading = False
        self._runtime_probe_inflight = False
        self._runtime_probe_request_id = 0
        self._runtime_ready_timer.stop()
        self._cookie_loaded_at = 0.0

    def cancel_verification(self) -> None:
        self._verification_pending = False

    def cancel_home_requests(self) -> None:
        """播放开始时取消未完成的首页补水，避免占用签名浏览器上下文。"""
        current = self._current
        if current is not None and current.operation == "home":
            self._cancel_current_home_for_priority()
        retained: deque[_BrowserRequest] = deque()
        while self._queue:
            request = self._queue.popleft()
            if request.operation == "home":
                request.cancelled = True
                request.error = RuntimeError("抖音首页加载已让位于播放")
                request.event.set()
            else:
                retained.append(request)
        self._queue = retained

    def _ensure_verification_page(self) -> None:
        if self._page is not None:
            return
        # 正常情况下验证来自一次已经初始化过的请求；保留兜底以便测试或
        # 用户从状态提示主动打开验证窗口。
        self._create_profile_and_page()
        self._sync_browser_cookies(self._verification_url)

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
        self._runtime_ready_timer.stop()
        if self._runtime_probe_request_id == request.request_id:
            self._runtime_probe_inflight = False
            self._runtime_probe_request_id = 0
        request.result = result
        request.error = error
        request.event.set()
        self._current = None
        QTimer.singleShot(0, self._start_next)

    def shutdown(self) -> None:
        self._shutdown = True
        self._verification_pending = False
        self._runtime_ready_timer.stop()
        error = RuntimeError("应用正在退出，抖音浏览器请求已取消")
        if self._current is not None:
            self._current.error = error
            self._current.event.set()
            self._current = None
        while self._queue:
            request = self._queue.popleft()
            request.error = error
            request.event.set()
        if self._view is not None:
            self._view.setPage(QWebEnginePage(self._view))
            self._view.close()
            self._view.deleteLater()
            self._view = None
        if self._page is not None:
            self._page.deleteLater()
            self._page = None
        if self._profile is not None:
            self._profile.deleteLater()
            self._profile = None
        self._request_interceptor = None
