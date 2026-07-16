# Tube_player 在线视频作者作品自动播放列表增强方案

状态：方案已审核通过，编码与非联网验证已完成  
备案日期：2026-07-15

## 1. 背景与目标

当前应用在完成 YouTube 或 Bilibili 单视频解析后，只加载并播放当前视频。除非用户原本从播放列表进入，否则播放器右侧没有与当前制作者相关的连续浏览内容。

本轮目标：

1. 通过非本地方式播放 YouTube 或 Bilibili 单个在线视频时，优先开始当前视频播放。
2. 当前视频开始播放后，在后台获取该制作者的其他视频。
3. 将当前视频和作者其他视频组合为临时动态播放列表，加载到现有播放器右侧播放列表面板。
4. 作者列表获取、解析失败或响应较慢时，不暂停、不重载、不跳转当前视频，也不阻塞播放器操作。

## 2. 现状与约束分析

### 2.1 当前解析数据

`VideoInfo` 已包含：

- `source_site`
- `uploader`
- `channel_id`
- `webpage_url`
- `raw_info`

但当前缺少跨 YouTube/Bilibili 统一的“制作者 ID + 制作者主页 URL”字段。尤其 Bilibili 的制作者 MID 更常出现在 yt-dlp 的 `uploader_id` 或原始信息中，不能只依赖现有 `channel_id`。

### 2.2 当前播放链路

当前单视频流程为：

1. `ResolverWorker` 后台解析视频。
2. `MainWindow._resolved()` 选择清晰度。
3. 调用 `mpv.load()` 开始播放。
4. 更新历史、收藏和播放器 UI。

作者视频不能加入这条同步链路。YouTube 频道作品提取或 Bilibili 空间投稿接口可能较慢、需要 Cookie/WBI 签名，若在 `mpv.load()` 前执行，会增加首帧等待时间。

### 2.3 当前播放列表上下文

主窗口只维护一个活动播放列表。若当前视频来自用户明确选择的 YouTube playlist、Bilibili 合集/多 P/收藏夹、已保存列表，直接用作者列表覆盖会破坏原列表顺序、当前索引、自动连播和保存语义。

因此第一版约定：

- 独立单视频播放：允许创建作者动态播放列表。
- 显式播放列表内播放：保留原播放列表，不创建或覆盖作者列表。
- 作者动态列表中切换视频：继续沿用当前作者列表，不重复覆盖或并发抓取同一作者。

### 2.4 “不影响当前播放”的资源约束

即使作者列表在 Worker 中获取，批量加载 50 个缩略图仍可能和视频流竞争网络。当前 `PlaylistItemWidget` 创建时会请求缩略图，因此本功能需要同时将播放列表缩略图调整为按面板打开/可见项懒加载。

## 3. 完善后的需求

### 3.1 适用范围

触发作者列表加载：

1. 从首页或搜索结果播放单个在线视频。
2. 从收藏页或历史页播放单个在线视频。
3. 通过“播放 URL”输入独立 YouTube/Bilibili 视频 URL。
4. 从已生成的作者动态列表切换视频时继续使用该列表。

不触发或不覆盖：

1. 本地文件播放。
2. YouTube playlist、mix、album 或携带有效 playlist 上下文的视频。
3. Bilibili 多 P、合集、番剧、课程、收藏夹、稍后再看等显式列表。
4. 用户加载的本地已保存播放列表。
5. 无法确定制作者 ID/主页的在线视频。

### 3.2 作者动态列表内容

第一版列表规则：

1. 列表标题使用“`<制作者名称> 的视频`”。
2. `source_type` 使用 `creator`，用于区别普通 playlist 和本地保存列表。
3. 当前正在播放的视频固定为第一项并高亮。
4. 后续加入最多 50 个该制作者的近期视频，按站点返回的发布时间倒序排列。
5. 排除与当前视频相同的条目，再统一去重。
6. YouTube 使用视频 ID 去重；Bilibili 使用 BV/AV 稿件 ID 去重，多 P 当前页视为同一稿件，避免同一投稿重复出现。
7. 缺少有效 URL、ID、标题或已明确删除/私有的条目不加入列表。
8. 作者接口只返回当前视频、没有其他可用视频时，不创建只有一项的作者列表。
9. 动态列表允许使用现有“保存”“下载选中”“下载全部”能力；保存后按普通已保存列表处理。

