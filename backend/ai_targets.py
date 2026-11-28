# -*- coding: utf-8 -*-
"""AI 助手的「分析目标」目录与事实上下文。

目标类型不再只有主机：主机 / 数据库 / 应用（中间件台账）/ 日志。
所有上下文只能来自两类来源：
  * 纳管主机的真实采集快照（workbench.live_metrics / workbench.alerts）；
  * 用户在资产、数据库、日志中心显式录入的内容（ops_center）。
没有数据就返回空上下文，绝不生成示例数字或示例日志。
凭据类内容不进入这里，密码 / Token / 密钥本体一律不返回。
"""
import json

from . import ops_center, store, workbench

TYPES = {
    "host": "主机",
    "database": "数据库",
    "application": "应用",
    "log": "日志",
}
NOTE = ("目标由纳管主机真实采集快照与用户录入台账生成；"
        "未采集、未录入时为空，不会生成模拟数据，也不返回口令与密钥。")


def _line(label, value, empty="未填写"):
    text = str(value).strip() if value is not None else ""
    return "- %s：%s" % (label, text if text else empty)


def _live_index():
    out = {}
    for row in workbench.live_metrics()["rows"]:
        name = str(row.get("host") or "")
        if name:
            out[name] = row
    return out


def _alerts_for(name):
    rows = workbench.alerts(page=1, size=5, window="24h")["rows"]
    return [r for r in rows if str(r.get("host") or "") == name][:5]


def list_targets():
    """按类型返回可选目标，供前端下拉按类型切换。"""
    groups = []
    live = _live_index()

    hosts = []
    for h in store.load().get("hosts", []):
        if not h.get("id"):
            continue
        name = h.get("name") or h.get("host") or h.get("id")
        detail = "%s@%s:%s" % (h.get("username", ""), h.get("host", ""), h.get("port", 22))
        hosts.append({"id": h.get("id"), "name": name, "region": h.get("region") or "",
                      "detail": detail, "monitored": name in live})
    groups.append({"type": "host", "label": TYPES["host"], "options": hosts})

    dbs = []
    for r in ops_center.db_assets()["rows"]:
        dbs.append({"id": r.get("id"), "name": r.get("name") or r.get("address") or r.get("id"),
                    "region": r.get("region") or "",
                    "detail": " / ".join(x for x in (r.get("engine"), r.get("address"),
                                                     r.get("host_name")) if x),
                    "monitored": r.get("cpu") is not None})
    groups.append({"type": "database", "label": TYPES["database"], "options": dbs})

    apps = []
    for r in ops_center.catalog("middleware")["rows"]:
        apps.append({"id": r.get("id"), "name": r.get("name") or r.get("id"),
                     "region": r.get("region") or "",
                     "detail": " / ".join(x for x in (r.get("engine"), r.get("address"),
                                                      r.get("status")) if x),
                     "monitored": False})
    groups.append({"type": "application", "label": TYPES["application"], "options": apps})

    logs = []
    for r in ops_center.log_sources()["rows"]:
        logs.append({"id": r.get("id"), "name": r.get("path") or r.get("id"),
                     "region": "", "detail": "%s · %s" % (r.get("host_name") or "",
                                                          r.get("category") or ""),
                     "monitored": False})
    groups.append({"type": "log", "label": TYPES["log"], "options": logs})

    return {"ok": True, "groups": groups, "note": NOTE}


def _host_context(host):
    lines = ["【主机资产（用户录入）】"]
    for key, label in (("name", "主机名称"), ("host", "地址"), ("port", "端口"),
                       ("username", "登录用户"), ("region", "地域"), ("room", "机房"),
                       ("biz_line", "业务线"), ("app_level", "应用等级"),
                       ("org", "责任组织"), ("maintenance", "维保日期"),
                       ("vendor", "品牌"), ("usage", "用途"), ("asset_age", "资产年限"),
                       ("note", "备注")):
        lines.append(_line(label, host.get(key)))
    name = host.get("name") or host.get("host") or ""
    row = _live_index().get(name)
    lines.append("【真实采集快照】")
    if not row:
        lines.append("- 尚未采集该主机的监控快照。")
    else:
        for key, label, unit in (("cpu", "CPU 使用率", "%"), ("mem", "内存使用率", "%"),
                                 ("disk", "磁盘使用率", "%"),
                                 ("net_errors", "网卡错误累计", ""), ("at", "采集时间", "")):
            value = row.get(key)
            lines.append(_line(label, ("—" if value is None else str(value) + unit), "—"))
    lines.append("【最近采集告警】")
    recent = _alerts_for(name)
    if not recent:
        lines.append("- 最近 24 小时没有该主机的采集告警。")
    else:
        for a in recent:
            lines.append("- %s · %s · %s" % (a.get("time", ""), a.get("level", ""),
                                             a.get("message", "")))
    return "\n".join(lines)


