# -*- coding: utf-8 -*-
"""终端屏幕还原（1:1 转录）纯函数单测，无需 SSH / 浏览器。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.term_capture import TerminalTranscript  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("[OK]   " + name)
    else:
        fail += 1
        print("[FAIL] " + name + (" " + str(extra) if extra else ""))


t = TerminalTranscript(80, 10)
check("普通命令行与输出逐行 1:1 转录",
      t.feed("[root@king ~]# ls -la\r\nopt/  python.file/\r\n") ==
      ["[root@king ~]# ls -la", "opt/  python.file/"])
check("SGR 颜色码被剥离，文字不受影响",
      t.feed("\x1b[1;32mOK\x1b[0m\r\n") == ["OK"])
check("中文宽字符不被插入空格",
      t.feed("\x1b[32m绿色\x1b[0m 中文宽字符\r\n") == ["绿色 中文宽字符"])
check("回车覆盖（进度条 / 补全）以最终显示为准",
      t.feed("10%\r50%\r100%\r\n") == ["100%"])
check("退格删除（shell 回显 \\b 空格 \\b）",
      t.feed("abcd\b \b\b \b\r\n") == ["ab"])
check("TAB 展开为列对齐空格", t.feed("a\tb\r\n") == ["a       b"])
check("未换行的最后一屏由 flush 吐出",
      t.feed("[root@king ~]# ") == [] and t.flush() == ["[root@king ~]#"])

t2 = TerminalTranscript(40, 6)
check("转义序列被 SSH 拆块时不会把 ESC[3 印成可见字符",
      t2.feed("hello \x1b[3") == [] and
      t2.feed("1mRED\x1b[0m\r\n") == ["hello RED"])
check("OSC 标题序列不进入正文",
      t2.feed("\x1b]0;my-title\x07body\r\n") == ["body"])

t3 = TerminalTranscript(40, 5)
t3.feed("first\r\nsecond\r\n")
check("清屏前先留档，清屏后仍能继续实时转录",
      t3.feed("\x1b[2J\x1b[H") == [] and
      t3.feed("new screen\r\n") == ["new screen"])

t4 = TerminalTranscript(20, 3)
out = t4.feed("l1\r\nl2\r\nl3\r\nl4\r\nl5\r\n")
check("滚屏时按显示顺序吐出每一行且不重复",
      out == ["l1", "l2", "l3", "l4", "l5"], out)

t5 = TerminalTranscript(40, 10)
check("CSI 光标上移 / 清行后覆盖的文字被正确还原",
      t5.feed("abcdef\r\n") == ["abcdef"] and
      t5.feed("\x1b[A\x1b[2Kxyz\r\n") == ["xyz"])

t6 = TerminalTranscript(40, 10)
check("备用屏幕（vim/less/top）进出时都留下画面",
      "vim content" in "\n".join(t6.feed("\x1b[?1049hvim content\r\n")) or
      "vim content" in "\n".join(t6.feed("\x1b[?1049l") + t6.flush()))

t7 = TerminalTranscript(40, 10)
t7.resize(100, 20)
check("resize 后屏幕尺寸生效",
      t7.cols == 100 and t7.rows == 20 and len(t7.screen) == 20)

t8 = TerminalTranscript(30, 6)
lines = t8.feed("cat /etc/hosts\r\n")
lines += t8.feed("127.0.0.1 localhost\r\n")
check("分成多个数据块到达时行不会丢失或错序",
      lines == ["cat /etc/hosts", "127.0.0.1 localhost"], lines)

print("\n结果: %d 通过 / %d 失败" % (ok, fail))
sys.exit(1 if fail else 0)
