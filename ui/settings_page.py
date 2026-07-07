from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services.config_service import ConfigService, detect_browser_cookie_sources


class SettingsPage(QWidget):
    settings_saved = Signal()

    def __init__(self, config: ConfigService) -> None:
        super().__init__()
        self.config = config

        self.active_proxy_label = QLabel()
        self.system_hint_label = QLabel(
            "读取顺序：系统代理优先；未检测到系统代理时使用下方配置代理。"
        )
        self.system_hint_label.setObjectName("MetaLabel")

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7890 / socks5://127.0.0.1:1080")

        self.cookie_edit = QTextEdit()
        self.cookie_edit.setMinimumHeight(150)
        self.cookie_edit.setPlaceholderText(
            "Netscape cookies.txt 内容，或浏览器请求头中的 Cookie: a=b; c=d"
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

        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setPlaceholderText(self.config.download_dir())
        browse_download_dir = QPushButton("浏览")
        browse_download_dir.clicked.connect(self._browse_download_dir)
        download_dir_row = QHBoxLayout()
        download_dir_row.addWidget(self.download_dir_edit, 1)
        download_dir_row.addWidget(browse_download_dir)

        self.ffmpeg_dir_edit = QLineEdit()
        self.ffmpeg_dir_edit.setPlaceholderText("ffmpeg.exe 所在目录")
        browse_ffmpeg_dir = QPushButton("浏览")
        browse_ffmpeg_dir.clicked.connect(self._browse_ffmpeg_dir)
        ffmpeg_dir_row = QHBoxLayout()
        ffmpeg_dir_row.addWidget(self.ffmpeg_dir_edit, 1)
        ffmpeg_dir_row.addWidget(browse_ffmpeg_dir)

        self.max_downloads_spin = QSpinBox()
        self.max_downloads_spin.setRange(1, 10)
        self.max_downloads_spin.setValue(1)

        form = QFormLayout()
        form.addRow("当前有效代理", self.active_proxy_label)
        form.addRow("配置代理", self.proxy_edit)
        form.addRow("从浏览器读取 Cookie", self.cookie_browser_combo)
        form.addRow("浏览器 Profile", self.cookie_profile_edit)
        form.addRow("Cookie 内容", self.cookie_edit)
        form.addRow("JS Runtime", self.js_runtime_combo)
        form.addRow("视频保存路径", download_dir_row)
        form.addRow("同时下载视频数", self.max_downloads_spin)
        form.addRow("FFmpeg 目录", ffmpeg_dir_row)

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
        layout.addWidget(self.system_hint_label)
        layout.addLayout(form)
        layout.addStretch()
        layout.addLayout(actions)

        self.load()

    def load(self) -> None:
        self.config.load()
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
        self.download_dir_edit.setText(str(self.config.get("download.save_dir", self.config.download_dir()) or self.config.download_dir()))
        self.ffmpeg_dir_edit.setText(str(self.config.get("download.ffmpeg_dir", "") or ""))
        self.max_downloads_spin.setValue(self.config.download_max_concurrent())
        self.refresh_active_proxy()

    def save(self) -> None:
        cookie_path = self._cookie_file_path()
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_text(self.cookie_edit.toPlainText().strip(), encoding="utf-8")

        self.config.set("youtube.proxy", self.proxy_edit.text().strip())
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
        self.config.save()
        self.config.download_dir()
        self.refresh_active_proxy()
        self.settings_saved.emit()

    def refresh_active_proxy(self) -> None:
        source, proxy = self.config.effective_proxy()
        self.active_proxy_label.setText(f"{source}: {proxy}" if proxy else source)

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
