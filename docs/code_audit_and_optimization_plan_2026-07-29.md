# 代码审计与优化方案（第二轮）

- 审计日期：2026-07-29
- 代码基线：分支 `release-lfs-fix`，`app_version.txt = 0.2.20`，工作区含未提交改动（`ui/playlist_overlay.py`、`ui/text_elision.py`）
- 审计范围：性能瓶颈、安全漏洞、功能不全（含内存/资源泄漏、并发正确性）
- 前序文档：`docs/performance_improvement_recommendations.md`（2026-07-10），本文第 6 节逐条标注其落地状态
- 状态：**Critical 项（S1、S2、C1、P1）及升级执行缺陷已落地（详见第 11 节）；全部 10 个 High 项（S3、S4、C2、C3、P2、P3、P4、F1、F2、F3）已编码完成并通过全量测试（213 tests OK，2026-07-30），详见第 12 节。**
- 验证约定：每条问题末尾的 **验证方法** 分「自动化」（可直接复制执行的 `python -m unittest ...`）与「人工」两部分；已修复项给出真实存在的测试模块，未修复项给出可复现的观察/检查步骤。全量回归命令：`python -m unittest discover -s tests -p "test_*.py"`。

## 1. 分级标准

| 级别 | 判定标准 |
| --- | --- |
| Critical | 可导致任意代码执行、数据破坏，或功能确定性挂死；必须优先修 |
| High | 明显可复现的崩溃/卡顿/信息暴露，影响主流程体验 |
| Medium | 特定条件下的性能退化、正确性偏差或防御缺失 |
| Low | 代码质量、可维护性、边界语义、测试覆盖 |

## 2. 问题总览

| ID | 级别 | 类别 | 摘要 | 位置 |
| --- | --- | --- | --- | --- |
| S1 | Critical | 安全 | 升级包下载后无任何完整性/签名校验即被执行 | `workers/update_download_worker.py:59`、`services/update_service.py:267` |
| S2 | Critical | 安全 | FFmpeg 压缩包硬编码 URL、无哈希校验、解压无路径穿越防护 | `services/ffmpeg_install_service.py:12`、`workers/archive_extract_worker.py:30` |
| C1 | Critical | 并发 | 投屏 FFmpeg 的 stderr 管道不排空，写满即挂死 | `dlna/media_server.py:270` |
| P1 | Critical | 性能 | 文本折行算法 O(n²) 次字体度量，首页/播放列表高频命中 | `ui/text_elision.py:28`、`ui/text_elision.py:79` |
| S3 | High | 安全 | 升级用 PowerShell 脚本写入用户可写目录并以 `Bypass` 执行 | `services/update_service.py:279`、`services/update_service.py:318` |
| S4 | High | 安全 | DLNA 媒体中继无客户端来源限制，局域网内可拉流与读本地文件 | `dlna/media_server.py:144` |
| C2 | High | 内存 | 缩略图回调写入已销毁 QLabel，抛异常并中断同批其余回调 | `ui/thumbnail_cache.py:82`、`ui/home_page.py:324` |
| C3 | High | 并发 | 退出时不等待线程池 worker，直接关闭 DLNA/mpv | `ui/main_window.py:1763` |
| P2 | High | 性能 | 首页一次性同步构建 ~56 张卡片并立即发起全部缩略图请求 | `ui/home_page.py:247`、`ui/home_page.py:95` |
| P3 | High | 性能 | 播放列表面板逐条建 widget、每次选中全量重算样式 | `ui/playlist_overlay.py:266`、`ui/playlist_overlay.py:477` |
| P4 | High | 性能 | 启动期串行构造全部服务与 8 个页面，全在 UI 线程 | `ui/main_window.py:71` |
| F1 | High | 功能 | Bilibili 请求层完全绕过代理设置 | `resolver/site_resolver.py:880` |
| F2 | High | 功能 | 系统代理静默覆盖用户显式配置的代理 | `services/config_service.py:69` |
| F3 | High | 功能 | 无可用清晰度时 `StopIteration` 逃逸，解析成功却崩在 UI 层 | `ui/main_window.py:746` |
| S5 | Medium | 安全 | 解析局域网不可信 XML 无大小上限、未禁用实体（billion laughs） | `dlna/discovery.py:277`、`dlna/controller.py` |
| S6 | Medium | 安全 | `_ensure_column` 用 f-string 拼接 DDL 标识符 | `database/sqlite_manager.py:121` |
| S7 | Medium | 安全 | Cookie 明文落盘且不收紧文件权限 | `services/cookie_service.py`、`services/config_service.py:80` |
| C4 | Medium | 并发 | `SiteResolver._page_cache` 无锁，多 worker 并发读写 | `resolver/site_resolver.py:45`、`resolver/site_resolver.py:203` |
| C5 | Medium | 并发 | `DlnaController._opener` 单实例被多线程共用 | `dlna/controller.py` |
| C6 | Medium | 并发 | 保存设置后重建服务，旧 resolver 的在飞 worker 无失效标记 | `ui/main_window.py:1335` |
| P5 | Medium | 性能 | 每次启动都跑两条全表 `UPDATE` 迁移，无版本守卫 | `database/sqlite_manager.py:110` |
| P6 | Medium | 性能 | SQLite 连接无 `WAL`/`synchronous`/`busy_timeout` 等 PRAGMA | `database/sqlite_manager.py:104` |
| P7 | Medium | 性能 | 每次操作新建连接，`SELECT`+`UPDATE` 两段式代替原生 upsert | `database/history_repository.py`、`database/favorite_repository.py:72` |
| P8 | Medium | 性能 | 每次 WBI 签名都额外请求一次 `nav` 接口 | `resolver/site_resolver.py:917` |
| P9 | Medium | 性能 | 浏览器 Cookie 探测无缓存，逐个浏览器重复读取 | `resolver/site_resolver.py:892`、`resolver/youtube_resolver.py:353` |
| P10 | Medium | 性能 | 缓存键指纹每次 `stat` + 读系统代理（Windows 注册表）+ SHA1 | `resolver/site_resolver.py:182` |
| P11 | Medium | 性能 | 逐字节 `stdout.read(1)` 读取 yt-dlp 输出 | `download/download_worker.py:268` |
| P12 | Medium | 性能 | 每次构建下载命令都 `shutil.which` 探测 yt-dlp/FFmpeg | `download/command_builder.py` |
| P13 | Medium | 性能 | mpv 每 500ms 在 UI 线程轮询 5 个属性，未用事件机制 | `player/mpv_player.py` |
| P14 | Medium | 性能 | 投屏中每 1.5s 发一次 SOAP 位置查询 | `ui/main_window.py:98` |
| P15 | Medium | 性能 | SSDP 每网卡 × 4 个 ST × 2 次广播，设备描述用 `iter()` 全后代搜索 | `dlna/discovery.py` |
| F4 | Medium | 功能 | yt-dlp `subprocess.run(timeout=120)` 未捕获 `TimeoutExpired` | `resolver/youtube_resolver.py:317` |
| F5 | Medium | 功能 | 文件名反解 video_id 正则易误匹配，且一律回落 YouTube URL | `download/download_manager.py:29`、`download/download_manager.py:411` |
| F6 | Medium | 功能 | 站点判定逻辑重复实现，非 B 站一律判为 youtube | `database/playlist_repository.py:203` |
| C7 | Low | 并发 | `MpvPlayer.shutdown()` 非幂等、无锁、无析构兜底 | `player/mpv_player.py:152` |
| P16 | Low | 性能 | `favorite_ids()` 全表扫描，被首页刷新/收藏同步频繁调用 | `database/favorite_repository.py:49` |
| P17 | Low | 性能 | 每次装载播放列表都 `findChildren` 全树重装事件过滤器 | `ui/player_page.py:471`、`ui/player_page.py:683` |
| P18 | Low | 功能 | 播放页封面不走 `ThumbnailCache`，且旧请求不作废（可能串图） | `ui/player_page.py:497` |
| P19 | Low | 性能 | `app_paths` 在 import 期做 mkdir + 写探测文件 | `app_paths.py:118` |
| P20 | Low | 性能 | `rglob("ffmpeg.exe")` 全量递归扫描解压目录 | `services/ffmpeg_install_service.py` |
| P21 | Low | 性能 | 升级下载每 128KB 发一次进度信号，无节流 | `workers/update_download_worker.py` |
| F7 | Low | 功能 | Linux 无自动升级通道，仅 Windows 支持 | `services/update_service.py:259` |
| F8 | Low | 功能 | 播放页与文本模块重复实现 `format_seconds` | `ui/player_page.py:843`、`ui/text_elision.py:93` |
| F9 | Low | 测试 | 文本折行、DLNA 中继、升级校验均无单测覆盖 | `tests/` |

## 3. 安全漏洞详解

### S1 [Critical] 升级包无完整性校验即执行

**位置**：`workers/update_download_worker.py:59`（落盘）→ `services/update_service.py:267-301`（`launch_installer`）/ `303-349`（`launch_portable_update`）

**说明**：`ReleaseAsset` 已带 `size` 字段（`services/update_service.py:105`），但下载完成后既不比对长度、也不比对哈希、更不校验 Windows 数字签名，直接 `os.replace(temp_path, self.target_path)` 后交由 PowerShell 启动 `.exe`，或用 robocopy 覆盖整个安装目录。任何能干扰这条链路的一方（中间人、被劫持的镜像、本地能写 `UPDATE_DIR` 的进程）都可获得当前用户权限下的任意代码执行；便携版路径的影响更大，因为覆盖的是整个程序目录。

**修复方案**：下载后强制三重校验，任一不过就删除临时文件并报错。

```python
# workers/update_download_worker.py
EXPECTED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}

def _verify_download(self, temp_path: Path) -> None:
    if urlparse(self.url).scheme != "https" or urlparse(self.url).hostname not in EXPECTED_HOSTS:
        raise RuntimeError("升级包下载地址不在受信任的发布域名内")
    actual_size = temp_path.stat().st_size
    if self.expected_size and actual_size != self.expected_size:
        raise RuntimeError(f"升级包大小校验失败：期望 {self.expected_size} 字节，实际 {actual_size} 字节")
    digest = hashlib.sha256()
    with temp_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if self.expected_sha256 and actual.lower() != self.expected_sha256.lower():
        raise RuntimeError("升级包 SHA256 校验失败，已终止升级")
    logger.info("update package verified size=%s sha256=%s", actual_size, actual)
```

预期哈希来源按优先级：GitHub 资产的 `digest` 字段 → 同一 Release 中的 `SHA256SUMS.txt` 资产 → Release 正文中的哈希清单。三者都缺失时，Windows 下退化为 Authenticode 签名校验（`services/update_service.py` 内新增，签名主体不匹配即拒绝执行）：

```powershell
$sig = Get-AuthenticodeSignature -LiteralPath $InstallerPath
if ($sig.Status -ne 'Valid') { throw "安装包签名无效：$($sig.StatusMessage)" }
```

**验证方法**（已修复，见 11.1）

