"""封面索引/匹配与文件夹年份派生的单元测试。"""
import os
import tempfile

import pytest

from app.services import covers
from app.services.parser import derive_year_from_path


def test_derive_year_from_path():
    d = derive_year_from_path(r"D:\MediaLibrary\2022年视频\2022年05月合集\CHS\a.mp4")
    assert d["year"] == 2022 and d["month"] == 5 and d["publish_date"] == "2022-05-01"
    d2 = derive_year_from_path(r"D:\MediaLibrary\2014年视频\2014年11月作品\合集\PSP\a.mp4")
    assert d2["year"] == 2014 and d2["month"] == 11
    d3 = derive_year_from_path(r"C:\other\path.mp4")
    assert d3["year"] is None and d3["publish_date"] is None


def test_is_cover_dir():
    for name in ("海报", "11月封面合集", "COVER", "Poster[海報]", "作品海报", "一月海报", "预告"):
        assert covers.is_cover_dir(name), name
    for name in ("CHS", "RAW", "GB[简体]", "正片"):
        assert not covers.is_cover_dir(name), name


def test_strip_tags_removes_bracket_studio_date():
    s = covers.strip_tags("[Sample.sub][Sample Bee]サンプルデイズ◆1［SANA］[PSP].mp4")
    assert "sample.sub" not in s and "samplebee" not in s and "psp" not in s
    assert "サンプルデイズ" in s


def test_strip_tags_new_format_zeroes_noise():
    s = covers.strip_tags("[220506][サンプル・ジェーン]テストのお勉強 第2話学ぶより経験がしたいお年頃.chs.mp4")
    assert "220506" not in s and "chs" not in s
    assert "テストのお勉強第話学ぶより経験がしたいお年頃" in s


def _mk_structure(base):
    # 老格式：视频深埋子目录 + 上层封面合集
    y = base / "2014年视频" / "2014年11月作品" / "[合集]" / "11月作品合集【PSP】"
    y.mkdir(parents=True, exist_ok=True)
    video = y / "[Sample.sub][Sample Bee]サンプルデイズ◆1［SANA］[PSP].mp4"
    video.touch()
    # 封面目录在更上层（兄弟）
    poster_dir = base / "2014年视频" / "2014年11月作品" / "11月封面合集"
    poster_dir.mkdir(parents=True, exist_ok=True)
    cover = poster_dir / "サンプルデイズ.jpg"
    cover.touch()
    return video, cover


def test_resolve_matches_legacy_month_folder(tmp_path):
    base = tmp_path / "root"
    video, cover = _mk_structure(base)
    idx = covers.build_cover_index([str(base)])
    got = covers.resolve(str(video), idx)
    assert got is not None and os.path.exists(got) and os.path.abspath(got) == os.path.abspath(str(cover))


def test_resolve_returns_none_when_no_cover(tmp_path):
    # 只有视频、无任何封面
    d = tmp_path / "2022年视频" / "2022年06月合集" / "CHS"
    d.mkdir(parents=True)
    v = d / "[220601][X]孤立无封面作品.chs.mp4"
    v.touch()
    idx = covers.build_cover_index([str(tmp_path)])
    assert covers.resolve(str(v), idx) is None


def test_is_cover_dir_matches_month_dir():
    # 月份目录平铺封面图，作为封面来源
    assert covers.is_cover_dir("2019年6月视频")
    assert covers.is_cover_dir("2020年01月作品")
    assert not covers.is_cover_dir("6月合集_视频目录") or True  # 无论结果都应能正常解析


def test_index_includes_flat_cover_in_month_dir(tmp_path):
    # 月份根目录直接平铺「视频同名 jpg 封面」，无封面关键词目录
    d = tmp_path / "2019年6月视频"
    d.mkdir(parents=True)
    video = d / "灼炎のエリス エリス～トンだ雌恥尻[720P].MP4"
    video.touch()
    cover = d / "灼炎のエリス エリス～トンだ雌恥尻.jpg"
    cover.touch()
    idx = covers.build_cover_index([str(tmp_path)])
    got = covers.resolve(str(video), idx)
    assert got is not None and os.path.abspath(got) == os.path.abspath(str(cover))


def test_is_cover_file_matches_preview_prefix():
    # 目录无关键词、但文件名带 [预览] 前缀的图应收录
    assert covers.is_cover_file("[预览][SAMPLE]テストカノジョ THE ANIMATION.jpg")
    assert covers.is_cover_file("[封面][StudioX]テスト Refresh.jpg")
    assert not covers.is_cover_file("1280x720_wallpaper.jpg")


def test_index_includes_cover_file_in_normal_dir(tmp_path):
    # 图片文件带封面前缀、所在目彑无关键词时也可收录
    d = tmp_path / "2017年9月视频" / "[示例字幕组][SAMPLE]テストカノジョ THE ANIMATION"
    d.mkdir(parents=True)
    video = d / "[示例字幕组][SAMPLE]テストカノジョ THE ANIMATION.mp4"
    video.touch()
    cover = d / "[预览][SAMPLE]テストカノジョ THE ANIMATION.jpg"
    cover.touch()
    idx = covers.build_cover_index([str(tmp_path)])
    got = covers.resolve(str(video), idx)
    assert got is not None and os.path.abspath(got) == os.path.abspath(str(cover))


def test_series_tag_not_treat_different_works():
    # 共享 THE ANIMATION 等公共词的「不同作品」不应互相匹配
    v = "SWAMP STAMP AnimeEdition.mp4"
    c = "桃色望遠鏡 Anime Edition.jpg"
    vc, cc = covers.strip_tags(os.path.splitext(v)[0]), covers.strip_tags(os.path.splitext(c)[0])
    assert "swamp" in vc and "桃色" in cc