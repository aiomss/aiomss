/* AI 结论 / 命令问答的富文本渲染（纯逻辑 + DOM 两层）。
 *
 * 纯函数 cleanLines / cleanText / looksTruncatedLine 不依赖 DOM，
 * 可在 Node 里直接单测（见 scripts/test_render.mjs）；renderRichText 只负责把
 * 结构化行画成 DOM。cleanLines 同时保留 text（带 Markdown 标记，供 DOM 渲染加粗/
 * 行内代码）与 plain（已清理，供纯文本断言），两者互不干扰。
 *
 * 浏览器暴露 window.AiOpsRender，CommonJS 暴露 module.exports。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.AiOpsRender = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /** 去掉一行内的 Markdown 标记，返回纯文本（不破坏中文与命令示例）。 */
  function stripInline(text) {
    return String(text || '')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/__([^_]+)__/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\*/g, '')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  /** 长文末尾的短半句（如「该机属于」）几乎必然是被 max_tokens 截断的残缺内容。 */
  function looksTruncatedLine(t) {
    const s = String(t || '').trim();
    if (!s || s.length < 2 || s.length > 20) return false;
    if (/[。！？!?；;：:，,、】）)\]}]$/.test(s)) return false;
    if (/^[-`\w/.\s]+$/.test(s)) return false;          // 纯命令 / 代码不算半句
    return /[\u4e00-\u9fa5]/.test(s);
  }

  const headingKey = (s) => String(s || '')
    .replace(/^(?:[一二三四五六七八九十]+[、.]|\d+[.、)]\s*)/, '').trim();
  const plainOf = (x) => x.plain || stripInline(x.text || '');

  /**
   * 把挤在同一行的多条内容拆成独立条目（用户反馈：建议 1) 2) 3) 挤成一坨看不清）。
   * 只在「句子边界之后」出现编号/项目符号时才拆，避免误伤 "配置 1) 选项" 这类行内引用。
   * 支持：· 开头（前一条以 。；：结尾）、1) 2） 3. 4、 等编号。
   */
  function splitInlineItems(t) {
    const s = String(t || '');
    if (s.length < 24) return [s];
    // 拆点：句读后紧跟「· 」或「N) / N） / N. / N、」编号
    const parts = s.split(/(?<=[。；;：:！!？?])\s*(?=(?:·\s*)?(?:\d{1,2}[)）.、]|\d{1,2}\s*\)|·\s))/);
    const out = parts.map(x => x.trim()).filter(Boolean);
    return out.length > 1 ? out : [s];
  }

  /** 提取行首编号（1) / 2） / 3. / 4、），返回 {num, rest}；无编号返回 {num:null, rest:t}。 */
  function extractNum(t) {
    const m = String(t || '').match(/^(\d{1,2})[)）.、]\s*(.*)$/);
    if (m && m[2]) return { num: m[1], rest: m[2] };
    return { num: null, rest: t };
  }

  /**
   * 把 AI 原文解析成结构化行，顺手清掉渲染噪声。
   * @param {string} text   AI 原文（可能是流式中间态）
   * @param {object}  opts   { final: boolean } —— final=true 时才做尾部残句清理
   * @returns {{lines: Array, truncated: boolean}}
   */
  function cleanLines(text, opts) {
    const final = !!(opts && opts.final);
    const src = String(text || '').replace(/\r/g, '');
    const rawLines = src.split('\n');
    const lines = [];
    let inCode = false;
    let codeBuf = [];
    let truncated = false;

    const flushCode = () => {
      if (!codeBuf.length) return;
      const body = codeBuf.join('\n').replace(/\n+$/, '').trim();
      if (body) lines.push({ type: 'code', text: body, plain: body });
      codeBuf = [];
    };

    for (let i = 0; i < rawLines.length; i++) {
      const raw = rawLines[i];
      if (/^\s*```/.test(raw)) {          // 代码围栏行本身不显示，内容转成代码块
        flushCode();
        inCode = !inCode;
        continue;
      }
      if (inCode) { codeBuf.push(raw.replace(/\s+$/, '')); continue; }

      let t = raw.trim();
      if (!t) continue;
      // 水平分隔线 / 孤立破折号
      if (/^(?:[-*_]\s*){3,}$/.test(t) || /^--+$/.test(t)) continue;
      t = t.replace(/^>\s?/, '');
      if (!t) continue;

      const sec = t.match(/^【(.+?)】\s*(.*)$/) || t.match(/^\[(.+?)\]\s*(.*)$/);
      if (sec) {
        const tag = stripInline(sec[1]);
        const body = (sec[2] || '').trim();
        if (body) {
          // 小节正文同样拆行：第一条挂在标题下（去掉行首项目符号），其余独立成条
          const segs = splitInlineItems(body).map(x => x.replace(/^[·•►]\s*/, ''));
          lines.push({ type: 'section', tag, text: segs[0], plain: stripInline(segs[0]) });
          segs.slice(1).forEach(seg => {
            const n = extractNum(seg);
            lines.push(n.num
              ? { type: 'num', num: n.num, text: n.rest, plain: stripInline(n.rest) }
              : { type: 'bullet', text: seg.replace(/^[·•►]\s*/, ''), plain: stripInline(seg.replace(/^[·•►]\s*/, '')) });
          });
        } else {
          lines.push({ type: 'section', tag, text: '', plain: '' });
        }
        continue;
      }

      let type = 'text';
      const headMd = t.match(/^#{1,4}\s+(.*)$/);
      const cnHeading = /^[一二三四五六七八九十]+[、.]/.test(t);
      if (headMd) { t = headMd[1]; type = 'heading'; }
      else if (cnHeading && stripInline(t).length < 40) type = 'heading';
      else if (/^[·•►]\s*/.test(t)) { type = 'bullet'; t = t.replace(/^[·•►]\s*/, ''); }
      else if (/^[-*]\s+/.test(t)) { type = 'bullet'; t = t.replace(/^[-*]\s+/, ''); }
      else if (/^\d{1,2}[)）.、]\s*\S/.test(t)) {
        const n = extractNum(t);
        if (n.num && !cnHeading) {
          if (stripInline(n.rest)) {
            lines.push({ type: 'num', num: n.num, text: n.rest, plain: stripInline(n.rest) });
          }
          continue;
        }
        type = 'bullet'; t = t.replace(/^\d+[.、)]\s*/, '');
      }

      if (!stripInline(t)) continue;

      // 相邻重复标题只保留一个：允许中间夹代码块（模型偶发把同一标题带围栏重复输出）
      if (type === 'heading' && lines.length) {
        let j = lines.length - 1;
        while (j >= 0 && lines[j].type === 'code') j--;
        if (j >= 0 && lines[j].type === 'heading' &&
            headingKey(plainOf(lines[j])) === headingKey(stripInline(t))) continue;
      }

      // 一行里挤了多条带句读边界的编号/项目符号内容 → 拆成多行
      if (type === 'bullet' || type === 'text') {
        const segs = splitInlineItems(t);
        if (segs.length > 1) {
          segs.forEach(seg => {
            const n = extractNum(seg);
            if (n.num && stripInline(n.rest)) {
              lines.push({ type: 'num', num: n.num, text: n.rest, plain: stripInline(n.rest) });
            } else {
              const b = seg.replace(/^[·•►]\s*/, '');
              if (stripInline(b)) lines.push({ type: 'bullet', text: b, plain: stripInline(b) });
            }
          });
          continue;
        }
      }
      lines.push({ type, text: t, plain: stripInline(t) });
    }
    flushCode();

    if (final && lines.length > 1) {
      // 长文末尾的残缺短句（「该机属于」/「7. 脚本化批量」）：删掉并标记。
      // 注意 lines.length > 1：绝不能把「唯一一行」也删掉，否则结论正文会整段空白。
      const last = lines[lines.length - 1];
      if (src.length > 60 && (last.type === 'text' || last.type === 'bullet' || last.type === 'num' || last.type === 'heading')
          && looksTruncatedLine(plainOf(last))) {
        lines.pop(); truncated = true;
      }
      // 末尾空标题（只有标题没有下文）：只删最后一个，且保留至少一行
      if (lines.length > 1 && lines[lines.length - 1].type === 'heading') {
        lines.pop(); truncated = true;
      }
    }
    // 兜底：清理后若把内容清空了、但原文非空，则保留原文一行，避免「AI 结论与建议」整块空白
    if (!lines.length && src.trim()) {
      lines.push({ type: 'text', text: src.trim(), plain: stripInline(src.trim()) });
    }
    return { lines, truncated };
  }

  /** 纯文本版清理结果，便于 Node 单测断言。 */
  function cleanText(text, opts) {
    const r = cleanLines(text, opts);
    return r.lines.map(x => x.type === 'section'
      ? '【' + x.tag + '】' + (x.plain ? ' ' + x.plain : '')
      : (x.type === 'num' ? x.num + '. ' + plainOf(x) : plainOf(x))).join('\n');
  }

  function make(doc, tag, cls, text) {
    const e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  /** 渲染一行内的 `行内代码` 与 **加粗**，并清掉孤立星号。 */
  function inlineNodes(node, text, doc) {
    const d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d) return;
    String(text || '').split(/(`[^`]+`|\*\*[^*]+\*\*)/g).forEach(p => {
      if (!p) return;
      if (p.startsWith('`') && p.endsWith('`')) {
        node.appendChild(make(d, 'code', 'sm-code', p.slice(1, -1)));
      } else if (p.startsWith('**') && p.endsWith('**')) {
        node.appendChild(make(d, 'strong', 'sm-strong', stripInline(p.slice(2, -2))));
      } else {
        node.appendChild(d.createTextNode(stripInline(p)));
      }
    });
  }

  /** 把 AI 原文画进容器；返回 cleanLines 的结果，便于测试断言。 */
  function renderRichText(container, text, opts) {
    const doc = container.ownerDocument || document;
    container.innerHTML = '';
    const result = cleanLines(text, opts);
    result.lines.forEach(item => {
      let node;
      if (item.type === 'section') {
        node = make(doc, 'div', 'sm-sec');
        node.appendChild(make(doc, 'span', 'sm-sec-tag', item.tag));
        if (item.text) {
          const rest = make(doc, 'span', 'sm-sec-rest');
          inlineNodes(rest, item.text, doc);
          node.appendChild(rest);
        }
      } else if (item.type === 'heading') {
        node = make(doc, 'div', 'sm-h'); inlineNodes(node, item.text, doc);
      } else if (item.type === 'bullet') {
        node = make(doc, 'div', 'sm-li');
        node.appendChild(make(doc, 'span', 'sm-bullet', '•'));
        const c = make(doc, 'span', 'sm-li-text');
        inlineNodes(c, item.text, doc); node.appendChild(c);
      } else if (item.type === 'num') {
        node = make(doc, 'div', 'sm-li sm-num');
        node.appendChild(make(doc, 'span', 'sm-num-badge', item.num));
        const c = make(doc, 'span', 'sm-li-text');
        inlineNodes(c, item.text, doc); node.appendChild(c);
      } else if (item.type === 'code') {
        node = make(doc, 'pre', 'sm-pre', item.text);
      } else {
        node = make(doc, 'div', 'sm-p'); inlineNodes(node, item.text, doc);
      }
      container.appendChild(node);
    });
    if (result.truncated) {
      container.appendChild(make(doc, 'div', 'sm-trunc',
        '⚠ 部分内容未完整生成，可缩小问题范围后重试。'));
    }
    if (!container.childNodes.length) container.appendChild(make(doc, 'div', 'sm-p', '（无内容）'));
    return result;
  }

  return { cleanLines, cleanText, looksTruncatedLine, stripInline,
           inlineNodes, renderRichText };
});
