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
check("start.bat 调用交互式控制台 console.py",
      "scripts\\console.py" in bat_text and '"%PY%" scripts\\console.py' in bat_text)
check("start.bat 通过 \"%PY%\" 变量调用项目 venv Python",
      '"%PY%" scripts\\console.py' in bat_text)
check("start.bat 菜单提示含 reload/stop/start/quit 命令",
      all(cmd in bat_text for cmd in ("r  reload", "x  stop", "b  start", "q  quit")))
# 加固版用 timeout /t 10 代替 pause：同样保证失败不闪退，且无需用户按键
check("start.bat 含 pause 或 timeout（失败不闪退）",
      "pause" in bat_text or "timeout /t" in bat_text)
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
check("start.sh 含 exec \"$PY\" scripts/console.py",
      'exec "$PY" scripts/console.py' in sh_text)
check("start.sh 菜单提示含 reload/stop 命令",
      "r=reload" in sh_text and "x=stop" in sh_text)

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
            "scripts/start.bat", "scripts/start.sh",
            "scripts/console.py", "scripts/test_console.py"):
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
check("问题1: app.js 空状态文案指向终端上方连接按钮",
      "点击终端上方的「连接」按钮" in appjs)

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

# ---------------- 本次修复：主机编辑校验 / 应用等级四级 / 刷新反馈 / 时间窗 ----------------
print("=== 主机编辑与时间窗修复 ===")
# 问题1：修改主机密码留空表示不修改，不能强制必填
check("修改主机：新增才要求密码，编辑时密码可留空",
      "if (!EDITING_HOST_ID && !p.password)" in appjs
      and "地址、用户名、密码必填" not in appjs)
check("密码框保留「留空表示不修改原密码」提示",
      "留空表示不修改原密码" in appjs or "留空表示不修改原密码" in index)
# 问题2：应用等级四级下拉
check("应用等级四级下拉（一般/重要/关键/核心）",
      all(x in appjs for x in ("APP_LEVELS", "'一般'", "'重要'", "'关键'", "'核心'"))
      and 'id="hLevel"' in index and "<select id=\"hLevel\"" in index)
# 问题3：刷新按钮有 loading 与可见反馈
check("刷新按钮有刷新中状态与已刷新反馈",
      "⟳ 刷新中…" in appjs and "已刷新" in appjs)
# 问题4：保存主机后在工作台视图自动重绘
check("主机保存后工作台自动重绘",
      "await wbLoad();" in appjs and "workbenchView" in appjs)
# 问题5：时间窗六档 + 实时指标
check("时间窗六档选择器存在于页面",
      all(('value="%s"' % v) in index for v in ("5m", "10m", "15m", "30m", "1h", "custom"))
      and 'id="wbWindow"' in index)
check("后端提供实时采集指标与时间窗接口",
      "def live_metrics" in open(os.path.join(ROOT, "backend", "workbench.py"),
                                 encoding="utf-8").read()
      and "/api/workbench/metrics" in open(os.path.join(ROOT, "backend", "app.py"),
                                          encoding="utf-8").read())
# 本次：资产台账展示已填参数，质量清单文案不再误导
check("后端提供资产台账接口",
      "def assets" in open(os.path.join(ROOT, "backend", "workbench.py"),
                           encoding="utf-8").read()
      and "/api/workbench/assets" in open(os.path.join(ROOT, "backend", "app.py"),
                                          encoding="utf-8").read())
check("前端有资产台账表格与渲染",
      'id="wbAssetRows"' in index and "function wbRenderAssets" in appjs
      and "/api/workbench/assets" in appjs)
check("质量清单 0 项时提示字段已填齐",
      "字段已填齐" in appjs and "0 表示已填齐" in open(
          os.path.join(ROOT, "backend", "workbench.py"), encoding="utf-8").read())

# 本次：资产管理 / 监控告警 / 日志中心 / 紫罗兰态势舱
print("=== 资产 / 监控告警 / 日志中心 / 驾驶舱 ===")
ops_py = open(os.path.join(ROOT, "backend", "ops_center.py"), encoding="utf-8").read()
check("后端存在资产与日志中心模块", "def catalog_meta" in ops_py and "def log_meta" in ops_py)
check("日志采集只做只读 tail 且校验路径", "tail -n" in ops_py and "_BAD_PATH" in ops_py)
check("凭据台账不接收口令本体", "不接收口令" in ops_py or "不接收口令" in index)
check("前端左侧功能导航含三组截图菜单",
      all(x in index for x in ("资产管理", "监控告警", "日志中心",
                               "仪表盘", "机房管理", "主机管理", "网络设备", "凭据管理",
                               "主机巡检", "数据库管理", "中间件管理",
                               "监控中心", "告警事件", "告警策略", "告警模板",
                               "告警聚合", "告警通知", "告警资源",
                               "日志管理", "日志查询", "日志采集", "日志告警",
                               "日志链路", "数据慢日志", "业务访问日志")))
