#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L10: interactive service console - command parsing + state machine.

Runs fully in-memory with fake processes/probes. Never binds port 8848 and
never launches a real service, so it is safe to run alongside other layers.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import console  # noqa: E402

PASS, FAIL, FAILURES = 0, 0, []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(name)
        print("  FAIL %s | %s" % (name, extra))


class FakeProc:
    """Minimal Popen-like object; `alive` list lets tests simulate crash."""

    def __init__(self, pid=1234, alive=None):
        self.pid = pid
        self._alive = alive if alive is not None else [True]
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return None if self._alive[0] else 0

    def terminate(self):
        self.terminated = True
        self._alive[0] = False

    def kill(self):
        self.killed = True
        self._alive[0] = False

    def wait(self, timeout=None):
        self.waited = True
        return 0


class Harness:
    """Deterministic environment: port state is controlled by a list."""

    def __init__(self, port_states=None):
        self.procs = []
        self.states = list(port_states or [False])
        self.spawn_calls = []
        self.sleeps = []

    def spawn(self, cmd, cwd):
        self.spawn_calls.append((cmd, cwd))
        p = FakeProc(pid=1000 + len(self.procs))
        self.procs.append(p)
        return p

    def probe(self):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def sleep(self, _secs):
        self.sleeps.append(_secs)

    def controller(self):
        return console.ServiceController(
            host="127.0.0.1", port=8848, python="PY",
            spawn=self.spawn, probe=self.probe, sleep=self.sleep)


print("=== 1. 命令解析（别名 -> 标准动作）===")
for raw, want in (
        ("r", "reload"), ("reload", "reload"), ("RELOAD", "reload"),
        ("restart", "reload"), ("x", "stop"), ("stop", "stop"),
        ("disconnect", "stop"), ("d", "stop"), ("b", "start"),
        ("s", "start"), ("start", "start"), ("o", "open"),
        ("st", "status"), ("status", "status"), ("h", "help"),
        ("?", "help"), ("q", "quit"), ("exit", "quit"),
        ("   r  ", "reload")):
    check("解析 %r -> %s" % (raw, want), console.parse_command(raw) == want,
          console.parse_command(raw))
check("空输入 -> 空动作（不误触发）", console.parse_command("   ") == "")
check("未知输入 -> unknown", console.parse_command("foobar") == "unknown")
check("None 输入 -> 空动作", console.parse_command(None) == "")

print("=== 2. 地址与端口探测 ===")
check("0.0.0.0 浏览地址归一为 127.0.0.1",
      console.display_host("0.0.0.0") == "127.0.0.1")
check("普通 host 原样返回", console.display_host("192.168.1.9") == "192.168.1.9")
check("空 host 归一为回环", console.display_host("") == "127.0.0.1")
check("unused-port 探测为未占用",
      console.port_open("127.0.0.1", 1) is False)

print("=== 3. 服务状态机：启动 / 重复启动 / 停止 ===")
h = Harness(port_states=[False, True])
c = h.controller()
check("初始为 STOPPED", "STOPPED" in c.status())
ok, msg = c.start()
check("启动成功", ok and "started" in msg, (ok, msg))
check("启动命令为 -m backend.app",
      h.spawn_calls[0][0] == ["PY", "-m", "backend.app"], h.spawn_calls[:1])
check("启动后为 RUNNING", c.running() and "RUNNING" in c.status())
check("URL 指向登录页", c.url == "http://127.0.0.1:8848")
ok, msg = c.start()
check("重复启动被拒绝且不再 fork", not ok and len(h.spawn_calls) == 1, (ok, msg))
ok, msg = c.stop()
check("停止成功且子进程被 terminate",
      ok and h.procs[0].terminated and h.procs[0].waited, (ok, msg))
check("停止后为 STOPPED 且 proc 释放",
      not c.running() and c.proc is None)
ok, msg = c.stop()
check("重复停止幂等不报错", not ok and "not running" in msg, (ok, msg))

print("=== 4. 端口占用保护（不抢别人的服务）===")
h = Harness(port_states=[True])
c = h.controller()
ok, msg = c.start()
check("端口被占用时拒绝启动", not ok and "occupied" in msg, (ok, msg))
check("端口被占用时没有 fork 子进程", len(h.spawn_calls) == 0)

print("=== 5. 重载 = 停止旧进程 + 启动新进程（窗口不关）===")
# start: probe free + wait ready; stop: wait release; start again: free + ready
h = Harness(port_states=[False, True, True, False, False, True])
c = h.controller()
assert c.start()[0]
old = h.procs[0]
ok, msg = c.reload()
check("重载成功", ok and "started" in msg, (ok, msg))
check("旧进程被停止", old.terminated)
check("重载产生新进程（新旧 PID 不同）",
      len(h.procs) == 2 and h.procs[1].pid != old.pid,
      [p.pid for p in h.procs])
check("重载后 RUNNING", c.running())

print("=== 6. 子进程启动即退出时不误报成功 ===")
h = Harness(port_states=[False])
# spawn a process that is already dead on first poll (immediate crash)
h.spawn = lambda cmd, cwd: FakeProc(alive=[False])
c = h.controller()
ok, msg = c.start()
check("进程闪退时报告失败", not ok and "exited" in msg, (ok, msg))
check("闪退后状态回到 STOPPED", not c.running() and c.proc is None)

print("=== 7. 交互循环：命令驱动 + EOF 优雅退出 ===")
h = Harness(port_states=[False, True, True, True, True, True])
c = h.controller()
answers = iter(["h", "st", "unknown-cmd", "x", "b", "q"])
lines = []


def fake_ask(_prompt):
    return next(answers)


def fake_say(msg):
    lines.append(msg)


code = console.run(c, fake_ask, fake_say)
joined = "\n".join(lines)
check("run 正常退出返回 0", code == 0, code)
check("启动即打印 help 提示", "Type 'h' for commands" in joined)
check("未知命令给出提示", "Unknown command" in joined)
check("帮助命令输出命令清单", "Commands:" in joined and "reload" in joined)
check("status 命令输出状态", "Status :" in joined)
check("quit 后打印 Bye", "Bye" in joined)
check("EOF 视为 quit（不崩溃）", code == 0)

h2 = Harness(port_states=[False, True, True])
c2 = h2.controller()
it = iter(["q"])
code2 = console.run(c2, lambda _p: next(it), lambda _m: None)
check("quit 时自动停止仍在运行的服务", code2 == 0 and not c2.running())

print("=== 8. 依赖注入：不会触碰真实端口/进程 ===")
check("Harness 全程未调用真实 subprocess",
      all(isinstance(p, FakeProc) for p in h.procs))

print()
print("结果: %d 通过 / %d 失败" % (PASS, FAIL))
if FAILURES:
    print("失败项: " + " | ".join(FAILURES))
sys.exit(1 if FAIL else 0)
