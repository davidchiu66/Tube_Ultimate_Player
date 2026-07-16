from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShortcutDefinition:
    action: str
    label: str
    default: str


SHORTCUT_DEFINITIONS = (
    ShortcutDefinition("play_pause", "播放 / 暂停", "Space"),
    ShortcutDefinition("stop", "停止", "S"),
    ShortcutDefinition("download", "下载当前视频", "D"),
    ShortcutDefinition("favorite", "收藏当前视频", "C"),
    ShortcutDefinition("cast", "投屏 / 停止投屏", "Ctrl+C"),
    ShortcutDefinition("fullscreen", "全屏 / 退出全屏", "Return"),
    ShortcutDefinition("fullscreen_keypad", "全屏（小键盘）", "Enter"),
    ShortcutDefinition("seek_backward_10", "后退 10 秒", "Left"),
    ShortcutDefinition("seek_forward_10", "前进 10 秒", "Right"),
    ShortcutDefinition("seek_backward_60", "后退 60 秒", "Ctrl+Left"),
    ShortcutDefinition("seek_forward_60", "前进 60 秒", "Ctrl+Right"),
    ShortcutDefinition("volume_up", "音量增加 5", "Up"),
    ShortcutDefinition("volume_down", "音量降低 5", "Down"),
    ShortcutDefinition("mute", "静音 / 恢复音量", "M"),
    ShortcutDefinition("seek_start", "跳转到开头", "Home"),
    ShortcutDefinition("seek_end", "跳转到结尾", "End"),
    ShortcutDefinition("playlist_previous", "播放列表上一项", "PgUp"),
    ShortcutDefinition("playlist_next", "播放列表下一项", "PgDown"),
)

DEFAULT_SHORTCUTS = {definition.action: definition.default for definition in SHORTCUT_DEFINITIONS}
