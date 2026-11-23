/* AI 运维控制台 · 前端逻辑（原生 JS + 本地 xterm.js，无构建、无 CDN） */
'use strict';

const Render = window.AiOpsRender || null;
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

const TermCtor = window.Terminal || (window.xterm && window.xterm.Terminal);
const FitCtor = window.FitAddon && (window.FitAddon.FitAddon || window.FitAddon);

const S = {
  hosts: [], profiles: [], server: { port: 8848 }, activeHost: '',
  sessions: {},                 // hostId -> {term, fit, sid, es, wrap, alive, buf, timer}
  runId: '', running: false, mode: 'diagnose', steps: {},
  controller: null, reasoningCount: 0, reasoningChars: 0, maxRetry: 8,
  pendingConfirm: null,         // {confirm_id, step, remaining, timer, answered}
  answerText: '', answerDirty: false, answerTimer: null,
  lastEventAt: 0, watchdogNotified: false,
};

const QUICK = [
  { t: '日志异常分析', p: '分析最近 1 小时的系统日志，找出哪个进程/服务报错最多，列出高频错误模式、是否出现 OOM/段错误/连接拒绝/超时，并给出根因判断与处理建议。', m: 'diagnose' },
  { t: '全面系统巡检', p: '对系统做一次全面巡检：负载、CPU、内存、Swap、磁盘与 inode 使用率、TOP 进程、监听端口、失败的系统服务、近期错误日志。逐项给出当前值、阈值和结论（正常/警告/严重）。', m: 'diagnose' },
  { t: '/ 分区满定位', p: '/ 分区使用率 100%，先定位是哪个目录占用最大，再给出清理方案（保留最近 7 天日志），只做只读排查并给建议，不要直接删除。', m: 'diagnose' },
  { t: '服务异常排查', p: '检查所有失败的系统服务，找出启动失败的原因，结合 journalctl 错误日志给出修复建议和验证方法。', m: 'diagnose' },
  { t: '网络与端口', p: '检查监听端口、已建立连接、异常连接与防火墙状态，判断是否存在可疑监听或连接堆积。', m: 'diagnose' },
  { t: 'vim 参数怎么用', p: 'vim 的常用启动参数有哪些？如何只读打开并跳到第 100 行？请给出作用、参数表、常用组合和注意事项。', m: 'ask' },
];

/* ---------------- 主题配色（6 套预设，CSS 变量 + 终端主题联动） ---------------- */
const THEMES = {
  steel: { name: '默认 · 蓝灰', swatch: ['#f2f5fb', '#3f51d6', '#111a2b'],
    vars: {}, term: { background: '#111a2b', foreground: '#dbe5f4', cursor: '#79c9ff' } },
  midnight: { name: '深夜蓝', swatch: ['#0e1526', '#4c7cf0', '#0a0f1d'],
    vars: { '--bg': '#141b2e', '--surface': '#1c2540', '--surface-2': '#232e4d',
      '--line': '#33405f', '--line-2': '#2a3552', '--text': '#e2eaf8', '--text-2': '#b9c6e0',
      '--muted': '#8394b5', '--primary': '#5f86f2', '--primary-2': '#8a6cf0', '--primary-soft': '#26335c',
      '--code': '#131b30', '--term-bg': '#0a0f1d', '--info-soft': '#1d2a4a', '--ok-soft': '#12312a',
      '--warn-soft': '#33270f', '--danger-soft': '#38171c' },
    term: { background: '#0a0f1d', foreground: '#d5e2f6', cursor: '#79c9ff' } },
  emerald: { name: '翡翠绿', swatch: ['#f1f8f4', '#0e9f6e', '#0e2a22'],
    vars: { '--bg': '#eef5f1', '--surface': '#ffffff', '--surface-2': '#f4faf7',
      '--line': '#d3e5da', '--line-2': '#e2efe8', '--text': '#122a20', '--text-2': '#33544a',
      '--muted': '#6f8f83', '--primary': '#0e9f6e', '--primary-soft': '#e2f4ec',
      '--term-bg': '#0e2a22', '--code': '#f2f7f4' },
    term: { background: '#0e2a22', foreground: '#d8f0e6', cursor: '#5fe0a8' } },
  sand: { name: '暖沙橙', swatch: ['#f8f4ee', '#d97a2b', '#241b12'],
    vars: { '--bg': '#f7f3ec', '--surface': '#fffdf9', '--surface-2': '#f9f5ee',
      '--line': '#e6dcc9', '--line-2': '#efe7d8', '--text': '#33291d', '--text-2': '#5c4d3a',
      '--muted': '#93826c', '--primary': '#d97a2b', '--primary-soft': '#fbead6',
      '--term-bg': '#241b12', '--code': '#f6f1e8' },
    term: { background: '#241b12', foreground: '#f0e6d6', cursor: '#ffb45e' } },
  violet: { name: '紫罗兰', swatch: ['#f4f2fb', '#6a47e0', '#171130'],
    vars: { '--bg': '#f3f1fa', '--surface': '#ffffff', '--surface-2': '#f7f5fd',
      '--line': '#ddd6f0', '--line-2': '#e9e4f6', '--text': '#221a3d', '--text-2': '#4a3f6e',
      '--muted': '#8478a8', '--primary': '#6a47e0', '--primary-soft': '#ece6fc',
      '--term-bg': '#171130', '--code': '#f5f3fb' },
    term: { background: '#171130', foreground: '#e4dcfa', cursor: '#b79bff' } },
  contrast: { name: '高对比', swatch: ['#ffffff', '#0b57d0', '#000000'],
    vars: { '--bg': '#ffffff', '--surface': '#ffffff', '--surface-2': '#f4f4f4',
      '--line': '#9aa0a6', '--line-2': '#c4c8cc', '--text': '#101010', '--text-2': '#2a2a2a',
      '--muted': '#555f66', '--primary': '#0b57d0', '--primary-soft': '#dce9ff',
      '--term-bg': '#000000', '--code': '#f0f0f0' },
    term: { background: '#000000', foreground: '#f2f2f2', cursor: '#ffd400' } },
};
let TERM_THEME = Object.assign({
  cursorAccent: '#111a2b', selectionBackground: '#3c5f8f99',
  black: '#1b2436', red: '#ff7b82', green: '#6ee7a8', yellow: '#ffd479',
  blue: '#7aa5ff', magenta: '#c792ea', cyan: '#67d8ef', white: '#e6edf7',
  brightBlack: '#5d6d88', brightRed: '#ff9ba0', brightGreen: '#9af0c4',
  brightYellow: '#ffe3a3', brightBlue: '#a9c4ff', brightMagenta: '#dcb6f7',
  brightCyan: '#9ae9f8', brightWhite: '#ffffff',
}, THEMES.steel.term);

const PREF_KEY = 'aoc_prefs_v1';
const PREF = { kaOn: false, kaChar: ' ', kaSec: 180, kaClean: true, theme: 'steel' };
let kaTimer = null;

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (raw) Object.assign(PREF, JSON.parse(raw));
  } catch (e) { /* ignore */ }
}
function savePrefs() {
  try { localStorage.setItem(PREF_KEY, JSON.stringify(PREF)); } catch (e) { /* ignore */ }
}

function applyTheme(id) {
  const t = THEMES[id] || THEMES.steel;
  const root = document.documentElement;
  const baseVars = ['--bg','--surface','--surface-2','--line','--line-2','--text','--text-2','--muted',
    '--primary','--primary-2','--primary-soft','--code','--term-bg','--info-soft','--ok-soft',
    '--warn-soft','--danger-soft'];
  baseVars.forEach(v => root.style.removeProperty(v));
  Object.entries(t.vars).forEach(([k, v]) => root.style.setProperty(k, v));
  // 完整重建终端主题（保留 16 色板，只换背景/前景/光标）
  TERM_THEME = Object.assign({
    cursorAccent: t.term.background, selectionBackground: '#3c5f8f99',
    black: '#1b2436', red: '#ff7b82', green: '#6ee7a8', yellow: '#ffd479',
    blue: '#7aa5ff', magenta: '#c792ea', cyan: '#67d8ef', white: '#e6edf7',
    brightBlack: '#5d6d88', brightRed: '#ff9ba0', brightGreen: '#9af0c4',
    brightYellow: '#ffe3a3', brightBlue: '#a9c4ff', brightMagenta: '#dcb6f7',
    brightCyan: '#9ae9f8', brightWhite: '#ffffff',
  }, t.term);
  Object.values(S.sessions).forEach(s => { try { s.term.options.theme = TERM_THEME; } catch (e) { /* ignore */ } });
  PREF.theme = id;
  savePrefs();
  document.querySelectorAll('.theme-card').forEach(c =>
    c.classList.toggle('on', c.dataset.theme === id));
}

/* ---------------- 反闲置保活：每隔 N 秒向活动会话发送字符 ---------------- */
function kaTick() {
  if (!PREF.kaOn) return;
  const ch = PREF.kaChar === '' ? ' ' : PREF.kaChar;
  const payload = PREF.kaClean ? ch + '\x7f'.repeat(ch.length) : ch;
  Object.values(S.sessions).forEach(s => {
    if (s.sid && s.alive) jsonPost('/api/terminal/input', { sid: s.sid, data: payload }).catch(() => {});
  });
}
function applyKeepalive() {
  if (kaTimer) { clearInterval(kaTimer); kaTimer = null; }
  if (PREF.kaOn) {
    const sec = Math.max(15, Math.min(3600, parseInt(PREF.kaSec, 10) || 180));
    kaTimer = setInterval(kaTick, sec * 1000);
  }
}

