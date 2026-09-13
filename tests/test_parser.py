import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import parser  # noqa: E402


def test_modern_filename():
    r = parser.parse_video_filename("[260522][Sample Studio]示例作品 後編[七人组].chs.mp4")
    assert r["studio"] == "Sample Studio"
    assert r["title"] == "示例作品 後編"
    assert r["publish_date"] == "2026-05-22"
    assert r["year"] == 2026
    assert r["subtitle"] == 1


def test_name_no_date():
    r = parser.parse_video_filename("[示例字幕组][StudioX]診療医 テスト療・花子＆美咲～危機いっぱい.mkv")
    assert r["studio"] == "示例字幕组"
    assert "テスト療" in r["title"]
    assert r["publish_date"] is None
    assert r["subtitle"] == 0


def test_old_encoding_bracket_removed():
    r = parser.parse_video_filename("[Sample.sub] [サンプルジェーン]テストコトイイコト 第2話 そうだ練習しよう[720P x264 Hi10P AAC].mp4")
    assert r["studio"] == "Sample.sub"
    assert "720P" not in r["title"]
    assert "テストコトイイコト" in r["title"]


def test_score_star_text():
    # 库内量纲 0~10（原文星级 ×2）
    assert parser.normalize_score("4星半") == 9.0
    assert parser.normalize_score("4星") == 8.0
    assert parser.normalize_score("3.5") == 7.0


def test_score_star_symbols():
    assert parser.normalize_score("★★★★★") == 10.0
    assert parser.normalize_score("★★★★☆") == 9.0
    assert parser.normalize_score("★★☆") == 5.0
    assert parser.normalize_score("★") == 2.0


def test_score_is_capped_at_ten():
    # 原文出现超过 5 星的数字（如「7」）也不能越过库内上限
    assert parser.normalize_score("★★★★★★★") == 10.0
    assert parser.normalize_score("7") == 10.0


def test_score_arrow_takes_final():
    assert parser.normalize_score("★★★★→★★★☆") == 7.0


def test_score_paren_note_takes_first():
    # 采用第一个星组（对画风扣星后的实际分 ★★ → 4 分）
    assert parser.normalize_score("★★（如果未对画风产生排斥反应则★★★）") == 4.0


def test_score_dual_objective():
    e = parser._finalize("タイトル", "实用度：0（纯主观）3.5（较客观）")
    assert e["score"] == 7.0


def test_normalize_title():
    assert parser.normalize_title("《示例作品 後編》") == "示例作品後編"
    assert parser.normalize_title("ABC-DEF") == "abcdef"


def test_jianping_parse():
    text = "《テストコトイイコト》一作。推荐度：4星半。\n《別の作品》二作。实用度：★★☆。\n"
    out = parser.parse_jianping(text)
    title_to_score = {o["title"]: o["score"] for o in out}
    assert title_to_score["テストコトイイコト"] == 9.0
    assert title_to_score["別の作品"] == 5.0