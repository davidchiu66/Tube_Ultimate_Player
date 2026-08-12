# Tube_player 播放与列表体验增强方案（七项）

状态：**R1–R7 已全部编码完成并通过离线测试**（2026-08-10 审阅通过后实施，实施记录见第 12 节）；同批附带修复用户实测到的"中文字幕 HTTP 429"（记为 R8，见 12.3）。**R9（播放器"音轨"选择）为 2026-08-11 追加项，方案见第 13 节；13.10 的四项裁定已确认，等待编码指示——未开始编码。**
备案日期：2026-08-10（R7 追加于同日；第 12 节实施记录追加于同日；R9 追加于 2026-08-11）
基线版本：`app_version.txt` = 0.2.24

本文覆盖用户 2026-08-10 提出的六项修改与增强（R1–R6）、同日追加排查的 **R7 YouTube 字幕缺失**与实测暴露的 **R8 中文字幕 429**，以及 2026-08-11 追加的 **R9 播放器音轨选择**。按仓库既有约定，先落档备案；**审阅通过后再启动编码**。

审阅裁定见第 11 节，正文已按裁定结果更新（R5 范围因此扩大到播放器浮层）。

**R7 结论前置说明**：经实测取证，用户观察到的"YouTube 没有字幕"**主因不是程序缺陷**——那几个视频在 YouTube 侧确实没有任何字幕轨。但排查过程中发现了 3 处真实的实现问题与 1 处 UX 缺口，详见第 7 节。

**R9 结论前置说明**：加"音轨"下拉的过程中取证发现一个既有缺陷——多语言视频当前**播出的是随机语言**（实测某视频默认俄语）。R9 的主体是修掉它，下拉只是出口；因此方案触及选轨逻辑，并连带影响下载与投屏。**第 13.10 的四项裁定已于 2026-08-11 确认（A1 / B 同意 / C1 / D 跟随本地系统语言）。**

---

## 0. 需求清单与裁定摘要

| 编号 | 原始需求 | 关键裁定 |
| --- | --- | --- |
| R1 | 增加"进入播放默认窗口/全屏"配置项 | 新增 `player.playback_window_mode`，默认 `window`（保持现有行为）；全屏在 `mpv.load()` 成功后才切换 |
| R2 | "默认首页"改名"网站选择"，两个站点后加备注标签 | 仅改 UI 文案；配置键 `content.default_home` 与控件属性名保持不变；备注为两个单选按钮之后的**一个** `MetaLabel` |
| R3 | 左侧热区滑入"合集列表"浮层 | `PlaylistOverlay` 参数化左右方向复用；主窗口新增**独立**合集状态集；用 `_active_queue` 仲裁自动连播 |
| R4 | 收藏页增加下载选中/全部、删除选中/全部 | "全部"口径 = **当前搜索筛选后可见行**（与下载页 `_visible_task_ids` 一致） |
| R5 | 播放列表页去掉"加载"按钮，切换即加载 | 播放列表页与**两侧播放器浮层**一并去掉；所有下拉加未聚焦忽略滚轮；浮层加载不再跳页 |
| R6 | 播放界面显示"已收藏"/"已下载" | "已收藏"已存在，补齐失效场景；新增下载态六档文案，随 `task_changed` 实时联动 |
| R7 | 排查 YouTube 视频取不到字幕 | **实测证明主因是站点侧无字幕轨，非程序缺陷**；但需修掉 3 处真实实现问题并补"无字幕"可见提示 |
| R8 | 中文字幕 HTTP 429（用户实测追加） | 机翻轨走翻译接口紧配额；带退避重试 + 可执行文案 + "机翻"标签，**不静默换成英文轨**（见 12.3） |
| R9 | 控制面板"清晰度"后新增"音轨"选择（2026-08-11 追加） | 取证发现多语言视频当前播**随机语言**；R9 主体是修此缺陷，需改选轨逻辑并连带下载/投屏，**四项已裁定（A1 / B 同意 / C1 / D 跟随本地系统语言）见 13.10，等待编码指示** |

需要用户在审阅时特别确认的三处裁定：**R2 备注标签是一个还是两个**、**R4 "全部"是可见行还是整表**、**R5 是否连播放器浮层的"加载"按钮一起去掉**。三项均已确认，结论记入第 11 节，其余按本文方案执行。

R7 为同日追加项，其结论与方案见第 7 节，**待审阅**。

R9 为 2026-08-11 追加项，方案见第 13 节；13.10 的四项裁定已确认（A1 / B 同意 / C1 / D 跟随本地系统语言），**等待用户编码指示**。

---

## 1. R1 进入播放时的窗口/全屏默认状态

### 1.1 现状

- `MainWindow._toggle_fullscreen()` / `_enter_player_fullscreen()` / `_leave_player_fullscreen()`（`ui/main_window.py:1807-1834`）已实现完整的全屏进出，并用 `_was_maximized_before_fullscreen` 记录进入前是否最大化，退出时还原。
- 全屏只能由用户手动触发（按钮或 `fullscreen` 快捷键）。播放开始时不改变窗口状态。
- `_handle_playback_stop()`（`ui/main_window.py:1264`）在停止播放时已自动退出全屏。
- `config/default_config.json` 的 `player` 段目前只有 `default_quality` / `hardware_decode` / `volume` / `speed`。

### 1.2 完善后的需求

1. 新增配置项，取值二选一：**窗口**（默认）、**全屏**。
2. 选"窗口"时：进入播放不改变当前窗口状态，保持用户此刻的普通/最大化/几何原样（即"保存当前现状"）。
3. 选"全屏"时：开始播放后自动进入全屏播放。
4. 自动进入的全屏与手动全屏完全等价：`Esc`/全屏键/停止播放都能正常退出，并还原进入播放前的窗口状态。
5. 边界补充（原需求未提，本方案裁定）：
   - 解析失败时**不进入**全屏，避免用户对着黑屏全屏窗口看不到错误对话框。
   - 已处于全屏时不重复触发。
   - **DLNA 投屏中不自动进入全屏**：画面在电视上，本机全屏只会得到一个黑窗口。
   - 播放列表/合集自动连播切下一条时，若已在全屏则保持全屏，不做进出抖动。
   - 本地文件播放同样遵循该配置。

### 1.3 方案

**配置**

`config/default_config.json` 的 `player` 段新增：

```json
"playback_window_mode": "window"
```

`services/config_service.py` 新增读写辅助：

```python
def playback_window_mode(self) -> str:      # 归一化为 "window" | "fullscreen"
def playback_starts_fullscreen(self) -> bool:
```

未知值一律回退 `"window"`，保证老配置与人工误改不会导致意外全屏。

**设置页**

`ui/settings_page.py` 在"播放"分组内新增一行 `进入播放时`，两个单选按钮 `窗口` / `全屏`，控件命名 `playback_mode_window` / `playback_mode_fullscreen`，保存逻辑写入 `player.playback_window_mode`。

**主窗口**

新增一处集中入口，避免在四个播放路径里重复判断：

```python
def _apply_playback_window_mode(self) -> None:
    """播放成功建立后按配置决定是否进入全屏。"""
    if not self.config.playback_starts_fullscreen():
        return
    if self.isFullScreen() or self._casting_to_dlna():
        return
    self._enter_player_fullscreen()
```

调用点：
- `_resolved()` 中 `mpv.load()` 成功、`set_playback_available(True)` 之后（`ui/main_window.py:1037-1042` 之后）。
- `play_local_file()` 中本地媒体加载成功之后。

不在 `play_url()` 入口调用——那时还没解析成功。

### 1.4 验收标准

1. 全新安装（无 `user_config.json`）时，配置为"窗口"，播放行为与 0.2.24 完全一致。
2. 设置为"全屏"并保存后，从首页/搜索/收藏/历史/URL/播放列表/本地文件任一入口播放，出画面后自动全屏。
3. 自动进入全屏后按 `Esc`、全屏快捷键、点击"退出全屏"均可退出；退出后窗口回到播放前的普通/最大化状态与几何位置。
4. 设置为"全屏"时若解析失败，窗口保持原状，错误对话框可见可关闭。
5. 设置为"全屏"且正在 DLNA 投屏时开始播放，本机窗口不进入全屏。
6. 自动连播连续切换 3 条以上，全屏状态保持稳定，无闪烁或反复进出。
7. 设置为"窗口"时，播放前后 `isMaximized()` / `geometry()` 不变。

---

## 2. R2 "默认首页"改名"网站选择"并补备注

### 2.1 现状

- `ui/settings_page.py:169` 使用 `form.addRow("默认首页", default_home_row)`。
- 单选按钮 `default_home_bilibili` / `default_home_youtube`（`ui/settings_page.py:71-87`），其 `toggled` 直接驱动 `_switch_cookie_site("bilibili"/"youtube")`——即这个选项**同时**决定首页站点与 Cookie 编辑目标，用户要求的备注正是为了说明这一点。
- Cookie 输入框占位文案（`ui/settings_page.py:91-94`）写着"内容会保存到当前默认首页对应的网站 Cookie"。
- 配置键 `content.default_home`；`ConfigService.default_home_source()` / `default_home_label()` / `_normalize_cookie_site()` / `cookie_site_for_url()` 都依赖它。
- `tests/test_site_cookie_settings.py`、`tests/test_settings_cookie_save.py`、`tests/test_youtube_empty_result.py` 引用了 `default_home_*` 控件或 `content.default_home` 键。

### 2.2 完善后的需求

1. 设置页该行标签由"默认首页"改为"网站选择"。
2. 在 Bilibili 与 YouTube 两个选项**之后**追加备注：`涉及网站 Cookie 选择`。
3. 纯 UI 文案变更：**配置键 `content.default_home` 不改**，`ConfigService` 公开方法名不改，控件属性名不改——否则 3 个测试模块与用户既有 `user_config.json` 都要跟着动，收益为零。
4. 顺带把其他仍写"默认首页"的用户可见文案统一为"网站选择"（Cookie 输入框占位文案至少一处）。

### 2.3 方案

```python
self.default_home_hint = QLabel("涉及网站 Cookie 选择")
self.default_home_hint.setObjectName("MetaLabel")
default_home_row.addWidget(self.default_home_bilibili)
default_home_row.addWidget(self.default_home_youtube)
default_home_row.addWidget(self.default_home_hint)
default_home_row.addStretch(1)
```

`form.addRow("网站选择", default_home_row)`。

Cookie 占位文案改为"内容会保存到当前**网站选择**对应的网站 Cookie"。

**已确认**（2026-08-10 审阅）：两个单选按钮**共用一个**备注标签，即上述实现。单选按钮自身文本保持 `Bilibili` / `YouTube` 不变，故 `tests/test_site_cookie_settings.py` 中按文本查找控件的断言无需改动。

### 2.4 验收标准

1. 设置页显示"网站选择"，不再出现"默认首页"字样（含 Cookie 占位文案）。
2. 两个单选按钮右侧显示灰色备注"涉及网站 Cookie 选择"。
3. 切换站点仍正确切换 Cookie 编辑目标与探测结果显示，行为无回归。
4. `content.default_home` 读写不变；用 0.2.24 生成的 `user_config.json` 启动后选中项正确。
5. `tests/test_site_cookie_settings.py`、`tests/test_settings_cookie_save.py`、`tests/test_youtube_empty_result.py` 无需改动即通过。

---

## 3. R3 左侧"合集列表"浮层

这是本批次工作量与风险最大的一项，单独展开。

### 3.1 现状

**浮层**：`ui/playlist_overlay.py` 的 `PlaylistOverlay(QFrame)` 已具备本需求要的全部交互能力——标题、`meta_label` 空态文案、已保存列表下拉 + 加载/保存/删除、`自动连播` 复选框、多选列表、播放选中/下载选中/下载全部/取消、缩略图按可见区懒加载、`_signature_for()` 短路避免重复重建。

但它的方向是硬编码右侧：

```python
def _is_in_hot_zone(self, pos: QPoint) -> bool:
    return pos.x() >= parent.width() - 22

def _move_panel(self, animated: bool) -> None:
    visible_x = parent.width() - self.width() - 12
    hidden_x = parent.width() + 4
```

**宿主**：`ui/player_page.py` 中与浮层耦合的点共 6 处，新增面板必须逐一补齐，否则会出现"鼠标移入面板后控制条把它当作空闲隐藏"、"全屏后面板位置错乱"等问题：

| 位置 | 作用 |
| --- | --- |
| `__init__:228` | 创建实例 |
| `resizeEvent` | `relayout(self.rect())` |
| `_handle_mouse_move` | `handle_pointer(pos_in_self)` |
| `_handle_idle_timeout` | `handle_idle_timeout()` |
| `_set_cursor_hidden` | 面板也要跟随隐藏/恢复光标 |
| `eventFilter` | 白名单，避免面板内操作被判定为"空闲" |

**主窗口状态**：只维护**一个**活动播放列表——`current_playlist` / `current_playlist_index` / `current_playlist_key` / `current_playlist_auto_play`。`_handle_playback_finished()`（`ui/main_window.py:1268`）只看这一份状态决定连播。`_is_creator_playlist_request_current()` 还以 `current_playlist is None` 作为"可以生成作者列表"的前置条件。**直接把合集塞进这份状态会破坏作者列表与显式播放列表的既有语义**，必须另开一份。

**站点数据可用性**：

- Bilibili：`x/web-interface/view?bvid=<BV>` 一次请求即可拿到 `data.ugc_season`（合集，含 `sections[].episodes[]`，每条自带 `bvid` / `title` / `arc.duration` / `arc.pic`）与 `data.pages`（分 P）。`_resolve_video_pages_playlist()`（`resolver/site_resolver.py:405`）已经在调这个接口，只是当前只用了 `pages`。番剧另有 `_resolve_bangumi_season_playlist()`（`:475`），空间合集另有 `_resolve_space_season_playlist()`（`:639`）。**合集能力基本是现成的。**
- YouTube：**没有公开接口能回答"这个视频属于哪些播放列表"**。可用来源只有播放 URL 自带的 `&list=<id>`，以及 `raw_info` 里偶尔存在的 `playlist_id` / `album`（音乐视频）。

### 3.2 完善后的需求

**3.2.1 触发与呈现**

1. 播放器左侧同样保留约 22px 热区；鼠标移入后从左侧滑入面板，标题为 `合集列表`。
2. 面板结构、条目样式、缩略图懒加载、多选、播放选中/下载选中/下载全部/取消、保存/加载/删除、自动连播复选框，全部与右侧播放列表面板一致。
3. 当前视频若属于某个合集：加载该合集的全部视频（含当前视频本身），当前项高亮。
4. 当前视频不属于任何合集：面板可以正常滑入，列表为空，`meta_label` 显示"当前视频不属于任何合集"。
5. 左右两个面板**互斥显示**：打开一侧时另一侧收起。理由：`PANEL_WIDTH = 430`，两侧同开需要 ≥884px 宽，而窗口最小宽度可低至 640px，同开必然重叠遮挡视频。
6. 窄窗口自适应：面板实际宽度取 `min(430, max(280, host_width // 2 - 20))`。

**3.2.2 "合集"的站点定义（本方案裁定）**

Bilibili，按优先级：

