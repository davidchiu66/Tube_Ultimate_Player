from __future__ import annotations

from PySide6.QtGui import QFontMetrics, QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel


def elide_multiline_text(label: QLabel, text: str, width: int, max_lines: int) -> str:
    """Wrap text to a fixed line count and replace overflow with three dots."""
    source = " ".join(str(text or "").split())
    if not source or max_lines <= 0:
        return ""

    line_width = max(1, int(width))
    layout = QTextLayout(source, label.font())
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)

    lines: list[tuple[int, int]] = []
    layout.beginLayout()
    try:
        while len(lines) <= max_lines:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(line_width)
            lines.append((line.textStart(), line.textLength()))
    finally:
        layout.endLayout()

    if not lines:
        return _elide_with_three_dots(label.fontMetrics(), source, line_width)

    visible_lines = lines[:max_lines]
    result = [source[start : start + length].strip() for start, length in visible_lines]
    last_start, last_length = visible_lines[-1]
    has_overflow = len(lines) > max_lines or last_start + last_length < len(source)
    if has_overflow:
        result[-1] = _elide_with_three_dots(label.fontMetrics(), source[last_start:], line_width)
    return "\n".join(result)


def _elide_with_three_dots(metrics: QFontMetrics, text: str, width: int) -> str:
    source = str(text or "").strip()
    suffix = "..."
    if metrics.horizontalAdvance(source) <= width:
        return source

    available = width - metrics.horizontalAdvance(suffix)
    if available <= 0:
        return suffix

    low, high = 0, len(source)
    while low < high:
        middle = (low + high + 1) // 2
        if metrics.horizontalAdvance(source[:middle]) <= available:
            low = middle
        else:
            high = middle - 1
    return f"{source[:low].rstrip()}{suffix}"
