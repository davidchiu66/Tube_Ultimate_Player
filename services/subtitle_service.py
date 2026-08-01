"""把字幕落成 mpv 能加载的本地文件。

为什么不直接把 URL 交给 mpv：
- B 站 AI 字幕根本没有 URL —— yt-dlp 把 SRT 正文内联在 `data` 字段里，只有写成
  文件才能用；
- YouTube 的字幕地址是带签名的长 URL，B 站的 aisubtitle 地址需要 Referer，交给
  mpv 内部的 http 去取容易 403，而这里可以复用应用自己的代理与请求头。
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.request
from pathlib import Path

from app_paths import CACHE_DIR
from resolver.models import SubtitleInfo


logger = logging.getLogger("tube_player.subtitle")

SUBTITLE_CACHE_DIR = CACHE_DIR / "subtitles"
# 字幕文件都很小，1MB 已经非常宽裕，防止异常响应把内存吃掉。
MAX_SUBTITLE_BYTES = 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def subtitle_cache_path(video_id: str, subtitle: SubtitleInfo) -> Path:
    """按 (视频, 语言, 轨道类型, 内容指纹) 命名，避免不同轨道互相覆盖。"""
    kind = "auto" if subtitle.is_auto else "manual"
    fingerprint = hashlib.sha1(
        f"{subtitle.url}|{len(subtitle.data)}|{subtitle.ext}".encode("utf-8")
    ).hexdigest()[:10]
    stem = _SAFE_NAME.sub("_", f"{video_id}.{subtitle.language}.{kind}.{fingerprint}")
    extension = _SAFE_NAME.sub("", subtitle.ext.lower()) or "srt"
    return SUBTITLE_CACHE_DIR / f"{stem}.{extension}"


def materialize_subtitle(
    subtitle: SubtitleInfo,
    video_id: str,
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
) -> Path:
    """返回本地字幕文件路径，必要时先下载。已缓存则直接复用。"""
    if not subtitle.is_usable:
        raise RuntimeError("该字幕轨没有可用内容")

    target = subtitle_cache_path(video_id, subtitle)
    if target.is_file() and target.stat().st_size > 0:
        logger.debug("subtitle cache hit path=%s", target)
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    if subtitle.data:
        # B 站 AI 字幕走这条路：正文已经在 JSON 里，不需要联网。
        target.write_text(subtitle.data, encoding="utf-8")
        logger.info("subtitle written from inline data language=%s path=%s", subtitle.language, target)
        return target

    payload = _download(subtitle.url, proxy=proxy, headers=headers)
    if not payload.strip():
        raise RuntimeError("字幕内容为空")
    target.write_text(payload, encoding="utf-8")
    logger.info(
        "subtitle downloaded language=%s ext=%s bytes=%s path=%s",
        subtitle.language,
        subtitle.ext,
        len(payload),
        target,
    )
    return target


def _download(url: str, *, proxy: str, headers: dict[str, str] | None) -> str:
    request = urllib.request.Request(url, headers=_request_headers(url, headers))
    handlers: list[urllib.request.BaseHandler] = []
    if proxy.startswith(("http://", "https://", "socks5://", "socks5h://")):
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        # 显式空映射：否则 urllib 会补一个读 http_proxy 环境变量的默认 handler。
        handlers.append(urllib.request.ProxyHandler({}))
    # OpenerDirector 非线程安全，每次调用都新建。
    opener = urllib.request.build_opener(*handlers)
    with opener.open(request, timeout=30) as response:
        raw = response.read(MAX_SUBTITLE_BYTES + 1)
    if len(raw) > MAX_SUBTITLE_BYTES:
        raise RuntimeError("字幕文件过大，已放弃加载")
    return raw.decode("utf-8", errors="replace")


def _request_headers(url: str, headers: dict[str, str] | None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }
    for name, value in (headers or {}).items():
        clean_name = str(name or "").strip()
        clean_value = str(value or "").replace("\r", "").replace("\n", "").strip()
        if clean_name and clean_value:
            result[clean_name] = clean_value
    if "bilibili" in url or "hdslb.com" in url:
        result.setdefault("Referer", "https://www.bilibili.com/")
    return result
