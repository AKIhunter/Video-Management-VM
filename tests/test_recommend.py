"""播放页推荐轮播：多因子加权打分与分页编排。

全部使用临时库与自造数据，不接触真实库。
断言只涉及 id / 计数 / 结构，不读取标签文本内容。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import recommend as rec  # noqa: E402


def _mk_cfg(tmp_path, monkeypatch):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    db.init(con)
    return con


def _add(con, mid, title, studio=None, year=None, rating=None, category="视频"):
    con.execute(
        "INSERT INTO media(id, category, title, file_path, studio, year, rating_norm) "
        "VALUES(?,?,?,?,?,?,?)",
        (mid, category, title, f"/tmp/v-{mid}.mp4", studio, year, rating))
    con.commit()


def _tag(con, mid, tid):
    con.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES(?,?)", (mid, tid))
    con.commit()


def _flatten(r):
    return [it for page in r["pages"] for it in page]


# ---------------- 基础分页结构 ----------------
def test_pagination_shape_and_excludes_self(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    for i in range(1, 32):                      # 31 部，排除自身剩 30
        _add(con, i, f"作品{i}", studio=f"S{i % 5}", year=2000 + (i % 10), rating=0.5)
    r = rec.recommend(con, 1, uid=1, per_page=12, pages=3, seed=42)
    assert r["per_page"] == 12
    assert len(r["pages"]) == 3
    assert [len(p) for p in r["pages"]] == [12, 12, 6]   # 每页 2×6，不滚动
    ids = [it["id"] for it in _flatten(r)]
    assert 1 not in ids                                    # 排除当前作品
    assert len(ids) == len(set(ids)) == 30                 # 无重复
    assert r["profile"]["candidate_total"] == 30


def test_page_size_clamped(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    for i in range(1, 6):
        _add(con, i, f"作品{i}")
    r = rec.recommend(con, 1, uid=1, per_page=99, pages=99, seed=1)  # 越界参数被收敛
    assert r["per_page"] == 24 and len(r["pages"]) <= 1


# ---------------- 主因子：标签重合度 ----------------
def test_shared_tags_rank_first(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    for i in range(1, 41):
        _add(con, i, f"作品{i}", rating=0.5)
    # id=1 的标签池
    for t in (1, 2, 3):
        _tag(con, 1, t)
    # 强相关：共享全部 3 个标签
    for i in (2, 3):
        for t in (1, 2, 3):
            _tag(con, i, t)
    # 弱相关：只共享 1 个标签
    for i in (4, 5):
        _tag(con, i, 1)

    r = rec.recommend(con, 1, uid=1, per_page=12, pages=3, seed=7)
    first_page_ids = [it["id"] for it in r["pages"][0]]
    assert {2, 3}.issubset(set(first_page_ids))            # 强相关进第一页
    top2 = [it["id"] for it in r["pages"][0][:2]]
    assert set(top2) == {2, 3}                              # 且排在最前
    # 共享标签数被正确回传（计数，不含标签文本）
    strong = next(it for it in _flatten(r) if it["id"] == 2)
    assert strong["shared_tags"] == 3
    assert "tag" in strong["reason"]


def test_no_tags_still_recommends(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    for i in range(1, 21):
        _add(con, i, f"作品{i}", studio="同社", year=2010, rating=0.7)
    r = rec.recommend(con, 1, uid=1, per_page=12, pages=3, seed=3)
    assert r["profile"]["has_tags"] is False
    assert len(r["pages"]) == 2 and len(r["pages"][0]) == 12
    assert all(it["shared_tags"] == 0 for it in _flatten(r))


# ---------------- 多样性约束 ----------------
def test_same_studio_cap_per_page(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "当前", studio="S0")
    for i in range(2, 32):                                  # 30 部，6 个制作组
        _add(con, i, f"作品{i}", studio=f"S{i % 6}")
    r = rec.recommend(con, 1, uid=1, per_page=12, pages=2, seed=11)
    for page in r["pages"]:
        counts = {}
        for it in page:
            counts[it["studio"]] = counts.get(it["studio"], 0) + 1
        assert max(counts.values()) <= rec.MAX_SAME_STUDIO   # 同一制作组不包场
        assert len(page) == 12                               # 且每页依然填满
    assert "studio" in r["pages"][0][0]["reason"]            # 同制作组排在首位


def test_arrange_fills_page_when_pool_is_narrow(tmp_path, monkeypatch):
    """候选池只有两个制作组时，硬上限无法满足，也必须把每页填满（软约束生效）。"""
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "当前", studio="A")
    for i in range(2, 10):
        _add(con, i, f"作品{i}", studio="A")
    for i in range(10, 34):
        _add(con, i, f"作品{i}", studio="B")
    r = rec.recommend(con, 1, uid=1, per_page=12, pages=2, seed=11)
    assert [len(p) for p in r["pages"]] == [12, 12]
    ids = [it["id"] for it in _flatten(r)]
    assert len(ids) == len(set(ids)) == 24


def test_watch_state_demotes_finished(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "当前")
    for i in range(2, 6):
        _add(con, i, f"作品{i}", rating=0.5)
    # 同因子下，看完 vs 未看
    con.execute("INSERT INTO watch_state(media_id,user_id,status) VALUES(2,1,'看完')")
    con.execute("INSERT INTO watch_state(media_id,user_id,status) VALUES(4,1,'未看')")
    con.commit()
    seen = {}
    for _ in range(6):                                      # 多次取样削弱随机扰动
        r = rec.recommend(con, 1, uid=1, per_page=12, pages=1)
        for rank, it in enumerate(_flatten(r)):
            seen.setdefault(it["id"], []).append(rank)
    avg = {k: sum(v) / len(v) for k, v in seen.items()}
    assert avg[4] < avg[2]                                  # 未看排在看完之前


# ---------------- 确定性 ----------------
def test_seed_is_deterministic(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    for i in range(1, 41):
        _add(con, i, f"作品{i}", studio=f"S{i % 7}", year=2005 + (i % 8), rating=0.4)
    a = rec.recommend(con, 1, uid=1, per_page=12, pages=3, seed=99)
    b = rec.recommend(con, 1, uid=1, per_page=12, pages=3, seed=99)
    assert [it["id"] for it in _flatten(a)] == [it["id"] for it in _flatten(b)]
    c = rec.recommend(con, 1, uid=1, per_page=12, pages=3, seed=100)
    assert [it["id"] for it in _flatten(a)] != [it["id"] for it in _flatten(c)]


def test_missing_source_returns_empty(tmp_path, monkeypatch):
    con = _mk_cfg(tmp_path, monkeypatch)
    _add(con, 1, "作品1")
    r = rec.recommend(con, 999, uid=1)
    assert r["pages"] == [] and r["page_count"] == 0
