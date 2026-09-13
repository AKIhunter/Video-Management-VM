# `webui/` — 前端（零构建）

原生 HTML / CSS / JS，**没有打包步骤**：改完刷新浏览器即可生效（不依赖任何构建工具或 npm）。

```
webui/
├─ index.html    主页面（首页浏览 + 白屏管理弹层）
├─ app.js        主逻辑（约 2000 行，单文件）
├─ style.css     全局样式（深色主题，CSS 变量集中在 :root）
├─ player.html   在线播放页（独立单文件：内联样式 + 内联脚本，不引用 app.js / style.css）
├─ logs.html     通知详情页
└─ assets/
   └─ fallback.svg   封面兜底占位图（本地无素材时由 /api/poster 返回）
```

---

## ⚠️ 最重要的约定

1. **既有元素 `id` 不能改**：`app.js` 大量通过 `document.getElementById` 强依赖 `index.html` 里的 id，改名等于直接白屏。
2. **`player.html` 是自包含的**：它不引用 `app.js` / `style.css`，样式和脚本都内联在自己文件里。
3. 新增功能优先放进现有的「白屏管理」统一入口，不要另起一个页面。

---

## 各文件职责

### `index.html` + `app.js` — 主页面
- 页眉：搜索 / 筛选栏（关键词、年份、制作组、分类、评分、状态、收藏、排序）+ **标签筛选气泡** + 「白屏管理」「管理中心」入口。
- **筛选栏持久化**：状态存 `sessionStorage`（键 `vm.filters.v1`，见 `app.saveFilters/restoreFilters`）——
  刷新浏览器保留，关标签页/新会话即清空（即「除非切换 session」）。新增筛选项时记得同步这两个函数。
- **无限滚动**：往下翻接近底部（提前约 600px）自动加载下一页（严格沿用当前筛选条件，不跑出筛选范围）；
  触发源多路兜底 —— IntersectionObserver + `scroll` + `wheel`(向下) + `resize`，全部汇到 `checkLoadMore()`（节流 150ms）；
  首屏没铺满视口也会在渲染后自动补齐；列表底部**始终有「加载更多」按钮**（`loadMoreManual`）作手动兜底。
  **内存守卫**：一次最多渲染 `OOM_CARD_STEP`（600）张卡，触顶时按钮文案提示达上限，再点会抬高上限继续。
- 作品卡片网格 + 详情抽屉（简介 / 标签 / 个人化操作 / 播放入口）。卡片带 `data-id`，评分变更后由
  `applyRatingEverywhere()` 就地刷新角标（无需整页重载）。
- **右上角 🔔 通知气泡**（5s 轮询未读数），点进 `logs.html?id=N`；**「个人中心」弹层**（`openMe/meTab`）：
  账号与签到（`loadMe/doCheckin`）、我的收藏（`loadFavs`）、等级权益（`renderPerks`，读 `/api/me` 的 `perks`）。
- 「白屏管理」弹层：作品编辑 / 作品管理 / 联网补全 / 扫描 / 标签管理 / 一键重启。
  - **一键重启**：提交后 `waitRestartBack()` 轮询 `/api/me`（首次 3s，之后 1s，最多 60 次），服务恢复即 `location.reload()` 自动刷新；确认词输入做了 `trim()` 容错。
  - **扫描 = 两段式**：`startScan` 只产出「待导入清单」，`loadStagedScan` 渲染清单（类型/分类/标题/路径），
    `importStagedScan` 人工导入（防呆：`scanCategory` 为空 = 「自动（按目录名）」时 alert 拒绝并高亮下拉），
    `clearStagedScan` 放弃清单。导入复用 `scan` 任务通道（`meta.phase="import"`），进度在同一进度条；
    扫描/导入结束由 `refreshScanStatus` 自动刷新清单（`_stagedAuto` 每任务一次）。
  - 作品管理 **重复作品检测**（`dupScan/dupRender/dupNav/dupIgnore/dupDelete/dupDeleteFiles`）：**逐组展示**（一次一组，
    `‹ 上一组 / 下一组 ›` 翻组）。**组头勾选框 = 全选/全不选本组**（`dupSelectAll`），不勾选的完全不受删除影响；
    「删除本组选中索引」走 `bulk delete`（只删索引）；「**删除本组选中作品（含文件）**」走
    `POST /api/admin/media/delete-files`（索引 + 视频文件，**二次确认**）；删除与忽略**只作用于当前组**；
    「本组忽略」调 `POST /api/admin/media/duplicates/ignore`，之后检测不再列出该组。
  - **作品列表「重置」** = 清空展示列表（`manageReset`，不重新查询）。
