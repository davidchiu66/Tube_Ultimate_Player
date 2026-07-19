# Tube_Ultimate_Player Linux 发布技术可行性评估与实施方案

## 1. 文档状态

- 文档性质：技术评估、实施方案、已审核备案
- 审核日期：2026-07-19
- 评估对象：Ubuntu、Fedora 及其他带 GUI 的主流 Linux 发行版
- 当前项目版本：`0.2.10`
- 目标架构：首期 `x86_64`
- 当前结论：**有条件可行，维护者已批准既定首版边界并已启动编码实施**
- 实施状态：已启动阶段 0/1；平台基础、Linux CI 和 AppImage/DEB 构建链路已进入代码实现，等待 Linux Runner 与人工图形验收

## 2. 执行摘要

Tube_Ultimate_Player 的主体技术栈为 Python、PySide6、yt-dlp、libmpv、FFmpeg 和 SQLite，除现有发布与运行时安装逻辑外，大部分组件都具备 Linux 支持能力。

从现有代码审计结果看：

- UI、数据库、播放列表、收藏、历史、下载队列、站点解析和大部分 DLNA 逻辑可直接复用。
- Linux 发布的核心阻碍不是 PySide6，而是当前播放器使用 libmpv 的 `wid` 窗口嵌入方式。
- `wid` 在 X11 下成熟可用；在原生 Wayland 下不能视为稳定的通用方案。
- 当前自动升级、Node.js 安装、FFmpeg 安装、浏览器 Cookie 检测和构建工作流明显偏向 Windows，需要平台抽象。
- 通过把首版明确限定为“X11 或 Wayland 会话中的 XWayland”，可以在不重写播放器渲染层的情况下提供可维护的 Linux 版本。

因此，已批准的首版策略为：

1. 仅支持 `x86_64`。
2. 官方验证 Ubuntu 22.04 LTS、Ubuntu 24.04 LTS。
3. Fedora 官方验证最新两个稳定版本；首期优先通过 AppImage 提供。
4. 播放窗口运行于 X11/XWayland，暂不承诺原生 Wayland。
5. 第一阶段提供 AppImage；Ubuntu 同时提供 `.deb`。
6. Fedora 原生 `.rpm` 作为第二阶段交付。
7. Linux 首版暂不实现 Windows 式自动覆盖升级，仅保留版本检测和下载/打开 Release 页面。
8. AppImage 必须捆绑 libmpv；优先发布同时自带 Deno、yt-dlp、FFmpeg/FFprobe 的增强版，并在发行包中完整履行第三方开源许可证义务。

在上述约束下，整体可行性评估为：

| 目标 | 可行性 | 说明 |
| --- | --- | --- |
| Ubuntu X11/XWayland 桌面版 | 高 | 现有 `wid` 嵌入方式可继续使用 |
| Fedora X11/XWayland 桌面版 | 高 | 主要差异在依赖名称和打包体系 |
| 其他 glibc 桌面发行版 AppImage | 中高 | 需处理 glibc 基线和系统图形库兼容 |
| 原生 Wayland 嵌入播放 | 中低 | 建议后续改用 libmpv Render API |
| ARM64 Linux | 中 | 依赖与 CI 需要单独准备，不纳入首期 |
| Alpine/musl | 低 | PyInstaller、Qt 和媒体依赖成本较高 |
| 无桌面环境/headless | 不支持 | 本项目是桌面 GUI 播放器 |

## 3. 现有代码跨平台审计

### 3.1 可直接复用的模块

以下模块原则上没有阻断 Linux 的设计问题：

- `resolver/`
  - YouTube/Bilibili URL 识别
  - 首页、搜索、播放列表和作者视频解析
  - 字幕解析和清晰度选择
- `database/`
  - SQLite 数据库
  - 收藏、历史和播放列表仓储
- `download/`
  - 下载任务模型、队列和状态持久化
  - yt-dlp 命令拼装框架
- `ui/`
  - 首页、搜索、播放器控制面板、设置、关于、收藏、历史、下载列表
  - Qt 布局、信号和主题体系
- `dlna/`
  - SSDP、SOAP、DIDL、HTTP 媒体服务主体逻辑
- `workers/`
  - 基于 `QThreadPool` 的后台任务模型
- `services/cookie_service.py`
  - Netscape Cookie 与原始 Cookie 文本解析主体
- `services/logging_service.py`
  - 日志脱敏和文件日志主体

