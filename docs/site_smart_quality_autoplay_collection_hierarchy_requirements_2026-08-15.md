# Tube_player 分站画质 / 智能画质 / 连播画质继承 / 合集专辑层级方案

状态：**需求分析与实施方案已落档；A-H 已完成用户裁定；R5-R8 编码及自动化回归已完成，待真实环境验收**

备案日期：2026-08-15

建议目标版本：`0.2.26`

本文覆盖本轮四项增强需求。编号沿用上一份 R1-R4 文档，记为 R5-R8。

---

## 0. 结论摘要

| 编号 | 需求 | 建议实现口径 |
| --- | --- | --- |
| R5 | 默认画质与视频网站绑定，Cookie 设置也按站点独立 | 设置页拆出独立的“网站配置”选择器；Bilibili / YouTube 分别保存默认画质、浏览器 Cookie 来源和 Profile；手工 Cookie 文件继续复用现有 `cookies.<site>.file` |
| R6 | 新增“智能选择”画质 | 不做通用测速站或持续后台测速；在首次播放且站点选为“智能”时，用目标视频真实 CDN 流地址做限量 Range 测速，并按实测带宽与格式码率选档；同一 CDN/代理路径结果短时缓存 |
| R7 | 列表/合集续播继承上一视频清晰度 | 播放器自然结束自动进入下一集，以及用户在同一播放列表/合集内手工点选分集，都继承上一视频实际使用的清晰度；从首页、搜索或 URL 打开的独立视频重新使用站点配置。精确标签不存在时按分辨率/帧率选最近档 |
| R8 | 合集内支持专辑/章节层级 | 保留 Bilibili `ugc_season.sections[].episodes[]` 层级，不再只拍平；左侧合集面板先显示专辑，选择专辑后进入分集列表并可返回上一级；当前专辑内自动连播，不默认跨专辑 |

优先级规则固定为：

1. **同一播放列表/合集内的续播继承画质**，包含自动连播和手工点选分集；
2. 从首页、搜索、历史、收藏或 URL 打开的新视频，若站点设置为“智能选择”则执行目标 CDN 测速；
3. 上述独立播放若站点设置为高/中/低，则使用该站点配置；
4. 用户在播放器手工切换画质只影响当前播放，但会成为同一列表/合集后续切集的继承值。

---

## 1. 现状与问题定位

### 1.1 默认画质仍是全局配置

- `config/default_config.json` 当前只有 `player.default_quality`。
- `ConfigService.default_quality_tier()` / `default_quality_label_override()` 不接收站点参数。
- `MainWindow._select_default_quality()` 只读取这一份全局配置。
- 因此 Bilibili 与 YouTube 无法采用不同默认画质。

### 1.2 Cookie 文件已分站，但浏览器来源仍是全局配置

- 手工 Cookie 文件已经是 `cookies.youtube.file` / `cookies.bilibili.file`。
- 自动探测结果也已经是 `cookies.<site>.auto_browser`。
- 但显式浏览器来源和 Profile 仍读取 `youtube.cookie_browser` / `youtube.cookie_browser_profile`。
- Bilibili API 与 YouTube yt-dlp 请求都可能使用这份全局浏览器设置。
- 设置页目前把“默认首页”单选按钮同时作为 Cookie 编辑目标站点，两个概念耦合，用户无法独立配置默认首页与站点凭据。

### 1.3 自动连播会重新套默认画质

- 播放器自然结束后，`_advance_playlist_queue()` / `_advance_collection_queue()` 会解析下一条。
- 下一条解析成功后统一进入 `_resolved()`。
- `_resolved()` 当前总是调用 `_select_default_quality(video)`，不会区分手工打开和自动连播。
- 用户在上一集手工切到 1080p 后，下一集仍会回到设置里的中档或低档。

### 1.4 Bilibili 合集层级数据已经存在，但被拍平

