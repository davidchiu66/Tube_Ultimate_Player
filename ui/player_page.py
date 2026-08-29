from __future__ import annotations

from random import choice as random_choice
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtSvg import QSvgRenderer
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
from app_paths import asset_path
from services.config_service import (
    PICTURE_IN_PICTURE_FIXED_STYLES,
    PICTURE_IN_PICTURE_STYLES,
    ConfigService,
)
from services.shortcut_service import SHORTCUT_DEFINITIONS
from ui.playlist_overlay import PlaylistOverlay
from ui.picture_in_picture import PictureInPictureResizeEdge, resize_edge_at
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
    picture_in_picture_requested = Signal()
    picture_in_picture_mouse_event = Signal(str, object)
    picture_in_picture_lock_changed = Signal(bool)
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
        self._cast_pending = False
        self._cast_seek_supported = True
        self._cast_volume_supported = True
        self._paused = True
        self._playback_finished = False
        self._fullscreen = False
        self._picture_in_picture = False
        self._picture_in_picture_style_preference = (
            config.picture_in_picture_style() if config is not None else "random"
        )
        self._picture_in_picture_style = self._resolve_picture_in_picture_style(
            self._picture_in_picture_style_preference
        )
        self._picture_in_picture_icons: dict[tuple[str, str], QIcon] = {}
        self._picture_in_picture_locked = False
        self._picture_in_picture_cursor_shape = Qt.CursorShape.ArrowCursor
        self._picture_in_picture_resize_gesture = False
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
        self._click_timer.timeout.connect(self._handle_video_single_click)

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
        self.picture_in_picture_button = QPushButton("小窗")
        self.picture_in_picture_button.setFixedWidth(84)
        self.picture_in_picture_button.setToolTip("小窗播放（W）")
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
        controls.addWidget(self.picture_in_picture_button)
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

        self._build_picture_in_picture_controls()

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
        self.picture_in_picture_button.clicked.connect(self.picture_in_picture_requested)
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
        self._install_mouse_tracking(self.picture_in_picture_title_bar)
        self._install_mouse_tracking(self.picture_in_picture_control_bar)
        self._install_mouse_tracking(self.picture_in_picture_end_overlay)
        self._setup_keyboard_shortcuts()
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._handle_shortcut_focus_changed)
        self._update_playback_buttons()
        self._position_control_panel(animated=False)

    def _build_picture_in_picture_controls(self) -> None:
        self._picture_in_picture_controls_visible = False
        self._picture_in_picture_seeking = False

        self.picture_in_picture_title_bar = QFrame(self)
        self.picture_in_picture_title_bar.setObjectName("PictureInPictureTitleBar")
        self.picture_in_picture_title_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        title_layout = QHBoxLayout(self.picture_in_picture_title_bar)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(6)
        self.picture_in_picture_title_label = QLabel("正在播放")
        self.picture_in_picture_title_label.setObjectName("PictureInPictureTitle")
        title_layout.addWidget(self.picture_in_picture_title_label, 1)
        self.picture_in_picture_close_button = QPushButton()
        self.picture_in_picture_close_button.setToolTip("退出小窗")
        self.picture_in_picture_close_button.setFixedSize(30, 30)
        title_layout.addWidget(self.picture_in_picture_close_button)

        self.picture_in_picture_control_bar = QFrame(self)
        self.picture_in_picture_control_bar.setObjectName("PictureInPictureControlBar")
        self.picture_in_picture_control_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        controls = QHBoxLayout(self.picture_in_picture_control_bar)
        controls.setContentsMargins(8, 5, 8, 5)
        controls.setSpacing(5)
        self.picture_in_picture_play_button = QPushButton()
        self.picture_in_picture_play_button.setToolTip("播放 / 暂停")
        self.picture_in_picture_previous_button = QPushButton()
        self.picture_in_picture_previous_button.setToolTip("上一集")
        self.picture_in_picture_next_button = QPushButton()
        self.picture_in_picture_next_button.setToolTip("下一集")
        self.picture_in_picture_position_label = QLabel("00:00 / 00:00")
        self.picture_in_picture_progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.picture_in_picture_progress_slider.setRange(0, 1000)
        self.picture_in_picture_mute_button = QPushButton()
        self.picture_in_picture_mute_button.setToolTip("静音 / 恢复音量")
        self.picture_in_picture_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.picture_in_picture_volume_slider.setRange(0, 100)
        self.picture_in_picture_volume_slider.setFixedWidth(72)
        self.picture_in_picture_fullscreen_button = QPushButton()
        self.picture_in_picture_fullscreen_button.setToolTip("退出小窗并进入全屏")
        self.picture_in_picture_restore_button = QPushButton()
        self.picture_in_picture_restore_button.setToolTip("返回播放器")
        self.picture_in_picture_lock_button = QPushButton()
        self.picture_in_picture_lock_button.setToolTip("锁定窗口位置和尺寸")
        for button in (
            self.picture_in_picture_play_button,
            self.picture_in_picture_previous_button,
            self.picture_in_picture_next_button,
            self.picture_in_picture_mute_button,
            self.picture_in_picture_fullscreen_button,
            self.picture_in_picture_restore_button,
            self.picture_in_picture_lock_button,
        ):
            button.setFixedSize(30, 30)
        controls.addWidget(self.picture_in_picture_play_button)
        controls.addWidget(self.picture_in_picture_previous_button)
        controls.addWidget(self.picture_in_picture_next_button)
        controls.addWidget(self.picture_in_picture_progress_slider, 1)
        controls.addWidget(self.picture_in_picture_position_label)
        controls.addWidget(self.picture_in_picture_mute_button)
        controls.addWidget(self.picture_in_picture_volume_slider)
        controls.addWidget(self.picture_in_picture_fullscreen_button)
        controls.addWidget(self.picture_in_picture_restore_button)
        controls.addWidget(self.picture_in_picture_lock_button)

        self.picture_in_picture_hint = QFrame(self)
        self.picture_in_picture_hint.setObjectName("PictureInPictureHint")
        self.picture_in_picture_hint.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.picture_in_picture_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hint_layout = QHBoxLayout(self.picture_in_picture_hint)
        hint_layout.setContentsMargins(10, 8, 10, 8)
        hint_layout.setSpacing(8)
        self.picture_in_picture_hint_icon = QLabel()
        self.picture_in_picture_hint_icon.setObjectName("PictureInPictureHintIcon")
        self.picture_in_picture_hint_icon.setFixedSize(24, 24)
        self.picture_in_picture_hint_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture_in_picture_hint_label = QLabel()
        self.picture_in_picture_hint_label.setObjectName("PictureInPictureHintText")
        self.picture_in_picture_hint_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        hint_layout.addWidget(self.picture_in_picture_hint_icon)
        hint_layout.addWidget(self.picture_in_picture_hint_label)
        self._picture_in_picture_hint_action = ""
        self._picture_in_picture_hint_timer = QTimer(self)
        self._picture_in_picture_hint_timer.setSingleShot(True)
        self._picture_in_picture_hint_timer.setInterval(1000)
        self._picture_in_picture_hint_timer.timeout.connect(self.picture_in_picture_hint.hide)

        self.picture_in_picture_end_overlay = QFrame(self)
        self.picture_in_picture_end_overlay.setObjectName("PictureInPictureEndOverlay")
        self.picture_in_picture_end_overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        end_layout = QVBoxLayout(self.picture_in_picture_end_overlay)
        end_layout.setContentsMargins(20, 16, 20, 16)
        end_layout.setSpacing(10)
        ended_label = QLabel("播放结束")
        ended_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        end_layout.addWidget(ended_label)
        end_actions = QHBoxLayout()
        self.picture_in_picture_replay_button = QPushButton("重新播放")
        self.picture_in_picture_return_button = QPushButton("返回播放器")
        end_actions.addWidget(self.picture_in_picture_replay_button)
        end_actions.addWidget(self.picture_in_picture_return_button)
        end_layout.addLayout(end_actions)

        overlay_style = """
            QFrame#PictureInPictureTitleBar, QFrame#PictureInPictureControlBar,
            QFrame#PictureInPictureEndOverlay {
                background-color: rgba(18, 18, 18, 220);
                color: white;
            }
            QLabel#PictureInPictureTitle, QLabel#PictureInPictureHintIcon,
            QLabel#PictureInPictureHintText {
                color: white;
            }
            QFrame#PictureInPictureHint {
                background-color: rgba(0, 0, 0, 204);
                border-radius: 8px;
            }
            QLabel#PictureInPictureHintText {
                font-size: 18px;
            }
        """
        self.picture_in_picture_title_bar.setStyleSheet(overlay_style)
        self.picture_in_picture_control_bar.setStyleSheet(overlay_style)
        self.picture_in_picture_end_overlay.setStyleSheet(overlay_style)
        self.picture_in_picture_hint.setStyleSheet(overlay_style)

        self._apply_picture_in_picture_style()

        self.picture_in_picture_close_button.clicked.connect(self.picture_in_picture_requested)
        self.picture_in_picture_restore_button.clicked.connect(self.picture_in_picture_requested)
        self.picture_in_picture_return_button.clicked.connect(self.picture_in_picture_requested)
        self.picture_in_picture_play_button.clicked.connect(self.play_pause_requested)
        self.picture_in_picture_replay_button.clicked.connect(self.play_pause_requested)
        self.picture_in_picture_previous_button.clicked.connect(lambda: self._shortcut_playlist_step(-1))
        self.picture_in_picture_next_button.clicked.connect(lambda: self._shortcut_playlist_step(1))
        self.picture_in_picture_mute_button.clicked.connect(self._shortcut_toggle_mute)
        self.picture_in_picture_fullscreen_button.clicked.connect(self.fullscreen_requested)
        self.picture_in_picture_lock_button.clicked.connect(self._toggle_picture_in_picture_lock)
        self.picture_in_picture_progress_slider.sliderPressed.connect(self._on_picture_in_picture_seek_start)
        self.picture_in_picture_progress_slider.sliderReleased.connect(self._on_picture_in_picture_seek_finish)
        self.picture_in_picture_volume_slider.valueChanged.connect(self._handle_picture_in_picture_volume_changed)

        self._picture_in_picture_idle_timer = QTimer(self)
        self._picture_in_picture_idle_timer.setSingleShot(True)
        self._picture_in_picture_idle_timer.setInterval(3000)
        self._picture_in_picture_idle_timer.timeout.connect(self._hide_picture_in_picture_controls)
        for widget in (
            self.picture_in_picture_title_bar,
            self.picture_in_picture_control_bar,
            self.picture_in_picture_hint,
            self.picture_in_picture_end_overlay,
        ):
            widget.hide()

    def set_picture_in_picture_style(self, style: str) -> None:
        """切换小窗控制器风格；随机偏好在应用设置时解析为一套固定风格。"""
        normalized = str(style or "").strip().lower()
        if normalized not in PICTURE_IN_PICTURE_STYLES:
            normalized = "random"
        resolved = self._resolve_picture_in_picture_style(
            normalized,
            previous=self._picture_in_picture_style,
        )
        if (
            normalized == self._picture_in_picture_style_preference
            and resolved == self._picture_in_picture_style
            and normalized != "random"
            and hasattr(self, "picture_in_picture_close_button")
        ):
            self._update_picture_in_picture_dynamic_icons()
            return
        self._picture_in_picture_style_preference = normalized
        self._picture_in_picture_style = resolved
        if hasattr(self, "picture_in_picture_close_button"):
            self._apply_picture_in_picture_style()

    @staticmethod
    def _resolve_picture_in_picture_style(style: str, *, previous: str = "") -> str:
        if style != "random":
            return style
        candidates = tuple(
            candidate
            for candidate in PICTURE_IN_PICTURE_FIXED_STYLES
            if candidate != previous
        )
        return random_choice(candidates or PICTURE_IN_PICTURE_FIXED_STYLES)

    @property
    def picture_in_picture_style(self) -> str:
        return self._picture_in_picture_style

    @property
    def picture_in_picture_style_preference(self) -> str:
        return self._picture_in_picture_style_preference

    def _picture_in_picture_icon(self, action: str) -> QIcon:
        key = (self._picture_in_picture_style, action)
        cached = self._picture_in_picture_icons.get(key)
        if cached is not None:
            return cached
        renderer = QSvgRenderer(str(asset_path("pip", f"pip_{self._picture_in_picture_style}.svg")))
        icon = QIcon()
        if renderer.isValid() and renderer.elementExists(action):
            pixmap = QPixmap(48, 48)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            renderer.render(painter, action, QRectF(0, 0, 48, 48))
            painter.end()
            icon = QIcon(pixmap)
        self._picture_in_picture_icons[key] = icon
        return icon

    def _apply_picture_in_picture_style(self) -> None:
        palette = {
            "style_a": ("#1b222a", "#38434e", "#27323c", "7px"),
            "style_b": ("#26313b", "#4a5966", "#344250", "6px"),
            "style_c": ("#171c22", "#526170", "#24303b", "15px"),
        }
        background, border, hover, radius = palette[self._picture_in_picture_style]
        button_style = f"""
            QPushButton {{
                background-color: {background};
                border: 1px solid {border};
                border-radius: {radius};
                padding: 0px;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
            QPushButton:pressed {{ background-color: {border}; }}
            QPushButton:disabled {{ opacity: 0.45; }}
        """
        buttons = (
            self.picture_in_picture_close_button,
            self.picture_in_picture_play_button,
            self.picture_in_picture_previous_button,
            self.picture_in_picture_next_button,
            self.picture_in_picture_mute_button,
            self.picture_in_picture_fullscreen_button,
            self.picture_in_picture_restore_button,
            self.picture_in_picture_lock_button,
        )
        for button in buttons:
            button.setStyleSheet(button_style)
            button.setIconSize(QSize(18, 18))
        self._update_picture_in_picture_dynamic_icons()
        if self._picture_in_picture_hint_action:
            self._update_picture_in_picture_hint_icon()

    def _update_picture_in_picture_dynamic_icons(self) -> None:
        if not hasattr(self, "picture_in_picture_close_button"):
            return
        icon_actions = {
            self.picture_in_picture_close_button: "close",
            self.picture_in_picture_play_button: "play"
            if self._paused or self._playback_finished
            else "pause",
            self.picture_in_picture_previous_button: "previous",
            self.picture_in_picture_next_button: "next",
            self.picture_in_picture_mute_button: "mute"
            if self.picture_in_picture_volume_slider.value() == 0
            else "volume",
            self.picture_in_picture_fullscreen_button: "fullscreen",
            self.picture_in_picture_restore_button: "restore",
            self.picture_in_picture_lock_button: "unlock"
            if self._picture_in_picture_locked
            else "lock",
        }
        for button, action in icon_actions.items():
            button.setText("")
            button.setIcon(self._picture_in_picture_icon(action))

    def _refresh_picture_in_picture_style_for_media(self) -> None:
        if self._picture_in_picture_style_preference == "random":
            self.set_picture_in_picture_style("random")

    def _update_picture_in_picture_hint_icon(self) -> None:
        action = self._picture_in_picture_hint_action
        if action not in {"play", "pause"}:
            return
        pixmap = self._picture_in_picture_icon(action).pixmap(24, 24)
        self.picture_in_picture_hint_icon.setPixmap(pixmap)

    def _handle_video_single_click(self) -> None:
        if self._picture_in_picture:
            self._show_picture_in_picture_playback_hint("play" if self._paused else "pause")
        self.play_pause_requested.emit()

    def trigger_picture_in_picture_click(self) -> None:
        if self._picture_in_picture and self._has_media and not self._loading:
            self._click_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_control_panel(animated=False)
        self.playlist_overlay.relayout(self.rect())
        self.collection_overlay.relayout(self.rect())
        self._position_shortcut_hint()
        self._position_picture_in_picture_controls()

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

        if self._picture_in_picture and self._is_picture_in_picture_surface(watched):
            if (
                watched is self.video_widget
                and event.type() == QEvent.Type.MouseButtonDblClick
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._click_timer.stop()
                self._ignore_next_release = True
                self.picture_in_picture_requested.emit()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                global_position = self._event_global_position(event)
                local_position = self.mapFromGlobal(global_position)
                edge = PictureInPictureResizeEdge.NONE
                if not self._picture_in_picture_locked:
                    edge = resize_edge_at(self.rect(), local_position)
                if edge != PictureInPictureResizeEdge.NONE:
                    self._picture_in_picture_resize_gesture = True
                    self.picture_in_picture_mouse_event.emit("press", global_position)
                    self._show_picture_in_picture_controls()
                    return True
                if self._is_picture_in_picture_drag_surface(watched):
                    self.picture_in_picture_mouse_event.emit("press", global_position)
                    self._show_picture_in_picture_controls()
                    return True
            if event.type() == QEvent.Type.MouseMove:
                global_position = self._event_global_position(event)
                self.picture_in_picture_mouse_event.emit("move", global_position)
                self._show_picture_in_picture_controls()
                if self._picture_in_picture_resize_gesture or self._is_picture_in_picture_drag_surface(watched):
                    return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                global_position = self._event_global_position(event)
                if self._picture_in_picture_resize_gesture:
                    self._picture_in_picture_resize_gesture = False
                    self.picture_in_picture_mouse_event.emit("release", global_position)
                    return True
                if self._is_picture_in_picture_drag_surface(watched):
                    self.picture_in_picture_mouse_event.emit("release", global_position)
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
            or watched is self.picture_in_picture_title_bar
            or self.picture_in_picture_title_bar.isAncestorOf(watched)
            or watched is self.picture_in_picture_control_bar
            or self.picture_in_picture_control_bar.isAncestorOf(watched)
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
                if self._picture_in_picture:
                    self._show_picture_in_picture_controls()
            elif watched is self and event.type() == QEvent.Type.Leave:
                QTimer.singleShot(0, self._reevaluate_control_pointer)

        return super().eventFilter(watched, event)

    @staticmethod
    def _event_global_position(event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _is_picture_in_picture_drag_surface(self, watched: QWidget) -> bool:
        return watched in {
            self.video_widget,
            self.picture_in_picture_title_bar,
            self.picture_in_picture_title_label,
        }

    def _is_picture_in_picture_surface(self, watched: QWidget) -> bool:
        return (
            watched is self
            or watched is self.video_widget
            or watched is self.picture_in_picture_title_bar
            or self.picture_in_picture_title_bar.isAncestorOf(watched)
            or watched is self.picture_in_picture_control_bar
            or self.picture_in_picture_control_bar.isAncestorOf(watched)
            or watched is self.picture_in_picture_end_overlay
            or self.picture_in_picture_end_overlay.isAncestorOf(watched)
        )

    def set_loading(self, loading: bool, message: str = "") -> None:
        self._loading = loading
        self.loading_label.setVisible(loading)
        self.loading_bar.setVisible(loading)
        if loading:
            text = message or "正在解析视频，请稍等..."
            self.loading_label.setText(text)
            self.title_label.setText(text)
            self.picture_in_picture_title_label.setText(text)
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
            self._cast_pending = False
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
        self._update_picture_in_picture_dynamic_icons()
        self._sync_auto_hide_state()
        self._update_playback_buttons()

    def set_cast_pending(self, pending: bool) -> None:
        self._cast_pending = bool(pending)
        self._update_playback_buttons()

    def set_playback_finished(self, finished: bool) -> None:
        self._playback_finished = finished
        self.picture_in_picture_end_overlay.setVisible(self._picture_in_picture and finished)
        if finished:
            self._show_picture_in_picture_controls()
        self._sync_auto_hide_state()
        self._update_playback_buttons()

    def update_video_info(self, video: VideoInfo, selected_quality: str) -> None:
        self._refresh_picture_in_picture_style_for_media()
        self._populating = True
        self._position = 0.0
        self.progress_slider.setValue(0)
        self.title_label.setText(video.title)
        self.picture_in_picture_title_label.setText(video.title or "正在播放")
        meta_parts = [
            *([f"作者 {video.uploader}"] if str(video.uploader or "").strip() else []),
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
        self._refresh_picture_in_picture_style_for_media()
        self._populating = True
        self._position = 0.0
        self._duration = 0.0
        self.position_label.setText("00:00")
        self.duration_label.setText("00:00")
        self.progress_slider.setValue(0)
        self.title_label.setText(path)
        self.picture_in_picture_title_label.setText(Path(path).name or path)
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
        volume = max(0, min(100, int(volume)))
        self.volume_slider.setValue(volume)
        blocked = self.picture_in_picture_volume_slider.blockSignals(True)
        self.picture_in_picture_volume_slider.setValue(volume)
        self.picture_in_picture_volume_slider.blockSignals(blocked)
        self._update_picture_in_picture_dynamic_icons()

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
        if self._playback_finished:
            # Finished playback disables auto-hide. Returning from PIP must
            # still leave the normal controller visible.
            self._controls_visible = True
            self._position_control_panel(animated=False)
        elif self._controls_visible:
            self._controls_visible = False
            self._position_control_panel(animated=True)
        else:
            self._position_control_panel(animated=False)

    @property
    def picture_in_picture(self) -> bool:
        return self._picture_in_picture

    @property
    def picture_in_picture_locked(self) -> bool:
        return self._picture_in_picture_locked

    def set_picture_in_picture(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._picture_in_picture == enabled:
            return
        self._picture_in_picture = enabled
        self._picture_in_picture_resize_gesture = False
        self._picture_in_picture_cursor_shape = Qt.CursorShape.ArrowCursor
        self._click_timer.stop()
        if enabled:
            self._idle_timer.stop()
            self._auto_hide_enabled = False
            self._set_cursor_hidden(False)
            layout = self.layout()
            if layout:
                layout.setContentsMargins(0, 0, 0, 0)
            self.video_widget.setMinimumHeight(0)
            self.control_panel.hide()
            self.playlist_overlay.hide_overlay(animated=False)
            self.collection_overlay.hide_overlay(animated=False)
            self.shortcut_hint.hide()
            self.picture_in_picture_title_label.setText(self.title_label.text() or "正在播放")
            self.picture_in_picture_title_bar.show()
            self.picture_in_picture_control_bar.show()
            self.picture_in_picture_end_overlay.setVisible(self._playback_finished)
            self._show_picture_in_picture_controls()
        else:
            self._picture_in_picture_idle_timer.stop()
            self._hide_picture_in_picture_controls()
            self.picture_in_picture_end_overlay.hide()
            self.picture_in_picture_title_bar.hide()
            self.picture_in_picture_control_bar.hide()
            self.video_widget.setMinimumHeight(360)
            layout = self.layout()
            if layout:
                layout.setContentsMargins(0, 0, 0, 0) if self._fullscreen else layout.setContentsMargins(16, 16, 16, 16)
            self.control_panel.show()
            self._position_control_panel(animated=False)
            self._set_cursor_hidden(False)
            self._apply_picture_in_picture_cursor(Qt.CursorShape.ArrowCursor)
            self._sync_auto_hide_state()
        self._position_picture_in_picture_controls()
        self._update_playback_buttons()

    def _toggle_picture_in_picture_lock(self) -> None:
        self._picture_in_picture_locked = not self._picture_in_picture_locked
        self._update_picture_in_picture_dynamic_icons()
        self.picture_in_picture_lock_button.setToolTip(
            "解锁后允许移动和缩放" if self._picture_in_picture_locked else "锁定窗口位置和尺寸"
        )
        if self._picture_in_picture_locked:
            self._picture_in_picture_resize_gesture = False
            self.set_picture_in_picture_cursor(Qt.CursorShape.ArrowCursor)
        self.picture_in_picture_lock_changed.emit(self._picture_in_picture_locked)

    def set_picture_in_picture_cursor(self, shape: Qt.CursorShape) -> None:
        if self._picture_in_picture_locked:
            shape = Qt.CursorShape.ArrowCursor
        self._picture_in_picture_cursor_shape = shape
        if self._picture_in_picture:
            self._apply_picture_in_picture_cursor(shape)

    def _apply_picture_in_picture_cursor(self, shape: Qt.CursorShape) -> None:
        cursor = QCursor(shape)
        surfaces = (
            self,
            self.video_widget,
            self.picture_in_picture_title_bar,
            self.picture_in_picture_control_bar,
            self.picture_in_picture_end_overlay,
            self.picture_in_picture_hint,
        )
        for widget in surfaces:
            widget.setCursor(cursor)
            for child in widget.findChildren(QWidget):
                child.setCursor(cursor)

    def set_picture_in_picture_locked(self, locked: bool) -> None:
        if bool(locked) != self._picture_in_picture_locked:
            self._toggle_picture_in_picture_lock()

    def _position_picture_in_picture_controls(self) -> None:
        if not hasattr(self, "picture_in_picture_title_bar"):
            return
        width = self.width()
        self.picture_in_picture_title_bar.setGeometry(0, 0, width, 34)
        self.picture_in_picture_control_bar.adjustSize()
        bar_height = max(46, self.picture_in_picture_control_bar.sizeHint().height())
        self.picture_in_picture_control_bar.setGeometry(0, max(0, self.height() - bar_height), width, bar_height)
        compact = width < 480
        small = width < 380
        self.picture_in_picture_position_label.setVisible(not compact)
        self.picture_in_picture_volume_slider.setVisible(not compact)
        for button in (
            self.picture_in_picture_play_button,
            self.picture_in_picture_previous_button,
            self.picture_in_picture_next_button,
            self.picture_in_picture_mute_button,
            self.picture_in_picture_fullscreen_button,
            self.picture_in_picture_restore_button,
            self.picture_in_picture_lock_button,
        ):
            button.setFixedWidth(26 if small else 30)
        self.picture_in_picture_end_overlay.adjustSize()
        end_size = self.picture_in_picture_end_overlay.sizeHint()
        self.picture_in_picture_end_overlay.setGeometry(
            max(0, (width - end_size.width()) // 2),
            max(0, (self.height() - end_size.height()) // 2),
            end_size.width(),
            end_size.height(),
        )
        self.picture_in_picture_hint.adjustSize()
        self.picture_in_picture_hint.move(
            max(0, (width - self.picture_in_picture_hint.width()) // 2),
            max(0, (self.height() - self.picture_in_picture_hint.height()) // 2),
        )
        self.picture_in_picture_title_bar.raise_()
        self.picture_in_picture_control_bar.raise_()
        self.picture_in_picture_end_overlay.raise_()
        self.picture_in_picture_hint.raise_()

    def _show_picture_in_picture_controls(self) -> None:
        if not self._picture_in_picture:
            return
        self._set_cursor_hidden(False)
        self._picture_in_picture_controls_visible = True
        self.picture_in_picture_title_bar.show()
        self.picture_in_picture_control_bar.show()
        self._position_picture_in_picture_controls()
        self._picture_in_picture_idle_timer.start()

    def _hide_picture_in_picture_controls(self) -> None:
        if not self._picture_in_picture or self._control_interaction_active:
            return
        pointer = self.mapFromGlobal(QCursor.pos())
        if self.picture_in_picture_title_bar.geometry().contains(pointer) or self.picture_in_picture_control_bar.geometry().contains(pointer):
            self._picture_in_picture_idle_timer.start()
            return
        self._picture_in_picture_controls_visible = False
        self.picture_in_picture_title_bar.hide()
        self.picture_in_picture_control_bar.hide()

    def _show_picture_in_picture_playback_hint(self, action: str) -> None:
        if not self._picture_in_picture:
            return
        self._picture_in_picture_hint_action = "pause" if action in {"pause", "⏸ 暂停"} else "play"
        self.picture_in_picture_hint_label.setText(
            "暂停" if self._picture_in_picture_hint_action == "pause" else "播放"
        )
        self._update_picture_in_picture_hint_icon()
        self._position_picture_in_picture_controls()
        self.picture_in_picture_hint.show()
        self.picture_in_picture_hint.raise_()
        self._picture_in_picture_hint_timer.start()

    def _on_picture_in_picture_seek_start(self) -> None:
        self._picture_in_picture_seeking = True

    def _on_picture_in_picture_seek_finish(self) -> None:
        self._picture_in_picture_seeking = False
        if self._duration > 0:
            self.seek_requested.emit(self.picture_in_picture_progress_slider.value() / 1000 * self._duration)

    def _handle_picture_in_picture_volume_changed(self, volume: int) -> None:
        if self.volume_slider.value() != volume:
            self.volume_slider.setValue(volume)

    def set_playlist_context(self, playlist, current_index: int = -1, auto_play_next: bool = False) -> None:
        self._playlist_count = len(playlist.entries) if playlist is not None else 0
        self._playlist_index = current_index
        self.playlist_overlay.set_playlist(playlist, current_index=current_index, auto_play_next=auto_play_next)
        # Playlist item widgets are created dynamically, after the overlay's
        # initial mouse-tracking setup. Track them too so activity anywhere in
        # the panel resets the playback idle timer and keeps the panel open.
        self._install_mouse_tracking(self.playlist_overlay)
        self.playlist_overlay.relayout(self.rect())
        self._update_picture_in_picture_queue_buttons()

    def clear_playlist_context(self) -> None:
        self._playlist_count = 0
        self._playlist_index = -1
        self.playlist_overlay.set_playlist(None)
        self._update_picture_in_picture_queue_buttons()

    def set_playlist_saved_items(self, playlists, current_key: str = "") -> None:
        self.playlist_overlay.set_saved_playlists(playlists, current_key=current_key)

    def set_playlist_current_index(self, index: int) -> None:
        self._playlist_index = index
        self.playlist_overlay.set_current_index(index)
        self._update_picture_in_picture_queue_buttons()

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
        self._update_picture_in_picture_queue_buttons()

    def clear_collection_context(self) -> None:
        self._collection_count = 0
        self._collection_index = -1
        self.collection_overlay.set_playlist(None)
        self._update_picture_in_picture_queue_buttons()

    def set_collection_saved_items(self, playlists, current_key: str = "") -> None:
        self.collection_overlay.set_saved_playlists(playlists, current_key=current_key)

    def set_collection_current_index(self, index: int) -> None:
        self._collection_index = index
        self.collection_overlay.set_current_index(index)
        self._update_picture_in_picture_queue_buttons()

    def set_collection_available(self, available: bool) -> None:
        """有视频在播就允许左侧滑出，即便探测结果是"不属于任何合集"（显示空态）。"""
        self.collection_overlay.set_context_available(available)

    def update_position(self, seconds: float) -> None:
        self._position = max(0.0, float(seconds or 0.0))
        self.position_label.setText(format_seconds(int(seconds)))
        if self._duration > 0 and not self._seeking:
            value = int(max(0, min(1000, seconds / self._duration * 1000)))
            self.progress_slider.setValue(value)
            if not self._picture_in_picture_seeking:
                self.picture_in_picture_progress_slider.setValue(value)
        self.picture_in_picture_position_label.setText(
            f"{format_seconds(self._position)} / {format_seconds(self._duration)}"
        )

    def update_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self.duration_label.setText(format_seconds(int(seconds)))
        self.picture_in_picture_position_label.setText(
            f"{format_seconds(self._position)} / {format_seconds(self._duration)}"
        )

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
        blocked = self.picture_in_picture_volume_slider.blockSignals(True)
        self.picture_in_picture_volume_slider.setValue(volume)
        self.picture_in_picture_volume_slider.blockSignals(blocked)
        self._update_picture_in_picture_dynamic_icons()

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
        self.picture_in_picture_button.setEnabled(enabled and not self._cast_active and not self._cast_pending)
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
        self.picture_in_picture_play_button.setEnabled(enabled)
        self._update_picture_in_picture_dynamic_icons()
        self.picture_in_picture_mute_button.setEnabled(
            enabled and (not self._cast_active or self._cast_volume_supported)
        )
        self.picture_in_picture_volume_slider.setEnabled(
            enabled and (not self._cast_active or self._cast_volume_supported)
        )
        self.picture_in_picture_progress_slider.setEnabled(
            enabled and (not self._cast_active or self._cast_seek_supported)
        )
        self.picture_in_picture_fullscreen_button.setEnabled(enabled)
        self._update_picture_in_picture_queue_buttons()

    def _update_picture_in_picture_queue_buttons(self) -> None:
        if self._playlist_count > 0:
            previous_enabled = self._playlist_index > 0
            next_enabled = 0 <= self._playlist_index < self._playlist_count - 1
        elif self._collection_count > 0:
            previous_enabled = self._collection_index > 0
            next_enabled = 0 <= self._collection_index < self._collection_count - 1
        else:
            previous_enabled = next_enabled = False
        self.picture_in_picture_previous_button.setEnabled(previous_enabled)
        self.picture_in_picture_next_button.setEnabled(next_enabled)

    def _sync_auto_hide_state(self) -> None:
        if self._picture_in_picture:
            self._auto_hide_enabled = False
            self._idle_timer.stop()
            self._set_cursor_hidden(False)
            return
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
        if self._picture_in_picture:
            self._show_picture_in_picture_controls()
            return
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
        if self._picture_in_picture or not self._auto_hide_enabled:
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
        return (
            widget is self.control_panel
            or self.control_panel.isAncestorOf(widget)
            or widget is self.picture_in_picture_control_bar
            or self.picture_in_picture_control_bar.isAncestorOf(widget)
            or widget is self.picture_in_picture_end_overlay
            or self.picture_in_picture_end_overlay.isAncestorOf(widget)
            or (
                widget is not self.picture_in_picture_title_label
                and self.picture_in_picture_title_bar.isAncestorOf(widget)
            )
        )

    def _show_cursor(self) -> None:
        self._set_cursor_hidden(False)

    def _set_cursor_hidden(self, hidden: bool) -> None:
        if self._picture_in_picture:
            self._apply_picture_in_picture_cursor(self._picture_in_picture_cursor_shape)
            return
        cursor = QCursor(Qt.CursorShape.BlankCursor if hidden else Qt.CursorShape.ArrowCursor)
        for widget in (
            self,
            self.video_widget,
            self.control_panel,
            self.playlist_overlay,
            self.collection_overlay,
            self.picture_in_picture_title_bar,
            self.picture_in_picture_control_bar,
        ):
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
            "picture_in_picture": self._shortcut_picture_in_picture,
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

    def _shortcut_picture_in_picture(self) -> None:
        if self._shortcut_context_active() and not self._cast_active:
            self.picture_in_picture_requested.emit()

    def _shortcut_exit_fullscreen(self) -> None:
        if not self._shortcut_context_active():
            return
        if self._picture_in_picture:
            self.picture_in_picture_requested.emit()
        elif self._fullscreen:
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
