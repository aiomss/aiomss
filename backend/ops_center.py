# -*- coding: utf-8 -*-
"""资产管理 / 日志中心：只存用户输入与纳管主机的真实采集结果。

边界：
  * 资产分类（网络设备、凭据、探针、数据库、中间件等）由用户录入，默认空；
    凭据条目只记录「名称 / 类型 / 责任人 / 用途」，不接收口令、Token 或密钥本体。
  * 机房 / 主机 / 巡检数据完全来自已纳管主机与 workbench 采集快照。
  * 日志来源由用户指定「纳管主机 + 绝对路径」，采集只执行只读 tail；
    日志行原文落盘，查不到就显示空态，不生成任何演示日志。
"""
import datetime
import json
import os
import re
import shlex
import threading
import uuid

from . import store, workbench

ROOT = store.ROOT
CATALOG_PATH = os.path.join(ROOT, "data", "ops-catalog.json")
LOG_PATH = os.path.join(ROOT, "data", "ops-logs.json")
DB_PATH = os.path.join(ROOT, "data", "ops-db.json")
DOMAIN_PATH = os.path.join(ROOT, "data", "ops-domains.json")
_LOCK = threading.RLock()

# 每个分类的字段由界面显式给出；不使用任意字段写入，避免把口令藏进备注之外的地方。
CATEGORIES = {
    "network_device": {"label": "网络设备",
                       "fields": ["name", "address", "region", "room", "org", "status", "note"]},
    "credential": {"label": "凭据管理",
                   "fields": ["name", "type", "owner", "scope", "status", "note"],
                   "tip": "只登记凭据台账（名称/类型/责任人/用途），不接收口令、Token 或密钥本体。"},
    "probe": {"label": "探针管理",
              "fields": ["name", "address", "region", "target", "status", "note"]},
    "database": {"label": "数据库管理",
                 "fields": ["name", "type", "version", "address", "region", "engine",
                            "env", "org", "status", "alive", "auth", "note", "host_id"]},
    "middleware": {"label": "中间件管理",
                   "fields": ["name", "address", "region", "engine", "org", "status", "note"]},
    "alert_source": {"label": "告警数据源",
                     "fields": ["name", "address", "region", "org", "status", "note"]},
}
FIELD_LABELS = {
    "name": "名称", "address": "地址", "region": "地域", "room": "机房",
    "org": "责任组织", "owner": "责任人", "scope": "用途", "type": "类型",
    "target": "采集目标", "engine": "版本/类型", "status": "状态", "note": "备注",
    "host_id": "关联纳管主机",
    "version": "版本", "env": "环境", "alive": "存活状态", "auth": "认证状态",
}
LOG_CATEGORIES = {
    "system": "系统日志", "slow": "数据慢日志", "access": "业务访问日志",
    "business": "业务日志",
}
_MAX_RECORDS = 2000
_MAX_ENTRIES = 5000
_BAD_PATH = re.compile(r"[;|&$`<>\"'\r\n\\]")
_LEVEL_RULES = [
    ("ERROR", re.compile(r"\b(ERROR|FATAL|CRITICAL|ERR|panic|Segfault|OOM)\b", re.I)),
    ("WARN", re.compile(r"\b(WARN|WARNING|timeout|refused)\b", re.I)),
    ("INFO", re.compile(r"\b(INFO|DEBUG|TRACE)\b", re.I)),
]


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default):
    with _LOCK:
        if not os.path.exists(path):
            return dict(default)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else dict(default)
        except Exception:
            return dict(default)


def _write(path, data):
    with _LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def _catalog():
    return _read(CATALOG_PATH, {"records": [], "updated_at": ""})


def _logs():
    return _read(LOG_PATH, {"sources": [], "entries": [], "updated_at": ""})


def _clean(value, limit=200):
    return str(value or "").strip()[:limit]


def _managed_host_ids():
    return {h.get("id") for h in store.load().get("hosts", []) if h.get("id")}


# ---------------- 资产管理：用户录入 + 纳管主机真实字段 ----------------
def catalog_meta():
    return {
        "ok": True,
        "categories": [{"key": k, "label": v["label"],
                        "fields": [{"key": f, "label": FIELD_LABELS.get(f, f)}
                                   for f in v["fields"]],
                        "tip": v.get("tip", "")}
                       for k, v in CATEGORIES.items()],
        "note": "这些分类由用户录入，默认空；凭据只登记台账，不接收口令与 Token。",
    }


def catalog(category="", keyword=""):
    rows = _catalog().get("records", [])
    if category:
        rows = [r for r in rows if r.get("category") == category]
    if keyword:
        kw = keyword.lower()
        rows = [r for r in rows if kw in json.dumps(r, ensure_ascii=False).lower()]
    return {"ok": True, "rows": rows[:_MAX_RECORDS], "total": len(rows),
            "note": catalog_meta()["note"]}