- Bilibili `x/web-interface/view` 返回的 `ugc_season.sections[].episodes[]` 已被当前解析器使用。
- `BilibiliResolver._collection_from_ugc_season()` 当前明确把所有 `sections` 顺序拼接成一条 `entries`。
- 这会丢失截图中的“纪录片合集 → 某个专辑 → 第 1/2/3 集”层级。
- `SiteResolver` 已有合集 LRU/TTL 缓存，但缓存键主要按当前视频 URL，换到同一合集的另一视频时仍可能重复探测；缓存对象也只有拍平后的列表。

---

## 2. R5 分站默认画质与 Cookie 配置

### 2.1 完善后的需求

1. Bilibili 与 YouTube 分别保存默认画质模式。
2. 每个站点可选择：智能选择 / 高 / 中 / 低。
3. 浏览器 Cookie 来源、Profile、手工 Cookie 内容也按 Bilibili / YouTube 分别保存和展示。
4. “默认首页”只决定启动/首页站点，不再隐式决定正在编辑哪个站点的配置。
5. 切换网站配置时，未保存的画质、浏览器来源、Profile 和 Cookie 文本均不能丢失。
6. 老配置不需要迁移脚本，读取时兼容并在用户修改对应字段后按新键保存。

### 2.2 配置结构

建议保留旧键作为兼容入口，新键如下：

```json
{
  "player": {
    "default_quality": "high",
    "default_quality_by_site": {
      "bilibili": "high",
      "youtube": "high"
    }
  },
  "cookies": {
    "bilibili": {
      "file": "",
      "browser": "auto",
      "browser_profile": "",
      "auto_browser": ""
    },
    "youtube": {
      "file": "",
      "browser": "auto",
      "browser_profile": "",
      "auto_browser": ""
    }
  }
}
```

兼容规则：

- `player.default_quality_by_site.<site>` 不存在时，回退旧的 `player.default_quality`。
- 旧值 `Auto` 继续按 `high` 处理。
- 旧的精确标签（如 `1080p`）继续作为该站点的精确覆盖值。
- `cookies.youtube.browser` 不存在时，YouTube 回退 `youtube.cookie_browser` 与 `youtube.cookie_browser_profile`。
- Bilibili 没有旧的显式浏览器配置，默认使用 `auto`。
- 保存无关设置时不能把旧精确画质标签或旧 Cookie 浏览器配置强制改写。

### 2.3 ConfigService 接口

建议新增或替换为：

```python
QUALITY_MODES = ("smart", "high", "medium", "low")

def default_quality_mode(self, site: str) -> str: ...
def default_quality_label_override(self, site: str) -> str: ...

def explicit_cookie_browser_for_site(self, site: str) -> str: ...
def auto_cookie_browser_for_site(self, site: str) -> str: ...
def cookie_browser_for_site(self, site: str) -> str: ...
```

所有请求端必须根据目标 URL/`VideoInfo.source_site` 传入真实站点，不能继续隐式固定为 YouTube。

### 2.4 设置页交互

“常规”页建议调整为：

```text
默认首页       [Bilibili] [YouTube]

网站配置       [Bilibili | YouTube]
默认画质       [智能选择 / 高 / 中 / 低]
浏览器 Cookie  [自动检测 / 不读取 / Firefox / Chrome ...]
Cookie Profile [................]
Cookie 内容     [站点对应文本框....................]
```

- “网站配置”使用明确的两项分段选择或单选按钮。
- 切换站点时先把当前控件内容写入内存草稿，再加载目标站点草稿。
- Cookie 探测结果继续同时展示两个站点，但当前站点的结果应有明确标识。
- `browser_profile` 仅在选择不带 Profile 的浏览器项时可编辑；选择 `firefox:xxx` 这类完整 spec 时保持现有禁用规则。

---

## 3. R6 智能画质选择

### 3.1 不采用持续通用测速的原因

不建议首版周期访问公共测速站：

- 公共测速站与 Bilibili/YouTube 实际 CDN 路径不同，测得快不代表视频 CDN 快。
- 后台周期下载会持续消耗流量、电量和代理流量。
- 用户切换代理、站点或 CDN 节点后，旧结果很快失真。

首版采用**播放前按目标视频真实流地址测速 + 短时缓存**。这更符合“本地网络同视频网络”的要求。

