# -*- coding: utf-8 -*-
"""运维工作台单测：所有指标必须来自纳管主机与采集快照，不允许内置示例数字。

用法（项目根目录）：python scripts/test_workbench.py
测试用注入的 3 台主机与快照隔离运行，不连真实主机、不改真实数据文件。
"""
import copy
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import store, workbench          # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  -> " + str(extra)[:200] if extra else ""))


# ---------------- 注入测试数据：3 台纳管主机、2 份监控快照、3 条告警 ----------------
HOSTS = [
    {"id": "h1", "name": "web-01", "host": "10.0.0.11", "region": "华东一",
     "room": "A栋3楼", "biz_line": "电商交易", "app_level": "核心",
     "org": "基础架构部", "maintenance": "2028-06-01"},
    {"id": "h2", "name": "db-01", "host": "10.0.0.12", "region": "华东二",
     "room": "", "biz_line": "", "app_level": "", "org": "", "maintenance": ""},
    {"id": "h3", "name": "old-01", "host": "10.0.0.13", "region": "",
     "room": "B栋1楼", "biz_line": "企业门户", "app_level": "普通",
     "org": "企业应用部", "maintenance": "2020-01-01"},
]
NOW = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
STATE = {
    "snapshots": {"h1": {"net_errors": 0}, "h2": {"net_errors": 0}},
    "alerts": [
        {"id": "a1", "time": NOW, "level": "严重", "resource": "CPU",
         "resource_key": "cpu", "metric": "CPU 使用率", "value": "95.0%",
         "region": "华东一", "host": "web-01", "host_id": "h1",
         "ip": "10.0.0.11", "message": "CPU 使用率超阈值"},
        {"id": "a2", "time": NOW, "level": "警告", "resource": "磁盘",
         "resource_key": "disk", "metric": "磁盘使用率", "value": "88%",
         "region": "华东一", "host": "web-01", "host_id": "h1",
         "ip": "10.0.0.11", "message": "根分区使用率超阈值"},
        {"id": "a3", "time": NOW, "level": "警告", "resource": "内存",
         "resource_key": "mem", "metric": "内存使用率", "value": "90.0%",
         "region": "华东二", "host": "db-01", "host_id": "h2",
         "ip": "10.0.0.12", "message": "内存使用率超阈值"},
    ],
    "updated_at": NOW,
}

_orig_hosts, _orig_state = workbench._managed_hosts, workbench._state
workbench._managed_hosts = lambda: copy.deepcopy(HOSTS)
workbench._state = lambda: copy.deepcopy(STATE)

