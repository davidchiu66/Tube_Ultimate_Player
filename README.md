# Tube_Ultimate_Player

<p align="center">
  <img src="docs/assets/readme-banner.svg" alt="Tube_Ultimate_Player banner" width="100%" />
</p>

<p align="center">
  <strong>基于 PySide6 + yt-dlp + libmpv 的 Windows 桌面视频播放器与下载工具</strong>
</p>

<p align="center">
  面向 YouTube 与 Bilibili 的搜索、首页浏览、URL 播放、播放列表管理与下载场景。
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-2563eb?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-1d4ed8?style=flat-square">
  <img alt="UI" src="https://img.shields.io/badge/UI-PySide6-0f766e?style=flat-square">
  <img alt="Resolver" src="https://img.shields.io/badge/Resolver-yt--dlp-7c3aed?style=flat-square">
  <img alt="Player" src="https://img.shields.io/badge/Player-libmpv-0891b2?style=flat-square">
</p>

## 亮点

- 同时支持 YouTube 与 Bilibili，两站点共用统一播放器、下载器、收藏与历史体系
- 支持首页推荐、关键词搜索、URL 直接播放、播放列表详情页与播放器侧滑播放列表面板
- 单视频播放后可在后台生成作者作品播放列表，不阻塞当前视频首帧播放
- 使用 `libmpv` 播放，支持暂停、停止、全屏、清晰度切换、倍速、字幕与自动隐藏控制面板
- 支持发现局域网 DLNA 播放设备，将在线视频和本地下载媒体远程投屏
- 投屏设备列表支持会话内缓存，后续打开时先快速校验 IP + 端口，缓存失效后再重新扫描
- 内置下载队列，支持并发下载、暂停、继续、删除、完成回放与完成 toast 提示
- 下载列表、收藏和播放历史显示视频来源，并支持本地筛选搜索
- 支持浏览器 Cookie 自动提取、手动 Cookie 文本保存、代理、FFmpeg、下载目录与默认首页配置
- 运行时配置、数据库、日志、下载目录统一写入 `%LocalAppData%\Tube_Ultimate_Player`

## 界面与流程

<p align="center">
  <img src="docs/assets/readme-workflow.svg" alt="Tube_Ultimate_Player workflow" width="100%" />
</p>

### 典型使用方式

1. 启动应用后进入默认首页，可选择 `Bilibili` 或 `YouTube`
2. 在顶部搜索框输入关键词，或通过“播放 URL”弹窗直接输入任意站点地址
3. 双击首页卡片、搜索结果、收藏、历史或播放列表条目开始播放
4. 在播放器中切换清晰度、倍速、全屏，或点击“投屏”将当前在线视频发送到局域网 DLNA 设备
5. 下载完成后，可直接在下载列表中双击本地文件再次播放
6. 在下载列表、收藏或历史页面中，可按标题、来源、作者或链接快速筛选

## 当前功能

| 模块 | 能力 |
| --- | --- |
| 首页 | YouTube / Bilibili 推荐内容、分页浏览、九宫格卡片展示 |
| 搜索 | 双站点关键词搜索、分页、等待动画与温馨提示 |
| URL 播放 | 弹窗输入 URL，自动识别 YouTube / Bilibili / 列表类链接 |
| 播放器 | `libmpv` 播放、暂停/继续、停止、自然结束重播、全屏、双击切换全屏、自动隐藏控制器 |
| 播放列表 | 明确播放列表、作者动态列表、侧滑播放列表面板、命名保存、自动连播、批量下载 |
| DLNA 投屏 | SSDP 多网卡发现、设备缓存校验、在线视频/本地媒体投屏、远程播放/暂停/停止、进度同步、Seek、音量控制 |
| 下载 | 下载队列、并发控制、暂停、继续、删除、来源显示、搜索、完成提示、本地文件播放 |
| 数据列表 | 收藏、历史、下载任务的来源显示、搜索筛选与统一表格布局 |
| 设置 | 代理、Cookie 文本、浏览器 Cookie 自动获取、FFmpeg、JS Runtime、下载目录、默认首页 |
| 关于 | 当前版本、GitHub 链接、检测新版本、Release Note 展示、在线升级下载 |

## Bilibili 支持说明

当前版本已经接入以下 Bilibili 能力：

- 首页推荐
- 关键词搜索
- 单视频 URL 播放
- 下载
- 多 P 视频
- 番剧/剧集 `ep` / `ss`
- 稍后再看
- 收藏夹 / 媒体列表
- UP 主空间合集 / season 列表

搜索链路采用分层回退：

1. 优先使用浏览器已登录 Cookie 调用公开搜索 API
2. 失败时回退到 WBI 签名搜索 API
3. 仍失败时回退到搜索结果页抓取

## 快速开始

### 运行环境

- Windows 10 / 11
- Python 3.10+
- `3rdpart/` 中的运行依赖文件

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动

```bash
python main.py
```

## 目录结构

