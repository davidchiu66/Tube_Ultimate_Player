from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QThreadPool, QTimer, QUrl, Qt, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app_paths import APP_NAME, UPDATE_DIR, asset_path
from database.favorite_repository import FavoriteRepository
from database.history_repository import HistoryRepository
from database.playlist_repository import PlaylistRepository
from database.sqlite_manager import SQLiteManager
from dlna.controller import DlnaController, build_didl_lite
from dlna.media_server import DlnaMediaServer, DlnaMediaSource, mime_type_for_extension, mime_type_for_file
from dlna.models import DlnaDevice
from download.download_manager import DownloadManager
from player.mpv_player import MpvPlayer
from platform_support import is_root_user
from resolver.models import HomeVideo, PlaylistEntry, PlaylistInfo, SavedPlaylist, VideoInfo, VideoQuality
from resolver.site_resolver import SiteResolver
from services.config_service import ConfigService
from services.ffmpeg_install_service import FfmpegInstallInfo, FfmpegInstallService
from services.runtime_install_service import RuntimeInstallService
from services.update_service import REPO_URL, UpdateCheckResult, UpdateService
from ui.about_page import AboutPage
from ui.cast_dialog import DlnaCastDialog
from ui.download_page import DownloadPage
from ui.favorite_page import FavoritePage
from ui.history_page import HistoryPage
from ui.home_page import HomePage
from ui.player_page import PlayerPage
from ui.playlist_page import PlaylistPage
from ui.settings_page import SettingsPage
from ui.toolbar import PlayerToolbar
from ui.toast import Toast
from ui.url_dialog import UrlPlayDialog
from workers.archive_extract_worker import ArchiveExtractWorker
from workers.creator_videos_worker import CreatorVideosWorker
from workers.dlna_worker import DlnaActionWorker
from workers.home_worker import HomeWorker
from workers.playlist_worker import PlaylistWorker
from workers.resolver_worker import ResolverWorker
from workers.search_worker import SearchWorker
from workers.update_check_worker import UpdateCheckWorker
from workers.update_download_worker import UpdateDownloadWorker


