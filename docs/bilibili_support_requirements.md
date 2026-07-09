# Tube_Ultimate_Player Bilibili / YouTube 双站点支持实施备案

更新日期：2026-07-09

## 1. 目标

本轮版本的目标，是在不改动 `libmpv` 播放核心的前提下，为现有 YouTube 播放器补齐 Bilibili 站点支持，并让首页、搜索、URL 播放、下载、收藏、历史与播放列表都能在双站点场景下统一工作。

## 2. 已落地范围

当前版本已经实现以下能力：

1. 双站点统一入口
   - 首页可按配置从 `Bilibili` 或 `YouTube` 加载
   - 搜索可按配置从 `Bilibili` 或 `YouTube` 执行
   - URL 播放按实际链接域名自动分流

2. Bilibili 首页与搜索
   - 首页推荐通过 Bilibili Web API 拉取
   - 搜索支持分页与等待提示
   - 搜索采用三层回退：
     - 候选 A：带浏览器 Cookie 的公开搜索 API
     - 候选 B：WBI 签名搜索 API
     - 候选 C：搜索结果页抓取兜底

3. Bilibili 播放与下载
   - 普通单视频播放
   - 视频下载
   - 共用现有 `yt-dlp + libmpv + DownloadManager` 链路

4. Bilibili 列表类内容
   - 多 P 视频
   - 番剧 `ep` / `ss`
   - 稍后再看
   - 收藏夹 / 媒体列表
   - 空间合集 / season 列表

5. 数据模型与持久化
   - `HomeVideo`、`VideoInfo`、`PlaylistEntry` 增加 `source_site`
   - 收藏、历史、播放列表快照可以区分来源站点

## 3. 实现方案

### 3.1 站点路由

新增统一站点分发层：

- `resolver/site_resolver.py`

职责：

1. 根据配置决定首页与搜索的数据源
2. 根据 URL 域名判断应交给 YouTube 还是 Bilibili 解析
3. 为 UI 层提供统一的解析、搜索、首页加载接口

### 3.2 Bilibili 首页

首页推荐通过 Bilibili Web API 获取，再映射为现有卡片模型：

- 接口：`/x/web-interface/index/top/feed/rcmd`
- UI：沿用首页分页网格

为了适配页面每页 56 条记录的呈现要求，首页会分批请求并聚合结果。

### 3.3 Bilibili 搜索

搜索链路优先利用浏览器已登录 Cookie，提高成功率与内容完整度：

1. 候选 A：浏览器 Cookie + 公开搜索 API
2. 候选 B：WBI 签名搜索 API
3. 候选 C：HTML 页面抓取兜底

同时配合：

- 等待动画
- 温馨提示文案
- 失败日志输出

### 3.4 Bilibili 列表能力

列表型内容统一转为 `PlaylistInfo + PlaylistEntry[]`，交给现有播放列表详情页与播放器侧滑面板复用。

关键原则：

1. 尽量复用 `yt-dlp` 已支持的 `entries`
2. 需要补 API 的特殊列表类型再通过站点 API 填充
3. 列表条目在真正播放时再按单视频解析

## 4. 配置策略

### 4.1 默认首页

设置页新增：

- `默认首页`
  - `Bilibili`
  - `YouTube`

默认值：

- `Bilibili`

保存后立即生效：

1. 重建 resolver
2. 清理首页缓存
3. 清理搜索上下文
4. 若当前正在首页，则自动刷新

### 4.2 Cookie 策略

双站点共用统一 Cookie 服务，支持：

1. 从浏览器自动获取
2. 用户手动粘贴 Cookie 文本

当前实测结论：

1. Bilibili 搜索在匿名场景更容易触发风控
2. 带登录 Cookie 的成功率更高
3. 因此浏览器 Cookie 是 Bilibili 搜索体验的关键增强项

## 5. 验证方法

### 5.1 首页

1. 将默认首页设置为 `Bilibili`
2. 保存后点击首页
3. 确认加载的是 Bilibili 推荐内容
4. 切换为 `YouTube` 后重复验证，确认立即生效

### 5.2 搜索

1. 分别在 `Bilibili` / `YouTube` 默认首页下执行同一关键词搜索
2. 确认结果来源与配置一致
3. 确认搜索过程中显示等待动画与提示语

### 5.3 URL 播放

1. 输入 YouTube 单视频 URL，确认正常播放
2. 输入 Bilibili 单视频 URL，确认正常播放
3. 输入 Bilibili 列表类 URL，确认进入列表详情页或加载播放列表上下文

### 5.4 下载

1. 对 YouTube 视频发起下载，确认无回归
2. 对 Bilibili 视频发起下载，确认：
   - 进入下载队列
   - 有进度、速度、剩余时间
   - 可暂停、继续、删除
   - 完成后可本地播放

## 6. 风险与边界

1. Bilibili 搜索仍可能受外部风控影响
2. 部分高质量视频与会员内容依赖登录状态
3. 上游站点接口变化可能影响解析成功率
4. 列表内容数量与可见性可能因账号、地区、Cookie 状态而变化

## 7. 结论

本轮 Bilibili 支持已从“需求分析”进入“已实施可发布”状态，当前项目已经具备双站点播放器的基本闭环能力：

1. 首页
2. 搜索
3. URL 播放
4. 列表播放
5. 下载
6. 收藏 / 历史 / 播放列表持久化
