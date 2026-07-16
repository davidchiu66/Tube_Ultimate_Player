# Tube_player DLNA 发送端第一期实施备案

实施日期：2026-07-15  
状态：编码与自动化验证完成，等待局域网设备播放验收

## 1. 本期范围

本期实现播放器 DLNA 发送端，面向当前正在播放的 YouTube/Bilibili 在线视频：

1. SSDP 搜索局域网 MediaRenderer。
2. 解析设备描述和 AVTransport、RenderingControl、ConnectionManager 控制地址。
3. 在播放器控制面板“全屏”按钮之前增加“投屏”按钮。
4. 选择设备后推送当前媒体并开始远端播放。
5. 投屏期间支持播放、暂停、停止、远端进度轮询、可用的 Seek 和音量控制。
6. 停止投屏后按最近远端进度恢复本地播放。

本期不实现本机 DLNA 接收端、媒体库投屏、转码和厂商专用适配。

## 2. 技术实现

### 2.1 设备发现

`dlna/discovery.py` 使用 SSDP M-SEARCH 搜索：

- `urn:schemas-upnp-org:device:MediaRenderer:1`
- `urn:schemas-upnp-org:service:AVTransport:1`

设备描述请求绕过系统代理，支持从嵌套设备中选择真正的 MediaRenderer，并过滤缺少 AVTransport 的设备。

### 2.2 远端控制

`dlna/controller.py` 使用 SOAP 实现：

- `SetAVTransportURI`
- `Play`
- `Pause`
- `Stop`
- `Seek REL_TIME`
- `GetPositionInfo`
- `SetVolume`

媒体元数据使用 DIDL-Lite；若设备拒绝元数据，自动使用空元数据重试。

### 2.3 在线媒体中继

`dlna/media_server.py` 在连接目标设备的局域网网卡上启动临时 HTTP 服务：

- 单一媒体流：反向代理上游 URL，保留 yt-dlp 请求头并转发 Range，支持 HTTP 206。
- 分离音视频流：FFmpeg 从当前播放位置读取两路流，复制视频，AAC/MP4A 音频直接复制，其他音频转 AAC，实时输出 MPEG-TS。
- 媒体 URL 使用随机 token，不暴露上游 URL。
- 设备停止、切换视频或应用退出时终止活动 FFmpeg 进程并关闭/清理媒体服务。

### 2.4 UI 与线程

- `ui/cast_dialog.py` 提供设备扫描、刷新和选择。
- `workers/dlna_worker.py` 在 QThreadPool 执行发现和 SOAP 请求。
- 后台结果通过绑定 Slot 回到主线程，不在后台线程操作 Qt 控件。
- 投屏成功后按钮变为“停止投屏”，本地 mpv 暂停。
- 播放/暂停、停止、进度和音量根据会话状态路由到本地或远端。

## 3. 兼容性策略

1. 当前清晰度存在独立音频流时必须使用 FFmpeg。
2. 实时 MPEG-TS 没有稳定的字节 Range，投屏期间禁用进度拖动。
3. 单流 HTTP 中继保留 Range，允许设备执行 Seek。
4. 设备没有 RenderingControl 时禁用远端音量滑块。
5. SOAP `SetAVTransportURI` 元数据失败时自动降级。
6. 端口 8899 被占用时自动使用随机空闲端口。

## 4. 验证结果

已通过：

1. 相关 Python 模块 `py_compile`。
2. 全部 18 项单元/集成测试。
3. SSDP 响应头解析。
4. MediaRenderer 设备描述与控制 URL 解析。
5. DIDL-Lite 特殊字符转义。
6. DLNA 时间格式转换。
7. 本机 HTTP Range 请求端到端转发，返回 206 和正确 Content-Range。
8. FFmpeg 分离音视频命令构造。
9. 使用配置中的 FFmpeg 8.1.2 对两路临时 HTTP 媒体执行真实封装，成功输出 MPEG-TS。
10. 播放器“投屏”按钮位于“全屏”之前，布局和可用状态正确。
11. 当前局域网 SSDP 实机发现成功：发现 `当贝投屏O9`，并解析到 AVTransport 地址。

未自动执行远端播放命令，避免测试过程在用户未操作时改变电视状态。

## 5. 实机验收方法

