"""P2 / P3 验证：首页分批建卡、按需取封面，播放列表只刷新变化的行。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from resolver.models import HomeVideo, PlaylistEntry, PlaylistInfo  # noqa: E402
from ui.home_page import CARD_BATCH_SIZE, HomePage  # noqa: E402
from ui.playlist_overlay import PlaylistItemWidget, PlaylistOverlay  # noqa: E402


TOTAL_VIDEOS = 40


def make_videos(count: int = TOTAL_VIDEOS) -> list[HomeVideo]:
    return [
        HomeVideo(
            video_id=f"vid{index}",
            title=f"视频 {index}",
            webpage_url=f"https://example.invalid/watch?v=vid{index}",
            uploader="频道",
            duration=100 + index,
            thumbnail=f"https://example.invalid/thumb{index}.jpg",
        )
        for index in range(count)
    ]


def make_playlist(count: int = 6) -> PlaylistInfo:
    entries = [
        PlaylistEntry(
            playlist_id="pl",
            video_id=f"vid{index}",
            title=f"条目 {index}",
            webpage_url=f"https://example.invalid/watch?v=vid{index}",
            uploader="频道",
            duration=200 + index,
            thumbnail=f"https://example.invalid/thumb{index}.jpg",
            position=index,
        )
        for index in range(count)
    ]
    return PlaylistInfo(
        playlist_id="pl",
        title="测试列表",
        webpage_url="https://example.invalid/playlist?list=pl",
        uploader="频道",
        entry_count=count,
        entries=entries,
    )


class _QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])


class HomeBatchBuildTests(_QtTestCase):
    """P2：首屏只建一批卡片，剩余批次交给事件循环。"""

    def setUp(self) -> None:
        self.page = HomePage()
        self.addCleanup(self.page.deleteLater)

    def _drain_batches(self) -> None:
        # 定时器在离屏测试里不会自己跑，直接把剩余批次逐批建完。
        while self.page._pending_videos:
            self.page._build_next_batch()

    def test_first_batch_is_capped(self) -> None:
        self.page.set_videos(make_videos())

        self.assertEqual(self.page.video_count(), CARD_BATCH_SIZE)
        self.assertEqual(len(self.page._pending_videos), TOTAL_VIDEOS - CARD_BATCH_SIZE)

    def test_all_cards_are_built_after_draining(self) -> None:
        videos = make_videos()
        self.page.set_videos(videos)

        self._drain_batches()

        # 观察行为不变：数量与顺序都要和一次性构建一致。
        self.assertEqual(self.page.video_count(), TOTAL_VIDEOS)
        self.assertEqual([card.video.video_id for card in self.page._cards],
                         [video.video_id for video in videos])

    def test_first_card_is_selected_only_once(self) -> None:
        self.page.set_videos(make_videos())
        first = self.page._cards[0]

        self._drain_batches()

        # 后续批次不得把选中项抢走。
        self.assertIs(self.page._selected_card, first)

    def test_thumbnails_are_not_requested_up_front(self) -> None:
        self.page.set_videos(make_videos())

        self.assertTrue(all(not card._thumbnail_requested for card in self.page._cards))
        self.assertEqual(self.page._thumbnail_cache.pending_count(), 0)

    def test_reload_discards_pending_batches(self) -> None:
        self.page.set_videos(make_videos())
        self.page.set_videos(make_videos(3))

        self.assertEqual(self.page._pending_videos, [])
        self.assertEqual(self.page.video_count(), 3)


class PlaylistRestyleCostTests(_QtTestCase):
    """P3：切换活动行/选中行时只重绘真正变化的行。"""

    def setUp(self) -> None:
        self.overlay = PlaylistOverlay()
        self.addCleanup(self.overlay.deleteLater)
        self.overlay.set_playlist(make_playlist(), current_index=0)
        self.restyles: list[int] = []
        original = PlaylistItemWidget._refresh_style

        def counting_refresh(widget: PlaylistItemWidget) -> None:
            self.restyles.append(widget.index)
            original(widget)

        patcher = mock.patch.object(PlaylistItemWidget, "_refresh_style", counting_refresh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _row(self, row: int) -> PlaylistItemWidget:
        widget = self.overlay._row_widget(row)
        self.assertIsNotNone(widget)
        return widget

    def test_moving_active_row_restyles_only_two_rows(self) -> None:
        self.overlay.set_current_index(3)

        # 旧活动行取消 + 新活动行点亮，最多再加上选中态的两行变化。
        self.assertLessEqual(len(set(self.restyles)), 4)
        self.assertNotIn(5, self.restyles)
        self.assertTrue(self._row(3).property("active"))
        self.assertFalse(self._row(0).property("active"))

    def test_repeating_the_same_index_does_not_restyle(self) -> None:
        self.overlay.set_current_index(0)
        self.restyles.clear()

        self.overlay.set_current_index(0)

        self.assertEqual(self.restyles, [])

    def test_selection_change_only_touches_changed_rows(self) -> None:
        self.overlay.list_widget.item(2).setSelected(True)
        self.restyles.clear()

        self.overlay.list_widget.item(4).setSelected(True)

        self.assertEqual(set(self.restyles), {4})
        self.assertTrue(self._row(4).property("selected"))
        self.assertTrue(self._row(2).property("selected"))

    def test_final_state_matches_a_full_scan(self) -> None:
        self.overlay.set_current_index(2)
        self.overlay.list_widget.item(4).setSelected(True)

        selected = {index.row() for index in self.overlay.list_widget.selectedIndexes()}
        for row in range(self.overlay.list_widget.count()):
            widget = self._row(row)
            self.assertEqual(bool(widget.property("selected")), row in selected)
            self.assertEqual(bool(widget.property("active")), row == 2)

    def test_rebuild_resets_cached_row_state(self) -> None:
        self.overlay.set_current_index(4)

        self.overlay.set_playlist(make_playlist(3), current_index=1)

        self.assertEqual(self.overlay._active_row, 1)
        self.assertTrue(self._row(1).property("active"))
        self.assertFalse(self._row(0).property("active"))


if __name__ == "__main__":
    unittest.main()
