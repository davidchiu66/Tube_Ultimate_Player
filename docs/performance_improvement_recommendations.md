# Tube_Ultimate_Player 性能改进建议备案

更新日期：2026-07-10

## 1. 检视目标

本次检视只做性能分析与整改建议备案，不更改现有业务输出、用户可见行为、数据格式或发布流程。

检视范围包括：

1. 首页、搜索、URL 解析与播放列表解析
2. 下载队列、进度更新与下载记录持久化
3. 首页网格、播放列表面板与缩略图加载
4. SQLite 仓储、JSON 配置与任务文件读写
5. 后台 worker 与主窗口调度

## 2. 总体结论

当前代码已经把主要耗时操作放到了 `QThreadPool` / `QRunnable` 中，整体交互不会被单个解析请求完全阻塞，这是一个好的基础。

下一步最值得优先优化的性能点不是单个算法复杂度，而是以下几类重复开销：

1. 网络解析重复拉取过多数据
2. 缩略图缺少缓存，页面刷新或切换时重复下载与解码
3. 下载进度每秒扫描目录，且每次进度更新都写 JSON 文件
4. UI 列表使用大量真实 QWidget，条目增多后创建和重排成本上升
5. SQLite 每次操作都新建连接，缺少 WAL、busy timeout 等运行参数

## 3. P0 建议：低风险、收益明显

### 3.1 为首页与搜索结果增加内存缓存

相关位置：

- [resolver/youtube_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/youtube_resolver.py:131)
- [resolver/youtube_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/youtube_resolver.py:183)
- [resolver/site_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/site_resolver.py:126)
- [resolver/site_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/site_resolver.py:474)
- [ui/main_window.py](/f:/Others/PYTHON/Tube_player/ui/main_window.py:301)
- [ui/main_window.py](/f:/Others/PYTHON/Tube_player/ui/main_window.py:332)

现状：

1. YouTube 首页和搜索使用 `yt-dlp --playlist-end total_needed`。
2. 翻到第 N 页时会重新拉取从第 1 条到第 N 页末尾的全部条目。
3. Bilibili 首页与搜索也会按页重复请求，缺少短期结果复用。
4. 主窗口目前只缓存了当前首页页数据，搜索和跨页缓存不足。

建议：

1. 增加 `Home/Search Page Cache`，key 至少包含：
   - source site
   - mode: home/search
   - keyword
   - page
   - page size
   - cookie source fingerprint 或配置版本号
2. 缓存生命周期建议：
   - 首页：1 到 5 分钟
   - 搜索：同一关键词会话内有效
3. 用户点击“刷新”时绕过缓存并更新缓存。
4. 设置保存、Cookie 变更、代理变更、默认首页变更时清空缓存。

预期收益：

1. 翻页返回、重复进入首页、同关键词搜索翻页可明显减少网络与 `yt-dlp` 启动成本。
2. 对 YouTube 尤其明显，因为 `yt-dlp` 进程启动和 JSON 解析成本较高。

输出不变约束：

1. 命中缓存时返回的数据结构必须仍是 `list[HomeVideo], has_next`。
2. “刷新”按钮语义保持为真实刷新。

### 3.2 缩略图增加进程内缓存

相关位置：

- [ui/home_page.py](/f:/Others/PYTHON/Tube_player/ui/home_page.py:107)
- [ui/home_page.py](/f:/Others/PYTHON/Tube_player/ui/home_page.py:117)
- [ui/playlist_overlay.py](/f:/Others/PYTHON/Tube_player/ui/playlist_overlay.py:104)
- [ui/playlist_overlay.py](/f:/Others/PYTHON/Tube_player/ui/playlist_overlay.py:114)

现状：

1. 首页卡片和播放列表面板每创建一个 item 都直接发起 `QNetworkAccessManager.get()`。
2. 页面切换、返回首页、播放列表重新加载时，会重复下载相同缩略图。
3. 每次响应后都会重新 `QPixmap.loadFromData()` 和 `scaled()`。

建议：

1. 增加轻量 `ThumbnailCache`：
   - key: thumbnail URL + target size
   - value: 已缩放后的 `QPixmap`
   - 容量：例如 200 到 500 张
   - 策略：LRU 或简单 FIFO
2. 对正在下载中的 URL 做 in-flight 合并：
   - 同一 URL 同一尺寸只发起一次网络请求
   - 多个 label 等待同一个 reply 完成后统一设置 pixmap