- **我的评分 = 10 颗星**（`myStarsHTML`）：点第 N 颗得 N 分（0~10 整数），再点当前最高星即清除；
  详情页已移除数字输入框，评分统一走后端 `PUT /api/media/{id}/state`。
- 关键复用件：
  - **标签气泡**是「单例 + 上下文」结构（`state._picker = {kind, anchor, selected, title, hint, live, onApply}`），
    被 **筛选栏 / 批量打标签 / 作品编辑** 三处复用 —— 改气泡只改一处。
    `live=true`（筛选栏）为**即点即筛**：`onTagOptChange()` 里勾选变化立即调 `onApply`，面板不关闭、
    「应用」按钮隐藏（`#fTagApplyBtn`）；「清空」也会立刻生效。`bulk / edit` 两处仍是勾选后点「应用」。
  - **封面渲染器** `refreshCoverSection`：本地封面与联网封面共用，数据源是 `/api/admin/task`。
  - 进度条统一 `width:0` 待机。

### `style.css` — 样式
- **所有颜色、圆角、间距变量集中在文件顶部 `:root`**（`--accent: #ff5c8a` 等）。
  换主题色 / 调色板 → **只改 `:root`**，不要在具体选择器里写死颜色。
- 层级约定：标签气泡用 `position: fixed` + `z-index: 300` 挂到 `body`，保证不被卡片裁剪或压制。

### `player.html` — 播放页
独立单文件，结构分三块：

1. **上半区 `.p-layout`**：左侧播放窗口 `.p-player` + 右侧「**关联作品**」栏 `.p-side`（宽 300px、sticky）。
   - 关联作品：`GET /api/play/{id}/related`（**名称高度相似 + 同目录**加权）。
     **内容在首次打开播放页时查询一次并固定**（`relatedLoaded`），切换播放不重新查询（轮播仍正常按当前作品刷新）；
     渲染时**当前播放项排在第一位**并带「正在播放」标记（`renderRelated`），切换时标记跟随移动。
   - **高度与多列**：`fitRelatedHeight()` 让列表高度 ≤ 播放器高度；`.rel-list` 用 CSS 多列（`columns:2`）+
     横向滚动条，超出的列拖动横向滚动条查看。
   - 点击 `playId()` 快速切换播放；`VIDEO "ended"` → **自动连播**固定列表中当前项的下一个（到末尾即停止）。
   - **格式兼容**：`/api/play/{id}/meta` 返回 `transcode=true`（avi/wmv/rmvb 等）时提示「实时转码，进度条不可拖动」。
   - **大文件守卫**：仅 >2GB（`memory_guard_bytes`）提示本地播放。
2. **作品信息条 `.p-info`**（播放器正下方）：**作品名称 / 目前评分（0~10，有效率）/ 收藏按钮 / 标签 / 我的评分（10 星）**。
   - 标签 chips 的删除按钮**仅 admin 渲染**（`player.html` 里 `ME.role` 来自 `/api/me`）；
     普通账号只增不删，后端 `DELETE /api/media/{id}/tags` 亦有 `require_admin` 兜底。
   - **我的评分**：`renderMyStars/setMyRating`，点第 N 颗 = N 分、再点当前最高星清除；后端返回均值后 `paintRating()` 就地刷新大分数。
   - 切换推荐卡片会重新拉 `/api/media/{id}` 刷新这条信息（`loadMediaInfo()`）。
   - **键盘**：`←`/`→` 视频 ±15s（`Ctrl` = ±30s，`SEEK_STEP/SEEK_STEP_CTRL`）、`↑`/`↓` 音量 ±5%（`VOLUME_STEP`），
     反馈走 `showHud()`（`.p-hud`，1.1s 自动消失）；输入框聚焦时不抢键。
     ⚠️ 方向键已不再做轮播翻页（翻页 = 箭头按钮 / 圆点 / 自动轮播 / 换一批）。
