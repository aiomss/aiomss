# -*- coding: utf-8 -*-
"""多主机 SSH 会话池：用户名/密码连接、命令执行、交互式 shell。

交互式 shell 保留**原始字节流**（含 ANSI / VT100 控制序列）推给前端 xterm.js，
因此 vim / top / less 等全屏程序、Tab 补全、Ctrl+C 都能原生工作。
只有 AI 诊断用到的「单条命令执行」才剥离控制序列，便于在步骤卡片里阅读。
"""
import codecs
import posixpath
import queue
import re
import stat as statmod
import threading
import time
import uuid

import paramiko

from . import store

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r")


def strip_ansi(s):
    return ANSI.sub("", s or "")


class SSHManager:
    CHUNK = 65536

    def __init__(self):
        self._lock = threading.RLock()
        self._clients = {}      # host_id -> SSHClient
        self._shells = {}       # sid -> {"host_id", "chan", "q", "thread", "alive"}
        self._jobs = {}         # job_id -> {"cancel": bool}（传输任务的取消标志）
        self._data_sink = None  # callable(sid, raw_bytes, text)：终端审计 1:1 落盘

    def set_data_sink(self, fn):
        """注册终端输出回调。埋在 reader 线程里，浏览器 SSE 断开也照样审计。"""
        self._data_sink = fn

    def connect(self, host_id):
        """取得（可复用的）到某主机的连接；失败抛异常。"""
        with self._lock:
            cli = self._clients.get(host_id)
            if cli is not None:
                try:
                    transport = cli.get_transport()
                    if transport and transport.is_active():
                        return cli
                except Exception:
                    pass
                self._drop_locked(host_id)

            h = store.get_host(host_id)
            if not h:
                raise RuntimeError("主机不存在: %s" % host_id)
            pwd = store.host_password(h)
            if not pwd:
                raise RuntimeError("主机 %s 未配置密码" % h.get("name"))

            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cli.connect(
                hostname=h.get("host"),
                port=int(h.get("port", 22)),
                username=h.get("username", "root"),
                password=pwd,
                look_for_keys=False,
                allow_agent=False,
                timeout=12,
                banner_timeout=12,
                auth_timeout=12,
            )
            self._clients[host_id] = cli
            return cli

    def _drop_locked(self, host_id):
        cli = self._clients.pop(host_id, None)
        if cli:
            try:
                cli.close()
            except Exception:
                pass

    def test(self, host_id=None, payload=None):
        """测试连接：可传已保存 host_id，或临时 payload（不落盘）。返回 (ok, msg, banner)"""
        if payload:
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                cli.connect(
                    hostname=payload.get("host"),
                    port=int(payload.get("port", 22)),
                    username=payload.get("username", "root"),
                    password=payload.get("password") or store.host_password(store.get_host(payload.get("id")) or {}),
                    look_for_keys=False, allow_agent=False,
                    timeout=10, banner_timeout=10, auth_timeout=10,
                )
                _, out, _ = cli.exec_command("uname -a", timeout=8)
                banner = out.read().decode("utf-8", "replace").strip()[:200]
                cli.close()
                return True, "连接成功", banner
            except Exception as e:
                try:
                    cli.close()
                except Exception:
                    pass
                return False, "%s: %s" % (type(e).__name__, e), ""
        try:
            cli = self.connect(host_id)
            _, out, _ = cli.exec_command("uname -a", timeout=8)
            banner = out.read().decode("utf-8", "replace").strip()[:200]
            return True, "连接成功", banner
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, e), ""

    def exec(self, host_id, command, timeout=30, max_chars=12000):
        """执行单条命令，返回 {stdout, stderr, exit_code, elapsed}。"""
        t0 = time.time()
        cli = self.connect(host_id)
        chan = cli.get_transport().open_session()
        chan.settimeout(timeout)
        chan.exec_command(command)
        out_buf, err_buf = [], []
        deadline = t0 + timeout
        while True:
            if chan.recv_ready():
                out_buf.append(chan.recv(65536).decode("utf-8", "replace"))
            if chan.recv_stderr_ready():
                err_buf.append(chan.recv_stderr(65536).decode("utf-8", "replace"))
            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                break
            if time.time() > deadline:
                chan.close()
                err_buf.append("\n[命令超时，已终止]")
                break
            time.sleep(0.05)
        exit_code = chan.recv_exit_status() if chan.exit_status_ready() else -1
        chan.close()
        # AI 步骤卡片是纯文本，剥掉颜色/光标控制码避免出现乱码
        stdout = strip_ansi("".join(out_buf))
        stderr = strip_ansi("".join(err_buf))
        clipped = ""
        if len(stdout) > max_chars:
            clipped = "\n... [输出过长，已截断 %d 字符，仅保留首尾] ..." % (len(stdout) - max_chars)
            stdout = stdout[:max_chars // 2] + clipped + "\n" + stdout[-max_chars // 2:]
        return {
            "stdout": stdout,
            "stderr": stderr[:3000],
            "exit_code": exit_code,
            "elapsed": round(time.time() - t0, 2),
            "command": command,
        }

    # ---------- 交互式 shell（原始 VT 流，交给 xterm.js 渲染） ----------
    def open_shell(self, host_id, cols=120, rows=36):
        cli = self.connect(host_id)
        chan = cli.invoke_shell(term="xterm-256color", width=cols, height=rows)
        chan.settimeout(0.1)
        sid = uuid.uuid4().hex[:12]
        q = queue.Queue()
        info = {"host_id": host_id, "chan": chan, "q": q, "alive": True}
        self._shells[sid] = info

        def emit(raw, text):
            # 每个字节块都先送审计（原始字节 + 解码文本），再推给前端渲染；
            # 审计永不因回调异常影响终端，也不依赖 SSE 是否还连着。
            sink = self._data_sink
            if sink:
                try:
                    sink(sid, raw, text)
                except Exception as e:
                    print("[audit] data sink failed: %s" % e)
            if text:
                q.put(text)                  # 原样保留 ANSI / 光标控制序列

        def reader():
            # 增量解码：SSH 数据可能把一个汉字切成两半，不能逐块 decode
            dec = codecs.getincrementaldecoder("utf-8")("replace")
            while info["alive"]:
                try:
                    if chan.recv_ready():
                        raw = chan.recv(65536)
                        if raw:
                            emit(raw, dec.decode(raw))
                        continue
                    if chan.exit_status_ready():
                        break
                    time.sleep(0.02)
                except Exception:
                    time.sleep(0.02)
            try:
                tail = dec.decode(b"", final=True)
                if tail:
                    emit(b"", tail)
            except Exception:
                pass
            emit(b"", "\r\n\x1b[90m[连接已断开，点右上角 ⟳ 重连]\x1b[0m\r\n")
            info["alive"] = False

        t = threading.Thread(target=reader, daemon=True)
        info["thread"] = t
        t.start()
        return sid

    def shell_host(self, sid):
        """给审计用：从会话 id 反查主机 id（找不到返回空串）。"""
        info = self._shells.get(sid)
        return (info or {}).get("host_id", "")

    def shell_stream(self, sid):
        """SSE 生成器：持续吐出 shell 输出，空闲时发心跳。"""
        info = self._shells.get(sid)
        if not info:
            yield {"type": "error", "text": "会话不存在"}
            return
        q = info["q"]
        idle = 0
        while info["alive"]:
            try:
                data = q.get(timeout=0.5)
                idle = 0
                yield {"type": "out", "text": data}
            except queue.Empty:
                idle += 1
                if idle >= 20:          # 10s 发一次心跳保活
                    idle = 0
                    yield {"type": "ping", "text": ""}
        yield {"type": "closed", "text": ""}

    def shell_input(self, sid, data):
        info = self._shells.get(sid)
        if not info or not info["alive"]:
            return False
        try:
            info["chan"].send(data)
            return True
        except Exception:
            return False

    def shell_resize(self, sid, cols, rows):
        info = self._shells.get(sid)
        if info and info["alive"]:
            try:
                info["chan"].resize_pty(width=cols, height=rows)
                return True
            except Exception:
                pass
        return False

    def close_shell(self, sid):
        info = self._shells.pop(sid, None)
        if info:
            info["alive"] = False
            try:
                info["chan"].close()
            except Exception:
                pass
            # 等 reader 把最后一块数据 / 断开提示送进审计再封存窗口文件
            t = info.get("thread")
            if t and t is not threading.current_thread():
                try:
                    t.join(timeout=0.5)
                except Exception:
                    pass

    # ---------- SFTP 文件传输（浏览 / 下载 / 上传，均可取消） ----------
    def new_job(self):
        jid = uuid.uuid4().hex[:10]
        with self._lock:
            self._jobs[jid] = {"cancel": False}
        return jid, self._jobs[jid]

    def cancel_job(self, jid):
        with self._lock:
            job = self._jobs.get(jid)
        if job:
            job["cancel"] = True
            return True
        return False

    def finish_job(self, jid):
        with self._lock:
            self._jobs.pop(jid, None)

    def _sftp(self, host_id):
        return self.connect(host_id).open_sftp()

    @staticmethod
    def _safe_name(name):
        """只保留纯文件名，去掉任何路径成分，防止目录穿越。"""
        n = posixpath.basename(str(name or "").replace("\\", "/")).strip()
        return n if n not in ("", ".", "..") else "unnamed"

    def sftp_list(self, host_id, path):
        path = posixpath.normpath(path or "/")
        sftp = self._sftp(host_id)
        try:
            entries = []
            for a in sftp.listdir_attr(path):
                mode = a.st_mode or 0
                if statmod.S_ISDIR(mode):
                    typ = "dir"
                elif statmod.S_ISLNK(mode):
                    typ = "link"
                else:
                    typ = "file"
                entries.append({"name": a.filename, "type": typ,
                                "size": a.st_size or 0,
                                "mtime": int(a.st_mtime or 0)})
            entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
            return {"path": posixpath.normpath(sftp.normalize(path)),
                    "home": sftp.normalize("."), "entries": entries}
        finally:
            sftp.close()

    def sftp_stat(self, host_id, path):
        path = posixpath.normpath(path)
        sftp = self._sftp(host_id)
        try:
            st = sftp.stat(path)
            return {"path": path, "name": posixpath.basename(path),
                    "size": st.st_size or 0}
        finally:
            sftp.close()

    def sftp_download(self, host_id, path, job):
        """生成原始字节块；job["cancel"] 置位后停止（前端随后断开响应）。"""
        path = posixpath.normpath(path)
        sftp = self._sftp(host_id)
        try:
            size = sftp.stat(path).st_size or 0
            with sftp.open(path, "rb") as f:
                remaining = size
                while remaining > 0:
                    if job.get("cancel"):
                        break
                    n = min(self.CHUNK, remaining)
                    data = f.read(n)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        finally:
            sftp.close()

    def sftp_open_upload(self, host_id, remote_dir, filename):
        """打开远端写入流，返回 (目标路径, sftp, file)。"""
        remote_dir = posixpath.normpath(remote_dir or "/")
        target = posixpath.join(remote_dir, self._safe_name(filename))
        sftp = self._sftp(host_id)
        try:
            f = sftp.open(target, "wb")
            f.set_pipelined(True)
            return target, sftp, f
        except Exception:
            sftp.close()
            raise

    def sftp_close_upload(self, target, sftp, f, cancelled):
        try:
            f.close()
        except Exception:
            pass
        if cancelled:
            try:
                sftp.remove(target)   # 取消时删掉半成品
            except Exception:
                pass
        try:
            sftp.close()
        except Exception:
            pass

    def disconnect(self, host_id):
        with self._lock:
            for sid, info in list(self._shells.items()):
                if info["host_id"] == host_id:
                    self.close_shell(sid)
            self._drop_locked(host_id)

    def status(self):
        with self._lock:
            active = []
            for hid, cli in self._clients.items():
                try:
                    if cli.get_transport() and cli.get_transport().is_active():
                        active.append(hid)
                except Exception:
                    pass
            return {"connected": active, "shells": list(self._shells.keys())}


manager = SSHManager()
