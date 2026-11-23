#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI Ops Console - interactive service console.

The web service runs in a child process while this window keeps a command
menu, so the service can be reloaded or stopped WITHOUT closing the window.

Commands (ASCII only, safe for Windows cmd):
    r / reload           reload service (stop + start)
    x / stop / disconnect stop service, keep this window
    b / start            start service again
    o / open             open the login page in a browser
    st / status          show current status
    h / help             show the command list
    q / quit             stop service and exit the console

All output is ASCII to avoid Windows cmd code-page problems. All I/O is
injectable (spawn / probe / sleep / ask / say) so the logic can be tested
without binding a real port or launching a real process.
"""
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(ROOT, "config", "config.json")

# canonical command -> action. Multiple aliases map to the same action.
COMMANDS = {
    "r": "reload", "reload": "reload", "restart": "reload",
    "x": "stop", "d": "stop", "stop": "stop", "disconnect": "stop",
    "b": "start", "s": "start", "start": "start",
    "o": "open", "open": "open",
    "st": "status", "status": "status",
    "h": "help", "help": "help", "?": "help",
    "q": "quit", "quit": "quit", "exit": "quit",
}

BANNER = """============================================================"""
HELP = """Commands:
  r , reload              reload service (stop then start, no window close)
  x , stop , disconnect   stop service, this window stays open
  b , start               start service again
  o , open                open http://%s:%d/login in browser
  st, status              show service status
  h , help                show this list
  q , quit                stop service and exit console

Closing this window also stops the service. Ctrl+C = quit."""


def server_addr():
    """Read host/port from config.json, falling back to the documented defaults."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            server = (json.load(f).get("server") or {})
        host = str(server.get("host") or "127.0.0.1")
        port = int(server.get("port") or 8848)
    except Exception:
        host, port = "127.0.0.1", 8848
    return host, port


def display_host(host):
    """0.0.0.0 is a bind address; browsers and probes must use loopback."""
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


def port_open(host, port, timeout=0.4):
    """True if something is listening on host:port right now."""
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((display_host(host), int(port))) == 0
    except OSError:
        return False
    finally:
        sock.close()


def parse_command(raw):
    """Return canonical action name, '' for blank input, 'unknown' otherwise."""
    key = str(raw or "").strip().lower()
    if not key:
        return ""
    return COMMANDS.get(key, "unknown")


class ServiceController:
    """Owns the child process and exposes start/stop/reload/status.

    Every side effect is injectable; tests pass fakes and never touch port 8848.
    """

    def __init__(self, host=None, port=None, python=None, spawn=None,
                 probe=None, sleep=None):
        if host is None or port is None:
            cfg_host, cfg_port = server_addr()
            host = cfg_host if host is None else host
            port = cfg_port if port is None else port
        self.host = host
        self.port = int(port)
        self.python = python or sys.executable
        self._spawn = spawn or self._default_spawn
        self._probe = probe or (lambda: port_open(self.host, self.port))
        self._sleep = sleep or time.sleep
        self.proc = None
        self.last_start = ""

    def _default_spawn(self, cmd, cwd):
        return subprocess.Popen(cmd, cwd=cwd)

    @property
    def url(self):
        return "http://%s:%d" % (display_host(self.host), self.port)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def pid(self):
        return getattr(self.proc, "pid", None) if self.running() else None

    def status(self):
        if self.running():
            return "RUNNING  pid=%s  %s/login" % (self.pid(), self.url)
        return "STOPPED  %s/login (use 'b' to start)" % self.url

    def start(self, wait=True):
        if self.running():
            return False, "Service is already running (pid=%s)." % self.pid()
        if self._probe():
            return False, "Port %s:%d is occupied by another process." % (
                display_host(self.host), self.port)
        try:
            self.proc = self._spawn([self.python, "-m", "backend.app"], ROOT)
        except Exception as exc:
            self.proc = None
            return False, "Failed to launch service: %s" % exc

        if wait:
            ready = False
            for _ in range(60):  # up to ~12s
                if self._probe():
                    ready = True
                    break
                if not self.running():
                    break
                self._sleep(0.2)
            if not ready and not self.running():
                self.proc = None
                return False, "Service exited during startup; see log above."
            note = "" if ready else " (port not answering yet)"
        else:
            note = ""
        self.last_start = time.strftime("%Y-%m-%d %H:%M:%S")
        return True, "Service started (pid=%s)%s." % (self.pid(), note)

    def stop(self, wait_port=True):
        if not self.running():
            self.proc = None
            return False, "Service is not running."
        proc = self.proc
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        self.proc = None
        if wait_port:
            for _ in range(25):  # up to ~5s
                if not self._probe():
                    break
                self._sleep(0.2)
        return True, "Service stopped. This window stays open."

    def reload(self):
        self.stop()
        return self.start()

    def open_browser(self):
        url = self.url + "/login"
        try:
            webbrowser.open(url)
            return True, "Opened %s" % url
        except Exception as exc:
            return False, "Could not open browser: %s" % exc


def run(ctrl, ask, say):
    """Interactive loop. Returns when the user quits or input ends (EOF)."""
    say(BANNER)
    say("  AI Ops Console - Service Console")
    say(BANNER)
    ok, msg = ctrl.start()
    say("[startup] %s %s" % ("OK  " if ok else "WARN", msg))
    say("Status : " + ctrl.status())
    say("Type 'h' for commands. Closing this window also stops the service.")
    say("")

    while True:
        try:
            raw = ask("console> ")
        except (KeyboardInterrupt, EOFError):
            say("")
            raw = "q"
        action = parse_command(raw)

        if action == "":
            continue
        if action == "help":
            say(HELP % (display_host(ctrl.host), ctrl.port))
        elif action == "status":
            say("Status : " + ctrl.status())
        elif action == "start":
            ok, msg = ctrl.start()
            say("[%s] %s" % ("OK" if ok else "!!", msg))
        elif action == "reload":
            ok, msg = ctrl.reload()
            say("[%s] %s" % ("OK" if ok else "!!", msg))
            say("Status : " + ctrl.status())
        elif action == "stop":
            ok, msg = ctrl.stop()
            say("[%s] %s" % ("OK" if ok else "!!", msg))
            say("Status : " + ctrl.status())
        elif action == "open":
            ok, msg = ctrl.open_browser()
            say("[%s] %s" % ("OK" if ok else "!!", msg))
        elif action == "quit":
            if ctrl.running():
                ok, msg = ctrl.stop()
                say("[OK] %s" % msg)
            say("Bye. You may close this window now.")
            return 0
        else:
            say("Unknown command '%s'. Type 'h' for help." % str(raw).strip())


def main():
    host, port = server_addr()
    ctrl = ServiceController(host, port)
    try:
        return run(ctrl, input, lambda s: print(s, flush=True))
    finally:
        if ctrl.running():
            try:
                ctrl.stop()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