- 自动化：`python -m unittest tests.test_update_verify`（23 项，应为 `OK`）。覆盖 `ensure_trusted_download_url` 的 HTTP/非白名单主机拒绝、`normalize_sha256` / `extract_sha256_for` 三级哈希解析、`verify_downloaded_file` 的大小不符与哈希不符（并断言临时文件已被删除）、以及 `UpdateDownloadWorker` 端到端放行/拒绝。
- 人工：把 `%LocalAppData%\Tube_Ultimate_Player\updates\` 下已下载的安装包用十六进制编辑器改掉一个字节（或直接 `echo x >> 安装包.exe`），再触发一次升级 —— 应看到中文的哈希校验失败提示且安装包不被执行；日志 `logs/` 内应有 `update package verified` 或校验失败记录。

### S2 [Critical] FFmpeg 安装包无校验、解压无路径穿越防护

**位置**：`services/ffmpeg_install_service.py:12`（`FFMPEG_DOWNLOAD_URL` 硬编码第三方地址）、`workers/archive_extract_worker.py:30`（`archive.extractall(path=self.extract_dir)`）

**说明**：安装包来自第三方个人构建站点，下载后不校验哈希；`py7zr.extractall` 不校验条目路径，恶意包内的 `..\..` 条目可写出解压目录之外。解压结果随后被写入配置，作为 `ffmpeg` 路径交给 yt-dlp 与 DLNA 中继执行 —— 即从"下载一个压缩包"直接变成"执行任意二进制"。

**修复方案**：

```python
# services/ffmpeg_install_service.py
FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.0.1-essentials_build.7z"
FFMPEG_SHA256 = "<pin 到具体版本的哈希>"
FFMPEG_ALLOWED_HOSTS = {"www.gyan.dev", "github.com", "objects.githubusercontent.com"}

# workers/archive_extract_worker.py
def _safe_targets(archive: py7zr.SevenZipFile, root: Path) -> list[str]:
    targets: list[str] = []
    for name in archive.getnames():
        resolved = (root / name).resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise RuntimeError(f"压缩包包含非法路径条目：{name}")
        if resolved.suffix.lower() in {".exe", ".dll", ".txt", ""}:
            targets.append(name)
    return targets
```

只提取白名单条目（`bin/ffmpeg.exe`、`bin/ffprobe.exe`、许可证文本），提取后再次确认 `ffmpeg.exe` 存在且可执行，失败则清空目录且不写配置。

**验证方法**（已修复，见 11.2）

- 自动化：`python -m unittest tests.test_archive_safety`（11 项，应为 `OK`）。`validate_archive_entry` 覆盖 7 种拒绝场景（空名、绝对路径、盘符、UNC、`..` 穿越、解析后越界、正常放行）；worker 集成用例断言含穿越条目的包**整包拒绝**（不进行任何解压，解压目录内外均无残留文件），以及关键文件缺失时报错。
- 人工：清空 `%LocalAppData%\Tube_Ultimate_Player\` 下的 ffmpeg 目录与配置里的 ffmpeg 路径，从设置页走一次真实的 FFmpeg 安装；成功后确认解压目录里只有白名单文件（`ffmpeg.exe`/`ffprobe.exe`/许可证），且配置中的路径可用（下载一个需要合并的视频）。把 `services/ffmpeg_install_service.py` 里的 `FFMPEG_ARCHIVE_SHA256` 临时改错一位再安装一次，应被拒绝且**不写入配置**。

### S3 [High] 升级脚本落盘于用户可写目录并以 `Bypass` 执行

**位置**：`services/update_service.py:279-295`、`services/update_service.py:318-339`

**说明**：`installer_launcher.ps1` / `portable_updater.ps1` 每次写入 `UPDATE_DIR`（普通用户可写），随后以 `-ExecutionPolicy Bypass -File` 执行。写入与执行之间存在 TOCTOU 窗口，本机任意低权限进程替换脚本内容即可在升级时提权到当前用户上下文执行；`portable_updater.ps1` 还持有对整个安装目录的 robocopy 覆盖能力。

**修复方案**：不再落盘脚本，改为 `-EncodedCommand`（Base64 UTF-16LE）直接传入内联脚本；如必须落盘，则写入后立刻重新读回比对内容哈希，并把参数全部通过 `-EncodedCommand` 内的变量传递而非命令行拼接。同时保持现有列表式 `subprocess.Popen`（已避免 shell 注入）。

**验证方法**（已修复，见 12.1）

- 自动化：`python -m unittest tests.test_update_launch`（12 项，应为 `OK`）。关键用例：`launch_installer` / `launch_portable_update` 执行后断言 `UPDATE_DIR` 内**不存在任何 `.ps1` 文件**；断言 `Popen` 收到的参数里含 `-EncodedCommand` 且不含 `-File`；断言 EncodedCommand 能被 Base64 + UTF-16LE 解码回原脚本（证明脚本体只存在于内存与命令行，不经过文件系统）；`build_launcher_command` 的参数转义用例断言含单引号的路径被转成 `''` 字面量、含换行的参数直接 `RuntimeError`；并保留 `DETACHED_PROCESS` 回归守卫（断言 `creationflags` 用的是 `CREATE_NO_WINDOW`）。
- 人工：升级前后各看一次 `%LocalAppData%\Tube_Ultimate_Player\updates\` 目录 —— 升级过程中不应出现 `installer_launcher.ps1` / `portable_updater.ps1`。安装版与便携版各跑一次真实升级流程，确认升级日志（脚本内 `Write-UpgradeLog` 的输出）正常写出且版本号确实更新。

### S4 [High] DLNA 媒体中继无来源限制

**位置**：`dlna/media_server.py:113-129`（监听）、`dlna/media_server.py:144-169`（`_serve`）、`dlna/media_server.py:225-257`（`_serve_file`）

**说明**：token 是 `secrets.token_urlsafe(18)`，强度足够，但它通过 SOAP 明文在局域网内传给渲染设备，且服务端不校验来访 IP、token 不过期。同一局域网内任意主机嗅探/抓到 URL 即可持续拉取当前媒体流；本地文件投屏时 `_serve_file` 会把磁盘文件原样吐出。此外目前 `register_source` 会 `self._sources.clear()`，多设备并行投屏时前一个会话被静默踢掉（顺带记为 F 类小问题）。

**修复方案**：

```python
# dlna/media_server.py
@dataclass(slots=True)
class _RegisteredSource:
    source: DlnaMediaSource
    allowed_host: str
    expires_at: float

def _serve(self, head_only: bool) -> None:
    ...
    entry = owner.source(token)
    client_host = self.client_address[0]
    if entry is None or time.time() > entry.expires_at or client_host != entry.allowed_host:
        logger.warning("DLNA media request rejected client=%s token_present=%s", client_host, entry is not None)
        self.send_error(404)
        return
```

`allowed_host` 取 `register_source(remote_host=...)` 传入的设备 IP；`expires_at` 建议按"停止投屏 + 冗余 30s"续期，`stop_streams()` 时立即失效。

**验证方法**（已修复，见 12.2）

- 自动化：`python -m unittest tests.test_dlna_access_control`（9 项，应为 `OK`）。`_authorize` 单元用例覆盖：token 不存在 → 拒绝、client IP 与投屏目标 IP 不符 → 拒绝、超过有效期（用可注入的 `time.monotonic` 假时钟推进）→ 拒绝、目标 IP + 有效 token + 未过期 → 放行。另有端到端用例：真起一个绑定 `127.0.0.1:0` 的 `ThreadingHTTPServer`，用 `urllib` 请求，断言未授权情形一律返回 **404**（而不是 401/403，避免让攻击者探测 token 是否存在），授权情形返回 200 且能读到内容。
- 人工：投屏一部视频，在电视端正常播放的同时，用同网段另一台机器 `curl -i "http://<本机IP>:<端口>/media/<token>"` —— 应得到 404；把 URL 里的 token 改掉一位同样是 404。在播放器里点「停止投屏」后再用**本机**访问同一 URL，也应变成 404（`stop_streams()` 会清空令牌表）。整个过程中电视端播放不应受影响。令牌有效期为 30 分钟（`_TOKEN_TTL = 1800`），可临时把它改成 `5.0` 秒后重投一次，观察 5 秒后取流被拒且日志出现「DLNA 媒体令牌已过期」。

### S5 [Medium] 局域网 XML 解析未加固

**位置**：`dlna/discovery.py:277`（`parse_device_description` 中 `ET.fromstring`）、`dlna/controller.py`（`_soap_values` 同样直接 `ET.fromstring`，`exc.read()` 无上限）

**说明**：`xml.etree.ElementTree` 对 billion laughs / quadratic blowup 是脆弱的（Python 官方文档 XML 漏洞表已明确列出），而这里解析的是局域网设备返回的内容，同时 `response.read()` 没有字节上限，一个恶意"设备"即可让发现流程吃满内存。

**修复方案**：统一一个 `_read_limited(response, limit=256 * 1024)` 辅助函数；解析前拒绝含 `<!DOCTYPE` / `<!ENTITY` 的载荷（或引入 `defusedxml`，但会新增依赖，建议先用零依赖的前置检查）：

```python
def _parse_xml_safely(payload: str) -> ET.Element:
    head = payload[:2048].lower()
    if "<!doctype" in head or "<!entity" in head:
        raise ValueError("设备返回的 XML 含不被接受的 DTD 声明")
    return ET.fromstring(payload)
```

**验证方法**（**尚未修复**，下列为复现与验收步骤）

- 复现（人工）：在局域网里用一个能自定义 SSDP 响应的脚本（或 `socat` 手工回一段 `LOCATION:` 指向自己的 HTTP 服务）把设备描述 XML 换成 billion laughs 载荷，再从投屏面板点「搜索设备」—— 当前实现会长时间卡住或内存暴涨。
- 修复后自动化（验收标准）：新增 `tests/test_dlna_discovery_hardening.py`，(1) 传入 billion laughs XML，断言抛出可读中文错误且**耗时 < 1 秒**；(2) 传入 512KB 超长载荷，断言按大小上限提前中止；(3) 同一批发现里混入一个正常设备与一个恶意设备，断言正常设备仍出现在结果列表中（单个设备的解析失败不能拖垮整批）。
- 修复后人工：正常投屏设备的发现与播放行为不得变化 —— 搜索一次，确认设备数量与修复前一致。

### S6 / S7 [Medium] 其余安全加固

- **S6**：`database/sqlite_manager.py:121-128` 的 `_ensure_column` 用 f-string 拼 `ALTER TABLE`。当前调用方全是模块内常量，暴露面为零，但应加标识符白名单校验（`re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)`）以免后续被外部输入触达。
- **S7**：Cookie 明文存放于运行目录且不收紧权限。建议写入后 Linux `chmod 0o600`、Windows 用 `icacls` 仅保留当前用户；同时在设置页提示"Cookie 等同于账号凭据"。浏览器 Cookie 自动探测（`services/config_service.py:246-300`）默认会遍历所有已装浏览器的 Cookie 库，建议改为显式选择后才读取。

**验证方法**（**尚未修复**，下列为复现与验收步骤）

- S6 复现（自动化）：直接对 `_ensure_column` 传入 `"x); DROP TABLE favorites;--"` 之类的列名，当前会把它拼进 `ALTER TABLE` 语句。修复后应断言这类非标识符入参抛出 `ValueError`，而合法列名（如 `duration`）仍能正常建列；再用 `python -m unittest discover -s tests -p "test_*.py"` 确认既有的数据库迁移用例不回归。
- S7 人工（Windows）：登录一次 B 站后执行 `icacls "%LocalAppData%\Tube_Ultimate_Player\cookie_bilibili.txt"` —— 当前会看到 `BUILTIN\Users` 之类的继承条目，修复后应只剩当前用户与 SYSTEM。Linux 用 `stat -c %a ~/.local/share/Tube_Ultimate_Player/cookie_bilibili.txt`，期望 `600`。收紧权限后必须回归一次：重启应用，确认 Cookie 仍能被读取且登录态未丢。
- S7 人工（自动探测）：在设置页触发一次浏览器 Cookie 导入，用进程监视工具（如 Process Monitor）确认修复后**只**读取用户显式选中的那个浏览器的 Cookie 库，而不是遍历全部。

## 4. 并发、内存与资源问题

### C1 [Critical] 投屏 FFmpeg 的 stderr 管道写满导致挂死

**位置**：`dlna/media_server.py:270-289`

**说明**：`_serve_muxed` 以 `stdout=PIPE, stderr=PIPE` 启动 FFmpeg，但循环里只读 `stdout`，`stderr` 只在进程结束后才 `read()`。一旦 FFmpeg 往 stderr 写满管道缓冲（Windows 约 4KB~64KB；网络流重试、时间戳警告很容易达到），FFmpeg 就阻塞在写 stderr，stdout 不再产出，中继随之停住 —— 表现为"投屏播放几分钟后卡死且无报错"。当前 `-loglevel error` 只是降低了触发概率，并未消除。

**修复方案**：把 stderr 重定向到临时文件，或用后台线程持续排空。推荐临时文件（无额外线程、失败时仍可取到日志）：

```python
stderr_file = tempfile.TemporaryFile()
process = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=stderr_file,
    stdin=subprocess.DEVNULL,
    creationflags=creationflags,
)
...
if return_code != 0:
    stderr_file.seek(0)
    detail = stderr_file.read().decode("utf-8", errors="replace")
    logger.error("FFmpeg 投屏封装失败 title=%s detail=%s", source.title, detail[-1000:])
