from __future__ import annotations

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

from services.config_service import (
    PROXY_MODE_AUTO,
    PROXY_MODE_LABELS,
    PROXY_MODES,
    ConfigService,
    detect_browser_cookie_sources,
)
from services.cookie_service import secure_cookie_file
from services.runtime_install_service import RuntimeStatus
from services.shortcut_service import SHORTCUT_DEFINITIONS


class SettingsPage(QWidget):
    settings_saved = Signal()
    install_node_requested = Signal()
    open_node_site_requested = Signal()
    reprobe_cookies_requested = Signal()

    def __init__(self, config: ConfigService) -> None:
        super().__init__()
        self.config = config
        self._cookie_texts = {"youtube": "", "bilibili": ""}
        self._cookie_site = "bilibili"
        self._loading_settings = True

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
        self.default_home_bilibili.toggled.connect(
            lambda checked: self._switch_cookie_site("bilibili") if checked else None
        )
        self.default_home_youtube.toggled.connect(
            lambda checked: self._switch_cookie_site("youtube") if checked else None
        )
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
            "内容会保存到当前默认首页对应的网站 Cookie。"
        )
        self.cookie_content_label = QLabel()

        self.cookie_browser_combo = QComboBox()

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
        self._loading_settings = True
        self.config.load()
        default_home = self.config.default_home_source()
        self._cookie_texts = {
            "youtube": self._read_cookie_text("youtube"),
            "bilibili": self._read_cookie_text("bilibili"),
        }
        self._cookie_site = default_home
        self.default_home_bilibili.setChecked(default_home != "youtube")
        self.default_home_youtube.setChecked(default_home == "youtube")
        self.proxy_edit.setText(str(self.config.get("youtube.proxy", "") or ""))
        proxy_mode_index = self.proxy_mode_combo.findData(self.config.proxy_mode())
        self.proxy_mode_combo.setCurrentIndex(proxy_mode_index if proxy_mode_index >= 0 else 0)
        self.cookie_edit.setPlainText(self._cookie_texts[default_home])
        self._update_cookie_content_label()
        browser = str(self.config.get("youtube.cookie_browser", "") or "")
        self._populate_cookie_browser_combo(browser)
        index = self.cookie_browser_combo.findData(browser)
        self.cookie_browser_combo.setCurrentIndex(index if index >= 0 else 0)
        self.cookie_profile_edit.setText(str(self.config.get("youtube.cookie_browser_profile", "") or ""))
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
        for definition in SHORTCUT_DEFINITIONS:
            sequence = self.config.shortcut_sequence(definition.action)
            self.shortcut_edits[definition.action].setKeySequence(QKeySequence(sequence))
        self.refresh_active_proxy()
        self.js_runtime_progress_label.clear()
        self._loading_settings = False

    def save(self) -> None:
        shortcuts = self._shortcut_values()
        if shortcuts is None:
            return
        self._store_cookie_draft()
        cookie_paths: dict[str, Path] = {}
        for site, text in self._cookie_texts.items():
            cookie_path = self._cookie_file_path(site, for_write=True)
            cookie_path.parent.mkdir(parents=True, exist_ok=True)
            cookie_path.write_text(text.strip(), encoding="utf-8")
            secure_cookie_file(cookie_path)
            cookie_paths[site] = cookie_path

        self.config.set("youtube.proxy", self.proxy_edit.text().strip())
        self.config.set("network.proxy_mode", self.proxy_mode_combo.currentData() or PROXY_MODE_AUTO)
        self.config.set("content.default_home", "youtube" if self.default_home_youtube.isChecked() else "bilibili")
        cookie_browser = self.cookie_browser_combo.currentData() or ""
        self.config.set("youtube.cookie_browser", cookie_browser)
        self.config.set(
            "youtube.cookie_browser_profile",
            "" if ":" in cookie_browser else self.cookie_profile_edit.text().strip(),
        )
        for site, cookie_path in cookie_paths.items():
            self.config.set(f"cookies.{site}.file", str(cookie_path))
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
        if not self.config.cookie_auto_probe_enabled():
            self.cookie_probe_label.setText("仅在「自动检测」模式下生效")
            self.reprobe_cookie_button.setEnabled(False)
            return
        self.reprobe_cookie_button.setEnabled(True)
        self.set_cookie_probe_result(result, missing)

    def _switch_cookie_site(self, site: str) -> None:
        if self._loading_settings or site == self._cookie_site:
            return
        self._store_cookie_draft()
        self._cookie_site = site
        self.cookie_edit.setPlainText(self._cookie_texts.get(site, ""))
        self._update_cookie_content_label()

    def _store_cookie_draft(self) -> None:
        self._cookie_texts[self._cookie_site] = self.cookie_edit.toPlainText()

    def _update_cookie_content_label(self) -> None:
        label = "Bilibili" if self._cookie_site == "bilibili" else "YouTube"
        self.cookie_content_label.setText(f"{label} Cookie 内容")

    def _cookie_file_path(self, site: str, *, for_write: bool = False) -> Path:
        configured = str(self.config.get(f"cookies.{site}.file", "") or "").strip()
        if configured:
            return Path(self.config.cookie_file(site))
        if not for_write:
            legacy_or_configured = self.config.cookie_file(site)
            if legacy_or_configured:
                return Path(legacy_or_configured)
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
