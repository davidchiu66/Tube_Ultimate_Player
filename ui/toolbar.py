from __future__ import annotations

from math import cos, pi, sin
from typing import Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QWidget,
)

from services.site_registry import SITE_DEFINITIONS, normalize_site

try:
    import qtawesome as qta
except ImportError:  # pragma: no cover
    qta = None


ICON_COLOR = "#f3f4f6"


def _make_icon(name: str, size: int = 18) -> QIcon:
    if qta is not None:
        try:
            return qta.icon(name, color=ICON_COLOR)
        except Exception:
            pass
    return _draw_fallback_icon(name, size)


def _draw_fallback_icon(name: str, size: int) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(ICON_COLOR))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setWidthF(max(1.4, size / 11))
    painter.setPen(pen)

    draw_map = {
        "fa5s.search": _draw_search_icon,
        "fa5s.link": _draw_link_icon,
        "fa5s.home": _draw_home_icon,
        "fa5s.list": _draw_list_icon,
        "fa5s.play-circle": _draw_play_circle_icon,
        "fa5s.download": _draw_download_icon,
        "fa5s.star": _draw_star_icon,
        "fa5s.history": _draw_history_icon,
        "fa5s.cog": _draw_cog_icon,
        "fa5s.info-circle": _draw_info_icon,
        "fa5s.thumbtack": _draw_thumbtack_icon,
    }
    drawer = draw_map.get(name, _draw_info_icon)
    drawer(painter, float(size))
    painter.end()
    return QIcon(pixmap)


def _draw_search_icon(painter: QPainter, size: float) -> None:
    painter.drawEllipse(QRectF(size * 0.14, size * 0.14, size * 0.48, size * 0.48))
    painter.drawLine(QPointF(size * 0.54, size * 0.54), QPointF(size * 0.82, size * 0.82))


def _draw_link_icon(painter: QPainter, size: float) -> None:
    painter.drawArc(QRectF(size * 0.11, size * 0.27, size * 0.34, size * 0.28), 20 * 16, 220 * 16)
    painter.drawArc(QRectF(size * 0.55, size * 0.45, size * 0.34, size * 0.28), 200 * 16, 220 * 16)
    painter.drawLine(QPointF(size * 0.36, size * 0.62), QPointF(size * 0.63, size * 0.35))


def _draw_home_icon(painter: QPainter, size: float) -> None:
    path = QPainterPath()
    path.moveTo(size * 0.14, size * 0.46)
    path.lineTo(size * 0.5, size * 0.14)
    path.lineTo(size * 0.86, size * 0.46)
    path.moveTo(size * 0.24, size * 0.4)
    path.lineTo(size * 0.24, size * 0.82)
    path.lineTo(size * 0.76, size * 0.82)
    path.lineTo(size * 0.76, size * 0.4)
    painter.drawPath(path)


def _draw_list_icon(painter: QPainter, size: float) -> None:
    for y in (0.3, 0.5, 0.7):
        painter.drawPoint(QPointF(size * 0.24, size * y))
        painter.drawLine(QPointF(size * 0.38, size * y), QPointF(size * 0.78, size * y))


def _draw_play_circle_icon(painter: QPainter, size: float) -> None:
    painter.drawEllipse(QRectF(size * 0.12, size * 0.12, size * 0.76, size * 0.76))
    path = QPainterPath()
    path.moveTo(size * 0.42, size * 0.33)
    path.lineTo(size * 0.42, size * 0.67)
    path.lineTo(size * 0.69, size * 0.5)
    path.closeSubpath()
    painter.fillPath(path, QColor(ICON_COLOR))


def _draw_download_icon(painter: QPainter, size: float) -> None:
    painter.drawLine(QPointF(size * 0.5, size * 0.16), QPointF(size * 0.5, size * 0.62))
    painter.drawLine(QPointF(size * 0.34, size * 0.47), QPointF(size * 0.5, size * 0.64))
    painter.drawLine(QPointF(size * 0.66, size * 0.47), QPointF(size * 0.5, size * 0.64))
    painter.drawLine(QPointF(size * 0.22, size * 0.78), QPointF(size * 0.78, size * 0.78))


def _draw_star_icon(painter: QPainter, size: float) -> None:
    outer = size * 0.34
    inner = outer * 0.45
    cx = cy = size * 0.5
    path = QPainterPath()
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = -pi / 2 + index * pi / 5
        point = QPointF(cx + cos(angle) * radius, cy + sin(angle) * radius)
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    painter.drawPath(path)


