from __future__ import annotations

import os
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.main_window import MainWindow  # noqa: E402


class _TextField:
    def __init__(self, value: str) -> None:
        self.value = value

    def text(self) -> str:
        return self.value


def _state(*, browse_mode: str = "home", keyword: str = "", source: str = "bilibili") -> SimpleNamespace:
    actions: list[dict[str, object]] = []
    stored_sources: list[str] = []
    url_edit = _TextField(keyword)
    state = SimpleNamespace(
        actions=actions,
        stored_sources=stored_sources,
        url_edit=url_edit,
        _browse_mode=browse_mode,
        _browse_source=source,
        _home_cache=[object()],
        _home_page=3,
        _home_has_next=True,
        _search_keyword="旧关键词",
        _search_page=4,
        _browse_source_switch_frozen=False,
        resolver=SimpleNamespace(home_source_label=lambda source: source),
    )

    def store_home_state(current_source: str) -> None:
        stored_sources.append(current_source)

    def load_home() -> None:
        state._browse_mode = "home"
        actions.append({"kind": "home", "source": state._browse_source, "page": 1})

    def start_search(search_keyword: str, page: int, **_kwargs) -> None:
        state._browse_mode = "search"
        actions.append(
            {
                "kind": "search",
                "source": state._browse_source,
                "keyword": search_keyword,
                "page": page,
            }
        )

    state._store_home_state = store_home_state
    state.load_home = load_home
    state._start_search = start_search
    state._set_browse_source_switch_frozen = lambda frozen: setattr(state, "_browse_source_switch_frozen", bool(frozen))
    state._set_browse_source = MethodType(MainWindow._set_browse_source, state)
    return state


class BrowseSourceSwitchTests(unittest.TestCase):
    def test_home_action_with_residual_text_loads_home_in_both_directions(self) -> None:
        for source, target in (("bilibili", "youtube"), ("youtube", "bilibili")):
            with self.subTest(source=source, target=target):
                state = _state(browse_mode="search", keyword="周杰伦", source=source)
                rendered: list[object] = []
                state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None)
                state.stack = SimpleNamespace(setCurrentWidget=lambda _widget: None)
                state._render_home = lambda *_args: rendered.append(object())

                MainWindow._show_home(state)
                state.actions.clear()
                state._set_browse_source(target)

                self.assertEqual(state.actions, [{"kind": "home", "source": target, "page": 1}])
                self.assertEqual(state._browse_mode, "home")
                self.assertEqual(state.url_edit.text(), "周杰伦")
                self.assertTrue(rendered)

    def test_home_mode_without_text_loads_new_site_home(self) -> None:
        state = _state(browse_mode="home", keyword="")
        state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None)

        state._set_browse_source("youtube")

        self.assertEqual(state.actions, [{"kind": "home", "source": "youtube", "page": 1}])
        self.assertEqual(state._search_keyword, "")
        self.assertEqual(state._search_page, 1)

    def test_search_mode_with_text_repeats_search_in_both_directions(self) -> None:
        for source, target in (("bilibili", "youtube"), ("youtube", "bilibili")):
            with self.subTest(source=source, target=target):
                state = _state(browse_mode="search", keyword="  周杰伦  ", source=source)
                state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None)

                state._set_browse_source(target)

                self.assertEqual(
                    state.actions,
                    [{"kind": "search", "source": target, "keyword": "周杰伦", "page": 1}],
                )
                self.assertEqual(state._search_keyword, "周杰伦")
                self.assertEqual(state._search_page, 1)
                self.assertEqual(state.url_edit.text(), "  周杰伦  ")

    def test_search_mode_with_empty_text_falls_back_to_home(self) -> None:
        state = _state(browse_mode="search", keyword="   ")
        state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None)

        state._set_browse_source("youtube")

        self.assertEqual(state.actions, [{"kind": "home", "source": "youtube", "page": 1}])
        self.assertEqual(state._browse_mode, "home")
        self.assertEqual(state._search_keyword, "")

    def test_startup_home_mode_switches_to_home(self) -> None:
        state = _state()
        state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None)

        state._set_browse_source("youtube")

        self.assertEqual(state.actions, [{"kind": "home", "source": "youtube", "page": 1}])
        self.assertEqual(state.stored_sources, ["bilibili"])
        self.assertEqual(state._home_cache, [])
        self.assertEqual(state._home_page, 1)
        self.assertFalse(state._home_has_next)

    def test_same_site_selection_is_a_noop(self) -> None:
        state = _state(browse_mode="search", keyword="周杰伦")

        state._set_browse_source(" BILIBILI ")

        self.assertEqual(state.actions, [])
        self.assertEqual(state.stored_sources, [])
        self.assertEqual(state._browse_source, "bilibili")
        self.assertEqual(state._search_keyword, "旧关键词")
        self.assertEqual(state._home_page, 3)

    def test_frozen_source_switch_is_ignored(self) -> None:
        state = _state(source="bilibili")
        state._browse_source_switch_frozen = True
        state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None)

        MainWindow._set_browse_source(state, "youtube")

        self.assertEqual(state._browse_source, "bilibili")
        self.assertEqual(state.actions, [])

    def test_source_switch_freezes_until_browse_worker_finishes(self) -> None:
        state = _state(source="bilibili")
        state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None, set_loading=lambda *_args: None)

        MainWindow._set_browse_source(state, "youtube")
        self.assertTrue(state._browse_source_switch_frozen)

        state._browse_generation = 1
        state._browse_verification_pending_for_current_source = lambda: False
        MainWindow._browse_load_finished(state, 1)

        self.assertFalse(state._browse_source_switch_frozen)


