from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QKeySequence, QPixmap, QShortcut
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from download.models import (
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_QUEUED,
)
from resolver.models import MUXED_AUDIO_TRACK_ID, VideoInfo
from services.config_service import ConfigService
from services.shortcut_service import SHORTCUT_DEFINITIONS
from ui.playlist_overlay import PlaylistOverlay
from ui.text_elision import format_upload_date
from ui.thumbnail_cache import build_image_request, read_image_reply
from ui.widgets import NoScrollComboBox


# 每格滚轮调整的音量，与键盘 volume_up / volume_down 的步长保持一致。
VOLUME_WHEEL_STEP = 5
# 标准滚轮一格是 120；高分辨率滚轮/触控板会给出更小的增量，累加后再折算成格数。
_WHEEL_NOTCH = 120.0
# 字幕下拉框里最多直接列出多少条，其余走「更多字幕…」对话框。
SUBTITLE_SHORTLIST = 12
SUBTITLE_MORE_SENTINEL = "__subtitle_more__"


class PlayerPage(QWidget):
    play_pause_requested = Signal()
    stop_requested = Signal()
    seek_requested = Signal(float)
    volume_changed = Signal(int)
    speed_changed = Signal(float)
    quality_changed = Signal(str)
    audio_track_changed = Signal(str)
    subtitle_changed = Signal(str)
    cast_requested = Signal()
    browser_play_requested = Signal()
    fullscreen_requested = Signal()
    download_requested = Signal()
    favorite_requested = Signal()
    playlist_entry_requested = Signal(int)
    playlist_download_requested = Signal(object)
    playlist_save_requested = Signal()
    playlist_load_requested = Signal(str)
    playlist_delete_requested = Signal(str)
    playlist_auto_play_changed = Signal(bool)
    collection_entry_requested = Signal(int)
    collection_download_requested = Signal(object)
    collection_save_requested = Signal()
    collection_load_requested = Signal(str)
    collection_delete_requested = Signal(str)
    collection_auto_play_changed = Signal(bool)

    def __init__(self, config: ConfigService | None = None) -> None:
        super().__init__()
        self._config = config
        self._keyboard_shortcuts: list[QShortcut] = []
        self._duration = 0.0
        self._position = 0.0
        self._playlist_count = 0
        self._playlist_index = -1
        self._collection_count = 0
        self._collection_index = -1
        self._volume_before_mute = 80
        self._seeking = False
        self._populating = False
        self._loading = False
        self._has_media = False
        self._download_available = False
        self._favorite_available = False
        self._favorite_active = False
        # 下载状态用 DownloadTask.status 的取值，空串表示当前视频没有下载任务。
        self._download_state = ""
        self._download_progress = 0.0
        self._cast_available = False
        self._browser_play_available = False
        self._cast_active = False
        self._cast_seek_supported = True
        self._cast_volume_supported = True
        self._paused = True
        self._playback_finished = False
        self._fullscreen = False
        self._controls_visible = True
        self._control_pointer_inside = False
        self._control_interaction_active = False
        self._ignore_next_release = False
        self._auto_hide_enabled = False
        self._wheel_accumulator = 0.0
        self._subtitles: dict = {}
        self._audio_tracks: dict = {}
        self._active_subtitle_key = ""

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self.play_pause_requested)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(3200)
        self._idle_timer.timeout.connect(self._handle_idle_timeout)

        self._network = QNetworkAccessManager(self)

        self.video_widget = QFrame(self)
        self.video_widget.setObjectName("VideoSurface")
        self.video_widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_widget.setMinimumHeight(360)
        self.video_widget.installEventFilter(self)
        self.video_widget.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(0)
        layout.addWidget(self.video_widget, 1)
        self.setMouseTracking(True)

        self.title_label = QLabel("请输入视频 URL 开始播放")
        self.title_label.setObjectName("TitleLabel")
        self.title_label.setWordWrap(True)

        self.meta_label = QLabel("时长 00:00 | 清晰度 Auto | 字幕 关闭")
        self.meta_label.setObjectName("MetaLabel")

        # 收藏/下载状态标识。按钮文字只能表达「点了会发生什么」，这里补一条只读状态，
        # 让用户不点开收藏页/下载页就知道当前视频收没收藏、下没下载完。
        self.status_label = QLabel()
        self.status_label.setObjectName("MetaLabel")
        self.status_label.hide()

        self.loading_label = QLabel("正在准备视频，请稍等...")
        self.loading_label.setObjectName("MetaLabel")
        self.loading_label.hide()
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.hide()

        self.thumbnail_label = QLabel()
        self.thumbnail_label.setObjectName("ThumbnailLabel")
        self.thumbnail_label.setFixedSize(120, 68)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setText("封面")
        self.browser_play_button = QPushButton("浏览器播放")

        meta_row = QHBoxLayout()
        meta_row.addWidget(self.thumbnail_label)
        meta_text = QVBoxLayout()
        meta_text.addWidget(self.title_label)
        meta_text.addWidget(self.meta_label)
        meta_text.addWidget(self.status_label)
        meta_text.addStretch()
        meta_row.addLayout(meta_text, 1)
        meta_row.addWidget(self.browser_play_button, 0, Qt.AlignmentFlag.AlignTop)

        self.position_label = QLabel("00:00")
        self.duration_label = QLabel("00:00")
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.sliderPressed.connect(self._on_seek_start)
        self.progress_slider.sliderReleased.connect(self._on_seek_finish)

        progress_row = QHBoxLayout()
        progress_row.addWidget(self.position_label)
        progress_row.addWidget(self.progress_slider, 1)
        progress_row.addWidget(self.duration_label)

        self.play_button = QPushButton("播放")
        self.stop_button = QPushButton("停止")
        self.download_button = QPushButton("下载")
        self.favorite_button = QPushButton("收藏")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(120)

        self.speed_combo = QComboBox()
        for label, value in (
            ("0.5x", 0.5),
            ("0.75x", 0.75),
            ("1.0x", 1.0),
            ("1.25x", 1.25),
            ("1.5x", 1.5),
            ("2.0x", 2.0),
        ):
            self.speed_combo.addItem(label, value)
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.setFixedWidth(88)

        self.quality_combo = NoScrollComboBox()
        self.quality_combo.addItem("Auto")
        self.quality_combo.setFixedWidth(104)

        # 音轨：切一次要重载整条流（见 main_window._change_audio_track），滚轮误触的
        # 代价和清晰度一样，所以三个下拉统一用 NoScrollComboBox。
        self.audio_combo = NoScrollComboBox()
        self.audio_combo.addItem("默认音轨", "")
        self.audio_combo.setFixedWidth(116)

        self.subtitle_combo = NoScrollComboBox()
        self.subtitle_combo.addItem("关闭", "")
        self.subtitle_combo.setFixedWidth(108)

        self.fullscreen_button = QPushButton("全屏")
        self.fullscreen_button.setFixedWidth(84)
        self.cast_button = QPushButton("投屏")
        self.cast_button.setFixedWidth(92)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.download_button)
        controls.addWidget(self.favorite_button)
        controls.addSpacing(4)
        controls.addLayout(self._control_group("音量", self.volume_slider))
        controls.addLayout(self._control_group("倍速", self.speed_combo))
        controls.addLayout(self._control_group("清晰度", self.quality_combo))
        controls.addLayout(self._control_group("音轨", self.audio_combo))
        controls.addLayout(self._control_group("字幕", self.subtitle_combo))
        controls.addWidget(self.cast_button)
        controls.addWidget(self.fullscreen_button)
        controls.addStretch(1)

        self.control_panel = QWidget(self)
        self.control_panel.setObjectName("PlayerControlPanel")
        self.control_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.control_panel.setMouseTracking(True)
        self.control_panel.installEventFilter(self)
        control_panel_layout = QVBoxLayout(self.control_panel)
        control_panel_layout.setContentsMargins(12, 10, 12, 12)
        control_panel_layout.setSpacing(8)
        control_panel_layout.addWidget(self.loading_label)
        control_panel_layout.addWidget(self.loading_bar)
        control_panel_layout.addLayout(meta_row)
        control_panel_layout.addLayout(progress_row)
        control_panel_layout.addLayout(controls)

        self._controls_animation = QPropertyAnimation(self.control_panel, b"pos", self)
        self._controls_animation.setDuration(220)
        self._controls_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.playlist_overlay = PlaylistOverlay(self)
        self.playlist_overlay.hide()
        # 左侧合集面板：与右侧播放列表同一个类，只是换了方向、标题和空态文案。
        self.collection_overlay = PlaylistOverlay(
            self,
            side="left",
            default_title="合集列表",
            object_name="CollectionOverlay",
            empty_text="当前视频不属于任何合集",
        )
        self.collection_overlay.hide()
        # 两侧互斥：430px × 2 需要 ≥884px 宽，窄窗口下同时展开必然盖住画面。
        self.playlist_overlay.set_sibling_overlay(self.collection_overlay)
        self.collection_overlay.set_sibling_overlay(self.playlist_overlay)

        self.shortcut_hint = QLabel(self)
        self.shortcut_hint.setObjectName("ShortcutHint")
        self.shortcut_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shortcut_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.shortcut_hint.setStyleSheet(
            """
            QLabel#ShortcutHint {
                background-color: rgba(0, 0, 0, 204);
                color: white;
                border-radius: 12px;
                font-size: 22px;
                font-weight: 600;
                padding: 14px 24px;
            }
            """
        )
        self.shortcut_hint.hide()
        self._shortcut_hint_timer = QTimer(self)
        self._shortcut_hint_timer.setSingleShot(True)
        self._shortcut_hint_timer.setInterval(1600)
        self._shortcut_hint_timer.timeout.connect(self.shortcut_hint.hide)

        self.play_button.clicked.connect(self.play_pause_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.download_button.clicked.connect(self.download_requested)
        self.favorite_button.clicked.connect(self.favorite_requested)
        self.browser_play_button.clicked.connect(self.browser_play_requested)
        self.volume_slider.valueChanged.connect(self._handle_volume_changed)
        self.speed_combo.currentIndexChanged.connect(self._emit_speed)
        self.quality_combo.currentTextChanged.connect(self._emit_quality)
        self.audio_combo.currentIndexChanged.connect(self._emit_audio_track)
        self.subtitle_combo.currentIndexChanged.connect(self._emit_subtitle)
        self.cast_button.clicked.connect(self.cast_requested)
        self.fullscreen_button.clicked.connect(self.fullscreen_requested)
        self.playlist_overlay.entry_activated.connect(self.playlist_entry_requested)
        self.playlist_overlay.download_entries_requested.connect(self.playlist_download_requested)
        self.playlist_overlay.save_requested.connect(self.playlist_save_requested)
        self.playlist_overlay.load_saved_requested.connect(self.playlist_load_requested)
        self.playlist_overlay.delete_saved_requested.connect(self.playlist_delete_requested)
        self.playlist_overlay.auto_play_changed.connect(self.playlist_auto_play_changed)
        self.collection_overlay.entry_activated.connect(self.collection_entry_requested)
        self.collection_overlay.download_entries_requested.connect(self.collection_download_requested)
        self.collection_overlay.save_requested.connect(self.collection_save_requested)
        self.collection_overlay.load_saved_requested.connect(self.collection_load_requested)
        self.collection_overlay.delete_saved_requested.connect(self.collection_delete_requested)
        self.collection_overlay.auto_play_changed.connect(self.collection_auto_play_changed)
        self.installEventFilter(self)
        self._install_mouse_tracking(self.control_panel)
        self._install_mouse_tracking(self.playlist_overlay)
        self._install_mouse_tracking(self.collection_overlay)
        self._setup_keyboard_shortcuts()
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._handle_shortcut_focus_changed)
        self._update_playback_buttons()
        self._position_control_panel(animated=False)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_control_panel(animated=False)
        self.playlist_overlay.relayout(self.rect())
        self.collection_overlay.relayout(self.rect())
        self._position_shortcut_hint()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and self._control_interaction_active:
            self._control_interaction_active = False
            QTimer.singleShot(0, self._reevaluate_control_pointer)

        if event.type() == QEvent.Type.Wheel and self._wheel_adjusts_volume(watched):
            self._show_cursor()
            if self._auto_hide_enabled:
                self._idle_timer.start()
            self._handle_volume_wheel(event)
            return True

        if watched is self.video_widget:
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self._ignore_next_release:
                    self._ignore_next_release = False
                    return True
                if self._has_media and not self._loading:
                    self._click_timer.start()
                return True
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._click_timer.stop()
                self._ignore_next_release = True
                self.fullscreen_requested.emit()
                return True

        if (
            watched is self
            or watched is self.video_widget
            or watched is self.control_panel
            or self.control_panel.isAncestorOf(watched)
            or watched is self.playlist_overlay
            or self.playlist_overlay.isAncestorOf(watched)
            or watched is self.collection_overlay
            or self.collection_overlay.isAncestorOf(watched)
        ):
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._handle_mouse_move(watched, pos)
            elif event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.Wheel):
                if event.type() == QEvent.Type.MouseButtonPress and self._is_control_widget(watched):
                    self._control_interaction_active = True
                self._show_cursor()
                if self._auto_hide_enabled:
                    self._idle_timer.start()
            elif watched is self and event.type() == QEvent.Type.Leave:
                QTimer.singleShot(0, self._reevaluate_control_pointer)

        return super().eventFilter(watched, event)

    def set_loading(self, loading: bool, message: str = "") -> None:
        self._loading = loading
        self.loading_label.setVisible(loading)
        self.loading_bar.setVisible(loading)
        if loading:
            text = message or "正在解析视频，请稍等..."
            self.loading_label.setText(text)
            self.title_label.setText(text)
            # 开始解析下一个视频时立刻清掉上一个的收藏/下载标识，否则标题已经变成
            # 「正在解析…」，下面却还挂着上一个视频的「已收藏 · 已下载」。
            # 走 set_favorite_state 而不是直接改字段，收藏按钮的文字才不会留在「已收藏」。
            self.set_favorite_state(False, available=False)
            self.set_download_state("")
        self._sync_auto_hide_state()
        self._update_playback_buttons()
        self._position_control_panel(animated=False)

    def set_playback_available(self, available: bool) -> None:
        self._has_media = available
        if not available:
            self._position = 0.0
            self._duration = 0.0
            self.position_label.setText("00:00")
            self.duration_label.setText("00:00")
            self.progress_slider.setValue(0)
            self._playback_finished = False
            self._cast_available = False
            self._browser_play_available = False
            self._cast_active = False
            self._download_available = False
            self.set_favorite_state(False, available=False)
            self.set_download_state("")
            self.set_paused(True)
        else:
            self._loading = False
            self.loading_label.hide()
            self.loading_bar.hide()
        self._sync_auto_hide_state()
        self._update_playback_buttons()
        self._position_control_panel(animated=False)

    def set_download_available(self, available: bool) -> None:
        self._download_available = available
        self._update_playback_buttons()

    def set_favorite_state(self, favorite: bool, available: bool = True) -> None:
        self._favorite_active = favorite
        self._favorite_available = available
        self.favorite_button.setText("已收藏" if favorite else "收藏")
        self._refresh_status_label()
        self._update_playback_buttons()

    def set_download_state(self, status: str = "", progress: float = 0.0) -> None:
        """记录当前视频的下载任务状态，空 status 表示没有任务。"""
        self._download_state = str(status or "")
        self._download_progress = float(progress or 0.0)
        self._refresh_status_label()

    def _download_state_text(self) -> str:
        state = self._download_state
        if state == STATUS_DOWNLOADING:
            return f"下载中 {self._download_progress:.0f}%" if self._download_progress > 0 else "下载中"
        return {
            STATUS_COMPLETED: "已下载",
            STATUS_QUEUED: "已加入下载队列",
            STATUS_PAUSED: "下载已暂停",
            STATUS_FAILED: "下载失败",
        }.get(state, "")

    def _refresh_status_label(self) -> None:
        badges = []
        if self._favorite_active:
            badges.append("已收藏")
        download_text = self._download_state_text()
        if download_text:
            badges.append(download_text)
        self.status_label.setText(" · ".join(badges))
        self.status_label.setVisible(bool(badges))

    def set_cast_available(self, available: bool) -> None:
        self._cast_available = available
        self._update_playback_buttons()

    def set_browser_play_available(self, available: bool) -> None:
        self._browser_play_available = available
        self._update_playback_buttons()

    def set_cast_state(
        self,
        active: bool,
        *,
        seek_supported: bool = True,
        volume_supported: bool = True,
    ) -> None:
        self._cast_active = active
        self._cast_seek_supported = seek_supported
        self._cast_volume_supported = volume_supported
        self.cast_button.setText("停止投屏" if active else "投屏")
        self._update_playback_buttons()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._sync_auto_hide_state()
        self._update_playback_buttons()

    def set_playback_finished(self, finished: bool) -> None:
        self._playback_finished = finished
        self._sync_auto_hide_state()
        self._update_playback_buttons()

    def update_video_info(self, video: VideoInfo, selected_quality: str) -> None:
        self._populating = True
        self._position = 0.0
        self.progress_slider.setValue(0)
        self.title_label.setText(video.title)
        meta_parts = [
            f"时长 {format_seconds(video.duration)}",
            f"清晰度 {selected_quality}",
            # 「无字幕」比「字幕 0 个」更像一句话：用户看到 0 个会怀疑是程序没取到。
            f"字幕 {len(video.subtitles)} 个" if video.subtitles else "无字幕",
        ]
        upload_date = format_upload_date(getattr(video, "upload_date", ""))
        if upload_date:
            meta_parts.append(f"更新 {upload_date}")
        self.meta_label.setText(" | ".join(meta_parts))
        self.duration_label.setText(format_seconds(video.duration))
        self._duration = float(video.duration or 0)

        self.quality_combo.clear()
        for label in video.qualities:
            self.quality_combo.addItem(label)
        index = self.quality_combo.findText(selected_quality)
        if index >= 0:
            self.quality_combo.setCurrentIndex(index)

        current_quality = video.qualities.get(selected_quality)
        self._populate_audio_combo(
            getattr(video, "audio_tracks", {}) or {},
            muxed_available=bool(getattr(current_quality, "muxed_video_url", None)),
        )
        self._populate_subtitle_combo(video.subtitles)
        self._active_subtitle_key = ""

        self._populating = False
        self.load_thumbnail(video.thumbnail)
        self.set_download_available(True)
        self.set_browser_play_available(bool(str(video.webpage_url or "").strip()))
        self._position_control_panel(animated=False)

    def _populate_audio_combo(self, audio_tracks: dict, muxed_available: bool = False) -> None:
        """填充音轨下拉；音轨表为空（单语言或 B 站）时只放一条占位并禁用。

        `select_audio_tracks` 已按 D 裁定排好序，第一条就是默认轨，这里保持插入序、
        默认选中第 0 项即可，不再另做排序。

        当前档位存在已混音变体时，末尾追加「随画面（免转码）」（C1 裁定）：投屏没有
        FFmpeg 时的出路，选中即回到今天的单流行为。它不是一条真音轨，用哨兵 track_id。
        """
        self._audio_tracks = dict(audio_tracks or {})
        self.audio_combo.clear()
        # 单条也按占位处理：一条轨没什么可选的，与 _update_playback_buttons 里
        # `len(...) > 1` 的启用条件保持同一判据，避免"有个能读的语言名却点不动"。
        if len(self._audio_tracks) < 2:
            self.audio_combo.addItem("默认音轨", "")
            return
        for key, track in self._audio_tracks.items():
            self.audio_combo.addItem(track.label, key)
        if muxed_available:
            self.audio_combo.addItem("随画面（免转码）", MUXED_AUDIO_TRACK_ID)

    def _populate_subtitle_combo(self, subtitles: dict) -> None:
        """下拉框只放前 SUBTITLE_SHORTLIST 条，其余走「更多字幕…」对话框。

        YouTube 的自动字幕可以有近五千条（机翻到各种语言），全塞进 QComboBox 既慢
        又没法找；SubtitleParser 已按「手动优先、中英文优先」排好序，取前面一截
        正好覆盖绝大多数使用场景。

        一条也没有时把唯一项写成「无可用字幕」并禁用：站点没给字幕是常态，
        但「关闭」这个文案让人分不清是没有字幕还是程序没取到。
        """
        self._subtitles = dict(subtitles or {})
        self.subtitle_combo.clear()
        if not self._subtitles:
            self.subtitle_combo.addItem("无可用字幕", "")
            self._update_playback_buttons()
            return
        self.subtitle_combo.addItem("关闭", "")
        for key, subtitle in list(self._subtitles.items())[:SUBTITLE_SHORTLIST]:
            self.subtitle_combo.addItem(subtitle.label, key)
        if len(self._subtitles) > SUBTITLE_SHORTLIST:
            self.subtitle_combo.addItem(
                f"更多字幕…（共 {len(self._subtitles)} 条）",
                SUBTITLE_MORE_SENTINEL,
            )
        self._update_playback_buttons()

    def _ensure_subtitle_item(self, key: str) -> None:
        """把对话框里选中的字幕补进下拉框并选中它。"""
        subtitle = self._subtitles.get(key)
        if subtitle is None:
            return
        index = self.subtitle_combo.findData(key)
        if index < 0:
            insert_at = max(1, self.subtitle_combo.count() - (1 if self._has_more_entry() else 0))
            self.subtitle_combo.insertItem(insert_at, subtitle.label, key)
            index = insert_at
        self.subtitle_combo.setCurrentIndex(index)

    def _has_more_entry(self) -> bool:
        return self.subtitle_combo.findData(SUBTITLE_MORE_SENTINEL) >= 0

    def update_local_file_info(self, path: str) -> None:
        self._populating = True
        self._position = 0.0
        self._duration = 0.0
        self.position_label.setText("00:00")
        self.duration_label.setText("00:00")
        self.progress_slider.setValue(0)
        self.title_label.setText(path)
        self.meta_label.setText("本地文件")
        self.thumbnail_label.setText("本地文件")
        self.thumbnail_label.setPixmap(QPixmap())
        self.quality_combo.clear()
        self.quality_combo.addItem("本地")
        self._populate_audio_combo({})
        self._populate_subtitle_combo({})
        self._active_subtitle_key = ""
        self._populating = False
        self.set_download_available(False)
        self.set_browser_play_available(False)
        self.set_favorite_state(False, available=False)
        self._position_control_panel(animated=False)

    def set_volume(self, volume: int) -> None:
        self.volume_slider.setValue(volume)

    def set_speed(self, speed: float) -> None:
        for index in range(self.speed_combo.count()):
            if float(self.speed_combo.itemData(index)) == float(speed):
                self.speed_combo.setCurrentIndex(index)
                break

    def set_fullscreen(self, fullscreen: bool) -> None:
        self._fullscreen = fullscreen
        layout = self.layout()
        if layout:
            layout.setContentsMargins(0, 0, 0, 0) if fullscreen else layout.setContentsMargins(16, 16, 16, 16)
        self.fullscreen_button.setText("退出全屏" if fullscreen else "全屏")
        self._control_pointer_inside = False
        self._control_interaction_active = False
        # 全屏切换会改变可用宽度，两侧面板都要重新算宽度与滑出位置。
        self.playlist_overlay.relayout(self.rect())
        self.collection_overlay.relayout(self.rect())
        if self._controls_visible:
            self._controls_visible = False
            self._position_control_panel(animated=True)
        else:
            self._position_control_panel(animated=False)

    def set_playlist_context(self, playlist, current_index: int = -1, auto_play_next: bool = False) -> None:
        self._playlist_count = len(playlist.entries) if playlist is not None else 0
        self._playlist_index = current_index
        self.playlist_overlay.set_playlist(playlist, current_index=current_index, auto_play_next=auto_play_next)
        # Playlist item widgets are created dynamically, after the overlay's
        # initial mouse-tracking setup. Track them too so activity anywhere in
        # the panel resets the playback idle timer and keeps the panel open.
        self._install_mouse_tracking(self.playlist_overlay)
        self.playlist_overlay.relayout(self.rect())

    def clear_playlist_context(self) -> None:
        self._playlist_count = 0
        self._playlist_index = -1
        self.playlist_overlay.set_playlist(None)

    def set_playlist_saved_items(self, playlists, current_key: str = "") -> None:
        self.playlist_overlay.set_saved_playlists(playlists, current_key=current_key)

    def set_playlist_current_index(self, index: int) -> None:
        self._playlist_index = index
        self.playlist_overlay.set_current_index(index)

    # ------------------------------------------------------------------
    # 左侧合集面板
    # ------------------------------------------------------------------

    def set_collection_context(self, playlist, current_index: int = -1, auto_play_next: bool = False) -> None:
        self._collection_count = len(playlist.entries) if playlist is not None else 0
        self._collection_index = current_index
        self.collection_overlay.set_playlist(playlist, current_index=current_index, auto_play_next=auto_play_next)
        # 条目控件是动态建的，建完要重新铺一遍鼠标跟踪，否则面板内移动不算"活动"。
        self._install_mouse_tracking(self.collection_overlay)
        self.collection_overlay.relayout(self.rect())

    def clear_collection_context(self) -> None:
        self._collection_count = 0
        self._collection_index = -1
        self.collection_overlay.set_playlist(None)

    def set_collection_saved_items(self, playlists, current_key: str = "") -> None:
        self.collection_overlay.set_saved_playlists(playlists, current_key=current_key)

    def set_collection_current_index(self, index: int) -> None:
        self._collection_index = index
        self.collection_overlay.set_current_index(index)

    def set_collection_available(self, available: bool) -> None:
        """有视频在播就允许左侧滑出，即便探测结果是"不属于任何合集"（显示空态）。"""
        self.collection_overlay.set_context_available(available)

    def update_position(self, seconds: float) -> None:
        self._position = max(0.0, float(seconds or 0.0))
        self.position_label.setText(format_seconds(int(seconds)))
        if self._duration > 0 and not self._seeking:
            value = int(max(0, min(1000, seconds / self._duration * 1000)))
            self.progress_slider.setValue(value)

    def update_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self.duration_label.setText(format_seconds(int(seconds)))

    def load_thumbnail(self, url: str) -> None:
        self.thumbnail_label.setText("封面")
        self.thumbnail_label.setPixmap(QPixmap())
        if not url:
            return
        reply = self._network.get(build_image_request(url))
        reply.finished.connect(lambda: self._thumbnail_finished(reply))

    @Slot()
    def _thumbnail_finished(self, reply: QNetworkReply) -> None:
        data, failure = read_image_reply(reply)
        pixmap = QPixmap()
        if not failure and pixmap.loadFromData(data):
            self.thumbnail_label.setPixmap(
                pixmap.scaled(
                    self.thumbnail_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.thumbnail_label.setPixmap(QPixmap())
            self.thumbnail_label.setText("封面")
        reply.deleteLater()

    def _position_control_panel(self, animated: bool) -> None:
        self.control_panel.adjustSize()
        panel_height = self.control_panel.sizeHint().height()
        width = max(320, self.width() - 32)
        self.control_panel.setFixedWidth(width)
        self.control_panel.setFixedHeight(panel_height)
        x = 16 if not self._fullscreen else max(12, (self.width() - width) // 2)
        visible_y = max(16, self.height() - panel_height - 16)
        hidden_y = self.height() + 4
        target = QPoint(x, visible_y if self._controls_visible else hidden_y)
        self.control_panel.raise_()
        if animated:
            self._controls_animation.stop()
            self._controls_animation.setStartValue(self.control_panel.pos())
            self._controls_animation.setEndValue(target)
            self._controls_animation.start()
        else:
            self._controls_animation.stop()
            self.control_panel.move(target)

    def _wheel_adjusts_volume(self, watched) -> bool:
        """只有视频区、播放器空白处与控制面板背景上的滚轮才用来调音量。

        音量滑块、清晰度/字幕/倍速下拉框、播放列表滚动区域都要保留各自的原生
        滚轮行为，因此这里用白名单而不是黑名单 —— 控制面板的**子控件**不算。
        """
        if not self._shortcut_context_active():
            return False
        return watched is self or watched is self.video_widget or watched is self.control_panel

    def _handle_volume_wheel(self, event) -> None:
        self._wheel_accumulator += float(event.angleDelta().y())
        notches = int(self._wheel_accumulator / _WHEEL_NOTCH)
        if not notches:
            return
        self._wheel_accumulator -= notches * _WHEEL_NOTCH
        # 复用键盘那条路径：夹取、投屏时的可用性判断与音量提示都在里面，
        # 两种操作方式不该有两套行为。
        self._shortcut_volume(notches * VOLUME_WHEEL_STEP)

    def _show_shortcut_hint(self, text: str) -> None:
        self.shortcut_hint.setText(text)
        self.shortcut_hint.adjustSize()
        self._position_shortcut_hint()
        self.shortcut_hint.raise_()
        self.shortcut_hint.show()
        self._shortcut_hint_timer.start()

    def _position_shortcut_hint(self) -> None:
        self.shortcut_hint.adjustSize()
        x = max(0, (self.width() - self.shortcut_hint.width()) // 2)
        y = max(0, (self.height() - self.shortcut_hint.height()) // 2)
        self.shortcut_hint.move(x, y)

    def _on_seek_start(self) -> None:
        self._seeking = True

    def _on_seek_finish(self) -> None:
        self._seeking = False
        if self._duration <= 0:
            return
        self.seek_requested.emit(self.progress_slider.value() / 1000 * self._duration)

    def _emit_speed(self) -> None:
        self.speed_changed.emit(float(self.speed_combo.currentData()))

    def _handle_volume_changed(self, volume: int) -> None:
        if volume > 0:
            self._volume_before_mute = volume
        self.volume_changed.emit(volume)

    def _emit_quality(self, label: str) -> None:
        if not self._populating:
            self.quality_changed.emit(label)

    def _emit_audio_track(self) -> None:
        if not self._populating:
            self.audio_track_changed.emit(str(self.audio_combo.currentData() or ""))

    def _emit_subtitle(self) -> None:
        if self._populating:
            return
        key = str(self.subtitle_combo.currentData() or "")
        if key == SUBTITLE_MORE_SENTINEL:
            self._open_subtitle_picker()
            return
        self._active_subtitle_key = key
        self.subtitle_changed.emit(key)

    def _open_subtitle_picker(self) -> None:
        from ui.subtitle_dialog import SubtitlePickerDialog

        previous = self._active_subtitle_key
        dialog = SubtitlePickerDialog(self._subtitles, self, current_key=previous)
        chosen = dialog.selected_key() if dialog.exec() else ""
        self._populating = True
        try:
            if chosen:
                self._ensure_subtitle_item(chosen)
            else:
                # 取消时回到原来的选项，而不是把「更多字幕…」当成一个字幕留在框里。
                index = max(0, self.subtitle_combo.findData(previous))
                self.subtitle_combo.setCurrentIndex(index)
        finally:
            self._populating = False
        if chosen and chosen != previous:
            self._active_subtitle_key = chosen
            self.subtitle_changed.emit(chosen)

    def _update_playback_buttons(self) -> None:
        enabled = self._has_media and not self._loading
        self.play_button.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)
        self.download_button.setEnabled(enabled and self._download_available)
        self.favorite_button.setEnabled(enabled and self._favorite_available and not self._favorite_active)
        self.browser_play_button.setEnabled(enabled and self._browser_play_available)
        self.cast_button.setEnabled(enabled and (self._cast_available or self._cast_active))
        self.speed_combo.setEnabled(enabled and not self._cast_active)
        self.quality_combo.setEnabled(enabled and not self._cast_active)
        # 只有一条音轨时没得选，和字幕下拉同理保持禁用。
        self.audio_combo.setEnabled(
            enabled and not self._cast_active and len(self._audio_tracks) > 1
        )
        # 没有字幕轨时保持禁用：下拉里只有「无可用字幕」一项，可点也没有意义。
        self.subtitle_combo.setEnabled(enabled and not self._cast_active and bool(self._subtitles))
        self.progress_slider.setEnabled(enabled and (not self._cast_active or self._cast_seek_supported))
        self.volume_slider.setEnabled(enabled and (not self._cast_active or self._cast_volume_supported))
        self.play_button.setText("播放" if self._paused or self._playback_finished else "暂停")

    def _sync_auto_hide_state(self) -> None:
        enabled = self._has_media and not self._loading and not self._playback_finished
        self._auto_hide_enabled = enabled
        if enabled:
            self._show_controls()
            self._idle_timer.start()
        else:
            self._idle_timer.stop()
            self._show_cursor()
            self._show_controls()

    def _handle_mouse_move(self, watched: QWidget, local_pos: QPoint) -> None:
        pos_in_self = watched.mapTo(self, local_pos)
        in_control_zone = self._is_in_control_hot_zone(pos_in_self)
        was_in_control_zone = self._control_pointer_inside
        self._show_cursor()
        self.playlist_overlay.handle_pointer(pos_in_self)
        self.collection_overlay.handle_pointer(pos_in_self)
        if in_control_zone:
            self._control_pointer_inside = True
        elif self._can_hide_controls_for_pointer_exit():
            self._control_pointer_inside = False
        if self._auto_hide_enabled:
            self._idle_timer.start()
            if in_control_zone:
                self._show_controls()
            elif was_in_control_zone and self._can_hide_controls_for_pointer_exit():
                self._hide_controls()

    def _handle_idle_timeout(self) -> None:
        if not self._auto_hide_enabled:
            return
        if not self._can_hide_controls_for_pointer_exit():
            self._idle_timer.start()
            return
        self._hide_controls()
        self.playlist_overlay.handle_idle_timeout()
        self.collection_overlay.handle_idle_timeout()
        self._set_cursor_hidden(True)

    def _is_in_control_hot_zone(self, pos: QPoint) -> bool:
        if self.control_panel.geometry().contains(pos):
            return True
        return pos.y() >= max(0, self.height() - 72)

    def _show_controls(self) -> None:
        if self._controls_visible:
            self._show_cursor()
            return
        self._controls_visible = True
        self._position_control_panel(animated=True)
        self._show_cursor()

    def _hide_controls(self) -> None:
        if not self._controls_visible:
            return
        self._controls_visible = False
        self._position_control_panel(animated=True)

    def _reevaluate_control_pointer(self) -> None:
        pos_in_self = self.mapFromGlobal(QCursor.pos())
        in_control_zone = self._is_in_control_hot_zone(pos_in_self)
        was_in_control_zone = self._control_pointer_inside
        can_hide = self._can_hide_controls_for_pointer_exit()
        if in_control_zone:
            self._control_pointer_inside = True
        elif can_hide:
            self._control_pointer_inside = False
        if not self._auto_hide_enabled:
            return
        if in_control_zone:
            self._show_controls()
        elif was_in_control_zone and can_hide:
            self._hide_controls()

    def _can_hide_controls_for_pointer_exit(self) -> bool:
        return not self._control_interaction_active and QApplication.activePopupWidget() is None

    def _is_control_widget(self, widget: QWidget) -> bool:
        return widget is self.control_panel or self.control_panel.isAncestorOf(widget)

    def _show_cursor(self) -> None:
        self._set_cursor_hidden(False)

    def _set_cursor_hidden(self, hidden: bool) -> None:
        cursor = QCursor(Qt.CursorShape.BlankCursor if hidden else Qt.CursorShape.ArrowCursor)
        for widget in (self, self.video_widget, self.control_panel, self.playlist_overlay, self.collection_overlay):
            widget.setCursor(cursor)

    def _install_mouse_tracking(self, widget: QWidget) -> None:
        widget.setMouseTracking(True)
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _setup_keyboard_shortcuts(self) -> None:
        for shortcut in self._keyboard_shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._keyboard_shortcuts.clear()

        handlers = {
            "play_pause": self._shortcut_play_pause,
            "stop": self._shortcut_stop,
            "download": self._shortcut_download,
            "favorite": self._shortcut_favorite,
            "cast": self._shortcut_cast,
            "fullscreen": self._shortcut_fullscreen,
            "fullscreen_keypad": self._shortcut_fullscreen,
            "fullscreen_exit": self._shortcut_exit_fullscreen,
            "seek_backward_10": lambda: self._shortcut_seek(-10.0),
            "seek_forward_10": lambda: self._shortcut_seek(10.0),
            "seek_backward_60": lambda: self._shortcut_seek(-60.0),
            "seek_forward_60": lambda: self._shortcut_seek(60.0),
            "volume_up": lambda: self._shortcut_volume(5),
            "volume_down": lambda: self._shortcut_volume(-5),
            "speed_up": lambda: self._shortcut_speed_step(1),
            "speed_down": lambda: self._shortcut_speed_step(-1),
            "mute": self._shortcut_toggle_mute,
            "seek_start": self._shortcut_seek_start,
            "seek_end": self._shortcut_seek_end,
            "playlist_previous": lambda: self._shortcut_playlist_step(-1),
            "playlist_next": lambda: self._shortcut_playlist_step(1),
        }
        for definition in SHORTCUT_DEFINITIONS:
            sequence = (
                self._config.shortcut_sequence(definition.action)
                if self._config is not None
                else definition.default
            )
            key_sequence = QKeySequence(sequence)
            if key_sequence.isEmpty():
                continue
            shortcut = QShortcut(key_sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(handlers[definition.action])
            self._keyboard_shortcuts.append(shortcut)
        self._update_shortcut_enabled_state()

    def reload_shortcuts(self) -> None:
        self._setup_keyboard_shortcuts()

    def _handle_shortcut_focus_changed(self, _old, _new) -> None:
        self._update_shortcut_enabled_state()

    def _update_shortcut_enabled_state(self) -> None:
        focus = QApplication.focusWidget()
        enabled = not isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit))
        for shortcut in self._keyboard_shortcuts:
            shortcut.setEnabled(enabled)

    def _shortcut_context_active(self) -> bool:
        return self.isVisible() and self._has_media and not self._loading

    def _shortcut_play_pause(self) -> None:
        if self._shortcut_context_active():
            self.play_pause_requested.emit()

    def _shortcut_stop(self) -> None:
        if self._shortcut_context_active():
            self.stop_requested.emit()

    def _shortcut_download(self) -> None:
        if self._shortcut_context_active() and self._download_available:
            self.download_requested.emit()

    def _shortcut_favorite(self) -> None:
        if self._shortcut_context_active() and self._favorite_available and not self._favorite_active:
            self.favorite_requested.emit()

    def _shortcut_cast(self) -> None:
        if self._shortcut_context_active() and (self._cast_available or self._cast_active):
            self.cast_requested.emit()

    def _shortcut_fullscreen(self) -> None:
        if self._shortcut_context_active():
            self.fullscreen_requested.emit()

    def _shortcut_exit_fullscreen(self) -> None:
        if self._shortcut_context_active() and self._fullscreen:
            self.fullscreen_requested.emit()

    def _shortcut_seek(self, delta: float) -> None:
        if not self._shortcut_context_active():
            return
        target = max(0.0, self._position + float(delta))
        if self._duration > 0:
            target = min(self._duration, target)
        self.seek_requested.emit(target)

    def _shortcut_volume(self, delta: int) -> None:
        if not self._shortcut_context_active():
            return
        if self._cast_active and not self._cast_volume_supported:
            return
        target = max(0, min(100, self.volume_slider.value() + int(delta)))
        if target != self.volume_slider.value():
            self.volume_slider.setValue(target)
            self._show_shortcut_hint(f"音量 {target}%")

    def _shortcut_toggle_mute(self) -> None:
        if not self._shortcut_context_active():
            return
        if self._cast_active and not self._cast_volume_supported:
            return
        current = self.volume_slider.value()
        if current > 0:
            self._volume_before_mute = current
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(max(1, min(100, self._volume_before_mute)))

    def _shortcut_speed_step(self, delta: int) -> None:
        if not self._shortcut_context_active() or not self.speed_combo.isEnabled():
            return
        target = max(0, min(self.speed_combo.count() - 1, self.speed_combo.currentIndex() + int(delta)))
        if target != self.speed_combo.currentIndex():
            self.speed_combo.setCurrentIndex(target)
            self._show_shortcut_hint(f"倍速 {float(self.speed_combo.currentData()):g}x")

    def _shortcut_seek_start(self) -> None:
        if self._shortcut_context_active():
            self.seek_requested.emit(0.0)

    def _shortcut_seek_end(self) -> None:
        if self._shortcut_context_active() and self._duration > 0:
            self.seek_requested.emit(self._duration)

    def _shortcut_playlist_step(self, delta: int) -> None:
        if not self._shortcut_context_active():
            return
        # 右侧播放列表为空时，快捷键跟着左侧合集走，否则合集里翻集只能用鼠标。
        if self._playlist_count <= 0 and self._collection_count > 0:
            target = self._collection_index + int(delta)
            if 0 <= target < self._collection_count:
                self.collection_entry_requested.emit(target)
            return
        target = self._playlist_index + int(delta)
        if 0 <= target < self._playlist_count:
            self.playlist_entry_requested.emit(target)

    @staticmethod
    def _control_group(label_text: str, widget: QWidget) -> QHBoxLayout:
        label = QLabel(label_text)
        label.setContentsMargins(0, 0, 0, 0)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(label)
        layout.addWidget(widget)
        return layout


def format_seconds(seconds: int | float) -> str:
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
