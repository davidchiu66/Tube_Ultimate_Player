from __future__ import annotations

from collections import OrderedDict

from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel


# 字体度量非常昂贵，按"字体 -> 单字符宽度"缓存，避免同一批列表项重复度量。
_MAX_FONT_CACHES = 8
_FONT_CACHES: OrderedDict[str, "_WidthCache"] = OrderedDict()


class _WidthCache:
    """按字符缓存宽度，并用缓存值累加出字符串宽度。"""

    __slots__ = ("_metrics", "_fallback", "_widths")

    def __init__(self, metrics: QFontMetrics) -> None:
        self._metrics = metrics
        self._fallback = max(1, metrics.averageCharWidth())
        self._widths: dict[str, int] = {}

    def char_width(self, char: str) -> int:
        width = self._widths.get(char)
        if width is None:
            measured = self._metrics.horizontalAdvance(char)
            width = measured if measured > 0 else self._fallback
            self._widths[char] = width
        return width

    def text_width(self, text: str) -> int:
        return sum(self.char_width(char) for char in text)


def _width_cache(label: QLabel) -> _WidthCache:
    metrics = label.fontMetrics()
    try:
        key = f"{label.font().toString()}@{label.logicalDpiX()}"
    except Exception:
        key = ""
    if not key:
        return _WidthCache(metrics)
    cache = _FONT_CACHES.get(key)
    if cache is None:
        cache = _WidthCache(metrics)
        _FONT_CACHES[key] = cache
        while len(_FONT_CACHES) > _MAX_FONT_CACHES:
            _FONT_CACHES.popitem(last=False)
    else:
        _FONT_CACHES.move_to_end(key)
    return cache


def elide_multiline_text(label: QLabel, text: str, width: int, max_lines: int) -> str:
    """Wrap text to a fixed line count and replace overflow with three dots."""
    source = " ".join(str(text or "").split())
    if not source or max_lines <= 0:
        return ""

    line_width = max(1, int(width))
    cache = _width_cache(label)
    lines = _wrap_text(cache, source, line_width, max_lines + 1)
    if not lines:
        return _elide_with_three_dots(cache, source, line_width)

    visible_lines = lines[:max_lines]
    result = [line.strip() for line in visible_lines]
    has_overflow = len(lines) > max_lines
    if has_overflow:
        overflow_text = " ".join(lines[max_lines - 1 :]).strip()
        result[-1] = _elide_with_three_dots(cache, overflow_text, line_width)
    return "\n".join(result)


def _wrap_text(cache: _WidthCache, text: str, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    current_width = 0
    last_space = -1
    for index, char in enumerate(text):
        char_width = cache.char_width(char)
        if not current or current_width + char_width <= width:
            current += char
            current_width += char_width
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
        current_width = cache.text_width(current)
        last_space = max((offset for offset, value in enumerate(current) if value.isspace()), default=-1)

    if current.strip():
        lines.append(current.rstrip())
    return lines


def _elide_with_three_dots(cache: _WidthCache, text: str, width: int) -> str:
    source = str(text or "").strip()
    suffix = "..."
    if cache.text_width(source) <= width:
        return source

    available = width - cache.text_width(suffix)
    if available <= 0:
        return suffix

    # 前缀宽度是单调递增的，二分找到能放下的最长前缀；宽度用前缀和避免重复求和。
    prefix_widths = [0]
    for char in source:
        prefix_widths.append(prefix_widths[-1] + cache.char_width(char))

    low, high = 0, len(source)
    while low < high:
        middle = (low + high + 1) // 2
        if prefix_widths[middle] <= available:
            low = middle
        else:
            high = middle - 1
    return f"{source[:low].rstrip()}{suffix}"


def format_seconds(seconds: int | float) -> str:
    """将秒数格式化为 HH:MM:SS 或 MM:SS 字符串。"""
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_upload_date(value: str) -> str:
    """把 yt-dlp / B 站换算出的 YYYYMMDD 显示成 YYYY-MM-DD；拿不到时返回空串。

    列表里的更新时间是「有就显示、没有就留空」——YouTube 扁平列表经常不带日期，
    强行补全需要对每条再解析一次，与首页性能优化冲突，因此不做。
    """
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return ""
    year, month, day = text[:4], text[4:6], text[6:8]
    if not ("0001" <= year and "01" <= month <= "12" and "01" <= day <= "31"):
        return ""
    return f"{year}-{month}-{day}"