function openPrefs() {
  $('prefKaOn').checked = PREF.kaOn;
  $('prefKaChar').value = PREF.kaChar === ' ' ? '' : PREF.kaChar;
  $('prefKaChar').placeholder = '空格';
  $('prefKaSec').value = PREF.kaSec;
  $('prefKaClean').checked = PREF.kaClean;
  const grid = $('themeGrid'); grid.innerHTML = '';
  Object.entries(THEMES).forEach(([id, t]) => {
    const c = el('div', 'theme-card' + (id === PREF.theme ? ' on' : ''));
    c.dataset.theme = id;
    const sw = el('div', 'tc-swatches');
    t.swatch.forEach(col => { const s = el('span', 'tc-sw'); s.style.background = col; sw.appendChild(s); });
    c.appendChild(sw);
    c.appendChild(el('div', 'tc-name', t.name));
    c.onclick = () => {
      applyTheme(id);
      // 终端底色变量 --term-bg 由 applyTheme 处理；即时预览无需保存按钮
    };
    grid.appendChild(c);
  });
  openModal('modalPrefs');
}
$('btnPrefs').onclick = openPrefs;
$('btnSavePrefs').onclick = () => {
  PREF.kaOn = $('prefKaOn').checked;
  const raw = $('prefKaChar').value;
  PREF.kaChar = raw === '' ? ' ' : raw;
  PREF.kaSec = Math.max(15, Math.min(3600, parseInt($('prefKaSec').value, 10) || 180));
  $('prefKaSec').value = PREF.kaSec;
  PREF.kaClean = $('prefKaClean').checked;
  savePrefs();
  applyKeepalive();
  closeModal('modalPrefs');
  setRunStatus('设置已保存：' + (PREF.kaOn ? ('保活开（每 ' + PREF.kaSec + 's）') : '保活关') + ' · 主题「' + (THEMES[PREF.theme] || THEMES.steel).name + '」。');
};

/* ---------------- 通用 ---------------- */
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (r.status === 401) { location.replace('/login'); throw new Error('登录已失效'); }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}
const jsonPost = (url, body) => api(url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/** POST + 读取 SSE 流，逐条回调事件 */
async function streamPost(url, body, onEvent, signal) {
  const resp = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  });
  if (resp.status === 401) { location.replace('/login'); throw new Error('登录已失效'); }
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data:')) {
          try { onEvent(JSON.parse(line.slice(5).trim())); } catch (e) { /* 忽略非 JSON 心跳 */ }
        }
      }
    }
  }
}

function setBadge(text, on) {
  $('connBadge').textContent = text;
  $('connBadge').className = 'badge ' + (on ? 'on' : 'off');
}
function setRunStatus(t) { $('runStatus').textContent = t; }

/** 等待期间让进度条「呼吸」，明确告诉用户仍在工作中，而不是卡死 */
function markWaiting(text) {
  const box = $('progressBox');
  if (!box.querySelector('.pg-row')) {
    updateProgress({ step: 0, total: 1, text: text || '处理中…', state: 'thinking' });
  }
  const t = box.querySelector('.pg-title');
  if (t && text) t.textContent = text;
  box.querySelectorAll('.pg-dot,.pg-fill').forEach(x => x.classList.add('waiting'));
  if (text) setRunStatus(text);
}
function clearWaiting() {
  $('progressBox').querySelectorAll('.pg-dot,.pg-fill')
    .forEach(x => x.classList.remove('waiting'));
}

/* ---------------- 加载与渲染 ---------------- */
async function loadState(keepSelect = true) {
  const st = await api('/api/state');
  S.hosts = st.hosts || [];
  S.profiles = st.ai_profiles || [];
  S.server = st.server || { port: 8848 };

  const tabs = $('termTabs'); tabs.innerHTML = '';
  // 只有真正建立了 SSH 会话的主机才出现在终端标签栏
  Object.keys(S.sessions).forEach(id => {
    const s = S.sessions[id];
    const h = S.hosts.find(x => x.id === id);
    if (!h || !s || !s.alive) return;
    const t = el('div', 'tab' + (id === S.activeHost ? ' active' : ''), h.name);
    t.title = h.username + '@' + h.host + ':' + h.port;
    t.onclick = () => { S.activeHost = id; showSession(id); loadState(); };
    tabs.appendChild(t);
  });

  const list = $('hostList'); list.innerHTML = '';
  if (!S.hosts.length) {
    const d = el('div', 'quick-item', '暂无主机，点「+ 添加」连接 Linux');
    d.onclick = () => openModal('modalHost');
    list.appendChild(d);
  }
  S.hosts.forEach(h => {
    const sess = S.sessions[h.id];
    const live = !!(sess && sess.alive);
    const item = el('div', 'host-item' + (h.id === S.activeHost ? ' active' : '') +
      (live ? ' live' : ''));
    item.appendChild(el('span', 'dot'));
    const meta = el('div', 'host-meta');
    meta.appendChild(el('div', 'n', h.name));
    meta.appendChild(el('div', 's', h.username + '@' + h.host + ':' + h.port));
    item.appendChild(meta);
    // 连接/断开完全由用户点击决定，登录或选中主机都不会自动建立 SSH 会话
    const conn = el('button', 'host-connect' + (live ? ' connected' : ''),
      live ? '断开' : '连接');
    conn.title = live ? '断开这台主机的 SSH 会话' : '连接这台主机的 SSH 终端';
    conn.onclick = async (e) => {
      e.stopPropagation();
      if (live) await disconnectHost(h.id);
      else {
        await selectHost(h.id);
        await openTerminal(h.id);
        await loadState();
      }
    };
    item.appendChild(conn);
    // 修改：打开与新增同一套弹窗，预填该主机信息，保存时带 id 走更新
    const edit = el('button', 'host-edit', '修改');
    edit.title = '修改这台主机的连接信息';
    edit.onclick = (e) => { e.stopPropagation(); editHost(h); };
    item.appendChild(edit);
    const del = el('button', 'host-del', '×');
    del.title = '删除主机';
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm('确定删除主机「' + h.name + '」？')) return;
      destroySession(h.id);
      await api('/api/hosts/' + h.id, { method: 'DELETE' });
      if (S.activeHost === h.id) S.activeHost = '';
      await loadState();
      if (!S.hosts.length) showEmptyTerm('主机已全部移除。点击「+ 添加」重新添加。');
      else await selectHost(S.hosts[0].id);
    };
    item.appendChild(del);
    item.onclick = () => selectHost(h.id);
    list.appendChild(item);
  });

  const selHost = $('selHost');
  const prevHost = selHost.value;
  selHost.innerHTML = '';
  S.hosts.forEach(h => {
    const o = el('option', '', h.name + ' (' + h.username + ')');
    o.value = h.id; selHost.appendChild(o);
  });
  if (keepSelect && S.hosts.some(h => h.id === prevHost)) selHost.value = prevHost;
  else if (S.activeHost) selHost.value = S.activeHost;

  const selP = $('selProfile');
  const prevP = selP.value;
  selP.innerHTML = '';
  S.profiles.forEach(p => {
    const o = el('option', '', p.name + ' · ' + p.api_key_masked);
    o.value = p.id; selP.appendChild(o);
  });
  if (keepSelect && S.profiles.some(p => p.id === prevP)) selP.value = prevP;
  renderModels();
  const p = currentProfile();
  $('quotaVal').textContent = (p && p.quota) ? p.quota : '—';

  const ql = $('quickTasks'); ql.innerHTML = '';
  QUICK.forEach(q => {
    const d = el('div', 'quick-item', q.t);
    d.onclick = () => {
      openAiDrawer();
      setMode(q.m);
      $('promptInput').value = q.p;
      setTimeout(() => $('promptInput').focus(), 60);
    };
    ql.appendChild(d);
  });

  return st;
}

let EDITING_HOST_ID = '';   // 为空表示新增；非空表示正在编辑该主机

function currentProfile() {
  const id = $('selProfile').value;
  return S.profiles.find(p => p.id === id) || S.profiles[0] || null;
}
function renderModels() {
  const p = currentProfile();
  const sel = $('selModel');
  const prev = sel.value;
  sel.innerHTML = '';
  const models = (p && p.models && p.models.length) ? p.models : ['deepseek-flash', 'deepseek-v4-pro'];
  models.forEach(m => { const o = el('option', '', m); o.value = m; sel.appendChild(o); });
  if (models.includes(prev)) sel.value = prev;
  else if (p && p.default_model && models.includes(p.default_model)) sel.value = p.default_model;
}

/* ---------------- 真·交互式终端（xterm.js） ---------------- */
function showEmptyTerm(msg) {
  const host = $('termHost');
  // 不能清空整个 termHost：已建立会话的 xterm DOM 要保留，只隐藏
  host.querySelectorAll('.term-inst').forEach(x => { x.style.display = 'none'; });
  let d = host.querySelector('.term-empty');
  if (!d) { d = el('div', 'term-empty'); host.appendChild(d); }
  d.innerHTML = '';
  d.style.display = '';
  d.appendChild(el('div', 'te-ico', '⌘'));
  d.appendChild(el('div', 'te-title', '还没有连接任何主机'));
  d.appendChild(el('div', 'te-text', msg || '在左侧主机列表点「连接」才会建立 SSH 会话；登录不会自动连接。'));
  const b = el('button', 'btn primary', '+ 添加 Linux 主机');
  b.onclick = () => openModal('modalHost');
  d.appendChild(b);
}

function createSession(hostId) {
  if (!TermCtor) { showEmptyTerm('终端组件未加载，请检查 frontend/vendor/xterm.js 是否存在。'); return null; }
  const empty = $('termHost').querySelector('.term-empty');
  if (empty) empty.style.display = 'none';

  const wrap = el('div', 'term-inst');
  $('termHost').appendChild(wrap);

  const term = new TermCtor({
    fontFamily: '"Cascadia Mono","Consolas","JetBrains Mono","Courier New",monospace',
    fontSize: 15, lineHeight: 1.35, letterSpacing: 0,
    cursorBlink: true, cursorStyle: 'bar', cursorWidth: 2,
    scrollback: 6000, rightClickSelectsWord: true,
    theme: TERM_THEME,
  });
  // 选中即复制：双击选词 / 框选文本后自动写入剪贴板（localhost 视为安全上下文可调用）
  term.onSelectionChange(() => {
    const sel = term.getSelection();
    if (sel && navigator.clipboard) navigator.clipboard.writeText(sel).catch(() => {});
  });
  const fit = FitCtor ? new FitCtor() : null;
  if (fit) term.loadAddon(fit);
  term.open(wrap);
  try { if (fit) fit.fit(); } catch (e) { /* 尺寸为 0 时忽略 */ }

  const sess = { hostId, wrap, term, fit, sid: '', es: null, alive: false,
                 buf: [], timer: null, notices: [] };

  // xterm.js 把 Tab / 方向键 / Ctrl+C / 回车等原样交给 onData —— 这就是 Tab 补全与 vim 的关键
  term.onData((data) => {
    if (!sess.sid) return;
    sess.buf.push(data);
    if (sess.timer) return;
    sess.timer = setTimeout(() => {
      sess.timer = null;
      const payload = sess.buf.join(''); sess.buf = [];
      if (payload) jsonPost('/api/terminal/input', { sid: sess.sid, data: payload }).catch(() => {});
    }, 10);
  });

  term.onResize(({ cols, rows }) => {
    if (sess.sid) jsonPost('/api/terminal/resize', { sid: sess.sid, cols, rows }).catch(() => {});
  });

  // 粘贴：把剪贴板文本按原始字符流送进 PTY
  wrap.addEventListener('paste', (e) => {
    const text = (e.clipboardData || window.clipboardData).getData('text');
    if (text && sess.sid) {
      e.preventDefault();
      jsonPost('/api/terminal/input', { sid: sess.sid, data: text }).catch(() => {});
    }
  });

  S.sessions[hostId] = sess;
  return sess;
}