1. `data.ugc_season` 存在 → 合集 = UGC 合集/季，标题取 `ugc_season.title`，条目遍历 `sections[].episodes[]`。
2. 无 `ugc_season` 但 `len(data.pages) > 1` → 合集 = 该稿件的全部分 P。裁定理由：用户在多 P 稿件里期待左侧能看到同稿件其他分 P，这与"合集"的直觉一致。
3. URL 属于 `/bangumi/play/` → 合集 = 番剧季，复用 `_resolve_bangumi_season_playlist()`。
4. 其余（单 P 无合集稿件）→ 空。

YouTube：

1. 播放 URL 携带 `&list=<id>` → 该 playlist 即合集。
2. `raw_info.playlist_id` 存在 → 用它。
3. 其余 → **空**。这是站点能力限制，不是实现缺陷；用户已明确允许空面板。此项写入第 8 节风险。

**3.2.3 与右侧面板的关系**

1. 左侧合集列表与右侧列表（显式播放列表 / 作者动态列表）**互不覆盖**，各自独立保有内容、索引、自动连播开关和已保存 key。
2. 内容可能重复（例如从 Bilibili 合集 URL 进入时，右侧是该合集、左侧也是该合集）。这是可接受的，两侧语义不同。但**不重复发请求**：若 `current_playlist.playlist_id` 与检测到的合集 ID 相同，直接复用其 `entries`。
3. 合集列表的生成**不再**以 `current_playlist is None` 为前置条件——显式播放列表内播放时，左侧合集面板照常工作。
4. 作者列表的现有优先级规则不变（仍只在 `current_playlist is None` 时生成）。

**3.2.4 自动连播仲裁（关键设计）**

新增 `self._active_queue: str`，取值 `"playlist"` / `"collection"` / `""`：

| 播放来源 | `_active_queue` |
| --- | --- |
| 右侧面板双击 / 播放列表页双击 / 播放列表自动连播 | `"playlist"` |
| 左侧合集面板双击 / 合集自动连播 | `"collection"` |
| 首页、搜索、收藏、历史、URL、本地文件 | `""` |

`_handle_playback_finished()` 改为：

```python
if self._active_queue == "collection" and self.current_collection_auto_play:
    → 播合集下一项
elif self._active_queue != "collection" and self.current_playlist_auto_play:
    → 播播放列表下一项      # "" 沿用现有作者列表行为，保持 0.2.24 语义
else:
    → 停在结束状态，等待重播
```

即：**谁最后驱动了当前播放，谁负责连播**。两个"自动连播"复选框互不干扰，不会出现两个队列同时推进的竞态。

**3.2.5 保存 / 加载 / 删除**

1. 复用 `PlaylistRepository`，`source_type="collection"`，保存流程与 `_save_active_playlist()` 一致（`QInputDialog` 取名、默认名用合集标题）。
2. 左侧下拉**只列出** `source_type == "collection"` 的已保存列表；右侧保持列出全部。避免两侧下拉互相污染。保存后的合集在**播放列表页**仍可见（该页列出全部），一个库不分裂。
3. 左侧加载已保存合集后：仅更新左侧面板内容 + toast 提示，**保持在播放器页、不打断当前播放、不自动开播**。与右侧 `_load_saved_playlist()` 会跳转到播放列表页的行为不同——左侧是播放中的浮层，跳页会打断观看。按 R5 裁定，右侧浮层同样改为不跳页（见 §5.2.8），两侧浮层语义因此统一；只有播放列表页保留跳页行为。
4. 自动连播开关变更写回 `playlists.set_auto_play_next(current_collection_key, ...)`（仅当该合集已保存）。

**3.2.6 加载时机、并发与失败反馈**

沿用 `docs/creator_videos_playlist_requirements.md` 已验证过的模式，包括其 §11.2 记录的踩坑（局部 lambda 转发导致信号丢失）：

1. `_resolved()` 中 `mpv.load()` 成功之后调度，`QTimer.singleShot(1200, ...)` 延迟启动，让首帧优先。
2. `_collection_generation` 递增令牌 + 当前 `video_id` 双校验；切视频、停止、本地播放立即作废旧令牌。
3. Worker 信号**直接携带** `generation` 与 `video_id`，直连 `MainWindow` 的 `@Slot` 绑定方法，**不使用局部 lambda**；主窗口在 `self._collection_workers[token]` 保留引用直到 `finished`。
4. `SiteResolver` 内新增 10 分钟 `_collection_cache`，key = 站点 + 合集 ID + `_config_fingerprint(site)`。
5. 失败反馈分级：
   - 站点接口失败且请求仍有效 → 一次非阻塞 toast「合集列表加载失败，当前视频继续播放」+ `logger.exception`。
   - **"不属于任何合集"只写 debug 日志，不弹 toast**。否则每播一个普通视频都提示一次，太吵。
   - 过期请求的失败只写 debug 日志。
6. 合集加载**不**调用 `PlayerPage.set_loading(True)`，**不**自动滑入面板，**不**触碰 `mpv`。

### 3.3 实施方案

**浮层参数化**（`ui/playlist_overlay.py`）

```python
def __init__(
    self,
    parent: QWidget | None = None,
    *,
    side: str = "right",              # "right" | "left"
    default_title: str = "播放列表",
    object_name: str = "PlaylistOverlay",
    empty_text: str = "当前没有可用的播放列表",
) -> None:
```

`_is_in_hot_zone()` 与 `_move_panel()` 按 `side` 分支：

| | right（现状） | left（新增） |
| --- | --- | --- |
| 热区 | `pos.x() >= parent.width() - 22` | `pos.x() <= 22` |
| 可见 x | `parent.width() - width - 12` | `12` |
| 隐藏 x | `parent.width() + 4` | `-width - 4` |

`relayout()` 中按宿主宽度夹取面板宽度。新增 `sibling_overlay` 弱引用，`show_overlay()` 时收起兄弟面板。

不新建子类：两个面板行为差异只有方向、标题和空态文案三项，参数化比继承更少重复。（R5 裁定去掉浮层"加载"按钮后，两侧按钮组完全一致，无需再区分。）

**QSS**（`resources/qss/dark_theme.qss:333-398`）

现有选择器扩展为并列形式，新面板直接复用配色：

```css
#PlaylistOverlay, #CollectionOverlay { ... }
#PlaylistOverlayList, #CollectionOverlayList { ... }
```

条目级 objectName（`PlaylistOverlayItem` 等）保持不变，两侧共用。

**Resolver**

`resolver/models.py`：`PlaylistInfo` 结构够用，不加字段。约定 `source_type = "collection"`，`playlist_id` 形如 `bilibili:ugcseason:<id>` / `bilibili:pages:<bvid>` / `bilibili:bangumi:ss<id>` / `youtube:playlist:<id>`。

`resolver/site_resolver.py` 新增统一入口：

```python
def resolve_collection_playlist(self, video: VideoInfo) -> PlaylistInfo | None:
    ...   # 缓存、分站点分发、去重、当前项定位、PlaylistInfo 组装
```

- `BilibiliResolver.resolve_collection(video)`：一次 `x/web-interface/view`，按 3.2.2 优先级产出；`/bangumi/play/` 走既有番剧路径。
- `YoutubeResolver.resolve_collection(video)`：按 3.2.2 从 URL `list=` 或 `raw_info.playlist_id` 取 id，复用 `resolve_playlist_generic()`。

`current_video_id` 设为当前视频 key，`_activate_collection()` 据此定位高亮索引（Bilibili 用 `_bilibili_video_key()` 兼容 BV/av/多 P 变体）。

**Worker**

新增 `workers/collection_worker.py` → `CollectionPlaylistWorker(QRunnable)`，`WorkerSignals` 携带 `(generation, video_id, PlaylistInfo | None)` / `(generation, video_id, str)` / `finished()`。只取扁平元数据，不解析媒体地址。

**PlayerPage**

新增 `self.collection_overlay = PlaylistOverlay(self, side="left", default_title="合集列表", object_name="CollectionOverlay", show_load_button=True, empty_text="当前视频不属于任何合集")`，并补齐 3.1 表格里 6 个耦合点、互设 `sibling_overlay`、`set_fullscreen()` 中两侧都 `relayout`。

新增信号 `collection_entry_requested` / `collection_download_requested` / `collection_save_requested` / `collection_load_requested` / `collection_delete_requested` / `collection_auto_play_changed`，以及方法 `set_collection_context()` / `clear_collection_context()` / `set_collection_saved_items()` / `set_collection_current_index()`。

**MainWindow**

新增状态：`current_collection` / `current_collection_index` / `current_collection_key` / `current_collection_auto_play` / `_active_queue` / `_collection_generation` / `_collection_workers`。

新增方法：`_activate_collection()` / `_clear_collection_context()` / `_play_collection_entry()` / `_schedule_collection_playlist()` / `@Slot _collection_loaded()` / `@Slot _collection_failed()` / `_invalidate_collection_request()` / `_save_active_collection()` / `_load_saved_collection()` / `_delete_saved_collection()` / `_set_collection_auto_play()`。

改造：`_handle_playback_finished()` 按 3.2.4 仲裁；`_play_playlist_entry()` 设 `_active_queue = "playlist"`；`play_url()` / `play_local_file()` / `_handle_playback_stop()` 重置 `_active_queue` 并作废合集令牌。

### 3.4 验收标准

1. 播放中鼠标移到左侧边缘，面板从左滑入，标题"合集列表"，外观与右侧面板一致（配色、条目、缩略图、按钮）。
2. Bilibili 合集稿件：左侧列出该合集全部视频，当前项高亮，条目顺序与站点一致。
3. Bilibili 多 P 稿件（无合集）：左侧列出全部分 P，当前 P 高亮。
4. Bilibili 番剧：左侧列出该季全部剧集。
5. Bilibili 普通单 P 无合集稿件：左侧面板可滑入，列表为空，提示"当前视频不属于任何合集"。
6. YouTube 带 `&list=` 播放：左侧列出该 playlist；不带 `list=` 的普通视频：左侧为空并给出同样提示。
7. 左侧双击条目正常解析播放；合集自动连播开启后自然结束能播下一条，关闭后停在结束状态等待重播。
8. 右侧播放列表/作者列表内容与索引在左侧加载前后**完全不变**；反之亦然。
9. 左右自动连播开关独立：只有驱动当前播放的那一侧生效，另一侧不参与连播。
10. 左侧支持保存（写入 `source_type="collection"`）、从左侧下拉加载（不跳页、不打断播放）、删除；左侧下拉不出现非合集的已保存列表。
11. 打开一侧面板时另一侧自动收起；窗口宽度 640px 时面板不遮挡超过一半画面、无横向溢出。
12. 合集加载期间当前视频不中断，不出现解析遮罩，播放/暂停/拖动/清晰度/字幕操作正常，播放位置持续递增。
13. 播放 A 后立即切 B，A 的合集结果不覆盖 B（日志出现"过期忽略"）。
14. 站点接口失败只出现一次非阻塞 toast 且当前视频继续播放；"不属于合集"不产生 toast。
15. 面板未打开时不产生批量缩略图请求；打开后只加载可见区。
16. 全屏进出后两侧面板热区与位置仍正确。

---

## 4. R4 收藏页批量操作

### 4.1 现状

`ui/favorite_page.py`（159 行）：`QTableWidget(0, 6)`，列为 标题/来源/作者/时长/收藏时间/操作；`SelectionMode.SingleSelection`；表头按钮 刷新 / 播放选中 / 删除收藏；每行内嵌 播放 / 删除；对外只有 `play_requested(str)` 与 `remove_requested(str)`；有 `search_edit` + `_apply_filter()`（按行隐藏实现筛选）。

`MainWindow._create_favorite_page()`（`ui/main_window.py:306`）只接了这两个信号。

`FavoriteRepository` 只有单条 `remove(video_id)`，无批量接口。

**可直接照搬的成熟范式**在 `ui/download_page.py`：`_visible_task_ids()` / `_selected_task_ids()` / `_candidates()` / `_emit_batch(action, *, selected_only)` / `_confirm_delete()` / `_update_batch_buttons()`，且其注释已明确写下"全部"取可见行的理由。

批量下载范式在 `MainWindow._download_playlist_entries()`（`ui/main_window.py:865`）。`DownloadManager.enqueue()` 自带按 `webpage_url` 去重（`download/download_manager.py:72-80`），重复入队只会提示"已完成下载"/"下载任务已存在"，不会产生重复任务。

### 4.2 完善后的需求

1. 表格改为 `ExtendedSelection`，支持 Ctrl/Shift 多选。
2. 新增批量按钮：`下载选中` / `下载全部` / `删除选中` / `删除全部`。
3. 现有"删除收藏"改名为"删除选中"（否则两个同义按钮并列），保留"刷新"与"播放选中"（多选时播第一条），保留每行内嵌的 播放/删除。
4. **"全部"= 当前搜索筛选后可见行**，与下载页口径一致；按钮 tooltip 明示条数（例如"下载当前列表中显示的 12 条"）。理由：用户搜完再点"删除全部"，期望删的几乎一定是眼前这些。**已于 2026-08-10 审阅确认。**
5. 删除前确认，文案说明"只删除收藏记录，不影响已下载的本地文件"。
6. 无可操作对象时给提示，不静默。
7. 单次入队超过 20 条时先确认，避免误点把整个收藏夹灌进下载队列。
8. 批量删除后自动刷新表格、播放器"已收藏"状态、以及首页卡片的收藏标记。

### 4.3 方案

**FavoritePage**

```python
download_videos_requested = Signal(list)   # list[dict]，收藏行原始数据
delete_videos_requested = Signal(list)     # list[str]，video_id
```

下载信号承载 dict 而非 id：构建下载任务需要 title/uploader/duration/thumbnail/source_site/webpage_url，收藏表已存全部字段，直接传避免主窗口回查数据库。

新增 `_visible_rows()` / `_selected_rows()` / `_emit_batch(action, *, selected_only)` / `_confirm_delete()` / `_confirm_bulk_download()` / `_update_batch_buttons()`，结构与 `download_page.py` 对齐；`itemSelectionChanged` 与 `textChanged` 都刷新按钮可用态。

**FavoriteRepository**

```python
def remove_many(self, video_ids: Iterable[str]) -> int:
    """单事务批量删除，返回实际删除条数。"""
```

不加 `clear()`：按 4.2.4 的可见行口径，"删除全部"也走 `remove_many`，多一个整表清空接口只会成为误用入口。

**MainWindow**

```python
def _download_favorite_videos(self, rows: list[dict]) -> None:   # dict → VideoInfo → _enqueue_download
def _remove_favorites(self, video_ids: list[str]) -> None:       # remove_many + 刷新三处视图
```

`_remove_favorites` 中若被删的 id 命中 `current_video.video_id`，同步 `player_page.set_favorite_state(False)`（与 R6 共用）。

### 4.4 验收标准

1. 收藏页可 Ctrl/Shift 多选，四个批量按钮按选中/可见状态正确置灰。
2. "下载选中"把选中行全部入队，下载页出现对应任务；已存在的任务只提示不重复创建。
3. "下载全部"在无搜索时覆盖整表；输入搜索词后只覆盖可见行，tooltip 条数与实际入队数一致。
4. 超过 20 条时先弹确认；取消则不入队任何任务。
5. "删除选中"/"删除全部"弹确认框，文案含"不影响已下载的本地文件"；确认后行消失、数据库记录消失。
6. 删除当前正在播放的视频后，播放器收藏按钮立即回到"收藏"可点状态。
7. 删除后首页卡片的收藏标记同步更新。
8. 空选中或空可见时点击按钮给出提示，不崩、不静默。
9. 现有单行 播放/删除、"播放选中"、搜索筛选、"刷新" 全部无回归。