class BrowseStateResetTests(unittest.TestCase):
    def test_saving_settings_resets_stale_search_mode(self) -> None:
        home_page = object()
        actions: list[str] = []
        state = SimpleNamespace(
            config=object(),
            mpv=SimpleNamespace(apply_network_options=lambda: None),
            download_manager=SimpleNamespace(reload_settings=lambda: None),
            player_page=SimpleNamespace(reload_shortcuts=lambda: None),
            stack=SimpleNamespace(currentWidget=lambda: object()),
            home_page=home_page,
            _home_cache=[object()],
            _home_page=3,
            _home_has_next=True,
            _home_state={"bilibili": object()},
            _search_keyword="周杰伦",
            _search_page=4,
            _browse_mode="search",
            _invalidate_creator_playlist_request=lambda: None,
            _refresh_runtime_status=lambda: None,
            _sync_about_page=lambda: None,
            load_home=lambda: self.fail("设置页保存时不应立即加载首页"),
        )
        with (
            patch("ui.main_window.SiteResolver", return_value=object()),
            patch("ui.main_window.UpdateService", return_value=object()),
            patch("ui.main_window.RuntimeInstallService", return_value=object()),
            patch("ui.main_window.FfmpegInstallService", return_value=object()),
        ):
            MainWindow._settings_saved(state)

        self.assertEqual(state._browse_mode, "home")
        self.assertEqual(state._search_keyword, "")
        self.assertEqual(state._search_page, 1)
        self.assertEqual(state._home_state, {})

        state.url_edit = _TextField("周杰伦")
        state._browse_source = "bilibili"
        state.home_page = SimpleNamespace(clear_videos=lambda **_kwargs: None)
        state.resolver = SimpleNamespace(home_source_label=lambda source: source)
        state._store_home_state = lambda _source: None
        state.load_home = lambda: actions.append("home")
        state._start_search = lambda *_args, **_kwargs: actions.append("search")
        MainWindow._set_browse_source(state, "youtube")

        self.assertEqual(actions, ["home"])
        self.assertEqual(state.url_edit.text(), "周杰伦")

if __name__ == "__main__":
    unittest.main()
