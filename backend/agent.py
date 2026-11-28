# -*- coding: utf-8 -*-
"""AI 诊断 Agent：AI 生成命令 → 安全闸门 → SSH 执行 → 结果回灌 → 直到给出结论。

通过 SSE 事件流把每一步推给前端：
  start / progress / heartbeat / reasoning / notice / step / step_update
  confirm_required / confirm_timeout / summary / answer_* / done

设计要点（v1.2）：
  · 任何耗时环节（AI 推理、SSH 执行、等待人工审批）都会持续推 heartbeat。
  · 审批有硬超时，超时自动拒绝并继续给出结论，绝不无限期挂起。
  · 问答流立即给反馈，答案按片段流式返回，并检测 max_tokens 截断。
  · 结论疑似半句（被 max_tokens 截断）时自动续写补全；内部容错不打扰用户。
"""
import json
import queue
import threading
import time
import uuid

from . import audit, safety, store
from .ai_client import (AIClient, complete_summary, humanize_raw,
                        summary_looks_truncated)
from .ssh_manager import manager

SYSTEM_PROMPT = """你是一位有十年以上经验的资深 Linux 运维工程师，正在通过 SSH 连接用户指定的主机进行运维诊断。

每一轮你**必须只输出一个合法的 JSON 对象**，不得包含任何其它文字、解释或 Markdown 围栏：
{"thought": "简短说明本轮判断与目的（1-2 句）", "command": "本轮要执行的单条 Linux 命令；不需要执行命令时填空字符串", "done": false, "summary": "仅当 done=true 时填写最终结论"}

硬性规则：
1. 输出必须以 { 开头、以 } 结尾的完整 JSON；键名只能是 thought / command / done / summary 四个，不要给键名加方括号。
2. command 必须是**单条、非交互式、可直接执行**的命令。禁止 vim / top / less / more / htop / tail -f / vi 等交互命令；查看进程用 `ps aux --sort=-%cpu | head -20`，看 CPU 用 `top -b -n 1 | head -20`。
3. 优先用只读命令收集证据（cat/grep/journalctl/df/free/ps/ss/systemctl status/lsof...）。
4. 命令输出会自动回灌给你，据此决定下一步；不要臆测没看到的结果。
5. 每一轮只执行一条命令；需要多条就分多轮。
6. 若用户只是在问**命令用法 / 概念 / 参数**（例如 vim 参数怎么用），不要执行命令：command 填空字符串，done=true，在 summary 里给出"作用 → 关键参数表 → 常用组合 → 注意事项"的完整讲解。
7. 涉及删除、重启、改配置、DDL/DML、权限变更等写操作，方可在 command 中给出，并在 thought 里明确说明影响范围；系统会先向用户弹确认。
8. 诊断完成时 done=true，summary 必须用中文给出可执行的**完整结论**，并使用【现象】【证据】【根因】【建议】【验证方法】这些小节标题分段。每个小节都要写成完整句子，严禁在半句话处结束；**严禁只写小节标题而不写正文**，也严禁 summary 为空或只写一句"已完成"。
8.0 **分条分行（非常重要）**：summary 里每一条内容都必须单独占一行（行间用 \\n 分隔），一行只写一条；多条建议 / 步骤用「1. 」「2. 」「3. 」（或「· 」）编号逐行分条，**严禁把多条内容用「；」「·」挤在同一行**。小节标题【xx】单独一行，其正文从下一行开始。
8.1 对**盘点/巡检类**问题（如"有几个用户""有没有匿名账户""跑了什么服务"），小节改用【总览】【明细】【风险】【建议】：【总览】一两句话直接回答问题（数量、结论）；【明细】逐项列出查到的事实（账户名、服务名、端口等，关键数字不能省略）；【风险】指出异常点（如空口令、可登录的系统账户、对外开放的高危端口），没有就写"未发现明显风险"；【建议】给出可执行的加固/处理步骤。summary 里必须把答案正文写全，**绝不能只回一句"已完成/已收集信息"**——用户在界面上只能看到 summary，命令输出不会自动变成报告。
9. 输出必须是合法 JSON，布尔值用 true/false，字符串内的换行使用 JSON 转义形式。
10. 若提问涉及「本地审计 / 谁在什么时候执行了什么命令 / 操作记录」，且下文提供了【本地审计文档】摘录：
    这是**唯一事实依据**，必须 command 填空字符串、done=true，直接基于文档回答，严禁再执行任何远程命令；
    summary 用【总览】【明细】【风险】【建议】分段，【明细】逐条写清「时间 · 用户 · 主机 · 操作」，
    文档里没有的人和事必须写「审计文档中未找到」，不得编造、推测或把示例当事实。
"""

