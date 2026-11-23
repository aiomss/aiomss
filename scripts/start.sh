#!/usr/bin/env bash
# AI 运维控制台 · Linux 启动脚本
set -e
cd "$(dirname "$0")/.."
PY="${PYTHON_BIN:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[ERROR] Python not found: $PY" >&2
    echo "Install Python 3.10+ or set PYTHON_BIN=/path/to/python" >&2
    exit 1
fi
echo "============================================================"
echo "  AI 运维控制台 启动中..."
echo "  浏览器打开: http://127.0.0.1:8848"
echo "============================================================"
exec "$PY" -m backend.app
