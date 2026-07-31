"""DLNA 协议层共用的 contentFeatures 定义。

单独成模块是为了让 `dlna/controller.py`（写进 DIDL-Lite 的 protocolInfo）与
`dlna/media_server.py`（写进 HTTP 响应头 contentFeatures.dlna.org）用同一份取值 ——
两边不一致时，部分电视会拒绝播放或按最保守的路径处理。
"""

from __future__ import annotations


# DLNA.ORG_OP 的两位分别是「支持 TimeSeekRange」与「支持 Range」：
# 实时转封装的管道流两者都不支持（00），可 Range 的单文件/本地文件支持后者（01）。
_OP_STREAMING = "00"
_OP_SEEKABLE = "01"

# DLNA.ORG_CI=0 表示未做码流转换；FLAGS 为流式播放的常用取值
# （streaming transfer mode + background transfer + connection stalling + DLNA v1.5）。
_FLAGS = "DLNA.ORG_CI=0;DLNA.ORG_FLAGS=8D500000000000000000000000000000"


def dlna_content_features(*, seekable: bool) -> str:
    return f"DLNA.ORG_OP={_OP_SEEKABLE if seekable else _OP_STREAMING};{_FLAGS}"
