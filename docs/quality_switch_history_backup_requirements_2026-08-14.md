# Tube_player 默认画质 / 站点切换 / 历史批量 / WebDAV 备份恢复方案（四项）

状态：**已落档；R1–R4 已完成编码、自动化测试和真实 WebDAV 手工验收**
备案日期：2026-08-14（裁定回填：2026-08-14）
基线版本：`app_version.txt` = 0.2.25（`docs/releases/v0.2.25.md` 尚未创建，发布前必须补，见第 7 节）

本文覆盖用户 2026-08-14 提出的四项需求（R1–R4）。按仓库既有约定：先理解、分析、完善需求，给出实施方案与验收标准，**落档备案后等审阅通过再启动编码**。

第 6 节记录 5 项裁定（A–E）的最终结论：A/B/C 采纳推荐口径；D 改为"立即重启 / 稍后重启"两个按钮（不是"立即退出"）；E 改为"直连优先，连续 3 次失败后自动改用配置的代理"。正文（4.2 第 12/15 条、4.5、4.8、4.10）已按最终裁定改写。

---

## 0. 需求清单与裁定摘要

| 编号 | 原始需求 | 关键裁定（推荐口径） |
| --- | --- | --- |
| R1 | 配置项增加默认视频画质：高/中/低 | 复用既有键 `player.default_quality`，取值改为 `high`/`medium`/`low`，默认 `high`（= 今天的行为）；旧值 `Auto` 视为 `high`，旧的精确标签（如 `1080p`）继续按标签精确匹配。"中"= **按去重后的分辨率高度列表取中位**（裁定 A） |
| R2 | 首页切换不得因搜索框有文本而误触发搜索 | 主窗口新增 `_browse_mode`（`home`/`search`）记录"前一个浏览动作"；只有它是 `search` 且搜索框非空时，切站点才重搜。不清空搜索框文本 |
| R3 | 播放历史增加下载选中/全部、删除选中/全部 | 完全对齐收藏页口径："全部"= **当前搜索筛选后的可见行**；批量代码抽成 `ui/library_batch.py` 的 Mixin 供收藏页/历史页共用；历史页读取上限 50 → 200（裁定 C） |
| R4 | 设置页新增"备份/恢复"Tab：多 WebDAV 管理 + 备份（带时间戳、最多留 20 个）+ 恢复（列出远端清单选包恢复） | 用 stdlib `urllib` 自写 WebDAV 客户端（不引第三方依赖）；备份包为 ZIP + `manifest.json` + 逐文件 sha256；WebDAV 凭据存**独立文件**并加密，**不进备份包**；Cookie 默认**不**备份（裁定 B）；恢复完成后弹"立即重启 / 稍后重启"（裁定 D）；网络**直连优先，连续 3 次失败后自动改用配置的代理**（裁定 E） |

---

## 1. R1 默认画质（高 / 中 / 低）

### 1.1 现状

- `config/default_config.json:3` 已有 `player.default_quality`，默认 `"Auto"`，但**设置页从未暴露过这个选项**，用户改不了。
- `MainWindow._select_default_quality()`（`ui/main_window.py:1350-1355`）的逻辑是：值不为 `Auto` 且与某个清晰度标签精确相同时用它，否则取 `video.qualities` 的第一条。
- `QualitySelector.select_all()`（`resolver/quality_selector.py:168-215`）产出的 `qualities` 是 `OrderedDict`，键为 `1080p60`/`1080p`/`720p` 这类标签，**按（高度, fps）降序**插入。所以"第一条"就是最高分辨率——这正是用户说的"同当前一样"。
- 播放页"清晰度"下拉（`ui/player_page.py:520-525`）按 `video.qualities` 的顺序填充，并选中主窗口传入的标签。

### 1.2 完善后的需求

1. 设置页"常规"Tab 增加"默认画质"选项，三选一：**高**（默认）、**中**、**低**。
2. 高 = 每次打开视频选最高分辨率档（与当前行为逐字节一致）；低 = 选最低分辨率档；中 = 选中间档。
3. 边界补充（原需求未提，本方案裁定）：
   - **"中"的口径按分辨率高度去重后取中位**，而不是按标签下标：`1080p60 / 1080p / 720p60 / 720p / 480p` 若按标签下标取中会落在 `720p60`，而用户心里的"中间画质"是分辨率意义上的中间。去重后高度为 `[1080, 720, 480]`，中位 = `720`，再在该高度内取最优（fps 最高）的一档，即 `720p60`。
   - 高度个数为偶数时取**偏低**的那一档（`heights[len // 2]`）：如 `[1080, 720]` → `720`。省流量比"中间偏高"更符合选"中"的意图。
   - 只有一档时，高/中/低都选它，不报错。
   - 一档都没有时仍返回 `None`，保留 `ui/main_window.py:1262-1282` 既有的"没有可播放清晰度"提示（F3 加固不能回退）。
   - 用户在播放页手动切的清晰度**只作用于本次播放**，不写回配置（与音轨、倍速的现有约定一致）。
   - 该选项影响播放页下载按钮（它下载的是 `current_quality_label`，`ui/main_window.py:1526`）；收藏页/历史页/播放列表页的批量下载仍按 `"Auto"` 入队（交给 yt-dlp 选最佳），本次不改，见 5.3 说明。

### 1.3 方案

**配置**

`config/default_config.json` 的 `player.default_quality` 由 `"Auto"` 改为 `"high"`。老用户的 `user_config.json` 里仍是 `"Auto"` 或某个精确标签，靠下面的兼容分支消化，无需迁移脚本。

`services/config_service.py` 新增：

```python
QUALITY_TIERS = ("high", "medium", "low")
QUALITY_TIER_LABELS = {"high": "高（最高分辨率）", "medium": "中（中间分辨率）", "low": "低（最低分辨率）"}

def default_quality_tier(self) -> str:
    """归一化成 high/medium/low；Auto、空值、旧的精确标签一律回退 high。"""

def default_quality_label_override(self) -> str:
    """旧配置里写死的清晰度标签（如 1080p），不是档位时返回空串。"""
```

