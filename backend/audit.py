# -*- coding: utf-8 -*-
"""本地终端审计：按「会话窗口」生成独立纯文本文件，完整 1:1 转录终端内容。

会话窗口 = 用户点「连接」建立 SSH 终端 → 点「断开」（或关页面）结束。
每次连接生成一个文件：

    <项目根>/audit/sessions/<用户名>_<连接时间>.txt
    例如：admin_2026-11-13_19-50-01.txt

文件内容（自上而下）：
  · 文件头   ：登录用户 / 来源 IP / 主机 / 目标地址 / 会话 ID / 连接时间 / 状态
  · 终端实录 ：从 SSH 连接建立起，屏幕上打印过的**全部内容**（登录横幅、命令回显、
               命令输出、报错、提示符、全屏程序画面），按显示顺序 1:1 转录为纯文本，
               不做删减、不截断；仅去掉颜色 / 光标定位等不可见控制码。
               写入是流式的：会话进行中打开 .txt 就能看到内容实时增长。
  · 事件标注 ：以「# [事件]」开头、带精确时间戳的结构化行（连接/断开/命令/
               上传下载/AI 代执行/登录等），便于检索「谁 · 什么时间 · 做了什么」。
  · 文件尾   ：断开时间 / 持续时长 / 终端实录行数 / 原始流字节数 / 事件条数 / 状态

另有 raw/<同名>.raw 保存服务端发来的**未经任何处理**的原始字节流（含 ANSI），
可逐字节复核或回放；主 .txt 是给人 / AI 直接读的 1:1 文本镜像。

数据来源唯一且不可绕过：SSH reader 线程每收到一个字节块就回调 feed_output()，
即使用户关掉浏览器、SSE 断开，服务端仍然照常落盘。前端本地提示走 write_notice()，
按键还原（feed_keys）与上传下载 / AI 事件走 write_event()，全部归入同一个窗口文件。
"""
import datetime as _dt
import os
import re
import threading

from .term_capture import TerminalTranscript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_DIR = os.path.join(ROOT, "audit")
SESSION_DIR = os.path.join(AUDIT_DIR, "sessions")
LEGACY_LOG = os.path.join(AUDIT_DIR, "command-audit.log")   # 兼容早期单文件版本


def _raw_dir():
    """原始流目录跟随 SESSION_DIR（单测会把 SESSION_DIR 指到临时目录）。"""
    return os.path.join(SESSION_DIR, "raw")

BEGIN_MARK = "===== 终端实录开始"
END_MARK = "===== 终端实录结束"
EVENT_PREFIX = "# [事件] "
MAX_LINE_CHARS = 4096             # 单行命令上限，防止异常输入撑爆文件
MAX_PENDING_CHUNKS = 4000         # open_session 之前最多缓存的字节块数

_lock = threading.RLock()
_lines = {}                       # sid -> {"buf", "mask_next", "approx"}  按键行缓冲
_sessions = {}                    # sid -> 会话状态（含屏幕还原器、raw 文件句柄）
_pending = {}                     # sid -> open_session 之前到达的 (raw, text)
_closed_sids = set()              # 已封存的 sid，避免断开后再缓存数据
_initialized = False

# ---------- 控制序列 ----------
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_ESC2 = re.compile(r"\x1b[@-Z\\-_]")
_HISTORY_KEY = re.compile(r"\x1b\[[AB]")
_CTRL_LINE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TS_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