### 3.2 测速时机与线程模型

1. Resolver 完成后已经拿到 `VideoQuality.video_url`、`tbr`、`audio_tbr` 和请求头。
2. 若当前站点模式不是 `smart`，直接选高/中/低，不测速。
3. 若是同一播放列表/合集内续播（自动连播或手工点选分集），直接继承上一视频画质，不测速。
4. 若是从首页、搜索、历史、收藏或 URL 发起的独立播放且模式为 `smart`：
   - 先检查相同站点、CDN host、代理路径的短时缓存；
   - 未命中时启动 `NetworkProbeWorker`；
   - UI 保持“正在评估网络并选择画质...”加载状态，不阻塞主线程；
   - worker 返回带宽后选择画质并继续 `mpv.load()`。

建议探测参数：

- 使用目标视频某个可播放画面流的真实 HTTP(S) URL；优先选最高档的 URL，因为它与最终高码率播放使用同类 CDN。
- 带上 `video.http_headers`，代理设置与播放器/解析器一致。
- 请求 `Range: bytes=0-1048575`，最多读取 1 MiB；服务器忽略 Range 时读取满 1 MiB 后主动关闭。
- 连接超时 3 秒，总探测预算 5 秒。
- 实际读取不足 128 KiB 时不把结果视为有效测速。
- 不落盘、不记录带签名参数的完整 URL，日志只写站点、host、字节数、耗时和 Mbps。

### 3.3 缓存

建议缓存键：

```text
(site, scheme, host, port, effective_proxy)
```

- TTL：5 分钟。
- LRU 上限：16 个路径。
- 仅内存缓存，重启清空。
- 代理配置变化后自然命中不同 key。
- 探测失败不缓存，避免一次临时故障长期影响后续播放。

### 3.4 自动选档算法

优先按实际格式码率选择，而不是只按高度：

```text
可用预算 = 实测吞吐 kbps × 0.65
候选需求 = video.tbr + audio_tbr
选择不超过可用预算的最高分辨率/最高帧率档
```

- 0.65 为带宽余量，用于网络波动、协议开销和播放器缓冲。
- `audio_tbr` 缺失时按 192 kbps 估算。
- 所有候选都超过预算时选最低档。
- 某些站点缺少 `tbr` 时按保守高度阈值回退：

| 实测速度 | 最大目标高度 |
| --- | --- |
| `< 3 Mbps` | 480p |
| `3-6 Mbps` | 720p |
| `6-12 Mbps` | 1080p |
| `12-25 Mbps` | 1440p |
| `>= 25 Mbps` | 最高可用档 |

- 目标高度不存在时，优先选择不超过目标的最近高度；没有更低档时才向上选择。
- 测速失败、超时、URL 非 HTTP(S) 或没有有效样本时，回退该视频的**中档**，不阻止播放。

### 3.5 建议新增模块

```text
services/network_quality_service.py
    NetworkMeasurement
    NetworkMeasurementCache
    select_quality_for_bandwidth(...)

workers/network_probe_worker.py
    NetworkProbeWorker
```

纯选档算法放 `services` 或 `resolver` 的无 Qt 模块，便于离线单测；网络 I/O 单独放 worker。

---

## 4. R7 列表/合集续播继承上一视频画质

### 4.1 完善后的需求

1. “播放自然结束后自动进入下一条”继承画质。
2. 用户在当前播放列表或合集中手工点击任一分集，同样继承当前视频画质。
3. 从首页、搜索、历史、收藏或 URL 打开的新视频不继承，重新按目标站点默认/智能画质处理。
4. 继承的是上一视频**实际正在使用的画质**，包括用户手工切换后的结果。
5. 列表/合集续播继承优先于站点默认和智能测速。
6. 下一视频没有完全相同标签时选择最接近档，不因缺档而失败。
7. 继承提示只能消费一次；解析失败、用户中途打开独立视频或过期 worker 返回时不能污染后续播放。

### 4.2 画质提示模型

建议增加纯数据结构：

