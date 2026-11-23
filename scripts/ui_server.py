# -*- coding: utf-8 -*-
"""L6 浏览器 UI 测试专用引导（测试进程，不是生产启动脚本）。

与 backend.app 在**同一进程**内签发 admin 会话，并用 uvicorn 在
127.0.0.1:8899 提供 app 对象本身（不是字符串导入路径），这样进程内
内存字典 auth._sessions 才会包含该 token。测试结束即随进程销毁。

用法（项目根目录）：python scripts/ui_server.py
stdout 首行：AOC_TOKEN=<token>
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import auth            # noqa: E402
from backend.app import app         # noqa: E402


def main():
    token = auth.issue_session("admin")
    # 只输出 token 名与值（测试用一次性会话），不输出任何配置/密钥内容
    print("AOC_TOKEN=" + token, flush=True)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8899, log_level="warning")


if __name__ == "__main__":
    main()
