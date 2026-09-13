"""视频帧封面「全黑判定 + 画面评分」启发式的回归测试。

做法：从 webui/app.js 里抽出 frameStats() 源码，在 Node 中用合成像素跑断言，
确保「全黑/接近全黑」会被识别（从而触发抽样换帧），且「有画面」的帧得分更高。
未安装 Node 时自动跳过。
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, "webui", "app.js")

_NODE_CANDIDATES = [
    shutil.which("node"),
    r"C:\Program Files\nodejs\node.exe",
    r"C:\Program Files\nodejs\node.exe",
]

_JS_ASSERT = r"""
function mk(w, h, f) {
  const d = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4, c = f(x, y);
    d[i] = c[0]; d[i+1] = c[1]; d[i+2] = c[2]; d[i+3] = 255;
  }
  return d;
}
const W = 160, H = 90;
const black = frameStats(mk(W, H, () => [0, 0, 0]), W, H);
const nearBlack = frameStats(mk(W, H, () => [4, 4, 6]), W, H);
const flat = frameStats(mk(W, H, () => [120, 120, 120]), W, H);
const scene = frameStats(mk(W, H, (x, y) => {
  const inFace = x > W * 0.35 && x < W * 0.65 && y > H * 0.2 && y < H * 0.8;
  return inFace ? [210, 170, 150] : [25, 22, 30];
}), W, H);
const busy = frameStats(mk(W, H, (x, y) => {
  const t = ((x * 7 + y * 13) % 97) / 97;
  return [40 + t * 190, 60 + ((x * 3) % 80), 90 + ((y * 5) % 120)];
}), W, H);
const ok = black.isBlack && nearBlack.isBlack
  && !scene.isBlack && !busy.isBlack
  && scene.score > black.score * 3
  && scene.score > flat.score
  && busy.score > flat.score;
console.log(ok ? "OK" : "NG");
console.log(JSON.stringify({
  black: black.isBlack, nearBlack: nearBlack.isBlack,
  scene: { isBlack: scene.isBlack, score: Number(scene.score.toFixed(3)) },
  flatScore: Number(flat.score.toFixed(3)), busyScore: Number(busy.score.toFixed(3)),
}));
"""


_PICK_JS = r"""
const a = pickBestTime([{ t: 3, score: 0.9, isBlack: true }, { t: 9, score: 0.2, isBlack: false }]);
const b = pickBestTime([{ t: 3, score: 0.3, isBlack: false }, { t: 9, score: 1.2, isBlack: false }]);
const c = pickBestTime([{ t: 3, score: 0.1, isBlack: true }, { t: 9, score: 0.4, isBlack: true }]);
const d = pickBestTime([null, { t: 9, score: 0.1, isBlack: true }]);
const e = pickBestTime([]);
const ok = a.t === 9 && !a.isBlack              // 非黑优先（即使分数更低）
  && b.t === 9 && b.score === 1.2               // 同为非黑：分数高者胜
  && c.t === 9 && c.isBlack                     // 全黑时：取评分最高者
  && d.t === 9 && e === null;
console.log(ok ? "OK" : "NG");
console.log(JSON.stringify({ a: a, b: b, c: c, d: d, e: e }));
"""


_STASH_JS = r"""
const _painted = [];
globalThis.app = { state: {} };
globalThis.document = {
  querySelectorAll: () => ({ forEach: (fn) => _painted.forEach(el => fn(el)) }),
};
const el = { src: "", dataset: {} };
_painted.push(el);

// ① 详情页(detail)先写入后，网格(grid)不得覆盖
stashFrame(9, "detail-good", 0.9, false, "detail");
const r1 = stashFrame(9, "grid-black", 0.0, true, "grid");
// ② 网格先写黑帧，详情页随后可以替换
stashFrame(8, "grid-black", 0.0, true, "grid");
const r2 = stashFrame(8, "detail-good", 0.2, false, "detail");
// ③ 同为网格：非黑优先（分数低也替换）
stashFrame(7, "grid-black", 0.9, true, "grid");
const r3 = stashFrame(7, "grid-good", 0.1, false, "grid");
// ④ 同为详情页非黑：分数高者胜
stashFrame(6, "d-low", 0.2, false, "detail");
const r4 = stashFrame(6, "d-high", 0.8, false, "detail");

const ok = r1 === "detail-good" && app.state._frames[9] === "detail-good" && app.state._src[9] === "detail"
  && r2 === "detail-good" && app.state._frames[8] === "detail-good"
  && r3 === "grid-good" && app.state._frames[7] === "grid-good" && app.state._blacks[7] === false
  && r4 === "d-high" && app.state._frames[6] === "d-high"
  && el.src === "d-high";     // 每次替换都会同步刷新页面上的元素
console.log(ok ? "OK" : "NG");
console.log(JSON.stringify({ r1: r1, r2: r2, r3: r3, r4: r4, src: app.state._src }));
"""


def _node():
    for p in _NODE_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def _extract(src, name):
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def test_frame_stats_heuristic(tmp_path):
    node = _node()
    if not node:
        pytest.skip("未找到 Node，跳过 JS 启发式测试")
    src = open(APP_JS, encoding="utf-8").read()
    js = _extract(src, "frameStats") + _JS_ASSERT
    f = tmp_path / "frame_stats_test.mjs"
    f.write_text(js, encoding="utf-8")
    out = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines and lines[0] == "OK", out.stdout + out.stderr


def test_pick_best_time_prefers_non_black(tmp_path):
    """选帧规则：非黑优先，其次比画面评分（保证不会把黑帧当成封面）。"""
    node = _node()
    if not node:
        pytest.skip("未找到 Node，跳过 JS 逻辑测试")
    src = open(APP_JS, encoding="utf-8").read()
    js = _extract(src, "pickBestTime") + _PICK_JS
    f = tmp_path / "pick_time_test.mjs"
    f.write_text(js, encoding="utf-8")
    out = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines and lines[0] == "OK", out.stdout + out.stderr


def test_stash_frame_source_rules(tmp_path):
    """缓存来源规则：网格结果永不覆盖详情页结果；非黑优先；同类比评分。"""
    node = _node()
    if not node:
        pytest.skip("未找到 Node，跳过 JS 逻辑测试")
    src = open(APP_JS, encoding="utf-8").read()
    js = _extract(src, "stashFrame") + _STASH_JS
    f = tmp_path / "stash_test.mjs"
    f.write_text(js, encoding="utf-8")
    out = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines and lines[0] == "OK", out.stdout + out.stderr
