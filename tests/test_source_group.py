# -*- coding: utf-8 -*-
"""「来源组」规则（scanner.source_group）单测：上级/本级；上级为数据盘根时仅记本级。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.scanner import source_group


def test_source_group_basic():
    assert source_group(r"D:\MediaLibrary\real_video\real_video_202503\a.mp4") == \
        "real_video/real_video_202503"
    assert source_group(r"D:\MediaLibrary\故事合集\DEMO-001\b.mp4") == \
        "故事合集/DEMO-001"


def test_source_group_under_disk_root():
    """上级就是数据盘根 → 仅记本级文件夹名。"""
    assert source_group(r"D:\MediaLibrary\某分类\c.mp4") == "某分类"
    assert source_group(r"D:\MediaLibrary\real_video\d.mp4") == "real_video"


def test_source_group_forward_slash_and_empty():
    assert source_group("D:/MediaLibrary/real_video/real_video_202503/e.mp4") == \
        "real_video/real_video_202503"
    assert source_group("") == ""
    assert source_group("e.mp4") in ("", "e.mp4")  # 无目录信息时不抛异常
