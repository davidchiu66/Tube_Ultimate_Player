from __future__ import annotations

from PySide6.QtCore import QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from dlna.models import DlnaDevice
from workers.dlna_worker import DlnaDeviceProbeWorker, DlnaDiscoveryWorker


class DlnaCastDialog(QDialog):
    devices_updated = Signal(object)

    def __init__(
        self,
        title: str,
        discovery_timeout: float = 3.0,
        parent=None,
        cached_devices: list[DlnaDevice] | None = None,
        cached_probe_timeout: float = 0.6,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("投屏到 DLNA 设备")
        self.resize(680, 420)
        self._title = title
        self._discovery_timeout = discovery_timeout
        self._cached_devices = list(cached_devices or [])
        self._cached_probe_timeout = cached_probe_timeout
        self._discovery_worker: DlnaDiscoveryWorker | None = None
        self._probe_worker: DlnaDeviceProbeWorker | None = None
        self._scan_after_probe = False

        title_label = QLabel(title)
        title_label.setObjectName("TitleLabel")
        title_label.setWordWrap(True)
        initial_status = "正在检测已缓存的投屏设备..." if self._cached_devices else "正在搜索局域网设备..."
        self.status_label = QLabel(initial_status)
        self.status_label.setObjectName("MetaLabel")

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["设备", "地址", "型号"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_cast_button)
        self.table.itemDoubleClicked.connect(lambda _item, _column: self._accept_selected())

        self.refresh_button = QPushButton("刷新")
        self.cast_button = QPushButton("投屏")
        self.cast_button.setEnabled(False)
        close_button = QPushButton("取消")
        self.refresh_button.clicked.connect(self._refresh_discovery)
        self.cast_button.clicked.connect(self._accept_selected)
        close_button.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        actions.addWidget(self.cast_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(title_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)

        QTimer.singleShot(0, self._start_initial_load)

    def selected_device(self) -> DlnaDevice | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, DlnaDevice) else None

    def _start_initial_load(self) -> None:
        if self._cached_devices:
            self._start_cached_probe()
        else:
            self.start_discovery()

    def _start_cached_probe(self) -> None:
        if self._is_busy():
            return
        self.table.setRowCount(0)
        self.status_label.setText("正在检测已缓存的投屏设备...")
        self.progress.show()
        self.refresh_button.setEnabled(False)
        self.cast_button.setEnabled(False)
        worker = DlnaDeviceProbeWorker(self._cached_devices, self._cached_probe_timeout)
        self._probe_worker = worker
        worker.signals.success.connect(self._cached_devices_probed)
        worker.signals.error.connect(self._cached_probe_failed)
        worker.signals.finished.connect(self._cached_probe_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def _refresh_discovery(self) -> None:
        self.start_discovery()

    def start_discovery(self) -> None:
        if self._is_busy():
            return
        self.table.setRowCount(0)
        self.status_label.setText("正在搜索局域网内的 DLNA 播放设备...")
        self.progress.show()
        self.refresh_button.setEnabled(False)
        self.cast_button.setEnabled(False)
        worker = DlnaDiscoveryWorker(self._discovery_timeout)
        self._discovery_worker = worker
        worker.signals.success.connect(self._devices_found)
        worker.signals.error.connect(self._discovery_failed)
        worker.signals.finished.connect(self._discovery_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _devices_found(self, devices: list[DlnaDevice]) -> None:
        self._show_devices(devices, from_cache=False)
        self.devices_updated.emit(list(devices))

    def _show_devices(self, devices: list[DlnaDevice], *, from_cache: bool) -> None:
        self.table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            name_item = QTableWidgetItem(device.friendly_name)
            name_item.setData(Qt.ItemDataRole.UserRole, device)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(device.host))
            self.table.setItem(row, 2, QTableWidgetItem(device.display_model or "-"))
        if devices:
            self.table.selectRow(0)
            if from_cache:
                self.status_label.setText(f"缓存中有 {len(devices)} 台在线投屏设备")
            else:
                self.status_label.setText(f"发现 {len(devices)} 台可投屏设备")
        else:
            self.status_label.setText("没有发现 DLNA 设备，请确认电视与电脑在同一局域网并允许应用通过防火墙")

    @Slot(object)
    def _cached_devices_probed(self, devices: list[DlnaDevice]) -> None:
        if devices:
            self._cached_devices = list(devices)
            self._show_devices(devices, from_cache=True)
            self.devices_updated.emit(list(devices))
            return
        self._scan_after_probe = True
        self.status_label.setText("缓存设备当前不可用，正在重新搜索局域网设备...")

    @Slot(str)
    def _cached_probe_failed(self, _message: str) -> None:
        self._scan_after_probe = True
        self.status_label.setText("缓存设备检测失败，正在重新搜索局域网设备...")

    @Slot()
    def _cached_probe_finished(self) -> None:
        self._probe_worker = None
        if self._scan_after_probe:
            self._scan_after_probe = False
            self.start_discovery()
            return
        self.progress.hide()
        self.refresh_button.setEnabled(True)
        self._update_cast_button()

    @Slot(str)
    def _discovery_failed(self, message: str) -> None:
        self.status_label.setText(f"设备搜索失败：{message}")

    @Slot()
    def _discovery_finished(self) -> None:
        self._discovery_worker = None
        self.progress.hide()
        self.refresh_button.setEnabled(True)
        self._update_cast_button()

    def _update_cast_button(self) -> None:
        self.cast_button.setEnabled(not self._is_busy() and self.selected_device() is not None)

    def _is_busy(self) -> bool:
        return self._discovery_worker is not None or self._probe_worker is not None

    def _accept_selected(self) -> None:
        if self.selected_device() is not None:
            self.accept()