---

## 5. R5 播放列表页与播放器浮层自动加载

### 5.1 现状

`ui/playlist_page.py`：

```python
self.load_saved_button = QPushButton("加载")                       # :55
self.load_saved_button.clicked.connect(self._load_saved)           # :127
self.saved_combo.currentIndexChanged.connect(self._update_button_state)  # :137
```

`set_saved_playlists()`（`:181`）在重填下拉时用 `blockSignals(True/False)` 包裹——这是自动加载能否安全落地的关键：程序化刷新绝不能触发加载。

`ui/playlist_overlay.py` 里有同构的一套：`self.load_button = QPushButton("加载")`（`:182`）、`self.load_button.clicked.connect(self._load_saved)`（`:226`）、`saved_combo.currentIndexChanged.connect(self._update_button_state)`（`:229`）、`set_saved_playlists()`（`:303`）同样用 `blockSignals` 包裹。

`tests/test_playlist_page.py` 直接断言 `page.load_saved_button.isEnabled()` 并调用 `page._load_saved()`，**必然随本项改动失败**，需同步改写。

**一个必须一并解决的既有行为**：`MainWindow._load_saved_playlist()`（`ui/main_window.py:908-921`）末尾有 `self.stack.setCurrentWidget(self.playlist_page)`。也就是说在播放器浮层里点"加载"会把用户从正在观看的视频**踢到播放列表页**。有确认按钮时这只是突兀；改成"切换即加载"后，一次滚轮误触就会中断观看。因此浮层的加载必须改为不跳页。

### 5.2 完善后的需求

1. 删除播放列表页的"加载"按钮。
2. **删除播放器左右两侧浮层的"加载"按钮**（审阅裁定：与播放列表页保持一致）。
3. 三处下拉均改为切换选中项即自动加载，无需二次点击。
4. 选中占位项"选择已保存列表"（key 为空）不触发加载。
5. 重复选中同一项不重复加载。
6. `set_saved_playlists()` 的程序化重填**绝不**触发加载。
7. 防误触：`QComboBox` 未展开时滚轮会直接改变选中项——去掉确认按钮后这等于"滚一下滚轮就换了整个列表"。三处下拉一律设置为未获焦点时忽略 `wheelEvent`。浮层里下拉紧邻长列表、误触概率更高，这一条是浮层去掉按钮的前置条件。
8. **浮层加载不跳页**：从浮层下拉加载已保存列表时，只更新浮层内容 + 非阻塞 toast，保持在播放器页，不打断当前播放、不自动开播。从播放列表页加载时保持现有行为（停留/切到播放列表页）。
9. 左侧合集浮层的加载语义与 §3.2.5 一致（下拉只列 `source_type == "collection"`，加载后同样不跳页、不打断播放）。

### 5.3 方案

**播放列表页**（`ui/playlist_page.py`）

- 移除 `load_saved_button` 及其布局与连接；`_update_button_state()` 中相关分支一并清理。
- `saved_combo.currentIndexChanged` 改接 `_handle_saved_selection_changed()`：

```python
def _handle_saved_selection_changed(self, _index: int) -> None:
    self._update_button_state()
    if self._suppress_saved_auto_load:
        return
    key = str(self.saved_combo.currentData() or "").strip()
    if not key or key == self._loaded_saved_key:
        return
    self._loaded_saved_key = key
    self.load_saved_requested.emit(key)
```

- 新增 `self._suppress_saved_auto_load = 0` 计数器，`set_saved_playlists()` 用它包裹整段重填（与 `blockSignals` 双保险：`blockSignals` 只挡信号，挡不住重填过程中对 `_update_button_state` 的直接调用链）。
- 新增 `self._loaded_saved_key = ""`，由 `MainWindow._load_saved_playlist()` 成功后经 `set_saved_playlists(current_key=...)` 回写。

**浮层**（`ui/playlist_overlay.py`）

- 移除 `load_button` 及其布局与连接；`combo_row` 变为 `saved_combo + save_button + delete_button`。§3.3 的 `show_load_button` 参数因此不再需要，不引入。
- 同样接入 `_handle_saved_selection_changed()` + `_suppress_saved_auto_load` + `_loaded_saved_key`，逻辑与页面版一致（两处代码同构，但分属不同类，不做提取——为三行判断引入公共基类不划算）。
- `_update_button_state()` 去掉 `load_button` 分支。

**滚轮防护**

新增一个小工具控件供三处复用：

```python
class NoScrollComboBox(QComboBox):
    """未获得焦点时忽略滚轮，避免误改选中项。"""

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
```

放在 `ui/widgets.py`（若无此模块则新建），`playlist_page.py` 与 `playlist_overlay.py` 的 `saved_combo` 改用它。同时 `setFocusPolicy(Qt.FocusPolicy.StrongFocus)`。

**主窗口不跳页**（`ui/main_window.py`）

`_load_saved_playlist()` 增加来源参数，默认保持现有行为：

```python
def _load_saved_playlist(self, playlist_key: str, *, switch_page: bool = True) -> None:
    ...
    if switch_page:
        self.stack.setCurrentWidget(self.playlist_page)
```

浮层信号改接 `switch_page=False` 的包装槽（`_load_saved_playlist_from_overlay`），并在成功后 toast 提示已加载的列表名；播放列表页信号仍接默认行为。左侧合集浮层走独立的 `_load_saved_collection()`（§3.3），本身就不跳页。

**测试**

改写 `tests/test_playlist_page.py`：把"按钮可用性"断言换成"手工切换触发一次 `load_saved_requested`、程序化 `set_saved_playlists()` 不触发、重复选中不重复触发"。浮层的同类断言放进 `tests/test_collection_overlay.py`（两侧浮层共用该文件）。

### 5.4 验收标准

1. 播放列表页与播放器左右两侧浮层均不再有"加载"按钮。
2. 三处下拉切到某个已保存列表后立即加载并渲染，无需再点任何按钮。
3. 选回占位项不触发加载、不清空当前列表。
4. 重复选中同一项不重复发起加载（日志只有一条 `playlist load` 记录）。
5. 保存新列表 / 删除列表导致下拉重填时，不发生任何自动加载。
6. 三处下拉未获得焦点时滚动鼠标滚轮，选中项不变；获得焦点后滚轮可正常切换。
7. **从播放器浮层加载已保存列表时不跳页**：仍停在播放器页，当前视频不中断、不重新开始，浮层内容更新并出现 toast。
8. 从播放列表页加载时行为与 0.2.24 一致。
9. `tests/test_playlist_page.py` 更新后全绿。

---

## 6. R6 播放界面"已收藏"/"已下载"标识

### 6.1 现状

**已收藏——已实现**：`PlayerPage.set_favorite_state()`（`ui/player_page.py:374-378`）已把按钮文案切为"已收藏"，并在 `_update_playback_buttons()`（`:691`）中以 `not self._favorite_active` 置灰。`MainWindow._resolved()`（`:1025`）与 `_favorite_current_video()`（`:1209`）都会调用。

缺口只有两处失效场景：
- 在收藏页/首页取消收藏当前正在播放的视频后，播放器按钮仍显示"已收藏"且置灰，用户无法重新收藏。
- `set_loading(True)` 时把 `_favorite_active` 重置为 `False`（`:360`），依赖后续 `_resolved()` 补回，切歌瞬间会闪一下"收藏"——可接受，不改。

**已下载——未实现**：只有 `set_download_available(available)`（`:370`）控制可用性，没有任何"已下载"态。`DownloadManager` 有 `_url_index` 与 `_find_by_url()`（`:307`），但**没有公开查询接口**；`_video_id_candidates()`（`:547`）已能兼容 Bilibili `bilibili:` 前缀与 BV/av/多 P 变体。

### 6.2 完善后的需求

1. 当前视频已收藏 → 按钮显示"已收藏"并置灰（已有，补齐失效场景）。
2. 当前视频已下载 → 按钮显示"已下载"并置灰，避免重复下载。
3. 中间态也要如实呈现，否则用户会在"排队中"时反复点：

| 任务状态 | 按钮文案 | 可点 |
| --- | --- | --- |
| 无任务 | 下载 | 是 |
| 已排队 / 下载中 | 下载中 | 否 |
| 已暂停 | 已暂停 | 是（点击提示任务已存在） |
| 已完成 | **已下载** | 否 |
| 失败 | 重新下载 | 是 |

4. 实时联动：下载完成的瞬间按钮即变"已下载"，无需切歌或重启。
5. 与既有 `set_download_available()` 语义正交：本地文件播放、投屏等场景仍不可下载，两者取与。
6. 匹配要兼容 Bilibili 的 id 变体（`bilibili:BVxxx`、`BVxxx`、`BVxxx:p2`、`avxxx`）。

### 6.3 方案

**DownloadManager**（`download/download_manager.py`）新增公开查询，保持 manager 薄、把文案解释权留给 UI：

```python
def task_for_video(self, video_id: str, webpage_url: str = "") -> DownloadTask | None:
    """按 URL 命中优先，回退到 video_id 变体匹配（复用 _video_id_candidates）。"""
```

**PlayerPage** 新增：

```python
def set_download_state(self, state: str) -> None:
    """state: "" | "queued" | "downloading" | "paused" | "completed" | "failed" """
```

内部存 `self._download_state`，按 6.2.3 表设置文案；`_update_playback_buttons()` 中：

```python
self.download_button.setEnabled(
    enabled and self._download_available and self._download_state not in {"queued", "downloading", "completed"}
)
```

不新增配色：与"已收藏"保持一致，只靠文案 + 全局 `QPushButton:disabled` 样式表达。

**MainWindow**

```python
def _sync_current_download_state(self, task: DownloadTask | None = None) -> None:
    """task 非空时仅在命中当前视频才刷新，避免每个任务变更都全量查询。"""
```

调用点：
- `_resolved()` 成功后（紧邻 `set_favorite_state` 一行）。
- `download_manager.task_added` / `task_changed` / `task_removed` 三个信号。
- `_enqueue_download()` 成功返回后。
- `play_local_file()` / `_handle_playback_stop()` 中复位为 `""`。

同时补齐收藏失效场景：`_remove_favorite()` 与 R4 的 `_remove_favorites()` 中，若命中 `current_video.video_id` 则 `player_page.set_favorite_state(False, available=True)`。

### 6.4 验收标准

1. 播放一个已收藏视频，按钮显示"已收藏"且置灰；播放未收藏视频显示"收藏"且可点。
2. 播放中点击"收藏"→ 立即变"已收藏"并置灰。
3. 在收藏页或首页取消收藏**正在播放**的视频 → 播放器按钮立即回到"收藏"可点。
4. 播放一个已下载完成的视频，按钮显示"已下载"且置灰。
5. 播放中点击"下载"→ 按钮变"下载中"并置灰；下载完成瞬间自动变"已下载"，无需任何刷新操作。
6. 暂停该任务 → 按钮变"已暂停"且可点；任务失败 → 变"重新下载"且可点。
7. 在下载页删除该任务记录 → 按钮回到"下载"可点。
8. Bilibili 多 P 稿件：对已下载的 P 显示"已下载"，切到未下载的 P 显示"下载"。
9. 本地文件播放与 DLNA 投屏中下载按钮仍按既有规则置灰，不被新状态覆盖。
10. 播放列表/合集内连续切换 5 条以上，收藏态与下载态每条都正确，无残留上一条的状态。

---

## 7. R7 YouTube 视频取不到字幕（追加排查项）

### 7.1 排查结论摘要

> **主因：那几个视频在 YouTube 侧本来就没有字幕轨，程序链路是通的。**

这一条是本次排查最重要的结论，因为它决定了"该不该改代码"。用户报告的现象真实存在，但**不是**程序丢失了字幕——是站点没有给。已用同一条命令在已知有字幕的视频上取回 **945 条字幕**并成功落地成文件，证明解析、过滤、下载、落盘全链路正常。

同时排查发现 3 处**真实的实现问题**和 1 处 **UX 缺口**：它们没有造成本次现象，但都是随时会造成"字幕莫名消失"的隐患，且其中一处让本次排查多花了大量时间。建议一并修掉。

### 7.2 取证过程与证据链

**证据 1：日志呈现完美的站点分野**

`%LOCALAPPDATA%\Tube_Ultimate_Player\logs\app.log`：

- 所有 Bilibili 视频：`subtitles=1` ~ `subtitles=6`
- 所有 YouTube 视频：`subtitles=0`（`Cn5qycUIjYE`、`d_5GYmICVTk`、`hQguBUKMcVs`、`-I4xrWf33is` 四条无一例外）

**证据 2：解析器收到的是空字典，不是"格式不支持"**

`SubtitleParser._append()` 在"有轨道但格式不被支持"时会打 `unsupported subtitle formats skipped`。全量 grep 该日志 → **零命中**。这条**反证**把责任范围从解析层上推到了 yt-dlp 输出层：解析器收到的原本就是 `{}`。

**证据 3：用应用自己构造的命令实测**

直接调用 `YoutubeResolver._build_command()` 产出的**完全相同**的命令行（含代理、Firefox cookies、`--js-runtimes`）：

| 视频 | rc | `subtitles` | `automatic_captions` |
| --- | --- | --- | --- |
| `hQguBUKMcVs` | 0 | 0 | 0 |
| `Cn5qycUIjYE` | 0 | 0 | 0 |
| `d_5GYmICVTk` | 0 | 0 | 0 |
| `-I4xrWf33is` | 0 | 0 | 0 |
| **`dQw4w9WgXcQ`（对照组）** | 0 | **5** | **940** |

对照组用**同一条命令、同一个 cookie、同一个代理、同一个 JS runtime** 取回了 945 条轨道。**这一行直接排除了配置、cookie、代理、JS runtime、命令构造、yt-dlp 版本的全部嫌疑。**

**证据 4：直接检查 YouTube 返回的原始播放器响应**

用 `--write-pages` 落盘 yt-dlp 实际收到的报文，再用 JSON 解码器抽取 `captionTracks` 数组：

| 视频 | watch 页 `captionTracks` |
| --- | --- |
| `hQguBUKMcVs` | **0 条** |
| `dQw4w9WgXcQ` | **6 条**（en 手动、en asr、de-DE、ja、pt-BR、es-419） |

站点返回的原始报文里就没有字幕轨。**这是决定性证据。**

**证据 5：换遍所有播放器客户端仍然为 0**

对 `hQguBUKMcVs` 逐个尝试 `web` / `tv` / `web_embedded` / `mweb` / `web_safari`（`ios` / `android` / `tv_simply` 因格式不可用而失败）——**全部 `subs=0 auto=0`**。不是客户端选择问题。

**证据 6：面上的普遍性**

随机取 5 条中文科技类视频实测：1 条有字幕（`subs=1 auto=157`），4 条为 0。**中文自媒体视频不上传字幕、且未被 YouTube 自动生成 ASR 轨，是相当普遍的情况**——尤其是发布时间很近的视频（本次 4 条的 `upload_date` 为 2026-07-31 ~ 2026-08-09，ASR 轨可能尚未生成）。

**证据 7：端到端落地验证**

