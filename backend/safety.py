# -*- coding: utf-8 -*-
"""命令安全策略：只读白名单、危险命令识别、只读模式与审批闸门。"""
import re

# 只读命令前缀（执行不改变系统状态）
READ_PREFIXES = (
    "echo ", "printf ",   # 纯输出，不改系统状态；若带 > 重定向会被危险规则命中
    "cat ", "cat\t", "zcat ", "less ", "more ", "head ", "tail ", "wc ", "grep ", "egrep ",
    "zgrep ", "awk ", "sed -n", "sed -n ", "sort ", "uniq ", "cut ", "tr ", "diff ", "file ",
    "stat ", "ls ", "ls\t", "ll ", "find ", "df ", "du ", "free ", "uptime", "uname", "hostname",
    "date", "id", "who", "w ", "last ", "ps ", "pstree", "top -b", "top -n", "ss ", "netstat",
    "lsof", "ip ", "ifconfig", "route ", "arp ", "mount ", "lsblk", "fdisk -l", "blkid",
    "dmesg", "journalctl", "systemctl status", "systemctl is-active", "systemctl is-enabled",
    "systemctl list-units", "systemctl list-unit-files", "service ", "getenforce", "sestatus",
    "crontab -l", "rpm -qa", "rpm -qi", "dpkg -l", "yum list", "dnf list", "yum info", "dnf info",
    "which ", "command -v", "whereis ", "type ", "printenv", "env", "echo $", "md5sum ", "sha256sum ",
    "iostat", "vmstat", "mpstat", "sar ", "pidstat", "nproc", "getconf", "ulimit -a",
    "curl -I", "curl -sI", "ping -c", "traceroute", "dig ", "nslookup ", "host ", "nmcli device status",
    "firewall-cmd --list", "docker ps", "docker images", "docker inspect", "docker logs",
    "kubectl get", "kubectl describe", "kubectl logs", "git status", "git log", "git diff",
    "readlink ", "basename ", "dirname ", "du -sh", "cat /proc", "smartctl -a",
    # 查看类命令：曾经被「未识别命令」误判成写操作，导致只读模式下被拦截
    "swapon --show", "swapon -s", "swapon --summary", "swapoff --show", "sysctl -a",
    "free ", "free\t", "systemctl --failed", "systemctl show ", "systemctl cat ",
    "timedatectl", "hostnamectl", "getent ", "column ", "jq ", "base64 ",
    "od ", "xxd ", "paste ", "join ", "comm ", "nl ", "seq ", "yes ", "tree ",
)

