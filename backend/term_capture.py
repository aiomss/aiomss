# -*- coding: utf-8 -*-
"""终端屏幕还原：把 SSH 推给 xterm.js 的原始 VT 流，转录成「屏幕上出现过的文字」。

审计要求「一比一」：终端上打印过的内容（登录横幅、命令回显、命令输出、报错、
提示符……）都要按显示顺序落到 .txt 里，且必须是人能直接读的纯文本。所以这里
维护一块 cols × rows 的字符屏幕，模拟 xterm 的显示行为：

  · 只丢弃颜色 / 光标定位等不可见控制码，可见文字原样保留（中文等宽字符占 2 列）
  · 处理 CR / LF / BS / TAB、CSI 光标移动、清屏 / 清行、滚屏、备用屏幕切换
  · 一行写完（LF）立即吐出，会话进行中打开 .txt 就能看到实时增长的内容
  · 行被 \r 覆盖（进度条、命令补全）时以最终显示内容为准
  · 滚出屏幕或清屏前先把这一屏可见文字吐出；会话结束 flush() 吐出最后一屏
  · SSH 分块可能把一条转义序列切成两半，feed() 会把不完整的尾巴缓存到下一块

未经任何处理的原始字节流另存为 .raw，可逐字节复核 / 回放，本模块不负责。
"""
import re
import unicodedata

# CSI / OSC / ESC + 单字符。OSC 结束符必须出现，否则视为「跨块未完成」。
_ESC_SEQ = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]"       # ESC [ ... 最终字节
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  # ESC ] ... BEL / ST
    r"|(?![\[\]])[ -~])"                # ESC + 单字符（ESC 7 / 8 / M / D / c ...）
)
_MAX_PENDING = 256                      # 缓存的不完整转义序列上限，防异常流撑爆内存


