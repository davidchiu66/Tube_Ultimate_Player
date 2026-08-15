from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QKeySequenceEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from resolver.models import LANGUAGE_NAMES
from services.config_service import (
    PROXY_MODE_AUTO,
    PROXY_MODE_LABELS,
    PROXY_MODES,
    QUALITY_MODES,
    QUALITY_TIER_LABELS,
    QUALITY_TIERS,
    ConfigService,
    detect_browser_cookie_sources,
)
from services.cookie_service import secure_cookie_file
from services.locale_service import system_language_tag
from services.runtime_install_service import RuntimeStatus
from services.shortcut_service import SHORTCUT_DEFINITIONS
from ui.backup_tab import BackupTab


logger = logging.getLogger("tube_player.ui")

# 默认音轨语言下拉的候选。LANGUAGE_NAMES 里还有 zh-cn / zh-tw 这类带地区的键，
# 放进设置页只会让列表变长而选不出差别——语言级的偏好足够匹配到具体音轨。
AUDIO_LANGUAGE_CHOICES = (
    "zh", "zh-hans", "zh-hant", "yue", "en", "ja", "ko",
    "es", "fr", "de", "ru", "pt", "ar", "it", "th", "vi", "id", "hi",
)


class SettingsPage(QWidget):
    settings_saved = Signal()
    install_node_requested = Signal()
    open_node_site_requested = Signal()
    reprobe_cookies_requested = Signal()
    backup_test_requested = Signal(object)
    backup_requested = Signal(object, bool)
    backup_restore_list_requested = Signal(object)
    backup_restore_requested = Signal(object, str)

    def __init__(self, config: ConfigService) -> None:
        super().__init__()
        self.config = config
        self._cookie_texts = {"youtube": "", "bilibili": ""}
        self._cookie_site = "bilibili"
        self._quality_drafts = {"youtube": "high", "bilibili": "high"}
        self._browser_drafts = {"youtube": "auto", "bilibili": "auto"}
        self._profile_drafts = {"youtube": "", "bilibili": ""}
        self._quality_changed_sites: set[str] = set()
        self._browser_changed_sites: set[str] = set()
        self._loading_settings = True
        self._default_quality_changed = False

        self.active_proxy_label = QLabel()
        self.system_hint_label = QLabel(
            "代理模式：自动模式下优先使用此处配置的代理，未配置时才跟随系统代理；"
            "如需忽略系统代理请选择强制直连。"
        )
        self.system_hint_label.setObjectName("MetaLabel")

        self.proxy_mode_combo = QComboBox()
        for mode in PROXY_MODES:
            self.proxy_mode_combo.addItem(PROXY_MODE_LABELS[mode], mode)

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7890 / socks5://127.0.0.1:1080")

        self.default_home_group = QButtonGroup(self)
        self.default_home_bilibili = QRadioButton("Bilibili")
        self.default_home_youtube = QRadioButton("YouTube")
        self.default_home_group.addButton(self.default_home_bilibili)
        self.default_home_group.addButton(self.default_home_youtube)
        default_home_row = QHBoxLayout()
        default_home_row.setContentsMargins(0, 0, 0, 0)
        default_home_row.setSpacing(16)
        default_home_row.addWidget(self.default_home_bilibili)
        default_home_row.addWidget(self.default_home_youtube)
        self.default_home_hint = QLabel("仅决定启动后默认打开的网站")
        self.default_home_hint.setObjectName("MetaLabel")
        default_home_row.addWidget(self.default_home_hint)
        default_home_row.addStretch(1)

        self.site_config_group = QButtonGroup(self)
        self.site_config_bilibili = QRadioButton("Bilibili")
        self.site_config_youtube = QRadioButton("YouTube")
        self.site_config_group.addButton(self.site_config_bilibili)
        self.site_config_group.addButton(self.site_config_youtube)
        self.site_config_bilibili.toggled.connect(
            lambda checked: self._switch_cookie_site("bilibili") if checked else None
        )
        self.site_config_youtube.toggled.connect(
            lambda checked: self._switch_cookie_site("youtube") if checked else None
        )
        site_config_row = QHBoxLayout()
        site_config_row.setContentsMargins(0, 0, 0, 0)
        site_config_row.setSpacing(16)
        site_config_row.addWidget(self.site_config_bilibili)
        site_config_row.addWidget(self.site_config_youtube)
        site_config_row.addStretch(1)

        self.playback_window_group = QButtonGroup(self)
        self.playback_window_windowed = QRadioButton("窗口")
        self.playback_window_fullscreen = QRadioButton("全屏")
        self.playback_window_group.addButton(self.playback_window_windowed)
        self.playback_window_group.addButton(self.playback_window_fullscreen)
        playback_window_row = QHBoxLayout()
        playback_window_row.setContentsMargins(0, 0, 0, 0)
        playback_window_row.setSpacing(16)
        playback_window_row.addWidget(self.playback_window_windowed)
        playback_window_row.addWidget(self.playback_window_fullscreen)
        self.playback_window_hint = QLabel("开始播放时的窗口状态，全屏后可按退出全屏快捷键返回窗口")
        self.playback_window_hint.setObjectName("MetaLabel")
        playback_window_row.addWidget(self.playback_window_hint)
        playback_window_row.addStretch(1)
        self._playback_window_row = playback_window_row

        self.default_quality_combo = QComboBox()
        for mode in QUALITY_MODES:
            self.default_quality_combo.addItem(QUALITY_TIER_LABELS[mode], mode)
        self.default_quality_combo.currentIndexChanged.connect(self._mark_default_quality_changed)
        default_quality_row = QHBoxLayout()
        default_quality_row.setContentsMargins(0, 0, 0, 0)
        default_quality_row.setSpacing(16)
        default_quality_row.addWidget(self.default_quality_combo)
        self.default_quality_hint = QLabel(
            "打开视频时自动选择的清晰度档位；该档位不存在时按最接近的一档"
        )
        self.default_quality_hint.setObjectName("MetaLabel")
        default_quality_row.addWidget(self.default_quality_hint)
        default_quality_row.addStretch(1)
        self._default_quality_row = default_quality_row

        self.default_audio_language_combo = QComboBox()
        # 首项把探测到的系统语言写进文案，用户不必猜"跟随系统"到底跟到了哪一种。
        detected = system_language_tag()
        self.default_audio_language_combo.addItem(f"跟随系统（{detected or '未知'}）", "auto")
        for code in AUDIO_LANGUAGE_CHOICES:
            name = LANGUAGE_NAMES.get(code, code)
            self.default_audio_language_combo.addItem(f"{name}（{code}）", code)
        audio_language_row = QHBoxLayout()
        audio_language_row.setContentsMargins(0, 0, 0, 0)
        audio_language_row.setSpacing(16)
        audio_language_row.addWidget(self.default_audio_language_combo)
        self.default_audio_language_hint = QLabel(
            "多语言配音视频优先选这个语言的音轨；视频没有该语言时回退到原声轨"
        )
        self.default_audio_language_hint.setObjectName("MetaLabel")
        audio_language_row.addWidget(self.default_audio_language_hint)
        audio_language_row.addStretch(1)
        self._audio_language_row = audio_language_row

        self.cookie_edit = QTextEdit()
        self.cookie_edit.setMinimumHeight(150)
        self.cookie_edit.setPlaceholderText(
            "粘贴 Netscape cookies.txt 内容，或浏览器请求头里的 Cookie: a=b; c=d\n"
            "内容会保存到当前网站选择对应的网站 Cookie。"
        )
        self.cookie_content_label = QLabel()

        self.cookie_browser_combo = QComboBox()
        self.cookie_browser_combo.currentIndexChanged.connect(self._mark_cookie_browser_changed)

        self.cookie_probe_label = QLabel()
        self.cookie_probe_label.setObjectName("MetaLabel")
        self.cookie_probe_label.setWordWrap(True)
        self.reprobe_cookie_button = QPushButton("重新检测")
        self.reprobe_cookie_button.clicked.connect(self.reprobe_cookies_requested.emit)
        cookie_probe_row = QHBoxLayout()
        cookie_probe_row.setContentsMargins(0, 0, 0, 0)
        cookie_probe_row.setSpacing(8)
        cookie_probe_row.addWidget(self.cookie_probe_label, 1)
        cookie_probe_row.addWidget(self.reprobe_cookie_button)
        self._cookie_probe_row = cookie_probe_row

        self.cookie_profile_edit = QLineEdit()
        self.cookie_profile_edit.setPlaceholderText("Default / Profile 1")
        self.cookie_profile_edit.textChanged.connect(self._mark_cookie_browser_changed)

        self.js_runtime_combo = QComboBox()
        self.js_runtime_combo.addItem("自动检测", "auto")
        self.js_runtime_combo.addItem("不使用", "")
        self.js_runtime_combo.addItem("Deno", "deno")
        self.js_runtime_combo.addItem("Node.js", "node")
        self.js_runtime_combo.addItem("QuickJS", "quickjs")
        self.js_runtime_combo.addItem("Bun", "bun")

        self.js_runtime_status_label = QLabel()
        self.js_runtime_status_label.setObjectName("MetaLabel")
        self.install_node_button = QPushButton("安装 Node.js")
        self.open_node_site_button = QPushButton("打开官网")
        self.install_node_button.clicked.connect(self.install_node_requested.emit)
        self.open_node_site_button.clicked.connect(self.open_node_site_requested.emit)
        js_actions = QHBoxLayout()
        js_actions.setContentsMargins(0, 0, 0, 0)
        js_actions.setSpacing(8)
        js_actions.addWidget(self.install_node_button)
        js_actions.addWidget(self.open_node_site_button)
        js_actions.addStretch(1)
        self.js_runtime_progress_label = QLabel()
        self.js_runtime_progress_label.setObjectName("MetaLabel")

        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setPlaceholderText(self.config.download_dir())
        browse_download_dir = QPushButton("浏览")
        browse_download_dir.clicked.connect(self._browse_download_dir)
        download_dir_row = QHBoxLayout()
        download_dir_row.setContentsMargins(0, 0, 0, 0)
        download_dir_row.setSpacing(8)
        download_dir_row.addWidget(self.download_dir_edit, 1)
        download_dir_row.addWidget(browse_download_dir)

        self.ffmpeg_dir_edit = QLineEdit()
        self.ffmpeg_dir_edit.setPlaceholderText("FFmpeg 可执行文件所在目录" if not sys.platform.startswith("win") else "ffmpeg.exe 所在目录")
        browse_ffmpeg_dir = QPushButton("浏览")
        browse_ffmpeg_dir.clicked.connect(self._browse_ffmpeg_dir)
        ffmpeg_dir_row = QHBoxLayout()
        ffmpeg_dir_row.setContentsMargins(0, 0, 0, 0)
        ffmpeg_dir_row.setSpacing(8)
        ffmpeg_dir_row.addWidget(self.ffmpeg_dir_edit, 1)
        ffmpeg_dir_row.addWidget(browse_ffmpeg_dir)

        self.max_downloads_spin = QSpinBox()
        self.max_downloads_spin.setRange(1, 10)
        self.max_downloads_spin.setValue(1)

        self.dlna_media_server_port_spin = QSpinBox()
        self.dlna_media_server_port_spin.setRange(1, 65535)
        self.dlna_media_server_port_spin.setValue(8899)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("默认首页", default_home_row)
        form.addRow("网站配置", site_config_row)
        form.addRow("进入播放", self._playback_window_row)
        form.addRow("默认画质", self._default_quality_row)
        form.addRow("默认音轨语言", self._audio_language_row)
        form.addRow("当前有效代理", self.active_proxy_label)
        form.addRow("代理模式", self.proxy_mode_combo)
        form.addRow("配置代理", self.proxy_edit)
        form.addRow("从浏览器读取 Cookie", self.cookie_browser_combo)
        form.addRow("Cookie 检测结果", self._cookie_probe_row)
        form.addRow("浏览器 Profile", self.cookie_profile_edit)
        form.addRow(self.cookie_content_label, self.cookie_edit)
        form.addRow("JS Runtime", self.js_runtime_combo)
        form.addRow("运行时状态", self.js_runtime_status_label)
        form.addRow("", js_actions)
        form.addRow("", self.js_runtime_progress_label)
        form.addRow("视频保存路径", download_dir_row)
        form.addRow("同时下载视频数", self.max_downloads_spin)
        form.addRow("FFmpeg 目录", ffmpeg_dir_row)
        form.addRow("DLNA 媒体服务端口", self.dlna_media_server_port_spin)

        general_content = QWidget()
        general_content.setMinimumWidth(560)
        general_layout = QVBoxLayout(general_content)
        general_layout.setContentsMargins(16, 16, 16, 16)
        general_layout.setSpacing(14)
        general_layout.addWidget(self.system_hint_label)
        general_layout.addLayout(form)
        general_layout.addStretch(1)
        general_tab = QScrollArea()
        general_tab.setWidgetResizable(True)
        general_tab.setFrameShape(QScrollArea.Shape.NoFrame)
        general_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        general_tab.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        general_tab.setWidget(general_content)

        self.shortcut_edits: dict[str, QKeySequenceEdit] = {}
        shortcut_form = QFormLayout()
        shortcut_form.setContentsMargins(0, 0, 0, 0)
        shortcut_form.setHorizontalSpacing(18)
        shortcut_form.setVerticalSpacing(10)
        for definition in SHORTCUT_DEFINITIONS:
            edit = QKeySequenceEdit()
            self.shortcut_edits[definition.action] = edit
            shortcut_form.addRow(definition.label, edit)

        shortcut_content = QWidget()
        shortcut_content.setLayout(shortcut_form)
        shortcut_scroll = QScrollArea()
        shortcut_scroll.setWidgetResizable(True)
        shortcut_scroll.setWidget(shortcut_content)

        shortcut_hint = QLabel("点击快捷键输入框后按下新的组合键。清空输入框可禁用该快捷键；相同快捷键不能分配给多个功能。")
        shortcut_hint.setObjectName("MetaLabel")
        shortcut_hint.setWordWrap(True)
        self.restore_shortcuts_button = QPushButton("恢复默认快捷键")
        self.restore_shortcuts_button.clicked.connect(self._restore_default_shortcuts)

        self.shortcut_tab = QWidget()
        shortcut_layout = QVBoxLayout(self.shortcut_tab)
        shortcut_layout.setContentsMargins(16, 16, 16, 16)
        shortcut_layout.setSpacing(12)
        shortcut_layout.addWidget(shortcut_hint)
        shortcut_layout.addWidget(shortcut_scroll, 1)
        shortcut_actions = QHBoxLayout()
        shortcut_actions.addWidget(self.restore_shortcuts_button)
        shortcut_actions.addStretch(1)
        shortcut_layout.addLayout(shortcut_actions)

        self.backup_tab = BackupTab(self.config)
        self.backup_tab.test_requested.connect(self.backup_test_requested.emit)
        self.backup_tab.backup_requested.connect(self.backup_requested.emit)
        self.backup_tab.restore_list_requested.connect(self.backup_restore_list_requested.emit)
        self.backup_tab.restore_requested.connect(self.backup_restore_requested.emit)

        self.tabs = QTabWidget()
        self.tabs.addTab(general_tab, "常规")
        self.tabs.addTab(self.shortcut_tab, "快捷键")
        self.tabs.addTab(self.backup_tab, "备份/恢复")

        self.save_button = QPushButton("保存设置")
        self.reload_button = QPushButton("重新读取")
        self.save_button.clicked.connect(self.save)
        self.reload_button.clicked.connect(self.load)

        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.reload_button)
        actions.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(actions)

        self.load()

    def load(self) -> None:
        self._loading_settings = True
        self.config.load()
        default_home = self.config.default_home_source()
        self._cookie_texts = {
            "youtube": self._read_cookie_text("youtube"),
            "bilibili": self._read_cookie_text("bilibili"),
        }
        self._quality_drafts = {
            site: self.config.default_quality_mode(site) for site in ("youtube", "bilibili")
        }
        self._browser_drafts = {
            site: self.config.configured_cookie_browser_for_site(site)
            for site in ("youtube", "bilibili")
        }
        self._profile_drafts = {
            site: self.config.cookie_browser_profile_for_site(site)
            for site in ("youtube", "bilibili")
        }
        self._cookie_site = default_home
        self.default_home_bilibili.setChecked(default_home != "youtube")
        self.default_home_youtube.setChecked(default_home == "youtube")
        starts_fullscreen = self.config.playback_starts_fullscreen()
        self.playback_window_windowed.setChecked(not starts_fullscreen)
        self.playback_window_fullscreen.setChecked(starts_fullscreen)
        self.site_config_bilibili.setChecked(default_home != "youtube")
        self.site_config_youtube.setChecked(default_home == "youtube")
        audio_language = str(self.config.get("player.default_audio_language", "auto") or "auto")
        audio_language_index = self.default_audio_language_combo.findData(audio_language)
        self.default_audio_language_combo.setCurrentIndex(
            audio_language_index if audio_language_index >= 0 else 0
        )
        self.proxy_edit.setText(str(self.config.get("youtube.proxy", "") or ""))
        proxy_mode_index = self.proxy_mode_combo.findData(self.config.proxy_mode())
        self.proxy_mode_combo.setCurrentIndex(proxy_mode_index if proxy_mode_index >= 0 else 0)
        self._load_site_draft(default_home)
        self.refresh_cookie_probe_from_config()
        runtime = str(self.config.get("youtube.js_runtime", "auto") or "")
        runtime_index = self.js_runtime_combo.findData(runtime)
        self.js_runtime_combo.setCurrentIndex(runtime_index if runtime_index >= 0 else 0)
        self.download_dir_edit.setText(
            str(self.config.get("download.save_dir", self.config.download_dir()) or self.config.download_dir())
        )
        self.ffmpeg_dir_edit.setText(str(self.config.get("download.ffmpeg_dir", "") or ""))
        self.max_downloads_spin.setValue(self.config.download_max_concurrent())
        self.dlna_media_server_port_spin.setValue(self.config.dlna_media_server_port())
        self.backup_tab.reload()
        for definition in SHORTCUT_DEFINITIONS:
            sequence = self.config.shortcut_sequence(definition.action)
            self.shortcut_edits[definition.action].setKeySequence(QKeySequence(sequence))
        self.refresh_active_proxy()
        self.js_runtime_progress_label.clear()
        self._loading_settings = False
        self._default_quality_changed = False
        self._quality_changed_sites.clear()
        self._browser_changed_sites.clear()

    def save(self) -> None:
        shortcuts = self._shortcut_values()
        if shortcuts is None:
            return
        self._store_site_draft()
        cookie_paths: dict[str, Path] = {}
        cookie_errors: list[str] = []
        for site, text in self._cookie_texts.items():
            cookie_path = self._cookie_file_path(site, for_write=True)
            # Cookie 写盘失败不能中断整个保存：否则默认首页、代理、快捷键等设置
            # 全部一起丢掉，而用户只会看到「设置好像没生效」。
            try:
                cookie_path.parent.mkdir(parents=True, exist_ok=True)
                cookie_path.write_text(text.strip(), encoding="utf-8")
                secure_cookie_file(cookie_path)
            except OSError as exc:
                logger.exception("写入 Cookie 文件失败 site=%s path=%s", site, cookie_path)
                cookie_errors.append(f"{site}: {exc}")
                continue
            cookie_paths[site] = cookie_path

        self.config.set("youtube.proxy", self.proxy_edit.text().strip())
        self.config.set("network.proxy_mode", self.proxy_mode_combo.currentData() or PROXY_MODE_AUTO)
        self.config.set("content.default_home", "youtube" if self.default_home_youtube.isChecked() else "bilibili")
        self.config.set(
            "player.playback_window_mode",
            "fullscreen" if self.playback_window_fullscreen.isChecked() else "window",
        )
        for site in self._quality_changed_sites:
            mode = self._quality_drafts.get(site) or QUALITY_MODES[0]
            self.config.set(f"player.default_quality_by_site.{site}", mode)
        if self._quality_changed_sites:
            # 旧调用方和旧版本只能看到全局值；固定跟随保存后的默认首页，避免同时修改
            # 两站时由 set 的无序迭代决定结果。新版本始终优先使用上面的分站键。
            legacy_site = "youtube" if self.default_home_youtube.isChecked() else "bilibili"
            self.config.set(
                "player.default_quality",
                self._quality_drafts.get(legacy_site) or QUALITY_MODES[0],
            )
        self.config.set(
            "player.default_audio_language",
            self.default_audio_language_combo.currentData() or "auto",
        )
        for site in self._browser_changed_sites:
            cookie_browser = self._browser_drafts.get(site, "")
            self.config.set(f"cookies.{site}.browser", cookie_browser)
            self.config.set(
                f"cookies.{site}.browser_profile",
                "" if ":" in cookie_browser else self._profile_drafts.get(site, "").strip(),
            )
        for site, cookie_path in cookie_paths.items():
            self.config.set(f"cookies.{site}.file", str(cookie_path))
        self.config.set("youtube.js_runtime", self.js_runtime_combo.currentData() or "")
        self.config.set("download.save_dir", self.download_dir_edit.text().strip() or self.config.download_dir())
        self.config.set("download.ffmpeg_dir", self.ffmpeg_dir_edit.text().strip())
        self.config.set("download.max_concurrent", self.max_downloads_spin.value())
        self.config.set("dlna.media_server_port", self.dlna_media_server_port_spin.value())
        self.config.set("backup.include_cookies", self.backup_tab.include_cookies.isChecked())
        for action, sequence in shortcuts.items():
            self.config.set(f"shortcuts.{action}", sequence)
        self.config.save()
        self.config.download_dir()
        self.refresh_active_proxy()
        self.js_runtime_progress_label.clear()
        self.settings_saved.emit()
        self._default_quality_changed = False
        self._quality_changed_sites.clear()
        self._browser_changed_sites.clear()
        if cookie_errors:
            QMessageBox.warning(
                self,
                "Cookie 保存失败",
                "其余设置已保存，但以下站点的 Cookie 文件写入失败：\n" + "\n".join(cookie_errors),
            )

    def _mark_default_quality_changed(self, _index: int) -> None:
        if not self._loading_settings:
            self._default_quality_changed = True
            self._quality_changed_sites.add(self._cookie_site)

    def _mark_cookie_browser_changed(self, *_args) -> None:
        self._update_cookie_profile_enabled()
        if not self._loading_settings:
            self._browser_changed_sites.add(self._cookie_site)

    def _update_cookie_profile_enabled(self) -> None:
        browser = str(self.cookie_browser_combo.currentData() or "")
        self.cookie_profile_edit.setEnabled(bool(browser and browser != "auto" and ":" not in browser))

    def set_backup_busy(self, busy: bool, text: str = "") -> None:
        self.backup_tab.set_busy(busy, text)

    def set_backup_progress(self, text: str) -> None:
        self.backup_tab.set_progress(text)

    def report_backup_result(self, ok: bool, message: str) -> None:
        self.backup_tab.report_result(ok, message)

    def show_remote_backups(self, backups: list) -> None:
        self.backup_tab.show_backups(backups)

    def _shortcut_values(self) -> dict[str, str] | None:
        values: dict[str, str] = {}
        assigned: dict[str, str] = {}
        labels = {definition.action: definition.label for definition in SHORTCUT_DEFINITIONS}
        for definition in SHORTCUT_DEFINITIONS:
            sequence = self.shortcut_edits[definition.action].keySequence().toString(
                QKeySequence.SequenceFormat.PortableText
            ).strip()
            normalized = sequence.casefold()
            if normalized and normalized in assigned:
                previous = assigned[normalized]
                QMessageBox.warning(
                    self,
                    "快捷键冲突",
                    f"“{labels[previous]}”和“{definition.label}”使用了相同快捷键：{sequence}",
                )
                self.tabs.setCurrentWidget(self.shortcut_tab)
                return None
            if normalized:
                assigned[normalized] = definition.action
            values[definition.action] = sequence
        return values

    def _restore_default_shortcuts(self) -> None:
        for definition in SHORTCUT_DEFINITIONS:
            self.shortcut_edits[definition.action].setKeySequence(QKeySequence(definition.default))

    def refresh_active_proxy(self) -> None:
        source, proxy = self.config.effective_proxy()
        self.active_proxy_label.setText(f"{source}: {proxy}" if proxy else source)

    def set_runtime_status(self, status: RuntimeStatus) -> None:
        self.js_runtime_status_label.setText(status.display_text)
        self.install_node_button.setVisible(not status.available and status.automatic_install_supported)
        self.open_node_site_button.setVisible(not status.available)
        if status.available:
            self.js_runtime_progress_label.clear()

    def set_runtime_install_busy(self, busy: bool, text: str = "") -> None:
        self.install_node_button.setEnabled(not busy)
        self.open_node_site_button.setEnabled(not busy)
        self.reload_button.setEnabled(not busy)
        if text:
            self.js_runtime_progress_label.setText(text)

    def set_runtime_install_progress(self, text: str) -> None:
        self.js_runtime_progress_label.setText(text)

    def _populate_cookie_browser_combo(self, _selected: str) -> None:
        self.cookie_browser_combo.blockSignals(True)
        self.cookie_browser_combo.clear()
        self.cookie_browser_combo.addItem("自动检测（按站点选择已登录的浏览器）", "auto")
        self.cookie_browser_combo.addItem("不从浏览器读取", "")

        detected = detect_browser_cookie_sources()
        for label, value in detected:
            self.cookie_browser_combo.addItem(label, value)
        self.cookie_browser_combo.blockSignals(False)

    def set_cookie_probe_result(
        self,
        result: dict[str, str],
        missing: list[str],
        unreadable: list[str] | None = None,
    ) -> None:
        """展示启动探测的结果；某站点没找到时给出手动配置引导。"""
        labels = {"bilibili": "Bilibili", "youtube": "YouTube"}
        parts = [f"{labels[site]}：{spec}" for site, spec in result.items() if spec]
        if unreadable:
            # 运行中的 Chromium 会独占 Cookies 库，读不到不等于没登录。
            parts.append(f"以下浏览器正在运行、无法读取：{'、'.join(unreadable)}（关闭后可重新检测）")
        if missing:
            names = "、".join(labels[site] for site in missing)
            parts.append(f"未找到 {names} 的登录 Cookie，请在下方手动粘贴 Cookie")
        self.cookie_probe_label.setText("；".join(parts) if parts else "尚未检测")

    def refresh_cookie_probe_from_config(self) -> None:
        result = {
            site: str(self.config.get(f"cookies.{site}.auto_browser", "") or "").strip()
            for site in ("bilibili", "youtube")
        }
        missing = [site for site, spec in result.items() if not spec]
        if not any(self.config.cookie_auto_probe_enabled(site) for site in ("bilibili", "youtube")):
            self.cookie_probe_label.setText("仅在「自动检测」模式下生效")
            self.reprobe_cookie_button.setEnabled(False)
            return
        self.reprobe_cookie_button.setEnabled(True)
        self.set_cookie_probe_result(result, missing)

    def _switch_cookie_site(self, site: str) -> None:
        if self._loading_settings or site == self._cookie_site:
            return
        self._store_site_draft()
        self._cookie_site = site
        self._load_site_draft(site)

    def _load_site_draft(self, site: str) -> None:
        was_loading = self._loading_settings
        self._loading_settings = True
        try:
            quality_index = self.default_quality_combo.findData(self._quality_drafts.get(site, "high"))
            self.default_quality_combo.setCurrentIndex(quality_index if quality_index >= 0 else 0)
            browser = self._browser_drafts.get(site, "auto")
            self._populate_cookie_browser_combo(browser)
            browser_index = self.cookie_browser_combo.findData(browser)
            self.cookie_browser_combo.setCurrentIndex(browser_index if browser_index >= 0 else 0)
            self.cookie_profile_edit.setText(self._profile_drafts.get(site, ""))
            self._update_cookie_profile_enabled()
            self.cookie_edit.setPlainText(self._cookie_texts.get(site, ""))
            self._update_cookie_content_label()
        finally:
            self._loading_settings = was_loading

    def _store_site_draft(self) -> None:
        self._quality_drafts[self._cookie_site] = self.default_quality_combo.currentData() or "high"
        browser = self.cookie_browser_combo.currentData() or ""
        self._browser_drafts[self._cookie_site] = browser
        self._profile_drafts[self._cookie_site] = "" if ":" in browser else self.cookie_profile_edit.text()
        self._store_cookie_draft()

    def _store_cookie_draft(self) -> None:
        self._cookie_texts[self._cookie_site] = self.cookie_edit.toPlainText()

    def _update_cookie_content_label(self) -> None:
        label = "Bilibili" if self._cookie_site == "bilibili" else "YouTube"
        self.cookie_content_label.setText(f"{label} Cookie 内容")

    def _cookie_file_path(self, site: str, *, for_write: bool = False) -> Path:
        # 必须走 cookie_file_path 而不是 cookie_file：后者对空文件返回空串，
        # Path("") 会变成当前目录，写入时报 PermissionError: '.'。
        configured = str(self.config.get(f"cookies.{site}.file", "") or "").strip()
        if configured or not for_write:
            return Path(self.config.cookie_file_path(site))
        return Path(self.config.default_cookie_file(site))

    def _read_cookie_text(self, site: str) -> str:
        path = self._cookie_file_path(site)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _browse_download_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择视频保存路径",
            self.download_dir_edit.text().strip() or self.config.download_dir(),
        )
        if path:
            self.download_dir_edit.setText(path)

    def _browse_ffmpeg_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 FFmpeg 目录",
            self.ffmpeg_dir_edit.text().strip() or "",
        )
        if path:
            self.ffmpeg_dir_edit.setText(path)
