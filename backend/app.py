# -*- coding: utf-8 -*-
"""AI 运维控制台 · 后端服务

启动：  python -m backend.app     （或用 scripts/start.bat / start.sh）
默认：  http://127.0.0.1:8848
所有页面与 API 默认需要登录，登录入口 /login。
"""
import json
import os
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import (StreamingResponse, JSONResponse, FileResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles

from . import agent, audit, auth, store, workbench
from .ai_client import AIClient
from .ssh_manager import manager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")

# 模块导入即确保 auth 配置存在（兼容 uvicorn backend.app:app 等启动方式）
auth.auth_config()

# 交互式终端的唯一审计入口：SSH reader 每收到一块字节就回调，
# 与浏览器 SSE 是否连接无关，保证「终端上打印过的所有内容」1:1 落盘。
manager.set_data_sink(audit.feed_output)

app = FastAPI(title="AI 运维控制台", version="1.1")

# 登录白名单：登录页本身自包含（内联样式与脚本），无需放行任何静态资源
PUBLIC_PATHS = {"/login", "/api/auth/captcha", "/api/auth/login",
                "/api/health", "/favicon.ico"}


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """最外层登录闸门：未登录一律拦下，页面跳登录页、API 返回 401。"""
    path = request.url.path
    if path in PUBLIC_PATHS:
        if path == "/favicon.ico":
            return Response(status_code=204)
        return await call_next(request)

    token = request.cookies.get(auth.COOKIE_NAME, "")
    if not auth.check_session(token):
        if path.startswith("/api/"):
            return JSONResponse(
                {"ok": False, "code": "UNAUTHORIZED", "message": "登录已失效，请重新登录"},
                status_code=401,
                headers={"Cache-Control": "no-store"})
        return RedirectResponse("/login", status_code=302)

    request.state.user = auth.session_user(token)
    return await call_next(request)


def no_store(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


def sse(gen):
    def stream():
        for evt in gen:
            yield "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"
    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform, no-store",
                 "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ---------------- 命令审计：统一的上下文 ----------------
def audit_ctx(req, host_id="", sid=""):
    """组装一条审计记录需要的「谁 / 从哪来 / 在哪台机器」。"""
    if not host_id and sid:
        host_id = manager.shell_host(sid)
    h = store.get_host(host_id) or {}
    user = getattr(req.state, "user", "") or "unknown"
    return {
        "user": user,
        "ip": auth.client_ip(req) or "-",
        "host": h.get("name") or host_id or "-",
        "target": ("%s@%s:%s" % (h.get("username", ""), h.get("host", ""),
                                 h.get("port", 22))) if h else "-",
        "sid": sid or "-",
    }


def audit_log(req, cmd, ev_type="CMD", host_id="", sid="", source="terminal"):
    """审计写入永不阻断业务：日志失败只记到 stderr，不影响终端/AI。"""
    try:
        audit.write_event(audit_ctx(req, host_id, sid), cmd, ev_type, source)
    except Exception as e:
        print("[audit] write failed: %s" % e)


# ---------------- 页面 ----------------
@app.get("/")
def index():
    return no_store(FileResponse(os.path.join(FRONTEND, "index.html")))


@app.get("/login")
def login_page(req: Request):
    # 已登录时直接回控制台，避免重复登录
    if auth.check_session(req.cookies.get(auth.COOKIE_NAME, "")):
        return RedirectResponse("/", status_code=302)
    return no_store(FileResponse(os.path.join(FRONTEND, "login.html")))


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


# ---------------- 登录鉴权 ----------------
@app.get("/api/auth/captcha")
def captcha(cid: str = ""):
    cid, _code, png = auth.make_captcha(cid)
    return Response(
        content=png, media_type="image/png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache", "X-Captcha-Id": cid})


@app.post("/api/auth/login")
async def auth_login(req: Request):
    body = await req.json()
    ip = auth.client_ip(req)
    locked, remain = auth.is_locked(ip)
    if locked:
        return JSONResponse(
            {"ok": False, "message": "连续输错次数过多，请 %d 秒后再试" % remain},
            status_code=429)

    ok, msg = auth.check_captcha(body.get("cid", ""), body.get("captcha", ""))
    if not ok:
        auth.record_fail(ip)
        return JSONResponse({"ok": False, "message": msg}, status_code=400)
    if not auth.verify_credentials(body.get("username", ""), body.get("password", "")):
        auth.record_fail(ip)
        return JSONResponse(
            {"ok": False, "message": "用户名或密码不正确，验证码已刷新"}, status_code=401)

    auth.clear_fail(ip)
    username = auth.auth_config()["username"]
    token = auth.issue_session(username, ip)
    # 「自用户登录那一刻」起就有了可追溯的记录（未连接终端时归入当天独立操作文件）
    try:
        audit.write_event({"user": username, "ip": ip, "host": "-", "target": "-",
                           "sid": "-"}, "登录 AI 运维控制台", "LOGIN", "web")
    except Exception as e:
        print("[audit] login event failed: %s" % e)
    resp = JSONResponse({"ok": True, "message": "登录成功"})
    resp.set_cookie(auth.COOKIE_NAME, token, max_age=auth.SESSION_TTL,
                    httponly=True, samesite="lax", path="/")
    return no_store(resp)


@app.get("/api/auth/me")
def auth_me(req: Request):
    return {"ok": True, "username": getattr(req.state, "user", "") or ""}


@app.post("/api/auth/logout")
async def auth_logout(req: Request):
    try:
        audit.write_event({"user": getattr(req.state, "user", "") or "unknown",
                           "ip": auth.client_ip(req), "host": "-", "target": "-",
                           "sid": "-"}, "退出 AI 运维控制台", "LOGOUT", "web")
    except Exception as e:
        print("[audit] logout event failed: %s" % e)
    auth.drop_session(req.cookies.get(auth.COOKIE_NAME, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return no_store(resp)


# ---------------- 状态 / 配置 ----------------
@app.get("/api/state")
def state():
    return {"ok": True, **store.public_state(), "ssh": manager.status()}


# ---------------- 运维工作台（轻量数据观察 · 只读） ----------------
@app.get("/api/workbench/overview")
def wb_overview(window: str = "1h", start: str = "", end: str = ""):
    return workbench.overview(window, start, end)


@app.get("/api/workbench/regions")
def wb_regions(window: str = "1h", start: str = "", end: str = ""):
    return workbench.regions(window, start, end)


@app.get("/api/workbench/quality")
def wb_quality(dimension: str = "", page: int = 1, size: int = 12):
    return workbench.quality(dimension, page, size)


@app.get("/api/workbench/alerts")
def wb_alerts(resource: str = "", region: str = "", page: int = 1, size: int = 12,
              window: str = "1h", start: str = "", end: str = ""):
    return workbench.alerts(resource, region, page, size, window, start, end)


@app.get("/api/workbench/metrics")
def wb_metrics():
    """最新采集的真实指标；无告警时也能看到数据，不是一片空白。"""
    return workbench.live_metrics()


@app.get("/api/workbench/assets")
def wb_assets():
    """纳管主机资产台账：列出用户已填写的资产参数原值。"""
    return workbench.assets()


@app.get("/api/workbench/windows")
def wb_windows():
    return {"ok": True, "windows": list(workbench.WINDOWS),
            "default": workbench.WINDOW_DEFAULT}


@app.get("/api/workbench/plugins")
def wb_plugins():
    return workbench.plugins()


@app.post("/api/workbench/plugin/run")
async def wb_plugin_run(req: Request):
    body = await req.json()
    pid = body.get("plugin_id", "")
    result = workbench.run_plugin(pid, body.get("window", "1h"),
                                  body.get("start", ""), body.get("end", ""))
    if result.get("ok"):
        audit_log(req, "运行工作台插件「%s」" % result["name"], "WORKBENCH",
                  "-", "-", "web")
    return result


@app.get("/api/workbench/sources")
def wb_sources():
    return workbench.sources()


@app.post("/api/workbench/sources")
async def wb_sources_save(req: Request):
    body = await req.json()
    result = workbench.save_sources(body)
    audit_log(req, "保存运维工作台只读数据源配置（mode=%s）" % result.get("mode", ""),
              "WORKBENCH", "-", "-", "web")
    return result


@app.post("/api/workbench/sources/test")
async def wb_sources_test(req: Request):
    body = await req.json()
    return workbench.test_source(body.get("kind", ""))


@app.post("/api/workbench/collect")
def wb_collect(req: Request):
    """对已纳管主机执行只读采集，更新工作台监控快照（不下发任何写操作）。"""
    result = workbench.collect()
    audit_log(req, "采集纳管主机监控数据（成功 %d / 失败 %d）" %
              (result.get("success", 0), result.get("failed", 0)),
              "WORKBENCH", "-", "-", "web")
    return result


@app.post("/api/workbench/audit")
async def wb_audit(req: Request):
    """前端进入工作台 / 下钻时的轻量审计；所有终端输出仍由 reader 钩子 1:1 落盘。"""
    body = await req.json()
    audit_log(req, body.get("action", "查看运维工作台"), "WORKBENCH",
              "-", "-", "web")
    return {"ok": True}


@app.post("/api/hosts/test")
async def host_test(req: Request):
    body = await req.json()
    if body.get("id") and not body.get("host"):
        ok, msg, banner = manager.test(host_id=body["id"])
    else:
        ok, msg, banner = manager.test(payload=body)
    return {"ok": ok, "message": msg, "banner": banner}


@app.post("/api/hosts")
async def hosts_upsert(req: Request):
    body = await req.json()
    if not body.get("host") or not body.get("username"):
        return JSONResponse({"ok": False, "message": "主机地址与用户名必填"}, status_code=400)
    hid = store.upsert_host(body)
    manager.disconnect(hid)
    return {"ok": True, "id": hid}


@app.delete("/api/hosts/{host_id}")
def hosts_delete(host_id: str):
    manager.disconnect(host_id)
    store.delete_host(host_id)
    return {"ok": True}


@app.post("/api/settings/ai")
async def ai_upsert(req: Request):
    body = await req.json()
    if not body.get("base_url"):
        return JSONResponse({"ok": False, "message": "API 地址必填"}, status_code=400)
    pid = store.upsert_profile(body)
    return {"ok": True, "id": pid}


@app.delete("/api/settings/ai/{profile_id}")
def ai_delete(profile_id: str):
    store.delete_profile(profile_id)
    return {"ok": True}


@app.post("/api/ai/test")
async def ai_test(req: Request):
    body = await req.json()
    # 有 id：以已保存的接入为底，允许弹窗里未保存的值临时覆盖；
    # 无 id（新增中）：绝不能用其它接入兜底——否则模型名会串到旧厂商导致 404
    if body.get("id"):
        profile = dict(store.get_profile(body.get("id")) or {})
        for k in ("base_url", "api_key", "default_model"):
            if body.get(k):
                profile[k] = body[k]
    else:
        profile = {k: body.get(k) for k in ("base_url", "api_key", "default_model") if body.get(k)}
    if not profile.get("base_url"):
        return {"ok": False, "message": "请先填写 API 地址"}
    if not profile.get("api_key"):
        return {"ok": False, "message": "未配置 API Key"}
    model = body.get("model") or profile.get("default_model") or ""
    if not model:
        return {"ok": False, "message": "请先填写默认模型，或点「拉取模型」自动获取"}
    ok, msg = AIClient(profile, model=model).test()
    return {"ok": ok, "message": msg}


@app.get("/api/ai/models")
def ai_models(id: str = "", refresh: str = "", base_url: str = "", api_key: str = ""):
    """拉取模型列表。新增未保存的接入可直接带表单里的 base_url / api_key 拉取。"""
    profile = dict(store.get_profile(id) or {}) if id else {}
    if base_url:
        profile["base_url"] = base_url
    if api_key:
        profile["api_key"] = api_key
    if not profile.get("base_url"):
        return {"ok": False, "models": [], "message": "未配置 AI 接入（缺少 API 地址）"}
    if not profile.get("api_key"):
        return {"ok": False, "models": [], "message": "请先填写 API Key"}
    try:
        models = AIClient(profile).list_models()
        if models and id:
            store.upsert_profile({"id": id, "models": models})
        return {"ok": True, "models": models}
    except Exception as e:
        return {"ok": False, "models": profile.get("models", []),
                "message": "拉取失败：%s" % e}


# ---------------- 交互式终端 ----------------
@app.post("/api/terminal/open")
async def term_open(req: Request):
    body = await req.json()
    cols = int(body.get("cols", 120))
    rows = int(body.get("rows", 36))
    host_id = body.get("host_id", "")
    try:
        # open_shell 一返回，reader 线程就可能已经吐出登录横幅 / 提示符；
        # feed_output 会先缓存这些字节块，open_session 再一次性补写，开头绝不丢。
        sid = manager.open_shell(host_id, cols=cols, rows=rows)
        ctx = dict(audit_ctx(req, host_id, sid), cols=cols, rows=rows)
        try:
            audit.open_session(sid, ctx)
        except Exception as e:
            print("[audit] open session failed: %s" % e)
        audit_log(req, "建立 SSH 终端连接", "CONNECT", host_id, sid)
        return {"ok": True, "sid": sid}
    except Exception as e:
        audit_log(req, "SSH 终端连接失败：%s" % audit.sanitize(str(e))[:200],
                  "CONNECT_FAIL", host_id)
        return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}


@app.get("/api/terminal/stream")
def term_stream(sid: str):
    return sse(manager.shell_stream(sid))


@app.post("/api/terminal/input")
async def term_input(req: Request):
    """接收 xterm.js 的原始按键流（含 Tab / 方向键 / Ctrl+C 等控制字符）。

    审计埋点：把原始按键还原成完整命令行后写入该连接对应的会话窗口文件。
    keepalive=true 的是反闲置保活字符，不属于用户操作，不参与命令还原。
    """
    body = await req.json()
    sid, data = body.get("sid", ""), body.get("data", "")
    if not body.get("keepalive") and sid and data:
        try:
            audit.feed_keys(sid, data, audit_ctx(req, sid=sid))
        except Exception as e:
            print("[audit] feed failed: %s" % e)
    return {"ok": manager.shell_input(sid, data)}


@app.post("/api/terminal/resize")
async def term_resize(req: Request):
    """终端窗口尺寸变化时同步 PTY 与审计屏幕，避免 vim 等全屏程序错位。"""
    body = await req.json()
    sid = body.get("sid", "")
    cols = int(body.get("cols", 120))
    rows = int(body.get("rows", 36))
    try:
        audit.resize_screen(sid, cols, rows)
    except Exception as e:
        print("[audit] resize screen failed: %s" % e)
    return {"ok": manager.shell_resize(sid, cols, rows)}


@app.post("/api/terminal/notice")
async def term_notice(req: Request):
    """前端写进 xterm 的本地提示（连接中 / 错误 / 中断 / 失败）也纳入同一窗口文件。"""
    body = await req.json()
    try:
        audit.write_notice(body.get("sid", ""), body.get("text", ""),
                           audit_ctx(req, body.get("host_id", ""), body.get("sid", "")))
    except Exception as e:
        print("[audit] notice failed: %s" % e)
    return {"ok": True}


@app.post("/api/terminal/close")
async def term_close(req: Request):
    body = await req.json()
    sid = body.get("sid", "")
    # 先取上下文（close_shell 之后就查不到主机信息了）
    ctx = audit_ctx(req, sid=sid)
    # 先停 reader 并等它把最后一块输出 / 断开提示写进审计，再封存窗口文件
    manager.close_shell(sid)
    try:
        audit.write_event(ctx, "关闭 SSH 终端连接", "DISCONNECT")
    except Exception as e:
        print("[audit] disconnect event failed: %s" % e)
    try:
        audit.close_session(sid, "正常断开")
    except Exception as e:
        print("[audit] close session failed: %s" % e)
    return {"ok": True}


# ---------------- SFTP 文件传输 ----------------
@app.get("/api/sftp/list")
def sftp_list(host_id: str, path: str = "/"):
    try:
        return {"ok": True, **manager.sftp_list(host_id, path)}
    except Exception as e:
        return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}


@app.get("/api/sftp/download")
async def sftp_download(req: Request, host_id: str, path: str):
    """流式下载远端文件；客户端断开或点取消后停止读取。"""
    import asyncio
    try:
        info = manager.sftp_stat(host_id, path)
    except Exception as e:
        return JSONResponse({"ok": False,
                             "message": "%s: %s" % (type(e).__name__, e)},
                            status_code=400)
    jid, jobj = manager.new_job()
    dl_ctx = audit_ctx(req, host_id, "-")
    audit_log(req, "下载远端文件 %s（%s 字节）" % (path, info["size"]),
              "DOWNLOAD", host_id, "-", "sftp")

    async def gate():
        # 客户端断开（用户点取消或关窗）后自动置位取消，及时释放 SFTP 通道
        try:
            while True:
                await req.disconnect()
                jobj["cancel"] = True
        except Exception:
            jobj["cancel"] = True

    def stream():
        try:
            for chunk in manager.sftp_download(host_id, path, jobj):
                yield chunk
        finally:
            manager.finish_job(jid)
            if jobj.get("cancel"):
                try:
                    audit.write_event(dl_ctx, "取消下载：%s" % path,
                                      "DOWNLOAD_CANCEL", "sftp")
                except Exception:
                    pass

    asyncio.create_task(gate())

    from urllib.parse import quote
    filename = info["name"]
    resp = StreamingResponse(
        stream(), media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(filename),
            "X-File-Size": str(info["size"]),
            "X-File-Name": quote(filename),
            "X-Transfer-Job": jid,
            "Cache-Control": "no-store",
        })
    return resp


