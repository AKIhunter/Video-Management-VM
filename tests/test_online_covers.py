"""联网封面补全的单元测试（网络部分打补丁，验证解析与任务写回逻辑）。"""
import json
import os

import pytest

from app import db
from app.services import online_covers as oc


def _mk_db(tmp_path, rows, monkeypatch):
    """在临时库建表并插入 rows；config.load 与 BASE 由 monkeypatch 贯穿整个测试。"""
    monkeypatch.setattr(oc.cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    # cover_dir 也指向临时目录，避免任务写到真实 cover_cache/
    monkeypatch.setattr(oc.cfg_mod, "BASE", str(tmp_path))
    con = db.connect()
    db.init(con)
    for r in rows:
        con.execute(
            "INSERT INTO media(id, category, title, file_path, poster_path, meta, edited_fields) "
            "VALUES(?,?,?,?,?,?,?)",
            (r["id"], "视频", r["title"], r["file_path"], r.get("poster_path"),
             json.dumps(r.get("meta") or {}), json.dumps(r.get("edited") or [])))
    con.commit()
    con.close()


def test_extract_image_candidates_og_first():
    html = ('<meta property="og:image" content="https://media-db.example.com/wp/wp-content/1.jpg">'
            '<img src="/wp/a.jpg"><img src="/wp/b.png"><img src="https://x.com/logo.png">')
    cands = oc._extract_image_candidates(html)
    assert cands[0] == "https://media-db.example.com/wp/wp-content/1.jpg"
    assert "/wp/a.jpg" in cands and "logo.png" not in cands


def test_extract_image_candidates_rev_attr_order():
    html = '<meta content="https://media-db.example.com/x.jpg" property="og:image">'
    assert oc._extract_image_candidates(html)[0] == "https://media-db.example.com/x.jpg"


def test_safe_stem():
    assert ":" not in oc._safe_stem(r"a/b:c*d?e")
    assert oc._safe_stem("") == "cover"


def test_verify_image_rejects_tiny_bytes():
    ok, w, h = oc._verify_image(b"1234")
    assert not ok


def test_build_keyword_truncates_and_cleans():
    kw = oc._build_keyword({"title": "デーモンバスターズ ～ひかりとひかりのデーモン退治～ 「ドキドキッ 勇者だらけ」"})
    assert "「" not in kw and len(kw) <= oc.KW_MAX
    kw2 = oc._build_keyword({"title": "OVAふたりの未来ノート2 ＃1"})
    assert "＃" not in kw2
    kw3 = oc._build_keyword({"title": "僕と先生と放課後の部室 後編"})
    assert "後編" in kw3 or "第" not in kw3


def test_rank_candidates_filters_site_images_and_prefers_year():
    cands = [
        "https://media-db.example.com/wp/wp-content/uploads/2025/10/aj01.webp",
        "https://media-db.example.com/wp/wp-content/uploads/2017/06/cropped-201706.jpg",
        "https://media-db.example.com/wp/wp-content/uploads/2017/06/ichigo-1.jpg",
        "https://media-db.example.com/wp/wp-content/uploads/2020/11/ichigo-2.jpg",
        "https://media-db.example.com/logo.png",
    ]
    ranked = oc._rank_candidates(cands, 2017)
    assert "logo.png" not in ranked
    assert "aj01.webp" not in ranked
    assert "cropped-201706.jpg" not in ranked
    assert ranked[0].endswith("ichigo-1.jpg")      # 同年份优先
    assert ranked[1].endswith("ichigo-2.jpg")      # 距作品年份更远的在后


def test_expand_candidates_restores_original():
    cands = ["https://media-db.example.com/wp/wp-content/uploads/2017/04/1-1-117x117.jpg",
             "https://media-db.example.com/wp/wp-content/uploads/2017/07/5.jpg"]
    expanded = oc._expand_candidates(cands)
    assert "https://media-db.example.com/wp/wp-content/uploads/2017/04/1-1.jpg" in expanded
    assert "https://media-db.example.com/wp/wp-content/uploads/2017/07/5.jpg" in expanded


def test_clean_post_title_strips_noise():
    assert "いちごショコラふれーばー1" in oc._clean_post_title("[熟]いちごショコラふれーばー1［NAZ］")
    assert "SWAMP" in oc._clean_post_title("【熟】SWAMP STAMP AnimeEdition")


def test_run_backfill_writes_poster_and_meta(tmp_path, monkeypatch):
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 1, "title": "某作品", "file_path": str(vfile)}], monkeypatch)

    monkeypatch.setattr(oc, "search_cover", lambda row: {
        "url": "https://x/1.jpg", "candidates": ["https://x/1.jpg"],
        "confidence": 0.9, "source": "media-db.example.com", "post_url": "https://p/1",
        "title_matched": "某作品", "error": None})

    def fake_download(row, cands, cover_dir):
        p = os.path.join(cover_dir, "1_某作品.jpg")
        with open(p, "wb") as f:
            f.write(b"fakeimg")
        return p, (400, 300)

    monkeypatch.setattr(oc, "download_cover", fake_download)

    job = _FakeJob()
    res = oc.run_online_cover_backfill(job)
    assert res["filled"] == 1 and res["total"] == 1 and res["failed"] == 0

    con = db.connect()
    row = con.execute("SELECT poster_path, meta FROM media WHERE id=1").fetchone()
    con.close()
    assert row["poster_path"].endswith("1_某作品.jpg")
    meta = json.loads(row["meta"])
    assert meta["online_cover"]["confidence"] == 0.9