def upsert_catalog(payload):
    payload = payload or {}
    category = str(payload.get("category") or "")
    if category not in CATEGORIES:
        return {"ok": False, "message": "未知资产分类：" + category}
    data = _catalog()
    record = {
        "id": str(payload.get("id") or uuid.uuid4().hex[:8])[:40],
        "category": category,
        "updated_at": _now(),
    }
    for field in CATEGORIES[category]["fields"]:
        record[field] = _clean(payload.get(field))
        if category == "credential" and field in ("scope", "note", "name"):
            # 防止用户把口令粘进台账字段；口令不属于这里。
            record[field] = re.sub(r"(?i)(password|passwd|token|secret)\s*[:=]\s*\S+",
                                   "[已隐藏]", record[field])
    if not record.get("name"):
        return {"ok": False, "message": "名称不能为空"}
    records = data.get("records", [])
    replaced = False
    for i, old in enumerate(records):
        if old.get("id") == record["id"] and old.get("category") == category:
            record["created_at"] = old.get("created_at", _now())
            records[i] = record
            replaced = True
            break
    if not replaced:
        record["created_at"] = record["updated_at"]
        records.append(record)
    data["records"] = records[-_MAX_RECORDS:]
    data["updated_at"] = _now()
    _write(CATALOG_PATH, data)
    return {"ok": True, "row": record, "message": "已保存" if replaced else "已新增"}


def delete_catalog(record_id):
    data = _catalog()
    before = len(data.get("records", []))
    data["records"] = [r for r in data.get("records", []) if r.get("id") != record_id]
    data["updated_at"] = _now()
    _write(CATALOG_PATH, data)
    return {"ok": True, "removed": before - len(data["records"])}


def rooms():
    """机房维度：按纳管主机已填写的 room 字段分组，不猜测机房。"""
    assets = workbench.assets()["rows"]
    grouped = {}
    for row in assets:
        name = row.get("room") or "未填写机房"
        g = grouped.setdefault(name, {"name": name, "hosts": 0, "names": [],
                                      "regions": set(), "monitored": 0})
        g["hosts"] += 1
        g["names"].append(row.get("host") or "")
        if row.get("region"):
            g["regions"].add(row["region"])
        if row.get("monitored") == "是":
            g["monitored"] += 1
    rows = []
    for name in sorted(grouped):
        g = grouped[name]
        rows.append({"name": name, "hosts": g["hosts"], "monitored": g["monitored"],
                     "regions": sorted(g["regions"]), "names": g["names"]})
    return {"ok": True, "rows": rows, "total": len(rows),
            "note": "机房名只取纳管主机里用户已填写的字段；未填写统一归入「未填写机房」。"}


def inspection(window="1h", start="", end=""):
    """主机巡检：采集快照 + 资产字段缺失，逐台列出，结论按用户配置的阈值判定。"""
    assets = {row.get("id"): row for row in workbench.assets()["rows"]}
    live = workbench.live_metrics()["rows"]
    th = workbench.thresholds()
    quality = {}
    for q in workbench.quality("", 1, 50)["rows"]:
        quality.setdefault(q.get("host"), []).append(q.get("dimension_label"))
    alerts = workbench.alerts("", "", 1, 50, window, start, end)["rows"]
    alert_count = {}
    for a in alerts:
        alert_count[a.get("host")] = alert_count.get(a.get("host"), 0) + 1
    rows = []
    for m in live:
        host = m["host"]
        hits = []
        for key, label in (("cpu", "CPU"), ("mem", "内存"), ("disk", "磁盘")):
            if m.get(key) is not None and m[key] >= th[key]:
                hits.append(label + " " + str(m[key]) + "%")
        if m.get("gpu_count"):
            hits.append("GPU " + str(m["gpu_count"]) + " 张")
        rows.append({
            "host": host, "ip": m.get("ip", ""), "region": m.get("region", ""),
            "cpu": m.get("cpu"), "mem": m.get("mem"), "disk": m.get("disk"),
            "net_errors": m.get("net_errors"), "gpu_count": m.get("gpu_count") or 0,
            "at": m.get("at", ""), "alerts": alert_count.get(host, 0),
            "quality": quality.get(host, []),
            "conclusion": "超阈值：" + "、".join(hits) if hits else "采集值未超阈值",
        })
    return {"ok": True, "rows": rows, "total": len(rows),
            "note": "巡检结论只对采集快照做阈值比对；未采集的主机不会出现在结果中，"
                    "也不会被判成健康。"}


# ---------------- 日志中心：用户指定路径 + SSH 只读 tail ----------------
def log_meta():
    return {
        "ok": True,
        "categories": [{"key": k, "label": v} for k, v in LOG_CATEGORIES.items()],
        "note": "日志源必须指定一台已纳管主机和绝对路径；采集只执行 tail 读取，"
                "不改远端文件。没有配置或没采集过时显示空态。",
    }


def log_sources():
    data = _logs()
    rows = []
    for s in data.get("sources", []):
        host = store.get_host(s.get("host_id")) or {}
        rows.append(dict(s, host_name=host.get("name") or s.get("host_id", "")))
    return {"ok": True, "rows": rows, "total": len(rows), "note": log_meta()["note"]}