@app.post("/api/sftp/cancel")
async def sftp_cancel(req: Request):
    body = await req.json()
    return {"ok": manager.cancel_job(body.get("job", ""))}


@app.post("/api/sftp/upload")
async def sftp_upload(req: Request, host_id: str = "", path: str = "/",
                      name: str = "", job: str = ""):
    """原始请求体流式写入远端 path/name。查询参数走 URL，避免 multipart 依赖。"""
    import posixpath
    if not host_id or not name:
        return JSONResponse({"ok": False, "message": "缺少 host_id 或文件名"},
                            status_code=400)
    if not job:
        jid, jobj = manager.new_job()
    else:
        jid = job
        with manager._lock:
            jobj = manager._jobs.get(jid)
        if jobj is None:
            jid, jobj = manager.new_job()
    target = None
    f = sftp = None
    try:
        target, sftp, f = manager.sftp_open_upload(host_id, path, name)
        written = 0
        try:
            async for body in req.stream():
                if jobj.get("cancel"):
                    break
                if body:
                    f.write(body)
                    written += len(body)
        except Exception:
            jobj["cancel"] = True
        cancelled = bool(jobj.get("cancel"))
        manager.sftp_close_upload(target, sftp, f, cancelled)
        sftp = f = None
        if cancelled:
            audit_log(req, "取消上传 %s/%s（已写入 %s 字节，半成品已删除）"
                      % (path, name, written), "UPLOAD_CANCEL", host_id, "-", "sftp")
            return {"ok": False, "cancelled": True, "message": "已取消上传，未保留半成品文件",
                    "written": written}
        audit_log(req, "上传本机文件到 %s（%s 字节）" % (target, written),
                  "UPLOAD", host_id, "-", "sftp")
        return {"ok": True, "path": target, "written": written,
                "message": "上传成功：%s" % posixpath.basename(target)}
    except Exception as e:
        if f is not None or sftp is not None:
            try:
                manager.sftp_close_upload(target or "", sftp, f, True)
            except Exception:
                pass
        return JSONResponse({"ok": False,
                             "message": "%s: %s" % (type(e).__name__, e)},
                            status_code=500)
    finally:
        manager.finish_job(jid)


