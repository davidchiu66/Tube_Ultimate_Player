# Linux 构建、测试与发布说明

## 当前支持边界

- 架构：仅 `x86_64/amd64`
- 官方目标：Ubuntu 22.04、Ubuntu 24.04
- Fedora：首期使用 AppImage，稳定后再增加 RPM
- 显示系统：X11，或 Wayland 桌面会话中的 XWayland
- 原生 Wayland：首版不支持；启动器在 Wayland 会话中默认设置 `QT_QPA_PLATFORM=xcb`
- 发布产物：增强 AppImage 与增强 DEB，均优先自带 Deno、FFmpeg/FFprobe、yt-dlp；AppImage 必须自带 libmpv
- 自动升级：只检测和下载 Linux 资产，不自动提权、安装 DEB 或替换 AppImage

完整技术评估见 [`linux_release_feasibility_and_solution.md`](linux_release_feasibility_and_solution.md)。

## Ubuntu 构建依赖

推荐在 Ubuntu 22.04 x86_64 中构建，以保持较低的 glibc 基线：

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  curl unzip xz-utils binutils patchelf desktop-file-utils libfuse2 \
  ffmpeg libmpv-dev mpv xvfb xauth \
  libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
  libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
  libxkbcommon-x11-0 libgl1 libegl1 \
  libfontconfig1 libdbus-1-3

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
```

## 本地技术验证

单元测试：

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

真实 X11/libmpv 嵌入烟雾测试：

```bash
xvfb-run -a -s "-screen 0 1280x720x24" \
  env QT_QPA_PLATFORM=xcb LIBGL_ALWAYS_SOFTWARE=1 \
  python tests/linux_mpv_smoke.py
```

烟雾测试会用 FFmpeg 生成短视频，创建真实 Qt/X11 窗口，初始化 libmpv 并确认播放位置开始推进。
Linux 默认使用兼容性较好的 mpv `vo=gpu`；Windows 继续使用 `vo=gpu-next`。

## 准备增强运行时

```bash
bash packaging/linux/prepare_runtime.sh
```

脚本会准备：

- `3rdpart/deno`
- `3rdpart/ffmpeg`
- `3rdpart/ffprobe`
- `3rdpart/yt-dlp_linux`
- `3rdpart/libmpv.so.*`
- `3rdpart/licenses/`
- `3rdpart/third-party-manifest.sha256`

构建脚本会拒绝缺少运行时或许可证文件的增强包。每次发布必须保留实际下载文件的 SHA256 清单。
linuxdeploy 完成后，工作流还会生成 `bundled-runtime-manifest.sha256`，记录最终进入包内的 libmpv 动态依赖闭包。

## 构建 AppDir

```bash
python build_linux.py
```

输出目录：

```text
build/linux/AppDir/
```

随后使用 linuxdeploy 收集 libmpv 的用户态动态依赖。不得捆绑 glibc、GPU 驱动或主机 Mesa 驱动。

## 构建 AppImage

项目工作流使用 linuxdeploy 和 appimagetool：

```bash
LIBMPV_PATH="$(find 3rdpart -maxdepth 1 -type f -name 'libmpv.so*' -print -quit)"
linuxdeploy --appdir build/linux/AppDir --library "$LIBMPV_PATH"

VERSION="$(tr -d '\r\n' < app_version.txt)"
ARCH=x86_64 appimagetool build/linux/AppDir \
  "dist/linux/Tube_Ultimate_Player_v${VERSION}_x86_64_with_deno_ffmpeg.AppImage"
```

如果系统没有 FUSE，可对 AppImage 工具使用 `--appimage-extract-and-run`。

## 构建 DEB

必须先让 linuxdeploy 完成 AppDir 的动态依赖收集，再执行：

```bash
python build_linux.py --reuse-appdir --build-deb
```

DEB 安装需要 root 权限：

```bash
sudo apt install ./dist/linux/tube-ultimate-player_*_amd64_with_deno_ffmpeg.deb
```

安装完成后应以当前桌面普通用户运行：

```bash
tube-ultimate-player
```

root 身份运行不会被硬性禁止，但浏览器 Cookie、密钥环、音频和图形会话可能不可用，并且生成文件归 root 所有。

## AppImage 使用

```bash
chmod +x Tube_Ultimate_Player_v*_x86_64_with_deno_ffmpeg.AppImage
./Tube_Ultimate_Player_v*_x86_64_with_deno_ffmpeg.AppImage
```

Wayland 桌面必须具备 XWayland 与 Qt xcb 依赖。应用默认使用 xcb；高级用户可用 `TUBE_PLAYER_QPA_PLATFORM` 覆盖，但原生 Wayland 嵌入不属于首版支持范围。

## 运行时目录

Linux 遵循 XDG Base Directory：

```text
$XDG_CONFIG_HOME/Tube_Ultimate_Player/
$XDG_DATA_HOME/Tube_Ultimate_Player/
$XDG_CACHE_HOME/Tube_Ultimate_Player/
$XDG_STATE_HOME/Tube_Ultimate_Player/logs/
$XDG_VIDEOS_DIR/Tube_Ultimate_Player/
```

未设置对应变量时分别回退到 `~/.config`、`~/.local/share`、`~/.cache`、`~/.local/state` 和 XDG 用户视频目录。

## GitHub Actions

- `test-linux.yml`：Ubuntu 22.04 单元测试及 Xvfb/libmpv 播放烟雾测试
- `release-linux.yml`：准备增强运行时、构建 AppImage/DEB、校验内容并上传构建产物

在 Linux Runner 完成首次成功构建和人工图形验收前，Linux 产物不应并入正式 GitHub Release 发布任务。

## 发布验收

- AppImage 和 DEB 均能在 Ubuntu 22.04/24.04 普通用户会话启动
- GNOME/KDE Wayland 会话通过 XWayland 正常嵌入播放
- `libmpv`、Deno、FFmpeg/FFprobe、yt-dlp 均从包内加载
- 首页、搜索、播放、字幕、下载、Cookie 和 DLNA 基础功能通过
- AppImage/DEB 包含许可证、版权信息、构建说明及 SHA256
- 不调用 `sudo`、`pkexec` 或系统包管理器执行自动升级
