import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import kinks  # noqa: E402


def test_pick_tags_caps_at_tag_limit():
    text = "本作战斗激烈，喜剧桥段多，含科幻设定与悬疑氛围，还有校园日常、恋爱与竞技、音乐元素"
    tags = kinks.pick_tags([text])
    assert len(tags) <= kinks.TAG_LIMIT
    assert tags[0] == "动作"          # 词典顺序优先
    assert set(tags) <= {"动作", "喜剧", "科幻", "悬疑", "校园", "日常", "恋爱", "竞技", "音乐"}


def test_pick_tags_dedup():
    tags = kinks.pick_tags(["战斗 战斗 战斗", "喜剧"], limit=10)
    assert tags == ["动作", "喜剧"]


def test_pick_tags_no_hit():
    assert kinks.pick_tags(["这是一段普通剧情描述"]) == []


def test_filter_kink_keeps_only_dictionary():
    out = kinks.filter_kink(["动作", "ノーマル", "恋爱", "无厘头"], limit=10)
    assert "动作" in out and "恋爱" in out
    assert "ノーマル" not in out and "无厘头" not in out


def test_filter_kink_keeps_all_hits_under_tag_limit():
    out = kinks.filter_kink(["动作", "喜剧", "奇幻", "科幻", "悬疑"])
    assert len(out) == 5  # 默认上限 TAG_LIMIT=10，5 个全部命中词典
