# -*- coding: utf-8 -*-
"""运维工作台 · 基于「已纳管主机」与「真实采集的监控数据」的只读观察

口径来源：《基于 DeepSeek Harness 打造轻量级运维工作台》的呈现方式，
但**所有数值都来自本机真实数据，不内置任何假数据**：

  * CMDB 主机清单 = 系统里已经纳管的主机（config/config.json 的 hosts）
  * 监控数据     = 通过 SSH 对这些纳管主机执行只读命令采集到的快照
  * 24h 活跃告警 = 采集指标按阈值判定并保留最近 24 小时的记录
  * 资产数据质量 = 逐台纳管主机检查机房 / 业务线 / 应用等级 / 组织 / 维保字段的缺失
  * 监控覆盖率   = 有监控快照的纳管主机数 ÷ 纳管主机总数

没有纳管主机、或还没有采集过时，所有指标为 0 并显示空态提示，
绝不用示例数字填充。插件只读，不下发任何写操作。
"""
import datetime
import json
import os
import re
import threading
import urllib.request
import uuid
from urllib.parse import urlparse

from . import store

ROOT = store.ROOT
STATE_PATH = os.path.join(ROOT, "data", "workbench-monitor.json")
_STATE_LOCK = threading.RLock()

# ---------------- 固定维度定义（数值全部实时计算） ----------------
DIMENSIONS = [
    {"key": "gap", "label": "主机数差异",
     "hint": "纳管清单有、监控快照无；新增未采集或下线未清理都会产生差异，归因需人工判断"},
    {"key": "room", "label": "未设置机房",
     "hint": "统计未填机房的纳管主机（地域归集依据）；0 表示已填齐"},
    {"key": "biz", "label": "未设置业务线",
     "hint": "统计未填业务线的纳管主机（业务分组依据）；0 表示已填齐"},
    {"key": "level", "label": "未设置应用等级",
     "hint": "统计未填应用等级的纳管主机（分级治理依据）；0 表示已填齐"},
    {"key": "org", "label": "未设置组织",
     "hint": "统计未填责任组织的纳管主机（明确归属）；0 表示已填齐"},
    {"key": "maintenance", "label": "维保到期",
     "hint": "统计维保为空或已到期的纳管主机；0 表示维保均在有效期内"},
]

RESOURCE_TYPES = [
    {"key": "cpu", "label": "CPU"},
    {"key": "mem", "label": "内存"},
    {"key": "disk", "label": "磁盘"},
    {"key": "net", "label": "网络"},
]

# 告警阈值（采集指标 -> 判定）；级别沿用团队既有规则，模型不参与定级。
# 默认值只在用户没有设置时兜底；用户可在「告警策略」页面显式修改并落盘。
THRESHOLDS = {"cpu": 85.0, "mem": 85.0, "disk": 85.0, "net": 10.0}
ALERT_TEMPLATE_DEFAULT = "{metric} {value}，超过阈值（{host} / {region}）"
ALERT_LEVEL_SEVERE = 90.0
ALERT_DEDUP_MINUTES = 10

# 监控时间窗：用户可选 5 分钟 / 10 分钟 / 15 分钟 / 30 分钟 / 1 小时 / 自定义
WINDOWS = [
    {"key": "5m", "label": "最近 5 分钟", "minutes": 5},
    {"key": "10m", "label": "最近 10 分钟", "minutes": 10},
    {"key": "15m", "label": "最近 15 分钟", "minutes": 15},
    {"key": "30m", "label": "最近 30 分钟", "minutes": 30},
    {"key": "1h", "label": "最近 1 小时", "minutes": 60},
    {"key": "custom", "label": "自定义", "minutes": 0},
]
WINDOW_DEFAULT = "1h"
# 落盘时只裁剪超过该时长的告警，避免展示窗口切换把历史告警删掉
ALERT_KEEP_HOURS = 24

# 一条只读命令完成全部采集：CPU 两次采样（间隔 1s）/ 内存 / 根分区 / 网卡错误计数 /
# GPU（有 nvidia-smi 才有值，没有则为空数组，绝不填示例数字）
COLLECT_CMD = (
    "echo __AOC_BEGIN__$(date +%s)__; "
    "hostname 2>/dev/null; "
    "hostname -I 2>/dev/null | awk '{print $1}'; "
    "head -1 /proc/stat; sleep 1; head -1 /proc/stat; "
    "grep -E '^(MemTotal|MemAvailable|MemFree):' /proc/meminfo; "
    "df -P / 2>/dev/null | tail -1; "
    "cat /proc/net/dev 2>/dev/null; "
    "echo __AOC_END__; "
    "echo __AOC_GPU_BEGIN__; "
    "command -v nvidia-smi >/dev/null 2>&1 && "
    "nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,"
    "memory.used,memory.total,temperature.gpu,power.draw "
    "--format=csv,noheader,nounits 2>/dev/null || true; "
    "echo __AOC_GPU_END__"
)

