# DLNA 投屏缺陷修复与功能增强方案（2026-07-31）

- 提出日期：2026-07-31
- 代码基线：分支 `release-lfs-fix`，`app_version.txt = 0.2.21`（v0.2.21 已发布），工作区干净
- 范围：3 个 DLNA 投屏缺陷（D1~D3）+ 6 项功能增强（E1~E6）
- 状态：**已于 2026-07-31 获批（按第 8 节推荐值执行），进入编码。实施记录见第 9 节。**
- 前序文档：`docs/code_audit_and_optimization_plan_2026-07-29.md`（第 11/12 节记录了 v0.2.21 已落地的加固项，本文不重复）
- 验证约定沿用前序文档：每条给出「自动化」（可直接复制执行的 `python -m unittest ...`）与「人工」两部分。全量回归：`python -m unittest discover -s tests -p "test_*.py"`（当前基线 213 项 OK）

## 1. 总览

| ID | 类别 | 摘要 | 主要位置 | 定性 |
| --- | --- | --- | --- | --- |
| D1 | 缺陷 | 投屏时进度条在起始点与当前点之间反复跳 | `ui/main_window.py:357`、`player/mpv_player.py:249` | **根因已确认** |
| D2 | 缺陷 | 播放几分钟后电视端未播完即中断退出 | `dlna/media_server.py:361`、`dlna/media_server.py:309` | 根因族已定位，需观测确认具体一支 |
| D3 | 缺陷 | 播放未中断但后段只有画面没有声音 | 同 D2 | 同 D2（同一根因族的另一种表现） |
| E1 | 增强 | 首页/搜索/播放列表/播放器显示视频更新时间 | `resolver/models.py`、两个 resolver、4 处 UI | 需你确认展示格式 |
| E2 | 增强 | 下载列表最新任务置顶 | `ui/download_page.py`、`ui/main_window.py:271` | 明确 |
| E3 | 增强 | 鼠标滚轮调节音量并复用音量提示 | `ui/player_page.py` | 需确认作用区域与步长 |
| E4 | 增强 | 「自动检测」改为启动时按站点逐浏览器探测 Cookie | `services/config_service.py`、新增 worker | 需确认失败提示形式 |
| E5 | 增强 | 下载列表批量暂停/启动/删除 | `ui/download_page.py`、`ui/main_window.py` | 需确认「所有」的口径 |
| E6 | 增强 | 「播放 URL」面板增加历史记录 | `ui/url_dialog.py`、`services/config_service.py` | 需确认记录时机 |

## 2. DLNA 缺陷根因分析

### 2.1 D1 — 进度条在起始点与当前点之间反复跳转（根因已确认）

**结论**：投屏期间**本地 mpv 的位置信号仍然在驱动播放器面板**，与 DLNA 远端位置轮询互相打架。这是代码层面可确定的，不需要复现即可判定。

证据链（三处代码）：

1. `ui/main_window.py:357-358` 把 mpv 的位置/时长信号**直连**到面板：
   ```python
   self.mpv.position_changed.connect(self.player_page.update_position)
   self.mpv.duration_changed.connect(self.player_page.update_duration)
   ```
2. 投屏成功后只是 `self.mpv.pause()`（`ui/main_window.py:1281`），而 `MpvPlayer.pause()` 仅设置 `pause` 属性（`player/mpv_player.py:85`）；500ms 的属性轮询定时器**没有停**，`_poll_properties` 里 `position_changed.emit(position)` 是**无条件发射**的（`player/mpv_player.py:256-257`）。于是每 500ms 面板都会收到一次「被冻结的本地位置 P」。
3. 另一条链路是 1500ms 的 `_dlna_position_timer` → `get_position` → `update_position(RelTime + _dlna_position_offset)`（`ui/main_window.py:1300-1307`），返回的是电视端真实推进的位置。

两条链路都写同一个 `update_position`（`ui/player_page.py:486`，同时改时间标签与进度条滑块），于是画面就是：每 500ms 掉回 P，每 1500ms 弹到真实位置 —— 完全对应你描述的「每次到当前播放时间点之前，都会跳回到播放起始时间点一次」。

**为什么在 B 站视频上格外明显**：B 站高清片源是分离音视频（`requires_mux=True`），此时 `_dlna_position_offset` 被设成投屏瞬间的本地位置 P，且**不做远端 seek**（`remote_seek = 0.0`），电视从 0 开始播、面板显示 `RelTime + P`。而 mpv 冻结上报的恰好就是 P —— 也就是进度条的「起始点」。单文件直投（多数 YouTube 片源）虽然同样会跳，但两条链路的数值更接近，观感上不容易察觉。**这个缺陷本身与站点无关。**

顺带同一处的第二个问题：`duration_changed` 也直连面板，本地时长 D 与远端 `TrackDuration + offset` 会互相覆盖，总时长同样在两个值之间跳。分离音视频时远端 `TrackDuration` 常常是 0 或 `NOT_IMPLEMENTED`（`dlna/controller.py:180` 会归零），所以总时长还可能被刷成 `00:00`。

### 2.2 D2 / D3 — 中途中断与后段无声（同一根因族）