def upsert_log_source(payload):
    payload = payload or {}
    hid = str(payload.get("host_id") or "")
    if hid not in _managed_host_ids():
        return {"ok": False, "message": "请选择一台已纳管的主机"}
    path = str(payload.get("path") or "").strip()
    if not path.startswith("/") or _BAD_PATH.search(path):
        return {"ok": False, "message": "路径须为 Linux 绝对路径，且不含 shell 特殊字符"}
    category = str(payload.get("category") or "system")
    if category not in LOG_CATEGORIES:
        return {"ok": False, "message": "未知日志分类"}
    pattern = str(payload.get("trace_pattern") or "")[:200]
    if pattern:
        try:
            re.compile(pattern)
        except re.error:
            return {"ok": False,
                    "message": "链路提取正则不合法，请检查括号与转义；"
                               "正确写法示例：(trace[=:]([a-z0-9-]+))"}
    data = _logs()
    source = {
        "id": str(payload.get("id") or uuid.uuid4().hex[:8])[:40],
        "host_id": hid, "path": path[:500], "category": category,
        "keyword": _clean(payload.get("keyword"), 200),
        "trace_pattern": pattern,
        "lines": max(10, min(1000, int(payload.get("lines") or 200))),
        "enabled": bool(payload.get("enabled", True)),
        "updated_at": _now(),
    }
    sources = data.get("sources", [])
    done = False
    for i, old in enumerate(sources):
        if old.get("id") == source["id"]:
            sources[i] = source
            done = True
            break
    if not done:
        sources.append(source)
    data["sources"] = sources[-200:]
    _write(LOG_PATH, data)
    return {"ok": True, "row": source, "message": "已保存日志源"}


def delete_log_source(source_id):
    data = _logs()
    before = len(data.get("sources", []))
    data["sources"] = [s for s in data.get("sources", []) if s.get("id") != source_id]
    data["entries"] = [e for e in data.get("entries", [])
                       if e.get("source_id") != source_id]
    data["updated_at"] = _now()
    _write(LOG_PATH, data)
    return {"ok": True, "removed": before - len(data.get("sources", []))}


def _level_of(line):
    for level, rule in _LEVEL_RULES:
        if rule.search(line):
            return level
    return "其他"


def collect_logs(source_id=""):
    """对用户配置的日志源执行只读 tail；失败原因如实返回，不伪造日志行。"""
    from .ssh_manager import manager

    data = _logs()
    sources = [s for s in data.get("sources", [])
               if s.get("enabled", True) and (not source_id or s.get("id") == source_id)]
    if not sources:
        return {"ok": True, "total": 0, "success": 0, "failed": 0, "results": [],
                "note": "没有启用的日志源。请先在「日志管理」添加纳管主机上的日志路径。"}
    entries = data.get("entries", [])
    results = []
    at = _now()
    for s in sources:
        host_id = s.get("host_id")
        path = s.get("path", "")
        try:
            cmd = "tail -n %d -- %s" % (int(s.get("lines") or 200), shlex.quote(path))
            r = manager.exec(host_id, cmd, timeout=15, max_chars=200000)
            if r.get("exit_code") not in (0, None):
                raise RuntimeError("tail 退出码 %s" % r.get("exit_code"))
            text = r.get("stdout", "") or ""
            got = 0
            pattern = None
            trace_warning = ""
            if s.get("trace_pattern"):
                try:
                    pattern = re.compile(s["trace_pattern"])
                except re.error as exc:
                    # 历史源里可能有写坏的正则：链路留空即可，日志行照常采集。
                    pattern = None
                    trace_warning = "链路提取正则不合法，本次未提取链路标识：%s" % exc
            for line in text.splitlines():
                raw = line.strip()
                if not raw:
                    continue
                trace = ""
                if pattern:
                    m = pattern.search(raw)
                    trace = (m.group(1) if m and m.lastindex else
                             (m.group(0) if m else ""))
                entries.append({
                    "id": uuid.uuid4().hex[:12], "time": at,
                    "source_id": s.get("id", ""), "host_id": host_id,
                    "host": (store.get_host(host_id) or {}).get("name") or host_id,
                    "path": path, "category": s.get("category", "system"),
                    "level": _level_of(raw), "trace": _clean(trace, 120),
                    "line": raw[:1500],
                })
                got += 1
            result = {"source_id": s.get("id"), "path": path,
                      "ok": True, "lines": got}
            if trace_warning:
                result["warning"] = trace_warning
            results.append(result)
        except Exception as e:
            results.append({"source_id": s.get("id"), "path": path, "ok": False,
                            "error": "%s: %s" % (type(e).__name__, e)})
    data["entries"] = entries[-_MAX_ENTRIES:]
    data["updated_at"] = at
    _write(LOG_PATH, data)
    return {"ok": True, "at": at, "total": len(sources),
            "success": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results,
            "note": "仅执行只读 tail；采集时间与日志原文一并落盘，未匹配到内容即为 0 行。"}


def query_logs(category="", level="", keyword="", source_id="", limit=200):
    limit = max(1, min(1000, int(limit or 200)))
    rows = _logs().get("entries", [])
    if category:
        rows = [e for e in rows if e.get("category") == category]
    if level:
        rows = [e for e in rows if e.get("level") == level]
    if source_id:
        rows = [e for e in rows if e.get("source_id") == source_id]
    if keyword:
        kw = keyword.lower()
        rows = [e for e in rows
                if kw in (e.get("line", "") + e.get("host", "") + e.get("path", "")).lower()]
    rows.sort(key=lambda e: (e.get("time", ""), e.get("id", "")), reverse=True)
    return {"ok": True, "rows": rows[:limit], "total": len(rows),
            "note": "日志内容为用户配置路径下真实采集到的原文，无配置或无采集结果时为空。"}


