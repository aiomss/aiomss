# -*- coding: utf-8 -*-
"""本地自测：畸形 AI 回复容错、命令安全策略、终端原始 VT 流与控制字符透传。

用法（在项目根目录）：python scripts/selftest.py
不连接任何真实主机、不调用 AI API。
"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import safety, ssh_manager          # noqa: E402
from backend.ai_client import _json_from_text, humanize_raw   # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  -> " + str(extra) if extra else ""))


print("=== 1. AI 畸形回复容错 ===")
# 截图中的真实样本：缺开头花括号、键名被写成 [thought]、末尾被截断
real_bad = ('"[thought]":"free -h 已能给出内存总量、已用、可用等信息，无需再执行命令，可直接给出结论。",'
            '"command":"","done":true,"summary":"问题现象：用户想了解这台主机的内存总量与剩余量。\n'
            '诊断过程（free -h 输出）：\n· 物理内存 total: 1.9Gi\n· 已使用 used: 952Mi\n'
            '· 可用内存 available: 1.0Gi\nSwap: 0B（未启用交换')
r = _json_from_text(real_bad)
check("截图形态可解析出结论", bool(r) and "1.9Gi" in (r or {}).get("summary", ""), r)
check("截图形态 done=true", bool(r) and r.get("done") is True, r)

cases = [
    ('{"thought":"t","command":"free -h","done":false,"summary":""}', "free -h", False),
    ('```json\n{"thought":"t","command":"df -h","done":true,"summary":"完成"}\n```', "df -h", True),
    ('这是说明：{"thought":"t","command":"uptime","done":false,"summary":""}', "uptime", False),
    ('{"thought":"t","command":"ls","done":false,"summary":""', "ls", False),   # 末尾被截断
    ('{"thought":"t","command":"id","done":false,"summary":"",}', "id", False),  # 尾逗号
    ("{'thought':'t','command':'whoami','done':false,'summary':''}", "whoami", False),
    ('{"thought":"t","command":"date","done":True,"summary":""}', "date", True),
    ('"[command]":"hostname","done":true,"summary":"主机正常"', "hostname", True),
]
for text, cmd, done in cases:
    out = _json_from_text(text)
    check("解析 %s" % cmd, bool(out) and out.get("command") == cmd and bool(out.get("done")) == done, out)

check("纯文本不会误判为结构", _json_from_text("你好，请稍等") is None)
h = humanize_raw(real_bad)
check("清洗后无 JSON 符号", "[thought]" not in h and '【结论】' in h, h[:120])
check("清洗保留换行结论", "1.9Gi" in h, h[:200])

print("=== 2. 命令安全策略回归 ===")
safe_cases = [
    ("cat /etc/passwd", "read"),
    ("grep -E 'error|fail' /var/log/messages | tail -50", "read"),
    ("awk '$5>80 {print}'", "read"),
    ("echo '---inode---'", "read"),
    ("df -hT", "read"),
]
for cmd, expect in safe_cases:
    lvl, _ = safety.classify(cmd)
    check("只读: " + cmd, lvl == expect, lvl)
for cmd in ("rm -rf /tmp/x", "systemctl restart sshd", "sed -i 's/a/b/' /etc/fstab"):
    lvl, reasons = safety.classify(cmd)
    check("非只读: " + cmd, lvl != "read", (lvl, reasons))

print("=== 3. 交互式终端原始 VT 流 / 控制字符透传 ===")


class FakeChan:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []
        self.resized = None
        self.closed = False

    def settimeout(self, sec):
        pass

    def recv_ready(self):
        return bool(self.chunks)

    def recv(self, n):
        return self.chunks.pop(0)

    def exit_status_ready(self):
        return False   # 保持会话存活，便于验证输入 / 尺寸同步

    def send(self, data):
        self.sent.append(data)
        return len(data)

    def resize_pty(self, width, height):
        self.resized = (width, height)

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, chan):
        self.chan = chan

    def invoke_shell(self, **kw):
        return self.chan


# 含清屏/光标控制序列，且故意把一个汉字拆成两个 TCP 包
han = "中".encode("utf-8")
chan = FakeChan([
    b"\x1b[2J\x1b[H",
    b"root@host:~# vim a.txt\r\n",
    han[:1], han[1:],
    b"\x1b[38;5;196mERROR\x1b[0m\r\n",
])
mgr = ssh_manager.SSHManager()
mgr.connect = lambda hid: FakeClient(chan)      # 不连真实主机
sid = mgr.open_shell("fake", cols=100, rows=30)
time.sleep(0.25)
info = mgr._shells[sid]
got = []
while not info["q"].empty():
    got.append(info["q"].get())
joined = "".join(got)
check("ANSI 控制序列未被剥离", "\x1b[2J" in joined and "\x1b[38;5;196m" in joined,
      repr(joined[:80]))
check("跨包汉字增量解码正确", "中" in joined and "\ufffd" not in joined, repr(joined[:120]))
mgr.shell_input(sid, "\t")
mgr.shell_input(sid, "\x1b[A")
mgr.shell_input(sid, "\x03")
check("Tab 补全字节原样送达 PTY", "\t" in chan.sent, chan.sent)
check("方向键 / Ctrl+C 原样送达", "\x1b[A" in chan.sent and "\x03" in chan.sent, chan.sent)
mgr.shell_resize(sid, 140, 42)
check("窗口尺寸同步 PTY", chan.resized == (140, 42), chan.resized)
mgr.close_shell(sid)
check("会话可正常关闭", chan.closed)

print("=== 4. AI 命令输出剥离颜色码 ===")
check("strip_ansi 清理颜色", ssh_manager.strip_ansi("\x1b[31mERROR\x1b[0m") == "ERROR")

print("=== 5. Agent 对畸形回复的友好降级 ===")
from backend import agent, store               # noqa: E402

_orig_profile, _orig_host, _orig_ai = store.get_profile, store.get_host, agent.AIClient
store.get_profile = lambda *a, **k: {"api_key": "fake", "default_model": "fake"}
store.get_host = lambda *a, **k: {"name": "测试机", "username": "root",
                                  "host": "127.0.0.1", "port": 22}


class FakeAI:
    def __init__(self, *a, **k):
        pass

    def chat_json(self, messages, max_tokens=2048):
        return None, "", real_bad


agent.AIClient = FakeAI
evts = list(agent.run("fake", "内存多大，剩余多少？"))
texts = [e.get("text", "") for e in evts]
check("不再抛原始 JSON 错误", not any("AI 未返回合法 JSON" in t for t in texts))
check("发出友好提示卡片", any(e.get("type") == "notice" and e.get("level") == "warn"
                          and "格式" in e.get("title", "") for e in evts))
check("AI 结论仍完整呈现", any(e.get("type") == "summary" and "1.9Gi" in e.get("text", "")
                          for e in evts))


class RecoveredAI(FakeAI):
    def chat_json(self, messages, max_tokens=2048):
        return _json_from_text(real_bad), "", real_bad


agent.AIClient = RecoveredAI
evts2 = list(agent.run("fake", "内存多大，剩余多少？"))
check("自动修复后给出结论", any(e.get("type") == "summary" and "1.9Gi" in e.get("text", "")
                          for e in evts2))
# v1.2 起「内部自动修复」属于正常路径，静默处理、不弹提示打扰用户
# （第 9 节也用断言固化了这一契约）。此处同步为验证不出现告警级提示。
check("内部自动修复静默完成（不弹告警提示）",
      not any(e.get("type") == "notice" and e.get("level") in ("warn", "error")
              for e in evts2), [e.get("title") for e in evts2 if e.get("type") == "notice"])
agent.AIClient, store.get_profile, store.get_host = _orig_ai, _orig_profile, _orig_host

print("=== 6. 登录鉴权 / 图片验证码 / 会话 ===")
from backend import auth                       # noqa: E402

cfg = auth.auth_config()
check("账号配置自动生成", bool(cfg.get("username")) and bool(cfg.get("salt"))
      and bool(cfg.get("password_hash")))
check("明文密码不落盘", "13660888023" not in json.dumps(store.load().get("auth", {})))
check("固定账号密码可验证", auth.verify_credentials("admin", "13660888023"))
check("错误密码被拒绝", not auth.verify_credentials("admin", "wrong-password"))
check("错误用户名被拒绝", not auth.verify_credentials("root", "13660888023"))

cid, code, png = auth.make_captcha()
check("验证码是真正的 PNG 图片", png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000, len(png))
check("验证码为 4 位字符", len(code) == 4 and code.isalnum(), code)
check("验证码不区分大小写", auth.check_captcha(cid, code.lower())[0])
check("验证码一次性（复用被拒）", not auth.check_captcha(cid, code)[0])
cid2, _code2, _ = auth.make_captcha()
check("错误验证码被拒绝", not auth.check_captcha(cid2, "!!!!")[0])

token = auth.issue_session("admin", "127.0.0.1")
check("会话可验证", auth.session_user(token) == "admin")
auth.drop_session(token)
check("注销后会话失效", auth.check_session(token) is None)

test_ip = "selftest-" + str(time.time())
check("初始未锁定", not auth.is_locked(test_ip)[0])
for _ in range(auth.MAX_FAILS):
    auth.record_fail(test_ip)
check("连续失败会锁定", auth.is_locked(test_ip)[0])
auth.clear_fail(test_ip)
check("锁定可清除", not auth.is_locked(test_ip)[0])

print("=== 7. 心跳与审批超时（不会无限挂起） ===")
ev_open = threading.Event()
hbs = list(agent._heartbeat_wait(ev_open, "测试等待", timeout=0.75, interval=0.2))
check("等待期间持续产出心跳", len(hbs) >= 2, len(hbs))
check("心跳带 elapsed / remaining", all("elapsed" in h and "remaining" in h for h in hbs))
ev_done = threading.Event()
ev_done.set()
check("事件到达立即停止", list(agent._heartbeat_wait(ev_done, "x", 5, 0.2)) == [])

_orig_timeout = agent.CONFIRM_TIMEOUT
agent.CONFIRM_TIMEOUT = 2.5     # 测试用短超时，生产为 120 秒
store.get_profile = lambda *a, **k: {"api_key": "fake", "default_model": "fake"}
store.get_host = lambda *a, **k: {"name": "测试机", "username": "root",
                                  "host": "127.0.0.1", "port": 22}


class DangerAI(FakeAI):
    def __init__(self, *a, **k):
        self.n = 0

    def chat_json(self, messages, max_tokens=2048):
        self.n += 1
        if self.n == 1:
            return ({"thought": "需要创建 swap 文件", "command": "fallocate -l 1G /swapfile",
                     "done": False, "summary": ""}, "", "")
        return ({"thought": "", "command": "", "done": True,
                 "summary": "【现象】命令未获批准\n【建议】请先确认后再执行"}, "", "")


agent.AIClient = DangerAI
t0 = time.time()
evts3 = list(agent.run("fake", "帮我加 1G swap", read_only=False,
                       auto_approve=False, max_retry=3))
elapsed = time.time() - t0
check("危险命令推给前端审批", any(e.get("type") == "confirm_required" for e in evts3))
check("审批等待期间有心跳", any(e.get("type") == "heartbeat" for e in evts3))
check("超时自动拒绝而非挂死", any(e.get("type") == "confirm_timeout" for e in evts3)
      and elapsed < 8, round(elapsed, 2))
check("未获批准时命令不执行", not any(e.get("type") == "step_update"
                                 and e.get("status") == "done" for e in evts3))
check("超时后仍给出结论", any(e.get("type") == "summary" for e in evts3))
check("流程以 done 正常收尾", bool(evts3) and evts3[-1].get("type") == "done")
agent.CONFIRM_TIMEOUT = _orig_timeout
agent.AIClient, store.get_profile, store.get_host = _orig_ai, _orig_profile, _orig_host

print("=== 8. 查看类命令不再误判为写操作 ===")
for cmd in ("swapon --show", "free -m; echo '---'; swapon --show; echo '---'; df -h /",
            "sysctl -a | grep swap"):
    lvl, reasons = safety.classify(cmd)
    check("只读: " + cmd[:36], lvl == "read", (lvl, reasons))
for cmd in ("fallocate -l 1G /swapfile", "mkswap /swapfile", "swapon /swapfile"):
    lvl, reasons = safety.classify(cmd)
    check("写操作: " + cmd, lvl != "read" and bool(reasons), (lvl, reasons))

print("=== 8.5 自动批准不应放行「未识别」命令（不确定的必须让用户确认） ===")
# 管道里的裸命令（sort/uniq/tail 无参数）此前被误判成「未识别命令」，现应识别为只读
for cmd in ("grep 'Failed password' /var/log/secure | awk '{print $1,$2}' | sort | uniq -c | tail -10",
            "sort", "uniq -c", "tail -10"):
    lvl, reasons = safety.classify(cmd)
    check("裸只读命令不再误判: " + cmd[:40], lvl == "read", (lvl, reasons))
# 未识别命令：即便勾选自动批准，也必须弹出确认让用户选择
allowed, need, lvl, reasons = safety.check("mysterytool --flag /x", read_only=False, auto_approve=True)
check("未识别命令即使自动批准也需确认", allowed and need is True, (allowed, need, lvl, reasons))
# 已明确判定的危险命令：自动批准则直接放行（无需弹窗）
allowed2, need2, _, _ = safety.check("rm -rf /tmp/x", read_only=False, auto_approve=True)
check("明确危险命令自动批准可放行", allowed2 and need2 is False, need2)
# 只读命令：无需确认
_, need3, _, _ = safety.check("df -hT", read_only=True, auto_approve=False)
check("只读命令无需确认", need3 is False, need3)
# 只读模式下未识别命令仍被拦截
allowed4, _, _, _ = safety.check("mysterytool --flag", read_only=True, auto_approve=True)
check("只读模式拦截未识别命令", allowed4 is False, allowed4)

print("=== 9. v1.2 回归：命令误判 / 结论截断 / 静默容错 / 阶段结论 ===")
from backend.ai_client import complete_summary, summary_looks_truncated  # noqa: E402

# 用户截图里 `docker ps --format` 曾被宽泛的 \bformat\b（格式化）误判成写命令
for cmd in (r"docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'",
            "docker ps --format '{{.Names}}'", "df -hT", "lsblk -f"):
    lvl, reasons = safety.classify(cmd)
    check("只读不再误判: " + cmd[:44], lvl == "read", (lvl, reasons))
for cmd in ("mkfs.ext4 /dev/vdb1", "format c:", "rm -rf /tmp/x"):
    lvl, reasons = safety.classify(cmd)
    check("危险命令仍拦截: " + cmd, lvl != "read" and bool(reasons), (lvl, reasons))

check("半句「该机属于」判定为截断", summary_looks_truncated("该机属于") is True)
check("完整句不判为截断",
      summary_looks_truncated("【现象】主机为单系统盘实例，当前运行约 207 个进程。") is False)
check("空结论判为截断", summary_looks_truncated("") is True)
check("逗号结尾判为截断", summary_looks_truncated("因此建议先归档日志，") is True)


class ContAI:
    def chat_text(self, messages, max_tokens=2048):
        return "一台单系统盘的小规格实例，建议归档旧日志。", ""


class FailAI:
    def chat_text(self, *a, **k):
        raise RuntimeError("模拟续写失败")


check("结论续写补完整", "一台单系统盘" in complete_summary(ContAI(), "该机属于"))
check("续写失败时原样返回", complete_summary(FailAI(), "该机属于") == "该机属于")
check("半句结论触发补全", "一台单系统盘" in agent._final_summary(ContAI(), "该机属于"))
check("完整结论不重复调用 AI",
      agent._final_summary(FailAI(), "已完成排查，结论正常。") == "已完成排查，结论正常。")

# --- 边界补充：长度 / 收尾标点 / 续写调用次数 ---
long_no_punct = ("本次排查共检查了 CPU 负载、内存占用、磁盘空间、网络连接与系统日志等五类指标，"
                 "各项数值均在正常阈值范围内，未发现异常进程、异常登录或错误日志，"
                 "内核参数与文件系统挂载状态也符合预期，系统整体运行平稳，"
                 "暂不需要执行任何变更操作，建议保持现有监控与告警策略并定期复查上述指标，"
                 "后续如出现指标持续攀升再按预案扩容或进一步定位")
check("长文本无句末标点不判截断",
      len(long_no_punct) >= 120 and summary_looks_truncated(long_no_punct) is False,
      len(long_no_punct))
check("以冒号结尾判截断", summary_looks_truncated("【建议】请先检查以下内容：") is True)
check("以全角右括号结尾不判截断", summary_looks_truncated("状态正常（CPU 12%）") is False)
check("以句号结尾不判截断", summary_looks_truncated("排查已完成。") is False)
check("以省略号结尾判截断", summary_looks_truncated("结论如下…") is True)


class CountingAI:
    """统计 chat_text 调用次数，用于验证完整结论不会重复发起 AI 续写。"""

    def __init__(self):
        self.calls = 0

    def chat_text(self, *a, **k):
        self.calls += 1
        return "续写内容。", ""


_ok_short = "已完成排查，结论正常。"
_cai_full = CountingAI()
check("完整短结论不重复调用 AI",
      agent._final_summary(_cai_full, _ok_short) == _ok_short and _cai_full.calls == 0,
      _cai_full.calls)
_cai_half = CountingAI()
_half_out = agent._final_summary(_cai_half, "该机属于")
check("半句结论恰好触发一次续写",
      _cai_half.calls == 1 and "续写内容" in _half_out, (_cai_half.calls, _half_out))
_cai_empty = CountingAI()
check("空结论不调用续写 AI",
      complete_summary(_cai_empty, "") == "" and _cai_empty.calls == 0, _cai_empty.calls)

_orig_ai2 = agent.AIClient
_orig_profile2, _orig_host2 = store.get_profile, store.get_host
store.get_profile = lambda *a, **k: {"api_key": "fake", "default_model": "fake"}
store.get_host = lambda *a, **k: {"name": "测试机", "username": "root",
                                  "host": "127.0.0.1", "port": 22}


class _StubAI:
    """AIClient 的最小测试替身接口：构造、截断标志、同步/流式调用。

    agent.run() / agent.ask() 都以 AIClient(profile, model=model) 构造实例，
    故替身必须接受任意位置/关键字参数；同时补齐 truncated 与各 chat_* 方法，
    避免后续流程因缺属性抛 AttributeError。生产逻辑一行未改。
    """
    truncated = False

    def __init__(self, *a, **k):
        pass

    def chat_json(self, messages, max_tokens=4096):
        return ({"thought": "", "command": "", "done": True, "summary": ""}, "", "")

    def chat_text(self, messages, max_tokens=2048):
        return "", ""

    def chat_stream(self, messages, max_tokens=6000):
        return iter(())


class RecoveredAI(_StubAI):
    def __init__(self, *a, **k):
        pass

    def chat_json(self, messages, max_tokens=4096):
        return ({"thought": "判断完成", "command": "", "done": True,
                 "summary": "【现象】磁盘使用率 38%，暂无告警。\n【建议】保持观察即可。",
                 "_recovered": True}, "", "")


agent.AIClient = RecoveredAI
evts = list(agent.run("fake", "测试", read_only=True, max_retry=1))
check("内部自动修复不弹提示给用户",
      not any(e.get("type") == "notice" and "自动修复" in (e.get("title") or "")
              for e in evts), [e.get("title") for e in evts if e.get("type") == "notice"])
check("正常结论仍完整呈现",
      any(e.get("type") == "summary" and "38%" in e.get("text", "") for e in evts))


class SteppingAI(_StubAI):
    def __init__(self, *a, **k):
        pass

    def chat_json(self, messages, max_tokens=4096):
        return ({"thought": "继续收集", "command": "", "done": False, "summary": ""}, "", "")


agent.AIClient = SteppingAI
evts2 = list(agent.run("fake", "测试", read_only=True, max_retry=1))
sm = [e for e in evts2 if e.get("type") == "summary"]
check("跑满步数给出阶段结论（不是「请缩小范围」）", bool(sm)
      and "阶段结论" in sm[-1].get("title", ""), [e.get("title") for e in sm])
check("阶段结论解释含义与后续动作", bool(sm)
      and "汇总现有证据" in sm[-1].get("text", "") and "最大重试" not in sm[-1].get("text", ""),
      sm[-1].get("text", "")[:120] if sm else "")


class TruncAskAI(_StubAI):
    truncated = True

    def __init__(self, *a, **k):
        pass

    def chat_stream(self, messages, max_tokens=6000):
        yield "vim -R 表示只读打开"


agent.AIClient = TruncAskAI
evts3 = list(agent.ask("vim -R 怎么用", "fake"))
types3 = [e.get("type") for e in evts3]
check("问答截断给出友好提示", any(e.get("type") == "notice"
      and "未完整生成" in (e.get("title") or "") for e in evts3),
      [e.get("title") for e in evts3 if e.get("type") == "notice"])
check("截断时仍以 answer_done/done 正常收尾",
      "answer_done" in types3 and bool(types3) and types3[-1] == "done", types3)

agent.AIClient, store.get_profile, store.get_host = _orig_ai2, _orig_profile2, _orig_host2

print()
print("结果: %d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