check("前端有态势舱容器与紫罗兰配色", 'id="wbCockpit"' in index
      and ".wb-cockpit" in stylecss and "#7b61ff" in stylecss)
check("前端提供告警策略与日志源录入弹窗", 'id="modalOpsRecord"' in index
      and "opsRecordSave" in appjs)
check("后端提供资产/告警/日志接口",
      all(x in open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()
          for x in ("/api/ops/catalog", "/api/ops/alert/config", "/api/ops/logs/sources",
                    "/api/ops/logs/collect", "/api/ops/dashboard")))
check("专项回归覆盖新功能", os.path.isfile(
    os.path.join(ROOT, "scripts", "test_ops_center.py")))

# ---------------- 本轮：主机统一入口 / 大屏 / 全局刷新 / 数据库与告警中心 ----------------
print("=== 主机管理 / 运维大屏 / 全局刷新 / 数据库与告警中心 ===")
opsext_path = os.path.join(ROOT, "frontend", "assets", "ops_ext.js")
check("前端扩展脚本 ops_ext.js 存在", os.path.isfile(opsext_path))
opsext = src("frontend/assets/ops_ext.js") if os.path.isfile(opsext_path) else ""
app_py = open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()
check("旧左侧主机列表竖条已从页面移除",
      'id="sidebar"' not in index and 'id="btnSideToggle"' not in index)
check("首页已去掉「可插拔分析插件」模块",
      "可插拔分析插件" not in index and "wbRenderPlugins(pl); wbRenderSources" not in appjs)
check("终端保留紧凑主机选择/连接工具条",
      'id="termHostSelect"' in index and 'id="btnTermConnect"' in index
      and "btnTermManage" in index)
check("顶栏有全局刷新与运维大屏入口",
      'id="btnGlobalRefresh"' in index and 'id="btnScreen"' in index
      and "screenView" in index and "globalRefresh" in appjs)
check("全局刷新联动工作台、当前页与大屏",
      "refreshAll" in opsext and "afterHostSaved" in opsext
      and "OpsExt.refreshAll" in appjs)
check("仪表盘态势舱已按要求去掉，不再写死维保数字",
      "维保已到期" not in opsext and "expired" not in opsext
      and "['维保到期', 0" not in opsext)
check("主机管理提供新增/编辑/删除/导入/导出",
      all(x in opsext for x in ("＋ 新增主机", "⬆ 导入", "⬇ 导出", "btn('编辑'", "btn('删除'"))
      and 'id="hostImportFile"' in index)
check("资产导航含平台导航与 DB 数据库分组",
      all(('data-page="%s"' % p) in index for p in
          ("platform", "db_assets", "db_query", "db_audit", "db_monitor",
           "alert_center", "domain_alert", "alert_source")))
check("台账新增弹窗按需加载字段元数据（冷启动可点）",
      "async function ensureCatalogMeta()" in appjs
      and "await ensureCatalogMeta()" in appjs
      and "await ensureCatalogMeta();" in opsext
      and "async function opsOpenForm(" in appjs)
check("探针管理 / 慢日志分析 / 数据库助手菜单已去掉",
      all(('data-page="%s"' % p) not in index
          for p in ("probe", "db_slow", "db_assistant")))
check("资产规模态势舱扩展已去掉，顶栏大屏入口保留",
      'id="opsDashboardExt"' not in index and 'id="btnScreen"' in index
      and "资产规模、监控覆盖与运行健康总览" not in opsext)
check("平台导航四个高亮入口已实现",
      "t[4] ? ' key'" in opsext and all(x in opsext for x in
      ("资产管理", "DB 数据库", "日志中心", "告警中心")))
check("平台导航置于左侧菜单最上方且红框占位入口已去掉",
      index.index('data-page="platform"') < index.index('data-page="dashboard"')
      and 'class="wb-nav-item wb-nav-quick" data-page="platform"' in index
      and "wb-nav-quick" in stylecss
      and all(x not in opsext for x in
      ("配置中心", "服务作业", "AIOps 运维", "工单中心", "费用中心", "审计中心")))
