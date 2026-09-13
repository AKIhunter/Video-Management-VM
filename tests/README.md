# `tests/` — 单元测试

`pytest`。**不启动服务、不动真实数据库**：直接 import 模块调用函数，用 `monkeypatch` 把 `config.load` 指向临时库。

```powershell
python -m pytest tests/ -q          # 全量（当前 151 passed）
python -m pytest tests/test_recommend.py -q
```

---

## 测试写法约定（照抄现有用例即可）

```python
def _mk_cfg(tmp_path, monkeypatch):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect(); db.init(con)
    return con
```

三条铁律：
1. **绝不连真实库** —— 一律 `tmp_path` 造临时库。
2. **不读作品表完整内容、不读标签文本** —— 断言只涉及 id / 计数 / 结构正则 / 布尔。
3. **外部依赖可跳过** —— 需要 ffmpeg 的用例无 ffmpeg 时 `pytest.skip`；需要 Node 的同理。

---

## 用例分布

| 文件 | 覆盖 |
|---|---|
| `test_parser.py` | 文件名三时代解析、标题归一、评分换算（原文星级 → 库内 0~10）、海报查找 |
| `test_scan_completion.py` | 扫描幂等 / 定点范围、联网补全流程 |
| `test_scan_import.py` | **两段式扫描**：dry_run 只进暂存不写库、按指定分类导入、消失/变动文件跳过、暂存区读写、导入防呆（拒绝空 / 「自动（按目录名）」/ 空清单） |
| `test_metadata.py` | 元数据候选清洗与长度约束 |
| `test_kinks.py` | 题材词典打标、`TAG_LIMIT` |
| `test_media_tags.py` | 标签增删、`edited_fields` 标记 |
| `test_bulk_tags.py` | 批量打标签 |
| `test_categories_manage.py` | 分类字典 CRUD 与 `sync_categories` |
| `test_tags_io.py` | 标签导出 / 导入（覆盖模式快照重绑） |
| `test_tag_restore.py` | 按导出 JSON 还原 `media_tags` 关联 |
| `test_covers.py` / `test_cover_extra.py` | 本地封面匹配、指定目录补全、遗留 video_frame 标记兼容 |
| `test_online_covers.py` | 联网封面搜索 / 审定候选 |
| `test_frames.py` | ffmpeg 抽帧（无 ffmpeg 自动 skip）：按 ids 限定范围、写回封面、**进度必须到 100%** |
| `test_frame_stats_js.py` | 前端抽帧统计脚本（无 Node 自动 skip） |
| `test_notifications.py` | 通知读写 |
| `test_roles.py` | **角色/权限**：`current_user` 角色取自 `users` 表、`require_admin` 403 门禁、标签「增可 / 删限 admin」的路由级依赖约束 |
| `test_account.py` | **个人中心与账号**：等级/签到规则纯函数、每日一次幂等、连续与断签、收藏夹（按账号隔离、不含路径）、账号 CRUD 与默认账号保护 |
| `test_ratings.py` | **评分（0~10）**：历史量纲 ×2 迁移与幂等、多用户均值、重复评分覆盖、清空回退、空闲结算与「有长任务则跳过」 |
| `test_duplicates.py` | **重复检测**：完全相同 / 高相似分组、分类隔离、阈值、空标题、只暴露安全字段 |
| `test_delete_files.py` | **删除作品（索引+文件）**：视频删除、素材目录海报保留并列出、服务端缓存封面一并删、文件缺失仍删索引、清空关联、缺 ids 拒绝 |
| `test_recommend.py` | **推荐轮播**：分页结构、排除自身、标签主因子、无标签兜底、多样性上限、窄池填满、观看状态降权、`seed` 确定性、源缺失 |

---

## 常见修改点速查

| 我想… | 改这里 |
|---|---|
| 加新用例 | 在此目录建 `test_<模块>.py`，按上面的 `_mk_cfg` 模板 |
| 用固定随机结果做断言 | 给被测函数传 `seed=`（推荐算法支持） |
| 跳过需要外部程序的用例 | `pytest.skip("no ffmpeg")` |
| 改测试运行方式 | 无配置文件（无 `pytest.ini` / `conftest.py`），直接命令行 |

> 注意：测试数量会随功能增长，本文档中的数字如与 `pytest` 输出不一致，**以实际输出为准**。
