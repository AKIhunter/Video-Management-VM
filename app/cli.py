import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 控制台默认 GBK，直接 print 日文/特殊字符会抛 UnicodeEncodeError，
# 统一把标准输出切成 UTF-8（无法 reconfigure 时静默降级）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app import config, db
from app.services import scanner


def cmd_init(_args):
    con = db.connect()
    db.init(con)
    con.close()
    print("[ok] 数据库已初始化:", config.load()["db_path"])


def cmd_scan(args):
    cfg = config.load()
    scope = args.scope or "full"
    print(f"[scan] scope={scope} path={args.path} roots={cfg['roots']} "
          f"分类={cfg['category_filter']}  dry_run={args.dry_run}")

    def progress(n, t, current=None):
        tm = f" 当前:{current}" if current else ""
        print(f"  进度 {n}/{t}{tm}", end="\r")

    res = scanner.do_scan(cfg, dry_run=args.dry_run, progress=progress,
                          scope=scope, path=args.path)
    print("\n[scan] 结果:", res)
    if args.pending:
        for p in res.get("rating_pending", []):
            print("  待匹配:", p["title"], "→", p["score"], "|", p["source"])


def cmd_tags_restore(args):
    """按导出文件还原 media_tags 关联（只重建 id 关联，不写入标签值）。"""
    from app.services.tag_restore import restore_from_file

    stat = restore_from_file(args.file, dry_run=not args.apply)
    mode = "已写入" if args.apply else "试运行（未写库，加 --apply 才执行）"
    print(f"[tags-restore] {mode}")
    print(f"  文件标签数 {stat['file_tags']} · 匹配到库内标签 {stat['matched_tags']}"
          f" · 未匹配（不新建）{stat['unmatched_tags']}")
    print(f"  计划/新增关联 {stat['links_added']} · 已存在跳过 {stat['skipped_existing']}"
          f" · 索引ID不存在 {stat['missing_media']} · 超上限 {stat['capped']}")
    print(f"  涉及作品 {stat['touched_media']} · 关联数 {stat['links_before']} → {stat['links_after']}"
          f" · 标签总数 {stat['total_tags']}")


def main():
    ap = argparse.ArgumentParser(prog="视频管理器", description="轻量视频索引管理器 CLI")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("init", help="初始化数据库")
    ps = sub.add_parser("scan", help="扫描建索引")
    ps.add_argument("--dry-run", action="store_true", help="仅统计不写库")
    ps.add_argument("--pending", action="store_true", help="打印未匹配的简评评分")
    ps.add_argument("--scope", choices=["full", "path"], default="full",
                    help="full=全盘 / path=指定路径")
    ps.add_argument("--path", default=None, help="scope=path 时的目标目录")
    pr = sub.add_parser("tags-restore", help="按导出 JSON 还原作品-标签关联（只重建 id 关联）")
    pr.add_argument("--file", required=True, help="导出文件路径（{tags:[{name,media_ids}]}）")
    pr.add_argument("--apply", action="store_true", help="真正写库（默认仅试运行）")
    args = ap.parse_args()
    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "scan":
        cmd_scan(args)
    elif args.cmd == "tags-restore":
        cmd_tags_restore(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()