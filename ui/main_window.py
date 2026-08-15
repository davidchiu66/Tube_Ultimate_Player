from __future__ import annotations

import logging
import os
import sys
import threading
import time
from functools import partial, wraps
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QCoreApplication, QThreadPool, QTimer, QUrl, Qt, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
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
from download.models import DownloadTask
from player.mpv_player import MpvPlayer
from platform_support import is_root_user
from resolver.models import (
    MUXED_AUDIO_TRACK_ID,
    AudioTrack,
    HomeVideo,
    PlaylistEntry,
    PlaylistInfo,
    PlaylistSection,
    PlaybackQualityHint,
    PlaybackRequestContext,
    SavedPlaylist,
    VideoInfo,
    VideoQuality,
)
from resolver.quality_selector import select_quality_by_hint, select_quality_by_tier
from resolver.site_resolver import SiteResolver
from services.config_service import ConfigService
from services.network_quality_service import NetworkMeasurement, NetworkMeasurementCache, select_quality_for_bandwidth
from services.ffmpeg_install_service import FfmpegInstallInfo, FfmpegInstallService
from services.runtime_install_service import NODE_TRUSTED_HOSTS, RuntimeInstallService
from services.restart_service import RestartError, restart_application
from services.update_service import REPO_URL, UpdateCheckResult, UpdateService, detect_platform_info
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
from workers.backup_worker import BackupListWorker, BackupRestoreWorker, BackupUploadWorker, WebdavTestWorker
from workers.collection_worker import CollectionWorker
from workers.cookie_probe_worker import CookieProbeWorker
from workers.creator_videos_worker import CreatorVideosWorker
from workers.dlna_worker import DlnaActionWorker
from workers.home_worker import HomeWorker
from workers.playlist_worker import PlaylistWorker
from workers.resolver_worker import ResolverWorker
from workers.search_worker import SearchWorker
from workers.subtitle_worker import SubtitleLoadWorker
from workers.update_check_worker import UpdateCheckWorker
from workers.update_download_worker import UpdateDownloadWorker
from workers.network_probe_worker import NetworkProbeWorker


logger = logging.getLogger("tube_player.ui")

# 退出时等待线程池收敛的上限，超时后记录告警并继续走关闭流程。
SHUTDOWN_WAIT_MS = 3000
# 首页结果在界面层的复用时长，与 SiteResolver 的首页缓存 TTL 保持一致。
HOME_CACHE_TTL_SECONDS = 300.0
_COOKIE_PROBE_STATE_LOCK = threading.Lock()
_COOKIE_PROBE_INFLIGHT = False


def _skip_after_shutdown(method):
    """关闭流程开始后丢弃后台 worker 的回调。

    线程池 worker 的信号可能在 closeEvent 之后才排到事件队列，此时 mpv、DLNA
    中继等依赖对象已经释放，继续执行槽函数会访问悬空资源。
    """

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if getattr(self, "_shutting_down", False):
            logger.debug("忽略关闭期间的回调 %s", method.__name__)
            return None
        return method(self, *args, **kwargs)

    return wrapper


def _task_created_at(task) -> float:
    """下载任务的创建时间，缺失时视为最早（排在列表最下方）。"""
    created_at = getattr(task, "created_at", None)
    return created_at.timestamp() if created_at is not None else 0.0


