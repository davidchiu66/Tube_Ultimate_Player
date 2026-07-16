from __future__ import annotations

import ctypes
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from app_paths import add_thirdpart_dll_directory, thirdpart_path
from services.config_service import ConfigService


MPV_FORMAT_STRING = 1
MPV_FORMAT_FLAG = 3
MPV_FORMAT_DOUBLE = 5


class MpvError(RuntimeError):
    pass


class MpvPlayer(QObject):
    position_changed = Signal(float)
    duration_changed = Signal(float)
    pause_changed = Signal(bool)
    playback_finished = Signal()
    error = Signal(str)

    def __init__(self, video_widget: QWidget, config: ConfigService) -> None:
        super().__init__(video_widget)
        self.video_widget = video_widget
        self.config = config
        self._lib = self._load_libmpv()
        self._bind_api()
        self._handle = self._lib.mpv_create()
        if not self._handle:
            raise MpvError("无法创建 libmpv 实例")

        self._configure_before_initialize()
        self._check(self._lib.mpv_initialize(self._handle), "初始化 libmpv 失败")

        self._duration = 0.0
        self._last_pause: bool | None = None
        self._last_eof = False
        self._last_load_request: tuple[str, str | None, dict[str, str]] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_properties)
        self._timer.start()

        self.set_volume(int(config.get("player.volume", 80)))
        self.set_speed(float(config.get("player.speed", 1.0)))

    def load(
        self,
        video_url: str,
        audio_url: str | None = None,
        start_position: float | None = None,
        headers: dict[str, str] | None = None,
        *,
        autoplay: bool = True,
    ) -> None:
        self._last_eof = False
        self.apply_network_options(headers or {})
        self.set_property_string("audio-files", audio_url or "")
        self.set_property_string(
            "start",
            f"{start_position:.3f}" if start_position and start_position > 0 else "none",
        )
        self.command("loadfile", video_url, "replace")
        if autoplay:
            # keep-open leaves mpv paused after EOF. Explicitly clear that state so
            # loading the next playlist entry actually starts playback.
            self.resume()
        self._last_load_request = (video_url, audio_url, dict(headers or {}))

    def pause(self) -> None:
        self.set_property_string("pause", "yes")

    def resume(self) -> None:
        self.set_property_string("pause", "no")

    def toggle_pause(self) -> None:
        paused = self.get_bool("pause")
        self.set_property_string("pause", "no" if paused else "yes")

    def restart(self) -> None:
        if self.get_bool("seekable"):
            self.command("seek", "0", "absolute+exact")
        elif self._last_load_request is not None:
            video_url, audio_url, headers = self._last_load_request
            self.load(video_url, audio_url, headers=headers)
        else:
            raise MpvError("没有可重新播放的媒体")

        # Keep the EOF edge latched until mpv reports that the restarted media left EOF.
        self._last_eof = True
        self.resume()

    def stop(self) -> None:
        self._last_eof = False
        self.command("stop")

    def seek(self, seconds: float) -> None:
        self.command("seek", str(max(0.0, seconds)), "absolute")

    def set_volume(self, volume: int) -> None:
        self.set_property_string("volume", str(max(0, min(100, volume))))

    def set_speed(self, speed: float) -> None:
        self.set_property_string("speed", str(max(0.25, min(4.0, speed))))

    def add_subtitle(self, subtitle_url: str) -> None:
        if subtitle_url:
            self.command("sub-add", subtitle_url, "select")

    def clear_subtitles(self) -> None:
        try:
            self.command("sub-remove")
        except MpvError:
            pass

    def position(self) -> float:
        return self.get_double("time-pos") or 0.0

    def duration(self) -> float:
        return self.get_double("duration") or 0.0

    def apply_network_options(self, headers: dict[str, str] | None = None) -> None:
        _, proxy = self.config.effective_proxy()
        if proxy:
            self.set_property_string("http-proxy", proxy)
        else:
            self.set_property_string("http-proxy", "")

        headers = headers or {}
        user_agent = headers.get("User-Agent") or headers.get("user-agent")
        if user_agent:
            self.set_property_string("user-agent", user_agent)
        referer = headers.get("Referer") or headers.get("referer")
        if referer:
            self.set_property_string("referrer", referer)

    def shutdown(self) -> None:
        if getattr(self, "_handle", None):
            self._timer.stop()
            self._lib.mpv_terminate_destroy(self._handle)
            self._handle = None

    def command(self, *args: str) -> None:
        encoded = [str(arg).encode("utf-8") for arg in args]
        argv = (ctypes.c_char_p * (len(encoded) + 1))()
        for index, value in enumerate(encoded):
            argv[index] = value
        argv[len(encoded)] = None
        self._check(self._lib.mpv_command(self._handle, argv), f"mpv 命令失败: {' '.join(args)}")

    def set_property_string(self, name: str, value: str) -> None:
        self._check(
            self._lib.mpv_set_property_string(
                self._handle,
                name.encode("utf-8"),
                str(value).encode("utf-8"),
            ),
            f"设置 mpv 属性失败: {name}",
        )

    def get_double(self, name: str) -> float | None:
        value = ctypes.c_double()
        result = self._lib.mpv_get_property(
            self._handle,
            name.encode("utf-8"),
            MPV_FORMAT_DOUBLE,
            ctypes.byref(value),
        )
        if result < 0:
            return None
        return float(value.value)

    def get_bool(self, name: str) -> bool:
        value = ctypes.c_int()
        result = self._lib.mpv_get_property(
            self._handle,
            name.encode("utf-8"),
            MPV_FORMAT_FLAG,
            ctypes.byref(value),
        )
        if result < 0:
            return False
        return bool(value.value)

    def _configure_before_initialize(self) -> None:
        options = {
            "wid": str(int(self.video_widget.winId())),
            "vo": "gpu-next",
            "hwdec": str(self.config.get("player.hardware_decode", "auto-safe")),
            "keep-open": "yes",
            "cache": "yes",
            "demuxer-max-bytes": "500M",
            "demuxer-max-back-bytes": "100M",
            "cache-secs": "20",
            "profile": "fast",
            "alang": "zh,en",
            "slang": "zh,en",
            "osc": "no",
        }
        _, proxy = self.config.effective_proxy()
        if proxy:
            options["http-proxy"] = proxy

        for key, value in options.items():
            self._check(
                self._lib.mpv_set_option_string(
                    self._handle,
                    key.encode("utf-8"),
                    str(value).encode("utf-8"),
                ),
                f"设置 mpv 选项失败: {key}",
            )

    def _poll_properties(self) -> None:
        duration = self.duration()
        if duration and abs(duration - self._duration) > 0.2:
            self._duration = duration
            self.duration_changed.emit(duration)

        position = self.position()
        if position >= 0:
            self.position_changed.emit(position)

        paused = self.get_bool("pause")
        if paused != self._last_pause:
            self._last_pause = paused
            self.pause_changed.emit(paused)

        eof_reached = self.get_bool("eof-reached")
        if eof_reached and not self._last_eof:
            self.playback_finished.emit()
        self._last_eof = eof_reached

    def _bind_api(self) -> None:
        self._lib.mpv_create.restype = ctypes.c_void_p

        self._lib.mpv_initialize.argtypes = [ctypes.c_void_p]
        self._lib.mpv_initialize.restype = ctypes.c_int

        self._lib.mpv_set_option_string.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        self._lib.mpv_set_option_string.restype = ctypes.c_int

        self._lib.mpv_set_property_string.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        self._lib.mpv_set_property_string.restype = ctypes.c_int

        self._lib.mpv_get_property.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        self._lib.mpv_get_property.restype = ctypes.c_int

        self._lib.mpv_command.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char_p),
        ]
        self._lib.mpv_command.restype = ctypes.c_int

        self._lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
        self._lib.mpv_terminate_destroy.restype = None

        self._lib.mpv_error_string.argtypes = [ctypes.c_int]
        self._lib.mpv_error_string.restype = ctypes.c_char_p

    def _check(self, result: int, message: str) -> None:
        if result < 0:
            detail = self._lib.mpv_error_string(result).decode("utf-8", errors="replace")
            raise MpvError(f"{message}: {detail}")

    @staticmethod
    def _load_libmpv() -> ctypes.CDLL:
        add_thirdpart_dll_directory()
        candidates = [
            thirdpart_path("libmpv-2.dll"),
            thirdpart_path("mpv-2.dll"),
            Path("libmpv.so.2"),
            Path("libmpv.dylib"),
        ]
        for candidate in candidates:
            try:
                if candidate.is_absolute() or candidate.exists():
                    return ctypes.CDLL(str(candidate))
                return ctypes.CDLL(str(candidate))
            except OSError:
                continue
        raise MpvError("未找到 libmpv 动态库，请确认 3rdpart/libmpv-2.dll 存在")