### 3.3 播放与自动连播

1. 作者列表后台加载期间，当前视频持续播放，播放/暂停、拖动、清晰度和字幕操作不受限制。
2. 作者列表成功后只更新播放列表上下文和右侧面板，不主动打开面板、不改变播放位置、不重新调用 `mpv.load()`。
3. 作者列表默认沿用现有自动连播开启状态。
4. 当前视频自然结束且自动连播开启时，播放作者列表中的下一项。
5. 用户关闭自动连播后，当前视频结束时停留在结束状态。
6. 用户双击作者列表条目时，复用现有单视频解析播放流程。

### 3.4 并发与过期结果

1. 每次独立在线视频播放生成递增的作者列表请求令牌。
2. 作者 Worker 延迟约 1.5 秒启动，让当前视频优先建立播放和缓冲。
3. 启动前以及结果回调时都校验请求令牌和当前视频 ID。
4. 用户切换视频、停止播放、开始本地播放或激活显式播放列表时，旧令牌立即失效。
5. 已运行 Worker 无需强制终止，但其过期结果不得更新 UI 或播放列表。
6. 同一制作者结果缓存 10 分钟；缓存键包含站点、制作者 ID、数量限制和 Cookie/代理配置指纹。

### 3.5 加载与失败反馈

1. 作者列表加载不调用 `PlayerPage.set_loading(True)`，避免把当前播放误显示为解析中。
2. Worker 使用线程池低优先级任务，只抓取扁平元数据，不下载视频、音频或字幕。
3. 播放列表面板保持关闭，用户原有操作焦点不改变。
4. 作者列表成功后，右侧热点可正常唤出面板。
5. 当前有效请求失败时使用非阻塞 toast 提示“作者视频列表加载失败，当前视频继续播放”，同时记录详细日志。
6. 过期请求失败只记录调试日志，不显示 toast。
7. 不使用模态错误框，不停止或重试当前媒体。

## 4. 站点获取方案

### 4.1 YouTube

制作者身份来源优先级：

1. `raw_info.channel_url`
2. `raw_info.uploader_url`
3. `channel_id` / `uploader_id`

若只有频道 ID，构造：

```text
https://www.youtube.com/channel/<channel_id>/videos
```

若得到频道主页或 `@handle` URL，规范化为其 `/videos` 页面。

使用现有 yt-dlp 进程调用模式新增频道扁平提取命令：

```text
yt-dlp --dump-single-json --flat-playlist --playlist-end 50 --skip-download <channel-videos-url>
```

继续复用代理、Cookie、浏览器 Cookie 轮换、JS runtime、超时、日志脱敏和 cookie 文件兜底策略。

只读取条目 ID、标题、URL、作者、时长、封面和可用性，不解析每一条视频的真实媒体地址。

### 4.2 Bilibili

制作者身份使用 MID，来源优先级：

1. `raw_info.uploader_id`
2. `raw_info.channel_id`
3. `raw_info.owner.mid` 等已知原始字段
4. 解析阶段可从视频详情接口补充的 owner MID

首选 Bilibili WBI 空间投稿接口：

```text
GET https://api.bilibili.com/x/space/wbi/arc/search
    ?mid=<MID>&pn=1&ps=50&order=pubdate
```

复用当前 `BilibiliResolver` 的 WBI 签名、Cookie、代理、请求头和错误处理。将 `vlist` 条目转换为 `PlaylistEntry`。

若公开/WBI 接口因字段变化或风控失败，可使用 yt-dlp 对以下地址进行扁平提取作为兜底：

