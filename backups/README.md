# `backups/` — 导出备份（⚠️ **含敏感数据，禁止提交**）

## 里面是什么
标签体系操作前自动生成的快照，文件形如：

```
tags_backup_YYYYMMDD_HHMMSS.csv          # 标签字典：id,标签值
media_tags_backup_YYYYMMDD_HHMMSS.csv    # 关联表：media_id,tag_id
```

产生时机：**覆盖导入标签 / 恢复标签关联**等破坏性操作前，服务端先落一份快照，便于回滚。

## ⚠️ 安全要求
这两个文件包含**标签文本**与**作品 id 关联**，属于本项目最敏感的数据之一：

- **绝不能提交到 Git**（已在 `.gitignore` 中排除，仅保留本 README）。
- 不要复制进任何「纯净版 / 示例版」项目。
- 分享日志或截图前先确认没有把这里的内容带出去。

## 回滚方式
```powershell
# 用标签快照重建关联（只重建 id↔id，不写入标签文本）
python -m app.cli tags-restore --file backups/media_tags_backup_YYYYMMDD_HHMMSS.csv
```

## 相关代码
- 生成快照：`app/routers/admin.py`（标签导入 / 恢复相关分段）
- 还原关联：`app/services/tag_restore.py :: restore_from_file()`