# 审计类问题追加的提示（只在命中审计意图时注入，普通诊断完全不增加 token）
AUDIT_RULE = """

【审计问答专用规则（本次生效）】
本次用户提问属于本地审计查询。下方【本地审计文档】是本机 audit/sessions/ 目录下、
按「一次连接+一次断开生成一个 用户名_时间.txt」的会话窗口文件的真实摘录。
你必须：command 填空字符串、done=true、直接给出中文完整结论，**不执行任何 SSH 命令**；
按【总览】【明细】【风险】【建议】分段，每条单独一行；时间、用户、主机、命令逐字引用文档，
未出现在文档里的信息一律写「审计文档中未找到」，严禁编造。
"""

# 非主机目标（数据库 / 应用 / 日志）只用真实数据做证据分析，不执行任何远程命令。
DATA_RULE = """

【目标数据分析专用规则（本次生效）】
下方【分析目标数据】由本系统从纳管主机真实采集快照或用户录入台账生成，是本次回答的唯一事实依据。
你必须：command 填空字符串、done=true、直接给出中文完整结论，**不执行任何 SSH 命令**；
按【现象/现状】【证据】【风险】【建议】【验证方法】分段，每条单独一行；
证据里没有的数字、主机、日志、配置项一律写「数据中未提供」，严禁臆测或把示例当事实。
"""

# 审批等待表
_pending = {}
_pending_lock = threading.RLock()
_stop_flags = {}

CONFIRM_TIMEOUT = 120       # 秒：审批硬超时，超时自动拒绝
AI_WAIT_TIMEOUT = 200       # 秒：单轮 AI 推理最长等待
EXEC_WAIT_TIMEOUT = 60      # 秒：单条命令最长等待
HEARTBEAT_INTERVAL = 3.0


def request_confirm(step_id, command, reasons, level):
    ev = threading.Event()
    with _pending_lock:
        _pending[step_id] = {"event": ev, "approved": False, "at": time.time()}
    return ev


def resolve_confirm(confirm_id, approved):
    with _pending_lock:
        item = _pending.get(confirm_id)
        if not item:
            return False
        item["approved"] = bool(approved)
        item["event"].set()
        return True


def stop_run(run_id):
    _stop_flags[run_id] = True
    with _pending_lock:
        for item in _pending.values():
            item["approved"] = False
            item["event"].set()


def _heartbeat_wait(ev, label, timeout=210, interval=HEARTBEAT_INTERVAL):
    """等待事件期间持续产出 heartbeat，避免 SSE 静默导致前端假死。"""
    started = time.time()
    while True:
        left = timeout - (time.time() - started)
        if left <= 0:
            return
        if ev.wait(min(interval, max(0.1, left))):
            return
        elapsed = int(time.time() - started)
        yield {"type": "heartbeat", "wait": label, "elapsed": elapsed,
               "remaining": max(0, int(timeout - elapsed)),
               "text": "%s（已等待 %ds）" % (label, elapsed)}


def _run_in_thread(fn, box, ev):
    def job():
        try:
            box["r"] = fn()
        except Exception as exc:
            box["e"] = exc
        finally:
            ev.set()
    threading.Thread(target=job, daemon=True).start()