logger = logging.getLogger("tube_player.ui")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        logger.info("main window initializing")
        self.setWindowTitle(APP_NAME)
        self._resize_for_available_screen()
        self._apply_window_icon()

        self.config = ConfigService()
        self.db = SQLiteManager()
        self.history = HistoryRepository(self.db)
        self.favorites = FavoriteRepository(self.db)
        self.playlists = PlaylistRepository(self.db)
        self.resolver = SiteResolver(self.config)
        self.update_service = UpdateService(self.config)
        self.runtime_install_service = RuntimeInstallService(self.config)
        self.ffmpeg_install_service = FfmpegInstallService(self.config)
        self.dlna_controller = DlnaController()
        self.dlna_media_server = DlnaMediaServer()
        self.thread_pool = QThreadPool.globalInstance()
        self.download_manager = DownloadManager(self.config, self.thread_pool)

        self.current_video: VideoInfo | None = None
        self.current_local_media_path = ""
        self.current_quality_label = ""
        self.current_playlist: PlaylistInfo | None = None
        self.current_playlist_index = -1
        self.current_playlist_key = ""
        self.current_playlist_auto_play = True
        self._pending_playlist_video_id = ""
        self._playback_return_widget: QWidget | None = None
        self._playback_finished = False
        self._was_maximized_before_fullscreen: bool | None = None
        self._creator_playlist_generation = 0
        self._creator_playlist_workers: dict[tuple[int, str], CreatorVideosWorker] = {}
        self._dlna_device: DlnaDevice | None = None
        self._dlna_device_cache: dict[str, DlnaDevice] = {}
        self._dlna_remote_paused = False
        self._dlna_cast_pending = False
        self._dlna_pending_cast_request_id = 0
        self._dlna_action_sequence = 0
        self._dlna_action_workers: dict[int, tuple[DlnaActionWorker, DlnaDevice, str]] = {}
        self._dlna_stop_notify_requests: set[int] = set()
        self._dlna_position_request_id = 0
        self._dlna_last_position = 0.0
        self._dlna_position_offset = 0.0
        self._dlna_pending_position_offset = 0.0
        self._dlna_seek_supported = True
        self._dlna_pending_seek_supported = True
        self._dlna_pending_volume = int(self.config.get("player.volume", 80))
        self._dlna_volume_timer = QTimer(self)
        self._dlna_volume_timer.setSingleShot(True)
        self._dlna_volume_timer.setInterval(180)
        self._dlna_volume_timer.timeout.connect(self._flush_dlna_volume)
        self._dlna_position_timer = QTimer(self)
        self._dlna_position_timer.setInterval(1500)
        self._dlna_position_timer.timeout.connect(self._poll_dlna_position)
        self._home_cache: list[HomeVideo] = []
        self._home_page = 1
        self._home_has_next = False
        self._search_keyword = ""
        self._search_page = 1
        self._last_update_result: UpdateCheckResult | None = None
        self._pending_node_installer_path = ""
        self._pending_ffmpeg_info: FfmpegInstallInfo | None = None
        self._ffmpeg_progress_dialog: QProgressDialog | None = None

        self.top_bar_widget = PlayerToolbar(self)
        self.url_edit = self.top_bar_widget.search_edit
        self.search_button = self.top_bar_widget.search_button
        self.play_url_button = self.top_bar_widget.url_button
        self.home_nav = self.top_bar_widget.home_button
        self.player_nav = self.top_bar_widget.player_button
        self.download_nav = self.top_bar_widget.download_button
        self.favorite_nav = self.top_bar_widget.favorite_button
        self.history_nav = self.top_bar_widget.history_button
        self.settings_nav = self.top_bar_widget.settings_button
        self.about_nav = self.top_bar_widget.about_button
        self.topmost_nav = self.top_bar_widget.topmost_button
        self._is_topmost = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

        self.stack = QStackedWidget()
        self.home_page = HomePage()
        self.player_page = PlayerPage(self.config)
        self.playlist_page = PlaylistPage()
        self.download_page = DownloadPage()
        self.favorite_page = FavoritePage(self.favorites)
        self.history_page = HistoryPage(self.history)
        self.settings_page = SettingsPage(self.config)
        self.about_page = AboutPage()

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.player_page)
        self.stack.addWidget(self.playlist_page)
        self.stack.addWidget(self.download_page)
        self.stack.addWidget(self.favorite_page)
        self.stack.addWidget(self.history_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.about_page)

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
        self._refresh_saved_playlists()
        self.player_page.set_volume(int(self.config.get("player.volume", 80)))
        self.player_page.set_speed(float(self.config.get("player.speed", 1.0)))
        self._sync_about_page()
        self._refresh_runtime_status()
        self.stack.setCurrentWidget(self.home_page)
        self._refresh_favorite_views()
        QTimer.singleShot(0, self.load_home)
        QTimer.singleShot(1200, self._maybe_prompt_ffmpeg_install)
        if is_root_user():
            QTimer.singleShot(0, self._show_root_session_warning)
        self.top_bar_widget.set_topmost_state(self._is_topmost)
        logger.info("main window initialized")

    def _show_root_session_warning(self) -> None:
        QMessageBox.warning(
            self,
            "不建议以 root 运行",
            "应用允许以 root 身份启动，但当前进程将使用 /root 下的独立配置和数据。\n\n"
            "浏览器 Cookie、桌面密钥环、音频会话和 X11/XWayland 显示授权可能不可用，"
            "下载文件也会归 root 所有。建议退出后使用当前桌面普通用户运行。",
        )

    def _connect_signals(self) -> None:
        self.top_bar_widget.search_requested.connect(self._toolbar_search_requested)
        self.play_url_button.clicked.connect(self._show_play_url_dialog)
        self.home_nav.clicked.connect(self._show_home)
        self.player_nav.clicked.connect(lambda: self.stack.setCurrentWidget(self.player_page))
        self.download_nav.clicked.connect(lambda: self.stack.setCurrentWidget(self.download_page))
        self.favorite_nav.clicked.connect(self._show_favorites)
        self.history_nav.clicked.connect(self._show_history)
        self.settings_nav.clicked.connect(lambda: self.stack.setCurrentWidget(self.settings_page))
        self.about_nav.clicked.connect(self._show_about)
        self.top_bar_widget.topmost_clicked.connect(self._toggle_topmost)

        self.home_page.refresh_requested.connect(self._refresh_home_page)
        self.home_page.play_requested.connect(self.play_url)
        self.home_page.favorite_requested.connect(self._favorite_home_video)
        self.home_page.download_requested.connect(self._download_home_video)
        self.home_page.page_requested.connect(self._load_page)
        self.playlist_page.back_requested.connect(self._show_player_page)
        self.playlist_page.play_entry_requested.connect(self._play_playlist_from_page)
        self.playlist_page.download_entries_requested.connect(self._download_playlist_entries)
        self.playlist_page.save_requested.connect(self._save_active_playlist)
        self.playlist_page.auto_play_changed.connect(self._set_playlist_auto_play)
        self.favorite_page.play_requested.connect(self.play_url)
        self.favorite_page.remove_requested.connect(self._remove_favorite)
        self.history_page.play_requested.connect(self.play_url)
        self.settings_page.settings_saved.connect(self._settings_saved)
        self.settings_page.install_node_requested.connect(self._install_node_runtime)
        self.settings_page.open_node_site_requested.connect(self.runtime_install_service.open_official_site)

        self.about_page.open_repo_requested.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        self.about_page.open_update_folder_requested.connect(self._open_update_folder)
        self.about_page.check_update_requested.connect(self._check_updates)
        self.about_page.upgrade_requested.connect(self._start_upgrade_download)

        self.player_page.play_pause_requested.connect(self._toggle_play_pause)
        self.player_page.stop_requested.connect(self._stop_playback)
        self.player_page.seek_requested.connect(self._seek_playback)
        self.player_page.volume_changed.connect(self._set_volume)
        self.player_page.speed_changed.connect(self._set_speed)
        self.player_page.quality_changed.connect(self._change_quality)
        self.player_page.subtitle_changed.connect(self._change_subtitle)
        self.player_page.cast_requested.connect(self._show_cast_dialog)
        self.player_page.browser_play_requested.connect(self._open_current_video_in_browser)
        self.player_page.fullscreen_requested.connect(self._toggle_fullscreen)
        self.player_page.download_requested.connect(self._download_current_video)
        self.player_page.favorite_requested.connect(self._favorite_current_video)
        self.player_page.playlist_entry_requested.connect(self._play_playlist_index)
        self.player_page.playlist_download_requested.connect(self._download_playlist_entries)
        self.player_page.playlist_save_requested.connect(self._save_active_playlist)
        self.player_page.playlist_load_requested.connect(self._load_saved_playlist)
        self.player_page.playlist_delete_requested.connect(self._delete_saved_playlist)
        self.player_page.playlist_auto_play_changed.connect(self._set_playlist_auto_play)

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
        self.mpv.pause_changed.connect(self._handle_mpv_pause_changed)
        self.mpv.playback_finished.connect(self._handle_playback_finished)

    def _apply_window_icon(self) -> None:
        for path in (
            asset_path("icons", "app-icon.ico"),
            asset_path("icons", "app-icon-256.png"),
            asset_path("icons", "app-icon.png"),
        ):
            if path.exists():
                self.setWindowIcon(QIcon(str(path)))
                return

    def _resize_for_available_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1180, 760)
            return
        available = screen.availableGeometry()
        width = min(1180, max(900, available.width() - 80))
        height = min(760, max(560, available.height() - 80))
        self.resize(width, height)

    def _restore_download_tasks(self) -> None:
        for task in self.download_manager.tasks():
            self.download_page.add_task(task)

    def _sync_about_page(self) -> None:
        current_version = self.update_service.local_version()
        _mode, mode_label = self.update_service.detect_install_mode()
        self.about_page.set_current_version(current_version)
        self.about_page.set_install_mode(mode_label)
        self.about_page.set_latest_version("-")
        self.about_page.set_release_notes("")
        self.about_page.set_status("可在这里检测新版本并查看发布说明。")
        self.about_page.set_upgrade_available(False)
        self.about_page.set_upgrade_progress(False, "")

    def _refresh_runtime_status(self) -> None:
        status = self.runtime_install_service.detect_runtime_status()
        self.settings_page.set_runtime_status(status)

    def _show_play_url_dialog(self) -> None:
        dialog = UrlPlayDialog(self)
        if dialog.exec():
            self.play_url(dialog.url())

    def _toolbar_search_requested(self, text: str) -> None:
        self.url_edit.setText(text)
        self.search_videos()

    def play_url(self, url: str | None = None) -> None:
        target = (url or "").strip()
        if not target:
            QMessageBox.information(self, "提示", "请输入要播放的视频 URL")
            return
        if not target.startswith(("http://", "https://")):
            QMessageBox.warning(self, "URL 无效", "请输入完整的 http:// 或 https:// 地址")
            return

        if self._dlna_device is not None or self._dlna_cast_pending:
            self._stop_dlna_cast(resume_local=False, notify=False)

        kind = self.resolver.detect_url_kind(target)
        if kind in ("playlist", "video_with_playlist"):
            self._load_playlist(target, auto_play_current=(kind == "video_with_playlist"))
            return

        self._remember_playback_return_widget()
        self._clear_playlist_context()
        self.current_local_media_path = ""
        logger.info("play url requested: %s", target)
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.set_loading(True, "正在解析视频地址，请稍候...")

        worker = ResolverWorker(target, self.resolver)
        worker.signals.success.connect(self._resolved)
        worker.signals.error.connect(self._resolve_failed)
        worker.signals.finished.connect(lambda: self.player_page.set_loading(False))
        self.thread_pool.start(worker)

    def _load_playlist(self, url: str, auto_play_current: bool = False) -> None:
        self._invalidate_creator_playlist_request()
        self._remember_playback_return_widget()
        logger.info("playlist load requested url=%s auto_play_current=%s", url, auto_play_current)
        self._pending_playlist_video_id = ""
        if auto_play_current:
            self._pending_playlist_video_id = str((parse_qs(urlparse(url).query).get("v") or [""])[0]).strip()
        self.stack.setCurrentWidget(self.playlist_page)
        self.playlist_page.set_loading(
            True,
            f"正在获取 {self._site_label_for_url(url)} 播放列表内容，请稍候...",
        )
        worker = PlaylistWorker(self.resolver, url)
        worker.signals.success.connect(self._playlist_loaded)
        worker.signals.error.connect(self._playlist_failed)
        worker.signals.finished.connect(lambda: self.playlist_page.set_loading(False))
        self.thread_pool.start(worker)

    def load_home(self) -> None:
        self._start_home_load(1)

    def _start_home_load(self, page: int, *, force_refresh: bool = False) -> None:
        logger.info("home load requested page=%s", page)
        self._home_page = max(1, page)
        self.stack.setCurrentWidget(self.home_page)
        source_label = self.resolver.home_source_label()
        self.home_page.set_home_context(self._home_page, False, source_label=source_label)
        self.home_page.set_loading(True, f"正在获取 {source_label} 首页内容（第 {self._home_page} 页），请稍候...")
        worker = HomeWorker(self.resolver, page=self._home_page, page_size=56, force_refresh=force_refresh)
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

    def _start_search(self, keyword: str, page: int, *, force_refresh: bool = False) -> None:
        logger.info("search requested keyword=%s page=%s", keyword, page)
        self.stack.setCurrentWidget(self.home_page)
        self.home_page.set_search_context(keyword, page, has_next=False)
        source_label = self.resolver.home_source_label()
        self.home_page.set_loading(
            True,
            f"正在搜索 {source_label}：{keyword}（第 {page} 页），请稍候，这一步通常会比首页加载稍慢一些...",
        )
        worker = SearchWorker(self.resolver, keyword, page=page, page_size=56, force_refresh=force_refresh)
        worker.signals.success.connect(self._search_loaded)
        worker.signals.error.connect(self._search_failed)
        worker.signals.finished.connect(lambda: self.home_page.set_loading(False))
        self.thread_pool.start(worker)

    @staticmethod
    def _site_label_for_url(url: str) -> str:
        parsed = urlparse(str(url or "").strip())
        host = parsed.netloc.lower()
        if host.endswith("bilibili.com") or host.endswith("b23.tv"):
            return "Bilibili"
        return "YouTube"

    def _refresh_home_page(self) -> None:
        if self.home_page.mode() == "search" and self._search_keyword:
            self._start_search(self._search_keyword, self.home_page.page(), force_refresh=True)
            return
        self._start_home_load(self.home_page.page(), force_refresh=True)

    def _home_loaded(self, videos: list[HomeVideo], has_next: bool) -> None:
        logger.info("home loaded page=%s count=%s has_next=%s", self._home_page, len(videos), has_next)
        self._home_cache = list(videos)
        self._home_has_next = has_next
        self.home_page.set_videos(videos, mode="home", page=self._home_page, has_next=has_next)
        self.home_page.set_home_context(self._home_page, has_next, source_label=self.resolver.home_source_label())
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

    def _playlist_loaded(self, playlist: PlaylistInfo) -> None:
        logger.info("playlist loaded title=%s count=%s", playlist.title, len(playlist.entries))
        if not playlist.entries:
            QMessageBox.information(self, "提示", "该播放列表中没有可用的视频。")
            return

        initial_index = self._find_playlist_index(playlist, self._pending_playlist_video_id)
        self._pending_playlist_video_id = ""
        self._activate_playlist(playlist, current_index=initial_index, playlist_key="")
        self.stack.setCurrentWidget(self.playlist_page)
        if initial_index >= 0:
            self._play_playlist_entry(playlist, initial_index)

    def _playlist_failed(self, message: str) -> None:
        self._pending_playlist_video_id = ""
        logger.error("playlist load failed: %s", message)
        QMessageBox.critical(self, "播放列表解析失败", message)

    def _activate_playlist(
        self,
        playlist: PlaylistInfo,
        *,
        current_index: int = -1,
        playlist_key: str = "",
        auto_play_next: bool = True,
        invalidate_creator_request: bool = True,
    ) -> None:
        if invalidate_creator_request:
            self._invalidate_creator_playlist_request()
        self.current_playlist = playlist
        self.current_playlist_index = current_index
        self.current_playlist_key = playlist_key
        self.current_playlist_auto_play = auto_play_next
        self.playlist_page.set_playlist(playlist, current_index=current_index, auto_play_next=auto_play_next)
        self.player_page.set_playlist_context(playlist, current_index=current_index, auto_play_next=auto_play_next)
        self._refresh_saved_playlists(current_key=playlist_key)

    def _clear_playlist_context(self) -> None:
        self._invalidate_creator_playlist_request()
        self.current_playlist = None
        self.current_playlist_index = -1
        self.current_playlist_key = ""
        self.current_playlist_auto_play = True
        self.player_page.clear_playlist_context()

    def _play_playlist_from_page(self, playlist: PlaylistInfo, index: int) -> None:
        self._activate_playlist(
            playlist,
            current_index=index,
            playlist_key=self.current_playlist_key,
            auto_play_next=self.current_playlist_auto_play,
        )
        self._play_playlist_entry(playlist, index)

    def _play_playlist_index(self, index: int) -> None:
        if self.current_playlist is None:
            return
        self._play_playlist_entry(self.current_playlist, index)

    def _play_playlist_entry(self, playlist: PlaylistInfo, index: int) -> None:
        if not (0 <= index < len(playlist.entries)):
            return
        if self._dlna_device is not None or self._dlna_cast_pending:
            self._stop_dlna_cast(resume_local=False, notify=False)
        entry = playlist.entries[index]
        logger.info("playlist play requested playlist=%s index=%s title=%s", playlist.title, index, entry.title)
        self._remember_playback_return_widget()
        self.current_playlist = playlist
        self.current_playlist_index = index
        self.playlist_page.set_current_index(index)
        self.player_page.set_playlist_current_index(index)
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.set_loading(True, f"正在解析播放列表第 {index + 1} 条视频，请稍候...")

        worker = ResolverWorker(entry.webpage_url, self.resolver)
        worker.signals.success.connect(self._resolved)
        worker.signals.error.connect(self._resolve_failed)
        worker.signals.finished.connect(lambda: self.player_page.set_loading(False))
        self.thread_pool.start(worker)

    def _download_playlist_entries(self, entries: list[PlaylistEntry]) -> None:
        if not entries:
            return
        for entry in entries:
            self._enqueue_download(
                VideoInfo(
                    video_id=entry.video_id,
                    title=entry.title,
                    source_site=entry.source_site,
                    uploader=entry.uploader,
                    duration=entry.duration,
                    webpage_url=entry.webpage_url,
                    thumbnail=entry.thumbnail,
                ),
                "Auto",
            )
        self.toast.show_message(f"已处理 {len(entries)} 条下载任务")

    def _save_active_playlist(self) -> None:
        playlist = self.current_playlist
        if playlist is None or not playlist.entries:
            QMessageBox.information(self, "提示", "当前没有可保存的播放列表。")
            return
        default_name = playlist.title or "我的播放列表"
        name, ok = QInputDialog.getText(self, "保存播放列表", "请输入播放列表名称：", text=default_name)
        if not ok:
            return
        playlist_name = str(name or "").strip()
        if not playlist_name:
            QMessageBox.information(self, "提示", "播放列表名称不能为空。")
            return
        playlist_key = self.playlists.save_playlist(
            name=playlist_name,
            entries=playlist.entries,
            source_url=playlist.webpage_url,
            source_type=playlist.source_type,
            auto_play_next=self.current_playlist_auto_play,
            playlist_key=self.current_playlist_key or None,
        )
        self.current_playlist_key = playlist_key
        self._refresh_saved_playlists(current_key=playlist_key)
        self.toast.show_message(f"已保存播放列表：{playlist_name}")

    def _load_saved_playlist(self, playlist_key: str) -> None:
        saved = self.playlists.get_playlist(playlist_key)
        if saved is None:
            QMessageBox.warning(self, "提示", "没有找到对应的已保存播放列表。")
            self._refresh_saved_playlists()
            return
        playlist = self._saved_to_playlist(saved)
        self._activate_playlist(
            playlist,
            current_index=0 if playlist.entries else -1,
            playlist_key=saved.playlist_key,
            auto_play_next=saved.auto_play_next,
        )
        self.stack.setCurrentWidget(self.playlist_page)

    def _delete_saved_playlist(self, playlist_key: str) -> None:
        saved = self.playlists.get_playlist(playlist_key)
        if saved is None:
            self._refresh_saved_playlists()
            return
        answer = QMessageBox.question(self, "删除播放列表", f"确定删除“{saved.name}”吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.playlists.delete_playlist(playlist_key)
        if self.current_playlist_key == playlist_key:
            self.current_playlist_key = ""
        self._refresh_saved_playlists()
        self.toast.show_message(f"已删除播放列表：{saved.name}")

    def _set_playlist_auto_play(self, enabled: bool) -> None:
        self.current_playlist_auto_play = bool(enabled)
        if self.current_playlist:
            self.playlist_page.set_playlist(
                self.current_playlist,
                current_index=self.current_playlist_index,
                auto_play_next=self.current_playlist_auto_play,
            )
            self.player_page.set_playlist_context(
                self.current_playlist,
                current_index=self.current_playlist_index,
                auto_play_next=self.current_playlist_auto_play,
            )
        if self.current_playlist_key:
            self.playlists.set_auto_play_next(self.current_playlist_key, self.current_playlist_auto_play)
            self._refresh_saved_playlists(current_key=self.current_playlist_key)

    def _refresh_saved_playlists(self, current_key: str = "") -> None:
        playlists = self.playlists.all_playlists()
        selected_key = current_key or self.current_playlist_key
        self.player_page.set_playlist_saved_items(playlists, selected_key)

    def _saved_to_playlist(self, saved: SavedPlaylist) -> PlaylistInfo:
        return PlaylistInfo(
            playlist_id=saved.playlist_key,
            title=saved.name,
            webpage_url=saved.source_url,
            source_site=self._site_label_for_url(saved.source_url).lower(),
            uploader="",
            thumbnail=saved.entries[0].thumbnail if saved.entries else "",
            entry_count=len(saved.entries),
            source_type=saved.source_type,
            entries=list(saved.entries),
        )

    @staticmethod
    def _find_playlist_index(playlist: PlaylistInfo, video_id: str) -> int:
        if not video_id:
            return -1
        for index, entry in enumerate(playlist.entries):
            if entry.video_id == video_id:
                return index
        return -1

    def _resolved(self, video: VideoInfo) -> None:
        self.current_video = video
        self.current_local_media_path = ""
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
        if self.current_playlist:
            self.player_page.set_playlist_context(
                self.current_playlist,
                current_index=self.current_playlist_index,
                auto_play_next=self.current_playlist_auto_play,
            )
            self.playlist_page.set_current_index(self.current_playlist_index)

        try:
            self.mpv.load(quality.video_url, quality.audio_url, headers=video.http_headers)
            self._set_playback_finished(False)
            self.player_page.set_loading(False)
            self.player_page.set_playback_available(True)
            self.player_page.set_cast_available(True)
            self.player_page.set_paused(False)
            self.history.record_play(video)
            self.history_page.refresh()
            if self.current_playlist is None:
                self._schedule_creator_playlist(video)
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

        playback_finished = self._playback_finished
        position = 0.0 if playback_finished else self.mpv.position()
        autoplay = playback_finished or not self.mpv.get_bool("pause")
        try:
            self.mpv.load(
                quality.video_url,
                quality.audio_url,
                start_position=position,
                headers=self.current_video.http_headers,
                autoplay=autoplay,
            )
            self.current_quality_label = label
            self._set_playback_finished(False)
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
            subtitle_ext = str(subtitle.ext or "").strip().lower()
            subtitle_url = str(subtitle.url or "").strip()
            if subtitle_ext == "xml" or urlparse(subtitle_url).path.lower().endswith(".xml"):
                logger.warning(
                    "unsupported XML subtitle ignored key=%s url=%s",
                    key,
                    subtitle_url,
                )
                self.toast.show_message("Bilibili XML 弹幕不是标准字幕，已忽略")
                return
            try:
                logger.info("subtitle load requested key=%s ext=%s", key, subtitle_ext)
                self.mpv.add_subtitle(subtitle_url)
            except Exception as exc:
                logger.exception("subtitle load failed key=%s", key)
                QMessageBox.warning(self, "字幕加载失败", str(exc))

    def _set_volume(self, volume: int) -> None:
        self.config.set("player.volume", volume)
        self.mpv.set_volume(volume)
        if self._dlna_device is not None:
            self._dlna_pending_volume = volume
            self._dlna_volume_timer.start()

    def _set_speed(self, speed: float) -> None:
        self.config.set("player.speed", speed)
        self.mpv.set_speed(speed)

    def _download_current_video(self) -> None:
        if not self.current_video:
            QMessageBox.information(self, "提示", "当前没有可下载的视频。")
            return
        self._enqueue_download(self.current_video, self.current_quality_label)

    def _open_current_video_in_browser(self) -> None:
        url = str(self.current_video.webpage_url or "").strip() if self.current_video else ""
        if not url:
            self.toast.show_message("浏览器播放失败：视频链接不可用")
            return
        if not QDesktopServices.openUrl(QUrl.fromUserInput(url)):
            self.toast.show_message("浏览器播放失败：无法打开系统默认浏览器")

    def _download_home_video(self, video: HomeVideo) -> None:
        if not video.webpage_url:
            self.toast.show_message("下载失败：视频地址不可用")
            return
        self._enqueue_download(
            VideoInfo(
                video_id=video.video_id,
                title=video.title,
                source_site=video.source_site,
                uploader=video.uploader,
                duration=video.duration,
                webpage_url=video.webpage_url,
                thumbnail=video.thumbnail,
            ),
            "Auto",
        )

    def _enqueue_download(self, video: VideoInfo, quality_label: str) -> None:
        try:
            task = self.download_manager.enqueue(video, quality_label)
        except Exception:
            logger.exception("download enqueue failed title=%s", video.title)
            self.toast.show_message(f"下载失败：{video.title or video.webpage_url}")
            return
        if task is None:
            self.toast.show_message(f"下载失败：{video.title or video.webpage_url}")

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
        if self._dlna_device is not None or self._dlna_cast_pending:
            self._stop_dlna_cast(resume_local=False, notify=False)
        self._remember_playback_return_widget()
        self.current_video = None
        self.current_local_media_path = str(Path(path).resolve())
        self.current_quality_label = ""
        self._clear_playlist_context()
        self.stack.setCurrentWidget(self.player_page)
        try:
            self.mpv.load(path)
            self._set_playback_finished(False)
            self.player_page.update_local_file_info(path)
            self.player_page.set_playback_available(True)
            self.player_page.set_cast_available(True)
            self.player_page.set_paused(False)
            self.player_page.set_download_available(False)
        except Exception as exc:
            logger.exception("local playback load failed path=%s", path)
            QMessageBox.critical(self, "播放失败", str(exc))

    def _stop_playback(self) -> None:
        logger.info("stop playback requested")
        if self._dlna_device is not None or self._dlna_cast_pending:
            self._stop_dlna_cast(resume_local=False, notify=False)
        self._invalidate_creator_playlist_request()
        self.mpv.stop()
        self._set_playback_finished(False)
        self.player_page.set_playback_available(False)
        if self.isFullScreen():
            self._leave_player_fullscreen()
        self._return_after_stop()

    def _handle_playback_finished(self) -> None:
        if self.current_playlist is not None and self.current_playlist_auto_play:
            next_index = self.current_playlist_index + 1
            if next_index < len(self.current_playlist.entries):
                logger.info("playlist autoplay next index=%s", next_index)
                self._play_playlist_entry(self.current_playlist, next_index)
                return
        logger.info("playback reached end; waiting for replay")
        self._set_playback_finished(True)

    def _toggle_play_pause(self) -> None:
        try:
            if self._dlna_device is not None:
                action = "play" if self._dlna_remote_paused else "pause"
                self._start_dlna_action(self._dlna_device, action)
                return
            if self._playback_finished:
                logger.info("restart playback requested")
                self.mpv.restart()
                self._set_playback_finished(False)
                self.player_page.update_position(0.0)
                self.player_page.set_paused(False)
                return
            self.mpv.toggle_pause()
        except Exception as exc:
            logger.exception("play/pause command failed")
            QMessageBox.warning(self, "播放控制失败", str(exc))

    def _handle_mpv_pause_changed(self, paused: bool) -> None:
        if self._dlna_device is not None:
            self.player_page.set_paused(self._dlna_remote_paused)
            return
        self.player_page.set_paused(paused)

    def _seek_playback(self, seconds: float) -> None:
        if self._dlna_device is not None:
            if not self._dlna_seek_supported:
                self.toast.show_message("当前实时封装投屏暂不支持拖动进度")
                return
            self._start_dlna_action(self._dlna_device, "seek", seconds)
            return
        self.mpv.seek(seconds)

    def _set_playback_finished(self, finished: bool) -> None:
        self._playback_finished = finished
        self.player_page.set_playback_finished(finished)

    def _show_cast_dialog(self) -> None:
        if self._dlna_device is not None:
            self._stop_dlna_cast(resume_local=True, notify=True)
            return
        if self._dlna_cast_pending:
            self.toast.show_message("正在连接投屏设备，请稍候")
            return
        if self.current_video is None and self.current_local_media_path:
            self._show_local_cast_dialog()
            return
        video = self.current_video
        if video is None:
            self.toast.show_message("当前媒体暂不支持投屏")
            return
        quality = video.qualities.get(self.current_quality_label)
        if quality is None:
            self.toast.show_message("当前清晰度没有可投屏媒体地址")
            return
        ffmpeg_path = ""
        if quality.audio_url:
            ffmpeg_dir = self.ffmpeg_install_service.effective_ffmpeg_dir()
            if ffmpeg_dir:
                executable = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
                candidate = Path(ffmpeg_dir) / executable
                if candidate.is_file():
                    ffmpeg_path = str(candidate)
            if not ffmpeg_path:
                self.toast.show_message("当前视频为分离音视频流，投屏前需要在设置中配置 FFmpeg")
                return

        device = self._select_dlna_device(video.title)
        if device is None:
            return

        position = self.mpv.position()
        _, proxy = self.config.effective_proxy()
        source = DlnaMediaSource(
            title=video.title,
            video_url=quality.video_url,
            audio_url=quality.audio_url,
            headers=dict(video.http_headers),
            mime_type=mime_type_for_extension(quality.ext),
            video_codec=quality.vcodec,
            audio_codec=quality.acodec,
            ffmpeg_path=ffmpeg_path,
            proxy=proxy,
            start_position=position if quality.audio_url else 0.0,
        )
        try:
            media_url = self.dlna_media_server.register_source(
                source,
                device.host,
                preferred_port=self.config.dlna_media_server_port(),
            )
        except Exception as exc:
            logger.exception("DLNA media server preparation failed")
            self.toast.show_message(f"投屏媒体服务启动失败：{exc}")
            return
        metadata = build_didl_lite(video.title, media_url, source.output_mime_type)
        remote_seek = 0.0 if source.requires_mux else position
        self._dlna_cast_pending = True
        self._dlna_pending_cast_request_id = self._dlna_action_sequence + 1
        self._dlna_pending_position_offset = position if source.requires_mux else 0.0
        self._dlna_pending_seek_supported = not source.requires_mux
        request_id = self._start_dlna_action(device, "cast", media_url, metadata, remote_seek, video_id=video.video_id)
        if request_id != self._dlna_pending_cast_request_id:
            logger.warning("DLNA cast request sequence changed expected=%s actual=%s", self._dlna_pending_cast_request_id, request_id)
            self._dlna_pending_cast_request_id = request_id
        self.toast.show_message(f"正在投屏到 {device.friendly_name}...")

    def _show_local_cast_dialog(self) -> None:
        local_path = Path(self.current_local_media_path)
        if not local_path.is_file():
            self.toast.show_message("当前本地媒体文件不可用，无法投屏")
            return

        title = local_path.name
        device = self._select_dlna_device(title)
        if device is None:
            return

        position = self.mpv.position()
        source = DlnaMediaSource(
            title=title,
            video_url="",
            file_path=str(local_path),
            mime_type=mime_type_for_file(local_path),
        )
        try:
            media_url = self.dlna_media_server.register_source(
                source,
                device.host,
                preferred_port=self.config.dlna_media_server_port(),
            )
        except Exception as exc:
            logger.exception("DLNA local media server preparation failed path=%s", local_path)
            self.toast.show_message(f"投屏媒体服务启动失败：{exc}")
            return

        metadata = build_didl_lite(title, media_url, source.output_mime_type)
        self._dlna_cast_pending = True
        self._dlna_pending_cast_request_id = self._dlna_action_sequence + 1
        self._dlna_pending_position_offset = 0.0
        self._dlna_pending_seek_supported = True
        media_key = self._current_dlna_media_key()
        request_id = self._start_dlna_action(device, "cast", media_url, metadata, position, video_id=media_key)
        if request_id != self._dlna_pending_cast_request_id:
            logger.warning(
                "DLNA local cast request sequence changed expected=%s actual=%s",
                self._dlna_pending_cast_request_id,
                request_id,
            )
            self._dlna_pending_cast_request_id = request_id
        self.toast.show_message(f"正在投屏到 {device.friendly_name}...")

    def _select_dlna_device(self, title: str) -> DlnaDevice | None:
        dialog = DlnaCastDialog(
            title,
            discovery_timeout=float(self.config.get("dlna.discovery_timeout", 3.0) or 3.0),
            cached_devices=list(self._dlna_device_cache.values()),
            parent=self,
        )
        dialog.devices_updated.connect(self._cache_dlna_devices)
        accepted = bool(dialog.exec())
        device = dialog.selected_device() if accepted else None
        dialog.deleteLater()
        return device

    @staticmethod
    def _dlna_device_cache_key(device: DlnaDevice) -> str:
        return device.uuid or device.av_transport_url or device.location or f"{device.host}:{device.friendly_name}"

    def _cache_dlna_devices(self, devices: list[DlnaDevice]) -> None:
        self._dlna_device_cache = {
            self._dlna_device_cache_key(device): device
            for device in devices
        }
        logger.info("DLNA device cache updated count=%s", len(self._dlna_device_cache))

    def _forget_cached_dlna_device(self, device: DlnaDevice) -> None:
        self._dlna_device_cache.pop(self._dlna_device_cache_key(device), None)

    def _current_dlna_media_key(self) -> str:
        if self.current_video is not None:
            return f"video:{self.current_video.video_id}"
        if self.current_local_media_path:
            return f"file:{Path(self.current_local_media_path)}"
        return ""

    def _start_dlna_action(
        self,
        device: DlnaDevice,
        action: str,
        *arguments,
        video_id: str = "",
    ) -> int:
        self._dlna_action_sequence += 1
        request_id = self._dlna_action_sequence
        worker = DlnaActionWorker(request_id, self.dlna_controller, device, action, *arguments)
        worker.signals.success.connect(self._dlna_action_succeeded)
        worker.signals.error.connect(self._dlna_action_failed)
        worker.signals.finished.connect(self._dlna_action_finished)
        self._dlna_action_workers[request_id] = (worker, device, video_id)
        self.thread_pool.start(worker)
        return request_id

    @Slot(int, str, object)
    def _dlna_action_succeeded(self, request_id: int, action: str, result) -> None:
        context = self._dlna_action_workers.get(request_id)
        if context is None:
            return
        _worker, device, video_id = context
        if action == "cast":
            if video_id.startswith("file:"):
                stale_media = self._current_dlna_media_key() != video_id
            else:
                stale_media = self.current_video is None or self.current_video.video_id != video_id
            if request_id != self._dlna_pending_cast_request_id or stale_media:
                logger.info("stale DLNA cast succeeded; stopping remote request=%s", request_id)
                self._start_dlna_action(device, "stop")
                self.dlna_media_server.stop_streams()
                return
            self._dlna_cast_pending = False
            self._dlna_pending_cast_request_id = 0
            self._dlna_device = device
            self._dlna_remote_paused = False
            self._dlna_last_position = self.mpv.position()
            self._dlna_position_offset = self._dlna_pending_position_offset
            self._dlna_seek_supported = self._dlna_pending_seek_supported
            self.mpv.pause()
            self.player_page.set_cast_state(
                True,
                seek_supported=self._dlna_seek_supported,
                volume_supported=bool(device.rendering_control_url),
            )
            self.player_page.set_paused(False)
            self._dlna_position_timer.start()
            self._dlna_pending_volume = self.player_page.volume_slider.value()
            self._dlna_volume_timer.start()
            self.toast.show_message(f"已投屏到 {device.friendly_name}")
            return
        if action == "pause" and device is self._dlna_device:
            self._dlna_remote_paused = True
            self.player_page.set_paused(True)
        elif action == "play" and device is self._dlna_device:
            self._dlna_remote_paused = False
            self.player_page.set_paused(False)
        elif action == "get_position" and device is self._dlna_device and isinstance(result, tuple):
            position, duration = result
            position += self._dlna_position_offset
            if duration > 0 and self._dlna_position_offset:
                duration += self._dlna_position_offset
            self._dlna_last_position = position
            self.player_page.update_position(position)
            if duration > 0:
                self.player_page.update_duration(duration)
        elif action == "stop" and request_id in self._dlna_stop_notify_requests:
            self._dlna_stop_notify_requests.discard(request_id)
            self.toast.show_message(f"已停止向 {device.friendly_name} 投屏")

    @Slot(int, str, str)
    def _dlna_action_failed(self, request_id: int, action: str, message: str) -> None:
        context = self._dlna_action_workers.get(request_id)
        if action == "cast" and request_id == self._dlna_pending_cast_request_id:
            if context is not None:
                self._forget_cached_dlna_device(context[1])
            self._dlna_cast_pending = False
            self._dlna_pending_cast_request_id = 0
            self._dlna_pending_position_offset = 0.0
            self.dlna_media_server.stop_streams()
        if action == "get_position":
            logger.warning("DLNA position poll failed: %s", message)
            return
        device_name = context[1].friendly_name if context else "DLNA 设备"
        self.toast.show_message(f"{device_name} 投屏控制失败：{message}")

    @Slot(int)
    def _dlna_action_finished(self, request_id: int) -> None:
        self._dlna_action_workers.pop(request_id, None)
        self._dlna_stop_notify_requests.discard(request_id)
        if request_id == self._dlna_position_request_id:
            self._dlna_position_request_id = 0

    def _stop_dlna_cast(self, *, resume_local: bool, notify: bool) -> None:
        self._dlna_cast_pending = False
        self._dlna_pending_cast_request_id = 0
        device = self._dlna_device
        self._dlna_device = None
        self._dlna_position_timer.stop()
        self._dlna_volume_timer.stop()
        self._dlna_position_request_id = 0
        if device is not None:
            request_id = self._start_dlna_action(device, "stop")
            if notify:
                self._dlna_stop_notify_requests.add(request_id)
        self.dlna_media_server.stop_streams()
        self._dlna_remote_paused = False
        self.player_page.set_cast_state(False)
        if resume_local:
            self.mpv.seek(self._dlna_last_position)
            self.mpv.resume()
            self.player_page.set_paused(False)
        self._dlna_last_position = 0.0
        self._dlna_position_offset = 0.0
        self._dlna_pending_position_offset = 0.0
        self._dlna_seek_supported = True
        self._dlna_pending_seek_supported = True

    def _poll_dlna_position(self) -> None:
        if self._dlna_device is None or self._dlna_position_request_id:
            return
        self._dlna_position_request_id = self._start_dlna_action(self._dlna_device, "get_position")

    def _flush_dlna_volume(self) -> None:
        if self._dlna_device is not None and self._dlna_device.rendering_control_url:
            self._start_dlna_action(self._dlna_device, "set_volume", self._dlna_pending_volume)

    def _schedule_creator_playlist(self, video: VideoInfo) -> None:
        if video.source_site not in {"youtube", "bilibili"}:
            return
        if not (video.creator_id or video.channel_id or video.creator_url):
            logger.info("creator playlist skipped; video has no creator identity id=%s", video.video_id)
            self.toast.show_message("无法识别视频制作者，未加载作者视频列表")
            return
        self._creator_playlist_generation += 1
        generation = self._creator_playlist_generation
        video_id = video.video_id
        logger.info(
            "creator playlist scheduled generation=%s site=%s video=%s creator=%s",
            generation,
            video.source_site,
            video_id,
            video.creator_id or video.channel_id,
        )
        QTimer.singleShot(
            1500,
            lambda: self._start_creator_playlist_worker(generation, video_id, video),
        )

    def _start_creator_playlist_worker(self, generation: int, video_id: str, video: VideoInfo) -> None:
        if not self._is_creator_playlist_request_current(generation, video_id):
            logger.debug("creator playlist start ignored as stale generation=%s video=%s", generation, video_id)
            return
        worker = CreatorVideosWorker(self.resolver, video, generation=generation, limit=50)
        worker.signals.success.connect(self._creator_playlist_loaded)
        worker.signals.error.connect(self._creator_playlist_failed)
        worker.signals.finished.connect(self._creator_playlist_worker_finished)
        self._creator_playlist_workers[(generation, video_id)] = worker
        self.thread_pool.start(worker, -1)

    @Slot(int, str, object)
    def _creator_playlist_loaded(
        self,
        generation: int,
        video_id: str,
        playlist: PlaylistInfo | None,
    ) -> None:
        logger.info(
            "creator playlist result received generation=%s video=%s count=%s",
            generation,
            video_id,
            len(playlist.entries) if playlist else 0,
        )
        try:
            if not self._is_creator_playlist_request_current(generation, video_id):
                logger.info("creator playlist result ignored as stale generation=%s video=%s", generation, video_id)
                return
            if playlist is None or len(playlist.entries) <= 1:
                logger.info("creator playlist has no additional entries generation=%s video=%s", generation, video_id)
                self.toast.show_message("未找到该制作者的其他可用视频")
                return
            current_index = self._find_playlist_index(playlist, video_id)
            self._activate_playlist(
                playlist,
                current_index=max(0, current_index),
                playlist_key="",
                auto_play_next=self.current_playlist_auto_play,
                invalidate_creator_request=False,
            )
            logger.info(
                "creator playlist applied generation=%s video=%s count=%s",
                generation,
                video_id,
                len(playlist.entries),
            )
            self.toast.show_message(f"已加载制作者视频列表，共 {len(playlist.entries)} 条")
        except Exception as exc:
            logger.exception("creator playlist UI apply failed generation=%s video=%s", generation, video_id)
            self.toast.show_message(f"作者视频列表应用失败：{exc}")

    @Slot(int, str, str)
    def _creator_playlist_failed(self, generation: int, video_id: str, message: str) -> None:
        if not self._is_creator_playlist_request_current(generation, video_id):
            logger.debug("stale creator playlist failure ignored generation=%s video=%s", generation, video_id)
            return
        logger.warning("creator playlist failed generation=%s video=%s: %s", generation, video_id, message)
        self.toast.show_message("作者视频列表加载失败，当前视频继续播放")

    @Slot(int, str)
    def _creator_playlist_worker_finished(self, generation: int, video_id: str) -> None:
        self._creator_playlist_workers.pop((generation, video_id), None)
        logger.info("creator playlist worker finished generation=%s video=%s", generation, video_id)

    def _is_creator_playlist_request_current(self, generation: int, video_id: str) -> bool:
        return (
            generation == self._creator_playlist_generation
            and self.current_video is not None
            and self.current_video.video_id == video_id
            and self.current_playlist is None
        )

    def _invalidate_creator_playlist_request(self) -> None:
        self._creator_playlist_generation += 1

    def _settings_saved(self) -> None:
        logger.info("settings saved")
        self._invalidate_creator_playlist_request()
        self.mpv.apply_network_options()
        self.resolver = SiteResolver(self.config)
        self.update_service = UpdateService(self.config)
        self.runtime_install_service = RuntimeInstallService(self.config)
        self.ffmpeg_install_service = FfmpegInstallService(self.config)
        self.download_manager.reload_settings()
        self.player_page.reload_shortcuts()
        self._home_cache = []
        self._home_page = 1
        self._home_has_next = False
        self._search_keyword = ""
        self._search_page = 1
        self._refresh_runtime_status()
        self._sync_about_page()
        if self.stack.currentWidget() is self.home_page:
            self.load_home()

    def _show_home(self) -> None:
        self.stack.setCurrentWidget(self.home_page)
        if self._home_cache:
            self.home_page.set_videos(
                self._home_cache,
                mode="home",
                page=self._home_page,
                has_next=self._home_has_next,
            )
            self.home_page.set_home_context(self._home_page, self._home_has_next, source_label=self.resolver.home_source_label())
            self.home_page.set_favorite_ids(self.favorites.favorite_ids())
        else:
            self.load_home()

    def _remember_playback_return_widget(self, widget: QWidget | None = None) -> None:
        candidate = widget or self.stack.currentWidget()
        if (
            self._playback_return_widget is not None
            and (candidate is self.player_page or candidate is self.playlist_page)
        ):
            return
        self._playback_return_widget = candidate

    def _return_after_stop(self) -> None:
        target = self._playback_return_widget
        self._playback_return_widget = None
        if target is None:
            self._show_home()
            return
        if target is self.home_page:
            self.stack.setCurrentWidget(self.home_page)
            return
        if target is self.download_page:
            self.stack.setCurrentWidget(self.download_page)
            return
        if target is self.favorite_page:
            self.favorite_page.refresh()
            self.stack.setCurrentWidget(self.favorite_page)
            return
        if target is self.history_page:
            self.history_page.refresh()
            self.stack.setCurrentWidget(self.history_page)
            return
        if target in {self.player_page, self.playlist_page, self.settings_page, self.about_page}:
            self.stack.setCurrentWidget(target)
            return
        self._show_home()

    def _show_player_page(self) -> None:
        self.stack.setCurrentWidget(self.player_page)

    def _show_favorites(self) -> None:
        self.favorite_page.refresh()
        self.stack.setCurrentWidget(self.favorite_page)

    def _show_history(self) -> None:
        self.history_page.refresh()
        self.stack.setCurrentWidget(self.history_page)

    def _show_about(self) -> None:
        self.stack.setCurrentWidget(self.about_page)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self._leave_player_fullscreen()
        else:
            self._enter_player_fullscreen()

    def _enter_player_fullscreen(self) -> None:
        if self.isFullScreen():
            return
        self._was_maximized_before_fullscreen = self.isMaximized()
        logger.info("entering fullscreen previous_maximized=%s", self._was_maximized_before_fullscreen)
        self.stack.setCurrentWidget(self.player_page)
        self.top_bar_widget.hide()
        self.showFullScreen()
        self.player_page.set_fullscreen(True)

    def _leave_player_fullscreen(self) -> None:
        if not self.isFullScreen():
            return
        restore_maximized = bool(self._was_maximized_before_fullscreen)
        logger.info("leaving fullscreen restore_maximized=%s", restore_maximized)
        self.top_bar_widget.show()
        self.player_page.set_fullscreen(False)
        if restore_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self._was_maximized_before_fullscreen = None

    def _toggle_topmost(self) -> None:
        self._set_topmost(not self._is_topmost)

    def _set_topmost(self, enabled: bool) -> None:
        self._is_topmost = bool(enabled)
        was_fullscreen = self.isFullScreen()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._is_topmost)
        if was_fullscreen:
            self.showFullScreen()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self.top_bar_widget.set_topmost_state(self._is_topmost)

    def _check_updates(self) -> None:
        logger.info("manual update check requested")
        self.about_page.set_checking(True)
        self.about_page.set_upgrade_available(False)
        self.about_page.set_upgrade_progress(False, "")

        worker = UpdateCheckWorker(self.update_service)
        worker.signals.success.connect(self._update_check_succeeded)
        worker.signals.error.connect(self._update_check_failed)
        worker.signals.finished.connect(lambda: self.about_page.set_checking(False))
        self.thread_pool.start(worker)

    def _update_check_succeeded(self, result: UpdateCheckResult) -> None:
        logger.info(
            "update check result current=%s latest=%s has_update=%s asset=%s",
            result.current_version,
            result.latest_version,
            result.has_update,
            result.selected_asset.name if result.selected_asset else "",
        )
        self._last_update_result = result
        self.about_page.set_current_version(result.current_version)
        self.about_page.set_install_mode(result.install_mode_label)
        self.about_page.set_latest_version(result.latest_version, result.release.published_at)
        self.about_page.set_release_notes(result.release.body)
        self.about_page.set_upgrade_available(result.has_update)
        if result.has_update:
            asset_name = result.selected_asset.name if result.selected_asset else "升级包"
            if result.install_mode.startswith("linux"):
                self.about_page.set_status(f"检测到新版本，可下载 {asset_name}；下载后请手动替换或通过包管理器安装。")
            else:
                self.about_page.set_status(f"检测到新版本，可下载 {asset_name} 进行升级。")
        else:
            message = "当前已经是最新版本。"
            if result.selected_asset is None:
                message = "已获取版本信息，但没有找到匹配当前运行形态的升级包。"
            self.about_page.set_status(message)

    def _update_check_failed(self, message: str) -> None:
        logger.error("update check failed: %s", message)
        self.about_page.set_status(f"检测版本失败：{message}")
        QMessageBox.warning(self, "检测版本失败", message)

    def _start_upgrade_download(self) -> None:
        result = self._last_update_result
        if not result or not result.has_update or not result.selected_asset:
            QMessageBox.information(self, "提示", "请先检测版本，或当前没有可用更新。")
            return

        asset = result.selected_asset
        target_path = self.update_service.download_target_path(asset)
        self.about_page.set_upgrade_available(False)
        self.about_page.set_upgrade_progress(True, f"正在下载升级包：{asset.name}")
        self.about_page.set_status("升级包下载中，请稍候...")

        worker = UpdateDownloadWorker(self.update_service, asset.download_url, target_path, asset.name)
        worker.signals.progress.connect(self._update_download_progress)
        worker.signals.success.connect(self._update_download_success)
        worker.signals.error.connect(self._update_download_failed)
        worker.signals.finished.connect(self._update_download_finished)
        self.thread_pool.start(worker)

    def _update_download_progress(self, downloaded: int, total: int, percent: float, speed_text: str) -> None:
        if total > 0:
            size_text = f"{_format_bytes(downloaded)} / {_format_bytes(total)}"
        else:
            size_text = _format_bytes(downloaded)
        message = f"正在下载升级包：{size_text}  {speed_text}".strip()
        self.about_page.set_upgrade_progress(True, message, percent)

    def _update_download_success(self, path: str) -> None:
        result = self._last_update_result
        mode_label = result.install_mode_label if result else "当前版本"
        self.about_page.set_upgrade_progress(False, f"升级包已下载完成：{path}", 100.0)
        self.about_page.set_status("升级包已准备好。")
        if result and result.install_mode.startswith("linux"):
            package = Path(path)
            self.about_page.set_status("Linux 升级包已下载，请退出应用后手动安装或替换。")
            QMessageBox.information(
                self,
                "Linux 升级包已下载",
                f"{mode_label} 的升级包已保存到：\n{path}\n\n"
                "Linux 首版不会自动提权或安装系统包。AppImage 请退出后替换原文件；"
                "DEB 请通过软件中心或 apt/dpkg 安装。",
            )
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(package.parent)))
            return
        if result and result.install_mode == "portable":
            detail = "确认后将关闭当前应用，自动解压并替换便携版文件，完成后重新启动新版。"
        else:
            detail = "确认后将关闭当前应用，并立即启动新版安装程序。"
        answer = QMessageBox.question(
            self,
            "升级包已下载",
            f"{mode_label} 的升级包已保存到：\n{path}\n\n{detail}\n\n是否立即继续升级？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.about_page.set_status("升级包已下载，等待用户启动升级。")
            return
        self._launch_downloaded_upgrade(path, result.install_mode if result else "installer")

    def _launch_downloaded_upgrade(self, path: str, install_mode: str) -> None:
        if install_mode.startswith("linux"):
            self.about_page.set_status("Linux 升级包需要由用户手动安装。")
            QMessageBox.information(self, "Linux 升级说明", "Linux 首版不执行自动安装或权限提升。")
            return
        try:
            if install_mode == "portable":
                self.update_service.launch_portable_update(path)
                status = "便携版升级程序已启动，正在关闭当前应用..."
            else:
                self.update_service.launch_installer(path)
                status = "新版安装程序已启动，正在关闭当前应用..."
        except Exception as exc:
            message = str(exc).strip() or "无法启动升级程序"
            logger.exception("failed to launch downloaded upgrade mode=%s path=%s", install_mode, path)
            self.about_page.set_status(f"启动升级失败：{message}")
            self.about_page.set_upgrade_available(True)
            QMessageBox.warning(self, "启动升级失败", message)
            return

        self.about_page.set_status(status)
        QTimer.singleShot(0, self.close)

    def _update_download_failed(self, message: str) -> None:
        logger.error("update download failed: %s", message)
        self.about_page.set_upgrade_progress(False, f"升级包下载失败：{message}")
        self.about_page.set_status("升级包下载失败。")
        self.about_page.set_upgrade_available(bool(self._last_update_result and self._last_update_result.has_update))
        QMessageBox.warning(self, "下载升级包失败", message)

    def _update_download_finished(self) -> None:
        if self._last_update_result and self._last_update_result.has_update:
            self.about_page.set_upgrade_available(True)

    def _open_update_folder(self) -> None:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(UPDATE_DIR)))

    def _install_node_runtime(self) -> None:
        logger.info("node installer requested")
        try:
            info = self.runtime_install_service.fetch_node_installer_info()
        except Exception as exc:
            message = str(exc)
            self.settings_page.set_runtime_install_busy(False, f"获取 Node.js 安装信息失败：{message}")
            QMessageBox.warning(self, "获取 Node.js 安装信息失败", message)
            return

        target_path = self.runtime_install_service.installer_target_path(info)
        self.settings_page.set_runtime_install_busy(True, f"正在下载 Node.js 安装包：{info.filename}")
        self._pending_node_installer_path = str(target_path)

        worker = UpdateDownloadWorker(self.update_service, info.url, target_path, info.filename)
        worker.signals.progress.connect(self._node_download_progress)
        worker.signals.success.connect(self._node_download_success)
        worker.signals.error.connect(self._node_download_failed)
        worker.signals.finished.connect(lambda: self.settings_page.set_runtime_install_busy(False))
        self.thread_pool.start(worker)

    def _node_download_progress(self, downloaded: int, total: int, percent: float, speed_text: str) -> None:
        if total > 0:
            text = f"Node.js 下载中：{_format_bytes(downloaded)} / {_format_bytes(total)}  {speed_text}"
        else:
            text = f"Node.js 下载中：{_format_bytes(downloaded)}  {speed_text}"
        if percent > 0:
            text += f"  ({percent:.1f}%)"
        self.settings_page.set_runtime_install_progress(text)

    def _node_download_success(self, path: str) -> None:
        logger.info("node installer downloaded path=%s", path)
        try:
            self.runtime_install_service.launch_installer(path)
            self.settings_page.set_runtime_install_progress(
                "Node.js 安装程序已启动。安装完成后，建议重新打开应用。"
            )
            QMessageBox.information(
                self,
                "Node.js 安装程序已启动",
                f"安装包已下载到：\n{path}\n\n安装完成后，建议重新打开应用。",
            )
        except Exception as exc:
            message = str(exc)
            self.settings_page.set_runtime_install_progress(f"无法自动启动安装程序：{message}")
            QMessageBox.warning(self, "启动安装程序失败", message)
            self.runtime_install_service.open_official_site()

    def _node_download_failed(self, message: str) -> None:
        logger.error("node installer download failed: %s", message)
        self.settings_page.set_runtime_install_progress(f"Node.js 下载失败：{message}")
        QMessageBox.warning(
            self,
            "下载 Node.js 失败",
            f"{message}\n\n将为你打开 Node.js 官网下载页。",
        )
        self.runtime_install_service.open_official_site()

    def _maybe_prompt_ffmpeg_install(self) -> None:
        if self.ffmpeg_install_service.is_available():
            return
        if not self.ffmpeg_install_service.automatic_install_supported():
            logger.warning(
                "FFmpeg is unavailable; Linux automatic installation is disabled, use bundled FFmpeg or the package manager"
            )
            return
        answer = QMessageBox.question(
            self,
            "FFmpeg 未配置",
            "未检测到可用的 FFmpeg。\n\n下载高质量视频时通常需要 FFmpeg 合并音频和视频。是否现在下载并安装？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_ffmpeg_install()

    def _start_ffmpeg_install(self) -> None:
        try:
            info = self.ffmpeg_install_service.install_info()
        except RuntimeError as exc:
            QMessageBox.information(self, "FFmpeg 安装说明", str(exc))
            return
        self._pending_ffmpeg_info = info
        self._show_ffmpeg_progress("正在下载 FFmpeg...", 0.0, indeterminate=True)

        worker = UpdateDownloadWorker(self.update_service, info.url, info.archive_path, info.archive_path.name)
        worker.signals.progress.connect(self._ffmpeg_download_progress)
        worker.signals.success.connect(self._ffmpeg_download_success)
        worker.signals.error.connect(self._ffmpeg_download_failed)
        self.thread_pool.start(worker)

    def _ffmpeg_download_progress(self, downloaded: int, total: int, percent: float, speed_text: str) -> None:
        if total > 0:
            message = f"正在下载 FFmpeg：{_format_bytes(downloaded)} / {_format_bytes(total)}  {speed_text}"
            self._show_ffmpeg_progress(message, percent)
        else:
            message = f"正在下载 FFmpeg：{_format_bytes(downloaded)}  {speed_text}"
            self._show_ffmpeg_progress(message, 0.0, indeterminate=True)

    def _ffmpeg_download_success(self, path: str) -> None:
        logger.info("ffmpeg archive downloaded path=%s", path)
        info = self._pending_ffmpeg_info or self.ffmpeg_install_service.install_info()
        self._show_ffmpeg_progress("FFmpeg 下载完成，正在解压...", 0.0, indeterminate=True)

        worker = ArchiveExtractWorker(Path(path), info.extract_dir)
        worker.signals.success.connect(self._ffmpeg_extract_success)
        worker.signals.error.connect(self._ffmpeg_extract_failed)
        self.thread_pool.start(worker)

    def _ffmpeg_download_failed(self, message: str) -> None:
        logger.error("ffmpeg download failed: %s", message)
        self._close_ffmpeg_progress()
        QMessageBox.warning(self, "FFmpeg 下载失败", message)

    def _ffmpeg_extract_success(self, _extract_dir: str) -> None:
        ffmpeg_dir = self.ffmpeg_install_service.locate_extracted_ffmpeg_dir()
        if not ffmpeg_dir:
            self._close_ffmpeg_progress()
            QMessageBox.warning(self, "FFmpeg 安装失败", "已解压 FFmpeg，但没有找到 FFmpeg 可执行文件。")
            return

        self.config.set("download.ffmpeg_dir", ffmpeg_dir)
        self.config.save()
        self.settings_page.ffmpeg_dir_edit.setText(ffmpeg_dir)
        self.download_manager.reload_settings()
        self._close_ffmpeg_progress()
        QMessageBox.information(self, "FFmpeg 已安装", f"FFmpeg 已安装并写入设置：\n{ffmpeg_dir}")

    def _ffmpeg_extract_failed(self, message: str) -> None:
        logger.error("ffmpeg extract failed: %s", message)
        self._close_ffmpeg_progress()
        QMessageBox.warning(self, "FFmpeg 解压失败", message)

    def _show_ffmpeg_progress(self, message: str, percent: float, indeterminate: bool = False) -> None:
        if self._ffmpeg_progress_dialog is None:
            dialog = QProgressDialog("正在准备 FFmpeg...", "", 0, 100, self)
            dialog.setWindowTitle("安装 FFmpeg")
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setCancelButton(None)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            self._ffmpeg_progress_dialog = dialog
        dialog = self._ffmpeg_progress_dialog
        dialog.setLabelText(message)
        if indeterminate:
            dialog.setRange(0, 0)
        else:
            dialog.setRange(0, 100)
            dialog.setValue(int(max(0, min(100, percent))))
        dialog.show()

    def _close_ffmpeg_progress(self) -> None:
        if self._ffmpeg_progress_dialog is not None:
            self._ffmpeg_progress_dialog.close()
            self._ffmpeg_progress_dialog.deleteLater()
            self._ffmpeg_progress_dialog = None

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            logger.info("main window closing")
            self._invalidate_creator_playlist_request()
            self._dlna_position_timer.stop()
            self._dlna_volume_timer.stop()
            self.dlna_media_server.stop()
            self.config.save()
            self.download_manager.flush()
            self.mpv.shutdown()
        finally:
            super().closeEvent(event)


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB")
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"
