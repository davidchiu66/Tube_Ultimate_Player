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
from services.cookie_service import load_browser_cookie_header, load_cookie_header
from services.chromium_cookie_extractor import extract_cookies_to_netscape


logger = logging.getLogger("tube_player.xiaohongshu_browser")

_HOME_URL = "https://www.xiaohongshu.com/red_video"
_ALLOWED_NAVIGATION_SUFFIXES = ("xiaohongshu.com", "xhslink.com", "xhscdn.com")


def _is_allowed_navigation(url: QUrl) -> bool:
    if url.scheme().lower() not in {"http", "https"}:
        return False
    host = url.host().strip().lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_NAVIGATION_SUFFIXES)


def _safe_url(value: str) -> str:
    url = QUrl(str(value or ""))
    return url.toString() if _is_allowed_navigation(url) else _HOME_URL


class _RequestInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        # 隐藏页面只负责发现卡片和安全上下文，禁止网页在后台自行播放媒体。
        if info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia:
            info.block(True)


class _Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
        if is_main_frame and not _is_allowed_navigation(url):
            logger.warning("blocked external Xiaohongshu navigation host=%s", url.host())
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)

    def createWindow(self, _window_type):  # noqa: N802
        return None


class XiaohongshuVerificationRequired(RuntimeError):
    def __init__(self, message: str, url: str) -> None:
        super().__init__(message)
        self.url = _safe_url(url)


@dataclass(slots=True)
class _BrowserRequest:
    request_id: int
    operation: str
    page: int
    page_size: int
    keyword: str = ""
    target_url: str = ""
    force_refresh: bool = False
    timeout: float = 50.0
    event: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: BaseException | None = None
    deadline: float = 0.0
    scroll_attempts: int = 0
    filter_attempts: int = 0
    cancelled: bool = False


_CAPTURE_SCRIPT = r"""
(() => {
  if (window.__tubePlayerXhsInstalled) return;
  window.__tubePlayerXhsInstalled = true;
  const state = window.__tubePlayerXhs = {
    homeItems: [], homeSeen: {}, homeHasMore: true,
    searchItems: [], searchSeen: {}, searchHasMore: true,
    creatorItems: [], creatorSeen: {}, creatorHasMore: true,
    error: ''
  };
  const text = value => String(value == null ? '' : value);
  const isRisk = value => {
    const raw = text(value && (value.msg || value.message || value.error)).toLowerCase();
    return /captcha|verify|security|risk|406|403|429|验证|风控|异常/.test(raw);
  };
  const isVideo = (item, kind) => {
    if (!item || typeof item !== 'object') return false;
    if (kind === 'home') return item.type === 'video' || (item.note_card && item.note_card.type === 'video');
    if (kind === 'creator' && item.type) return item.type === 'video';
    const card = item.note_card || item.noteCard || item;
    return card.type === 'video' || card.note_type === 'video';
  };
  const itemId = item => text(item && (item.id || item.note_id || item.noteId ||
    (item.note_card && (item.note_card.note_id || item.note_card.noteId || item.note_card.id))));
  const consume = (kind, payload) => {
    try {
      if (!payload || typeof payload !== 'object') return;
      if (isRisk(payload)) state.error = text(payload.msg || payload.message || '页面需要安全验证');
      const root = payload.data && typeof payload.data === 'object' ? payload.data : payload;
      let items = kind === 'home' ? (root.data || root.items || root.result)
        : kind === 'creator' ? (root.notes || root.items || root.data)
        : (root.items || root.data || root.notes);
      if (!Array.isArray(items)) return;
      const target = kind === 'home' ? state.homeItems : kind === 'creator' ? state.creatorItems : state.searchItems;
      const seen = kind === 'home' ? state.homeSeen : kind === 'creator' ? state.creatorSeen : state.searchSeen;
      for (const item of items) {
        const id = itemId(item);
        if (!id || seen[id] || !isVideo(item, kind)) continue;
        seen[id] = true;
        target.push(item);
      }
      if (kind === 'home') {
        if (Object.prototype.hasOwnProperty.call(root, 'has_no_more')) state.homeHasMore = !root.has_no_more;
        else if (Object.prototype.hasOwnProperty.call(root, 'has_more')) state.homeHasMore = !!root.has_more;
      } else if (kind === 'creator') {
        if (Object.prototype.hasOwnProperty.call(root, 'has_more')) state.creatorHasMore = !!root.has_more;
      } else if (Object.prototype.hasOwnProperty.call(root, 'has_more')) {
        state.searchHasMore = !!root.has_more;
      }
    } catch (error) {
      state.error = text(error);
    }
  };
  const classify = url => {
    const value = text(url);
    if (value.includes('/api/sns/web/v1/common/dual_feed')) return 'home';
    if (value.includes('/api/sns/web/v2/search/notes')) return 'search';
    if (value.includes('/api/sns/web/v1/user_posted')) return 'creator';
    return '';
  };
  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = async function(input) {
      const response = await originalFetch.apply(this, arguments);
      const kind = classify(input && input.url || input);
      if (kind) response.clone().json().then(value => consume(kind, value)).catch(() => {});
      return response;
    };
  }
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__tubePlayerXhsKind = classify(url);
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    if (this.__tubePlayerXhsKind) {
      this.addEventListener('loadend', () => {
        try { consume(this.__tubePlayerXhsKind, JSON.parse(this.responseText)); } catch (_) {}
      }, {once: true});
    }
    return originalSend.apply(this, arguments);
  };
})();
"""