def _fmt_result(r, max_chars=6000):
    out = r.get("stdout", "")
    if len(out) > max_chars:
        out = out[:max_chars // 2] + "\n...[截断]...\n" + out[-max_chars // 2:]
    parts = ["退出码: %s | 耗时: %ss" % (r.get("exit_code"), r.get("elapsed"))]
    if out.strip():
        parts.append("stdout:\n" + out)
    if r.get("stderr", "").strip():
        parts.append("stderr:\n" + r["stderr"][:1500])
    return "\n".join(parts) if len(parts) > 1 else "（无输出）"


def _friendly_ai_error(e):
    s = str(e)
    if "HTTP 401" in s or "Authentication" in s:
        return "API Key 无效或已过期，请在「API修改」里更新。"
    if "HTTP 402" in s or "Insufficient" in s or "balance" in s.lower():
        return "账户余额或额度不足，请到 API 平台充值后再试。"
    if "HTTP 429" in s:
        return "调用过于频繁或触发限流，稍等片刻再试。"
    if "HTTP 5" in s:
        return "AI 服务端暂时不可用，请稍后重试。"
    if "timeout" in s.lower() or "timed out" in s.lower():
        return "AI 响应超时，可能是问题较复杂，可缩小范围后重试。"
    return "AI 服务调用未完成，请检查网络与 API 配置后重试。"


def _notice(level, title, text, detail=""):
    return {"type": "notice", "level": level, "title": title,
            "text": text, "detail": detail}


def _final_summary(ai, summary, thought="", raw=""):
    """把结论收拾成完整的人话：半句结论自动续写补全，内部容错不打扰用户。"""
    text = (summary or "").strip()
    if not text:
        return ((thought or "").strip() or humanize_raw(raw)
                or "诊断完成（无总结文本）")
    if summary_looks_truncated(text):
        text = complete_summary(ai, text)
    return text


def run(host_id, prompt, profile_id=None, model=None, read_only=True,
        auto_approve=False, max_retry=60, user="unknown", ip="-", audit_context="",
        target_context="", target_label=""):
    """SSE 事件生成器。

    audit_context 非空：本地审计问答；target_context 非空：数据库/应用/日志等
    非主机目标的事实分析。两者都注入真实数据、免主机校验、不跑远程命令。
    """
    run_id = uuid.uuid4().hex[:10]
    _stop_flags[run_id] = False
    evidence = audit_context or target_context
    host = store.get_host(host_id)
    profile = store.get_profile(profile_id)
    steps_total = int(max_retry)
    target_name = target_label or (("本地审计文档" if audit_context else "") or
                                   ((host or {}).get("name", host_id)))

    yield {"type": "start", "run_id": run_id, "mode": "diagnose",
           "host": target_name,
           "model": model or (profile or {}).get("default_model", ""),
           "prompt": prompt, "read_only": read_only,
           "auto_approve": auto_approve, "max_retry": steps_total}

    if not profile or not profile.get("api_key"):
        yield _notice("error", "无法开始诊断", "尚未配置 AI 接入的 API Key。",
                      "请点击右上角「API修改」，填写 API 地址与 Key 并保存。")
        yield {"type": "progress", "text": "已结束", "state": "finished"}
        yield {"type": "done", "text": ""}
        return
    if not host and not evidence:
        yield _notice("error", "无法开始诊断", "分析目标不存在或已被删除。",
                      "请重新选择纳管主机或已录入的数据库 / 应用 / 日志目标。")
        yield {"type": "progress", "text": "已结束", "state": "finished"}
        yield {"type": "done", "text": ""}
        return

    ai = AIClient(profile, model=model)
    sys_prompt = SYSTEM_PROMPT + (AUDIT_RULE if audit_context
                                  else (DATA_RULE if target_context else ""))
    if evidence:
        # 审计文档 / 非主机目标：事实已随请求给出，不连主机、不跑远程命令
        if audit_context:
            fact_text = audit_context
            instruction = "请按审计规则直接给出结论（command 为空、done=true）。"
        else:
            fact_text = target_context
            instruction = "请严格基于【分析目标数据】给出结论（command 为空、done=true）。"
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content":
                "分析目标：%s\n用户要求：%s\n\n%s\n\n%s"
                % (target_name, prompt, fact_text, instruction)},
        ]
        hname = target_name
        htarget = "-"
    else:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content":
                "目标主机：%s（%s@%s:%s）\n用户要求：%s\n\n请开始诊断，输出第一步的 JSON。" %
                (host.get("name"), host.get("username"), host.get("host"),
                 host.get("port", 22), prompt)},
        ]
        hname, htarget = host.get("name"), "%s@%s:%s" % (
            host.get("username"), host.get("host"), host.get("port", 22))
    blocked_count = 0

    for step in range(1, steps_total + 1):
        if _stop_flags.get(run_id):
            yield {"type": "progress", "step": step, "text": "已手动停止", "state": "stopped"}
            break
        yield {"type": "progress", "step": step, "total": steps_total,
               "text": "正在分析第 %d 步" % step, "state": "thinking"}

        # --- AI 推理（独立线程 + 心跳） ---
        box, ev = {}, threading.Event()
        _run_in_thread(lambda: ai.chat_json(messages), box, ev)
        for hb in _heartbeat_wait(ev, "AI 正在分析第 %d 步" % step, AI_WAIT_TIMEOUT):
            yield hb
        if "e" in box:
            yield _notice("error", "AI 调用失败", _friendly_ai_error(box["e"]), str(box["e"]))
            break
        if "r" not in box:
            yield _notice("error", "AI 响应超时", "本轮分析等待过久，已跳过，请缩小问题范围后重试。")
            break
        parsed, reasoning, raw = box["r"]

        if not parsed:
            # 不再把一整坨原始 JSON 当错误抛给用户：清洗成人话再呈现
            text = humanize_raw(raw)
            yield _notice("warn", "AI 回复格式不标准",
                          "本轮回复没能按约定的结构化格式返回，系统已按纯文本呈现，未执行任何命令。",
                          "通常是模型偶发输出异常，直接点「开始执行」重试即可。")
            if text:
                yield {"type": "summary", "title": "AI 原始答复（未解析为可执行步骤）",
                       "text": text, "tone": "warn"}
            break

        # _recovered（内部容错自动修复）是正常路径，静默处理，不再弹提示打扰用户
        thought = (parsed.get("thought") or "").strip()
        cmd = (parsed.get("command") or "").strip()
        done = bool(parsed.get("done"))
        summary = (parsed.get("summary") or "").strip()

        # 审计 / 非主机目标不允许落到远程命令：模型偶尔不听话时，拦下并让它直接总结
        if evidence and cmd:
            rule = ("本次是本地审计查询，禁止执行任何远程命令。请立即基于【本地审计文档】给出完整结论："
                    "command 填空字符串、done=true、summary 按【总览】【明细】【风险】【建议】分段，"
                    "逐条写清时间、用户、主机、操作。") if audit_context else (
                    "本次只分析已给出的【分析目标数据】，禁止执行任何远程命令。请立即基于这些真实数据"
                    "给出完整结论：command 填空字符串、done=true，按【现象/现状】【证据】【风险】"
                    "【建议】【验证方法】分段，数据里没有的写「数据中未提供」。")
            messages.append({"role": "user", "content": rule})
            continue

        if reasoning:
            yield {"type": "reasoning", "step": step, "text": reasoning[:4000],
                   "chars": len(reasoning)}
        if thought:
            yield {"type": "thought", "step": step, "text": thought}

        if done:
            yield {"type": "progress", "step": step, "total": steps_total,
                   "text": "诊断完成，已生成结论", "state": "done"}
            yield {"type": "summary", "title": "AI 结论与建议",
                   "text": _final_summary(ai, summary, thought, raw)}
            break

        if not cmd:
            messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                             "你既没有给出命令也没有结束。若已完成请 done=true 并写 summary；否则给出下一条命令。"})
            continue

        level, reasons = safety.classify(cmd)
        allowed, need_confirm, _, _ = safety.check(cmd, read_only, auto_approve)

        yield {"type": "step", "step": step, "command": cmd, "thought": thought,
               "risk": level, "reasons": reasons, "status": "pending"}

        if not allowed:
            blocked_count += 1
            yield {"type": "step_update", "step": step, "status": "blocked",
                   "output": "只读模式下未执行该命令。\n原因：" + "；".join(reasons),
                   "exit_code": None, "elapsed": 0}
            messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                "该命令被只读模式拦截，请改用只读命令收集证据，或直接基于已有信息给出结论。"
                + ("已连续被拦截 %d 次，本轮禁止再给出任何写命令，请立即 done=true 给出结论。" % blocked_count
                   if blocked_count >= 2 else "")})
            continue
        blocked_count = 0

        # --- 危险命令审批（心跳 + 硬超时，绝不无限期挂起） ---
        if need_confirm:
            confirm_id = uuid.uuid4().hex[:10]
            yield {"type": "confirm_required", "step": step, "confirm_id": confirm_id,
                   "command": cmd, "risk": level, "reasons": reasons,
                   "purpose": thought, "timeout": CONFIRM_TIMEOUT}
            evc = request_confirm(confirm_id, cmd, reasons, level)
            for hb in _heartbeat_wait(evc, "等待你确认危险命令", CONFIRM_TIMEOUT, interval=2.0):
                hb["text"] = "等待你确认危险命令（%ss 后自动拒绝）" % hb.get("remaining", 0)
                yield hb
            with _pending_lock:
                item = _pending.pop(confirm_id, {})
            responded = evc.is_set()
            approved = responded and bool(item.get("approved"))
            if not responded:
                yield {"type": "confirm_timeout", "step": step,
                       "text": "确认超时，已自动拒绝该命令"}
            if not approved:
                yield {"type": "step_update", "step": step, "status": "skipped",
                       "output": "超时自动拒绝" if not responded else "用户拒绝执行该命令",
                       "exit_code": None, "elapsed": 0}
                messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
                messages.append({"role": "user", "content":
                                 "该命令未获批准执行，请不要再尝试同类变更，改用只读排查或基于已有信息给出结论。"})
                continue

        yield {"type": "progress", "step": step, "total": steps_total,
               "text": "正在执行第 %d 步命令" % step, "state": "running"}
        yield {"type": "step_update", "step": step, "status": "running"}

        # AI 代执行的命令同样计入审计，标明发起用户，避免「谁改的」查不到
        try:
            audit.write_event(
                {"user": user, "ip": ip, "host": hname, "target": htarget,
                 "sid": run_id},
                audit.sanitize(cmd), "AI_CMD", "ai")
        except Exception:
            pass

        # --- SSH 执行（独立线程 + 心跳） ---
        ebox, eev = {}, threading.Event()
        _run_in_thread(lambda: manager.exec(host_id, cmd, timeout=45), ebox, eev)
        for hb in _heartbeat_wait(eev, "正在执行第 %d 步命令" % step, EXEC_WAIT_TIMEOUT):
            yield hb
        if "e" in ebox or "r" not in ebox:
            err = ebox.get("e") or RuntimeError("命令执行超时或无返回")
            yield {"type": "step_update", "step": step, "status": "error",
                   "output": "执行失败：%s" % err, "exit_code": None, "elapsed": 0}
            feedback = "命令 `%s` 执行失败：%s" % (cmd, err)
        else:
            r = ebox["r"]
            yield {"type": "step_update", "step": step, "status": "done",
                   "output": (r["stdout"] or "（无输出）") +
                             (("\n[stderr]\n" + r["stderr"]) if r["stderr"].strip() else ""),
                   "exit_code": r["exit_code"], "elapsed": r["elapsed"]}
            feedback = "命令：`%s`\n%s" % (cmd, _fmt_result(r))

        messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
        messages.append({"role": "user", "content": feedback + "\n\n据此给出下一步 JSON；若已能下结论则 done=true 并写 summary。"})
        if len(messages) > 16:
            messages = [messages[0], messages[1]] + messages[-14:]
    else:
        # 跑满步数：给「阶段结论」，标题与正文都解释清楚含义，不再丢一句「请缩小范围」
        yield {"type": "progress", "step": steps_total, "total": steps_total,
               "text": "排查已较充分，正在汇总证据生成结论", "state": "summing"}
        sbox, sev = {}, threading.Event()
        messages.append({"role": "user", "content":
            "已达到最大步数，禁止再执行命令。请立即基于上面已有的命令输出给出最终结论："
            "command 填空字符串、done=true、summary 用【现象】【证据】【根因】【建议】【验证方法】分段，"
            "每个小节都要写成完整句子。"})
        _run_in_thread(lambda: ai.chat_json(messages, max_tokens=6144), sbox, sev)
        for hb in _heartbeat_wait(sev, "正在汇总结论", AI_WAIT_TIMEOUT):
            yield hb
        intro = ("本次排查步骤较多，已在 %d 步时汇总现有证据给出结论。"
                 "下面内容基于已收集到的真实输出整理；若还需更深入分析，"
                 "可把问题拆得更具体后重新发起。\n\n" % steps_total)
        try:
            final, _, raw = sbox.get("r", (None, "", ""))
            base = ((final or {}).get("summary") or (final or {}).get("thought")
                    or humanize_raw(raw))
            text = _final_summary(ai, base, raw=raw)
            yield {"type": "summary", "title": "阶段结论 · 已排查 %d 步" % steps_total,
                   "text": intro + text, "tone": "warn"}
        except Exception as e:
            yield {"type": "summary", "title": "阶段结论 · 已排查 %d 步" % steps_total,
                   "text": intro + "结论汇总失败：%s" % e, "tone": "warn"}

    _stop_flags.pop(run_id, None)
    yield {"type": "progress", "text": "执行结束", "state": "finished"}
    yield {"type": "done", "text": ""}