def _draw_history_icon(painter: QPainter, size: float) -> None:
    painter.drawArc(QRectF(size * 0.18, size * 0.18, size * 0.62, size * 0.62), 35 * 16, 290 * 16)
    painter.drawLine(QPointF(size * 0.18, size * 0.3), QPointF(size * 0.18, size * 0.14))
    painter.drawLine(QPointF(size * 0.18, size * 0.14), QPointF(size * 0.32, size * 0.14))
    painter.drawLine(QPointF(size * 0.5, size * 0.33), QPointF(size * 0.5, size * 0.5))
    painter.drawLine(QPointF(size * 0.5, size * 0.5), QPointF(size * 0.63, size * 0.57))


def _draw_cog_icon(painter: QPainter, size: float) -> None:
    painter.drawEllipse(QRectF(size * 0.3, size * 0.3, size * 0.4, size * 0.4))
    center = QPointF(size * 0.5, size * 0.5)
    for index in range(8):
        angle = index * pi / 4
        outer = QPointF(center.x() + cos(angle) * size * 0.37, center.y() + sin(angle) * size * 0.37)
        inner = QPointF(center.x() + cos(angle) * size * 0.25, center.y() + sin(angle) * size * 0.25)
        painter.drawLine(inner, outer)


def _draw_info_icon(painter: QPainter, size: float) -> None:
    painter.drawEllipse(QRectF(size * 0.13, size * 0.13, size * 0.74, size * 0.74))
    painter.drawPoint(QPointF(size * 0.5, size * 0.33))
    painter.drawLine(QPointF(size * 0.5, size * 0.43), QPointF(size * 0.5, size * 0.67))


def _draw_thumbtack_icon(painter: QPainter, size: float) -> None:
    painter.drawLine(QPointF(size * 0.5, size * 0.50), QPointF(size * 0.5, size * 0.84))
    painter.drawLine(QPointF(size * 0.33, size * 0.24), QPointF(size * 0.67, size * 0.24))
    painter.drawLine(QPointF(size * 0.28, size * 0.24), QPointF(size * 0.72, size * 0.42))
    painter.drawLine(QPointF(size * 0.72, size * 0.24), QPointF(size * 0.28, size * 0.42))
    painter.drawLine(QPointF(size * 0.38, size * 0.42), QPointF(size * 0.62, size * 0.42))


class ToolbarButton(QPushButton):
    def __init__(self, text: str, icon_name: str, tooltip: str, parent=None) -> None:
        super().__init__(text, parent)
        self.full_text = text
        self.setObjectName("ToolbarButton")
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setMinimumWidth(72)
        self.setIcon(_make_icon(icon_name, 18))
        self.setIconSize(QSize(18, 18))
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

    def set_compact_mode(self, icon_only: bool) -> None:
        self.setText("" if icon_only else self.full_text)
        self.setMinimumWidth(42 if icon_only else 72)
        self.setMaximumWidth(46 if icon_only else 118)


