# Tube_Ultimate_Player

<p align="center">
  <img src="docs/assets/readme-banner.svg" alt="Tube_Ultimate_Player banner" width="100%" />
</p>

<p align="center">
  <strong>一个基于 PySide6 + yt-dlp + libmpv 的 Windows 桌面播放器与下载工具</strong>
</p>

<p align="center">
  面向 YouTube 视频的搜索、播放、收藏、历史记录与下载管理场景。
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-2563eb?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-1d4ed8?style=flat-square">
  <img alt="UI" src="https://img.shields.io/badge/UI-PySide6-0f766e?style=flat-square">
  <img alt="Resolver" src="https://img.shields.io/badge/Resolver-yt--dlp-7c3aed?style=flat-square">
  <img alt="Player" src="https://img.shields.io/badge/Player-libmpv-0891b2?style=flat-square">
</p>

## 亮点

- 支持 YouTube 首页推荐浏览、关键词搜索、URL 直接播放三条主路径
- 使用 `libmpv` 负责播放，支持暂停、停止、倍速、音量、清晰度、字幕、全屏
- 内置下载队列，支持并发下载、暂停、继续、删除、完成后本地回看
- 支持收藏、历史记录、Cookie、代理、FFmpeg、下载目录等设置
- 运行时数据与源码仓库分离，兼容未来安装到 `C:\Program Files\...` 的场景

## 界面与流程

<p align="center">
  <img src="docs/assets/readme-workflow.svg" alt="Tube_Ultimate_Player workflow" width="100%" />
</p>

### 典型使用流程

1. 在首页推荐里翻页浏览，或者直接输入关键词搜索。
2. 双击卡片或使用 URL 弹窗解析并播放视频。
3. 在播放中切换清晰度、字幕、倍速，或者一键收藏当前视频。
4. 将视频加入下载队列，查看进度、暂停、继续或删除任务。
5. 下载完成后，可直接在下载列表中调用 `libmpv` 播放本地文件。

## 功能概览

| 模块 | 能力 |
| --- | --- |
| 首页 | YouTube 推荐列表、分页展示、卡片选择、收藏入口 |
| 搜索 | 关键词搜索、分页、等待提示、卡片播放 |
| 播放器 | `libmpv` 播放、暂停、停止、全屏、自动隐藏控制器 |
| 下载 | 下载队列、并发控制、暂停、继续、删除、完成回放 |
| 数据 | 收藏、历史、下载任务持久化 |
| 设置 | 代理、Cookie、浏览器 Cookie 读取、FFmpeg、下载目录 |

## 快速开始

### 运行环境

- Windows
- Python 3.10+
- `3rdpart/` 中提供运行所需的第三方二进制文件

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动

```bash
python main.py
```

## 项目结构

```text
Tube_player/
├─ 3rdpart/                  # 第三方二进制依赖
├─ config/                   # 默认配置模板
├─ database/                 # SQLite 持久化
├─ docs/                     # 设计文档与 README 资源
│  └─ assets/
├─ download/                 # 下载队列与 worker
├─ player/                   # libmpv 封装
├─ resolver/                 # yt-dlp 解析与搜索
├─ resources/                # QSS 等静态资源
├─ services/                 # 配置、日志、Cookie 等服务
├─ ui/                       # PySide6 页面与控件
├─ workers/                  # 后台线程任务
├─ .github/workflows/        # 发布工作流
├─ app_version.txt           # 发布版本号
├─ build_installer.py        # 安装包构建脚本
├─ build_portable.py         # 便携版构建脚本
└─ main.py
```

## 运行时目录

为了兼容未来安装到 `C:\Program Files\...` 的场景，所有可写数据统一放到：

```text
%LocalAppData%\Tube_Ultimate_Player
```

如果当前运行环境无法写入该目录，程序会自动回退到当前用户可写目录；在受限沙箱或便携调试环境里，通常会回退到项目下的 `runtime/`。

### 运行时写入内容

- `config/user_config.json`
- `cookie.txt`
- `data/tube_ultimate_player.sqlite3`
- `data/download_tasks.json`
- `downloads/`
- `logs/app.log`
- `logs/yt-dlp.log`
- `cache/`

### 说明

- 仓库中的 `config/default_config.json` 仅作为默认配置模板，运行时不会写回仓库。
- 日志文件在每次启动时会清空，只保留本次运行日志。

## 构建与发布

### 版本号

项目发布版本从根目录下的 `app_version.txt` 读取。

### GitHub Actions

仓库内已提供两套发布工作流：

- `release-portable.yml`：构建便携版 zip
- `release-installer.yml`：构建 Windows 安装包

两套工作流都会：

- 读取 `app_version.txt`
- 从 `yt-dlp` 官方发布地址获取最新 `yt-dlp.exe`
- 构建产物并上传到 GitHub Actions Artifact
- 若由 GitHub Release 触发，则自动上传到 Release 附件

## 第三方组件

本项目依赖或调用以下第三方组件：

- [PySide6](https://doc.qt.io/qtforpython/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [mpv / libmpv](https://mpv.io/)

其中：

- `yt-dlp.exe` 为独立第三方可执行文件，本项目仅调用其公开命令行能力。
- `libmpv-2.dll` 为独立第三方动态库，本项目通过 `ctypes` 调用其公开接口。

更多说明见：

- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## 合规性声明

1. 本项目是通用桌面客户端，不提供任何受版权保护内容的托管、镜像或转售服务。
2. 用户应仅在拥有合法权利、授权或当地法律允许的情况下使用本工具访问、播放或下载内容。
3. 用户应自行遵守 YouTube 及相关内容平台的服务条款、版权政策、地区限制、年龄限制和其他适用规则。
4. 本项目不保证对任何第三方平台的持续可用性、兼容性或访问权限。
5. 用户导入的 Cookie、代理、账号状态及下载行为均由用户自行负责；仓库不包含任何真实个人 Cookie、账号数据或私有配置。
6. 本项目对第三方软件和服务的引用仅用于兼容与集成说明，不代表对其拥有权或附带再授权。

## 仓库说明

为了避免个人数据泄露，以下内容不应提交到仓库：

- `cookie.txt`
- `config/user_config.json`
- `logs/`
- `downloads/`
- `cache/`
- `data/`
- `runtime/`
- `__pycache__/`

这些内容已在 `.gitignore` 中忽略。