对有字幕的视频走完整链路 `resolve()` → `SubtitleParser` → `materialize_subtitle()`：

```
VideoInfo.subtitles = 945
OK  en:manual     ext=srt bytes=4155
OK  de-DE:manual  ext=srt bytes=3898
OK  ja:manual     ext=srt bytes=2794
```

**字幕能正确解析、下载、落盘成 mpv 可加载的 SRT。功能是好的。**

### 7.3 排查中发现的真实问题

以下 4 项都**不是**本次现象的成因，但都是真实缺陷。

**P1（UX 缺口，建议必修）：无字幕时用户得不到任何解释。**

当前 `subtitle_combo` 只剩一个"关闭"选项，`meta_label` 显示"字幕 0 个"。用户无法区分"这个视频没有字幕"与"程序坏了/没配好"——本次工单正是这个歧义的直接产物。这是**用户实际受损的点**，也是 R7 真正该交付的东西。

**P2（真实缺陷）：四个字幕参数在 `--dump-single-json` 下全是空转。**

`_build_command()` 里的：

```python
"--write-subs", "--write-auto-subs",
"--sub-langs", ",".join(languages),
"--sub-format", "vtt/srt/best",
```

这四个参数只影响 `requested_subtitles` 与**实际下载文件**，对 `subtitles` / `automatic_captions` 两个原始字典**没有任何过滤作用**——实测 `--sub-langs zh-Hans,zh-Hant,zh,en` 之下 `automatic_captions` 依然返回全部 940 种语言。也就是说 `youtube.subtitle_languages` 这个配置项**当前完全不起作用**，是个纯粹的摆设。删掉这四个参数后输出一字不差（已实测）。

危害有二：一是每次解析都白白多解析一份 `requested_subtitles`；二是**它制造了"字幕已经配置好了"的假象**，让排查时天然倾向于认为链路没问题。

**P3（潜在缺陷）：`PREFERRED_EXTS` 过窄，可能整批静默丢弃字幕。**

`SubtitleParser.PREFERRED_EXTS = ("srt", "vtt", "ass", "ssa")`，而 `_select_entry()` 在没有命中这四种时**返回 `None`**。实测 YouTube 单条轨道同时提供 `json3 / srv1 / srv2 / srv3 / ttml / vtt / srt` 七种格式，当前必定命中 `srt`，所以现在是安全的。但**只要 YouTube 哪天停供 srt/vtt，全部字幕会在解析层被静默丢光**，表现与本次一模一样，且极难排查。这是一颗定时炸弹。

**P4（可观测性缺口，本次排查的实际成本来源）：没有"原始轨道数"的正面日志。**

现在只有 `subtitles=0` 这个**结果**日志。"站点没给"与"解析器全丢了"两种截然不同的故障产生**完全相同的日志**。本次是靠"某条 info 日志不存在"这种反证才定位的，成本很高且不可复制。

### 7.4 方案

**P1 无字幕提示（核心交付）**

`ui/player_page.py`：

1. `_populate_subtitle_combo()` 在 `subtitles` 为空时，把唯一项文案由"关闭"改为 **"无可用字幕"** 并 `setEnabled(False)`；非空时恢复"关闭"与可用态。
2. `update_video_info()` 的 `meta_parts` 中，`字幕 0 个` 改为 **`无字幕`**，非零时维持 `字幕 N 个`。

`ui/main_window.py`：在 `_resolved()` 中，若 `video.source_site == "youtube"` 且 `not video.subtitles`，打一条 `logger.info`（**不弹 toast**——绝大多数视频没字幕是常态，弹窗会变成骚扰）。

裁定：**只做静默的可见性改善，不做主动打扰**。用户是在主动找字幕时才会看下拉框，那时"无可用字幕"就足以解释一切。

**P2 移除空转参数**

`resolver/youtube_resolver.py::_build_command()` 删除 `--write-subs` / `--write-auto-subs` / `--sub-langs` / `--sub-format` 四个参数（`--dump-single-json` 下无作用，已实测删除前后输出一致）。

`youtube.subtitle_languages` 配置项的处置**留给审阅裁定**（见 7.6 待确认项）。

**P3 放宽格式支持**

`resolver/subtitle_parser.py`：

1. `PREFERRED_EXTS` 扩为 `("srt", "vtt", "ass", "ssa", "ttml", "srv3", "srv2", "srv1", "json3")`，按 mpv 兼容度排序——前四种优先级不变，**只在前四种都拿不到时**才回退到后面几种。行为完全向后兼容。
2. `_select_entry()` 末尾的 `return None` 改为**回退返回第一条可用条目**，保证"有内容就不会被静默丢弃"，最坏情况是 mpv 加载失败并给出明确报错——**明确的失败远好过静默的消失**。

**P4 补正面日志**

`SubtitleParser.parse()` 增加一条 `logger.debug`，记录**原始**语言数与**解析后**轨道数：

```python
logger.debug(
    "subtitle parse raw_manual=%s raw_auto=%s parsed=%s",
    len(subtitles or {}), len(automatic_captions or {}), len(parsed),
)
```

有了这条，"站点没给"（`raw_*=0`）与"解析器丢了"（`raw_*>0` 而 `parsed=0`）一眼可分。

### 7.5 验收标准

1. 播放一个**确实没有字幕**的 YouTube 视频：字幕下拉显示"无可用字幕"且不可点，meta 显示"无字幕"，**不弹任何提示框**，日志有一条 info 说明。
2. 播放一个**有字幕**的 YouTube 视频（如 `dQw4w9WgXcQ`）：下拉正常列出前 12 条 + "更多字幕…"，选中后字幕正常显示在画面上。
3. Bilibili 字幕（含 AI 字幕内联 `data` 的情形）**无任何回归**，弹幕（`danmaku` / `xml`）仍被正确排除。
4. 删除四个字幕参数后，`resolve()` 返回的 `subtitles` 数量与删除前**完全一致**（回归对比测试）。
5. 构造一个只含 `json3` / `srv3` 的样例，解析后**能拿到轨道**而不是空字典（P3 单测）。
6. 构造一个只含 `xml`（弹幕）的样例，解析后仍为空（排除逻辑不被 P3 放宽破坏）。
7. 日志中能通过 `raw_manual` / `raw_auto` / `parsed` 三个数字区分"站点没给"与"解析丢弃"。
8. 切换到"无字幕"视频再切回"有字幕"视频，下拉状态与可用性正确复位，无残留。

### 7.6 待确认项（需审阅裁定）

**`youtube.subtitle_languages` 配置项如何处置？** 该项当前**完全不起作用**（P2）。三个选项：

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A（推荐）** | 保留配置项，改为**在 `SubtitleParser` 排序阶段真正生效**——把用户配置的语言提到下拉框最前面，取代当前写死的 `PREFERRED_LANGUAGE_PREFIXES = ("zh","yue","en")` | 让配置项名副其实，且直接改善"940 条里找不到想要的那条"的实际体验 |
| B | 直接删除该配置项 | 最干净，但用户既有 `user_config.json` 里有这个键，删除等于承认它一直是坏的 |
| C | 原样保留不动 | 零成本，但留着一个永远不生效的配置项，下次还会误导排查 |

本方案倾向 **A**：它把一个"假配置"变成"真配置"，成本只有排序函数的十几行。

> **实施时的处置（未获单独裁定，按下述假设执行，可随时回退）**：编码阶段按 **A** 落地。理由是 P2 已经删掉了名义上消费该键的四个 yt-dlp 参数，若不同时改造，等于明知故犯地留下一个永远不生效的配置键。改动面很小——`SubtitleParser.parse()` 增加一个 `preferred_languages` 参数、新增 `_language_prefixes()`、`_sorted()` 改成按传入前缀排序，`YoutubeResolver._parse_info()` 把配置传进去；退回 B 或 C 只需删掉这四处。若审阅认为应选 B/C，请在此处批注。

### 7.7 风险与限制

1. **"没有字幕"是 YouTube 的常态，不是可以被修复的缺陷。** 中文自媒体视频尤其明显（实测抽样 5 条中 4 条无字幕）。R7 交付的是**准确的告知**，不是"让所有视频都有字幕"。验收时须按此预期判定。
2. **新发布视频的 ASR 轨可能延迟生成。** 本次 4 条视频发布于 2026-07-31 ~ 08-09，过一段时间后可能自行出现自动字幕。这意味着**同一条 URL 在不同时间的字幕数可能不同**，验收取证时需记录时间。
3. **P3 放宽格式后，mpv 可能加载失败。** `json3` / `srv3` 是 YouTube 私有格式，mpv 不认。回退逻辑保证"不静默丢弃"，但用户可能看到加载失败提示。这是**有意的权衡**：明确失败优于静默消失。若后续实际出现，再补一个 json3 → SRT 的转换器。
4. **P2 删除参数属于行为变更。** 虽已实测输出一致，仍须保留一条回归断言（验收第 4 条），防止未来 yt-dlp 版本改变这些参数在 `--dump-single-json` 下的语义。

---

## 8. 实施范围

**修改**

| 文件 | 涉及需求 |
| --- | --- |
| `config/default_config.json` | R1 |
| `services/config_service.py` | R1 |
| `ui/settings_page.py` | R1, R2 |
| `ui/playlist_overlay.py` | R3, R5 |
| `ui/player_page.py` | R1, R3, R6, R7 |
| `ui/main_window.py` | R1, R3, R4, R5, R6, R7 |
| `ui/favorite_page.py` | R4 |
| `ui/playlist_page.py` | R5 |
| `database/favorite_repository.py` | R4 |
| `download/download_manager.py` | R6 |
| `resolver/site_resolver.py` | R3 |
| `resolver/youtube_resolver.py` | R3, R7 |
| `resolver/subtitle_parser.py` | R7 |
| `resources/qss/dark_theme.qss` | R3 |

**新增**

- `workers/collection_worker.py`
- `ui/widgets.py`（`NoScrollComboBox`，若已有同类模块则并入而不新建）
- `tests/test_playback_window_mode.py`
- `tests/test_collection_overlay.py`（含两侧浮层的自动加载与滚轮防护断言）
- `tests/test_collection_playlist.py`
- `tests/test_favorite_page_batch.py`
- `tests/test_player_download_state.py`
- `tests/test_youtube_subtitle_absence.py`（R7：无字幕 UI 态、`json3`/`srv3` 回退、弹幕仍排除、参数删除前后一致性）

**改写**

- `tests/test_playlist_page.py`（R5 必改）

**不修改**

- mpv 播放内核与媒体 URL 选择逻辑
- 数据库表结构（`remove_many` 只是新查询，`source_type="collection"` 复用既有列）
- 已保存播放列表持久化格式
- 下载任务模型与 yt-dlp 命令构造（**R7 的四个字幕参数除外**）
- `content.default_home` 配置键与 `ConfigService` 现有公开方法签名
- DLNA 投屏链路
- `services/subtitle_service.py`（R7 实测证明其下载与落盘链路正常，无需改动）

> 实施时的例外：`services/subtitle_service.py` 最终**被修改了**，但不是因为 R7——是为附带修复的 R8（中文机翻字幕 429），见 12.3。R7 本身确实没有改动它。同理，`ui/toast.py`、`resolver/models.py` 也因 R8 进入改动清单。

**发布物**：当前 `app_version.txt` = 0.2.24。本批次预计作为 0.2.25 发布，届时需按仓库约定补 `docs/releases/v0.2.25.md`（发布流程会校验其存在）。版本号与发布时机由用户决定，本方案不预先改动 `app_version.txt`。

---

## 9. 风险与限制

1. **YouTube 合集覆盖面有限**（R3）。没有公开接口能回答"该视频属于哪些播放列表"，只能覆盖 URL 自带 `list=` 与 `raw_info.playlist_id` 两种情形。普通 YouTube 视频的左侧面板将长期为空。这是站点能力边界，需在验收时按此预期判定，而不是当作缺陷。
2. **左右面板并存的交互复杂度**（R3）。两个浮层 + 控制条 + 自动隐藏光标 + 全屏切换共同作用，`eventFilter` 与 `_set_cursor_hidden` 任一处漏改都会表现为"鼠标在面板上时控制条乱闪"。已在 3.1 列出全部 6 个耦合点逐项核对。
3. **自动连播仲裁是新引入的状态机**（R3）。`_active_queue` 若在某条播放路径上忘记设置，表现为"视频结束后不连播"或"连播到了另一个列表"。必须为三种取值各写一条测试。
4. **Bilibili 接口与风控**（R3）。`x/web-interface/view` 的 `ugc_season` 字段结构可能变化，合集/番剧接口可能触发风控。失败必须隔离到"仅左侧面板为空 + 日志"，不得影响播放。
5. **去掉确认按钮后的误触**（R5）。滚轮改选中项是 `QComboBox` 的默认行为，去掉"加载"按钮后其后果从"选错了再点一次"升级为"直接换掉整个列表"。审阅裁定连播放器浮层的"加载"按钮一并去掉，浮层下拉紧邻长列表、误触概率更高，因此 `NoScrollComboBox` 与"浮层加载不跳页"是这项裁定的**强制前置条件**，不可省略。验收第 6、7 条为专项验证。
6. **批量下载的资源冲击**（R4）。收藏夹可能有数百条；一次全量入队会长时间占用网络。已用 20 条确认阈值兜底，但 `download.max_concurrent` 仍是最终闸门。
7. **"已下载"判定依赖任务记录而非文件存在性**（R6）。用户手动删除本地文件后，任务记录仍是 completed，按钮仍显示"已下载"。`DownloadManager._import_completed_files()` 与 `_find_downloaded_file()` 已有一定自愈能力，但本项不额外做文件存在性探测——每次切歌做磁盘 IO 不值得。此限制写入验收说明。
8. **测试环境限制**。R3 的站点接口、R4 的真实下载、R1 的全屏行为都需要联网/有窗口环境。离线自动化只能覆盖 JSON 转换、状态机、几何计算与信号触发；接口连通性、风控兜底、全屏观感必须人工验收。
9. **R7 的"无字幕"不可修复**（详见 7.7）。YouTube 侧没有字幕轨时，任何客户端、cookie、JS runtime 组合都取不到——已实测排除。R7 交付的是准确告知与隐患修复，不是"让所有视频都有字幕"。

---

## 10. 验证方法

### 10.1 静态与自动化

```powershell
python -m py_compile config/default_config.json ; # 仅 JSON 语法由测试断言覆盖
python -m py_compile services/config_service.py ui/settings_page.py ui/playlist_overlay.py ui/player_page.py ui/main_window.py ui/favorite_page.py ui/playlist_page.py database/favorite_repository.py download/download_manager.py resolver/site_resolver.py resolver/youtube_resolver.py resolver/subtitle_parser.py workers/collection_worker.py
python -m unittest discover -s tests -p "test_*.py" -v
```

离线可覆盖的断言：

