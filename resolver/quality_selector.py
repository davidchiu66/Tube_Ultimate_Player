from __future__ import annotations

from collections import OrderedDict

from resolver.models import AudioTrack, VideoQuality
from services.locale_service import match_audio_language


class QualitySelector:
    # format_note 在单语言视频上只有码率档位，不是语言名，不能拿来当轨道名。
    _QUALITY_NOTES = {"ultralow", "low", "medium", "high", "default"}

    VIDEO_CODEC_PRIORITY = {
        "avc1": 300,
        "h264": 300,
        "vp9": 200,
        "vp09": 200,
        "av01": 100,
    }

    AUDIO_CODEC_PRIORITY = {
        "opus": 300,
        "mp4a": 200,
        "aac": 200,
        "mp3": 100,
    }

    @classmethod
    def audio_formats(cls, formats: list[dict]) -> list[dict]:
        """纯音频候选（有音无画且地址可播），供音轨选取与默认轨共用同一份输入。"""
        return [
            f for f in formats
            if cls.playable_url(f)
            and f.get("acodec") not in (None, "none")
            and f.get("vcodec") in (None, "none")
        ]

    @classmethod
    def select_audio_tracks(
        cls, formats: list[dict], preferred_language: str = ""
    ) -> "OrderedDict[str, AudioTrack]":
        """按语言归并出可选音轨；插入序即下拉显示序，第一条就是默认轨。

        只有 0 或 1 种语言时照常返回（0 组自然是空表），由调用方据此禁用下拉——
        一条轨没什么可"选"的，但它仍是这个视频的默认轨，选轨逻辑不必分两套。
        """
        audios = cls.audio_formats(formats)
        by_language: OrderedDict[str, list[dict]] = OrderedDict()
        for fmt in audios:
            by_language.setdefault(str(fmt.get("language") or ""), []).append(fmt)
        if not by_language:
            return OrderedDict()

        # 每种语言只留最优的一条：多个码率档在这一步被压平。
        # DRC（动态范围压缩）是同一条轨的加工版，码率还常常略高，按分数挑会盖过原轨——
        # 同语言存在非 DRC 时一律先剔掉，只有整组都是 DRC 才退而用它。
        best_per_language = {
            language: max(cls._drop_drc(items), key=cls.score_audio)
            for language, items in by_language.items()
        }

        preferred = match_audio_language(list(best_per_language), preferred_language)

        def _rank(language: str) -> tuple[int, str]:
            fmt = best_per_language[language]
            preference = int(fmt.get("language_preference") or -1)
            if language and language == preferred:
                bucket = 0          # 系统语言优先（D 裁定）
            elif preference == 5:
                bucket = 1          # 站点默认轨
            elif preference >= 10:
                bucket = 2          # 原声轨
            else:
                bucket = 3
            return bucket, cls._track_name(fmt) or language

        tracks: OrderedDict[str, AudioTrack] = OrderedDict()
        for language in sorted(best_per_language, key=_rank):
            fmt = best_per_language[language]
            track_id = str(fmt.get("format_id") or "")
            tracks[track_id] = AudioTrack(
                track_id=track_id,
                language=language,
                url=cls.playable_url(fmt),
                acodec=str(fmt.get("acodec") or ""),
                abr=fmt.get("abr"),
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
                tbr=fmt.get("tbr"),
                language_preference=int(fmt.get("language_preference") or -1),
                name=cls._track_name(fmt),
            )
        return tracks

    @classmethod
    def _drop_drc(cls, formats: list[dict]) -> list[dict]:
        """滤掉 DRC 变体；整组都是 DRC 时原样返回，避免把一种语言整个丢掉。"""
        plain = [f for f in formats if "-drc" not in str(f.get("format_id") or "").lower()]
        return plain or formats

    @classmethod
    def _track_name(cls, fmt: dict) -> str:
        """从 format_note 里取可读语言名；码率档位（medium/low…）不算名字。"""
        note = str(fmt.get("format_note") or "").strip()
        if not note or note.lower() in cls._QUALITY_NOTES:
            return ""
        # yt-dlp 会写成 "English (United States) original, medium"，逗号后是码率档，
        # 末尾的 original/default 由 is_original 另行表达，留在名字里会和后缀重复。
        name = note.split(",", 1)[0].strip()
        for marker in (" original", " default"):
            if name.lower().endswith(marker):
                name = name[: -len(marker)].strip()
        return name

    @classmethod
    def select_all(
        cls,
        formats: list[dict],
        preferred_language: str = "",
        audio_tracks: "OrderedDict[str, AudioTrack] | None" = None,
    ) -> dict[str, VideoQuality]:
        candidates = [f for f in formats if cls.playable_url(f)]
        videos = [
            f for f in candidates
            if f.get("vcodec") not in (None, "none") and int(f.get("height") or 0) > 0
        ]
        audios = cls.audio_formats(formats)

        if audio_tracks is None:
            audio_tracks = cls.select_audio_tracks(formats, preferred_language)
        # A1：多语言视频必须走"纯视频 + 独立音轨"，否则已混音的 HLS 会把语言焊死。
        prefer_split_audio = len(audio_tracks) >= 2

        default_track = next(iter(audio_tracks.values()), None)
        if default_track is not None:
            best_audio = next(
                (f for f in audios if str(f.get("format_id") or "") == default_track.track_id),
                None,
            )
        else:
            best_audio = max(audios, key=cls.score_audio, default=None)

        def _video_key(fmt: dict) -> tuple[int, int]:
            if not prefer_split_audio:
                return 0, cls.score_video(fmt)
            # 纯视频优先，且只在有独立音轨可配时才有意义。
            split = 1 if fmt.get("acodec") in (None, "none") else 0
            return split, cls.score_video(fmt)

        def _label_of(fmt: dict) -> str:
            height = int(fmt.get("height") or 0)
            fps = int(fmt.get("fps") or 0)
            return f"{height}p{fps}" if fps and fps > 30 else f"{height}p"

        best_by_label: dict[str, dict] = {}
        # 同档位的已混音变体单独留一份：A1 把画面轨切到纯视频后，"随画面（免转码）"
        # 还要能回到今天的单流地址（C1）。
        best_muxed_by_label: dict[str, dict] = {}
        for fmt in videos:
            label = _label_of(fmt)
            current = best_by_label.get(label)
            if current is None or _video_key(fmt) > _video_key(current):
                best_by_label[label] = fmt
            if fmt.get("acodec") not in (None, "none"):
                muxed = best_muxed_by_label.get(label)
                if muxed is None or cls.score_video(fmt) > cls.score_video(muxed):
                    best_muxed_by_label[label] = fmt

        ordered_labels = sorted(
            best_by_label,
            key=lambda label: (
                int(label.split("p", 1)[0] or 0),
                int(label.split("p", 1)[1] or 0) if label.split("p", 1)[1] else 0,
            ),
            reverse=True,
        )

        qualities: OrderedDict[str, VideoQuality] = OrderedDict()
        for label in ordered_labels:
            fmt = best_by_label[label]
            audio_url = None
            audio_format_id = None
            audio_filesize = None
            audio_tbr = None
            acodec = fmt.get("acodec") or "none"
            if acodec in (None, "none") and best_audio:
                audio_url = cls.playable_url(best_audio)
                audio_format_id = str(best_audio.get("format_id") or "")
                audio_filesize = best_audio.get("filesize") or best_audio.get("filesize_approx")
                audio_tbr = best_audio.get("tbr") or best_audio.get("abr")
                acodec = best_audio.get("acodec") or "none"

            # 只有在本档位确实走了分离流时，"随画面"才是另一种选择；本身就是单流时留空。
            muxed_fmt = best_muxed_by_label.get(label) if audio_url else None
            muxed_video_url = cls.playable_url(muxed_fmt) if muxed_fmt else None

            qualities[label] = VideoQuality(
                label=label,
                height=int(fmt.get("height") or 0),
                width=int(fmt.get("width") or 0),
                fps=int(fmt.get("fps") or 0),
                vcodec=str(fmt.get("vcodec") or "none"),
                acodec=str(acodec),
                ext=str(fmt.get("ext") or ""),
                format_id=str(fmt.get("format_id") or ""),
                video_url=cls.playable_url(fmt),
                audio_url=audio_url,
                audio_format_id=audio_format_id,
                audio_filesize=audio_filesize,
                audio_tbr=audio_tbr,
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
                tbr=fmt.get("tbr"),
                muxed_video_url=muxed_video_url,
            )

        return qualities

    @classmethod
    def score_video(cls, fmt: dict) -> int:
        codec = str(fmt.get("vcodec") or "")
        codec_score = 0
        for key, score in cls.VIDEO_CODEC_PRIORITY.items():
            if key in codec:
                codec_score = score
                break

        return (
            int(fmt.get("height") or 0) * 1_000_000
            + int(fmt.get("fps") or 0) * 10_000
            + codec_score * 100
            + int(fmt.get("tbr") or 0)
        )

    @classmethod
    def score_audio(cls, fmt: dict) -> int:
        codec = str(fmt.get("acodec") or "")
        codec_score = 0
        for key, score in cls.AUDIO_CODEC_PRIORITY.items():
            if key in codec:
                codec_score = score
                break
        return codec_score * 10_000 + int(fmt.get("abr") or fmt.get("tbr") or 0)

    @staticmethod
    def playable_url(fmt: dict) -> str:
        url = str(fmt.get("url") or "").strip()
        if url:
            return url

        protocol = str(fmt.get("protocol") or "").lower()
        manifest_url = str(fmt.get("manifest_url") or "").strip()
        if manifest_url and any(key in protocol for key in ("m3u8", "hls", "dash", "http")):
            return manifest_url

        return ""