def test_run_backfill_skips_low_confidence(tmp_path, monkeypatch):
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 2, "title": "某作品", "file_path": str(vfile)}], monkeypatch)
    monkeypatch.setattr(oc, "search_cover",
                        lambda row: {"candidates": ["https://x/1.jpg"], "confidence": 0.1,
                                     "error": None, "url": "https://x/1.jpg"})
    job = _FakeJob()
    res = oc.run_online_cover_backfill(job)
    assert res["filled"] == 0 and res["notfound"] == 1


def test_run_backfill_respects_edited_poster(tmp_path, monkeypatch):
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 3, "title": "某作品", "file_path": str(vfile),
                       "edited": ["poster_path"]}], monkeypatch)
    monkeypatch.setattr(oc, "search_cover", lambda row: {"candidates": ["https://x/1.jpg"],
                                                         "confidence": 0.9, "error": None,
                                                         "url": "https://x/1.jpg"})
    job = _FakeJob()
    res = oc.run_online_cover_backfill(job)
    assert res["skipped"] == 1 and res["filled"] == 0


def test_run_backfill_saves_low_conf_review(tmp_path, monkeypatch):
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 5, "title": "低置信作品", "file_path": str(vfile)}], monkeypatch)
    monkeypatch.setattr(oc, "search_cover", lambda row: {
        "candidates": ["https://x/1.jpg", "https://x/2.jpg"],
        "confidence": 0.32, "error": None, "url": "https://x/1.jpg",
        "post_url": "https://p/5", "title_matched": "低置信"})
    job = _FakeJob()
    res = oc.run_online_cover_backfill(job)
    assert res["filled"] == 0 and res["notfound"] == 1
    con = db.connect()
    rev = con.execute("SELECT * FROM cover_reviews WHERE media_id=5 AND status='pending'").fetchone()
    con.close()
    assert rev is not None
    assert json.loads(rev["candidates"]) == ["https://x/1.jpg", "https://x/2.jpg"]
    assert rev["confidence"] == 0.32 and rev["post_url"] == "https://p/5"


def test_run_backfill_saves_nosource_review(tmp_path, monkeypatch):
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 6, "title": "无源作品", "file_path": str(vfile)}], monkeypatch)
    monkeypatch.setattr(oc, "search_cover", lambda row: {
        "candidates": [], "confidence": 0.0, "error": "未找到结果",
        "url": None, "post_url": None, "title_matched": None})
    job = _FakeJob()
    res = oc.run_online_cover_backfill(job)
    assert res["notfound"] == 1
    con = db.connect()
    rev = con.execute("SELECT * FROM cover_reviews WHERE media_id=6 AND status='pending'").fetchone()
    con.close()
    assert rev is not None and rev["candidates"] == "[]" and rev["error"] == "未找到结果"


