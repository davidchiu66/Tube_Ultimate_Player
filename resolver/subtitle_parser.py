from __future__ import annotations

from collections import OrderedDict

from resolver.models import SubtitleInfo


class SubtitleParser:
    PREFERRED_EXTS = ("vtt", "srt", "ttml", "json3")

    @classmethod
    def parse(
        cls,
        subtitles: dict,
        automatic_captions: dict,
    ) -> dict[str, SubtitleInfo]:
        parsed: OrderedDict[str, SubtitleInfo] = OrderedDict()
        cls._append(parsed, subtitles, is_auto=False)
        cls._append(parsed, automatic_captions, is_auto=True)
        return parsed

    @classmethod
    def _append(cls, target: OrderedDict[str, SubtitleInfo], data: dict, is_auto: bool) -> None:
        for language, entries in data.items():
            if not isinstance(entries, list):
                continue
            selected = cls._select_entry(entries)
            if not selected:
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
            )

    @classmethod
    def _select_entry(cls, entries: list[dict]) -> dict | None:
        usable = [entry for entry in entries if entry.get("url")]
        if not usable:
            return None
        for ext in cls.PREFERRED_EXTS:
            for entry in usable:
                if entry.get("ext") == ext:
                    return entry
        return usable[0]