# 危险/写操作特征
DANGER_PATTERNS = [
    (r"\brm\s+-[rfRF]", "递归/强制删除"),
    (r"(^|[;&|]\s*)\s*rm\s+", "删除文件"),
    (r"\brmdir\b", "删除目录"),
    (r"\bdd\s+", "底层磁盘写入"),
    # 只匹配真正的磁盘格式化；不能写裸 \bformat\b，否则 `docker ps --format` 会被误判
    (r"\bmkfs\b|\bmkfs\.\w+\b|\bformat\s+(?:/dev/|[A-Za-z]:)", "格式化磁盘"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b", "关机/重启"),
    (r"\bkill\s+-9\b|\bkillall\b|\bpkill\b|\bkill\s+\d", "终止进程"),
    (r"\bsystemctl\s+(restart|stop|start|reload|enable|disable|mask|daemon-reload)", "服务状态变更"),
    (r"\bservice\s+\S+\s+(restart|stop|start|reload)", "服务状态变更"),
    (r"\bchmod\b|\bchown\b|\bchgrp\b|\bsetfacl\b", "权限/属主变更"),
    (r"(?:^|\s)(?:useradd|usermod|userdel|groupadd|passwd)\b", "账号变更"),
    (r"\biptables\b|\bnft\b|\bfirewall-cmd\b(?!.*--list)", "防火墙变更"),
    (r"\bsysctl\s+-w\b", "内核参数变更"),
    (r"\bsed\s+-i\b", "就地修改文件"),
    (r"\btruncate\b|\btee\b", "截断/写入文件"),
    (r"\bcrontab\b(?!\s+-l)", "定时任务变更"),
    (r"\bumount\b|\bmount\s+-o\b", "挂载变更"),
    (r"\b(apt|apt-get|yum|dnf|rpm|pip|npm)\s+(install|remove|erase|update|upgrade|purge|uninstall)", "软件包变更"),
    (r"\bdocker\s+(rm|stop|kill|prune|restart|run|exec)", "容器变更"),
    (r"\bkubectl\s+(delete|apply|rollout|scale|drain)", "K8s 变更"),
    (r"\bgit\s+(push|reset\s+--hard|clean\s+-f|checkout\s+-f)", "代码库变更"),
    (r"\bmysql\b|\bpsql\b|\bsqlplus\b", "数据库交互（可能含 DDL/DML）"),
    (r"\b(insert|update|delete|drop|alter|truncate)\s+(into|table|from|database)\b", "数据库 DDL/DML"),
    (r"\s>>?\s+(?:/|~|\.{1,2}/|[\w.-]+\.[A-Za-z])", "输出重定向（覆盖/追加文件）"),
    (r"\|\s*(ba)?sh\b|\bcurl\b.*\|\s*sh", "管道直接执行远程脚本"),
    (r"\bpython[0-9.]*\s+-c\b|\bperl\s+-e\b", "执行内联代码"),
]

# 明确的写操作（给出可读原因，避免笼统的「未识别命令」）
# 注意 swapon 的查看形态 --show / -s 不算写操作
WRITE_PATTERNS = [
    (r"\bfallocate\b", "创建/预分配文件（占用磁盘）"),
    (r"\bmkswap\b", "初始化 swap 空间"),
    (r"\bswapon\b(?!\s+(?:--show|-s|--summary|--help))", "启用 swap（改变系统状态）"),
    (r"\bswapoff\b", "关闭 swap"),
    (r"\bsysctl\b(?!\s+-a)", "内核参数操作"),
    (r"\bsed\s+[^|]*\s+-i\b", "就地修改文件"),
]

_WRITE_HINT = re.compile(
    r"\bmv\b|\bcp\b|\bmkdir\b|\btouch\b|\bln\s|\btar\s|\brsync\b|\bscp\b|\bsed\s+-i\b",
    re.I,
)


def _is_read_only(low):
    """判断子命令是否为只读命令。

    注意：READ_PREFIXES 多数带尾随空格（如 "sort "），而管道里常用**裸命令**
    （如 `... | sort | uniq -c | tail`），此时 `low == "sort"` 不会 startswith("sort ")，
    会被误判成「未识别命令」。这里同时接受「完全等于」与「以 前缀+空格 开头」。
    """
    for p in READ_PREFIXES:
        ps = p.strip()
        if not ps:
            continue
        if low == ps or low.startswith(ps + " "):
            return True
    return low.startswith("cat") or low.startswith("journalctl")


def split_subcommands(cmd):
    """按 ; && || | 拆成子命令逐段评估，但**忽略引号内**的分隔符。

    日志分析常写 grep -E 'error|fail'，引号里的 | 不能被当成管道。
    """
    parts, buf = [], []
    quote = None
    esc = False
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if esc:
            buf.append(c)
            esc = False
            i += 1
            continue
        if c == "\\" and quote != "'":
            buf.append(c)
            esc = True
            i += 1
            continue
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "&" and i + 1 < n and cmd[i + 1] == "&":
            parts.append("".join(buf)); buf = []; i += 2; continue
        if c == "|" and i + 1 < n and cmd[i + 1] == "|":
            parts.append("".join(buf)); buf = []; i += 2; continue
        if c in ("|", ";"):
            parts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(c)
        i += 1
    if buf:
        parts.append("".join(buf))
    return [s.strip() for s in parts if s.strip()]


def classify(cmd):
    """返回 (level, reasons)。level: read / write / danger"""
    cmd = (cmd or "").strip()
    if not cmd:
        return "read", []
    segments = split_subcommands(cmd)
    reasons = []
    level = "read"
    for seg in segments:
        low = seg.lower().strip()
        hit = False
        for pat, why in DANGER_PATTERNS:
            if re.search(pat, low, re.I):
                reasons.append("%s（%s）" % (why, seg[:60]))
                level = "danger"
                hit = True
                break
        if hit:
            continue
        w_hit = False
        for pat, why in WRITE_PATTERNS:
            if re.search(pat, low, re.I):
                reasons.append("%s（%s）" % (why, seg[:60]))
                if level != "danger":
                    level = "write"
                w_hit = True
                break
        if w_hit:
            continue
        if _WRITE_HINT.search(low):
            reasons.append("写操作（%s）" % seg[:60])
            if level != "danger":
                level = "write"
        elif not _is_read_only(low):
            # 未知命令按 write 保守处理
            if level == "read":
                level = "write"
                reasons.append("未识别命令，按写操作保守处理（%s）" % seg[:60])
    return level, reasons


def check(cmd, read_only=False, auto_approve=False):
    """返回 (allowed, need_confirm, level, reasons)

    安全原则：
      · 只读模式下，非只读命令一律拦截。
      · **未识别/不确定的命令，即使勾选了「自动批准」也必须弹窗让用户确认**——
        自动批准只应自动化「已明确判定」的写/危险操作，不能把「拿不准」的命令也放行。
      · 已明确判定的写/危险命令：勾选自动批准则自动放行，否则需确认。
    """
    level, reasons = classify(cmd)
    uncertain = any("未识别命令" in r for r in reasons)
    if read_only and level != "read":
        return False, False, level, ["只读模式已开启，拦截非只读命令"] + reasons
    if uncertain:
        return True, True, level, reasons
    if level in ("write", "danger") and not auto_approve:
        return True, True, level, reasons
    return True, False, level, reasons