1. `player.playback_window_mode` 读写与非法值回退；`_apply_playback_window_mode()` 在四种前置条件下的进/不进全屏。
2. 设置页"网站选择"文案与备注 label 存在；`content.default_home` 键未变。
3. Bilibili `ugc_season` / `pages` 样例 JSON → `PlaylistEntry`；分 P 兜底；YouTube `list=` 路径；合集 ID 与 `playlist_id` 命名。
4. 左侧热区判定、左右几何镜像、窄窗口宽度夹取、互斥显示。
5. `_active_queue` 三种取值下 `_handle_playback_finished()` 的分支选择。
6. 合集令牌有效/过期/停止后三种状态；Worker 局部变量离开作用域后结果仍能投递（沿用作者列表的生命周期测试范式）。
7. 收藏页可见行/选中行口径、多选、批量信号载荷、`remove_many` 返回值。
8. 播放列表页与浮层：手工切换触发一次加载、程序化重填不触发、重复选中不重复触发、未聚焦滚轮不改选中项、浮层加载不跳页。
9. 下载态六档文案与可点性；`task_changed` 命中/未命中当前视频的刷新行为。
10. R7：空字幕 → 下拉"无可用字幕"且不可点、meta"无字幕"；仅 `json3`/`srv3` 样例能解析出轨道；仅 `xml` 弹幕样例仍为空；`parse()` 的 `raw_*`/`parsed` 日志字段存在。

### 10.2 人工验收矩阵

- **R1**：窗口/全屏两档 × 首页/搜索/URL/收藏/历史/播放列表/本地文件七个入口；解析失败、投屏中、连播中三个边界。
- **R2**：设置页外观 + 站点切换后 Cookie 目标正确 + 老配置兼容。
- **R3**：Bilibili 合集稿件 / 多 P 稿件 / 番剧 / 普通单 P；YouTube 带 `list=` / 不带；左右独立性、连播仲裁、保存加载删除、互斥显示、窄窗口、全屏进出、切视频竞态、接口失败。
- **R4**：无搜索/有搜索 × 选中/全部 × 下载/删除；20 条阈值；删除当前播放中视频。
- **R5**：页面与左右浮层三处下拉 × 切换即加载 / 占位项 / 重复选中 / 程序化重填 / 滚轮误触；浮层加载不跳页且不打断播放。
- **R6**：收藏四态、下载六态、下载完成瞬间联动、多 P 分别判定、本地/投屏场景。
- **R7**：无字幕 YouTube 视频（如 `hQguBUKMcVs`）× 有字幕 YouTube 视频（如 `dQw4w9WgXcQ`）× Bilibili 有字幕/AI 字幕/纯弹幕稿件；两类视频间来回切换的状态复位；选中字幕后画面确实出字。

---

## 11. 审阅结论记录

审阅日期：2026-08-10。三项待定裁定用户已全部确认：

| 项 | 问题 | 用户裁定 |
| --- | --- | --- |
| R2 | 备注标签一个还是两个 | **两个单选框共用一个备注标签**（采纳本方案） |
| R4 | "全部"是可见行还是整表 | **当前搜索筛选后可见行**（采纳本方案，与下载页一致） |
| R5 | 是否连播放器浮层的"加载"按钮一起去掉 | **一起去掉，保持一致**（否决本方案的"浮层保留"） |

R5 裁定导致的方案变更（正文已同步更新）：

1. 去掉范围从播放列表页扩大到播放器左右两侧浮层，共三处下拉。
2. `NoScrollComboBox`（未聚焦忽略滚轮）从"建议"升级为**强制前置条件**，三处下拉全部适用。
3. 新增一项原方案没有的改动：`MainWindow._load_saved_playlist()` 增加 `switch_page` 参数，**浮层加载不再跳转到播放列表页**。原因是浮层里点"加载"会把用户从正在观看的视频踢走，有确认按钮时只是突兀，改成"切换即加载"后一次滚轮误触就会中断观看。
4. `PlaylistOverlay` 的 `show_load_button` 参数取消（两侧按钮组已完全一致）。
5. 实施范围新增 `ui/widgets.py`；`ui/playlist_overlay.py` 与 `ui/main_window.py` 的涉及需求增加 R5。

结论：**R1–R6 方案定稿，可启动编码。R7 为同日追加项，其结论（主因是站点侧无字幕）与 7.6 的配置项处置裁定待审阅。**

---

## 12. 实施记录（2026-08-10 编码完成）

用户在审阅通过后指示："你开始根据方案进行编码，最后附带解决这个问题"——其中"这个问题"指同时报告的中文字幕 HTTP 429，本节记为 **R8**。

实施顺序：R2 → R5 → R1 → R6 → R4 → R3 → R7 → R8（先小后大、先独立后耦合，R3 放在最后是因为它要用到 R5 改造后的浮层）。

### 12.1 R1–R7 交付状态

| 编号 | 状态 | 主要落点 | 专项测试 |
| --- | --- | --- | --- |
| R1 | 已交付 | `config/default_config.json`、`services/config_service.py`、`ui/settings_page.py`、`ui/main_window.py` | `tests/test_playback_window_mode.py`（9） |
| R2 | 已交付 | `ui/settings_page.py`（仅文案 + 一个共用备注标签，配置键未动） | `tests/test_site_cookie_settings.py`（4，含"键未变"断言） |
| R3 | 已交付 | `resolver/site_resolver.py`、`resolver/youtube_resolver.py`、`workers/collection_worker.py`、`ui/playlist_overlay.py`、`ui/player_page.py`、`ui/main_window.py`、`dark_theme.qss` | `tests/test_collection_overlay.py`（35） |
| R4 | 已交付 | `ui/favorite_page.py`、`database/favorite_repository.py`、`ui/main_window.py` | `tests/test_favorite_batch.py`（18） |
| R5 | 已交付 | `ui/playlist_page.py`、`ui/playlist_overlay.py`、`ui/widgets.py`、`ui/main_window.py` | `tests/test_playlist_page.py`（9，已按裁定改写） |
| R6 | 已交付 | `ui/player_page.py`、`download/download_manager.py`、`ui/main_window.py` | `tests/test_player_status_badges.py`（17） |
| R7 | 已交付 | `ui/player_page.py`、`ui/main_window.py`、`resolver/youtube_resolver.py`、`resolver/subtitle_parser.py` | `tests/test_subtitles.py`、`tests/test_subtitle_selection.py` |

与第 8 节"新增"清单的两处出入（实现时合并，不影响覆盖面）：

- `tests/test_collection_playlist.py` 未单独新建，合集解析断言并入 `tests/test_collection_overlay.py`。
- `tests/test_youtube_subtitle_absence.py` 未单独新建，R7 断言按主题分别并入 `tests/test_subtitles.py`（解析层）与 `tests/test_subtitle_selection.py`（界面层）。

全量测试：`python -m unittest discover -s tests -p "test_*.py"` → **552 tests OK**（编码前基线 491）。

### 12.2 R7 的一处偏差与一处遗留

1. **7.6 按 A 方案落地**（见该节批注）。`youtube.subtitle_languages` 现在真正决定字幕下拉的排序；未配置时回落到内置的中英文优先。
2. **验收第 4 条（删除四个字幕参数前后 `resolve()` 字幕数完全一致）无法离线验证**。该断言必须联网调用 yt-dlp 才有意义，离线只能断言"命令里确实不再含这四个参数、其余参数原样"（`ResolveCommandTests`）。**这条留作人工联网验收项。**
3. `_append()` 里那条"subtitle format may not be playable"的 info 日志目前**实际不可达**——P3 把回退列表扩到与 `PREFERRED_EXTS` 等同后，任何被选中的条目都在该元组内。保留它是为了将来再加入元组外格式时仍有提示；如觉多余可直接删。

### 12.3 R8 中文字幕 HTTP 429（附带修复）

**现象**：选 English 字幕正常，选中文字幕报"字幕加载失败：HTTP Error 429: Too Many Requests"，切换其他中文轨重试同样失败。

**根因**：YouTube 的中文轨大多是**机器翻译轨**——地址形如 `…&kind=asr&lang=en&tlang=zh-Hans`，由翻译接口按请求现场生成，配额按 IP 计且远紧于原文轨。原文轨（`lang=en`，无 `tlang`）走的是另一条已缓存的路径，所以英文永远正常。实测把地址里的 `&tlang=` 去掉即返回 200，yt-dlp 自己取同一条轨也是同样的 429——**不是本程序的请求头、代理或 Cookie 问题**。

**修复**（`services/subtitle_service.py`、`resolver/models.py`、`ui/toast.py`、`ui/main_window.py`）：

1. 原下载函数改名 `_download_once()`，外面套一层带重试的 `_download()`：429 与 5xx 最多重试 3 次；退避优先听服务端 `Retry-After`，否则 1.5s × 次数，统一加 0~0.4s 抖动避免多轨同时重试，并以 `MAX_RETRY_WAIT_SECONDS = 8` 封顶——服务端给几百秒时不陪它死等。
2. 401/403/404 **不重试**，直接换成"签名可能过期，请重新解析该视频"。
3. 重试仍失败时抛出的是**能照着做的一句话**而不是裸的 `HTTP Error 429`：机翻轨的文案点名"这条是机器翻译字幕""建议改选原文字幕（如 English），或等一两分钟再试"。
4. `SubtitleInfo.is_translated`（地址含 `tlang=`）+ 标签后缀"机翻"，让用户**在点之前**就能看出哪几条走的是紧配额通道。
5. 顺带修掉一个会让上述文案白写的缺陷：`Toast.show_message()` 把宽度夹到 420px 后仍按未换行的 `sizeHint()` 高度 resize，多行文案会被裁掉（实测长文案需 74px 只给了 60px）。改为按夹取后的宽度用 `heightForWidth()` 重算；同时长文案（>40 字）显示时长从 3s 放宽到 8s。

**明确不做**：不在 429 时静默回退到原文轨。用户要的是中文，给英文属于答非所问；正确做法是把原因和选项讲清楚，由用户决定。

**测试**：`tests/test_subtitles.py::DownloadRetryTests`（9）覆盖重试后成功、次数封顶、机翻/非机翻两种文案、403 不重试、5xx 重试、`Retry-After` 生效且被封顶、`is_translated` 与"机翻"标签；`tests/test_subtitle_selection.py::SubtitleFailureToastTests`（3）覆盖长文案不被裁切与两档显示时长。测试中 `time.sleep` 被 patch，不产生真实等待。

**人工验收**：连续切换 3~5 条中文机翻字幕，应出现"重试后成功"或上述带建议的提示，而不再是裸的 HTTP 429；提示框内文字完整可读。

---

## 13. R9 播放器控制面板新增"音轨"选择（2026-08-11 追加，方案已定稿）

原始需求：**在播放器控制面板上增加"音轨"选择，放到"清晰度"后面。**

**结论前置**：这不是一次纯 UI 增补。取证发现，多语言视频当前**播放的是一条随机语言的音频**（实测某视频播出俄语），用户不但不能选，连"现在放的是哪条"都不知道。R9 真正要交付的是**修掉这个既有缺陷**并把选择权交给用户；下拉框只是它的出口。因此本节的方案会触及 `QualitySelector` 的选轨逻辑，并连带影响下载与投屏——这些连带影响已在 13.6 逐条列出，**其中两项需要用户裁定**。

### 13.1 现状

**控制面板**（`ui/player_page.py:224-233`）当前顺序：

```
播放 | 停止 | 下载 | 收藏 ‖ 音量 | 倍速 | 清晰度 | 字幕 ‖ 投屏 | 全屏
```

"音轨"要插在 `清晰度` 与 `字幕` 之间，即第 229 行与 230 行之间。两个已有下拉都是 `QComboBox` + `setFixedWidth`（清晰度 104、字幕 108），标签靠 `_control_group()`（`ui/player_page.py:1064`）拼装。

**取轨链路**：`YoutubeResolver._parse_info()`（`resolver/youtube_resolver.py:671`）→ `QualitySelector.select_all(formats)` → `dict[清晰度标签, VideoQuality]`。B 站也走同一条链路（`_parse_info` 内按 `webpage_url` 分流站点字段，选轨逻辑共用）。

`select_all()` 的两个关键行为（`resolver/quality_selector.py:25-89`）：

1. **全片只挑一条音频**：`best_audio = max(audios, key=cls.score_audio)`，而 `score_audio = 编码分×10000 + abr`（第 107-115 行）——**没有任何语言项**。多语言视频里，谁码率高谁赢。
2. **只有视频轨"不含音频"时才挂音频**：`if acodec in (None, "none") and best_audio`（第 64 行）。视频轨自带音频（muxed）时，`audio_url` 为 `None`，音频语言由视频轨决定，程序层面没有任何干预余地。

**播放链路**：`MpvPlayer.load()`（`player/mpv_player.py:62-83`）在 `loadfile` 之前设置 `audio-files` 属性，即"外挂一条音频文件"。切轨=换 `audio-files` 后重新 `loadfile`，与 `_change_quality()`（`ui/main_window.py:1342+`）完全同构。

### 13.2 取证：三个视频的实测形状

用仓库自身的 `YoutubeResolver._build_command()` + `QualitySelector` 做探针（脚本置于会话临时目录，不入库；只打印非敏感字段）。

**① 多语言 YouTube（`0e3GPea1Tyg`，273 个 format）**

| 观测项 | 结果 |
| --- | --- |
| 纯音频 format | 100 条，**24 种语言** |
| `language_preference` | `en-US` = **10**（`format_note` 含 "original"）、`zh-Hans` = **5**（含 "(default)"）、其余 22 种 = **-1** |
| DRC 变体 | 4 条（`format_id` 带 `-drc`，动态范围压缩版，语言与主轨重复） |
| 编码分布 | opus 75 / mp4a 25 |
| `score_audio` 实际选中 | `251-21` = **印尼语**（abr 134.444 最高） |
| 每一档实际选中的视频轨 | 1080p=`96-0`、720p=`95-0`、480p=`94-0`（`protocol: m3u8_native`，`acodec: mp4a.40.2`，**`language: ru`**）、360p=`93-7`（`language: tr`） |
| 各档 `audio_format_id` | **`None`**（视频轨自带音频，不挂外部音频） |

**这就是缺陷本体**：该视频每一档都被 muxed HLS 变体拿下，用户听到的是**俄语**（360p 档是土耳其语）；即使走到挂外部音频那条分支，`score_audio` 也会挑**印尼语**。两条路径都是"随机语言"。

**② 单语言 YouTube（`dQw4w9WgXcQ`，37 个 format）**

| 档位 | 选中 format | protocol | acodec | 挂载音频 |
| --- | --- | --- | --- | --- |
| 144p–1080p | `91`–`96` | `m3u8_native` | `mp4a.40.2`（`language: en`） | 无（muxed） |
| 1440p | `271` | `https` | `none` | `251` |
| 2160p | `313` | `https` | `none` | `251` |

说明 **muxed 优先不是多语言视频特有**：`score_video` 含 `tbr` 项，而 muxed 变体的 `tbr` 把音频码率也算进去了（1080p muxed `tbr` 4688 > 同档纯视频），所以 ≤1080p 恒定被 muxed 拿下。单语言视频听感上没问题，但它决定了"多语言视频也走这条分支"。

**③ Bilibili（`BV1GJ411x7h7`，18 个 format）**

3 条纯音频，**只有 1 种语言**（`language` 为空串），是 `30216/30232/30280` 三档**码率**变体；选中 `30280`（abr 203.786），且**每一档都正常挂载**（视频轨是纯视频）。B 站是"码率问题"，不是"语言问题"。

### 13.3 完善后的需求