function showSession(hostId) {
  const emptyBox = $('termHost').querySelector('.term-empty');
  if (emptyBox) emptyBox.style.display = 'none';
  Object.keys(S.sessions).forEach(id => {
    S.sessions[id].wrap.style.display = (id === hostId) ? 'block' : 'none';
  });
  const s = S.sessions[hostId];
  if (s) {
    requestAnimationFrame(() => {
      try { if (s.fit) s.fit.fit(); } catch (e) { /* ignore */ }
      s.term.focus();
    });
  }
  const h = S.hosts.find(x => x.id === hostId);
  if (h) setBadge(s && s.alive ? '已连接 · ' + h.name : '未连接 · ' + h.name, !!(s && s.alive));
}

function destroySession(hostId) {
  const s = S.sessions[hostId];
  if (!s) return;
  if (s.es) s.es.close();
  if (s.sid) jsonPost('/api/terminal/close', { sid: s.sid }).catch(() => {});
  if (s.timer) clearTimeout(s.timer);
  try { s.term.dispose(); } catch (e) { /* ignore */ }
  if (s.wrap && s.wrap.parentNode) s.wrap.parentNode.removeChild(s.wrap);
  delete S.sessions[hostId];
}

function closeSession(hostId) {
  const s = S.sessions[hostId];
  if (!s) return;
  if (s.es) { s.es.close(); s.es = null; }
  if (s.sid) jsonPost('/api/terminal/close', { sid: s.sid }).catch(() => {});
  s.sid = ''; s.alive = false;
}

async function selectHost(hostId) {
  // 只切换「目标主机」，绝不自动建立 SSH 连接
  S.activeHost = hostId;
  await loadState();
  $('selHost').value = hostId;
  const s = S.sessions[hostId];
  if (s && s.alive) { showSession(hostId); return; }
  const h = S.hosts.find(x => x.id === hostId);
  showEmptyTerm('已选中「' + (h ? h.name : hostId) + '」。点击左侧该主机的「连接」按钮才会建立 SSH 会话。');
  setBadge('未连接', false);
}

async function disconnectHost(hostId) {
  destroySession(hostId);
  await loadState();
  if (S.activeHost === hostId) {
    showEmptyTerm('已断开。需要时点击左侧该主机的「连接」按钮重新连接。');
    setBadge('未连接', false);
  } else {
    const s = S.sessions[S.activeHost];
    if (s && s.alive) showSession(S.activeHost);
  }
}

/** 收尾时清掉进度条，避免残留「AI 正在思考（已等待 Ns）…」 */
function hideRunMeta() {
  clearWaiting();
  $('progressBox').innerHTML = '';
  if (!$('reasoningBox').innerHTML.trim()) $('runMeta').classList.add('hidden');
}

/* 本地写进 xterm 的提示也要进审计：SSH 输出由后端 reader 线程 1:1 落盘，
   这些「正在连接 / 连接失败 / 输出流中断」是前端自己打印的，需单独上报。 */
function plainTermText(raw) {
  return String(raw || '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?/g, '')
    .replace(/\x1b[@-Z\\-_]/g, '')
    .replace(/\x1b/g, '')
    .replace(/[\r\n]+/g, ' ')
    .trim();
}

function uiWrite(s, text) {
  s.term.write(text);
  const plain = plainTermText(text);
  if (!plain) return;
  const payload = { sid: s.sid || '', host_id: s.hostId || '', text: plain };
  // 连接前还没有 sid，先缓存；拿到 sid 或确认连接失败后再补报，不丢内容
  if (!s.sid) { (s.notices || (s.notices = [])).push(payload); return; }
  jsonPost('/api/terminal/notice', payload).catch(() => {});
}

function flushNotices(s, forceLoose) {
  const list = s.notices || [];
  s.notices = [];
  list.forEach(p => jsonPost('/api/terminal/notice',
    Object.assign({}, p, { sid: forceLoose ? '' : (s.sid || '') })).catch(() => {}));
}

async function openTerminal(hostId) {
  const h = S.hosts.find(x => x.id === hostId);
  if (!h) return;
  let s = S.sessions[hostId];
  if (s && s.alive) { showSession(hostId); return; }
  if (s) closeSession(hostId); else s = createSession(hostId);
  if (!s) return;
  showSession(hostId);
  uiWrite(s, '\x1b[38;5;110m正在连接 ' + h.username + '@' + h.host + ':' + h.port + ' ...\x1b[0m\r\n');
  setBadge('连接中…', false);

  try {
    const cols = s.term.cols || 120, rows = s.term.rows || 30;
    const r = await jsonPost('/api/terminal/open', { host_id: hostId, cols, rows });
    if (!r.ok) throw new Error(r.message || '连接失败');
    s.sid = r.sid; s.alive = true;
    flushNotices(s, false);                  // 连接前的提示补进刚建立的会话窗口
    s.term.write('\x1b[2K\r');
    s.term.focus();
    setBadge('已连接 · ' + h.name, true);
    const es = new EventSource('/api/terminal/stream?sid=' + encodeURIComponent(r.sid));
    s.es = es;
    es.onmessage = (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      // SSH 原始流由后端 reader 线程直接写审计，前端不重复上报
      if (d.type === 'out') s.term.write(d.text);
      else if (d.type === 'error') uiWrite(s, '\r\n\x1b[31m[错误] ' + d.text + '\x1b[0m\r\n');
      else if (d.type === 'closed') {
        uiWrite(s, '\r\n\x1b[90m[会话已结束]\x1b[0m\r\n');
        s.alive = false; setBadge('已断开 · ' + h.name, false);
      }
    };
    es.onerror = () => {
      if (!s.alive) return;
      uiWrite(s, '\r\n\x1b[33m[终端输出流中断，点右上角 ⟳ 可重连]\x1b[0m\r\n');
      setBadge('连接中断', false); es.close(); s.alive = false;
      // 会话失效时后端会返回 401，由 api() 统一跳回登录页
      api('/api/auth/me').catch(() => {});
    };
    if (s.fit) { try { s.fit.fit(); } catch (e) { /* ignore */ } }
  } catch (e) {
    uiWrite(s, '\r\n\x1b[31m连接失败：' + e.message + '\x1b[0m\r\n');
    uiWrite(s, '\x1b[90m请检查地址、端口、用户名与密码，或点右上角 ⟳ 重试。\x1b[0m\r\n');
    flushNotices(s, true);                 // 没建立会话，归入当天独立操作文件
    setBadge('连接失败', false);
  }
}

$('btnClearTerm').onclick = () => {
  const s = S.sessions[S.activeHost];
  if (s) { s.term.clear(); s.term.focus(); }
};
$('btnReconnect').onclick = () => {
  if (!S.activeHost) return;
  if (!S.sessions[S.activeHost]) { setRunStatus('请先点击左侧该主机的「连接」按钮。'); return; }
  openTerminal(S.activeHost);
};
$('btnFullTerm').onclick = () => {
  $('termPanel').classList.toggle('maximized');
  setTimeout(() => {
    const s = S.sessions[S.activeHost];
    if (s && s.fit) { try { s.fit.fit(); s.term.focus(); } catch (e) { /* ignore */ } }
  }, 60);
};
window.addEventListener('resize', () => {
  const s = S.sessions[S.activeHost];
  if (s && s.fit) { try { s.fit.fit(); } catch (e) { /* ignore */ } }
});
if (window.ResizeObserver) {
  new ResizeObserver(() => {
    const s = S.sessions[S.activeHost];
    if (s && s.fit && s.wrap.style.display !== 'none') {
      try { s.fit.fit(); } catch (e) { /* ignore */ }
    }
  }).observe($('termHost'));
}

/* ---------------- 模式切换 ---------------- */
function setMode(m) {
  S.mode = m;
  document.querySelectorAll('.mode-tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === m));
  $('btnRun').textContent = m === 'ask' ? '▶ 开始提问' : '▶ 开始诊断';
  $('promptInput').placeholder = m === 'ask'
    ? '直接问 Linux 命令用法，例如：vim 的 -R / +N / -d 参数分别是什么意思？'
    : '写清要求，例如：/ 分区使用率 100%，先定位是哪个目录占用，再给出清理方案并处理（保留最近 7 天日志）';
}
document.querySelectorAll('.mode-tabs button').forEach(b => b.onclick = () => setMode(b.dataset.mode));

/* ---------------- 右侧 AI 助手抽屉：默认隐藏，点图标展开 ---------------- */
function fitTermSoon(delay) {
  setTimeout(() => {
    window.dispatchEvent(new Event('resize'));
    const s = S.sessions[S.activeHost];
    if (s && s.fit && s.alive) { try { s.fit.fit(); } catch (e) { /* ignore */ } }
  }, delay);
}
function openAiDrawer() {
  document.body.classList.add('ai-open');
  fitTermSoon(320);
  setTimeout(() => $('promptInput').focus(), 340);
}
function closeAiDrawer() {
  document.body.classList.remove('ai-open');
  fitTermSoon(320);
}
$('btnAiToggle').onclick = openAiDrawer;
$('btnAiClose').onclick = closeAiDrawer;
// Esc 收起抽屉（弹窗打开时不响应，避免和弹窗的关闭冲突）
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.body.classList.contains('ai-open') &&
      !document.querySelector('.modal:not(.hidden)')) closeAiDrawer();
});

/* ---------------- 左侧主机列表：折叠 / 展开 ---------------- */
(function () {
  const KEY = 'aoc_side_collapsed';
  function apply(collapsed) {
    document.body.classList.toggle('side-collapsed', collapsed);
    $('btnSideToggle').textContent = collapsed ? '▶' : '◀';
    $('btnSideToggle').title = collapsed ? '展开主机列表' : '收起主机列表';
    fitTermSoon(260);
  }
  $('btnSideToggle').onclick = () => {
    const c = !document.body.classList.contains('side-collapsed');
    try { localStorage.setItem(KEY, c ? '1' : '0'); } catch (e) { /* ignore */ }
    apply(c);
  };
  let saved = '';
  try { saved = localStorage.getItem(KEY) || ''; } catch (e) { /* ignore */ }
  if (saved === '1') apply(true);
})();

