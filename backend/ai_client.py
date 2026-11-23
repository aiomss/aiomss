# -*- coding: utf-8 -*-
"""OpenAI 兼容 AI 客户端（适配 DeepSeek V4 推理模型：reasoning_content / JSON mode / SSE）。

包含一层「回复容错」：推理模型偶尔会输出畸形 JSON（缺花括号、key 被写成 [thought]、
中文引号、末尾被截断等）。本模块会逐级修复，实在修不好也能把字段和结论抠出来，
避免把一整坨原始 JSON 当作错误抛给用户。

v1.2 新增：截断检测与结论续写。推理模型会先消耗大量 token 生成思维链，
正文可能被 max_tokens 拦腰截断（表现为结论只剩「该机属于」这类半句），
因此 chat_json / chat_stream 会记录 finish_reason，供 Agent 决定是否补全。
"""
import json
import re

import requests

FIELDS = ("thought", "command", "done", "summary")


def _normalize(text):
    """统一引号、键名、布尔字面量，去掉 Markdown 围栏。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I | re.S)
    t = re.sub(r"\s*```\s*$", "", t, flags=re.S)
    t = (t.replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2018", "'").replace("\u2019", "'"))
    # 模型有时把键写成 "[thought]" / [thought]: / 'thought'
    # 先处理带引号的整体，避免替换后变成 ""thought"" 破坏结构
    t = re.sub(r'(["\'])\[\s*(thought|command|done|summary)\s*\]\1', r'"\2"', t, flags=re.I)
    t = re.sub(r'\[\s*(thought|command|done|summary)\s*\]\s*:', r'"\1":', t, flags=re.I)
    t = re.sub(r"\bTrue\b", "true", t)
    t = re.sub(r"\bFalse\b", "false", t)
    t = re.sub(r"\bNone\b", "null", t)
    return t.strip()


def _try_load(s):
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _balance_fix(s):
    """修复被 max_tokens 截断的 JSON：补齐字符串引号、花括号、去掉尾逗号。"""
    s = s.strip().rstrip(",")
    in_str = False
    esc = False
    depth = 0
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
    tail = '"' if in_str else ""
    if depth > 0:
        tail += "}" * depth
    fixed = (s + tail) if tail else s
    fixed = re.sub(r",\s*\}", "}", fixed)
    return fixed


def _salvage(text):
    """最后一招：按字段名切段，把畸形文本里的结论/命令抠出来。返回 dict 或 None。"""
    t = _normalize(text)
    marks = list(re.finditer(r'["\']?(thought|command|done|summary)["\']?\s*:\s*', t, re.I))
    if not marks:
        return None
    out = {}
    for i, m in enumerate(marks):
        key = m.group(1).lower()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        seg = t[m.end():end].strip()
        seg = seg.rstrip().rstrip("}").rstrip().rstrip(",").rstrip()
        if seg.startswith('"'):
            mm = re.match(r'"((?:[^"\\]|\\.)*)"', seg, re.S)
            seg = mm.group(1) if mm else seg.strip('"')
        elif seg.startswith("'"):
            mm = re.match(r"'((?:[^'\\]|\\.)*)'", seg, re.S)
            seg = mm.group(1) if mm else seg.strip("'")
        else:
            seg = seg.split(",")[0].strip()
        seg = seg.strip().strip('"\'')
        if key == "done":
            out[key] = seg.lower() in ("true", "1", "yes", "是")
        else:
            out[key] = (seg.replace("\\n", "\n").replace("\\t", "\t")
                        .replace('\\"', '"').replace("\\\\", "\\")).strip()
    for k in FIELDS:
        out.setdefault(k, False if k == "done" else "")
    out["_recovered"] = True
    return out if (out.get("summary") or out.get("thought") or out.get("command")) else None


def _json_from_text(text):
    """稳健解析：直接 JSON > 局部对象 > 补齐花括号 > 裸字段 > 字段抠取。"""
    if not text:
        return None
    t = _normalize(text)

    v = _try_load(t)
    if v:
        return v

    # 从第一个 { 开始到末尾（可能带解释文字或截断）
    idx = t.find("{")
    if idx >= 0:
        cand = t[idx:].strip()
        v = _try_load(cand) or _try_load(_balance_fix(cand))
        if v:
            return v
        # 截取到最后一个 } 再试
        last = cand.rfind("}")
        if last > 0:
            v = _try_load(cand[:last + 1]) or _try_load(_balance_fix(cand[:last + 1]))
            if v:
                return v

    # 单引号写法 {'thought':'t', 'command':'whoami', ...}
    if "'" in t:
        t3 = re.sub(r"'([^'\\]*)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', t)
        v = _try_load(t3) or _try_load(_balance_fix(t3))
        if v:
            return v

    # 完全没有外层花括号，形如  "thought":"...", "command":"", "done":true
    if re.search(r'["\']?(thought|command|done|summary)["\']?\s*:\s*', t, re.I):
        cand = "{" + t.strip().rstrip(",") + "}"
        v = _try_load(cand) or _try_load(_balance_fix(cand))
        if v:
            v["_recovered"] = True
            return v

    return _salvage(text)


def humanize_raw(text):
    """把彻底无法解析的原始回复清洗成人类可读文本。"""
    t = _normalize(text)
    t = (t.replace("\\n", "\n").replace("\\t", "  ")
          .replace('\\"', '"').replace("\\\\", "\\"))
    t = re.sub(r'["\']?\[?(thought|command|done|summary)\]?["\']?\s*:\s*',
               lambda m: {"thought": "【判断】", "command": "【命令】",
                          "done": "【结束】", "summary": "【结论】"}[m.group(1).lower()] + " ",
               t, flags=re.I)
    t = re.sub(r"^\s*\{|\}\s*$", "", t, flags=re.M)
    t = re.sub(r"[ \t]*,[ \t]*$", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# --------------------------------------------------------------------------
# 结论完整性：推理模型的正文常被 max_tokens 拦腰截断，只剩「该机属于」这类半句
# --------------------------------------------------------------------------
_BAD_TAIL = ("...", "…", "，", ",", "、", "：", ":", "（", "(", "【", "-", "/", "·")

# 句末收尾符号：中文句号/叹号/问号、英文标点、右引号与右括号（含全角）。
# 补齐全角右括号/书名号，避免中文结论以「）」「》」正常收尾时被误判为半句。
_END_PUNCT = re.compile(r"[。！？.!?」』】）〕〉》］｝\)}\]]\s*$")


def summary_looks_truncated(text):
    """判断结论是否像是被截断的半句。给 Agent 决定是否发起续写补全。

    判定顺序很关键：空文本 / 明显半截的尾巴优先，其次看是否已有句末标点，
    最后才用长度兜底。若先按长度判断，「【现象】…约 207 个进程。」这类
    带句号的完整短句会被误判成截断，白白多发起一次 complete_summary()
    续写 AI 调用，甚至造成结论文字重复。
    """
    t = (text or "").strip()
    if not t:
        return True
    if t.endswith(_BAD_TAIL):
        return True
    if _END_PUNCT.search(t):
        return False
    # 没有句末标点的短段落很可能是半句；长段落允许以列表项收尾
    return len(t) < 120


def complete_summary(ai, partial, max_tokens=2048):
    """结论被截断时续写补完整；失败或模型无输出时原样返回，绝不抛异常。"""
    tail = (partial or "").strip()
    if not tail:
        return tail
    try:
        text, _ = ai.chat_text([
            {"role": "system", "content":
             "你在续写一段被截断的中文运维结论。只输出续写的部分，不要重复已有内容，"
             "不要写 Markdown 代码围栏，不要加新的大标题，保持原文的分节与列表风格，"
             "把没说完的句子补完整并收尾。"},
            {"role": "user", "content":
             "下面这段结论在结尾处被截断了，请从截断处接着写完整（只输出续写内容）：\n\n"
             + tail[-1600:]},
        ], max_tokens=max_tokens)
    except Exception:
        return tail
    more = (text or "").strip()
    if not more:
        return tail
    # 模型偶尔把最后一句重打一遍，做一次轻量去重
    last = tail[-40:]
    if len(last) >= 20 and more.startswith(last[:20]):
        more = more[len(last[:20]):].lstrip()
    return (tail + "\n" + more).strip()


class AIClient:
    def __init__(self, profile, model=None, timeout=180):
        self.profile = profile or {}
        self.base_url = (self.profile.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.api_key = self.profile.get("api_key", "")
        self.model = model or self.profile.get("default_model") or "deepseek-flash"
        self.effort = self.profile.get("effort") or "low"
        self.timeout = timeout
        self.truncated = False       # 最近一次调用是否因 max_tokens 被截断

    def _headers(self):
        return {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }

    def _url(self, path):
        return self.base_url + path

    def list_models(self):
        r = requests.get(self._url("/models"), headers=self._headers(), timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        return [m.get("id") for m in data if m.get("id")]

    def test(self):
        try:
            r = requests.post(
                self._url("/chat/completions"), headers=self._headers(),
                json={"model": self.model,
                      "messages": [{"role": "user", "content": "回复 OK"}],
                      "max_tokens": 64},
                timeout=45,
            )
            if r.status_code != 200:
                txt = r.text[:200]
                if r.status_code == 404 and ("model" in txt.lower() or "Model not exist" in txt):
                    return False, ("HTTP 404：模型「%s」在该地址不存在。"
                                   "请点「拉取模型」获取真实模型名，或检查是否把模型名填在了"
                                   "「名称」框里（模型名应填在「默认模型」/「模型列表」）。" % self.model)
                if r.status_code == 401 or "Authentication" in txt:
                    return False, "HTTP 401：API Key 无效，请检查 Key 是否完整粘贴。"
                return False, "HTTP %d: %s" % (r.status_code, txt)
            data = r.json()
            msg = data["choices"][0]["message"]
            return True, "模型 %s 可用（回复: %s）" % (data.get("model", self.model),
                                                  (msg.get("content") or "OK")[:30])
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, e)

    def _payload(self, messages, json_mode=False, stream=False, max_tokens=4096):
        p = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if json_mode:
            p["response_format"] = {"type": "json_object"}
        if self.effort:
            p["effort"] = self.effort
        return p

    def _post(self, payload, stream=False):
        """若服务端不认 effort/response_format，自动去掉该字段重试一次。"""
        r = requests.post(self._url("/chat/completions"), headers=self._headers(),
                          json=payload, stream=stream, timeout=self.timeout)
        if r.status_code == 400 and ("effort" in payload or "response_format" in payload):
            p2 = dict(payload)
            p2.pop("effort", None)
            p2.pop("response_format", None)
            r = requests.post(self._url("/chat/completions"), headers=self._headers(),
                              json=p2, stream=stream, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError("AI 接口 HTTP %d: %s" % (r.status_code, r.text[:300]))
        return r

    def chat_json(self, messages, max_tokens=4096):
        """返回 (parsed_dict, reasoning_text, raw_text)。失败时 parsed 为 None。"""
        r = self._post(self._payload(messages, json_mode=True, max_tokens=max_tokens))
        choice = r.json()["choices"][0]
        self.truncated = choice.get("finish_reason") == "length"
        msg = choice["message"]
        raw = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        parsed = _json_from_text(raw)
        # 推理模型偶尔把正文塞进 reasoning 而 content 为空
        if not parsed and not raw.strip() and reasoning.strip():
            parsed = _json_from_text(reasoning)
        return parsed, reasoning, raw

    def chat_text(self, messages, max_tokens=4096):
        r = self._post(self._payload(messages, max_tokens=max_tokens))
        msg = r.json()["choices"][0]["message"]
        return (msg.get("content") or "").strip(), (msg.get("reasoning_content") or "")

    def chat_stream(self, messages, max_tokens=6000):
        """流式产出正文片段（reasoning 片段不带 content，会被自然跳过）。"""
        self.truncated = False
        r = self._post(self._payload(messages, stream=True, max_tokens=max_tokens), stream=True)
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                choice = chunk["choices"][0]
                if choice.get("finish_reason") == "length":
                    self.truncated = True
                delta = choice.get("delta", {})
                text = delta.get("content")
                if text:
                    yield text
            except Exception:
                continue