3. 首页卡片和播放列表面板共用同一个缓存服务。

预期收益：

1. 减少网络请求、图片解码和缩放成本。
2. 页面切换、刷新 UI、打开播放列表面板会更轻。

输出不变约束：

1. 缩略图内容与现有 URL 一致。
2. 加载失败仍展示原来的失败文本或等价提示。

### 3.3 下载任务 JSON 写入去抖

相关位置：

- [download/download_manager.py](/f:/Others/PYTHON/Tube_player/download/download_manager.py:158)
- [download/download_manager.py](/f:/Others/PYTHON/Tube_player/download/download_manager.py:364)

现状：

1. 下载进度 `_progress()` 每次收到进度信号都会调用 `_save_tasks()`。
2. `_save_tasks()` 每次会序列化全部任务并写入 `download_tasks.json`。
3. 多任务并发下载时，磁盘写入频率会随进度信号数量线性增加。

建议：

1. 引入保存节流：
   - 状态变化、任务新增、完成、失败、删除：立即保存
   - 普通进度变化：最多每 1 到 3 秒保存一次
2. 程序关闭时强制 flush。
3. 保存时可先写临时文件再原子替换，兼顾性能与损坏恢复。

预期收益：

1. 显著降低下载时磁盘写入频率。
2. 多任务并发下载时 UI 和磁盘压力更稳定。

输出不变约束：

1. UI 进度仍实时更新。
2. 异常退出时最多丢失最近 1 到 3 秒的进度数值，但不丢任务。

### 3.4 下载进度文件扫描改为缓存匹配文件集合

相关位置：

- [download/download_worker.py](/f:/Others/PYTHON/Tube_player/download/download_worker.py:282)
- [download/download_worker.py](/f:/Others/PYTHON/Tube_player/download/download_worker.py:290)
- [download/download_worker.py](/f:/Others/PYTHON/Tube_player/download/download_worker.py:299)
- [download/download_manager.py](/f:/Others/PYTHON/Tube_player/download/download_manager.py:313)

现状：

1. `_downloaded_bytes()` 每秒扫描下载目录。
2. `_find_downloaded_file()` 完成时再次扫描目录。
3. 下载目录文件较多时，目录遍历会成为持续开销。

建议：

1. 每个下载 worker 启动时建立候选文件匹配器。
2. 首次扫描找到相关临时文件后缓存路径列表。
3. 后续进度估算优先 stat 已知路径；只有路径不存在或没有匹配项时再扫描目录。
4. 完成时复用缓存路径或 `yt-dlp after_move` 输出路径。

预期收益：

1. 下载目录文件多时，进度估算成本从“每秒全目录扫描”降为“每秒少量 stat”。
2. 对并发下载和历史下载文件很多的用户更友好。

输出不变约束：

1. 进度、速度、剩余时间字段保持现有语义。
2. 完成文件路径仍以真实存在的最终文件为准。

## 4. P1 建议：中等改造，适合下一轮集中处理

### 4.1 Bilibili WBI key 缓存

相关位置：

- [resolver/site_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/site_resolver.py:690)

现状：

1. 每次走 WBI 搜索候选 B 时都会请求 `/x/web-interface/nav`。
2. WBI key 在短时间内通常稳定。

建议：

1. 在 `BilibiliResolver` 内缓存 `img_key/sub_key/mixin_key`。
2. 设置 TTL，例如 30 分钟到 2 小时。
3. 如果签名接口返回签名相关错误，再清空缓存并重试一次。

预期收益：

1. Bilibili 搜索失败回退到 WBI 时可少一次网络请求。
2. 连续搜索时响应更快。

输出不变约束：

1. 签名生成结果不变。
2. 缓存失效后仍按当前逻辑重新获取 nav。

### 4.2 浏览器 Cookie 探测结果缓存

相关位置：

- [resolver/site_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/site_resolver.py:656)
- [resolver/site_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/site_resolver.py:665)
- [services/config_service.py](/f:/Others/PYTHON/Tube_player/services/config_service.py:224)
- [services/cookie_service.py](/f:/Others/PYTHON/Tube_player/services/cookie_service.py:186)

现状：

1. Bilibili 搜索会优先找浏览器 Cookie。
2. 如果显式浏览器和自动浏览器都不可用，会遍历系统中检测到的浏览器 Cookie 来源。
3. Cookie 数据库复制和读取是相对昂贵的 I/O 操作。

建议：