这些模块仍需要 Linux 集成测试，但不需要推倒重写。

### 3.2 需要修改的跨平台边界

| 模块 | 当前状态 | Linux 所需调整 |
| --- | --- | --- |
| `player/mpv_player.py` | 优先加载 Windows DLL，使用 `wid` | 使用 `ctypes.util.find_library("mpv")`，兼容 `libmpv.so.2`、`libmpv.so.1`；区分 X11/XWayland与原生 Wayland |
| `app_paths.py` | Windows 使用 LocalAppData，其他平台统一写入 `~/.Tube_Ultimate_Player` | 按 XDG Base Directory 规范拆分 config/data/cache/state/downloads |
| `download/command_builder.py` | 优先 `yt-dlp.exe`、`ffmpeg.exe` | 支持 `yt-dlp_linux`、`yt-dlp`、`ffmpeg`、`ffprobe` |
| `resolver/youtube_resolver.py` | 优先 `yt-dlp.exe` | 增加 Linux 内置二进制和 PATH 查找 |
| `services/config_service.py` | 浏览器探测仅实现 Windows 路径 | 增加 XDG 下 Chrome/Chromium/Brave/Firefox Profile 探测 |
| `services/cookie_service.py` | Firefox Profile 根目录依赖 `%APPDATA%` | 使用 `~/.mozilla/firefox`、Flatpak/Snap 路径；处理 Linux Secret Service |
| `services/runtime_install_service.py` | 下载 Windows Node MSI，使用 `os.startfile` | Linux 禁用 MSI 流程；优先内置 Deno或系统运行时，提供发行版安装说明 |
| `services/ffmpeg_install_service.py` | 下载 Windows 7z 包 | Linux 使用系统包或 Linux 专用压缩包，不复用 Windows 下载地址 |
| `services/update_service.py` | 选择 EXE/ZIP并用 PowerShell替换 | Linux 需要 AppImage、DEB、RPM 资产选择与不同升级策略 |
| `build_installer.py` | PyInstaller + Inno Setup | 新增 Linux PyInstaller、AppImage、DEB、RPM 构建脚本 |
| `.github/workflows/` | 全部正式构建运行于 `windows-latest` | 增加 Ubuntu 构建矩阵和 Fedora 容器/Runner |
| `ui/main_window.py` | 启动后可提示下载 Windows FFmpeg/Node | 按平台隐藏或替换安装入口，避免 Linux 调用 Windows 安装流程 |

## 4. 播放器与显示系统评估

### 4.1 当前渲染模式

当前播放器在 libmpv 初始化前设置：

```text
wid=<Qt QWidget.winId()>
vo=gpu-next
```

这是一种传统窗口嵌入方案。

### 4.2 X11

在 X11 下，`QWidget.winId()` 返回 X11 Window ID，libmpv `wid` 嵌入方案成熟，技术风险可控。

结论：首版可以正式支持。

### 4.3 Wayland + XWayland

在 GNOME/KDE Wayland 会话中，可以强制 Qt 使用 `xcb` 平台插件，让应用通过 XWayland 运行：

```bash
QT_QPA_PLATFORM=xcb Tube_Ultimate_Player
```

此时 libmpv 仍获得 X11 Window ID，现有播放器结构可保留。

首版启动器应在检测到 `WAYLAND_DISPLAY` 时：

1. 检查 XWayland/xcb 依赖是否可用。
2. 默认设置 `QT_QPA_PLATFORM=xcb`。
3. 提供环境变量允许高级用户覆盖。
4. 在日志中明确记录实际 Qt 平台插件。

结论：首版建议正式支持，但文档必须说明实际通过 XWayland 运行。

### 4.4 原生 Wayland

原生 Wayland 不提供可直接交给外部进程/库的传统全局窗口句柄语义。继续依赖 `wid` 无法保证在不同桌面、Qt 版本和 mpv 版本上稳定工作。

如果后续要求原生 Wayland，应考虑：

- libmpv Render API
- OpenGL/Vulkan 渲染上下文
- Qt `QOpenGLWidget`、`QQuickFramebufferObject` 或自定义渲染表面
- HiDPI、色彩空间、VSync、窗口重建和显卡驱动兼容

这属于播放器渲染层重构，不建议放入 Linux 首版。

## 5. Linux 首版支持范围

### 5.1 官方支持

建议首期定义为：