1. 控制面板在**清晰度之后、字幕之前**新增"音轨"下拉，宽度与相邻两个下拉同一量级（建议 116，比字幕略宽，容纳"英语（原声）"这类文案）。
2. 下拉列出该视频**所有可选音频语言轨**，每项文案为「可读语言名（原声/默认）」，例：`英语（原声）`、`中文（简体）`、`俄语`。
3. **默认选中项不再随机**，按 D 裁定的优先级链选取：**优先与本地系统语言相同的音轨**（详见 13.4-D）。
4. 切换即生效：选中另一条音轨后立即换轨播放，**保持当前播放进度与播放/暂停状态**（与切清晰度同款体验）。
5. 只有一条音轨（B 站全部、单语言 YouTube 全部、本地文件）时，下拉显示"默认音轨"并**禁用**——与字幕的"无可用字幕"同款处理，让"没得选"和"程序没取到"可区分。
6. 投屏中禁用，与清晰度下拉一致。
7. 切清晰度后**保持已选音轨**（不得因为换档把语言弹回默认）。
8. 用户选了某条音轨后**下载与投屏跟随该选择**——看的是中文、下的是俄语属于缺陷。
9. 全部行为对 B 站与本地文件必须**安全降级**，不得引入新的失败路径。

### 13.4 四项裁定（2026-08-11 用户已确认）

> 这四项都会改变可观测行为或波及既有功能，落档时未预先替用户决定。用户裁定结果：**A1 / B 同意 / C1 / D 改为跟随本地系统语言**（D 为用户自定义规则，原 D1-D3 均否决）。以下保留各项的备选与理由，便于回溯。

**A. 选视频轨的策略（决定"能不能选"）—— 已裁定：A1**

要让多语言视频出现可选音轨，必须让视频轨走"纯视频 + 外挂音频"，否则语言被焊死在 muxed 里。

| 方案 | 规则 | 影响面 |
| --- | --- | --- |
| **A1（采纳）** | **仅当**该视频存在 ≥2 种音频语言时，同档优先选纯视频轨；单语言视频**完全维持现状** | 影响面最小，只动"本来就坏"的那类视频 |
| A2 | 只要存在纯音频轨就一律优先纯视频轨 | 规则更干净，但 `dQw4w9WgXcQ` 这类单语言视频的 1080p 也会从 muxed 改成 DASH，**所有 YouTube 投屏都将需要 FFmpeg**（见 C），回归面过大 |
| A3 | 不动选轨逻辑，只在已有外挂音频的档位显示音轨下拉 | 结果是"最需要选音轨的视频恰好选不了"（`0e3GPea1Tyg` 全档 muxed，下拉恒为空）。**不推荐** |

**裁定 A1**，理由：单语言视频当前行为是正确的，没有理由让它承担回归风险；多语言视频当前行为是错的，改动它只有收益。

**B. `score_audio` 的语言项 —— 已裁定：同意本方案**

`score_audio` 现在是纯码率比较。多语言下必须先按语言分组、组内再比码率，否则"选中的语言"和"实际最高码率的轨"打架。方案：**保持 `score_audio` 的签名与语义不变**（组内择优仍然用它），在 `select_all()` 外层按 `language` 分组。这样既有测试不受影响。**用户已同意，按此实施。**

**C. 投屏（DLNA）的连带影响 —— 已裁定：C1**

`_show_cast_dialog()`（`ui/main_window.py:1716-1723`）在 `quality.audio_url` 非空时**强制要求 FFmpeg**，否则直接拒绝投屏（"当前视频为分离音视频流，投屏前需要在设置中配置 FFmpeg"）。A1 之下，多语言视频从"muxed 可直投"变成"分离流需 FFmpeg"。

| 方案 | 行为 |
| --- | --- |
| **C1（采纳）** | 接受该限制。多语言视频投屏时若无 FFmpeg，提示文案补一句"或在音轨中选择「随画面（免转码）」" |
| C2 | 投屏时自动回退到 muxed 视频轨（语言不可控），不需要 FFmpeg | 
| C3 | 不做特殊处理，让用户自己去配 FFmpeg |

**裁定 C1**，理由：它把代价说清楚并给出一个可点的出路，而不是让用户对着一条死路。配套地，音轨下拉在"存在 muxed 变体"时**额外提供一项「随画面（免转码）」**，选中即回到今天的 muxed 行为。

**D. 默认音轨的选取链 —— 已裁定：跟随本地系统语言**

用户裁定：**优先选择与本地系统语言相同的音轨**（中文系统优先中文、英文系统优先英文，以此类推）。原方案给出的 D1/D2/D3 均被否决，采用用户指定的这条链：

```
① 配置显式指定的语言（player.default_audio_language，非 "auto" 时）
② 与本地系统语言匹配的音轨          ← 用户裁定的主规则
③ 站点默认轨（language_preference == 5）
④ 原声轨（language_preference >= 10）
⑤ 排序后的第一条
```

②→③→④→⑤ 是**逐级回退**：系统语言在该视频没有对应音轨时（例如泰语系统看只有中英日的视频），落到站点默认；再没有则原声；最后取第一条。任何一级都不会失败，链条恒有结果。

**系统语言怎么取**：用 `QLocale.system()`（Qt 已是依赖，无需新增第三方库），取 `language()` / `script()` / `territory()` 三者而**不是** `name()`——`name()` 给的是 `zh_CN` 这种带地区、不带书写体系的串，而 YouTube 的音轨语言码是 `zh-Hans` / `zh-Hant` 这种带书写体系、不带地区的形态，直接比字符串会双向都匹配不上。`script()` 能明确区分简繁（`SimplifiedHanScript` / `TraditionalHanScript`），是简繁不误配的关键。

**匹配规则**（`services/locale_service.py` 新增 `match_audio_language(languages, system_tag)`，四级由严到宽；签名见 13.5.2a）：

| 级别 | 规则 | 例（简中系统 `zh` + `Hans` + `CN`） |
| --- | --- | --- |
| 1 | 完整标签相等（大小写无关，`_` 归一为 `-`） | 命中 `zh-Hans` |
| 2 | 语言 + 书写体系相等（忽略地区） | `zh-Hans-CN` 系统命中 `zh-Hans` |
| 3 | 语言相等且**书写体系不冲突**，中性码优先 | 只有 `zh-Hant` 与 `zh` 时命中 **`zh`**（不取繁体） |
| 4 | 语言相等（书写体系冲突时的最后兜底） | 只有 `zh-Hant` 时命中 `zh-Hant`，好过回退到英语 |

第 3 级的"中性优先"是简繁不误配的落点：简中系统宁可拿中性的 `zh`，也不要繁体的 `zh-Hant`；但第 4 级仍允许在别无选择时取繁体——**同语系的繁体也远好过完全另一种语言**。同理适用于 `en-US` / `en-GB`（地区差异在第 2 级即被忽略）。

`system_language_tag()` 的 `locale` 参数**可显式注入**（与 `platform_support.py`、`app_paths.py` 的既有可注入风格一致），`match_audio_language()` 则直接收一个语言标签字符串。这样离线测试可以直接构造简中/繁中/英文/日文/泰语系统而不依赖跑测机器的真实区域设置——否则这套逻辑在 CI 上等于没测。

配置键 `player.default_audio_language` 仍然新增，默认 `"auto"` 含义即上述完整链条；填具体语言码（如 `en`）则**跳过第②级**，直接按①处理，用于"系统是中文但我想默认听英文原声"这类需求。

> D 与 A、C 无耦合：无论 A/C 取哪个方案，D 的链条一致。

### 13.5 方案

#### 13.5.1 数据模型（`resolver/models.py`）

新增 `AudioTrack`，并在 `VideoInfo` 上挂一张有序表。**`VideoQuality` 的现有字段一个不动**（`audio_url` / `audio_format_id` / `audio_filesize` / `audio_tbr` / `acodec` 保持"默认轨"的语义），避免波及下载、投屏、既有测试。

```python
@dataclass
class AudioTrack:
    track_id: str            # = format_id，如 "251-21"
    language: str            # 如 "zh-Hans"；B 站为 ""
    url: str
    acodec: str = ""
    abr: float | None = None
    filesize: int | None = None
    tbr: float | None = None
    language_preference: int = -1
    name: str = ""           # yt-dlp 的可读名，取自 format_note 前段

    @property
    def is_original(self) -> bool: ...      # language_preference >= 10
    @property
    def is_site_default(self) -> bool: ...  # language_preference == 5
    @property
    def display_language(self) -> str: ...  # 复用 LANGUAGE_NAMES，缺失回退语言码
    @property
    def label(self) -> str: ...             # "英语（原声）" / "中文（简体）" / "俄语"
```

`VideoInfo` 增 `audio_tracks: dict[str, AudioTrack] = field(default_factory=dict)`（键 = `track_id`，插入序即显示序）。默认值为空 dict，**旧调用方与既有测试无需改动**。

`LANGUAGE_NAMES`（`resolver/models.py:9`）已覆盖 24 种常用语言，直接复用；`0e3GPea1Tyg` 的 24 种语言里表内缺的那些回退到 yt-dlp 给的可读名（`format_note` 里就有，如 "Hindi"），再缺才回退语言码。

#### 13.5.2 选轨（`resolver/quality_selector.py`）

新增 `select_audio_tracks(formats) -> OrderedDict[str, AudioTrack]`：

1. 取纯音频候选（与 `select_all` 同一过滤条件）。
2. **丢弃 DRC 变体**：`format_id` 含 `-drc` 且存在同语言非 DRC 轨时跳过（`0e3GPea1Tyg` 有 4 条，否则会出现两条同名"英语（原声）"）。
3. 按 `language` 分组（`None`/`""` 归为一组，键 `""`）；**组内用现有 `score_audio` 择优**，每种语言只出一条。
4. 排序：**系统语言匹配轨（D 裁定，见 13.5.2a）→ 站点默认（==5）→ 原声（>=10）→ 其余按可读名**。
5. 只有 0 或 1 组时照常返回（调用方据此禁用下拉）。

`select_all()` 增加一个可选参数 `prefer_split_audio: bool`（由 A1 判定：`len(select_audio_tracks(formats)) >= 2`）。为真时，同档 `best_by_label` 的比较**先按"是否纯视频"排序，再按 `score_video`**——一行 key 改造，不动 `score_video` 本身，既有 `score_video` 测试全部保留。

同时，`select_all()` 挂载的"默认音轨"改为 `select_audio_tracks()` 排序后的第一条（即 D 链条的结果），而不再是 `max(audios, key=score_audio)`。这直接修掉"印尼语"那条路径。

> `QualitySelector` 是纯静态类、无配置依赖。`player.default_audio_language` 与系统语言标签由 `YoutubeResolver._parse_info()` 读取后作为参数传入（`preferred_language: str = ""`），保持 `QualitySelector` 的可测性——测试可直接传 `"zh-Hans"` 而不必伪造 `QLocale`。

#### 13.5.2a 系统语言匹配（`services/locale_service.py`，新增）

D 裁定要求跟随本地系统语言，这块逻辑独立成模块，原因有二：一是 `QualitySelector` 必须保持无外部依赖以便测试；二是系统语言探测在 CI 上不可控，必须可注入。

```python
def system_language_tag(locale: QLocale | None = None) -> str:
    """返回 BCP-47 风格标签，如 zh-Hans / en-US；locale 可注入以便测试。"""

def match_audio_language(languages: list[str], system_tag: str) -> str:
    """按 13.4-D 的四级规则从候选语言码里挑一条，挑不中返回 ""。"""
```

`system_language_tag()` 用 `QLocale.system()` 的 `language()` / `script()` / `territory()` 组装，**不用 `name()`**（理由见 13.4-D）。`match_audio_language()` 是纯函数，不碰 Qt，四级规则全部可离线覆盖。

`ui.language`（`config/default_config.json:68`）当前是**无人读取的死键**，因此 D 不借道它——否则等于把新功能挂在一个从未生效的配置上。是否让 `ui.language` 参与本链条不在 R9 范围内，维持现状。

#### 13.5.3 界面（`ui/player_page.py`）

1. 新增 `self.audio_combo`，**用 `NoScrollComboBox`**（R5 引入，`ui/widgets.py`）而非裸 `QComboBox`——控制面板会自动隐藏，鼠标划过时误触滚轮会直接触发换轨重载。同时把已有的 `quality_combo` / `subtitle_combo` 一并换成 `NoScrollComboBox`（现存同类风险，顺手消掉）。
2. `controls.addLayout(self._control_group("音轨", self.audio_combo))` 插在清晰度与字幕之间（第 229/230 行之间）。宽度 116。
3. 新增 `audio_track_changed = Signal(str)`（载荷 = `track_id`），与 `quality_changed` / `subtitle_changed` 并列（第 52-53 行附近）。
4. 新增 `_populate_audio_combo(tracks, selected_track_id)`，与 `_populate_subtitle_combo()` 同构：空表或单条时写入"默认音轨"（`data=""`）并禁用；有多条时逐条 `addItem(track.label, track_id)`。**不需要"更多…"对话框**——音轨最多几十条（实测 24），不是字幕那种四千条量级。
5. `update_video_info()` 内在填充清晰度之后调用它；`update_local_file_info()` 调用 `_populate_audio_combo({}, "")`。
6. `_update_playback_buttons()` 增一行，与字幕同款条件：
   ```python
   self.audio_combo.setEnabled(enabled and not self._cast_active and len(self._audio_tracks) > 1)
   ```
7. `_emit_audio_track()` 受 `self._populating` 保护，与 `_emit_quality()` 一致。

#### 13.5.4 主窗口（`ui/main_window.py`）

1. `player_page.audio_track_changed.connect(self._change_audio_track)`（第 390-391 行旁）。
2. 新增状态 `self.current_audio_track_id: str`，在 `_resolved()` 里由 `_select_default_audio_track(video)` 初始化：读 `player.default_audio_language`，为 `"auto"` 时走 `system_language_tag()` + `match_audio_language()`（D 链条第②级），未命中再回退站点默认/原声/第一条。
3. 新增 `_current_audio_url(quality)`：所选音轨存在且当前档位是纯视频轨时返回 `track.url`，否则返回 `quality.audio_url`（含 muxed 时的 `None`）。**`_resolved()`、`_change_quality()`、`restart` 三处 `mpv.load()` 统一改用它**——这是"切清晰度不丢音轨"（需求 7）的实现点。
4. `_change_audio_track(track_id)` 直接照抄 `_change_quality()` 的模板：记录 `position` / `autoplay` → `mpv.load(quality.video_url, track.url, start_position=position, headers=..., autoplay=autoplay)` → 失败弹 `QMessageBox.critical(self, "切换音轨失败", str(exc))`。
5. `_show_cast_dialog()`：`DlnaMediaSource` 的 `audio_url` / `audio_codec` 改取所选音轨（按 C1 裁定，另加"随画面（免转码）"选项的处理）。
6. 下载：`build_download_task()` 增加可选参数 `audio_format_id`，非空时覆盖 `quality.audio_format_id` 参与 `f"{format_id}+{audio_format_id}"` 拼接；调用方传入 `self.current_audio_track_id`。无 FFmpeg 时维持既有的"单文件降级"分支不变。

#### 13.5.5 配置与设置页

`config/default_config.json` 的 `player` 段增 `"default_audio_language": "auto"`；`ui/settings_page.py` 在"默认清晰度"旁增一个下拉，首项为 **`跟随系统（简体中文）`**——括号里显示 `system_language_tag()` 的实际探测结果，让用户一眼看出程序认为的系统语言是什么，其余项为 `简体中文` / `繁体中文` / `英语` / `日语` / `韩语` 等常用语言（取自 `LANGUAGE_NAMES`），写回该键。`ConfigService` 无需新方法，走既有 `get`/`set`。