先说一个关键的结构性事实，它决定了这两个现象为什么是**一体两面**：投屏分离音视频时，FFmpeg 用两路独立的 HTTP 输入（`-i 视频URL` + `-i 音频URL`），`-map 0:v:0 -map 1:a:0` 混流成 MPEG-TS 推给电视（`dlna/media_server.py:361-382`）。FFmpeg 的行为是：

- **视频那一路提前结束** → 整个 TS 输出结束 → 电视收到流末尾，表现为「没播完就退出」= **D2**
- **音频那一路提前结束** → FFmpeg 继续只写视频包直到视频结束 → 表现为「画面还在，声音没了」= **D3**

所以两个现象指向同一个问题：**上游 HTTP 输入在长时间流式读取中被中断，而当前实现对此毫无防护。** 下面是代码层面已确认的三个缺口，按影响排序。

#### 缺口 1（主因）：FFmpeg 输入没有任何重连与超时保护

`build_ffmpeg_mux_command`（`dlna/media_server.py:361-382`）拼出的命令只有 `-hide_banner -loglevel error`、代理、`-headers`、`-ss`、两个 `-i`、编码器与 `-f mpegts`。**没有 `-reconnect` 系列，没有 `-rw_timeout`/`-timeout`。**

这在投屏场景下几乎注定出问题，原因是**推流节奏由电视决定**：`_serve_muxed` 只在电视读走数据后才继续 `process.stdout.read()`（`dlna/media_server.py:334`），管道背压会让 FFmpeg 长时间停在原地不读上游。电视缓冲一填满就可能几十秒不取数据，上游 CDN 的空闲连接被回收、或者随手一个 TCP reset —— FFmpeg 默认行为是**直接结束该路输入**，不重试。视频路踩到就是 D2，音频路踩到就是 D3。「时间不定、概率很大」正是网络侧偶发事件的典型特征。

B 站的音频与视频常常来自不同 CDN 主机，两路可靠性不同，这也解释了为什么 D3（只丢音频）会单独出现。

#### 缺口 2：诊断信息被丢弃，目前处于"盲修"状态

`_serve_muxed` 只在 `return_code != 0` 时才读 stderr 尾部记日志（`dlna/media_server.py:336-342`）。而真实路径几乎都走不到那里：

- 电视断开 → `wfile.write` 抛 `BrokenPipeError` → 被 `_serve` 捕获后只记一句「client disconnected」（`dlna/media_server.py:212-213`），**stderr 整个丢掉**；
- FFmpeg 因上游 EOF 正常收尾 → `return_code == 0` → 同样不记；
- `finally` 里无条件 `_terminate_process`，`TemporaryFile` 随 `with` 关闭销毁。

也就是说，现在日志里根本看不到 FFmpeg 说了什么。这一条本身不是缺陷成因，但它是**必须先补的观测缺口**：D2/D3 具体踩的是哪一支（上游 reset / 403 过期 / 时间戳不连续 / 电视主动断开），只有 FFmpeg 的 stderr 能回答。所以方案把它排在第一步。

#### 缺口 3：DLNA 响应头与元数据不完整

代码层面可确认缺失的三项：

1. **没有 `contentFeatures.dlna.org` 响应头**，也没有响应 `getcontentFeatures.dlna.org` 请求头。`_serve_muxed` 只发了 `Content-Type` / `Accept-Ranges: none` / `transferMode.dlna.org`（`dlna/media_server.py:310-315`）。多数电视（三星/LG/索尼）依赖这个头判断流的可 seek 性与「是否为流式内容」，缺失时容易走保守路径。
2. **DIDL-Lite 的 `protocolInfo` 里没有 DLNA 标志位**，只有 `http-get:*:video/mp2t:*`（`dlna/controller.py:166`）。规范做法是补 `DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=...`。
3. **DIDL-Lite 的 `res` 没有 `duration` 属性**（`dlna/controller.py:152-168`），电视不知道总时长。这与 D1 里「总时长可能显示 00:00」是同一个信息缺失的两面，也有渲染器会因为总时长未知而在内部估计的时间点提前停止。

#### 缺口 4：令牌 30 分钟过期，长视频必然被中断

`_TOKEN_TTL = 1800`，`expires_at` 在 `register_source` 时一次性写死（`dlna/media_server.py:24,87`），`authorize()` 过期即**弹出令牌**（`dlna/media_server.py:115-119`）。电视在缓冲不足时重新发起 GET 是常见行为，一旦超过 30 分钟就会拿到 404，播放终止且不可恢复。

这一条解释不了「几分钟」的现象，但它是**投屏长视频的确定性缺陷**，应当一并修掉。

#### 已排除的可能

- **不是 stderr 管道死锁**：v0.2.21 已把 stderr 改写临时文件（前序文档 11.3），`tests/test_dlna_media_server.py` 有守卫用例。
- **不是 UI 线程阻塞**：中继跑在 `ThreadingHTTPServer` 的独立线程里。
- **不是音频编码不支持**：`-c:a copy` 只用于 aac/mp4a，其余转 `aac 192k`（`dlna/media_server.py:376-380`），不会出现「电视不认音轨」式的全程无声——而你描述的是**前面有声、后面没声**，这只能是音轨中途消失。

## 3. DLNA 解决方案

