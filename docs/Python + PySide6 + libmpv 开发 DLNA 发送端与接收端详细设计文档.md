# Python + PySide6 + libmpv 开发 DLNA 发送端与接收端详细设计文档

## 1. 项目概述

本文档描述一个基于 **Python + PySide6 + libmpv** 的桌面端 DLNA 应用设计方案。

该应用同时支持两类核心能力：

1. **DLNA 发送端**
   - 发现局域网内的 DLNA / UPnP 播放设备
   - 将本地视频、音频、图片推送到电视、盒子、播放器
   - 控制远端设备播放、暂停、停止、跳转、音量

2. **DLNA 接收端**
   - 将本机应用注册为一个 DLNA Renderer
   - 接收来自手机、平板、电脑等 DLNA Controller 的投屏请求
   - 使用 libmpv 作为本地播放内核
   - 提供播放控制接口，例如 Play、Pause、Stop、Seek、Volume

整体目标是开发一个类似 Windows 桌面投屏工具、本地媒体中心、DLNA 播放控制器、软件 DLNA Renderer 的跨平台桌面应用。

---

## 2. 技术选型

### 2.1 GUI 框架

使用：

```text
PySide6
```

原因：

- Qt 桌面生态成熟
- 支持 Windows、macOS、Linux
- 能够方便嵌入 libmpv 播放窗口
- 支持异步任务、线程、信号槽
- UI 可维护性好

### 2.2 播放内核

使用：

```text
libmpv
```

原因：

- 支持格式广
- 支持网络流播放
- 支持硬件解码
- 可嵌入 Qt 窗口
- 支持丰富的播放控制 API
- 对 HTTP URL、DLNA 媒体 URL、RTSP、HLS 等兼容性较好

### 2.3 DLNA / UPnP 组件

推荐使用：

```text
async-upnp-client
```

用于：

- SSDP 设备发现
- UPnP 设备描述解析
- 调用 AVTransport、RenderingControl、ConnectionManager 服务
- 作为 DLNA Controller 与外部 Renderer 通信

也可以补充使用：

```text
aiohttp
```

用于：

- 本地 HTTP 媒体服务
- 设备描述 XML 服务
- DLNA Renderer 服务接口
- SOAP 请求处理
- 静态媒体文件输出

### 2.4 本地 HTTP 服务

DLNA 推送本地文件时，电视无法直接读取电脑硬盘文件路径，因此需要程序启动一个 HTTP Server。

例如：

```text
本地文件：D:/Movies/test.mp4
HTTP 地址：http://192.168.1.10:8899/media/test.mp4
```

远端电视通过 HTTP 拉取媒体文件。

推荐：

```text
aiohttp
```

### 2.5 数据存储

推荐：

```text
SQLite
```

用于保存：

- 媒体库
- 播放历史
- 收藏设备
- 设备别名
- 最近播放
- 用户配置

Python 可使用 `sqlite3` 或 `SQLAlchemy`。

---

## 3. 系统角色定义

本应用设计为三合一结构：

```text
DLNA Controller + DLNA Media Server + DLNA Renderer
```

| 角色 | 缩写 | 功能 |
|---|---|---|
| 控制器 | DMC | 搜索设备、控制电视播放 |
| 媒体服务器 | DMS | 向电视提供本地媒体 URL |
| 媒体渲染器 | DMR | 接收别人投屏并播放 |

---

## 4. 总体架构

```text
+-------------------------------------------------------+
|                    PySide6 GUI                        |
|                                                       |
|  +----------------+   +-----------------------------+ |
|  | 设备列表        |   | 本地媒体库                    | |
|  +----------------+   +-----------------------------+ |
|  | 播放控制面板    |   | libmpv 播放窗口               | |
|  +----------------+   +-----------------------------+ |
+-------------------------------------------------------+
                         |
                         v
+-------------------------------------------------------+
|                 Application Service Layer             |
|                                                       |
|  +------------------+  +----------------------------+ |
|  | DeviceManager    |  | MediaLibraryService        | |
|  +------------------+  +----------------------------+ |
|  | DlnaController   |  | LocalRendererService       | |
|  +------------------+  +----------------------------+ |
|  | MediaHttpServer  |  | PlaybackService            | |
|  +------------------+  +----------------------------+ |
+-------------------------------------------------------+
                         |
                         v
+-------------------------------------------------------+
|                  Infrastructure Layer                 |
|                                                       |
|  +------------------+  +----------------------------+ |
|  | SSDP/UPnP Client |  | aiohttp HTTP/SOAP Server   | |
|  +------------------+  +----------------------------+ |
|  | libmpv Binding   |  | SQLite                     | |
|  +------------------+  +----------------------------+ |
+-------------------------------------------------------+
```