### 13.6 影响面与连带风险

| 项 | 影响 | 处置 |
| --- | --- | --- |
| 多语言视频的投屏 | muxed → 分离流，**需要 FFmpeg** | 按 C 裁定；C1 下提示文案给出"随画面（免转码）"出路 |
| 多语言视频的下载 | `format_id` → `format_id+audio_format_id`，**需要 FFmpeg 合流** | 既有"单文件降级"分支已覆盖无 FFmpeg 场景，仅需让它跟随所选音轨 |
| 单语言 YouTube | A1 下**完全不变** | 用 `dQw4w9WgXcQ` 的 format 夹具写"选轨结果与改动前逐字段相等"的回归断言 |
| B 站 | 1 种语言 → 下拉恒为"默认音轨"且禁用 | 用 `BV1GJ411x7h7` 夹具断言"码率变体不被展开成多条音轨" |
| 本地文件 | mpv 内部音轨（如多音轨 MKV）**本期不支持** | 显式列为不做项，下拉禁用；见 13.8 |
| 系统语言探测（D） | 系统语言若被识别错（如 Linux 上 `LANG` 未设），默认音轨会落到站点默认轨而非用户母语 | 回退链保证不失败；设置页首项显示**实际探测到的语言**，识别错时用户一眼可见并可手动指定 |
| 既有测试 | `score_video` / `score_audio` 签名与语义均不变 | 预期零改写；若有断言依赖"1080p 选中 muxed"，按新策略更新并在提交信息注明 |

### 13.7 验收标准

**自动化（离线，用 yt-dlp `--dump-single-json` 的 format 夹具，不联网）**

1. 多语言夹具（仿 `0e3GPea1Tyg`：24 种语言 + 4 条 DRC + muxed HLS 各档）→ `select_audio_tracks()` 返回 **24 条、无 DRC 重复**，首条为 D 链条选中的轨。
2. 同一夹具 → `select_all()` 各档的 `video_url` 取自**纯视频轨**，`audio_format_id` 非空且**等于**默认音轨；断言其 `language` **不是** `ru`/`id`（即缺陷已修）。
3. 单语言夹具（仿 `dQw4w9WgXcQ`）→ `select_all()` 结果与改动前**逐字段相等**（1080p 仍是 muxed `96`，1440p/2160p 仍挂 `251`）；`select_audio_tracks()` 返回 1 条。
4. B 站夹具（仿 `BV1GJ411x7h7`）→ `select_audio_tracks()` 返回 **1 条**（三档码率折叠为一条，取 `30280`），不因语言码为空而崩。
5. 无任何纯音频轨的夹具 → 返回空表，`select_all()` 行为不变。
6. `AudioTrack.label`：原声轨含"原声"、站点默认轨不重复标"原声"、表内语言用中文名、表外语言回退 yt-dlp 可读名、都没有时回退语言码。

**D 裁定专项（系统语言匹配，`services/locale_service.py`，全部注入 locale 不依赖真实区域设置）**

7. `system_language_tag()`：注入简中/繁中/美英/英英/日文 `QLocale`，分别得到 `zh-Hans` / `zh-Hant` / `en-US` / `en-GB` / `ja`；**简繁必须区分**（这是不用 `name()` 的理由）。
8. `match_audio_language()` 一级：候选含 `zh-Hans`、系统 `zh-Hans` → 命中 `zh-Hans`。
9. 二级（忽略地区）：系统 `en-US`、候选只有 `en` → 命中 `en`；系统 `en-GB`、候选只有 `en-US` → 命中 `en-US`。
10. 三级（中性优先，简繁不误配）：系统 `zh-Hans`、候选 `zh-Hant` + `zh` → 命中 **`zh`**。
11. 四级（同语系兜底）：系统 `zh-Hans`、候选只有 `zh-Hant` + `en` → 命中 **`zh-Hant`**（不得回退到 `en`）。
12. 未命中：系统 `th`、候选 `en` + `zh-Hans` → 返回 `""`，随后由调用方回退到站点默认轨（`language_preference == 5`）。
13. 多语言夹具 + 简中系统 → 默认音轨为**中文（简体）**；同一夹具 + 英文系统 → 默认音轨为**英语（原声）**；同一夹具 + 泰语系统 → 回退为**站点默认轨**。这条是 D 裁定的核心断言。
14. `player.default_audio_language` 填具体语言码（如 `en`）时**跳过系统语言**，即便系统是中文也默认英语。

**界面与主窗口**

15. `PlayerPage`：多音轨视频下拉项数 == 音轨数且默认选中项正确；单音轨/无音轨时项为"默认音轨"且 `isEnabled()` 为假；`update_local_file_info()` 后同样禁用。
16. `PlayerPage`：投屏态下 `audio_combo.isEnabled()` 为假（与 `quality_combo` 同步）。
17. `PlayerPage`：`audio_combo` 是 `NoScrollComboBox`，未聚焦时 `wheelEvent` 被忽略（复用 R5 的断言写法）；`quality_combo` / `subtitle_combo` 同。
18. `PlayerPage`：`_populating` 期间填充下拉**不**发出 `audio_track_changed`；用户切换时发出且载荷为 `track_id`。
19. `MainWindow._change_audio_track()`（以 `SimpleNamespace` 打桩 mpv）：以当前 `position` 与 `autoplay` 调用 `mpv.load()`，`video_url` 不变、`audio_url` 为新轨；`mpv.load` 抛异常时弹"切换音轨失败"而不是逃逸。
20. `MainWindow`：**切清晰度后 `current_audio_track_id` 不变**，且 `mpv.load()` 收到的 `audio_url` 仍是所选音轨（需求 7 的专项断言）。
21. `build_download_task()`：传入 `audio_format_id` 时 `format_selector == f"{format_id}+{audio_format_id}"`；无 FFmpeg 时仍走"单文件降级"分支。
22. 全量：`python -m unittest discover -s tests -p "test_*.py"` 全绿，且**总数 ≥ 552 + 新增数**（当前基线 552）。

**人工验收（需联网 + 有窗口环境）**

23. 在**中文系统**上打开一个多语言视频（如 `0e3GPea1Tyg`）：音轨下拉可用、列出多种语言，**默认播放中文而不是俄语**——这是本需求最核心的一条。
24. 把系统显示语言切到英文后重启程序，打开同一视频：**默认变为英语**（验证 D 裁定真的跟随系统而不是写死中文）。若不便改系统语言，可改用设置页"默认音轨语言"选具体语言做等价验证，并在验收记录里注明用的是替代路径。
25. 切到另一条音轨：**画面不跳、进度不回零**、暂停态保持，声音在 1~2 秒内改变。
26. 切完音轨再切清晰度：**音轨保持不变**，不弹回默认。
27. 对同一视频点下载：产物音轨为所选语言（用播放器验证）。
28. 打开单语言视频（如 `dQw4w9WgXcQ`）与任一 B 站视频：音轨下拉为"默认音轨"且置灰；播放、下载、投屏行为与本次改动前**无差别**。
29. 投屏一个多语言视频：无 FFmpeg 时提示文案里应能看到"随画面（免转码）"的出路（C1）。
30. 鼠标划过控制面板时滚动滚轮：音轨/清晰度/字幕三个下拉**都不**改变选中项。

### 13.8 明确不做

1. **本地文件的内部音轨切换**。多音轨 MKV 需要走 mpv 的 `track-list` / `aid` 属性，与在线流的"外挂音频 URL"是两套机制，且要新增 `MpvPlayer` 的属性读取 API。本期不做，下拉在本地文件下禁用。
2. **不重载切轨（mpv `audio-add` + `aid`）**。理论上能做到无缝换轨，但 `MpvPlayer` 目前只在 `loadfile` 前设置 `audio-files`，改造要新增命令与轨道状态管理，且不同 libmpv 版本行为差异未验证。**先用与切清晰度同款的重载路径**（已被现网验证），无缝切轨留作后续优化。
3. **把码率变体当音轨展示**（B 站的 `30216/30232/30280`）。音轨语义是"语言"，码率已经由清晰度隐含表达；展开会让 B 站凭空多出三个看不懂的选项。
4. **DRC 变体单列**。它是同语言的动态范围压缩版，不是另一条语言轨。
5. **音轨的持久化记忆（按视频/按频道记住上次选择）**。本期只做全局默认配置项。

### 13.9 实施范围（R9）

**修改**

| 文件 | 说明 |
| --- | --- |
| `resolver/models.py` | 新增 `AudioTrack`；`VideoInfo.audio_tracks` |
| `resolver/quality_selector.py` | 新增 `select_audio_tracks()`；`select_all()` 增 `prefer_split_audio` 与默认轨来源 |
| `resolver/youtube_resolver.py` | `_parse_info()` 填充 `audio_tracks`，读 `player.default_audio_language` |
| `ui/player_page.py` | 新增 `audio_combo` / `audio_track_changed` / `_populate_audio_combo()`；三个下拉改 `NoScrollComboBox` |
| `ui/main_window.py` | `_change_audio_track()`、`_current_audio_url()`、`_select_default_audio_track()`（含系统语言链）；投屏与下载跟随所选音轨 |
| `ui/settings_page.py` | 新增"默认音轨语言"下拉，首项显示实际探测到的系统语言 |
| `config/default_config.json` | `player.default_audio_language` |
| `download/command_builder.py` | `build_download_task()` 增 `audio_format_id` 可选参数 |

**新增**

- `services/locale_service.py`（D 裁定：`system_language_tag()` + `match_audio_language()`，locale 可注入）
- `tests/test_audio_track_selection.py`（选轨层 + 系统语言匹配 + 界面层 + 主窗口切轨，覆盖验收 1-22）
- `tests/fixtures/`（三份精简的 format 夹具 JSON；若不新建目录则内联到测试模块）

**不修改**

- `player/mpv_player.py`（重载路径已够用，见 13.8-2）
- `dlna/` 各模块（只是传入的 `audio_url` 变了，链路不动）
- 数据库、播放列表持久化、快捷键

**发布物**：并入 0.2.25 批次，`app_version.txt` 不预先改动。

### 13.10 裁定记录（2026-08-11 已确认）

| 项 | 问题 | 用户裁定 |
| --- | --- | --- |
| **A** | 选视频轨策略：A1 仅多语言视频改走纯视频轨 / A2 一律 / A3 不动 | **A1**（采纳本方案推荐） |
| **B** | `score_audio` 保持签名、语言分组放外层 | **同意本方案** |
| **C** | 多语言视频投屏需 FFmpeg：C1 接受并给"随画面（免转码）"出路 / C2 自动回退 muxed / C3 不处理 | **C1**（采纳本方案推荐） |
| **D** | 默认音轨的选取链 | **改为跟随本地系统语言**（中文系统优先中文、英文系统优先英文，以此类推）；原 D1/D2/D3 **均否决** |
| E | 下载是否跟随所选音轨（13.3-8） | 随 A/C 裁定一并生效，按"跟随"实施 |

**D 裁定导致的方案变更**（正文已同步更新）：

1. 优先级链改为 `配置 → **系统语言** → 站点默认 → 原声 → 第一条`（13.4-D）。
2. **新增 `services/locale_service.py`**（原方案没有这个文件）：`system_language_tag()` 用 `QLocale.system()` 的 `language()`/`script()`/`territory()` 组装——**不用 `name()`**，因为 `name()` 给的 `zh_CN` 不带书写体系，与 YouTube 的 `zh-Hans`/`zh-Hant` 双向都匹配不上；`match_audio_language()` 是四级由严到宽的纯函数（13.5.2a）。
3. **简繁不误配**成为一条独立的设计约束：简中系统在只有 `zh-Hant` + `zh` 时取中性的 `zh`（第 3 级），但在只有 `zh-Hant` 时仍取繁体而不是跳到英语（第 4 级）。
4. 验收标准从 21 条扩到 30 条，新增 **D 专项 8 条**（验收 7-14），全部注入 `QLocale` 以摆脱对跑测机器区域设置的依赖；人工验收新增"切换系统语言后默认音轨随之改变"（验收 24）。
5. 设置页"默认音轨语言"下拉首项改为 **`跟随系统（<实际探测结果>）`**，把程序的判断显式暴露给用户——这是系统语言识别错时唯一的自查入口（见 13.6 风险表）。
6. 实施范围新增 `services/locale_service.py`。

**结论：R9 方案定稿，四项裁定已全部确认，等待用户下达编码指示后启动实施。**

---

## 14. R9 实施记录（2026-08-11 编码完成）

用户在四项裁定确认后指示："开始编码"。实施顺序：数据模型 → `locale_service` → 选轨 → 解析层 → 界面 → 主窗口 → 配置/设置页/下载 → 测试（自下而上，每层落地后即可被上层复用）。

### 14.1 交付状态

| 落点 | 状态 | 说明 |
| --- | --- | --- |
| `resolver/models.py` | 已交付 | `AudioTrack`（含 `is_original` / `is_site_default` / `display_language` / `label`）、`VideoInfo.audio_tracks`、`VideoQuality.muxed_video_url`、`MUXED_AUDIO_TRACK_ID` 哨兵 |
| `services/locale_service.py`（新增） | 已交付 | `system_language_tag()` / `parse_language_tag()` / `match_audio_language()`，locale 可注入 |
| `resolver/quality_selector.py` | 已交付 | `audio_formats()` / `select_audio_tracks()` / `_drop_drc()` / `_track_name()`；`select_all()` 增 `audio_tracks=` 参数并保留同档已混音地址 |
| `resolver/youtube_resolver.py` | 已交付 | `_preferred_audio_language()` + `_parse_info()` 填表 |
| `ui/player_page.py` | 已交付 | `audio_combo` / `audio_track_changed` / `_populate_audio_combo()`；三个下拉统一改 `NoScrollComboBox` |
| `ui/main_window.py` | 已交付 | `_select_default_audio_track()` / `_current_audio_track()` / `_current_stream_urls()` / `_cast_audio_codec()` / `_change_audio_track()`；投屏与下载跟随所选音轨 |
| `ui/settings_page.py` | 已交付 | "默认音轨语言"下拉，首项 `跟随系统（<探测结果>）` |
| `config/default_config.json` | 已交付 | `player.default_audio_language = "auto"` |
| `download/command_builder.py`、`download/download_manager.py` | 已交付 | `build_download_task()` / `enqueue()` 增 `audio_format_id` |
| `tests/test_audio_track_selection.py`（新增） | 已交付 | **63 个用例**，覆盖验收 1-21 与 29 的可离线部分 |

全量测试：`python -m unittest discover -s tests -p "test_*.py"` → **613 tests OK**（R9 编码前基线 552），满足验收 22。

### 14.2 与 13.5 方案的四处偏差（均为实现时发现的更优解，不改变行为约定）

1. **`select_all()` 收的是音轨表而不是 `prefer_split_audio: bool`**（13.5.2 原文）。签名定为 `select_all(formats, preferred_language="", audio_tracks=None)`，A1 的判定在函数内部由 `len(audio_tracks) >= 2` 得出。改动的理由是：默认轨必须与 `select_audio_tracks()` 的排序首条**严格一致**，若两处各算一次，任何一侧的排序调整都会让"下拉里选中的"和"实际在播的"错位。传表进来则只算一次。`audio_tracks=None` 时函数自行调用一次，旧调用方（B 站解析器、既有测试）不必改。