```text
https://space.bilibili.com/<MID>/video
```

接口和兜底均失败时只报告作者列表失败，不影响当前 mpv 播放。

## 5. 设计方案

### 5.1 数据模型

在 `VideoInfo` 增加跨站点字段：

- `creator_id: str`
- `creator_url: str`

保留现有 `uploader` 作为显示名称，`channel_id` 保持兼容。解析阶段统一从 yt-dlp 原始字段中归一化作者身份，避免主窗口直接读取站点特定 `raw_info`。

`PlaylistInfo` 不需要新增结构字段，使用：

- `playlist_id = "<site>:creator:<creator_id>"`
- `source_type = "creator"`
- `webpage_url = creator_url`
- `current_video_id = 当前视频 ID`

### 5.2 Resolver

在 `SiteResolver` 增加统一入口：

```python
def resolve_creator_playlist(self, video: VideoInfo, limit: int = 50) -> PlaylistInfo | None:
    ...
```

内部按 `video.source_site` 分发到：

- `YoutubeResolver.resolve_creator_playlist(...)`
- `BilibiliResolver.resolve_creator_playlist(...)`

统一入口负责缓存、当前项插入、去重、数量限制和最终 `PlaylistInfo` 组装；站点 Resolver 只负责作者身份解析和扁平作品条目获取。

### 5.3 Worker

新增：

- `workers/creator_videos_worker.py`

职责：

1. 接收解析完成的 `VideoInfo`、数量限制和 Resolver。
2. 在线程池中执行作者作品元数据获取。
3. 发出 `success(PlaylistInfo | None)`、`error(str)`、`finished()`。
4. 不直接访问播放器和 UI。

### 5.4 主窗口协调

`MainWindow` 增加作者请求代次和启动/回调方法：

- `_creator_playlist_generation`
- `_schedule_creator_playlist(video)`
- `_creator_playlist_loaded(generation, video_id, playlist)`
- `_creator_playlist_failed(generation, video_id, message)`
- `_invalidate_creator_playlist_request()`

调用顺序：

1. `_resolved()` 先完成 `mpv.load()`、播放状态和历史记录更新。
2. 确认当前不是显式播放列表上下文。
3. 生成新令牌，使用 `QTimer.singleShot(1500, ...)` 延迟启动低优先级 Worker。
4. 成功回调再次校验令牌、当前视频 ID、非本地状态和播放列表优先级。
5. 校验通过后调用 `_activate_playlist(..., current_index=0, auto_play_next=True)`。

停止、本地播放、显式播放列表加载和新的独立 URL 播放均使旧令牌失效。

### 5.5 播放列表面板与缩略图

为避免列表应用瞬间发起大量图片请求：

1. `PlaylistItemWidget` 创建时只设置文本和封面占位。
2. 增加一次性的 `ensure_thumbnail_loaded()`。
3. `PlaylistOverlay` 打开后，只为当前可见范围及少量预加载范围请求缩略图。
4. 滚动时继续加载新进入可见范围的条目。
5. 已缓存缩略图继续复用 `ThumbnailCache`，普通播放列表同时受益。

作者列表成功时不自动滑入面板，因此不会遮挡正在播放的视频。

## 6. 实施范围

实际修改：

- `resolver/models.py`
- `resolver/youtube_resolver.py`
- `resolver/site_resolver.py`
- `ui/main_window.py`
- `ui/playlist_overlay.py`

实际新增：

- `workers/creator_videos_worker.py`
- `tests/test_creator_playlist.py`

不修改：

- mpv 播放内核和当前媒体 URL
- 下载任务模型
- 数据库表结构
- 本地文件播放逻辑
- 已保存播放列表持久化格式

## 7. 验证方法

### 7.1 静态与自动化验证

```powershell
python -m py_compile resolver/models.py resolver/youtube_resolver.py resolver/site_resolver.py workers/creator_videos_worker.py ui/main_window.py ui/playlist_overlay.py
```