---

# 第一部分：DLNA 发送端设计

## 5.1 发送端核心流程

用户选择本地媒体文件后投屏到电视：

```text
用户选择媒体文件
        ↓
启动本地 HTTP Server
        ↓
生成媒体访问 URL
        ↓
搜索 DLNA Renderer
        ↓
用户选择目标设备
        ↓
调用 SetAVTransportURI
        ↓
调用 Play
        ↓
电视开始播放
```

## 5.2 设备发现模块 DeviceDiscoveryService

### 功能

- 搜索局域网内 UPnP / DLNA 设备
- 过滤可作为 Renderer 的设备
- 获取设备名称、制造商、型号、服务地址
- 定时刷新设备在线状态

### 发现协议

使用 SSDP 组播：

```text
239.255.255.250:1900
```

查找目标：

```text
ssdp:all
urn:schemas-upnp-org:device:MediaRenderer:1
urn:schemas-upnp-org:service:AVTransport:1
urn:schemas-upnp-org:service:RenderingControl:1
```

### 设备对象模型

```python
class DlnaDevice:
    uuid: str
    friendly_name: str
    manufacturer: str
    model_name: str
    location: str
    host: str
    av_transport: object
    rendering_control: object
    connection_manager: object
    online: bool
```

### 设备筛选标准

一个可投屏设备通常需要包含：

```text
AVTransport
RenderingControl
ConnectionManager
```

其中最关键的是 `AVTransport`。如果设备没有 AVTransport 服务，则无法推送播放 URL。

## 5.3 媒体 HTTP 服务 MediaHttpServer

### 作用

向 DLNA 播放设备提供本地媒体文件访问。

### 示例

```text
本地路径：C:/Users/User/Videos/movie.mp4
HTTP 地址：http://192.168.1.20:8899/media/abc123
```

### URL 设计

不建议直接暴露真实文件路径。推荐：

```text
GET /media/{media_id}
GET /cover/{media_id}
GET /subtitle/{media_id}
```

例如：

```text
http://192.168.1.20:8899/media/8f3c9a1e
```

### Range 支持

非常重要。DLNA 电视播放视频时通常会发送：

```http
Range: bytes=123456-
```

所以 HTTP 服务必须支持：

- HTTP 206 Partial Content
- Range 请求
- Content-Length
- Content-Range
- Accept-Ranges

否则会出现无法拖动进度、播放失败、电视直接断开、大文件无法播放等问题。

### 响应头示例

```http
HTTP/1.1 206 Partial Content
Content-Type: video/mp4
Accept-Ranges: bytes
Content-Length: 1048576
Content-Range: bytes 0-1048575/734003200
```

## 5.4 媒体类型识别

根据文件扩展名和 MIME 类型识别媒体。

### 视频

```text
.mp4  -> video/mp4
.mkv  -> video/x-matroska
.avi  -> video/x-msvideo
.mov  -> video/quicktime
.ts   -> video/mp2t
.m2ts -> video/mp2t
```

### 音频

```text
.mp3  -> audio/mpeg
.flac -> audio/flac
.wav  -> audio/wav
.aac  -> audio/aac
.m4a  -> audio/mp4
```

### 图片

```text
.jpg  -> image/jpeg
.jpeg -> image/jpeg
.png  -> image/png
.bmp  -> image/bmp
```

## 5.5 DLNA 元数据 DIDL-Lite

发送媒体给 Renderer 时，很多电视不仅需要 URL，还需要 DIDL-Lite 元数据。

### 视频示例

```xml
<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
           xmlns:dc="http://purl.org/dc/elements/1.1/"
           xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"
           xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">
  <item id="1" parentID="0" restricted="1">
    <dc:title>movie.mp4</dc:title>
    <upnp:class>object.item.videoItem.movie</upnp:class>
    <res protocolInfo="http-get:*:video/mp4:*">
      http://192.168.1.20:8899/media/8f3c9a1e
    </res>
  </item>
</DIDL-Lite>
```

### 音频