```python
@dataclass(frozen=True)
class PlaybackQualityHint:
    label: str
    height: int
    fps: int
```

自动连播或列表/合集手工切集前，从当前 `VideoQuality` 生成 hint，并与目标 URL/请求代次绑定。

### 4.3 匹配规则

对下一视频按以下顺序：

1. 标签完全相同，例如 `1080p60 -> 1080p60`；
2. 高度相同，选择该高度下 fps 最接近的档；
3. 高度不同，选择绝对差最小的高度；
4. 距离相同则选较低高度，避免无意增加带宽；
5. 同高度仍有多个候选时选 fps 最接近，最后再按现有画质排序稳定决胜。

示例：

- 上一集 `1080p`，下一集有 `1080p60/1080p/720p`：选 `1080p`。
- 上一集 `1080p60`，下一集只有 `1080p/720p`：选 `1080p`。
- 上一集 `1080p`，下一集只有 `1440p/720p`：两边同距，选 `720p`。
- 上一集 `480p`，下一集最低只有 `720p`：选 `720p`。

### 4.4 请求上下文

不能只放一个裸的 `_pending_quality_hint`，否则异步解析乱序时可能套到错误视频。

建议把解析入口收敛到一个 helper，为每次解析生成：

```python
PlaybackRequestContext(
    request_id,
    target_url,
    reason="direct" | "queue_manual" | "autoplay",
    quality_hint=None | PlaybackQualityHint,
)
```

- `_resolved()` 只消费与当前 request id 匹配的 context。
- 新请求开始后，旧请求结果继续沿用现有关闭守卫，并增加 request id 过期判断。
- `reason in {"queue_manual", "autoplay"}` 且 hint 有效时进入继承逻辑。
- `direct` 覆盖首页、搜索、历史、收藏和 URL 播放，必须清除旧的继承 hint。
- context 消费后立即清除。

---

## 5. R8 合集中的专辑/章节层级

### 5.1 站点能力边界

**Bilibili**：

- 当前使用的 `x/web-interface/view` 已返回 `ugc_season.sections[].episodes[]`。
- 这份数据足以一次请求拿到合集、专辑/章节和分集，不需要每点一个专辑再跑一次 yt-dlp。
- yt-dlp 继续负责最终视频详情/流地址解析；层级结构优先使用 Bilibili API，避免被 yt-dlp 的扁平 playlist 输出丢失。

**YouTube**：

- YouTube 普通 playlist 没有可靠的“播放列表内专辑”公开层级。
- yt-dlp 对大多数 YouTube playlist 返回平面 `entries`。
- 本轮不按标题猜测分组，不制造错误层级；YouTube 保持平面列表。

### 5.2 数据模型

建议新增：

```python
@dataclass
class PlaylistSection:
    section_id: str
    title: str
    position: int
    thumbnail: str = ""
    entries: list[PlaylistEntry] = field(default_factory=list)

@dataclass
class PlaylistInfo:
    ...
    current_section_id: str = ""
    sections: list[PlaylistSection] = field(default_factory=list)
```

兼容要求：

- `PlaylistInfo.entries` 继续保留完整拍平列表，现有下载、保存和旧 UI 不立即失效。
- `sections` 只在站点确实提供层级时填充。
- 只有一个无标题 section 时按旧的平面列表展示，不强行多一层点击。
- 每个 section 内的 `PlaylistEntry.playlist_id` 使用稳定的 section key，父合集仍保留稳定 `playlist_id`/`webpage_url`。

### 5.3 Bilibili 解析

`_collection_from_ugc_season()` 改为：

1. 遍历每个 `section`，读取 `id`、`title/name`、封面等可用字段；
2. 每个 section 单独构建 `PlaylistSection.entries`；
3. 同时按 section 顺序生成父 `PlaylistInfo.entries` 拍平副本；
4. 根据当前视频 id 找到 `current_section_id` 和当前分集；
5. API 字段缺失或格式异常时跳过脏 section/episode，不让整个合集解析失败。

### 5.4 左侧合集面板交互

不建议再叠加第三个独立浮层，窄窗口会遮住大部分画面。建议在现有左侧合集面板中做两级导航：

