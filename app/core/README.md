# `app/core/` — 公共工具层

**无业务语义**的通用工具，被 `services/` 与 `routers/` 复用。
依赖规则：`core` **不允许** import `services/` 或 `routers/`（避免循环依赖）。

```
core/
├─ fsutils.py        文件系统工具：带编码探测的 UTF-8 读取、简评文件发现
└─ media_helpers.py  媒体通用 helper：edited_fields 解析、媒体 404 查询
```

---

## `fsutils.py`

| 函数 | 作用 |
|---|---|
| `read_utf8(path)` | 读文本文件，**自动探测编码**（`utf-8-sig → utf-8 → gbk → big5`），失败时兜底 |
| `find_jianping_files(roots)` | 在扫描根目录下发现「简评」类文本文件，供打标 / 评分解析使用 |

> 磁盘上的中文文本编码很杂（GBK / Big5 都有），所有读文本的地方都应走 `read_utf8`，不要直接 `open()`。

## `media_helpers.py`

| 函数 | 作用 |
|---|---|
| `edited(row)` | 把 `media.edited_fields`（JSON 数组字符串）解析成 `set`；损坏时返回空集 |
| `media_or_404(con, mid)` | 按 id 取媒体行，不存在直接抛 `HTTPException(404)` |

> `edited_fields` 是「人工编辑留痕」：写进这个集合的字段，联网补全**永不覆盖**。
> 新增一类可人工编辑的字段时，记得在对应的写入路径里调 `edited()` 判断并追加标记
> （参考 `routers/media.py :: _mark_tagedited`）。

---

## 什么该放这里

- ✅ 纯函数、无 DB 无网络、跨模块复用两次以上（编码读取、路径处理、JSON 容错解析）
- ❌ 任何带业务含义的逻辑（扫码、打分、推荐…）→ 放 `services/`
- ❌ HTTP 相关（`Request` / `HTTPException` 之外的）→ 放 `routers/`

## 常见修改点

| 我想… | 改这里 |
|---|---|
| 支持新的文本编码 | `fsutils.read_utf8()` 的探测顺序 |
| 改「简评文件」的识别规则 | `fsutils.find_jianping_files()` |
| 改 `edited_fields` 的判定语义 | `media_helpers.edited()` |