**选档逻辑**

`resolver/quality_selector.py` 增加模块级函数（放这里而不是主窗口：纯函数、无 Qt 依赖，可离线单测）：

```python
def select_quality_by_tier(
    qualities: "dict[str, VideoQuality]", tier: str
) -> "VideoQuality | None":
    """按档位挑一档清晰度。tier ∈ {high, medium, low}，未知值按 high。"""
```

实现要点：不依赖调用方保证顺序，函数内部按 `(height, fps)` 再降序排一次；`medium` 先对 `height` 去重降序，取 `heights[len(heights) // 2]`，再回到排序后的列表里取该高度的第一条。

`MainWindow._select_default_quality()` 改为：

```python
override = self.config.default_quality_label_override()
if override and override in video.qualities:
    return video.qualities[override]           # 兼容旧配置里的精确标签
return select_quality_by_tier(video.qualities, self.config.default_quality_tier())
```

**设置页**

`ui/settings_page.py` 在"进入播放"一行之后插入"默认画质"行：`QComboBox`（`self.default_quality_combo`），三项 `data` 分别为 `high`/`medium`/`low`，配一条 `MetaLabel` 备注"打开视频时自动选择的清晰度档位；该档位不存在时按最接近的一档"。`load()` 里回填 `config.default_quality_tier()`，`save()` 里写 `player.default_quality`。

### 1.4 验收标准

1. 设置页"常规"Tab 出现"默认画质"下拉，三项为高/中/低；保存后 `user_config.json` 的 `player.default_quality` 为 `high`/`medium`/`low`。
2. 选"高"：打开任意视频，播放页"清晰度"选中的是下拉里最上面那一档（与改动前完全一致）。
3. 选"低"：选中的是下拉最后一档。
4. 选"中"：`1080p60/1080p/720p60/720p/480p` 的视频选中 `720p60`；`1080p/720p` 的视频选中 `720p`；只有一档时选中那一档。
5. 旧配置 `"Auto"` 不改动即按"高"工作；旧配置 `"1080p"` 且视频有 `1080p` 时仍精确命中 `1080p`。
6. 无任何清晰度的视频仍弹"该视频没有可播放的清晰度"，不抛异常。
7. 新增 `tests/test_default_quality_tier.py` 全绿，且 `tests/test_main_window_hardening.py` 的 `DefaultQualityTests` 更新后全绿。

---

## 2. R2 切换视频网站时不再误触发搜索

### 2.1 现状

`MainWindow._set_browse_source()`（`ui/main_window.py:719-744`）**只看搜索框里有没有文本**：

```python
keyword = self.url_edit.text().strip()
if keyword:
    self._start_search(keyword, 1)
    return
self.load_home()
```

`self.url_edit` 就是工具栏的搜索框（`ui/main_window.py:206`），它在搜索后不会被清空。于是：搜索 → 点"首页" → 切站点，得到的是**又一次搜索**，而不是新站点的首页。用户看到的现象即"做首页切换时被搜索劫持"。

`ui/home_page.py:242` 的 `mode()` 已经在维护 `home`/`search` 两态（`set_home_context()` 置 `home`，`set_search_context()` 置 `search`），`_load_page()` / `_refresh_home_page()` 都在用它。

### 2.2 完善后的需求

1. 当前浏览动作是**首页**时，切换站点（B 站 ↔ YouTube）一律加载新站点首页，**即使搜索框里还有文本**。
2. 只有当前浏览动作是**搜索**时，切换站点才检测搜索框：非空则用该关键词在新站点重搜；为空则回落到新站点首页。
3. 切换站点不清空搜索框的文本（用户可能马上要点"搜索"）。
4. 两个方向、两个站点对称，无例外。

### 2.3 方案

主窗口新增一处显式状态，而不是从 `home_page.mode()` 反查——`_set_browse_source` 的判定与页面渲染解耦后才好离线单测：

- `__init__` 中于 `self._search_keyword = ""` 附近加 `self._browse_mode = "home"`（注释说明它记录"前一个浏览动作"，仅用于站点切换的取向）。
- `_start_home_load()`、`_apply_home_cache()`、`_show_home()` 置 `self._browse_mode = "home"`；`_start_search()` 置 `"search"`。
- `_set_browse_source()` 的分支改为：

```python
keyword = self.url_edit.text().strip()
if self._browse_mode == "search" and keyword:
    self._search_keyword = keyword
    self._search_page = 1
    self._start_search(keyword, 1)
    return
self._search_keyword = ""
self._search_page = 1
self.load_home()
```

其余不动：首页留档（`_store_home_state`）、`_browse_generation` 自增丢弃旧结果、`normalize_source` 去重复切换，全部保持现状。

### 2.4 验收标准

1. 搜索"周杰伦"→ 点"首页"→ 切到 YouTube：出现 YouTube **首页**列表，搜索框里的"周杰伦"仍在，未发起搜索（`logs/app.log` 只有 `home load requested source=youtube`，没有 `search requested`）。
2. 反方向（YouTube 首页 → 切 B 站）同样只加载首页。
3. 搜索"周杰伦"后**直接**切站点：在新站点用"周杰伦"重搜（保持既有行为，不能退化）。
4. 搜索后清空搜索框再切站点：加载新站点首页。
5. 启动后未做任何浏览动作就切站点：加载新站点首页。
6. 新增 `tests/test_browse_source_switch.py` 覆盖上述 5 种组合，全绿。

---

## 3. R3 播放历史批量操作

### 3.1 现状