### 3.1 D1：投屏期间切断本地位置信号（确定性修复）

不再把 mpv 信号直连面板，改为经过一层槽函数，投屏中直接丢弃：

```python
# ui/main_window.py
self.mpv.position_changed.connect(self._handle_mpv_position_changed)
self.mpv.duration_changed.connect(self._handle_mpv_duration_changed)

def _handle_mpv_position_changed(self, seconds: float) -> None:
    if self._dlna_device is not None or self._dlna_cast_pending:
        return          # 投屏中由 _poll_dlna_position 单独驱动面板
    self.player_page.update_position(seconds)
```

时长同理。选择「加守卫」而不是「投屏时 disconnect / 停掉 mpv 轮询定时器」的原因：`_dlna_last_position` 在停止投屏时要用来把本地播放 seek 回去（`ui/main_window.py:1353`），mpv 轮询还承担 `pause_changed`、`playback_finished` 与 `eof-reached` 检测，停掉会牵连播放结束与自动下一集逻辑；而 `_dlna_cast_pending` 也要一并判断，否则「正在连接设备」的窗口期仍会跳。

配套把 `_dlna_position_offset` 参与的时长计算收紧：远端 `TrackDuration <= 0` 时**不覆盖**已有时长（保留本地解析出的真实时长），避免总时长被刷成 `00:00`。

### 3.2 D2/D3 第一步：先把观测补上（必须先做）

改动集中在 `dlna/media_server.py`：

1. `_serve_muxed` 的 `finally` 里**无条件**记录：FFmpeg 退出码、已转发字节数、本次会话时长、stderr 尾部 2000 字符。区分三种收尾原因并分别记日志：`client_disconnected` / `ffmpeg_eof` / `ffmpeg_error`。
2. 把 `log_message` 从 DEBUG 提到 INFO，并显式记录请求方法、`Range`、`User-Agent`、客户端 IP —— 用于回答「电视是否在重新发起 GET、带什么 Range」。
3. `build_ffmpeg_mux_command` 增加 `-progress pipe:2`（写入 stderr，即临时文件），这样日志里能看到最后一次的 `out_time`，直接判断中断发生在第几分钟。
4. FFmpeg 日志级别从 `error` 提到 `warning`（重连、时间戳不连续都是 warning 级）。

这一步的产出是：**你复现一次 D2/D3 后，`logs/` 里就能直接看出踩的是哪一支**，后续如果 3.3 没有完全解决，不必再猜。

### 3.3 D2/D3 第二步：上游输入健壮性（针对主因）

`build_ffmpeg_mux_command` 为**两路输入都**加上重连与超时（必须放在各自的 `-i` 之前，属于 per-input 选项）：

```python
_INPUT_RESILIENCE = [
    "-reconnect", "1",
    "-reconnect_streamed", "1",
    "-reconnect_on_network_error", "1",
    "-reconnect_on_http_error", "4xx,5xx",
    "-reconnect_delay_max", "10",
    "-rw_timeout", "20000000",     # 20s，单位微秒
]
```

同时在混流侧减少时间戳漂移导致电视丢音轨的可能：`-fflags +genpts`、`-max_interleave_delta 0`、`-muxdelay 0`、`-max_muxing_queue_size 4096`；转码音频（非 aac 片源）时补 `-af aresample=async=1:first_pts=0`。

**行为不变约束**：`-c:v copy` 不动（不引入转码开销），`+resend_headers` 保留，命令的其余结构不变；新增项全部是 FFmpeg 标准流式选项，不改变正常路径的输出内容。

`_serve_proxy`（单文件直投路径，`dlna/media_server.py:221-273`）同样没有任何重试：`while chunk := response.read(...)` 中途报错就整条连接结束。补一层「读取中断后按已发送字节数带 `Range` 续传」的重试（最多 3 次、指数退避），并在重试时记日志。

### 3.4 D2 第三步：DLNA 协议层补全

1. `_serve_muxed` / `_serve_proxy` / `_serve_file` 统一补 `contentFeatures.dlna.org` 响应头；流式转封装用 `DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=8D500000000000000000000000000000`，可 Range 的单文件用 `DLNA.ORG_OP=01`。
2. `build_didl_lite` 增加可选 `duration` 与 `size` 参数，`protocolInfo` 带上同一组 DLNA 标志；调用方（`ui/main_window.py:1149`、`1189`）把 `video.duration` 传进去。
3. `_TOKEN_TTL` 改为**滑动窗口**：`authorize()` 成功时顺延 `expires_at`，并把基础 TTL 提到 2 小时；`stop_streams()` 仍然立即清空令牌（S4 的访问控制语义不变，`tests/test_dlna_access_control.py` 必须保持全绿）。

### 3.5 关于「彻底方案」的说明

如果补齐 3.2~3.4 后 D2 仍偶发，日志会指向剩下两条路，届时再单独决策，本轮不做：

- **本地先落盘再投屏**：把转封装结果写成本地 TS 文件，电视从本地文件取流（可 Range、可 seek，彻底摆脱上游抖动），代价是要等一段缓冲、占磁盘。
- **改用 HLS 分片**：中继输出 m3u8 + 分片，单片失败只影响几秒。改动最大，兼容性也最杂。

