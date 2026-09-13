"""联网补全候选：约束校验（简介≤500字、tag≤30个且≤20字、去重）与 media-db 解析。"""
import pytest

from app.services.metadata_provider import (MediaDbResolver, LocalResolver, _pick_best,
                                   build_keyword, extract_baidu_synopsis,
                                   extract_media_db_post, extract_media_db_results,
                                   synopsis_missing, validate_candidate)


class TestValidateCandidate:
    def test_synopsis_truncated_to_500(self):
        c = validate_candidate({"synopsis": "好" * 600, "tags": []})
        assert len(c["synopsis"]) == 501  # 500 + “…”
        assert c["synopsis"].endswith("…")

    def test_synopsis_short_kept(self):
        assert validate_candidate({"synopsis": "短简介"})["synopsis"] == "短简介"

    def test_tags_dedup_and_limit(self):
        c = validate_candidate({"tags": [" 纯爱 ", "纯爱", "剧情", "", "剧情"] + ["x" * 30] * 40})
        assert c["tags"].count("纯爱") == 1
        assert all(len(t) <= 20 for t in c["tags"])
        assert len(c["tags"]) <= 30
        assert "" not in c["tags"]

    def test_whitespace_normalized(self):
        c = validate_candidate({"tags": ["  猎 奇 向  "]})
        assert c["tags"] == ["猎 奇 向"]
        assert len(c["tags"]) == 1


class TestMediaDbParse:
    def test_extract_results(self):
        html_text = '<h2><a href="https://media-db.example.com/archives/1">作品A 第一话</a></h2>' \
                    '<a href="https://media-db.example.com/category/x">分类</a>' \
                    '<a href="https://media-db.example.com/archives/2">作品B</a>'
        out = extract_media_db_results(html_text)
        assert any("作品A" in t for t, _u in out)
        assert any("作品B" in t for t, _u in out)
        # 分类链接被过滤
        assert all(u.startswith("http") for _t, u in out)

    def test_extract_post_synopsis_and_tags(self):
        html_text = (
            '<div class="entry-content">'
            '<a rel="tag">纯爱</a><a rel="tag">剧情</a>'
            '<p>这是一个很长的剧情简介，用来测试是否可以捕获正文段落并作为简介候选使用。</p>'
            '</div>'
        )
        cand = extract_media_db_post(html_text)
        assert len(cand["synopsis"]) <= 500
        assert "剧情简介" in cand["synopsis"]
        assert "纯爱" in cand["tags"]

    def test_local_resolver(self):
        r = LocalResolver().resolve({
            "title": "T", "title_jp": None, "studio": "S",
            "publish_date": "2022-01-01", "poster_path": "/p.png", "rating_norm": 4.0,
        })
        assert r["title"] == "T"
        assert r["title_jp"] == "T"
        assert r["rating"] == 4.0


class TestHelpers:
    def test_media_db_url_uses_wp_search(self):
        url = MediaDbResolver()._search_url("测试作品")
        assert url.startswith("https://media-db.example.com/wp/?s=")
        assert MediaDbResolver().base in url

    def test_synopsis_missing(self):
        assert synopsis_missing({"synopsis": ""}) is True
        assert synopsis_missing({"synopsis": "   "}) is True
        assert synopsis_missing({"synopsis": "有简介"}) is False

    def test_build_keyword_prefers_jp(self):
        row = {"title": "中文标题", "title_jp": "日本語タイトル"}
        assert build_keyword(row) == "日本語タイトル"
        assert build_keyword({"title": "只有中文", "title_jp": None}) == "只有中文"

    def test_pick_best_ranks_by_relevance(self):
        results = [("不相关结果", "u1"), ("目标作品名称", "u2"), ("另一无关", "u3")]
        best = _pick_best(results, "目标作品名称")
        assert best[1] == "u2"


class TestBaiduParse:
    def test_extract_baidu_synopsis(self):
        html_text = (
            '<div class="result"><h3><a href="https://www.baidu.com/link?url=abc">作品A 第一话</a></h3>'
            '<span>作品A的最新剧情介绍，讲述了一个温馨的故事。</span></div>'
            '<div class="result"><h3><a href="https://x/x">无关项</a></h3><span>别的东西</span></div>'
        )
        out = extract_baidu_synopsis(html_text)
        assert any("作品A" in t for t, _a, _u in out)
        # 选关联度最高的结果
        best = _pick_best(out, "作品A 第一话")
        assert "作品A" in best[0]


class TestCascade:
    def _fake_fetch(self, monkeypatch, page_text):
        import app.services.metadata_provider as mp
        monkeypatch.setattr(mp, "_fetch", lambda url, timeout=10.0: page_text)

    def test_cascade_local_short_circuit(self, monkeypatch):
        import app.services.metadata_provider as mp
        monkeypatch.setattr(mp, "_fetch", lambda url, timeout=10.0: "<html>不应联网</html>")
        from app.services.metadata_provider import CascadeResolver
        row = {"title": "T", "title_jp": "T", "studio": "S", "publish_date": None,
               "rating_norm": None, "poster_path": None, "synopsis": "本地已有简介"}
        cand = CascadeResolver().resolve(row)
        assert cand["source"] == "local"
        assert cand["synopsis"] == "本地已有简介"

    def test_cascade_falls_back_to_baidu_then_media_db(self, monkeypatch):
        import app.services.metadata_provider as mp
        state = {"calls": 0}

        def fake_fetch(url, timeout=10.0):
            state["calls"] += 1
            if url.startswith("https://www.baidu.com"):
                # 百度给不出结果 -> 降级到 media-db
                return '<div class="result"></div>'
            # media-db：命中单篇
            return ('<a href="https://media-db.example.com/archives/1">目标作品名</a>'
                    '<div class="entry-content"><a rel="tag">纯爱</a>'
                    '<p>这是一段够长的剧情简介文本，可用于补全候选。</p></div>')

        monkeypatch.setattr(mp, "_fetch", fake_fetch)
        from app.services.metadata_provider import CascadeResolver
        row = {"title": "目标作品名", "title_jp": None, "studio": "S", "publish_date": None,
               "rating_norm": None, "poster_path": None, "synopsis": ""}
        cand = CascadeResolver().resolve(row)
        assert state["calls"] >= 2  # 百度失败后才走 media-db
        assert cand["source"] == "media-db.example.com"
        assert "剧情简介" in cand["synopsis"]