- `ui/history_page.py` 只有"刷新"、"播放选中"、每行一个"播放"按钮；表格是 `SingleSelection`（`ui/history_page.py:36`），选不了多行。
- `ui/favorite_page.py:67-104` 与 `167-216` 已实现完整的四个批量按钮 + `_visible_rows/_selected_rows/_records_for_rows/_emit_batch/_confirm_delete/_update_batch_buttons`，"全部"= 搜索筛选后的可见行；主窗口侧对应 `_download_favorite_records()` / `_remove_favorites()`（`ui/main_window.py:1587-1628`）。
- `database/history_repository.py` **只有** `record_play()` 与 `recent(limit=50)`，没有任何删除方法。
- `history` 表没有 `video_id` 唯一约束（`database/sqlite_manager.py:12-30`），`record_play()` 用"取最后一条再 UPDATE"维护单条记录。按 `video_id` 删除会删掉该视频的全部历史行，这正是期望语义。

### 3.2 完善后的需求

1. 历史页增加四个批量按钮：**下载选中 / 下载全部 / 删除选中 / 删除全部**，位置、样式、启用/禁用规则与收藏页一致。
2. 表格改多选（`ExtendedSelection`）；每行操作列在"播放"之后补一个"删除"。
3. "全部"= 当前搜索筛选后的可见行（与收藏页、下载页同口径）。
4. 删除前确认，文案说明"只删播放历史记录，不动已下载的本地文件"。
5. 批量下载沿用收藏页做法：历史记录里没有清晰度信息，统一按 `"Auto"` 入队，成功/跳过条数用 toast 汇总。
6. 边界补充（原需求未提，本方案裁定）：
   - 历史页当前只读最近 **50** 条，若表里有 300 条，"删除全部"只会删掉眼前 50 条——这是个陷阱。上限提到 **200**，并在"删除全部/下载全部"的 tooltip 里写清实际条数（裁定 C）。
   - 被搜索筛掉的选中行不参与批量（收藏页已有此行为，`tests/test_favorite_batch.py:114`）。
   - 删除后立刻刷新历史页；正在播放的视频被删历史不影响播放。

### 3.3 方案

**仓储层**

`database/history_repository.py` 新增，签名与 `FavoriteRepository` 对齐：

```python
def remove(self, video_id: str) -> int:            # 删单个视频的全部历史行
def remove_many(self, video_ids: list[str]) -> int # 一条 DELETE ... IN (...)，返回行数
```

`recent()` 的默认 `limit` 保持 50 不改（其他调用方可能依赖），由历史页显式传 `HISTORY_PAGE_LIMIT = 200`。

**批量代码去重**

新增 `ui/library_batch.py`，把收藏页里已经打磨过的批量逻辑抽成 `LibraryBatchMixin`：

- 提供 `_visible_rows()`、`_selected_rows()`、`_records_for_rows()`、`_emit_batch(action, *, selected_only)`、`_update_batch_buttons()`、`_build_batch_row()`（造那一排按钮并返回 `QHBoxLayout`）。
- 子类提供三个钩子：`_batch_records` → `self._rows`、`_batch_delete_confirm_text(count, selected_only)`、`_batch_id_of(record)`（收藏/历史都用 `video_id`）。
- **方法名与信号名保持不动**（`download_videos_requested` / `remove_videos_requested` / `_emit_batch` / `_update_batch_buttons`），`tests/test_favorite_batch.py` 现有 15 个用例应零修改通过——这是本次重构的回归闸口。

`ui/favorite_page.py` 改为继承该 Mixin（删掉被上移的方法），`ui/history_page.py` 同样继承并补：多选、批量按钮行、行内"删除"按钮、`remove_requested` / `download_videos_requested` / `remove_videos_requested` 三个信号。

**主窗口**

- 把"记录 dict → `VideoInfo`"的转换从 `_download_favorite_records()` 里提到**模块级函数** `_records_to_video_infos(records)`（不是实例方法：`tests/test_favorite_batch.py` 用 `SimpleNamespace` 当 self 调用未绑定方法，实例方法会因缺属性炸掉）。
- 新增 `_download_history_records(records)`、`_remove_history_record(video_id)`、`_remove_history_records(video_ids)`，toast 文案：`已从播放历史中移除 N 条`。
- `_create_history_page()`（`ui/main_window.py:339-342`）补三条 `connect`。

### 3.4 验收标准

1. 历史页出现四个批量按钮，可框选/Ctrl 多选多行；每行有"播放"+"删除"。
2. 未选中任何行时"下载选中/删除选中"禁用，"下载全部/删除全部"可用且 tooltip 写明条数；筛选无结果时四个全禁用。
3. 搜索"Bilibili"后点"删除全部"，只删掉可见的 B 站历史，YouTube 历史仍在。
4. "删除选中"弹确认框，选"否"不删任何数据。
5. "下载全部"把可见行按 `Auto` 入队，toast 汇总 `已加入下载队列 N 个[，跳过 M 个…]`；已在队列的不重复入队。
6. 历史页一次最多展示 200 条。
7. 新增 `tests/test_history_batch.py` 全绿；`tests/test_favorite_batch.py`、`tests/test_list_source_search.py` 不改动即全绿。

---

## 4. R4 设置页"备份/恢复"Tab（WebDAV）

### 4.1 现状

- `ui/settings_page.py:284-286` 的 `QTabWidget` 目前只有"常规"和"快捷键"两个 Tab；页面底部是全局的"重新读取 / 保存设置"。
- 配置与数据分散在 `app_paths.py` 定义的运行时目录：`CONFIG_DIR/user_config.json`、`DATA_DIR/tube_ultimate_player.sqlite3`（history/favorite/playlist_library/playlist_item 四张表）、`DATA_DIR/download_tasks.json`（`download/download_manager.py:28`）、Cookie 文件（Windows 在 `RUNTIME_ROOT/cookie_<site>.txt`，Linux 在 `CONFIG_DIR/cookie_<site>.txt`，见 `services/config_service.py:218-223`）。
- 项目**没有** `requests` 依赖，所有 HTTP 都走 stdlib `urllib.request`（`services/update_service.py:585-597`、`dlna/controller.py:26`）。
- 已有可复用的安全解压校验 `validate_archive_entry()`（`workers/archive_extract_worker.py:23-48`，防 zip-slip，`tests/test_archive_safety.py` 有覆盖）。
- 已有可复用的 Windows DPAPI 封装思路 `_dpapi_unprotect()`（`services/chromium_cookie_extractor.py:386-408`，纯 ctypes，无第三方依赖）。
- 依赖里已有 `cryptography>=42.0`。