## 4. 功能增强分析与方案

### 4.1 E1 — 各处显示视频更新时间

**现状**：`VideoInfo.upload_date` 已有（yt-dlp 的 `YYYYMMDD`，`resolver/models.py:112`），但**列表用的 `HomeVideo` 与 `PlaylistEntry` 没有这个字段**，数据库 `playlist_item` 表也没有对应列。

**数据可得性（这是本项最需要你知情的约束）**：

| 场景 | 数据源 | 可得性 |
| --- | --- | --- |
| 播放器页 | `VideoInfo.upload_date`（完整解析） | ✅ 一直可得 |
| B 站首页/搜索/空间/收藏 | 各 API 的 `pubdate` / `senddate` / `ctime`（`resolver/site_resolver.py` 现在直接丢掉了） | ✅ 可得，只是没解析 |
| YouTube 首页/搜索/播放列表 | yt-dlp `--flat-playlist`（`resolver/youtube_resolver.py:418,460`）的 `timestamp` / `release_timestamp` / `upload_date` | ⚠️ **经常缺失**。扁平模式下 YouTube 多数 tab 与搜索结果不带日期 |

要让 YouTube 列表也一定有日期，只能对每条结果再做一次完整解析 —— 一页 56 条就是 56 次 yt-dlp 调用，与 v0.2.21 刚做完的首页性能优化直接冲突。**因此方案是：拿得到就显示，拿不到就留空**，不为此付性能代价。

**方案**：

1. `HomeVideo` / `PlaylistEntry` 新增 `upload_date: str = ""`，统一存 `YYYYMMDD`（与 `VideoInfo` 一致，便于排序与格式化）；`PlaylistEntry.to_home_video()` 一并传递。
2. `playlist_item` 表加 `upload_date` 列，走现有 `_ensure_column` 自动迁移（`database/sqlite_manager.py:121`），老库无痛升级。
3. B 站侧在 `_home_video_from_*` 三个构造点解析 `pubdate`/`senddate`/`created`（unix 秒 → `YYYYMMDD`）；YouTube 侧在 `_parse_home_entry` / `_parse_playlist_entry` 读 `upload_date` → `timestamp` → `release_timestamp`。
4. 新增 `ui/text_elision.py::format_upload_date(value) -> str`（与 `format_seconds` 同处，F8 的整合方向一致），空值返回 `""`。
5. 展示位置（4 处）：
   - 首页/搜索卡片：`ui/home_page.py:72-77` 的 `meta` 列表追加一项 → `作者 | 12:34 | 2026-07-28`
   - 播放列表面板：`ui/playlist_overlay.py:118-122` 的 meta 追加
   - 播放列表页：`ui/playlist_page.py:72` 表头在「时长」后插入「更新时间」列
   - 播放器页：`ui/player_page.py:396` 的 meta 追加 `| 更新 2026-07-28`

**需你确认**：格式用**绝对日期 `2026-07-28`**（推荐，无歧义、可排序）还是**相对时间「3 天前」**？拿不到日期时显示空白（推荐）还是 `—`？

### 4.2 E2 — 下载列表最新任务置顶

**现状**：`DownloadPage.add_task` 用 `row = self.table.rowCount()` 追加到末尾（`ui/download_page.py:103`），启动时 `main_window` 按 `download_manager.tasks()` 的原始顺序（= JSON 里的旧→新）逐条 add（`ui/main_window.py:271-272`），所以最旧的在最上面。

**方案**：

1. `add_task` 改为 `self.table.insertRow(0)`，并把 `_rows` 里所有已有行号 +1（现在只有 `remove_task` 做了行号维护，插入侧必须同样处理，否则行号全错）。
2. 启动填充改为按 `created_at` 降序排一次再逐条 add（等价于倒序遍历），保证「文件里的顺序」与「表格里的顺序」都是新→旧。
3. 排序**只在插入时决定**，任务状态变化不重排 —— 下载中的任务不会在表里跳动。

**顺带发现**：`_task_id_for_row` / `_apply_filter` 都是按 `_rows` 线性扫描，行号维护出错会直接表现为「点了 A 的按钮却操作了 B」。这一项虽小，但必须有单测守门（见 6.2）。

### 4.3 E3 — 鼠标滚轮调节音量

**需求理解**：原文「鼠标滚轮的开机键」按上下文应为「**快捷键**」的笔误 —— 即滚轮上滚增大音量、下滚减小音量，并复用键盘调音量时那个屏幕中央的半透明提示（v0.2.19 引入的 `_show_shortcut_hint`，`ui/player_page.py:542`）。**若我理解有误请指出。**

**方案**：在 `PlayerPage.eventFilter` 已有的 `QEvent.Type.Wheel` 分支（`ui/player_page.py:303`）上做，而不是新写 `wheelEvent`：现有分支已经在处理滚轮（用于唤出鼠标指针），直接在同处接管即可。

```python
if event.type() == QEvent.Type.Wheel and self._wheel_volume_target(watched):
    notches = event.angleDelta().y() / 120.0
    if notches:
        self._shortcut_volume(int(notches) * VOLUME_WHEEL_STEP)
    return True          # 吃掉事件，避免继续传给父级滚动区域
```

