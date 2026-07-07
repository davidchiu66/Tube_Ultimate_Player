from __future__ import annotations

import logging

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app_paths import APP_NAME
from database.favorite_repository import FavoriteRepository
from database.history_repository import HistoryRepository
from database.sqlite_manager import SQLiteManager
from download.download_manager import DownloadManager
from player.mpv_player import MpvPlayer
from resolver.models import HomeVideo, VideoInfo, VideoQuality
from resolver.youtube_resolver import YoutubeResolver
from services.config_service import ConfigService
from ui.download_page import DownloadPage
from ui.favorite_page import FavoritePage
from ui.history_page import HistoryPage
from ui.home_page import HomePage
from ui.player_page import PlayerPage
from ui.settings_page import SettingsPage
from ui.toast import Toast
from ui.url_dialog import UrlPlayDialog
from workers.home_worker import HomeWorker
from workers.resolver_worker import ResolverWorker
from workers.search_worker import SearchWorker


logger = logging.getLogger("tube_player.ui")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        logger.info("main window initializing")
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)

        self.config = ConfigService()
        self.db = SQLiteManager()
        self.history = HistoryRepository(self.db)
        self.favorites = FavoriteRepository(self.db)
        self.resolver = YoutubeResolver(self.config)
        self.thread_pool = QThreadPool.globalInstance()
        self.download_manager = DownloadManager(self.config, self.thread_pool)

        self.current_video: VideoInfo | None = None
        self.current_quality_label = ""
        self._home_cache: list[HomeVideo] = []
        self._home_page = 1
        self._home_has_next = False
        self._search_keyword = ""
        self._search_page = 1

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("搜索 YouTube 视频")
        self.search_button = QPushButton("搜索")
        self.play_url_button = QPushButton("播放URL")
        self.home_nav = QPushButton("首页")
        self.player_nav = QPushButton("播放器")
        self.download_nav = QPushButton("下载列表")
        self.favorite_nav = QPushButton("收藏")
        self.history_nav = QPushButton("历史")
        self.settings_nav = QPushButton("设置")

        self.top_bar_widget = QWidget()
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(12, 12, 12, 0)
        top_bar.setSpacing(8)
        top_bar.addWidget(self.url_edit, 1)
        top_bar.addWidget(self.search_button)
        top_bar.addWidget(self.play_url_button)
        top_bar.addWidget(self.home_nav)
        top_bar.addWidget(self.player_nav)
        top_bar.addWidget(self.download_nav)
        top_bar.addWidget(self.favorite_nav)
        top_bar.addWidget(self.history_nav)
        top_bar.addWidget(self.settings_nav)
        self.top_bar_widget.setLayout(top_bar)

        self.stack = QStackedWidget()
        self.home_page = HomePage()
        self.player_page = PlayerPage()
        self.download_page = DownloadPage()
        self.favorite_page = FavoritePage(self.favorites)
        self.history_page = HistoryPage(self.history)
        self.settings_page = SettingsPage(self.config)

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.player_page)
        self.stack.addWidget(self.download_page)
        self.stack.addWidget(self.favorite_page)
        self.stack.addWidget(self.history_page)
        self.stack.addWidget(self.settings_page)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.top_bar_widget)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.toast = Toast(self)

        self.mpv = MpvPlayer(self.player_page.video_widget, self.config)
        self._connect_signals()
        self._restore_download_tasks()
        self.player_page.set_volume(int(self.config.get("player.volume", 80)))
        self.player_page.set_speed(float(self.config.get("player.speed", 1.0)))
        self.stack.setCurrentWidget(self.home_page)
        self._refresh_favorite_views()
        QTimer.singleShot(0, self.load_home)
        logger.info("main window initialized")

    def _connect_signals(self) -> None:
        self.search_button.clicked.connect(self.search_videos)
        self.play_url_button.clicked.connect(self._show_play_url_dialog)
        self.url_edit.returnPressed.connect(self.search_videos)
        self.home_nav.clicked.connect(self._show_home)
        self.player_nav.clicked.connect(lambda: self.stack.setCurrentWidget(self.player_page))
        self.download_nav.clicked.connect(lambda: self.stack.setCurrentWidget(self.download_page))
        self.favorite_nav.clicked.connect(self._show_favorites)
        self.history_nav.clicked.connect(self._show_history)
        self.settings_nav.clicked.connect(lambda: self.stack.setCurrentWidget(self.settings_page))

        self.home_page.refresh_requested.connect(self._refresh_home_page)
        self.home_page.play_requested.connect(self.play_url)
        self.home_page.favorite_requested.connect(self._favorite_home_video)
        self.home_page.page_requested.connect(self._load_page)
        self.favorite_page.play_requested.connect(self.play_url)
        self.favorite_page.remove_requested.connect(self._remove_favorite)
        self.history_page.play_requested.connect(self.play_url)
        self.settings_page.settings_saved.connect(self._settings_saved)

        self.player_page.play_pause_requested.connect(self.mpv.toggle_pause)
        self.player_page.stop_requested.connect(self._stop_playback)
        self.player_page.seek_requested.connect(self.mpv.seek)
        self.player_page.volume_changed.connect(self._set_volume)
        self.player_page.speed_changed.connect(self._set_speed)
        self.player_page.quality_changed.connect(self._change_quality)
        self.player_page.subtitle_changed.connect(self._change_subtitle)
        self.player_page.fullscreen_requested.connect(self._toggle_fullscreen)
        self.player_page.download_requested.connect(self._download_current_video)
        self.player_page.favorite_requested.connect(self._favorite_current_video)

        self.download_page.pause_requested.connect(self.download_manager.pause_task)
        self.download_page.start_requested.connect(self.download_manager.start_task)
        self.download_page.delete_requested.connect(self.download_manager.delete_task)
        self.download_page.play_file_requested.connect(self.play_local_file)
        self.download_manager.task_added.connect(self.download_page.add_task)
        self.download_manager.task_changed.connect(self.download_page.update_task)
        self.download_manager.task_removed.connect(self.download_page.remove_task)
        self.download_manager.message.connect(self.toast.show_message)

        self.mpv.position_changed.connect(self.player_page.update_position)
        self.mpv.duration_changed.connect(self.player_page.update_duration)
        self.mpv.pause_changed.connect(self.player_page.set_paused)

    def _restore_download_tasks(self) -> None:
        for task in self.download_manager.tasks():
            self.download_page.add_task(task)

    def _show_play_url_dialog(self) -> None:
        dialog = UrlPlayDialog(self)
        if dialog.exec():
            self.play_url(dialog.url())

    def play_url(self, url: str | None = None) -> None:
        target = (url or "").strip()
        if not target:
            QMessageBox.information(self, "提示", "请输入 YouTube URL")
            return
        if not target.startswith(("http://", "https://")):
            QMessageBox.warning(self, "URL 无效", "请输入完整的 http:// 或 https:// 地址")
            return

        logger.info("play url requested: %s", target)
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.set_loading(True, "正在解析视频地址，请稍等...")

        worker = ResolverWorker(target, self.resolver)
        worker.signals.success.connect(self._resolved)
        worker.signals.error.connect(self._resolve_failed)
        worker.signals.finished.connect(lambda: self.player_page.set_loading(False))
        self.thread_pool.start(worker)

    def load_home(self) -> None:
        self._start_home_load(1)

    def _start_home_load(self, page: int) -> None:
        logger.info("home load requested page=%s", page)
        self._home_page = max(1, page)
        self.stack.setCurrentWidget(self.home_page)
        self.home_page.set_home_context(self._home_page, False)
        self.home_page.set_loading(True, f"正在获取 YouTube 首页推荐（第 {self._home_page} 页），请稍等...")
        worker = HomeWorker(self.resolver, page=self._home_page, page_size=56)
        worker.signals.success.connect(self._home_loaded)
        worker.signals.error.connect(self._home_failed)
        worker.signals.finished.connect(lambda: self.home_page.set_loading(False))
        self.thread_pool.start(worker)

    def search_videos(self) -> None:
        keyword = self.url_edit.text().strip()
        if not keyword:
            QMessageBox.information(self, "提示", "请输入搜索关键词")
            return
        self._search_keyword = keyword
        self._search_page = 1
        self._start_search(keyword, 1)

    def _load_page(self, page: int) -> None:
        if self.home_page.mode() == "home":
            self._start_home_load(page)
            return
        if not self._search_keyword:
            return
        self._search_page = max(1, page)
        self._start_search(self._search_keyword, self._search_page)

    def _start_search(self, keyword: str, page: int) -> None:
        logger.info("search requested keyword=%s page=%s", keyword, page)
        self.stack.setCurrentWidget(self.home_page)
        self.home_page.set_search_context(keyword, page, has_next=False)
        self.home_page.set_loading(
            True,
            f"正在搜索 YouTube：{keyword}（第 {page} 页），请稍等，这一步通常会比首页加载更慢一些...",
        )
        worker = SearchWorker(self.resolver, keyword, page=page, page_size=45)
        worker.signals.success.connect(self._search_loaded)
        worker.signals.error.connect(self._search_failed)
        worker.signals.finished.connect(lambda: self.home_page.set_loading(False))
        self.thread_pool.start(worker)

    def _refresh_home_page(self) -> None:
        if self.home_page.mode() == "search" and self._search_keyword:
            self._start_search(self._search_keyword, self.home_page.page())
            return
        self._start_home_load(self.home_page.page())

    def _home_loaded(self, videos: list[HomeVideo], has_next: bool) -> None:
        logger.info("home loaded page=%s count=%s has_next=%s", self._home_page, len(videos), has_next)
        self._home_cache = list(videos)
        self._home_has_next = has_next
        self.home_page.set_videos(videos, mode="home", page=self._home_page, has_next=has_next)
        self.home_page.set_favorite_ids(self.favorites.favorite_ids())

    def _home_failed(self, message: str) -> None:
        logger.error("home load failed: %s", message)
        self.home_page.set_error(message)

    def _search_loaded(self, videos: list[HomeVideo], has_next: bool) -> None:
        logger.info(
            "search loaded keyword=%s page=%s count=%s has_next=%s",
            self._search_keyword,
            self._search_page,
            len(videos),
            has_next,
        )
        self.home_page.set_videos(
            videos,
            mode="search",
            keyword=self._search_keyword,
            page=self._search_page,
            has_next=has_next,
        )
        self.home_page.set_favorite_ids(self.favorites.favorite_ids())

    def _search_failed(self, message: str) -> None:
        logger.error("search failed keyword=%s page=%s: %s", self._search_keyword, self._search_page, message)
        self.home_page.set_error(message)

    def _resolved(self, video: VideoInfo) -> None:
        self.current_video = video
        quality = self._select_default_quality(video)
        self.current_quality_label = quality.label
        logger.info(
            "video resolved id=%s title=%s selected_quality=%s qualities=%s subtitles=%s",
            video.video_id,
            video.title,
            quality.label,
            list(video.qualities.keys()),
            len(video.subtitles),
        )
        self.player_page.update_video_info(video, quality.label)
        self.player_page.set_favorite_state(self.favorites.is_favorite(video.video_id), available=True)

        try:
            self.mpv.load(quality.video_url, quality.audio_url, headers=video.http_headers)
            self.player_page.set_loading(False)
            self.player_page.set_playback_available(True)
            self.player_page.set_paused(False)
            self.history.record_play(video)
            self.history_page.refresh()
        except Exception as exc:
            logger.exception("playback load failed")
            QMessageBox.critical(self, "播放失败", str(exc))

    def _resolve_failed(self, message: str) -> None:
        logger.error("resolve failed: %s", message)
        QMessageBox.critical(
            self,
            "解析失败",
            "无法解析该视频。\n\n"
            "可能原因：视频不可用、网络连接失败、yt-dlp 版本过旧、地区或年龄限制、代理或 Cookie 设置错误。\n"
            "Cookie 可使用 Netscape cookies.txt；如果是浏览器请求头里的 Cookie 串，程序会自动转换。\n"
            "详细日志已写入运行目录下的 logs/app.log 和 logs/yt-dlp.log。\n\n"
            f"{message}",
        )

    def _select_default_quality(self, video: VideoInfo) -> VideoQuality:
        preferred = str(self.config.get("player.default_quality", "Auto") or "Auto")
        if preferred != "Auto" and preferred in video.qualities:
            return video.qualities[preferred]
        return next(iter(video.qualities.values()))

    def _change_quality(self, label: str) -> None:
        if not self.current_video or label == self.current_quality_label:
            return
        quality = self.current_video.qualities.get(label)
        if not quality:
            return

        position = self.mpv.position()
        try:
            self.mpv.load(
                quality.video_url,
                quality.audio_url,
                start_position=position,
                headers=self.current_video.http_headers,
            )
            self.current_quality_label = label
        except Exception as exc:
            logger.exception("quality switch failed label=%s", label)
            QMessageBox.critical(self, "切换清晰度失败", str(exc))

    def _change_subtitle(self, key: str) -> None:
        if not self.current_video:
            return
        if not key:
            self.mpv.clear_subtitles()
            return
        subtitle = self.current_video.subtitles.get(key)
        if subtitle:
            try:
                self.mpv.add_subtitle(subtitle.url)
            except Exception as exc:
                logger.exception("subtitle load failed key=%s", key)
                QMessageBox.warning(self, "字幕加载失败", str(exc))

    def _set_volume(self, volume: int) -> None:
        self.config.set("player.volume", volume)
        self.mpv.set_volume(volume)

    def _set_speed(self, speed: float) -> None:
        self.config.set("player.speed", speed)
        self.mpv.set_speed(speed)

    def _download_current_video(self) -> None:
        if not self.current_video:
            QMessageBox.information(self, "提示", "当前没有可下载的视频。")
            return
        self.download_manager.enqueue(self.current_video, self.current_quality_label)
        self.stack.setCurrentWidget(self.download_page)

    def _favorite_current_video(self) -> None:
        if not self.current_video:
            return
        created = self.favorites.add_video_info(self.current_video)
        self.player_page.set_favorite_state(True, available=True)
        self._refresh_favorite_views()
        self.toast.show_message("已加入收藏" if created else "该视频已在收藏中，已刷新信息")

    def _favorite_home_video(self, video: HomeVideo) -> None:
        created = self.favorites.add_home_video(video)
        self._refresh_favorite_views()
        self.toast.show_message("已加入收藏" if created else "该视频已在收藏中，已刷新信息")

    def _remove_favorite(self, video_id: str) -> None:
        self.favorites.remove(video_id)
        self._refresh_favorite_views()
        if self.current_video and self.current_video.video_id == video_id:
            self.player_page.set_favorite_state(False, available=True)
        self.toast.show_message("已从收藏中移除")

    def _refresh_favorite_views(self) -> None:
        favorite_ids = self.favorites.favorite_ids()
        self.home_page.set_favorite_ids(favorite_ids)
        self.favorite_page.refresh()
        if self.current_video:
            self.player_page.set_favorite_state(self.current_video.video_id in favorite_ids, available=True)

    def play_local_file(self, path: str) -> None:
        logger.info("play local file requested: %s", path)
        self.current_video = None
        self.current_quality_label = ""
        self.stack.setCurrentWidget(self.player_page)
        try:
            self.mpv.load(path)
            self.player_page.update_local_file_info(path)
            self.player_page.set_playback_available(True)
            self.player_page.set_paused(False)
            self.player_page.set_download_available(False)
        except Exception as exc:
            logger.exception("local playback load failed path=%s", path)
            QMessageBox.critical(self, "播放失败", str(exc))

    def _stop_playback(self) -> None:
        logger.info("stop playback requested")
        self.mpv.stop()
        self.player_page.set_playback_available(False)
        if self.isFullScreen():
            self.showNormal()
            self.top_bar_widget.show()
            self.player_page.set_fullscreen(False)
        self._show_home()

    def _settings_saved(self) -> None:
        logger.info("settings saved")
        self.mpv.apply_network_options()
        self.resolver = YoutubeResolver(self.config)
        self.download_manager.reload_settings()

    def _show_home(self) -> None:
        self.stack.setCurrentWidget(self.home_page)
        if self._home_cache:
            self.home_page.set_videos(
                self._home_cache,
                mode="home",
                page=self._home_page,
                has_next=self._home_has_next,
            )
            self.home_page.set_favorite_ids(self.favorites.favorite_ids())
        else:
            self.load_home()

    def _show_favorites(self) -> None:
        self.favorite_page.refresh()
        self.stack.setCurrentWidget(self.favorite_page)

    def _show_history(self) -> None:
        self.history_page.refresh()
        self.stack.setCurrentWidget(self.history_page)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            logger.info("leaving fullscreen")
            self.showNormal()
            self.top_bar_widget.show()
            self.player_page.set_fullscreen(False)
        else:
            logger.info("entering fullscreen")
            self.stack.setCurrentWidget(self.player_page)
            self.top_bar_widget.hide()
            self.showFullScreen()
            self.player_page.set_fullscreen(True)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            logger.info("main window closing")
            self.config.save()
            self.mpv.shutdown()
        finally:
            super().closeEvent(event)