已增加针对性测试：

1. YouTube 频道扁平 JSON 转换为 `PlaylistEntry`。
2. Bilibili 空间投稿 API JSON 转换为 `PlaylistEntry`。
3. 当前视频固定首项、Bilibili 多 P 按稿件去重、最大 50 个其他视频。
4. 制作者 ID/URL 字段优先级与兜底。
5. 请求令牌有效、过期、停止后返回三种状态。
6. 显式播放列表不被作者动态列表覆盖。
7. 作者列表加载失败不调用 mpv 的 load/pause/stop/seek。

### 7.2 YouTube 手工验证

1. 从搜索结果播放一个普通 YouTube 单视频。
2. 确认当前视频先正常出画面和声音，作者列表加载期间不中断、不显示播放器加载遮罩。
3. 等待后台任务完成，移动鼠标到右侧热点，确认显示“`作者名 的视频`”。
4. 确认当前视频位于第一项并高亮，后续没有当前视频重复项，其他视频不超过 50 条。
5. 双击任一作者视频，确认正常解析和播放，作者列表上下文保持。
6. 让当前视频自然结束，确认自动连播到下一项；关闭自动连播后重复，确认停留在结束状态。
7. 保存作者列表并重新加载，确认条目和顺序有效。

### 7.3 Bilibili 手工验证

1. 从首页、搜索、收藏或直接 URL 播放一个普通 Bilibili 单视频。
2. 重复 YouTube 的播放不中断、面板标题、当前项、去重、切换和自动连播验证。
3. 选择包含多 P 的投稿，确认当前 P 对应稿件不会在作者其他视频中重复出现。
4. 分别在有 Cookie、无 Cookie 条件下验证 WBI 接口和 yt-dlp 兜底。
5. 使用容易触发风控的作者空间验证：当前视频继续播放，只出现非阻塞失败提示并写入日志。

### 7.4 优先级与竞态验证

1. 播放 YouTube playlist 中某项，确认原 playlist 不被作者列表替换。
2. 播放 Bilibili 合集、收藏夹或番剧中某项，确认原列表不被替换。
3. 加载已保存列表并播放，确认保存列表不被替换。
4. 播放视频 A 后立即切换视频 B，等待 A 的作者请求完成，确认 A 的结果不会覆盖 B。
5. 播放在线视频后立即点击停止或播放本地文件，确认迟到结果不会重新出现播放列表。
6. 作者列表加载过程中切换清晰度、字幕、暂停和拖动，确认操作正常且播放位置不被重置。

### 7.5 资源影响验证

1. 记录作者任务开始前后当前视频时间位置，确认持续递增。
2. 通过日志确认作者任务没有调用当前媒体的 `mpv.load()`。
3. 作者列表加载完成但面板未打开时，确认没有批量缩略图请求。
4. 打开面板后只加载可见项缩略图，快速滚动时布局不卡顿且不重复请求已缓存图片。
5. 作者接口超时或失败时，当前播放、控制面板和播放结束重播功能不受影响。

## 8. 验收标准

1. YouTube 和 Bilibili 独立在线视频播放后均能后台生成作者动态列表。
2. 当前视频启动优先，作者列表成功、失败或超时均不改变当前播放状态和位置。
3. 当前视频首项高亮，作者其他视频去重、顺序和数量限制正确。
4. 显式播放列表、本地文件及过期异步结果不会被错误覆盖。
5. 作者列表支持现有条目播放、自动连播、保存和批量下载能力。
6. 缩略图按需加载，不在作者列表应用时产生集中网络请求。
7. 日志能够区分站点、制作者 ID、请求代次、耗时、成功数量、缓存命中、失败和过期结果。
8. 静态检查、针对性测试和手工验证矩阵全部通过。

## 9. 风险与后续方向