- CPU：x86_64
- 桌面：GNOME、KDE Plasma、Xfce
- 显示：
  - X11
  - Wayland 会话中的 XWayland
- 发行版：
  - Ubuntu 22.04 LTS
  - Ubuntu 24.04 LTS
  - Fedora 最新两个稳定版本
- 文件系统：本地 ext4、btrfs、xfs 等普通用户可写文件系统
- 音频：PipeWire、PulseAudio 或 mpv 可识别的 ALSA 环境

### 5.2 尽力支持

- Linux Mint
- Debian 12 及以后版本
- openSUSE Tumbleweed/Leap
- Arch Linux/Manjaro
- 其他使用 glibc、X11/XWayland 和标准桌面栈的发行版

尽力支持仅针对 AppImage，不保证原生软件包或所有硬件加速组合。

### 5.3 首期不支持

- 原生 Wayland 渲染模式
- ARM64/aarch64
- 32 位 x86
- Alpine Linux/musl
- 无 GUI/headless 服务器
- 容器内直接进行硬件加速播放
- Snap/Flatpak 正式包
- Linux 下静默自动安装系统依赖

## 6. 运行环境详细要求

### 6.1 通用要求

- glibc 版本不得低于构建基线
- 可用的 X11 或 XWayland
- OpenGL 3.3 级别或 mpv `gpu-next` 可用的图形驱动
- CA 根证书和可用 HTTPS 网络
- 普通用户可写的 XDG 配置、缓存、数据和下载目录
- DEB/RPM 等系统包允许并通常需要通过 root 权限安装
- 图形应用默认由发起桌面会话的普通用户运行；root 运行仅作为尽力支持场景，不纳入首版正式兼容性承诺

### 6.1.1 root 安装与运行边界

“使用 root 安装”和“以 root 身份运行应用”必须分开处理：

- 使用 `sudo apt install`、`sudo dpkg -i` 或后续的 `sudo dnf install` 安装系统包属于正常且受支持的安装方式。
- AppImage 不需要安装，普通用户赋予执行权限后即可运行；不应要求使用 root。
- 首版不主动禁止 root 启动，但应显示安全与兼容性警告，并在日志中记录实际 UID、HOME 和桌面会话类型。
- root 进程通常使用 `/root` 作为 HOME，因此会创建独立配置、数据库、Cookie 和下载目录，不会自动继承桌面用户的数据。
- 在 X11 中，root 进程只有在具备正确的 `DISPLAY` 与 `XAUTHORITY` 授权时才能连接用户的显示会话；应用不得自动执行 `xhost +` 等放宽访问控制的操作。
- 在 Wayland/XWayland 会话中，显示套接字、DBus、Secret Service、PipeWire/PulseAudio 等通常按登录用户隔离，root 运行可能无法创建窗口、读取浏览器 Cookie、访问密钥环或输出音频。
- root 下载或生成的文件默认归 root 所有，普通用户后续可能无法修改或删除；应用只负责提示，不自动递归修改文件所有权。
- root 运行会扩大媒体解析器、浏览器 Cookie 和网络内容处理的权限风险，因此文档和 UI 均应明确标注“不建议”。

结论：**root 权限安装完全支持；root 身份运行不做硬性封禁，但仅提供尽力支持，正式测试和问题受理以普通桌面用户运行环境为基准。**

### 6.2 Ubuntu 建议依赖

具体包名需在 CI 中按版本验证，基线建议包括：

```bash
sudo apt install \
  libmpv-dev mpv ffmpeg \
  libxcb-cursor0 libxkbcommon-x11-0 \
  libgl1 libegl1 libfontconfig1 libdbus-1-3 \
  xwayland
```

注意：

- Ubuntu 22.04 与 24.04 的 libmpv 二进制包名和 SONAME 可能不同。
- 实现必须通过 `find_library` 和多个 SONAME 候选处理差异，不能只写死 `libmpv.so.2`。
- AppImage 若依赖 FUSE 运行，Ubuntu 22.04+ 可能需要 `libfuse2`；同时应支持 `--appimage-extract-and-run` 兜底。

### 6.3 Fedora 建议依赖

基线建议包括：

```bash
sudo dnf install \
  mpv-libs \
  libxkbcommon-x11 xcb-util-cursor \
  mesa-libGL mesa-libEGL fontconfig dbus-libs \
  xorg-x11-server-Xwayland
```

FFmpeg 说明：

