"""扫描暂存区：扫描只产出「待导入清单」，人工点「导入」才真正写库。

- 扫描（``scope=full / path``）一律跑 **dry_run** 模式，把「新增 / 有变化」的候选收集到这里，**不写库**。
- 用户在界面上看到清单并点「导入」后，由 ``scanner.import_staged`` 真正落库；导入成功才清空暂存。
- 仅存于**进程内存**：重启服务即清空（避免遗留过期路径），需要时重新扫描即可，代价很低。
"""
import threading

_lock = threading.Lock()
_items = []     # [{action: "add"|"update", media_id, category, title, file_path, ...}]
_meta = {}      # {"scope","path","category","created_at"}


def set_stage(items, meta) -> int:
    """覆盖式写入暂存清单（每次新扫描都会重置），返回条数。"""
    global _items, _meta
    with _lock:
        _items = [dict(i) for i in (items or [])]
        _meta = dict(meta or {})
        return len(_items)


def get_meta() -> dict:
    with _lock:
        return dict(_meta)


def summary() -> dict:
    with _lock:
        return {
            "meta": dict(_meta),
            "total": len(_items),
            "added": sum(1 for i in _items if i.get("action") == "add"),
            "updated": sum(1 for i in _items if i.get("action") == "update"),
        }


def items() -> list:
    """拷贝一份完整清单（导入线程用）。"""
    with _lock:
        return [dict(i) for i in _items]


def view(limit: int = 500, offset: int = 0) -> list:
    """裁剪后的展示视图（不含 meta/scan_hash 等内部字段）。"""
    with _lock:
        rows = _items[offset:offset + limit]
    return [{
        "action": r.get("action"),
        "media_id": r.get("media_id"),
        "category": r.get("category"),
        "title": r.get("title"),
        "title_jp": r.get("title_jp"),
        "year": r.get("year"),
        "publish_date": r.get("publish_date"),
        "studio": r.get("studio"),
        "file_path": r.get("file_path"),
        "file_size": r.get("file_size"),
        "has_poster": bool(r.get("poster_path")),
    } for r in rows]


def clear() -> int:
    global _items, _meta
    with _lock:
        n = len(_items)
        _items, _meta = [], {}
        return n