3. **推荐轮播 `.p-reco`**：每页 `.rc-page` 固定 **2 行 × 6 列 = 12 格**，`overflow: hidden` + `translate3d` 分页，**永不滚动**。
   整区 **`zoom: 1.3`** 放大（卡片/文字/按钮同步 ×1.3），`width: 76.923%` 反向补偿使可视宽度仍与播放器区域一致。
   - 卡片文字行展示**发布时间**（`fmtPublish()`，`publish_date` → `YYYY-MM`，退回年份；作品名保留在 `title` 属性）。
   - 卡片徽章展示**具体共享标签**（`tagChips()` 读 `shared_tag_names`，最多 `SHARED_TAG_SHOW=3` 个 + `+N` 折叠，无标签时退回时长）；
     **不再显示**「N 个相同标签 / 同制作组 / 同期作品」与制作组。
4. **评论区 `.p-comments`（TODO 预留）**：轮播下方 `<section id="commentSec" hidden>` 占位，当前不渲染；
   实现要点见 [../docs/architecture.md](../docs/architecture.md) 的「未来 todo → 播放页评论区」。

**可调常量（都在内联脚本顶部）**

| 常量 | 默认 | 作用 |
|---|---|---|
| `PER_PAGE` | 12 | 每页格数（2 行 × 6 列） |
| `MAX_PAGES` | 3 | 最多几页 |
| `AUTO_DELAY` | 8000 | 自动翻页间隔（毫秒） |

**交互清单**：点击卡片当场切片 · 当前项高亮 · 悬停缩放 + 播放图标 · 8 秒自动轮播（悬停/聚焦暂停）· 圆点跳页 · 两侧箭头（单页自动隐藏）· 「换一批」（换随机 seed）· 自动轮播开关（存 `localStorage.reco_auto`）· ←/→ 翻页（焦点在播放器上时不拦截）。

**响应式**：>1200px 严格 6 列 → ≤1200 4 列 → ≤820 3 列（隐藏箭头）→ ≤520 2 列。封面一律 `object-fit: contain`，**不放大、不失真**。

**无障碍**：箭头/圆点有 `aria-label`，翻页状态 `aria-live`，支持 `prefers-reduced-motion`（系统开启减弱动效则默认关闭自动轮播）。

### `logs.html` — 通知详情
读 `/api/admin/notifications/{nid}` 渲染任务日志正文。

### `assets/fallback.svg`
`/api/poster/{id}` 找不到本地素材时返回的占位图。**换占位图直接替换这个文件**。

---

## 常见修改点速查

| 我想… | 改这里 |
|---|---|
| 换主题色 / 配色 | `style.css` 的 `:root` 变量 |
| 改首页布局 / 卡片样式 | `index.html` 结构 + `style.css` 对应类 |
| 改交互逻辑 / 请求接口 | `app.js`（搜索 → 找到对应模块的函数） |
| 改筛选栏持久化 / 自动加载 | `app.js` 的 `saveFilters/restoreFilters`、`resetPaging/nextPage/OOM_CARD_STEP` |
| 改我的评分星级 / 分数口径 | `app.js` 的 `myStarsHTML` / `ratingBadge` / `applyRatingEverywhere` |
| 改重复作品检测交互 | `app.js` 的 `dupScan/dupRender/dupDelete/dupDeleteFiles/dupIgnore` + `index.html` 的「重复作品检测」分段 |
| 改关联作品栏（固定/高度/标记） | `player.html` 的 `loadRelated/renderRelated/fitRelatedHeight`（`relatedLoaded` 控制只查一次） |
| 改标签气泡行为 | `app.js` 里的单例 `state._picker` 与渲染函数（三处共用） |
| 改播放页轮播页数 / 速度 / 列数 | `player.html` 顶部常量 + CSS 的 `.rc-page{grid-template-columns}` 媒体查询 |
| 改播放页信息条（名称/评分/收藏/标签） | `player.html` 的 `.p-info` 结构 + `loadMediaInfo/renderFav/renderTags/addTag/removeTag` |
| 改标签增删权限 | `app/authz.py`（角色来自 `users` 表）+ `media.py` 的 tags 接口依赖；前端 `app.isAdmin()` / player 的 `ME.role` |
| 接评论区 | `player.html` 的 `#commentSec` 占位 + 新建 `comments` 表与对应路由 |
| 改封面占位图 | `assets/fallback.svg` |
| 加一个新的管理弹层 | 不建议；请并入现有「白屏管理」分段 |

---

## 相关文档
- 功能操作说明：[../docs/user-manual.md](../docs/user-manual.md)
- 架构与数据流：[../docs/architecture.md](../docs/architecture.md)