```xml
<upnp:class>object.item.audioItem.musicTrack</upnp:class>
```

### 图片

```xml
<upnp:class>object.item.imageItem.photo</upnp:class>
```

## 5.6 DLNA 控制模块 DlnaController

### 主要接口

```python
class DlnaController:
    async def set_uri(self, device, media_url, metadata): ...
    async def play(self, device): ...
    async def pause(self, device): ...
    async def stop(self, device): ...
    async def seek(self, device, position): ...
    async def set_volume(self, device, volume): ...
    async def get_position(self, device): ...
    async def get_transport_info(self, device): ...
```

### 对应 UPnP Action

| 功能 | 服务 | Action |
|---|---|---|
| 设置播放地址 | AVTransport | SetAVTransportURI |
| 播放 | AVTransport | Play |
| 暂停 | AVTransport | Pause |
| 停止 | AVTransport | Stop |
| 跳转 | AVTransport | Seek |
| 获取进度 | AVTransport | GetPositionInfo |
| 获取状态 | AVTransport | GetTransportInfo |
| 设置音量 | RenderingControl | SetVolume |
| 获取音量 | RenderingControl | GetVolume |
| 静音 | RenderingControl | SetMute |

## 5.7 发送端播放控制流程

### 开始播放

```text
SetAVTransportURI
        ↓
Play
        ↓
定时 GetPositionInfo
        ↓
更新 UI 进度条
```

### 暂停

```text
Pause
```

### 停止

```text
Stop
```

### 拖动进度

```text
Seek REL_TIME 00:12:35
```

时间格式：

```text
HH:MM:SS
```

---

# 第二部分：DLNA 接收端设计

## 6.1 接收端目标

让本应用在局域网中表现为一个 DLNA MediaRenderer。

```text
手机视频 App
        ↓
发现 “Python DLNA Player”
        ↓
投屏
        ↓
本应用收到播放 URL
        ↓
libmpv 播放
```

## 6.2 接收端核心组件

```text
LocalRendererService
    ├── SSDP Advertiser
    ├── DeviceDescriptionServer
    ├── AVTransportService
    ├── RenderingControlService
    ├── ConnectionManagerService
    └── MPVPlaybackService
```

## 6.3 接收端启动流程

```text
应用启动
    ↓
启动 aiohttp Web Server
    ↓
加载设备描述 XML
    ↓
启动 AVTransport SOAP 服务
    ↓
启动 RenderingControl SOAP 服务
    ↓
启动 ConnectionManager SOAP 服务
    ↓
启动 SSDP Notify 广播
    ↓
局域网设备发现本机 Renderer
```

## 6.4 本机 Renderer 设备描述

设备描述地址示例：

```text
http://192.168.1.20:8898/description.xml
```

### description.xml 结构

```xml
<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>Python DLNA Player</friendlyName>
    <manufacturer>Custom</manufacturer>
    <modelName>PySide6 DLNA Renderer</modelName>
    <UDN>uuid:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:AVTransport</serviceId>
        <SCPDURL>/service/avtransport.xml</SCPDURL>
        <controlURL>/control/avtransport</controlURL>
        <eventSubURL>/event/avtransport</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:RenderingControl</serviceId>
        <SCPDURL>/service/renderingcontrol.xml</SCPDURL>
        <controlURL>/control/renderingcontrol</controlURL>
        <eventSubURL>/event/renderingcontrol</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/service/connectionmanager.xml</SCPDURL>
        <controlURL>/control/connectionmanager</controlURL>
        <eventSubURL>/event/connectionmanager</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>
```

## 6.5 SSDP 广播设计

本机 Renderer 需要周期性发送 NOTIFY 消息。

### 广播地址

```text
239.255.255.250:1900
```

### NOTIFY 示例

```http
NOTIFY * HTTP/1.1
HOST: 239.255.255.250:1900
CACHE-CONTROL: max-age=1800
LOCATION: http://192.168.1.20:8898/description.xml
NT: urn:schemas-upnp-org:device:MediaRenderer:1
NTS: ssdp:alive
SERVER: Python/3 UPnP/1.0 PySide6DLNA/1.0
USN: uuid:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx::urn:schemas-upnp-org:device:MediaRenderer:1
```

应用退出时发送：

```http
NTS: ssdp:byebye
```

## 6.6 AVTransport 服务设计

AVTransport 是接收端最核心的服务，负责设置播放地址、播放、暂停、停止、跳转、获取状态、获取进度。

