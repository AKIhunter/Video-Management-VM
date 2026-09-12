#!/usr/bin/env bash
# 视频管理器 · 一键启动（macOS / Linux，Git Bash 亦可）
#
# 用法：
#   chmod +x run.sh && ./run.sh
#   PYTHON=/usr/bin/python3.11 ./run.sh
#
# 解释器查找顺序（与 run.ps1 一致）：
#   1) $PYTHON 环境变量  2) 项目根 .python-path（一行路径）
#   3) .venv/bin/python  4) PATH 中的 python3 / python
#   全都缺依赖时会尝试 pip install -r requirements.txt。
set -e
cd "$(dirname "$0")"

have_deps() {
    [ -n "$1" ] || return 1
    command -v "$1" >/dev/null 2>&1 || [ -x "$1" ] || return 1
    "$1" -c "import fastapi, uvicorn" >/dev/null 2>&1
}

pick_python() {
    local cand=""
    if [ -n "${PYTHON:-}" ]; then cand="$PYTHON"; fi
    if [ -z "$cand" ] && [ -f ".python-path" ]; then
        cand="$(tr -d '\r\n' < .python-path)"
    fi
    if [ -n "$cand" ] && have_deps "$cand"; then echo "$cand"; return 0; fi

    if [ -x ".venv/bin/python" ] && have_deps ".venv/bin/python"; then
        echo ".venv/bin/python"; return 0
    fi
    for c in python3 python; do
        if have_deps "$c"; then echo "$c"; return 0; fi
    done
    # 没找到装了依赖的：退回可用的解释器，稍后装依赖
    if [ -n "$cand" ]; then echo "$cand"; return 0; fi
    if command -v python3 >/dev/null 2>&1; then echo "python3"; return 0; fi
    if command -v python  >/dev/null 2>&1; then echo "python";  return 0; fi
    return 1
}

echo "=========================================="
echo "  视频管理器 · 一键启动"
echo "=========================================="

echo "[0/4] 选择 Python 解释器..."
PY="$(pick_python)" || {
    echo "[×] 未找到 python。请安装 Python 3.11+，或用 PYTHON=/path/to/python 指定。" >&2
    exit 1
}
echo "      使用： $PY"

echo "[1/4] 检查依赖..."
if ! have_deps "$PY"; then
    echo "      缺少依赖，正在安装 requirements.txt ..."
    "$PY" -m pip install -r requirements.txt
    if ! have_deps "$PY"; then
        echo "[×] 依赖安装失败，请检查网络或 pip 源。" >&2
        exit 1
    fi
fi

echo "[2/4] 检查配置..."
if [ ! -f config.json ]; then
    cp config.example.json config.json
    echo "      已由模板生成 config.json —— 请按需修改其中的 roots（媒体根目录）！"
fi

echo "[3/4] 初始化数据库..."
"$PY" -m app.cli init

echo "[4/4] 启动服务： http://127.0.0.1:8080    (Ctrl+C 退出)"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8080