def ask(prompt, profile_id=None, model=None, audit_context="",
        target_context="", target_label=""):
    """纯问答（不连主机）：审计摘录 / 非主机目标的真实数据作为事实依据。"""
    yield {"type": "start", "mode": "ask", "text": "AI 正在思考…"}
    profile = store.get_profile(profile_id)
    if not profile or not profile.get("api_key"):
        yield _notice("error", "无法提问", "尚未配置 AI 接入的 API Key。",
                      "请点击右上角「API修改」填写并保存。")
        yield {"type": "done", "text": ""}
        return

    ai = AIClient(profile, model=model)
    if audit_context:
        sys = ("你是运维审计分析助手。下面的【本地审计文档】摘录来自本机 audit/sessions/ 目录，"
               "每个 用户名_时间.txt 文件对应用户一次「连接 → 断开」的完整会话窗口，"
               "是本次回答的**唯一事实依据**。\n"
               "用中文按【总览】【明细】【风险】【建议】分段，每条内容单独一行；"
               "【明细】逐条写清「时间 · 用户 · 主机 · 操作」，时间、命令、主机名逐字照抄；"
               "文档里没有的人或事必须写「审计文档中未找到」，严禁编造、推测或把示例当事实；"
               "可以统计次数、归纳风险并给出可执行建议。不要输出 Markdown 代码围栏，"
               "不要半句话结束。\n\n" + audit_context + "\n\n用户问题：")
    elif target_context:
        sys = ("你是运维数据分析助手。下面的【分析目标数据】由纳管主机真实采集或用户录入生成，"
               "是本次回答的**唯一事实依据**。\n"
               "分析目标：%s\n用中文按【现象/现状】【证据】【风险】【建议】【验证方法】分段，"
               "每条内容单独一行；数字、主机、配置、日志必须逐字引用数据，没有的写「数据中未提供」，"
               "严禁编造、推测或把示例当事实。不要输出 Markdown 代码围栏，不要半句话结束。"
               "\n\n%s\n\n用户问题：" % (target_label or "分析目标", target_context))
    else:
        sys = ("你是资深 Linux 运维专家。用中文回答，结构清晰：作用 → 关键参数（带示例）→ 常用组合 → "
               "注意事项。涉及危险参数（rm -rf、chmod 777、> 覆盖、kill -9、dd）须明确警示。"
               "每个小节都要写完整，禁止在半句话处结束，不要输出 Markdown 代码围栏。"
               "用户问题：")
    q = queue.Queue()

    def worker():
        try:
            for chunk in ai.chat_stream(
                    [{"role": "system", "content": sys},
                     {"role": "user", "content": prompt}], max_tokens=6000):
                q.put(("delta", chunk))
            if getattr(ai, "truncated", False):
                q.put(("truncated", ""))
            q.put(("done", ""))
        except Exception as e:
            q.put(("error", str(e)))

    threading.Thread(target=worker, daemon=True).start()
    started = time.time()
    first = True
    slow_notified = False
    while True:
        try:
            kind, payload = q.get(timeout=HEARTBEAT_INTERVAL)
        except queue.Empty:
            elapsed = int(time.time() - started)
            yield {"type": "heartbeat", "wait": "ask", "elapsed": elapsed,
                   "text": "AI 正在思考（已等待 %ds）…" % elapsed}
            if elapsed >= 45 and not slow_notified:
                slow_notified = True
                yield _notice("info", "模型仍在推理",
                              "该模型会先生成思考过程，首次回答可能需要十几秒到一分钟，请稍候。",
                              "若持续无响应，可点「停止」后重试。")
            continue
        if kind == "delta":
            if first:
                first = False
                yield {"type": "answer_start", "text": ""}
            yield {"type": "answer_delta", "text": payload}
        elif kind == "error":
            yield _notice("error", "AI 调用失败",
                          _friendly_ai_error(Exception(payload)), payload)
            break
        elif kind == "truncated":
            yield _notice("warn", "回答可能未完整生成",
                          "模型达到单次输出上限，最后一段可能被截断，可缩小问题范围后重新提问。")
        else:
            break
    yield {"type": "answer_done", "text": ""}
    yield {"type": "done", "text": ""}