check("大屏为亮蓝紫独立配色并展示真实指标",
      "screen-view" in stylecss and "#16244f" in stylecss
      and "linear-gradient(135deg,#1b2e6d" in stylecss
      and "renderScreen" in opsext and "纳管主机" in opsext)
check("大屏铺满浏览器并支持一键全屏",
      ".screen-view{position:fixed;inset:0" in stylecss
      and 'id="btnScreenFull"' in index and "toggleScreenFull" in opsext)
check("左侧菜单分组标题可点击伸缩",
      index.count('class="wb-nav-title" role="button"') == 4
      and "wb-nav-caret" in stylecss and "wbExpandNavFor" in appjs
      and "NAV_GROUP_KEY" in appjs)
check("终端主机下拉按地域分组并支持搜索",
      'id="termHostSearch"' in index and "renderTermHostOptions" in appjs
      and "el('optgroup')" in appjs and "未分配地域" in appjs)
check("AI 助手目标只保留主机",
      'id="aiTargetType"' in index and 'value="host"' in index
      and all(('value="%s"' % t) not in index
              for t in ("database", "application", "log"))
      and "target_type" in appjs and "/api/agent/run" in app_py)
check("命令问答已改为 AI 问答",
      'data-mode="ask">AI 问答<' in index and "命令问答" not in index)
check("清空重置同时清空输入框，输入区已加大",
      "$('promptInput').value = ''" in appjs
      and "min-height:150px" in stylecss and "db-assistant-input" in stylecss
      and "q.rows = 6" in opsext)
check("数据库资产清单按实例/类型/版本/IP-端口/环境/监控接入/存活/认证排列",
      all(("key: '%s'" % k) in opsext for k in
          ("type", "version", "address", "env", "monitor", "alive", "auth"))
      and '"monitor":' in src("backend/ops_center.py"))
check("链路提取正则给出正确写法提示且路径修复为 messages",
      "(trace[=:]([a-z0-9-]+))" in appjs and "field-tip" in appjs
      and "field-tip" in stylecss
      and "/var/log/messages" in src("scripts/test_ops_center.py"))
check("大屏支持 5/10/15/30 分钟时间窗且指标带单位",
      'id="screenWindow"' in index
      and all(('value="%s"' % t) in index for t in ("5m", "10m", "15m", "30m", "1h"))
      and '"10m"' in src("backend/workbench.py")
      and "screen-unit" in stylecss and "screen-unit" in opsext)
check("AI 多目标上下文只取纳管主机与用户录入数据",
      "DATA_RULE" in src("backend/agent.py")
      and "目标数据分析专用规则" in src("backend/agent.py")
      and "数据库资产（用户录入）" in src("backend/ai_targets.py")
      and "密钥本体一律不返回" in src("backend/ai_targets.py"))
check("保存按钮均有可见反馈",
      "数据已同步到当前页面" in appjs and "opsRecordMsg" in appjs
      and "已保存" in appjs)
check("AI 执行按钮与标签文案改为开始执行/命令执行",
      'data-mode="diagnose" class="active">命令执行<' in index
      and 'id="btnRun" class="btn primary">▶ 开始执行<' in index
      and "'▶ 开始执行'" in appjs and "▶ 开始诊断" not in index
      and "▶ 开始诊断" not in appjs and "执行诊断" not in index)
check("数据库与告警中心后端接口全部注册",
      all(x in app_py for x in ("/api/ops/hosts", "/api/ops/dashboard/detail",
                                "/api/ops/screen", "/api/ops/db/assets",
                                "/api/ops/db/records", "/api/ops/db/monitor",
                                "/api/ops/alert/center", "/api/ops/domains")))
check("主机导出不携带密码或口令散列",
      "不包含密码、密码散列" in open(
          os.path.join(ROOT, "backend", "ops_center.py"), encoding="utf-8").read())
check("页面与脚本不再出现文章示例数字 4820/93/67/642",
      not any(x in index + appjs + opsext for x in ("4820", "93%", "67%", "642")))

print()
print("结果: %d 通过 / %d 失败" % (PASS, FAIL))
if FAILURES:
    print("失败项: " + " | ".join(FAILURES))
sys.exit(1 if FAIL else 0)