### 4.2 完善后的需求

**WebDAV 账号管理**

1. 可添加多个 WebDAV 配置，字段：名称、服务器地址、用户名、密码、远程目录（默认 `Tube_Ultimate_Player/backups`）。
2. 可编辑、可删除；添加/编辑对话框内有"测试连接"按钮，当场给出成功/失败原因。
3. 列表单选，选中行即"当前使用的 WebDAV"；删除当前选中项后自动落到第一项。

**备份**

4. 选中某个 WebDAV 后点"备份"：把当前所有配置和数据打成一个压缩包，文件名带时间戳，上传到该 WebDAV 的远程目录。
5. 远程目录内**最多保留最近 20 个**备份，超出的自动删除（只删本程序命名规则的包）。

**恢复**

6. 点"恢复"：拉取该 WebDAV 远程目录里的备份清单（名称、时间、大小），用户选一个包恢复。

**边界与安全补充（原需求未提，本方案裁定）**

7. 备份内容 = `user_config.json` + SQLite 库（历史/收藏/播放列表）+ `download_tasks.json`。**不含**：日志、缩略图缓存、已下载的视频文件、升级包、`3rdpart/` 二进制。
8. **Cookie 默认不备份**（裁定 B）：Cookie 等于站点登录态，上传到网盘的风险与收益不对称。提供勾选项"包含 Cookie（含站点登录凭据）"，默认不勾，勾选时在旁边给红字提示。
9. **WebDAV 账号与密码不进备份包**：否则备份包里带着能读写该网盘的凭据，一旦包泄露等于网盘泄露。账号存独立文件 `CONFIG_DIR/backup_targets.json`，密码加密存储（4.6）。恢复时用户本来就得先填 WebDAV 才能取包，不存在鸡生蛋问题。
10. 备份包内含 `manifest.json`：应用版本、schema 版本、创建时间、平台、是否含 Cookie、逐文件 sha256。恢复时逐项校验，任一不符即中止且不动现有数据。
11. 恢复前先把**当前**状态打一个本地快照到 `RUNTIME_ROOT/backups/pre-restore-<时间戳>.zip`（不上传），给用户留退路。
12. 恢复完成后必须重启应用才生效（内存里持有 `ConfigService`、SQLite 连接、`DownloadManager` 队列）。弹框给两个按钮：**立即重启**（拉起新进程后退出当前进程）与**稍后重启**（关掉弹框，状态行常驻提示"已恢复，重启后生效"）（裁定 D）。
13. 所有 WebDAV 网络 I/O 与压缩/解压都在 `workers/` 的 `QRunnable` 里跑，UI 线程零阻塞；进行中按钮禁用并显示进度文案。
14. 恢复不会删除已下载的视频文件；恢复后的下载任务若指向不存在的本地文件，沿用 `download_manager` 现有的"文件不存在"处理，不额外造轮子。
15. WebDAV 网络路径：**先直连**（忽略应用代理）；同一账号上连续 **3 次**直连因网络层原因失败后，自动改用 `ConfigService.effective_proxy()` 给出的代理重试；代理成功后本次会话记住该账号走代理，不再每次先撞 3 次（裁定 E）。详见 4.5。
16. 地址为 `http://` 时在对话框里给出明文传输警告（不阻断，自建 LAN 场景合理）。

### 4.3 新增/改动文件

| 文件 | 职责 |
| --- | --- |
| `services/webdav_client.py`（新） | stdlib urllib 实现的最小 WebDAV 客户端 |
| `services/backup_service.py`（新） | 打包/校验/恢复/远端保留策略/备份包命名 |
| `services/backup_targets.py`（新） | WebDAV 账号的读写（`backup_targets.json`）与选中项管理 |
| `services/secret_store.py`（新） | 密码加密落盘（Windows DPAPI / 其他平台 Fernet） |
| `workers/backup_worker.py`（新） | `WebdavTestWorker` / `BackupUploadWorker` / `BackupListWorker` / `BackupRestoreWorker` |
| `services/restart_service.py`（新） | 重启当前应用（frozen 与源码运行两种形态），供裁定 D 的"立即重启"使用 |
| `ui/backup_tab.py`（新） | "备份/恢复"Tab 的界面与交互（不含线程） |
| `ui/dialogs.py`（改） | 追加 `WebdavAccountDialog`、`BackupPickerDialog` |
| `ui/settings_page.py`（改） | 挂第三个 Tab，并把 Tab 的请求信号转发给主窗口 |
| `ui/main_window.py`（改） | 起 worker、连信号、回填结果、重启提示 |
| `config/default_config.json`（改） | 新增 `backup` 段 |
| `resources/qss/dark.qss` / `dark_theme.qss`（改） | 新表格与按钮的样式（复用 `LibraryTable` / `LibraryActionButton` 命名） |

### 4.4 备份包结构

文件名：`tube-backup-<版本>-<YYYYMMDD-HHMMSS>.zip`，例 `tube-backup-0.2.25-20260814-153012.zip`（时间戳用本地时间；解析用于排序与保留策略）。

```
manifest.json
config/user_config.json
data/tube_ultimate_player.sqlite3
data/download_tasks.json
cookies/cookie_youtube.txt        # 仅在勾选"包含 Cookie"时存在
cookies/cookie_bilibili.txt       # 同上
```

`manifest.json`：

```json
{
  "app": "Tube_Ultimate_Player",
  "schema": 1,
  "app_version": "0.2.25",
  "created_at": "2026-08-14T15:30:12+08:00",
  "platform": "win32",
  "include_cookies": false,
  "files": [{"path": "config/user_config.json", "size": 2048, "sha256": "…"}]
}
```

SQLite 不直接复制活动文件，用 `sqlite3.Connection.backup()` 先导到临时文件再入包——避免把 WAL/写入中途的库拷成半截。压缩用 stdlib `zipfile`（`ZIP_DEFLATED`），不引 py7zr：备份包要能被任何解压工具打开。

