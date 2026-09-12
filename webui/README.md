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
- 页眉：搜索 / 筛选栏（关键词、年份、制作组、分类、评分、状态、收藏、排序）+ **标签筛选气泡**。
- 作品卡片网格 + 详情抽屉（简介 / 标签 / 个人化操作 / 播放入口）。
- 右上角 🔔 通知气泡（5s 轮询未读数），点进 `logs.html?id=N`。
- 「白屏管理」弹层：作品编辑 / 作品管理 / 联网补全 / 扫描 / 标签管理 / 一键重启。
- 关键复用件：
  - **标签气泡**是「单例 + 上下文」结构（`state._picker = {kind, anchor, selected, title, hint, onApply}`），
    被 **筛选栏 / 批量打标签 / 作品编辑** 三处复用 —— 改气泡只改一处。
  - **封面渲染器** `refreshCoverSection`：本地封面与联网封面共用，数据源是 `/api/admin/task`。
  - 进度条统一 `width:0` 待机。

### `style.css` — 样式
- **所有颜色、圆角、间距变量集中在文件顶部 `:root`**（`--accent: #ff5c8a` 等）。
  换主题色 / 调色板 → **只改 `:root`**，不要在具体选择器里写死颜色。
- 层级约定：标签气泡用 `position: fixed` + `z-index: 300` 挂到 `body`，保证不被卡片裁剪或压制。

### `player.html` — 播放页
独立单文件，结构分两块：

1. **播放窗口 `.p-player`**：`video { width: 100% }`，与下方推荐区**同在 `max-width: 1440px` 容器**内 → 两者严格同宽同列。
2. **推荐轮播 `.p-reco`**：每页 `.rc-page` 固定 **2 行 × 6 列 = 12 格**，`overflow: hidden` + `translate3d` 分页，**永不滚动**。

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
| 改标签气泡行为 | `app.js` 里的单例 `state._picker` 与渲染函数（三处共用） |
| 改播放页轮播页数 / 速度 / 列数 | `player.html` 顶部常量 + CSS 的 `.rc-page{grid-template-columns}` 媒体查询 |
| 改封面占位图 | `assets/fallback.svg` |
| 加一个新的管理弹层 | 不建议；请并入现有「白屏管理」分段 |

---

## 相关文档
- 功能操作说明：[../docs/user-manual.md](../docs/user-manual.md)
- 架构与数据流：[../docs/architecture.md](../docs/architecture.md)
