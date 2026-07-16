from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from dlna.controller import DlnaController
from dlna.discovery import discover_devices, probe_cached_devices
from dlna.models import DlnaDevice


logger = logging.getLogger("tube_player.dlna")


class DlnaDiscoverySignals(QObject):
    success = Signal(object)
    error = Signal(str)
    finished = Signal()


class DlnaDiscoveryWorker(QRunnable):
    def __init__(self, timeout: float = 3.0) -> None:
        super().__init__()
        self.timeout = timeout
        self.signals = DlnaDiscoverySignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.success.emit(discover_devices(self.timeout))
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("DLNA discovery worker failed")
            self.signals.error.emit(detail)
        finally:
            self.signals.finished.emit()


class DlnaDeviceProbeWorker(QRunnable):
    def __init__(self, devices: list[DlnaDevice], timeout: float = 0.6) -> None:
        super().__init__()
        self.devices = list(devices)
        self.timeout = timeout
        self.signals = DlnaDiscoverySignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.success.emit(probe_cached_devices(self.devices, self.timeout))
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("DLNA cached device probe worker failed")
            self.signals.error.emit(detail)
        finally:
            self.signals.finished.emit()


class DlnaActionSignals(QObject):
    success = Signal(int, str, object)
    error = Signal(int, str, str)
    finished = Signal(int)


class DlnaActionWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        controller: DlnaController,
        device: DlnaDevice,
        action: str,
        *arguments,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.controller = controller
        self.device = device
        self.action = action
        self.arguments = arguments
        self.signals = DlnaActionSignals()

    @Slot()
    def run(self) -> None:
        try:
            method = getattr(self.controller, self.action)
            result = method(self.device, *self.arguments)
            self.signals.success.emit(self.request_id, self.action, result)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("DLNA action worker failed action=%s device=%s", self.action, self.device.friendly_name)
            self.signals.error.emit(self.request_id, self.action, detail)
        finally:
            self.signals.finished.emit(self.request_id)
