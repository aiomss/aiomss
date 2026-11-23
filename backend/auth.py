# -*- coding: utf-8 -*-
"""登录鉴权：固定账号 + 图片验证码 + 会话 Cookie。

安全设计：
  · 密码不以明文落盘，配置里只存 PBKDF2-HMAC-SHA256 散列与随机盐。
  · 验证码为服务端生成的 PNG 图片，一次性、有时效、带干扰，校验失败即作废。
  · 会话 token 随机 32 字节，仅存内存，Cookie 为 HttpOnly + SameSite=Lax。
  · 连续失败锁定，缓解暴力猜测。
  · 所有 API 与页面默认受保护，仅登录相关端点在白名单内。
"""
import hashlib
import hmac
import io
import math
import os
import random
import re
import secrets
import threading
import time

from PIL import Image, ImageDraw, ImageFont

from . import store

COOKIE_NAME = "aoc_session"
SESSION_TTL = 8 * 3600          # 登录有效期 8 小时
CAPTCHA_TTL = 300               # 验证码 5 分钟
MAX_FAILS = 5                   # 连续失败次数
LOCK_SECONDS = 60               # 锁定时长
PBKDF2_ROUNDS = 200_000

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "13660888023"   # 仅用于首次初始化生成散列，之后不再使用

_CID_RE = re.compile(r"^[0-9a-f]{16,64}$")
# 去掉 0/O/1/I/l 等易混淆字符
_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

_lock = threading.RLock()
_captchas = {}      # cid -> {code, expire, tries}
_sessions = {}      # token -> {user, expire, ip}
_fails = {}         # ip -> {count, until}


# ---------------- 账号 ----------------
def _hash(password, salt_hex):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ROUNDS
    ).hex()


def auth_config():
    """确保配置里存在 auth 段（首次运行用默认账号密码生成散列）。"""
    d = store.load()
    with _lock:
        a = d.get("auth")
        if not isinstance(a, dict) or not a.get("password_hash") or not a.get("salt"):
            salt = secrets.token_hex(16)
            a = {
                "username": DEFAULT_USERNAME,
                "salt": salt,
                "password_hash": _hash(DEFAULT_PASSWORD, salt),
                "note": "密码以 PBKDF2 散列保存；修改请运行 scripts/set_password.py",
            }
            d["auth"] = a
            store.save()
        return a


def set_password(new_password):
    """修改登录密码（供 scripts/set_password.py 调用）。"""
    if not new_password or len(new_password) < 6:
        raise ValueError("密码至少 6 位")
    d = store.load()
    with _lock:
        salt = secrets.token_hex(16)
        a = d.setdefault("auth", {})
        a["username"] = a.get("username") or DEFAULT_USERNAME
        a["salt"] = salt
        a["password_hash"] = _hash(new_password, salt)
        a["note"] = "密码以 PBKDF2 散列保存；修改请运行 scripts/set_password.py"
        store.save()
    _sessions.clear()
    return a["username"]


def verify_credentials(username, password):
    a = auth_config()
    ok_user = hmac.compare_digest(str(username or "").strip(), a.get("username", ""))
    ok_pass = hmac.compare_digest(_hash(password or "", a["salt"]), a.get("password_hash", ""))
    return ok_user and ok_pass