```

**验证方法**（已修复，见 11.3）

- 自动化：`python -m unittest tests.test_dlna_media_server`（1 项，应为 `OK`）。用例把 `subprocess.Popen` 替换成一个"向 stderr 写入 1MB、同时向 stdout 写入可校验内容"的假进程，断言中继仍能把 stdout **完整**读完并原样转发 —— 修复前这个用例会因 stderr 管道写满、子进程阻塞而超时。
- 人工：投屏一部 20 分钟以上的**分离音视频**视频（B 站高码率片源通常就是），从头看到尾，确认不再中途卡死；同时看 `logs/` 里 FFmpeg 的 stderr 是否被持续消费（应能看到 remux 进度行，而不是在某个时间点后彻底静默）。

### C2 [High] 缩略图回调写入已销毁的 QLabel

**位置**：`ui/thumbnail_cache.py:20`（`_in_flight` 持 `list[QLabel]` 强引用）、`ui/thumbnail_cache.py:82-88`、`ui/home_page.py:324-332`（`_clear_cards` 只 `deleteLater()`）

**说明**：翻页/搜索时 `_clear_cards()` 销毁全部卡片，但在飞请求的 `_in_flight` 仍持有这些 `QLabel`。回调里 `label.setPixmap(...)` 作用于已析构的 C++ 对象会抛 `RuntimeError`，而该循环没有 try/except —— 异常一抛，**同一 key 下排在后面的 waiter 全部收不到图**，表现为"翻页回来有几张封面永远是占位文字"。同时 Python 侧包装对象被字典钉住，构成实质性的内存泄漏。

**修复方案**：waiter 用弱引用 + 逐个隔离异常，并在页面清理时主动注销。

```python
# ui/thumbnail_cache.py
self._in_flight: dict[tuple[str, int, int], list[weakref.ReferenceType[QLabel]]] = {}

for ref in waiters:
    label = ref()
    if label is None:
        continue
    try:
        if success:
            label.setPixmap(cached)
            label.setText("")
        else:
            label.setPixmap(QPixmap())
            label.setText(error_text)
    except RuntimeError:
        logger.debug("thumbnail target already destroyed url=%s", key[0])
```

并新增 `ThumbnailCache.cancel_for(label)`，在 `HomePage._clear_cards()` 与 `PlaylistItemWidget` 析构时调用。

**验证方法**（已修复，见 12.3）

- 自动化：`python -m unittest tests.test_thumbnail_loading`（8 项，应为 `OK`）。关键用例：在离屏 `QApplication` 下注册一个 label 后 `deleteLater()` + `processEvents()`，再触发同一 URL 的回调，断言(1) 不抛 `RuntimeError`、(2) **同批**另一个存活 label 仍能正常收到 pixmap（证明单个已析构目标不会拖垮整批等待者）、(3) 回调结束后该 URL 的等待者列表被清空、`pending_count()` 归零（证明不再泄漏 wrapper）。
- 人工：在首页/搜索结果页快速连续翻页 10 次以上（让封面请求追不上页面切换），确认无异常栈、封面最终都能显示；观察内存占用在反复翻页后能回落，而不是单调增长。

### C3 [High] 退出期不等待后台 worker

**位置**：`ui/main_window.py:1763-1774`

**说明**：`closeEvent` 依次 `dlna_media_server.stop()`、`config.save()`、`download_manager.flush()`、`mpv.shutdown()`，但完全不等 `QThreadPool` 里在跑的解析/搜索/DLNA 动作 worker。这些 worker 结束时会 emit 信号回已析构的页面，或在 `mpv.shutdown()` 释放句柄之后继续通过 ctypes 触达 mpv —— 对应用户偶发看到的"关闭窗口时闪退/无响应"。

**修复方案**：加一个全局关闭标志，worker 完成回调先检查，再等线程池收敛：

```python
def closeEvent(self, event) -> None:  # noqa: N802
    try:
        logger.info("main window closing")
        self._shutting_down = True
        self._invalidate_creator_playlist_request()
        self._dlna_position_timer.stop()
        self._dlna_volume_timer.stop()
        self.dlna_media_server.stop()
        self.config.save()
        self.download_manager.flush()
        if not QThreadPool.globalInstance().waitForDone(3000):
            logger.warning("background workers still running at shutdown")
        self.mpv.shutdown()
    finally:
        super().closeEvent(event)
```

配套：所有 `_xxx_loaded` / `_xxx_failed` 槽首行加 `if self._shutting_down: return`。

**验证方法**（已修复，见 12.4）

- 自动化：`python -m unittest tests.test_main_window_hardening`（10 项，应为 `OK`）。其中 `ShutdownGuardTests` 三项覆盖关闭守卫：`_shutting_down` 置位前回调正常执行并返回结果，置位后回调直接返回 `None`（迟到的 worker 信号被丢弃），且 `_skip_after_shutdown` 包装后方法名仍是原名（`functools.wraps` 未丢签名，便于日志定位）。
- 人工：在首页加载中、搜索中、投屏中分别立即关闭窗口，各 3 次，确认进程干净退出（任务管理器里没有残留 `python.exe` / `Tube_Ultimate_Player.exe`）且 `logs/` 里没有 `RuntimeError: Internal C++ object already deleted` 之类的异常栈。

### C4~C7 [Medium/Low] 其余并发问题

- **C4**（`resolver/site_resolver.py:45`、`203-219`）：`_creator_cache` 已有 `_creator_cache_lock`，但 `_page_cache` 的 `get`/`pop`/`move_to_end`/`popitem` 全裸奔。首页与搜索 worker 可并发跑（不同线程），`OrderedDict` 的复合操作非原子，会出现 `KeyError`/缓存条目错乱。修复：加 `threading.Lock`，与 creator 缓存对齐。
- **C5**（`dlna/controller.py`）：`self._opener` 在构造时创建一次，被多个 DLNA 动作 worker 并发使用。`OpenerDirector` 非线程安全（handler 内部有可变状态）。修复：每次请求内联构建 opener（与 `dlna/media_server.py:184` 的做法一致），或用 `threading.local()`。
- **C6**（`ui/main_window.py:1335-1353`）：保存设置后重建 `SiteResolver`/`UpdateService`，但旧 resolver 上的在飞 worker 仍会回调并把过期结果写进页面。修复：引入单调递增的 `self._resolver_generation`，worker 携带创建时的代号，回调时不匹配即丢弃（与既有 `_invalidate_creator_playlist_request` 思路一致）。
- **C7**（`player/mpv_player.py:152-156`）：`shutdown()` 仅靠 `getattr(self, "_handle", None)` 守卫，重复调用或与轮询定时器竞争时可能二次释放。修复：加 `threading.Lock` + 置空句柄的原子化，并在 `__del__` 兜底停掉定时器。

**验证方法**（未修复，以下为修复时的验收标准）

- 复现（人工）：C4——把首页与搜索同时触发（打开首页后立刻搜索，反复十几次），观察日志是否出现 `_page_cache` 相关的 `KeyError` 或返回了另一个请求的分页数据；这类竞态是概率性的，必要时把 `_PAGE_CACHE_MAX` 改小（如 2）来放大冲突。C6——进入某个视频的解析过程中立刻打开设置页保存一次设置，看解析完成后是否仍把结果写进了页面。C7——连续快速切换视频/关闭播放页，观察退出时有无 libmpv 层的崩溃或 `double free`。
- 修复后自动化（验收标准）：新增 `tests/test_concurrency_hardening.py`，(1) C4：起 8 个线程对同一个 `SiteResolver` 的 `_page_cache` 并发读写 200 轮，断言无异常且最终缓存条目数不超过上限；(2) C5：断言 `dlna/controller.py` 每次动作都新建 opener（`assertIsNot` 两次调用的结果），与 F1 的 `test_opener_is_rebuilt_per_call` 同构；(3) C6：递增 `_resolver_generation` 后投递一个「旧代号」的成功回调，断言页面状态未被改写；(4) C7：对同一个 `MpvPlayer` 连调 `shutdown()` 三次，断言只真正释放一次句柄（把 `mpv_terminate_destroy` 打成计数桩）且不抛异常。
- 修复后人工：重跑上面三条复现步骤，均不再出现异常；投屏、切视频、改设置、退出四个动作交叉快速操作 5 分钟，日志无 `RuntimeError`/崩溃。

## 5. 性能瓶颈详解

### P1 [Critical] 文本折行 O(n²) 次字体度量

**位置**：`ui/text_elision.py:28-56`（`_wrap_text`）、`ui/text_elision.py:79-90`（`_text_width`）
**调用点**：`ui/home_page.py:122-124`（每张首页卡片，每次 resize）、`ui/playlist_overlay.py:115-117`（每个播放列表条目，每次 resize）

**说明**：这是本轮最大的性能发现。`_wrap_text` 对标题的每个字符都调一次 `_text_width(candidate)`，而 `_text_width` 内部又对**整个候选串**逐字符 `horizontalAdvance` 一遍（第 84 行的列表推导）。对长度 n 的标题就是 O(n²) 次跨语言边界的 Qt 度量调用：一个 60 字标题约 3600+ 次。首页一页 56 张卡片、每次窗口 resize 全部重排，量级来到十万次以上 —— 直接对应"首页加载/拉伸窗口时明显卡顿"。

**修复方案**：按字符缓存宽度 + 增量累加，把复杂度降到 O(n)。

```python
# ui/text_elision.py
def _char_width_lookup(metrics: QFontMetrics) -> dict[str, int]:
    key = metrics.fontDpi(), metrics.height(), metrics.averageCharWidth()
    cache = _CHAR_WIDTH_CACHE.setdefault(key, {})
    return cache

def _text_width(metrics: QFontMetrics, text: str, cache: dict[str, int] | None = None) -> int:
    if not text:
        return 0
    lookup = cache if cache is not None else _char_width_lookup(metrics)
    fallback = max(1, metrics.averageCharWidth())
    total = 0
    for char in text:
        width = lookup.get(char)
        if width is None:
            width = metrics.horizontalAdvance(char) or fallback
            lookup[char] = width
        total += width
    return total
