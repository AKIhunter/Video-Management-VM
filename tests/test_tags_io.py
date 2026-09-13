"""标签筛选（media）+ 标签导出/导入（admin：纯文本 `id,标签值`）+ 删除防误触 的单测。

注：全部使用临时库与自造标签值，不接触任何真实标签数据。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.routers import admin, media  # noqa: E402


def _mk_db(tmp_path, monkeypatch):
    cfg = lambda: {  # noqa: E731
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    }
    monkeypatch.setattr(media.cfg_mod, "load", cfg)
    monkeypatch.setattr(admin.cfg_mod, "load", cfg)
    con = db.connect()
    db.init(con)
    for i in (1, 2, 3):
        con.execute(
            "INSERT INTO media(id, category, title, file_path, edited_fields) "
            "VALUES(?, '视频', ?, ?, '[]')", (i, f"作品{i}", f"/x/{i}.mp4"))
    con.commit()
    return con


def _add(con, mid, name):
    # _u 是鉴权占位（路由层由 Depends 注入），单元测试直接给个 admin 身份
    return media.add_tag(mid, media.TagIn(name=name), _u={"id": 1, "role": "admin"}, con=con)


def _tags(con):
    return [(r["id"], r["name"]) for r in con.execute("SELECT id, name FROM tags ORDER BY id")]


# ---------------- 标签筛选 ----------------
def test_media_filter_by_tag(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "甲标签")
    _add(con, 2, "甲标签")
    _add(con, 3, "乙标签")

    r = media.list_media(tag="甲标签", con=con, size=30, page=1)
    assert r["total"] == 2
    assert {i["id"] for i in r["items"]} == {1, 2}

    assert media.list_media(tag="不存在", con=con, size=30, page=1)["total"] == 0
    assert media.list_media(con=con, size=30, page=1)["total"] == 3
    con.close()


def test_stats_exposes_tags(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "甲标签")
    _add(con, 2, "甲标签")
    _add(con, 3, "乙标签")
    names = {t["name"]: t["c"] for t in media.stats(con=con)["tags"]}
    assert names.get("甲标签") == 2 and names.get("乙标签") == 1
    con.close()


# ---------------- 导出：纯文本 `id,标签值`（UTF-8 带 BOM） ----------------
def test_export_is_plain_text_id_comma_name(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "甲标签")
    _add(con, 2, "乙标签")
    rows = _tags(con)

    resp = admin.export_tags(None, con)
    raw = resp.body
    # 必须带 UTF-8 BOM，避免 Windows Excel/记事本按 ANSI 打开产生乱码
    assert raw[:3] == b"\xef\xbb\xbf"
    body = raw.decode("utf-8")
    lines = body.lstrip("\ufeff").rstrip("\n").split("\n")
    assert len(lines) == len(rows)
    # 每行形如 `数字,任意非空值`，不含 JSON 结构
    assert all(re.match(r"^\d+,.+$", ln) for ln in lines)
    assert "{" not in body and '"' not in body
    assert [int(ln.split(",", 1)[0]) for ln in lines] == [r[0] for r in rows]
    assert [ln.split(",", 1)[1] for ln in lines] == [r[1] for r in rows]
    assert "attachment" in resp.headers.get("content-disposition", "")
    con.close()


def test_export_body_decodes_strictly_as_utf8(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "甲标签")
    resp = admin.export_tags(None, con)
    # 严格 UTF-8 可解码且无替换字符（说明导出是干净的 UTF-8，不是 ANSI）
    txt = resp.body.decode("utf-8")
    assert "\ufffd" not in txt
    con.close()


# ---------------- 导入：编码探测 ----------------
def _b64(raw: bytes) -> str:
    import base64
    return base64.b64encode(raw).decode("ascii")


def test_import_gbk_bytes_via_base64(tmp_path, monkeypatch):
    """GBK(ANSI) 文件：按原始字节上传后应被正确识别，不产生乱码。"""
    con = _mk_db(tmp_path, monkeypatch)
    text = "101,甲标签\n102,乙标签\n"
    r = admin.import_tags({"mode": "merge", "data_b64": _b64(text.encode("gbk"))}, None, con)
    assert r["added_tags"] == 2
    assert r["encoding"] in ("gbk", "big5")
    names = [n for _i, n in _tags(con)]
    assert "甲标签" in names and "乙标签" in names
    assert all("\ufffd" not in n for n in names)
    con.close()


def test_import_utf8_bom_bytes(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    raw = ("\ufeff" + "201,甲标签\n").encode("utf-8")
    r = admin.import_tags({"mode": "merge", "data_b64": _b64(raw)}, None, con)
    assert r["encoding"] == "utf-8-sig" and r["added_tags"] == 1
    con.close()


def test_import_rejects_mojibake_text(tmp_path, monkeypatch):
    """含替换字符 U+FFFD 的内容必须被拒绝，防止把乱码写进标签库。"""
    from fastapi import HTTPException
    import pytest
    con = _mk_db(tmp_path, monkeypatch)
    bad = "1,\ufffd\ufffd\ufffd"
    with pytest.raises(HTTPException):
        admin.import_tags({"mode": "merge", "data": bad}, None, con)
    with pytest.raises(HTTPException):
        admin.import_tags({"mode": "merge", "data_b64": _b64(bad.encode("utf-8"))}, None, con)
    assert _tags(con) == []
    con.close()


def test_import_overwrite_requires_confirm_text_and_backs_up(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import pytest
    con = _mk_db(tmp_path, monkeypatch)
    monkeypatch.setattr(admin.cfg_mod, "BASE", str(tmp_path))
    _add(con, 1, "旧标签")
    assert con.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0] > 0

    # 未带确认词 → 拒绝，且不做任何清空
    with pytest.raises(HTTPException):
        admin.import_tags({"mode": "overwrite", "data": "301,重建标签"}, None, con)
    assert _tags(con) != []

    r = admin.import_tags({"mode": "overwrite", "data": "301,重建标签",
                           "confirm_text": "__OVERWRITE__"}, None, con)
    assert r["mode"] == "overwrite" and r["added_tags"] == 1
    assert _tags(con) == [(301, "重建标签")]
    assert con.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0] == 0
    # 覆盖前已自动备份
    assert r["backup"] and len(r["backup"]["files"]) == 2
    for fn in r["backup"]["files"]:
        assert (tmp_path / "backups" / fn).exists()
    con.close()


# ---------------- 导入：解析并写入 ----------------
def test_import_merge_parses_lines_and_keeps_ids(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    text = "101,甲标签\n102,乙标签\n"
    r = admin.import_tags({"mode": "merge", "data": text}, None, con)
    assert r["ok"] and r["format"] == "csv-line"
    assert r["added_tags"] == 2 and r["kept_ids"] == 2 and r["remapped"] == 0
    assert _tags(con) == [(101, "甲标签"), (102, "乙标签")]
    con.close()


def test_import_merge_remaps_id_when_taken(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "原标签")  # 占用某个 id
    taken = _tags(con)[0][0]
    r = admin.import_tags({"mode": "merge", "data": f"{taken},新标签"}, None, con)
    assert r["added_tags"] == 1 and r["remapped"] == 1 and r["kept_ids"] == 0
    names = [n for _i, n in _tags(con)]
    assert "原标签" in names and "新标签" in names
    con.close()


def test_import_merge_skips_blank_header_comment_and_dupes(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "已有标签")
    text = ("# 注释行\n"
            "\n"
            "id,标签值\n"          # 表头跳过
            "200,已有标签\n"        # 名称已存在 → skipped
            "200,新标签\n")         # 新增
    r = admin.import_tags({"mode": "merge", "data": text}, None, con)
    assert r["added_tags"] == 1 and r["skipped"] == 1
    con.close()


def test_import_accepts_name_only_lines(tmp_path, monkeypatch):
    con = _mk_db(tmp_path, monkeypatch)
    r = admin.import_tags({"mode": "merge", "data": "纯名称一\n纯名称二\n"}, None, con)
    assert r["added_tags"] == 2
    con.close()


def test_overwrite_import_rebinds_associations(tmp_path, monkeypatch):
    """只更新标签字典（覆盖导入）时，作品-标签关联应按名称重新绑定，不得脱绑。"""
    con = _mk_db(tmp_path, monkeypatch)
    monkeypatch.setattr(admin.cfg_mod, "BASE", str(tmp_path))
    _add(con, 1, "甲")
    _add(con, 2, "甲")
    _add(con, 2, "乙")
    assert con.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0] == 3

    # 新字典用不同的 id（101/102）提供同样的标签名
    r = admin.import_tags({"mode": "overwrite", "data": "101,甲\n102,乙\n",
                           "confirm_text": "__OVERWRITE__"}, None, con)
    assert r["added_tags"] == 2 and r["total_tags"] == 2
    assert r["rebound_links"] == 3 and r["lost_links"] == 0
    assert r["total_links"] == 3            # 关联未丢失

    # 关联指向的是**新字典**的 id，且关联表仍然只有 id 两列
    new_ids = {name: tid for tid, name in _tags(con)}
    pairs = {(row["media_id"], row["tag_id"]) for row in con.execute(
        "SELECT media_id, tag_id FROM media_tags").fetchall()}
    assert pairs == {(1, new_ids["甲"]), (2, new_ids["甲"]), (2, new_ids["乙"])}
    cols = [c["name"] for c in con.execute("PRAGMA table_info(media_tags)").fetchall()]
    assert cols == ["media_id", "tag_id"]
    con.close()


def test_overwrite_import_reports_lost_when_name_gone(tmp_path, monkeypatch):
    """新字典缺少某个同名标签时，对应关联计入 lost_links。"""
    con = _mk_db(tmp_path, monkeypatch)
    monkeypatch.setattr(admin.cfg_mod, "BASE", str(tmp_path))
    _add(con, 1, "甲")
    _add(con, 2, "乙")
    r = admin.import_tags({"mode": "overwrite", "data": "201,甲\n",
                           "confirm_text": "__OVERWRITE__"}, None, con)
    assert r["rebound_links"] == 1 and r["lost_links"] == 1 and r["total_links"] == 1
    con.close()


def test_import_rejects_bad_mode_and_missing_data(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import pytest
    con = _mk_db(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        admin.import_tags({"mode": "bad", "data": "1,x"}, None, con)
    with pytest.raises(HTTPException):
        admin.import_tags({"mode": "merge"}, None, con)
    con.close()


def test_import_legacy_json_still_accepted(tmp_path, monkeypatch):
    """兼容旧版 JSON 导出（仅取 name，忽略 media_ids）。"""
    con = _mk_db(tmp_path, monkeypatch)
    legacy = '{"tags":[{"name":"旧甲"},{"name":"旧乙"}]}'
    r = admin.import_tags({"mode": "merge", "data": legacy}, None, con)
    assert r["added_tags"] == 2
    con.close()


def test_media_filter_by_multiple_tags_is_or(tmp_path, monkeypatch):
    """多选标签：满足任一标签即显示（OR）。"""
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "甲标签")
    _add(con, 2, "乙标签")
    _add(con, 3, "丙标签")

    r = media.list_media(tags="甲标签,乙标签", con=con, size=30, page=1)
    assert r["total"] == 2
    assert {i["id"] for i in r["items"]} == {1, 2}

    # 含不存在的标签：不影响其余命中
    assert media.list_media(tags="甲标签,不存在", con=con, size=30, page=1)["total"] == 1
    # 去重与空白容错
    assert media.list_media(tags=" 甲标签 , 甲标签 ", con=con, size=30, page=1)["total"] == 1
    # 与其它筛选可叠加（year 为空 → 全部仍是 3；仅校验不报错且结果合理）
    assert media.list_media(tags="甲标签,乙标签,丙标签", con=con, size=30, page=1)["total"] == 3
    con.close()


def test_list_tags_includes_unused(tmp_path, monkeypatch):
    """标签筛选气泡的数据源：包含字典中暂无关联的标签。"""
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "甲标签")
    con.execute("INSERT INTO tags(name) VALUES('孤立标签')")  # 无关联
    con.commit()
    items = media.list_tags(con=con)["items"]
    names = [x["name"] for x in items]
    assert "甲标签" in names and "孤立标签" in names
    by = {x["name"]: x["c"] for x in items}
    assert by["甲标签"] == 1 and by["孤立标签"] == 0
    con.close()


# ---------------- 标签恢复（乱码清理 / 简评痕迹重建） ----------------
def test_recover_dry_run_then_drop_garbled_and_restore_meta(tmp_path, monkeypatch):
    import json as _json
    con = _mk_db(tmp_path, monkeypatch)
    # 一个乱码标签（含 U+FFFD）
    con.execute("INSERT INTO tags(id, name) VALUES(901, ?)", ("\ufffd\ufffd",))
    con.execute("INSERT INTO media_tags(media_id, tag_id) VALUES(1, 901)")
    # 一部作品带简评打标留痕
    con.execute("UPDATE media SET meta=? WHERE id=2",
                (_json.dumps({"auto_tags": {"source": "jianping", "tags": ["甲标签", "乙标签"]}},
                             ensure_ascii=False),))
    con.commit()

    diag = admin.recover_tags({"dry_run": True}, None, con)
    assert diag["dry_run"] is True
    assert diag["garbled_tags"] == 1 and diag["garbled_links"] == 1
    assert diag["recoverable_media"] == 1
    assert diag["garbled_tags"] == 1  # 只读，未删除

    r = admin.recover_tags({"dry_run": False, "actions": ["drop_garbled", "restore_meta"]},
                           None, con)
    assert r["garbled_tags"] == 1 and r["restored_media"] == 1 and r["restored_links"] == 2
    assert con.execute("SELECT COUNT(*) FROM tags WHERE id=901").fetchone()[0] == 0
    names = [n for _i, n in _tags(con)]
    assert "甲标签" in names and "乙标签" in names
    assert all("\ufffd" not in n for n in names)
    con.close()


def test_recover_restore_meta_respects_tag_limit(tmp_path, monkeypatch):
    import json as _json
    from app.services.kinks import TAG_LIMIT
    con = _mk_db(tmp_path, monkeypatch)
    for i in range(TAG_LIMIT):
        _add(con, 1, f"已有{i}")
    con.execute("UPDATE media SET meta=? WHERE id=1",
                (_json.dumps({"auto_tags": {"tags": ["补充甲", "补充乙"]}}, ensure_ascii=False),))
    con.commit()
    admin.recover_tags({"dry_run": False, "actions": ["restore_meta"]}, None, con)
    n = con.execute("SELECT COUNT(*) FROM media_tags WHERE media_id=1").fetchone()[0]
    assert n == TAG_LIMIT  # 不超上限
    con.close()


# ---------------- 删除标签防误触 ----------------
def test_delete_tag_requires_confirm_name(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import pytest
    con = _mk_db(tmp_path, monkeypatch)
    _add(con, 1, "待删标签")
    _add(con, 2, "待删标签")
    tid = _tags(con)[0][0]

    with pytest.raises(HTTPException):
        admin.delete_tag(tid, "", None, con)
    with pytest.raises(HTTPException):
        admin.delete_tag(tid, "别的名字", None, con)
    assert con.execute("SELECT COUNT(*) c FROM tags WHERE id=?", (tid,)).fetchone()["c"] == 1

    r = admin.delete_tag(tid, "待删标签", None, con)
    assert r["ok"] and r["removed_links"] == 2
    assert con.execute("SELECT COUNT(*) c FROM tags WHERE id=?", (tid,)).fetchone()["c"] == 0
    assert con.execute("SELECT COUNT(*) c FROM media_tags WHERE tag_id=?", (tid,)).fetchone()["c"] == 0
    con.close()
