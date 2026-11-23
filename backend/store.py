# -*- coding: utf-8 -*-
"""配置持久化：主机清单、AI 接入、服务参数。SSH 密码用 Fernet 加密后落盘，API Key 仅存后端。"""
import json
import os
import threading
import uuid
from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
EXAMPLE_PATH = os.path.join(CONFIG_DIR, "config.example.json")

DEFAULT = {
    "server": {"host": "127.0.0.1", "port": 8848},
    "secret": "",
    "ai_profiles": [],
    "hosts": [],
}

_lock = threading.RLock()
_cache = None


def _ensure():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        if os.path.exists(EXAMPLE_PATH):
            with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = dict(DEFAULT)
        if not data.get("secret"):
            data["secret"] = Fernet.generate_key().decode()
        _write(data)


def _write(data):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load():
    global _cache
    with _lock:
        _ensure()
        if _cache is None:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
            if not _cache.get("secret"):
                _cache["secret"] = Fernet.generate_key().decode()
                _write(_cache)
        return _cache


def save():
    global _cache
    with _lock:
        if _cache is not None:
            _write(_cache)


def _fernet():
    return Fernet(load()["secret"].encode())


def encrypt(plain):
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


def mask_key(key):
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "****"
    return key[:7] + "****" + key[-4:]


def public_state():
    """返回给前端的脱敏状态：绝不下发完整 API Key 与 SSH 密码。"""
    d = load()
    profiles = []
    for p in d.get("ai_profiles", []):
        profiles.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "base_url": p.get("base_url"),
            "api_key_masked": mask_key(p.get("api_key", "")),
            "has_key": bool(p.get("api_key")),
            "models": p.get("models", []),
            "default_model": p.get("default_model", ""),
            "effort": p.get("effort", "low"),
            "quota": p.get("quota", ""),
        })
    hosts = []
    for h in d.get("hosts", []):
        hosts.append({
            "id": h.get("id"),
            "name": h.get("name"),
            "host": h.get("host"),
            "port": h.get("port", 22),
            "username": h.get("username"),
            "has_password": bool(h.get("password_enc") or h.get("password")),
            "note": h.get("note", ""),
            "region": h.get("region", ""),
            "room": h.get("room", ""),
            "biz_line": h.get("biz_line", ""),
            "app_level": h.get("app_level", ""),
            "org": h.get("org", ""),
            "maintenance": h.get("maintenance", ""),
        })
    return {
        "server": d.get("server", DEFAULT["server"]),
        "ai_profiles": profiles,
        "hosts": hosts,
    }


def get_profile(profile_id=None):
    d = load()
    ps = d.get("ai_profiles", [])
    if not ps:
        return None
    if profile_id:
        for p in ps:
            if p.get("id") == profile_id:
                return p
    return ps[0]


def get_host(host_id):
    for h in load().get("hosts", []):
        if h.get("id") == host_id:
            return h
    return None


def host_password(host):
    if host.get("password_enc"):
        return decrypt(host["password_enc"])
    return host.get("password", "")


def upsert_host(payload):
    d = load()
    hid = payload.get("id") or uuid.uuid4().hex[:8]
    item = {
        "id": hid,
        "name": payload.get("name") or payload.get("host"),
        "host": payload.get("host", ""),
        "port": int(payload.get("port", 22)),
        "username": payload.get("username", "root"),
        "note": payload.get("note", ""),
        # 运维工作台的资产维度：全部来自用户填写，缺失即计入待治理项
        "region": str(payload.get("region") or "")[:100],
        "room": str(payload.get("room") or "")[:100],
        "biz_line": str(payload.get("biz_line") or "")[:100],
        "app_level": str(payload.get("app_level") or "")[:100],
        "org": str(payload.get("org") or "")[:100],
        "maintenance": str(payload.get("maintenance") or "")[:20],
    }
    if payload.get("password"):
        item["password_enc"] = encrypt(payload["password"])
    with _lock:
        hosts = d.setdefault("hosts", [])
        found = False
        for i, h in enumerate(hosts):
            if h.get("id") == hid:
                if "password_enc" not in item and h.get("password_enc"):
                    item["password_enc"] = h["password_enc"]
                hosts[i] = item
                found = True
                break
        if not found:
            hosts.append(item)
        save()
    return hid


def delete_host(host_id):
    d = load()
    with _lock:
        d["hosts"] = [h for h in d.get("hosts", []) if h.get("id") != host_id]
        save()


def upsert_profile(payload):
    d = load()
    pid = payload.get("id") or uuid.uuid4().hex[:8]
    with _lock:
        ps = d.setdefault("ai_profiles", [])
        item = None
        for p in ps:
            if p.get("id") == pid:
                item = p
                break
        if item is None:
            item = {"id": pid}
            ps.append(item)
        for k in ("name", "base_url", "models", "default_model", "effort", "quota"):
            if k in payload:
                item[k] = payload[k]
        # api_key 未传则保留原值；传空字符串表示清空
        if "api_key" in payload and payload["api_key"]:
            item["api_key"] = payload["api_key"]
        save()
    return pid


def delete_profile(profile_id):
    d = load()
    with _lock:
        d["ai_profiles"] = [p for p in d.get("ai_profiles", []) if p.get("id") != profile_id]
        save()


def set_server(payload):
    d = load()
    with _lock:
        d["server"].update({k: v for k, v in payload.items() if k in ("host", "port")})
        save()


def workbench_config():
    """运维工作台的只读数据源配置；缺字段时补默认值，不影响既有配置。"""
    d = load()
    wb = d.get("workbench")
    if not isinstance(wb, dict):
        wb = {"mode": "managed", "cmdb": {}, "monitor": {}}
        with _lock:
            d["workbench"] = wb
            save()
    return wb


def set_workbench(payload):
    d = load()
    with _lock:
        wb = d.setdefault("workbench", {})
        if "mode" in payload:
            wb["mode"] = payload["mode"]
        for kind in ("cmdb", "monitor"):
            if kind in payload and isinstance(payload[kind], dict):
                cur = wb.setdefault(kind, {})
                cur.update({k: v for k, v in payload[kind].items()
                            if k in ("name", "url", "token", "note")})
        save()
    return workbench_config()