/* ---------------- AI 抽屉左缘拖拽条：左右挤压调节宽度 ---------------- */
(function () {
  const sp = $('aiResizer');
  const KEY = 'aoc_ai_width';
  if (!sp) return;
  const MIN_W = 380;
  function maxW() { return Math.max(MIN_W, window.innerWidth - 260); }
  function setW(px) {
    document.documentElement.style.setProperty('--ai-w', Math.round(px) + 'px');
  }
  let saved = '';
  try { saved = localStorage.getItem(KEY) || ''; } catch (e) { /* ignore */ }
  if (saved) setW(parseFloat(saved));

  let dragging = false, startX = 0, startW = 0;
  function xOf(e) { return (e.touches && e.touches[0]) ? e.touches[0].clientX : e.clientX; }
  function down(e) {
    dragging = true;
    startX = xOf(e);
    startW = $('aiDrawer').getBoundingClientRect().width;
    sp.classList.add('dragging');
    document.body.classList.add('ai-resizing');
    if (e.cancelable) e.preventDefault();
  }
  function move(e) {
    if (!dragging) return;
    // 抽屉贴右缘：鼠标左移 = 变宽
    const w = Math.max(MIN_W, Math.min(maxW(), startW + (startX - xOf(e))));
    setW(w);
    if (e.cancelable) e.preventDefault();
  }
  function up() {
    if (!dragging) return;
    dragging = false;
    sp.classList.remove('dragging');
    document.body.classList.remove('ai-resizing');
    try { localStorage.setItem(KEY, $('aiDrawer').getBoundingClientRect().width + 'px'); } catch (e) { /* ignore */ }
    // 浮层覆盖模式：终端宽度不变，无需重新 fit
  }
  sp.addEventListener('mousedown', down);
  sp.addEventListener('touchstart', down, { passive: false });
  window.addEventListener('mousemove', move);
  window.addEventListener('touchmove', move, { passive: false });
  window.addEventListener('mouseup', up);
  window.addEventListener('touchend', up);
  // 双击拖拽条：还原默认宽度
  sp.addEventListener('dblclick', () => {
    document.documentElement.style.removeProperty('--ai-w');
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
  });
})();

/* ---------------- 进度条 / 思考过程 / 提示卡片 ---------------- */
function resetRunMeta() {
  $('runMeta').classList.remove('hidden');
  $('progressBox').innerHTML = '';
  $('reasoningBox').classList.add('hidden');
  $('reasoningBox').innerHTML = '';
  delete $('reasoningBox').dataset.init;
  S.reasoningCount = 0;
  S.reasoningChars = 0;
  S.answerText = ''; S.answerDirty = false;
  if (S.answerTimer) { clearTimeout(S.answerTimer); S.answerTimer = null; }
  clearWaiting();
}

function updateProgress(e) {
  const box = $('progressBox');
  box.innerHTML = '';
  const state = e.state || 'thinking';
  const step = e.step || 0;
  // 不再向用户展示「最大重试」步数上限：进度条按已执行步数平滑推进（约 12 步到 80%）
  const pct = (state === 'done' || state === 'finished') ? 100
    : (state === 'summing' ? 96 : Math.min(92, Math.round(step / 12 * 80)));

  const row = el('div', 'pg-row');
  row.appendChild(el('span', 'pg-dot ' + state));
  row.appendChild(el('span', 'pg-title', e.text || '准备中'));
  row.appendChild(el('span', 'pg-count', step ? ('已执行 ' + step + ' 步') : ''));
  box.appendChild(row);
  const bar = el('div', 'pg-bar');
  const fill = el('div', 'pg-fill ' + state);
  fill.style.width = pct + '%';
  bar.appendChild(fill);
  box.appendChild(bar);
}

function splitReasoning(text) {
  // 把密密麻麻的思维链整理成短句段落，默认折叠，展开后可读
  const t = String(text || '').replace(/\r/g, '');
  const parts = t.split(/(?<=[。！？.!?])\s+|\n+/).map(x => x.trim()).filter(Boolean);
  const out = [];
  let cur = '';
  parts.forEach(p => {
    if ((cur + ' ' + p).length > 160) { if (cur) out.push(cur); cur = p; }
    else cur = cur ? (cur + ' ' + p) : p;
  });
  if (cur) out.push(cur);
  return out.length ? out : [t.slice(0, 4000)];
}

function addReasoning(e) {
  const box = $('reasoningBox');
  box.classList.remove('hidden');
  if (!box.dataset.init) {
    const head = el('div', 'rs-head');
    head.appendChild(el('span', 'rs-ico', '◈'));
    head.appendChild(el('span', 'rs-title', 'AI 思考过程'));
    const cnt = el('span', 'rs-count', '0 段'); cnt.id = 'rsCount';
    head.appendChild(cnt);
    const toggle = el('button', 'rs-toggle', '展开 ▾');
    head.appendChild(toggle);
    const body = el('div', 'rs-body hidden'); body.id = 'rsBody';
    toggle.onclick = () => {
      const hidden = body.classList.toggle('hidden');
      toggle.textContent = hidden ? '展开 ▾' : '收起 ▴';
      head.classList.toggle('open', !hidden);
    };
    box.appendChild(head); box.appendChild(body);
    box.dataset.init = '1';
  }
  S.reasoningCount++;
  S.reasoningChars += (e.chars || (e.text || '').length);
  $('rsCount').textContent = S.reasoningCount + ' 段 · 约 ' + S.reasoningChars + ' 字';
  const body = $('rsBody');
  const sec = el('div', 'rs-sec');
  sec.appendChild(el('div', 'rs-sec-h', '第 ' + (e.step || S.reasoningCount) + ' 步 · 推理记录'));
  splitReasoning(e.text).forEach(p => sec.appendChild(el('p', 'rs-p', p)));
  body.appendChild(sec);
}

function addNotice(level, title, text, detail) {
  const box = el('div', 'notice ' + (level || 'info'));
  const head = el('div', 'nt-head');
  head.appendChild(el('span', 'nt-ico', level === 'error' ? '!' : level === 'warn' ? '!' : 'i'));
  head.appendChild(el('span', 'nt-title', title || '提示'));
  head.appendChild(el('span', 'nt-level',
    level === 'error' ? '错误' : level === 'warn' ? '注意' : '说明'));
  box.appendChild(head);
  const body = el('div', 'nt-body');
  String(text || '').split(/\n+/).filter(Boolean).forEach(line =>
    body.appendChild(el('p', 'nt-p', line.trim())));
  box.appendChild(body);
  if (detail) {
    const d = el('div', 'nt-detail');
    const btn = el('button', 'nt-detail-btn', '查看技术细节 ▾');
    const pre = el('pre', 'nt-pre hidden', String(detail).slice(0, 4000));
    btn.onclick = () => {
      const hidden = pre.classList.toggle('hidden');
      btn.textContent = hidden ? '查看技术细节 ▾' : '收起技术细节 ▴';
    };
    d.appendChild(btn); d.appendChild(pre); box.appendChild(d);
  }
  $('steps').appendChild(box);
  $('steps').scrollTop = $('steps').scrollHeight;
  return box;
}

/* ---------------- 结论：结构化、可读渲染 ---------------- */
// 富文本渲染委托给可独立单测的 render.js（清理 Markdown 残留 / 围栏 / 空标题 / 半句）
function inlineCode(node, text) {
  if (Render) return Render.inlineNodes(node, text);
  node.textContent = String(text || '').replace(/\*/g, '');
}
function renderRichText(container, text, opts) {
  if (Render) return Render.renderRichText(container, text, opts);
  container.textContent = String(text || '');
  return { lines: [], truncated: false };
}