class SearchBox(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolbarSearchBox")
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.leading_icon = QLabel()
        self.leading_icon.setObjectName("ToolbarSearchIcon")
        self.leading_icon.setFixedSize(24, 24)
        self.leading_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.leading_icon.setPixmap(_make_icon("fa5s.search", 18).pixmap(18, 18))

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("ToolbarSearchEdit")
        self.search_edit.setFixedHeight(36)
        self.search_edit.setPlaceholderText("搜索在线视频")
        self.search_edit.setClearButtonEnabled(False)
        self.search_edit.setFrame(False)
        self.search_edit.setMinimumWidth(120)
        self.search_edit.installEventFilter(self)

        self.trailing_icon = QPushButton()
        self.trailing_icon.setObjectName("ToolbarSearchTrigger")
        self.trailing_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.trailing_icon.setFixedSize(36, 36)
        self.trailing_icon.setToolTip("搜索在线视频")
        self.trailing_icon.setIcon(_make_icon("fa5s.search", 18))
        self.trailing_icon.setIconSize(QSize(18, 18))

        separator = QFrame()
        separator.setObjectName("ToolbarSeparator")
        separator.setFixedWidth(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 2, 4, 2)
        layout.setSpacing(10)
        layout.addWidget(self.leading_icon)
        layout.addWidget(self.search_edit, 1)
        layout.addWidget(separator)
        layout.addWidget(self.trailing_icon)

    def set_compact_mode(self, compact: bool) -> None:
        self.leading_icon.setVisible(not compact)
        # 五个单字符站点标识需要留出空间；紧凑模式把搜索输入压到更小，
        # 避免 DPI/小窗口下挤掉右侧导航按钮。
        self.search_edit.setMinimumWidth(80 if compact else 220)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.search_edit:
            if event.type() == QEvent.Type.FocusIn:
                self._set_focus_state(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._set_focus_state(False)
        return super().eventFilter(watched, event)

    def _set_focus_state(self, focused: bool) -> None:
        self.setProperty("focus", focused)
        self.style().unpolish(self)
        self.style().polish(self)


class SourceSelector(QFrame):
    """工具栏里的站点单选组：切换首页/搜索所用的视频网站。

    这里选的站点只作用于本次会话的浏览行为，不写回「默认首页」配置项。
    """

    source_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolbarSourceSelector")
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self._radios: dict[str, QRadioButton] = {}

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 2, 10, 2)
        self._layout.setSpacing(10)
        for definition in SITE_DEFINITIONS:
            radio = QRadioButton(definition.label)
            radio.setObjectName("ToolbarSourceRadio")
            radio.setToolTip(f"首页与搜索使用 {definition.label}")
            radio.setCursor(Qt.CursorShape.PointingHandCursor)
            self._radios[definition.key] = radio
            setattr(self, f"{definition.key}_radio", radio)
            self.button_group.addButton(radio)
            self._layout.addWidget(radio)
            radio.toggled.connect(
                lambda checked, source=definition.key: self._emit_if_checked(checked, source)
            )
        self._radios["bilibili"].setChecked(True)

    def set_compact_mode(self, compact: bool) -> None:
        """窄窗口下换用短标签并收紧边距，把空间让给搜索框与导航按钮。

        与 ToolbarButton 的做法一致；全称仍留在 tooltip 里。
        """
        for definition in SITE_DEFINITIONS:
            self._radios[definition.key].setText(definition.compact_label if compact else definition.label)
            self._radios[definition.key].setMinimumWidth(22 if compact else 58)
            self._radios[definition.key].setMaximumWidth(30 if compact else 90)
        margin = 2 if compact else 10
        self._layout.setContentsMargins(margin, 2, margin, 2)
        self._layout.setSpacing(2 if compact else 10)

    def source(self) -> str:
        for source, radio in self._radios.items():
            if radio.isChecked():
                return source
        return "bilibili"

    def set_source(self, source: str) -> None:
        """静默同步选中态，不触发 source_changed（用于外部状态回填）。"""
        target = self._radios[normalize_site(source, "bilibili")]
        if target.isChecked():
            return
        for radio in self._radios.values():
            radio.blockSignals(True)
        target.setChecked(True)
        for radio in self._radios.values():
            radio.blockSignals(False)

    def set_frozen(self, frozen: bool) -> None:
        """冻结站点切换，避免首页/搜索切站期间提交新的并发浏览任务。"""
        self.setEnabled(not bool(frozen))
        self.setToolTip("网站正在加载，请稍候" if frozen else "选择首页与搜索使用的网站")

    def _emit_if_checked(self, checked: bool, source: str) -> None:
        if checked:
            self.source_changed.emit(source)


class PlayerToolbar(QWidget):
    search_requested = Signal(str)
    source_changed = Signal(str)
    url_requested = Signal()
    home_clicked = Signal()
    playlist_clicked = Signal()
    player_clicked = Signal()
    download_clicked = Signal()
    favorite_clicked = Signal()
    history_clicked = Signal()
    settings_clicked = Signal()
    about_clicked = Signal()
    topmost_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerToolbar")
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._compact_mode: str | None = None

        self.search_box = SearchBox(self)
        self.search_edit = self.search_box.search_edit
        self.source_selector = SourceSelector(self)
        self.search_button = ToolbarButton("搜索", "fa5s.search", "搜索在线视频", self)
        self.url_button = ToolbarButton("播放URL", "fa5s.link", "打开网络视频地址", self)
        self.home_button = ToolbarButton("首页", "fa5s.home", "查看首页视频列表", self)
        self.playlist_button = ToolbarButton("播放列表", "fa5s.list", "查看播放列表", self)
        self.player_button = ToolbarButton("播放器", "fa5s.play-circle", "返回播放器界面", self)
        self.download_button = ToolbarButton("下载列表", "fa5s.download", "查看下载任务", self)
        self.favorite_button = ToolbarButton("收藏", "fa5s.star", "查看收藏视频", self)
        self.history_button = ToolbarButton("历史", "fa5s.history", "查看播放历史", self)
        self.settings_button = ToolbarButton("设置", "fa5s.cog", "打开设置", self)
        self.about_button = ToolbarButton("关于", "fa5s.info-circle", "查看应用信息", self)
        self.topmost_button = ToolbarButton("置顶", "fa5s.thumbtack", "窗口置顶", self)
        self.topmost_button.setCheckable(True)

        self.search_box.trailing_icon.clicked.connect(self._emit_search)
        self.source_selector.source_changed.connect(self.source_changed)
        self.search_button.clicked.connect(self._emit_search)
        self.url_button.clicked.connect(self.url_requested.emit)
        self.home_button.clicked.connect(self.home_clicked.emit)
        self.playlist_button.clicked.connect(self.playlist_clicked.emit)
        self.player_button.clicked.connect(self.player_clicked.emit)
        self.download_button.clicked.connect(self.download_clicked.emit)
        self.favorite_button.clicked.connect(self.favorite_clicked.emit)
        self.history_button.clicked.connect(self.history_clicked.emit)
        self.settings_button.clicked.connect(self.settings_clicked.emit)
        self.about_button.clicked.connect(self.about_clicked.emit)
        self.topmost_button.clicked.connect(self.topmost_clicked.emit)
        self.search_edit.returnPressed.connect(self._emit_search)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        layout.addWidget(self.source_selector)
        layout.addWidget(self.search_box, 1)
        layout.addWidget(self.search_button)
        layout.addWidget(self.url_button)
        layout.addWidget(self.home_button)
        layout.addWidget(self.playlist_button)
        layout.addWidget(self.player_button)
        layout.addWidget(self.download_button)
        layout.addWidget(self.favorite_button)
        layout.addWidget(self.history_button)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.about_button)
        layout.addWidget(self.topmost_button)

        self._setup_shortcuts()
        self._set_responsive_mode("icon")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_responsive_mode()

    def set_search_text(self, text: str) -> None:
        self.search_edit.setText(text)

    def source(self) -> str:
        return self.source_selector.source()

    def set_source(self, source: str) -> None:
        self.source_selector.set_source(source)

    def set_source_switch_frozen(self, frozen: bool) -> None:
        self.source_selector.set_frozen(frozen)

    def set_topmost_state(self, enabled: bool) -> None:
        self.topmost_button.setChecked(enabled)
        self.topmost_button.setToolTip("取消置顶" if enabled else "窗口置顶")

    def _emit_search(self) -> None:
        self.search_requested.emit(self.search_edit.text().strip())

    def _setup_shortcuts(self) -> None:
        self._shortcut("Ctrl+F", self.focus_search)
        self._shortcut("Ctrl+L", self.url_requested.emit)
        self._shortcut("Ctrl+H", self.history_clicked.emit)
        self._shortcut("Ctrl+,", self.settings_clicked.emit)

    def focus_search(self) -> None:
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def _shortcut(self, sequence: str, callback: Callable[[], None]) -> None:
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.activated.connect(callback)

    def _update_responsive_mode(self) -> None:
        width = self.width()
        mode = "icon" if width < 1220 else "full"
        if mode == self._compact_mode:
            return
        self._set_responsive_mode(mode)

    def _set_responsive_mode(self, mode: str) -> None:
        self._compact_mode = mode
        icon_only = mode == "icon"
        self.source_selector.set_compact_mode(icon_only)
        self.search_box.set_compact_mode(icon_only)
        self.source_selector.setMaximumWidth(168 if icon_only else 360)
        for button in (
            self.search_button,
            self.url_button,
            self.home_button,
            self.playlist_button,
            self.player_button,
            self.download_button,
            self.favorite_button,
            self.history_button,
            self.settings_button,
            self.about_button,
            self.topmost_button,
        ):
            button.set_compact_mode(icon_only)
