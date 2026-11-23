# -*- coding: utf-8 -*-
"""修改控制台登录密码（密码只以 PBKDF2 散列写入本机配置，明文不落盘）。

用法：
    python scripts/set_password.py                 # 交互式输入两次
    python scripts/set_password.py 你的新密码      # 直接传参
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import auth  # noqa: E402


def main():
    if len(sys.argv) > 1:
        pwd = sys.argv[1]
    else:
        pwd = getpass.getpass("请输入新密码（至少 6 位）: ")
        again = getpass.getpass("请再输入一次: ")
        if pwd != again:
            print("两次输入不一致，未修改。")
            return 1
    try:
        user = auth.set_password(pwd)
    except ValueError as e:
        print("修改失败：%s" % e)
        return 1
    print("已修改账号「%s」的登录密码。" % user)
    print("所有已登录会话已失效，请用新密码重新登录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