# ---------- 脱敏 ----------
_SECRET_PATTERNS = [
    (re.compile(r"(?i)(--?(?:password|passwd|pwd|token|api[-_]?key|secret|access[-_]?key)"
                r"(?:=|\s+))([^\s'\"&|;]+)"), r"\1***"),
    (re.compile(r"(?i)(\s-p)([^\s\-]\S*)"), r"\1***"),
    (re.compile(r"(?i)\b((?:password|passwd|pwd|token|secret|api_key|access_key)"
                r"\s*[:=]\s*)([^\s'\"&|;]+)"), r"\1***"),
    (re.compile(r"(://[^/\s:@]+:)([^@\s/]+)(@)"), r"\1***\3"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
]

# 这些命令之后的一次输入极可能是交互式口令，整体打码
_MASK_NEXT = re.compile(
    r"(?i)^\s*(sudo\s+-S\b|passwd\b|smbpasswd\b|htpasswd\b|chpasswd\b|su\b|su$|"
    r"login\b|openssl\s+passwd\b|mysql\b[^\n]*-p\s*$|psql\b[^\n]*-W\b)")

# 审计意图关键词（用户输入命中即让 AI 去查本地会话文档）
_AUDIT_INTENT = re.compile(
    r"(?i)(审计|操作记录|命令历史|执行过|执行了|谁.*(?:操作|执行|登录|连|改|删|下载|上传)|"
    r"什么时候|什么时间|哪天|几点|最近.*(?:做了|操作|改)|改动记录|登录记录|"
    r"audit|command history|who (?:did|ran|executed)|last (?:command|login))")


def _now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp(dt=None):
    return (dt or _dt.datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")


def log_path():
    """审计文档所在目录的绝对路径（界面「复制路径」、AI 上下文都引用它）。"""
    return SESSION_DIR


def _safe_user(u):
    u = re.sub(r"[^\w\u4e00-\u9fa5.\-]", "_", str(u or "unknown"), flags=re.UNICODE)
    return (u.strip("._") or "unknown")[:32]


def _clean(s):
    """把任意文本压成单行，去掉控制字符（命令里的 | 保留，字段以 ' | cmd=' 定位）。"""
    s = str(s or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = _CTRL_LINE.sub("", s)
    return s[:MAX_LINE_CHARS]


def _clean_line(s):
    """终端实录的一行：已经是单行，只去掉残留的不可见控制字符。"""
    return _CTRL_LINE.sub("", str(s or ""))


def sanitize(cmd):
    """命令行 / 终端输出里的口令、Token、密钥等敏感串脱敏。"""
    s = str(cmd or "")
    for pat, rep in _SECRET_PATTERNS:
        s = pat.sub(rep, s)
    return s


# ---------- 会话文件头 / 尾 ----------
def _header(meta):
    return (
        "# ============================================================\n"
        "# AI 运维控制台 · 终端会话完整审计记录\n"
        "# 规则：每次「连接 → 断开」为一个完整会话窗口，单独生成一个文本文件；\n"
        "#       文件名为「登录用户名_连接时间.txt」。\n"
        "# ------------------------------------------------------------\n"
        "# 记录内容：自 SSH 终端建立那一刻起，终端屏幕上打印过的全部内容\n"
        "#   （登录横幅、命令回显、命令输出、报错、提示符、全屏程序画面……）\n"
        "#   按显示顺序 1:1 转录为纯文本，不删减、不截断；仅去掉颜色、光标定位\n"
        "#   等不可见控制码。写入是流式的，会话进行中打开本文件即可看到实时内容。\n"
        "#   以「# [事件]」开头的行是带精确时间戳的结构化操作标注，便于检索\n"
        "#   「谁 · 什么时间 · 在哪台主机 · 做了什么」。\n"
        "#   同目录 raw/ 下的同名 .raw 保存服务端原始字节流（含 ANSI），可逐字节复核。\n"
        "# 说明：仅口令 / Token / 密钥等敏感串会替换成 ***；交互式口令输入\n"
        "#   （终端不回显）本来就不会出现在记录里。\n"
        "# ============================================================\n"
        "# session_file : %(file)s\n"
        "# user         : %(user)s\n"
        "# ip           : %(ip)s\n"
        "# host         : %(host)s\n"
        "# target       : %(target)s\n"
        "# sid          : %(sid)s\n"
        "# connected_at : %(start)s\n"
        "# status       : %(status)s\n"
        "# viewport     : %(viewport)s\n"
        "# ------------------------------------------------------------\n"
        "# 事件行格式：# [事件] 时间 | type=事件类型 | source=来源 | cmd=操作内容\n"
        "# type: CONNECT 连接 / DISCONNECT 断开 / CMD 终端命令 / AI_CMD AI执行命令 /\n"
        "#       UPLOAD 上传 / DOWNLOAD 下载 / DIAGNOSE AI诊断 / AI_ASK AI问答 / LOGIN 登录 等\n"
        "# ============================================================\n\n"
        "%(begin)s · %(start)s =====\n"
    ) % dict(meta, begin=BEGIN_MARK)


def _footer(end, duration, term_lines, term_chars, raw_bytes, count, status):
    return (
        "%s · %s =====\n\n"
        "# ------------------------------------------------------------\n"
        "# 会话结束\n"
        "# disconnected_at : %s\n"
        "# duration        : %s\n"
        "# terminal_lines  : %d\n"
        "# terminal_chars  : %d\n"
        "# raw_bytes       : %d\n"
        "# commands        : %d\n"
        "# status          : %s\n"
        "# ============================================================\n"
    ) % (END_MARK, end, end, duration, term_lines, term_chars, raw_bytes,
         count, status)


def _duration(a, b):
    try:
        d = _dt.datetime.strptime(b, "%Y-%m-%d %H:%M:%S") - \
            _dt.datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        sec = max(0, int(d.total_seconds()))
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return ("%d 小时 %d 分 %d 秒" % (h, m, s)) if h else \
               ("%d 分 %d 秒" % (m, s)) if m else ("%d 秒" % s)
    except Exception:
        return "-"


def ensure():
    """创建目录；首次启动时把进程崩溃遗留的「进行中」会话补成异常终止。"""
    global _initialized
    os.makedirs(SESSION_DIR, exist_ok=True)
    os.makedirs(_raw_dir(), exist_ok=True)
    if _initialized:
        return SESSION_DIR
    _initialized = True
    try:
        for name in sorted(os.listdir(SESSION_DIR)):
            if not name.endswith(".txt") or not os.path.isfile(os.path.join(SESSION_DIR, name)):
                continue
            fp = os.path.join(SESSION_DIR, name)
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            if "# 会话结束" in text or "status       : 独立操作" in text:
                continue
            m = re.search(r"# connected_at : (\S+ \S+)", text)
            start = m.group(1) if m else _now()
            cnt = sum(1 for ln in text.splitlines() if parse_line(ln))
            tl = len(read_transcript(fp))
            with open(fp, "a", encoding="utf-8", newline="\n") as f:
                f.write(_footer(_now(), _duration(start, _now()), tl,
                                0, 0, cnt, "异常终止（服务重启，未收到断开请求）"))
    except OSError:
        pass
    return SESSION_DIR


def _unique_path(user, stamp, tag=""):
    """生成不重名的会话文件名：用户名_时间.txt，同秒重连追加 _2 / _3。"""
    base = "%s_%s%s" % (_safe_user(user), stamp, tag)
    path = os.path.join(SESSION_DIR, base + ".txt")
    n = 2
    while os.path.exists(path):
        path = os.path.join(SESSION_DIR, "%s_%d.txt" % (base, n))
        n += 1
    return path


def _raw_path(txt_path):
    d = _raw_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, os.path.splitext(os.path.basename(txt_path))[0] + ".raw")


def _create_file(meta):
    path = _unique_path(meta["user"], _stamp(), meta.get("suffix", ""))
    meta = dict(meta, file=os.path.basename(path))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_header(meta))
    raw_path = _raw_path(path)
    fp = open(raw_path, "wb")           # noqa: SIM115  句柄随会话封存时关闭
    return path, dict(meta), raw_path, fp


def _state(meta, path, raw_path, raw_fp, screen, closed=False):
    return {"path": path, "ctx": meta, "start": meta["start"], "count": 0,
            "closed": closed, "raw_path": raw_path, "raw_fp": raw_fp,
            "screen": screen, "term_lines": 0, "term_chars": 0, "raw_bytes": 0}


def open_session(sid, ctx):
    """终端连接成功 → 开启一个会话窗口文件。返回文件绝对路径。"""
    if not sid:
        return ""
    ctx = ctx or {}
    with _lock:
        ensure()
        start = _now()
        meta = {
            "user": _clean(ctx.get("user") or "unknown"),
            "ip": _clean(ctx.get("ip") or "-"),
            "host": _clean(ctx.get("host") or "-"),
            "target": _clean(ctx.get("target") or "-"),
            "sid": _clean(sid), "start": start,
            "status": "进行中（结束状态见文件末尾摘要）", "suffix": "",
            "viewport": "%s x %s" % (ctx.get("cols", 120), ctx.get("rows", 36)),
        }
        path, meta, raw_path, raw_fp = _create_file(meta)
        screen = TerminalTranscript(ctx.get("cols", 120), ctx.get("rows", 36))
        _sessions[sid] = _state(meta, path, raw_path, raw_fp, screen)
        _closed_sids.discard(sid)
        # 连接瞬间可能已经有横幅 / 提示符到达，补写进同一个窗口，绝不丢开头
        for raw, text in _pending.pop(sid, []):
            _feed_locked(_sessions[sid], raw, text)
        return path


def close_session(sid, status="正常断开"):
    """终端断开 → 吐出最后一屏、写结束摘要，窗口文件就此完整封存。"""
    with _lock:
        info = _sessions.pop(sid, None)
        reset_session(sid)
        _pending.pop(sid, None)
        _closed_sids.add(sid)
        if not info or info["closed"] or not os.path.exists(info["path"]):
            return ""
        try:
            _write_lines(info, info["screen"].flush())
        except Exception as e:
            print("[audit] flush terminal transcript failed: %s" % e)
        try:
            if info["raw_fp"]:
                info["raw_fp"].close()
                info["raw_fp"] = None
        except Exception:
            pass
        end = _now()
        with open(info["path"], "a", encoding="utf-8", newline="\n") as f:
            f.write(_footer(end, _duration(info["start"], end),
                            info["term_lines"], info["term_chars"],
                            info["raw_bytes"], info["count"], status))
        return info["path"]


def reset_session(sid):
    """只清按键行缓冲（连接断开、Ctrl+C 等场景）。"""
    with _lock:
        _lines.pop(sid, None)


# ---------- 写入 ----------
def _resolve_path(ctx):
    """决定一条事件 / 终端内容写到哪个会话窗口文件。

    1) sid 明确对应活跃会话 → 写该文件；
    2) 没有 sid（上传下载 / AI 诊断问答）→ 归入该用户当前最近一个活跃会话；
    3) 用户当前没有终端会话 → 写当天的「独立操作」文件，记录不丢失。
    """
    sid = str((ctx or {}).get("sid") or "-")
    if sid != "-" and sid in _sessions and not _sessions[sid]["closed"]:
        return _sessions[sid]["path"], _sessions[sid]
    user = (ctx or {}).get("user") or "unknown"
    candidates = [v for v in _sessions.values()
                  if not v["closed"] and v["ctx"].get("user") == user]
    if candidates:
        info = max(candidates, key=lambda v: v["start"])
        return info["path"], info

    key = "__loose__%s_%s" % (_safe_user(user), _dt.datetime.now().strftime("%Y-%m-%d"))
    if key in _sessions:
        return _sessions[key]["path"], _sessions[key]
    meta = {
        "user": _clean(user), "ip": _clean((ctx or {}).get("ip") or "-"),
        "host": "-", "target": "-", "sid": "-", "start": _now(),
        "status": "独立操作（未关联终端会话，按天汇总）", "suffix": "_独立操作",
        "viewport": "-",
    }
    path, meta, raw_path, raw_fp = _create_file(meta)
    _sessions[key] = _state(meta, path, raw_path, raw_fp,
                            TerminalTranscript(), closed=True)
    return path, _sessions[key]


def _write_lines(info, lines):
    """把终端实录的多行一次性追加到会话 .txt（已脱敏敏感串）。"""
    if not lines:
        return []
    safe = [sanitize(_clean_line(ln)) for ln in lines]
    with open(info["path"], "a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(safe) + "\n")
        f.flush()
    info["term_lines"] += len(safe)
    info["term_chars"] += sum(len(x) + 1 for x in safe)
    return safe


def _feed_locked(info, raw, text):
    """服务端一个原始字节块：raw 原样留档，text 送进屏幕还原器后逐行落盘。"""
    if raw:
        try:
            if info["raw_fp"]:
                info["raw_fp"].write(raw)
                info["raw_fp"].flush()
                info["raw_bytes"] += len(raw)
        except Exception as e:
            print("[audit] write raw stream failed: %s" % e)
    if not text:
        return []
    try:
        return _write_lines(info, info["screen"].feed(text))
    except Exception as e:
        print("[audit] write terminal transcript failed: %s" % e)
        return []


def feed_output(sid, raw=b"", text=""):
    """SSH reader 线程每收到一块数据就调用：1:1 转录终端内容 + 保存原始字节流。

    open_session 之前到达的数据先缓存（连接瞬间的横幅 / 提示符不能丢），
    会话已封存之后到达的数据丢弃。返回本次落盘的纯文本行。
    """
    if not sid:
        return []
    with _lock:
        ensure()
        info = _sessions.get(sid)
        if not info or info["closed"]:
            if sid in _closed_sids:
                return []
            buf = _pending.setdefault(sid, [])
            if len(buf) < MAX_PENDING_CHUNKS:
                buf.append((raw or b"", text or ""))
            return []
        return _feed_locked(info, raw or b"", text or "")


def write_notice(sid, text, ctx=None):
    """前端写进终端的本地提示（连接中 / 会话已结束 / 连接失败等）也一并留档。"""
    body = _clean(text)
    if not body:
        return ""
    with _lock:
        if sid and sid in _closed_sids:
            return ""                       # 窗口已封存，不再往别的文件里串记录
        ensure()
        c = dict(ctx or {})
        c["sid"] = sid or "-"                 # 以本次上报的窗口为准
        _path, info = _resolve_path(c)
        return "\n".join(_write_lines(info, [body]))


def resize_screen(sid, cols, rows):
    """终端尺寸变化后同步屏幕还原器，保证中文宽字符 / 自动换行不错位。"""
    with _lock:
        info = _sessions.get(sid)
        if info and info.get("screen"):
            info["screen"].resize(cols, rows)


def write_event(ctx, cmd, ev_type="CMD", source="terminal"):
    """写一条结构化事件到当前会话窗口文件。ctx 由 app.py 组装。"""
    ctx = ctx or {}
    line = "%s%s | type=%s | source=%s | cmd=%s\n" % (
        EVENT_PREFIX, _now(), _clean(ev_type), _clean(source), _clean(cmd))
    with _lock:
        ensure()
        path, info = _resolve_path(ctx)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
        if str(ctx.get("host") or "-") != "-" and info["ctx"].get("host") == "-":
            info["ctx"]["host"] = _clean(ctx["host"])
        info["count"] += 1
    return line


def _submit(sid, ctx):
    """提交当前行缓冲：返回 (命令文本, 是否被打码)。"""
    st = _lines.get(sid)
    if not st:
        return None, False
    raw = st["buf"].strip()
    st["buf"] = ""
    approx = st["approx"]
    st["approx"] = False
    masked = st["mask_next"]
    st["mask_next"] = False
    if not raw:
        return None, False
    if masked:
        return "[已脱敏：疑似口令/敏感输入]", True
    cmd = sanitize(raw)
    if approx:
        cmd += "  [命令由历史/补全调出，完整原文见上方终端实录]"
    if _MASK_NEXT.search(cmd):
        st["mask_next"] = True
    return cmd, False


def feed_keys(sid, data, ctx, source="terminal"):
    """喂入 xterm.js 原始按键流，遇到回车就补一条 CMD 事件。

    注意：终端实录（1:1）才是命令原文的唯一权威来源；这里的按键还原只用于
    生成可检索的结构化事件，历史键 / Tab 补全看不到远端内容时会如实标注。
    keepalive 字符由调用方过滤，不会进来。
    """
    if not sid or not data:
        return []
    with _lock:
        st = _lines.setdefault(sid, {"buf": "", "mask_next": False, "approx": False})
        if _HISTORY_KEY.search(data):
            st["approx"] = True
            st["buf"] = "[历史命令：由终端从 shell 历史调出，原文见终端实录]"
        text = _CSI.sub("", data)
        text = _OSC.sub("", text)
        text = _ESC2.sub("", text)
        text = text.replace("\x1b", "")

        produced = []
        for ch in text:
            code = ord(ch)
            if ch in "\r\n":
                cmd, _masked = _submit(sid, ctx)
                if cmd:
                    write_event(ctx, cmd, "CMD", source)
                    produced.append(cmd)
            elif ch in "\x7f\b":
                st["buf"] = st["buf"][:-1]
            elif ch in "\x03\x04\x15":        # Ctrl+C / Ctrl+D / Ctrl+U：丢弃当前行
                st["buf"] = ""
                st["mask_next"] = False
                st["approx"] = False
            elif ch == "\t":                    # Tab 补全过程发生在远端，本地不可见
                st["approx"] = True
            elif code < 32:
                continue
            else:
                st["buf"] += ch
                if len(st["buf"]) > MAX_LINE_CHARS:
                    st["buf"] = st["buf"][-MAX_LINE_CHARS:]
        return produced


# ---------- 读取 / 检索 ----------
def _files():
    """所有会话窗口文件（按文件名 = 时间顺序，从旧到新）；兼容早期单文件日志。"""
    fs = []
    try:
        for name in sorted(os.listdir(SESSION_DIR)):
            if name.endswith(".txt") and os.path.isfile(os.path.join(SESSION_DIR, name)):
                fs.append(os.path.join(SESSION_DIR, name))
    except OSError:
        pass
    legacy = LEGACY_LOG if os.path.exists(LEGACY_LOG) else ""
    if legacy:
        fs.append(legacy)
    return fs


def _meta_of(text, filename):
    m = {"file": filename, "user": "", "ip": "-", "host": "-", "target": "-",
         "sid": "-", "start": "", "end": "", "duration": "-", "commands": 0,
         "terminal_lines": 0, "terminal_chars": 0, "raw_bytes": 0,
         "status": "进行中", "size": 0}
    keys = {"session_file": "file", "user": "user", "ip": "ip", "host": "host",
            "target": "target", "sid": "sid", "connected_at": "start",
            "disconnected_at": "end", "duration": "duration",
            "commands": "commands", "status": "status",
            "terminal_lines": "terminal_lines", "terminal_chars": "terminal_chars",
            "raw_bytes": "raw_bytes"}
    ints = {"commands", "terminal_lines", "terminal_chars", "raw_bytes"}
    for ln in text.splitlines():
        if not ln.startswith("#") or ln.startswith(EVENT_PREFIX):
            continue
        mm = re.match(r"#\s*([\w_]+)\s*:\s*(.*)$", ln)
        if not mm:
            continue
        k, v = mm.group(1).strip(), mm.group(2).strip()
        if k in keys:
            m[keys[k]] = int(v) if k in ints and v.isdigit() else v
    if "# 会话结束" in text and not m["end"]:
        m["end"] = "-"
    return m


def read_transcript(fp, max_lines=None):
    """读取会话文件里的「终端实录」1:1 纯文本行（不含事件行与文件头尾）。"""
    lines, in_body = [], False
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for ln in f.read().splitlines():
                if ln.startswith(BEGIN_MARK):
                    in_body = True
                    continue
                if ln.startswith(END_MARK):
                    in_body = False
                    continue
                if not in_body:
                    continue
                if ln.startswith(EVENT_PREFIX) or parse_line(ln):
                    continue
                lines.append(ln)
    except OSError:
        pass
    if max_lines:
        lines = lines[-max_lines:]
    return lines


def read_session_file(fp):
    """读取单个会话文件，返回 (meta, [结构化事件 dict])。"""
    meta = {}
    entries = []
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        meta = _meta_of(text, os.path.basename(fp))
        meta["size"] = os.path.getsize(fp)
        meta["size_human"] = "%.1f KB" % (meta["size"] / 1024.0)
        meta["path"] = fp
        for ln in text.splitlines():
            e = parse_line(ln)
            if not e:
                continue
            for k in ("user", "ip", "host", "target", "sid"):
                if not e.get(k) and meta.get(k):
                    e[k] = meta[k]
            e["file"] = meta["file"]
            entries.append(e)
    except OSError:
        pass
    return meta, entries


def read_entries(limit=200, keyword="", user="", host="", ev_type="", since="", until=""):
    """跨所有会话窗口文件检索结构化事件，按时间倒序返回 dict 列表。"""
    kw = (keyword or "").strip().lower()
    u = (user or "").strip().lower()
    h = (host or "").strip().lower()
    t = (ev_type or "").strip().upper()
    out = []
    with _lock:
        for fp in _files():
            meta, entries = read_session_file(fp)
            for e in entries:
                if since and e["time"] < since:
                    continue
                if until and e["time"] > until:
                    continue
                if u and u not in e["user"].lower():
                    continue
                if h and h not in (e["host"] + " " + e["target"]).lower():
                    continue
                if t:
                    et = e["type"].upper()
                    if t == "CONNECT" and et not in ("CONNECT", "DISCONNECT"):
                        continue
                    if t == "UPLOAD" and et not in ("UPLOAD", "DOWNLOAD", "UPLOAD_CANCEL",
                                                    "DOWNLOAD_CANCEL"):
                        continue
                    if t == "DIAGNOSE" and et not in ("DIAGNOSE", "AI_ASK"):
                        continue
                    if t in ("CMD", "AI_CMD") and et != t:
                        continue
                if kw and kw not in e["raw"].lower():
                    continue
                out.append(e)
            # 关键词检索同时覆盖「终端实录」原文，让命令输出也能被搜到。
            # 实录行本身没有逐行时间戳，时间取该会话连接时间，内容标注来源。
            if kw and not t:
                for ln in read_transcript(fp):
                    if kw not in ln.lower():
                        continue
                    cmd = "[终端实录] " + ln[:500]
                    if u and u not in meta.get("user", "").lower():
                        continue
                    if h and h not in (meta.get("host", "") + " " +
                                       meta.get("target", "")).lower():
                        continue
                    out.append({"time": meta.get("start", ""), "user": meta.get("user", ""),
                                "ip": meta.get("ip", "-"), "host": meta.get("host", "-"),
                                "target": meta.get("target", "-"), "sid": meta.get("sid", "-"),
                                "type": "OUT", "source": "terminal", "cmd": cmd,
                                "raw": cmd, "file": meta.get("file", "")})
                    if len(out) > 5000:
                        break
    out.sort(key=lambda x: x["time"], reverse=True)
    return out[:max(1, min(int(limit or 200), 5000))]


def parse_line(line):
    """解析一条结构化事件行。cmd 用 ' | cmd=' 定位，内容含 | 也不会被切断。

    兼容三种格式：新会话文件（# [事件] 时间 | type=.. | source=.. | cmd=..）、
    旧会话文件（时间 | type=.. | source=.. | cmd=..）与早期单文件日志。
    终端实录行（不带时间戳 + 事件前缀）一律返回 None，不会被误当成事件。
    """
    s = str(line or "").strip()
    if s.startswith(EVENT_PREFIX):
        s = s[len(EVENT_PREFIX):].strip()
    if " | cmd=" not in s or not _TS_LINE.match(s):
        return None
    head, cmd = s.split(" | cmd=", 1)
    e = {"time": "", "user": "", "ip": "", "host": "", "target": "",
         "sid": "", "type": "", "source": "", "cmd": cmd.strip(), "raw": line,
         "file": ""}
    fields = head.split(" | ")
    e["time"] = fields[0].strip()
    for kv in fields[1:]:
        if "=" in kv:
            k, v = kv.split("=", 1)
            if k.strip() in e:
                e[k.strip()] = v.strip()
    return e


def list_sessions():
    """会话窗口清单（按连接时间倒序），供界面左侧列表展示。"""
    out = []
    with _lock:
        ensure()
        for fp in _files():
            if os.path.abspath(fp) == os.path.abspath(LEGACY_LOG):
                continue
            meta, entries = read_session_file(fp)
            if not meta:
                continue
            if not meta["commands"]:
                meta["commands"] = len(entries)
            if not meta["terminal_lines"]:
                # 进行中的会话还没有文件尾摘要，实时数一下已落盘的实录行
                meta["terminal_lines"] = len(read_transcript(fp))
            out.append(meta)
    out.sort(key=lambda x: x.get("start") or "", reverse=True)
    return out


def stats():
    with _lock:
        ensure()
        files = _files()
        sessions = list_sessions()
        total = len(read_entries(limit=5000))
        size = sum(os.path.getsize(fp) for fp in files if os.path.exists(fp))
        entries = read_entries(limit=1)
        return {"path": SESSION_DIR, "dir": SESSION_DIR,
                "sessions": len(sessions), "files": len(sessions),
                "total": total, "size": size,
                "terminal_lines": sum(s.get("terminal_lines", 0) for s in sessions),
                "raw_bytes": sum(s.get("raw_bytes", 0) for s in sessions),
                "size_human": "%.1f KB" % (size / 1024.0),
                "latest": entries[0]["time"] if entries else ""}


def is_audit_question(prompt):
    return bool(_AUDIT_INTENT.search(prompt or ""))


def _keywords(prompt):
    """从问题里抽出可用于在终端实录中定位内容的词（>=2 字符，去掉常见虚词）。"""
    stop = {"审计", "一下", "哪些", "什么", "怎么", "是否", "有没有", "今天", "昨天",
            "最近", "记录", "命令", "操作", "执行", "用户", "时间", "主机", "会话",
            "请帮", "告诉", "帮我", "看看", "查一", "所有", "多少", "这个", "那个",
            "the", "and", "for", "what", "who", "when", "did", "all", "list"}
    words = re.findall(r"[\w./\-]{2,}", (prompt or "").lower())
    return [w for w in words if w not in stop and not w.isdigit()][:8]


def context_for_ai(prompt, limit=250):
    """审计类问题 → 「本地会话文档」上下文：结构化事件 + 终端实录摘录。

    非审计问题返回空字符串，完全不增加 token 开销。
    """
    if not is_audit_question(prompt):
        return ""
    p = (prompt or "").lower()
    users = [n for n in ("admin", "king", "root") if n in p]
    types = []
    if "上传" in prompt or "upload" in p:
        types.append("UPLOAD")
    if "下载" in prompt or "download" in p:
        types += ["DOWNLOAD"]
    if "连接" in prompt or "登录" in prompt or "断开" in prompt:
        types += ["CONNECT", "DISCONNECT", "CONNECT_CANCEL"]

    with _lock:
        sessions = list_sessions()
        metas = {s["file"]: s for s in sessions}
        all_entries = []
        for fp in _files():
            m, es = read_session_file(fp)
            metas.setdefault(m.get("file", ""), m)
            all_entries.extend(es)
    total_all = len(all_entries)
    if users:
        hit = [e for e in all_entries if any(u in e["user"].lower() for u in users)]
        if hit:
            all_entries = hit
    if types:
        hit = [e for e in all_entries if e["type"].upper() in types]
        if hit:
            all_entries = hit
    if "今天" in prompt:
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        hit = [e for e in all_entries if e["time"].startswith(today)]
        if hit:
            all_entries = hit
    all_entries.sort(key=lambda x: x["time"])
    picked = all_entries[-limit:]
    picked_files = sorted({e["file"] for e in picked})

    lines = [
        "【本地审计文档 · 唯一事实依据】",
        "本机固定目录：%s" % SESSION_DIR,
        "该目录按「终端会话窗口」存放 UTF-8 纯文本审计文件：用户每次连接一台主机直到断开，"
        "生成一个独立 .txt 文件，文件名为「登录用户名_连接时间.txt」，"
        "例如 admin_2026-11-13_19-50-01.txt。",
        "每个文件头部记录 登录用户 / 来源IP / 主机名称 / 目标地址 / 连接时间；"
        "正文「终端实录」把该会话里终端上打印过的全部内容（命令回显、输出、报错、提示符）"
        "1:1 转录为纯文本；穿插的「# [事件]」行是带时间戳的结构化操作标注；"
        "文件末尾记录 断开时间 / 持续时长 / 终端实录行数 / 事件条数。",
        "当前共 %d 个会话窗口文件、累计 %d 条结构化事件；下面按会话分组摘录最近 %d 条（已脱敏）："
        % (len(sessions), total_all, len(picked)),
        "---- 结构化事件记录开始 ----",
    ]
    for fn in picked_files:
        meta = metas.get(fn, {})
        lines.append("【会话文件 %s ｜ 用户 %s ｜ IP %s ｜ 主机 %s ｜ 目标 %s ｜ 连接 %s ｜ 断开 %s ｜ 状态 %s】"
                     % (fn, meta.get("user", "-"), meta.get("ip", "-"),
                        meta.get("host", "-"), meta.get("target", "-"),
                        meta.get("start", "-"), meta.get("end") or "进行中",
                        meta.get("status", "-")))
        for e in picked:
            if e["file"] != fn:
                continue
            lines.append("%s | user=%s | ip=%s | host=%s | target=%s | type=%s | source=%s | cmd=%s"
                         % (e["time"], e["user"], e["ip"], e["host"], e["target"],
                            e["type"], e["source"], e["cmd"]))
    if not picked:
        lines.append("（会话文档中暂无匹配的记录）")
    lines.append("---- 结构化事件记录结束 ----")

    # 终端实录摘录：让 AI 能回答「执行后输出了什么 / 当时屏幕上是什么」
    kws = _keywords(prompt)
    budget = 12000
    lines.append("---- 终端实录摘录（1:1 原文，用于核对命令输出与屏幕内容）----")
    recent = [s["file"] for s in sessions[:3]]
    for fn in recent:
        if budget <= 0:
            break
        fp = os.path.join(SESSION_DIR, fn)
        tr = read_transcript(fp)
        if not tr:
            continue
        if kws:
            idx = [i for i, ln in enumerate(tr) if any(k in ln.lower() for k in kws)]
            if idx:
                want = set()
                for i in idx[:20]:
                    for j in range(max(0, i - 2), min(len(tr), i + 3)):
                        want.add(j)
                sel = sorted(want)[:80]
                note = "按问题关键词定位"
            else:
                sel = list(range(max(0, len(tr) - 60), len(tr)))
                note = "关键词无命中，取末尾"
        else:
            sel = list(range(max(0, len(tr) - 60), len(tr)))
            note = "取末尾"
        if not sel:
            continue
        excerpt = "\n".join(tr[i] for i in sel)[:min(4000, budget)]
        budget -= len(excerpt)
        lines.append("【终端实录 · %s ｜ 共 %d 行 ｜ %s，以下为第 %d–%d 行】"
                     % (fn, len(tr), note, sel[0] + 1, sel[-1] + 1))
        lines.append(excerpt)
    if budget == 12000:
        lines.append("（会话文档中暂无终端实录内容）")
    lines += [
        "---- 终端实录摘录结束 ----",
        "回答规则：只能依据上述真实记录回答「哪个用户、什么时间、在哪台主机、做了什么操作、"
        "命令输出了什么」；结构化事件是操作索引，终端实录是命令与输出的 1:1 原文，两者互相印证；"
        "记录里没有的人或事必须明确说「审计文档中未找到」，严禁编造、推测或把示例当事实；"
        "引用时间、命令、输出、主机名、文件名时逐字照抄；可以统计次数、归纳风险并给出建议。",
    ]
    return "\n".join(lines)
