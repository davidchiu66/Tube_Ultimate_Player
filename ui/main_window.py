from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from app_paths import APP_NAME, UPDATE_DIR, asset_path
from database.favorite_repository import FavoriteRepository
from database.history_repository import HistoryRepository
from database.sqlite_manager import SQLiteManager
from download.download_manager import DownloadManager
from player.mpv_player import MpvPlayer
from resolver.models import HomeVideo, VideoInfo, VideoQuality
from resolver.youtube_resolver import YoutubeResolver
from services.config_service import ConfigService
from services.runtime_install_service import RuntimeInstallService
from services.update_service import REPO_URL, UpdateCheckResult, UpdateService
from ui.about_page import AboutPage
from ui.download_page import DownloadPage
from ui.favorite_page import FavoritePage
from ui.history_page import HistoryPage
from ui.home_page import HomePage
from ui.player_page import PlayerPage
from ui.settings_page import SettingsPage
from ui.toolbar import PlayerToolbar
from ui.toast import Toast
from ui.url_dialog import UrlPlayDialog
from workers.home_worker import HomeWorker
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
        self.resize(1180, 760)
        self._apply_window_icon()

        self.config = ConfigService()
        self.db = SQLiteManager()
        self.history = HistoryRepository(self.db)
        self.favorites = FavoriteRepository(self.db)
        self.resolver = YoutubeResolver(self.config)
        self.update_service = UpdateService(self.config)
        self.runtime_install_service = RuntimeInstallService(self.config)
        self.thread_pool = QThreadPool.globalInstance()
        self.download_manager = DownloadManager(self.config, self.thread_pool)

        self.current_video: VideoInfo | None = None
        self.current_quality_label = ""
        self._home_cache: list[HomeVideo] = []
        self._home_page = 1
        self._home_has_next = False
        self._search_keyword = ""
        self._search_page = 1
        self._last_update_result: UpdateCheckResult | None = None
        self._pending_node_installer_path = ""

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

        self.stack = QStackedWidget()
        self.home_page = HomePage()
        self.player_page = PlayerPage()
        self.download_page = DownloadPage()
        self.favorite_page = FavoritePage(self.favorites)
        self.history_page = HistoryPage(self.history)
        self.settings_page = SettingsPage(self.config)
        self.about_page = AboutPage()

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.player_page)
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
        self.player_page.set_volume(int(self.config.get("player.volume", 80)))
        self.player_page.set_speed(float(self.config.get("player.speed", 1.0)))
        self._sync_about_page()
        self._refresh_runtime_status()
        self.stack.setCurrentWidget(self.home_page)
        self._refresh_favorite_views()
        QTimer.singleShot(0, self.load_home)
        logger.info("main window initialized")

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

        self.home_page.refresh_requested.connect(self._refresh_home_page)
        self.home_page.play_requested.connect(self.play_url)
        self.home_page.favorite_requested.connect(self._favorite_home_video)
        self.home_page.page_requested.connect(self._load_page)
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

    def _apply_window_icon(self) -> None:
        for path in (
            asset_path("icons", "app-icon.ico"),
            asset_path("icons", "app-icon-256.png"),
            asset_path("icons", "app-icon.png"),
        ):
            if path.exists():
                self.setWindowIcon(QIcon(str(path)))
                return

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
            QMessageBox.information(self, "提示", "请输入 YouTube URL")
            return
        if not target.startswith(("http://", "https://")):
            QMessageBox.warning(self, "URL 无效", "请输入完整的 http:// 或 https:// 地址")
            return

        logger.info("play url requested: %s", target)
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.set_loading(True, "正在解析视频地址，请稍候...")

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
        self.home_page.set_loading(True, f"正在获取 YouTube 首页推荐（第 {self._home_page} 页），请稍候...")
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
            f"正在搜索 YouTube：{keyword}（第 {page} 页），请稍候，这一步通常会比首页加载稍慢一些...",
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
        self.update_service = UpdateService(self.config)
        self.runtime_install_service = RuntimeInstallService(self.config)
        self.download_manager.reload_settings()
        self._refresh_runtime_status()
        self._sync_about_page()

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

    def _show_about(self) -> None:
        self.stack.setCurrentWidget(self.about_page)

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
        if result and result.install_mode == "portable":
            detail = "便携版升级包已下载完成，请关闭当前应用后手动替换文件。"
        else:
            detail = "安装包已下载完成，请关闭当前应用后运行安装程序。"
        QMessageBox.information(
            self,
            "升级包已下载",
            f"{mode_label} 的升级包已保存到：\n{path}\n\n{detail}",
        )

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

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            logger.info("main window closing")
            self.config.save()
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
