# -*- coding: utf-8 -*-
"""资产管理 / 监控告警 / 日志中心 / 主机台账 / 大屏 / 数据库 / 域名单测。

用法（项目根目录）：python scripts/test_ops_center.py
全部状态写入临时目录或内存；store.load/save 与 SSH exec 均被替换，
绝不污染真实 config/config.json 与 data/*，也不连接真实主机。
"""
import copy
import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import ai_targets, ops_center, ssh_manager, store, workbench  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  -> " + str(extra)[:300] if extra else ""))


HOSTS = [
    {"id": "h1", "name": "web-01", "host": "10.0.0.11", "username": "root",
     "password_enc": "ENC(secret123)", "region": "华东一",
     "room": "A栋3楼", "biz_line": "电商交易", "app_level": "核心",
     "org": "基础架构部", "maintenance": "2028-01-01", "vendor": "华为",
     "usage": "业务服务器", "asset_age": "2年"},
    {"id": "h2", "name": "db-01", "host": "10.0.0.12", "username": "root",
     "region": "华东二", "room": "", "biz_line": "", "app_level": "",
     "org": "", "maintenance": ""},
]
NOW = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
STATE = {"snapshots": {"h1": {"cpu": 91.0, "mem": 60.0, "disk": 70.0,
                              "net_errors": 3, "gpu": [
                                  {"index": "0", "name": "A100", "util": 72.3,
                                   "mem_util": 55.0, "mem_used": 40.0,
                                   "mem_total": 80.0, "temp": 34.0, "power": 121.1}],
                              "collected_at": NOW}},
         "alerts": [{"id": "a1", "time": NOW, "level": "严重", "resource": "CPU",
                     "resource_key": "cpu", "metric": "CPU 使用率", "value": "91.0%",
                     "region": "华东一", "host": "web-01", "host_id": "h1",
                     "ip": "10.0.0.11", "message": "CPU 超阈值"}],
         "updated_at": NOW}

tmp = tempfile.TemporaryDirectory()
REAL_CATALOG, REAL_LOG = ops_center.CATALOG_PATH, ops_center.LOG_PATH
REAL_DB, REAL_DOMAIN = ops_center.DB_PATH, ops_center.DOMAIN_PATH
ops_center.CATALOG_PATH = os.path.join(tmp.name, "ops-catalog.json")
ops_center.LOG_PATH = os.path.join(tmp.name, "ops-logs.json")
ops_center.DB_PATH = os.path.join(tmp.name, "ops-db.json")
ops_center.DOMAIN_PATH = os.path.join(tmp.name, "ops-domains.json")

# 配置完全内存化：store.load 返回同一个内存字典，save 空操作，不碰真实 config。
FAKE_CONFIG = {
    "server": {"host": "127.0.0.1", "port": 8848},
    "secret": "",
    "ai_profiles": [],
    "hosts": copy.deepcopy(HOSTS),
    "workbench": {"mode": "managed", "cmdb": {}, "monitor": {},
                  "alert_config": {
                      "thresholds": {"cpu": 85.0, "mem": 85.0, "disk": 85.0, "net": 10.0},
                      "template": "",
                      "notifications": [{"id": "n1", "name": "值班群",
                                         "type": "IM", "target": "ops"}]}},
}
_real_load, _real_save, _real_encrypt = store.load, store.save, store.encrypt
_real_get_host = store.get_host
store.load = lambda: FAKE_CONFIG
store.save = lambda: None
store.encrypt = lambda plain: "ENC(" + plain + ")"
store.get_host = lambda hid: next(
    (h for h in FAKE_CONFIG["hosts"] if h.get("id") == hid), None)

_real_ids = ops_center._managed_host_ids
ops_center._managed_host_ids = lambda: {"h1", "h2"}

_orig_hosts, _orig_state = workbench._managed_hosts, workbench._state
_backup_alert_cfg = copy.deepcopy(workbench.alert_config())
workbench._managed_hosts = lambda: copy.deepcopy(FAKE_CONFIG["hosts"])
workbench._state = lambda: copy.deepcopy(STATE)
_orig_exec = ssh_manager.manager.exec