1. YouTube/Bilibili 页面和接口可能变更或触发登录、地区、频率、验证码限制，必须保留失败隔离和日志。
2. “最新 50 个视频”不是作者全部历史作品；第一版以加载速度和播放稳定性优先，后续可增加分页。
3. 会员、充电、付费、年龄或地区受限条目即使出现在作者列表中，单项解析时仍可能失败。
4. 第一版不把作者列表与用户显式 playlist 混合，避免污染原列表；后续若需要，可设计独立的“当前队列/作者作品”标签页。
5. 后续可增加设置项，用于控制是否自动加载作者列表、数量上限和默认自动连播行为。

## 10. 审核结论记录

审核结论：用户于 2026-07-15 明确批准方案并同意启动编码。  
实施结论：功能代码、离线自动化测试和静态检查已完成，等待真实 YouTube/Bilibili 网络环境手工验收。

已执行验证：

1. `python -m py_compile` 检查所有本次新增和修改模块：通过。
2. `python -m unittest discover -s tests -v`：11 项测试全部通过。
3. YouTube 作者 `/videos` URL 规范化与扁平响应转换：通过。
4. Bilibili WBI 投稿响应转换、时长和封面规范化：通过。
5. 当前视频首项、结果去重、缓存深拷贝与 Bilibili 多 P 稿件去重：通过。
6. 作者请求令牌有效/过期及显式播放列表优先级：通过。
7. 播放列表条目创建时不请求封面，显式触发后只请求一次：通过。
8. `git diff --check`：通过，仅出现仓库既有的 CRLF 转换提示。

受当前非交互式、非联网验证环境限制，仍需按第 7.2 至 7.5 节使用真实 YouTube/Bilibili 视频完成接口、风控兜底、播放不中断和自动连播手工验收。

## 11. 2026-07-15 实机问题修复记录

### 11.1 日志证据

用户实机日志证明站点解析本身成功：

- Bilibili：作者 MID `4401694` 的投稿接口返回 50 条，动态列表组装为 50 条。
- YouTube：频道 `UCD_gy8DWV_DhjJ-bQXF5dGQ` 返回 50 条，频道 `UCshQ2rQwYcwwU1BvG-PBoDg` 返回 32 条。

但上述记录后均没有出现 `creator playlist applied` 或过期结果日志，说明结果停在 Worker 成功返回与主窗口应用之间。

### 11.2 根因

Worker 成功/失败信号连接到了 `_start_creator_playlist_worker()` 中创建的局部 lambda。耗时 Worker 返回后会立即自动销毁，排队中的无接收者上下文 lambda 回调存在随发送者连接一起消失的风险，因此主窗口没有收到结果，也没有异常日志。

### 11.3 修复

1. Worker 信号直接携带 `generation`、`video_id` 和结果/错误。
2. 信号直接连接到 `MainWindow` 的 `@Slot` 绑定方法，不再使用局部 lambda 转发。
3. 主窗口在任务完成信号处理前显式保留 Worker 引用，完成后再释放，保证排队信号生命周期。
4. 成功回调增加“收到结果”“应用成功”“过期忽略”日志。
5. UI 应用阶段增加异常捕获、完整日志和非阻塞 toast，避免再次静默失败。
6. Worker 完成增加独立日志，可完整观察任务生命周期。

### 11.4 回归验证

新增耗时 Worker 生命周期测试：启动后局部 Worker 变量立即离开作用域，结果仍必须投递到绑定的主线程接收者。该测试与其余 10 项测试均通过。

### 11.5 完成提示补充

作者列表请求完成后，对当前仍有效的请求统一显示非阻塞 toast：

1. 成功应用：提示已加载及列表条数。
2. 成功返回但没有其他可用视频：提示未找到其他视频。
3. 无法识别制作者身份：提示未加载作者列表。
4. 站点解析失败：提示加载失败但当前视频继续播放。
5. UI 应用失败：提示列表应用失败。

已经因切换视频、停止或显式播放列表而失效的旧请求不显示 toast，避免过期消息干扰当前操作。
