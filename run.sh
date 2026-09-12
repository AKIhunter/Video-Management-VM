#!/usr/bin/env bash
# 视频管理器 · 一键启动（macOS / Linux，Git Bash 亦可）
# 用法：chmod +x run.sh && ./run.sh        （可用 PYTHON=python3.11 ./run.sh 指定解释器）
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

echo "=========================================="
echo "  视频管理器 · 一键启动"
echo "=========================================="

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[×] 未找到 $PY，请安装 Python 3.11+ 或设置 PYTHON 环境变量。" >&2
    exit 1
fi

echo "[1/4] 检查依赖..."
if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    echo "      缺少依赖，正在安装 requirements.txt ..."
    "$PY" -m pip install -r requirements.txt
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
