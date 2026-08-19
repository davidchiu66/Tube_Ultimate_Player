"""Pure geometry helpers for the in-app picture-in-picture window."""

from __future__ import annotations

import math
from enum import IntFlag

from PySide6.QtCore import QPoint, QRect, QSize, Qt


DEFAULT_ASPECT = 16 / 9
MIN_WIDTH = 320
MIN_HEIGHT = 180
MAX_WIDTH = 1920
MAX_HEIGHT = 1080
EDGE_HOT_ZONE = 8
SNAP_THRESHOLD = 20


class PictureInPictureResizeEdge(IntFlag):
    NONE = 0
    LEFT = 1
    TOP = 2
    RIGHT = 4
    BOTTOM = 8


def cursor_shape_for_resize_edge(edge: PictureInPictureResizeEdge) -> Qt.CursorShape:
    if edge in (PictureInPictureResizeEdge.LEFT, PictureInPictureResizeEdge.RIGHT):
        return Qt.CursorShape.SizeHorCursor
    if edge in (PictureInPictureResizeEdge.TOP, PictureInPictureResizeEdge.BOTTOM):
        return Qt.CursorShape.SizeVerCursor
    if edge in (
        PictureInPictureResizeEdge.LEFT | PictureInPictureResizeEdge.TOP,
        PictureInPictureResizeEdge.RIGHT | PictureInPictureResizeEdge.BOTTOM,
    ):
        return Qt.CursorShape.SizeFDiagCursor
    if edge in (
        PictureInPictureResizeEdge.RIGHT | PictureInPictureResizeEdge.TOP,
        PictureInPictureResizeEdge.LEFT | PictureInPictureResizeEdge.BOTTOM,
    ):
        return Qt.CursorShape.SizeBDiagCursor
    return Qt.CursorShape.ArrowCursor


def normalized_aspect(value: float | int | None, fallback: float = DEFAULT_ASPECT) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(result) or result <= 0.1 or result > 10:
        return fallback
    return result


def _size_for_width(width: int, aspect: float) -> QSize:
    return QSize(max(1, int(width)), max(1, int(round(width / aspect))))


def _size_for_height(height: int, aspect: float) -> QSize:
    return QSize(max(1, int(round(height * aspect))), max(1, int(height)))


def minimum_size_for_aspect(
    aspect: float,
    min_width: int = MIN_WIDTH,
    min_height: int = MIN_HEIGHT,
) -> QSize:
    aspect = normalized_aspect(aspect)
    width = max(1, int(min_width))
    height = max(1, int(min_height))
    if width / aspect < height:
        return _size_for_height(height, aspect)
    return _size_for_width(width, aspect)


def maximum_size_for_screen(available: QRect, aspect: float) -> QSize:
    aspect = normalized_aspect(aspect)
    width_limit = min(MAX_WIDTH, max(1, int(round(available.width() * 0.5))))
    height_limit = min(MAX_HEIGHT, max(1, available.height()))
    by_width = _size_for_width(width_limit, aspect)
    by_height = _size_for_height(height_limit, aspect)
    return by_height if by_height.width() <= width_limit else QSize(width_limit, by_width.height())


def constrain_size(size: QSize, aspect: float, available: QRect) -> QSize:
    """Clamp to aspect-preserving min/max bounds and the available work area."""
    aspect = normalized_aspect(aspect)
    minimum = minimum_size_for_aspect(aspect)
    maximum = maximum_size_for_screen(available, aspect)
    width = max(1, int(size.width()))
    height = max(1, int(size.height()))
    by_width = _size_for_width(width, aspect)
    by_height = _size_for_height(height, aspect)
    candidate = min(
        (by_width, by_height),
        key=lambda item: abs(item.width() - width) + abs(item.height() - height),
    )
    width = max(minimum.width(), min(maximum.width(), candidate.width()))
    height = max(minimum.height(), min(maximum.height(), candidate.height()))
    candidate = _size_for_width(width, aspect)
    if candidate.height() > maximum.height():
        candidate = _size_for_height(maximum.height(), aspect)
    if candidate.width() < minimum.width() or candidate.height() < minimum.height():
        candidate = minimum
    return candidate


def _bounded_size_for_width(width: int, aspect: float, available: QRect) -> QSize:
    minimum = minimum_size_for_aspect(aspect)
    maximum = maximum_size_for_screen(available, aspect)
    width = max(minimum.width(), min(maximum.width(), int(width)))
    height = int(round(width / normalized_aspect(aspect)))
    if height > maximum.height():
        height = maximum.height()
        width = int(round(height * normalized_aspect(aspect)))
    if height < minimum.height():
        height = minimum.height()
        width = int(round(height * normalized_aspect(aspect)))
    return QSize(width, height)


def _bounded_size_for_height(height: int, aspect: float, available: QRect) -> QSize:
    minimum = minimum_size_for_aspect(aspect)
    maximum = maximum_size_for_screen(available, aspect)
    height = max(minimum.height(), min(maximum.height(), int(height)))
    width = int(round(height * normalized_aspect(aspect)))
    if width > maximum.width():
        width = maximum.width()
        height = int(round(width / normalized_aspect(aspect)))
    if width < minimum.width():
        width = minimum.width()
        height = int(round(width / normalized_aspect(aspect)))
    return QSize(width, height)