def log_stats():
    data = _logs()
    entries = data.get("entries", [])
    levels, cats = {}, {}
    for e in entries:
        levels[e.get("level", "其他")] = levels.get(e.get("level", "其他"), 0) + 1
        cats[e.get("category", "system")] = cats.get(e.get("category", "system"), 0) + 1
    alerts = []
    for s in data.get("sources", []):
        kw = str(s.get("keyword") or "").strip()
        if not kw:
            continue
        hits = [e for e in entries if e.get("source_id") == s.get("id")
                and kw.lower() in e.get("line", "").lower()]
        if hits:
            alerts.append({"source_id": s.get("id"), "keyword": kw,
                           "host": hits[-1].get("host", ""),
                           "path": s.get("path", ""), "count": len(hits),
                           "sample": hits[-1].get("line", "")[:300],
                           "time": hits[-1].get("time", "")})
    alerts.sort(key=lambda x: x["count"], reverse=True)
    return {"ok": True, "levels": levels, "categories": cats, "alerts": alerts,
            "total": len(entries), "updated_at": data.get("updated_at", ""),
            "note": "日志告警由用户在日志源里填写的关键词对真实采集行匹配得出，"
                    "未配置关键词则没有日志告警。"}


def log_traces(keyword="", limit=100):
    rows = _logs().get("entries", [])
    grouped = {}
    for e in rows:
        trace = e.get("trace") or ""
        if not trace:
            continue
        if keyword and keyword.lower() not in (
                trace + e.get("line", "") + e.get("host", "")).lower():
            continue
        g = grouped.setdefault(trace, {"trace": trace, "count": 0, "hosts": set(),
                                       "latest": "", "sample": e.get("line", "")[:300]})
        g["count"] += 1
        g["hosts"].add(e.get("host", ""))
        if e.get("time", "") > g["latest"]:
            g["latest"] = e["time"]
            g["sample"] = e.get("line", "")[:300]
    out = []
    for key in sorted(grouped):
        g = grouped[key]
        g["hosts"] = sorted(g["hosts"])
        out.append(g)
    return {"ok": True, "rows": out[:limit], "total": len(out),
            "note": "链路标识由用户在日志源里配置的提取正则从真实日志行中提取；"
                    "未配置正则时该页为空。"}


# ---------------- 主机台账：脱敏导出 / 逐条校验导入 ----------------
def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _live_index():
    """最新采集快照索引：只用 workbench 已采到的真实行建索引。"""
    idx = {}
    for row in workbench.live_metrics().get("rows", []):
        for key in (row.get("host"), row.get("ip")):
            if key:
                idx[str(key)] = row
    return idx


def _live_for(host, idx=None):
    idx = idx if idx is not None else _live_index()
    if not host:
        return None
    for key in (host.get("name"), host.get("id"), host.get("host"),
                host.get("address")):
        if key and str(key) in idx:
            return idx[str(key)]
    return None


def _sanitize_host(host, live=None):
    """返回可安全下发 / 导出的主机行：绝不携带 password 或 password_enc。"""
    live = live or {}
    try:
        port = int(host.get("port") or 22)
    except (TypeError, ValueError):
        port = 22
    return {
        "id": host.get("id", ""),
        "name": host.get("name") or host.get("host") or host.get("id", ""),
        "host": host.get("host", ""),
        "port": port,
        "username": host.get("username", ""),
        "has_password": bool(host.get("password_enc") or host.get("password")),
        "note": host.get("note", ""),
        "region": host.get("region", ""),
        "room": host.get("room", ""),
        "biz_line": host.get("biz_line", ""),
        "app_level": host.get("app_level", ""),
        "org": host.get("org", ""),
        "maintenance": host.get("maintenance", ""),
        "vendor": host.get("vendor", ""),
        "usage": host.get("usage", ""),
        "asset_age": host.get("asset_age", ""),
        "cpu": live.get("cpu"),
        "mem": live.get("mem"),
        "disk": live.get("disk"),
        "at": live.get("at") or None,
        "monitored": "是" if live else "否",
    }


def hosts():
    idx = _live_index()
    rows = [_sanitize_host(h, _live_for(h, idx))
            for h in store.load().get("hosts", [])]
    return {"ok": True, "rows": rows, "total": len(rows),
            "note": "主机清单来自已纳管主机；cpu/mem/disk/at 取 workbench 最新真实采集快照，"
                    "未采集为 null、monitored 为否。任何口令与口令散列都不会出现在结果里。"}


def hosts_export():
    """导出载荷（app.py 负责加 attachment 响应头）；只含脱敏后的主机行。"""
    return {"exported_at": _now(), "hosts": hosts()["rows"],
            "note": "导出内容为已纳管主机的脱敏台账与最新真实采集指标；"
                    "不包含密码、密码散列或任何凭据本体。"}