### 内部状态模型

```python
class TransportState:
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED_PLAYBACK = "PAUSED_PLAYBACK"
    TRANSITIONING = "TRANSITIONING"
    NO_MEDIA_PRESENT = "NO_MEDIA_PRESENT"
```

### 数据模型

```python
class RendererState:
    current_uri: str
    current_metadata: str
    transport_state: str
    duration: float
    position: float
    volume: int
    muted: bool
```

## 6.7 SetAVTransportURI

当手机或其他设备投屏时，会调用 `SetAVTransportURI`。

参数通常包括：

```text
CurrentURI
CurrentURIMetaData
```

处理流程：

```text
接收 SOAP 请求
    ↓
解析 CurrentURI
    ↓
保存 URI 与 Metadata
    ↓
调用 mpv.loadfile(uri)
    ↓
更新状态为 STOPPED 或 TRANSITIONING
    ↓
返回 SOAP 成功响应
```

## 6.8 Play

```text
收到 Play 请求
    ↓
如果已有 current_uri
    ↓
mpv.pause = False
    ↓
如果未加载则 loadfile
    ↓
更新状态 PLAYING
    ↓
通知 UI
```

## 6.9 Pause

```text
收到 Pause 请求
    ↓
mpv.pause = True
    ↓
更新状态 PAUSED_PLAYBACK
    ↓
通知 UI
```

## 6.10 Stop

```text
收到 Stop 请求
    ↓
mpv.command("stop")
    ↓
更新状态 STOPPED
    ↓
清空或保留 current_uri
    ↓
通知 UI
```

## 6.11 Seek

DLNA Seek 常用格式：

```text
Unit: REL_TIME
Target: 00:10:20
```

处理流程：

```text
解析 Target
    ↓
转换为秒
    ↓
mpv.seek(seconds, "absolute")
    ↓
返回 SOAP 成功响应
```

## 6.12 GetPositionInfo

需要返回：

```text
TrackDuration
RelTime
AbsTime
RelCount
AbsCount
TrackURI
TrackMetaData
```

示例：

```xml
<TrackDuration>01:30:20</TrackDuration>
<RelTime>00:10:05</RelTime>
<TrackURI>http://example.com/video.mp4</TrackURI>
```

数据来自 libmpv：

```text
mpv.duration
mpv.time-pos
```

## 6.13 GetTransportInfo

返回当前状态：

```xml
<CurrentTransportState>PLAYING</CurrentTransportState>
<CurrentTransportStatus>OK</CurrentTransportStatus>
<CurrentSpeed>1</CurrentSpeed>
```

## 6.14 RenderingControl 服务设计

RenderingControl 负责音量、静音等。

### 支持 Action

```text
GetVolume
SetVolume
GetMute
SetMute
```

### 音量范围

DLNA 通常使用 `0 - 100`，libmpv 音量也可以映射为 `0 - 100`。

### SetVolume 流程

```text
收到 SetVolume
    ↓
读取 DesiredVolume
    ↓
限制在 0-100
    ↓
mpv.volume = value
    ↓
更新状态
```

### SetMute 流程

```text
收到 SetMute
    ↓
DesiredMute = 0 或 1
    ↓
mpv.mute = True / False
```

## 6.15 ConnectionManager 服务设计

ConnectionManager 用于声明本 Renderer 支持的协议和格式。

### GetProtocolInfo 返回示例

```text
http-get:*:video/mp4:*
http-get:*:video/x-matroska:*
http-get:*:audio/mpeg:*
http-get:*:audio/flac:*
http-get:*:image/jpeg:*
```

返回 XML 中的 Source/Sink：

```xml
<Source></Source>
<Sink>
http-get:*:video/mp4:*,
http-get:*:video/x-matroska:*,
http-get:*:audio/mpeg:*,
http-get:*:image/jpeg:*
</Sink>
```

---

# 第三部分：libmpv 集成设计

## 7.1 libmpv 绑定方式

Python 可使用：

```text
python-mpv
```

安装：

```bash
pip install python-mpv
```

系统还需要安装 mpv/libmpv。Windows 下通常需要 `mpv-2.dll`、`libmpv-2.dll` 或者将 mpv 运行库目录加入 PATH。

## 7.2 PySide6 嵌入 mpv 播放窗口

设计一个 QWidget 作为视频容器：

```python
class MpvWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
```

