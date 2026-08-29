from __future__ import annotations

import unittest
from collections import OrderedDict
from types import SimpleNamespace

from download.command_builder import build_download_task
from resolver.models import VideoInfo, VideoQuality
from resolver.source_utils import detect_source_site, source_site_label
from resolver.youtube_resolver import YoutubeResolver, _current_video_id_for_site, _detect_source_site
from services.site_registry import SITE_KEYS, site_for_url
from resolver.site_resolver import SiteResolver
from resolver.quality_selector import QualitySelector


class DouyinTikTokSupportTests(unittest.TestCase):
    def test_site_detection_and_labels(self) -> None:
        cases = (
            ("https://www.douyin.com/video/123", "douyin", "抖音"),
            ("https://v.douyin.com/abc/", "douyin", "抖音"),
            ("https://www.tiktok.com/@user/video/456", "tiktok", "TikTok"),
            ("https://vm.tiktok.com/abc/", "tiktok", "TikTok"),
        )
        for url, site, label in cases:
            self.assertEqual(site_for_url(url), site)
            self.assertEqual(detect_source_site(url), site)
            self.assertEqual(source_site_label(site, url), label)
            self.assertEqual(_detect_source_site(url), site)

    def test_site_keys_include_both_new_sites(self) -> None:
        self.assertEqual(SITE_KEYS, ("bilibili", "youtube", "douyin", "tiktok"))

    def test_generic_video_ids_are_stable(self) -> None:
        self.assertEqual(_current_video_id_for_site("https://www.douyin.com/video/123", "douyin"), "douyin:123")
        self.assertEqual(_current_video_id_for_site("https://www.tiktok.com/@u/video/456", "tiktok"), "tiktok:456")

    def test_generic_playlist_entry_mapping(self) -> None:
        entry = YoutubeResolver._parse_generic_playlist_entry(
            {"id": "456", "title": "TikTok clip", "webpage_url": "https://www.tiktok.com/@u/video/456"},
            "tiktok:user:u",
            1,
            "tiktok",
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.video_id, "tiktok:456")
        self.assertEqual(entry.source_site, "tiktok")

    def test_home_feed_maps_douyin_and_tiktok_payloads(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._page_cache = {}
        resolver.config = SimpleNamespace(
            cookie_browser_for_site=lambda _site: "",
            effective_proxy=lambda: ("", ""),
        )
        payloads = {
            "douyin": {
                "aweme_list": [{
                    "aweme_id": "123", "desc": "Douyin clip", "author": {"nickname": "作者"},
                    "video": {"duration": 12000, "cover": {"url_list": ["https://img/d.jpg"]}},
                }], "has_more": 1,
            },
            "tiktok": {
                "itemList": [{
                    "id": "456", "desc": "TikTok clip", "author": {"uniqueId": "user", "nickname": "User"},
                    "video": {"duration": 9, "cover": "https://img/t.jpg"},
                }], "hasMore": True,
            },
        }
        resolver._request_short_video_json = lambda site, *_args: payloads[site]

        douyin, _ = resolver._fetch_short_video_home("douyin", 1, 1)
        tiktok, _ = resolver._fetch_short_video_home("tiktok", 1, 1)

        self.assertEqual(douyin[0].video_id, "douyin:123")
        self.assertEqual(douyin[0].duration, 12)
        self.assertEqual(tiktok[0].video_id, "tiktok:456")
        self.assertEqual(tiktok[0].webpage_url, "https://www.tiktok.com/@user/video/456")

    def test_portrait_quality_uses_short_edge_label(self) -> None:
        qualities = QualitySelector.select_all([{
            "format_id": "portrait-1080", "url": "https://cdn/video.mp4", "width": 1080,
            "height": 1920, "fps": 30, "vcodec": "h264", "acodec": "aac", "ext": "mp4",
        }])
        self.assertIn("1080p", qualities)

    def test_douyin_creator_payload_maps_to_entries(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver.config = SimpleNamespace()
        resolver._request_short_video_json = lambda *_args: {
            "aweme_list": [{"aweme_id": "123", "desc": "Clip", "author": {"nickname": "A"},
                             "video": {"duration": 10000, "cover": {"url_list": ["x"]}}}],
            "has_more": 0,
        }
        video = SimpleNamespace(channel_id="sec-uid", creator_url="https://www.douyin.com/user/sec-uid")
        _url, entries = resolver._fetch_douyin_creator_videos(video, 10)
        self.assertEqual(entries[0].video_id, "douyin:123")

    def test_douyin_search_reads_aweme_info_cards(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._douyin_browse_min_interval_seconds = 0
        resolver._request_short_video_json = lambda *_args: {
            "data": [{"aweme_info": {"aweme_id": "123", "desc": "科技", "author": {"nickname": "A"},
                                        "video": {"duration": 10000, "cover": {"url_list": ["x"]}}}}],
            "has_more": 1,
        }
        videos, more = resolver._search_douyin("科技", 1, 10)
        self.assertEqual(videos[0].video_id, "douyin:123")
        self.assertTrue(more)

    def test_tiktok_search_fallback_filters_feed(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._fetch_short_video_home = lambda _site, page, _size: ([
            SimpleNamespace(video_id=str(page), title="Technology news" if page == 1 else "Cooking", uploader="A"),
        ], page < 2)
        videos, _more = resolver._search_tiktok_fallback("technology", 1, 10)
        self.assertEqual(len(videos), 1)

    def test_tiktok_search_fallback_does_not_silently_return_empty(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._fetch_short_video_home = lambda *_args: ([
            SimpleNamespace(video_id="1", title="Cooking", uploader="A"),
        ], False)
        with self.assertRaisesRegex(RuntimeError, "TikTok 搜索暂时无法获取结果"):
            resolver._search_tiktok_fallback("净水器", 1, 10)

    def test_tiktok_page_two_does_not_slice_cursor_results_away(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver.config = SimpleNamespace(
            cookie_browser_for_site=lambda _site: "",
            effective_proxy=lambda: ("", ""),
        )
        resolver._request_short_video_json = lambda _site, _endpoint, params, _root: {
            "itemList": [{
                "id": str(params["cursor"]), "desc": "clip", "author": {"uniqueId": "u"},
                "video": {"duration": 1, "cover": "x"},
            }],
            "hasMore": False,
        }
        videos, _more = resolver._fetch_short_video_home("tiktok", 2, 1)
        self.assertEqual(len(videos), 1)

    def test_tiktok_official_search_reads_item_list_and_caches_items(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver.config = SimpleNamespace(cookie_browser_for_site=lambda _site: "")
        resolver._request_short_video_json = lambda *_args: {
            "item_list": [{
                "id": "789", "desc": "净水器", "author": {"uniqueId": "u"},
                "video": {"duration": 2, "cover": "x"},
            }],
            "has_more": 1,
        }
        videos, more = resolver._search_tiktok("净水器", 1, 10)
        self.assertEqual(videos[0].video_id, "tiktok:789")
        self.assertTrue(more)
        self.assertIsNotNone(resolver._short_video_item_lookup(videos[0].webpage_url))

    def test_tiktok_search_propagates_session_id_and_aggregates_batches(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver.config = SimpleNamespace(cookie_browser_for_site=lambda _site: "")
        calls: list[dict[str, str]] = []

        def request(_site, _endpoint, params, _referer):
            calls.append(dict(params))
            cursor = int(params["cursor"])
            if cursor:
                self.assertEqual(params.get("search_id"), "search-session")
            return {
                "item_list": [{
                    "id": str(cursor + index), "desc": "净水器", "author": {"uniqueId": "u"},
                    "video": {"duration": 2, "cover": "x"},
                } for index in range(20)],
                "cursor": cursor + 20,
                "has_more": 1,
                "log_pb": {"impr_id": "search-session"},
            }

        resolver._request_short_video_json = request
        videos, more = resolver._search_tiktok("净水器", 1, 56)
        self.assertEqual(len(videos), 56)
        self.assertTrue(more)
        self.assertEqual([call["cursor"] for call in calls], ["0", "20", "40"])

        calls.clear()
        page_two, more = resolver._search_tiktok("净水器", 2, 56)
        self.assertEqual(len(page_two), 56)
        self.assertEqual(page_two[0].video_id, "tiktok:56")
        self.assertTrue(more)
        self.assertEqual([call["cursor"] for call in calls], ["0", "20", "40", "60", "80", "100"])

    def test_douyin_search_uses_one_native_batch_per_ui_page(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._douyin_browse_min_interval_seconds = 0
        calls: list[dict[str, str]] = []

        def request(_site, endpoint, params, _referer):
            self.assertIn("/general/search/single/", endpoint)
            calls.append(dict(params))
            offset = int(params["offset"])
            if offset:
                self.assertEqual(params.get("search_id"), "douyin-session")
            return {
                "data": [{
                    "aweme_info": {
                        "aweme_id": str(offset + index), "desc": "净水器",
                        "author": {"nickname": "A"},
                        "video": {"duration": 2000, "cover": {"url_list": ["x"]}},
                    },
                } for index in range(20)],
                "cursor": offset + 20,
                "has_more": 1,
                "log_pb": {"impr_id": "douyin-session"},
            }

        resolver._request_short_video_json = request
        videos, more = resolver._search_douyin("净水器", 1, 56)
        self.assertEqual(len(videos), 20)
        self.assertTrue(more)
        self.assertEqual([call["offset"] for call in calls], ["0"])

        page_two, more = resolver._search_douyin("净水器", 2, 56)
        self.assertEqual(len(page_two), 20)
        self.assertTrue(more)
        self.assertEqual([call["offset"] for call in calls], ["0", "20"])
        self.assertEqual(calls[1]["search_id"], "douyin-session")

    def test_douyin_search_returns_partial_native_batch_without_extra_request(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._douyin_browse_min_interval_seconds = 0
        calls: list[int] = []

        def request(_site, _endpoint, params, _referer):
            offset = int(params["offset"])
            calls.append(offset)
            return {
                "data": [{
                    "aweme_info": {
                        "aweme_id": str(offset + index), "desc": "钟馗",
                        "author": {"nickname": "A"},
                        "video": {"duration": 2000, "cover": {"url_list": ["x"]}},
                    },
                } for index in range(17)],
                "cursor": offset + 20,
                "has_more": 0,
                "log_pb": {"impr_id": "douyin-session"},
            }

        resolver._request_short_video_json = request
        videos, more = resolver._search_douyin("钟馗", 1, 56)

        self.assertEqual(len(videos), 17)
        self.assertTrue(more)
        self.assertEqual(calls, [0])
        session = next(iter(resolver._douyin_search_sessions.values()))
        self.assertEqual(session["page_ranges"][1], (0, 17))
        self.assertTrue(session["has_more"])

    def test_douyin_search_continues_after_partial_page_boundary(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._douyin_browse_min_interval_seconds = 0
        phase = "blocked"

        def request(_site, _endpoint, params, _referer):
            nonlocal phase
            offset = int(params["offset"])
            if phase == "blocked" and offset:
                return {"search_nil_info": {"search_nil_item": "verify_check"}}
            size = 17 if offset == 0 else 20
            return {
                "data": [{
                    "aweme_info": {
                        "aweme_id": str(offset + index), "desc": "钟馗",
                        "author": {"nickname": "A"},
                        "video": {"duration": 2000, "cover": {"url_list": ["x"]}},
                    },
                } for index in range(size)],
                "cursor": offset + 20,
                "has_more": 1,
                "log_pb": {"impr_id": "douyin-session"},
            }

        resolver._request_short_video_json = request
        first_page, more = resolver._search_douyin("钟馗", 1, 56)
        self.assertEqual(len(first_page), 17)
        self.assertTrue(more)

        phase = "continued"
        session = next(iter(resolver._douyin_search_sessions.values()))
        session["blocked_until"] = 0
        second_page, more = resolver._search_douyin("钟馗", 2, 56)

        self.assertEqual(len(second_page), 20)
        self.assertTrue(more)
        self.assertEqual(session["page_ranges"][2][0], 17)
        self.assertEqual(second_page[0].video_id, "douyin:20")

    def test_douyin_search_force_refresh_falls_back_to_fuller_session(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._douyin_browse_min_interval_seconds = 0
        blocked = False

        def request(_site, _endpoint, params, _referer):
            if blocked:
                return {"search_nil_info": {"search_nil_type": "verify_check"}}
            offset = int(params["offset"])
            return {
                "data": [{
                    "aweme_info": {
                        "aweme_id": str(offset + index), "desc": "净水器",
                        "author": {"nickname": "A"},
                        "video": {"duration": 2000, "cover": {"url_list": ["x"]}},
                    },
                } for index in range(20)],
                "cursor": offset + 20,
                "has_more": 1,
                "log_pb": {"impr_id": "douyin-session"},
            }

        resolver._request_short_video_json = request
        original, _more = resolver._search_douyin("净水器", 1, 56)
        blocked = True
        refreshed, more = resolver._search_douyin("净水器", 1, 56, force_refresh=True)

        self.assertEqual([item.video_id for item in refreshed], [item.video_id for item in original])
        self.assertTrue(more)

    def test_douyin_home_limits_requests_and_uses_shared_browse_gate(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._douyin_browse_min_interval_seconds = 0
        resolver.config = SimpleNamespace(
            cookie_browser_for_site=lambda _site: "firefox:default-release",
            effective_proxy=lambda: ("", ""),
        )
        calls: list[int] = []

        def request(_site, _endpoint, params, _referer):
            self.assertEqual(params["browser_name"], "Firefox")
            self.assertEqual(params["engine_name"], "Gecko")
            cursor = int(params["cursor"])
            calls.append(cursor)
            return {
                "aweme_list": [{
                    "aweme_id": str(cursor), "desc": "clip", "author": {"nickname": "A"},
                    "video": {"duration": 1000, "cover": {"url_list": ["x"]}},
                }],
                "has_more": 1,
            }

        resolver._request_short_video_json = request
        videos, more = resolver._fetch_short_video_home("douyin", 1, 56)

        self.assertEqual(len(videos), 1)
        self.assertTrue(more)
        self.assertEqual(len(calls), 1)

        page_two, more = resolver._fetch_short_video_home("douyin", 2, 56)
        self.assertEqual(len(page_two), 1)
        self.assertTrue(more)
        self.assertEqual(calls, [0, 1])

    def test_douyin_home_signed_browser_aggregates_natural_scroll_batches(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._config_fingerprint = lambda _site: "signed"
        resolver.config = SimpleNamespace(cookie_browser_for_site=lambda _site: "firefox:profile")

        class BrowserClient:
            def __init__(self) -> None:
                self.requests: list[tuple[int, int]] = []

            def request_home_json(self, _endpoint, _params, _referer, *, page, target_count):
                self.requests.append((page, target_count))
                return {
                    "aweme_list": [{
                        "aweme_id": str(index), "desc": "clip",
                        "author": {"nickname": "A"},
                        "video": {"duration": 1000, "cover": {"url_list": ["x"]}},
                    } for index in range(target_count)],
                    "cursor": page * 9,
                    "has_more": 1,
                }

        browser = BrowserClient()
        resolver._douyin_browser_client = browser
        videos, more = resolver._fetch_short_video_home("douyin", 1, 56)

        self.assertEqual(len(videos), 18)
        self.assertTrue(more)
        self.assertEqual(browser.requests, [(1, 18)])

    def test_douyin_signed_browser_does_not_fall_back_to_unsigned_http(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)

        class BrowserClient:
            @staticmethod
            def request_json(_endpoint, _params, _referer):
                raise RuntimeError("signed request failed")

        resolver._douyin_browser_client = BrowserClient()
        resolver._request_short_video_json = lambda *_args: self.fail("unsigned fallback must not run")

        with self.assertRaisesRegex(RuntimeError, "signed request failed"):
            resolver._request_douyin_browse_json("https://example", {}, "https://www.douyin.com/")

    def test_tiktok_video_uses_secuid_and_cached_creator_entries(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._short_video_item_cache = OrderedDict()
        resolver._short_video_item_cache_lock = __import__("threading").Lock()
        resolver._creator_cache = OrderedDict()
        resolver._creator_cache_lock = __import__("threading").Lock()
        resolver._config_fingerprint = lambda _site: "test"
        resolver.config = SimpleNamespace(cookie_browser_for_site=lambda _site: "")
        for item_id in ("1", "2", "3"):
            resolver._short_video_item_store("tiktok", {
                "id": item_id,
                "desc": f"clip {item_id}",
                "author": {
                    "uniqueId": "678hahaha", "nickname": "Display Name", "secUid": "MS4-sec-uid",
                },
                "video": {
                    "duration": 3, "cover": "cover",
                    "playAddr": {"UrlList": [f"https://cdn.example/{item_id}.mp4"], "Width": 576, "Height": 1024},
                },
            })

        video = resolver.resolve_cached_short_video("https://www.tiktok.com/@678hahaha/video/1")
        self.assertIsNotNone(video)
        self.assertEqual(video.uploader, "Display Name")
        self.assertEqual(video.creator_id, "MS4-sec-uid")
        self.assertEqual(video.channel_id, "MS4-sec-uid")
        self.assertEqual(video.creator_url, "https://www.tiktok.com/@678hahaha")
        playlist = resolver.resolve_creator_playlist(video, 50)
        self.assertIsNotNone(playlist)
        self.assertEqual(len(playlist.entries), 3)
        self.assertIn("Display Name", playlist.title)

    def test_tiktok_download_reuses_media_url_without_persisting_cookie(self) -> None:
        quality = VideoQuality(
            label="576p", height=1024, width=576, fps=30, vcodec="h264", acodec="aac",
            ext="mp4", format_id="tiktok-1-576x1024", video_url="https://www.tiktok.com/aweme/v1/play/?id=1",
        )
        video = VideoInfo(
            video_id="tiktok:1", title="clip", source_site="tiktok",
            webpage_url="https://www.tiktok.com/@u/video/1", qualities=OrderedDict((("576p", quality),)),
            http_headers={
                "User-Agent": "browser", "Referer": "https://www.tiktok.com/@u/video/1",
                "Origin": "https://www.tiktok.com", "Cookie": "sessionid=secret",
            },
        )
        task = build_download_task(video, "Auto", SimpleNamespace(download_dir=lambda: "downloads"))
        self.assertEqual(task.url, video.webpage_url)
        self.assertEqual(task.download_url, quality.video_url)
        self.assertEqual(task.format_selector, "best")
        self.assertEqual(task.quality_label, "576p")
        self.assertNotIn("Cookie", task.http_headers)
        self.assertNotIn("secret", str(task.to_dict()))

    def test_cached_tiktok_item_resolves_without_ytdlp(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._short_video_item_cache = {}
        resolver._short_video_item_cache_lock = __import__("threading").Lock()
        item = {
            "id": "456", "desc": "TikTok clip", "author": {"uniqueId": "user", "nickname": "User"},
            "video": {"duration": 9, "cover": "https://img/t.jpg",
                      "PlayAddrStruct": {"UrlList": ["https://cdn/t.mp4"], "Width": 576, "Height": 1024}},
        }
        resolver._short_video_item_store("tiktok", item)
        video = resolver.resolve("https://www.tiktok.com/@user/video/456")
        self.assertEqual(video.video_id, "tiktok:456")
        self.assertIn("576p", video.qualities)
        self.assertEqual(video.qualities["576p"].video_url, "https://cdn/t.mp4")

    def test_short_video_playback_prefers_authenticated_site_gateway(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._short_video_item_cache = {}
        resolver._short_video_item_cache_lock = __import__("threading").Lock()
        resolver.config = SimpleNamespace(cookie_browser_for_site=lambda _site: "")
        gateway = "https://www.tiktok.com/aweme/v1/play/?item_id=456"
        item = {
            "id": "456", "desc": "TikTok clip", "author": {"uniqueId": "user"},
            "video": {
                "duration": 9, "cover": "https://img/t.jpg", "width": 720, "height": 1280,
                "playAddr": "https://cdn.example/blocked-hd.mp4",
                "bitrateInfo": [{
                    "PlayAddr": {
                        "UrlList": ["https://cdn.example/blocked.mp4", gateway],
                        "Width": 576, "Height": 1024,
                    },
                    "CodecType": "h264",
                }],
            },
        }
        video = resolver._video_info_from_short_video_item(item, "tiktok", "")
        self.assertNotIn("720p", video.qualities)
        self.assertEqual(video.qualities["576p"].video_url, gateway)

    def test_short_video_playback_cookie_drops_unrelated_browser_state(self) -> None:
        compact = SiteResolver._compact_short_video_cookie_header(
            "sessionid=login; large_ui_state=" + ("x" * 20_000) + "; ttwid=device",
            "douyin",
        )
        self.assertEqual(compact, "sessionid=login; ttwid=device")

    def test_douyin_creator_playlist_prefers_nickname(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._creator_cache = OrderedDict()
        resolver._creator_cache_lock = __import__("threading").Lock()
        resolver._config_fingerprint = lambda _site: "test"
        resolver._fetch_douyin_creator_videos = lambda _video, _limit: ("https://www.douyin.com/user/sec", [
            SimpleNamespace(source_site="douyin", video_id="douyin:2", webpage_url="https://www.douyin.com/video/2",
                             title="other", uploader="中国军号", duration=1, thumbnail="", availability=""),
        ])
        video = SimpleNamespace(source_site="douyin", video_id="douyin:1", webpage_url="https://www.douyin.com/video/1",
                                creator_id="jfjxwcbzx", channel_id="jfjxwcbzx", creator_url="https://www.douyin.com/user/sec",
                                uploader="jfjxwcbzx", title="current", duration=1, thumbnail="", raw_info={})
        playlist = resolver.resolve_creator_playlist(video, 10)
        self.assertIn("中国军号", playlist.title)


if __name__ == "__main__":
    unittest.main()