- Fedora 官方仓库可能提供受限的 `ffmpeg-free`。
- 完整编解码能力通常来自 RPM Fusion 或应用自带的合规构建。
- 如果增强包捆绑 FFmpeg，必须同时处理许可证与更新责任。

## 7. 目录与权限方案

Linux 应遵循 XDG Base Directory 规范。

建议目录：

```text
$XDG_CONFIG_HOME/Tube_Ultimate_Player/
  user_config.json
  cookie_youtube.txt
  cookie_bilibili.txt

$XDG_DATA_HOME/Tube_Ultimate_Player/
  tube_ultimate_player.sqlite3
  download_tasks.json

$XDG_CACHE_HOME/Tube_Ultimate_Player/
  thumbnails/
  subtitles/
  cookies/
  updates/

$XDG_STATE_HOME/Tube_Ultimate_Player/
  logs/

$HOME/Videos/Tube_Ultimate_Player/
  下载文件
```

若对应环境变量未设置，应回退到：

- `~/.config`
- `~/.local/share`
- `~/.cache`
- `~/.local/state`

安全要求：

- Cookie 文件权限应设置为 `0600`。
- 日志不得输出 Cookie、代理密码和下载鉴权参数。
- 不应在 `/opt`、`/usr` 或 AppImage 挂载目录写入运行数据。
- root 启动时应明确提示将使用 `/root` 下的独立数据目录，并警告所创建文件可能无法被普通用户管理；不得将 root 数据静默写入普通用户目录。

## 8. 依赖与捆绑策略

### 8.1 yt-dlp

可选方案：

1. 随包提供官方 `yt-dlp_linux`。
2. 使用 Python 包入口。
3. 使用系统 `yt-dlp`。

建议顺序：内置 `yt-dlp_linux` → 系统 PATH → 明确错误提示。

### 8.2 Deno/JS Runtime

增强 AppImage 可捆绑官方 Linux x86_64 Deno 单文件可执行程序。

普通 DEB/RPM 可：

- 优先使用系统 Deno/Node。
- 未检测到时给出发行版安装命令或官网链接。
- 不在 Linux 上复用 Windows MSI 安装流程。

### 8.3 FFmpeg

可以提供标准包和增强包，但首版发布优先保障增强包：

- 标准包：依赖系统 FFmpeg。
- 增强包：捆绑 Deno、FFmpeg/FFprobe，作为首版优先交付和主要推荐下载项。

增强包需要：

- 固定上游来源和版本。
- 保存 SHA256。
- 附带相应许可证文本。
- 明确 GPL/LGPL 构建属性。
- 建立安全更新机制。

### 8.4 libmpv

DEB/RPM 可以通过包依赖使用系统 libmpv，以减少驱动和媒体库冲突。

维护者已确认 AppImage 必须捆绑 libmpv，以实现开箱即用。阶段 0 可以先用系统 libmpv 验证 `wid` 嵌入技术路径，但正式 AppImage 验收必须切换到随包 libmpv，并完成其动态依赖闭包检查。

捆绑范围应包含 libmpv 及其运行所需、允许再分发的用户态依赖，但不应捆绑 glibc、GPU 驱动或主机 Mesa 驱动。构建产物必须附带 FFmpeg/libmpv 及其他第三方组件的许可证、版权声明、构建配置和对应源代码获取方式，并依据实际构建选项履行 LGPL/GPL 要求。

### 8.5 Qt/PySide6

PyInstaller 会收集 PySide6 和 Qt 插件，但必须验证包含：

- `platforms/libqxcb.so`
- xcb 相关插件
- imageformats
- iconengines
- TLS/网络依赖
- 必要字体回退

原生 Wayland插件可随包保留，但首版启动器默认使用 xcb。

## 9. 浏览器 Cookie 约束

Linux 浏览器 Cookie 支持比 Windows 更复杂。

### 9.1 Profile 路径

需检测：

- Chrome：`~/.config/google-chrome`
- Chromium：`~/.config/chromium`
- Brave：`~/.config/BraveSoftware/Brave-Browser`
- Firefox：`~/.mozilla/firefox`
- Snap 浏览器目录
- Flatpak 浏览器目录

### 9.2 加密与密钥环

Chromium 系浏览器可能使用：

- GNOME Keyring
- Secret Service
- KWallet

约束：

- 桌面会话必须已解锁密钥环。
- AppImage 对 Secret Service 的访问依赖 DBus 会话。
- Flatpak/Snap 浏览器的 Cookie 文件可能受沙箱限制。
- 不能承诺所有浏览器和安装来源都可自动提取。