# ---------------- AI 诊断 ----------------
@app.post("/api/agent/run")
async def agent_run(req: Request):
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"ok": False, "message": "请输入诊断要求"}, status_code=400)
    audit_log(req, "发起 AI 诊断：" + audit.sanitize(prompt)[:200], "DIAGNOSE",
              body.get("host_id", ""), "-", "ai")
    return sse(agent.run(
        host_id=body.get("host_id", ""),
        prompt=prompt,
        profile_id=body.get("profile_id"),
        model=body.get("model"),
        read_only=bool(body.get("read_only", True)),
        auto_approve=bool(body.get("auto_approve", False)),
        # 不再让用户关心「最大重试」：默认步数放宽到 60，正常诊断都会自然跑完出完整结论；
        # 仅保留兜底上限，防止极端情况下无限循环。
        max_retry=int(body.get("max_retry", 60)),
        user=getattr(req.state, "user", "") or "unknown",
        ip=auth.client_ip(req) or "-",
        audit_context=audit.context_for_ai(prompt),
    ))


@app.post("/api/agent/confirm")
async def agent_confirm(req: Request):
    body = await req.json()
    ok = agent.resolve_confirm(body.get("confirm_id", ""), body.get("approved", False))
    return {"ok": ok}


@app.post("/api/agent/stop")
async def agent_stop(req: Request):
    body = await req.json()
    agent.stop_run(body.get("run_id", ""))
    return {"ok": True}