def import_hosts(payload):
    payload = payload or {}
    items = payload.get("hosts")
    if not isinstance(items, list):
        return {"ok": False, "imported": 0, "skipped": 0, "total": 0,
                "errors": [{"index": -1, "error": "hosts 必须是数组"}],
                "note": "请提供 hosts 数组；每条单独校验，失败不影响其它条目。"}
    imported, skipped, errors = 0, 0, []
    current = store.load().get("hosts", [])
    for i, item in enumerate(items):
        label = ""
        try:
            if not isinstance(item, dict):
                raise ValueError("条目必须是对象")
            address = str(item.get("host") or "").strip()
            username = str(item.get("username") or "").strip()
            label = str(item.get("name") or address)[:60]
            if not address:
                raise ValueError("host（主机地址）不能为空")
            if not username:
                raise ValueError("username（用户名）不能为空")
            try:
                port = int(item.get("port") or 22)
            except (TypeError, ValueError):
                raise ValueError("port 必须是数字")
            if not 1 <= port <= 65535:
                raise ValueError("port 必须在 1~65535 之间")
            # 地址 + 用户名定位同一台已纳管主机：不传密码时保留其原密码。
            existing = next((h for h in current
                             if h.get("host") == address and h.get("username") == username), None)
            rec = {
                "id": (existing or {}).get("id"),
                "name": str(item.get("name") or address)[:200],
                "host": address[:200],
                "username": username[:100],
                "port": port,
                "note": str(item.get("note") or "")[:500],
                "region": str(item.get("region") or "")[:100],
                "room": str(item.get("room") or "")[:100],
                "biz_line": str(item.get("biz_line") or "")[:100],
                "app_level": str(item.get("app_level") or "")[:100],
                "org": str(item.get("org") or "")[:100],
                "maintenance": str(item.get("maintenance") or "")[:20],
                "vendor": str(item.get("vendor") or "")[:100],
                "usage": str(item.get("usage") or "")[:100],
                "asset_age": str(item.get("asset_age") or "")[:50],
            }
            if item.get("password"):
                rec["password"] = str(item["password"])[:200]
            store.upsert_host(rec)
            current = store.load().get("hosts", [])
            imported += 1
        except Exception as e:
            skipped += 1
            errors.append({"index": i, "name": label, "error": str(e)[:200]})
    return {"ok": True, "imported": imported, "skipped": skipped, "total": len(items),
            "errors": errors,
            "note": "逐条校验 host/username/port，失败条目不阻断其它条目；"
                    "密码可空且不会覆盖已纳管主机的原密码，返回结果不含密码。"}


# ---------------- 大屏 / 仪表盘详情：全部由真实数据汇总 ----------------
def _distribution(host_list, field, empty_label="未填写"):
    counts = {}
    for h in host_list:
        value = str(h.get(field) or "").strip() or empty_label
        counts[value] = counts.get(value, 0) + 1
    return [{"name": k, "value": counts[k]}
            for k in sorted(counts, key=lambda x: (-counts[x], x))]


def _health_level(metric, th, severe):
    """用数值比较阈值定级，绝不对 '91.0%' 这类字符串做比较。"""
    vals = {k: _num(metric.get(k)) for k in ("cpu", "mem", "disk")}
    if any(v is not None and v >= severe for v in vals.values()):
        return "严重"
    for key, value in vals.items():
        limit = _num(th.get(key))
        if value is not None and limit is not None and value >= limit:
            return "警告"
    return "正常"


def _alert_rows(window):
    return workbench.alerts("", "", 1, 50, window, "", "").get("rows", [])


def _recent_view(alerts, limit=10):
    return [{"time": a.get("time", ""), "level": a.get("level", ""),
             "host": a.get("host", ""), "message": _clean(a.get("message"), 300)}
            for a in alerts[:limit]]


def dashboard_detail(window="1h"):
    ov = workbench.overview(window, "", "")
    metrics = {m["key"]: m["value"] for m in ov["metrics"]}
    alerts = _alert_rows(window)
    host_list = store.load().get("hosts", [])
    live = workbench.live_metrics().get("rows", [])
    th = workbench.thresholds()
    severe = _num(getattr(workbench, "ALERT_LEVEL_SEVERE", 90.0)) or 90.0
    health = [{"host": m.get("host", ""), "cpu": m.get("cpu"), "mem": m.get("mem"),
               "disk": m.get("disk"), "at": m.get("at", ""),
               "level": _health_level(m, th, severe)} for m in live]
    top = {}
    for a in alerts:
        key = a.get("resource") or a.get("resource_key") or "未分类"
        top[key] = top.get(key, 0) + 1
    regions = [{"name": (r.get("name") or "未填写地域"), "value": r.get("hosts", 0)}
               for r in workbench.regions(window, "", "")["rows"]]
    return {
        "ok": True,
        "generated_at": _now(),
        "window_label": ov["window_label"],
        "summary": {
            "managed": metrics.get("hosts", 0),
            "monitored": ov["monitored"],
            "coverage": metrics.get("coverage", 0),
            "active_alerts": metrics.get("alerts", 0),
            "db_assets": catalog("database")["total"],
            "network_devices": catalog("network_device")["total"],
            "credentials": catalog("credential")["total"],
            "log_sources": log_sources()["total"],
        },
        "distributions": {
            "vendor": _distribution(host_list, "vendor"),
            "usage": _distribution(host_list, "usage"),
            "age": _distribution(host_list, "asset_age"),
            "region": regions,
        },
        "health": health,
        "alert_top": [{"key": k, "count": top[k]}
                      for k in sorted(top, key=lambda x: (-top[x], x))[:8]],
        "recent": _recent_view(alerts),
        "note": "所有卡片来自纳管主机、用户录入字段、用户配置的资产台账与真实采集快照；"
                "分布中未填写的字段归入「未填写」，health 只列有采集快照的主机，"
                "级别按阈值数值比对得出。无数据时为空数组或 0，不编造示例。",
    }


def _parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.datetime.strptime(str(s), fmt)
        except (TypeError, ValueError):
            pass
    return datetime.datetime.min