复用 `_shortcut_volume`（`ui/player_page.py:786`）可以一次拿到三件事：0~100 夹取、投屏且设备不支持音量时忽略、以及音量提示 —— 与键盘路径完全一致，不会出现两套行为。

**不能被接管的控件**（否则会破坏现有交互）：音量滑块自身（QSlider 原生支持滚轮）、清晰度/字幕/倍速下拉框（QComboBox 原生滚轮切项）、播放列表面板的滚动区域（要能滚动列表）、进度条。实现上用白名单：只有 `video_widget`、播放器页空白区与控制面板背景才接管。

**需你确认**：每格滚轮的步长用 **5**（与键盘 `Z/X` 之外的 `volume_up/down` 一致，推荐）还是 2？控制面板背景要不要也接管（推荐要，因为全屏时鼠标常停在下方）？

### 4.4 E4 — 「自动检测」改为按站点逐浏览器探测 Cookie

**现状与问题**：`cookie_browser()` 在 `auto` 模式下调 `detect_browser_cookie_source()`，而后者是 `detect_browser_cookie_sources()[0]` —— **只取探测到的第一个浏览器**（`services/config_service.py:196-205, 278-281`），然后把它交给 yt-dlp 的 `--cookies-from-browser`。这意味着：

- 「装了 Edge 但只在 Chrome 登录了 B 站」时，自动模式选中 Edge，B 站就是未登录状态；
- 没有**按站点**的概念，YouTube 与 B 站被迫共用同一个浏览器；
- 只有在请求**失败之后**才会靠 `_try_with_browser_cookies`（`resolver/youtube_resolver.py:353`）逐个浏览器重试，代价是一次失败 + 多次重试的延迟。

**方案：启动时按站点探测，选出"确实登录过"的浏览器**

关键技术点：**判断某浏览器是否登录过某站点，不需要解密 Cookie**。Chromium 的 `Cookies` 库里 `host_key` 与 `name` 列是明文（只有 `value` 加密），Firefox 的 `moz_cookies` 同理。因此把库文件复制到临时目录、以只读方式打开，查一条 COUNT 就够：

```python
# Chromium
SELECT COUNT(*) FROM cookies
WHERE host_key LIKE ? AND name IN ('SESSDATA', 'bili_jct', 'DedeUserID')
# Firefox
SELECT COUNT(*) FROM moz_cookies WHERE host LIKE ? AND name IN (...)
```

登录态标志 Cookie：B 站 `SESSDATA` / `bili_jct` / `DedeUserID`；YouTube `SID` / `SAPISID` / `__Secure-3PAPISID` / `LOGIN_INFO`。

**这一点值得单独说明**：探测过程**只读 host 与 name，不读也不解密 value**，因此不会接触到任何凭据内容 —— 这与前序审计文档 S7 对「遍历所有浏览器 Cookie 库」的顾虑并不冲突。复制到临时文件再打开是为了绕开浏览器运行时对库文件的独占锁（yt-dlp 也是这么做的），用完即删。

实现结构：

1. 新增 `services/cookie_probe_service.py`：`probe_site_cookie_browsers(sites) -> dict[str, str]`，纯函数、可离线单测（平台/HOME/浏览器列表全部可注入）。
2. 新增 `workers/cookie_probe_worker.py`（`QRunnable` + `WorkerSignals`，遵循既有 worker 规范），启动后异步跑，**不阻塞首屏**。
3. `ConfigService` 新增 `cookie_browser_for_site(site)`：优先用户显式配置 → 探测结果（`cookies.<site>.auto_browser`）→ 空。原 `cookie_browser()` / `auto_cookie_browser()` 保留，内部改为按站点分派，`download/command_builder.py:104-113` 与 `youtube_resolver` 的三处命令构建改调按站点版本。
4. 两个站点都没找到 → 中文 toast + 设置页显著提示「未在任何浏览器中找到 X 的登录 Cookie，请手动配置 Cookie」；设置页加一个「重新检测」按钮，不必重启。
5. 现有的失败重试链路（`_try_with_browser_cookies`、`should_retry_with_cookie_file`）**保留**作为兜底。

**需你确认**：探测结果要不要写进 `user_config.json` 持久化（推荐要，这样下次启动即使探测失败也有上次的结果可用）？「一个都没找到」的提示用 toast（不打断）还是模态对话框（推荐 toast + 设置页常驻提示，启动时弹窗很烦）？

### 4.5 E5 — 下载列表批量暂停/启动/删除

**现状**：每行 4 个按钮，逐条操作（`ui/download_page.py:115-135`）；表格 `SelectionBehavior.SelectRows` 但**没设 `SelectionMode`**，默认是单选，无法多选。

**方案**：