@app.post("/api/agent/ask")
async def agent_ask(req: Request):
    body = await req.json()
    prompt = body.get("prompt", "")
    audit_log(req, "AI 问答：" + audit.sanitize(prompt)[:200], "AI_ASK", "-", "-", "ai")
    return sse(agent.ask(prompt, body.get("profile_id"), body.get("model"),
                         audit_context=audit.context_for_ai(prompt)))


# ---------------- 本地会话审计文档 ----------------
def _safe_session_file(name):
    """只接受会话目录下的纯文件名，杜绝目录穿越读取任意文件。"""
    name = os.path.basename(str(name or ""))
    if not name.endswith(".txt") or name in (".", "..") or "/" in name or "\\" in name:
        return ""
    fp = os.path.join(audit.SESSION_DIR, name)
    return fp if os.path.isfile(fp) else ""


@app.get("/api/audit/sessions")
def audit_sessions():
    """会话窗口清单：每个 .txt 就是一次「连接 → 断开」的完整记录。"""
    try:
        return {"ok": True, "dir": audit.SESSION_DIR,
                "stats": audit.stats(), "sessions": audit.list_sessions()}
    except Exception as e:
        return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}


@app.get("/api/audit/session")
def audit_session(file: str = ""):
    """读取单个会话窗口文件的完整纯文本，供界面右侧预览。"""
    fp = _safe_session_file(file)
    if not fp:
        return {"ok": False, "message": "会话文件不存在或文件名不合法"}
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        clipped = False
        if len(text) > 1000000:                 # 实时 1:1 实录可能很大，预览限流
            text = text[:1000000] + "\n\n…（文件过大，预览仅显示前 1000000 字符；"
            text += "点「下载此会话」可取得完整原文）\n"
            clipped = True
        _meta, _ = audit.read_session_file(fp)
        return {"ok": True, "file": os.path.basename(fp), "text": text,
                "meta": _meta, "clipped": clipped}
    except Exception as e:
        return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}


