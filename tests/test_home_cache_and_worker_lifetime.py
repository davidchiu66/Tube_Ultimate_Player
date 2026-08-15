from __future__ import annotations

import gc
import os
import time
import unittest
from types import MethodType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from resolver.models import HomeVideo
from ui.main_window import HOME_CACHE_TTL_SECONDS, MainWindow


def _video(video_id: str) -> HomeVideo:
    return HomeVideo(video_id=video_id, title=video_id, webpage_url=f"https://example.com/{video_id}")


class _FakeHomePage:
    def __init__(self) -> None:
        self.rendered: list[tuple[list[HomeVideo], int, bool]] = []
        self.loading: list[bool] = []
        self.contexts: list[tuple[int, bool]] = []

    def set_videos(self, videos, *, mode="home", page=1, has_next=False, keyword="") -> None:
        self.rendered.append((list(videos), page, has_next))

    def set_home_context(self, page, has_next, source_label="") -> None:
        self.contexts.append((page, has_next))

    def set_favorite_ids(self, _ids) -> None:
        pass

    def set_loading(self, loading, _message="") -> None:
        self.loading.append(bool(loading))


class _HomeCacheState(SimpleNamespace):
    """只挂上首页缓存链路需要的字段，避免构造整个 MainWindow。"""


def _state() -> _HomeCacheState:
    home_page = _FakeHomePage()
    started: list[dict] = []
    state = _HomeCacheState(
        home_page=home_page,
        started=started,
        _home_cache=[],
        _home_page=1,
        _home_has_next=False,
        _home_state={},
        _browse_mode="home",
        _browse_source="bilibili",
        _browse_generation=0,
        _search_keyword="",
        _search_page=1,
        _shutting_down=False,
        stack=SimpleNamespace(setCurrentWidget=lambda _widget: None),
        favorites=SimpleNamespace(favorite_ids=lambda: set()),
        resolver=SimpleNamespace(home_source_label=lambda source: source),
        url_edit=SimpleNamespace(text=lambda: ""),
    )
    for name in (
        "_store_home_state",
        "_take_home_state",
        "_render_home",
        "_apply_home_cache",
        "_show_home",
        "_set_browse_source",
        "load_home",
    ):
        setattr(state, name, MethodType(getattr(MainWindow, name), state))
    # 真实 _start_home_load 会构造 HomeWorker 并提交线程池，这里只记录调用参数。
    state._start_home_load = MethodType(_recording_start_home_load, state)
    state._start_search = MethodType(_recording_start_search, state)
    return state


def _recording_start_home_load(self, page: int, *, force_refresh: bool = False) -> None:
    source = self._browse_source
    target_page = max(1, page)
    self._browse_mode = "home"
    if force_refresh:
        self._home_state.pop(source, None)
    else:
        cached = self._take_home_state(source, target_page)
        if cached is not None:
            videos, cached_page, has_next = cached
            self._apply_home_cache(videos, cached_page, has_next, reason="page")
            return
    self._home_page = target_page
    self._browse_generation += 1
    self.started.append({"kind": "home", "page": target_page, "source": source})


def _recording_start_search(self, keyword: str, page: int, **_kwargs) -> None:
    self._browse_mode = "search"
    self.started.append({"kind": "search", "keyword": keyword, "page": page})