def _database_context(row):
    lines = ["【数据库资产（用户录入）】"]
    for key, label in (("name", "名称"), ("address", "地址"), ("region", "地域"),
                       ("engine", "类型/版本"), ("org", "责任组织"), ("status", "状态"),
                       ("host_name", "关联纳管主机"), ("note", "备注")):
        lines.append(_line(label, row.get(key)))
    lines.append("【关联主机的真实采集快照】")
    if row.get("cpu") is None:
        lines.append("- 未关联纳管主机，或该主机尚未采集，暂无真实指标。")
    else:
        for key, label in (("cpu", "CPU 使用率"), ("mem", "内存使用率"),
                           ("disk", "磁盘使用率"), ("at", "采集时间")):
            lines.append(_line(label, row.get(key), "—"))
    return "\n".join(lines)


def _application_context(row):
    lines = ["【应用 / 中间件台账（用户录入）】"]
    for key, label in (("name", "应用名称"), ("address", "地址"), ("region", "地域"),
                       ("engine", "版本/类型"), ("org", "责任组织"), ("status", "状态"),
                       ("note", "备注")):
        lines.append(_line(label, row.get(key)))
    lines.append("- 该台账由用户录入，系统不会为应用编造运行指标。")
    return "\n".join(lines)


def _log_context(row):
    lines = ["【日志源（用户录入的纳管主机 + 绝对路径）】"]
    for key, label in (("host_name", "所属主机"), ("path", "路径"),
                       ("category", "分类"), ("keyword", "关键字"), ("lines", "每次采集行数"),
                       ("updated_at", "更新时间")):
        lines.append(_line(label, row.get(key)))
    lines.append("【真实采集日志行】")
    entries = ops_center.query_logs(source_id=row.get("id", ""), limit=30)["rows"]
    if not entries:
        lines.append("- 未采集到日志行；没有真实内容可分析。")
    else:
        for e in entries:
            lines.append("- %s [%s] %s" % (e.get("time", ""), e.get("level", ""),
                                           str(e.get("line", ""))[:500]))
    return "\n".join(lines)


def context(target_type="", target_id=""):
    """返回可直接注入模型的真实数据文本；目标无效时 ok=False。"""
    t = str(target_type or "")
    tid = str(target_id or "")
    if t not in TYPES or not tid:
        return {"ok": False, "message": "请选择有效的分析目标"}
    catalog = list_targets()
    group = next((g for g in catalog["groups"] if g["type"] == t), None)
    option = next((o for o in (group or {}).get("options", []) if str(o.get("id")) == tid), None)
    if not option:
        return {"ok": False, "message": "分析目标不存在或已被删除，请重新选择"}
    name = option.get("name") or ""

    if t == "host":
        host = next((h for h in store.load().get("hosts", [])
                     if str(h.get("id")) == tid), None)
        if not host:
            return {"ok": False, "message": "主机不存在或已被删除"}
        text, label = _host_context(host), "主机 · " + name
    elif t == "database":
        row = next((r for r in ops_center.db_assets()["rows"]
                    if str(r.get("id")) == tid), None)
        if not row:
            return {"ok": False, "message": "数据库资产不存在或已被删除"}
        text, label = _database_context(row), "数据库 · " + name
    elif t == "application":
        row = next((r for r in ops_center.catalog("middleware")["rows"]
                    if str(r.get("id")) == tid), None)
        if not row:
            return {"ok": False, "message": "应用台账不存在或已被删除"}
        text, label = _application_context(row), "应用 · " + name
    else:
        row = next((r for r in ops_center.log_sources()["rows"]
                    if str(r.get("id")) == tid), None)
        if not row:
            return {"ok": False, "message": "日志源不存在或已被删除"}
        text, label = _log_context(row), "日志 · " + name

    return {"ok": True, "type": t, "id": tid, "label": label,
            "context": text, "note": NOTE, "length": len(text),
            "preview": json.dumps({"type": t, "name": name}, ensure_ascii=False)}