首版建议：

1. 优先调用 yt-dlp 自身的 `--cookies-from-browser`。
2. UI 仅展示实际检测到的 Profile。
3. 保留手动粘贴 Cookie 和 Netscape 文件作为稳定兜底。
4. Firefox 原生读取逻辑扩展到 Linux路径。

## 10. DLNA 与防火墙要求

DLNA 逻辑本身可跨平台，但 Linux 防火墙可能默认阻止发现或本地媒体服务。

需要允许：

- UDP 1900：SSDP 组播
- 本地 TCP 媒体服务端口，默认 8899
- 到电视/盒子的 HTTP/SOAP 出站连接

Ubuntu/UFW 示例：

```bash
sudo ufw allow 1900/udp
sudo ufw allow 8899/tcp
```

Fedora/firewalld 示例：

```bash
sudo firewall-cmd --permanent --add-port=1900/udp
sudo firewall-cmd --permanent --add-port=8899/tcp
sudo firewall-cmd --reload
```

应用不应自动修改防火墙，只能检测、提示并提供文档。

## 11. 自动升级策略

当前自动升级器依赖 PowerShell、Windows EXE 和 Robocopy，不能在 Linux 复用。

### 11.1 Linux 首版

建议：

- 保留 GitHub Release 版本检查。
- 根据平台选择 Linux 资产。
- 提供“打开 Release 页面”或“下载到更新目录”。
- 不自动安装 DEB/RPM。
- 不调用 `sudo`、`pkexec` 或系统包管理器。

### 11.2 AppImage 后续升级

可选方案：

- AppImageUpdate/zsync
- 下载新 AppImage 到同目录
- 校验 SHA256
- 当前进程退出后原子替换

约束：AppImage 文件所在目录必须可写。

### 11.3 DEB/RPM

建议交由系统包管理器处理：

- DEB：提示使用 `apt`/软件中心安装新包。
- RPM：提示使用 `dnf`/软件中心安装新包。

不建议应用自行提权。

## 12. 发布包方案

### 12.1 第一优先：AppImage

用途：Ubuntu、Fedora及其他 glibc 桌面发行版的统一便携包。

已批准的主要产物：

```text
Tube_Ultimate_Player_v<version>_x86_64_with_deno_ffmpeg.AppImage
Tube_Ultimate_Player_v<version>_x86_64.AppImage  # 次要/可选标准版
```

两个 AppImage 形态都必须捆绑 libmpv；增强版还必须捆绑 Deno、yt-dlp、FFmpeg/FFprobe，并作为默认推荐下载项。

优点：

- 无需安装。
- 跨发行版分发方便。
- 与现有 Windows 便携版产品形态接近。

风险：

- glibc 基线。
- FUSE 兼容。
- Qt 插件和 libmpv 依赖收集。
- 系统 GPU 驱动不能完全捆绑。

构建应在最老的支持基线系统中进行，建议 Ubuntu 22.04 Runner/容器。

### 12.2 第二优先：DEB

用途：Ubuntu、Debian、Linux Mint。

建议：

- 安装到 `/opt/Tube_Ultimate_Player` 或遵循 FHS 的 `/usr/lib` 结构。
- 提供 `/usr/bin/tube-ultimate-player` 启动器。
- 安装 `.desktop` 文件和多尺寸图标。
- 通过包依赖声明 libmpv、XWayland/xcb 组件。
- 用户数据仍写入 XDG 目录。

### 12.3 第三优先：RPM

用途：Fedora、RHEL 系衍生桌面发行版。

RPM 应在 Fedora 容器或 Runner 内构建，不建议从 Ubuntu 直接生成未经验证的 RPM。

### 12.4 暂不建议：Flatpak/Snap

原因：

- 浏览器 Cookie 文件和密钥环访问更复杂。
- DLNA 组播和本地 HTTP 服务需要额外权限。
- 外部 yt-dlp、FFmpeg、Deno 调用需要沙箱声明。
- mpv、GPU、Wayland/X11 权限组合增加维护成本。

可以作为后续独立项目评估。

## 13. 桌面集成要求

Linux 包应包含：

- `tube-ultimate-player.desktop`
- 16/24/32/48/64/128/256/512 图标
- 应用分类：`AudioVideo;Video;Network;`
- `StartupWMClass`
- 可选 URL Scheme
- 可选视频文件 MIME 类型关联