# ---------------- 图片验证码 ----------------
def _font(size):
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\consolab.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_png(code):
    w, h = 168, 54
    img = Image.new("RGB", (w, h), (244, 247, 253))
    draw = ImageDraw.Draw(img)

    # 背景色块 + 干扰线 + 噪点
    for _ in range(7):
        c = (random.randint(205, 238), random.randint(212, 242), random.randint(230, 252))
        draw.line([(random.randint(0, w), random.randint(0, h)),
                   (random.randint(0, w), random.randint(0, h))], fill=c, width=2)
    for _ in range(260):
        draw.point((random.randint(0, w - 1), random.randint(0, h - 1)),
                   fill=(random.randint(160, 215), random.randint(170, 220), random.randint(190, 235)))

    font = _font(30)
    x = 16
    for ch in code:
        color = (random.randint(25, 85), random.randint(45, 105), random.randint(95, 165))
        tile = Image.new("RGBA", (40, 44), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        td.text((6, 2), ch, font=font, fill=color + (255,))
        tile = tile.rotate(random.randint(-20, 20), resample=Image.BICUBIC, expand=False)
        img.paste(tile, (x, random.randint(3, 11)), tile)
        x += random.randint(33, 37)

    # 波浪扭曲：逐列做正弦位移，增加机器识别成本
    warped = Image.new("RGB", (w, h), (244, 247, 253))
    phase = random.uniform(0, math.pi * 2)
    for col in range(w):
        dy = int(2.2 * math.sin(col / 15.0 + phase))
        src = img.crop((col, 0, col + 1, h))
        warped.paste(src, (col, dy))
        if dy > 0:
            warped.paste(src.crop((0, h - dy, 1, h)), (col, 0))
    draw = ImageDraw.Draw(warped)
    for _ in range(3):
        draw.line([(random.randint(0, w), random.randint(6, h - 6)),
                   (random.randint(0, w), random.randint(6, h - 6))],
                  fill=(150, 170, 210), width=1)
    draw.rectangle([0, 0, w - 1, h - 1], outline=(205, 216, 236), width=1)

    buf = io.BytesIO()
    warped.save(buf, format="PNG")
    return buf.getvalue()


def make_captcha(cid=None):
    """生成验证码，返回 (cid, code, png_bytes)。cid 可由前端提供以便刷新。"""
    code = "".join(random.SystemRandom().choice(_CHARS) for _ in range(4))
    if not cid or not _CID_RE.match(cid):
        cid = secrets.token_hex(16)
    with _lock:
        _gc()
        _captchas[cid] = {"code": code, "expire": time.time() + CAPTCHA_TTL, "tries": 0}
    return cid, code, _render_png(code)


def check_captcha(cid, answer):
    """一次性校验：无论对错都作废旧验证码，返回 (ok, message)。"""
    with _lock:
        item = _captchas.pop(cid or "", None)
    if not item:
        return False, "验证码已过期，请点击图片刷新后重试"
    if item["expire"] < time.time():
        return False, "验证码已过期，请点击图片刷新后重试"
    if str(answer or "").strip().upper() != item["code"]:
        return False, "验证码不正确，已自动刷新，请重新输入"
    return True, ""


# ---------------- 会话 ----------------
def issue_session(username, ip=""):
    token = secrets.token_urlsafe(32)
    with _lock:
        _gc()
        _sessions[token] = {"user": username, "expire": time.time() + SESSION_TTL, "ip": ip}
    return token


def check_session(token):
    if not token:
        return None
    with _lock:
        s = _sessions.get(token)
        if not s:
            return None
        if s["expire"] < time.time():
            _sessions.pop(token, None)
            return None
        return s


def drop_session(token):
    with _lock:
        _sessions.pop(token or "", None)


def session_user(token):
    s = check_session(token)
    return s["user"] if s else ""


# ---------------- 失败锁定 ----------------
def client_ip(request):
    try:
        return request.client.host if request.client else "local"
    except Exception:
        return "local"


def is_locked(ip):
    with _lock:
        f = _fails.get(ip)
        if not f:
            return False, 0
        if f["until"] > time.time():
            return True, int(f["until"] - time.time()) + 1
        _fails.pop(ip, None)
        return False, 0


def record_fail(ip):
    with _lock:
        f = _fails.setdefault(ip, {"count": 0, "until": 0})
        f["count"] += 1
        if f["count"] >= MAX_FAILS:
            f["until"] = time.time() + LOCK_SECONDS
            f["count"] = 0


def clear_fail(ip):
    with _lock:
        _fails.pop(ip, None)


def _gc():
    """清理过期验证码/会话/锁定记录，避免内存无界增长。"""
    now = time.time()
    for k in [k for k, v in _captchas.items() if v["expire"] < now]:
        _captchas.pop(k, None)
    for k in [k for k, v in _sessions.items() if v["expire"] < now]:
        _sessions.pop(k, None)
    for k in [k for k, v in _fails.items() if v["until"] and v["until"] < now]:
        _fails.pop(k, None)