获取窗口 ID：

```python
wid = int(self.winId())
```

创建 mpv：

```python
self.mpv = mpv.MPV(
    wid=str(wid),
    input_default_bindings=True,
    input_vo_keyboard=True,
    osc=True
)
```

### 注意

| 平台 | 说明 |
|---|---|
| Windows | 使用 HWND |
| Linux X11 | 使用 XID |
| macOS | 嵌入可能更复杂，需额外适配 |

## 7.3 MPVPlaybackService

对 UI 与 DLNA 服务统一暴露播放接口。

```python
class MPVPlaybackService:
    def load(self, url: str): ...
    def play(self): ...
    def pause(self): ...
    def stop(self): ...
    def seek(self, seconds: float): ...
    def set_volume(self, volume: int): ...
    def set_mute(self, muted: bool): ...
    def get_duration(self) -> float: ...
    def get_position(self) -> float: ...
    def get_state(self) -> str: ...
```

这样 DLNA 接收端和本地播放器都调用同一套播放服务。

## 7.4 libmpv 事件监听

需要监听：

- 播放开始
- 文件加载完成
- 播放结束
- 时长变化
- 进度变化
- 暂停状态变化
- 错误状态

示例逻辑：

```text
mpv file-loaded
    ↓
获取 duration
    ↓
通知 UI

mpv end-file
    ↓
状态改为 STOPPED
    ↓
通知 DLNA Event

mpv pause changed
    ↓
同步 RendererState
```

---

# 第四部分：PySide6 UI 设计

## 8.1 主界面布局

推荐布局：

```text
+-------------------------------------------------------+
| 菜单栏                                                |
+---------------------+---------------------------------+
| 设备列表             | 播放区域 libmpv                 |
|                     |                                 |
| - 客厅电视           |                                 |
| - 卧室盒子           |                                 |
| - 本机 Renderer      |                                 |
+---------------------+---------------------------------+
| 本地媒体库 / 文件列表                                  |
+-------------------------------------------------------+
| 播放控制栏：播放 暂停 停止 进度 音量 投屏 本地播放       |
+-------------------------------------------------------+
```

## 8.2 主要页面

### 设备页

- 扫描设备
- 显示设备名称
- 显示制造商
- 显示 IP
- 显示是否支持 AVTransport
- 设置默认设备

### 媒体库页

- 添加文件夹
- 扫描视频、音乐、图片
- 显示封面
- 显示文件大小
- 显示时长
- 双击本地播放
- 右键投屏

### 播放页

- libmpv 播放窗口
- 当前播放标题
- 播放进度
- 音量
- 字幕选择
- 音轨选择
- 全屏

### 设置页

- DLNA Renderer 名称
- HTTP 服务端口
- Renderer 服务端口
- 是否开机启动接收端
- 是否启用硬解
- 默认投屏设备
- FFmpeg 路径
- 日志级别

## 8.3 UI 与异步任务通信

由于 PySide6 主线程负责 UI，DLNA 网络任务建议运行在 asyncio event loop thread 或 QThread。

推荐架构：

```text
UI Thread
    ↓ signal/slot
Async Service Thread
    ↓
async-upnp-client / aiohttp
```

### 信号定义示例

```python
class AppSignals(QObject):
    deviceFound = Signal(object)
    deviceRemoved = Signal(str)
    playbackStateChanged = Signal(str)
    positionChanged = Signal(float, float)
    errorOccurred = Signal(str)
```

---

# 第五部分：项目目录结构

推荐目录：

```text
dlna_player/
│
├── main.py
├── requirements.txt
├── README.md
│
├── app/
│   ├── __init__.py
│   ├── application.py
│   ├── config.py
│   └── logging_config.py
│
├── ui/
│   ├── main_window.py
│   ├── device_panel.py
│   ├── media_library_panel.py
│   ├── player_panel.py
│   ├── settings_dialog.py
│   └── resources.qrc
│
├── dlna/
│   ├── __init__.py
│   ├── device_discovery.py
│   ├── dlna_controller.py
│   ├── didl.py
│   ├── soap.py
│   ├── ssdp.py
│   ├── local_renderer.py
│   ├── av_transport_service.py
│   ├── rendering_control_service.py
│   ├── connection_manager_service.py
│   └── service_description.py
│
├── media/
│   ├── __init__.py
│   ├── media_library.py
│   ├── media_scanner.py
│   ├── media_http_server.py
│   ├── mime_utils.py
│   └── thumbnail.py
│
├── player/
│   ├── __init__.py
│   ├── mpv_widget.py
│   ├── mpv_service.py
│   └── playback_state.py
│
├── storage/
│   ├── __init__.py
│   ├── database.py
│   ├── models.py
│   └── repository.py
│
├── utils/
│   ├── __init__.py
│   ├── network.py
│   ├── time_utils.py
│   ├── xml_utils.py
│   └── platform_utils.py
│
└── tests/
    ├── test_didl.py
    ├── test_time_utils.py
    ├── test_media_http_server.py
    └── test_soap.py
```