class XiaohongshuBrowserService(QObject):
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
        self._interceptor: _RequestInterceptor | None = None
        self._mode = ""
        self._keyword = ""
        self._page_loaded = False
        self._search_video_filter_active = False
        self._shutdown = False
        self._cookie_source = ""
        self._cookie_loaded_at = 0.0
        self._verification_pending = False
        self._verification_url = _HOME_URL
        self.request_enqueued.connect(self._enqueue, Qt.ConnectionType.QueuedConnection)

    def request_home(
        self,
        page: int,
        page_size: int = 20,
        *,
        force_refresh: bool = False,
        timeout: float = 55.0,
    ) -> dict:
        return self._submit("home", page, page_size, force_refresh=force_refresh, timeout=timeout)

    def request_search(
        self,
        keyword: str,
        page: int,
        page_size: int = 20,
        *,
        force_refresh: bool = False,
        timeout: float = 55.0,
    ) -> dict:
        return self._submit(
            "search",
            page,
            page_size,
            keyword=str(keyword or "").strip(),
            force_refresh=force_refresh,
            timeout=timeout,
        )

    def request_note_detail(self, url: str, *, timeout: float = 40.0) -> dict:
        return self._submit(
            "detail",
            1,
            1,
            target_url=_safe_url(url),
            force_refresh=True,
            timeout=timeout,
        )

    def request_creator(
        self,
        url: str,
        *,
        user_id: str,
        token: str = "",
        source: str = "pc_user",
        limit: int = 50,
        timeout: float = 60.0,
    ) -> dict:
        target = _safe_url(url)
        query = {"xsec_source": source or "pc_user"}
        if token:
            query["xsec_token"] = token
        separator = "&" if "?" in target else "?"
        target = target + separator + urllib.parse.urlencode(query)
        return self._submit(
            "creator",
            1,
            max(1, min(50, int(limit))),
            keyword=str(user_id or "").strip(),
            target_url=target,
            force_refresh=True,
            timeout=timeout,
        )

    def _submit(
        self,
        operation: str,
        page: int,
        page_size: int,
        *,
        keyword: str = "",
        target_url: str = "",
        force_refresh: bool,
        timeout: float,
    ) -> dict:
        if QThread.currentThread() is self.thread():
            raise RuntimeError("小红书浏览器请求不能阻塞 Qt 主线程")
        if operation in {"search", "creator"} and not keyword:
            raise RuntimeError("小红书搜索关键词不能为空")
        with self._sequence_lock:
            self._sequence += 1
            request_id = self._sequence
        request = _BrowserRequest(
            request_id=request_id,
            operation=operation,
            page=max(1, int(page)),
            page_size=max(1, min(50, int(page_size))),
            keyword=keyword,
            target_url=target_url,
            force_refresh=bool(force_refresh),
            timeout=max(10.0, float(timeout)),
        )
        self.request_enqueued.emit(request)
        if not request.event.wait(request.timeout + 5.0):
            request.cancelled = True
            raise RuntimeError("小红书浏览器请求等待超时")
        if request.error is not None:
            raise request.error
        if not isinstance(request.result, dict):
            raise RuntimeError("小红书浏览器返回数据格式无效")
        return request.result

    @Slot(object)
    def _enqueue(self, request: _BrowserRequest) -> None:
        if self._shutdown:
            request.error = RuntimeError("应用正在退出，小红书浏览器请求已取消")
            request.event.set()
            return
        self._queue.append(request)
        self._start_next()

    def _start_next(self) -> None:
        if self._current is not None or not self._queue or self._shutdown:
            return
        while self._queue:
            request = self._queue.popleft()
            if request.cancelled:
                request.error = RuntimeError("小红书浏览器请求已取消")
                request.event.set()
                continue
            self._current = request
            break
        if self._current is None:
            return
        self._ensure_page()
        self._sync_cookies()
        request = self._current
        request.deadline = time.monotonic() + request.timeout
        target_url = self._target_url(request)
        needs_navigation = (
            request.operation in {"detail", "creator"}
            or
            request.force_refresh
            or self._mode != request.operation
            or (request.operation == "search" and self._keyword != request.keyword)
            or not self._page_loaded
        )
        if needs_navigation:
            self._mode = request.operation
            self._keyword = request.keyword
            self._page_loaded = False
            self._search_video_filter_active = False
            self._reset_capture_state(request.operation)
            self._page.setUrl(QUrl(target_url))
        elif request.operation == "search" and not self._search_video_filter_active:
            QTimer.singleShot(0, self._apply_search_video_filter)
        else:
            QTimer.singleShot(0, self._poll_current)

    def _reset_capture_state(self, operation: str) -> None:
        if self._page is None:
            return
        key = {
            "home": "home",
            "search": "search",
            "creator": "creator",
        }.get(operation)
        if key is None:
            return
        self._page.runJavaScript(
            f"""
            (() => {{
              const state = window.__tubePlayerXhs;
              if (!state) return;
              state.{key}Items = [];
              state.{key}Seen = {{}};
              state.{key}HasMore = true;
              state.error = '';
            }})();
            """
        )

    def _target_url(self, request: _BrowserRequest) -> str:
        if request.operation == "home":
            return _HOME_URL
        if request.operation == "detail":
            return request.target_url or _HOME_URL
        if request.operation == "creator":
            return request.target_url or _HOME_URL
        query = urllib.parse.urlencode(
            {"keyword": request.keyword, "source": "web_explore_feed", "type": "51"}
        )
        return f"https://www.xiaohongshu.com/search_result?{query}"

    def _ensure_page(self) -> None:
        if self._page is not None:
            return
        profile = QWebEngineProfile(self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        interceptor = _RequestInterceptor(profile)
        profile.setUrlRequestInterceptor(interceptor)
        script = QWebEngineScript()
        script.setName("tube-player-xiaohongshu-capture")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(_CAPTURE_SCRIPT)
        profile.scripts().insert(script)
        page = _Page(profile, self)
        # 没有关联可见 View 的 Page 默认处于不可见状态，站点的 IntersectionObserver
        # 和无限滚动会因此停止；保持页面可见，但仍不创建实际窗口。
        page.setVisible(True)
        page.loadFinished.connect(self._load_finished)
        # 使用独立的离屏窗口提供真实 viewport。将 View 作为主窗口子控件并 show()
        # 会覆盖主界面（安装版会表现为左半边黑屏），因此不能挂在 MainWindow 上。
        view = QWebEngineView()
        view.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        view.resize(1280, 900)
        view.move(-20000, -20000)
        view.setPage(page)
        view.show()
        self._profile = profile
        self._interceptor = interceptor
        self._page = page
        self._view = view

    def _sync_cookies(self) -> None:
        if self._profile is None:
            return
        browser = str(self.config.cookie_browser_for_site("xiaohongshu") or "")
        cookie_file = str(self.config.cookie_file("xiaohongshu") or "")
        source = f"{browser}|{cookie_file}"
        now = time.monotonic()
        if source == self._cookie_source and now - self._cookie_loaded_at < 60.0:
            return
        source_changed = bool(self._cookie_source and source != self._cookie_source)
        if source_changed:
            self._profile.cookieStore().deleteAllCookies()
        cookie_header = load_cookie_header(cookie_file, _HOME_URL) if cookie_file else ""
        if not cookie_header and browser:
            cookie_header = load_browser_cookie_header(browser, _HOME_URL)
        if not cookie_header and browser and not browser.lower().startswith("firefox"):
            extracted = extract_cookies_to_netscape(browser, _HOME_URL)
            if extracted:
                cookie_header = load_cookie_header(extracted, _HOME_URL)
        origin = QUrl("https://www.xiaohongshu.com/")
        store = self._profile.cookieStore()
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            if not name:
                continue
            cookie = QNetworkCookie(name.encode("utf-8"), value.encode("utf-8"))
            cookie.setDomain(".xiaohongshu.com")
            cookie.setPath("/")
            cookie.setSecure(True)
            store.setCookie(cookie, origin)
        self._cookie_source = source
        self._cookie_loaded_at = now

    def _load_finished(self, ok: bool) -> None:
        request = self._current
        if request is None:
            return
        if not ok:
            self._finish_current(error=RuntimeError("小红书页面加载失败，请检查网络、代理和 Cookie"))
            return
        self._page_loaded = True
        if request.operation == "detail":
            QTimer.singleShot(350, self._poll_detail)
        elif request.operation == "creator":
            QTimer.singleShot(700, self._poll_current)
        elif request.operation == "search":
            QTimer.singleShot(350, self._apply_search_video_filter)
        else:
            QTimer.singleShot(500, self._poll_current)

    def _apply_search_video_filter(self) -> None:
        request = self._current
        if request is None or request.operation != "search" or self._page is None:
            return
        script = r"""
        (() => {
          const matches = [...document.querySelectorAll('button,a,div,span')]
            .filter(node => String(node.innerText || '').trim() === '视频');
          const target = matches.find(node => node.classList && node.classList.contains('channel')) || matches[0];
          if (!target) return 'missing';
          const state = window.__tubePlayerXhs;
          if (state) {
            state.searchItems = [];
            state.searchSeen = {};
            state.searchHasMore = true;
            state.error = '';
          }
          target.click();
          return 'clicked';
        })();
        """
        self._page.runJavaScript(script, self._search_video_filter_applied)

    def _search_video_filter_applied(self, value) -> None:
        request = self._current
        if request is None or request.operation != "search":
            return
        if str(value or "") == "clicked":
            self._search_video_filter_active = True
            QTimer.singleShot(700, self._poll_current)
            return
        request.filter_attempts += 1
        if request.filter_attempts >= 6 or time.monotonic() >= request.deadline:
            self._finish_current(error=RuntimeError("小红书搜索页面没有找到视频筛选项"))
            return
        QTimer.singleShot(500, self._apply_search_video_filter)

    def _poll_current(self) -> None:
        request = self._current
        if request is None or self._page is None:
            return
        if request.cancelled:
            self._finish_current(error=RuntimeError("小红书浏览器请求已取消"))
            return
        script = r"""
        (() => {
          const state = window.__tubePlayerXhs || {};
          const body = String(document.body && document.body.innerText || '').slice(0, 3000);
          return JSON.stringify({
            homeCount: (state.homeItems || []).length,
            searchCount: (state.searchItems || []).length,
            creatorCount: (state.creatorItems || []).length,
            homeHasMore: state.homeHasMore !== false,
            searchHasMore: state.searchHasMore !== false,
            creatorHasMore: state.creatorHasMore !== false,
            error: String(state.error || ''),
            body: body,
            url: location.href
          });
        })();
        """
        self._page.runJavaScript(script, self._poll_received)

    def _poll_detail(self) -> None:
        request = self._current
        if request is None or request.operation != "detail" or self._page is None:
            return
        script = r"""
        (() => {
          const state = window.__INITIAL_STATE__ || {};
          const noteState = state.note && (state.note.value || state.note) || {};
          const map = noteState.noteDetailMap && (noteState.noteDetailMap.value || noteState.noteDetailMap) || {};
          const pathId = (location.pathname.match(/\/(?:explore|discovery\/item)\/([\da-f]+)/i) || [])[1] || '';
          let detail = pathId && map[pathId];
          if (!detail) detail = Object.values(map)[0];
          const note = detail && (detail.note && (detail.note.value || detail.note) || detail);
          const body = String(document.body && document.body.innerText || '').slice(0, 3000);
          return JSON.stringify({note: note || null, body: body, url: location.href});
        })();
        """
        self._page.runJavaScript(script, self._detail_received)

    def _detail_received(self, value) -> None:
        request = self._current
        if request is None or request.operation != "detail":
            return
        try:
            payload = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            payload = {}
        note = payload.get("note")
        if isinstance(note, dict) and note:
            self._finish_current(
                result={"note": note, "url": _safe_url(str(payload.get("url") or request.target_url))}
            )
            return
        body = str(payload.get("body") or "").lower()
        if any(token in body for token in ("captcha", "verify", "安全验证", "验证码", "访问异常")):
            exc = XiaohongshuVerificationRequired("小红书视频详情需要完成安全验证", request.target_url)
            self._finish_current(error=exc)
            self._notify_verification(exc.url, str(exc))
            return
        request.scroll_attempts += 1
        if time.monotonic() >= request.deadline or request.scroll_attempts >= 30:
            self._finish_current(error=RuntimeError("小红书视频详情加载超时或该笔记不是视频"))
            return
        QTimer.singleShot(500, self._poll_detail)

    def _poll_received(self, value) -> None:
        request = self._current
        if request is None:
            return
        try:
            state = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            state = {}
        risk_text = f"{state.get('error', '')} {state.get('body', '')}".lower()
        if any(token in risk_text for token in ("captcha", "verify", "risk", "安全验证", "验证码", "访问异常")):
            exc = XiaohongshuVerificationRequired("小红书需要完成安全验证", self._target_url(request))
            self._finish_current(error=exc)
            self._notify_verification(exc.url, str(exc))
            return
        count_key = {
            "home": "homeCount",
            "search": "searchCount",
            "creator": "creatorCount",
        }.get(request.operation, "homeCount")
        more_key = {
            "home": "homeHasMore",
            "search": "searchHasMore",
            "creator": "creatorHasMore",
        }.get(request.operation, "homeHasMore")
        count = int(state.get(count_key) or 0)
        target = request.page * request.page_size
        has_more = bool(state.get(more_key, True))
        timed_out = time.monotonic() >= request.deadline
        if count >= target or not has_more or timed_out or request.scroll_attempts >= 18:
            if count <= (request.page - 1) * request.page_size:
                if timed_out:
                    self._finish_current(error=RuntimeError("小红书页面签名组件或内容加载超时"))
                else:
                    self._finish_current(error=RuntimeError("小红书没有返回可播放的视频内容"))
                return
            self._collect_result(has_more)
            return
        request.scroll_attempts += 1
        self._page.runJavaScript(
            """
            (() => {
              window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight));
              for (const node of document.querySelectorAll('main, section, div')) {
                if (node.scrollHeight > node.clientHeight + 400 && node.clientHeight > 200) {
                  node.scrollTop = node.scrollHeight;
                }
              }
              window.dispatchEvent(new Event('scroll'));
            })();
            """
        )
        QTimer.singleShot(850, self._poll_current)

    def _collect_result(self, has_more: bool) -> None:
        request = self._current
        if request is None or self._page is None:
            return
        key = {
            "home": "homeItems",
            "search": "searchItems",
            "creator": "creatorItems",
        }.get(request.operation, "homeItems")
        self._page.runJavaScript(
            f"(() => JSON.stringify((window.__tubePlayerXhs && window.__tubePlayerXhs.{key}) || []))();",
            lambda items: self._result_received(items, has_more),
        )

    def _result_received(self, items, has_more: bool) -> None:
        request = self._current
        if request is None:
            return
        try:
            parsed = json.loads(str(items or "[]"))
        except json.JSONDecodeError:
            parsed = []
        rows = [item for item in (parsed or []) if isinstance(item, dict)]
        logger.info(
            "xiaohongshu browser request completed operation=%s page=%s count=%s has_more=%s",
            request.operation,
            request.page,
            len(rows),
            has_more,
        )
        self._finish_current(result={"items": rows, "has_more": bool(has_more)})

    def _notify_verification(self, url: str, reason: str) -> None:
        self._verification_url = _safe_url(url)
        if self._verification_pending:
            return
        self._verification_pending = True
        QTimer.singleShot(0, lambda: self.verification_required.emit(self._verification_url, reason))

    def verification_pending(self) -> bool:
        return self._verification_pending

    def attach_verification_view(self, view, url: str = "") -> None:
        self._ensure_page()
        if self._page is None:
            raise RuntimeError("小红书验证页面尚未初始化")
        target = _safe_url(url or self._verification_url)
        self._verification_url = target
        old_page = view.page()
        if self._view is not None and self._view.page() is self._page:
            self._view.setPage(QWebEnginePage(self._view))
        view.setPage(self._page)
        if old_page is not self._page:
            old_page.deleteLater()
        self._page.setUrl(QUrl(target))

    def reload_verification_page(self) -> None:
        if self._page is not None:
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
        self._page_loaded = False
        self._search_video_filter_active = False
        self._cookie_loaded_at = 0.0

    def reload_settings(self) -> None:
        if self._profile is not None:
            self._profile.cookieStore().deleteAllCookies()
        self._cookie_source = ""
        self._cookie_loaded_at = 0.0
        self._mode = ""
        self._keyword = ""
        self._page_loaded = False
        self._search_video_filter_active = False

    def cancel_verification(self) -> None:
        self._verification_pending = False

    def cancel_home_requests(self) -> None:
        """播放开始时取消未完成的首页请求，释放 WebEngine 页面给前台操作。"""
        current = self._current
        if current is not None and current.operation == "home":
            current.cancelled = True
            self._finish_current(error=RuntimeError("小红书首页加载已让位于播放"))
        retained: deque[_BrowserRequest] = deque()
        while self._queue:
            request = self._queue.popleft()
            if request.operation == "home":
                request.cancelled = True
                request.error = RuntimeError("小红书首页加载已让位于播放")
                request.event.set()
            else:
                retained.append(request)
        self._queue = retained

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
        self._verification_pending = False
        error = RuntimeError("应用正在退出，小红书浏览器请求已取消")
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