try:
    print("=== 1. 资产分类默认空，只存用户输入 ===")
    meta = ops_center.catalog_meta()
    cats = [c["key"] for c in meta["categories"]]
    check("包含业务资产分类与告警数据源", cats == [
        "network_device", "credential", "probe", "database", "middleware",
        "alert_source"], cats)
    check("告警数据源分类含所需字段",
          [f["key"] for f in meta["categories"][-1]["fields"]] ==
          ["name", "address", "region", "org", "status", "note"],
          meta["categories"][-1])
    check("默认没有任何台账记录", ops_center.catalog()["total"] == 0)
    saved = ops_center.upsert_catalog({"category": "network_device", "name": "核心交换机",
                                       "address": "10.0.0.254", "note": "用户录入"})
    check("网络设备由用户录入", saved.get("ok") and saved["row"]["name"] == "核心交换机", saved)
    check("未录入的字段保持为空", saved["row"]["status"] == "", saved)
    ops_center.upsert_catalog({"category": "credential", "name": "只读账号",
                               "scope": "password:123456 仅示例", "type": "CMDB"})
    cred = ops_center.catalog("credential")["rows"][0]
    check("凭据台账不接收明文口令", "123456" not in cred["scope"] and "[已隐藏]" in cred["scope"],
          cred)
    check("未知分类被拒绝", ops_center.upsert_catalog({"category": "fake"})["ok"] is False)
    rid = saved["row"]["id"]
    check("删除台账记录", ops_center.delete_catalog(rid)["removed"] == 1)
    check("删除后不残留", all(r["id"] != rid for r in ops_center.catalog("network_device")["rows"]))

    print("=== 2. 机房 / 巡检来自纳管主机与采集快照 ===")
    rooms = ops_center.rooms()
    check("机房按已填字段归集", [r["name"] for r in rooms["rows"]] ==
          ["A栋3楼", "未填写机房"], rooms["rows"])
    check("机房主机数来自纳管清单", rooms["rows"][0]["hosts"] == 1, rooms["rows"])
    ins = ops_center.inspection()
    check("巡检只列有快照的主机", ins["total"] == 1 and ins["rows"][0]["host"] == "web-01", ins)
    check("巡检结论按阈值比对真实采集值", "CPU 91.0%" in ins["rows"][0]["conclusion"],
          ins["rows"][0])
    check("GPU 数量来自采集结果", ins["rows"][0]["gpu_count"] == 1, ins["rows"][0])

    print("=== 3. 日志源必须是用户指定的纳管主机绝对路径 ===")
    check("默认没有日志源", ops_center.log_sources()["total"] == 0)
    check("非纳管主机被拒绝", ops_center.upsert_log_source(
        {"host_id": "h9", "path": "/var/log/messages"})["ok"] is False)
    check("相对路径被拒绝", ops_center.upsert_log_source(
        {"host_id": "h1", "path": "var/log/messages"})["ok"] is False)
    check("shell 特殊字符被拒绝", ops_center.upsert_log_source(
        {"host_id": "h1", "path": "/var/log/messages; rm -rf /"})["ok"] is False)
    bad_pattern = ops_center.upsert_log_source(
        {"host_id": "h1", "path": "/var/log/messages", "trace_pattern": "(["})
    check("非法链路正则被拒绝", bad_pattern["ok"] is False, bad_pattern)
    check("正则校验错误提示正确写法",
          "(trace[=:]([a-z0-9-]+))" in bad_pattern["message"], bad_pattern)
    src = ops_center.upsert_log_source({
        "host_id": "h1", "path": "/var/log/messages", "category": "system",
        "keyword": "ERROR", "trace_pattern": "trace=([a-z0-9-]+)", "lines": 50})
    check("合法日志源保存成功", src.get("ok") and src["row"]["host_id"] == "h1", src)
    check("日志源回显纳管主机名",
          ops_center.log_sources()["rows"][0]["host_name"] == "web-01")

    print("=== 4. 日志采集只读 tail，内容来自远端真实输出 ===")
    lines = ["2026-11-24 INFO service started",
             "2026-11-24 ERROR trace=abc-123 disk read failed",
             "2026-11-24 WARN trace=abc-123 retry", ""]
    calls = []

    def fake_exec(hid, cmd, timeout=0, max_chars=0):
        calls.append((hid, cmd))
        return {"stdout": "\n".join(lines), "exit_code": 0}

    ssh_manager.manager.exec = fake_exec
    res = ops_center.collect_logs()
    check("采集成功 1 个日志源", res["success"] == 1 and res["total"] == 1, res)
    check("只执行 tail 只读命令", len(calls) == 1 and calls[0][1].startswith("tail -n 50 -- "), calls)
    check("路径原样安全传入（无 shell 特殊字符）",
          calls[0][1].endswith(" /var/log/messages")
          and not any(x in calls[0][1] for x in (";", "|", "&", "`")), calls)
    check("空行未落库", ops_center.query_logs()["total"] == 3, ops_center.query_logs()["total"])
    check("级别由日志原文判定",
          {e["level"] for e in ops_center.query_logs()["rows"]} == {"ERROR", "WARN", "INFO"},
          [e["level"] for e in ops_center.query_logs()["rows"]])
    check("关键词过滤命中真实行", ops_center.query_logs(keyword="disk")["total"] == 1)
    check("慢日志分类没有数据即为 0", ops_center.query_logs(category="slow")["total"] == 0)
    stats = ops_center.log_stats()
    check("日志告警由用户关键词匹配得出", len(stats["alerts"]) == 1
          and stats["alerts"][0]["keyword"] == "ERROR", stats["alerts"])
    traces = ops_center.log_traces()
    check("链路标识由用户正则提取", traces["total"] == 1
          and traces["rows"][0]["trace"] == "abc-123", traces["rows"])
    check("链路行数为真实命中数", traces["rows"][0]["count"] == 2, traces["rows"])
    check("删除日志源同时删除其采集记录", ops_center.delete_log_source(src["row"]["id"])["removed"] == 1
          and ops_center.query_logs()["total"] == 0)

    print("=== 5. 告警策略 / 聚合 / 驾驶舱 ===")
    cfg = workbench.alert_config()
    check("默认阈值存在", cfg["thresholds"]["cpu"] == 85.0, cfg["thresholds"])
    saved_cfg = workbench.save_alert_config({
        "thresholds": {"cpu": 80, "mem": 75, "disk": 70, "net": 5},
        "template": "{level} {metric} {value} @ {host}",
        "notifications": [{"name": "值班群", "type": "IM", "target": "ops"}],
    })
    check("用户阈值立即生效", saved_cfg["thresholds"]["cpu"] == 80.0, saved_cfg)
    check("用户模板被保存", "值班" not in saved_cfg["template"]
          and saved_cfg["template"].startswith("{level}"), saved_cfg["template"])
    check("通知对象不含口令字段", set(saved_cfg["notifications"][0]) ==
          {"id", "name", "type", "target", "note"}, saved_cfg["notifications"])
    st = {"snapshots": {}, "alerts": []}
    added = workbench._append_alerts(st, HOSTS[0],
                                     {"cpu": 78.0, "mem": 76.0, "disk": 60.0, "net_errors": 0}, NOW)
    check("用户阈值参与告警判定（78% CPU 不告警、76% 内存告警）",
          len(added) == 1 and added[0]["resource_key"] == "mem", added)
    check("告警消息使用用户模板", added[0]["message"].startswith("警告 内存使用率"), added)
    agg = workbench.alert_aggregate("resource")
    check("告警聚合按采集告警统计", agg["rows"][0]["key"] == "CPU"
          and agg["rows"][0]["count"] == 1, agg["rows"])
    resources = workbench.alert_resources()
    check("告警资源列出纳管主机", resources["total"] == 2, resources["total"])
    check("未采集主机告警数为 0", [r for r in resources["rows"]
                              if r["host"] == "db-01"][0]["alerts"] == 0, resources["rows"])
    dash = workbench.dashboard()
    check("驾驶舱主机数来自纳管主机", dash["managed"] == 2, dash["managed"])
    check("驾驶舱有真实 GPU 实测汇总", dash["hosts"][0]["gpu_count"] == 1
          and dash["hosts"][0]["gpu_util"] == 72.3, dash["hosts"][:1])
    check("驾驶舱不写死示例数字", "4820" not in str(dash) and "121.1 W" not in str(dash.get("note")),
          dash.get("note"))

    print("=== 6. GPU 解析空态与格式容错 ===")
    sample = ("__AOC_BEGIN__1763856000__\nweb-01\n10.0.0.11\n"
              "cpu  100 0 50 800 10 0 0 0\ncpu  150 0 70 900 10 0 0 0\n"
              "MemTotal:       1000000 kB\nMemAvailable:    250000 kB\n"
              "/dev/sda1  100 50 30 75% /\n"
              "eth0: 100 100 5 2 0 0 0 0 200 100 3 1 0 0 0 0\n__AOC_END__\n"
              "__AOC_GPU_BEGIN__\n__AOC_GPU_END__")
    snap = workbench._parse_collect(sample)
    check("无 nvidia-smi 时 GPU 为空数组而不是假数据", snap and snap["gpu"] == [], snap)
    sample2 = sample.replace("__AOC_GPU_BEGIN__\n", "__AOC_GPU_BEGIN__\n0, NVIDIA A100, 50, 60, 40, 80, 45, [N/A]\n")
    snap2 = workbench._parse_collect(sample2)
    check("GPU 功耗不可读时为 None", snap2 and snap2["gpu"][0]["power"] is None, snap2)

    print("=== 7. 主机台账：脱敏、真实指标、逐条校验导入 ===")
    FAKE_CONFIG["hosts"] = copy.deepcopy(HOSTS)
    listed = ops_center.hosts()
    row1 = next(r for r in listed["rows"] if r["id"] == "h1")
    row2 = next(r for r in listed["rows"] if r["id"] == "h2")
    dump = json.dumps(listed, ensure_ascii=False).lower()
    forbidden = {"password", "password_enc", "secret", "api_key"}
    check("主机行不含密码或口令散列字段",
          all(k not in forbidden for row in listed["rows"] for k in row)
          and "secret123" not in dump and "enc(" not in dump,
          [list(r) for r in listed["rows"]])
    check("has_password 只反映是否设置过密码", row1["has_password"] is True
          and row2["has_password"] is False, [row1["has_password"], row2["has_password"]])
    check("指标来自真实采集快照", row1["cpu"] == 91.0 and row1["mem"] == 60.0
          and row1["at"] == NOW and row1["monitored"] == "是", row1)
    check("未采集主机指标为 null、monitored 为否",
          row2["cpu"] is None and row2["mem"] is None and row2["disk"] is None
          and row2["at"] is None and row2["monitored"] == "否", row2)
    check("用户填写的厂商/用途/机龄原值下发", row1["vendor"] == "华为"
          and row1["usage"] == "业务服务器" and row1["asset_age"] == "2年", row1)
    exported = ops_center.hosts_export()
    check("导出载荷含时间戳与脱敏主机", exported["exported_at"]
          and len(exported["hosts"]) == 2, exported.get("exported_at"))
    exp_dump = json.dumps(exported, ensure_ascii=False).lower()
    check("导出文件不含任何密码",
          all(k not in {"password", "password_enc", "secret", "api_key"}
              for row in exported["hosts"] for k in row)
          and "secret123" not in exp_dump and "enc(" not in exp_dump, exported)

    imported = ops_center.import_hosts({"hosts": [
        {"name": "new-01", "host": "10.0.0.20", "username": "root", "port": 22,
         "password": "p1", "vendor": "中兴", "usage": "业务", "asset_age": "1年"},
        {"host": "10.0.0.11", "username": "root", "note": "更新已有主机但不带密码"},
        {"host": "", "username": "root"},
        {"host": "10.0.0.21", "username": ""},
        {"host": "10.0.0.22", "username": "root", "port": 70000},
    ]})
    check("导入成功 2 条", imported["imported"] == 2, imported)
    check("导入跳过 3 条并逐条记录原因", imported["skipped"] == 3
          and len(imported["errors"]) == 3, imported["errors"])
    check("逐条失败不阻断其它条目", "p1" not in json.dumps(imported, ensure_ascii=False), imported)
    new_row = next(r for r in ops_center.hosts()["rows"] if r["host"] == "10.0.0.20")
    check("导入写入厂商/用途/机龄", new_row["vendor"] == "中兴"
          and new_row["usage"] == "业务" and new_row["asset_age"] == "1年", new_row)
    updated = store.get_host("h1")
    check("地址+用户名命中已纳管主机并复用 id",
          any(h["host"] == "10.0.0.11" and h["id"] == "h1" for h in FAKE_CONFIG["hosts"]), updated)
    check("空密码不覆盖已有密码", updated["password_enc"] == "ENC(secret123)"
          and updated["note"] == "更新已有主机但不带密码", updated)
    check("非法 hosts 载荷被拒绝", ops_center.import_hosts({})["ok"] is False)

    print("=== 8. 大屏详情 / 大屏：零伪造，全部来自真实数据 ===")
    FAKE_CONFIG["hosts"] = copy.deepcopy(HOSTS)
    detail = ops_center.dashboard_detail()
    check("详情纳管/监控来自真实清单", detail["summary"]["managed"] == 2
          and detail["summary"]["monitored"] == 1, detail["summary"])
    check("覆盖率按真实比例计算", detail["summary"]["coverage"] == 50.0, detail["summary"])
    check("活跃告警来自所选窗口真实告警", detail["summary"]["active_alerts"] == 1,
          detail["summary"])
    check("台账数量来自用户录入", detail["summary"]["network_devices"] == 0
          and detail["summary"]["credentials"] == 1
          and detail["summary"]["db_assets"] == 0, detail["summary"])
    check("厂商/用途/机龄空值归入未填写",
          detail["distributions"]["vendor"] == [{"name": "华为", "value": 1},
                                                 {"name": "未填写", "value": 1}]
          and detail["distributions"]["usage"] == [{"name": "业务服务器", "value": 1},
                                                   {"name": "未填写", "value": 1}],
          detail["distributions"]["vendor"])
    check("地域分布来自纳管主机字段",
          {d["name"] for d in detail["distributions"]["region"]} == {"华东一", "华东二"},
          detail["distributions"]["region"])
    check("health 只列有快照的主机", len(detail["health"]) == 1
          and detail["health"][0]["host"] == "web-01", detail["health"])
    check("health 用数值阈值定级", detail["health"][0]["level"] == "严重"
          and detail["health"][0]["cpu"] == 91.0, detail["health"])
    check("alert_top 来自真实告警", detail["alert_top"][0]["key"] == "CPU"
          and detail["alert_top"][0]["count"] == 1, detail["alert_top"])
    screen = ops_center.screen()
    check("大屏 KPI 来自真实汇总", screen["kpis"][0]["value"] == 2
          and screen["kpis"][2]["value"] == 1, screen["kpis"])
    check("趋势按 10 分钟归集且只用真实告警", len(screen["alert_trend"]) == 1
          and screen["alert_trend"][0]["count"] == 1, screen["alert_trend"])
    check("大屏资源只列有快照主机", len(screen["resources"]) == 1
          and screen["resources"][0]["host"] == "web-01", screen["resources"])
    check("大屏地域统计来自纳管主机", screen["regions"][0]["hosts"] == 1,
          screen["regions"])
    check("大屏/详情均无写死示例数字", "4820" not in json.dumps(detail, ensure_ascii=False)
          and "4820" not in json.dumps(screen, ensure_ascii=False))
    empty_state = dict(STATE, alerts=[])
    workbench._state = lambda: copy.deepcopy(empty_state)
    empty_screen = ops_center.screen()
    check("无告警时趋势为空数组、KPI 为 0", empty_screen["alert_trend"] == []
          and empty_screen["kpis"][2]["value"] == 0, empty_screen["alert_trend"])
    workbench._state = lambda: copy.deepcopy(STATE)

    print("=== 9. 数据库管理：用户录入 + 关联主机真实指标 ===")
    ops_center.upsert_catalog({"category": "database", "name": "订单库",
                               "address": "10.0.0.12:3306", "engine": "mysql",
                               "type": "MySQL", "version": "8.0.36", "env": "生产",
                               "alive": "存活", "auth": "已认证", "status": "运行中",
                               "host_id": "h2"})
    ops_center.upsert_catalog({"category": "database", "name": "用户库",
                               "address": "10.0.0.11:3306", "engine": "mysql",
                               "status": "已下线", "host_id": "h1"})
    assets = ops_center.db_assets()
    check("数据库资产含 host_id 与关联主机名", assets["total"] == 2
          and all("host_id" in r and "host_name" in r for r in assets["rows"]), assets["rows"])
    user_db = next(r for r in assets["rows"] if r["name"] == "用户库")
    order_db = next(r for r in assets["rows"] if r["name"] == "订单库")
    check("关联主机指标为真实采集值", user_db["host_name"] == "web-01"
          and user_db["cpu"] == 91.0 and user_db["at"] == NOW, user_db)
    check("关联主机无快照时指标为 null", order_db["host_name"] == "db-01"
          and order_db["cpu"] is None, order_db)
    db_meta = next(c for c in ops_center.catalog_meta()["categories"]
                   if c["key"] == "database")
    db_labels = {f["key"]: f["label"] for f in db_meta["fields"]}
    check("数据库新字段中文标签齐全", all(db_labels.get(k) == v for k, v in
          {"type": "类型", "version": "版本", "env": "环境",
           "alive": "存活状态", "auth": "认证状态"}.items()), db_labels)
    check("数据库新字段写入并回显", all(order_db.get(k) == v for k, v in
          {"type": "MySQL", "version": "8.0.36", "env": "生产",
           "alive": "存活", "auth": "已认证"}.items()), order_db)
    check("旧 engine 字段保持兼容", user_db.get("engine") == "mysql"
          and order_db.get("engine") == "mysql", (user_db, order_db))
    check("监控接入基于真实快照判定", user_db["monitor"] == "内置监控"
          and order_db["monitor"] == "未配置", (user_db["monitor"], order_db["monitor"]))
    check("监控接入不凭 status 字段猜测", user_db["status"] == "已下线"
          and user_db["monitor"] == "内置监控" and order_db["status"] == "运行中"
          and order_db["monitor"] == "未配置", (user_db, order_db))
    saved_rec = ops_center.upsert_db_record({
        "kind": "query", "title": "慢查询核对", "host_id": "h1",
        "sql": "select 1", "result": "用户粘贴的结果", "note": "仅录入不执行"})
    check("数据库记录由用户录入", saved_rec["ok"] and saved_rec["row"]["title"] == "慢查询核对",
          saved_rec)
    check("记录声明不执行 SQL", "不执行任何 SQL" in ops_center.db_records()["note"])
    check("按 kind 过滤记录", ops_center.db_records("query")["total"] == 1
          and ops_center.db_records("audit")["total"] == 0)
    check("未填标题被拒绝", ops_center.upsert_db_record({"kind": "query"})["ok"] is False)
    check("未知类型被拒绝", ops_center.upsert_db_record({"kind": "xx", "title": "x"})["ok"] is False)
    check("非纳管主机被拒绝", ops_center.upsert_db_record(
        {"kind": "audit", "title": "x", "host_id": "h9"})["ok"] is False)
    rid2 = saved_rec["row"]["id"]
    changed = ops_center.upsert_db_record({"id": rid2, "kind": "audit", "title": "改为审核记录"})
    check("记录可更新", changed["ok"] and changed["row"]["kind"] == "audit", changed)
    check("更新不产生重复", ops_center.db_records()["total"] == 1)
    check("删除记录", ops_center.delete_db_record(rid2)["removed"] == 1
          and ops_center.db_records()["total"] == 0)
    monitor = ops_center.db_monitor()
    check("监控只列关联且已采集主机", monitor["total"] == 1
          and monitor["rows"][0]["host"] == "web-01"
          and monitor["rows"][0]["cpu"] == 91.0, monitor["rows"])

    print("=== 10. 告警中心：真实告警 + 用户通知对象 ===")
    center = ops_center.alert_center()
    check("活跃/严重/警告来自真实告警", center["summary"]["active"] == 1
          and center["summary"]["critical"] == 1
          and center["summary"]["warning"] == 0, center["summary"])
    check("今日按本地日期统计", center["summary"]["today"] == 1, center["summary"])
    check("top_names 按告警资源统计", center["top_names"][0]["name"] == "CPU"
          and center["top_names"][0]["count"] == 1, center["top_names"])
    check("distribution 按级别统计", center["distribution"] == [{"name": "严重", "count": 1}],
          center["distribution"])
    check("通知对象来自用户配置", center["notification_stats"] == [
        {"name": "值班群", "count": 1}], center["notification_stats"])
    check("recent 字段完整", set(center["recent"][0]) ==
          {"time", "level", "host", "message"}, center["recent"])

    print("=== 11. 域名告警：校验 + 纳管主机上只读探测 ===")
    check("非法域名被拒绝", ops_center.upsert_domain(
        {"name": "http://example.com", "host_id": "h1"})["ok"] is False)
    check("普通字符串不是域名", ops_center.upsert_domain(
        {"name": "not a domain", "host_id": "h1"})["ok"] is False)
    check("非纳管主机被拒绝", ops_center.upsert_domain(
        {"name": "example.com", "host_id": "h9"})["ok"] is False)
    check("默认没有域名", ops_center.domains()["total"] == 0)
    d1 = ops_center.upsert_domain({"name": "Example.COM", "host_id": "h1",
                                   "tags": "生产,核心"})
    d2 = ops_center.upsert_domain({"name": "down.example.com", "host_id": "h2"})
    check("域名规范化为小写", d1["ok"] and d1["row"]["name"] == "example.com"
          and ops_center.domains()["rows"][0]["host_name"] == "web-01", d1)
    domain_calls = []

    def domain_exec(hid, cmd, timeout=0, max_chars=0):
        domain_calls.append((hid, cmd))
        if "curl" in cmd:
            return {"stdout": "200 0.123\n", "exit_code": 0}
        if "openssl" in cmd:
            return {"stdout": "notAfter=Nov 24 12:00:00 2026 GMT\n", "exit_code": 0}
        return {"stdout": "", "exit_code": 1}

    ssh_manager.manager.exec = domain_exec
    checked = ops_center.check_domains()
    check("探测在用户绑定的纳管主机执行", len(domain_calls) >= 2
          and all(hid in ("h1", "h2") for hid, _ in domain_calls), domain_calls)
    check("使用 curl 只读命令并带超时",
          "curl -sS -o /dev/null" in domain_calls[0][1]
          and "--max-time 12" in domain_calls[0][1], domain_calls[0])
    check("HTTP 状态与延迟取自远端输出", checked["success"] == 2
          and checked["results"][0]["http_status"] == 200
          and checked["results"][0]["latency_ms"] == 123.0, checked["results"])
    check("SSL 到期时间取自 openssl 真实输出",
          checked["results"][0]["ssl_expire"] == "2026-11-24", checked["results"])
    check("探测结果回写域名记录", ops_center.domains()["rows"][0]["status"] == "正常"
          and ops_center.domains()["rows"][0]["checked_at"] == checked["at"])
    check("删除域名", ops_center.delete_domain(d2["row"]["id"])["removed"] == 1)

    def fail_exec(hid, cmd, timeout=0, max_chars=0):
        return {"stdout": "", "stderr": "could not connect", "exit_code": 7}

    ssh_manager.manager.exec = fail_exec
    failed = ops_center.check_domains(d1["row"]["id"])
    check("连接失败如实返回 error，不伪造结果", failed["failed"] == 1
          and failed["results"][0]["ok"] is False
          and failed["results"][0]["error"], failed["results"])
    check("失败后状态回写异常", ops_center.domains()["rows"][0]["status"] == "异常")

    print("=== 12. AI 多目标：主机 / 数据库 / 应用 / 日志的真实上下文 ===")
    ops_center.upsert_catalog({"category": "middleware", "name": "订单缓存",
                               "address": "10.0.0.11:6379", "engine": "redis 7",
                               "org": "基础架构部", "status": "运行中"})
    tg = ai_targets.list_targets()
    types = [g["type"] for g in tg["groups"]]
    check("AI 目标包含主机/数据库/应用/日志四类",
          types == ["host", "database", "application", "log"], types)
    check("主机目标来自已纳管主机且不含密码",
          len(tg["groups"][0]["options"]) == 2
          and "ENC(" not in json.dumps(tg, ensure_ascii=False), tg["groups"][0])
    app_opt = tg["groups"][2]["options"][0]
    app_ctx = ai_targets.context("application", app_opt["id"])
    check("应用上下文只来自用户录入台账",
          app_ctx["ok"] and "订单缓存" in app_ctx["context"]
          and "redis 7" in app_ctx["context"]
          and "不会为应用编造运行指标" in app_ctx["context"], app_ctx)

    db_row = next(r for r in ops_center.db_assets()["rows"] if r["name"] == "用户库")
    db_ctx = ai_targets.context("database", db_row["id"])
    check("数据库上下文含关联主机的真实采集值",
          db_ctx["ok"] and "91.0" in db_ctx["context"]
          and "用户库" in db_ctx["context"], db_ctx)
    check("数据库上下文没有编造查询执行结果",
          "select" not in db_ctx["context"].lower()
          and "查询结果" not in db_ctx["context"], db_ctx)

    host_ctx = ai_targets.context("host", "h1")
    check("主机上下文含真实快照与采集告警",
          host_ctx["ok"] and "91.0" in host_ctx["context"]
          and "CPU 超阈值" in host_ctx["context"], host_ctx)
    check("主机上下文不返回口令散列",
          "ENC(" not in host_ctx["context"]
          and "secret123" not in host_ctx["context"], host_ctx["context"][:200])

    new_src = ops_center.upsert_log_source({
        "host_id": "h1", "path": "/var/log/messages", "category": "system",
        "keyword": "ERROR", "lines": 50})
    check("日志目标建档成功", new_src["ok"], new_src)
    before = ai_targets.context("log", new_src["row"]["id"])
    check("没有采集时不编造日志内容", "未采集到日志行" in before["context"], before)

    def one_line_exec(hid, cmd, timeout=0, max_chars=0):
        return {"stdout": "2026-11-24 ERROR disk read failed", "exit_code": 0}

    ssh_manager.manager.exec = one_line_exec
    ops_center.collect_logs()
    log_ctx = ai_targets.context("log", new_src["row"]["id"])
    check("日志上下文来自用户配置路径与真实采集行",
          log_ctx["ok"] and "/var/log/messages" in log_ctx["context"]
          and "disk read failed" in log_ctx["context"], log_ctx)
    check("无效目标被拒绝", ai_targets.context("fake", "x")["ok"] is False
          and ai_targets.context("host", "not-exist")["ok"] is False)

    print("=== 13. 监控时间窗包含 10 分钟 ===")
    keys = [w["key"] for w in workbench.WINDOWS]
    check("10m 位于 5m 与 15m 之间",
          keys.index("10m") == keys.index("5m") + 1
          and keys.index("15m") == keys.index("10m") + 1, keys)
    ten = next(w for w in workbench.WINDOWS if w["key"] == "10m")
    check("10m 配置 label 与 minutes",
          ten == {"key": "10m", "label": "最近 10 分钟", "minutes": 10}, ten)
    check("window_label 自动支持 10m", workbench.window_label("10m") == "最近 10 分钟")
    lo, hi = workbench.window_bounds("10m")
    check("window_bounds 自动按 10 分钟取窗", (hi - lo).total_seconds() == 600, (lo, hi))

    print("=== 14. 非法链路正则时日志行照常采集 ===")
    # 直接写入一条历史脏数据（保存接口会拒绝，但历史配置可能已存在写坏的正则）。
    bad_id = "bad-regex-source"
    raw_logs = ops_center._logs()
    raw_logs["sources"].append({
        "id": bad_id, "host_id": "h1", "path": "/var/log/messages",
        "category": "system", "keyword": "",
        "trace_pattern": "(trace=:]([a-z0-9-]+)",  # 截图写法缺少右括号
        "lines": 20, "enabled": True, "updated_at": ops_center._now()})
    ops_center._write(ops_center.LOG_PATH, raw_logs)

    def two_lines_exec(hid, cmd, timeout=0, max_chars=0):
        return {"stdout": "2026-11-24 INFO service started\n"
                          "2026-11-24 ERROR trace=abc-123 disk read failed",
                "exit_code": 0}

    ssh_manager.manager.exec = two_lines_exec
    before_lines = ops_center.query_logs()["total"]
    res_bad = ops_center.collect_logs(bad_id)
    check("非法正则时该源仍采集成功",
          res_bad["total"] == 1 and res_bad["success"] == 1
          and res_bad["results"][0]["ok"] is True, res_bad)
    check("非法正则时给出 warning 说明",
          "warning" in res_bad["results"][0]
          and "正则" in res_bad["results"][0]["warning"], res_bad["results"][0])
    check("非法正则时日志行照常落库",
          ops_center.query_logs()["total"] - before_lines == 2,
          ops_center.query_logs()["total"] - before_lines)
    check("非法正则时链路标识为空",
          all(e["trace"] == "" for e in ops_center.query_logs()["rows"]),
          [e["trace"] for e in ops_center.query_logs()["rows"]])
    ops_center.delete_log_source(bad_id)
finally:
    ssh_manager.manager.exec = _orig_exec
    # 先恢复内存配置里的告警策略，再恢复 store 的真实读写函数，避免污染 config.json
    try:
        workbench.save_alert_config(_backup_alert_cfg)
    except Exception:
        pass
    ops_center.CATALOG_PATH, ops_center.LOG_PATH = REAL_CATALOG, REAL_LOG
    ops_center.DB_PATH, ops_center.DOMAIN_PATH = REAL_DB, REAL_DOMAIN
    ops_center._managed_host_ids = _real_ids
    store.get_host = _real_get_host
    store.load, store.save, store.encrypt = _real_load, _real_save, _real_encrypt
    workbench._managed_hosts, workbench._state = _orig_hosts, _orig_state
    tmp.cleanup()

print()
print("结果: %d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
