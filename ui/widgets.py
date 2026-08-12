"""几个页面共用的小控件。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QWidget


class NoScrollComboBox(QComboBox):
    """未获得焦点时忽略滚轮，避免误改选中项。

    去掉"加载"按钮、改成"切换即加载"之后，`QComboBox` 默认的滚轮行为后果被放大了：
    原本滚错了再点一次就好，现在滚一下就直接换掉整个列表。播放器浮层里的下拉框紧邻
    可滚动的长列表，用户滚列表时极易扫到它，所以这层防护是"切换即加载"的前置条件。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 仍然允许点击/Tab 获得焦点——聚焦之后滚轮恢复正常，不牺牲可用性。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt 命名
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
