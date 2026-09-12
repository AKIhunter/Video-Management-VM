# `cover_cache/` — 联网封面缓存（生成物，但**不要随手删**）

## 里面是什么
从互联网下载、**已被人工审定采用**的封面图，文件名形如 `{media_id}_{安全标题}.{ext}`。

## 它不是垃圾文件
```
联网搜索封面 → 人工审定列表点「采用所选」
   → online_covers.download_cover() 下载到 cover_cache/
   → 写回 media.poster_path 指向这个文件
```
也就是说：**目录里的图 = 库里正在使用的封面**。删除会让对应作品立刻破图（退化为 `assets/fallback.svg`），
且 `poster_path` 变成悬空路径，只能重新联网搜索 + 人工审定才能恢复。

## 自带的清理机制
`app/routers/admin.py :: _cleanup_dirty_data('cover-online')`（在终止联网封面任务时触发）会：
1. 删除 `cover_reviews` 中 `status='pending'` 的待审定候选；
2. 删除 `cover_cache/` 下**没有被任何 `poster_path` 引用**的孤儿文件。

所以孤儿的清理是自动的，**不需要手工删目录**。

## 如果确实想腾空这个目录
任选其一：
1. **迁移**：把图片转存到别处并 `UPDATE media.poster_path` 指过去（总量不变，只是换个位置）；
2. **弃用**：清空相关作品的 `poster_path`，重跑「封面补全」（本地匹配 → 抽帧 → 联网）拿新封面。

## 相关代码与常量
| 想改… | 改这里 |
|---|---|
| 缓存目录名 | `app/services/online_covers.py :: CACHE_DIR`（同时改 `routers/admin.py` 里的 `cover_dir`） |
| 图片最小边长 / 长宽比上限 | `online_covers.py :: MIN_WH` / `MAX_WH_RATIO` |
| 标题相似度阈值 | `online_covers.py :: MIN_CONF` |
