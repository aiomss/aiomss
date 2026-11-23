# -*- coding: utf-8 -*-
"""HTTP 端到端自检（进程内 ASGI TestClient，无需先启动服务）。

覆盖：登录闸门、图片验证码、会话 Cookie、脱敏、问答 SSE 事件结构、登出。
不连接真实 Linux 主机、不调用计费 AI 接口（问答流用本地假客户端）。

用法（项目根目录）：python scripts/httpcheck.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient       # noqa: E402
from backend import agent, auth                # noqa: E402
from backend.app import app                    # noqa: E402

PASS, FAIL = 0, 0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  -> " + str(extra)[:200] if extra else ""))


def parse_sse(text):
    out = []
    for block in text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                try:
                    out.append(json.loads(line[5:].strip()))
                except Exception:
                    pass
    return out


def fresh_captcha(client, cid):
    r = client.get("/api/auth/captcha", params={"cid": cid})
    return r, auth._captchas[cid]["code"]


c = TestClient(app, follow_redirects=False)

print("=== 1. 未登录闸门 ===")
r = c.get("/")
check("访问首页跳登录页", r.status_code in (302, 307)
      and "/login" in r.headers.get("location", ""), (r.status_code, r.headers.get("location")))
r = c.get("/api/state")
check("API 返回 401", r.status_code == 401, r.status_code)
r = c.get("/static/index.html")
check("静态资源也被拦截", r.status_code in (302, 401), r.status_code)
r = c.get("/login")
check("登录页可访问且含验证码控件", r.status_code == 200
      and "cap-img" in r.text and "验证码" in r.text, r.status_code)
r = c.get("/api/health")
check("健康检查在白名单", r.status_code == 200 and r.json().get("ok"), r.status_code)
r = c.get("/favicon.ico")
check("favicon 不触发重定向", r.status_code == 204, r.status_code)

print("=== 2. 图片验证码与登录 ===")
r, _ = fresh_captcha(c, "a" * 32)
check("验证码返回真实 PNG", r.status_code == 200 and r.content[:8] == PNG_MAGIC
      and r.headers.get("content-type", "").startswith("image/png"), len(r.content))
check("验证码响应禁止缓存", "no-store" in r.headers.get("cache-control", ""),
      r.headers.get("cache-control"))

r = c.post("/api/auth/login", json={"username": "admin", "password": "13660888023",
                                    "captcha": "!!!!", "cid": "a" * 32})
check("错误验证码被拒绝", r.status_code == 400 and not r.json().get("ok"), r.status_code)

_, code2 = fresh_captcha(c, "b" * 32)
r = c.post("/api/auth/login", json={"username": "admin", "password": "wrong-pwd",
                                    "captcha": code2, "cid": "b" * 32})
check("错误密码被拒绝", r.status_code == 401 and not r.json().get("ok"), r.status_code)

_, code3 = fresh_captcha(c, "c" * 32)
r = c.post("/api/auth/login", json={"username": "admin", "password": "13660888023",
                                    "captcha": code3, "cid": "c" * 32})
check("正确凭据登录成功", r.status_code == 200 and r.json().get("ok"), r.text[:150])
set_cookie = r.headers.get("set-cookie", "")
check("Cookie 为 HttpOnly + SameSite", "httponly" in set_cookie.lower()
      and "samesite" in set_cookie.lower(), set_cookie[:120])
token = r.cookies.get("aoc_session") or set_cookie.split("aoc_session=")[-1].split(";")[0]
check("会话 token 已签发", bool(token) and auth.check_session(token) is not None)

print("=== 3. 登录后接口 ===")
COOKIES = {"aoc_session": token}
r = c.get("/", cookies=COOKIES)
check("首页可访问且含审批浮层/退出按钮", r.status_code == 200
      and "confirmDock" in r.text and "btnLogout" in r.text, r.status_code)
r = c.get("/api/state", cookies=COOKIES)
check("状态接口可用", r.status_code == 200 and r.json().get("ok"), r.status_code)
r = c.get("/api/auth/me", cookies=COOKIES)
check("当前用户为 admin", r.json().get("username") == "admin", r.text)
r = c.get("/static/assets/app.js", cookies=COOKIES)
check("静态资源可访问", r.status_code == 200 and "showConfirmDock" in r.text, r.status_code)
st = c.get("/api/state", cookies=COOKIES).json()
profiles = st.get("ai_profiles", [])
check("API Key 不下发明文，仅返回脱敏值",
      all(p.get("api_key") is None and "****" in (p.get("api_key_masked") or "")
          for p in profiles), profiles)

print("=== 4. 命令问答 SSE 流（本地假 AI） ===")
_orig_ai = agent.AIClient


class FakeAI:
    def __init__(self, *a, **k):
        pass

    def chat_stream(self, messages, max_tokens=3000):
        yield "vim 的 -R 参数"
        yield "表示以只读方式打开文件。"


agent.AIClient = FakeAI
r = c.post("/api/agent/ask", cookies=COOKIES,
           json={"prompt": "vim -R 怎么用", "model": "fake"})
evts = parse_sse(r.text)
types = [e.get("type") for e in evts]
check("问答接口返回 SSE", r.status_code == 200
      and "text/event-stream" in r.headers.get("content-type", ""), r.status_code)
check("首个事件是即时反馈 start", bool(types) and types[0] == "start", types[:4])
check("包含 answer_start / answer_delta", "answer_start" in types
      and "answer_delta" in types, types)
check("答案内容完整", any("只读方式" in e.get("text", "") for e in evts), evts)
check("以 done 正常收尾", bool(types) and types[-1] == "done", types[-3:])
agent.AIClient = _orig_ai

print("=== 5. 审批端点与登出 ===")
r = c.post("/api/agent/confirm", cookies=COOKIES,
           json={"confirm_id": "not-exist", "approved": True})
check("审批端点可调用", r.status_code == 200 and r.json().get("ok") is False, r.text)
r = c.post("/api/auth/logout", cookies=COOKIES)
check("登出成功", r.status_code == 200 and r.json().get("ok"), r.status_code)
r = c.get("/api/state", cookies=COOKIES)
check("登出后 API 重新被拦", r.status_code == 401, r.status_code)

print("=== 6. v1.2 前端契约（静态资源与文案） ===")
S6_PASS, S6_FAIL = PASS, FAIL
TOKEN2 = auth.issue_session("admin")
CK2 = {"aoc_session": TOKEN2}

r = c.get("/static/assets/render.js", cookies=CK2)
check("render.js 可访问且导出 cleanLines/renderRichText",
      r.status_code == 200 and "cleanLines" in r.text
      and "renderRichText" in r.text, r.status_code)

r = c.get("/", cookies=CK2)
home = r.text
check("首页含「API修改」按钮", r.status_code == 200 and "API修改" in home, r.status_code)
check("设置弹窗标题为「API修改 · AI 接入配置」", "API修改 · AI 接入配置" in home)
check("首页引入 render.js", "/static/assets/render.js" in home)
check("首页不含终端提示「Tab 补全 · vim / top 可用 · Ctrl+C 中断」",
      "Tab 补全 · vim / top 可用 · Ctrl+C 中断" not in home)
check("首页含「⚙ 设置」入口（会话保活 / 界面配色）",
      'id="btnPrefs"' in home and "⚙ 设置" in home)

r = c.get("/static/assets/app.js", cookies=CK2)
appjs = r.text
check("app.js 含 host-connect 连接按钮",
      r.status_code == 200 and "host-connect" in appjs, r.status_code)
check("app.js 含 disconnectHost", "disconnectHost" in appjs)
check("app.js 含 hideRunMeta", "hideRunMeta" in appjs)
check("app.js 含 answer_done 收尾", "answer_done" in appjs)
check("app.js 含「点击左侧该主机的「连接」按钮」提示",
      "点击左侧该主机的「连接」按钮" in appjs)
init_pos = appjs.find("(async function init()")
init_tail = appjs[init_pos:] if init_pos >= 0 else ""
check("init() 起至文件尾不含 openTerminal( （登录不自动连接）",
      init_pos >= 0 and "openTerminal(" not in init_tail,
      "init_pos=%d openTerminal=%s" % (init_pos, "openTerminal(" in init_tail))
check("app.js 含「修改」主机按钮 host-edit", "host-edit" in appjs)
check("已移除最大重试控件（app.js 无 numRetry）", "numRetry" not in appjs)
check("app.js 终端选中即复制（onSelectionChange 写剪贴板）",
      "onSelectionChange" in appjs and "clipboard.writeText" in appjs)
check("app.js 终端字号已增大（fontSize: 15）", "fontSize: 15" in appjs)

r = c.get("/static/assets/style.css", cookies=CK2)
css = r.text
check("style.css 含 .host-connect",
      r.status_code == 200 and ".host-connect" in css, r.status_code)
check("style.css 含 .sm-pre", ".sm-pre" in css)
check("style.css 含 .sm-trunc", ".sm-trunc" in css)
print("小节结果: %d 通过 / %d 失败" % (PASS - S6_PASS, FAIL - S6_FAIL))

print("=== 7. Agent SSE 契约（本地假 AI，零真实调用） ===")
S7_PASS, S7_FAIL = PASS, FAIL


class FakeContractAI:
    """command 恒为空 / done=False：验证跑满步数的「阶段结论」；不联网。"""

    def __init__(self, *a, **k):
        self.truncated = False

    def chat_stream(self, messages, max_tokens=6000):
        yield "vim -R 表示以只读方式打开文件。"
        yield "常用组合：vim -R +100 /var/log/app.log。"

    def chat_json(self, messages, max_tokens=4096):
        return ({"thought": "继续收集证据", "command": "", "done": False,
                 "summary": "【现象】测试环境中的诊断流程运行正常。"
                            "【证据】已收集到完整的日志与状态信息。"
                            "【建议】请把问题拆得更具体后重新发起排查。"},
                "", "{}")

    def chat_text(self, messages, max_tokens=2048):
        return ("【建议】以上为完整结论，请按需复核。", "")


_o_ai = agent.AIClient
_o_gp = agent.store.get_profile
_o_gh = agent.store.get_host
agent.AIClient = FakeContractAI
agent.store.get_profile = lambda *_a, **_k: {
    "api_key": "fake-for-test", "base_url": "http://127.0.0.1",
    "default_model": "fake"}
agent.store.get_host = lambda *_a, **_k: {
    "name": "fake-host", "host": "127.0.0.1", "username": "tester", "port": 22}
try:
    r = c.post("/api/agent/ask", cookies=CK2,
               json={"prompt": "vim -R 怎么用", "model": "fake"})
    evts = parse_sse(r.text)
    types = [e.get("type") for e in evts]

    def first_of(name):
        return types.index(name) if name in types else -1

    order_ok = (0 <= first_of("answer_start") < first_of("answer_delta")
                < first_of("answer_done") < first_of("done"))
    check("问答 SSE 序列 answer_start→answer_delta→answer_done→done",
          r.status_code == 200 and order_ok, types)
    bad_titles = [e.get("title", "") for e in evts
                  if e.get("type") == "notice"
                  and "自动修复" in str(e.get("title", ""))]
    check("问答流不向用户弹「自动修复」提示", not bad_titles, bad_titles)
    check("问答流含 answer_done 收尾事件", first_of("answer_done") >= 0, types[-4:])

    r2 = c.post("/api/agent/run", cookies=CK2,
                json={"prompt": "检查磁盘", "host_id": "fake",
                      "profile_id": "fake", "model": "fake",
                      "read_only": True, "auto_approve": False,
                      "max_retry": 1})
    evts2 = parse_sse(r2.text)
    types2 = [e.get("type") for e in evts2]
    sums = [e for e in evts2 if e.get("type") == "summary"]
    stage = next((e for e in sums
                  if "阶段结论" in str(e.get("title", ""))), None)
    check("跑满步数产出「阶段结论」标题", stage is not None,
          [e.get("title") for e in sums])
    stage_text = str((stage or {}).get("text", ""))
    check("阶段结论正文含「汇总现有证据」释义", "汇总现有证据" in stage_text,
          stage_text[:120])
    check("阶段结论不再出现「最大重试」字样", "最大重试" not in stage_text,
          stage_text[:120])
    check("阶段结论流以 done 收尾",
          bool(types2) and types2[-1] == "done", types2[-3:])
finally:
    agent.AIClient = _o_ai
    agent.store.get_profile = _o_gp
    agent.store.get_host = _o_gh
print("小节结果: %d 通过 / %d 失败" % (PASS - S7_PASS, FAIL - S7_FAIL))

print()
print("结果: %d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