```

`_wrap_text` 里改为维护 `current_width` 累加变量，只对新增字符查一次缓存（回退时减去被切走的部分），不再重测整串。字距（kerning）带来的误差对"标题折行 + 三点省略"场景可以接受；若要完全保真，另一条路是改用 `QFontMetrics.boundingRect(rect, Qt.TextWordWrap, text)` 或 `QTextLayout` 让折行发生在 C++ 侧，一次调用完成。建议先实现缓存版（改动小、零行为差异风险低），并在单测中固定几组标题的折行结果作为回归基线。

**收益预估**：首页首次布局与 resize 的文本度量开销下降 1~2 个数量级；配合 P2 可让翻页从"明显卡顿"回到"即时"。

**验证方法**（已修复，见 11.4）

- 自动化：`python -m unittest tests.test_text_elision`（10 项，应为 `OK`）。用离屏 `QApplication` 断言 (1) 超长文本行数不超过 `max_lines` 且末行以 `...` 结尾（`test_two_line_limit_with_three_dots`）、(2) 同参数重复调用结果完全一致（`test_result_is_stable_across_repeated_calls`，这是「行为不变原则」的守门用例：缓存不得让同一输入折出两种结果）、(3) 纯空白文本与 `max_lines=0` 都返回空串、(4) 每个不同字符只量一次宽度（`WidthCacheTests`，用计数假 `QFontMetrics` 断言 `horizontalAdvance` 调用次数等于去重字符数）、(5) 200 次长标题折行耗时 < 2s（`test_long_title_batch_stays_fast`，缓存失效时这条会先红）。
- 人工：打开一个标题很长的视频（60 字以上，中英混排最好），对比首页卡片、播放页标题、播放列表条目三处的折行与省略位置和优化前截图一致；再把窗口从最窄拖到最宽，确认折行随宽度平滑变化、没有出现空行或截断半个字。

### P2 [High] 首页卡片同步全量构建

**位置**：`ui/home_page.py:247-281`（`set_videos`）、`ui/home_page.py:86-97`（每卡 `layout.activate()` + `_apply_title()` + `_load_thumbnail()`）

**说明**：一页最多 56 张 `HomeVideoCard`，全部在一次 UI 线程调用里构建：每张创建 3 个按钮 + 3 个标签 + 2 层布局，主动 `layout.activate()` 强制同步布局计算，再命中 P1 的折行路径，最后立刻发起缩略图请求 —— 56 个并发 HTTP 请求同时打出，`QNetworkAccessManager` 队列拥塞，前几张图反而更慢。

**修复方案**：三步，按收益排序：

1. **缩略图改为可见性驱动**：把 `PlaylistOverlay._load_visible_thumbnails()` 的懒加载模式移植到首页（滚动/resize 后只加载与视口相交的卡片），`HomeVideoCard._load_thumbnail` 改为 `ensure_thumbnail_loaded()` 幂等入口。
2. **分批构建**：每批 12 张，用 `QTimer.singleShot(0, ...)` 让出事件循环，界面先出骨架再填充。
3. **去掉每卡 `layout.activate()`**：改为构建完一批后对 `grid_host` 调一次 `updateGeometry()`；标题折行推迟到首次 `resizeEvent`（此时宽度才是最终值，当前在 `__init__` 里算一次、resize 再算一次，属重复劳动）。

**验证方法**（已修复，见 12.5）

- 自动化：`python -m unittest tests.test_ui_render_cost`（10 项，应为 `OK`）。`HomeBatchBuildTests` 用 40 条假 `HomeVideo` 断言 (1) `set_videos` 只建第一批 `CARD_BATCH_SIZE` 张卡，其余留在 `_pending_videos`；(2) 把剩余批次逐批建完后，卡片**数量与顺序**都与一次性构建完全一致（行为不变守门）；(3) 后续批次不会把选中项从第一张卡抢走；(4) 首屏不预取封面——`all(not card._thumbnail_requested)` 且 `_thumbnail_cache.pending_count() == 0`；(5) 重新 `set_videos(3)` 会丢弃上一次的待建批次，不会串批。
- 人工：打开首页并立刻观察——列表应「立刻出现一屏、剩下的补齐」而不是整页卡住；封面随滚动逐步出现。连续快速翻 5 页/切换首页来源，不应出现上一页的卡片或封面串到当前页；滚到底后所有封面最终都会加载完成。


### P3 [High] 播放列表面板全量 widget 与全量样式重算

**位置**：`ui/playlist_overlay.py:266-272`（逐条 `PlaylistItemWidget` + `setItemWidget`）、`ui/playlist_overlay.py:306-316`（`set_current_index` 遍历全部行）、`ui/playlist_overlay.py:477-482`（`_sync_selection_visuals` 对每行 `unpolish`/`polish`）

**说明**：这正是前序文档 4.4 条目，至今未落地。合并播放列表可达数百条：每条一个 `QFrame` + 4 个子控件；每次选中变化都对**所有行**做 `unpolish/polish`（QSS 全量重算，是 Qt 里最贵的操作之一）；`_load_visible_thumbnails` 每次滚动遍历全部行调 `visualItemRect()`，且 `_schedule_visible_thumbnail_load` 无去抖，滚动一次触发多次全表扫描。

**修复方案**：分两级，先做低风险的第 1 级，评估后再决定是否做第 2 级。

1. **增量刷新（低风险）**：记录 `_active_row` 与上次选中集合，只对发生变化的行 `unpolish/polish`；`_schedule_visible_thumbnail_load` 接一个 60ms 单次 `QTimer` 去抖；用 `list_widget.indexAt()` 定位视口首末行，只遍历该区间而非全表。
2. **delegate 化（中风险）**：用 `QStyledItemDelegate` + `QAbstractListModel` 承载条目绘制，彻底去掉 per-item widget。收益最大，但会改变鼠标事件路径（`ui/player_page.py:471` 的 `_install_mouse_tracking` 依赖遍历子控件装事件过滤器），需同步调整自动隐藏逻辑。

**验证方法**（已修复，见 12.6）

- 自动化：`python -m unittest tests.test_ui_render_cost`（10 项，应为 `OK`）。`PlaylistRestyleCostTests` 把 `PlaylistItemWidget._refresh_style` 换成计数版，断言 (1) `set_current_index(3)` 只重刷「旧活动行 + 新活动行（+ 选中态变化行）」共 ≤4 行，未涉及的第 5 行完全没被刷；(2) 重复设同一个 index 一次重刷都不产生；(3) 改选中行只碰变化的那一行（`set(restyles) == {4}`），旧选中行的 `selected` 属性仍然正确；(4) 全表扫一遍，每行的 `selected`/`active` 属性与 `selectedIndexes()` 和当前活动行一致（保证增量刷新没有漏刷造成状态漂移）；(5) 重新 `set_playlist` 会重置缓存的行状态。
- 人工：打开一个 300 条以上的播放列表，上下快速滚动应无明显掉帧；连续快速点不同条目/连续切歌，高亮（活动行）与选中框始终只有正确的一行，不出现两行同时高亮或高亮残留；封面按需加载。


### P4 [High] 启动期串行构造

**位置**：`ui/main_window.py:71-183`、`database/sqlite_manager.py:110-119`

**说明**：`MainWindow.__init__` 在 UI 线程里串行完成：ConfigService（读 JSON + 探测系统代理）→ SQLiteManager（建表 + 两条全表 UPDATE 迁移）→ 3 个 repository → SiteResolver → UpdateService/RuntimeInstallService/FfmpegInstallService（`shutil.which` 磁盘探测）→ DlnaController/DlnaMediaServer → DownloadManager（读任务 JSON + 目录扫描）→ 8 个页面 → MpvPlayer（加载 libmpv）。窗口出现前用户什么都看不到。

**修复方案**：
- 页面延迟构造：`QStackedWidget` 里除首页外，其余页面首次切入时才实例化（现有 `main_window` 的页面引用都集中在 `__init__`，改为属性惰性初始化 + `_ensure_page(name)`）。
- 迁移加版本守卫（见 P5），把启动期两条全表写入降为零。
- FFmpeg/yt-dlp 探测结果缓存（见 P12），并移出启动关键路径。

**验证方法**（已修复，见 12.7）

- 自动化：`python -m unittest tests.test_main_window_hardening`（10 项，应为 `OK`）。`LazyPageTests` 断言 (1) 启动后 `_lazy_pages` 为空、`stack.count() == 2`（只有首页与播放页）；(2) 依次访问 `playlist/download/favorite/history/settings/about` 六个属性，每访问一次 `stack` 才 +1，且**第二次访问命中缓存**（返回同一对象且 `stack` 不再增长）；(3) `_created_page("settings")` 这种「查已建页面」的调用不会顺手把页面建出来（否则惰性构造就被自己的内部查询破坏了）。
- 人工：冷启动计时——在 `main()` 起点与 `MainWindow.show()` 之后各看一次日志时间戳，与优化前对比（预期首屏可见时间明显下降）。然后逐个点开左侧六个页面，每个页面首次进入都要正常渲染、无空白无报错；退出应用时日志无异常堆栈。


### P5/P6/P7 [Medium] 数据库层

**P5 迁移无版本守卫**（`database/sqlite_manager.py:110-119`）：每次启动都执行两条 `UPDATE ... WHERE source_site='youtube' AND lower(webpage_url) LIKE '%bilibili.com/%'` 全表扫描 + 写入。修复：在 `settings` 表存 `schema_migration_version`，只在版本落后时执行，执行后写回。

**P6 缺少 PRAGMA**（`database/sqlite_manager.py:104-108`，前序文档 4.5 未落地）：

```python
def connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self.db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
```

WAL 让"播放时写历史"与"历史页读取"不再互斥；`busy_timeout` 消除并发下的 `database is locked`。注意 WAL 会额外产生 `-wal`/`-shm` 文件，需确认 `data/` 已在 `.gitignore` 内（已确认）。

**P7 两段式 upsert + 连接开销**（`database/favorite_repository.py:72-108`、`database/history_repository.py`）：`favorite.video_id` 已有 `UNIQUE` 约束（`database/sqlite_manager.py:32`），可直接用原生 upsert 把两次往返压成一次：

```python
conn.execute(
    """
    INSERT INTO favorite (video_id, title, source_site, webpage_url, uploader, duration, thumbnail, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(video_id) DO UPDATE SET
        title=excluded.title, source_site=excluded.source_site, webpage_url=excluded.webpage_url,
        uploader=excluded.uploader, duration=excluded.duration, thumbnail=excluded.thumbnail,
        updated_at=excluded.updated_at
    """,
    (...),
)
```

`_upsert` 需要返回"是否新增"，可用 `conn.execute("SELECT changes()")` 前后对比或改为先 `INSERT OR IGNORE` 再判断 `rowcount`。`history` 表没有 `UNIQUE(video_id)`，若要同样处理需先加唯一索引（属数据迁移，需谨慎，建议本轮只加 WAL 与索引复核，不改 history 写入语义）。

**P16**：`favorite_ids()`（`database/favorite_repository.py:49-52`）每次全表取 id，被首页加载与收藏视图刷新反复调用。修复：在 `MainWindow` 侧维护一份内存集合，增删收藏时增量更新，仅在启动和设置重建时全量读。

**验证方法**（未修复，以下为修复时的验收标准）

- 复现（人工）：P5——在 `database/sqlite_manager.py` 的两条迁移 `UPDATE` 前后各打一条日志，重启应用 3 次，每次都会看到全表扫描执行（应当只在第一次执行）。P6——`sqlite3 data/tube_player.db "PRAGMA journal_mode;"` 当前返回 `delete`；边播视频（写历史）边反复刷新历史页，日志里可能出现 `database is locked`。P7——在 `_upsert` 里打点，收藏一个视频会看到两次 SQL 往返。P16——在 `favorite_ids()` 里打点，打开首页 + 切到收藏页会看到多次全表查询。
- 修复后自动化（验收标准）：新增 `tests/test_database_perf.py`，全部用临时目录里的库，(1) P5：连续 `SqliteManager` 初始化 3 次，把迁移 SQL 包一层计数桩，断言只执行 1 次，且 `settings` 表里 `schema_migration_version` 已写回；(2) P6：`connect()` 后断言 `PRAGMA journal_mode` 为 `wal`、`foreign_keys` 为 1、`busy_timeout` 为 5000；(3) P7：同一 `video_id` 收藏两次，断言表里只有 1 行、`updated_at` 被刷新、`title` 等字段取新值，且 `_upsert` 返回的「是否新增」第一次 True 第二次 False（语义不变守门）；(4) P16：把 `favorite_ids()` 包成计数桩，做一次「启动 → 加收藏 → 删收藏 → 刷新收藏页」，断言全量读次数为 1，且中途内存集合与数据库实际内容一致（用一次真实查询对账）。
- 修复后人工：删掉 `data/` 后冷启动一次（验证建库路径），再启动第二次确认迁移不再执行；`sqlite3 ... "PRAGMA journal_mode;"` 返回 `wal`，且 `data/` 下出现 `-wal`/`-shm`（已在 `.gitignore` 内）。收藏/取消收藏 10 次，重启后收藏列表与操作结果一致——**数据库最终内容不得因为这轮优化发生变化**。

### P8/P9/P10 [Medium] 解析层重复远程调用与探测

- **P8 WBI 签名**（`resolver/site_resolver.py:917-923`，前序文档 4.1）：每次签名都请求 `x/web-interface/nav` 拿 `img_key`/`sub_key`，等于把每次 B 站首页/搜索的 HTTP 往返翻倍。这两个 key B 站按天轮换，缓存 30 分钟完全安全：用 `(key, expires_at)` + 锁缓存，失效或签名被拒（`-352`）时刷新。
- **P9 浏览器 Cookie 探测**（`resolver/site_resolver.py:892-915`、`resolver/youtube_resolver.py:353`，前序文档 4.2）：每次请求都可能逐个浏览器读取 Cookie 库（磁盘 + 解密开销），且 YouTube/Bilibili 两侧各写了一份类似逻辑。修复：抽出 `CookieResolver`，对 `(browser_spec, domain)` 结果缓存 60 秒并记录失败浏览器的短期黑名单；同时统一 `download/command_builder.py:104-113` 的四分支 cookie 选择逻辑，消除三处重复。
- **P10 缓存键指纹**（`resolver/site_resolver.py:182-201` + `services/config_service.py:69-78`）：每次算 cache key 都 `stat` Cookie 文件 + 调 `detect_system_proxy()`（Windows 下读注册表）+ SHA1 一次 JSON。修复：`detect_system_proxy()` 结果缓存 30 秒（设置保存时主动失效），指纹本身按"配置版本号"缓存。

**验证方法**（未修复，以下为修复时的验收标准）

- 复现（人工）：把 `tube_player.resolver` 日志级别调到 DEBUG，打开 B 站首页再搜索一次。P8——会看到每次签名前都请求一次 `x/web-interface/nav`（B 站请求数正好翻倍）。P9——每次请求都能看到浏览器 Cookie 库的读取日志（配了「从浏览器读 Cookie」时更明显）。P10——在 `detect_system_proxy()` 里打点，会看到它被每次缓存键计算各调一次。
- 修复后自动化（验收标准）：新增 `tests/test_resolver_caching.py`，全部离线（把 HTTP 层打桩），(1) P8：连续签名 5 次，断言 `nav` 只被请求 1 次；把缓存标记为过期后再签一次，断言重新请求；模拟返回 `-352` 时断言强制刷新 key 并重试一次（失效路径不能被缓存挡住）；(2) P9：同一 `(browser_spec, domain)` 连查 10 次，断言底层读取只发生 1 次；对一个读取失败的浏览器断言进入短期黑名单、后续不再重试；(3) P10：连算 10 次缓存键，断言 `detect_system_proxy()` 只被调 1 次，且**保存设置后指纹必须变化**（这是关键守门用例：缓存不能让改了代理/Cookie 之后还命中旧缓存）。
- 修复后人工：改一次代理设置并保存 → 立刻打开 B 站首页，返回的内容必须是新代理下的结果（不能命中旧缓存）；切换 Cookie 来源浏览器后同样要立刻生效。观察日志确认单次首页加载的 B 站请求数比优化前减少约一半。

### P11~P15 [Medium] 其余性能项

- **P11 逐字节读子进程输出**（`download/download_worker.py:261-283`）：`stdout.read(1)` 每个字符一次 Python 调用，yt-dlp 进度行刷新频繁，3 个并发任务时 CPU 占用可观。修复：改为 `read(4096)` 块读 + 在缓冲区内切分 `\r`/`\n`，语义保持不变（yt-dlp 用 `\r` 刷新同一行，必须继续按 `\r` 切分，不能用 `readline()`）。
- **P12 命令构建期重复探测**（`download/command_builder.py`）：`_ffmpeg_location` / `_ffmpeg_available` / `_find_ytdlp` 每次构建命令都 `shutil.which` + 多次 `Path.exists()`。修复：模块级缓存 + 配置变更时失效。另：`_find_ytdlp()` 最终回落 `Path("yt-dlp")` 依赖 PATH，找不到时报错信息不明确，建议显式抛出中文错误提示用户去设置页安装。
- **P13 mpv 属性轮询**（`player/mpv_player.py`）：每 500ms 在 UI 线程通过 ctypes 取 `duration`/`position`/`pause`/`eof-reached` 等 5 个属性。libmpv 原生支持 `mpv_observe_property` + 事件回调。修复分两步：先把轮询里"未变化就不 emit"做严格化（减少下游 UI 刷新），再评估接入 `observe_property`（需一个事件泵线程，改动较大，建议独立一轮）。
- **P14 DLNA 位置轮询**（`ui/main_window.py:98-119`）：投屏中每 1.5s 一次 SOAP `GetPositionInfo`。修复：拉到 2.5s，并在设备返回 `NOT_IMPLEMENTED` 后自动停止轮询（很多渲染器不支持）。
- **P15 SSDP 发现开销**（`dlna/discovery.py`）：每个网卡 × 4 个 SearchTarget（含 `ssdp:all`）× 2 次广播，另 `_child_text` 用 `node.iter()` 做全后代搜索。修复：去掉 `ssdp:all`（其余 3 个 ST 已覆盖媒体渲染器）、`_child_text` 改为按已知路径 `find()`。
- **P17**（`ui/player_page.py:471`、`683-688`）：每次装载播放列表都对整棵 overlay 树 `findChildren(QWidget)` 重装事件过滤器。Qt 会去重，故无重复回调，但遍历数百个 widget 的成本被叠加在切歌路径上。修复：只对新建的条目 widget 装（在 `PlaylistOverlay` 内部发信号通知），或在 P3 的 delegate 化后自然消失。
- **P18**（`ui/player_page.py:497-520`）：播放页封面绕过 `ThumbnailCache`，既不复用缓存，也不作废旧请求 —— 快速切歌时旧请求后到会把上一首的封面贴到当前视频上。修复：改用 `ThumbnailCache`，并按当前 `video_id` 做回调有效性校验。
- **P19**（`app_paths.py:118-128`）：import 期就对候选目录 `mkdir` 并写 `.write_test` 探测可写性。副作用发生在 import 阶段，测试与打包环境都会被写文件。修复：改为 `ensure_runtime_dirs()` 内首次调用时惰性求值并缓存结果。
- **P20**（`services/ffmpeg_install_service.py`）：`rglob("ffmpeg.exe")` 递归整棵解压目录。修复：按已知布局 `*/bin/ffmpeg.exe` 优先命中，失败再回落 rglob。
- **P21**（`workers/update_download_worker.py`）：每 128KB emit 一次进度。修复：按"进度变化 ≥1% 或间隔 ≥200ms"节流。

**验证方法**（未修复，以下为修复时的验收标准）

- 复现（人工）：P11——同时跑 3 个下载任务，任务管理器里看 python 进程 CPU 占用（逐字节读会明显偏高）。P17——在 `findChildren` 调用处打点并打印返回数量，装载一个 300 条的播放列表再切歌，会看到每次切歌都遍历数百个 widget。P18——快速连续切换 3 首歌，播放页封面可能停在上一首（旧请求后到覆盖）。P19——直接 `python -c "import app_paths"`，会发现候选目录已被建出、且留下 `.write_test` 痕迹。P21——下载大文件时观察进度信号频率。
- 修复后自动化（验收标准）：
  - P11：`tests/test_download_progress.py` —— 用假进程吐出混合 `\r`/`\n` 的 yt-dlp 进度文本（含一行被拆到两个读块中间的边界用例），断言块读切分出的行序列与逐字节实现**完全一致**（行为不变守门），且 `read` 调用次数远小于字符数。
  - P17/P18：在 `tests/test_ui_render_cost.py` 追加 —— 装载播放列表两次，断言 `findChildren` 不在切歌路径上被调用；封面用例断言旧 `video_id` 的回调到达时被丢弃、当前封面不被改写。
  - P19：`tests/test_app_paths.py` —— `import app_paths` 后断言尚未创建任何目录（用临时 HOME/LocalAppData），调用 `ensure_runtime_dirs()` 后才创建；重复调用只探测一次可写性。
  - P21：在 `tests/test_update_verify.py` 或新模块中断言下载 10MB 假数据时 `progress` emit 次数 ≤120（1% 粒度），且**最后一次一定是 100%**（节流不能吞掉终态）。
- 修复后人工：3 个并发下载时 CPU 占用较优化前下降；300 条播放列表切歌不掉帧；快速连切 5 首歌，封面始终是当前歌曲；下载升级包时进度条平滑推进并准确停在 100%。

## 6. 功能不全详解

### F1 [High] Bilibili 请求不走代理

**位置**：`resolver/site_resolver.py:879-881`

**说明**：B 站分支直接 `urllib.request.urlopen(req, timeout=25)`，用的是全局默认 opener，完全无视 `ConfigService.effective_proxy()`。结果是"设置了代理，YouTube 通了，B 站仍走直连"，在需要代理出网的网络环境下 B 站首页/搜索/解析全部失败，且报错信息不指向代理。

**修复方案**：与 `services/update_service.py:379-384` 的做法统一，抽一个共享的 opener 构建函数（也顺带完成前序文档 5.2「统一 HTTP session」）：

```python
def _opener(self) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    _source, proxy = self.config.effective_proxy()
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)