1. 表格改 `SelectionMode.ExtendedSelection`（与 `ui/playlist_page.py:75` 一致，Ctrl/Shift 多选）。
2. 标题下方加一行 6 个按钮：`暂停选中 | 暂停所有 | 启动选中 | 启动所有 | 删除选中 | 删除所有`。
3. 新增三个批量信号 `pause_tasks_requested(list)` / `start_tasks_requested(list)` / `delete_tasks_requested(list)`，`DownloadManager` 侧加对应的批量方法，内部**一次落盘、一次调度**（现有 `delete_task` 每条都 `_save_tasks()` + `_schedule()`，删 20 条就是 20 次写盘 20 次调度）。
4. 状态过滤沿用单条按钮的规则：暂停只对 `queued`/`downloading` 生效，启动只对 `paused`/`failed` 生效，删除全状态可用；批量时**跳过不适用的项而不是报错**，操作完 toast 汇报「已暂停 3 个任务，跳过 2 个」。
5. 「删除所有」「删除选中」（≥2 条时）弹中文确认框。**注意**：现有 `DownloadManager.delete_task` 只删任务记录、**不删本地文件**（`download/download_manager.py:116-130`），确认文案要如实说明这一点，不要让用户以为文件也被删了。
6. 按钮可用性随选中项与任务集合动态更新（无选中时「选中」类按钮禁用，无任务时「所有」类禁用）。

**需你确认（重要）**：「所有」的口径 —— 是**表格里的全部任务**，还是**当前搜索筛选后可见的任务**？我推荐后者：下载页有搜索框（`ui/download_page.py:50`），用户搜完再点「删除所有」时，期望删的几乎一定是眼前这些；按钮 tooltip 会写明当前口径与条数。

### 4.6 E6 — 「播放 URL」面板增加历史记录

**现状**：`UrlPlayDialog` 只有一个输入框（`ui/url_dialog.py`，49 行），无任何历史。

**方案**：

1. 对话框加一个「最近播放」列表（`QListWidget`）：单击填入输入框，双击=填入并直接播放；每项显示 `标题（有则用标题，否则用 URL）` + 副行小字显示 URL；右键菜单「删除此条 / 清空历史」。对话框高度从 140 放大到约 380。
2. 存储用 `ConfigService`：`player.recent_urls`，`list[{url, title, played_at}]`，**上限 20 条，按 URL 归一化去重，最新在前**。选 config 而不是新建 SQLite 表的理由：只需要「最近 N 条」这一种读法，没有查询/统计需求，config 已有 `set`/`save` 通路，改动面最小；若后续要做「按站点筛选/统计播放次数」，再迁到 `database/` 也不会浪费（届时只需搬存储层）。
3. 记录时机：**提交即记录**（此时只有 URL，`title` 留空），解析成功后回填 `title`。这样解析失败的 URL 也留在历史里可以再试一次 —— 直接投屏/网络抖动导致的失败很常见，若只在成功时记录，用户反而要重新粘贴。
4. 与「历史」页的关系：**互不替代**。历史页记录的是所有播放过的视频（含首页/搜索进入的），这里只记录从 URL 面板输入过的地址，语义不同，不做合并。

**需你确认**：上限 20 条是否合适？要不要在设置页提供「清空 URL 历史」入口（对话框内已有右键清空）？

## 5. 实施分期

每期独立提交、独立可回滚，期末跑全量单测 + 手工冒烟。

| 期次 | 内容 | 理由 |
| --- | --- | --- |
| 第 1 期 | **D1** | 根因已确认、改动小（约 20 行）、体感最直接，先交付 |
| 第 2 期 | **D2/D3 观测（3.2）** | 必须先有日志才能确认后续修复是否命中；单独一期便于让你复现一次拿日志 |
| 第 3 期 | **D2/D3 加固（3.3 + 3.4）** | 输入重连 + 协议头 + 令牌滑动窗口，一次性覆盖已确认的全部缺口 |
| 第 4 期 | **E2 + E3 + E6** | 三项都局限在单个 UI 文件，互不影响，一期做完 |
| 第 5 期 | **E1** | 跨 model / 两个 resolver / DB 迁移 / 4 处 UI，单独一期 |
| 第 6 期 | **E4 + E5** | E4 有新 service + 新 worker，E5 涉及 manager 批量接口，都需要较完整的测试 |

如果你希望先看到功能而不是先修投屏，第 4~6 期可以整体提到第 2 期之前 —— D1 建议无论如何都排第一，因为它是一行守卫就能解决的确定性缺陷。

## 6. 验证方法

### 6.1 D1 进度条

- 自动化：新增 `tests/test_dlna_cast_progress.py`（约 5 项）。用 `SimpleNamespace` 假 state 直接调用未绑定的 `MainWindow._handle_mpv_position_changed`，断言：(1) 未投屏时透传给 `player_page.update_position`；(2) `_dlna_device` 非空时**一次都不调用**面板方法；(3) `_dlna_cast_pending` 为真时同样丢弃（连接设备的窗口期）；(4) 时长同理；(5) 远端 `TrackDuration <= 0` 时不覆盖已有时长（防止总时长被刷成 0）。
- 人工：投一部 B 站分离音视频视频，盯住进度条与时间标签 60 秒 —— 应当**只单调前进**，不再回跳；总时长显示正确且不闪。然后点「停止投屏」，本地播放应从电视上的位置继续（这条是回归守门：3.1 的改动不能破坏 `_dlna_last_position`）。再用 YouTube 单文件片源重复一次。

### 6.2 E2 下载列表顺序

