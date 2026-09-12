# `frames_cache/` — 视频抽帧缓存（生成物）

**这个目录是运行时自动生成的，内容不需要提交，可随时删除**（删了之后重跑「截图视频封面」会重新生成）。

## 里面是什么
`{media_id}.jpg` —— 由 `app/services/frames.py` 调用 **ffmpeg** 从视频里抽出的预览帧小图（宽 512px）。

## 怎么产生的
```
白屏管理 → 封面补全 → 「截图视频封面」
   → POST /api/admin/cover-frame { ids }
   → frames.run_frame_backfill(job, ids)
   → pick_bright_frame() 挑一帧「不黑」的
   → 落盘 frames_cache/{id}.jpg 并写回 media.poster_path
```

抽帧前端的 `posterHTML()` 只是渲染 `/api/poster/{id}`，**真正的取帧在服务端完成** —— 这是为了
避免浏览器为每张卡片离屏抓帧导致内存暴涨（历史上确实踩过这个坑）。

## 删除的后果
- 已写回 `poster_path` 的记录会**封面破图**（退化成 `assets/fallback.svg`）。
- 所以在删除之前，先确认没有作品的 `poster_path` 指向这个目录：
  白屏管理里重跑一次「截图视频封面」即可全部恢复。

## 相关配置
| 想改… | 改这里 |
|---|---|
| 抽帧输出目录 | `config.json` 的 `frames_dir`（默认 `frames_cache`）；`app/services/frames.py :: frame_dir()` |
| 抽帧宽度（清晰度） | `app/services/frames.py :: FRAME_WIDTH`（默认 512） |
| ffmpeg 可执行文件位置 | `config.json` 的 `ffmpeg_path`（留空则用 `imageio-ffmpeg` 捆绑版） |