```text
第一级：纪录片合集
  历史纪录片《近人曾国藩》全20集  >
  荒野求生全集解说               >
  1993年纪录片《毛泽东》全12集   >

第二级：纪录片合集 > 1993年纪录片《毛泽东》
  [返回合集]
  第1集：人民心中
  第2集：历史的选择
  第3集：曲折之路
```

- 第一级显示 section 标题、分集数和可用封面。
- 单击/双击 section 进入第二级分集列表。
- 第二级复用现有播放、下载选中、下载全部和自动连播控件。
- 当前视频所在专辑自动高亮；打开面板时默认进入当前专辑的分集层，顶部可返回合集专辑列表。
- 无 section 的旧式合集保持现有单层行为。
- 右侧普通播放列表面板不启用层级模式。

### 5.5 自动连播范围

建议自动连播只在**当前选中的专辑/section 内**前进：

- 当前专辑最后一集结束后停止并进入“播放结束”状态。
- 不自动跨到下一个专辑，避免把不同系列或主题无提示串播。
- 用户返回上一级选择另一专辑后，该专辑成为新的连播队列。

如果后续确实需要跨专辑连播，应增加独立选项，而不是复用当前“自动连播”的含义。

### 5.6 缓存

当前缓存需要增强为两级索引：

1. `video identity -> stable collection id`；
2. `stable collection id + config fingerprint -> 完整 PlaylistInfo（含 sections）`。

建议参数：

- 正向合集树 TTL：30 分钟；
- “不属于合集”负缓存：10 分钟；
- LRU 上限：64 个合集；
- 内存缓存，重启清空；
- Cookie/代理指纹变化时缓存 key 自动变化；
- 返回值继续 `deepcopy`，避免 UI 修改缓存对象。

因为 Bilibili 一次 view 请求已经带回全部 sections/episodes，选择专辑时应直接读缓存，不再请求网络。

### 5.7 已保存合集的层级持久化

为避免保存后层级丢失，建议给 `playlist_item` 增加可空列：

```text
section_id TEXT
section_title TEXT
section_position INTEGER
section_thumbnail TEXT
```

- 用现有 `_ensure_column()` 做无损升级。
- 旧记录四列为空，继续按平面列表读取。
- 保存层级合集时每个 entry 带上所属 section 元数据。
- 从数据库加载后按 `section_position` 重建 `PlaylistSection`。
- 备份包已经包含 SQLite，无需额外增加备份文件。

---

## 6. 主要文件改动方案

| 文件 | 计划改动 |
| --- | --- |
| `config/default_config.json` | 新增分站默认画质与分站浏览器 Cookie 配置 |
| `services/config_service.py` | 分站画质/Cookie accessor、旧配置兼容、配置归一化 |
| `ui/settings_page.py` | 默认首页与网站配置解耦；分站画质、浏览器、Profile、Cookie 草稿 |
| `resolver/quality_selector.py` | 带宽选档、自动连播最近档纯函数 |
| `services/network_quality_service.py`（新） | 测速结果、LRU/TTL 缓存和基于码率的选档 |
| `workers/network_probe_worker.py`（新） | 目标 CDN Range 测速，后台运行 |
| `resolver/models.py` | `PlaybackQualityHint`、`PlaylistSection`、`PlaylistInfo.sections` |
| `resolver/site_resolver.py` | 分站 Cookie 调用、Bilibili sections 保留、合集树缓存 |
| `resolver/youtube_resolver.py` | 所有 Cookie 来源调用改为按目标站点；YouTube 保持平面合集 |
| `ui/playlist_overlay.py` | 可选的层级模式、section 列表、面包屑/返回 |
| `ui/player_page.py` | 转发 section 选择信号，左侧面板启用层级模式 |
| `ui/main_window.py` | 播放请求 context、智能测速链路、连播画质继承、当前 section 状态 |
| `database/sqlite_manager.py` | 为 `playlist_item` 增加 section 元数据列 |
| `database/playlist_repository.py` | 保存/恢复 section 元数据 |

