"""服务端 ffmpeg 预抽帧（app/frames.py）回归测试。

用 imageio-ffmpeg 生成合成测试视频（纯色），验证抽帧/黑帧判定/回退/落库写回。
无 ffmpeg 环境自动跳过（与 test_frame_stats_js.py 的 Node 检测一致）。
"""
import os
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config as config_mod  # noqa: E402
from app import db  # noqa: E402
from app.services import frames  # noqa: E402


def _ffmpeg():
    exe = frames.ffmpeg_exe()
    if not exe or not os.path.exists(exe):
        pytest.skip("无 ffmpeg，跳过抽帧测试")
    return exe


def _make_video(exe, path, color="red", seconds=6):
    subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i",
         f"color=c={color}:s=320x180:d={seconds}:r=10",
         "-c:v", "mpeg4", "-q:v", "5", path],
        capture_output=True, timeout=90)
    assert os.path.exists(path)


# ---------------- ffmpeg 来源 ----------------
def test_ffmpeg_exe_fallback():
    exe = frames.ffmpeg_exe({"ffmpeg_path": ""})
    assert exe and os.path.exists(exe)


def test_ffmpeg_exe_config_override(tmp_path):
    fake = tmp_path / "fake_ffmpeg.exe"
    fake.write_bytes(b"x")
    exe = frames.ffmpeg_exe({"ffmpeg_path": str(fake)})
    assert exe == str(fake)


# ---------------- 抽帧 ----------------
def test_extract_frame_creates_scaled_jpg(tmp_path):
    exe = _ffmpeg()
    video = tmp_path / "test.mp4"
    _make_video(exe, str(video), color="red", seconds=6)
    out = tmp_path / "out.jpg"
    assert frames.extract_frame(str(video), 2.0, str(out)) is True
    assert out.exists() and out.stat().st_size > 0
    from PIL import Image
    assert Image.open(out).width == frames.FRAME_WIDTH


def test_is_black_image(tmp_path):
    from PIL import Image
    black = tmp_path / "black.png"
    Image.new("L", (100, 100), 0).save(black)
    assert frames.is_black_image(str(black)) is True

    nb = tmp_path / "nb.png"
    img = Image.new("L", (100, 100), 120)
    px = img.load()
    for x in range(30, 70):
        for y in range(30, 70):
            px[x, y] = 255
    img.save(nb)
    assert frames.is_black_image(str(nb)) is False


def test_pick_bright_frame_picks_non_black(tmp_path):
    exe = _ffmpeg()
    red = tmp_path / "red.mp4"
    _make_video(exe, str(red), color="red", seconds=6)
    out = tmp_path / "picked.jpg"
    assert frames.pick_bright_frame(str(red), str(out)) is True
    assert not frames.is_black_image(str(out))


def test_pick_bright_frame_falls_back_on_black(tmp_path):
    """全黑视频：首帧黑 → 回退 50% 也黑 → 仍返回存在（至少不是空）。"""
    exe = _ffmpeg()
    black = tmp_path / "black.mp4"
    _make_video(exe, str(black), color="black", seconds=6)
    out = tmp_path / "black_picked.jpg"
    assert frames.pick_bright_frame(str(black), str(out)) is True
    assert os.path.exists(out)


# ---------------- 落库写回 ----------------
def test_run_frame_backfill_writes_poster(tmp_path, monkeypatch):
    exe = _ffmpeg()
    video = tmp_path / "vid.mp4"
    _make_video(exe, str(video), color="blue", seconds=6)

    cfg = {
        "db_path": str(tmp_path / "t.db"),
        "frames_dir": str(tmp_path / "frames"),
        "ffmpeg_path": "",
        "roots": [], "category_filter": ["视频"], "default_user_id": 1,
        "log_dir": str(tmp_path / "logs"), "log_level": "INFO",
    }
    monkeypatch.setattr(config_mod, "load", lambda: dict(cfg))

    con = db.connect()
    db.init(con)
    con.execute(
        "INSERT INTO media(id, category, title, file_path, poster_path, meta, edited_fields) "
        "VALUES(?,?,?,?,?,?,?)",
        (1, "视频", "测试", str(video), "", '{"cover_mode":"video_frame"}', "[]"))
    con.commit()
    con.close()

    class _J:
        stop = threading.Event()
        def tick(self, done, total=None, current=None):
            pass

    r = frames.run_frame_backfill(_J(), ids=[1])
    assert r["extracted"] == 1 and r["failed"] == 0

    con = db.connect()
    row = con.execute("SELECT poster_path, meta FROM media WHERE id=1").fetchone()
    con.close()
    assert row["poster_path"] and os.path.exists(row["poster_path"])
    assert "video_frame" not in (row["meta"] or "")


def test_run_frame_backfill_skips_existing_poster(tmp_path, monkeypatch):
    """已有实体封面的作品应被跳过（幂等，不重复抽帧）。"""
    exe = _ffmpeg()
    video = tmp_path / "vid2.mp4"
    _make_video(exe, str(video), color="green", seconds=6)
    poster = tmp_path / "existing.jpg"
    poster.write_bytes(b"fake")

    cfg = {
        "db_path": str(tmp_path / "t2.db"),
        "frames_dir": str(tmp_path / "frames2"),
        "ffmpeg_path": "", "roots": [], "category_filter": ["视频"], "default_user_id": 1,
        "log_dir": str(tmp_path / "logs2"), "log_level": "INFO",
    }
    monkeypatch.setattr(config_mod, "load", lambda: dict(cfg))

    con = db.connect()
    db.init(con)
    con.execute(
        "INSERT INTO media(id, category, title, file_path, poster_path, meta, edited_fields) "
        "VALUES(?,?,?,?,?,?,?)",
        (1, "视频", "测试", str(video), str(poster), '{"cover_mode":"video_frame"}', "[]"))
    con.commit()
    con.close()

    class _J:
        stop = threading.Event()
        def tick(self, done, total=None, current=None):
            pass

    r = frames.run_frame_backfill(_J(), ids=[1])
    assert r["extracted"] == 0 and r["skipped"] == 1