示例启动参数首版应包含 XWayland 策略：

```text
Exec=env QT_QPA_PLATFORM=xcb /opt/Tube_Ultimate_Player/Tube_Ultimate_Player
```

最终实现应使用启动脚本动态判断，而不是永久禁止高级用户使用其他 Qt 平台插件。

## 14. CI/CD 方案

建议新增：

```text
.github/workflows/test-linux.yml
.github/workflows/release-linux-appimage.yml
.github/workflows/release-linux-deb.yml
.github/workflows/release-linux-rpm.yml
```

### 14.1 单元测试

- `ubuntu-latest`
- `QT_QPA_PLATFORM=offscreen`
- 安装 libmpv 和 FFmpeg
- 运行完整 unittest

### 14.2 X11 集成测试

- 使用 Xvfb
- `QT_QPA_PLATFORM=xcb`
- 创建真实播放器窗口
- 验证 libmpv 初始化和本地短视频播放

### 14.3 打包测试

- AppImage：Ubuntu 22.04 基线
- DEB：Ubuntu 22.04/24.04 安装与卸载
- RPM：Fedora最新两个稳定版本容器
- 校验产物 SHA256
- 校验桌面文件与图标
- 解包检查 Qt 平台插件和内置工具

### 14.4 手动图形测试

自动化无法完全覆盖：

- GNOME Wayland + XWayland
- KDE Wayland + XWayland
- 原生 X11
- Intel/AMD/NVIDIA 驱动
- 多屏与 HiDPI
- PipeWire/PulseAudio
- 系统密钥环和浏览器 Cookie

## 15. 测试矩阵与验收标准

### 15.1 最低测试矩阵

| 系统 | 会话 | 包 | 必测 |
| --- | --- | --- | --- |
| Ubuntu 22.04 | X11 | AppImage/DEB | 启动、播放、下载、字幕、FFmpeg |
| Ubuntu 24.04 | Wayland + XWayland | AppImage/DEB | 嵌入播放、全屏、HiDPI |
| Fedora 最新稳定版 | Wayland + XWayland | AppImage | 启动、播放、下载、DLNA |
| Fedora 前一稳定版 | X11或XWayland | AppImage/RPM阶段 | 基础回归 |

### 15.2 功能验收

- 应用以普通用户启动，不要求 root。
- 首页和搜索可加载 YouTube/Bilibili 内容。
- 在线视频与本地视频正常嵌入播放。
- 暂停、Seek、音量、字幕、全屏和播放列表正常。
- 下载任务可启动、暂停、继续并使用 FFmpeg 合并。
- Deno/Node 能被 yt-dlp 正确调用。
- 手动 Cookie 可用；浏览器 Cookie 有明确支持边界。
- SQLite 数据在 XDG 目录中持久化。
- 日志、缓存、配置和下载目录权限正确。
- DLNA 在允许防火墙端口后可发现和投屏。
- AppImage/DEB/RPM 卸载不会删除用户数据。

### 15.3 稳定性验收

- 连续播放 4 小时无明显内存持续增长。
- 连续切换 50 个视频不崩溃。
- 多次进入/退出全屏不丢失嵌入窗口。
- Wayland 会话中通过 XWayland运行时无黑屏和窗口漂移。
- 网络中断后 UI 不阻塞。

## 16. 风险清单

| 风险 | 概率 | 影响 | 缓解措施 |
| --- | --- | --- | --- |
| 原生 Wayland 下 `wid` 不可靠 | 高 | 高 | 首版强制 XWayland；后续 Render API |
| libmpv SONAME 跨发行版差异 | 高 | 中 | `find_library` + 多候选 + CI矩阵 |
| AppImage Qt/xcb 插件缺失 | 中 | 高 | linuxdeploy 检查、解包测试、Xvfb smoke test |
| 捆绑 libmpv/FFmpeg 许可证复杂 | 中 | 高 | 发布前许可证审核、附带文本、固定来源 |
| Chromium Cookie 密钥环无法访问 | 高 | 中 | yt-dlp 原生提取、手动 Cookie 兜底 |
| Fedora 编解码器受限 | 中 | 中 | 增强包或 RPM Fusion 文档 |
| NVIDIA/Wayland 驱动差异 | 中 | 高 | XWayland、`hwdec=auto-safe`、软件解码兜底 |
| DLNA 被 firewalld/ufw 阻止 | 高 | 中 | 检测提示和文档，不自动改防火墙 |
| PyInstaller glibc 基线过新 | 中 | 高 | 在 Ubuntu 22.04 构建 AppImage |
| Linux 自动升级破坏包管理 | 中 | 高 | 首版不自动安装 DEB/RPM |