def _records_to_video_infos(records: list) -> list[VideoInfo]:
    """把收藏/历史记录转换为批量下载需要的 VideoInfo，忽略无地址记录。"""
    videos: list[VideoInfo] = []
    for record in list(records or []):
        url = str(record.get("webpage_url") or "").strip()
        if not url:
            continue
        try:
            duration = int(record.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        videos.append(
            VideoInfo(
                video_id=str(record.get("video_id") or ""),
                title=str(record.get("title") or ""),
                source_site=str(record.get("source_site") or ""),
                uploader=str(record.get("uploader") or ""),
                duration=duration,
                webpage_url=url,
                thumbnail=str(record.get("thumbnail") or ""),
            )
        )
    return videos


def _enqueue_library_records(owner, records: list, *, empty_message: str, log_name: str) -> None:
    videos = _records_to_video_infos(records)
    if not videos:
        owner.toast.show_message(empty_message)
        return
    try:
        created, skipped = owner.download_manager.enqueue_many(videos, "Auto")
    except Exception:
        logger.exception("%s batch download failed count=%s", log_name, len(videos))
        owner.toast.show_message("批量下载失败")
        return
    message = f"已加入下载队列 {created} 个"
    if skipped:
        message += f"，跳过 {skipped} 个（已在队列或已完成）"
    owner.toast.show_message(message)


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
        # 下载改用 DownloadManager 自带的专用线程池，避免与解析/搜索/投屏抢占全局线程池。
        self.download_manager = DownloadManager(self.config)

        self.current_video: VideoInfo | None = None
        self.current_local_media_path = ""
        self.current_quality_label = ""
        # 所选音轨的 track_id；空串 = 跟随清晰度自带的默认轨（单语言视频恒为空）。
        self.current_audio_track_id = ""
        self.current_playlist: PlaylistInfo | None = None
        self.current_playlist_index = -1
        self.current_playlist_key = ""
        # 自动连播默认关闭：新建/新载入的列表播完当前一条就停，用户勾选后才继续。
        self.current_playlist_auto_play = False
        self._pending_playlist_video_id = ""
        self._playback_return_widget: QWidget | None = None
        self._playback_finished = False
        self._was_maximized_before_fullscreen: bool | None = None
        # "进入播放默认全屏"只在一次播放动作的开头生效一次：用户之后手动退出全屏，
        # 不能因为播放列表自动连播、切清晰度等内部重载又被拉回全屏。
        self._pending_playback_fullscreen = False
        self._playback_request_id = 0
        self._pending_quality_hint: PlaybackQualityHint | None = None
        self._pending_quality_reason = "direct"
        self._playback_request_context: PlaybackRequestContext | None = None
        self._pending_smart_video: VideoInfo | None = None
        self._pending_smart_kbps: float | None = None
        self._network_measurements = NetworkMeasurementCache()
        self._creator_playlist_generation = 0
        self._creator_playlist_workers: dict[tuple[int, str], CreatorVideosWorker] = {}
        # 左侧「合集列表」：与右侧播放列表各自独立一套状态，互不覆盖。
        self.current_collection: PlaylistInfo | None = None
        self.current_collection_index = -1
        self.current_collection_key = ""
        self.current_collection_auto_play = False
        self._collection_generation = 0
        self._collection_workers: dict[tuple[int, str], CollectionWorker] = {}
        # 谁最后驱动了当前播放，谁负责连播；空串表示单条播放，没有队列在驱动。
        self._active_queue = ""
        # 线程池 worker 在结束前必须留有 Python 引用，详见 _start_worker 的说明。
        self._active_workers: dict[int, object] = {}
        self._worker_sequence = 0
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
        # 站点 -> (缓存时间, 视频, 页码, 还有下一页)，切换站点时直接复用，避免每次都重新拉取。
        self._home_state: dict[str, tuple[float, list[HomeVideo], int, bool]] = {}
        self._search_keyword = ""
        self._search_page = 1
        # 记录上一次浏览动作是「首页」还是「搜索」。切换站点时只据此取向：
        # 搜索框里的残留文本不该把首页浏览劫持成又一次搜索。
        self._browse_mode = "home"
        # 工具栏首次站点跟随设置中的默认首页，之后切换只影响本次会话。
        self._browse_source = self.config.default_home_source()
        # 每发起一轮首页/搜索加载就自增，用来丢弃切换站点后才回来的旧结果。
        self._browse_generation = 0
        self._subtitle_request_id = 0
        self._pending_recent_url = ""
        self._last_update_result: UpdateCheckResult | None = None
        self._pending_node_installer_path = ""
        self._pending_ffmpeg_info: FfmpegInstallInfo | None = None
        self._ffmpeg_progress_dialog: QProgressDialog | None = None
        self._shutting_down = False

        self.top_bar_widget = PlayerToolbar(self)
        self.top_bar_widget.set_source(self._browse_source)
        self.url_edit = self.top_bar_widget.search_edit
        self.search_button = self.top_bar_widget.search_button
        self.play_url_button = self.top_bar_widget.url_button
        self.home_nav = self.top_bar_widget.home_button
        self.playlist_nav = self.top_bar_widget.playlist_button
        self.player_nav = self.top_bar_widget.player_button
        self.download_nav = self.top_bar_widget.download_button
        self.favorite_nav = self.top_bar_widget.favorite_button
        self.history_nav = self.top_bar_widget.history_button
        self.settings_nav = self.top_bar_widget.settings_button
        self.about_nav = self.top_bar_widget.about_button
        self.topmost_nav = self.top_bar_widget.topmost_button
        self._is_topmost = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

        self.stack = QStackedWidget()
        # 首页与播放页启动后立刻可见，必须预先构建；其余页面在首次访问时再建，
        # 以缩短冷启动时间（每个页面的信号连接与初始状态都在对应工厂里补齐）。
        self._lazy_pages: dict[str, QWidget] = {}
        self.home_page = HomePage()
        self.player_page = PlayerPage(self.config)

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.player_page)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.top_bar_widget)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.toast = Toast(self)

        self.mpv = MpvPlayer(self.player_page.video_widget, self.config)
        self._connect_signals()
        self.player_page.set_volume(int(self.config.get("player.volume", 80)))
        self.player_page.set_speed(float(self.config.get("player.speed", 1.0)))
        self._refresh_saved_playlists()
        self.stack.setCurrentWidget(self.home_page)
        self._refresh_favorite_views()
        QTimer.singleShot(0, self.load_home)
        QTimer.singleShot(1200, self._maybe_prompt_ffmpeg_install)
        QTimer.singleShot(300, self._start_cookie_probe)
        if is_root_user():
            QTimer.singleShot(0, self._show_root_session_warning)
        self.top_bar_widget.set_topmost_state(self._is_topmost)
        logger.info("main window initialized")

    # ------------------------------------------------------------------
    # 懒加载页面
    # ------------------------------------------------------------------

    @property
    def playlist_page(self) -> PlaylistPage:
        return self._page("playlist")

    @property
    def download_page(self) -> DownloadPage:
        return self._page("download")

    @property
    def favorite_page(self) -> FavoritePage:
        return self._page("favorite")

    @property
    def history_page(self) -> HistoryPage:
        return self._page("history")

    @property
    def settings_page(self) -> SettingsPage:
        return self._page("settings")

    @property
    def about_page(self) -> AboutPage:
        return self._page("about")

    def _page(self, name: str) -> QWidget:
        """返回指定页面，必要时先构建它。"""
        page = self._lazy_pages.get(name)
        if page is None:
            page = getattr(self, f"_create_{name}_page")()
            self._lazy_pages[name] = page
            self.stack.addWidget(page)
            logger.info("lazy page created name=%s", name)
        return page

    def _created_page(self, name: str):
        """只返回已经构建过的页面，避免同步状态时把页面提前建出来。"""
        return self._lazy_pages.get(name)

    def _create_playlist_page(self) -> PlaylistPage:
        page = PlaylistPage()
        page.back_requested.connect(self._show_player_page)
        page.play_entry_requested.connect(self._play_playlist_from_page)
        page.download_entries_requested.connect(self._download_playlist_entries)
        page.save_requested.connect(self._save_active_playlist)
        page.load_saved_requested.connect(self._load_saved_playlist)
        page.delete_saved_requested.connect(self._delete_saved_playlist)
        page.auto_play_changed.connect(self._set_playlist_auto_play)
        if self.current_playlist is not None:
            page.set_playlist(
                self.current_playlist,
                current_index=self.current_playlist_index,
                auto_play_next=self.current_playlist_auto_play,
            )
        page.set_saved_playlists(self.playlists.all_playlists(), self.current_playlist_key)
        return page

    def _create_download_page(self) -> DownloadPage:
        page = DownloadPage()
        page.pause_requested.connect(self.download_manager.pause_task)
        page.start_requested.connect(self.download_manager.start_task)
        page.delete_requested.connect(self.download_manager.delete_task)
        page.pause_tasks_requested.connect(self._pause_download_tasks)
        page.start_tasks_requested.connect(self._start_download_tasks)
        page.delete_tasks_requested.connect(self._delete_download_tasks)
        page.play_file_requested.connect(self.play_local_file)
        self.download_manager.task_added.connect(page.add_task)
        self.download_manager.task_changed.connect(page.update_task)
        self.download_manager.task_removed.connect(page.remove_task)
        # add_task 把新行插在最前，所以这里按 created_at 升序喂进去，
        # 表格里最终就是「新→旧」，与后续新建任务的落位一致。
        for task in sorted(self.download_manager.tasks(), key=_task_created_at):
            page.add_task(task)
        return page

    def _create_favorite_page(self) -> FavoritePage:
        page = FavoritePage(self.favorites)
        page.play_requested.connect(self.play_url)
        page.remove_requested.connect(self._remove_favorite)
        page.download_videos_requested.connect(self._download_favorite_records)
        page.remove_videos_requested.connect(self._remove_favorites)
        return page

    def _create_history_page(self) -> HistoryPage:
        page = HistoryPage(self.history)
        page.play_requested.connect(self.play_url)
        page.remove_requested.connect(self._remove_history_record)
        page.download_videos_requested.connect(self._download_history_records)
        page.remove_videos_requested.connect(self._remove_history_records)
        return page

    def _create_settings_page(self) -> SettingsPage:
        page = SettingsPage(self.config)
        page.settings_saved.connect(self._settings_saved)
        page.install_node_requested.connect(self._install_node_runtime)
        page.open_node_site_requested.connect(self._open_node_official_site)
        page.reprobe_cookies_requested.connect(self._reprobe_cookies)
        page.backup_test_requested.connect(self._test_webdav_account)
        page.backup_requested.connect(self._start_backup_upload)
        page.backup_restore_list_requested.connect(self._load_remote_backups)
        page.backup_restore_requested.connect(self._start_backup_restore)
        page.set_runtime_status(self.runtime_install_service.detect_runtime_status())
        return page

    def _create_about_page(self) -> AboutPage:
        page = AboutPage()
        page.open_repo_requested.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        page.open_update_folder_requested.connect(self._open_update_folder)
        page.check_update_requested.connect(self._check_updates)
        page.upgrade_requested.connect(self._start_upgrade_download)
        _mode, mode_label = self.update_service.detect_install_mode()
        self._apply_about_page_defaults(
            page,
            current_version=self.update_service.local_version(),
            mode_label=mode_label,
        )
        return page

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
        self.top_bar_widget.source_changed.connect(self._set_browse_source)
        self.play_url_button.clicked.connect(self._show_play_url_dialog)
        self.home_nav.clicked.connect(self._show_home)
        self.playlist_nav.clicked.connect(self._show_playlist_page)
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

        self.player_page.play_pause_requested.connect(self._toggle_play_pause)
        self.player_page.stop_requested.connect(self._stop_playback)
        self.player_page.seek_requested.connect(self._seek_playback)
        self.player_page.volume_changed.connect(self._set_volume)
        self.player_page.speed_changed.connect(self._set_speed)
        self.player_page.quality_changed.connect(self._change_quality)
        self.player_page.audio_track_changed.connect(self._change_audio_track)
        self.player_page.subtitle_changed.connect(self._change_subtitle)
        self.player_page.cast_requested.connect(self._show_cast_dialog)
        self.player_page.browser_play_requested.connect(self._open_current_video_in_browser)
        self.player_page.fullscreen_requested.connect(self._toggle_fullscreen)
        self.player_page.download_requested.connect(self._download_current_video)
        self.player_page.favorite_requested.connect(self._favorite_current_video)
        self.player_page.playlist_entry_requested.connect(self._play_playlist_index)
        self.player_page.playlist_download_requested.connect(self._download_playlist_entries)
        self.player_page.playlist_save_requested.connect(self._save_active_playlist)
        self.player_page.playlist_load_requested.connect(self._load_saved_playlist_from_overlay)
        self.player_page.playlist_delete_requested.connect(self._delete_saved_playlist)
        self.player_page.playlist_auto_play_changed.connect(self._set_playlist_auto_play)
        self.player_page.collection_entry_requested.connect(self._play_collection_index)
        self.player_page.collection_download_requested.connect(self._download_playlist_entries)
        self.player_page.collection_save_requested.connect(self._save_active_collection)
        self.player_page.collection_load_requested.connect(self._load_saved_collection_from_overlay)
        self.player_page.collection_delete_requested.connect(self._delete_saved_playlist)
        self.player_page.collection_auto_play_changed.connect(self._set_collection_auto_play)

        self.download_manager.message.connect(self.toast.show_message)
        # 下载页是懒加载的，状态标识不能依赖它的连接，这里单独订阅一份。
        self.download_manager.task_added.connect(self._sync_download_state_from_task)
        self.download_manager.task_changed.connect(self._sync_download_state_from_task)
        self.download_manager.task_removed.connect(self._handle_download_task_removed)

        self.mpv.position_changed.connect(self._handle_mpv_position_changed)
        self.mpv.duration_changed.connect(self._handle_mpv_duration_changed)
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
            self.resize(960, 640)
            return
        available = screen.availableGeometry()
        width = self._adaptive_window_length(available.width(), preferred=1180, minimum=720)
        height = self._adaptive_window_length(available.height(), preferred=760, minimum=520)
        self.setMinimumSize(min(640, width), min(420, height))
        self.resize(width, height)

    @staticmethod
    def _adaptive_window_length(available: int, *, preferred: int, minimum: int) -> int:
        """返回不超过工作区的窗口边长，因此无需在显示后再夹取窗口位置。

        注意：不要再加"把窗口挪回工作区"的逻辑。Windows 的最大化窗口 frame 会向左/右/下
        各外扩 8px 不可见阴影边框（frameGeometry 起点为负数），按工作区夹取会误判为越界并
        调用 move()，而 Windows 拒绝移动最大化窗口，只会反复打印
        QWindowsWindow::setGeometry: Unable to set geometry 警告。
        """
        if available <= 0:
            return preferred
        margin = max(24, min(80, int(available * 0.06)))
        usable = max(1, available - margin)
        if usable < minimum:
            return usable
        return min(preferred, usable)

    @staticmethod
    def _apply_about_page_defaults(page: AboutPage, *, current_version: str = "", mode_label: str = "") -> None:
        page.set_current_version(current_version)
        page.set_install_mode(mode_label)
        page.set_platform(detect_platform_info().describe())
        page.set_latest_version("-")
        page.set_release_notes("")
        page.set_status("可在这里检测新版本并查看发布说明。")
        page.set_upgrade_available(False)
        page.set_upgrade_progress(False, "")

    def _sync_about_page(self) -> None:
        # 关于页尚未构建时无需同步，构建时工厂会自行读取最新版本信息。
        page = self._created_page("about")
        if page is None:
            return
        current_version = self.update_service.local_version()
        _mode, mode_label = self.update_service.detect_install_mode()
        self._apply_about_page_defaults(page, current_version=current_version, mode_label=mode_label)

    def _refresh_runtime_status(self) -> None:
        page = self._created_page("settings")
        if page is None:
            return
        page.set_runtime_status(self.runtime_install_service.detect_runtime_status())

    def _open_node_official_site(self) -> None:
        # runtime_install_service 会在设置保存后被重建，因此不能直接连它的绑定方法。
        self.runtime_install_service.open_official_site()

    def _show_play_url_dialog(self) -> None:
        dialog = UrlPlayDialog(self, config=self.config)
        if dialog.exec():
            url = dialog.url()
            # 提交即记录：解析失败的地址也留在历史里可再试，标题稍后由 _resolved 回填。
            if url:
                self.config.add_recent_url(url)
                self.config.save()
                # 记住本次输入，解析出标题后回填到这条历史（yt-dlp 常会归一化 URL，
                # 直接用 webpage_url 可能匹配不到用户输入的那条）。
                self._pending_recent_url = url
            self.play_url(url)

    def _toolbar_search_requested(self, text: str) -> None:
        self.url_edit.setText(text)
        self.search_videos()

    def _start_worker(self, worker, priority: int = 0) -> None:
        """提交线程池任务，并在任务结束前一直持有 worker 的 Python 引用。

        QThreadPool.start() 之后 C++ 侧接管 worker 的销毁，但 Python 包装对象和它
        持有的 signals（QObject）没有别的引用，随时可能被 GC 回收。一旦在排队中的
        跨线程信号投递出去之前回收，轻则回调静默丢失（首页/搜索加载完却不刷新界面），
        重则访问已释放的发送者直接崩溃退出。这里统一登记引用，等 finished 到达主线程
        后再释放。
        """
        self._worker_sequence += 1
        token = self._worker_sequence
        self._active_workers[token] = worker
        # 用 partial 绑定 worker 本身：即使 _release_worker 因关闭流程被跳过，
        # 连接本身仍持有引用，直到 signals 随 worker 一起销毁。
        worker.signals.finished.connect(partial(self._release_worker, token, worker))
        self.thread_pool.start(worker, priority)

    def _release_worker(self, token: int, _worker, *_args) -> None:
        self._active_workers.pop(token, None)

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
        # 单条播放不属于任何队列，连播交给探测结果决定。
        self._active_queue = ""
        request_id = self._begin_playback_request(target, reason="direct")
        self.current_local_media_path = ""
        logger.info("play url requested: %s", target)
        self._arm_playback_window_mode()
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.set_loading(True, "正在解析视频地址，请稍候...")

        worker = ResolverWorker(target, self.resolver)
        worker.signals.success.connect(lambda video: self._resolved_for_request(request_id, video))
        worker.signals.error.connect(lambda message: self._resolve_failed_for_request(request_id, message))
        worker.signals.finished.connect(lambda: self._url_resolve_finished(request_id))
        self._start_worker(worker)

    @_skip_after_shutdown
    def _url_resolve_finished(self, request_id: int | None = None) -> None:
        if request_id is not None and request_id != self._playback_request_id:
            return
        if self._pending_smart_video is None:
            self.player_page.set_loading(False)

    def _begin_playback_request(self, target_url: str, *, reason: str, inherit: bool = False) -> int:
        self._playback_request_id += 1
        self._pending_quality_reason = reason
        self._pending_quality_hint = self._quality_hint() if inherit else None
        self._playback_request_context = PlaybackRequestContext(
            self._playback_request_id,
            str(target_url or ""),
            reason,
            self._pending_quality_hint,
        )
        self._pending_smart_video = None
        self._pending_smart_kbps = None
        return self._playback_request_id

    def _quality_hint(self) -> PlaybackQualityHint | None:
        if not self.current_video or not self.current_quality_label:
            return None
        quality = self.current_video.qualities.get(self.current_quality_label)
        if quality is None:
            return None
        return PlaybackQualityHint(quality.label, int(quality.height or 0), int(quality.fps or 0))

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
        worker.signals.finished.connect(self._playlist_load_finished)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _playlist_load_finished(self) -> None:
        self.playlist_page.set_loading(False)

    def load_home(self) -> None:
        self._start_home_load(1)

    def _store_home_state(self, source: str) -> None:
        """把当前站点的首页结果留档，切回来时可以直接复用。"""
        if not source or not self._home_cache:
            return
        self._home_state[source] = (time.time(), list(self._home_cache), self._home_page, self._home_has_next)

    def _take_home_state(self, source: str, page: int = 0) -> tuple[list[HomeVideo], int, bool] | None:
        """取指定站点的首页缓存；过期或页码对不上就返回 None。page<=0 表示不限页码。"""
        cached = self._home_state.get(source)
        if cached is None:
            return None
        cached_at, videos, cached_page, has_next = cached
        if not videos or time.time() - cached_at > HOME_CACHE_TTL_SECONDS:
            self._home_state.pop(source, None)
            return None
        if page > 0 and cached_page != page:
            return None
        return list(videos), cached_page, has_next

    def _render_home(self, videos: list[HomeVideo], page: int, has_next: bool) -> None:
        self.home_page.set_videos(videos, mode="home", page=page, has_next=has_next)
        self.home_page.set_home_context(
            page,
            has_next,
            source_label=self.resolver.home_source_label(self._browse_source),
        )
        self.home_page.set_favorite_ids(self.favorites.favorite_ids())

    def _apply_home_cache(self, videos: list[HomeVideo], page: int, has_next: bool, *, reason: str) -> None:
        logger.info(
            "home cache hit reason=%s source=%s page=%s count=%s",
            reason,
            self._browse_source,
            page,
            len(videos),
        )
        # 命中缓存同样要推进代次：在途的旧请求回来后不能再覆盖当前画面。
        self._browse_generation += 1
        self._browse_mode = "home"
        self._home_cache = list(videos)
        self._home_page = page
        self._home_has_next = has_next
        self.stack.setCurrentWidget(self.home_page)
        self._render_home(self._home_cache, page, has_next)
        self.home_page.set_loading(False)

    def _start_home_load(self, page: int, *, force_refresh: bool = False) -> None:
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
        logger.info("home load requested page=%s source=%s", target_page, source)
        self._home_page = target_page
        self._browse_generation += 1
        generation = self._browse_generation
        self.stack.setCurrentWidget(self.home_page)
        source_label = self.resolver.home_source_label(source)
        self.home_page.set_home_context(self._home_page, False, source_label=source_label)
        self.home_page.set_loading(True, f"正在获取 {source_label} 首页内容（第 {self._home_page} 页），请稍候...")
        worker = HomeWorker(
            self.resolver,
            page=self._home_page,
            page_size=56,
            force_refresh=force_refresh,
            source=source,
        )
        worker.signals.success.connect(partial(self._home_loaded, generation))
        worker.signals.error.connect(partial(self._home_failed, generation))
        worker.signals.finished.connect(partial(self._browse_load_finished, generation))
        self._start_worker(worker)

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
        source = self._browse_source
        logger.info("search requested keyword=%s page=%s source=%s", keyword, page, source)
        self._browse_mode = "search"
        self._browse_generation += 1
        generation = self._browse_generation
        self.stack.setCurrentWidget(self.home_page)
        self.home_page.set_search_context(keyword, page, has_next=False)
        source_label = self.resolver.home_source_label(source)
        self.home_page.set_loading(
            True,
            f"正在搜索 {source_label}：{keyword}（第 {page} 页），请稍候，这一步通常会比首页加载稍慢一些...",
        )
        worker = SearchWorker(
            self.resolver,
            keyword,
            page=page,
            page_size=56,
            force_refresh=force_refresh,
            source=source,
        )
        worker.signals.success.connect(partial(self._search_loaded, generation))
        worker.signals.error.connect(partial(self._search_failed, generation))
        worker.signals.finished.connect(partial(self._browse_load_finished, generation))
        self._start_worker(worker)

    def _set_browse_source(self, source: str) -> None:
        """工具栏切换站点：按上一次的浏览动作，在新站点重做同一件事。

        只影响本次会话的浏览行为，不写回「默认首页」配置项。上一次是搜索、且搜索框里
        还留着关键词时才重搜；上一次是首页浏览就一律回首页——搜索框里的残留文本不算
        搜索意图（文本本身保留，用户随时可以再点搜索）。首页结果按站点留档，切回来能直接复用。
        """
        normalized = SiteResolver.normalize_source(source)
        if not normalized or normalized == self._browse_source:
            return
        # 先把旧站点的首页结果留档，再切换。
        self._store_home_state(self._browse_source)
        self._browse_source = normalized
        logger.info("browse source switched to %s mode=%s", normalized, self._browse_mode)
        self._home_cache = []
        self._home_page = 1
        self._home_has_next = False

        keyword = self.url_edit.text().strip()
        if self._browse_mode == "search" and keyword:
            self._search_keyword = keyword
            self._search_page = 1
            self._start_search(keyword, 1)
            return
        self._search_keyword = ""
        self._search_page = 1
        self.load_home()

    def _browse_load_finished(self, generation: int) -> None:
        # 站点切换后旧 worker 仍会跑完，它的 finished 不该把新一轮的加载态关掉。
        if generation == self._browse_generation:
            self.home_page.set_loading(False)

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

    def _is_stale_browse_result(self, generation: int) -> bool:
        """切换站点后，先前那一轮的结果要丢掉，别把已经换掉的页面又覆盖回去。"""
        if generation == self._browse_generation:
            return False
        logger.info("discard stale browse result generation=%s current=%s", generation, self._browse_generation)
        return True

    @_skip_after_shutdown
    def _home_loaded(self, generation: int, videos: list[HomeVideo], has_next: bool) -> None:
        if self._is_stale_browse_result(generation):
            return
        logger.info("home loaded page=%s count=%s has_next=%s", self._home_page, len(videos), has_next)
        self._home_cache = list(videos)
        self._home_has_next = has_next
        self._store_home_state(self._browse_source)
        self._render_home(self._home_cache, self._home_page, has_next)

    @_skip_after_shutdown
    def _home_failed(self, generation: int, message: str) -> None:
        if self._is_stale_browse_result(generation):
            return
        logger.error("home load failed: %s", message)
        self.home_page.set_error(message)

    @_skip_after_shutdown
    def _search_loaded(self, generation: int, videos: list[HomeVideo], has_next: bool) -> None:
        if self._is_stale_browse_result(generation):
            return
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

    @_skip_after_shutdown
    def _search_failed(self, generation: int, message: str) -> None:
        if self._is_stale_browse_result(generation):
            return
        logger.error("search failed keyword=%s page=%s: %s", self._search_keyword, self._search_page, message)
        self.home_page.set_error(message)

    @_skip_after_shutdown
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

    @_skip_after_shutdown
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
        auto_play_next: bool = False,
        invalidate_creator_request: bool = True,
    ) -> None:
        if invalidate_creator_request:
            self._invalidate_creator_playlist_request()
        self.current_playlist = playlist
        self.current_playlist_index = current_index
        self.current_playlist_key = playlist_key
        self.current_playlist_auto_play = auto_play_next
        # 播放列表页可能尚未构建，此时状态已记在 current_playlist_*，构建时会自动回放。
        page = self._created_page("playlist")
        if page is not None:
            page.set_playlist(playlist, current_index=current_index, auto_play_next=auto_play_next)
        self.player_page.set_playlist_context(playlist, current_index=current_index, auto_play_next=auto_play_next)
        self._refresh_saved_playlists(current_key=playlist_key)

    def _clear_playlist_context(self) -> None:
        self._invalidate_creator_playlist_request()
        self.current_playlist = None
        self.current_playlist_index = -1
        self.current_playlist_key = ""
        self.current_playlist_auto_play = False
        page = self._created_page("playlist")
        if page is not None:
            page.clear_playlist()
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

    # ------------------------------------------------------------------
    # 左侧「合集列表」
    # ------------------------------------------------------------------

    def _activate_collection(
        self,
        playlist: PlaylistInfo,
        *,
        current_index: int = -1,
        collection_key: str = "",
        auto_play_next: bool = False,
    ) -> None:
        self.current_collection = playlist
        self.current_collection_index = current_index
        self.current_collection_key = collection_key
        self.current_collection_auto_play = auto_play_next
        self.player_page.set_collection_context(
            playlist,
            current_index=current_index,
            auto_play_next=auto_play_next,
        )
        self.player_page.set_collection_available(True)
        self._refresh_saved_playlists()

    def _clear_collection_context(self, *, keep_available: bool = False) -> None:
        """清空左侧面板。keep_available=True 用于"探测完成但不属于任何合集"，此时仍允许滑出看空态。"""
        self.current_collection = None
        self.current_collection_index = -1
        self.current_collection_key = ""
        self.current_collection_auto_play = False
        if self._active_queue == "collection":
            self._active_queue = ""
        self.player_page.clear_collection_context()
        self.player_page.set_collection_available(keep_available)

    def _play_collection_index(self, index: int) -> None:
        if self.current_collection is None:
            return
        self._play_collection_entry(self.current_collection, index)

    def _play_collection_entry(self, playlist: PlaylistInfo, index: int, *, arm_window_mode: bool = True) -> None:
        if not (0 <= index < len(playlist.entries)):
            return
        if self._dlna_device is not None or self._dlna_cast_pending:
            self._stop_dlna_cast(resume_local=False, notify=False)
        entry = playlist.entries[index]
        inherit = (
            self._active_queue == "collection"
            and self.current_collection is not None
            and self.current_collection.playlist_id == playlist.playlist_id
        )
        logger.info("collection play requested collection=%s index=%s title=%s", playlist.title, index, entry.title)
        if arm_window_mode:
            self._arm_playback_window_mode()
        self._remember_playback_return_widget()
        self.current_collection = playlist
        self.current_collection_index = index
        self._active_queue = "collection"
        request_id = self._begin_playback_request(
            entry.webpage_url,
            reason="queue_manual" if arm_window_mode else "autoplay",
            inherit=inherit,
        )
        self.player_page.set_collection_current_index(index)
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.set_loading(True, f"正在解析合集第 {index + 1} 条视频，请稍候...")

        worker = ResolverWorker(entry.webpage_url, self.resolver)
        worker.signals.success.connect(lambda video: self._resolved_for_request(request_id, video))
        worker.signals.error.connect(lambda message: self._resolve_failed_for_request(request_id, message))
        worker.signals.finished.connect(lambda: self._url_resolve_finished(request_id))
        self._start_worker(worker)

    def _save_active_collection(self) -> None:
        playlist = self.current_collection
        if playlist is None or not playlist.entries:
            QMessageBox.information(self, "提示", "当前没有可保存的合集。")
            return
        default_name = playlist.title or "我的合集"
        name, ok = QInputDialog.getText(self, "保存合集", "请输入合集名称：", text=default_name)
        if not ok:
            return
        collection_name = str(name or "").strip()
        if not collection_name:
            QMessageBox.information(self, "提示", "合集名称不能为空。")
            return
        # source_type 固定写 "collection"：左侧下拉只列这一类，混进播放列表会互相干扰。
        collection_key = self.playlists.save_playlist(
            name=collection_name,
            entries=playlist.entries,
            source_url=playlist.webpage_url,
            source_type="collection",
            auto_play_next=self.current_collection_auto_play,
            playlist_key=self.current_collection_key or None,
        )
        self.current_collection_key = collection_key
        self._refresh_saved_playlists()
        self.toast.show_message(f"已保存合集：{collection_name}")

    def _load_saved_collection_from_overlay(self, playlist_key: str) -> None:
        """左侧加载已保存合集：只换左侧内容，不跳页、不打断当前播放。"""
        saved = self.playlists.get_playlist(playlist_key)
        if saved is None:
            QMessageBox.warning(self, "提示", "没有找到对应的已保存合集。")
            self._refresh_saved_playlists()
            return
        playlist = self._saved_to_playlist(saved)
        current_video_id = self.current_video.video_id if self.current_video else ""
        self._activate_collection(
            playlist,
            current_index=self._find_playlist_index(playlist, current_video_id),
            collection_key=saved.playlist_key,
            auto_play_next=saved.auto_play_next,
        )
        self.toast.show_message(f"已加载合集：{saved.name}")

    def _set_collection_auto_play(self, enabled: bool) -> None:
        self.current_collection_auto_play = bool(enabled)
        if self.current_collection is not None:
            self.player_page.set_collection_context(
                self.current_collection,
                current_index=self.current_collection_index,
                auto_play_next=self.current_collection_auto_play,
            )
        if self.current_collection_key:
            self.playlists.set_auto_play_next(self.current_collection_key, self.current_collection_auto_play)
            self._refresh_saved_playlists()

    def _schedule_collection_probe(self, video: VideoInfo) -> None:
        """解析成功后探测当前视频所属合集。晚一点发起，先把播放跑起来。"""
        self._collection_generation += 1
        generation = self._collection_generation
        video_id = video.video_id
        index = self._find_playlist_index(self.current_collection, video_id) if self.current_collection else -1
        if index >= 0:
            # 还在同一个合集里换集：只挪高亮，不要把面板收回去闪一下。
            self.current_collection_index = index
            self.player_page.set_collection_current_index(index)
        else:
            self._clear_collection_context()
        if self.resolver.normalize_source(video.source_site) == "":
            return
        QTimer.singleShot(
            600,
            lambda: self._start_collection_worker(generation, video_id, video),
        )

    def _start_collection_worker(self, generation: int, video_id: str, video: VideoInfo) -> None:
        if not self._is_collection_request_current(generation, video_id):
            logger.debug("collection start ignored as stale generation=%s video=%s", generation, video_id)
            return
        worker = CollectionWorker(self.resolver, video, generation=generation)
        worker.signals.success.connect(self._collection_loaded)
        worker.signals.error.connect(self._collection_failed)
        worker.signals.finished.connect(self._collection_worker_finished)
        self._collection_workers[(generation, video_id)] = worker
        self._start_worker(worker, -1)

    @Slot(int, str, object)
    @_skip_after_shutdown
    def _collection_loaded(self, generation: int, video_id: str, playlist: PlaylistInfo | None) -> None:
        if not self._is_collection_request_current(generation, video_id):
            logger.debug("collection result ignored as stale generation=%s video=%s", generation, video_id)
            return
        if playlist is None or not playlist.entries:
            # 「不属于任何合集」是常态，只记日志，不打扰用户。
            logger.debug("collection not found video=%s", video_id)
            self._clear_collection_context(keep_available=True)
            return
        current_index = self._find_playlist_index(playlist, video_id)
        if current_index < 0:
            current_index = max(0, self._find_playlist_index(playlist, playlist.current_video_id))
        auto_play_next = self.current_collection_auto_play
        collection_key = ""
        saved = self._find_saved_collection(playlist.webpage_url)
        if saved is not None:
            collection_key = saved.playlist_key
            auto_play_next = saved.auto_play_next
        self._activate_collection(
            playlist,
            current_index=current_index,
            collection_key=collection_key,
            auto_play_next=auto_play_next,
        )
        logger.info("collection applied video=%s count=%s", video_id, len(playlist.entries))

    @Slot(int, str, str)
    @_skip_after_shutdown
    def _collection_failed(self, generation: int, video_id: str, message: str) -> None:
        if not self._is_collection_request_current(generation, video_id):
            return
        # 探测失败不影响播放，也不弹提示：左侧空着即可。
        logger.warning("collection probe failed video=%s: %s", video_id, message)
        self._clear_collection_context(keep_available=True)

    @Slot(int, str)
    @_skip_after_shutdown
    def _collection_worker_finished(self, generation: int, video_id: str) -> None:
        self._collection_workers.pop((generation, video_id), None)

    def _is_collection_request_current(self, generation: int, video_id: str) -> bool:
        return (
            generation == self._collection_generation
            and self.current_video is not None
            and self.current_video.video_id == video_id
        )

    def _find_saved_collection(self, source_url: str) -> SavedPlaylist | None:
        """按来源地址找已保存的合集，用来复用同一条记录而不是每次另存一份。"""
        url = str(source_url or "").strip()
        if not url:
            return None
        for saved in self.playlists.all_playlists():
            if str(saved.source_type or "") == "collection" and str(saved.source_url or "") == url:
                return saved
        return None

    def _play_playlist_entry(self, playlist: PlaylistInfo, index: int, *, arm_window_mode: bool = True) -> None:
        if not (0 <= index < len(playlist.entries)):
            return
        if self._dlna_device is not None or self._dlna_cast_pending:
            self._stop_dlna_cast(resume_local=False, notify=False)
        entry = playlist.entries[index]
        inherit = (
            self._active_queue == "playlist"
            and self.current_playlist is not None
            and self.current_playlist.playlist_id == playlist.playlist_id
        )
        logger.info("playlist play requested playlist=%s index=%s title=%s", playlist.title, index, entry.title)
        if arm_window_mode:
            self._arm_playback_window_mode()
        self._remember_playback_return_widget()
        self.current_playlist = playlist
        self.current_playlist_index = index
        self._active_queue = "playlist"
        request_id = self._begin_playback_request(
            entry.webpage_url,
            reason="queue_manual" if arm_window_mode else "autoplay",
            inherit=inherit,
        )
        page = self._created_page("playlist")
        if page is not None:
            page.set_current_index(index)
        self.player_page.set_playlist_current_index(index)
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.set_loading(True, f"正在解析播放列表第 {index + 1} 条视频，请稍候...")

        worker = ResolverWorker(entry.webpage_url, self.resolver)
        worker.signals.success.connect(lambda video: self._resolved_for_request(request_id, video))
        worker.signals.error.connect(lambda message: self._resolve_failed_for_request(request_id, message))
        worker.signals.finished.connect(lambda: self._url_resolve_finished(request_id))
        self._start_worker(worker)

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

    def _load_saved_playlist(self, playlist_key: str, *, switch_page: bool = True) -> bool:
        saved = self.playlists.get_playlist(playlist_key)
        if saved is None:
            QMessageBox.warning(self, "提示", "没有找到对应的已保存播放列表。")
            self._refresh_saved_playlists()
            return False
        playlist = self._saved_to_playlist(saved)
        self._activate_playlist(
            playlist,
            current_index=0 if playlist.entries else -1,
            playlist_key=saved.playlist_key,
            auto_play_next=saved.auto_play_next,
        )
        if switch_page:
            self.stack.setCurrentWidget(self.playlist_page)
        return True

    def _load_saved_playlist_from_overlay(self, playlist_key: str) -> None:
        """浮层加载不跳页：只换浮层内容，不打断当前播放。"""
        if not self._load_saved_playlist(playlist_key, switch_page=False):
            return
        name = self.current_playlist.title if self.current_playlist else ""
        self.toast.show_message(f"已加载播放列表：{name}" if name else "已加载播放列表")

    def _delete_saved_playlist(self, playlist_key: str) -> None:
        saved = self.playlists.get_playlist(playlist_key)
        if saved is None:
            self._refresh_saved_playlists()
            return
        # 播放列表与合集共用同一份保存记录，提示文案就不写死"播放列表"了。
        answer = QMessageBox.question(self, "删除已保存列表", f"确定删除“{saved.name}”吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.playlists.delete_playlist(playlist_key)
        if self.current_playlist_key == playlist_key:
            self.current_playlist_key = ""
        if self.current_collection_key == playlist_key:
            self.current_collection_key = ""
        self._refresh_saved_playlists()
        self.toast.show_message(f"已删除：{saved.name}")

    def _set_playlist_auto_play(self, enabled: bool) -> None:
        self.current_playlist_auto_play = bool(enabled)
        if self.current_playlist:
            page = self._created_page("playlist")
            if page is not None:
                page.set_playlist(
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
        page = self._created_page("playlist")
        if page is not None:
            page.set_saved_playlists(playlists, selected_key)
        self.player_page.set_playlist_saved_items(playlists, selected_key)
        # 左侧下拉只列合集：把播放列表也塞进去，两个面板就成了同一份内容的两个入口。
        collections = [saved for saved in playlists if str(saved.source_type or "") == "collection"]
        self.player_page.set_collection_saved_items(collections, self.current_collection_key)

    def _saved_to_playlist(self, saved: SavedPlaylist) -> PlaylistInfo:
        sections_by_id: dict[str, PlaylistSection] = {}
        for entry in saved.entries:
            section_id = str(getattr(entry, "section_id", "") or "")
            if not section_id:
                continue
            section = sections_by_id.get(section_id)
            if section is None:
                section = PlaylistSection(
                    section_id=section_id,
                    title=str(getattr(entry, "section_title", "") or ""),
                    position=int(getattr(entry, "section_position", 0) or 0),
                    thumbnail=str(getattr(entry, "section_thumbnail", "") or entry.thumbnail),
                )
                sections_by_id[section_id] = section
            section.entries.append(entry)
        sections = sorted(sections_by_id.values(), key=lambda section: section.position)
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
            sections=sections if len(sections) > 1 or any(section.title for section in sections) else [],
        )

    @staticmethod
    def _find_playlist_index(playlist: PlaylistInfo, video_id: str) -> int:
        if not video_id:
            return -1
        for index, entry in enumerate(playlist.entries):
            if entry.video_id == video_id:
                return index
        return -1

    @_skip_after_shutdown
    def _resolved_for_request(self, request_id: int, video: VideoInfo) -> None:
        if request_id != self._playback_request_id:
            logger.info("ignoring stale resolve result request=%s current=%s", request_id, self._playback_request_id)
            return
        self._resolved(video)

    @_skip_after_shutdown
    def _resolved(self, video: VideoInfo) -> None:
        self.current_video = video
        self.current_local_media_path = ""
        pending_url = getattr(self, "_pending_recent_url", "")
        if pending_url and video.title:
            self.config.update_recent_url_title(pending_url, video.title)
            self.config.save()
        self._pending_recent_url = ""
        quality = self._select_default_quality(video)
        if quality is None:
            if self._pending_smart_video is video:
                return
            logger.error(
                "video resolved without playable quality id=%s title=%s",
                video.video_id,
                video.title,
            )
            self.current_quality_label = ""
            self.current_audio_track_id = ""
            self._pending_playback_fullscreen = False
            self.player_page.set_loading(False)
            self.player_page.set_playback_available(False)
            self.player_page.set_cast_available(False)
            QMessageBox.critical(
                self,
                "解析失败",
                "该视频没有可播放的清晰度。\n\n"
                "可能原因：视频为付费/会员内容、直播尚未开始、地区或年龄限制、"
                "yt-dlp 版本过旧或 Cookie 已失效。\n"
                "详细日志已写入运行目录下的 logs/app.log 和 logs/yt-dlp.log。",
            )
            return
        self.current_quality_label = quality.label
        self._pending_quality_hint = None
        self._pending_quality_reason = "direct"
        self.current_audio_track_id = self._select_default_audio_track(video)
        logger.info(
            "video resolved id=%s title=%s selected_quality=%s qualities=%s subtitles=%s",
            video.video_id,
            video.title,
            quality.label,
            list(video.qualities.keys()),
            len(video.subtitles),
        )
        if not video.subtitles:
            # 站点没有字幕轨是常态（尤其是中文自媒体视频），只留一条日志，不弹提示骚扰用户；
            # 界面上由「无可用字幕」下拉项负责解释。
            logger.info(
                "no subtitle tracks available site=%s id=%s",
                video.source_site,
                video.video_id,
            )
        self.player_page.update_video_info(video, quality.label)
        self.player_page.set_favorite_state(self.favorites.is_favorite(video.video_id), available=True)
        self._sync_current_download_state()
        if self.current_playlist:
            self.player_page.set_playlist_context(
                self.current_playlist,
                current_index=self.current_playlist_index,
                auto_play_next=self.current_playlist_auto_play,
            )
            playlist_page = self._created_page("playlist")
            if playlist_page is not None:
                playlist_page.set_current_index(self.current_playlist_index)

        try:
            video_url, audio_url = self._current_stream_urls(quality)
            self.mpv.load(video_url, audio_url, headers=video.http_headers)
            self._set_playback_finished(False)
            self.player_page.set_loading(False)
            self.player_page.set_playback_available(True)
            self.player_page.set_cast_available(True)
            self.player_page.set_paused(False)
            self.history.record_play(video)
            # 历史页未构建时，其构造函数会读取最新数据，无需在这里刷新。
            history_page = self._created_page("history")
            if history_page is not None:
                history_page.refresh()
            if self.current_playlist is None:
                self._schedule_creator_playlist(video)
            self._schedule_collection_probe(video)
            self._apply_playback_window_mode()
        except Exception as exc:
            self._pending_playback_fullscreen = False
            logger.exception("playback load failed")
            QMessageBox.critical(self, "播放失败", str(exc))

    @_skip_after_shutdown
    def _resolve_failed(self, message: str) -> None:
        self._pending_playback_fullscreen = False
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

    @_skip_after_shutdown
    def _resolve_failed_for_request(self, request_id: int, message: str) -> None:
        if request_id != self._playback_request_id:
            logger.info("ignoring stale resolve error request=%s current=%s", request_id, self._playback_request_id)
            return
        self._resolve_failed(message)

    def _select_default_quality(self, video: VideoInfo) -> VideoQuality | None:
        inherited = select_quality_by_hint(video.qualities, getattr(self, "_pending_quality_hint", None))
        if inherited is not None:
            return inherited
        site = str(video.source_site or "youtube").lower()
        resolver = getattr(self, "resolver", None)
        if resolver is not None:
            site = resolver.normalize_source(site) or self.config.cookie_site_for_url(video.webpage_url)
        try:
            override = self.config.default_quality_label_override(site)
        except TypeError:
            override = self.config.default_quality_label_override()
        if override and override in video.qualities:
            return video.qualities[override]
        mode_getter = getattr(self.config, "default_quality_mode", None)
        if mode_getter is not None:
            mode = mode_getter(site)
        else:
            try:
                mode = self.config.default_quality_tier(site)
            except TypeError:
                mode = self.config.default_quality_tier()
        if mode != "smart":
            return select_quality_by_tier(video.qualities, mode)
        if self._pending_smart_kbps is not None:
            kbps = self._pending_smart_kbps
            self._pending_smart_kbps = None
            if kbps < 0:
                return select_quality_by_tier(video.qualities, "medium")
            return select_quality_for_bandwidth(video.qualities, kbps)
        candidate = max(
            video.qualities.values(),
            key=lambda quality: (int(quality.height or 0), int(quality.fps or 0), float(quality.tbr or 0)),
            default=None,
        )
        if candidate is None:
            return None
        _proxy_label, proxy = self.config.effective_proxy()
        cached = self._network_measurements.get(site, candidate.video_url, proxy)
        if cached is not None:
            return select_quality_for_bandwidth(video.qualities, cached.kbps)
        self._start_network_probe(video, candidate.video_url, site, proxy)
        return None

    def _start_network_probe(self, video: VideoInfo, url: str, site: str, proxy: str) -> None:
        request_id = self._playback_request_id
        self._pending_smart_video = video
        self.player_page.set_loading(True, "正在评估网络并选择画质...")
        worker = NetworkProbeWorker(url, video.http_headers, proxy)
        worker.signals.success.connect(
            lambda kbps: self._network_probe_succeeded(request_id, video, url, site, proxy, kbps)
        )
        worker.signals.error.connect(lambda message: self._network_probe_failed(request_id, video, message))
        self._start_worker(worker)

    @_skip_after_shutdown
    def _network_probe_succeeded(
        self,
        request_id: int,
        video: VideoInfo,
        url: str,
        site: str,
        proxy: str,
        kbps: float,
    ) -> None:
        if request_id != self._playback_request_id or self._pending_smart_video is not video:
            return
        self._network_measurements.put(
            NetworkMeasurement(site, urlparse(url).hostname or "", proxy, float(kbps), time.monotonic()),
            url,
        )
        self._pending_smart_video = None
        self._pending_smart_kbps = float(kbps)
        logger.info("network quality measured site=%s host=%s kbps=%.0f", site, urlparse(url).hostname or "", kbps)
        self._resolved(video)

    @_skip_after_shutdown
    def _network_probe_failed(self, request_id: int, video: VideoInfo, message: str) -> None:
        if request_id != self._playback_request_id or self._pending_smart_video is not video:
            return
        logger.warning("network quality probe failed site=%s: %s", video.source_site, message)
        self._pending_smart_video = None
        self._pending_smart_kbps = -1.0
        self._resolved(video)

    def _select_default_audio_track(self, video: VideoInfo) -> str:
        """按 D 裁定挑默认音轨，返回 track_id；没有可选音轨时返回空串。

        `select_audio_tracks()` 已按同一条链排好序（配置 → 系统语言 → 站点默认 →
        原声 → 第一条），这里取首条即可；配置里指定的语言在解析层就已参与排序。
        """
        tracks = getattr(video, "audio_tracks", None) or {}
        return next(iter(tracks), "")

    def _current_stream_urls(self, quality: VideoQuality) -> tuple[str, str | None]:
        """把"所选音轨"折算成 (video_url, audio_url)，三处 mpv.load() 共用。

        这是"切清晰度不丢音轨"的实现点：清晰度换了，音轨仍按 current_audio_track_id 挂。
        """
        if self.current_audio_track_id == MUXED_AUDIO_TRACK_ID:
            # 「随画面（免转码）」：回到已混音单流，语言由站点决定（C1）。
            muxed_url = getattr(quality, "muxed_video_url", None)
            if muxed_url:
                return muxed_url, None
        track = self._current_audio_track()
        # 本档位自带音频（muxed）时不外挂音轨，否则会出现两条音频。
        if track and quality.audio_url:
            return quality.video_url, track.url
        return quality.video_url, quality.audio_url

    def _current_audio_track(self) -> AudioTrack | None:
        if not self.current_video or not self.current_audio_track_id:
            return None
        tracks = getattr(self.current_video, "audio_tracks", None) or {}
        return tracks.get(self.current_audio_track_id)

    def _cast_audio_codec(self, quality: VideoQuality) -> str:
        """投屏混流时上报的音轨编码：跟随所选音轨，而非清晰度自带的默认轨。"""
        track = self._current_audio_track()
        return track.acodec if track else quality.acodec

    def _change_audio_track(self, track_id: str) -> None:
        if not self.current_video or track_id == self.current_audio_track_id:
            return
        quality = self.current_video.qualities.get(self.current_quality_label)
        if not quality:
            return
        tracks = getattr(self.current_video, "audio_tracks", None) or {}
        if track_id and track_id != MUXED_AUDIO_TRACK_ID and track_id not in tracks:
            return

        previous = self.current_audio_track_id
        playback_finished = self._playback_finished
        position = 0.0 if playback_finished else self.mpv.position()
        autoplay = playback_finished or not self.mpv.get_bool("pause")
        # 先落状态再算地址：_current_stream_urls 读的就是它。失败时回滚。
        self.current_audio_track_id = track_id
        video_url, audio_url = self._current_stream_urls(quality)
        try:
            self.mpv.load(
                video_url,
                audio_url,
                start_position=position,
                headers=self.current_video.http_headers,
                autoplay=autoplay,
            )
            self._set_playback_finished(False)
        except Exception as exc:
            self.current_audio_track_id = previous
            logger.exception("audio track switch failed track_id=%s", track_id)
            QMessageBox.critical(self, "切换音轨失败", str(exc))

    def _change_quality(self, label: str) -> None:
        if not self.current_video or label == self.current_quality_label:
            return
        quality = self.current_video.qualities.get(label)
        if not quality:
            return

        playback_finished = self._playback_finished
        position = 0.0 if playback_finished else self.mpv.position()
        autoplay = playback_finished or not self.mpv.get_bool("pause")
        video_url, audio_url = self._current_stream_urls(quality)
        try:
            self.mpv.load(
                video_url,
                audio_url,
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
        # 迟到的旧字幕请求不许改动当前轨道。
        self._subtitle_request_id += 1
        if not key:
            self.mpv.clear_subtitles()
            return
        subtitle = self.current_video.subtitles.get(key)
        if subtitle is None:
            logger.warning("subtitle key not found key=%s", key)
            return
        if not subtitle.is_usable:
            self.toast.show_message(f"{subtitle.display_language} 字幕没有可用内容")
            return

        _source, proxy = self.config.effective_proxy()
        worker = SubtitleLoadWorker(
            self._subtitle_request_id,
            key,
            subtitle,
            self.current_video.video_id,
            proxy=proxy,
            headers=dict(self.current_video.http_headers),
        )
        worker.signals.success.connect(self._subtitle_ready)
        worker.signals.error.connect(self._subtitle_failed)
        logger.info(
            "subtitle load requested key=%s language=%s ext=%s inline=%s",
            key,
            subtitle.language,
            subtitle.ext,
            bool(subtitle.data),
        )
        self._start_worker(worker)

    @Slot(int, str, str)
    @_skip_after_shutdown
    def _subtitle_ready(self, request_id: int, key: str, path: str) -> None:
        if request_id != self._subtitle_request_id:
            logger.info("stale subtitle result ignored key=%s", key)
            return
        try:
            self.mpv.add_subtitle(path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("subtitle apply failed key=%s path=%s", key, path)
            QMessageBox.warning(self, "字幕加载失败", str(exc))

    @Slot(int, str, str)
    @_skip_after_shutdown
    def _subtitle_failed(self, request_id: int, key: str, message: str) -> None:
        if request_id != self._subtitle_request_id:
            return
        # 限流类提示是两三句话的处置建议（改选原文字幕、稍后再试），3 秒读不完。
        timeout = 8000 if len(message) > 40 else 3000
        self.toast.show_message(f"字幕加载失败：{message}", timeout_ms=timeout)

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
        # 下载跟随下拉里选中的那条音轨；「随画面（免转码）」不是真音轨，
        # _current_audio_track() 对它返回 None，于是退回清晰度自带的默认轨。
        track = self._current_audio_track()
        self._enqueue_download(
            self.current_video,
            self.current_quality_label,
            track.track_id if track else "",
        )

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

    def _enqueue_download(
        self, video: VideoInfo, quality_label: str, audio_format_id: str = ""
    ) -> None:
        try:
            task = self.download_manager.enqueue(video, quality_label, audio_format_id)
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

    def _remove_favorites(self, video_ids: list) -> None:
        """收藏页批量删除：一条语句删完再统一刷新视图。"""
        ids = list(
            dict.fromkeys(
                str(video_id or "").strip()
                for video_id in list(video_ids or [])
                if str(video_id or "").strip()
            )
        )
        if not ids:
            return
        try:
            removed = self.favorites.remove_many(ids)
        except Exception:
            logger.exception("favorite batch remove failed count=%s", len(ids))
            self.toast.show_message("批量删除收藏失败")
            return
        self._refresh_favorite_views()
        if self.current_video and self.current_video.video_id in set(ids):
            self.player_page.set_favorite_state(False, available=True)
        self.toast.show_message(f"已从收藏中移除 {removed} 条")

    def _download_favorite_records(self, records: list) -> None:
        """收藏页批量下载。收藏表里没有清晰度信息，统一按 Auto 入队。"""
        _enqueue_library_records(
            self,
            records,
            empty_message="下载失败：收藏记录里没有可用的视频地址",
            log_name="favorite",
        )

    def _download_history_records(self, records: list) -> None:
        """历史页批量下载；历史记录没有清晰度信息，统一按 Auto 入队。"""
        _enqueue_library_records(
            self,
            records,
            empty_message="下载失败：播放历史里没有可用的视频地址",
            log_name="history",
        )

    def _remove_history_record(self, video_id: str) -> None:
        clean_id = str(video_id or "").strip()
        if not clean_id:
            return
        try:
            removed = self.history.remove(clean_id)
        except Exception:
            logger.exception("history remove failed video_id=%s", clean_id)
            self.toast.show_message("删除播放历史失败")
            return
        page = self._created_page("history")
        if page is not None:
            page.refresh()
        self.toast.show_message(f"已从播放历史中移除 {removed} 条")

    def _remove_history_records(self, video_ids: list) -> None:
        ids = [str(video_id or "").strip() for video_id in list(video_ids or [])]
        ids = [video_id for video_id in ids if video_id]
        if not ids:
            return
        try:
            removed = self.history.remove_many(ids)
        except Exception:
            logger.exception("history batch remove failed count=%s", len(ids))
            self.toast.show_message("批量删除播放历史失败")
            return
        page = self._created_page("history")
        if page is not None:
            page.refresh()
        self.toast.show_message(f"已从播放历史中移除 {removed} 条")

    def _refresh_favorite_views(self) -> None:
        favorite_ids = self.favorites.favorite_ids()
        self.home_page.set_favorite_ids(favorite_ids)
        page = self._created_page("favorite")
        if page is not None:
            page.refresh()
        if self.current_video:
            self.player_page.set_favorite_state(self.current_video.video_id in favorite_ids, available=True)

    def _sync_current_download_state(self) -> None:
        """把当前播放视频的下载任务状态同步给播放页。"""
        video = self.current_video
        if video is None:
            self.player_page.set_download_state("")
            return
        task = self.download_manager.task_for_video(url=video.webpage_url, video_id=video.video_id)
        if task is None:
            self.player_page.set_download_state("")
        else:
            self.player_page.set_download_state(task.status, task.progress)

    def _sync_download_state_from_task(self, task: DownloadTask) -> None:
        """只有当变化的任务正是当前播放的视频时才刷新，避免下载队列刷屏拖累播放页。"""
        video = self.current_video
        if video is None:
            return
        same_url = bool(task.url) and task.url == video.webpage_url
        same_id = bool(task.video_id) and task.video_id == video.video_id
        if same_url or same_id:
            self.player_page.set_download_state(task.status, task.progress)

    def _handle_download_task_removed(self, _task_id: str) -> None:
        self._sync_current_download_state()

    def play_local_file(self, path: str) -> None:
        logger.info("play local file requested: %s", path)
        if self._dlna_device is not None or self._dlna_cast_pending:
            self._stop_dlna_cast(resume_local=False, notify=False)
        self._remember_playback_return_widget()
        self.current_video = None
        self.current_local_media_path = str(Path(path).resolve())
        self.current_quality_label = ""
        self.current_audio_track_id = ""
        self._clear_playlist_context()
        self._clear_collection_context()
        self._arm_playback_window_mode()
        self.stack.setCurrentWidget(self.player_page)
        try:
            self.mpv.load(path)
            self._set_playback_finished(False)
            self.player_page.update_local_file_info(path)
            self.player_page.set_playback_available(True)
            self.player_page.set_cast_available(True)
            self.player_page.set_paused(False)
            self.player_page.set_download_available(False)
            self.player_page.set_download_state("")
            self._apply_playback_window_mode()
        except Exception as exc:
            self._pending_playback_fullscreen = False
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
        # 谁最后驱动了当前播放，谁优先负责连播；另一侧作为兜底。
        first = self._advance_collection_queue if self._active_queue == "collection" else self._advance_playlist_queue
        second = self._advance_playlist_queue if self._active_queue == "collection" else self._advance_collection_queue
        if first() or second():
            return
        logger.info("playback reached end; waiting for replay")
        self._set_playback_finished(True)

    def _advance_playlist_queue(self) -> bool:
        if self.current_playlist is None or not self.current_playlist_auto_play:
            return False
        next_index = self.current_playlist_index + 1
        if next_index >= len(self.current_playlist.entries):
            return False
        logger.info("playlist autoplay next index=%s", next_index)
        # 连播是同一次播放会话的延续，不重新套用"进入播放"的窗口模式：
        # 用户中途退出全屏后，不该被下一集又拽回全屏。
        self._play_playlist_entry(self.current_playlist, next_index, arm_window_mode=False)
        return True

    def _advance_collection_queue(self) -> bool:
        if self.current_collection is None or not self.current_collection_auto_play:
            return False
        next_index = self.current_collection_index + 1
        if 0 <= self.current_collection_index < len(self.current_collection.entries):
            current_entry = self.current_collection.entries[self.current_collection_index]
            section_id = str(getattr(current_entry, "section_id", "") or "")
            if section_id and next_index < len(self.current_collection.entries):
                next_section = str(getattr(self.current_collection.entries[next_index], "section_id", "") or "")
                if next_section != section_id:
                    return False
        if next_index >= len(self.current_collection.entries):
            return False
        logger.info("collection autoplay next index=%s", next_index)
        self._play_collection_entry(self.current_collection, next_index, arm_window_mode=False)
        return True

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

    def _casting_to_dlna(self) -> bool:
        """投屏中（含正在连接设备的窗口期）。"""
        return self._dlna_device is not None or self._dlna_cast_pending

    def _handle_mpv_position_changed(self, seconds: float) -> None:
        # 投屏期间本地 mpv 只是被 pause，属性轮询定时器仍在跑并持续上报被冻结的
        # 本地位置；面板此时由 _poll_dlna_position 单独驱动，两条链路都写
        # update_position 会让进度条在「投屏起始点」与「远端真实位置」之间反复跳。
        if self._casting_to_dlna():
            return
        self.player_page.update_position(seconds)

    def _handle_mpv_duration_changed(self, seconds: float) -> None:
        if self._casting_to_dlna():
            return
        self.player_page.update_duration(seconds)

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
        # 投屏跟随当前所选音轨（C1）：选了「随画面」就退回单流免转码，
        # 选了具体语言则仍走 FFmpeg 混流，播的语言与本地一致。
        cast_video_url, cast_audio_url = self._current_stream_urls(quality)
        ffmpeg_path = ""
        if cast_audio_url:
            ffmpeg_dir = self.ffmpeg_install_service.effective_ffmpeg_dir()
            if ffmpeg_dir:
                executable = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
                candidate = Path(ffmpeg_dir) / executable
                if candidate.is_file():
                    ffmpeg_path = str(candidate)
            if not ffmpeg_path:
                # C1：本档位有已混音变体时，"随画面（免转码）"就是不装 FFmpeg 也能投的出路，
                # 提示里点名它；没有该变体时（如 1440p 只有纯视频轨）仍是老文案。
                if getattr(quality, "muxed_video_url", None):
                    self.toast.show_message(
                        "当前音轨需要 FFmpeg 混流才能投屏；"
                        "也可把音轨切到「随画面（免转码）」直接投，代价是语言由站点决定"
                    )
                else:
                    self.toast.show_message("当前视频为分离音视频流，投屏前需要在设置中配置 FFmpeg")
                return

        device = self._select_dlna_device(video.title)
        if device is None:
            return

        position = self.mpv.position()
        _, proxy = self.config.effective_proxy()
        source = DlnaMediaSource(
            title=video.title,
            video_url=cast_video_url,
            audio_url=cast_audio_url,
            headers=dict(video.http_headers),
            mime_type=mime_type_for_extension(quality.ext),
            video_codec=quality.vcodec,
            audio_codec=self._cast_audio_codec(quality),
            ffmpeg_path=ffmpeg_path,
            proxy=proxy,
            start_position=position if cast_audio_url else 0.0,
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
        metadata = build_didl_lite(
            video.title,
            media_url,
            source.output_mime_type,
            duration=float(video.duration or 0),
            seekable=not source.requires_mux,
        )
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

        metadata = build_didl_lite(
            title,
            media_url,
            source.output_mime_type,
            duration=self.mpv.duration(),
            seekable=True,
        )
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
        self._start_worker(worker)
        return request_id

    @Slot(int, str, object)
    @_skip_after_shutdown
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
            # 分离音视频实时封装时渲染器常返回 0 或 NOT_IMPLEMENTED，此时保留本地
            # 解析出的真实时长，不能把总时长刷成 00:00。
            if duration > 0:
                self.player_page.update_duration(duration)
        elif action == "stop" and request_id in self._dlna_stop_notify_requests:
            self._dlna_stop_notify_requests.discard(request_id)
            self.toast.show_message(f"已停止向 {device.friendly_name} 投屏")

    @Slot(int, str, str)
    @_skip_after_shutdown
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
    @_skip_after_shutdown
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
        self._start_worker(worker, -1)

    @Slot(int, str, object)
    @_skip_after_shutdown
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
    @_skip_after_shutdown
    def _creator_playlist_failed(self, generation: int, video_id: str, message: str) -> None:
        if not self._is_creator_playlist_request_current(generation, video_id):
            logger.debug("stale creator playlist failure ignored generation=%s video=%s", generation, video_id)
            return
        logger.warning("creator playlist failed generation=%s video=%s: %s", generation, video_id, message)
        self.toast.show_message("作者视频列表加载失败，当前视频继续播放")

    @Slot(int, str)
    @_skip_after_shutdown
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
        # Cookie/代理等设置可能已经变了，旧首页结果一律作废。
        self._home_state.clear()
        self._search_keyword = ""
        self._search_page = 1
        # 设置保存会重建解析器并作废当前浏览结果；清除搜索上下文时也要同步
        # 重置浏览动作，否则切站点仍可能被工具栏残留文本误判为搜索。
        self._browse_mode = "home"
        self._refresh_runtime_status()
        self._sync_about_page()
        if self.stack.currentWidget() is self.home_page:
            self.load_home()

    def _test_webdav_account(self, account) -> None:
        page = self.settings_page
        page.set_backup_busy(True, "正在测试 WebDAV 连接...")
        worker = WebdavTestWorker(account, self.config)
        worker.signals.progress.connect(page.set_backup_progress)
        worker.signals.success.connect(lambda message: self._backup_operation_succeeded(str(message)))
        worker.signals.error.connect(self._backup_operation_failed)
        self._start_worker(worker)

    def _start_backup_upload(self, account, include_cookies: bool) -> None:
        page = self.settings_page
        page.set_backup_busy(True, "正在创建备份包...")
        self.config.set("backup.include_cookies", bool(include_cookies))
        self.config.save()
        self.download_manager.flush()
        worker = BackupUploadWorker(account, self.config, bool(include_cookies))
        worker.signals.progress.connect(page.set_backup_progress)
        worker.signals.success.connect(self._backup_upload_succeeded)
        worker.signals.error.connect(self._backup_operation_failed)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _backup_upload_succeeded(self, result: dict) -> None:
        page = self.settings_page
        warnings = list(result.get("warnings") or [])
        message = f"备份已上传：{result.get('name', '')}"
        if warnings:
            message += "；" + "；".join(warnings)
        self.config.set("backup.last_backup_at", result.get("created_at", ""))
        self.config.set("backup.last_backup_name", result.get("name", ""))
        self.config.save()
        page.set_backup_busy(False)
        page.report_backup_result(True, message)
        page.backup_tab.refresh_status()

    def _load_remote_backups(self, account) -> None:
        page = self.settings_page
        page.set_backup_busy(True, "正在读取远端备份清单...")
        worker = BackupListWorker(account, self.config)
        worker.signals.progress.connect(page.set_backup_progress)
        worker.signals.success.connect(self._remote_backups_loaded)
        worker.signals.error.connect(self._backup_operation_failed)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _remote_backups_loaded(self, backups: list) -> None:
        page = self.settings_page
        page.set_backup_busy(False)
        page.set_backup_progress("")
        page.show_remote_backups(backups)

    def _start_backup_restore(self, account, remote_name: str, allow_newer: bool = False) -> None:
        page = self.settings_page
        page.set_backup_busy(True, "正在下载备份...")
        self.download_manager.flush()
        worker = BackupRestoreWorker(account, self.config, remote_name, allow_newer=allow_newer)
        worker.signals.progress.connect(page.set_backup_progress)
        worker.signals.success.connect(partial(self._backup_restore_succeeded, account, remote_name))
        worker.signals.error.connect(self._backup_operation_failed)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _backup_restore_succeeded(self, account, remote_name: str, result: dict) -> None:
        if result.get("needs_confirmation"):
            answer = QMessageBox.question(
                self,
                "备份版本较新",
                f"该备份来自版本 {result.get('backup_version')}，当前版本为 {result.get('current_version')}。\n"
                "继续恢复可能出现兼容问题，确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._start_backup_restore(account, remote_name, True)
            else:
                self.settings_page.set_backup_busy(False)
                self.settings_page.set_backup_progress("已取消恢复")
            return

        self.settings_page.set_backup_busy(False)
        self.settings_page.set_backup_progress("已恢复，重启后生效")
        # 当前进程仍持有恢复前的内存配置和任务队列。暂停后续落盘，避免关闭时
        # ConfigService / DownloadManager 把旧状态覆盖回刚恢复的文件。
        self.config.suspend_persistence()
        self.download_manager.suspend_persistence()
        self.settings_page.save_button.setEnabled(False)
        self.settings_page.reload_button.setEnabled(False)
        message = QMessageBox(self)
        message.setWindowTitle("恢复完成")
        message.setText("恢复完成，需要重启应用才能生效。")
        message.setInformativeText(f"恢复前的本地快照：\n{result.get('snapshot', '')}")
        restart_button = message.addButton("立即重启", QMessageBox.ButtonRole.AcceptRole)
        message.addButton("稍后重启", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is restart_button:
            self.close()
            try:
                restart_application()
            except RestartError as exc:
                QMessageBox.critical(None, "自动重启失败", str(exc))
                return
            QCoreApplication.exit(0)

    @_skip_after_shutdown
    def _backup_operation_succeeded(self, message: str) -> None:
        page = self.settings_page
        page.set_backup_busy(False)
        page.report_backup_result(True, message)

    @_skip_after_shutdown
    def _backup_operation_failed(self, message: str) -> None:
        page = self.settings_page
        page.set_backup_busy(False)
        page.report_backup_result(False, message)
        QMessageBox.warning(self, "备份/恢复失败", message)

    def _show_home(self) -> None:
        # 点「首页」即把浏览动作切回首页：之后切换站点不再受搜索框残留文本影响。
        self._browse_mode = "home"
        self.stack.setCurrentWidget(self.home_page)
        if self._home_cache:
            self._render_home(self._home_cache, self._home_page, self._home_has_next)
            return
        cached = self._take_home_state(self._browse_source)
        if cached is not None:
            videos, page, has_next = cached
            self._apply_home_cache(videos, page, has_next, reason="show")
            return
        self.load_home()

    def _remember_playback_return_widget(self, widget: QWidget | None = None) -> None:
        candidate = widget or self.stack.currentWidget()
        # 直接查已构建页面表，避免仅为了比较身份就把懒加载页面建出来。
        if (
            self._playback_return_widget is not None
            and (candidate is self.player_page or candidate is self._lazy_pages.get("playlist"))
        ):
            return
        self._playback_return_widget = candidate

    def _return_after_stop(self) -> None:
        target = self._playback_return_widget
        self._playback_return_widget = None
        if target is None:
            self._show_home()
            return
        if target is self.home_page or target is self.player_page:
            self.stack.setCurrentWidget(target)
            return
        # 能成为返回目标的页面一定已经构建过，因此只在已建页面里查找。
        for name, page in self._lazy_pages.items():
            if page is not target:
                continue
            if name in {"favorite", "history"}:
                page.refresh()
            self.stack.setCurrentWidget(page)
            return
        self._show_home()

    def _show_player_page(self) -> None:
        self.stack.setCurrentWidget(self.player_page)

    def _show_playlist_page(self) -> None:
        self._refresh_saved_playlists()
        self.stack.setCurrentWidget(self.playlist_page)

    def _show_favorites(self) -> None:
        self.favorite_page.refresh()
        self.stack.setCurrentWidget(self.favorite_page)

    def _show_history(self) -> None:
        self.history_page.refresh()
        self.stack.setCurrentWidget(self.history_page)

    def _show_about(self) -> None:
        self.stack.setCurrentWidget(self.about_page)

    def _arm_playback_window_mode(self) -> None:
        """一次播放动作开始时记下是否要自动全屏，等真正出画面再执行。

        解析可能失败，提前全屏会让用户对着一块黑屏找退出键，所以只在这里"上膛"。
        """
        self._pending_playback_fullscreen = self.config.playback_starts_fullscreen()

    def _apply_playback_window_mode(self) -> None:
        """媒体已经加载成功时调用，按配置进入全屏；窗口模式不动当前窗口状态。"""
        if not self._pending_playback_fullscreen:
            return
        self._pending_playback_fullscreen = False
        if self.isFullScreen():
            return
        logger.info("applying playback window mode: fullscreen")
        self._enter_player_fullscreen()

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
        worker.signals.finished.connect(self._update_check_finished)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _update_check_finished(self) -> None:
        self.about_page.set_checking(False)

    @_skip_after_shutdown
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
        if result.platform_info is not None:
            self.about_page.set_platform(result.platform_info.describe())
        self.about_page.set_latest_version(result.latest_version, result.release.published_at)
        self.about_page.set_release_notes(result.release.body)
        self.about_page.set_upgrade_available(result.has_update)
        if result.has_update:
            asset_name = result.selected_asset.name if result.selected_asset else "升级包"
            if result.install_mode.startswith("linux"):
                self.about_page.set_status(f"检测到新版本，可下载 {asset_name}；下载后请手动替换或通过包管理器安装。")
            else:
                self.about_page.set_status(f"检测到新版本，可下载 {asset_name} 进行升级。")
        elif result.arch_mismatch:
            arch = result.platform_info.host_arch if result.platform_info else "本机"
            self.about_page.set_status(
                f"检测到新版本 {result.latest_version}，但该版本没有提供 {arch} 架构的升级包，已停止升级以免装上跑不起来的包。"
                "可前往 GitHub 手动确认。"
            )
        else:
            message = "当前已经是最新版本。"
            if result.selected_asset is None:
                message = "已获取版本信息，但没有找到匹配当前运行形态的升级包。"
            self.about_page.set_status(message)

    @_skip_after_shutdown
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

        worker = UpdateDownloadWorker(
            self.update_service,
            asset.download_url,
            target_path,
            asset.name,
            expected_size=asset.size,
            expected_sha256_resolver=lambda: self.update_service.resolve_expected_sha256(result.release, asset),
            verify_signature=asset.name.lower().endswith(".exe"),
        )
        worker.signals.progress.connect(self._update_download_progress)
        worker.signals.success.connect(self._update_download_success)
        worker.signals.error.connect(self._update_download_failed)
        worker.signals.finished.connect(self._update_download_finished)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _update_download_progress(self, downloaded: int, total: int, percent: float, speed_text: str) -> None:
        if total > 0:
            size_text = f"{_format_bytes(downloaded)} / {_format_bytes(total)}"
        else:
            size_text = _format_bytes(downloaded)
        message = f"正在下载升级包：{size_text}  {speed_text}".strip()
        self.about_page.set_upgrade_progress(True, message, percent)

    @_skip_after_shutdown
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
        QTimer.singleShot(0, self._quit_for_upgrade)

    def _quit_for_upgrade(self) -> None:
        """确保应用进程真正退出：升级脚本要等本进程结束才能替换文件或执行安装包。"""
        logger.info("quitting for upgrade")
        # 兜底：即使事件循环退出后仍有卡住的线程拖住进程，也要在超时后结束进程，
        # 否则升级脚本会一直等待旧进程退出，表现为"安装包没有被执行"。
        _arm_exit_watchdog()
        try:
            self.close()
        except Exception:
            logger.exception("close window before upgrade failed")
        if not self.thread_pool.waitForDone(3000):
            logger.warning("thread pool still busy before upgrade exit")
        application = QApplication.instance()
        if application is not None:
            application.quit()

    @_skip_after_shutdown
    def _update_download_failed(self, message: str) -> None:
        logger.error("update download failed: %s", message)
        self.about_page.set_upgrade_progress(False, f"升级包下载失败：{message}")
        self.about_page.set_status("升级包下载失败。")
        self.about_page.set_upgrade_available(bool(self._last_update_result and self._last_update_result.has_update))
        QMessageBox.warning(self, "下载升级包失败", message)

    @_skip_after_shutdown
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

        worker = UpdateDownloadWorker(
            self.update_service,
            info.url,
            target_path,
            info.filename,
            expected_sha256_resolver=lambda: self.runtime_install_service.fetch_installer_sha256(info),
            trusted_hosts=NODE_TRUSTED_HOSTS,
        )
        worker.signals.progress.connect(self._node_download_progress)
        worker.signals.success.connect(self._node_download_success)
        worker.signals.error.connect(self._node_download_failed)
        worker.signals.finished.connect(self._node_download_finished)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _node_download_finished(self) -> None:
        self.settings_page.set_runtime_install_busy(False)

    @_skip_after_shutdown
    def _node_download_progress(self, downloaded: int, total: int, percent: float, speed_text: str) -> None:
        if total > 0:
            text = f"Node.js 下载中：{_format_bytes(downloaded)} / {_format_bytes(total)}  {speed_text}"
        else:
            text = f"Node.js 下载中：{_format_bytes(downloaded)}  {speed_text}"
        if percent > 0:
            text += f"  ({percent:.1f}%)"
        self.settings_page.set_runtime_install_progress(text)

    @_skip_after_shutdown
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

    @_skip_after_shutdown
    def _node_download_failed(self, message: str) -> None:
        logger.error("node installer download failed: %s", message)
        self.settings_page.set_runtime_install_progress(f"Node.js 下载失败：{message}")
        QMessageBox.warning(
            self,
            "下载 Node.js 失败",
            f"{message}\n\n将为你打开 Node.js 官网下载页。",
        )
        self.runtime_install_service.open_official_site()

    def _start_cookie_probe(self) -> None:
        """启动后异步探测各站点的登录 Cookie 浏览器（仅「自动检测」模式）。"""
        if not self.config.cookie_auto_probe_enabled():
            return
        global _COOKIE_PROBE_INFLIGHT
        with _COOKIE_PROBE_STATE_LOCK:
            if _COOKIE_PROBE_INFLIGHT:
                logger.debug("cookie probe already running")
                return
            _COOKIE_PROBE_INFLIGHT = True
        worker = CookieProbeWorker()
        worker.signals.success.connect(self._cookie_probe_finished)
        worker.signals.error.connect(self._cookie_probe_failed)
        worker.signals.finished.connect(self._cookie_probe_worker_finished)
        # 优先级设低：探测只影响后续请求的 Cookie 选择，不该和首页加载抢线程。
        self._start_worker(worker, -1)

    @staticmethod
    def _cookie_probe_worker_finished() -> None:
        global _COOKIE_PROBE_INFLIGHT
        with _COOKIE_PROBE_STATE_LOCK:
            _COOKIE_PROBE_INFLIGHT = False

    @Slot(object)
    @_skip_after_shutdown
    def _cookie_probe_finished(self, report) -> None:
        result = dict(getattr(report, "matches", None) or {})
        unreadable = list(getattr(report, "unreadable", None) or [])
        self.config.set_probed_cookie_browsers(result)
        self.config.save()
        missing = [site for site in ("bilibili", "youtube") if not result.get(site)]
        settings_page = self._created_page("settings")
        if settings_page is not None:
            settings_page.set_cookie_probe_result(result, missing, unreadable)
        if not missing:
            logger.info("cookie probe matched every site result=%s", result)
            return
        labels = {"bilibili": "Bilibili", "youtube": "YouTube"}
        names = "、".join(labels[site] for site in missing)
        # 提示用 toast 而不是模态框：启动时弹窗打断使用，设置页另有常驻提示。
        if unreadable:
            self.toast.show_message(
                f"未找到 {names} 的登录 Cookie；{len(unreadable)} 个浏览器正在运行导致无法读取，"
                "可关闭浏览器后重新检测"
            )
        else:
            self.toast.show_message(f"未在任何浏览器中找到 {names} 的登录 Cookie，请在设置中手动配置")

    @Slot(str)
    @_skip_after_shutdown
    def _cookie_probe_failed(self, message: str) -> None:
        logger.warning("cookie probe failed: %s", message)

    def _pause_download_tasks(self, task_ids: list) -> None:
        self._report_batch_download("暂停", self.download_manager.pause_tasks(list(task_ids)), len(task_ids))

    def _start_download_tasks(self, task_ids: list) -> None:
        self._report_batch_download("启动", self.download_manager.start_tasks(list(task_ids)), len(task_ids))

    def _delete_download_tasks(self, task_ids: list) -> None:
        self._report_batch_download("删除", self.download_manager.delete_tasks(list(task_ids)), len(task_ids))

    def _report_batch_download(self, action: str, changed: int, requested: int) -> None:
        skipped = max(0, requested - changed)
        message = f"已{action} {changed} 个任务"
        if skipped:
            message += f"，跳过 {skipped} 个"
        self.toast.show_message(message)

    def _reprobe_cookies(self) -> None:
        """设置页手动触发重新探测，不必重启应用。"""
        if not self.config.cookie_auto_probe_enabled():
            self.toast.show_message("请先把「从浏览器读取 Cookie」设为自动检测")
            return
        self.toast.show_message("正在检测各站点的登录 Cookie...")
        self._start_cookie_probe()

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

        worker = UpdateDownloadWorker(
            self.update_service,
            info.url,
            info.archive_path,
            info.archive_path.name,
            expected_size=info.size,
            expected_sha256=info.sha256,
            trusted_hosts=info.trusted_hosts,
        )
        worker.signals.progress.connect(self._ffmpeg_download_progress)
        worker.signals.success.connect(self._ffmpeg_download_success)
        worker.signals.error.connect(self._ffmpeg_download_failed)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _ffmpeg_download_progress(self, downloaded: int, total: int, percent: float, speed_text: str) -> None:
        if total > 0:
            message = f"正在下载 FFmpeg：{_format_bytes(downloaded)} / {_format_bytes(total)}  {speed_text}"
            self._show_ffmpeg_progress(message, percent)
        else:
            message = f"正在下载 FFmpeg：{_format_bytes(downloaded)}  {speed_text}"
            self._show_ffmpeg_progress(message, 0.0, indeterminate=True)

    @_skip_after_shutdown
    def _ffmpeg_download_success(self, path: str) -> None:
        logger.info("ffmpeg archive downloaded path=%s", path)
        info = self._pending_ffmpeg_info or self.ffmpeg_install_service.install_info()
        self._show_ffmpeg_progress("FFmpeg 下载完成，正在解压...", 0.0, indeterminate=True)

        worker = ArchiveExtractWorker(Path(path), info.extract_dir, required_files=("ffmpeg.exe",))
        worker.signals.success.connect(self._ffmpeg_extract_success)
        worker.signals.error.connect(self._ffmpeg_extract_failed)
        self._start_worker(worker)

    @_skip_after_shutdown
    def _ffmpeg_download_failed(self, message: str) -> None:
        logger.error("ffmpeg download failed: %s", message)
        self._close_ffmpeg_progress()
        QMessageBox.warning(self, "FFmpeg 下载失败", message)

    @_skip_after_shutdown
    def _ffmpeg_extract_success(self, _extract_dir: str) -> None:
        ffmpeg_dir = self.ffmpeg_install_service.locate_extracted_ffmpeg_dir()
        if not ffmpeg_dir:
            self._close_ffmpeg_progress()
            QMessageBox.warning(self, "FFmpeg 安装失败", "已解压 FFmpeg，但没有找到 FFmpeg 可执行文件。")
            return

        self.config.set("download.ffmpeg_dir", ffmpeg_dir)
        self.config.save()
        # 设置页未构建时，构造函数会从配置里读到新目录，这里只同步已打开的页面。
        settings_page = self._created_page("settings")
        if settings_page is not None:
            settings_page.ffmpeg_dir_edit.setText(ffmpeg_dir)
        self.download_manager.reload_settings()
        self._close_ffmpeg_progress()
        QMessageBox.information(self, "FFmpeg 已安装", f"FFmpeg 已安装并写入设置：\n{ffmpeg_dir}")

    @_skip_after_shutdown
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
            self._shutting_down = True
            self._invalidate_creator_playlist_request()
            self._dlna_position_timer.stop()
            self._dlna_volume_timer.stop()
            # 先终止下载子进程，再等待线程池里的解析/搜索/投屏 worker 收敛，
            # 最后才释放它们依赖的 DLNA 中继与 mpv；否则回调可能访问已销毁的对象而崩溃。
            self.download_manager.shutdown()
            if not self.thread_pool.waitForDone(SHUTDOWN_WAIT_MS):
                logger.warning("后台任务在 %s 毫秒内未全部结束，继续退出流程", SHUTDOWN_WAIT_MS)
            # worker 都已跑完，剩下的 finished 回调不会再被派发，这里统一收掉登记的引用。
            self._active_workers.clear()
            self.dlna_media_server.stop()
            self.config.save()
            self.download_manager.flush()
            self.mpv.shutdown()
        finally:
            super().closeEvent(event)


def _arm_exit_watchdog(timeout: float = 10.0) -> None:
    """在升级前布置退出看门狗，超时仍未退出则强制结束进程。"""
    watchdog = threading.Timer(timeout, lambda: os._exit(0))
    watchdog.daemon = True
    watchdog.start()


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