SOURCE_NOTE = (
    "数据来自系统已纳管的主机：主机清单取本地配置，监控指标由 SSH 只读采集，"
    "告警按采集阈值判定，资产质量按纳管字段缺失统计。未采集时显示 0，不填假数据。"
    "工作台只看、只比对、只下钻，不下发任何写操作。"
)


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sources():
    return store.workbench_config()


def mode():
    """旧配置里的 demo 一律迁移为 managed：系统只认纳管主机的真实数据。"""
    m = _sources().get("mode", "managed")
    return "managed" if m in ("demo", "", None) else m


# ---------------- 告警策略：用户显式配置才生效，未配置时用内置兜底 ----------------
def thresholds():
    cfg = (_sources().get("alert_config") or {}).get("thresholds") or {}
    out = dict(THRESHOLDS)
    for key in out:
        try:
            value = float(cfg.get(key, out[key]))
            if 0 <= value <= 1000:
                out[key] = value
        except (TypeError, ValueError):
            continue
    return out


def alert_config():
    cfg = _sources().get("alert_config") or {}
    notices = [n for n in (cfg.get("notifications") or [])
               if isinstance(n, dict) and n.get("name")]
    return {
        "ok": True,
        "thresholds": thresholds(),
        "template": str(cfg.get("template") or ALERT_TEMPLATE_DEFAULT)[:300],
        "notifications": [{k: str(n.get(k) or "")[:120]
                           for k in ("id", "name", "type", "target", "note")}
                          for n in notices[:50]],
        "note": "告警阈值、消息模板和通知对象均由用户在此录入；系统只做判定与展示，"
                "不会自动发送通知，也不保存通知渠道的口令。",
    }


def save_alert_config(payload):
    payload = payload or {}
    cfg = _sources().get("alert_config") or {}
    th = {}
    for key, default in THRESHOLDS.items():
        raw = (payload.get("thresholds") or {}).get(key, thresholds()[key])
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
        th[key] = round(max(0.0, min(1000.0, value)), 1)
    template = str(payload.get("template") or ALERT_TEMPLATE_DEFAULT)[:300]
    notices = []
    for n in (payload.get("notifications") or [])[:50]:
        if not isinstance(n, dict) or not str(n.get("name") or "").strip():
            continue
        notices.append({
            "id": str(n.get("id") or uuid.uuid4().hex[:8])[:40],
            "name": str(n.get("name") or "")[:120],
            "type": str(n.get("type") or "其他")[:120],
            "target": str(n.get("target") or "")[:120],
            "note": str(n.get("note") or "")[:120],
        })
    cfg.update({"thresholds": th, "template": template, "notifications": notices})
    store.set_workbench({"mode": mode(), "alert_config": cfg})
    return alert_config()


# ---------------- 数据读取（测试可替换这两个函数做隔离） ----------------
def _managed_hosts():
    """返回已经纳管的主机清单（唯一 CMDB 数据源）。"""
    return [h for h in store.load().get("hosts", []) if h.get("id")]


def _empty_state():
    return {"snapshots": {}, "alerts": [], "updated_at": ""}


def _state():
    """读取本地采集快照；文件不存在时返回空状态。"""
    with _STATE_LOCK:
        if not os.path.exists(STATE_PATH):
            return _empty_state()
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "snapshots": data.get("snapshots") or {},
                "alerts": data.get("alerts") or [],
                "updated_at": data.get("updated_at", ""),
            }
        except Exception:
            return _empty_state()


def _write_state(data):
    with _STATE_LOCK:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)