### 4.5 WebDAV 客户端（`services/webdav_client.py`）

```python
@dataclass(frozen=True)
class WebdavAccount:
    account_id: str; name: str; base_url: str; username: str
    password: str; remote_dir: str = "Tube_Ultimate_Player/backups"

@dataclass(frozen=True)
class RemoteBackup:
    name: str; size: int; modified_at: str; href: str

class WebdavError(RuntimeError): ...

class WebdavClient:
    def test_connection(self) -> str        # 成功返回一句可读描述，含实际通道（直连 / 经代理 xxx）
    def ensure_dir(self) -> None            # 逐级 MKCOL，已存在（405/301）视为成功
    def upload(self, local: Path, remote_name: str, progress=None) -> None   # PUT
    def list_backups(self) -> list[RemoteBackup]                             # PROPFIND Depth:1
    def download(self, remote_name: str, local: Path, progress=None) -> None # GET
    def delete(self, remote_name: str) -> None                               # DELETE
```

实现要点：

- `urllib.request.Request` 支持 `method=`，PROPFIND/MKCOL/DELETE 直接传方法名即可，不需要自定义 handler。
- **抢占式 Basic 认证**：直接写 `Authorization: Basic …` 头。`HTTPPasswordMgr` 要等 401 挑战，很多 WebDAV 服务（含坚果云）对 PROPFIND 的挑战行为不一致，抢占式最稳。
- 传输层做成一个内部 `_Transport`：持有"直连 opener"（`build_opener(ProxyHandler({}))`，显式空映射，避免 urllib 去读环境变量里的 `http_proxy`）与"代理 opener"（`ProxyHandler({"http": proxy, "https": proxy})`，代理值取 `ConfigService.effective_proxy()`，与解析器/下载器同源），`User-Agent: Tube_Ultimate_Player/1.0`；控制类请求（OPTIONS/PROPFIND/MKCOL/DELETE）超时 15s，PUT/GET 超时 60s。
- **直连优先 + 3 次失败后转代理**（裁定 E）：每次请求先用直连 opener，最多尝试 3 次，间隔 1s / 2s。只有**网络层**失败才计数并重试——`URLError`、`socket.timeout`、`ConnectionError`、`http.client` 的连接类异常；`HTTPError`（401/403/404/405/507…）说明链路是通的、是服务器在回话，**立即抛出不重试也不转代理**，否则密码错会莫名其妙走上代理。
- 3 次直连都失败后：若 `effective_proxy()` 有代理地址，用代理 opener 再试 1 次并写 `logger.info("webdav fallback to proxy account=%s", …)`；成功后把该账号在**本次会话**内标记为"走代理"（`_prefer_proxy = True`），后续请求直接用代理，不再每次先撞 3 次直连——否则"列清单→下载→恢复"这条链路会重复付 3 次超时。标记只存在内存里，不落盘，重启即回到直连优先。
- 没配代理时，3 次失败后直接抛原始网络错误，文案追加"（已重试 3 次直连，且未配置代理）"；代理也失败时报"直连与代理均失败"，两条原因都带上。
- 上传/下载**传输中途**断掉：算 1 次失败，整个请求从头重试（不做 Range 续传），仍受同一套 3 次 + 转代理策略约束。
- 计数是"每次请求"而非"每个账号累计"：一次点击里的 3 次连接失败即触发回退，不需要用户点三下。
- PROPFIND 请求体只要 `<D:propfind><D:prop><D:getcontentlength/><D:getlastmodified/></D:prop></D:propfind>`，`Depth: 1`；响应用 `xml.etree.ElementTree` 解析 `{DAV:}response`，只保留文件名匹配 `tube-backup-*.zip` 的条目。**XML 解析只取需要的字段，不做 DTD/实体展开**（`ElementTree` 默认不解析外部实体）。
- 错误映射为中文：401 → "用户名或密码不正确（Nextcloud 请使用应用专用密码）"；403 → "服务器拒绝访问，请检查该账号对目录的写权限"；404 → "远程目录不存在"；405 → "服务器不支持该操作，请确认地址是 WebDAV 入口"；507/insufficient storage → "网盘空间不足"；`URLError` → 原因原文。
- 上传按块读写（1 MiB）并回报进度百分比；不做分片续传（备份包通常 < 10 MB）。

### 4.6 凭据存储（`services/secret_store.py` + `backup_targets.json`）

`CONFIG_DIR/backup_targets.json`（POSIX 下 `chmod 0600`，复用 `services/cookie_service.secure_cookie_file()` 的写法）：

```json
{"active_id": "a1b2", "accounts": [
  {"id": "a1b2", "name": "家里的 NAS", "base_url": "https://dav.example.com/dav",
   "remote_dir": "Tube_Ultimate_Player/backups", "username": "me",
   "password": "dpapi:BASE64…", "created_at": "2026-08-14T15:20:00+08:00"}]}
```

`secret_store.protect(text) / unprotect(token)`：

- Windows：ctypes 调 `CryptProtectData` / `CryptUnprotectData`（用户级绑定），前缀 `dpapi:`。已有的 `_dpapi_unprotect()` 逻辑照搬扩写。
- 其他平台：`cryptography` 的 Fernet，密钥放 `CONFIG_DIR/.backup_key`（`0600`），前缀 `fernet:`。
- 两者都不可用时退回 `plain:` 并写一条 warning 日志，同时在 UI 状态栏提示"当前平台无法加密保存密码"。**不做静默降级**。
- `unprotect` 遇到无法识别/解密失败的 token 返回空串，UI 提示"密码已失效，请重新输入"，不崩溃。

### 4.7 界面（`ui/backup_tab.py`）