---

# 第六部分：关键类设计

## 9.1 ApplicationContext

负责管理全局服务。

```python
class ApplicationContext:
    config: AppConfig
    signals: AppSignals
    device_manager: DeviceManager
    dlna_controller: DlnaController
    media_http_server: MediaHttpServer
    local_renderer: LocalRendererService
    playback_service: MPVPlaybackService
    media_library: MediaLibraryService
```

## 9.2 DeviceManager

职责：

- 启动设备发现
- 维护设备列表
- 检查设备在线状态
- 提供当前选中设备

```python
class DeviceManager:
    async def start_discovery(self): ...
    async def stop_discovery(self): ...
    async def refresh(self): ...
    def get_devices(self): ...
    def get_device(self, uuid): ...
```

## 9.3 MediaHttpServer

职责：

- 启动 HTTP 服务
- 注册媒体文件
- 支持 Range 请求
- 输出媒体访问 URL

```python
class MediaHttpServer:
    async def start(self, host, port): ...
    async def stop(self): ...
    def register_file(self, path: str) -> str: ...
    async def handle_media_request(self, request): ...
```

## 9.4 LocalRendererService

职责：

- 启动本机 DLNA Renderer
- 提供 description.xml
- 提供 SOAP control endpoint
- 启动 SSDP 广播

```python
class LocalRendererService:
    async def start(self): ...
    async def stop(self): ...
    async def send_alive_notify(self): ...
    async def send_byebye_notify(self): ...
```

## 9.5 AVTransportService

职责：

- 解析 AVTransport SOAP 请求
- 映射到 MPVPlaybackService
- 返回 SOAP 响应

```python
class AVTransportService:
    async def handle_action(self, action_name, arguments): ...
    async def set_av_transport_uri(self, current_uri, metadata): ...
    async def play(self): ...
    async def pause(self): ...
    async def stop(self): ...
    async def seek(self, unit, target): ...
    async def get_position_info(self): ...
    async def get_transport_info(self): ...
```

## 9.6 RenderingControlService

```python
class RenderingControlService:
    async def get_volume(self): ...
    async def set_volume(self, volume): ...
    async def get_mute(self): ...
    async def set_mute(self, muted): ...
```

---

# 第七部分：线程与异步模型

## 10.1 推荐模型

```text
主线程：
    PySide6 UI

后台 asyncio 线程：
    aiohttp server
    SSDP discovery
    SSDP notify
    DLNA SOAP client/server

libmpv：
    由 UI 播放控件持有
    播放控制通过 Qt Signal 调用
```

## 10.2 为什么不直接在 UI 线程跑 asyncio？

因为：

- SSDP 查询可能阻塞
- aiohttp 需要独立 event loop
- SOAP 请求不能卡 UI
- 设备发现需要长期运行
- Renderer 需要持续监听外部请求

## 10.3 线程通信原则

不要在后台线程直接操作 Qt 控件。

错误做法：

```python
self.main_window.label.setText("PLAYING")
```

正确做法：

```python
signals.playbackStateChanged.emit("PLAYING")
```

然后 UI 线程更新界面。

---

# 第八部分：网络与端口设计

## 11.1 默认端口

| 服务 | 默认端口 |
|---|---:|
| SSDP | 1900 |
| 本地媒体 HTTP Server | 8899 |
| 本机 Renderer HTTP/SOAP Server | 8898 |

## 11.2 网络地址选择

如果电脑有多个网卡，需要选择正确的局域网 IP。

推荐优先使用：

```text
192.168.x.x
10.x.x.x
172.16.x.x - 172.31.x.x
```

避免使用：

```text
127.0.0.1
169.254.x.x
虚拟网卡 IP
VPN IP
```

## 11.3 防火墙提示

