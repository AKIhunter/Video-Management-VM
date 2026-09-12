"""内容标签词典与打标：示例词条下的匹配与截断行为。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import tagdict  # noqa: E402


def test_pick_tags_matches_example_entries():
    tags = tagdict.pick_tags(["这是一部奇幻冒险的校园恋爱作品"])
    assert "奇幻" in tags and "校园" in tags and "恋爱" in tags


def test_pick_tags_respects_limit_and_dedup():
    text = "动作 喜剧 奇幻 科幻 悬疑 日常 竞技 音乐 校园 恋爱 动作"
    tags = tagdict.pick_tags([text], limit=5)
    assert len(tags) == 5
    assert len(set(tags)) == 5


def test_filter_tags_keeps_only_dictionary_hits():
    out = tagdict.filter_tags(["喜剧", "随便写的东西", "科幻"], limit=10)
    assert out == ["喜剧", "科幻"]
    assert all(t in [tag for _p, tag in tagdict.TAG_ENTRIES] for t in out)


def test_empty_input():
    assert tagdict.pick_tags([]) == []
    assert tagdict.filter_tags([]) == []