```
备份/恢复
┌ WebDAV 服务器 ─────────────────────────────────────────────┐
│ [表格: 名称 | 服务器地址 | 用户名 | 远程目录]  ← 单选，选中行即当前使用 │
│ [新增] [编辑] [删除] [测试连接]                                │
└──────────────────────────────────────────────────────────┘
备份内容  配置、播放历史、收藏、播放列表、下载任务（固定）
          ☐ 包含 Cookie（含站点登录凭据，上传前请确认网盘可信）
[立即备份]  [从备份恢复]
状态：上次备份 2026-08-14 15:30 · tube-backup-0.2.25-20260814-153012.zip
进度：（MetaLabel，单行文案，如"正在上传 43%"）
```

- Tab 内不起线程：按钮点击 → 发信号 `test_requested(account)` / `backup_requested(account, include_cookies)` / `restore_list_requested(account)` / `restore_requested(account, remote_name)`。`SettingsPage` 原样向上转发，`MainWindow` 起 worker 并回调 `set_busy(bool, text)` / `set_progress(text)` / `show_backups(list)` / `report_result(ok, message)`。
- `WebdavAccountDialog`：五个输入框 + "测试连接"（同样通过信号交给主窗口跑 worker，对话框内只显示结果）+ 校验（名称非空、地址必须 http/https、用户名非空）。
- `BackupPickerDialog`：表格列"备份文件 / 时间 / 大小"，按时间倒序，双击或点"恢复"确认；恢复前二次确认框写明"将覆盖当前配置与数据，覆盖前会在本地留一份快照"。
- 底部全局"保存设置"按钮不参与本 Tab：WebDAV 账号在对话框点"确定"时立即落盘（它们不在 `user_config.json` 里），"包含 Cookie"勾选项写 `backup.include_cookies`，随全局保存走。

### 4.8 备份 / 恢复流程

**备份**（`BackupUploadWorker`）

1. `build_backup_archive()` 打包到 `RUNTIME_ROOT/backups/`（临时产物，成功上传后保留最近 3 份本地副本，其余删除）。
2. `client.ensure_dir()` → `client.upload()`（带进度）。
3. 上传成功后 `client.list_backups()`，按文件名时间戳倒序，删除第 21 个及以后的包；删除失败只警告不算备份失败。
4. 写 `backup.last_backup_at` / `backup.last_backup_name` 到配置，UI 刷新状态行。

**恢复**（`BackupRestoreWorker`）

1. `client.download()` 到 `RUNTIME_ROOT/backups/incoming-<名字>`。
2. 打开 ZIP：先对**每个条目**跑 `validate_archive_entry()`（防 zip-slip）；读 `manifest.json`，校验 `app`、`schema <= 1`、逐文件 sha256；`app_version` 高于当前版本时返回"需用户确认"给 UI 再问一次。
3. 把当前配置/数据打一份 `pre-restore-<时间戳>.zip` 到本地。
4. 解到临时目录，再逐个 `os.replace()` 到目标路径（同分区内原子替换；跨分区时先复制到目标目录下的 `.tmp` 再 replace）。Cookie 只在包里有且用户勾选时才覆盖。
5. 成功后 UI 弹"恢复完成，需要重启应用才能生效"，两个按钮：**立即重启** / **稍后重启**（裁定 D）。选"稍后重启"时状态行常驻显示"已恢复，重启后生效"，直到下次启动。

**重启实现（`services/restart_service.py`）**

```python
def restart_application(extra_args: list[str] | None = None) -> None:
    """拉起一个新的应用进程，随后由调用方退出当前进程。"""
```

- 命令行：frozen（PyInstaller）时为 `[sys.executable, *sys.argv[1:]]`；源码运行时为 `[sys.executable, sys.argv[0], *sys.argv[1:]]`（`sys.argv[0]` 即 `main.py`，为空则回退 `RUNTIME_ROOT/main.py`）。
- Windows 用 `subprocess.Popen(..., creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS, close_fds=True, stdin/stdout/stderr=DEVNULL)`；POSIX 用 `start_new_session=True`。这里可以用 `DETACHED_PROCESS`——`update_service._spawn_launcher` 之所以禁用它，是因为 PowerShell 脚本没有控制台就会空转退出（见该函数注释），本程序是 GUI 进程，不受此限。
- 调用顺序：先 `QApplication.quit()` 前的收尾（`MainWindow.close()` 触发既有 `closeEvent`，落盘配置、停播放器、存下载队列），再 `restart_application()`，再 `QCoreApplication.exit(0)`。仓库当前**没有单实例互斥**（已确认无 `QLocalServer`/mutex/锁文件），新旧进程短暂并存不会互相拒绝启动；但为避免旧进程的 SQLite 连接还没释放，新进程启动前先走完 `closeEvent`。
- `Popen` 失败时不退出当前进程，改为提示"自动重启失败，请手动重启应用"并保留"稍后重启"的常驻提示。工作目录固定为 `Path(sys.argv[0]).parent`（frozen 下为 exe 所在目录），避免继承到临时目录。

**失败与边界**

- 任一步失败：不留半截状态（第 4 步之前失败等于什么都没动），错误原文 + 建议写进对话框，同时 `logger.exception`。
- 第 4 步中途失败（极端：磁盘满）：提示"部分文件已替换，请用 `RUNTIME_ROOT/backups/pre-restore-*.zip` 恢复"，并把该路径写在弹框里。
- 无账号 / 未选账号时"备份/恢复/测试"禁用。
- 远程目录为空时"恢复"提示"该 WebDAV 上还没有备份包"。

### 4.9 配置新增

`config/default_config.json` 增加：

```json
"backup": {
  "include_cookies": false,
  "last_backup_at": "",
  "last_backup_name": ""
}
```

`.gitignore` 追加 `config/backup_targets.json` 与 `config/.backup_key`（运行时目录已被 `runtime/` 覆盖，这两条是源码目录的兜底）。

### 4.10 验收标准