with self._opener().open(req, timeout=25) as resp:
    return resp.read().decode("utf-8", errors="replace")
```

注意 opener 不可跨线程共用（见 C5），按调用构建。

**验证方法**（已修复，见 12.8）

- 自动化：`python -m unittest tests.test_proxy_settings`（8 项，应为 `OK`）。`BilibiliProxyTests` 用假 `ConfigService`（只提供 `effective_proxy()`）断言 (1) 配置了代理时 `_build_opener()` 的 handler 链里恰好有 1 个 `ProxyHandler`，且 `http`/`https` 两个键都指向该代理；(2) 无代理时即使环境变量里塞了 `http_proxy`/`https_proxy`，opener 也不会带上读环境变量的代理（`ProxyHandler({})` 不注册 `*_open`，`build_opener` 因此既不装它也不补默认的那个，结果是真正的直连）；(3) 每次调用都新建 opener——`OpenerDirector` 非线程安全，多个 worker 不能共用。
- 人工：设置里填一个**不存在**的代理（如 `127.0.0.1:1`）→ 打开 B 站视频应当失败（这证明代理确实被用上了，而不是被绕过）；改回有效代理后恢复正常。若手边有抓包工具，可确认 B 站请求走的是代理端口而非直连。


### F2 [High] 系统代理静默覆盖用户配置

**位置**：`services/config_service.py:69-78`

**说明**：`effective_proxy()` 无条件优先返回系统代理，用户在设置页显式填写的 `youtube.proxy` 只有在系统代理为空时才生效。用户改了设置却毫无效果，且界面上没有任何提示说明"当前实际用的是系统代理"。

**修复方案**：改成显式三态，默认值保持当前行为以兼容既有用户配置：

```python
def effective_proxy(self) -> tuple[str, str]:
    mode = str(self.get("network.proxy_mode", "auto") or "auto").strip().lower()
    configured = normalize_proxy(str(self.get("youtube.proxy", "") or "").strip())
    if mode == "off":
        return "已禁用代理", ""
    if mode == "manual":
        return ("配置代理", configured) if configured else ("未使用代理", "")
    if configured:                       # auto 模式下用户填了就优先用户的
        return "配置代理", configured
    system_proxy = detect_system_proxy()
    return ("系统代理", system_proxy) if system_proxy else ("未使用代理", "")