def _alert_trend(alerts, window):
    lo, hi = workbench.window_bounds(window, "", "")
    step = 600 if (hi - lo).total_seconds() <= 3600 else 3600
    buckets = {}
    for a in alerts:
        t = _parse_dt(a.get("time"))
        if t == datetime.datetime.min:
            continue
        if step == 600:
            ts = t.replace(minute=t.minute - t.minute % 10, second=0, microsecond=0)
            label = ts.strftime("%H:%M")
        else:
            ts = t.replace(minute=0, second=0, microsecond=0)
            label = ts.strftime("%H:00")
        cur = buckets.setdefault(ts, {"label": label, "count": 0})
        cur["count"] += 1
    return [buckets[k] for k in sorted(buckets)]


def screen(window="1h"):
    ov = workbench.overview(window, "", "")
    metrics = {m["key"]: m["value"] for m in ov["metrics"]}
    alerts = _alert_rows(window)
    live = workbench.live_metrics().get("rows", [])
    managed, coverage, active = (metrics.get("hosts", 0), metrics.get("coverage", 0),
                                 metrics.get("alerts", 0))
    notice_count = len(workbench.alert_config()["notifications"])
    db_assets = catalog("database")["total"]
    kpis = [
        {"label": "纳管主机", "value": managed, "unit": "台", "tone": "info"},
        {"label": "监控覆盖率", "value": coverage, "unit": "%",
         "tone": "success" if coverage >= 80 else "warning"},
        {"label": "活跃告警", "value": active, "unit": "条",
         "tone": "danger" if active else "success"},
        {"label": "数据库资产", "value": db_assets, "unit": "个", "tone": "info"},
        {"label": "通知对象", "value": notice_count, "unit": "个", "tone": "info"},
    ]
    assets = [
        {"label": "数据库", "value": db_assets},
        {"label": "网络设备", "value": catalog("network_device")["total"]},
        {"label": "凭据台账", "value": catalog("credential")["total"]},
        {"label": "探针", "value": catalog("probe")["total"]},
        {"label": "中间件", "value": catalog("middleware")["total"]},
        {"label": "日志源", "value": log_sources()["total"]},
        {"label": "告警数据源", "value": catalog("alert_source")["total"]},
    ]
    regions = [{"name": r.get("name", ""), "hosts": r.get("hosts", 0),
                "monitored": r.get("monitored", 0)}
               for r in workbench.regions(window, "", "")["rows"]]
    return {
        "ok": True,
        "generated_at": _now(),
        "window_label": ov["window_label"],
        "kpis": kpis,
        "alerts": _recent_view(alerts),
        "alert_trend": _alert_trend(alerts, window),
        "assets": assets,
        "resources": [{"host": m.get("host", ""), "cpu": m.get("cpu"),
                       "mem": m.get("mem"), "disk": m.get("disk"),
                       "at": m.get("at", "")} for m in live],
        "regions": regions,
        "note": "KPI / 趋势 / 资源全部来自纳管主机、真实采集快照与所选时间窗内的采集告警；"
                "趋势窗口 ≤1h 按 10 分钟归集、>1h 按小时归集，无记录即空数组。"
                "通知对象为用户在告警配置里录入的对象，系统不替用户发送通知。",
    }


# ---------------- 数据库管理：用户录入 + 关联主机真实指标 ----------------
DB_KINDS = ("query", "audit", "slow")
DB_NOTE = ("查询/审核记录由用户录入或粘贴，系统不执行任何 SQL；"
           "资产关联纳管主机后展示该主机最新真实采集指标，未关联或无快照时指标为空。")


def _db():
    return _read(DB_PATH, {"records": [], "updated_at": ""})


def db_assets():
    idx = _live_index()
    rows = []
    for r in catalog("database")["rows"]:
        hid = str(r.get("host_id") or "")
        host = store.get_host(hid) if hid else None
        live = _live_for(host, idx) if host else None
        rows.append({
            "id": r.get("id", ""), "name": r.get("name", ""),
            "type": r.get("type", ""), "version": r.get("version", ""),
            "address": r.get("address", ""), "region": r.get("region", ""),
            "engine": r.get("engine", ""), "env": r.get("env", ""),
            "org": r.get("org", ""), "status": r.get("status", ""),
            "alive": r.get("alive", ""), "auth": r.get("auth", ""),
            "note": r.get("note", ""),
            "host_id": hid,
            "host_name": (host.get("name") or host.get("host") or hid) if host else "",
            # 监控接入只按「是否关联纳管主机且存在真实采集快照」判定，不凭 status 猜。
            "monitor": "内置监控" if live else "未配置",
            "cpu": live.get("cpu") if live else None,
            "mem": live.get("mem") if live else None,
            "disk": live.get("disk") if live else None,
            "at": (live.get("at") or None) if live else None,
        })
    return {"ok": True, "rows": rows, "total": len(rows), "note": DB_NOTE}


def db_records(kind=""):
    rows = _db().get("records", [])
    if kind in DB_KINDS:
        rows = [r for r in rows if r.get("kind") == kind]
    rows.sort(key=lambda r: (r.get("updated_at", ""), r.get("id", "")), reverse=True)
    return {"ok": True, "rows": rows, "total": len(rows), "note": DB_NOTE}


