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

    # 按 mpv 兼容度排序：前四种是 mpv 能直接加载的，后面几种是 YouTube 私有/半私有格式，
    # 只在同一条轨道拿不到前四种时才回退——明确的加载失败远好过字幕在解析层被静默丢光。
    PREFERRED_EXTS = ("srt", "vtt", "ass", "ssa", "ttml", "srv3", "srv2", "srv1", "json3")
    # 这些"语言"其实不是字幕轨。
    EXCLUDED_LANGUAGES = ("danmaku",)
    EXCLUDED_EXTS = ("xml",)
    # 用户没配置字幕语言时的默认排序（中文、英文优先），其余按可读名排序。
    PREFERRED_LANGUAGE_PREFIXES = ("zh", "yue", "en")

    @classmethod
    def parse(
        cls,
        subtitles: dict,
        automatic_captions: dict,
        preferred_languages: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, SubtitleInfo]:
        parsed: OrderedDict[str, SubtitleInfo] = OrderedDict()
        cls._append(parsed, subtitles, is_auto=False)
        cls._append(parsed, automatic_captions, is_auto=True)
        # 有了原始轨道数，「站点没给」（raw_*=0）与「解析器全丢了」（raw_*>0 而 parsed=0）
        # 才能一眼分开——只有结果日志时这两种故障长得一模一样。
        logger.debug(
            "subtitle parse raw_manual=%s raw_auto=%s parsed=%s",
            len(subtitles or {}),
            len(automatic_captions or {}),
            len(parsed),
        )
        return cls._sorted(parsed, cls._language_prefixes(preferred_languages))

    @classmethod
    def _language_prefixes(
        cls,
        preferred_languages: list[str] | tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        """用户配置的字幕语言决定下拉框顺序，没配置时回落到内置的中英文优先。"""
        prefixes = tuple(
            str(language).strip().lower()
            for language in (preferred_languages or ())
            if str(language).strip()
        )
        return prefixes or cls.PREFERRED_LANGUAGE_PREFIXES

    @classmethod
    def _append(cls, target: OrderedDict[str, SubtitleInfo], data: dict, is_auto: bool) -> None:
        for language, entries in data.items():
            if not isinstance(entries, list):
                continue
            if str(language).strip().lower() in cls.EXCLUDED_LANGUAGES:
                continue
            selected = cls._select_entry(entries)
            if not selected:
                # 走到这里只剩「整条轨道都没有可用内容」一种情况（弹幕 xml、空 url+空 data）；
                # 格式不认识已经由 _select_entry 的回退接住了。
                logger.debug(
                    "subtitle track has no usable entry language=%s automatic=%s",
                    language,
                    is_auto,
                )
                continue
            ext = str(selected.get("ext") or "").lower()
            if ext and ext not in cls.PREFERRED_EXTS:
                logger.info(
                    "subtitle format may not be playable language=%s automatic=%s ext=%s",
                    language,
                    is_auto,
                    ext,
                )
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
        # 格式完全没见过也别丢：有内容就交给 mpv，最坏是它报一句加载失败，
        # 比字幕凭空消失好排查得多。
        return usable[0]

    @staticmethod
    def _has_content(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        if str(entry.get("ext") or "").lower() in SubtitleParser.EXCLUDED_EXTS:
            return False
        return bool(str(entry.get("url") or "").strip() or str(entry.get("data") or "").strip())

    @classmethod
    def _sorted(
        cls,
        parsed: OrderedDict[str, SubtitleInfo],
        language_prefixes: tuple[str, ...],
    ) -> dict[str, SubtitleInfo]:
        """手动字幕在前、自动字幕在后；配置里的语言优先，其余按可读名排。

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
                (index for index, prefix in enumerate(language_prefixes) if language.startswith(prefix)),
                len(language_prefixes),
            )
            return (1 if subtitle.is_auto else 0, preferred, subtitle.display_language.lower())

        return OrderedDict(sorted(parsed.items(), key=rank))
