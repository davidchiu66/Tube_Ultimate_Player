from __future__ import annotations

from collections import OrderedDict

from resolver.models import VideoQuality


class QualitySelector:
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
    def select_all(cls, formats: list[dict]) -> dict[str, VideoQuality]:
        candidates = [f for f in formats if cls.playable_url(f)]
        videos = [
            f for f in candidates
            if f.get("vcodec") not in (None, "none") and int(f.get("height") or 0) > 0
        ]
        audios = [
            f for f in candidates
            if f.get("acodec") not in (None, "none")
            and f.get("vcodec") in (None, "none")
        ]
        best_audio = max(audios, key=cls.score_audio, default=None)

        best_by_label: dict[str, dict] = {}
        for fmt in videos:
            height = int(fmt.get("height") or 0)
            fps = int(fmt.get("fps") or 0)
            label = f"{height}p{fps}" if fps and fps > 30 else f"{height}p"
            current = best_by_label.get(label)
            if current is None or cls.score_video(fmt) > cls.score_video(current):
                best_by_label[label] = fmt

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