def _parse_time(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(str(s), fmt)
        except Exception:
            pass
    return datetime.datetime.min


def window_bounds(window="1h", start="", end=""):
    """把窗口参数换算为 (起, 止)。custom 用起止时间，缺失端给合理缺省。"""
    now = datetime.datetime.now()
    if window == "custom":
        lo = _parse_time(start) if start else now - datetime.timedelta(hours=1)
        hi = _parse_time(end) if end else now
        if lo == datetime.datetime.min:
            lo = now - datetime.timedelta(hours=1)
        if hi == datetime.datetime.min:
            hi = now
        if hi < lo:
            lo, hi = hi, lo
        return lo, hi
    minutes = next((w["minutes"] for w in WINDOWS if w["key"] == window),
                   WINDOWS[-2]["minutes"])  # 未知值回退到 1 小时
    return now - datetime.timedelta(minutes=minutes), now


def window_label(window="1h", start="", end=""):
    if window == "custom":
        return (start or "不限") + " ~ " + (end or "现在")
    return next((w["label"] for w in WINDOWS if w["key"] == window), "最近 1 小时")


def _recent_alerts(state=None, window="1h", start="", end=""):
    """所选时间窗内的采集告警，按时间倒序；窗口完全由用户选择决定。"""
    state = state if state is not None else _state()
    lo, hi = window_bounds(window, start, end)
    rows = [a for a in state.get("alerts", [])
            if lo <= _parse_time(a.get("time")) <= hi]
    rows.sort(key=lambda a: _parse_time(a.get("time")), reverse=True)
    return rows


# ---------------- 资产质量：逐台纳管主机检查字段缺失 ----------------
def _quality_rows(hosts=None, snapshots=None):
    hosts = _managed_hosts() if hosts is None else hosts
    snapshots = _state()["snapshots"] if snapshots is None else snapshots
    today = datetime.date.today().isoformat()
    out = []
    for h in hosts:
        hname = h.get("name") or h.get("host") or h.get("id", "")
        base = {
            "host": hname,
            "ip": h.get("host", ""),
            "region": h.get("region", "") or "未分配地域",
            "room": h.get("room", "") or "",
            "biz_line": h.get("biz_line", "") or "",
            "app_level": h.get("app_level", "") or "",
            "org": h.get("org", "") or "",
            "maintenance": h.get("maintenance", "") or "",
            "monitored": "是" if h.get("id") in snapshots else "否",
        }
        checks = [
            ("gap", "监控快照缺失", base["monitored"] == "否",
             "纳管主机尚无监控快照，请点击「采集监控数据」"),
            ("room", "未设置机房", not h.get("room"),
             "纳管主机机房字段为空，无法归入地域维度"),
            ("biz", "未设置业务线", not h.get("biz_line"),
             "纳管主机业务线字段为空，无法按业务分组"),
            ("level", "未设置应用等级", not h.get("app_level"),
             "纳管主机应用等级字段为空，影响分级治理"),
            ("org", "未设置组织", not h.get("org"),
             "纳管主机责任组织字段为空，缺口无人认领"),
            ("maintenance", "维保到期", not h.get("maintenance") or
             str(h.get("maintenance"))[:10] <= today,
             "维保字段为空或已到期，需确认续保或下线"),
        ]
        for dim, label, hit, detail in checks:
            if hit:
                rec = dict(base)
                rec.update({"id": "%s-%s" % (h.get("id"), dim),
                            "dimension": dim, "dimension_label": label,
                            "detail": detail})
                out.append(rec)
    return out


def quality_summary():
    rows = _quality_rows()
    return [{"key": d["key"], "label": d["label"],
             "count": sum(1 for r in rows if r["dimension"] == d["key"]),
             "hint": d["hint"]} for d in DIMENSIONS]


def assets():
    """纳管主机资产台账：逐台列出用户已填写的资产字段原值。

    与「质量清单」互补：质量清单只列待治理项（字段都填齐时全为 0），
    本接口始终列出所有纳管主机及其已填参数，用于确认配置确实被读到。
    """
    hosts = _managed_hosts()
    snapshots = _state()["snapshots"]
    today = datetime.date.today().isoformat()
    rows = []
    for h in hosts:
        maint = str(h.get("maintenance") or "")
        if not maint:
            mstate = "未填写"
        elif maint[:10] <= today:
            mstate = "已到期"
        else:
            mstate = "有效"
        rows.append({
            "id": h.get("id", ""),
            "host": h.get("name") or h.get("host") or h.get("id", ""),
            "ip": h.get("host", ""),
            "region": h.get("region") or "",
            "room": h.get("room") or "",
            "biz_line": h.get("biz_line") or "",
            "app_level": h.get("app_level") or "",
            "org": h.get("org") or "",
            "maintenance": maint,
            "maintenance_state": mstate,
            "monitored": "是" if h.get("id") in snapshots else "否",
        })
    return {
        "ok": True,
        "rows": rows,
        "total": len(rows),
        "note": "逐台列出已纳管主机保存的资产参数原值（未填写显示为空）；"
                "这里看已填的值，「资产数据质量清单」看缺哪些字段，两者互补。",
    }


def resource_summary(window="1h", start="", end=""):
    rows = _recent_alerts(None, window, start, end)
    return [{"key": r["key"], "label": r["label"],
             "count": sum(1 for a in rows if a.get("resource_key") == r["key"])}
            for r in RESOURCE_TYPES]


def live_metrics():
    """最新一次采集到的真实指标快照，按主机列出；无快照则返回空列表。

    监控日清面板既看「告警」，也看「采到的值」——采集成功但没有超阈值时，
    告警为 0 是正常的，此时应展示真实采集值而不是一片空白。
    """
    hosts = _managed_hosts()
    snapshots = _state()["snapshots"]
    rows = []
    for h in hosts:
        snap = snapshots.get(h.get("id"))
        if not snap:
            continue
        gpus = snap.get("gpu") or []
        def avg(field):
            vals = [g[field] for g in gpus if isinstance(g.get(field), (int, float))]
            return round(sum(vals) / len(vals), 1) if vals else None
        rows.append({
            "host": h.get("name") or h.get("host") or h.get("id", ""),
            "ip": h.get("host", ""),
            "region": h.get("region") or "未分配地域",
            "cpu": snap.get("cpu"), "mem": snap.get("mem"),
            "disk": snap.get("disk"), "net_errors": snap.get("net_errors"),
            "gpu_count": len(gpus), "gpu_util": avg("util"),
            "gpu_mem_util": avg("mem_util"), "gpu_temp": avg("temp"),
            "gpu_power": avg("power"), "gpu_names": sorted({g.get("name", "") for g in gpus}),
            "at": snap.get("collected_at") or snap.get("at", ""),
        })
    rows.sort(key=lambda r: _parse_time(r.get("at")), reverse=True)
    return {
        "ok": True, "rows": rows, "updated_at": _state().get("updated_at", ""),
        "note": "显示每台纳管主机最新一次只读采集的真实指标；数值未超阈值时不产生告警属正常。"
    }


def overview(window="1h", start="", end=""):
    hosts = _managed_hosts()
    snapshots = _state()["snapshots"]
    monitored = sum(1 for h in hosts if h.get("id") in snapshots)
    total = len(hosts)
    alerts = _recent_alerts(None, window, start, end)
    quality_count = len(_quality_rows(hosts, snapshots))
    wlabel = window_label(window, start, end)
    if not total:
        note = "尚未纳管主机。请先在 SSH 终端添加主机，工作台不会自行编造数据。"
    elif not monitored:
        note = ("已纳管 %d 台主机，但还没有采集到监控快照。点「采集监控数据」"
                "从纳管主机只读采集指标；在此之前覆盖率与告警均为 0。" % total)
    else:
        note = ("数据来自已纳管的 %d 台主机（其中 %d 台有监控快照）。"
                "告警由采集指标按阈值判定，资产质量按纳管字段缺失统计；不下发任何写操作。"
                % (total, monitored))
    return {
        "ok": True,
        "mode": mode(),
        "generated_at": _now(),
        "managed": total,
        "monitored": monitored,
        "window": window, "window_label": wlabel,
        "metrics": [
            {"key": "hosts", "label": "主机总数", "value": total, "unit": "台",
             "hint": "系统已纳管主机数（不设时间窗口）"},
            {"key": "coverage", "label": "监控覆盖率",
             "value": round(100.0 * monitored / total, 1) if total else 0, "unit": "%",
             "hint": "有监控快照的纳管主机数 ÷ 纳管主机数"},
            {"key": "alerts", "label": "活跃告警（" + wlabel + "）",
             "value": len(alerts), "unit": "条",
             "hint": "采集指标超阈值的记录，统计窗口：" + wlabel},
            {"key": "quality", "label": "资产数据质量",
             "value": quality_count, "unit": "项",
             "hint": "按 6 个维度归集的纳管主机待治理项"},
        ],
        "note": note,
    }


def regions(window="1h", start="", end=""):
    hosts = _managed_hosts()
    state = _state()
    snapshots = state["snapshots"]
    rows = []
    alerts = _recent_alerts(state, window, start, end)
    by_id = {h.get("id"): h for h in hosts}
    grouped = {}
    for h in hosts:
        name = h.get("region") or "未分配地域"
        grouped.setdefault(name, []).append(h)
    for name in sorted(grouped):
        items = grouped[name]
        rows.append({
            "id": name, "name": name,
            "hosts": len(items),
            "monitored": sum(1 for h in items if h.get("id") in snapshots),
            "rate": round(100.0 * sum(1 for h in items if h.get("id") in snapshots)
                          / len(items), 1),
            "alerts": sum(1 for a in alerts if (
                (by_id.get(a.get("host_id")) or {}).get("region") or "未分配地域") == name),
        })
    total_hosts = len(hosts)
    total_mon = sum(1 for h in hosts if h.get("id") in snapshots)
    return {
        "ok": True,
        "rows": rows,
        "total": {
            "hosts": total_hosts, "monitored": total_mon,
            "rate": round(100.0 * total_mon / total_hosts, 1) if total_hosts else 0,
            "alerts": len(alerts),
        },
        "note": "地域取纳管主机保存的地域字段，未填写归入「未分配地域」；"
                "告警数为该地域纳管主机在「" + window_label(window, start, end) +
                "」内的采集告警。",
    }


def quality(dimension="", page=1, size=12):
    page, size = max(1, int(page)), min(50, max(1, int(size)))
    rows = _quality_rows()
    if dimension:
        rows = [r for r in rows if r["dimension"] == dimension]
    start = (page - 1) * size
    label = next((d["label"] for d in DIMENSIONS if d["key"] == dimension), "全部维度")
    return {
        "ok": True, "dimension": dimension or "", "label": label,
        "summary": quality_summary(), "rows": rows[start:start + size],
        "total": len(rows), "page": page, "size": size,
        "note": "逐台纳管主机检查字段缺失；全部为 0 表示字段已填齐、没有待治理项，"
                "已填的具体值请到「纳管主机资产台账」查看。差异只说明数据对不上，归因仍需人工判断。",
    }


def alerts(resource="", region="", page=1, size=12, window="1h",
           start="", end=""):
    page, size = max(1, int(page)), min(50, max(1, int(size)))
    rows = _recent_alerts(None, window, start, end)
    if resource:
        rows = [r for r in rows if r["resource_key"] == resource]
    if region:
        rows = [r for r in rows if r["region"] == region]
    offset = (page - 1) * size
    return {
        "ok": True, "resource": resource or "", "region": region or "",
        "window": window, "window_label": window_label(window, start, end),
        "summary": resource_summary(window, start, end),
        "rows": rows[offset:offset + size],
        "total": len(rows), "page": page, "size": size,
        "note": "告警来自纳管主机采集指标，级别沿用团队既有规则，模型不参与定级；"
                "窗口为「" + window_label(window, start, end) + "」。",
    }


def alert_aggregate(dimension="resource", window="1h", start="", end=""):
    """告警聚合：按用户选择的维度对所选时间窗的真实告警分组。"""
    if dimension not in ("resource", "host", "region", "level"):
        dimension = "resource"
    rows = _recent_alerts(None, window, start, end)
    grouped = {}
    for a in rows:
        key = a.get(dimension) or "未分配"
        g = grouped.setdefault(key, {"key": key, "count": 0,
                                     "severe": 0, "hosts": set(), "latest": ""})
        g["count"] += 1
        g["severe"] += 1 if a.get("level") == "严重" else 0
        if a.get("host"):
            g["hosts"].add(a["host"])
        if a.get("time", "") > g["latest"]:
            g["latest"] = a["time"]
    out = []
    for key in sorted(grouped):
        g = grouped[key]
        g["hosts"] = sorted(g["hosts"])
        out.append(g)
    return {"ok": True, "dimension": dimension, "rows": out,
            "window_label": window_label(window, start, end),
            "note": "聚合对象为采集告警；数量由所选时间窗内记录直接统计，不额外造数。"}


def alert_resources(window="1h", start="", end=""):
    """告警资源：逐台纳管主机列出是否已有监控快照与所选窗口告警数。"""
    hosts = _managed_hosts()
    snapshots = _state()["snapshots"]
    alerts = _recent_alerts(None, window, start, end)
    rows = []
    for h in hosts:
        hid = h.get("id")
        rows.append({
            "id": hid, "host": h.get("name") or h.get("host") or hid,
            "ip": h.get("host", ""),
            "region": h.get("region") or "未分配地域",
            "monitored": "是" if hid in snapshots else "否",
            "alerts": sum(1 for a in alerts if a.get("host_id") == hid),
            "collected_at": (snapshots.get(hid) or {}).get("collected_at", ""),
        })
    return {"ok": True, "rows": rows, "total": len(rows),
            "window_label": window_label(window, start, end),
            "note": "资源清单来自已纳管主机；未采集的主机告警数为 0，不表示健康。"}


def dashboard(window="1h", start="", end=""):
    """驾驶舱：所有卡片由纳管主机、采集快照、告警与资产字段实时汇总。"""
    ov = overview(window, start, end)
    metrics = {m["key"]: m for m in ov["metrics"]}
    live = live_metrics()
    ag = alert_aggregate("resource", window, start, end)
    rg = regions(window, start, end)
    qs = quality_summary()
    return {
        "ok": True, "generated_at": _now(), "window_label": ov["window_label"],
        "managed": ov["managed"], "monitored": ov["monitored"],
        "coverage": metrics["coverage"]["value"],
        "alerts": metrics["alerts"]["value"], "quality": metrics["quality"]["value"],
        "hosts": live["rows"], "regions": rg["rows"],
        "alert_top": ag["rows"][:8], "quality_top": qs,
        "note": ov["note"],
    }


# ---------------- SSH 只读采集 ----------------
def _cpu_totals(line):
    parts = line.split()
    if len(parts) < 9 or parts[0] != "cpu":
        return None
    try:
        nums = [float(x) for x in parts[1:9]]
    except ValueError:
        return None
    return sum(nums), nums[3] + nums[4]     # total, idle+iowait


def _parse_collect(text):
    """解析采集命令输出；失败返回 None，绝不用随机数代替。"""
    begin = re.search(r"__AOC_BEGIN__(\d+)__", text or "")
    end = re.search(r"__AOC_END__", text or "")
    if not begin or not end:
        return None
    lines = text.splitlines()
    i = next((n for n, l in enumerate(lines) if "__AOC_BEGIN__" in l), -1)
    if i < 0 or len(lines) < i + 5:
        return None
    name = lines[i + 1].strip()
    address = lines[i + 2].strip()
    cpu1 = _cpu_totals(lines[i + 3])
    cpu2 = _cpu_totals(lines[i + 4])
    cpu = None
    if cpu1 and cpu2:
        dt = cpu2[0] - cpu1[0]
        di = cpu2[1] - cpu1[1]
        if dt > 0:
            cpu = round(max(0.0, min(100.0, 100.0 * (1.0 - di / dt))), 1)
    total_mem = re.search(r"MemTotal:\s*(\d+)", text)
    free_mem = (re.search(r"MemAvailable:\s*(\d+)", text) or
                re.search(r"MemFree:\s*(\d+)", text))
    mem = None
    if total_mem and free_mem and int(total_mem.group(1)) > 0:
        mem = round(max(0.0, min(100.0,
                       100.0 * (1.0 - int(free_mem.group(1)) / float(total_mem.group(1))))), 1)
    percents = re.findall(r"(\d+)%", text)
    disk = float(percents[-1]) if percents else None
    net_errors = 0
    for line in lines:
        if ":" not in line or not re.match(r"^\s*\S+:\s+\d+", line):
            continue
        nums = [int(x) for x in line.split()[1:] if x.isdigit()]
        if len(nums) >= 12:
            net_errors += nums[2] + nums[3] + nums[10] + nums[11]
    # GPU 只在主机真的装了 nvidia-smi 时才有行；没有就返回空列表。
    gpu = []
    try:
        gi = next(n for n, l in enumerate(lines) if "__AOC_GPU_BEGIN__" in l)
        gj = next(n for n, l in enumerate(lines[gi + 1:], gi + 1)
                  if "__AOC_GPU_END__" in l)
        for line in lines[gi + 1:gj]:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 8:
                continue

            def num(idx):
                m = re.search(r"-?\d+(?:\.\d+)?", parts[idx])
                return float(m.group(0)) if m else None

            gpu.append({
                "index": parts[0], "name": parts[1],
                "util": num(2), "mem_util": num(3),
                "mem_used": num(4), "mem_total": num(5),
                "temp": num(6), "power": num(7),
            })
    except (StopIteration, ValueError, IndexError):
        gpu = []
    return {
        "name": name, "address": address,
        "cpu": cpu, "mem": mem, "disk": disk, "net_errors": net_errors,
        "gpu": gpu,
        "at": datetime.datetime.fromtimestamp(int(begin.group(1))).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _make_alert(host, resource_key, metric, value, message, at, level=None, severity=0.0):
    """value 是展示用的字符串（如 95.0%），定级必须用原始数值 severity。"""
    if level is None:
        level = "严重" if severity >= ALERT_LEVEL_SEVERE else "警告"
    ctx = {"metric": metric, "value": value, "resource": resource_key,
           "host": host.get("name") or host.get("host") or host.get("id", ""),
           "ip": host.get("host", ""), "region": host.get("region") or "未分配地域",
           "level": level}
    try:
        configured = str((_sources().get("alert_config") or {}).get("template") or "").strip()
        if configured:
            message = configured.format(**ctx)[:500]
    except (KeyError, ValueError, IndexError, AttributeError):
        pass  # 模板写错时保留系统默认消息，不让采集失败
    return {
        "id": uuid.uuid4().hex[:12],
        "time": at,
        "level": level,
        "resource": next((r["label"] for r in RESOURCE_TYPES if r["key"] == resource_key), resource_key),
        "resource_key": resource_key,
        "metric": metric,
        "value": value,
        "region": host.get("region") or "未分配地域",
        "host": host.get("name") or host.get("host") or host.get("id", ""),
        "host_id": host.get("id", ""),
        "ip": host.get("host", ""),
        "message": message,
    }


def _append_alerts(state, host, snap, at):
    """按用户配置（或内置兜底）阈值生成告警；同主机同指标 10 分钟内不重复。"""
    th = thresholds()
    checks = []
    if snap.get("cpu") is not None and snap["cpu"] >= th["cpu"]:
        checks.append(("cpu", "CPU 使用率", "%.1f%%" % snap["cpu"],
                       "CPU 使用率 %.1f%% 超过阈值 %.0f%%" % (snap["cpu"], th["cpu"]),
                       snap["cpu"]))
    if snap.get("mem") is not None and snap["mem"] >= th["mem"]:
        checks.append(("mem", "内存使用率", "%.1f%%" % snap["mem"],
                       "内存使用率 %.1f%% 超过阈值 %.0f%%" % (snap["mem"], th["mem"]),
                       snap["mem"]))
    if snap.get("disk") is not None and snap["disk"] >= th["disk"]:
        checks.append(("disk", "磁盘使用率", "%.0f%%" % snap["disk"],
                       "根分区使用率 %.0f%% 超过阈值 %.0f%%" % (snap["disk"], th["disk"]),
                       snap["disk"]))
    prev = state["snapshots"].get(host.get("id"), {})
    delta = (snap.get("net_errors") or 0) - (prev.get("net_errors") or 0)
    if delta > th["net"]:
        checks.append(("net", "网络错误增量", "+%d" % delta,
                       "网卡错误/丢包计数较上次采集增加 %d" % delta, 0.0))
    added = []
    for key, metric, value, message, severity in checks:
        dup = any(a.get("host_id") == host.get("id") and a.get("resource_key") == key
                  and abs((_parse_time(at) - _parse_time(a.get("time"))).total_seconds())
                  < ALERT_DEDUP_MINUTES * 60 for a in state["alerts"])
        if not dup:
            added.append(_make_alert(host, key, metric, value, message, at, severity=severity))
    state["alerts"].extend(added)
    return added


def collect():
    """对已纳管主机逐一 SSH 执行只读命令，落盘快照与告警；不修改远端。"""
    from .ssh_manager import manager          # 延迟导入，便于单测隔离

    hosts = _managed_hosts()
    state = _state()
    at = _now()
    results = []
    for h in hosts:
        hid = h.get("id")
        name = h.get("name") or h.get("host") or hid
        try:
            r = manager.exec(hid, COLLECT_CMD, timeout=18, max_chars=40000)
            snap = _parse_collect(r.get("stdout", ""))
            if not snap:
                raise RuntimeError("采集输出解析失败（exit=%s）" % r.get("exit_code"))
            snap["host_id"] = hid
            snap["collected_at"] = at
            state["snapshots"][hid] = snap
            added = _append_alerts(state, h, snap, at)
            results.append({"host_id": hid, "host": name, "ok": True,
                            "cpu": snap["cpu"], "mem": snap["mem"],
                            "disk": snap["disk"], "alerts": len(added)})
        except Exception as e:
            results.append({"host_id": hid, "host": name, "ok": False,
                            "error": "%s: %s" % (type(e).__name__, e)})
    # 落盘只裁剪超过保留期的告警；展示窗口由用户选择，不在这里过滤
    keep_cutoff = datetime.datetime.now() - datetime.timedelta(hours=ALERT_KEEP_HOURS)
    state["alerts"] = [a for a in state.get("alerts", [])
                       if _parse_time(a.get("time")) >= keep_cutoff]
    state["updated_at"] = at
    _write_state(state)
    return {
        "ok": True,
        "at": at,
        "total": len(hosts),
        "success": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
        "note": "采集仅执行只读命令（/proc、df、hostname），不对远端做任何修改。",
    }


# ---------------- 可插拔分析插件 ----------------
PLUGIN_DEFS = [
    {"id": "asset_governance", "name": "资产全量治理",
     "desc": "按机房 / 业务线 / 应用等级 / 组织 / 维保 / 差异六个维度归集纳管主机的待治理字段",
     "hint": "资产分析面向已纳管主机，不设时间窗口；点任一维度可下钻到具体主机。"},
    {"id": "monitor_daily", "name": "监控日清",
     "desc": "按 CPU / 内存 / 磁盘 / 网络展开所选时间窗内由采集指标判定的告警",
     "hint": "监控数据时效性强，按所选时间窗口看才有判断价值。"},
    {"id": "coverage_check", "name": "监控覆盖率对账",
     "desc": "以纳管主机数为分母，逐地域比对有监控快照的主机数与覆盖率",
     "hint": "覆盖率的分母为纳管主机数，分母质量决定该指标的有效性。"},
]


def plugins():
    return {"ok": True, "plugins": list(PLUGIN_DEFS), "windows": list(WINDOWS),
            "note": "插件只读、可插拔：新增分析维度只需注册一个插件定义，不改终端与审计链路。"}


def run_plugin(plugin_id, window="1h", start="", end=""):
    p = next((x for x in PLUGIN_DEFS if x["id"] == plugin_id), None)
    if not p:
        return {"ok": False, "message": "未知插件：%s" % plugin_id}
    if plugin_id == "asset_governance":
        columns = [{"key": "label", "label": "治理维度"},
                   {"key": "count", "label": "待治理项"},
                   {"key": "hint", "label": "口径说明"}]
        rows = quality_summary()
    elif plugin_id == "monitor_daily":
        columns = [{"key": "label", "label": "资源类型"},
                   {"key": "count", "label": "活跃告警（" +
                    window_label(window, start, end) + "）"}]
        rows = resource_summary(window, start, end)
    else:
        columns = [{"key": "name", "label": "地域"},
                   {"key": "hosts", "label": "纳管主机数"},
                   {"key": "monitored", "label": "有监控快照"},
                   {"key": "rate", "label": "覆盖率(%)"},
                   {"key": "alerts", "label": "告警数"}]
        rows = regions(window, start, end)["rows"]
    return {"ok": True, "id": plugin_id, "name": p["name"],
            "columns": columns, "rows": rows, "note": p["hint"], "at": _now()}


# ---------------- 只读数据源配置（可选查询接口，不下发任何写操作） ----------------
def sources():
    cfg = _sources()

    def view(kind):
        c = cfg.get(kind) or {}
        return {"name": c.get("name", ""), "url": c.get("url", ""),
                "note": c.get("note", ""), "has_token": bool(c.get("token", ""))}

    return {"ok": True, "mode": mode(),
            "cmdb": view("cmdb"), "monitor": view("monitor"), "note": SOURCE_NOTE}


def save_sources(payload):
    m = payload.get("mode", "managed")
    if m not in ("managed", "http"):
        m = "managed"
    data = {"mode": m}
    for kind in ("cmdb", "monitor"):
        v = payload.get(kind) or {}
        item = {}
        for field in ("name", "url", "note"):
            if field in v:
                item[field] = str(v[field])[:500]
        # 留空的 Key 表示不修改，避免保存时把已有只读令牌清掉
        if str(v.get("token") or "").strip():
            item["token"] = str(v["token"])[:500]
        data[kind] = item
    store.set_workbench(data)
    return sources()


def test_source(kind):
    if kind not in ("cmdb", "monitor"):
        return {"ok": False, "message": "未知数据源：%s" % kind}
    cfg = (_sources().get(kind) or {})
    url = str(cfg.get("url") or "").strip()
    if not url:
        return {"ok": False, "message": "尚未配置 %s 的只读查询地址" % kind}
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "message": "地址必须是完整的 http(s) URL"}
    headers = {"Accept": "application/json", "User-Agent": "ai-ops-console-workbench/1.0"}
    if cfg.get("token"):
        headers["Authorization"] = "Bearer " + str(cfg["token"])
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(1024 * 1024)
        data = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(data, list):
            keys = sorted(data[0].keys())[:12] if data and isinstance(data[0], dict) else []
            return {"ok": True, "message": "只读查询成功：返回 %d 条记录" % len(data),
                    "sample_keys": keys}
        if isinstance(data, dict):
            return {"ok": True, "message": "只读查询成功：返回 JSON 对象",
                    "sample_keys": sorted(data.keys())[:12]}
        return {"ok": False, "message": "接口返回的不是 JSON 数组或对象，无法归集"}
    except Exception as e:
        return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}