## 17. 推荐实施阶段

### 阶段 0：技术验证 Spike

预计：1–3 人日。

目标：

- Ubuntu 24.04 X11/XWayland 启动 PySide6。
- 正确加载系统 libmpv。
- 使用现有 `wid` 成功嵌入本地视频。
- 验证暂停、Seek、全屏。
- 验证 PyInstaller one-dir 原型。

若此阶段无法稳定嵌入播放，则停止后续打包，转入 Render API 方案重新评估。

### 阶段 1：Linux 核心适配

预计：5–8 人日。

- 平台能力抽象。
- XDG 路径。
- libmpv 查找。
- Linux 二进制名称。
- Cookie Profile 路径。
- 禁用 Windows 安装器和升级入口。
- Linux 错误提示。
- 单元测试。

### 阶段 2：AppImage 与 DEB

预计：4–7 人日。

- Linux PyInstaller 脚本。
- AppDir/AppImage 构建。
- `.desktop`、图标、AppRun。
- DEB 元数据和依赖。
- 标准/增强产物。
- SHA256 和第三方声明。

### 阶段 3：Fedora 与 RPM

预计：3–5 人日。

- Fedora CI。
- RPM spec。
- firewalld、PipeWire、XWayland验证。
- Fedora 安装/卸载测试。

### 阶段 4：原生 Wayland（可选）

预计：额外 8–15 人日，存在较大不确定性。

- libmpv Render API 重构。
- OpenGL/Vulkan 表面。
- Qt Wayland 生命周期。
- 多显卡和 HiDPI。

不建议与首版 Linux 发布同时进行。

## 18. 推荐首版交付物

已批准的首版目标：

1. `Tube_Ultimate_Player_v<version>_x86_64_with_deno_ffmpeg.AppImage`，作为主要推荐产物并捆绑 libmpv
2. `Tube_Ultimate_Player_v<version>_x86_64.AppImage`，作为次要或可选标准产物，同样捆绑 libmpv
3. `tube-ultimate-player_<version>_amd64.deb`
4. Ubuntu 22.04/24.04 使用文档
5. Fedora AppImage 使用文档
6. X11/XWayland 限制说明
7. Linux 第三方依赖与许可证说明
8. Linux 自动化测试和发布工作流

RPM 建议作为紧随其后的第二批交付，而不是阻塞首个 Linux 版本。

## 19. 已审核确认的决策

维护者于 2026-07-19 确认：

1. 接受首版必须运行于 X11/XWayland，不承诺原生 Wayland。
2. 接受首期仅支持 `x86_64`。
3. 首发采用 AppImage + DEB；版本稳定后再提供 RPM。
4. AppImage 必须捆绑 libmpv，减少用户自行安装依赖的负担。
5. 优先提供自带 Deno、FFmpeg/FFprobe 的增强版；标准包可以作为次要产物或后续补充。
6. 接受 Linux 首版不做自动安装升级。
7. 再发行包必须附带 FFmpeg、libmpv 及相关依赖的捆绑许可说明，并严格按照实际采用组件及构建选项履行开源许可证要求。
8. Fedora 首发不要求 RPM，先使用 AppImage，稳定后再提供 RPM。
9. 允许使用 root 权限安装系统包；不禁止 root 运行，但 root 运行仅为尽力支持，不作为首版正式兼容环境。

## 20. 最终建议

维护者已给出“有条件批准”，实施边界为：

- 批准阶段 0 技术验证。
- 首版目标锁定 x86_64、X11/XWayland。
- 优先 AppImage 和 Ubuntu DEB。
- Fedora 首版通过 AppImage 支持，RPM进入下一阶段。
- AppImage 必须捆绑 libmpv，首版优先交付自带 Deno/FFmpeg 的增强版。
- 不将原生 Wayland 和自动系统升级纳入首版。
- 支持使用 root 安装；正式运行和测试以普通桌面用户为基准。
- 在技术验证确认 libmpv 嵌入稳定后，再进入完整编码。

本文件作为已审核的评估和实施备案。当前决策已经确认，但 Linux 适配代码、构建脚本和发布工作流仍在维护者明确下达编码启动指令后创建。