1. 设置页出现第三个 Tab"备份/恢复"，布局与"快捷键"Tab 同层级。
2. 新增一个 WebDAV（如坚果云 `https://dav.jianguoyun.com/dav/`）后，"测试连接"在 UI 不卡顿的前提下给出成功提示；故意改错密码时提示"用户名或密码不正确…"。
3. 可添加第二、第三个 WebDAV；编辑后列表即时更新；删除后 `backup_targets.json` 里对应条目消失，选中项自动落到第一项。
4. `backup_targets.json` 里的 `password` 字段**不是明文**（Windows 上以 `dpapi:` 开头）；该文件不出现在任何备份包里。
5. 点"备份"：远端出现 `tube-backup-<版本>-<时间戳>.zip`；用任意解压工具打开，内含 `manifest.json`、`config/user_config.json`、`data/*.sqlite3`、`data/download_tasks.json`；未勾选 Cookie 时**没有** `cookies/` 目录。
6. 连续备份 21 次（或预置 21 个包）后，远端只剩最近 20 个，被删的是最旧的，且非本程序命名的文件未被删除。
7. 点"恢复"：弹出清单，条目按时间倒序且显示大小；选一个包恢复后弹"恢复完成，需要重启应用才能生效"，含"立即重启"与"稍后重启"两个按钮。
8. 点"立即重启"：应用自动关闭并重新起来（frozen 版与 `python main.py` 源码运行两种形态都验），重启后收藏/历史/播放列表/下载任务/设置与备份时一致；点"稍后重启"：弹框关闭、应用继续可用，状态行常驻"已恢复，重启后生效"，手动重启后数据同样正确。
9. 恢复前 `RUNTIME_ROOT/backups/` 下生成了 `pre-restore-*.zip`。
10. 篡改包内任一文件后恢复：报校验失败，且现有数据**未被改动**。
11. 构造含 `../` 条目的恶意 ZIP：恢复被拒绝，报路径穿越。
12. 备份/恢复/测试进行中，UI 可正常滚动、切 Tab；相关按钮置灰并显示进度文案。
13. 断网时点备份：给出网络错误提示（文案含"已重试 3 次直连"），不崩溃、无残留半截远端文件（PUT 失败即无文件）。
14. 代理回退（裁定 E）：填一个**直连不可达**但经代理可达的 WebDAV 地址并配好代理，点"测试连接"→ 3 次直连失败后自动经代理成功，提示里写明"经代理 …"，`logs/app.log` 有 `webdav fallback to proxy`；随后点"备份"**不再**重复 3 次直连（日志无第二组重试）。
15. 密码故意填错：**立即**报"用户名或密码不正确…"，不重试、不走代理（日志无 fallback）。
16. 未配置代理且直连不通：报错文案含"已重试 3 次直连，且未配置代理"。
17. 新增 `tests/test_webdav_client.py`、`tests/test_backup_archive.py`、`tests/test_secret_store.py`、`tests/test_restart_service.py` 全绿。

---

## 5. 测试计划

### 5.1 新增测试模块

| 模块 | 覆盖点 |
| --- | --- |
| `tests/test_default_quality_tier.py` | `select_quality_by_tier` 的高/中/低；奇偶档位数；同高度多 fps；单档；空表返回 `None`；`ConfigService.default_quality_tier()` 对 `Auto`/空/非法值/精确标签的归一化 |
| `tests/test_browse_source_switch.py` | `_set_browse_source` 在 `home`+有文本 / `home`+无文本 / `search`+有文本 / `search`+无文本 / 同站点重复切换 五种组合下的取向；首页留档仍被调用 |
| `tests/test_history_batch.py` | `HistoryRepository.remove/remove_many`（含空表、未知 id、同 id 多行）；`HistoryPage` 多选、批量按钮启用规则、"全部"= 可见行、删除确认；`MainWindow._download_history_records/_remove_history_records` 的 toast 文案与异常不外泄 |
| `tests/test_webdav_client.py` | 请求方法/头部/Basic 编码正确；PROPFIND multistatus XML 解析（含中文文件名、URL 编码 href）；只认本程序命名的包；HTTP 错误码 → 中文文案映射；**代理回退策略**：直连失败 3 次后才用代理、`HTTPError` 不重试不回退、回退成功后同一账号后续请求直接走代理、未配代理时抛原始错误并带重试说明（用假 opener 计数调用次数，不联网、不真睡（退避时间可注入）） |
| `tests/test_restart_service.py` | frozen / 源码运行两种形态下的命令行拼装；`Popen` 参数（分离进程、DEVNULL、cwd）；`Popen` 抛 `OSError` 时不退出且抛出可读错误（打桩 `subprocess.Popen`） |
| `tests/test_backup_archive.py` | 打包内容与 `manifest.json` 字段；sha256 校验通过/失败；恢复回环（临时目录里 config+db+tasks 完整还原）；zip-slip 拒绝；`schema` 过高拒绝；保留策略只留 20 个且只删本程序命名的包；文件名时间戳解析与排序 |
| `tests/test_secret_store.py` | `protect/unprotect` 回环；无法识别 token 返回空串；`plain:` 降级路径；密钥文件权限（POSIX） |

全部离线：WebDAV 用假 opener/假响应，不起真实网络；Qt 用例沿用 `QT_QPA_PLATFORM=offscreen`（与现有测试一致）。

### 5.2 需要更新的现有测试

- `tests/test_main_window_hardening.py::DefaultQualityTests`：`_state()` 的假 config 要提供 `default_quality_tier()` / `default_quality_label_override()`，并补一条"medium 选中间档"的用例。
- `tests/test_settings_shortcuts.py` / `tests/test_settings_cookie_save.py`：若因新增 Tab/控件失败则同步修正（预期仅构造函数层面影响）。
- `tests/test_favorite_batch.py`：**不应修改**——Mixin 重构后仍全绿是回归闸口。

### 5.3 手工验收

R1 的四档验证、R2 的五种切换组合、R3 的批量操作、R4 的 17 条（含真实 WebDAV 一次完整备份→恢复→立即重启，以及一次"直连不可达 + 代理可达"的回退实测）。R4 建议至少在一个真实服务上过一遍（坚果云或 Nextcloud），因为各家对 MKCOL/PROPFIND 的细节差异只能实测暴露。