---

## 7. 实施顺序

1. **模型与纯函数**：配置 accessor、画质 hint、带宽选档、最近画质匹配、section 模型。
2. **R5 设置页**：分站配置 UI、草稿切换、旧配置兼容。
3. **R7 连播继承**：播放请求 context 与自动连播优先级；先用纯函数验证行为。
4. **R6 智能画质**：测速 worker、缓存、异步播放续接、失败回退。
5. **R8 解析与持久化**：Bilibili section 解析、缓存、数据库升级。
6. **R8 UI**：左侧面板两级导航、当前 section、section 内连播。
7. **完整回归与真实环境验收**。

该顺序先固定选择规则和请求上下文，再接真实网络与复杂 UI，便于控制回归范围。

---

## 8. 测试计划

### 8.1 新增测试模块

| 测试文件 | 覆盖点 |
| --- | --- |
| `tests/test_site_quality_settings.py` | 两站画质独立、旧全局值回退、精确标签兼容、无关保存不改写 |
| `tests/test_site_cookie_settings.py` | 两站 browser/Profile 独立、自动探测按站点、Bilibili/YouTube 请求各用自己的配置 |
| `tests/test_network_quality_selection.py` | 码率预算、缺 tbr 高度阈值、最低档回退、测速失败回退、缓存 TTL/代理隔离 |
| `tests/test_network_probe_worker.py` | Range、最大读取量、超时、忽略 Range、请求头、URL 日志脱敏 |
| `tests/test_autoplay_quality_continuity.py` | 精确标签、同高度、最近高度、等距取低、自动连播与手工切集继承、独立播放不继承、hint 单次消费和过期请求 |
| `tests/test_collection_sections.py` | Bilibili 多 sections 解析、单 section 平面回退、脏数据、当前 section、缓存命中 |
| `tests/test_collection_hierarchy_ui.py` | 专辑层/分集层切换、返回、当前项、section 内下载与连播、右侧面板不受影响 |
| `tests/test_playlist_section_repository.py` | 新列迁移、层级保存/重建、旧平面记录兼容 |

### 8.2 需要更新的现有测试

- `tests/test_default_quality_tier.py`
- `tests/test_settings_shortcuts.py` 及设置页构造类测试
- `tests/test_main_window_hardening.py`
- `tests/test_collection_overlay.py`
- `tests/test_creator_playlist.py`
- Cookie 重试/探测相关测试
- 播放列表与自动连播相关测试

### 8.3 测速测试原则

- 自动化测试使用本地假 HTTP 响应或假 opener，不访问真实视频网站。
- worker 测试不真等待超时，时钟/读取器可注入。
- 真实环境只做手工验收，不把外网稳定性变成 CI 依赖。

---

## 9. 验收标准

### 9.1 分站设置

1. Bilibili 选高、YouTube 选中，分别打开视频时使用各自配置。
2. 两站均可独立选择智能/高/中/低，保存并重启后保持。
3. 两站浏览器 Cookie 来源与 Profile 独立；Bilibili 请求不会误用 YouTube 的显式浏览器配置，反之亦然。
4. 切换网站配置后再切回，尚未保存的控件内容仍在。
5. 修改默认首页不改变正在编辑的网站配置，也不覆盖另一站点数据。
6. 旧 `player.default_quality=1080p` 在没有新键时两站继续精确命中；保存无关设置后旧值不被改写。

### 9.2 智能画质

1. 选择智能后，首次手工播放会启动后台 CDN 测速，UI 不冻结。
2. 测速只读取目标视频 CDN 最多 1 MiB，不访问第三方测速站。
3. 同 CDN/同代理 5 分钟内再次播放直接命中缓存，不重复测速。
4. 代理变化后不复用直连测速结果。
5. 格式有 tbr 时按 65% 带宽预算选择最高可承受档。
6. 缺 tbr 时按文档高度阈值选择；目标高度缺失时选择不超过目标的最近档。
7. 测速超时/失败时 5 秒内回退中档并继续播放，不弹致命错误。
8. 日志中不出现带签名查询参数的完整 CDN URL。