- 自动化：`tests/test_download_page_order.py`（约 6 项）。断言：(1) 连续 add 3 个任务后表格从上到下是「第 3、第 2、第 1」；(2) **每个 task_id 的行号与其按钮所在行严格对应**（插入时行号维护正确 —— 这是最容易错的地方）；(3) 删除中间一行后其余行号仍正确；(4) `update_task` 不改变行序；(5) 启动填充按 `created_at` 降序；(6) 搜索筛选后行号映射不乱。
- 人工：新建 3 个下载任务，确认最新的始终在最上面且下载中的行不会跳动；对第 2 行点「暂停」，暂停的必须是第 2 行那个任务。

### 6.3 D2/D3 加固

- 自动化：扩充 `tests/test_dlna_media_server.py`。(1) `build_ffmpeg_mux_command` 的**两路输入各自**都带 `-reconnect`/`-rw_timeout`，且这些选项出现在对应的 `-i` **之前**（顺序错了 FFmpeg 会当输出选项忽略）；(2) aac 片源仍是 `-c:a copy`、非 aac 仍转 `aac 192k`（行为不变守门）；(3) `contentFeatures.dlna.org` 出现在三条 serve 路径的响应头里；(4) `build_didl_lite` 传入 duration 时 `res` 带 `duration` 属性、`protocolInfo` 带 DLNA 标志，不传时保持旧输出（兼容守门）；(5) 令牌滑动窗口：`authorize` 成功后 `expires_at` 顺延，`stop_streams()` 后立即失效；(6) `_serve_proxy` 上游中途抛错时按已发字节数带 `Range` 续传，最终字节流与不中断时**完全一致**。`tests/test_dlna_access_control.py` 9 项必须保持全绿。
- 人工（这一步需要你配合，因为只有真实电视能验证）：
  1. 投一部 **20 分钟以上的 B 站分离音视频**片源，从头看到尾，确认不中断、全程有声。
  2. 中途故意制造一次网络抖动（拔一下网线/切一次 Wi-Fi 约 5 秒），播放应能自行恢复而不是终止。
  3. 投一部 **40 分钟以上**的视频（验证令牌 30 分钟过期已修）。
  4. 无论成功失败，把 `logs/` 里 `tube_player.dlna.http` 的记录发我 —— 3.2 之后日志会明确写出收尾原因（`client_disconnected` / `ffmpeg_eof` / `ffmpeg_error`）、退出码、已转发字节、最后的 `out_time` 与 FFmpeg stderr 尾部。

### 6.4 E1 更新时间

- 自动化：`tests/test_upload_date.py`（约 8 项）。(1) `format_upload_date` 覆盖正常值、空值、非法值；(2) B 站三个构造点从 `pubdate`/`senddate`/`created` 正确换算 `YYYYMMDD`（用固定 unix 时间戳，避免时区漂移 —— 统一按本地时区换算并在用例里固定）；(3) YouTube 侧 `upload_date` → `timestamp` → `release_timestamp` 的回退顺序；(4) 三者都缺失时为空串且**不抛异常**；(5) `playlist_item` 新列自动迁移：用旧 schema 建库后再初始化，断言列已加且旧数据可读。
- 人工：B 站首页/搜索/播放列表/播放器四处都应显示日期，与网页上的发布时间一致；YouTube 侧日期缺失时布局不能塌（meta 行不留下多余的 `|`）。

### 6.5 E3 滚轮音量

- 自动化：`tests/test_wheel_volume.py`（约 6 项）。用离屏 `QApplication` 构造 `QWheelEvent` 投给 `eventFilter`，断言：(1) 视频区上滚音量 +步长、下滚 -步长；(2) 触发了音量提示（`shortcut_hint` 可见且文案含「音量」）；(3) 0/100 边界不越界；(4) 投屏且设备不支持音量时忽略；(5) **音量滑块、下拉框、播放列表滚动区域上的滚轮不被接管**（`eventFilter` 返回 False，事件继续传递 —— 这是防止破坏现有交互的守门用例）；(6) 无媒体时不响应。
- 人工：窗口模式与全屏各滚一次，音量与提示都要正常；在音量滑块上滚动应只走滑块原生行为（不出现「一格变两倍」）；在播放列表面板上滚动必须仍是滚列表。

### 6.6 E4 Cookie 探测

- 自动化：`tests/test_cookie_probe.py`（约 10 项）。全部用临时目录伪造浏览器目录与 SQLite 库（真建 `cookies` / `moz_cookies` 表，只塞 `host_key`+`name`，不需要真实加密值）。断言：(1) 只有 Chrome 有 B 站登录 Cookie 时选中 Chrome；(2) B 站与 YouTube 可以**选中不同浏览器**；(3) 一个都没有时返回空并给出可读原因；(4) 库文件被独占锁定时走「复制到临时文件再读」且不抛异常；(5) 探测**从不读取 `value` 列**（用只暴露 host/name 的假 cursor 断言查询语句里不含 `value`）；(6) Firefox 分支；(7) 显式配置的浏览器优先于探测结果；(8) 探测结果持久化后下次启动可直接命中；(9) 非法/缺失的 profile 目录被跳过而不是中断整轮；(10) 探测耗时上限（单浏览器 < 200ms 的桩计时）。
- 人工：只在 Chrome 登录 B 站、只在 Edge 登录 YouTube，重启应用 —— 两个站点都应处于登录态（B 站能看到高清清晰度、YouTube 能拉到需要登录的内容）；把两个浏览器的登录都退掉再重启，应看到中文提示引导手动配置 Cookie，且**应用其余功能不受影响**。