def test_save_review_upsert_replaces_pending(tmp_path, monkeypatch):
    _mk_db(tmp_path, [], monkeypatch)
    con = db.connect()
    oc.save_review(con, 1, {"candidates": ["https://x/1.jpg"], "confidence": 0.3,
                            "post_url": "https://p/1", "title_matched": "T", "error": None})
    oc.save_review(con, 1, {"candidates": ["https://x/2.jpg"], "confidence": 0.35,
                            "post_url": "https://p/2", "title_matched": "T2", "error": None})
    rows = con.execute("SELECT * FROM cover_reviews WHERE media_id=1 AND status='pending'").fetchall()
    con.close()
    assert len(rows) == 1
    assert json.loads(rows[0]["candidates"]) == ["https://x/2.jpg"]


def test_search_cover_keyword_override(monkeypatch):
    calls = []
    monkeypatch.setattr(oc, "_search_media_db",
                        lambda kw: (calls.append(kw) or ("T", "https://p/1", 0.9)))
    monkeypatch.setattr(oc, "_fetch", lambda url, **kw: b"<html></html>")
    monkeypatch.setattr(oc, "_extract_image_candidates", lambda html: ["https://x/1.jpg"])
    monkeypatch.setattr(oc, "_rank_candidates", lambda cands, year: cands)
    res = oc.search_cover({"title": "原标题", "file_path": "D:/2020年01月/v.mp4"},
                          keyword="自定义关键词")
    assert calls and calls[0] == "自定义关键词"
    assert res["candidates"] == ["https://x/1.jpg"]


def test_accept_route_function_writes_back(tmp_path, monkeypatch):
    from app.routers import admin as admin_mod
    monkeypatch.setattr(oc.cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    monkeypatch.setattr(oc.cfg_mod, "BASE", str(tmp_path))
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 8, "title": "审定作品", "file_path": str(vfile)}], monkeypatch)
    con = db.connect()
    oc.save_review(con, 8, {"candidates": ["https://x/1.jpg", "https://x/2.jpg"],
                            "confidence": 0.33, "post_url": "https://p/8",
                            "title_matched": "T", "error": None})
    con.commit()

    def fake_download(row, cands, cover_dir):
        p = os.path.join(cover_dir, "8_审定作品.jpg")
        with open(p, "wb") as f:
            f.write(b"img")
        return p, (400, 300)

    monkeypatch.setattr(admin_mod, "download_cover", fake_download)
    rev = con.execute("SELECT id FROM cover_reviews WHERE media_id=8").fetchone()
    r = admin_mod.accept_cover_review(rev["id"], {"url": "https://x/1.jpg"}, None, con)
    assert r["ok"] and r["media_id"] == 8
    m = con.execute("SELECT poster_path, edited_fields, meta FROM media WHERE id=8").fetchone()
    assert m["poster_path"].endswith("8_审定作品.jpg")
    assert "poster_path" in json.loads(m["edited_fields"])
    # accept 已统一为删除记录（与 batch-submit 行为一致）
    assert con.execute("SELECT COUNT(*) c FROM cover_reviews WHERE id=?",
                       (rev["id"],)).fetchone()["c"] == 0
    assert json.loads(m["meta"])["online_cover"]["manual_review"] is True
    # 已处理的记录不可重复采纳（记录已删除 → 404）
    import pytest as _pytest
    with _pytest.raises(Exception):
        admin_mod.accept_cover_review(rev["id"], None, None, con)
    con.close()


def test_accept_route_rejects_unknown_candidate(tmp_path, monkeypatch):
    from app.routers import admin as admin_mod
    monkeypatch.setattr(oc.cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    monkeypatch.setattr(oc.cfg_mod, "BASE", str(tmp_path))
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 9, "title": "审定作品", "file_path": str(vfile)}], monkeypatch)
    con = db.connect()
    oc.save_review(con, 9, {"candidates": ["https://x/1.jpg"], "confidence": 0.3,
                            "post_url": "https://p/9", "title_matched": "T", "error": None})
    con.commit()
    rev = con.execute("SELECT id FROM cover_reviews WHERE media_id=9").fetchone()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        admin_mod.accept_cover_review(rev["id"], {"url": "https://x/999.jpg"}, None, con)
    assert ei.value.status_code == 400
    con.close()