try:
    print("=== 1. 总览指标由纳管主机与快照算出 ===")
    ov = workbench.overview()
    metrics = {m["key"]: m["value"] for m in ov["metrics"]}
    check("主机总数 = 纳管主机数", metrics.get("hosts") == 3, metrics)
    check("有监控快照 2 台", ov.get("monitored") == 2, ov)
    check("覆盖率 = 2/3 = 66.7%", metrics.get("coverage") == 66.7, metrics)
    check("告警数 = 注入的 3 条", metrics.get("alerts") == 3, metrics)
    check("质量项 = 逐台字段检查结果 7 项", metrics.get("quality") == 7, metrics)
    check("模式为 managed（不是演示数据）", ov.get("mode") == "managed", ov.get("mode"))
    check("说明里写明纳管来源", "纳管" in ov.get("note", ""), ov.get("note"))
    check("说明里不含示例数字", "4820" not in ov.get("note", ""), ov.get("note"))

    print("=== 2. 地域维度按纳管主机地域归集 ===")
    rg = workbench.regions()
    check("三个地域（含未分配）", len(rg["rows"]) == 3, rg["rows"])
    by_name = {r["name"]: r for r in rg["rows"]}
    check("华东一 1 台 / 监控 1 / 告警 2",
          [by_name["华东一"][k] for k in ("hosts", "monitored", "alerts")] == [1, 1, 2],
          by_name.get("华东一"))
    check("华东二 1 台 / 监控 1 / 告警 1",
          [by_name["华东二"][k] for k in ("hosts", "monitored", "alerts")] == [1, 1, 1],
          by_name.get("华东二"))
    check("未分配地域 1 台 / 监控 0 / 告警 0",
          [by_name["未分配地域"][k] for k in ("hosts", "monitored", "alerts")] == [1, 0, 0],
          by_name.get("未分配地域"))
    check("合计主机数与覆盖率", rg["total"]["hosts"] == 3 and rg["total"]["rate"] == 66.7,
          rg["total"])
    check("地域告警合计与总览一致", rg["total"]["alerts"] == 3, rg["total"])

    print("=== 3. 资产质量逐台检查纳管字段 ===")
    summary = workbench.quality_summary()
    counts = {d["key"]: d["count"] for d in summary}
    check("六个维度", len(summary) == 6, summary)
    check("维度计数来自字段缺失",
          counts == {"gap": 1, "room": 1, "biz": 1, "level": 1, "org": 1, "maintenance": 2},
          counts)
    check("合计 7 项与总览一致", sum(counts.values()) == 7, counts)
    all_quality = workbench.quality("", 1, 50)
    check("全量记录 7 条", all_quality["total"] == 7, all_quality["total"])
    check("单页不超过 50 条", len(all_quality["rows"]) == 7, len(all_quality["rows"]))
    biz = workbench.quality("biz", 1, 12)
    check("业务线维度 1 项，指向 db-01",
          biz["total"] == 1 and biz["rows"][0]["host"] == "db-01", biz["rows"][:1])
    check("下钻记录是纳管主机真实字段", all(r["biz_line"] == "" and r["detail"]
                                      for r in biz["rows"]), biz["rows"][:1])
    gap = workbench.quality("gap", 1, 12)
    check("差异维度指向未采集的 old-01",
          gap["total"] == 1 and gap["rows"][0]["host"] == "old-01", gap["rows"][:1])
    check("差集归因提示存在", "人工判断" in gap["note"], gap["note"])

    print("=== 4. 24 小时监控日清来自采集告警 ===")
    res = workbench.resource_summary()
    rcounts = {r["key"]: r["count"] for r in res}
    check("四类资源类型", list(rcounts) == ["cpu", "mem", "disk", "net"], rcounts)
    check("资源告警合计 3", sum(rcounts.values()) == 3, rcounts)
    check("cpu 1 / mem 1 / disk 1 / net 0",
          rcounts == {"cpu": 1, "mem": 1, "disk": 1, "net": 0}, rcounts)
    cpu = workbench.alerts(resource="cpu", page=1, size=12)
    check("CPU 告警 1 条且指向 web-01",
          cpu["total"] == 1 and cpu["rows"][0]["host"] == "web-01", cpu["rows"][:1])
    east1 = workbench.alerts(region="华东一", page=1, size=12)
    check("华东二告警过滤生效", workbench.alerts(region="华东二")["total"] == 1)
    check("华东一告警 2 条", east1["total"] == 2, east1["total"])
    check("告警时间与级别可用", all(":" in r["time"] and r["level"] for r in cpu["rows"]),
          cpu["rows"][:1])

    print("=== 4b. 时间窗可配置：同一批告警按窗口切片 ===")
    t2 = (datetime.datetime.now() - datetime.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    t40 = (datetime.datetime.now() - datetime.timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M:%S")
    timed = {"snapshots": {}, "alerts": [
        {"time": t2, "resource_key": "cpu", "level": "警告", "region": "华东"},
        {"time": t40, "resource_key": "mem", "level": "警告", "region": "华东"},
    ], "updated_at": NOW}
    workbench._state = lambda: copy.deepcopy(timed)
    check("5 分钟窗口只保留 2 分钟前那条",
          len(workbench._recent_alerts(None, "5m")) == 1,
          workbench._recent_alerts(None, "5m"))
    check("1 小时窗口两条都在",
          len(workbench._recent_alerts(None, "1h")) == 2)
    st = (datetime.datetime.now() - datetime.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
    en = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    check("自定义区间按起止时间过滤",
          len(workbench._recent_alerts(None, "custom", st, en)) == 1)
    check("窗口列表含 6 档", len(workbench.WINDOWS) == 6,
          [w["key"] for w in workbench.WINDOWS])
    check("总览告警标签显示所选窗口",
          "5 分钟" in workbench.overview("5m")["metrics"][2]["label"])
    check("插件清单下发窗口选项",
          len(workbench.plugins()["windows"]) == 6)
    check("监控日清插件列名带窗口",
          "5 分钟" in workbench.run_plugin("monitor_daily", "5m")["columns"][1]["label"])
    # 恢复原有注入状态，后续小节不受影响
    workbench._state = lambda: copy.deepcopy(STATE)

    print("=== 4c. 实时采集指标：无告警也有真实数据 ===")
    live = {"snapshots": {"h1": {"cpu": 41.2, "mem": 75.0, "disk": 75.0,
                                  "net_errors": 11, "collected_at": NOW}},
            "alerts": [], "updated_at": NOW}
    workbench._state = lambda: copy.deepcopy(live)
    mt = workbench.live_metrics()
    check("采集成功即返回指标行（即使 0 告警）", len(mt["rows"]) == 1, mt["rows"])
    check("指标行含真实数值而非空", mt["rows"][0]["cpu"] == 41.2
          and mt["rows"][0]["net_errors"] == 11, mt["rows"][:1])
    check("未采集的主机不出现在指标表",
          all(r["host"] != "db-01" for r in mt["rows"]), mt["rows"])
    workbench._state = lambda: copy.deepcopy(STATE)

    print("=== 4d. 资产台账：已填参数可见，与质量清单互补 ===")
    hosts_full = [
        {"id": "h1", "name": "web-01", "host": "10.0.0.1", "region": "华东",
         "room": "A栋", "biz_line": "支付", "app_level": "核心",
         "org": "基础架构部", "maintenance": "2027-12-31"},
        {"id": "h2", "name": "db-01", "host": "10.0.0.2", "region": "",
         "room": "", "biz_line": "", "app_level": "", "org": "",
         "maintenance": ""},
    ]
    workbench._managed_hosts = lambda: copy.deepcopy(hosts_full)
    workbench._state = lambda: copy.deepcopy(STATE)
    a = workbench.assets()
    check("资产台账列出全部纳管主机（不只列待治理项）",
          a["total"] == 2 and len(a["rows"]) == 2, a["total"])
    check("台账回显已填字段原值",
          a["rows"][0]["region"] == "华东" and a["rows"][0]["room"] == "A栋"
          and a["rows"][0]["app_level"] == "核心" and a["rows"][0]["org"] == "基础架构部",
          a["rows"][:1])
    check("未填字段保持为空而非编造",
          a["rows"][1]["room"] == "" and a["rows"][1]["maintenance"] == "", a["rows"][1])
    check("维保状态按有效期判定",
          a["rows"][0]["maintenance_state"] == "有效"
          and a["rows"][1]["maintenance_state"] == "未填写",
          [r["maintenance_state"] for r in a["rows"]])
    qsum = {d["key"]: d["count"] for d in workbench.quality_summary()}
    check("字段填齐的主机不计入质量清单",
          qsum["room"] == 1 and qsum["level"] == 1 and qsum["org"] == 1,
          qsum)
    check("质量清单文案说明 0 表示已填齐",
          "0" in workbench.quality()["note"] and "台账" in workbench.quality()["note"],
          workbench.quality()["note"])
    check("台账维度提示不再误导为字段为空",
          all("0 表示" in d["hint"] for d in workbench.quality_summary()[1:]),
          [d["hint"] for d in workbench.quality_summary()[1:]])
    workbench._managed_hosts = lambda: copy.deepcopy(HOSTS)

    print("=== 5. 插件基于真实数据运行 ===")
    plugs = workbench.plugins()["plugins"]
    check("内置 3 个插件", len(plugs) == 3, plugs)
    for pid, expect_rows in (("asset_governance", 6), ("monitor_daily", 4), ("coverage_check", 3)):
        r = workbench.run_plugin(pid)
        check("插件 %s 可运行且行数来自数据" % pid,
              r.get("ok") and len(r.get("rows", [])) == expect_rows, r)
    cov = workbench.run_plugin("coverage_check")
    check("覆盖率插件合计行来自纳管主机",
          any(row.get("name") == "未分配地域" for row in cov["rows"]), cov)
    check("未知插件被拒绝", workbench.run_plugin("not-exist").get("ok") is False)

    print("=== 6. 采集输出解析（样本仅为解析器输入，不写入业务数据） ===")
    sample = ("__AOC_BEGIN__1763856000__\nweb-01\n10.0.0.11\n"
              "cpu  100 0 50 800 10 0 0 0\ncpu  150 0 70 900 10 0 0 0\n"
              "MemTotal:       1000000 kB\nMemAvailable:    250000 kB\n"
              "/dev/sda1  100 50 30 75% /\n"
              "eth0: 100 100 5 2 0 0 0 0 200 100 3 1 0 0 0 0\n__AOC_END__")
    snap = workbench._parse_collect(sample)
    check("解析出主机名与地址", snap and snap["name"] == "web-01"
          and snap["address"] == "10.0.0.11", snap)
    check("CPU 使用率由两次 /proc/stat 采样算出", snap and snap["cpu"] == 41.2, snap)
    check("内存使用率 75%", snap and snap["mem"] == 75.0, snap)
    check("磁盘使用率 75%", snap and snap["disk"] == 75.0, snap)
    check("网卡错误计数 11", snap and snap["net_errors"] == 11, snap)
    check("采集输出不完整时返回 None", workbench._parse_collect("no markers") is None)

    print("=== 7. 阈值判定与 10 分钟去重 ===")
    st = {"snapshots": {}, "alerts": []}
    h = HOSTS[0]
    snap = {"cpu": 95.0, "mem": 50.0, "disk": 60.0, "net_errors": 0}
    added = workbench._append_alerts(st, h, snap, NOW)
    check("CPU 95% 生成严重告警",
          len(added) == 1 and added[0]["resource_key"] == "cpu"
          and added[0]["level"] == "严重", added)
    st["snapshots"]["h1"] = dict(snap, net_errors=0)
    again = workbench._append_alerts(st, h, dict(snap, net_errors=50), NOW)
    check("同主机同指标 10 分钟内不重复", len(again) == 1
          and again[0]["resource_key"] == "net", again)
    check("网络错误增量生成告警", again[0]["value"] == "+50", again)

    print("=== 8. 空态不编造数据 ===")
    workbench._managed_hosts = lambda: []
    workbench._state = lambda: {"snapshots": {}, "alerts": [], "updated_at": ""}
    empty = workbench.overview()
    ev = {m["key"]: m["value"] for m in empty["metrics"]}
    check("无纳管主机时四项指标均为 0",
          all(ev[k] == 0 for k in ("hosts", "coverage", "alerts", "quality")), ev)
    check("空态提示先添加主机", "尚未纳管主机" in empty["note"], empty["note"])
    check("无主机时地域表为空", workbench.regions()["rows"] == [])
    workbench._managed_hosts = lambda: copy.deepcopy(HOSTS)
    workbench._state = lambda: copy.deepcopy({"snapshots": {}, "alerts": [], "updated_at": ""})
    uncollected = workbench.overview()
    uv = {m["key"]: m["value"] for m in uncollected["metrics"]}
    check("有主机但未采集时覆盖率与告警为 0",
          uv["hosts"] == 3 and uv["coverage"] == 0 and uv["alerts"] == 0, uv)
    check("未采集提示点采集按钮", "采集监控数据" in uncollected["note"], uncollected["note"])

    print("=== 9. 只读数据源边界 ===")
    workbench._managed_hosts, workbench._state = _orig_hosts, _orig_state
    src = workbench.sources()
    check("默认模式 managed", src.get("mode") == "managed", src.get("mode"))
    check("不回传 Token 字段",
          all("token" not in (src.get(k) or {}) for k in ("cmdb", "monitor")), src)
    check("空地址测试不会发起请求", workbench.test_source("cmdb")["ok"] is False)
    check("非法地址被拒绝", workbench.test_source("__bad__")["ok"] is False)
    backup = copy.deepcopy(store.workbench_config())

    def _restore_view(cfg, kind):
        c = cfg.get(kind) or {}
        return {k: c.get(k, "") for k in ("name", "url", "token", "note")}

    try:
        saved = workbench.save_sources({
            "mode": "managed",
            "cmdb": {"name": "unit-test", "url": "http://127.0.0.1:1/api", "token": "fake-token"},
            "monitor": {"name": "unit-test-monitor", "url": ""},
        })
        check("保存后仍不下发 Token",
              all("token" not in (saved.get(k) or {}) for k in ("cmdb", "monitor")), saved)
        check("名称写入配置", store.workbench_config()["cmdb"]["name"] == "unit-test")
    finally:
        store.set_workbench({"mode": backup.get("mode", "managed"),
                             "cmdb": _restore_view(backup, "cmdb"),
                             "monitor": _restore_view(backup, "monitor")})
finally:
    workbench._managed_hosts, workbench._state = _orig_hosts, _orig_state

print()
print("结果: %d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