首次启动时应提示用户允许：

- Python
- 应用 exe
- SSDP UDP
- HTTP Server 端口
- Renderer Server 端口

否则可能出现搜不到电视、电视无法访问媒体 URL、手机搜不到本机 Renderer 等问题。

---

# 第九部分：兼容性设计

## 12.1 视频格式兼容

虽然 libmpv 本地播放能力很强，但电视作为远端 Renderer 时，支持格式取决于电视。

推荐投屏优先格式：

```text
MP4 + H.264 + AAC
```

一般兼容最好。

## 12.2 常见问题

| 问题 | 原因 | 方案 |
|---|---|---|
| 电视搜不到 | 防火墙、不同网段、组播被禁 | 检查网络和防火墙 |
| 可以投但不播放 | 格式不支持 | 转码为 MP4/H.264/AAC |
| 不能拖动进度 | HTTP 不支持 Range | 实现 206 响应 |
| 播放一会儿断开 | HTTP 连接异常 | 优化分块读取 |
| 手机搜不到本机 Renderer | SSDP NOTIFY 不完整 | 完善 description.xml 和 alive 广播 |
| 某些电视无响应 | SOAP 参数不兼容 | 针对厂商适配 |

## 12.3 厂商适配策略

建议保留设备兼容层：

```python
class DeviceCompatibilityAdapter:
    def build_metadata(self, device, media): ...
    def normalize_action_args(self, device, action, args): ...
    def before_play(self, device): ...
```

可针对 Samsung、LG、Sony、Hisense、TCL、Xiaomi、Chromecast built-in 设备做兼容处理。

---

# 第十部分：配置设计

## 13.1 配置文件

建议使用：

```text
config.json
```

示例：

```json
{
  "app_name": "PySide6 DLNA Player",
  "renderer_name": "Python DLNA Player",
  "media_server_port": 8899,
  "renderer_server_port": 8898,
  "enable_renderer": true,
  "enable_discovery": true,
  "default_device_uuid": "",
  "media_folders": [],
  "mpv": {
    "hwdec": "auto-safe",
    "volume": 80,
    "sub_auto": true,
    "audio_device": "auto"
  },
  "network": {
    "preferred_interface": "",
    "ssdp_interval": 30
  }
}
```

---

# 第十一部分：日志设计

## 14.1 日志分类

```text
logs/
├── app.log
├── dlna.log
├── http.log
├── mpv.log
└── error.log
```

## 14.2 需要记录的关键事件

- 应用启动/退出
- HTTP Server 启动失败
- SSDP 发现请求
- 设备上线/下线
- SOAP 请求与响应
- 投屏 URL
- mpv 播放错误
- Range 请求异常
- 防火墙/端口占用异常

---

# 第十二部分：异常处理设计

## 15.1 设备发现异常

```text
异常：组播失败
提示：请检查防火墙或网络环境
```

## 15.2 投屏失败

可能原因：

- 设备离线
- URL 不可访问
- 文件格式不支持
- SOAP 调用失败
- HTTP Server 未启动

处理策略：

```text
1. 检查 HTTP Server 状态
2. 检查目标设备在线状态
3. 发送 SetAVTransportURI
4. 如果失败，尝试简化 DIDL metadata
5. 再失败，提示用户转换格式
```

## 15.3 接收端播放失败

可能原因：

- 对方传入的 URL 不可访问
- HTTPS 证书问题
- 需要 User-Agent
- 格式不支持
- mpv 无法解码

处理策略：

```text
1. 将 URL 交给 mpv
2. 如果 mpv 报错，记录 error
3. 状态改为 STOPPED
4. 返回 SOAP 错误或播放失败状态
```

---

# 第十三部分：安全设计

## 16.1 本地 HTTP 服务访问控制

由于 DLNA 在局域网中工作，HTTP 服务理论上局域网设备都能访问。

建议：

- 只监听局域网 IP，不监听 0.0.0.0，或提供配置项
- 媒体 URL 使用随机 media_id
- 不暴露真实路径
- 禁止目录遍历
- 限制访问根目录
- 可选添加临时 token

示例 URL：

```text
http://192.168.1.20:8899/media/8f3c9a1e?token=xxxx
```

## 16.2 SOAP 接口保护

DLNA 本身不强调鉴权，但桌面端可添加：

- 仅允许同网段访问
- 可选择信任设备
- 记录控制来源 IP
- 弹窗确认陌生设备投屏请求