1. 缓存“可用 Cookie 来源”和“不可用 Cookie 来源”的短期结果。
2. 按域名区分，例如 `youtube.com` 与 `bilibili.com`。
3. 设置 TTL，例如 5 到 15 分钟。
4. 设置页保存 Cookie/浏览器配置后清空缓存。

预期收益：

1. 连续解析或搜索时减少浏览器 profile 扫描和 SQLite Cookie 读取。
2. 降低浏览器数据库锁冲突概率。

输出不变约束：

1. 用户修改设置后必须立即生效。
2. Cookie 过期或失效时仍能按当前回退逻辑报错或切换来源。

### 4.3 首页网格重排去抖

相关位置：

- [ui/home_page.py](/f:/Others/PYTHON/Tube_player/ui/home_page.py:330)

现状：

1. `resizeEvent()` 会直接触发 `_relayout_cards()`。
2. `_relayout_cards()` 会取出所有 grid item 再重新 addWidget。
3. 用户拖动窗口大小时，短时间内可能重复重排 56 个卡片。

建议：

1. 在 `resizeEvent()` 中使用 50 到 100ms 的 single-shot timer 去抖。
2. 只有列数变化时才真正重排。
3. 缓存当前 columns，避免尺寸细微变化重复搬动 widgets。

预期收益：

1. 窗口拖拽和最大化时 UI 更稳。
2. 避免重复 layout 造成卡顿。

输出不变约束：

1. 最终布局列数和现在一致。
2. 页面内容、选中态和按钮状态不变。

### 4.4 播放列表面板改用轻量 delegate 或懒加载 item widget

相关位置：

- [ui/playlist_overlay.py](/f:/Others/PYTHON/Tube_player/ui/playlist_overlay.py:220)
- [ui/playlist_overlay.py](/f:/Others/PYTHON/Tube_player/ui/playlist_overlay.py:244)
- [ui/playlist_overlay.py](/f:/Others/PYTHON/Tube_player/ui/playlist_overlay.py:249)

现状：

1. 播放列表面板为每条记录创建 `PlaylistItemWidget`。
2. 每个 item widget 还可能发起缩略图请求。
3. 当列表达到数百条时，创建 QWidget 和图片请求会变重。

建议：

1. 第一阶段：仅对可见区域及附近条目加载缩略图。
2. 第二阶段：改为 `QListView + QAbstractListModel + QStyledItemDelegate`。
3. 保留当前 item 高度、双击、选中、多选、当前播放项高亮等输出。

预期收益：

1. 长播放列表打开更快。
2. 内存占用更稳定。

输出不变约束：

1. UI 视觉应保持同当前面板一致。
2. 多选、双击播放、批量下载行为不变。

### 4.5 SQLite 连接和 PRAGMA 优化

相关位置：

- [database/sqlite_manager.py](/f:/Others/PYTHON/Tube_player/database/sqlite_manager.py:84)

现状：

1. 每个仓储方法通过 `with self.db.connect()` 新建连接。
2. 当前没有设置 WAL、busy timeout、foreign keys 等运行参数。

建议：

1. 在 `connect()` 后设置：
   - `PRAGMA journal_mode=WAL`
   - `PRAGMA synchronous=NORMAL`
   - `PRAGMA busy_timeout=3000`
   - `PRAGMA foreign_keys=ON`
2. 可保留短连接模式，先不引入长期连接，降低风险。
3. 后续如数据库访问频率继续升高，再考虑连接复用。

预期收益：

1. 降低读写互相阻塞概率。
2. 收藏、历史、播放列表写入更稳。

输出不变约束：

1. 表结构和查询结果不变。
2. 数据库文件仍在同一路径。

## 5. P2 建议：中长期架构优化

### 5.1 YouTube 首页和搜索分页策略优化

相关位置：

- [resolver/youtube_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/youtube_resolver.py:131)
- [resolver/youtube_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/youtube_resolver.py:183)

现状：

1. `total_needed = page * page_size + 1`。
2. 第 5 页会重新要求 `yt-dlp` 拉取前 281 条左右。

建议：

1. 短期先依赖 P0 的分页缓存。
2. 中期评估使用 `--playlist-start` / `--playlist-end` 分页拉取，避免每页从头取。
3. 对 `ytsearch` 需要验证 yt-dlp 对 start/end 的支持和结果稳定性。

预期收益：

1. 深分页时网络和 JSON 解析明显减少。
2. 首页/搜索翻页体验更接近真实分页。