1. 确保电脑与电视/盒子在同一局域网，电视已启用 DLNA 投屏。
2. 播放一个 Bilibili 视频，点击“投屏”，确认设备列表出现目标设备。
3. 选择设备投屏，确认电视有画面和声音，本地播放暂停，按钮变为“停止投屏”。
4. 验证播放/暂停、音量和停止；观察控制面板进度随电视更新。
5. 点击“停止投屏”，确认电视停止，本地从最近远端进度恢复。
6. 使用 YouTube 视频重复上述步骤。
7. 测试没有独立音频的单流媒体，确认进度拖动可用。
8. 关闭 Windows 防火墙或拒绝首次网络授权时，确认 UI 给出可理解的发现/连接错误。
9. 检查 `%LocalAppData%\Tube_Ultimate_Player\logs\app.log` 中 `tube_player.dlna` 和 `tube_player.dlna.http` 记录。

## 6. 多网卡设备发现问题修复

2026-07-15 实机日志记录三次 `locations=0 renderers=0`。电脑同时启用了 WLAN、ZeroTier 和 Tailscale，原实现未指定 SSDP 组播发送接口，Windows 路由选择导致查询没有稳定地从 WLAN 发出。

修复内容：

1. 枚举所有有效本机 IPv4 地址，为每个地址创建并绑定独立发现套接字，通过 `IP_MULTICAST_IF` 明确组播出口。
2. 所有网卡使用同一个扫描截止时间并行接收响应，不按网卡叠加超时。
3. 除 MediaRenderer 和 AVTransport 外，补充 `upnp:rootdevice` 与 `ssdp:all`，兼容只响应通用查询的电视。
4. 并发读取设备描述，避免单个失效的历史地址串行阻塞整个设备列表。
5. 同一 IP 发布多个渲染器描述时合并为一个物理设备，优先保留名称明确包含 DLNA/投屏且支持音量控制的入口。
6. 过滤 `ssdp:all` 返回的非 DLNA 自定义服务；日志记录扫描网卡列表以及每个有效候选响应的来源、ST、LOCATION，便于后续诊断。
7. 首次完整扫描结果在当前应用会话内缓存；后续打开投屏窗口时并行检测缓存设备控制地址的 IP 与端口，在线设备直接展示。缓存全部失效时自动回退完整 SSDP 扫描，手动“刷新”始终强制重新扫描。

修复后在当前网络从 `192.168.5.6` WLAN 实测发现：

- `当贝投屏O9`：`192.168.5.3`
- `客厅电视 DLNA投屏`：`192.168.5.31`
- 本地视频、音频投屏增强备案：

2026-07-15 增强播放器本地媒体投屏能力。本地播放文件时，播放器控制面板的“投屏”按钮可用，操作路径与在线视频一致；本地视频、音频通过内置临时 HTTP 服务暴露给目标电视、盒子或播放器，由设备通过局域网 URL 拉取媒体文件。

实现要点：

1. 本地文件服务支持 `HEAD`、`GET` 和单段 `Range` 请求，返回 `Accept-Ranges: bytes`、`Content-Length` 与 `Content-Range`，便于 DLNA 设备预读和拖动进度。
2. 根据文件扩展名识别常见视频与音频 MIME：MP4、MKV、WebM、MOV、TS、AVI、WMV、MP3、M4A、AAC、FLAC、WAV、OGG、OPUS、WMA。
3. 本机媒体服务默认端口仍为 `8899`；设置页新增“DLNA 媒体服务端口”，保存到 `dlna.media_server_port`。如果端口被占用，服务保持既有兼容策略，自动退回随机空闲端口。
4. 本地投屏成功后，本地 mpv 暂停；点击“停止投屏”后，按远端最近进度恢复本地播放。

验证方法：

1. 在设置页确认“DLNA 媒体服务端口”为 `8899`，也可改为其它未占用端口后保存。
2. 从下载列表或本地文件入口播放一个本地 MP4/MKV/MP3/FLAC。
3. 点击“投屏”，选择电视、盒子或播放器，确认远端开始播放，本地暂停。
4. 拖动远端或控制面板进度，确认进度可用；音频文件应只播放声音。
5. 点击“停止投屏”，确认远端停止，本地从最近进度恢复。
6. 查看日志中的 `tube_player.dlna.http`，确认本地媒体 URL、端口与 Range 请求记录正常。
