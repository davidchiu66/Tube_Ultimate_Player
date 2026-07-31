"""D1 验证：投屏期间面板位置/时长只由 DLNA 轮询驱动。

投屏时本地 mpv 只是被 pause，属性轮询定时器仍在跑并持续上报被冻结的本地位置。
若两条链路都写 player_page.update_position，进度条就会在「投屏起始点」与
「远端真实位置」之间反复跳 —— 本模块把守卫行为固定成基线。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ui.main_window import MainWindow


class FakePanel:
    """只记录调用的假面板。"""

    def __init__(self) -> None:
        self.positions: list[float] = []
        self.durations: list[float] = []

    def update_position(self, seconds: float) -> None:
        self.positions.append(seconds)

    def update_duration(self, seconds: float) -> None:
        self.durations.append(seconds)


def make_state(*, device=None, pending: bool = False, offset: float = 0.0):
    state = SimpleNamespace(
        _dlna_device=device,
        _dlna_cast_pending=pending,
        _dlna_position_offset=offset,
        _dlna_last_position=0.0,
        _dlna_stop_notify_requests=set(),
        _shutting_down=False,
        player_page=FakePanel(),
    )
    state._casting_to_dlna = lambda: MainWindow._casting_to_dlna(state)
    return state


class LocalPositionGuardTests(unittest.TestCase):
    def test_position_passes_through_when_not_casting(self) -> None:
        state = make_state()

        MainWindow._handle_mpv_position_changed(state, 12.5)

        self.assertEqual(state.player_page.positions, [12.5])

    def test_position_dropped_while_casting(self) -> None:
        state = make_state(device=object())

        MainWindow._handle_mpv_position_changed(state, 12.5)

        self.assertEqual(state.player_page.positions, [])

    def test_position_dropped_while_cast_pending(self) -> None:
        # 「正在连接投屏设备」的窗口期同样不能让本地位置写进面板。
        state = make_state(pending=True)

        MainWindow._handle_mpv_position_changed(state, 12.5)

        self.assertEqual(state.player_page.positions, [])

    def test_duration_follows_the_same_guard(self) -> None:
        idle = make_state()
        casting = make_state(device=object())

        MainWindow._handle_mpv_duration_changed(idle, 600.0)
        MainWindow._handle_mpv_duration_changed(casting, 600.0)

        self.assertEqual(idle.player_page.durations, [600.0])
        self.assertEqual(casting.player_page.durations, [])


class RemotePositionTests(unittest.TestCase):
    """远端轮询结果是投屏期间面板唯一的位置来源。"""

    def _dispatch(self, state, position: float, duration: float) -> None:
        device = state._dlna_device
        state._dlna_action_workers = {7: (object(), device, "abc")}
        MainWindow._dlna_action_succeeded(state, 7, "get_position", (position, duration))

    def test_offset_is_applied_to_remote_position(self) -> None:
        device = object()
        state = make_state(device=device, offset=30.0)

        self._dispatch(state, 12.0, 0.0)

        self.assertEqual(state.player_page.positions, [42.0])
        self.assertEqual(state._dlna_last_position, 42.0)

    def test_unknown_remote_duration_does_not_overwrite(self) -> None:
        # 实时封装时渲染器常返回 0 / NOT_IMPLEMENTED，总时长必须保留本地解析值。
        device = object()
        state = make_state(device=device, offset=30.0)

        self._dispatch(state, 12.0, 0.0)

        self.assertEqual(state.player_page.durations, [])

    def test_known_remote_duration_is_offset_and_applied(self) -> None:
        device = object()
        state = make_state(device=device, offset=30.0)

        self._dispatch(state, 12.0, 600.0)

        self.assertEqual(state.player_page.durations, [630.0])


if __name__ == "__main__":
    unittest.main()