---

## 6. 裁定结果（2026-08-14 用户已确认）

| 编号 | 议题 | 裁定 | 说明 |
| --- | --- | --- | --- |
| **A** | "中"档的口径 | **同意推荐**：按去重后的分辨率高度取中位，偶数取偏低 | `1080p60/1080p/720p60/720p/480p` → `720p60`；`1080p/720p` → `720p`。见 1.2 |
| **B** | 备份是否包含 Cookie | **同意推荐**：默认不含，提供勾选项 + 风险提示 | `backup.include_cookies` 默认 `false`。见 4.2 第 8 条 |
| **C** | 历史页读取上限 | **同意推荐**：50 → 200，"全部"仍 = 当前筛选后的可见行 | `recent()` 默认值不动，历史页显式传 200。见 3.2 第 6 条 |
| **D** | 恢复后的生效方式 | **调整**：弹框给"**立即重启**"和"**稍后重启**"两个选项（不是"立即退出"） | 新增 `services/restart_service.py` 负责拉起新进程；"稍后重启"保留常驻提示。见 4.8 |
| **E** | WebDAV 是否走代理 | **调整**：**先直连**；同一请求连续 3 次直连失败后，自动改用配置的代理重试 | 仅网络层失败计数，`HTTPError` 不重试不回退；回退成功后本次会话记住走代理。见 4.5 |

另需确认：本批功能计划随 **v0.2.25** 发布，发布前需新增 `docs/releases/v0.2.25.md`（发布工作流强制要求版本号与该文件匹配）；`app_version.txt` 当前已是 `0.2.25`（工作区未提交改动）。

---

## 7. 实施顺序与范围控制

1. **R2**（最小、纯行为修正）→ 单测 → 自查。
2. **R1**（配置 + 选档函数 + 设置页一行）→ 单测 + 更新既有用例。
3. **R3**（仓储删除方法 → Mixin 抽取 → 历史页 → 主窗口接线）→ 单测，重点看 `test_favorite_batch.py` 零修改通过。
4. **R4**（`secret_store` → `webdav_client`（含直连/代理回退）→ `backup_service` → `restart_service` → workers → UI）→ 单测 → 真实 WebDAV 手工验收（含一次代理回退实测）。
5. 全量 `python -m unittest discover -s tests -p "test_*.py"`；补 `docs/releases/v0.2.25.md`；本文第 8 节回填实施记录。

**明确不做**（避免范围蔓延）：定时/自动备份、增量备份、除 WebDAV 之外的备份通道（S3/FTP/本地导出——`build_backup_archive()` 已为将来的本地导出留好接口，但本次不出 UI）、多设备双向同步、备份包加密（网盘侧与传输侧 TLS 已覆盖主要威胁；如需再议）、把默认画质档位套用到收藏/历史/播放列表页的批量下载。

---

## 8. 实施记录

### 8.1 R2 站点切换

- 实现文件：`ui/main_window.py`。
- 测试文件：新增 `tests/test_browse_source_switch.py`，同步更新 `tests/test_home_cache_and_worker_lifetime.py` 的测试状态夹具。
- 实现与第 2 节方案一致：`_browse_mode` 记录前一个浏览动作，首页残留搜索文本不再触发重搜。

### 8.2 R1 默认画质

- 实现文件：`config/default_config.json`、`services/config_service.py`、`resolver/quality_selector.py`、`ui/settings_page.py`、`ui/main_window.py`。
- 测试文件：新增 `tests/test_default_quality_tier.py`，同步更新 `tests/test_main_window_hardening.py`。
- 实现与第 1 节方案一致：高/中/低按分辨率选择，`Auto` 按高处理，旧精确标签继续优先命中。

### 8.3 R3 历史批量操作

- 实现文件：`database/history_repository.py`、新增 `ui/library_batch.py`、`ui/favorite_page.py`、`ui/history_page.py`、`ui/main_window.py`。
- 测试文件：新增 `tests/test_history_batch.py`；`tests/test_favorite_batch.py` 与 `tests/test_list_source_search.py` 未修改并继续通过。
- 历史页显式读取最近 200 条；“全部”严格等于当前搜索筛选后的可见行。
- 在原方案的模块级 `_records_to_video_infos()` 之外，增加模块级 `_enqueue_library_records()` 统一收藏/历史的入队与 toast 汇总；行为口径不变。
- 历史删除与下载入口增加异常兜底，失败只提示并记录日志，不让异常逃回 Qt 事件循环。

### 8.4 测试结果

- 2026-08-14：`python -m unittest discover -s tests -p "test_*.py"`
- 结果：**719 tests，全部通过**。

### 8.5 R4 WebDAV 备份/恢复

- 新增服务：`services/secret_store.py`、`services/webdav_client.py`、`services/backup_targets.py`、`services/backup_service.py`、`services/restart_service.py`。
- 新增 worker 与界面：`workers/backup_worker.py`、`ui/backup_tab.py`；`ui/dialogs.py`、`ui/settings_page.py`、`ui/main_window.py` 完成账号管理、后台任务、恢复确认与重启接线。
- 备份使用 SQLite `Connection.backup()` 快照、ZIP manifest/sha256 校验、逐条目路径安全校验；本地保留 3 份、远端保留 20 份。
- WebDAV 凭据独立加密落盘，不进入备份包；Cookie 默认不备份。网络按直连 3 次失败后回退配置代理，并在会话内记住代理通道。
- 恢复前生成 `pre-restore-*.zip`；恢复成功后暂停旧进程的配置与下载队列落盘，避免退出时覆盖刚恢复的数据，并提供“立即重启 / 稍后重启”。
- 新增 `tests/test_secret_store.py`、`tests/test_webdav_client.py`、`tests/test_backup_archive.py`、`tests/test_restart_service.py`；后续复核补充了上传失败清理、凭据失效提示、manifest 严格校验和重启路径回退覆盖。真实 WebDAV 的连接、上传、代理回退和恢复已完成手工验收。