def clamp_geometry_to_screen(rect: QRect, available: QRect) -> QRect:
    width = min(rect.width(), available.width())
    height = min(rect.height(), available.height())
    x = max(available.left(), min(rect.left(), available.right() - width + 1))
    y = max(available.top(), min(rect.top(), available.bottom() - height + 1))
    return QRect(x, y, width, height)


def snap_geometry_to_edges(
    rect: QRect,
    available: QRect,
    threshold: int = SNAP_THRESHOLD,
) -> QRect:
    result = clamp_geometry_to_screen(rect, available)
    left_distance = abs(result.left() - available.left())
    right_distance = abs(available.right() - result.right())
    top_distance = abs(result.top() - available.top())
    bottom_distance = abs(available.bottom() - result.bottom())
    x = result.x()
    y = result.y()
    if left_distance < threshold:
        x = available.left()
    elif right_distance < threshold:
        x = available.right() - result.width() + 1
    if top_distance < threshold:
        y = available.top()
    elif bottom_distance < threshold:
        y = available.bottom() - result.height() + 1
    return QRect(x, y, result.width(), result.height())


def initial_geometry(
    available: QRect,
    aspect: float = DEFAULT_ASPECT,
    saved: QRect | None = None,
    *,
    margin: int = 24,
    initial_width: int = 640,
    initial_height: int = 360,
) -> QRect:
    aspect = normalized_aspect(aspect)
    if saved is not None and saved.isValid() and saved.width() > 0 and saved.height() > 0:
        size = constrain_size(saved.size(), aspect, available)
        return clamp_geometry_to_screen(QRect(saved.topLeft(), size), available)
    size = constrain_size(QSize(initial_width, initial_height), aspect, available)
    x = available.right() - size.width() + 1 - max(0, int(margin))
    y = available.bottom() - size.height() + 1 - max(0, int(margin))
    return clamp_geometry_to_screen(QRect(x, y, size.width(), size.height()), available)


def resize_geometry(
    start: QRect,
    edge: PictureInPictureResizeEdge,
    cursor: QPoint,
    aspect: float,
    available: QRect,
) -> QRect:
    """Resize from an edge/corner while keeping the opposite anchor fixed."""
    if edge == PictureInPictureResizeEdge.NONE:
        return start
    aspect = normalized_aspect(aspect)
    left = start.left()
    top = start.top()
    right = start.right()
    bottom = start.bottom()
    horizontal = bool(edge & (PictureInPictureResizeEdge.LEFT | PictureInPictureResizeEdge.RIGHT))
    vertical = bool(edge & (PictureInPictureResizeEdge.TOP | PictureInPictureResizeEdge.BOTTOM))
    width_candidate = start.width()
    height_candidate = start.height()
    if edge & PictureInPictureResizeEdge.LEFT:
        width_candidate = max(1, right - cursor.x() + 1)
    elif edge & PictureInPictureResizeEdge.RIGHT:
        width_candidate = max(1, cursor.x() - left + 1)
    if edge & PictureInPictureResizeEdge.TOP:
        height_candidate = max(1, bottom - cursor.y() + 1)
    elif edge & PictureInPictureResizeEdge.BOTTOM:
        height_candidate = max(1, cursor.y() - top + 1)

    if horizontal and vertical:
        width_change = abs(width_candidate / max(1, start.width()) - 1.0)
        height_change = abs(height_candidate / max(1, start.height()) - 1.0)
        if height_change > width_change:
            size = _bounded_size_for_height(height_candidate, aspect, available)
        else:
            size = _bounded_size_for_width(width_candidate, aspect, available)
    elif horizontal:
        size = _bounded_size_for_width(width_candidate, aspect, available)
    else:
        size = _bounded_size_for_height(height_candidate, aspect, available)

    if edge & PictureInPictureResizeEdge.LEFT:
        left = right - size.width() + 1
    else:
        right = left + size.width() - 1
    if edge & PictureInPictureResizeEdge.TOP:
        top = bottom - size.height() + 1
    else:
        bottom = top + size.height() - 1
    result = clamp_geometry_to_screen(QRect(QPoint(left, top), QPoint(right, bottom)), available)
    return result


def resize_edge_at(rect: QRect, point: QPoint, hot_zone: int = EDGE_HOT_ZONE) -> PictureInPictureResizeEdge:
    edge = PictureInPictureResizeEdge.NONE
    if abs(point.x() - rect.left()) <= hot_zone:
        edge |= PictureInPictureResizeEdge.LEFT
    elif abs(point.x() - rect.right()) <= hot_zone:
        edge |= PictureInPictureResizeEdge.RIGHT
    if abs(point.y() - rect.top()) <= hot_zone:
        edge |= PictureInPictureResizeEdge.TOP
    elif abs(point.y() - rect.bottom()) <= hot_zone:
        edge |= PictureInPictureResizeEdge.BOTTOM
    return edge