```text
Tube_Ultimate_Player/
├── 3rdpart/                  # 第三方二进制依赖（仓库内不提交 libmpv-2.dll 与 yt-dlp.exe）
├── config/                   # 默认配置模板
├── database/                 # SQLite 持久化与仓储
├── docs/                     # 设计文档、发布说明与 README 资源
│   ├── assets/
│   └── releases/
├── download/                 # 下载队列与下载 worker
├── dlna/                     # SSDP 发现、SOAP 控制、DIDL 与在线媒体 HTTP 中继
├── player/                   # libmpv 封装
├── resolver/                 # 站点解析、搜索、首页加载
├── resources/                # QSS 与图标资源
├── services/                 # 配置、日志、Cookie、升级、FFmpeg 等服务
├── ui/                       # PySide6 页面与组件
├── workers/                  # 后台线程任务
├── .github/workflows/        # 发布工作流
├── app_version.txt           # 当前正式版本号
├── build_installer.py        # 安装包构建脚本
├── build_portable.py         # 便携版构建脚本
└── main.py
```

## 运行时目录

应用运行时会优先写入：

```text
%LocalAppData%\Tube_Ultimate_Player
```

其中包括：

- `config/user_config.json`
- `cookie.txt`
- `data/tube_ultimate_player.sqlite3`
- `data/download_tasks.json`
- `downloads/`
- `logs/app.log`
- `logs/yt-dlp.log`
- `cache/`
- `updates/`

如果当前环境无法写入该目录，程序会回退到当前用户可写目录；在受限调试环境中，通常会回退到项目内的 `runtime/`。

## 构建与发布

### 版本号

发布版本统一从根目录 `app_version.txt` 读取，当前仓库使用正式版发布流程，不再使用预发布标签。

### GitHub Actions

仓库中提供三套工作流：

- `release.yml`：完整正式发布流程，包含便携版、安装包构建与 GitHub Release 发布
- `release-portable.yml`：仅构建便携版
- `release-installer.yml`：仅构建安装包

正式发布流程会：

1. 读取 `app_version.txt`
2. 校验 `docs/releases/v<version>.md` 是否存在
3. 下载最新 `yt-dlp.exe`
4. 从 SourceForge 下载 `libmpv-2.dll`
5. 结合仓库中的其余 `3rdpart` 依赖构建产物
6. 上传便携版、安装包，并发布到 GitHub Releases

## JS Runtime 说明

部分站点解析链路依赖 JS Runtime。若系统未安装 Node.js，可在“设置”页直接检测并触发安装。

若用户不希望自动安装，也可以手动安装 Node.js：

1. 前往官方站点：https://nodejs.org/
2. 安装 LTS 版本
3. 重启应用后重新检测

## DLNA 投屏说明

1. 电脑与电视/盒子需要位于同一局域网。
2. 播放在线视频后，点击控制面板中“全屏”之前的“投屏”按钮。
3. 首次打开会扫描局域网设备；后续打开会优先校验缓存设备，在线设备直接展示。
4. 选择设备并开始投屏；投屏成功后本地播放自动暂停。
5. 投屏期间播放、暂停、停止、音量和可用的进度控制会路由到远端设备。
6. Bilibili 和部分 YouTube 清晰度使用分离音视频流，需要在设置中配置 FFmpeg；应用会实时封装为电视可播放的 MPEG-TS，不重新编码视频。
7. 下载列表中的本地视频、音频也可投屏，设备通过局域网 HTTP 服务读取文件。
8. 实时封装流不支持字节 Range，因此本期禁用进度拖动；停止投屏时会按电视最近进度恢复本地播放。
9. 如果搜索不到设备，请检查系统防火墙、路由器组播设置，并确认设备支持 DLNA MediaRenderer/AVTransport。

## 下载、收藏与历史

- 三个列表统一使用黑底金边表格风格，避免深色主题下出现白底白字不可读的问题。
- 下载任务、收藏和播放历史均显示来源（`YouTube` / `Bilibili`）。
- 收藏和播放历史保存作者信息；旧 SQLite 数据库启动时会自动补充缺失字段。
- 列表搜索为本地即时筛选，不会重复请求站点接口。

## 测试

运行完整自动化测试：

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## 第三方组件与合规声明

本项目依赖或调用以下第三方组件：

- [PySide6](https://doc.qt.io/qtforpython/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [mpv / libmpv](https://mpv.io/)
- [FFmpeg](https://ffmpeg.org/)

其中：

- `yt-dlp.exe` 为独立第三方可执行文件，本项目仅调用其公开命令行能力
- `libmpv-2.dll` 为独立第三方动态库，本项目通过 `ctypes` 调用其公开接口
- FFmpeg 由用户本机或设置页指定路径提供，本项目不修改其行为

更多说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

### 合规性声明

1. 本项目是通用桌面客户端，不提供任何受版权保护内容的托管、镜像或转售服务。
2. 用户应仅在拥有合法权利、授权或当地法律允许的情况下使用本工具访问、播放或下载内容。
3. 用户应自行遵守 YouTube、Bilibili 及相关平台的服务条款、版权政策、地区限制、年龄限制和其他适用规则。
4. 本项目不保证对任何第三方平台的持续可用性、兼容性或访问权限。
5. 用户导入的 Cookie、代理、账号状态及下载行为均由用户自行负责；仓库不包含任何真实个人 Cookie、账号数据或私有配置。
6. 本项目对第三方软件和服务的引用仅用于兼容与集成说明，不代表对其拥有权或附带再授权。

## 仓库说明

为避免个人数据泄露，以下内容不应提交到仓库：

- `cookie.txt`
- `config/user_config.json`
- `logs/`
- `downloads/`
- `cache/`
- `data/`
- `runtime/`
- `updates/`
- `__pycache__/`

这些内容已在 `.gitignore` 中忽略。
