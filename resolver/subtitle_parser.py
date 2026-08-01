from __future__ import annotations

from collections import OrderedDict
import logging

from resolver.models import SubtitleInfo


logger = logging.getLogger("tube_player.resolver")


class SubtitleParser:
    """把 yt-dlp 的 subtitles / automatic_captions 整理成可直接使用的轨道表。

    两个容易踩的点：
    - **不是所有条目都有 url**：B 站 AI 字幕由 yt-dlp 直接内联在 `data` 字段里
      （已经是 SRT 正文）。只按 url 过滤会把 B 站字幕整批丢掉。
    - **danmaku 不是字幕**：yt-dlp 的 B 站提取器把弹幕也放在 subtitles 里
      （ext=xml），mpv 播不了，必须在这里就排除，而不是等用户选中后再报错。
    """

    # 只保留 mpv 能直接加载的格式。json3/srv1-3/ttml 同语言通常都有 srt/vtt 兄弟条目，
    # 拿不到就跳过，不必自己写转换器。
    PREFERRED_EXTS = ("srt", "vtt", "ass", "ssa")
    # 这些"语言"其实不是字幕轨。
    EXCLUDED_LANGUAGES = ("danmaku",)
    EXCLUDED_EXTS = ("xml",)
    # 排在最前面的语言（中文、英文优先），其余按可读名排序。
    PREFERRED_LANGUAGE_PREFIXES = ("zh", "yue", "en")

    @classmethod
    def parse(
        cls,
        subtitles: dict,
        automatic_captions: dict,
    ) -> dict[str, SubtitleInfo]:
        parsed: OrderedDict[str, SubtitleInfo] = OrderedDict()
        cls._append(parsed, subtitles, is_auto=False)
        cls._append(parsed, automatic_captions, is_auto=True)
        return cls._sorted(parsed)

    @classmethod
    def _append(cls, target: OrderedDict[str, SubtitleInfo], data: dict, is_auto: bool) -> None:
        for language, entries in data.items():
            if not isinstance(entries, list):
                continue
            if str(language).strip().lower() in cls.EXCLUDED_LANGUAGES:
                continue
            selected = cls._select_entry(entries)
            if not selected:
                extensions = sorted({str(entry.get("ext") or "").lower() for entry in entries if cls._has_content(entry)})
                if extensions and not set(extensions) <= set(cls.EXCLUDED_EXTS):
                    logger.info(
                        "unsupported subtitle formats skipped language=%s automatic=%s formats=%s",
                        language,
                        is_auto,
                        extensions,
                    )
                continue
            key_base = f"{language}:{'auto' if is_auto else 'manual'}"
            key = key_base
            index = 2
            while key in target:
                key = f"{key_base}:{index}"
                index += 1
            target[key] = SubtitleInfo(
                language=str(language),
                ext=str(selected.get("ext") or ""),
                url=str(selected.get("url") or ""),
                is_auto=is_auto,
                data=str(selected.get("data") or ""),
                name=str(selected.get("name") or ""),
            )

    @classmethod
    def _select_entry(cls, entries: list[dict]) -> dict | None:
        usable = [entry for entry in entries if cls._has_content(entry)]
        if not usable:
            return None
        for ext in cls.PREFERRED_EXTS:
            for entry in usable:
                if str(entry.get("ext") or "").lower() == ext:
                    return entry
        return None

    @staticmethod
    def _has_content(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        if str(entry.get("ext") or "").lower() in SubtitleParser.EXCLUDED_EXTS:
            return False
        return bool(str(entry.get("url") or "").strip() or str(entry.get("data") or "").strip())

    @classmethod
    def _sorted(cls, parsed: OrderedDict[str, SubtitleInfo]) -> dict[str, SubtitleInfo]:
        """手动字幕在前、自动字幕在后；中英文优先，其余按可读名排。

        YouTube 的 automatic_captions 可能有近五千种（机翻到各种语言），排序决定了
        用户能不能在下拉框里一眼找到常用的那几条。
        """

        def rank(item: tuple[str, SubtitleInfo]) -> tuple[int, int, str]:
            _key, subtitle = item
            # B 站的 AI 轨语言码是 ai-zh，要按 zh 排序才能让中文排在最前。
            language = subtitle.language.lower()
            if language.startswith("ai-"):
                language = language[3:]
            preferred = next(
                (index for index, prefix in enumerate(cls.PREFERRED_LANGUAGE_PREFIXES) if language.startswith(prefix)),
                len(cls.PREFERRED_LANGUAGE_PREFIXES),
            )
            return (1 if subtitle.is_auto else 0, preferred, subtitle.display_language.lower())

        return OrderedDict(sorted(parsed.items(), key=rank))
