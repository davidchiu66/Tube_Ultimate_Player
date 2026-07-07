from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from resolver.models import HomeVideo
from ui.player_page import format_seconds


CARD_SIZE = 220
THUMBNAIL_SIZE = 126
GRID_SPACING = 14


class HomeVideoCard(QFrame):
    clicked = Signal(object)
    double_clicked = Signal(object)
    favorite_requested = Signal(object)

    def __init__(self, video: HomeVideo, network: QNetworkAccessManager) -> None:
        super().__init__()
        self.video = video
        self._network = network

        self.setObjectName("HomeVideoCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(CARD_SIZE, CARD_SIZE)
        self.installEventFilter(self)

        self.favorite_button = QPushButton("收藏")
        self.favorite_button.setFixedHeight(28)
        self.favorite_button.clicked.connect(lambda: self.favorite_requested.emit(self.video))

        self.thumbnail_label = QLabel()
        self.thumbnail_label.setObjectName("HomeThumbnail")
        self.thumbnail_label.setFixedSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setText("加载封面...")

        self.title_label = QLabel(video.title)
        self.title_label.setObjectName("HomeVideoTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setFixedHeight(60)

        meta = []
        if video.uploader:
            meta.append(video.uploader)
        if video.duration:
            meta.append(format_seconds(video.duration))
        self.meta_label = QLabel(" | ".join(meta) if meta else video.video_id)
        self.meta_label.setObjectName("MetaLabel")
        self.meta_label.setFixedHeight(22)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addWidget(self.favorite_button)
        action_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(action_row)
        layout.addWidget(self.thumbnail_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.title_label)
        layout.addWidget(self.meta_label)
        layout.addStretch(1)

        self._load_thumbnail()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self:
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit(self)
                return True
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self.double_clicked.emit(self)
                return True
        return super().eventFilter(watched, event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_favorite(self, favorite: bool) -> None:
        self.favorite_button.setText("已收藏" if favorite else "收藏")
        self.favorite_button.setEnabled(not favorite)

    def _load_thumbnail(self) -> None:
        if not self.video.thumbnail:
            self.thumbnail_label.setText("无封面")
            return
        reply = self._network.get(QNetworkRequest(QUrl(self.video.thumbnail)))
        reply.finished.connect(lambda: self._thumbnail_finished(reply))

    @Slot()
    def _thumbnail_finished(self, reply: QNetworkReply) -> None:
        data = reply.readAll()
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.thumbnail_label.setPixmap(
                pixmap.scaled(
                    self.thumbnail_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.thumbnail_label.setText("封面加载失败")
        reply.deleteLater()


class HomePage(QWidget):
    refresh_requested = Signal()
    play_requested = Signal(str)
    favorite_requested = Signal(object)
    page_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        self._selected_card: HomeVideoCard | None = None
        self._cards: list[HomeVideoCard] = []
        self._favorite_ids: set[str] = set()
        self._network = QNetworkAccessManager(self)
        self._mode = "home"
        self._keyword = ""
        self._page = 1
        self._has_next = False

        self.title_label = QLabel("首页")
        self.title_label.setObjectName("PageTitle")
        self.refresh_button = QPushButton("刷新")
        self.play_button = QPushButton("播放选中")
        self.prev_button = QPushButton("上一页")
        self.next_button = QPushButton("下一页")
        self.page_label = QLabel("")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label = QLabel("正在加载 YouTube 首页...")
        self.status_label.setObjectName("MetaLabel")
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.hide()

        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.prev_button)
        header.addWidget(self.page_label)
        header.addWidget(self.next_button)
        header.addSpacing(8)
        header.addWidget(self.refresh_button)
        header.addWidget(self.play_button)

        self.grid_host = QWidget()
        self.grid_layout = QGridLayout(self.grid_host)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setHorizontalSpacing(GRID_SPACING)
        self.grid_layout.setVerticalSpacing(GRID_SPACING)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.grid_host)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self.status_label)
        layout.addWidget(self.loading_bar)
        layout.addWidget(self.scroll_area, 1)

        self.refresh_button.clicked.connect(self.refresh_requested)
        self.play_button.clicked.connect(self._play_selected)
        self.prev_button.clicked.connect(lambda: self.page_requested.emit(max(1, self._page - 1)))
        self.next_button.clicked.connect(lambda: self.page_requested.emit(self._page + 1))
        self.play_button.setEnabled(False)
        self._update_pagination()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout_cards()

    def mode(self) -> str:
        return self._mode

    def keyword(self) -> str:
        return self._keyword

    def page(self) -> int:
        return self._page

    def set_loading(self, loading: bool, message: str = "") -> None:
        self._loading = loading
        self.refresh_button.setEnabled(not loading)
        self.loading_bar.setVisible(loading)
        self._update_play_button()
        self._update_pagination()
        if loading:
            self.status_label.setText(message or "正在加载内容，请稍等...")

    def set_home_context(self, page: int = 1, has_next: bool = False) -> None:
        self._mode = "home"
        self._keyword = ""
        self._page = max(1, page)
        self._has_next = has_next
        self.title_label.setText("首页")
        self._update_pagination()

    def set_search_context(self, keyword: str, page: int, has_next: bool) -> None:
        self._mode = "search"
        self._keyword = keyword
        self._page = max(1, page)
        self._has_next = has_next
        self.title_label.setText(f"搜索结果：{keyword}")
        self._update_pagination()

    def set_videos(
        self,
        videos: list[HomeVideo],
        *,
        mode: str = "home",
        keyword: str = "",
        page: int = 1,
        has_next: bool = False,
    ) -> None:
        self._finish_loading()
        if mode == "search":
            self.set_search_context(keyword, page, has_next)
        else:
            self.set_home_context(page, has_next)

        self._clear_cards()
        for video in videos:
            card = HomeVideoCard(video, self._network)
            card.clicked.connect(self._select_card)
            card.double_clicked.connect(self._play_card)
            card.favorite_requested.connect(self.favorite_requested)
            card.set_favorite(video.video_id in self._favorite_ids)
            self._cards.append(card)

        self._relayout_cards()
        if self._mode == "search":
            self.status_label.setText(f"搜索“{keyword}”第 {page} 页，共加载 {len(videos)} 个视频")
        else:
            self.status_label.setText(f"首页第 {page} 页，共加载 {len(videos)} 个视频")
        if self._cards:
            self._select_card(self._cards[0])
        self._update_play_button()
        self._update_pagination()

    def set_error(self, message: str) -> None:
        self._finish_loading()
        prefix = "搜索失败" if self._mode == "search" else "首页加载失败"
        self.status_label.setText(f"{prefix}：{message}")
        self._update_play_button()
        self._update_pagination()

    def set_favorite_ids(self, favorite_ids: set[str]) -> None:
        self._favorite_ids = set(favorite_ids)
        for card in self._cards:
            card.set_favorite(card.video.video_id in self._favorite_ids)

    def video_count(self) -> int:
        return len(self._cards)

    def _select_card(self, card: HomeVideoCard) -> None:
        if self._selected_card:
            self._selected_card.set_selected(False)
        self._selected_card = card
        card.set_selected(True)
        self._update_play_button()

    def _play_selected(self) -> None:
        if self._selected_card:
            self._play_card(self._selected_card)

    def _play_card(self, card: HomeVideoCard) -> None:
        if card.video.webpage_url:
            self.play_requested.emit(card.video.webpage_url)

    def _update_play_button(self) -> None:
        self.play_button.setEnabled(not self._loading and self._selected_card is not None)

    def _update_pagination(self) -> None:
        self.prev_button.setVisible(True)
        self.next_button.setVisible(True)
        self.page_label.setVisible(True)
        self.prev_button.setEnabled(not self._loading and self._page > 1)
        self.next_button.setEnabled(not self._loading and self._has_next)
        prefix = "首页" if self._mode == "home" else "搜索"
        self.page_label.setText(f"{prefix} 第 {self._page} 页")

    def _finish_loading(self) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        self.loading_bar.hide()

    def _clear_cards(self) -> None:
        self._selected_card = None
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._cards.clear()

    def _relayout_cards(self) -> None:
        while self.grid_layout.count():
            self.grid_layout.takeAt(0)
        if not self._cards:
            return

        viewport_width = max(1, self.scroll_area.viewport().width() - 8)
        columns = max(1, (viewport_width + GRID_SPACING) // (CARD_SIZE + GRID_SPACING))
        for index, card in enumerate(self._cards):
            row, col = divmod(index, columns)
            self.grid_layout.addWidget(card, row, col)
