from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from services.douyin_browser_service import (
    DouyinBrowserService,
    DouyinVerificationRequired,
    _BrowserRequest,
    _safe_douyin_url,
)
from ui.douyin_verification_dialog import DouyinVerificationDialog
from ui.main_window import MainWindow


class DouyinVerificationServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_search_verify_check_is_classified(self) -> None:
        payload = {"search_nil_info": {"search_nil_type": "verify_check"}}
        self.assertIn("搜索", DouyinBrowserService._verification_reason(payload))

    def test_home_verification_flag_is_classified(self) -> None:
        payload = {"_tube_player_verification_required": True}
        self.assertIn("推荐流", DouyinBrowserService._verification_reason(payload))

    def test_html_captcha_body_is_classified(self) -> None:
        self.assertTrue(DouyinBrowserService._body_requires_verification("<html>risk-captcha</html>"))
        self.assertFalse(DouyinBrowserService._body_requires_verification('{"captcha":"metadata only"}'))

    def test_runtime_probe_json_string_is_parsed(self) -> None:
        result = DouyinBrowserService._parse_runtime_probe(
            '{"ready":true,"url":"https://www.douyin.com/jingxuan","challenge":false}'
        )
        self.assertTrue(result["ready"])
        self.assertFalse(result["challenge"])

    def test_verification_url_is_limited_to_douyin_domains(self) -> None:
        self.assertEqual(
            _safe_douyin_url("https://verify.zijieapi.com/captcha"),
            "https://verify.zijieapi.com/captcha",
        )
        self.assertEqual(
            _safe_douyin_url("https://example.com/phishing"),
            "https://www.douyin.com/jingxuan",
        )

    def test_browser_profile_is_off_the_record_and_muted(self) -> None:
        config = SimpleNamespace(cookie_browser_for_site=lambda _site: "")
        service = DouyinBrowserService(config)
        service._create_profile_and_page()
        self.assertTrue(service._profile.isOffTheRecord())
        self.assertTrue(service._page.isAudioMuted())
        service.shutdown()

    def test_result_finishes_worker_with_typed_verification_error(self) -> None:
        config = SimpleNamespace(cookie_browser_for_site=lambda _site: "")
        service = DouyinBrowserService(config)
        request = _BrowserRequest(
            request_id=7,
            endpoint="https://www.douyin.com/aweme/v1/web/general/search/single/",
            params={},
            referer="https://www.douyin.com/search/test",
            timeout=10,
        )
        service._current = request
        service._current_result_received(json.dumps({
            "requestId": 7,
            "status": 200,
            "body": json.dumps({"search_nil_info": {"search_nil_item": "verify_check"}}),
        }))

        self.assertTrue(request.event.is_set())
        self.assertIsInstance(request.error, DouyinVerificationRequired)
        self.assertTrue(service.verification_pending())
        service.shutdown()

    def test_foreground_request_can_preempt_home_request(self) -> None:
        config = SimpleNamespace(cookie_browser_for_site=lambda _site: "")
        service = DouyinBrowserService(config)
        request = _BrowserRequest(
            request_id=3,
            endpoint="https://www.douyin.com/aweme/v1/web/tab/feed/",
            params={},
            referer="https://www.douyin.com/jingxuan",
            timeout=30,
            operation="home",
        )
        service._current = request

        service._cancel_current_home_for_priority()

        self.assertTrue(request.cancelled)
        self.assertTrue(request.event.is_set())
        self.assertIsNone(service._current)
        service.shutdown()

    def test_api_timeout_resets_runtime_and_retries_once(self) -> None:
        class FakeBrowser:
            def __init__(self) -> None:
                self.calls = 0
                self.reset = 0

            def request_json(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("抖音浏览器 API 请求超时")
                return {"ok": True}

        service = object.__new__(__import__("resolver.site_resolver", fromlist=["SiteResolver"]).SiteResolver)
        browser = FakeBrowser()
        service._douyin_browser_client = browser

        result = service._request_douyin_browse_json(
            "https://www.douyin.com/aweme/v1/web/general/search/single/",
            {"keyword": "测试"},
            "https://www.douyin.com/search/测试",
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(browser.calls, 2)


class _FakeVerificationService:
    def __init__(self) -> None:
        self.attached = 0
        self.detached = 0
        self.reloaded = 0

    def attach_verification_view(self, _view, _url) -> None:
        self.attached += 1

    def detach_verification_view(self, _view) -> None:
        self.detached += 1

    def reload_verification_page(self) -> None:
        self.reloaded += 1


class DouyinVerificationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_attaches_page_and_emits_continue(self) -> None:
        service = _FakeVerificationService()
        dialog = DouyinVerificationDialog(
            service,
            "https://www.douyin.com/jingxuan",
            "需要验证",
        )
        continued: list[bool] = []
        dialog.continue_requested.connect(lambda: continued.append(True))

        self.assertEqual(service.attached, 1)
        dialog.refresh_button.click()
        self.assertEqual(service.reloaded, 1)
        dialog.continue_button.click()
        self.assertEqual(continued, [True])
        self.assertEqual(service.detached, 1)
        dialog.deleteLater()

    def test_dialog_emits_cancel_once(self) -> None:
        service = _FakeVerificationService()
        dialog = DouyinVerificationDialog(service, "", "")
        cancelled: list[bool] = []
        dialog.cancel_requested.connect(lambda: cancelled.append(True))

        dialog.reject()
        dialog.reject()
        self.assertEqual(cancelled, [True])
        self.assertEqual(service.detached, 1)
        dialog.deleteLater()


class _FakeBrowserService:
    def __init__(self) -> None:
        self.completed = 0

    def complete_verification(self) -> None:
        self.completed += 1


class _FakeToast:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_message(self, message: str) -> None:
        self.messages.append(message)


class DouyinVerificationRetryTests(unittest.TestCase):
    def test_home_retry_runs_once_after_verification(self) -> None:
        calls: list[tuple[int, bool]] = []
        state = SimpleNamespace(
            _douyin_verification_retry_context=("home", "", 3, 9),
            douyin_browser_service=_FakeBrowserService(),
            _browse_source="douyin",
            _browse_generation=9,
            _shutting_down=False,
            _home_page=1,
            _start_home_load=lambda page, force_refresh=False: calls.append((page, force_refresh)),
            toast=_FakeToast(),
        )

        with patch("ui.main_window.QTimer.singleShot", side_effect=lambda _delay, callback: callback()):
            MainWindow._complete_douyin_verification(state)
            MainWindow._complete_douyin_verification(state)

        self.assertEqual(calls, [(3, True)])
        self.assertEqual(state.douyin_browser_service.completed, 2)

    def test_search_retry_restores_keyword_and_page(self) -> None:
        calls: list[tuple[str, int, bool]] = []
        state = SimpleNamespace(
            _douyin_verification_retry_context=("search", "净水器", 2, 4),
            douyin_browser_service=_FakeBrowserService(),
            _browse_source="douyin",
            _browse_generation=4,
            _shutting_down=False,
            _search_keyword="",
            _search_page=1,
            _start_search=lambda keyword, page, force_refresh=False: calls.append(
                (keyword, page, force_refresh)
            ),
            toast=_FakeToast(),
        )

        with patch("ui.main_window.QTimer.singleShot", side_effect=lambda _delay, callback: callback()):
            MainWindow._complete_douyin_verification(state)

        self.assertEqual(calls, [("净水器", 2, True)])
        self.assertEqual(state._search_keyword, "净水器")
        self.assertEqual(state._search_page, 2)

if __name__ == "__main__":
    unittest.main()
