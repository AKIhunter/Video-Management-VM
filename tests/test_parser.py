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
    assert parser.normalize_score("4星半") == 4.5
    assert parser.normalize_score("4星") == 4.0
    assert parser.normalize_score("3.5") == 3.5


def test_score_star_symbols():
    assert parser.normalize_score("★★★★★") == 5.0
    assert parser.normalize_score("★★★★☆") == 4.5
    assert parser.normalize_score("★★☆") == 2.5
    assert parser.normalize_score("★") == 1.0


def test_score_arrow_takes_final():
    assert parser.normalize_score("★★★★→★★★☆") == 3.5


def test_score_paren_note_takes_first():
    # 采用第一个星组（画风扣星后的实际分 ★★）
    assert parser.normalize_score("★★（如果未对画风产生排斥反应则★★★）") == 2.0


def test_score_dual_objective():
    e = parser._finalize("タイトル", "实用度：0（纯主观）3.5（较客观）")
    assert e["score"] == 3.5


def test_normalize_title():
    assert parser.normalize_title("《示例作品 後編》") == "示例作品後編"
    assert parser.normalize_title("ABC-DEF") == "abcdef"


def test_jianping_parse():
    text = "《テストコトイイコト》一作。推荐度：4星半。\n《別の作品》二作。实用度：★★☆。\n"
    out = parser.parse_jianping(text)
    title_to_score = {o["title"]: o["score"] for o in out}
    assert title_to_score["テストコトイイコト"] == 4.5
    assert title_to_score["別の作品"] == 2.5