@app.get("/api/audit/raw")
def audit_raw(file: str = ""):
    """下载该会话未经任何处理的原始字节流（含 ANSI），用于逐字节复核 / 回放。"""
    fp = _safe_session_file(file)
    if not fp:
        return JSONResponse({"ok": False, "message": "会话文件不存在"}, status_code=404)
    rp = audit._raw_path(fp)
    if not os.path.isfile(rp):
        return JSONResponse({"ok": False, "message": "原始流文件不存在"}, status_code=404)
    return FileResponse(rp, media_type="application/octet-stream",
                        filename=os.path.basename(rp))


@app.get("/api/audit/query")
def audit_query(keyword: str = "", user: str = "", host: str = "", ev_type: str = "",
                since: str = "", until: str = "", limit: int = 200, file: str = ""):
    """跨所有会话窗口文件检索记录；指定 file 时只查某一个会话窗口。"""
    try:
        entries = audit.read_entries(limit, keyword, user, host, ev_type, since, until)
        if file:
            fp = _safe_session_file(file)
            name = os.path.basename(fp) if fp else file
            entries = [e for e in entries if e.get("file") == name][:limit]
        return {"ok": True, "stats": audit.stats(), "entries": entries,
                "count": len(entries), "sessions": audit.list_sessions()}
    except Exception as e:
        return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}