def upsert_db_record(payload):
    p = payload or {}
    kind = str(p.get("kind") or "query")
    if kind not in DB_KINDS:
        return {"ok": False, "message": "未知记录类型：" + kind}
    title = _clean(p.get("title"), 200)
    if not title:
        return {"ok": False, "message": "标题不能为空"}
    hid = str(p.get("host_id") or "")
    if hid and hid not in _managed_host_ids():
        return {"ok": False, "message": "host_id 必须是已纳管主机"}
    data = _db()
    record = {
        "id": str(p.get("id") or uuid.uuid4().hex[:8])[:40],
        "kind": kind, "title": title, "host_id": hid,
        "sql": str(p.get("sql") or "")[:20000],
        "result": str(p.get("result") or "")[:20000],
        "note": _clean(p.get("note"), 500),
        "status": _clean(p.get("status"), 50),
        "updated_at": _now(),
    }
    records = data.get("records", [])
    replaced = False
    for i, old in enumerate(records):
        if old.get("id") == record["id"]:
            record["created_at"] = old.get("created_at", record["updated_at"])
            records[i] = record
            replaced = True
            break
    if not replaced:
        record["created_at"] = record["updated_at"]
        records.append(record)
    data["records"] = records[-_MAX_RECORDS:]
    data["updated_at"] = _now()
    _write(DB_PATH, data)
    return {"ok": True, "row": record, "message": "已保存" if replaced else "已新增"}


def delete_db_record(record_id):
    data = _db()
    before = len(data.get("records", []))
    data["records"] = [r for r in data.get("records", []) if r.get("id") != record_id]
    data["updated_at"] = _now()
    _write(DB_PATH, data)
    return {"ok": True, "removed": before - len(data.get("records", []))}


def db_monitor(window="1h"):
    idx = _live_index()
    rows = []
    for asset in catalog("database")["rows"]:
        hid = str(asset.get("host_id") or "")
        if not hid:
            continue
        host = store.get_host(hid)
        live = _live_for(host, idx) if host else None
        if not live:
            continue
        rows.append({
            "host_id": hid,
            "host": (host.get("name") or host.get("host") or hid) if host else hid,
            "name": asset.get("name", ""), "address": asset.get("address", ""),
            "cpu": live.get("cpu"), "mem": live.get("mem"), "disk": live.get("disk"),
            "net_errors": live.get("net_errors"), "at": live.get("at", ""),
            "gpu_count": live.get("gpu_count") or 0,
        })
    return {"ok": True, "rows": rows, "total": len(rows),
            "window_label": workbench.window_label(window, "", ""), "note": DB_NOTE}


# ---------------- 告警中心：真实采集告警 + 用户通知对象 ----------------
def alert_center(window="1h"):
    state = workbench._state()
    all_alerts = state.get("alerts", [])
    alerts = workbench._recent_alerts(state, window, "", "")
    today = datetime.date.today().isoformat()
    counts = {}
    for a in alerts:
        key = a.get("resource") or a.get("resource_key") or "未分类"
        counts[key] = counts.get(key, 0) + 1
    groups = {}
    for a in alerts:
        key = a.get("region") or "未分配地域"
        groups[key] = groups.get(key, 0) + 1
    levels = {}
    for a in alerts:
        key = a.get("level") or "未分级"
        levels[key] = levels.get(key, 0) + 1
    notices = workbench.alert_config()["notifications"]
    return {
        "ok": True,
        "summary": {
            "active": len(alerts),
            "critical": sum(1 for a in alerts if a.get("level") == "严重"),
            "warning": sum(1 for a in alerts if a.get("level") == "警告"),
            "today": sum(1 for a in all_alerts
                         if str(a.get("time", ""))[:10] == today),
        },
        "top_names": [{"name": k, "count": counts[k]}
                      for k in sorted(counts, key=lambda x: (-counts[x], x))[:8]],
        "top_groups": [{"name": k, "count": groups[k]}
                       for k in sorted(groups, key=lambda x: (-groups[x], x))[:8]],
        "distribution": [{"name": k, "count": levels[k]}
                         for k in sorted(levels, key=lambda x: (-levels[x], x))],
        "recent": _recent_view(alerts),
        "notification_stats": [{"name": n.get("name", ""), "count": len(alerts)}
                               for n in notices[:50]],
        "generated_at": _now(),
        "note": "全部告警来自 workbench 对纳管主机的真实采集判定；今日按本地日期统计。"
                "通知对象来自用户配置，count 为其在所选窗口内可覆盖的活跃告警数，"
                "系统不会自动发送通知。",
    }


# ---------------- 域名告警：纳管主机上只读探测，失败如实返回 ----------------
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")


def _domains():
    return _read(DOMAIN_PATH, {"records": [], "updated_at": ""})


def domains():
    rows = []
    for d in _domains().get("records", []):
        hid = str(d.get("host_id") or "")
        host = store.get_host(hid) if hid else None
        rows.append({
            "id": d.get("id", ""), "name": d.get("name", ""),
            "tags": d.get("tags", ""), "host_id": hid,
            "host_name": (host.get("name") or host.get("host") or hid) if host else "",
            "status": d.get("status", ""), "http_status": d.get("http_status"),
            "latency_ms": d.get("latency_ms"), "ssl_expire": d.get("ssl_expire", ""),
            "checked_at": d.get("checked_at", ""),
            "enabled": bool(d.get("enabled", True)), "note": d.get("note", ""),
        })
    return {"ok": True, "rows": rows, "total": len(rows),
            "note": "域名由用户录入，探测必须在用户指定的纳管主机上执行只读命令；"
                    "未探测过时状态为空，不生成任何模拟网络结果。"}