### 6.7 E5 批量操作

- 自动化：`tests/test_download_batch.py`（约 8 项）。断言：(1)「暂停选中」只对 `queued`/`downloading` 生效、跳过其余并如实汇报条数；(2)「启动选中」只对 `paused`/`failed` 生效；(3)「所有」按确认口径取任务集合；(4) 批量删除 20 条只触发**一次**落盘与一次调度（把 `_save_tasks`/`_schedule` 打成计数桩）；(5) 空选中时按钮禁用；(6) 批量操作后表格行号与 task_id 映射仍正确；(7) 批量删除不触碰本地文件（断言文件仍存在）；(8) 单条按钮的原有行为不回归。
- 人工：建 5 个任务，多选 3 个暂停/启动，确认只有这 3 个变化；「删除所有」要先弹确认框，取消后一条都不能少；确认删除后检查磁盘上已下载的文件仍在。

### 6.8 E6 URL 历史

- 自动化：`tests/test_url_history.py`（约 7 项）。断言：(1) 记录后出现在列表首位；(2) 重复 URL 归一化去重且被提到最前而不是重复两条；(3) 超过 20 条时淘汰最旧的；(4) 解析成功后 `title` 被回填到对应条目；(5) 删除单条/清空生效并落盘；(6) 老配置文件里没有 `player.recent_urls` 时不报错；(7) 历史里的非法条目（缺 url）被忽略。
- 人工：连续用 URL 面板播 3 个地址，重开面板应看到 3 条、最新在最上、标题正确；双击一条能直接播放；故意输一个错误 URL，应留在历史里可再试。

## 7. 风险与约束

- **D2/D3 无法在编码阶段自证**：本地单测只能验证命令构造、响应头与续传逻辑，「电视上能连续播完 40 分钟」只有真机能验证。因此把观测（3.2）单独排一期，并在 6.3 里明确需要你配合复现取日志。如果 3.3 之后仍偶发，日志会指向 3.5 里的两条后备方案，届时再决策。
- **行为不变原则**（沿用前序文档）：`-c:v copy` 不变、aac 片源仍 `copy`、DIDL 在不传 duration 时输出与现在一致、S4 访问控制语义与 `tests/test_dlna_access_control.py` 不动、下载任务的最终数据内容不因 E2/E5 发生变化。每项都以单测固定基线。
- **YouTube 列表拿不到更新时间是数据源限制**，不是实现取巧。要强行拿到就得每条再解析一次，与 v0.2.21 的首页性能优化直接冲突，本轮不做。
- **E4 会读取所有已安装浏览器的 Cookie 库文件**（只读 host/name 列、不解密 value、临时副本用完即删）。这是需求本身要求的行为，实现上会把「不接触 value」写成单测守门。
- **不引入新依赖**：Cookie 探测用标准库 `sqlite3` + `shutil`，不引入 `browser-cookie3` 之类的包，避免影响 PyInstaller 打包体积与 spec。
- **约定遵循**：`from __future__ import annotations`、`logging.getLogger("tube_player.<area>")` + 惰性 `%` 格式化、阻塞逻辑走 `QRunnable` + `WorkerSignals`、路径统一从 `app_paths` 取、配置统一走 `ConfigService`、用户可见文案与日志保持中文。
- **本轮不顺手做的事**：前序审计文档里剩余的 Medium/Low 项（C4~C7、P5~P21、F4~F8、S5~S7）不在本方案范围内，除非与上述改动直接相邻（例如 E1 会顺带把 `format_upload_date` 放到 `ui/text_elision.py`，与 F8 的整合方向一致）。

## 8. 需要你确认的决策点

编码前需要你拍板的 8 个点，括号内是我的推荐：

1. **E1 时间格式**：绝对日期 `2026-07-28`（推荐）/ 相对时间「3 天前」；拿不到时留空（推荐）/ 显示 `—`
2. **E1 范围**：只做需求列的四处（首页-搜索/播放列表/播放器，推荐），还是收藏页与历史页也加？
3. **E3 理解确认**：「鼠标滚轮的开机键」= 滚轮调音量的快捷操作（我按这个理解写的方案）
4. **E3 步长与区域**：每格 5（推荐）/ 2；控制面板背景是否也接管（推荐接管）
5. **E4 持久化与提示**：探测结果写入 `user_config.json`（推荐）/ 只在内存；找不到时 toast + 设置页常驻提示（推荐）/ 启动弹模态框
6. **E5「所有」的口径**：当前筛选可见的任务（推荐）/ 表格里的全部任务
7. **E6 上限与入口**：20 条（推荐）；设置页是否加「清空 URL 历史」
8. **期次顺序**：按第 5 节（D1 → 观测 → 投屏加固 → 功能）执行（推荐），还是先做功能增强？

只要 3、4、6 三条（会直接改变交互）明确，其余按推荐值执行也可以 —— 你回一句「按推荐来」我就开工。
