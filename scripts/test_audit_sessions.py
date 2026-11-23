# -*- coding: utf-8 -*-
"""会话窗口审计：隔离环境自测（不碰用户真实 audit 目录）。

覆盖：终端内容 1:1 实录、原始字节流、连接瞬间数据不丢、会话窗口文件生成 /
命名 / 摘要 / 检索 / 统计 / AI 上下文、异常终止兜底。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend import audit  # noqa: E402

tmp = tempfile.mkdtemp(prefix="aoc-audit-test-")
audit.AUDIT_DIR = tmp
audit.SESSION_DIR = os.path.join(tmp, "sessions")
audit.LEGACY_LOG = os.path.join(tmp, "command-audit.log")
audit._sessions.clear()
audit._lines.clear()
audit._pending.clear()
audit._closed_sids.clear()
audit._initialized = False

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("[OK]   " + name)
    else:
        fail += 1
        print("[FAIL] " + name + (" " + str(extra) if extra else ""))


def feed(sid, text, raw=None):
    """模拟 SSH reader 线程推来一个字节块。"""
    b = raw if raw is not None else text.encode("utf-8")
    return audit.feed_output(sid, b, text)


ctx = {"user": "admin", "ip": "127.0.0.1", "host": "阿里云主机",
       "target": "root@203.0.113.20:2222", "sid": "s1"}
ctx_win = dict(ctx, cols=80, rows=8)

# ---- 连接瞬间（open_session 之前）就到达的数据不能被丢掉 ----
early = b"\x1b[?1034hWelcome to Baidu Cloud\r\n"
feed("s1", early.decode(), early)

# ---- 会话 1：连接 → 终端全量输出 → 敲命令 → 断开 ----
p1 = audit.open_session("s1", ctx_win)
check("连接时立即生成会话文件", bool(p1) and os.path.isfile(p1))
check("文件名 = 用户名_连接时间.txt",
      os.path.basename(p1).startswith("admin_") and
      os.path.basename(p1).endswith(".txt") and
      len(os.path.basename(p1)) > 20, os.path.basename(p1))

audit.write_event(ctx, "建立 SSH 终端连接", "CONNECT")
feed("s1", "\x1b[32m[root@king ~]#\x1b[0m ")
feed("s1", "cat /etc/hosts\r\n127.0.0.1   localhost localhost.localdomain\r\n")
feed("s1", "\x1b[31mPermission denied\x1b[0m\r\n")
feed("s1", "[root@king ~]# mysql -uroot -pSecret999 -e 'select 1'\r\n")
feed("s1", "[root@king ~]# ")                 # 最后一个提示符，断开时 flush 才吐出
check("终端输出实时落盘（会话未结束就能读到内容）",
      "cat /etc/hosts" in open(p1, "r", encoding="utf-8").read())

c = audit.feed_keys("s1", "lsblk\r", ctx)
check("逐字符输入还原成命令 lsblk", c == ["lsblk"], c)
c = audit.feed_keys("s1", "df -hX\x7f\r", ctx)
check("退格键正确修正命令 df -h", c == ["df -h"], c)
c = audit.feed_keys("s1", 'mysql -uroot -pSecret123 -e "select 1"\r', ctx)
check("口令脱敏不写明文", c and "Secret123" not in c[0] and "***" in c[0], c)
audit.write_event(dict(ctx, sid="-"), "下载远端文件 /etc/hosts（1024 字节）", "DOWNLOAD", "sftp")
audit.write_event(dict(ctx, sid="-"), "发起 AI 诊断：检查磁盘", "DIAGNOSE", "ai")
audit.write_event(ctx, "关闭 SSH 终端连接", "DISCONNECT")
audit.close_session("s1", "正常断开")

t1 = open(p1, "r", encoding="utf-8").read()
check("文件头含用户/IP/主机/连接时间",
      all(x in t1 for x in ("# user         : admin", "# ip           : 127.0.0.1",
                            "# host         : 阿里云主机", "# connected_at")))
check("正文含终端命令 lsblk / df -h", "cmd=lsblk" in t1 and "cmd=df -h" in t1)
check("正文含未关联 sid 的下载与 AI 诊断事件",
      "/etc/hosts" in t1 and "type=DIAGNOSE" in t1)
check("断开后写入会话结束摘要（时间/时长/条数/状态）",
      "# 会话结束" in t1 and "# disconnected_at" in t1 and
      "# duration" in t1 and "# commands" in t1 and "正常断开" in t1)
check("会话 1 命令计数正确（CONNECT+3 CMD+DOWNLOAD+DIAGNOSE+DISCONNECT=7）",
      "# commands        : 7" in t1,
      [l for l in t1.splitlines() if "# commands" in l])

# ---- 终端实录 1:1 的核心断言 ----
check("连接前到达的登录横幅补写进文件，开头不丢",
      "Welcome to Baidu Cloud" in t1)
check("终端上打印的命令回显与输出 1:1 落到 txt",
      all(x in t1 for x in ("[root@king ~]# cat /etc/hosts",
                            "127.0.0.1   localhost localhost.localdomain",
                            "Permission denied", "# [事件]")), )
check("ANSI 颜色 / 光标控制码不写入可读文本",
      "\x1b[32m" not in t1 and "\x1b[31m" not in t1)
check("实录里的口令同样脱敏，不写明文",
      "Secret999" not in t1 and "-p***" in t1)
check("断开时 flush 最后一屏（未换行的提示符也留下）",
      t1.count("[root@king ~]#") >= 2)
check("终端实录有明确的起止分界",
      audit.BEGIN_MARK in t1 and audit.END_MARK in t1)
check("文件尾统计了实录行数 / 原始流字节数",
      "# terminal_lines" in t1 and "# raw_bytes" in t1 and
      "# terminal_chars" in t1)

tr = audit.read_transcript(p1)
check("read_transcript 只返回 1:1 实录行，不含文件头尾与事件行",
      tr and all(not ln.startswith("# [事件]") for ln in tr) and
      all(not ln.startswith("# ===") for ln in tr),
      tr[:5])
check("read_transcript 保持终端显示顺序",
      tr.index("127.0.0.1   localhost localhost.localdomain") <
      tr.index("Permission denied"), tr)

rp = audit._raw_path(p1)
check("同目录 raw/ 生成同名 .raw 原始流文件", os.path.isfile(rp))
raw = open(rp, "rb").read()
check(".raw 保留未经处理的 ANSI 与登录横幅原始字节",
      b"\x1b[32m" in raw and b"Welcome to Baidu Cloud" in raw)
check("文件尾 raw_bytes 与实际字节数一致",
      ("# raw_bytes       : %d" % len(raw)) in t1,
      [l for l in t1.splitlines() if "raw_bytes" in l])

# ---- 会话 2：不同用户，验证多文件并存 ----
ctx2 = dict(ctx, user="king", host="百度云主机", sid="s2")
p2 = audit.open_session("s2", dict(ctx2, cols=80, rows=8))
audit.write_event(ctx2, "建立 SSH 终端连接", "CONNECT")
feed("s2", "[king@bcc ~]# systemctl status nginx\r\nactive (running)\r\n")
audit.feed_keys("s2", "systemctl status nginx\r", ctx2)
audit.write_event(ctx2, "关闭 SSH 终端连接", "DISCONNECT")
audit.close_session("s2", "正常断开")
check("第二次连接生成独立文件且不与第一个重名", p2 != p1 and os.path.isfile(p2))
check("第二个文件名带用户名 king", os.path.basename(p2).startswith("king_"),
      os.path.basename(p2))

# ---- 无终端会话时的独立操作文件 ----
audit.write_event({"user": "admin", "ip": "10.0.0.1", "host": "-", "target": "-",
                   "sid": "-"}, "AI 问答：ls 怎么用", "AI_ASK", "ai")
check("无活跃会话时按用户+当天生成独立操作文件",
      any("独立操作" in os.path.basename(f) for f in audit._files()),
      [os.path.basename(f) for f in audit._files()])

# ---- 检索 / 清单 / 统计 / AI 上下文 ----
sessions = audit.list_sessions()
check("会话清单按连接时间倒序返回 3 个窗口", len(sessions) == 3,
      [(s["file"], s["user"], s["status"]) for s in sessions])
check("清单含用户/主机/开始结束/命令数/状态/实录行数",
      all(k in sessions[0] for k in ("file", "user", "ip", "host", "target",
                                     "start", "end", "duration", "commands",
                                     "terminal_lines", "status")))
check("已结束会话有断开时间与状态",
      any(s["user"] == "admin" and s["end"] != "" and "正常断开" in s["status"]
          for s in sessions), [(s["file"], s["end"], s["status"]) for s in sessions])

entries = audit.read_entries(limit=100)
check("跨会话检索能读到所有记录", len(entries) >= 10, len(entries))
check("每条记录带 用户/主机/会话文件名",
      all(e["user"] and e["host"] and e["file"] for e in entries),
      [(e["user"], e["host"], e["file"]) for e in entries[:3]])
check("按用户过滤生效",
      all("king" in e["user"] for e in audit.read_entries(user="king")),
      [e["user"] for e in audit.read_entries(user="king")])
check("按命令关键字过滤生效",
      any("nginx" in e["cmd"] for e in audit.read_entries(keyword="nginx")))
check("按类型过滤 CMD 生效",
      all(e["type"].upper() == "CMD" for e in audit.read_entries(ev_type="CMD")))
check("关键词检索能命中「终端实录原文」里的输出（不只是命令）",
      any(e["type"] == "OUT" and "Permission denied" in e["cmd"]
          for e in audit.read_entries(keyword="permission denied")),
      [e["cmd"] for e in audit.read_entries(keyword="permission denied")])

st = audit.stats()
check("统计返回目录路径/会话数/文件数/总记录数/实录行数",
      st["path"] == audit.SESSION_DIR and st["sessions"] == 3 and
      st["files"] == 3 and st["total"] >= 10 and st["terminal_lines"] > 0, st)

ai = audit.context_for_ai("审计：admin 今天执行过哪些命令？")
check("AI 审计上下文注入会话目录与文件分组",
      "audit" in ai and "会话文件" in ai and "唯一事实依据" in ai and
      "用户名_连接时间" in ai, ai[:200])
check("AI 上下文含真实命令且已脱敏",
      "lsblk" in ai and "Secret123" not in ai)
check("AI 上下文附带终端实录摘录（能回答输出了什么）",
      "终端实录摘录" in ai and "localhost" in ai and "终端实录 ·" in ai)
check("非审计问题不注入上下文（零 token 开销）",
      audit.context_for_ai("vim 怎么只读打开文件") == "")

# ---- 前端本地提示也归入同一个窗口 ----
p6 = audit.open_session("s6", dict(ctx, cols=80, rows=8))
audit.write_notice("s6", "正在连接 root@203.0.113.20:2222 ...", ctx)
audit.close_session("s6", "正常断开")
check("前端本地提示写进终端实录",
      "正在连接 root@203.0.113.20:2222" in
      "\n".join(audit.read_transcript(p6)))

# ---- 进程重启：孤儿会话自动补「异常终止」 ----
p3 = audit.open_session("s3", dict(ctx, cols=80, rows=8))
audit.write_event(ctx, "建立 SSH 终端连接", "CONNECT")
feed("s3", "[root@king ~]# uptime\r\n 20:30:01 up 10 days\r\n")
audit.feed_keys("s3", "uptime\r", ctx)
audit._initialized = False
audit._sessions.clear()
audit._lines.clear()
audit.ensure()
t3 = open(p3, "r", encoding="utf-8").read()
check("服务重启后未断开的窗口被补写异常终止",
      "# 会话结束" in t3 and "异常终止" in t3)

# ---- 同秒重连不覆盖 ----
audit._initialized = True
p4 = audit.open_session("s4", ctx)
p5 = audit.open_session("s5", ctx)
check("同秒重连自动加序号，不覆盖已有文件", p4 != p5 and os.path.isfile(p4) and os.path.isfile(p5))
check("会话已封存后迟到的输出不会串进别的文件",
      audit.feed_output("s1", b"late", "late") == [] and
      "late" not in open(p1, "r", encoding="utf-8").read())

# ---- 端到端：真实走 SSH reader 线程 → 审计回调 → 会话窗口文件 ----
from backend import app as webapp  # noqa: E402  导入即注册 manager.set_data_sink
from backend import ssh_manager  # noqa: E402

check("应用启动时已把终端输出回调挂到 SSH reader",
      ssh_manager.manager._data_sink is audit.feed_output)


class FakeChan:
    def __init__(self, chunks):
        self.chunks, self.closed = list(chunks), False

    def settimeout(self, _): pass
    def recv_ready(self): return bool(self.chunks)
    def recv(self, _): return self.chunks.pop(0) if self.chunks else b""
    def exit_status_ready(self): return self.closed
    def close(self): self.closed = True
    def send(self, d): return len(d)
    def resize_pty(self, **_): pass


class FakeClient:
    def __init__(self, chan): self.chan = chan
    def invoke_shell(self, **_): return self.chan


chunks = [b"\x1b[?1034hLast login: Fri Nov 13 20:30:01 2026\r\n",
          b"[root@king ~]# df -h\r\nFilesystem  Size  Used\r\n",
          "[root@king ~]# 中文目录\r\n".encode("utf-8")]
chan = FakeChan(chunks)
orig_connect = ssh_manager.manager.connect
ssh_manager.manager.connect = lambda _hid: FakeClient(chan)
try:
    sid_e2e = ssh_manager.manager.open_shell("fake-host", cols=80, rows=8)
    # 故意晚一点 open_session，模拟「reader 先吐数据、会话文件后建」
    for _ in range(50):
        if not chan.chunks:
            break
        time.sleep(0.02)
    pe = audit.open_session(sid_e2e, dict(ctx, cols=80, rows=8, sid=sid_e2e))
    ssh_manager.manager.close_shell(sid_e2e)   # reader 把断开提示也送进审计
    audit.close_session(sid_e2e, "正常断开")
finally:
    ssh_manager.manager.connect = orig_connect

te = open(pe, "r", encoding="utf-8").read()
check("reader 线程的每一块输出都 1:1 落入会话文件（含连接瞬间的横幅）",
      all(x in te for x in ("Last login: Fri Nov 13", "[root@king ~]# df -h",
                            "Filesystem  Size  Used", "中文目录", "连接已断开")), )
check("晚建会话也不丢数据：pending 缓冲已清空",
      sid_e2e not in audit._pending)
check("reader 断开提示同样写进实录", "连接已断开" in te)
re_ = open(audit._raw_path(pe), "rb").read()
check("端到端 .raw 保留原始 ANSI 字节", b"\x1b[?1034h" in re_)

print("\n---- 会话文件示例（前 2200 字符）----")
print(t1[:2200])
print("\n结果: %d 通过 / %d 失败" % (ok, fail))
sys.exit(1 if fail else 0)