```

同时在 `config/default_config.json` 增加 `network.proxy_mode` 默认 `"auto"`，设置页加三选一控件，并把 `effective_proxy()` 返回的标签显示在设置页（用户能看到当前实际生效的是哪一个）。

**验证方法**（已修复，见 12.9）

- 自动化：`python -m unittest tests.test_proxy_settings`（8 项，应为 `OK`）。`ProxyModeTests` 把 `detect_system_proxy` 打成桩，逐个模式断言 `effective_proxy()` 返回的「来源标签 + 代理地址」二元组：(1) auto 模式下**配置代理优先于系统代理**（`("配置代理", "http://127.0.0.1:7890")`，这条就是本问题的回归守门用例）；(2) auto 且未配置代理时才回落到 `("系统代理", ...)`；(3) manual 模式下 `detect_system_proxy` **一次都不会被调用**（`detect.assert_not_called()`），未填代理就是不用代理，来源标签含「手动模式」；(4) off 模式返回 `("强制直连", "")` 且同样不探测系统代理；(5) 配置里写了非法模式值时降级为 auto，不抛异常。
- 人工：设置页把模式切到「手动」但不填代理 → 即使系统开着 VPN/代理，应用也应直连；切到「关闭」→ 即使配置里留着代理地址也必须直连；切回「自动」→ 未填配置代理时跟随系统代理。三种情况下设置页显示的来源标签要和实际行为一致（标签就是 `effective_proxy()` 的第一个返回值）。


### F3 [High] 无可用清晰度时 `StopIteration` 逃逸

**位置**：`ui/main_window.py:746-750`

**说明**：`_select_default_quality` 用 `next(iter(video.qualities.values()))` 取兜底清晰度。当解析成功但 `qualities` 为空（会员/地区限制、直播、纯音频、B 站部分番剧），`StopIteration` 会从 UI 槽里抛出 —— 解析明明成功了，用户看到的是崩溃或"无响应"，而不是"该视频无可播放清晰度"。

**修复方案**：

```python
def _select_default_quality(self, video: VideoInfo) -> str:
    preferred = self.config.get("player.default_quality", "")
    if preferred and preferred in video.qualities:
        return preferred
    return next(iter(video.qualities), "")