风险：

1. YouTube 首页推荐本身是动态流，严格分页可能不稳定。
2. 需要确保结果顺序和用户感知不发生突兀变化。

### 5.2 网络请求层统一为可复用 session

相关位置：

- [resolver/site_resolver.py](/f:/Others/PYTHON/Tube_player/resolver/site_resolver.py:627)
- [services/update_service.py](/f:/Others/PYTHON/Tube_player/services/update_service.py:178)
- [services/runtime_install_service.py](/f:/Others/PYTHON/Tube_player/services/runtime_install_service.py:98)

现状：

1. Bilibili API 使用 `urllib.request.urlopen()`。
2. 更新检测和运行时安装也各自发起请求。
3. 当前没有统一连接复用、重试、超时和 User-Agent 管理。

建议：

1. 增加 `services/http_client.py`。
2. 统一处理：
   - timeout
   - retry
   - User-Agent
   - proxy
   - JSON 解析
   - 简单连接复用
3. 如果继续坚持标准库，可先封装 `urllib`；如果允许新增依赖，可评估 `requests.Session`。

预期收益：

1. 减少重复代码。
2. 网络行为更一致，可观测性更好。

输出不变约束：

1. API 返回解析结构不变。
2. 错误提示文案保持现有语义。

### 5.3 图片磁盘缓存

相关位置：

- [ui/home_page.py](/f:/Others/PYTHON/Tube_player/ui/home_page.py:107)
- [ui/playlist_overlay.py](/f:/Others/PYTHON/Tube_player/ui/playlist_overlay.py:104)

建议：

1. 在 `%LocalAppData%\Tube_Ultimate_Player\cache\thumbnails` 下做磁盘缓存。
2. key 使用 URL hash。
3. 缓存原始图片或缩放后的目标尺寸图片。
4. 增加容量上限和过期清理。

预期收益：

1. 应用重启后仍能快速显示已见过的封面。
2. 首页和播放列表打开速度更稳定。

风险：

1. 要处理缓存清理和损坏图片。
2. 图片版权与用户隐私方面应保持本地私有缓存，不纳入仓库和发布包。

## 6. 推荐整改顺序

建议分 4 个小批次推进：

1. 下载持久化与目录扫描优化
   - 下载任务 JSON 写入去抖
   - 下载 worker 缓存匹配文件路径
   - 验证多任务下载、暂停、继续、完成恢复

2. UI 缩略图与首页重排优化
   - 进程内 thumbnail cache
   - in-flight 请求合并
   - 首页 resize 去抖和列数变化判断

3. 解析层缓存优化
   - 首页/搜索短期缓存
   - Bilibili WBI key 缓存
   - 浏览器 Cookie 来源短期缓存

4. 数据库和网络层基础设施优化
   - SQLite PRAGMA
   - 统一 HTTP client
   - 播放列表面板 delegate 化评估

## 7. 验证方法

### 7.1 基准指标

建议在整改前后记录以下指标：

1. 应用启动到首页首屏完成的耗时
2. 首页第一页、第二页、返回第一页耗时
3. 同关键词搜索第一页、第二页、返回第一页耗时
4. 打开 100 条以上播放列表的耗时
5. 下载 3 个任务时 `download_tasks.json` 每分钟写入次数
6. 下载目录存在 500 个文件时的进度刷新稳定性
7. 首页窗口拖拽 resize 时是否卡顿

### 7.2 行为回归

每批整改后至少验证：

1. YouTube 首页、搜索、URL 播放不回归
2. Bilibili 首页、搜索、URL 播放不回归
3. 播放列表详情页与侧滑面板不回归
4. 下载进度、暂停、继续、删除、完成、本地播放不回归
5. 收藏、历史、播放列表保存与重启恢复不回归

## 8. 不建议优先做的事项

1. 不建议马上大规模重写 UI 架构。
2. 不建议在没有基准数据前引入复杂异步框架。
3. 不建议为了缓存牺牲“刷新”按钮的真实刷新语义。
4. 不建议把缩略图缓存、下载任务和用户数据放回项目目录。

## 9. 结论

当前项目的主要性能提升空间集中在“重复 I/O、重复网络请求、重复 UI widget 创建”三个方向。建议下一步从 P0 项开始，优先做不改变输出的缓存、节流和去抖；这些改动风险低、收益直接，也能为后续更大的架构整理留出清晰基线。
