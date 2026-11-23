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
echo "  AI Ops Console - Service Console"
echo "  URL: http://127.0.0.1:8848/login"
echo "  Commands: r=reload  x=stop  b=start  o=open  st=status  q=quit"
echo "============================================================"
exec "$PY" scripts/console.py