class HomeCacheTests(unittest.TestCase):
    def test_switching_back_reuses_the_cached_home_without_a_worker(self) -> None:
        state = _state()
        state._home_cache = [_video("b1")]
        state._home_has_next = True
        state._store_home_state("bilibili")

        state._set_browse_source("youtube")
        self.assertEqual([item["kind"] for item in state.started], ["home"])
        self.assertEqual(state.started[0]["source"], "youtube")

        state._home_cache = [_video("y1")]
        state._store_home_state("youtube")
        state._set_browse_source("bilibili")

        # 切回来不再发起新的 home worker，直接用缓存重绘。
        self.assertEqual([item["kind"] for item in state.started], ["home"])
        self.assertEqual([v.video_id for v in state._home_cache], ["b1"])
        self.assertTrue(state._home_has_next)
        self.assertEqual(state.home_page.rendered[-1][0][0].video_id, "b1")
        self.assertFalse(state.home_page.loading[-1])

    def test_cache_hit_advances_generation_so_stale_results_are_dropped(self) -> None:
        state = _state()
        state._browse_mode = "search"
        state._home_cache = [_video("b1")]
        state._store_home_state("bilibili")
        before = state._browse_generation

        state._apply_home_cache([_video("b1")], 1, False, reason="page")

        self.assertGreater(state._browse_generation, before)
        self.assertEqual(state._browse_mode, "home")

    def test_expired_cache_falls_back_to_a_fresh_load(self) -> None:
        state = _state()
        state._home_cache = [_video("b1")]
        state._store_home_state("bilibili")
        cached_at, videos, page, has_next = state._home_state["bilibili"]
        state._home_state["bilibili"] = (cached_at - HOME_CACHE_TTL_SECONDS - 1, videos, page, has_next)

        self.assertIsNone(state._take_home_state("bilibili"))

        state._home_cache = []
        state._show_home()
        self.assertEqual([item["kind"] for item in state.started], ["home"])

    def test_cached_page_number_must_match_the_requested_page(self) -> None:
        state = _state()
        state._home_cache = [_video("b1")]
        state._home_page = 2
        state._store_home_state("bilibili")

        self.assertIsNone(state._take_home_state("bilibili", 1))
        self.assertIsNotNone(state._take_home_state("bilibili", 2))
        self.assertIsNotNone(state._take_home_state("bilibili"))

    def test_switching_after_search_still_searches_instead_of_using_the_cache(self) -> None:
        state = _state()
        state._browse_mode = "search"
        state._home_cache = [_video("b1")]
        state._store_home_state("bilibili")
        state.url_edit = SimpleNamespace(text=lambda: "  辣椒炒肉  ")

        state._set_browse_source("youtube")

        self.assertEqual([item["kind"] for item in state.started], ["search"])
        self.assertEqual(state.started[0]["keyword"], "辣椒炒肉")

    def test_cached_videos_are_copied_so_callers_cannot_corrupt_the_store(self) -> None:
        state = _state()
        state._home_cache = [_video("b1")]
        state._store_home_state("bilibili")

        videos, _page, _has_next = state._take_home_state("bilibili")
        videos.append(_video("injected"))

        again, _page, _has_next = state._take_home_state("bilibili")
        self.assertEqual([v.video_id for v in again], ["b1"])


class _ProbeSignals(QObject):
    success = Signal(object, bool)
    finished = Signal()


class _ProbeWorker(QRunnable):
    """模拟命中缓存的 HomeWorker：几乎立刻返回，随后被 GC 盯上。"""

    def __init__(self) -> None:
        super().__init__()
        self.signals = _ProbeSignals()

    @Slot()
    def run(self) -> None:
        self.signals.success.emit([_video("cached")], True)
        self.signals.finished.emit()


class WorkerLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_started_workers_survive_until_finished_is_delivered(self) -> None:
        delivered: list[int] = []
        owner = SimpleNamespace(
            _active_workers={},
            _worker_sequence=0,
            thread_pool=QThreadPool.globalInstance(),
        )
        owner._start_worker = MethodType(MainWindow._start_worker, owner)
        owner._release_worker = MethodType(MainWindow._release_worker, owner)

        loop = QEventLoop()

        def start_batch() -> None:
            for _ in range(50):
                worker = _ProbeWorker()
                worker.signals.success.connect(lambda _videos, _has_next: delivered.append(1))
                owner._start_worker(worker)
                # worker 这个局部名立刻失效，只剩 _start_worker 里登记的引用。
            gc.collect()
            QTimer.singleShot(800, loop.quit)

        QTimer.singleShot(0, start_batch)
        loop.exec()
        self.assertTrue(owner.thread_pool.waitForDone(3000))
        self.app.processEvents()

        # 修复前这里会大面积丢信号（甚至直接崩溃），修复后必须一条不落。
        self.assertEqual(len(delivered), 50)
        # finished 到达后引用要及时释放，不能越积越多。
        self.assertEqual(owner._active_workers, {})


if __name__ == "__main__":
    unittest.main()