function showSummary(text, title, incremental, tone, finalState) {
  const box = $('summary');
  const opts = { final: finalState === undefined ? !incremental : !!finalState };
  if (incremental && box.dataset.init === '1') {
    const body = box.querySelector('.summary-body');
    if (body) {
      box.className = 'summary' + (tone === 'warn' ? ' warn' : '');
      renderRichText(body, text, opts);
      return;
    }
  }
  box.innerHTML = '';
  box.className = 'summary' + (tone === 'warn' ? ' warn' : '');
  const head = el('div', 'sm-head');
  head.appendChild(el('span', 'sm-ico', tone === 'warn' ? '!' : '✓'));
  head.appendChild(el('span', 'sm-title', title || 'AI 结论与建议'));
  box.appendChild(head);
  const body = el('div', 'summary-body');
  renderRichText(body, text, opts);
  box.appendChild(body);
  box.dataset.init = '1';
  box.classList.remove('hidden');
  // 流式增量渲染时不反复抢滚动焦点
  if (!incremental) box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/** 流式答案节流渲染，避免每个 token 都全量重绘导致闪烁 */
function scheduleAnswerRender() {
  S.answerDirty = true;
  if (S.answerTimer) return;
  S.answerTimer = setTimeout(() => {
    S.answerTimer = null;
    if (!S.answerDirty) return;
    S.answerDirty = false;
    showSummary(S.answerText, '命令 / 概念讲解', true, undefined, false);
  }, 140);
}
function flushAnswer(final) {
  if (S.answerTimer) { clearTimeout(S.answerTimer); S.answerTimer = null; }
  S.answerDirty = false;
  if (S.answerText) showSummary(S.answerText, '命令 / 概念讲解', true, undefined, !!final);
}

/* ---------------- 步骤流 ---------------- */
function clearSteps() {
  hideConfirmDock();
  $('steps').innerHTML = '';
  $('summary').classList.add('hidden');
  $('summary').innerHTML = '';
  delete $('summary').dataset.init;
  S.steps = {};
  resetRunMeta();
  setRunStatus('还没有执行步骤。');
}
function makeStep(n, evt) {
  const card = el('div', 'step ' + (evt.risk || 'read'));
  const head = el('div', 'step-head');
  head.appendChild(el('span', 'step-no', String(n)));
  head.appendChild(el('span', 'step-cmd', '$ ' + evt.command));
  const cp = el('button', 'step-copy', '复制');
  cp.title = '复制完整命令';
  cp.onclick = () => {
    const txt = evt.command || '';
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(() => { cp.textContent = '已复制'; setTimeout(() => cp.textContent = '复制', 1200); });
    }
  };
  head.appendChild(cp);
  const tag = el('span', 'step-tag ' + evt.risk,
    evt.risk === 'danger' ? '危险' : evt.risk === 'write' ? '写操作' : '只读');
  head.appendChild(tag);
  head.appendChild(el('span', 'step-time', '待执行'));
  card.appendChild(head);
  if (evt.thought) card.appendChild(el('div', 'step-thought', '💭 ' + evt.thought));
  if (evt.reasons && evt.reasons.length && evt.risk !== 'read')
    card.appendChild(el('div', 'step-thought warn', '⚠ ' + evt.reasons.join('；')));
  $('steps').appendChild(card);
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  S.steps[n] = card;
  return card;
}
function updateStep(n, d) {
  const card = S.steps[n];
  if (!card) return;
  const time = card.querySelector('.step-time');
  const map = { running: '执行中…', blocked: '已拦截', skipped: '已跳过', error: '执行失败', pending: '待确认' };
  if (d.status === 'done') time.textContent = (d.elapsed !== undefined ? d.elapsed + 's' : '完成');
  else time.textContent = map[d.status] || d.status;
  card.querySelectorAll('.step-out,.step-foot,.confirm-bar,.out-toggle,.step-wait').forEach(x => x.remove());
  if (d.output !== undefined) {
    const pre = el('pre', 'step-out', d.output || '（无输出）');
    card.appendChild(pre);
    const lines = String(d.output || '').split('\n').length;
    if (lines > 24) {
      pre.classList.add('collapsed');
      const label = '展开全部输出（共 ' + lines + ' 行）▾';
      const tog = el('button', 'out-toggle', label);
      tog.onclick = () => {
        const collapsed = pre.classList.toggle('collapsed');
        tog.textContent = collapsed ? label : '收起输出 ▴';
      };
      card.appendChild(tog);
    }
  }
  if (d.exit_code !== undefined && d.exit_code !== null)
    card.appendChild(el('div', 'step-foot', '退出码: ' + d.exit_code +
      (d.exit_code === 0 ? '  ·  成功' : '  ·  非 0，请留意')));
}
/* ---------- 危险命令审批：固定在视窗内，绝不因滚动而消失 ---------- */
function hideConfirmDock() {
  if (S.pendingConfirm && S.pendingConfirm.timer) clearInterval(S.pendingConfirm.timer);
  S.pendingConfirm = null;
  const dock = $('confirmDock');
  if (dock) dock.classList.add('hidden');
  document.body.classList.remove('confirming');
  clearWaiting();
}
function showConfirmDock(e) {
  hideConfirmDock();
  const timeout = e.timeout || 120;
  const item = { confirm_id: e.confirm_id, step: e.step,
                 remaining: timeout, timer: null, answered: false };
  S.pendingConfirm = item;
  $('cdStep').textContent = '第 ' + e.step + ' 步 · ' +
    (e.risk === 'danger' ? '危险命令' : '写操作');
  $('cdCmd').textContent = '$ ' + (e.command || '');
  // 这条命令是干什么用的（来自 AI 本步的 thought），帮助用户判断是否执行
  const purpose = (e.purpose || '').trim();
  const pEl = $('cdPurpose');
  if (pEl) {
    pEl.textContent = '命令作用：' + (purpose || '该命令将在目标服务器上执行，请确认符合你的预期后再批准。');
    pEl.style.display = '';
  }
  $('cdReasons').textContent = (e.reasons && e.reasons.length)
    ? '⚠ ' + e.reasons.join('；') : '该命令会改变系统状态，请确认后再执行。';
  $('cdCountdown').textContent = timeout + 's';
  $('confirmDock').classList.remove('hidden');
  document.body.classList.add('confirming');
  markWaiting('⏸ 等待你确认危险命令（请在下方确认条操作）');

  const card = S.steps[e.step];
  if (card) {
    const old = card.querySelector('.step-wait');
    if (old) old.remove();
    card.appendChild(el('div', 'step-wait', '⏸ 已暂停，请在页面下方的确认条中批准或拒绝'));
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  item.timer = setInterval(() => {
    if (S.pendingConfirm !== item) return;
    item.remaining = Math.max(0, item.remaining - 1);
    $('cdCountdown').textContent = item.remaining + 's';
  }, 1000);
}
async function answerConfirm(approved) {
  const item = S.pendingConfirm;
  if (!item || item.answered) return;
  item.answered = true;
  const id = item.confirm_id, step = item.step;
  hideConfirmDock();
  try {
    const r = await jsonPost('/api/agent/confirm', { confirm_id: id, approved });
    if (!r.ok) addNotice('warn', '确认未生效', '该审批已超时或任务已结束，命令不会被执行。');
  } catch (err) {
    addNotice('error', '确认失败', '与本地服务的连接中断，命令不会被执行。', err.message);
  }
  const card = S.steps[step];
  if (card) { const w = card.querySelector('.step-wait'); if (w) w.remove(); }
  updateStep(step, approved
    ? { status: 'running' }
    : { status: 'skipped', output: '用户拒绝执行该命令' });
  setRunStatus(approved ? '已批准，正在执行命令…' : '已拒绝该命令，AI 将继续分析。');
}
$('cdApprove').onclick = () => answerConfirm(true);
$('cdReject').onclick = () => answerConfirm(false);

function onAgentEvent(e) {
  switch (e.type) {
    case 'start':
      S.runId = e.run_id || '';
      if (e.mode === 'ask' || S.mode === 'ask') {
        S.maxRetry = 1;
        showSummary('正在生成讲解，请稍候…', '命令 / 概念讲解', false, 'warn');
        updateProgress({ step: 0, total: 1, text: 'AI 正在思考…', state: 'thinking' });
        markWaiting('AI 正在思考…');
      } else {
        S.maxRetry = e.max_retry || 8;
        setRunStatus('运行中 · 目标 ' + e.host + ' · 模型 ' + e.model);
        updateProgress({ step: 0, total: S.maxRetry, text: '诊断已启动', state: 'thinking' });
      }
      break;
    case 'progress': updateProgress(e); setRunStatus(e.text); break;
    case 'heartbeat':
      if (e.wait === 'confirm' && S.pendingConfirm && e.remaining !== undefined) {
        S.pendingConfirm.remaining = e.remaining;
        $('cdCountdown').textContent = e.remaining + 's';
      }
      markWaiting(e.text || '仍在工作…');
      break;
    case 'reasoning': addReasoning(e); break;
    case 'notice': addNotice(e.level, e.title, e.text, e.detail); break;
    case 'error': addNotice('error', '执行中断', e.text || '发生未知错误'); break;
    case 'info': addNotice('info', '提示', e.text); break;
    case 'thought': break;   // 已并入步骤卡片
    case 'step': makeStep(e.step, e); break;
    case 'step_update':
      if (S.pendingConfirm && S.pendingConfirm.step === e.step) hideConfirmDock();
      updateStep(e.step, e);
      break;
    case 'confirm_required':
      updateStep(e.step, { status: 'pending' });
      showConfirmDock(e);
      break;
    case 'confirm_timeout':
      hideConfirmDock();
      addNotice('warn', '确认超时', e.text || '超时未确认，已自动拒绝该命令。');
      break;
    case 'answer_start':
      S.answerText = '';
      $('summary').classList.add('hidden');
      delete $('summary').dataset.init;
      break;
    case 'answer_delta':
      S.answerText += e.text;
      scheduleAnswerRender();
      break;
    case 'answer_done':
      flushAnswer(true);
      hideRunMeta();
      setRunStatus('回答完成');
      break;
    case 'summary': showSummary(e.text, e.title, false, e.tone); break;
    case 'done': hideConfirmDock(); break;
  }
}

/* ---------------- 运行 ---------------- */
$('btnRun').onclick = async () => {
  if (S.running) {
    addNotice('info', '任务进行中', '上一轮任务还在运行，可点「停止」或「清空 / 重置」后再开始。');
    return;
  }
  const prompt = $('promptInput').value.trim();
  if (!prompt) { alert('请先写清要求'); return; }
  const body = {
    prompt, host_id: $('selHost').value,
    profile_id: $('selProfile').value, model: $('selModel').value,
    read_only: $('chkReadOnly').checked, auto_approve: $('chkAuto').checked,
    max_retry: 60,
  };
  if (S.mode === 'diagnose' && !body.host_id) { alert('请先在左侧添加并选中一台 Linux 主机'); return; }
  clearSteps();
  S.maxRetry = body.max_retry;
  S.running = true;
  S.lastEventAt = Date.now();
  S.watchdogNotified = false;
  $('btnRun').disabled = true; $('btnStop').disabled = false;

  const url = S.mode === 'ask' ? '/api/agent/ask' : '/api/agent/run';
  const ctrl = new AbortController(); S.controller = ctrl;

  // 点击后立刻给可见反馈，杜绝「点了没反应」
  if (S.mode === 'ask') {
    showSummary('正在生成讲解，请稍候…', '命令 / 概念讲解', false, 'warn');
    updateProgress({ step: 0, total: 1, text: 'AI 正在思考…', state: 'thinking' });
    markWaiting('AI 正在思考…');
  } else {
    updateProgress({ step: 0, total: S.maxRetry, text: '正在启动诊断…', state: 'thinking' });
    markWaiting('正在启动诊断…');
  }

  try {
    await streamPost(url, body, (e) => {
      S.lastEventAt = Date.now();
      onAgentEvent(e);
    }, ctrl.signal);
  } catch (err) {
    if (err.name !== 'AbortError' && err.message !== '登录已失效') {
      addNotice('error', '请求失败', '与本地服务的连接中断，请确认服务仍在运行。', err.message);
    }
  } finally {
    hideConfirmDock();
    if (S.mode === 'ask') { flushAnswer(true); hideRunMeta(); }
    S.running = false;
    $('btnRun').disabled = false; $('btnStop').disabled = true;
    clearWaiting();
    if (S.mode === 'ask') setRunStatus('回答完成。');
    else if (!$('runStatus').textContent.includes('等待')) setRunStatus('执行结束。');
  }
};

$('btnStop').onclick = async () => {
  if (S.runId) await jsonPost('/api/agent/stop', { run_id: S.runId }).catch(() => {});
  if (S.controller) S.controller.abort();
  hideConfirmDock();
  S.running = false;
  $('btnRun').disabled = false; $('btnStop').disabled = true;
  setRunStatus('已停止。');
  clearWaiting();
};

$('btnClear').onclick = async () => {
  if (S.running && !confirm('当前任务仍在运行，是否停止并重置？')) return;
  if (S.running) {
    if (S.runId) await jsonPost('/api/agent/stop', { run_id: S.runId }).catch(() => {});
    if (S.controller) S.controller.abort();
    S.running = false;
    $('btnRun').disabled = false; $('btnStop').disabled = true;
  }
  hideConfirmDock();
  clearSteps();
  setRunStatus('已重置，可以重新开始。');
};

/* 看门狗：即便 SSE 彻底静默，也保证按钮不会永久禁用 */
setInterval(() => {
  if (!S.running) return;
  const silent = Date.now() - S.lastEventAt;
  if (silent > 90000) {
    if (S.controller) S.controller.abort();
    hideConfirmDock();
    S.running = false;
    $('btnRun').disabled = false; $('btnStop').disabled = true;
    addNotice('warn', '连接已超时',
      '超过 90 秒没有收到任何新进展，本轮已自动结束，按钮已恢复，可重新点击开始。');
    setRunStatus('已自动恢复，可重新开始。');
  } else if (silent > 30000 && !S.watchdogNotified) {
    S.watchdogNotified = true;
    setRunStatus('暂无新进展，仍在等待…（可点「停止」终止）');
  }
}, 5000);

/* ---------------- 模型 / 配置 ---------------- */
$('btnRefreshModels').onclick = async () => {
  const id = $('selProfile').value;
  if (!id) { alert('请先在右上角「API修改」里配置 AI 接入'); return; }
  setRunStatus('正在拉取模型列表…');
  try {
    const r = await api('/api/ai/models?id=' + encodeURIComponent(id) + '&refresh=1');
    if (r.ok) { await loadState(); setRunStatus('模型列表已更新：' + r.models.join(', ')); }
    else setRunStatus('拉取失败：' + (r.message || ''));
  } catch (e) { setRunStatus('拉取失败：' + e.message); }
};
$('selProfile').onchange = () => { renderModels(); };
$('btnRefreshQuota').onclick = async () => { await loadState(); setRunStatus('已刷新'); };

/* ---------------- 弹窗 ---------------- */
function openModal(id) { $(id).classList.remove('hidden'); }
function closeModal(id) { $(id).classList.add('hidden'); }
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => closeModal(b.dataset.close));
document.querySelectorAll('.modal').forEach(m => m.onclick = (e) => {
  if (e.target === m) m.classList.add('hidden');
});
$('btnAddHost').onclick = () => {
  EDITING_HOST_ID = '';
  $('modalHost').querySelector('.modal-head b').textContent = '添加 Linux 主机';
  $('hName').value = ''; $('hHost').value = ''; $('hPort').value = '22';
  $('hUser').value = 'root'; $('hPass').value = ''; $('hNote').value = '';
  $('hTestMsg').textContent = ''; $('hTestMsg').className = 'test-msg';
  openModal('modalHost');
};
function editHost(h) {
  EDITING_HOST_ID = h.id;
  $('modalHost').querySelector('.modal-head b').textContent = '修改 Linux 主机';
  $('hName').value = h.name || '';
  $('hHost').value = h.host || '';
  $('hPort').value = h.port || 22;
  $('hUser').value = h.username || 'root';
  $('hPass').value = '';                                   // 安全起见留空，不回显密码
  $('hPass').placeholder = '留空表示不修改原密码';
  $('hNote').value = h.note || '';
  $('hTestMsg').textContent = ''; $('hTestMsg').className = 'test-msg';
  openModal('modalHost');
}
function hostPayload() {
  return {
    id: EDITING_HOST_ID || undefined,
    name: $('hName').value.trim() || $('hHost').value.trim(),
    host: $('hHost').value.trim(), port: parseInt($('hPort').value || '22', 10),
    username: $('hUser').value.trim(), password: $('hPass').value,
    note: $('hNote').value.trim(),
  };
}
$('btnTestHost').onclick = async () => {
  const msg = $('hTestMsg');
  msg.textContent = '正在测试连接…'; msg.className = 'test-msg';
  try {
    const r = await jsonPost('/api/hosts/test', hostPayload());
    msg.textContent = (r.ok ? '✅ ' : '❌ ') + r.message + (r.banner ? '  |  ' + r.banner : '');
    msg.className = 'test-msg ' + (r.ok ? 'ok' : 'bad');
  } catch (e) { msg.textContent = '❌ ' + e.message; msg.className = 'test-msg bad'; }
};
$('btnSaveHost').onclick = async () => {
  const p = hostPayload();
  if (!p.host || !p.username || !p.password) { alert('地址、用户名、密码必填'); return; }
  const r = await jsonPost('/api/hosts', p);
  if (r.ok) {
    closeModal('modalHost');
    EDITING_HOST_ID = '';
    $('modalHost').querySelector('.modal-head b').textContent = '添加 Linux 主机';
    $('hPass').placeholder = 'SSH 登录密码';
    S.activeHost = r.id;
    await loadState();
    await selectHost(r.id);
    setRunStatus('主机已保存。');
  }
};