---

# 第十四部分：打包发布设计

## 17.1 Windows 打包

推荐：

```bash
pyinstaller -w main.py
```

需要包含：

```text
libmpv-2.dll
mpv-2.dll
python-mpv
PySide6 plugins
```

常见目录：

```text
dist/
├── DLNAPlayer.exe
├── libmpv-2.dll
├── mpv-2.dll
├── platforms/
├── styles/
└── resources/
```

## 17.2 macOS

注意：

- libmpv dylib 路径
- Qt 插件路径
- 权限弹窗
- 本地网络访问权限
- Bonjour/组播限制

## 17.3 Linux

依赖：

```bash
sudo apt install mpv libmpv-dev
```

或者发行版对应包管理器安装。

---

# 第十五部分：开发阶段规划

## 18.1 第一阶段：最小可用发送端

目标：

- PySide6 主界面
- 扫描 DLNA 设备
- 启动本地 HTTP Server
- 选择本地 MP4 文件
- 推送到电视播放
- 支持 Play / Pause / Stop

优先级最高。

## 18.2 第二阶段：完善发送端

增加：

- Range 请求
- 进度获取
- Seek
- 音量控制
- DIDL-Lite 元数据
- 媒体库
- 设备收藏

## 18.3 第三阶段：本地播放器

增加：

- libmpv 嵌入 PySide6
- 本地播放
- 字幕
- 音轨
- 全屏
- 播放列表

## 18.4 第四阶段：DLNA 接收端

增加：

- description.xml
- SSDP alive/byebye
- AVTransport SOAP
- RenderingControl SOAP
- ConnectionManager SOAP
- 接收手机投屏并用 libmpv 播放

## 18.5 第五阶段：兼容与发布

增加：

- 厂商适配
- 防火墙提示
- 日志系统
- 自动更新
- Windows/macOS/Linux 打包
- 崩溃日志

---

# 第十六部分：推荐依赖

```txt
PySide6
python-mpv
aiohttp
async-upnp-client
lxml
zeroconf
SQLAlchemy
pydantic
watchdog
```

- `PySide6`：桌面 UI
- `python-mpv`：libmpv Python 封装
- `aiohttp`：HTTP / SOAP Server
- `async-upnp-client`：UPnP / DLNA 客户端
- `lxml`：XML 解析与生成
- `SQLAlchemy`：数据库 ORM
- `watchdog`：监听媒体目录变化

---

# 第十七部分：核心难点总结

## 19.1 发送端难点

主要难点是：

```text
HTTP Range + DIDL-Lite + 不同电视兼容
```

如果只实现 SetAVTransportURI，很多设备可能可以播放，但体验不稳定。

## 19.2 接收端难点

主要难点是：

```text
SSDP 广播 + SOAP 协议完整性 + 状态同步
```

本机 Renderer 必须让外部设备认为它是一个合格的 MediaRenderer。

## 19.3 libmpv 难点

主要难点是：

```text
跨平台窗口嵌入 + 线程安全 + 播放状态回调
```

尤其是 macOS 和 Wayland 环境需要额外测试。

---

# 第十八部分：建议 MVP 范围

如果要快速落地，建议 MVP 先做以下功能：

```text
1. PySide6 主窗口
2. 本地视频选择
3. aiohttp 媒体 HTTP Server
4. SSDP 搜索电视
5. SetAVTransportURI + Play
6. Pause / Stop
7. 本地 libmpv 播放
```

暂时不做：

```text
1. 完整媒体库
2. 图片投屏
3. 音乐投屏
4. 转码
5. 完整 DMR 接收端
6. 多厂商适配
```

等发送端稳定后，再实现接收端 Renderer。

---

## 结论

使用 **Python + PySide6 + libmpv** 完全可以开发一个同时支持 **DLNA 发送端和接收端** 的桌面应用。

推荐最终技术架构是：

```text
PySide6
    负责 GUI

libmpv / python-mpv
    负责本地播放和接收端渲染

aiohttp
    负责媒体 HTTP Server 与 Renderer SOAP Server

async-upnp-client
    负责发现和控制外部 DLNA 设备

SQLite
    负责配置、媒体库、历史记录
```

开发顺序建议：

```text
先做发送端
再做本地播放器
最后做接收端 Renderer
```

这样风险最低，也最容易逐步验证功能。
