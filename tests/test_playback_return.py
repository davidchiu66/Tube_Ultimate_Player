from __future__ import annotations

import unittest
from types import SimpleNamespace

from ui.main_window import MainWindow


class PlaybackReturnTests(unittest.TestCase):
    def test_playlist_switch_keeps_original_home_source(self) -> None:
        home = object()
        player = object()
        playlist = object()
        state = SimpleNamespace(
            _playback_return_widget=home,
            player_page=player,
            playlist_page=playlist,
            stack=SimpleNamespace(currentWidget=lambda: player),
        )

        MainWindow._remember_playback_return_widget(state)
        state.stack = SimpleNamespace(currentWidget=lambda: playlist)
        MainWindow._remember_playback_return_widget(state)

        self.assertIs(state._playback_return_widget, home)

    def test_new_source_page_replaces_previous_return_target(self) -> None:
        home = object()
        favorite = object()
        player = object()
        playlist = object()
        state = SimpleNamespace(
            _playback_return_widget=home,
            player_page=player,
            playlist_page=playlist,
            stack=SimpleNamespace(currentWidget=lambda: favorite),
        )

        MainWindow._remember_playback_return_widget(state)

        self.assertIs(state._playback_return_widget, favorite)


if __name__ == "__main__":
    unittest.main()