function fillProfileForm(p) {
  $('sId').value = p ? p.id : '';
  $('sName').value = p ? p.name : 'DeepSeek 官方';
  $('sUrl').value = p ? p.base_url : 'https://api.deepseek.com';
  $('sKey').value = '';
  $('sKey').placeholder = p && p.has_key ? '已配置 ' + p.api_key_masked + '（留空不修改）' : 'sk-...';
  $('sModels').value = p && p.models && p.models.length ? p.models.join(', ') : 'deepseek-flash, deepseek-v4-pro';
  $('sModel').value = p ? (p.default_model || 'deepseek-flash') : 'deepseek-flash';
  $('sEffort').value = p ? (p.effort || 'low') : 'low';
  $('sQuota').value = p ? (p.quota || '') : '';
  $('sPort').value = (S.server && S.server.port) || 8848;
  $('sTestMsg').textContent = ''; $('sTestMsg').className = 'test-msg';
}
function renderProfileSel() {
  const sel = $('sProfileSel');
  sel.innerHTML = '';
  S.profiles.forEach(p => {
    const o = el('option', '', p.name + '（' + p.base_url.replace(/^https?:\/\//, '') + '）');
    o.value = p.id; sel.appendChild(o);
  });
  const cur = currentProfile();
  if (cur) sel.value = cur.id;
  $('btnDelProfile').disabled = S.profiles.length <= 1;
}
$('sProfileSel').onchange = () => {
  const p = S.profiles.find(x => x.id === $('sProfileSel').value);
  fillProfileForm(p);
  $('btnDelProfile').disabled = S.profiles.length <= 1;
};
$('btnSettings').onclick = () => {
  renderProfileSel();
  fillProfileForm(currentProfile());
  openModal('modalSettings');
};
$('btnNewProfile').onclick = () => {
  $('sId').value = '';
  $('sName').value = '新接入（如 DeepSeek / 智谱 / 通义）';
  $('sUrl').value = '';
  $('sKey').value = ''; $('sKey').placeholder = 'sk-...';
  $('sModels').value = '';
  $('sModel').value = '';
  $('sQuota').value = '';
  $('sTestMsg').textContent = '填好名称 / API 地址 / Key 后点「保存」，即成为可切换的新接入。';
  $('sTestMsg').className = 'test-msg';
  $('sName').focus();
};
$('btnDelProfile').onclick = async () => {
  const id = $('sId').value || (currentProfile() || {}).id;
  if (!id) return;
  if (S.profiles.length <= 1) { alert('至少保留一套 AI 接入。'); return; }
  const p = S.profiles.find(x => x.id === id);
  if (!confirm('确定删除接入「' + (p ? p.name : id) + '」？其 API Key 会一并删除。')) return;
  const r = await api('/api/settings/ai/' + encodeURIComponent(id), { method: 'DELETE' });
  if (r.ok) {
    await loadState();
    renderProfileSel();
    fillProfileForm(currentProfile());
    $('sTestMsg').textContent = '✅ 已删除该接入。';
    $('sTestMsg').className = 'test-msg ok';
  } else {
    alert('删除失败：' + (r.message || '未知错误'));
  }
};
function settingsPayload() {
  return {
    id: $('sId').value.trim() || undefined,
    name: $('sName').value.trim() || 'DeepSeek',
    base_url: $('sUrl').value.trim().replace(/\/+$/, ''),
    api_key: $('sKey').value.trim(),
    models: $('sModels').value.split(',').map(s => s.trim()).filter(Boolean),
    default_model: $('sModel').value.trim(),
    effort: $('sEffort').value,
    quota: $('sQuota').value.trim(),
  };
}
$('btnTestAI').onclick = async () => {
  const msg = $('sTestMsg');
  msg.textContent = '正在测试 API…'; msg.className = 'test-msg';
  try {
    const p = settingsPayload();
    const r = await jsonPost('/api/ai/test', {
      id: p.id, model: p.default_model,
      base_url: p.base_url, api_key: p.api_key || undefined,
    });
    msg.textContent = (r.ok ? '✅ ' : '❌ ') + r.message;
    msg.className = 'test-msg ' + (r.ok ? 'ok' : 'bad');
  } catch (e) { msg.textContent = '❌ ' + e.message; msg.className = 'test-msg bad'; }
};
$('btnPullModels').onclick = async () => {
  const msg = $('sTestMsg');
  msg.textContent = '正在拉取…'; msg.className = 'test-msg';
  try {
    // 新增未保存的接入：直接用表单里的地址/Key 拉取，不必先保存
    const q = [];
    if ($('sId').value.trim()) q.push('id=' + encodeURIComponent($('sId').value.trim()));
    if ($('sUrl').value.trim()) q.push('base_url=' + encodeURIComponent($('sUrl').value.trim()));
    if ($('sKey').value.trim()) q.push('api_key=' + encodeURIComponent($('sKey').value.trim()));
    const r = await api('/api/ai/models?' + q.join('&') + '&refresh=1');
    if (r.ok) {
      $('sModels').value = r.models.join(', ');
      if (!$('sModel').value.trim() && r.models.length) $('sModel').value = r.models[0];
      msg.textContent = '✅ 已拉取 ' + r.models.length + ' 个模型：' + r.models.slice(0, 8).join(', ') + (r.models.length > 8 ? ' …' : '');
      msg.className = 'test-msg ok';
    } else { msg.textContent = '❌ ' + (r.message || '失败'); msg.className = 'test-msg bad'; }
  } catch (e) { msg.textContent = '❌ ' + (e.message || '失败'); msg.className = 'test-msg bad'; }
};
$('btnSaveSettings').onclick = async () => {
  const p = settingsPayload();
  if (!p.base_url) { alert('API 地址必填'); return; }
  await jsonPost('/api/settings/ai', p);
  const port = parseInt($('sPort').value || '8848', 10);
  if (port !== 8848) alert('端口已记录，需重启服务后生效。');
  closeModal('modalSettings');
  await loadState();
  renderProfileSel();
  setRunStatus('AI 接入配置已保存。');
};

/* ---------------- SFTP 文件下载 / 上传 ---------------- */
const FM = { mode: 'download', path: '/', home: '/', entries: [], filter: '',
             abort: null, jobId: '', xhr: null, busy: false };

function fmtSize(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
  return (n / 1073741824).toFixed(2) + ' GB';
}
function fmJoin(dir, name) {
  const d = String(dir || '/').replace(/\/+$/, '');
  return (d + '/' + name) || '/';
}
function fmHost() {
  const h = S.hosts.find(x => x.id === S.activeHost);
  return h;
}
function fmSetMsg(text, cls) {
  const m = $('fileMsg');
  m.textContent = text || '';
  m.className = 'file-msg' + (cls ? ' ' + cls : '');
}
function fmProgress(show, text, pct, cancelable) {
  const box = $('fileProgress');
  box.classList.toggle('hidden', !show);
  if (text !== undefined) $('fpText').textContent = text;
  if (pct !== undefined) {
    $('fpPct').textContent = pct + '%';
    $('fpFill').style.width = Math.min(100, pct) + '%';
  }
  $('fpCancel').style.display = cancelable === false ? 'none' : '';
}

async function fmList(path) {
  if (FM.busy) return;
  const h = fmHost();
  if (!h) { fmSetMsg('请先连接一台主机', 'bad'); return; }
  fmSetMsg('加载中…');
  try {
    const r = await api('/api/sftp/list?host_id=' + encodeURIComponent(h.id) +
                        '&path=' + encodeURIComponent(path || '/'));
    if (!r.ok) { fmSetMsg('无法打开：' + (r.message || '未知错误'), 'bad'); return; }
    FM.path = r.path; FM.home = r.home || '/'; FM.entries = r.entries || [];
    $('filePath').value = FM.path;
    fmSetMsg('');
    fmRender();
  } catch (e) {
    fmSetMsg('加载失败：' + e.message, 'bad');
  }
}

function fmRender() {
  const box = $('fileList');
  box.innerHTML = '';
  const kw = FM.filter.toLowerCase();
  const items = FM.entries.filter(x => !kw || x.name.toLowerCase().includes(kw));
  if (FM.path !== '/') {
    const up = el('div', 'file-item dir');
    up.appendChild(el('span', 'fi-ico', '↩'));
    up.appendChild(el('span', 'fi-name', '..'));
    up.appendChild(el('span', 'fi-size', '上级目录'));
    up.onclick = () => fmList(FM.path.includes('/') ? (FM.path.replace(/\/[^/]*$/, '') || '/') : '/');
    box.appendChild(up);
  }
  if (!items.length) {
    box.appendChild(el('div', 'file-empty', kw ? '没有匹配「' + FM.filter + '」的文件' : '（空目录）'));
    return;
  }
  items.forEach(x => {
    const d = el('div', 'file-item ' + (x.type === 'dir' ? 'dir' : x.type === 'link' ? 'link' : 'file'));
    d.appendChild(el('span', 'fi-ico', x.type === 'dir' ? '📁' : x.type === 'link' ? '🔗' : '📄'));
    const n = el('span', 'fi-name', x.name);
    d.appendChild(n);
    if (x.type === 'dir') d.appendChild(el('span', 'fi-size', '目录'));
    else d.appendChild(el('span', 'fi-size', fmtSize(x.size)));
    d.appendChild(el('span', 'fi-time', x.mtime ? new Date(x.mtime * 1000).toLocaleString('zh-CN', { hour12: false }).slice(0, 16) : ''));
    if (x.type === 'dir') {
      d.ondblclick = d.onclick = () => { FM.filter = ''; $('fileSearch').value = ''; fmList(fmJoin(FM.path, x.name)); };
    } else {
      d.onclick = () => {
        document.querySelectorAll('.file-item.sel').forEach(s => s.classList.remove('sel'));
        d.classList.add('sel');
        if (FM.mode === 'download') fmDownload(fmJoin(FM.path, x.name), x.name, x.size);
        else fmSetMsg('已选中「' + x.name + '」作为上传目标目录的参考；上传请点右下角按钮选择本机文件', 'ok');
      };
    }
    box.appendChild(d);
  });
}

async function fmDownload(remotePath, name, size) {
  if (FM.busy) return;
  const h = fmHost();
  FM.busy = true;
  fmProgress(true, '开始下载 ' + name + '…', 0);
  const ctrl = new AbortController(); FM.abort = ctrl;
  try {
    const resp = await fetch('/api/sftp/download?host_id=' + encodeURIComponent(h.id) +
                             '&path=' + encodeURIComponent(remotePath), { signal: ctrl.signal });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const total = parseInt(resp.headers.get('X-File-Size') || String(size || 0), 10);
    const fname = decodeURIComponent(resp.headers.get('X-File-Name') || name);
    const reader = resp.body.getReader();
    const chunks = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      got += value.length;
      fmProgress(true, '正在下载 ' + fname + '（' + fmtSize(got) + (total ? ' / ' + fmtSize(total) : '') + '）',
                 total ? Math.round(got / total * 100) : 0);
    }
    const blob = new Blob(chunks);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    fmProgress(true, '下载完成：' + fname + '（' + fmtSize(got) + '）', 100, false);
    setTimeout(() => fmProgress(false), 2500);
    fmSetMsg('下载完成，已保存到本机「下载」文件夹', 'ok');
  } catch (e) {
    if (e.name === 'AbortError') { fmSetMsg('已取消下载', 'warn'); fmProgress(false); }
    else { fmSetMsg('下载失败：' + e.message, 'bad'); fmProgress(false); }
  } finally {
    FM.busy = false; FM.abort = null;
  }
}

function fmCancelTransfer() {
  if (FM.abort) { try { FM.abort.abort(); } catch (e) {} }
  if (FM.xhr) {
    const x = FM.xhr; FM.xhr = null;
    try { x.abort(); } catch (e) {}
    if (FM.jobId) jsonPost('/api/sftp/cancel', { job: FM.jobId }).catch(() => {});
    fmProgress(false);
    fmSetMsg('已取消上传，服务器上不会保留半成品文件', 'warn');
    FM.busy = false;
  }
}

function fmUpload(file) {
  if (FM.busy) { fmSetMsg('已有传输在进行，请先取消或等待完成', 'warn'); return; }
  const h = fmHost();
  FM.busy = true;
  fmProgress(true, '准备上传 ' + file.name + '…', 0);
  const x = new XMLHttpRequest();
  FM.xhr = x;
  x.open('POST', '/api/sftp/upload?host_id=' + encodeURIComponent(h.id) +
        '&path=' + encodeURIComponent(FM.path) +
        '&name=' + encodeURIComponent(file.name));
  x.upload.onprogress = (ev) => {
    if (ev.lengthComputable) {
      fmProgress(true, '正在上传 ' + file.name + '（' + fmtSize(ev.loaded) + ' / ' + fmtSize(ev.total) + '）',
                 Math.round(ev.loaded / ev.total * 100));
    }
  };
  x.onload = () => {
    FM.xhr = null; FM.busy = false;
    let r = {};
    try { r = JSON.parse(x.responseText || '{}'); } catch (e) {}
    if (r.ok) {
      fmProgress(true, '上传成功：' + r.path, 100, false);
      fmSetMsg('已上传到 ' + r.path, 'ok');
      setTimeout(() => { fmProgress(false); fmList(FM.path); }, 2000);
    } else if (r.cancelled) {
      fmProgress(false);
      fmSetMsg('已取消上传', 'warn');
    } else {
      fmProgress(false);
      fmSetMsg('上传失败：' + (r.message || 'HTTP ' + x.status), 'bad');
    }
  };
  x.onerror = () => {
    FM.xhr = null; FM.busy = false; fmProgress(false);
    fmSetMsg('上传失败：网络中断', 'bad');
  };
  x.send(file);
}

function openFileManager(mode) {
  const h = fmHost();
  if (!h) { alert('请先在左侧主机列表点「连接」，再使用文件传输。'); return; }
  FM.mode = mode;
  FM.filter = ''; $('fileSearch').value = '';
  $('fileModalTitle').textContent = mode === 'download' ? '文件下载 · 点击文件即下载' : '文件上传 · 进入目标目录后选择本机文件';
  $('fileModalHost').textContent = h.name + '（' + h.host + '）';
  $('btnPickUpload').classList.toggle('hidden', mode !== 'upload');
  $('fileHint').textContent = mode === 'download'
    ? '点击文件即可下载 · 双击目录进入 · 路径栏粘贴后可回车跳转'
    : '先双击进入要上传到的目录，再点右下角选择本机文件';
  fmProgress(false);
  fmSetMsg('');
  openModal('modalFiles');
  fmList(FM.home || '/');
}
$('btnFileDownload').onclick = () => openFileManager('download');
$('btnFileUpload').onclick = () => openFileManager('upload');
$('fileUp').onclick = () => fmList(FM.path.replace(/\/[^/]*$/, '') || '/');
$('fileHome').onclick = () => fmList(FM.home || '/');
$('fileRefresh').onclick = () => fmList(FM.path);
$('fileSearch').oninput = (e) => { FM.filter = e.target.value.trim(); fmRender(); };
$('filePath').onkeydown = (e) => {
  if (e.key === 'Enter') { e.preventDefault(); fmList($('filePath').value.trim() || '/'); }
};
$('fpCancel').onclick = fmCancelTransfer;
$('btnPickUpload').onclick = () => $('filePick').click();
$('filePick').onchange = (e) => {
  const f = e.target.files && e.target.files[0];
  if (f) fmUpload(f);
  e.target.value = '';
};
// 关闭下载弹窗时中断未完成的下载
(function () {
  const m = $('modalFiles');
  const closeAll = () => {
    if (FM.abort) { try { FM.abort.abort(); } catch (e) {} }
    if (FM.xhr) { try { FM.xhr.abort(); } catch (e) {} if (FM.jobId) jsonPost('/api/sftp/cancel', { job: FM.jobId }).catch(() => {}); }
    FM.busy = false; FM.abort = null; FM.xhr = null;
    fmProgress(false);
  };
  m.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', closeAll));
})();

/* ---------------- 命令审计：按「连接 → 断开」生成的会话窗口 txt ---------------- */
const AU = { sessions: [], selected: '', dir: '', meta: null };

function auditShowStats(st) {
  $('auditPath').textContent = AU.dir || st.path || '';
  const box = $('auditStats');
  box.innerHTML = '';
  const add = (t) => box.appendChild(el('span', 'st', t));
  add('会话窗口 ' + (st.sessions || 0) + ' 个');
  add('终端实录 ' + (st.terminal_lines || 0) + ' 行');
  add('结构化事件 ' + (st.total || 0) + ' 条');
  add('会话文件 ' + (st.files || 0) + ' 个 · ' + (st.size_human || '0 KB'));
  if (st.latest) add('最近记录 ' + st.latest);
}

function auditRenderSessions() {
  const list = $('auditSessionList');
  list.innerHTML = '';
  $('auditSessionCount').textContent = AU.sessions.length;
  if (!AU.sessions.length) {
    const d = el('div', 'audit-empty');
    d.textContent = '还没有会话记录。连接一台主机即生成「用户名_时间.txt」，终端上打印的内容会实时 1:1 写入，断开时自动封存。';
    list.appendChild(d);
    return;
  }
  AU.sessions.forEach(s => {
    const item = el('button', 'audit-session-item' + (s.file === AU.selected ? ' on' : ''));
    item.type = 'button';
    item.title = s.file;
    item.appendChild(el('div', 'si-file', s.file));

    const l1 = el('div', 'si-line');
    l1.appendChild(el('span', '', s.user || '-'));
    l1.appendChild(el('span', '', '·'));
    l1.appendChild(el('span', '', s.host || '-'));
    l1.appendChild(el('span', 'si-tag', (s.terminal_lines || 0) + ' 行实录 · ' +
                                          (s.commands || 0) + ' 条事件'));
    item.appendChild(l1);

    const l2 = el('div', 'si-line');
    const endShort = (s.end && s.end !== '-') ? (' → ' + String(s.end).slice(11)) : '';
    l2.appendChild(el('span', '', (s.start || '-') + endShort));
    const live = !s.end || s.end === '-' || String(s.status || '').includes('进行中');
    const bad = String(s.status || '').includes('异常');
    l2.appendChild(el('span', 'si-tag ' + (live ? 'live' : 'done'),
                      bad ? '异常终止' : (live ? '进行中' : '已结束')));
    item.appendChild(l2);

    item.onclick = () => auditSelect(s.file);
    list.appendChild(item);
  });
}

async function auditSelect(file) {
  if (!file) return;
  AU.selected = file;
  auditRenderSessions();
  $('auditDetailTitle').textContent = '会话窗口 · ' + file;
  $('auditText').textContent = '正在读取会话文件…';
  try {
    const r = await api('/api/audit/session?file=' + encodeURIComponent(file));
    if (!r.ok) { $('auditText').textContent = r.message || '读取失败'; return; }
    const m = r.meta || {};
    AU.meta = m;
    $('auditMeta').textContent = [
      '用户 ' + (m.user || '-'), '来源 IP ' + (m.ip || '-'), '主机 ' + (m.host || '-'),
      '目标 ' + (m.target || '-'), '连接 ' + (m.start || '-'),
      '断开 ' + ((m.end && m.end !== '-') ? m.end : '进行中'),
      '时长 ' + (m.duration || '-'), '实录 ' + (m.terminal_lines || 0) + ' 行',
      '事件 ' + (m.commands || 0) + ' 条', '状态 ' + (m.status || '-'),
    ].join('　·　');
    $('auditText').textContent = r.text || '（空文件）';
    $('btnAuditDownloadOne').disabled = false;
    $('btnAuditDownloadRaw').disabled = false;
  } catch (e) {
    $('auditText').textContent = '读取失败：' + e.message;
  }
}

async function auditQuery() {
  const q = new URLSearchParams({
    keyword: $('auditKeyword').value.trim(),
    ev_type: $('auditType').value,
    limit: $('auditLimit').value,
  });
  const tb = $('auditRows');
  try {
    const r = await api('/api/audit/query?' + q.toString());
    tb.innerHTML = '';
    const rows = r.ok ? (r.entries || []) : [];
    if (!rows.length) {
      const tr = el('tr');
      const td = el('td', '', r.ok ? '没有匹配的审计记录。' : ('查询失败：' + (r.message || '')));
      td.colSpan = 6; td.style.textAlign = 'center'; td.style.color = 'var(--muted)';
      tr.appendChild(td); tb.appendChild(tr);
      return;
    }
    rows.forEach(e => {
      const tr = el('tr');
      [e.time, e.user, e.host, e.type].forEach((v, i) => {
        const td = el('td', i === 3 ? 'ty' : '', v || '-');
        tr.appendChild(td);
      });
      const cmdTd = el('td', 'cmd', e.cmd || '');
      tr.appendChild(cmdTd);
      tr.appendChild(el('td', 'fn', e.file || '-'));
      tr.title = '点击查看该会话窗口的完整文本';
      tr.onclick = () => { if (e.file) auditSelect(e.file); };
      tb.appendChild(tr);
    });
  } catch (e) {
    tb.innerHTML = '';
    const tr = el('tr'), td = el('td', '', '查询失败：' + e.message);
    td.colSpan = 6; tr.appendChild(td); tb.appendChild(tr);
  }
}

async function auditLoad() {
  try {
    const r = await api('/api/audit/sessions');
    if (!r.ok) { $('auditStats').textContent = '读取失败：' + (r.message || ''); return; }
    AU.dir = r.dir || (r.stats && r.stats.path) || '';
    AU.sessions = r.sessions || [];
    auditShowStats(r.stats || {});
    auditRenderSessions();

    let f = AU.selected;
    if (!f || !AU.sessions.some(s => s.file === f)) {
      f = AU.sessions.length ? AU.sessions[0].file : '';
    }
    if (f) {
      await auditSelect(f);
    } else {
      AU.selected = ''; AU.meta = null;
      $('auditDetailTitle').textContent = '完整会话记录';
      $('auditMeta').textContent = '请从左侧选择一个会话窗口';
      $('auditText').textContent = '每个会话窗口对应一个独立的 .txt 文件，点左侧即可查看「终端上打印过的全部内容」1:1 原文。';
      $('btnAuditDownloadOne').disabled = true;
      $('btnAuditDownloadRaw').disabled = true;
    }
    await auditQuery();
  } catch (e) {
    $('auditStats').textContent = '读取失败：' + e.message;
  }
}

$('btnAudit').onclick = () => { openModal('modalAudit'); auditLoad(); };
$('btnAuditRefresh').onclick = auditQuery;
$('auditKeyword').onkeydown = (e) => { if (e.key === 'Enter') auditQuery(); };
$('auditType').onchange = auditQuery;
$('auditLimit').onchange = auditQuery;
$('btnAuditCopy').onclick = async () => {
  const p = AU.dir || $('auditPath').textContent;
  try {
    await navigator.clipboard.writeText(p);
    setRunStatus('已复制会话文档目录：' + p);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = p; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); setRunStatus('已复制会话文档目录：' + p); }
    catch (e2) { alert('复制失败，请手动选择路径文本复制：\n' + p); }
    document.body.removeChild(ta);
  }
};
$('btnAuditReveal').onclick = async () => {
  try {
    const r = await jsonPost('/api/audit/reveal', {});
    if (!r.ok) alert('打开目录失败：' + (r.message || ''));
  } catch (e) { alert('打开目录失败：' + e.message); }
};
$('btnAuditDownloadOne').onclick = () => {
  if (!AU.selected) { alert('请先在左侧选择一个会话窗口'); return; }
  location.href = '/api/audit/download?file=' + encodeURIComponent(AU.selected);
};
$('btnAuditDownloadRaw').onclick = () => {
  if (!AU.selected) { alert('请先在左侧选择一个会话窗口'); return; }
  location.href = '/api/audit/raw?file=' + encodeURIComponent(AU.selected);
};
$('btnAuditExport').onclick = () => { location.href = '/api/audit/download'; };
$('btnAuditAsk').onclick = () => {
  const user = ($('userTag').textContent || '当前用户').trim() || '当前用户';
  let q;
  if (AU.selected && AU.meta) {
    q = '审计：请分析会话文件 ' + AU.selected + '，按时间顺序列出用户 ' +
        (AU.meta.user || user) + ' 在这次连接窗口里执行了哪些命令，' +
        '写明连接/断开时间、涉及主机与来源 IP，并给出风险与建议。';
  } else {
    q = '审计：' + user + ' 今天执行过哪些命令？请按「时间 · 用户 · 主机 · 操作」逐条列出，' +
        '说明分别属于哪个会话窗口文件，并给出风险与建议。';
  }
  closeModal('modalAudit');
  setMode('ask');
  openAiDrawer();
  $('promptInput').value = q;
  setTimeout(() => {
    if (!S.running && !$('btnRun').disabled) $('btnRun').click();
    else if (S.running) setRunStatus('已有任务运行中，问题已填入输入框，稍后点「开始提问」即可。');
  }, 420);
};

/* ---------------- 登录态 ---------------- */
$('btnLogout').onclick = async () => {
  if (!confirm('确定退出登录？')) return;
  await jsonPost('/api/auth/logout', {}).catch(() => {});
  location.replace('/login');
};

/* ---------------- 启动 ---------------- */
(async function init() {
  loadPrefs();
  applyTheme(PREF.theme);
  applyKeepalive();
  try {
    const me = await api('/api/auth/me');
    if (me && me.username) $('userTag').textContent = me.username;
    await api('/api/health');
    await loadState(false);
    if (S.hosts.length) {
      S.activeHost = S.hosts[0].id;
      $('selHost').value = S.activeHost;
      await loadState();
      showEmptyTerm('已选中「' + S.hosts[0].name + '」。点击左侧该主机的「连接」按钮才会建立 SSH 会话。');
      setBadge('未连接', false);
    } else {
      showEmptyTerm();
      setBadge('未连接', false);
    }
  } catch (e) {
    showEmptyTerm('后端服务未就绪：' + e.message + '。请先运行 scripts/start.bat 或 scripts/start.sh。');
  }
})();