2. **`select_audio_tracks()` 在单语言时返回那一条轨，而不是空表**。13.5.2-5 写的是"只有 0 或 1 组时照常返回"，验收 3/4 也要求单语言与 B 站返回 1 条——实现最初按"< 2 即空表"写，与文档冲突，已改回文档口径。**"没什么可选的"这个判断移到界面层**：`_populate_audio_combo()` 用 `len(self._audio_tracks) < 2` 写占位项，与 `_update_playback_buttons()` 里既有的 `len(...) > 1` 启用条件同一判据，不会出现"显示了一个能读的语言名却点不动"。副作用已逐条验证无害：单语言视频的 `current_audio_track_id` 变为非空，但 `_current_stream_urls()` 在 muxed 档位（`quality.audio_url is None`）走回落分支、在分离档位取到的是同一条地址；下载传的 id 恰等于 `quality.audio_format_id`。

3. **`_current_audio_url(quality)` 实现为 `_current_stream_urls(quality) -> (video_url, audio_url)`**。原方案只换音频地址，但 C1 的"随画面（免转码）"必须同时把**画面**换回同档的已混音单流，只返回一个 url 不够。为此 `VideoQuality` 增了 `muxed_video_url` 字段（13.5.1 原写"现有字段一个不动"——这是新增字段，未修改任何既有字段的语义）。

4. **`tests/fixtures/` 目录未新建**，三份夹具按 13.9 允许的方式内联在测试模块里（`multi_language_formats()` / `single_language_formats()` / `bilibili_formats()`）。合成夹具比真实 dump 更适合断言：24 种语言、DRC、muxed HLS 各档都是**手写可控**的，不含任何签名地址，也不会随 YouTube 改版失效。

另有两处 13.9"实施范围"表未列但必须动的文件：`download/download_manager.py`（`enqueue()` 要把 `audio_format_id` 透传给 `build_download_task()`；`enqueue_many()` **有意未动**——列表页批量下载没有解析出的音轨表）、`tests/test_mpv_autoplay.py`（`_change_quality()` 改走 `_current_stream_urls()` 后，该模块的打桩状态需要补 `current_audio_track_id` 并绑定两个真实方法，顺带让这两个用例真正覆盖到切清晰度时的选轨）。

### 14.3 实现要点

- **默认轨的链条只在一处**。`select_audio_tracks()` 的排序 key 就是 D 链条（系统语言 → 站点默认 `==5` → 原声 `>=10` → 其余按可读名），`MainWindow._select_default_audio_track()` 只是 `next(iter(tracks), "")`。链条要改时只有一个地方要改。
- **DRC 按语言组内剔除**：同语言存在非 DRC 时一律丢掉 DRC（它的码率常常略高，按 `score_audio` 挑会盖过原轨）；整组都是 DRC 才保留，不会让一种语言凭空消失。
- **`format_note` 不当轨名用的兜底**：`ultralow/low/medium/high/default` 这些码率档在单语言视频上会出现在 `format_note` 里，`_track_name()` 显式排除；多语言时取逗号前那段并去掉尾部的 `original`/`default`（后缀由 `AudioTrack.label` 统一表达，否则会出现"英语（原声）（原声）"）。
- **哨兵不会漏进 yt-dlp 的格式串**：`build_download_task()` 用 `video.audio_tracks.get(audio_format_id)` 做**成员查找**而非字符串判断，`MUXED_AUDIO_TRACK_ID` 与任何过期 id 都取不到轨，自然回落到 `quality.audio_format_id`。
- **切轨失败先回滚再报错**：`_change_audio_track()` 先落 `current_audio_track_id`（`_current_stream_urls()` 读的就是它），`mpv.load()` 抛异常时把状态改回去再弹框，避免下拉停在一条并没有在播的轨上。
- **验收 29 顺带补实**：无 FFmpeg 时的投屏提示改为**条件文案**——本档位有 `muxed_video_url` 时点名"也可把音轨切到「随画面（免转码）」直接投"，没有时（如只有纯视频轨的 2160p）维持原文案，不指路到一个当前选不出来的选项。

### 14.4 遗留与人工验收

1. **验收 23-30 需联网 + 有窗口环境**，其中 29 的两条分支已在离线用例里覆盖（`test_cast_without_ffmpeg_points_at_the_no_transcode_option` / `test_cast_hint_stays_plain_without_a_muxed_variant`），其余仍需人工。**最核心的一条是 23**：中文系统打开 `0e3GPea1Tyg` 应默认中文而不是俄语——这是本需求的起因。
2. **验收 24（切系统语言后默认轨随之改变）** 若不便改系统显示语言，按该条给的替代路径走设置页"默认音轨语言"选具体语言，验收记录里注明用的是替代路径。
3. **系统语言探测错误时的自查入口**只有设置页首项括号里的探测结果（如 `跟随系统（zh-Hans-CN）`）。若用户报"默认语言不对"，先让其截这一项。
4. 13.8 的五条不做项维持不做：本地文件内部音轨、无缝切轨（仍走重载）、码率变体当音轨、DRC 单列、按视频记忆音轨选择。

## 15. R10 两处缺陷核实与修复（2026-08-11）

用户报两条：**（1）** 读取 Cookies 时能识别到 Firefox，但读不出来，只有把 Firefox 设为默认浏览器才行；指定非默认的 Firefox（已登录 YouTube）也失败。**（2）** 播放时左侧滑出的合集列表没有上传日期，右侧播放列表有。

两条都**确认存在**，且都不是"看起来像"的问题——各有可复现的取证。

### 15.1 缺陷一：自动 Cookie 源的兜底顺序按"谁是默认"而非"能不能读"

**根因**（`services/config_service.py:detect_browser_cookie_source()`）：兜底直接取 `detect_browser_cookie_sources()[0]`。那份列表是**排给用户看的**，按"默认浏览器优先"排序（`deduped.sort(...)` 把 `默认浏览器 - ` 开头的顶到最前）。而 Chromium 系从 Chrome 127 起给 Cookie 库套了 App-Bound Encryption，yt-dlp 多半只报 `Failed to decrypt with DPAPI` / `Could not copy Chrome cookie database`；Firefox 的 `moz_cookies.value` 是明文，一向读得出来。于是默认浏览器是 Chromium 系时，兜底必然选中一个**读不出 Cookie 的源**——把 Firefox 设为默认浏览器就是把它挪到了 `[0]`，这正是用户观察到的现象。

本机取证：`detect_browser_cookie_sources()` 回 5 条，顺序为便携版 Chrome（挂着"默认浏览器"）→ brave → firefox → chrome → edge；逐个用 yt-dlp 抽取，**只有 Firefox 成功**（308 条，登录标志齐全），两条 Chrome 报无法复制库、brave/edge 报 DPAPI 解密失败。

这条兜底的覆盖面比看起来大：`auto_cookie_browser()`、`cookie_browser()`、以及 `auto_cookie_browser_for_site()` 在**没有探测结果时**都落到它——即首次启动（异步探测还没回）、探测失败、站点未命中三种情况。启动探测本身工作正常（本机探测结果为 Firefox），所以平时看不出毛病，只在探测尚未落地的窗口期暴露。

**修复**：新增 `is_firefox_cookie_spec()` / `rank_cookie_sources()`，按内核分档、档内保持原顺序（`sorted` 稳定），Firefox 优先；理由与 `cookie_probe_service._ranked()` 是同一套。`detect_browser_cookie_source()` 改取排序后的首条。**给用户看的下拉列表顺序一个字没动**——默认浏览器仍在最前，改的只是程序自动挑选时的口径。已存的探测结果优先级不变，仍然压过排序。

顺带对齐三处"换浏览器重试"的候选顺序，它们此前都按发现顺序试，把可用的 Firefox 压在几个读不出来的 Chromium 后面：

- `resolver/youtube_resolver.py:_alternate_cookie_browsers()` 另有一处独立缺陷：`current` 用 `auto_cookie_browser()`（默认浏览器）算，而自动模式下真正用出去的是 `auto_cookie_browser_for_site()`（按站点探测的结果）。两者不一致时，**已经失败过的那个源会被当成新候选再试一遍**，真正可用的又被挤到后面。改为与 `download/download_worker.py` 同口径。
- `download/download_worker.py`、`resolver/site_resolver.py:_browser_cookie_header()` 只是套上排序（后者尤其值得：`load_browser_cookie_header()` 目前**只支持 Firefox**，非 Firefox 的候选全是空转）。

### 15.2 缺陷一的两处连带修复：WAL 与 profile 发现范围

核实过程中查出两个独立的、会导致"明明登录了却读不出来"的问题，一并修掉：

**（a）Cookie 库是 WAL 模式，读法不对会读到旧快照。** `cookie_probe_service._open_readonly()` 与 `cookie_service._load_firefox_cookie_header()` 都只复制主库，前者还用 `immutable=1` 打开。构造一个"写入未 checkpoint"的库实测（`tests/test_firefox_profiles.py` 已固化为用例）：

| 复制方式 | 打开方式 | 结果 |
| --- | --- | --- |
| 只拷主库 | `mode=ro&immutable=1` | **连表都不存在** |
| 只拷主库 | `mode=ro` | **连表都不存在** |
| 连 `-wal`/`-shm` 一起拷 | `mode=ro&immutable=1` | **连表都不存在**（immutable 直接无视 WAL） |
| 连 `-wal`/`-shm` 一起拷 | `mode=ro` | 正常读到 |

即两处必须同时改才有效。Firefox 正在运行时，刚登录的 Cookie 就在 `-wal` 里——**这是常态，不是边角情况**。直接打开源库那条兜底路径仍保留 `immutable=1`：此时库多半正被独占，那是唯一还能读出点东西的方式。本机今日 WAL 恰为 0 字节，所以这条不是今天那个现象的触发因素，属于同源的健壮性缺口。

**（b）profile 发现只扫 `%APPDATA%\Mozilla\Firefox\Profiles`。** `config_service`、`cookie_probe_service`、`cookie_service` 三处各写了一遍同样的逻辑，都有两个洞：Microsoft Store 版 Firefox 的数据被重定向到 `%LOCALAPPDATA%\Packages\Mozilla.Firefox_*\LocalCache\Roaming\...`，**完全看不见**；多 profile 时顺序随 `iterdir()` 波动，而"哪个 profile 在用"只有 `profiles.ini` 知道（`[Install*]` 段的 `Default=` 比 `[Profile*]` 的 `Default=1` 可靠——后者是传统标记，装过多个版本后常已不是实际在用的那个），目录名 `xxxxxxxx.default-release` 是猜不出来的。

**修复**：抽出 `services/firefox_profiles.py`（仅依赖标准库，无循环导入风险），三处共用；Store 版的 profile 以**绝对路径**形式给出 spec（yt-dlp 会把裸目录名拼到 `%APPDATA%` 下扑空）。这也是"指定非默认 Firefox 失败"最可能的成因——本机的登录 profile 恰在标准位置，相对名 spec 可用，该分支未能在本机复现。

### 15.3 缺陷二：合集列表缺日期，是数据缺失不是渲染问题

**根因**（`resolver/youtube_resolver.py`）：`--flat-playlist` 默认不返回任何日期字段，必须显式加 `--extractor-args youtubetab:approximate_date`。`_build_home_command()` 一直带着它，`_build_playlist_command()` 漏了。左侧合集走 `resolve_collection()` → `resolve_playlist_generic()` → `_build_playlist_command()`，拿到的条目 `upload_date` 与 `timestamp` 双双为 `None`；右侧播放列表的条目来自首页/创作者页命令（带了该参数）或数据库里存好的行，所以有日期。

两侧列表**共用同一个 `PlaylistItemWidget`**，它本来就会在 meta 行拼上 `format_upload_date(...)`——只要数据里有日期就会显示。所以这不是显示问题。

**修复**：`_build_playlist_command()` 补上 `*_APPROXIMATE_DATE_ARGS`（一行）。实测真实合集（某频道 1546 条上传）：修复后 **1546/1546 条都有日期**，跨度 `20060812`–`20260811`、45 个不同日期、随位置单调递减。

一处需知情的性质：该参数名为 approximate 并非修辞——YouTube 的 tab 页只给相对时长（"3 天前"），所以最新几条会**collapse 到同一天**，越老的越粗。首页与创作者列表一直就是这个精度，此改动只是让合集列表与它们一致。

### 15.4 落点与测试

| 落点 | 状态 | 说明 |
| --- | --- | --- |
| `services/firefox_profiles.py`（新增） | 已交付 | `firefox_install_roots()` / `firefox_profiles()` / `resolve_firefox_profile_dir()` / `copy_sqlite_database()`；`FirefoxProfile` 带 `is_default` |
| `services/config_service.py` | 已交付 | `is_firefox_cookie_spec()` / `rank_cookie_sources()`；`detect_browser_cookie_source()` 改按排序取；Windows 分支改走共用发现，Store 版给绝对路径 |
| `services/cookie_probe_service.py` | 已交付 | `_open_readonly()` 带 WAL 且副本不用 immutable；`_firefox_profile_parents()` 覆盖 Store/Snap/Flatpak |
| `services/cookie_service.py` | 已交付 | `_load_firefox_cookie_header()` 改 `mkdtemp` + 带边车复制；profile 解析委托给共用实现 |
| `resolver/youtube_resolver.py` | 已交付 | `_alternate_cookie_browsers()` 对齐 `auto_cookie_browser_for_site()` 并排序；`_build_playlist_command()` 补 `approximate_date` |
| `download/download_worker.py`、`resolver/site_resolver.py` | 已交付 | 重试/兜底候选套上排序 |
| `tests/test_firefox_profiles.py`（新增） | 已交付 | 15 例：Store 根、`[Install*]` 优先于 `[Profile*]`、WAL 复制（含"只拷主库会丢"的反证） |
| `tests/test_cookie_source_ranking.py`（新增） | 已交付 | 14 例：排序、三条兜底路径、探测结果仍优先、Store 版 spec 用绝对路径 |
| `tests/test_cookie_retry_order.py`（新增） | 已交付 | 7 例：Firefox 先试、按站点探测的源不重试、显式选择跳过 |
| `tests/test_collection_upload_date.py`（新增） | 已交付 | 8 例：两条命令都带该参数、`timestamp` → `upload_date`、无日期不致失败、meta 行含日期 |
| `tests/test_cookie_probe.py` | 已扩充 | +2 例：WAL 中的登录 Cookie 能被看见、Firefox 根覆盖 Store/Snap |

全量测试：`python -X utf8 -m unittest discover -s tests -p "test_*.py"` → **661 tests OK**（本轮前基线 615）。

**人工验收**（需真实环境，本机无法覆盖）：

1. 把默认浏览器设回 Chromium 系、删掉 `config/user_config.json` 里的 `cookies.*.auto_browser` 后启动——首次解析应直接用上 Firefox 的 Cookie，不再需要"把 Firefox 设为默认浏览器"。
2. Microsoft Store 版 Firefox 的机器上，设置页的 Cookie 浏览器下拉应能列出其 profile，选中后能读出登录态。
3. Firefox **开着**并且刚登录 YouTube（Cookie 还在 WAL 里）时立即检测，应判为已登录。


