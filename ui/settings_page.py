from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
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

from services.config_service import ConfigService, detect_browser_cookie_sources
from services.runtime_install_service import RuntimeStatus
from services.shortcut_service import SHORTCUT_DEFINITIONS


class SettingsPage(QWidget):
    settings_saved = Signal()
    install_node_requested = Signal()
    open_node_site_requested = Signal()

    def __init__(self, config: ConfigService) -> None:
        super().__init__()
        self.config = config

        self.active_proxy_label = QLabel()
        self.system_hint_label = QLabel("代理读取顺序：优先使用系统代理；未检测到系统代理时再使用此处配置。")
        self.system_hint_label.setObjectName("MetaLabel")

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
        default_home_row.addStretch(1)

        self.cookie_edit = QTextEdit()
        self.cookie_edit.setMinimumHeight(150)
        self.cookie_edit.setPlaceholderText(
            "粘贴 Netscape cookies.txt 内容，或浏览器请求头里的 Cookie: a=b; c=d\n"
            "可用于 YouTube / Bilibili；程序会按目标站点自动转换。"
        )

        self.cookie_browser_combo = QComboBox()

        self.cookie_profile_edit = QLineEdit()
        self.cookie_profile_edit.setPlaceholderText("Default / Profile 1")

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
        self.ffmpeg_dir_edit.setPlaceholderText("ffmpeg.exe 所在目录")
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
        form.addRow("当前有效代理", self.active_proxy_label)
        form.addRow("配置代理", self.proxy_edit)
        form.addRow("从浏览器读取 Cookie", self.cookie_browser_combo)
        form.addRow("浏览器 Profile", self.cookie_profile_edit)
        form.addRow("Cookie 内容", self.cookie_edit)
        form.addRow("JS Runtime", self.js_runtime_combo)
        form.addRow("运行时状态", self.js_runtime_status_label)
        form.addRow("", js_actions)
        form.addRow("", self.js_runtime_progress_label)
        form.addRow("视频保存路径", download_dir_row)
        form.addRow("同时下载视频数", self.max_downloads_spin)
        form.addRow("FFmpeg 目录", ffmpeg_dir_row)
        form.addRow("DLNA 媒体服务端口", self.dlna_media_server_port_spin)

        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_layout.setContentsMargins(16, 16, 16, 16)
        general_layout.setSpacing(14)
        general_layout.addWidget(self.system_hint_label)
        general_layout.addLayout(form)
        general_layout.addStretch(1)

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

        self.tabs = QTabWidget()
        self.tabs.addTab(general_tab, "常规")
        self.tabs.addTab(self.shortcut_tab, "快捷键")

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
        self.config.load()
        default_home = self.config.default_home_source()
        self.default_home_bilibili.setChecked(default_home != "youtube")
        self.default_home_youtube.setChecked(default_home == "youtube")
        self.proxy_edit.setText(str(self.config.get("youtube.proxy", "") or ""))
        self.cookie_edit.setPlainText(self._read_cookie_text())
        browser = str(self.config.get("youtube.cookie_browser", "") or "")
        self._populate_cookie_browser_combo(browser)
        index = self.cookie_browser_combo.findData(browser)
        self.cookie_browser_combo.setCurrentIndex(index if index >= 0 else 0)
        self.cookie_profile_edit.setText(str(self.config.get("youtube.cookie_browser_profile", "") or ""))
        runtime = str(self.config.get("youtube.js_runtime", "auto") or "")
        runtime_index = self.js_runtime_combo.findData(runtime)
        self.js_runtime_combo.setCurrentIndex(runtime_index if runtime_index >= 0 else 0)
        self.download_dir_edit.setText(
            str(self.config.get("download.save_dir", self.config.download_dir()) or self.config.download_dir())
        )
        self.ffmpeg_dir_edit.setText(str(self.config.get("download.ffmpeg_dir", "") or ""))
        self.max_downloads_spin.setValue(self.config.download_max_concurrent())
        self.dlna_media_server_port_spin.setValue(self.config.dlna_media_server_port())
        for definition in SHORTCUT_DEFINITIONS:
            sequence = self.config.shortcut_sequence(definition.action)
            self.shortcut_edits[definition.action].setKeySequence(QKeySequence(sequence))
        self.refresh_active_proxy()
        self.js_runtime_progress_label.clear()

    def save(self) -> None:
        shortcuts = self._shortcut_values()
        if shortcuts is None:
            return
        cookie_path = self._cookie_file_path()
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_text(self.cookie_edit.toPlainText().strip(), encoding="utf-8")

        self.config.set("youtube.proxy", self.proxy_edit.text().strip())
        self.config.set("content.default_home", "youtube" if self.default_home_youtube.isChecked() else "bilibili")
        cookie_browser = self.cookie_browser_combo.currentData() or ""
        self.config.set("youtube.cookie_browser", cookie_browser)
        self.config.set(
            "youtube.cookie_browser_profile",
            "" if ":" in cookie_browser else self.cookie_profile_edit.text().strip(),
        )
        self.config.set("youtube.cookie_file", str(cookie_path))
        self.config.set("youtube.js_runtime", self.js_runtime_combo.currentData() or "")
        self.config.set("download.save_dir", self.download_dir_edit.text().strip() or self.config.download_dir())
        self.config.set("download.ffmpeg_dir", self.ffmpeg_dir_edit.text().strip())
        self.config.set("download.max_concurrent", self.max_downloads_spin.value())
        self.config.set("dlna.media_server_port", self.dlna_media_server_port_spin.value())
        for action, sequence in shortcuts.items():
            self.config.set(f"shortcuts.{action}", sequence)
        self.config.save()
        self.config.download_dir()
        self.refresh_active_proxy()
        self.js_runtime_progress_label.clear()
        self.settings_saved.emit()

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
        self.install_node_button.setVisible(not status.available)
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

    def _populate_cookie_browser_combo(self, selected: str) -> None:
        self.cookie_browser_combo.blockSignals(True)
        self.cookie_browser_combo.clear()
        self.cookie_browser_combo.addItem("自动检测（默认浏览器优先）", "auto")
        self.cookie_browser_combo.addItem("不从浏览器读取", "")

        detected = detect_browser_cookie_sources()
        for label, value in detected:
            self.cookie_browser_combo.addItem(label, value)

        fallback = (
            ("Microsoft Edge", "edge"),
            ("Google Chrome", "chrome"),
            ("Firefox", "firefox"),
            ("Brave", "brave"),
            ("Chromium", "chromium"),
            ("Opera", "opera"),
            ("Vivaldi", "vivaldi"),
        )
        existing = {self.cookie_browser_combo.itemData(index) for index in range(self.cookie_browser_combo.count())}
        for label, value in fallback:
            if value not in existing:
                self.cookie_browser_combo.addItem(label, value)

        if selected and self.cookie_browser_combo.findData(selected) < 0:
            self.cookie_browser_combo.addItem(selected, selected)
        self.cookie_browser_combo.blockSignals(False)

    def _cookie_file_path(self) -> Path:
        configured = str(self.config.get("youtube.cookie_file", "") or "").strip()
        if configured:
            return Path(self.config.cookie_file())
        return Path(self.config.default_cookie_file())

    def _read_cookie_text(self) -> str:
        path = self._cookie_file_path()
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