def _clamp(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _cw(ch):
    """字符占几个显示列：中文/全角 2，组合字符 0，其余 1。"""
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class _Line:
    """一行屏幕内容，按「显示列」存字符；宽字符的第二格用空串占位。"""
    __slots__ = ("cells",)

    def __init__(self, cols):
        self.cells = [" "] * cols

    def clear(self):
        self.cells[:] = [" "] * len(self.cells)

    def resize(self, cols):
        n = len(self.cells)
        if cols > n:
            self.cells += [" "] * (cols - n)
        elif cols < n:
            del self.cells[cols:]

    def put(self, col, ch):
        w = _cw(ch)
        n = len(self.cells)
        if w == 0:                       # 组合字符挂到前一格
            if 0 < col <= n and self.cells[col - 1] != "":
                self.cells[col - 1] += ch
            return 0
        if col >= n:
            return 1
        # 空串只可能是「前一个宽字符占用的右半格」。覆盖它时把左半格补成空格，
        # 否则会留下半个汉字；覆盖左半格时把右半格还原成空格。
        if self.cells[col] == "" and col > 0:
            self.cells[col - 1] = " "
        if col + 1 < n and self.cells[col + 1] == "" and self.cells[col] != "":
            self.cells[col + 1] = " "
        self.cells[col] = ch
        if w == 2 and col + 1 < n:
            self.cells[col + 1] = ""
        return w

    def erase(self, col, count=1):
        for i in range(max(0, col), min(len(self.cells), col + count)):
            self.cells[i] = " "

    def insert_blanks(self, col, count=1):
        n = len(self.cells)
        col = max(0, min(n, col))
        self.cells[col:col] = [" "] * count
        del self.cells[n:]

    def delete_chars(self, col, count=1):
        n = len(self.cells)
        col = max(0, min(n, col))
        del self.cells[col:min(n, col + count)]
        self.cells += [" "] * (n - len(self.cells))

    def text(self):
        return "".join(c for c in self.cells if c != "").rstrip()


class TerminalTranscript:
    """增量式终端转录器。feed(text) 返回本次新产生的可读行，flush() 返回最后一屏。"""

    def __init__(self, cols=120, rows=36):
        self.cols = _clamp(cols, 20, 400, 120)
        self.rows = _clamp(rows, 6, 200, 36)
        self.screen = [_Line(self.cols) for _ in range(self.rows)]
        self.last = [None] * self.rows      # 每行上一次已吐出的文字；None = 从未吐出
        self.row = 0
        self.col = 0
        self.saved = (0, 0)
        self._pending = ""                  # 跨块未完成的转义序列
        self._out = []
        self.lines_out = 0                  # 累计吐出的行数
        self.chars_out = 0                  # 累计吐出的字符数

    # ---------- 对外 ----------
    def resize(self, cols, rows):
        self.cols = _clamp(cols, 20, 400, self.cols)
        self.rows = _clamp(rows, 6, 200, self.rows)
        for ln in self.screen:
            ln.resize(self.cols)
        while len(self.screen) < self.rows:
            self.screen.append(_Line(self.cols))
            self.last.append(None)
        if len(self.screen) > self.rows:
            del self.screen[self.rows:]
            del self.last[self.rows:]
        self.row = max(0, min(self.rows - 1, self.row))
        self.col = max(0, min(self.cols - 1, self.col))

    def feed(self, text):
        """喂入一段（已按 UTF-8 增量解码的）VT 文本，返回新增的纯文本行。"""
        if not text:
            return []
        if self._pending:
            text = self._pending + text
            self._pending = ""
        # 若末尾是不完整的转义序列，留到下一块一起解析，避免把 ESC [ 3 打成可见字符
        idx = text.rfind("\x1b")
        if idx >= 0:
            tail = text[idx:]
            if not _ESC_SEQ.match(tail):
                if len(tail) < _MAX_PENDING:
                    self._pending = tail
                    text = text[:idx]
                else:                       # 异常超长：丢掉这个 ESC，其余照常处理
                    text = text[:idx] + tail[1:]
        self._out = []
        pos = 0
        for m in _ESC_SEQ.finditer(text):
            if m.start() > pos:
                self._plain(text[pos:m.start()])
            self._esc(m.group(0))
            pos = m.end()
        if pos < len(text):
            self._plain(text[pos:])
        out, self._out = self._out, []
        return out

    def flush(self):
        """会话结束 / 清屏：把屏幕上尚未吐出的可见行按显示顺序吐出（去尾部空行）。"""
        self._out = []
        self._flush_visible()
        out, self._out = self._out, []
        return out

    # ---------- 输出 ----------
    def _emit_row(self, i):
        t = self.screen[i].text()
        if self.last[i] == t:               # 同一行重复刷新且内容未变，不重复落盘
            return
        self.last[i] = t
        self._out.append(t)
        self.lines_out += 1
        self.chars_out += len(t) + 1

    def _flush_visible(self):
        last_nonblank = -1
        for i, ln in enumerate(self.screen):
            if ln.text():
                last_nonblank = i
        for i in range(last_nonblank + 1):
            self._emit_row(i)

    def _clear_screen(self):
        for ln in self.screen:
            ln.clear()
        self.last = [None] * self.rows
        self.row = 0
        self.col = 0

    def _scroll(self, n):
        for _ in range(max(0, min(n, self.rows))):
            self._emit_row(0)
            self.screen.pop(0)
            self.last.pop(0)
            self.screen.append(_Line(self.cols))
            self.last.append(None)

    # ---------- 字符与光标 ----------
    def _linefeed(self):
        if self.row + 1 >= self.rows:
            self._scroll(1)
            self.row = self.rows - 1
        else:
            self.row += 1

    def _put(self, ch):
        w = _cw(ch)
        if self.col + max(1, w) > self.cols:      # 自动换行
            self._emit_row(self.row)
            self.col = 0
            self._linefeed()
        used = self.screen[self.row].put(self.col, ch)
        self.col += max(1, used)

    def _plain(self, s):
        for ch in s:
            o = ord(ch)
            if ch == "\r":
                self.col = 0
            elif ch == "\n" or ch == "\x0b" or ch == "\x0c":
                self._emit_row(self.row)         # 写完一行立即落盘（实时可见）
                self._linefeed()
            elif ch in ("\b", "\x7f"):           # BS / DEL 只回退光标，字符由后续空格覆盖
                self.col = max(0, self.col - 1)
            elif ch == "\t":
                self.col = min(self.cols - 1, (self.col // 8 + 1) * 8)
            elif o < 32 or o == 127:
                continue                         # BEL / 其它 C0 控制字符不可见
            else:
                self._put(ch)

    def _esc(self, seq):
        if seq.startswith("\x1b["):
            self._csi(seq[2:])
        elif seq.startswith("\x1b]"):
            pass                                 # OSC 标题等，不影响正文
        elif seq == "\x1b7":
            self.saved = (self.row, self.col)
        elif seq == "\x1b8":
            self.row, self.col = self.saved
        elif seq == "\x1bD":                     # IND 下移一行
            self._linefeed()
        elif seq == "\x1bM":                     # RI 上移一行
            self.row = max(0, self.row - 1)
        elif seq == "\x1bE":
            self.col = 0
            self._linefeed()
        elif seq == "\x1bc":                     # RIS 复位
            self._flush_visible()
            self._clear_screen()

    def _csi(self, body):
        if not body:
            return
        private = body.startswith("?")
        core = body[1:] if private else body
        final = core[-1]
        raw = core[:-1].lstrip("><!=")
        nums = []
        for part in raw.split(";"):
            if part == "":
                nums.append(0)
            elif part.isdigit():
                nums.append(int(part))
            else:
                nums.append(0)
        n = nums[0] if nums else 0
        n = n if n > 0 else 1
        p2 = nums[1] if len(nums) > 1 else 1

        if private and final in ("h", "l"):
            code = nums[0] if nums else 0
            if code in (47, 1047, 1049):         # 备用屏幕（vim / less / top）
                self._flush_visible()
                self._clear_screen()
            return
        if private:
            return

        if final == "A":
            self.row = max(0, self.row - n)
        elif final == "B":
            self.row = min(self.rows - 1, self.row + n)
        elif final == "C":
            self.col = min(self.cols - 1, self.col + n)
        elif final == "D":
            self.col = max(0, self.col - n)
        elif final == "E":
            self.row = min(self.rows - 1, self.row + n)
            self.col = 0
        elif final == "F":
            self.row = max(0, self.row - n)
            self.col = 0
        elif final in ("G", "`"):
            self.col = max(0, min(self.cols - 1, n - 1))
        elif final == "d":
            self.row = max(0, min(self.rows - 1, n - 1))
        elif final in ("H", "f"):
            self.row = max(0, min(self.rows - 1, n - 1))
            self.col = max(0, min(self.cols - 1, p2 - 1))
        elif final == "J":
            if raw.startswith("2") or raw.startswith("3"):
                self._flush_visible()            # 清屏前先把这一屏留档
                self._clear_screen()
            elif raw.startswith("1"):
                self.screen[self.row].erase(0, self.col + 1)
                for i in range(self.row):
                    self.screen[i].clear()
                    self.last[i] = None
            else:
                self.screen[self.row].erase(self.col)
                for i in range(self.row + 1, self.rows):
                    self.screen[i].clear()
                    self.last[i] = None
        elif final == "K":
            if raw.startswith("1"):
                self.screen[self.row].erase(0, self.col + 1)
            elif raw.startswith("2"):
                self.screen[self.row].clear()
            else:
                self.screen[self.row].erase(self.col)
        elif final == "S":
            self._scroll(n)
        elif final == "T":
            for _ in range(min(n, self.rows)):
                self.screen.pop()
                self.last.pop()
                self.screen.insert(0, _Line(self.cols))
                self.last.insert(0, None)
        elif final == "L":
            for _ in range(min(n, self.rows)):
                self.screen.pop(self.rows - 1)
                self.last.pop(self.rows - 1)
                self.screen.insert(self.row, _Line(self.cols))
                self.last.insert(self.row, None)
        elif final == "M":
            for _ in range(min(n, self.rows)):
                self.screen.pop(self.row)
                self.last.pop(self.row)
                self.screen.append(_Line(self.cols))
                self.last.append(None)
        elif final == "P":
            self.screen[self.row].delete_chars(self.col, n)
        elif final == "@":
            self.screen[self.row].insert_blanks(self.col, n)
        elif final == "X":
            self.screen[self.row].erase(self.col, n)
        elif final == "s":
            self.saved = (self.row, self.col)
        elif final == "u":
            self.row, self.col = self.saved
        # m 颜色、h/l 模式、n 查询、r 滚动区域等对正文无影响，忽略