@app.get("/api/audit/download")
def audit_download(file: str = ""):
    """下载单个会话窗口文本；不指定 file 则合并全部会话文件。"""
    try:
        if file:
            fp = _safe_session_file(file)
            if not fp:
                return JSONResponse({"ok": False, "message": "会话文件不存在"}, status_code=404)
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            out_name = os.path.basename(fp)
        else:
            parts = []
            for fp in audit._files():
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    parts.append("===== %s =====\n" % os.path.basename(fp))
                    parts.append(f.read())
                    parts.append("\n")
            text = "".join(parts) or "（审计文档暂无记录）\n"
            out_name = "command-audit-all-sessions.txt"
        return Response(
            content=text.encode("utf-8"), media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition":
                     "attachment; filename*=UTF-8''%s" % quote(out_name),
                     "Cache-Control": "no-store"})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)


@app.post("/api/audit/reveal")
def audit_reveal():
    """在资源管理器中打开会话文档目录（仅本机 Windows 有效）。"""
    try:
        audit.ensure()
        os.startfile(audit.SESSION_DIR)          # noqa: S606  仅打开本地目录
        return {"ok": True, "path": audit.SESSION_DIR}
    except Exception as e:
        return {"ok": False, "message": str(e), "path": audit.SESSION_DIR}


@app.get("/api/health")
def health():
    return {"ok": True, "service": "ai-ops-console"}


if __name__ == "__main__":
    import uvicorn
    cfg = store.load().get("server", {})
    host = cfg.get("host", "127.0.0.1")
    port = int(cfg.get("port", 8848))
    print("=" * 58)
    print("  AI Ops Console is running")
    print("  URL:        http://%s:%d" % (host if host != "0.0.0.0" else "127.0.0.1", port))
    print("  Login page: http://%s:%d/login" % (host if host != "0.0.0.0" else "127.0.0.1", port))
    print("=" * 58)
    uvicorn.run(app, host=host, port=port, log_level="warning")
