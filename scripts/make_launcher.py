"""Rebuild the desktop launcher after the shortcut is recreated or the project moves.

Run:  python scripts/make_launcher.py
Add --restart-explorer if the desktop still shows a stale icon.

Why: a .bat file always shows Windows' gear icon. A real .ico is attached to the
shortcut's ICON_LOCATION StringData, and the per-user batfile DefaultIcon covers
the .bat itself. The ICON_LOCATION must be a BARE .ico path -- adding ",0" makes
Explorer silently fall back to the default batch icon.
"""
import ctypes
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import winreg
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "assets", "aiops-console.ico")
WORKDIR = os.path.join(ROOT, "scripts")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
OLD = os.path.join(DESKTOP, "start.bat - 快捷方式.lnk")
NEW = os.path.join(DESKTOP, "AI 运维控制台.lnk")
ORDER = ((0x4, "NAME"), (0x8, "REL"), (0x10, "WORKDIR"),
         (0x20, "ARGS"), (0x40, "ICON"))


class SFI(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260),
                ("szTypeName", wintypes.WCHAR * 80)]


def patched_lnk(src, label):
    b = bytearray(open(src, "rb").read())
    flags = struct.unpack_from("<I", b, 20)[0]
    if not flags & 0x80:
        raise RuntimeError("shortcut is not unicode-encoded: " + src)
    off = 76
    if flags & 0x1:
        off += 2 + struct.unpack_from("<H", b, 76)[0]
    if flags & 0x2:
        off += struct.unpack_from("<I", b, off)[0]
    sd_start = off
    vals = {}
    for bit, name in ORDER:
        if flags & bit:
            n = struct.unpack_from("<H", b, off)[0]
            vals[name] = bytes(b[off + 2:off + 2 + n * 2]).decode("utf-16-le")
            off += 2 + n * 2
    extra = bytes(b[off:])

    vals["NAME"] = label
    vals["WORKDIR"] = WORKDIR
    vals["ICON"] = ICON                    # bare path, NO ",0"
    nf = flags | 0x4 | 0x10 | 0x40 | 0x80
    prefix = bytearray(b[:sd_start])
    struct.pack_into("<I", prefix, 20, nf)
    sd = b"".join(struct.pack("<H", len(vals.get(n, "")))
                  + vals.get(n, "").encode("utf-16-le")
                  for bit, n in ORDER if nf & bit)
    return bytes(prefix + sd + extra)


def icon_location(path):
    s = SFI()
    ctypes.windll.shell32.SHGetFileInfoW(
        path, 0, ctypes.byref(s), ctypes.sizeof(s), 0x000001000)
    return s.szDisplayName


def set_bat_icon():
    k = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Classes\batfile\DefaultIcon")
    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, ICON)
    winreg.CloseKey(k)


def refresh(restart=False):
    sh = ctypes.windll.shell32
    sh.SHChangeNotify(0x08000000, 0x0000, None, None)
    for p in (OLD, NEW):
        if os.path.isfile(p):
            sh.SHChangeNotify(0x00002000, 0x0005, ctypes.c_wchar_p(p), None)
    ie4 = r"C:\Windows\System32\ie4uinit.exe"
    if os.path.isfile(ie4):
        try:
            subprocess.run([ie4, "-show"], timeout=20, capture_output=True)
        except Exception:
            pass
    if restart:
        subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"],
                       capture_output=True)
        time.sleep(2)
        # tasklist emits the local codepage (GBK here); keep bytes, not text=True
        listed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq explorer.exe", "/NH"],
            capture_output=True).stdout.lower()
        if b"explorer.exe" not in listed:
            subprocess.Popen(["explorer.exe"])
            print("Explorer restarted manually")
        else:
            print("Explorer auto-restarted")


def main():
    if not os.path.isfile(ICON):
        raise SystemExit("missing icon, run make_icon.py first: " + ICON)
    src = OLD if os.path.isfile(OLD) else NEW
    if not os.path.isfile(src):
        raise SystemExit("no desktop shortcut found; create one to start.bat first")
    backup = os.path.join(tempfile.gettempdir(), "aiops-shortcut-backup.lnk")
    shutil.copy2(src, backup)
    payload = patched_lnk(src, "AI 运维控制台")
    for path in (OLD, NEW):
        with open(path, "wb") as f:
            f.write(payload)
    set_bat_icon()
    refresh("--restart-explorer" in sys.argv)
    for path in (OLD, NEW):
        loc = icon_location(path)
        print(("OK  " if ICON.lower() in loc.lower() else "FAIL"),
              os.path.basename(path), "->", repr(loc))
    print("backup:", backup)


if __name__ == "__main__":
    main()