### 9.3 自动连播画质继承

1. 站点默认中档，首集自动选 480p；用户手工切到 1080p，自动连播下一集选 1080p。
2. 下一集无 1080p 但有 1080p60 时，按匹配规则选择最接近档。
3. 下一集只有 720p/1440p 时，上一集 1080p 应选择 720p。
4. 用户在同一播放列表/合集内手工点击另一集，继承上一集实际画质。
5. 用户从首页、搜索、历史、收藏或 URL 打开视频时不继承，重新使用该站点默认/智能逻辑。
6. 自动连播和列表/合集手工切集均不启动智能测速 worker。
7. 自动连播或手工切集解析失败后再打开独立视频，不会错误继承之前的 pending hint。

### 9.4 合集/专辑层级

1. 构造含三个 `ugc_season.sections` 的 Bilibili 合集，左侧面板首先显示三个专辑，而不是所有分集拍平。
2. 选择专辑后进入该专辑分集列表，可返回合集专辑层。
3. 当前视频所在专辑与分集均正确高亮。
4. 单 section、无 section、多 P 和普通平面播放列表保持现有行为。
5. 选择专辑不再请求网络；使用解析时缓存的完整合集树。
6. 同一合集内切换视频后，合集探测命中稳定 collection id 缓存，不重复拉取完整层级。
7. 专辑内自动连播只播放该专辑下一集，最后一集结束后停止，不跳到下一专辑。
8. 保存层级合集并重启后，专辑结构仍可从数据库恢复；旧版保存的平面合集仍可正常加载。
9. YouTube 播放列表继续以平面形式显示，不因标题相似被错误分组。

### 9.5 回归

1. 手工切画质、音轨、字幕、下载和 DLNA 投屏行为不退化。
2. R1-R4 既有测试继续通过。
3. 全量 `unittest`、`compileall`、`git diff --check` 通过。
4. Windows 安装版与便携版各完成一次真实 Bilibili/YouTube 播放验收。

---

## 10. 风险与非目标

- 智能画质是播放开始时的选择，不在播放过程中持续升降档；当前 mpv 链路不是自适应 ABR 管线，强做动态切档会导致重载和位置恢复问题。
- 测速反映当时的目标 CDN/代理路径，不保证整段播放期间网络不波动。
- 首次智能播放最多增加约 1 MiB 流量和最多 5 秒探测时间；缓存命中后无额外等待。
- 本轮不增加用户自定义 Mbps 阈值，先固定算法并用日志/测试验证；后续有真实反馈再开放高级参数。
- 本轮只对站点实际返回的层级建模，不按标题、编号或正则猜测专辑。
- YouTube 不提供可靠的“视频属于哪些播放列表/专辑层级”时继续返回平面或空合集，这是站点能力边界。

---

## 11. 用户裁定结果（2026-08-15）

| 编号 | 议题 | 最终裁定 |
| --- | --- | --- |
| A | 智能测速方式 | **同意推荐**：使用目标视频真实 CDN 的限量 Range 测速，不做公共测速站或周期后台测速 |
| B | 智能测速失败回退 | **同意推荐**：回退中档并继续播放，不回退最高档，也不阻断播放 |
| C | 测速缓存 | **同意推荐**：同站点/CDN/代理路径缓存 5 分钟，仅内存保存 |
| D | 续播缺少同档时 | **同意推荐**：按最近高度选择；等距时选较低档 |
| E | 手工点击下一集是否继承 | **修改推荐口径**：同一播放列表/合集内手工点击分集仍继承当前实际画质 |
| F | 专辑边界 | **同意推荐**：自动连播只在当前专辑内，不自动跨专辑 |
| G | 合集层级 UI | **同意推荐**：在现有左侧面板内做“专辑层 → 分集层 + 返回”，不叠加第三个浮层 |
| H | 层级持久化 | **同意推荐**：SQLite 保存 section 元数据，重启后仍保留专辑结构 |

A-H 已全部完成裁定。本文至此定稿，后续收到开始编码指令后按第 7 节顺序实施。
