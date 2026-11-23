# -*- coding: utf-8 -*-
"""L5 静态/字节/配置层：启动脚本字节级验收、配置结构、目录归类。

用法（项目根目录）：python scripts/test_static.py
不打印 config/config.json 的任何值，只输出布尔断言结果。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = 0, 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] " + name)
    else:
        FAIL += 1
        FAILURES.append(name)
        print("  [FAIL] " + name + ("  -> " + str(extra)[:160] if extra else ""))


def read_bytes(rel):
    with open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


# ---------------- 1. start.bat 字节级 ----------------
print("=== 1. scripts/start.bat 字节级 ===")
bat_path = "scripts/start.bat"
bat_ok = os.path.isfile(os.path.join(ROOT, bat_path))
check("start.bat 存在", bat_ok)
bat = read_bytes(bat_path) if bat_ok else b""
check("start.bat 无 BOM", not (bat[:3] == b"\xef\xbb\xbf"), bat[:3])
check("start.bat 纯 ASCII（nonAscii=0）",
      all(x <= 127 for x in bat),
      sum(1 for x in bat if x > 127))
bat_text = bat.decode("ascii", "replace")
crlf = bat_text.count("\r\n")
lone_lf = len(re.findall(r"(?<!\r)\n", bat_text))
lone_cr = len(re.findall(r"\r(?!\n)", bat_text))
check("start.bat 全 CRLF（无孤立 LF）", lone_lf == 0, "loneLF=%d" % lone_lf)
check("start.bat 无孤立 CR", lone_cr == 0, "loneCR=%d" % lone_cr)
check("start.bat 以 CRLF 结尾", bat[-2:] == b"\r\n", bat[-4:])
check("start.bat 含 CRLF 行", crlf > 0, "crlf=%d" % crlf)
check("start.bat 含 @echo off", "@echo off" in bat_text)
check("start.bat 含 cd /d 回项目根", 'cd /d "%~dp0.."' in bat_text)
check("start.bat 含 -m backend.app 启动命令", "-m backend.app" in bat_text)
check("start.bat 通过 \"%PY%\" 变量调用托管 Python",
      '"%PY%" -m backend.app' in bat_text)
check("start.bat 含 pause（失败不闪退）", "pause" in bat_text)
check("start.bat 逐行双引号成对",
      all(line.count('"') % 2 == 0 for line in bat_text.split("\r\n")))

# ---------------- 2. start.sh 字节级 ----------------
print("=== 2. scripts/start.sh 字节级 ===")
sh_path = "scripts/start.sh"
sh_ok = os.path.isfile(os.path.join(ROOT, sh_path))
check("start.sh 存在", sh_ok)
sh = read_bytes(sh_path) if sh_ok else b""
check("start.sh 无 BOM", not (sh[:3] == b"\xef\xbb\xbf"), sh[:3])
check("start.sh 无 CR（纯 LF）", b"\r" not in sh, sh.count(b"\r"))
try:
    sh_text = sh.decode("utf-8", "strict")
    utf8_ok = True
except UnicodeDecodeError as e:
    sh_text, utf8_ok = "", False
    print("    decode error: " + str(e))
check("start.sh 是合法 UTF-8", utf8_ok)
check("start.sh 含 python3 存在性检查", 'command -v "$PY"' in sh_text)
check("start.sh 含 PYTHON_BIN 可覆盖", "PYTHON_BIN" in sh_text)
check("start.sh 含 exec \"$PY\" -m backend.app",
      'exec "$PY" -m backend.app' in sh_text)

# ---------------- 3. config/config.json 结构 ----------------
print("=== 3. config/config.json 结构（不输出任何配置值） ===")
cfg_path = os.path.join(ROOT, "config", "config.json")
cfg_exists = os.path.isfile(cfg_path)
check("config.json 存在", cfg_exists)
cfg = {}
if cfg_exists:
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        parse_ok = True
    except Exception as e:
        parse_ok = False
        print("    parse error: " + str(e))
else:
    parse_ok = False
check("config.json 可解析为 JSON", parse_ok)
auth_cfg = cfg.get("auth", {}) if isinstance(cfg.get("auth"), dict) else {}
check("auth 含 password_hash 且非空",
      isinstance(auth_cfg.get("password_hash"), str)
      and len(auth_cfg.get("password_hash", "")) > 0)
check("auth 不含明文 password 字段", "password" not in auth_cfg)
check("hosts 是 list", isinstance(cfg.get("hosts"), list),
      type(cfg.get("hosts")).__name__)
check("ai_profiles 是 list", isinstance(cfg.get("ai_profiles"), list),
      type(cfg.get("ai_profiles")).__name__)
server_cfg = cfg.get("server", {}) if isinstance(cfg.get("server"), dict) else {}
check("server.port == 8848", server_cfg.get("port") == 8848,
      type(server_cfg.get("port")).__name__)

# ---------------- 4. 目录归类 ----------------
print("=== 4. 目录归类与交付物 ===")
for sub in ("backend", "frontend", "config", "docs", "scripts", "assets"):
    check("目录存在: %s/" % sub, os.path.isdir(os.path.join(ROOT, sub)))
for rel in ("requirements.txt", "frontend/index.html",
            "frontend/assets/render.js", "frontend/assets/app.js",
            "frontend/assets/style.css", "scripts/test_render.mjs",
            "scripts/selftest.py", "scripts/httpcheck.py",
            "scripts/start.bat", "scripts/start.sh"):
    check("文件存在: %s" % rel, os.path.isfile(os.path.join(ROOT, rel)))

# ---------------- 5. 六个截图问题的源码静态契约 ----------------
print("=== 5. v1.2 六个截图问题的源码静态契约 ===")


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


try:
    index = src("frontend/index.html")
    appjs = src("frontend/assets/app.js")
    renderjs = src("frontend/assets/render.js")
    stylecss = src("frontend/assets/style.css")
    agentpy = src("backend/agent.py")
    aiclient = src("backend/ai_client.py")
    src_ok = True
except Exception as e:
    index = appjs = renderjs = stylecss = agentpy = aiclient = ""
    src_ok = False
    print("    read error: " + str(e))
check("六问题源码可读取（UTF-8）", src_ok)

# 问题1：登录后取消自动连接
init_pos = appjs.find("(async function init()")
init_tail = appjs[init_pos:] if init_pos >= 0 else ""
check("问题1: app.js init() 起至文件尾不含 openTerminal(",
      init_pos >= 0 and "openTerminal(" not in init_tail,
      "init_pos=%d" % init_pos)
check("问题1: app.js 每台主机渲染 host-connect 且默认「连接」",
      "host-connect" in appjs and "'连接'" in appjs)
check("问题1: app.js 空状态文案含「连接」按钮说明",
      "点击左侧该主机的「连接」按钮" in appjs)

# 问题2：内部自动修复不外泄 / 半句结论修补
notice_titles = re.findall(r'_notice\(\s*"[^"]+"\s*,\s*"([^"]+)"', agentpy)
check("问题2: agent.py 所有 notice 标题均不含「自动修复」",
      not any("自动修复" in t for t in notice_titles), notice_titles)
check("问题2: agent.py 引入 summary_looks_truncated 修补半句结论",
      "summary_looks_truncated" in agentpy)
check("问题2: ai_client.py 提供 complete_summary 续写补全",
      "def complete_summary" in aiclient)

# 问题3：API修改
check("问题3: index.html 顶栏含「API修改」按钮",
      'id="btnSettings"' in index and "API修改" in index)
check("问题3: index.html 设置弹窗标题为「API修改 · AI 接入配置」",
      "API修改 · AI 接入配置" in index)
check("问题3: index.html 含「⚙ 设置」入口（会话保活 / 界面配色）",
      'id="btnPrefs"' in index and "⚙ 设置" in index)

# 问题4：终端 hint 删除
hint = "Tab 补全 · vim / top 可用 · Ctrl+C 中断"
check("问题4: index.html 不含终端 hint", hint not in index)
check("问题4: app.js 不含终端 hint", hint not in appjs)

# 问题5：阶段结论标题与释义
check("问题5: agent.py 含「阶段结论 · 已排查 N 步」标题",
      "阶段结论 · 已排查 %d 步" in agentpy)
check("问题5: agent.py 阶段结论正文含「汇总现有证据」且不再提「最大重试」",
      "汇总现有证据" in agentpy and "最大重试" not in agentpy)
check("问题5: agent.py 不再使用旧的「AI 结论与建议（已达最大步数）」",
      "AI 结论与建议（已达最大步数）" not in agentpy)

# 问题6：问答状态机与富文本
check("问题6: app.js 含 hideRunMeta 与 answer_done 收尾",
      "hideRunMeta" in appjs and "answer_done" in appjs)
check("问题6: render.js 暴露 cleanLines / renderRichText",
      "cleanLines" in renderjs and "renderRichText" in renderjs)
check("问题6: style.css 含 .sm-pre / .sm-trunc",
      ".sm-pre" in stylecss and ".sm-trunc" in stylecss)
check("问题6: index.html 引入 render.js",
      "/static/assets/render.js" in index)

print()
print("结果: %d 通过 / %d 失败" % (PASS, FAIL))
if FAILURES:
    print("失败项: " + " | ".join(FAILURES))
sys.exit(1 if FAIL else 0)