```

调用方拿到空字符串时走"提示用户该视频暂无可播放清晰度"的分支（复用现有 `set_playback_available(False)` + 状态栏提示），必要时给出"用浏览器打开"的引导。

**验证方法**（已修复，见 12.10）

- 自动化：`python -m unittest tests.test_main_window_hardening`（10 项，应为 `OK`）。`DefaultQualityTests` 直接以未绑定方法的形式调用 `MainWindow._select_default_quality`（用 `SimpleNamespace` 假 state 提供 `config.get`），断言 (1) `qualities={}` 时返回 `None` 而不是让 `StopIteration` 逃逸（修复后签名是 `VideoQuality | None`，不是空串）；(2) 首选清晰度存在时命中它；(3) 首选为 `Auto` 时取第一个；(4) 首选清晰度不在列表里（如配置成 `4320p`）时回落到第一个而不是报错。
- 人工：打开一个取不到任何清晰度的视频（会员限定、地区限制、或直接把网络断开后再解析）→ 应当弹出中文提示对话框（`ui/main_window.py` 里的 `QMessageBox.critical`）并停在当前页面，而不是控制台抛 `StopIteration`、播放页卡死或整个应用退出。提示关掉后继续操作其他视频应正常。


### F4~F9 [Medium/Low] 其余功能缺口

- **F4**（`resolver/youtube_resolver.py:317`）：`subprocess.run(..., timeout=120)` 未捕获 `subprocess.TimeoutExpired`，超时会以未处理异常形式冒到 worker 的通用 except（若有）或线程池，错误信息对用户无意义。修复：捕获后 `raise RuntimeError("解析超时，请检查网络或代理设置") from exc`，并把超时值提为可配置。
- **F5**（`download/download_manager.py:29,341-350,411-...`）：`VIDEO_ID_IN_FILENAME_RE = r"\[(?P<video_id>[0-9A-Za-z:_-]{6,128})\]"` 会命中标题里任何方括号内容（如 `[4K修复]`），误判后 `_url_from_video_id` 又一律拼成 YouTube 链接，导致"重新下载/定位来源"指向错误视频。修复：先按 `source_site` 决定 URL 模板，并把正则限制为文件名末尾的最后一组方括号（yt-dlp 输出模板 `%(title).200B [%(id)s].%(ext)s` 保证 id 在末尾），同时优先用任务记录里已有的 `webpage_url` 而非从文件名反解。
- **F6**（`database/playlist_repository.py:203-207`）：本地重复实现了一份 `_detect_source_site`，且非 B 站域名一律判为 `youtube`。修复：改为 import `resolver.source_utils.detect_source_site`，与 `favorite_repository` 保持一致。
- **F7**（`services/update_service.py:259-261`）：`automatic_upgrade_supported` 仅覆盖 Windows，Linux 只能提示。修复：在 Linux 分支给出明确的中文指引（AppImage 手动替换 / `dnf upgrade` COPR），避免用户以为功能坏了。
- **F8**（`ui/player_page.py:843-849` 与 `ui/text_elision.py:93-100`）：`format_seconds` 两份实现（`ui/home_page.py:18` 从 player_page 导入、`ui/playlist_overlay.py:20` 从 text_elision 导入）。修复：保留 `ui/text_elision.py` 一份，player_page 改为 re-export 或直接改所有导入点。
- **F9**：`tests/` 目前无覆盖文本折行、DLNA 中继与升级校验。本轮每个 Critical/High 修复都必须带单测（具体见各条"验证方法"）。
- **补充（Low）**：`dlna/media_server.py:58-70` 的 `register_source` 会 `self._sources.clear()`，多设备并行投屏时旧会话被静默中断；`_parse_range_header`（`dlna/media_server.py:364-366`）对空文件返回 `(0, -1, False)` 语义含糊。两项建议在 S4 改造时一并整理。

**验证方法**（F9 已随本轮修复达成，其余未修复；以下为修复时的验收标准）

- F9 现状：本轮已补齐三块此前完全空白的覆盖 —— 文本折行 `tests/test_text_elision.py`、DLNA 中继 `tests/test_dlna_media_server.py` + `tests/test_dlna_access_control.py`、升级校验 `tests/test_update_verify.py` + `tests/test_update_launch.py`。验证：`python -m unittest discover -s tests -p "test_*.py"` 应为 `OK`（当前 213 项）。
- 复现（人工）：F4——把网络限速或指向一个不可达代理后解析 YouTube 视频，等满超时，日志会出现裸的 `subprocess.TimeoutExpired` 而不是中文提示。F5——下载一个标题里含 `[4K修复]` 之类方括号的视频，然后在下载页对它用「重新下载/打开来源」，会跳到错误的视频（甚至把 B 站视频拼成 YouTube 链接）。F6——收藏一个非 B 站非 YouTube 域名的视频，看 `playlist` 与 `favorite` 两张表里 `source_site` 是否不一致。F7——在 Linux 上点检查更新，提示文案未说明该怎么手动升级。F8——`grep -rn "def format_seconds" ui/` 会看到两份实现。
- 修复后自动化（验收标准）：
  - F4：`tests/test_youtube_resolver_errors.py` —— 把 `subprocess.run` 打成抛 `TimeoutExpired` 的桩，断言外层抛出的是 `RuntimeError` 且消息含「解析超时」，并且 `__cause__` 保留原异常。
  - F5：`tests/test_download_source_mapping.py` —— 参数化一组文件名（含 `标题 [4K修复] [dQw4w9WgXcQ].mp4`、B 站 `BV1xx411c7mD`、无 id 的文件名），断言 (1) 只取**末尾最后一组**方括号；(2) URL 按 `source_site` 生成（B 站不得拼成 YouTube 域名）；(3) 任务记录里已有 `webpage_url` 时优先用它、完全不走文件名反解。
  - F6：断言 `playlist_repository` 与 `favorite_repository` 对同一批 URL（B 站/YouTube/其他域名各一个）给出**完全相同**的 `source_site`，且其他域名不再被硬判为 `youtube`。
  - F7：断言 Linux 分支下 `automatic_upgrade_supported` 为 False 且返回的提示文案非空、包含手动升级指引关键词（AppImage / dnf）。
  - F8：断言 `ui.player_page.format_seconds is ui.text_elision.format_seconds`（同一对象，证明只有一份实现），并保留 `00:00`/`01:15`/`01:02:05` 三组基线断言不回归。
  - 补充项：多设备投屏改造后，断言 `register_source` 注册第二个设备时不会清掉第一个设备的 token（两个 token 都能 `authorize` 通过）；`_parse_range_header` 对空文件返回明确语义（建议 `None` 或显式 416），并补一条空文件用例。
- 修复后人工：逐条重跑上面的复现步骤，均得到正确行为；下载页对 10 个不同来源的历史任务点「打开来源」，全部跳转正确。

## 7. 前序文档（2026-07-10）落地状态复核

逐条对照真实代码确认，避免重复备案：

| 前序条目 | 状态 | 证据 |
| --- | --- | --- |
| 3.1 首页/搜索内存缓存 | 已落地 | `resolver/site_resolver.py:45,203-219` LRU + TTL |
| 3.2 缩略图进程内缓存 | 已落地 | `ui/thumbnail_cache.py:19-53` LRU + in-flight 合并 |
| 3.3 下载任务 JSON 去抖 + 原子写 | 已落地 | `download/download_manager.py` 定时器去抖 + `tmp`→`replace` |
| 3.4 下载进度目录扫描缓存 | 已落地 | `download/download_worker.py:294` `_DownloadFileMatcher` 路径缓存 |
| 4.1 Bilibili WBI key 缓存 | **未落地** | 本文 P8 |
| 4.2 浏览器 Cookie 探测缓存 | **未落地** | 本文 P9 |
| 4.4 播放列表面板 delegate 化 | **未落地** | 本文 P3 |
| 4.5 SQLite PRAGMA 调优 | **未落地** | 本文 P6 |
| 5.1 YouTube 分页策略优化 | **未落地** | 保留在前序文档，本轮不重复展开 |
| 5.2 统一 HTTP session | **未落地** | 本文 F1 一并解决 |
| 5.3 图片磁盘缓存 | **未落地** | 建议排在 P2 之后评估（内存缓存已能覆盖多数场景） |

## 8. 分期实施计划

每一期独立可交付、独立可回滚（单独提交），期末跑全量单测 `python -m unittest discover -s tests -p "test_*.py"` + 手工冒烟。

**第 1 期：安全底线（S1、S2、S3、S4、C1）**
风险最高、彼此耦合小。产出：升级包三重校验 + FFmpeg 包哈希固定与安全解压 + PowerShell 不落盘 + DLNA 来源限制 + FFmpeg stderr 排空。新增测试：`tests/test_update_verify.py`、`tests/test_archive_safety.py`、`tests/test_dlna_media_server.py`。

**第 2 期：正确性与稳定性（C2、C3、C4、C5、C6、C7、F3、F4）**
消除崩溃与悬垂：缩略图弱引用 + 退出等待 worker + 缓存加锁 + opener 不跨线程 + resolver 代号失效 + 空清晰度兜底 + 解析超时中文报错。

**第 3 期：可感知性能（P1、P2、P3-1、P4）**
P1 文本度量缓存 → P2 首页分批 + 可见性缩略图 → P3 第 1 级增量刷新 → P4 页面惰性构造。这一期用户体感最强，建议每项单独提交并各自记录耗时对比。

**第 4 期：后台与数据层（P5、P6、P7、P8、P9、P10、P11、P12、P16）**
迁移版本守卫 + PRAGMA + 原生 upsert + WBI/Cookie/代理探测缓存 + 块读子进程输出 + 命令构建缓存。

**第 5 期：功能补齐与收尾（F1、F2、F5、F6、F7、F8、P13~P15、P17~P21）**
代理链路统一与三态模式（需同步改 `config/default_config.json` 与设置页）、下载来源反解修正、重复实现合并、其余 Low 项。

**可选第 6 期：P3-2 播放列表 delegate 化**
收益大但改动面广（触及播放页鼠标事件路径），建议在第 3 期实测后单独决策。

## 9. 约束与风险

- **行为不变原则**：性能类改造（P1、P2、P3、P7）必须保证可观察输出一致 —— 折行结果、封面最终状态、数据库最终内容不得变化；每项都以单测固定基线。
- **需要用户决策的点**：F2 的代理三态会改变现有"系统代理优先"的默认行为（默认值 `auto` 下已尽量兼容）；P7 若要给 `history` 加唯一索引属数据迁移，本轮建议不做。
- **打包影响**：S2 固定 FFmpeg 版本哈希后，升级 FFmpeg 版本需同步更新常量；P6 的 WAL 会新增 `-wal`/`-shm` 文件（`data/` 已 gitignore）。
- **不引入新依赖**：S5 用零依赖的 DTD 前置检查而非 `defusedxml`，避免影响 PyInstaller 打包体积与 spec 文件。
- **约定遵循**：所有改动沿用 `from __future__ import annotations`、`logging.getLogger("tube_player.<area>")` + 惰性 `%` 格式化、worker 走 `QRunnable` + `WorkerSignals`、路径统一从 `app_paths` 取、配置统一走 `ConfigService`；用户可见文案与日志保持中文。

## 10. 本轮未逐行覆盖的文件

以下文件本轮仅做接口层面确认，未逐行审计，不代表无问题：`ui/settings_page.py`、`ui/toolbar.py`、`ui/history_page.py`、`ui/favorite_page.py`、`ui/download_page.py`、`services/runtime_install_service.py`、`services/logging_service.py`、`services/shortcut_service.py`、`main.py`、`platform_support.py`、`workers/` 下除 `update_download_worker.py` / `archive_extract_worker.py` 外的模块。若需要，可在方案批准后追加一轮补充审计。

---

## 11. 第 1 期实施记录（2026-07-29）

> 本节记录批准编码后的实际改动、新发现的问题与最终验证结果，作为永久档案。

### 11.1 S1 — 升级包完整性校验（含升级执行根因修复）

**实际改动文件**：`services/update_service.py`、`workers/update_download_worker.py`、`ui/main_window.py`

**新增函数/类**（`update_service.py`）：
- 常量：`TRUSTED_DOWNLOAD_HOSTS`、`HASH_CHUNK_SIZE`、`SHA256_PATTERN`
- `ReleaseAsset.digest`（新字段，从 GitHub API `digest` 解析）
- `normalize_sha256`、`extract_sha256_for`、`_is_checksum_asset`
- `ensure_trusted_download_url`：HTTPS 前置校验 + 受信主机白名单
- `sha256_file`：1 MB 分块 SHA256 计算
- `verify_downloaded_file`：大小 + 哈希双重校验，不符即删临时文件
- `UpdateService.resolve_expected_sha256`：按 asset digest → SHA256SUMS 资产 → Release 正文三级优先级解析预期哈希
- `UpdateService.verify_authenticode`：Windows Authenticode 签名状态校验
- `UpdateService._spawn_launcher`（根因修复，见下）

**`workers/update_download_worker.py`**：
- 构造函数新增 `expected_size`、`expected_sha256`、`expected_sha256_resolver`、`trusted_hosts`、`verify_signature`
- 下载前校验 URL、下载后校验重定向地址、写盘后调 `verify_downloaded_file`，通过后才 `os.replace`

**`ui/main_window.py`**：
- 升级包、Node、FFmpeg 三路下载均传入对应的哈希/大小/受信主机参数
- 新增 `_quit_for_upgrade`（`close()` → `waitForDone(3000)` → `QApplication.quit()`）
- 新增模块级 `_arm_exit_watchdog`（daemon `threading.Timer(10, os._exit(0))`）
- `_launch_downloaded_upgrade` 末尾改为 `QTimer.singleShot(0, self._quit_for_upgrade)`

**新发现：在线升级"安装包从未被执行"根因**

原实现在 `launch_installer` / `launch_portable_update` 里使用 `DETACHED_PROCESS`（`0x8`）标志启动 PowerShell。实测确认：`powershell.exe -File` 在无控制台的情况下（父进程为 GUI 窗口），`DETACHED_PROCESS` 导致 PowerShell 以退出码 0 立即退出且不执行任何脚本行——连无条件日志写入都未发生。**这是"下载完成却永远不执行安装包/便携包替换"的确定性根因，与网络/权限/路径无关。**

修复：去掉 `DETACHED_PROCESS`，改用 `CREATE_NO_WINDOW`（为子进程分配不可见控制台，脚本可正常运行；Windows 不因父进程退出而结束该子进程）。

同步改造 PowerShell 脚本：
- 新增 `LAUNCHER_COMMON_PRELUDE`（公共函数注入）：`Write-UpgradeLog`（无条件写日志）、`Wait-ForProcessExit`（pid + exe 全路径双条件，有界超时）、`Start-UpgradeProcess`（`-PassThru` 校验 + `-Verb RunAs` 回退）
- 两个脚本均使用新的等待/启动函数，并记录安装包/robocopy 退出码
- `INSTALLER_LAUNCHER_SCRIPT` 新增 `-AppExecutable` 参数，用于 `Wait-ForProcessExit` 的 exe 路径匹配

**新增测试**：`tests/test_update_verify.py`（23 项）、`tests/test_update_launch.py`（原有改造，共 12 项，其中 3 项随 S3 追加，见 12.1）

---

### 11.2 S2 — FFmpeg 包哈希固定与路径穿越防护

**实际改动文件**：`services/ffmpeg_install_service.py`、`workers/archive_extract_worker.py`、`services/runtime_install_service.py`

**`ffmpeg_install_service.py`**：
- 固定 `FFMPEG_ARCHIVE_SHA256 = "a0c715acca3839bfd203e600a7775b83cfe3ff928a4eceb9ca54f2982365901c"`（实测计算，与 gyan.dev 侧车 `.sha256` 文件一致）
- 固定 `FFMPEG_ARCHIVE_SIZE = 32563789`
- 新增 `FFMPEG_TRUSTED_HOSTS = ("gyan.dev", "www.gyan.dev")`
- `FfmpegInstallInfo` 新增 `sha256`、`size`、`trusted_hosts` 字段

**`workers/archive_extract_worker.py`**（整文件重写）：
- 新增 `ArchiveEntryRejected` 异常
- `validate_archive_entry`：拒绝空名/绝对路径/盘符/UNC/`..`，并用 `Path.is_relative_to` 复核解析后路径
- `validate_archive_entries`：全量预校验，任一非法条目整包拒绝，不进行任何解压
- `run()`：先全量校验再 `extractall`，最后 `_ensure_required_files`（rglob 查找关键文件）
- 新增 `required_files` 构造参数

**`services/runtime_install_service.py`**：
- 新增 `NODE_TRUSTED_HOSTS = ("nodejs.org",)`
- 新增 `fetch_installer_sha256(info)`：读 `SHASUMS256.txt` 并提取对应哈希

**新增测试**：`tests/test_archive_safety.py`（11 项）

---

### 11.3 C1 — DLNA FFmpeg stderr 管道死锁

**实际改动文件**：`dlna/media_server.py`

`_serve_muxed` 改用 `tempfile.TemporaryFile()` 承接 stderr，整个转封装期间 FFmpeg 可持续写 stderr 而不阻塞；失败时从临时文件读尾部 1000 字符记录到日志。finally 块新增关闭 `process.stdout`（消除 `ResourceWarning`）。

关键注释已写入代码：
```
# stderr 必须写入临时文件而不是管道：FFmpeg 的 stderr 在整个转封装期间持续输出，
# 而这里只读 stdout，管道写满（约 64KB）后 FFmpeg 会阻塞，投屏随即卡死。
```

**新增测试**：`tests/test_dlna_media_server.py`（1 项）——子进程先写 2 MB stderr 再写 512 KB stdout，断言线程 60s 内不死锁且 stdout 字节完整。反向验证：旧 `stderr=PIPE` 路径在 8s 内确实超时（死锁复现）。

---

### 11.4 P1 — 文本折行 O(n²) 字体度量

**实际改动文件**：`ui/text_elision.py`（整文件重写）

- 模块级 `_FONT_CACHES: OrderedDict[str, _WidthCache]`，上限 8，LRU 淘汰
- `_WidthCache`（`__slots__`）：`char_width` 按单字符缓存（零宽退化为 `averageCharWidth`），`text_width` 累加求和
- 缓存键：`f"{label.font().toString()}@{label.logicalDpiX()}"` — 覆盖 DPI 变化
- `_wrap_text`：改为增量累加 `current_width`，新字符查缓存一次
- `_elide_with_three_dots`：前缀和数组 + 二分定位省略点
- 复杂度：O(n) 次度量调用（每个唯一字符仅测量一次），较原 O(n²) 下降一个数量级

**新增测试**：`tests/test_text_elision.py`（10 项）：
- `WidthCacheTests`：每字符只调用一次 `horizontalAdvance`（用计数型假 metrics 断言调用次数 == 唯一字符数）、零宽退化
- `ElisionBehaviourTests`：两行上限 + 末行 `...`、结果幂等、空白输入、零行预算、200 次折行 < 2s（性能回归守卫）
- `FormatSecondsTests`：分秒、时分秒、零值

---

### 11.5 全量测试结果

第 1 期（Critical 轮次）结束时的快照：

```
python -m unittest discover -s tests -p "test_*.py" -v
Ran 168 tests in 4.766s
OK
```

> High 轮次结束后的当前数字是 213 项，见 12.11。

新增/改造测试模块：

| 模块 | 新增项数 | 覆盖范围 |
| --- | --- | --- |
| `test_update_verify.py` | 23 | 白名单校验、哈希解析、文件校验、worker 端到端 |
| `test_archive_safety.py` | 11 | 条目校验（7 种拒绝场景）、worker 集成（穿越/缺文件/正常）|
| `test_dlna_media_server.py` | 1 | 2 MB stderr 不死锁 |
| `test_text_elision.py` | 10 | 宽度缓存调用次数、折行行为、性能回归 |
| `test_update_launch.py`（改）| 12 | 子进程创建标志回归、升级脚本内容、退出事件循环（其中 3 项随 S3 追加，`DETACHED_PROCESS` 已换成 `CREATE_NO_WINDOW`，见 12.1）|

原有 129 项测试全部保持 OK，无回归。

---

## 12. High 级实施记录（2026-07-30）

> 本节记录全部 10 个 High 项（S3、S4、C2、C3、P2、P3、P4、F1、F2、F3）的实际改动与验证结果。
> 这些项横跨第 1~5 期，因此按**严重级别**而不是期次归档。每条对应第 3~6 节里的「验证方法」。

### 12.1 S3：升级脚本改用 `-EncodedCommand`，不再落盘

**实际改动文件**：`services/update_service.py`

**新增函数**：`_build_powershell_command(script, *args)`

原实现把 PowerShell 脚本写进 `UPDATE_DIR/*.ps1`，再用 `powershell -File` 执行。`UPDATE_DIR` 位于
`%LocalAppData%`，任何以当前用户身份运行的进程都可以在「写入」与「执行」之间改写它，
也就是典型的 TOCTOU：我们校验过的内容和实际执行的内容不是同一份。

修复思路是让脚本正文根本不接触文件系统——编译成 Base64（UTF-16LE）后随命令行一起传给 PowerShell：

```python
encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
return [
    powershell, "-NoProfile", "-NonInteractive",
    "-ExecutionPolicy", "Bypass",
    "-EncodedCommand", encoded,
]
```

`launch_installer()` / `launch_portable_update()` 都改走 `_spawn_launcher()`，创建标志固定为
`CREATE_NO_WINDOW`。这里有个坑值得记下来：**不能用 `DETACHED_PROCESS`**——不分配控制台时
`powershell.exe` 会立刻退出而不执行脚本；`CREATE_NO_WINDOW` 分配的是一个不可见控制台，脚本能正常跑完。

**新增测试**：`tests/test_update_launch.py`（12 项）

---

### 12.2 S4：DLNA 中继加客户端来源限制与令牌过期

**实际改动文件**：`dlna/media_server.py`

**新增类与常量**：`_RegisteredSource`、`_TOKEN_TTL = 1800.0`、`DlnaMediaServer.authorize()`

原实现只要拿到 URL 就能拉流，且令牌永不过期——同一局域网内的任何设备都能长期访问用户正在投屏的内容。
现在 `register_source()` 用 `secrets.token_urlsafe(18)` 发令牌，并记下允许的客户端地址与
`time.monotonic() + _TOKEN_TTL` 的截止时刻；`authorize()` 三项都要过：令牌存在、来源地址匹配、未过期。

关键细节：**三种失败都返回同一个 404**，不区分「令牌不存在」和「令牌存在但你无权访问」，
否则调用方可以靠状态码差异探测令牌是否有效。

**新增测试**：`tests/test_dlna_access_control.py`（9 项）

### 12.3 C2：封面回填对已销毁控件做逐个隔离

**实际改动文件**：`ui/thumbnail_cache.py`

**新增方法**：`cancel_for(label)`、`pending_count()`

原实现在 `_handle_finished` 里顺序回填同一批等待者。页面切换后控件的 C++ 对象已被销毁，
第一个失效控件抛出的 `RuntimeError("Internal C++ object already deleted.")` 会中断整个循环：
后面的等待者拿不到图，`_in_flight` 里的包装对象也留了下来。

现在等待者用 `weakref.ref(label)` 登记，回填时逐个 `try/except RuntimeError`：

```python
try:
    ...
except RuntimeError:
    logger.debug("thumbnail target already destroyed url=%s", key[0])
```

同一张图重复请求只登记等待者、不重复发网络请求；`cancel_for()` 供页面清空卡片前主动注销，
`pending_count()` 让测试和诊断能直接读在途请求数。

**新增测试**：`tests/test_thumbnail_loading.py`（8 项）

---

### 12.4 C3：关闭时等待线程池收敛，迟到回调统一丢弃

**实际改动文件**：`ui/main_window.py`

**新增装饰器**：`_skip_after_shutdown`（约 30 个 worker 回调已挂载）

原 `closeEvent` 不等 `QThreadPool`，直接释放 DLNA 中继与 mpv，随后到达的 worker 回调会访问
已销毁的对象而崩溃。现在关闭顺序被固定为：置 `_shutting_down` → 停下载子进程 →
`thread_pool.waitForDone(SHUTDOWN_WAIT_MS)` → 才释放中继、保存配置、`mpv.shutdown()`。
超时只记 warning 并继续退出，不把用户卡在关不掉的窗口里。

`_skip_after_shutdown` 是第二道闸：即使某个回调在等待窗口之外到达，它也会被静默丢弃。
装饰器用 `functools.wraps` 保住 `__name__`，Qt 的信号连接与日志才不会变成一堆 `wrapper`。

**新增测试**：`tests/test_main_window_hardening.py::ShutdownGuardTests`（3 项）

---

### 12.5 P2：首页分批建卡 + 封面按需加载

**实际改动文件**：`ui/home_page.py`

**新增常量与方法**：`CARD_BATCH_SIZE = 12`、`_pending_videos`、`_batch_timer`、`_build_next_batch()`、`video_count()`

原实现一次性构建约 56 张卡片并同时发起全部封面请求，首屏因此明显卡顿。现在 `set_videos()`
只建第一批，其余留在 `_pending_videos`，由 interval 为 0 的单次 `QTimer` 逐批补齐；
封面则由滚动条 `valueChanged` 驱动，`_thumbnail_requested` 保证每张卡只请求一次。

行为不变是这里的硬约束：**排完所有批次后卡片的数量与顺序必须与一次性构建完全一致**，
后续批次也不得抢走 `_selected_card`。重新加载时 `_batch_timer.stop()` + 清空 `_pending_videos`，
旧批次不会串到新数据里。

**新增测试**：`tests/test_ui_render_cost.py::HomeBatchBuildTests`（5 项）

---

### 12.6 P3：播放列表只重绘变化的行

**实际改动文件**：`ui/playlist_overlay.py`

**新增状态**：`_active_row`、`_selected_rows`、`_row_widget(row)`

原实现每次选中变化都对整表做 `unpolish()/polish()`，300+ 条列表滚动明显掉帧。
现在 `set_current_index()` 只动「上一个活动行」和「新活动行」；选中态用对称差分：

```python
for row in current ^ self._selected_rows:
```

`_refresh_style()` 在状态没变时直接返回——重绘代价不低，能省就省。
`set_playlist()` 会重置这两份缓存状态，避免重建后残留上一份列表的行号。

**新增测试**：`tests/test_ui_render_cost.py::PlaylistRestyleCostTests`（5 项）

### 12.7 P4：页面惰性构造

**实际改动文件**：`ui/main_window.py`

**新增状态与方法**：`_lazy_pages: dict[str, QWidget]`、六个页面的惰性 property、`_created_page(name)`

原实现在 `__init__` 里串行构建全部 8 个页面，启动时间里有相当一部分花在用户当下看不到的页面上。
现在只有首页与播放页进 `QStackedWidget`，其余六个（playlist / download / favorite / history / settings / about）
在首次访问对应 property 时才建，建好后存进 `_lazy_pages` 复用。

`_created_page(name)` 是配套的只读查询：需要「如果页面已经建了就通知它」的场景用它，
不能因为一次状态同步就把整个页面提前建出来，那等于把惰性化又退回去了。

**新增测试**：`tests/test_main_window_hardening.py::LazyPageTests`（3 项）

---

### 12.8 F1：Bilibili 请求层接入代理设置

**实际改动文件**：`resolver/site_resolver.py`

**新增方法**：`BilibiliResolver._build_opener()`

原实现直接用 `urllib.request.urlopen`，也就是全局 opener——它不带我们的 `ProxyHandler`，
用户在设置里填的代理对 B 站请求完全无效。现在每次请求前构建自己的 opener：

```python
if proxy:
    handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
else:
    handlers.append(urllib.request.ProxyHandler({}))
```

两个细节：一是无代理时也要显式给空映射，否则 urllib 会补一个读 `http_proxy` 环境变量的默认
handler，「关闭代理」就成了空话；二是 `OpenerDirector` 非线程安全，多个 worker 不能共用，
所以每次调用都重建，而不是缓存一个实例。

**新增测试**：`tests/test_proxy_settings.py::BilibiliProxyTests`（3 项）

---

### 12.9 F2：配置代理优先于系统代理

**实际改动文件**：`services/config_service.py`

**新增常量与方法**：`PROXY_MODE_AUTO/MANUAL/OFF`、`PROXY_MODES`、`PROXY_MODE_LABELS`、`proxy_mode()`、`effective_proxy()`

原实现让系统代理静默覆盖用户填写的代理：界面上显示「已配置代理」，流量却走了另一条链路。
显式配置代表明确意图，因此 `effective_proxy()` 的判定顺序是 强制直连 → 配置代理 → 手动模式（不回退）→ 系统代理。

返回值与本文档第 6 节最初的提议略有不同，最终实现返回的是 `(来源描述, 代理地址)` 二元组，
且描述文案已本地化对齐界面：关闭模式返回 `("强制直连", "")`，手动模式未填写时返回
`("未配置代理（手动模式）", "")`，而不是提议里的 `"已禁用代理"`。`proxy_mode()` 会小写化输入，
无法识别的值退化为 auto，老配置文件不会因此启动失败。

**新增测试**：`tests/test_proxy_settings.py::ProxyModeTests`（5 项）

---

### 12.10 F3：无清晰度时不再让 `StopIteration` 逃逸

**实际改动文件**：`ui/main_window.py`

**改动方法**：`_select_default_quality(self, video) -> VideoQuality | None`

会员限定、地区限制或临时断网的视频解析出来可能一个清晰度都没有，原实现的 `next(iter(...))`
会抛出 `StopIteration`，在 Qt 槽函数里表现为一次没有任何提示的失败。

同样与最初提议有出入：提议里返回空串 `str`，最终实现返回 `VideoQuality | None`——
调用方本来就需要一个 `VideoQuality` 对象，返回空串只是把类型错误推迟到下一行。
拿到 `None` 时调用方弹中文 `QMessageBox.critical`，用户知道发生了什么。
偏好清晰度缺失时回退到第一个可用项，而不是直接失败。

**新增测试**：`tests/test_main_window_hardening.py::DefaultQualityTests`（4 项）

---

### 12.11 全量测试结果

```
python -m unittest discover -s tests -p "test_*.py"
Ran 213 tests in 57.355s
OK
```

（用时受冷启动影响较大，热跑约 12~14s，与行为无关。）

| 测试模块 | 项数 | 覆盖项 |
| --- | --- | --- |
| `tests/test_update_verify.py` | 23 | S1 下载校验、HTTPS 与主机白名单 |
| `tests/test_archive_safety.py` | 11 | S2 压缩包路径穿越 |
| `tests/test_update_launch.py`（改） | 12 | S3 `-EncodedCommand`、`CREATE_NO_WINDOW`、退出事件循环 |
| `tests/test_dlna_access_control.py` | 9 | S4 令牌来源限制、过期、统一 404 |
| `tests/test_dlna_media_server.py` | 1 | C1 remux 命令构造 |
| `tests/test_thumbnail_loading.py` | 8 | C2 失效控件隔离、去重、在途计数 |
| `tests/test_main_window_hardening.py` | 10 | C3 关闭守卫、F3 清晰度兜底、P4 惰性页面 |
| `tests/test_text_elision.py` | 10 | P1 折行结果稳定性、宽度缓存 |
| `tests/test_ui_render_cost.py` | 10 | P2 分批建卡、P3 增量重绘 |
| `tests/test_proxy_settings.py` | 8 | F1 opener 代理、F2 代理优先级 |

Critical 轮次结束时为 168 项，本轮 High 修复新增 45 项，合计 213 项，原有测试无回归。

---

**下一步：Critical 项（S1/S2/C1/P1）与全部 10 个 High 项已落地并通过全量测试（213 项 OK）。剩余 Medium/Low 项（C4~C7、P5~P21、F4~F8、S5~S7）的验收标准已逐条写入各自「验证方法」，可在你评审并确认后按第 8 节的期次推进。**