def upsert_domain(payload):
    p = payload or {}
    name = str(p.get("name") or "").strip().lower()
    if "://" in name or not _DOMAIN_RE.match(name):
        return {"ok": False, "message": "name 必须是合法域名（不含协议与路径）"}
    hid = str(p.get("host_id") or "")
    if hid not in _managed_host_ids():
        return {"ok": False, "message": "host_id 必须是已纳管的主机"}
    data = _domains()
    old = next((r for r in data.get("records", []) if r.get("id") == p.get("id")), None)
    record = {
        "id": str(p.get("id") or uuid.uuid4().hex[:8])[:40],
        "name": name,
        "tags": str(p.get("tags") or "")[:200],
        "host_id": hid,
        "enabled": bool(p.get("enabled", True)),
        "note": _clean(p.get("note"), 300),
        # 探测结果只在真正执行过 check 后回写；新增时保持空态。
        "status": (old or {}).get("status", ""),
        "http_status": (old or {}).get("http_status"),
        "latency_ms": (old or {}).get("latency_ms"),
        "ssl_expire": (old or {}).get("ssl_expire", ""),
        "checked_at": (old or {}).get("checked_at", ""),
        "updated_at": _now(),
    }
    records = data.get("records", [])
    replaced = False
    for i, r in enumerate(records):
        if r.get("id") == record["id"]:
            records[i] = record
            replaced = True
            break
    if not replaced:
        record["created_at"] = record["updated_at"]
        records.append(record)
    data["records"] = records[-500:]
    data["updated_at"] = _now()
    _write(DOMAIN_PATH, data)
    return {"ok": True, "row": record, "message": "已保存" if replaced else "已新增"}


def delete_domain(domain_id):
    data = _domains()
    before = len(data.get("records", []))
    data["records"] = [r for r in data.get("records", []) if r.get("id") != domain_id]
    data["updated_at"] = _now()
    _write(DOMAIN_PATH, data)
    return {"ok": True, "removed": before - len(data.get("records", []))}


def _cert_date(raw):
    text = str(raw or "").strip().replace(" GMT", "").replace(" UTC", "")
    for fmt in ("%b %d %H:%M:%S %Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
    return text[:40]


def check_domains(domain_id=""):
    """在域名绑定的纳管主机上用 curl / openssl 做只读探测，不伪造任何网络结果。"""
    from .ssh_manager import manager

    data = _domains()
    targets = [d for d in data.get("records", [])
               if d.get("enabled", True) and (not domain_id or d.get("id") == domain_id)]
    at = _now()
    if not targets:
        return {"ok": True, "total": 0, "success": 0, "failed": 0, "results": [],
                "at": at,
                "note": "没有启用的域名。请先在「域名告警」添加域名并绑定纳管主机。"}
    results = []
    for d in targets:
        name, hid = d.get("name", ""), str(d.get("host_id") or "")
        code, latency, error = None, None, ""
        # 先 https，失败再 http：所有网络结果都来自远端 curl 的真实 stdout/退出码。
        for scheme in ("https", "http"):
            url = scheme + "://" + name
            cmd = ("curl -sS -o /dev/null -w '%{http_code} %{time_total}' "
                   "--max-time 12 -- " + shlex.quote(url))
            try:
                r = manager.exec(hid, cmd, timeout=18, max_chars=2000)
                if r.get("exit_code") not in (0, None):
                    raise RuntimeError("curl 退出码 %s" % r.get("exit_code"))
                parts = (r.get("stdout") or "").strip().split()
                if len(parts) < 2:
                    raise RuntimeError("curl 输出格式异常：%s" % (r.get("stdout") or "")[:80])
                code, latency = int(parts[0]), round(float(parts[1]) * 1000, 1)
                error = ""
                break
            except Exception as e:
                error = "%s: %s" % (type(e).__name__, e)
        ssl_expire = ""
        if code is not None:
            try:
                cmd = ("echo | openssl s_client -servername %s -connect %s:443 "
                       "2>/dev/null | openssl x509 -noout -enddate 2>/dev/null"
                       % (shlex.quote(name), shlex.quote(name)))
                r = manager.exec(hid, cmd, timeout=18, max_chars=2000)
                m = re.search(r"notAfter=(.+)", r.get("stdout") or "")
                if m:
                    ssl_expire = _cert_date(m.group(1))
            except Exception:
                pass  # 证书不可读时留空，绝不用猜测的到期时间填充
        ok = code is not None and 200 <= code < 400
        if not ok and not error:
            error = "HTTP 状态 %s" % code
        d["status"] = "正常" if ok else "异常"
        d["http_status"] = code
        d["latency_ms"] = latency
        d["ssl_expire"] = ssl_expire
        d["checked_at"] = at
        results.append({"id": d.get("id", ""), "name": name, "ok": ok,
                        "http_status": code, "latency_ms": latency,
                        "ssl_expire": ssl_expire, "error": error})
    data["updated_at"] = at
    _write(DOMAIN_PATH, data)
    return {"ok": True, "total": len(targets),
            "success": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results, "at": at,
            "note": "仅在上述域名绑定的纳管主机上执行 curl / openssl 只读命令；"
                    "HTTP 状态、延迟与证书到期时间均取自真实输出，连接失败如实返回 error。"}