def test_reject_and_rescan_route_functions(tmp_path, monkeypatch):
    from app.routers import admin as admin_mod
    monkeypatch.setattr(oc.cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    monkeypatch.setattr(oc.cfg_mod, "BASE", str(tmp_path))
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    _mk_db(tmp_path, [{"id": 10, "title": "审定作品", "file_path": str(vfile)}], monkeypatch)
    con = db.connect()
    oc.save_review(con, 10, {"candidates": ["https://x/1.jpg"], "confidence": 0.3,
                             "post_url": "https://p/10", "title_matched": "T", "error": None})
    con.commit()
    rev = con.execute("SELECT id FROM cover_reviews WHERE media_id=10").fetchone()

    r = admin_mod.reject_cover_review(rev["id"], None, con)
    assert r["ok"]
    # 拒绝后记录被删除（不再以 rejected 状态保留）
    assert con.execute("SELECT COUNT(*) c FROM cover_reviews WHERE id=?",
                       (rev["id"],)).fetchone()["c"] == 0

    # 重新建一条 pending 测 rescan
    oc.save_review(con, 10, {"candidates": ["https://x/1.jpg"], "confidence": 0.3,
                             "post_url": "https://p/10", "title_matched": "T", "error": None})
    con.commit()
    rev2 = con.execute("SELECT id FROM cover_reviews WHERE media_id=10 AND status='pending'").fetchone()
    monkeypatch.setattr(admin_mod, "search_cover",
                        lambda row, keyword=None: {"candidates": ["https://y/new.jpg"],
                                                   "confidence": 0.5, "post_url": "https://p/new",
                                                   "title_matched": "新帖", "error": None})
    r2 = admin_mod.rescan_cover_review(rev2["id"], {"keyword": "新关键词"}, None, con)
    assert r2["ok"] and r2["review"]["candidates"] == ["https://y/new.jpg"]
    assert r2["review"]["confidence"] == 0.5
    con.close()


def test_run_backfill_skips_existing_poster(tmp_path, monkeypatch):
    vfile = tmp_path / "v.mp4"
    vfile.touch()
    pfile = tmp_path / "have.jpg"
    pfile.touch()
    _mk_db(tmp_path, [{"id": 4, "title": "某作品", "file_path": str(vfile),
                       "poster_path": str(pfile)}], monkeypatch)
    job = _FakeJob()
    res = oc.run_online_cover_backfill(job)
    assert res["total"] == 0


class _FakeJob:
    def __init__(self, ids=None):
        import threading
        self.stop = threading.Event()
        self.meta = {"ids": ids} if ids else {}

    def begin(self, total, current=None):
        pass

    def tick(self, done, total=None, current=None):
        pass


def test_run_backfill_scope_ids_filters_targets(tmp_path, monkeypatch):
    """联网搜封面必须限定在解析出的索引ID范围内。"""
    v1, v2 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    v1.touch(); v2.touch()
    _mk_db(tmp_path, [{"id": 11, "title": "作品甲", "file_path": str(v1)},
                      {"id": 12, "title": "作品乙", "file_path": str(v2)}], monkeypatch)
    monkeypatch.setattr(oc, "search_cover", lambda row: {
        "candidates": [], "confidence": 0, "source": "media-db.example.com",
        "post_url": None, "title_matched": "x", "error": "无源"})

    res = oc.run_online_cover_backfill(_FakeJob(ids=[12]))
    assert res["total"] == 1 and res["scope_ids"] == 1

    res_all = oc.run_online_cover_backfill(_FakeJob())   # 不传 ids：不限制（向后兼容）
    assert res_all["total"] == 2 and res_all["scope_ids"] == 0


def test_cover_online_requires_ids(tmp_path, monkeypatch):
    """未解析索引ID时，联网搜封面不得启动，并给出操作提示。"""
    from app.routers import admin as admin_mod
    r = admin_mod.trigger_cover_online({"ids": []}, None)
    assert r["started"] is False and "解析" in r["reason"]
