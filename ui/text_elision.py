from __future__ import annotations

from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel


def elide_multiline_text(label: QLabel, text: str, width: int, max_lines: int) -> str:
    """Wrap text to a fixed line count and replace overflow with three dots."""
    source = " ".join(str(text or "").split())
    if not source or max_lines <= 0:
        return ""

    line_width = max(1, int(width))
    metrics = label.fontMetrics()
    lines = _wrap_text(metrics, source, line_width, max_lines + 1)
    if not lines:
        return _elide_with_three_dots(metrics, source, line_width)

    visible_lines = lines[:max_lines]
    result = [line.strip() for line in visible_lines]
    has_overflow = len(lines) > max_lines
    if has_overflow:
        overflow_text = " ".join(lines[max_lines - 1 :]).strip()
        result[-1] = _elide_with_three_dots(metrics, overflow_text, line_width)
    return "\n".join(result)


def _wrap_text(metrics: QFontMetrics, text: str, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    last_space = -1
    for index, char in enumerate(text):
        candidate = current + char
        if not current or _text_width(metrics, candidate) <= width:
            current = candidate
            if char.isspace():
                last_space = len(current) - 1
            continue

        if last_space > 0:
            line = current[:last_space].rstrip()
            remainder = current[last_space + 1 :] + char
        else:
            line = current.rstrip()
            remainder = char
        if line:
            lines.append(line)
            if len(lines) >= max_lines:
                lines.append(remainder + text[index + 1 :])
                return lines
        current = remainder.lstrip()
        last_space = max((index for index, value in enumerate(current) if value.isspace()), default=-1)

    if current.strip():
        lines.append(current.rstrip())
    return lines


def _elide_with_three_dots(metrics: QFontMetrics, text: str, width: int) -> str:
    source = str(text or "").strip()
    suffix = "..."
    if _text_width(metrics, source) <= width:
        return source

    available = width - _text_width(metrics, suffix)
    if available <= 0:
        return suffix

    low, high = 0, len(source)
    while low < high:
        middle = (low + high + 1) // 2
        if _text_width(metrics, source[:middle]) <= available:
            low = middle
        else:
            high = middle - 1
    return f"{source[:low].rstrip()}{suffix}"


def _text_width(metrics: QFontMetrics, text: str) -> int:
    width = metrics.horizontalAdvance(text)
    if not text:
        return width
    fallback = max(1, metrics.averageCharWidth())
    char_widths = [metrics.horizontalAdvance(char) for char in text]
    if width > 0 and all(char_width > 0 or char.isspace() for char, char_width in zip(text, char_widths)):
        return width
    total = 0
    for char, char_width in zip(text, char_widths):
        total += char_width if char_width > 0 else fallback
    return total
