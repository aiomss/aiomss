/* L6：浏览器 UI 端到端（Node v24 全局 WebSocket，零第三方依赖的 CDP 客户端）
 * 运行： node scripts/test_ui.mjs
 *
 * 覆盖：登录后不自动连接（Network 层硬证据）、主机列表「连接」按钮、
 *       顶栏「API修改」、终端 hint 删除、未连接徽标、空状态文案、
 *       window.AiOpsRender 加载、设置弹窗标题、富文本脏样本清洗、两张截图。
 */
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const PY = 'C:\\Users\\18202\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe';
const HOST = 'http://127.0.0.1:8899';
const CDP = 'http://127.0.0.1:9333';
const ASSETS = path.join(ROOT, 'assets');

let PASS = 0, FAIL = 0, SKIP = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { PASS++; console.log('[OK]   ' + name); }
  else {
    FAIL++; failures.push(name);
    console.log('[FAIL] ' + name + (detail !== undefined ? '  -> ' + String(detail).slice(0, 220) : ''));
  }
}
function skipCase(name, reason) {
  SKIP++; console.log('[SKIP] ' + name + '  -> ' + reason);
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function findBrowser() {
  const c = [
    process.env.CHROME_BIN,
    process.env.EDGE_BIN,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean);
  return c.find(p => { try { return fs.existsSync(p); } catch { return false; } }) || null;
}

function killTree(child) {
  if (!child || !child.pid) return;
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', timeout: 10000 });
    } else { try { child.kill('SIGKILL'); } catch { /* ignore */ } }
  } catch { /* ignore */ }
}

async function waitUntil(fn, timeoutMs, label) {
  const t0 = Date.now();
  let last;
  while (Date.now() - t0 < timeoutMs) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(250);
  }
  throw new Error('timeout: ' + label + ' (' + String(last).slice(0, 160) + ')');
}

async function main() {
  const browser = findBrowser();
  if (!browser) {
    skipCase('L6 浏览器 UI 层', '未探测到 Chrome/Edge 可执行文件，无法启动 CDP；本层整层 SKIP（不记为 PASS）');
    console.log('\n结果: %d 通过 / %d 失败 / %d SKIP', PASS, FAIL, SKIP);
    console.log('UI_SKIP=1');
    return 0;
  }
  check('探测到可用浏览器', true, path.basename(browser));
  console.log('[INFO] browser_path=' + browser);

  fs.mkdirSync(ASSETS, { recursive: true });
  const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aoc-cdp-'));
  let ui = null, chrome = null, ws = null;

  try {
    // ---- 1. 启动同进程签发的测试服务（8899，绝不占用 8848） ----
    ui = spawn(PY, [path.join(ROOT, 'scripts', 'ui_server.py')],
      { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
    let uiOut = '', token = null;
    ui.stdout.on('data', d => {
      uiOut += d.toString('utf8');
      const m = uiOut.match(/AOC_TOKEN=([^\r\n]+)/);
      if (m) token = m[1];
    });
    let uiErr = '';
    ui.stderr.on('data', d => { uiErr += d.toString('utf8'); });

    await waitUntil(async () => {
      if (!token) return false;
      try {
        const r = await fetch(HOST + '/api/health', { signal: AbortSignal.timeout(2000) });
        return r.status === 200;
      } catch { return false; }
    }, 40000, 'ui_server 8899 就绪');
    check('ui_server 在 8899 就绪并签发会话（token 已捕获，不回显）', !!token);
    check('测试服务 /api/health 返回 200', true, HOST + '/api/health');

    // ---- 2. 启动 headless Chrome（CDP 9333） ----
    chrome = spawn(browser, [
      '--headless=new', '--disable-gpu', '--no-first-run',
      '--no-default-browser-check', '--no-proxy-server',
      '--remote-debugging-port=9333',
      '--user-data-dir=' + profileDir,
      '--window-size=1440,900', 'about:blank',
    ], { stdio: 'ignore' });

    let target = null;
    await waitUntil(async () => {
      try {
        const r = await fetch(CDP + '/json/list', { signal: AbortSignal.timeout(2000) });
        const list = await r.json();
        target = (list || []).find(x => x.type === 'page' && x.webSocketDebuggerUrl) || null;
        return !!target;
      } catch { return false; }
    }, 30000, 'Chrome CDP 9333 /json/list');
    check('Chrome CDP 端点可用并取到 page target', !!target);

    // ---- 3. 极简 CDP 客户端（全局 WebSocket） ----
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', reject, { once: true });
    });
    let nextId = 1;
    const pending = new Map();
    const events = [];
    ws.addEventListener('message', e => {
      let msg;
      try { msg = JSON.parse(String(e.data)); } catch { return; }
      if (msg.id && pending.has(msg.id)) {
        const p = pending.get(msg.id); pending.delete(msg.id);
        if (msg.error) p.reject(new Error(JSON.stringify(msg.error)));
        else p.resolve(msg.result);
      } else if (msg.method) events.push(msg);
    });
    const send = (method, params = {}, timeout = 20000) => new Promise((resolve, reject) => {
      const id = nextId++;
      const timer = setTimeout(() => {
        pending.delete(id); reject(new Error('CDP timeout: ' + method));
      }, timeout);
      pending.set(id, {
        resolve: v => { clearTimeout(timer); resolve(v); },
        reject: e => { clearTimeout(timer); reject(e); },
      });
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalValue = async (expression) => {
      const r = await send('Runtime.evaluate',
        { expression, returnByValue: true, awaitPromise: false });
      if (r.exceptionDetails) {
        const d = r.exceptionDetails;
        throw new Error(String(d.text || 'eval exception') + ' ' +
          String((d.exception && (d.exception.description || d.exception.value)) || ''));
      }
      return r.result && r.result.value;
    };
    const capture = async (file) => {
      const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, 30000);
      fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
      return fs.statSync(file).size;
    };
    // 截图只用于文档展示：把本机主机清单里的真实 IP 改成示例值，
    // 避免以后把 assets/*.png 同步到公开仓库时泄露生产地址。
    const maskHostAddresses = async () => {
      await evalValue(`document.querySelectorAll('#termHostSelect option').forEach(function(x){ x.textContent = 'example-host (root@example.com)'; });`);
    };

    await send('Network.enable');
    await send('Page.enable');
    await send('Runtime.enable');
    check('CDP Network/Page/Runtime 域已启用', true);

    // ---- 4. 先访问 /login 截登录页（此时未注入会话） ----
    await send('Page.navigate', { url: HOST + '/login' });
    await waitUntil(async () => (await evalValue('document.readyState')) === 'complete',
      20000, '/login DOM ready');
    const loginInfo = await evalValue(`(() => ({
      title: document.title,
      hasCaptcha: !!document.querySelector('.cap-img'),
      text: (document.body.innerText || '')
    }))()`);
    check('登录页可访问且含验证码图片控件',
      !!(loginInfo && (loginInfo.hasCaptcha || (loginInfo.text || '').includes('验证码'))),
      loginInfo && loginInfo.title);
    const loginPng = path.join(ASSETS, 'login-preview.png');
    const loginSize = await capture(loginPng);
    check('保存 assets/login-preview.png', loginSize > 1000, loginSize + ' bytes');

    // ---- 5. 注入会话并访问首页 ----
    await send('Network.setCookie', {
      name: 'aoc_session', value: token, url: HOST + '/', path: '/',
      httpOnly: false, secure: false,
    });
    await send('Page.navigate', { url: HOST + '/' });
    let domReady = null;
    await waitUntil(async () => {
      domReady = await evalValue(`(() => ({
        state: document.readyState,
        render: typeof window.AiOpsRender,
        hosts: document.querySelectorAll('#termHostSelect option').length,
        ext: typeof window.OpsExt,
        badge: (document.getElementById('connBadge') || {}).textContent,
        emptyReady: !!document.querySelector('#termHost .term-empty')
      }))()`);
      return domReady && domReady.state === 'complete'
        && domReady.render === 'object' && domReady.hosts > 0
        && domReady.ext === 'object' && domReady.emptyReady === true;
    }, 25000, '首页 DOM ready + 终端主机选择器渲染');
    check('首页加载完成且 window.AiOpsRender 已挂载', domReady.render === 'object', domReady);
    check('工作台扩展脚本已挂载（window.OpsExt）', domReady.ext === 'object', domReady);

    // ---- 6. DOM 断言（逐项对应 6 个截图问题） ----
    const dom = await evalValue(`(() => {
      const btn = document.getElementById('btnSettings');
      const items = Array.from(document.querySelectorAll('#termHostSelect option'));
      const badge = document.getElementById('connBadge');
      const empty = document.querySelector('#termHost .term-empty');
      const modalTitle = document.querySelector('#modalSettings .modal-head b');
      return {
        settingsText: btn ? btn.textContent.trim() : null,
        bodyText: document.body.innerText || '',
        htmlHead: document.documentElement.innerHTML.slice(0, 300000),
        hostCount: items.length,
        sidebarGone: !document.querySelector('.sidebar'),
        connectBtn: (document.getElementById('btnTermConnect') || {}).textContent,
        manageBtn: !!document.getElementById('btnTermManage'),
        badge: badge ? badge.textContent.trim() : null,
        emptyText: empty ? empty.textContent : '',
        modalTitle: modalTitle ? modalTitle.textContent.trim() : null,
        renderLoaded: typeof window.AiOpsRender === 'object'
          && typeof window.AiOpsRender.cleanText === 'function'
          && typeof window.AiOpsRender.renderRichText === 'function'
      };
    })()`);

    check('顶栏按钮文字为「API修改」（不含「设置」）',
      !!dom.settingsText && dom.settingsText.includes('API修改')
      && !dom.settingsText.includes('设置'), dom.settingsText);
    check('页面不含终端 hint「Tab 补全 · vim / top 可用 · Ctrl+C 中断」',
      !dom.bodyText.includes('Tab 补全 · vim / top 可用 · Ctrl+C 中断')
      && !dom.htmlHead.includes('Tab 补全 · vim / top 可用 · Ctrl+C 中断'));
    check('设置弹窗标题为「API修改 · AI 接入配置」',
      dom.modalTitle === 'API修改 · AI 接入配置', dom.modalTitle);
    check('终端主机选择器列出所有已纳管主机', dom.hostCount > 0, dom.hostCount);
    check('旧左侧主机列表竖条已移除', dom.sidebarGone === true, dom.sidebarGone);
    check('终端保留连接按钮与主机管理入口',
      dom.connectBtn === '连接' && dom.manageBtn === true,
      JSON.stringify({ connectBtn: dom.connectBtn, manageBtn: dom.manageBtn }));
    check('终端徽标显示「未连接」',
      !!dom.badge && dom.badge.includes('未连接') && !dom.badge.includes('已连接'), dom.badge);
    check('空状态文案指向终端上方连接按钮',
      dom.emptyText.includes('终端上方') || dom.emptyText.includes('登录不会自动连接'),
      dom.emptyText.slice(0, 160));
    check('window.AiOpsRender 清洗/渲染函数已加载', dom.renderLoaded === true);

    // ---- 6.4 本轮：主机下拉地域分组 + 搜索；AI 目标四类 ----
    const hostPicker = await evalValue(`(() => {
      const sel = document.getElementById('termHostSelect');
      const g = sel.querySelector('optgroup');
      return { groups: sel.querySelectorAll('optgroup').length,
               options: sel.options.length, search: !!document.getElementById('termHostSearch'),
               region: g ? g.label : '' };
    })()`);
    check('主机下拉按地域分组并带搜索框',
      hostPicker.groups >= 1 && hostPicker.options > 0 && hostPicker.search === true
      && hostPicker.region.length > 0, JSON.stringify(hostPicker));
    await evalValue(`(() => {
      const s = document.getElementById('termHostSearch');
      s.value = 'no-such-host-zzz'; s.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    })()`);
    const noMatch = await evalValue(`(() => {
      const sel = document.getElementById('termHostSelect');
      return { n: sel.options.length, text: (sel.options[0] || {}).textContent || '' };
    })()`);
    check('搜索无匹配时给出空提示而不是列出全部主机',
      noMatch.n <= 1 && noMatch.text.includes('没有匹配'), JSON.stringify(noMatch));
    await evalValue(`(() => {
      const s = document.getElementById('termHostSearch');
      s.value = ''; s.dispatchEvent(new Event('input', { bubbles: true })); return true;
    })()`);

    const aiTypes = await evalValue(`(() => ({
      types: Array.from(document.querySelectorAll('#aiTargetType option')).map(x => x.value),
      hosts: document.querySelectorAll('#selHost option').length
    }))()`);
    check('AI 目标类型只保留主机',
      aiTypes.types.join(',') === 'host' && aiTypes.hosts > 0, JSON.stringify(aiTypes));

    const aiShot = await evalValue(`(() => {
      const wasOpen = document.body.classList.contains('ai-open');
      document.body.classList.add('ai-open');
      document.getElementById('promptInput').value =
        '分析当前目标最近 1 小时的状态，按【现象/证据/风险/建议】给出结论。';
      document.querySelectorAll('#termHostSelect option, #selHost option').forEach(function(x){
        x.textContent = 'example-host (root@example.com)';
      });
      return wasOpen;
    })()`);
    await new Promise(r => setTimeout(r, 260));
    const aiPng = path.join(ASSETS, 'ai-targets-preview.png');
    check('保存 assets/ai-targets-preview.png（AI 多目标输入区）',
      await capture(aiPng) > 1000);
    await evalValue(`(function(wasOpen){
      if (!wasOpen) document.body.classList.remove('ai-open');
      document.getElementById('promptInput').value = '';
      return true;
    })(${aiShot ? 'true' : 'false'})`);
    await sleep(320);   // 等抽屉收起动画结束，避免后续布局断言读到过渡中的位置

    // ---- 6.5 本轮修改点：插件模块下线、顶栏全局刷新与大屏入口 ----
    const featureInfo = await evalValue(`(() => ({
      plugins: document.querySelectorAll('#wbPlugins .wb-plugin').length,
      globalRefresh: !!document.getElementById('btnGlobalRefresh'),
      screen: !!document.getElementById('btnScreen'),
      noPluginCard: !document.body.innerText.includes('可插拔分析插件')
    }))()`);
    check('「可插拔分析插件」模块已从首页去掉',
      featureInfo.plugins === 0 && featureInfo.noPluginCard === true, featureInfo);
    check('顶栏提供全局刷新与大屏入口',
      featureInfo.globalRefresh === true && featureInfo.screen === true, featureInfo);

    // ---- 6.5.1 AI 面板文案：开始执行 / 命令执行 ----
    const aiLabels = await evalValue(`(() => ({
      tab: document.querySelector('.mode-tabs [data-mode="diagnose"]').textContent.trim(),
      run: document.getElementById('btnRun').textContent.trim(),
      askTab: document.querySelector('.mode-tabs [data-mode="ask"]').textContent.trim()
    }))()`);
    check('AI 面板标签为命令执行 / AI 问答，按钮为开始执行',
      aiLabels.tab === '命令执行' && aiLabels.run === '▶ 开始执行'
      && aiLabels.askTab === 'AI 问答', JSON.stringify(aiLabels));
    const runAfterMode = await evalValue(`(() => {
      document.querySelector('.mode-tabs [data-mode="ask"]').click();
      const ask = document.getElementById('btnRun').textContent.trim();
      document.querySelector('.mode-tabs [data-mode="diagnose"]').click();
      return { ask: ask, diag: document.getElementById('btnRun').textContent.trim() };
    })()`);
    check('切回命令执行后按钮仍为开始执行',
      runAfterMode.ask === '▶ 开始提问' && runAfterMode.diag === '▶ 开始执行',
      JSON.stringify(runAfterMode));
    const clearedInput = await evalValue(`(() => {
      document.getElementById('promptInput').value = '待清空的旧要求';
      document.getElementById('btnClear').click();
      return document.getElementById('promptInput').value;
    })()`);
    check('清空 / 重置同时清空输入框', clearedInput === '', JSON.stringify(clearedInput));

    // ---- 6.6 布局无溢出 / 不重叠（防终端塌陷压住 AI 面板） ----
    const layout = await evalValue(`(() => {
      const tp = document.querySelector('.term-panel').getBoundingClientRect();
      const ai = document.querySelector('.ai-panel').getBoundingClientRect();
      const rb = document.getElementById('btnRun').getBoundingClientRect();
      const th = document.querySelector('.term-host').getBoundingClientRect();
      const dr = document.getElementById('aiDrawer').getBoundingClientRect();
      return { tpBottom: tp.bottom, aiTop: ai.top, aiBottom: ai.bottom, rbTop: rb.top,
               thH: th.height, vh: window.innerHeight, vw: window.innerWidth,
               drLeft: Math.round(dr.left), aiOpen: document.body.classList.contains('ai-open') };
    })()`);
    check('终端区有实际高度（不再塌陷为 0）', layout.thH > 80, JSON.stringify(layout));
    check('布局无溢出：AI 面板底部在视口内', layout.aiBottom <= layout.vh + 2, JSON.stringify(layout));
    check('AI 抽屉默认隐藏在右侧视口外（点助手图标才展开）',
      !layout.aiOpen && layout.drLeft >= layout.vw - 2, JSON.stringify(layout));

    // ---- 6.7 动态渲染：模拟一次诊断跑出大量步骤后，输出区必须真实可见且可滚动 ----
    // （直接回应「测试不严谨」的批评：不再只验静态 DOM，而是注入溢出内容验证运行后渲染）
    const render = await evalValue(`(() => {
      const steps = document.getElementById('steps');
      steps.innerHTML = '';
      for (let i = 1; i <= 14; i++) {
        const card = document.createElement('div');
        const isWrite = (i % 3 === 0);
        card.className = 'step ' + (isWrite ? 'write' : 'read');
        const out = ('第 ' + i + ' 步采集样本：/dev/vda1 40G 已用 15G 可用 24G inode 2%\\\\n').repeat(18);
        card.innerHTML =
          '<div class="step-head"><span class="step-no">' + i + '</span>' +
          '<span class="step-cmd">$ df -h /; uptime; free -m; ss -ltnp</span>' +
          '<span class="step-tag ' + (isWrite ? 'write' : 'read') + '">' + (isWrite ? '写操作' : '只读') + '</span></div>' +
          '<div class="step-thought">这是第 ' + i + ' 步的推理说明，用于验证步骤区在内容超长时能够正确分配高度并内部滚动，而不是被压成零高度导致输出不可见。</div>' +
          '<pre class="step-out">' + out + '</pre>';
        steps.appendChild(card);
      }
      const sum = document.getElementById('summary');
      sum.classList.remove('hidden');
      let body = '';
      for (let i = 0; i < 40; i++) body += '<p class="sm-p">结论段落 ' + i + '：诊断显示系统负载正常，磁盘使用率偏高，建议先归档历史日志再评估清理方案，避免直接删除正在写入的文件。</p>';
      sum.innerHTML = '<div class="sm-head"><span class="sm-ico">✓</span><span class="sm-title">AI 结论与建议</span></div><div class="summary-body">' + body + '</div>';
      const aiResults = document.getElementById('aiResults');
      const sRect = steps.getBoundingClientRect();
      const ai = document.querySelector('.ai-panel').getBoundingClientRect();
      const ctrlEl = document.querySelector('.ai-controls');
      const ctrlTop1 = Math.round(ctrlEl.getBoundingClientRect().top);
      aiResults.scrollTop = Math.min(1000, aiResults.scrollHeight - aiResults.clientHeight);
      const ctrlTop2 = Math.round(ctrlEl.getBoundingClientRect().top);
      return {
        aiResultsH: aiResults.clientHeight,
        aiResultsScroll: aiResults.scrollHeight,
        aiResultsOverflow: aiResults.scrollHeight > aiResults.clientHeight + 4,
        stepsH: Math.round(sRect.height),
        sumH: Math.round(sum.getBoundingClientRect().height),
        controlsVisible: !!ctrlEl,
        ctrlMoved: Math.abs(ctrlTop1 - ctrlTop2),
        aiBottom: ai.bottom,
        vh: window.innerHeight
      };
    })()`);
    check('诊断跑出大量步骤后：步骤区渲染出真实高度（>200px，未塌陷为零）',
      render.stepsH > 200, JSON.stringify(render));
    check('诊断跑出大量步骤后：结果区（.ai-results）有界滚动露出全部输出（scrollHeight>clientHeight）',
      render.aiResultsOverflow === true, JSON.stringify(render));
    check('控制区固定且可见（运行/停止按钮始终在框内）',
      render.controlsVisible === true, render.controlsVisible);
    check('滚动结果区时控制区不随之移动（始终钉在框内顶部，不出现横条）',
      render.ctrlMoved < 2, JSON.stringify(render));
    check('结论区有真实高度（>40px，未塌陷）', render.sumH > 40, JSON.stringify(render));
    check('动态渲染后仍无溢出：AI 面板底部在视口内',
      render.aiBottom <= render.vh + 2, JSON.stringify(render));

    // ---- 6.8 右侧抽屉的竖向拖拽条 + 结果区左右对齐 ----
    const sp = await evalValue(`(() => {
      const rz=document.querySelector('.ai-resizer');
      const term=document.getElementById('termPanel');
      const aiPanel=document.querySelector('.ai-panel');
      const main=document.querySelector('.main');
      const drawer=document.getElementById('aiDrawer');
      const mainBefore=Math.round(main.getBoundingClientRect().width);
      const drawerBefore=Math.round(drawer.getBoundingClientRect().width);
      document.documentElement.style.setProperty('--ai-w','760px');  // 模拟往左拖宽抽屉
      const mainAfter=Math.round(main.getBoundingClientRect().width);
      const drawerAfter=Math.round(drawer.getBoundingClientRect().width);
      document.documentElement.style.removeProperty('--ai-w');
      const tb=term.getBoundingClientRect(), ab=aiPanel.getBoundingClientRect();
      return {
        exists: !!rz, cursor: rz ? getComputedStyle(rz).cursor : null,
        oldSplitter: !!document.getElementById('splitter'),
        mainBefore, mainAfter, drawerBefore, drawerAfter,
        panelSameX: Math.abs(tb.x - ab.x) < 1,
        panelSameW: Math.abs(tb.width - ab.width) < 1,
        controlsLeft: Math.round(document.querySelector('.ai-controls .ctl-row').getBoundingClientRect().x),
        aiResultsLeft: Math.round(document.getElementById('aiResults').getBoundingClientRect().x),
        stepsLeft: Math.round(document.getElementById('steps').getBoundingClientRect().x),
        summaryLeft: Math.round(document.getElementById('summary').getBoundingClientRect().x)
      };
    })()`);
    check('抽屉左缘存在竖向拖拽条（cursor: col-resize，旧上下分隔条已移除）',
      sp.exists && sp.cursor === 'col-resize' && !sp.oldSplitter, JSON.stringify(sp));
    check('拖宽抽屉只改变抽屉宽度、终端主区宽度不变（浮层覆盖不挤压）',
      sp.drawerAfter > sp.drawerBefore + 20 && Math.abs(sp.mainAfter - sp.mainBefore) < 2,
      JSON.stringify(sp));
    check('AI 面板不再与终端同宽并列（已改为右侧浮层抽屉）',
      !sp.panelSameX && !sp.panelSameW, JSON.stringify(sp));
    check('结果输出框与控制区左边缘对齐（框对齐；框内内容按内边距缩进属正常）',
      sp.controlsLeft === sp.aiResultsLeft && sp.stepsLeft >= sp.aiResultsLeft, JSON.stringify(sp));

    // ---- 6.9 用户输入框撑满宽度（修复"输入框太窄"） ----
    const ta = await evalValue(`(() => {
      const pi=document.getElementById('promptInput');
      const panel=document.querySelector('.ai-panel').getBoundingClientRect();
      const r=pi.getBoundingClientRect();
      return { w: Math.round(r.width), panelW: Math.round(panel.width),
               ratio: +(r.width / panel.width).toFixed(2),
               minH: Math.round(r.height) };
    })()`);
    check('用户输入框撑满面板宽度（宽度占比 >85%，修复太窄）',
      ta.ratio > 0.85, JSON.stringify(ta));
    check('用户输入框有足够高度（>60px，可看清长需求）', ta.minH > 60, JSON.stringify(ta));

    // ---- 6.10 危险命令确认条：竖排卡片、留在视口内、按钮可点 ----
    const cd = await evalValue(`(() => {
      const dock=document.getElementById('confirmDock');
      dock.classList.remove('hidden');
      document.getElementById('cdCmd').textContent =
        "$ ls -l /var/log/secure /var/log/auth.log 2>&1; systemctl is-active sshd 2>&1; ss -hhn -V 2>&1 | head -2";
      document.getElementById('cdReasons').textContent = '未识别命令，按写操作保守处理（ss -hhn -V 2>&1）';
      document.getElementById('cdPurpose').textContent = '命令作用：统计 SSH 登录失败次数，用于定位暴力破解来源。';
      document.getElementById('cdStep').textContent = '第 1 步 · 写操作';
      document.getElementById('cdCountdown').textContent = '116s';
      const bar=document.querySelector('.confirm-bar');
      const rb=bar.getBoundingClientRect();
      const head=bar.querySelector('.cd-head').getBoundingClientRect();
      const foot=bar.querySelector('.cd-foot').getBoundingClientRect();
      const appr=document.getElementById('cdApprove').getBoundingClientRect();
      const out={ vw:window.innerWidth, vh:window.innerHeight,
        barLeft:Math.round(rb.left), barRight:Math.round(rb.right),
        barTop:Math.round(rb.top), barBottom:Math.round(rb.bottom),
        barW:Math.round(rb.width), barH:Math.round(rb.height),
        footBelowHead: foot.top >= head.top - 1,
        apprVisible: appr.width>4 && appr.height>4 && appr.top>=0 && appr.bottom<=window.innerHeight+2,
        purposeLen: (document.getElementById('cdPurpose').textContent||'').length,
        pe: getComputedStyle(bar).pointerEvents };
      dock.classList.add('hidden');
      return out;
    })()`);
    check('危险命令确认条为竖排卡片（不再横排溢出）',
      cd.footBelowHead && cd.barH > 100, JSON.stringify(cd));
    check('确认条整体留在视口内，不溢出屏幕',
      cd.barLeft >= 0 && cd.barRight <= cd.vw + 2 && cd.barTop >= -2 && cd.barBottom <= cd.vh + 2, JSON.stringify(cd));
    check('确认条「批准执行」按钮可见且可点（pointer-events:auto）',
      cd.apprVisible && cd.pe === 'auto', JSON.stringify(cd));
    check('确认条显示「命令作用」说明（帮助用户判断是否执行）',
      cd.purposeLen > 6, JSON.stringify(cd));

    // ---- 6.11 布局：拖拽条已取消；输入控制区固定在底部、结果区在其上方滚动 ----
    const lay = await evalValue(`(() => {
      const sp2=document.getElementById('splitterAi');
      const controls=document.querySelector('.ai-controls');
      const results=document.getElementById('aiResults');
      const cb=controls.getBoundingClientRect(), rb=results.getBoundingClientRect();
      return { splitterGone: !sp2,
               controlsBelowResults: cb.top >= rb.bottom - 1,
               resultsScroll: getComputedStyle(results).overflowY };
    })()`);
    check('拖拽条已取消（无 #splitterAi）', lay.splitterGone === true, JSON.stringify(lay));
    check('输入控制区固定在结果区下方（贴近页面底部）', lay.controlsBelowResults === true, JSON.stringify(lay));
    check('结果输出区自带滚动条（overflow:auto）', lay.resultsScroll === 'auto', JSON.stringify(lay));

    // ---- 6.12 运维工作台：总览 / 下钻 / 仪表盘扩展 / 切回终端 ----
    await evalValue(`document.getElementById('btnViewWorkbench').click()`);
    await waitUntil(async () =>
      (await evalValue(`document.querySelectorAll('#wbKpis .wb-kpi').length`)) === 4,
      15000, '运维工作台四项总览渲染');
    const wb = await evalValue(`(() => {
      const kpis = Array.from(document.querySelectorAll('#wbKpis .wb-kpi')).map(x => ({
        label: x.querySelector('.wb-kpi-label').textContent.trim(),
        value: x.querySelector('.wb-kpi-value').textContent.trim()
      }));
      return {
        kpis: kpis,
        regionRows: document.querySelectorAll('#wbRegionRows tr').length,
        quality: document.querySelectorAll('#wbQualityList .wb-quality-item').length,
        dashboardCards: document.querySelectorAll('#opsDashboardExt .odb-card').length,
        dashboardKpis: document.querySelectorAll('#opsDashboardExt .odb-kpi').length,
        bars: document.querySelectorAll('#wbResourceBars .wb-bar-track').length,
        sourceTag: (document.getElementById('wbSourceTag').textContent || '').trim(),
        collectBtn: !!document.getElementById('wbCollect'),
        winOpts: Array.from(document.querySelectorAll('#wbWindow option')).map(o => o.value),
        metricRows: document.querySelectorAll('#wbMetricRows tr').length,
        assetRows: document.querySelectorAll('#wbAssetRows tr').length,
        assetTotal: (document.getElementById('wbAssetsTotal').textContent || '').trim(),
        qualityTotal: (document.getElementById('wbQualityTotal').textContent || '').trim(),
        windowLabel: (document.getElementById('wbWindowLabel').textContent || '').trim(),
        refreshBtn: !!document.getElementById('wbRefresh'),
        levelOpts: Array.from(document.querySelectorAll('#hLevel option')).map(o => o.value),
        termHidden: document.getElementById('termPanel').classList.contains('hidden'),
        wbVisible: !document.getElementById('workbenchView').classList.contains('hidden')
      };
    })()`);
    check('工作台四项指标均为真实数值（不再写死文章示例）',
      wb.kpis.length === 4 && wb.kpis.every(k => /^[0-9]/.test(k.value))
      && wb.kpis[1].value.includes('%'), JSON.stringify(wb.kpis));
    check('数据源标签显示已纳管主机数', wb.sourceTag.includes('已纳管'), wb.sourceTag);
    check('界面提供采集监控数据入口', wb.collectBtn === true, wb.collectBtn);
    check('地域维度按纳管主机归集（至少合计行）', wb.regionRows >= 2, wb.regionRows);
    check('资产质量六个维度、资源类型四条已渲染，态势舱扩展已去掉',
      wb.quality === 6 && wb.bars === 4 && wb.dashboardCards === 0 && wb.dashboardKpis === 0,
      JSON.stringify(wb));
    check('纳管主机资产台账展示已填参数（行含纳管主机）',
      wb.assetRows >= 1 && /[1-9]\d*\s*台已纳管/.test(wb.assetTotal),
      JSON.stringify({rows: wb.assetRows, total: wb.assetTotal}));
    check('质量清单 0 项时说明字段已填齐（不再误导为没读到）',
      /字段已填齐|待治理/.test(wb.qualityTotal), wb.qualityTotal);
    check('切到工作台时终端隐藏且工作台可见',
      wb.termHidden === true && wb.wbVisible === true, JSON.stringify(wb));
    check('监控时间窗提供 5/10/15/30 分钟、1 小时、自定义六档',
      JSON.stringify(wb.winOpts) === JSON.stringify(['5m', '10m', '15m', '30m', '1h', 'custom']),
      JSON.stringify(wb.winOpts));
    check('实时采集指标表已渲染（空态也给提示行）', wb.metricRows >= 1, wb.metricRows);
    check('告警分布显示所选时间窗标签', wb.windowLabel.length > 0, wb.windowLabel);
    check('应用等级为一般/重要/关键/核心四级下拉',
      ['一般', '重要', '关键', '核心'].every(x => wb.levelOpts.includes(x)),
      JSON.stringify(wb.levelOpts));

    // 切换时间窗后标签随之变化（验证问题5的交互确实生效）
    await evalValue(`(() => { const s = document.getElementById('wbWindow');
      s.value = '5m'; s.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await waitUntil(async () =>
      (await evalValue(`(document.getElementById('wbWindowLabel').textContent || '').includes('5 分钟')`)),
      12000, '切换 5 分钟时间窗');
    check('切换时间窗后告警标签更新为最近 5 分钟', true);
    await evalValue(`(() => { const s = document.getElementById('wbWindow');
      s.value = '1h'; s.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await waitUntil(async () =>
      (await evalValue(`(document.getElementById('wbWindowLabel').textContent || '').includes('1 小时')`)),
      12000, '切回 1 小时时间窗');

    // 刷新按钮：点击后进入 loading 并给出可见反馈（验证问题3）
    await evalValue(`document.getElementById('wbRefresh').click()`);
    const refreshFeedback = await evalValue(`(() => {
      const m = document.getElementById('wbCollectMsg').textContent || '';
      const b = document.getElementById('wbRefresh');
      return { msg: m.trim(), btn: b.textContent.trim(), disabled: b.disabled };
    })()`);
    check('刷新按钮给出可见反馈文案',
      (refreshFeedback.msg.includes('正在同步全部数据源') || refreshFeedback.msg.includes('已刷新'))
      && (refreshFeedback.btn.includes('刷新中') || refreshFeedback.disabled),
      JSON.stringify(refreshFeedback));
    await waitUntil(async () =>
      (await evalValue(`(document.getElementById('wbCollectMsg').textContent || '').includes('已刷新')`)),
      12000, '刷新完成反馈');
    check('刷新完成后按钮恢复可用',
      (await evalValue(`!document.getElementById('wbRefresh').disabled`)) === true);

    await evalValue(`document.querySelector('#wbQualityList .wb-quality-item').click()`);
    await waitUntil(async () => await evalValue(`(() => {
      const m = document.getElementById('modalDrill');
      return !m.classList.contains('hidden') && document.querySelectorAll('#drillRows tr').length > 0;
    })()`), 12000, '资产质量下钻明细渲染');
    const drill = await evalValue(`(() => ({
      rows: document.querySelectorAll('#drillRows tr').length,
      cols: document.querySelectorAll('#drillHead th').length,
      pager: (document.getElementById('drillPager').textContent || '').trim()
    }))()`);
    check('资产质量点击后下钻到具体主机（明细表 + 分页）',
      drill.rows > 0 && drill.cols >= 8 && drill.pager.includes('共'), JSON.stringify(drill));
    await evalValue(`document.querySelector('[data-close="modalDrill"]').click()`);

    // 全局刷新按钮：必须重读全部数据并给出可见反馈
    await evalValue(`document.getElementById('btnGlobalRefresh').click()`);
    await waitUntil(async () =>
      (await evalValue(`(document.getElementById('wbCollectMsg').textContent || '').includes('全局刷新完成')`)),
      15000, '顶栏全局刷新完成');
    check('顶栏全局刷新可用且给出完成反馈', true);

    // 回归：冷启动直接进各台账页点「新增」必须打开弹窗、保存并真正落库。
    // 此前 catalogMeta 只由主机管理 / catalog 页加载，直接进 DB 资产会抛异常且无反应。
    const catalogCases = [
      ['db_assets', 'database', '数据库资产'],
      ['network_device', 'network_device', '网络设备'],
      ['credential', 'credential', '凭据管理'],
      ['middleware', 'middleware', '应用/中间件管理'],
      ['alert_source', 'alert_source', '告警数据源'],
    ];
    for (const [page, category, title] of catalogCases) {
      const name = 'UI自测_' + page + '_' + Date.now();
      const cookie = 'aoc_session=' + token;
      await evalValue(`(() => {
        OPS.catalogMeta = null;
        document.querySelector('#wbNav [data-page="${page}"]').click();
        return true;
      })()`);
      await waitUntil(async () => await evalValue(`(() =>
        Array.from(document.querySelectorAll('#opsPageTools button'))
          .some(x => x.textContent.includes('新增')))()`),
      12000, title + ' 新增入口');

      const clicked = await evalValue(`(() => {
        OPS.catalogMeta = null;
        const b = Array.from(document.querySelectorAll('#opsPageTools button'))
          .find(x => x.textContent.includes('新增'));
        if (b) b.click();
        return !!b;
      })()`);
      let opened = false, saved = false, visible = false, persisted = false;
      try {
        await waitUntil(async () => await evalValue(`(() =>
          !document.getElementById('modalOpsRecord').classList.contains('hidden') &&
          document.querySelectorAll('#opsRecordFields input').length > 0)()`),
        10000, title + ' 新增弹窗');
        opened = true;
        await evalValue(`(() => {
          const f = document.getElementById('opsRecordFields');
          f.querySelector('input').value = ${JSON.stringify(name)};
          document.getElementById('opsRecordSave').click();
          return true;
        })()`);
        await waitUntil(async () => await evalValue(`(() =>
          document.getElementById('modalOpsRecord').classList.contains('hidden') &&
          (document.getElementById('opsPageBody').textContent || '').includes(${JSON.stringify(name)}))()`),
        15000, title + ' 新增保存回显');
        saved = true; visible = true;
        const all = await (await fetch(HOST + '/api/ops/catalog?category=' + encodeURIComponent(category),
          { headers: { Cookie: cookie } })).json();
        persisted = (all.rows || []).some(r => r.name === name);

        // 同一条记录继续验证「编辑 / 删除」按钮，不允许只做存在性检查。
        const renamed = name + '_改';
        let edited = false, editPersisted = false, deleted = false, deletePersisted = false;
        await evalValue(`(() => {
          window.__aocConfirm = window.confirm; window.confirm = () => true; return true;
        })()`);
        try {
          const editClicked = await evalValue(`(() => {
            const row = Array.from(document.querySelectorAll('#opsPageBody tbody tr'))
              .find(tr => tr.textContent.includes(${JSON.stringify(name)}));
            const b = row && Array.from(row.querySelectorAll('button'))
              .find(x => x.textContent.includes('编辑'));
            if (b) b.click();
            return !!b;
          })()`);
          await waitUntil(async () => await evalValue(`(() =>
            !document.getElementById('modalOpsRecord').classList.contains('hidden'))()`),
          10000, title + ' 编辑弹窗');
          await evalValue(`(() => {
            document.getElementById('opsRecordFields').querySelector('input').value = ${JSON.stringify(renamed)};
            document.getElementById('opsRecordSave').click();
            return true;
          })()`);
          await waitUntil(async () => await evalValue(`(() =>
            document.getElementById('modalOpsRecord').classList.contains('hidden') &&
            (document.getElementById('opsPageBody').textContent || '').includes(${JSON.stringify(renamed)}))()`),
          15000, title + ' 编辑生效');
          edited = !!editClicked;
          const afterEdit = await (await fetch(HOST + '/api/ops/catalog?category=' + encodeURIComponent(category),
            { headers: { Cookie: cookie } })).json();
          editPersisted = (afterEdit.rows || []).some(r => r.name === renamed);

          const delClicked = await evalValue(`(() => {
            const row = Array.from(document.querySelectorAll('#opsPageBody tbody tr'))
              .find(tr => tr.textContent.includes(${JSON.stringify(renamed)}));
            const b = row && Array.from(row.querySelectorAll('button'))
              .find(x => x.textContent.includes('删除'));
            if (b) b.click();
            return !!b;
          })()`);
          await waitUntil(async () => await evalValue(`(() =>
            !(document.getElementById('opsPageBody').textContent || '').includes(${JSON.stringify(renamed)}))()`),
          15000, title + ' 删除生效');
          deleted = !!delClicked;
          const afterDel = await (await fetch(HOST + '/api/ops/catalog?category=' + encodeURIComponent(category),
            { headers: { Cookie: cookie } })).json();
          deletePersisted = !(afterDel.rows || []).some(r => r.name === renamed);
        } catch (e) {
          // 编辑/删除失败时保持 false，由下面的断言如实报告
        } finally {
          await evalValue(`(() => { window.confirm = window.__aocConfirm || window.confirm; return true; })()`);
        }
        check(title + '「编辑 / 删除」真实点击并落库',
          edited && editPersisted && deleted && deletePersisted,
          JSON.stringify({ edited, editPersisted, deleted, deletePersisted, renamed }));
      } catch (e) {
        // 如实记失败，同时尽力关闭弹窗，不影响后续用例
        try { await evalValue(`document.querySelector('[data-close="modalOpsRecord"]').click()`); } catch { /* ignore */ }
      }
      check(title + '「新增」真实点击→保存→回显→落库',
        clicked && opened && saved && visible && persisted,
        JSON.stringify({ clicked, opened, saved, visible, persisted, name }));

      // 测试后清理：即使中间断言失败，也按名称从后端删除测试记录
      const all = await (await fetch(HOST + '/api/ops/catalog?category=' + encodeURIComponent(category),
        { headers: { Cookie: cookie } })).json();
      for (const row of (all.rows || []).filter(r => r.name === name || r.name === name + '_改')) {
        await fetch(HOST + '/api/ops/catalog/' + encodeURIComponent(row.id),
          { method: 'DELETE', headers: { Cookie: cookie } });
      }
    }

    // 主机管理页：新增/导入/导出/删除按钮都在，且数据来自 /api/ops/hosts
    await evalValue(`document.querySelector('#wbNav [data-page="hosts"]').click()`);
    await waitUntil(async () => await evalValue(`(() => {
      const tools = document.getElementById('opsPageTools').textContent || '';
      return tools.includes('导入') && tools.includes('导出') &&
        document.querySelectorAll('#opsPageBody tbody tr').length > 0;
    })()`), 15000, '主机管理页面渲染');
    const hostPage = await evalValue(`(() => {
      const tools = document.getElementById('opsPageTools').textContent;
      return {
        add: tools.includes('新增主机'), import: tools.includes('导入'),
        export: tools.includes('导出'), file: !!document.getElementById('hostImportFile'),
        edit: (document.getElementById('opsPageBody').textContent || '').includes('编辑')
      };
    })()`);
    check('主机管理具备新增/导入/导出/编辑/删除入口',
      hostPage.add && hostPage.import && hostPage.export && hostPage.file && hostPage.edit,
      JSON.stringify(hostPage));

    // 数据库记录保存按钮：真实点击 -> 写入 -> 表格可见（测试后清理）
    await evalValue(`document.querySelector('#wbNav [data-page="db_query"]').click()`);
    await waitUntil(async () => await evalValue(`(() =>
      document.getElementById('opsPageTitle').textContent === 'SQL 查询' &&
      document.querySelectorAll('#opsPageTools button').length > 0)()`),
      12000, 'SQL 查询页');
    const dbPageState = await evalValue(`(() => ({
      tools: document.querySelectorAll('#opsPageTools button').length,
      msg: (document.getElementById('opsPageMsg').textContent || '').trim(),
      owned: window.OpsExt.OWNED_PAGES.has('db_query')
    }))()`);
    check('SQL 查询页工具按钮与数据加载正常',
      dbPageState.tools > 0 && dbPageState.owned, JSON.stringify(dbPageState));
    await evalValue(`(() => {
      document.querySelector('#opsPageTools button').click();
      const f = document.getElementById('opsRecordFields');
      f.querySelectorAll('input')[0].value = 'UI自测查询';
      f.querySelectorAll('textarea')[0].value = 'SELECT 1';
      f.querySelectorAll('textarea')[1].value = '用户输入的结果';
      document.getElementById('opsRecordSave').click();
    })()`);
    await waitUntil(async () => await evalValue(`(() =>
      document.getElementById('modalOpsRecord').classList.contains('hidden') &&
      (document.getElementById('opsPageBody').textContent || '').includes('UI自测查询'))()`),
      15000, '数据库记录保存生效');
    check('数据库记录「保存」按钮真正写入并回显到页面', true);
    {
      const cookie = 'aoc_session=' + token;
      const all = await (await fetch(HOST + '/api/ops/db/records?kind=query',
        { headers: { Cookie: cookie } })).json();
      const testRow = (all.rows || []).find(r => r.title === 'UI自测查询');
      if (testRow) {
        await fetch(HOST + '/api/ops/db/records/' + encodeURIComponent(testRow.id),
          { method: 'DELETE', headers: { Cookie: cookie } });
      }
    }

    // 告警策略保存按钮：保存后给出可见成功反馈
    await evalValue(`document.querySelector('#wbNav [data-page="alert_policies"]').click()`);
    await waitUntil(async () => await evalValue(`(() =>
      (document.getElementById('opsPageBody').textContent || '').includes('保存告警策略'))()`),
      12000, '告警策略页');
    await evalValue(`document.querySelector('#opsPageBody button').click()`);
    await waitUntil(async () => await evalValue(`(() =>
      (document.getElementById('opsPageMsg').textContent || '').includes('已保存'))()`),
      12000, '告警策略保存反馈');
    check('告警策略「保存」按钮有可见成功反馈', true);

    // 日志源保存按钮：非法路径必须在弹窗内给出错误，而不是无反应
    await evalValue(`document.querySelector('#wbNav [data-page="log_manage"]').click()`);
    await waitUntil(async () => await evalValue(`(() =>
      (document.getElementById('opsPageTools').textContent || '').includes('新增日志源'))()`),
      12000, '日志管理页');
    await evalValue(`(() => {
      document.querySelector('#opsPageTools button').click();
      const f = document.getElementById('opsRecordFields');
      f.querySelector('select').value = (f.querySelector('select option:last-child') || {}).value || '';
      f.querySelectorAll('input')[0].value = 'relative.log';
      document.getElementById('opsRecordSave').click();
    })()`);
    await waitUntil(async () => await evalValue(`(() =>
      (document.getElementById('opsRecordMsg').textContent || '').includes('绝对路径'))()`),
      12000, '日志源保存校验反馈');
    check('日志源「保存」按钮有校验反馈（不再无反应）', true);
    await evalValue(`document.querySelector('[data-close="modalOpsRecord"]').click()`);

    // 运维大屏：打开后必须渲染真实指标面板，并能关闭回到工作台
    await evalValue(`document.getElementById('btnScreen').click()`);
    await waitUntil(async () => await evalValue(`(() =>
      !document.getElementById('screenView').classList.contains('hidden') &&
      document.querySelectorAll('#screenView .screen-panel').length >= 8)()`),
      15000, '运维大屏渲染');
    await waitUntil(async () => await evalValue(`(() => {
      const m = document.getElementById('screenMsg').textContent || '';
      return m.includes('窗口：') || m.includes('失败');
    })()`), 15000, '大屏真实数据加载');
    const screen = await evalValue(`(() => ({
      panels: document.querySelectorAll('#screenView .screen-panel').length,
      kpis: document.querySelectorAll('#screenKpis .screen-kpi').length,
      screenMsg: (document.getElementById('screenMsg').textContent || '').trim(),
      bg: getComputedStyle(document.getElementById('screenView')).backgroundColor,
      note: (document.getElementById('screenNote').textContent || '').trim(),
      clock: (document.getElementById('screenTime').textContent || '').trim()
    }))()`);
    check('大屏渲染面板/KPI/时钟且说明数据口径',
      screen.panels >= 8 && screen.kpis >= 3 && /^\d{2}:\d{2}:\d{2}$/.test(screen.clock)
      && screen.note.includes('纳管主机'), JSON.stringify(screen));
    const screenExtra = await evalValue(`(() => ({
      windows: Array.from(document.querySelectorAll('#screenWindow option')).map(o => o.value),
      units: document.querySelectorAll('#screenView .screen-unit').length,
      resRows: document.querySelectorAll('#screenResources .screen-res').length,
      resEmpty: (document.querySelector('#screenResources .screen-empty') || {}).textContent || ''
    }))()`);
    // 有真实采集数据时指标必须带 % 单位；无纳管数据时大屏显示空状态、不展示模拟指标（同样正确）
    const screenUnitsOk = screenExtra.resRows > 0
      ? screenExtra.units > 0
      : screenExtra.resEmpty.includes('尚未采集纳管主机');
    check('大屏可切换 5/10/15/30 分钟窗口且指标单位口径正确',
      JSON.stringify(screenExtra.windows) === JSON.stringify(['5m', '10m', '15m', '30m', '1h'])
      && screenUnitsOk, JSON.stringify(screenExtra));
    const screenLayout = await evalValue(`(() => {
      const box = document.getElementById('screenView');
      const r = box.getBoundingClientRect();
      return { top: r.top, left: r.left, width: r.width, height: r.height,
               vw: window.innerWidth, vh: window.innerHeight,
               bg: getComputedStyle(box).backgroundColor,
               fullBtn: !!document.getElementById('btnScreenFull'),
               fullFn: typeof window.OpsExt.toggleScreenFull === 'function' };
    })()`);
    check('大屏铺满整个浏览器且提供全屏按钮',
      screenLayout.top === 0 && screenLayout.left === 0
      && Math.abs(screenLayout.width - screenLayout.vw) < 2
      && Math.abs(screenLayout.height - screenLayout.vh) < 2
      && screenLayout.fullBtn && screenLayout.fullFn, JSON.stringify(screenLayout));
    check('大屏背景改成亮蓝紫而非原来的近黑色',
      screenLayout.bg !== 'rgb(5, 13, 28)' && screenLayout.bg.includes('rgb'),
      screenLayout.bg);
    const screenPng = path.join(ASSETS, 'screen-preview.png');
    check('保存 assets/screen-preview.png（运维大屏）', await capture(screenPng) > 1000);
    await evalValue(`document.getElementById('btnScreenClose').click()`);
    check('关闭大屏后回到原视图',
      (await evalValue(`document.getElementById('screenView').classList.contains('hidden')`)) === true);

    // ---- 6.13 资产管理 / 监控告警 / 日志中心 / 态势舱 ----
    const navInfo = await evalValue(`(() => ({
      items: document.querySelectorAll('#wbNav .wb-nav-item').length,
      groups: Array.from(document.querySelectorAll('#wbNav .wb-nav-title')).map(x => x.textContent.trim()),
      cockpit: document.querySelectorAll('#wbCockpit .cockpit-panel').length,
      cockpitColor: getComputedStyle(document.getElementById('wbCockpit')).backgroundImage.includes('gradient')
    }))()`);
    check('左侧四组功能导航覆盖资产/监控/DB/日志全部菜单',
      navInfo.items >= 30 && navInfo.groups.length === 4, JSON.stringify(navInfo));
    const platNav = await evalValue(`(() => {
      const nav = document.getElementById('wbNav');
      const first = nav.querySelector('.wb-nav-item');
      return { first: first ? (first.dataset.page || '') : '',
               cls: first ? first.className : '' };
    })()`);
    check('平台导航置于左侧菜单最上方并带 quick 样式',
      platNav.first === 'platform' && platNav.cls.includes('wb-nav-quick'), JSON.stringify(platNav));
    const navToggle = await evalValue(`(() => {
      const g = document.querySelector('#wbNav .wb-nav-group');
      const t = g.querySelector('.wb-nav-title');
      const item = g.querySelector('.wb-nav-item');
      const before = t.getAttribute('aria-expanded');
      t.click();
      const collapsed = { state: t.getAttribute('aria-expanded'),
                          display: getComputedStyle(item).display };
      t.click();
      return { before: before, collapsed: collapsed,
               restored: t.getAttribute('aria-expanded'),
               display2: getComputedStyle(item).display };
    })()`);
    check('菜单分组标题点击可收起并再次展开',
      navToggle.before === 'true' && navToggle.collapsed.state === 'false'
      && navToggle.collapsed.display === 'none' && navToggle.restored === 'true'
      && navToggle.display2 !== 'none', JSON.stringify(navToggle));
    check('紫罗兰态势舱渲染三个数据面板且不照搬参考配色',
      navInfo.cockpit === 3 && navInfo.cockpitColor === true, JSON.stringify(navInfo));
    for (const [page, title] of [['rooms', '机房管理'], ['inspection', '主机巡检'],
      ['monitor_center', '监控中心'], ['alert_policies', '告警策略'],
      ['log_manage', '日志管理'], ['log_query', '日志查询'],
      ['platform', '平台导航'], ['db_assets', '数据库资产'],
      ['db_monitor', '监控概览'], ['alert_center', '告警中心'],
      ['domain_alert', '域名告警']]) {
      await evalValue(`document.querySelector('#wbNav [data-page="${page}"]').click()`);
      await waitUntil(async () => await evalValue(`(() => {
        const t = (document.getElementById('opsPageTitle').textContent || '').trim();
        const b = document.getElementById('opsPageBody');
        return t === '${title}' && !document.getElementById('wbPageGeneric').classList.contains('hidden')
          && (b.querySelector('table') || b.querySelector('.ops-settings') ||
              b.querySelector('.plat-grid') || b.querySelector('.odb-kpis'));
      })()`), 12000, title + ' 页面渲染');
      check('功能页「' + title + '」可打开并渲染真实数据表格', true);
    }
    await evalValue(`document.querySelector('#wbNav [data-page="platform"]').click()`);
    await waitUntil(async () => await evalValue(`
      document.querySelectorAll('#opsPageBody .plat-tile b').length >= 6`),
      12000, '平台导航入口渲染');
    const platTiles = await evalValue(`
      Array.from(document.querySelectorAll('#opsPageBody .plat-tile b'))
        .map(x => x.textContent.trim())`);
    check('平台导航只保留六个入口且红色框占位项已去掉',
      platTiles.length === 6 && !['配置中心', '服务作业', 'AIOps 运维',
        '工单中心', '费用中心', '审计中心'].some(x => platTiles.includes(x))
      && ['资产管理', '容器管理', 'DB 数据库', '日志中心', '告警中心', '系统管理']
        .every(x => platTiles.includes(x)), JSON.stringify(platTiles));

    await evalValue(`document.querySelector('#wbNav [data-page="dashboard"]').click()`);
    await waitUntil(async () =>
      (await evalValue(`!document.getElementById('wbPageDashboard').classList.contains('hidden')`)),
      12000, '返回仪表盘');

    await maskHostAddresses();
    const wbPng = path.join(ASSETS, 'workbench-preview.png');
    const wbSize = await capture(wbPng);
    check('保存 assets/workbench-preview.png（工作台界面）', wbSize > 1000, wbSize + ' bytes');

    await evalValue(`document.getElementById('btnViewTerminal').click()`);
    await waitUntil(async () => await evalValue(`
      !document.getElementById('termPanel').classList.contains('hidden')`), 8000, '切回 SSH 终端');
    check('切回 SSH 终端后终端重新显示（会话与审计不受影响）', true);

    // ---- 7. 硬证据：初始化期间没有任何 /api/terminal/open 请求 ----
    const termOpens = events
      .filter(m => m.method === 'Network.requestWillBeSent')
      .map(m => String((m.params && m.params.request && m.params.request.url) || ''))
      .filter(u => u.includes('/api/terminal/open'));
    check('登录/初始化未发出 /api/terminal/open（不自动连接）',
      termOpens.length === 0, JSON.stringify(termOpens));

    // ---- 8. 页面内用 AiOpsRender 处理综合脏样本 ----
    const dirty = [
      '1. **打开哪些文件、以什么布局打开**（分屏、标签页、diff）',
      '2. **打开后定位到哪里**（行号 / 搜索命中 / tag）',
      '--',
      '> 注意 view +100 file 与 vim -R +100 file 等价。',
      '```bash',
      'vim -R +100 /var/log/app.log',
      '```',
      '四、常用组合速查',
      '```bash',
      'vim -R +100 /var/log/app.log',
      '```',
      '四、常用组合速查',
      '【现象】主机磁盘使用率偏高，当前共运行约 207 个进程。',
      '【证据】df -hT 显示 /dev/vda1 40G 已用 15G，可用 24G。',
      '【建议】先归档历史日志，不要直接删除。',
      '七、脚本化批量',
      '· 该机属于',
    ].join('\n');

    const rr = await evalValue(`(() => {
      const R = window.AiOpsRender;
      const sample = ${JSON.stringify(dirty)};
      const txt = R.cleanText(sample, { final: true });
      const lines = R.cleanLines(sample, { final: true });
      const box = document.createElement('div');
      R.renderRichText(box, sample, { final: true });
      return {
        txt: txt,
        domText: box.textContent || '',
        types: lines.lines.map(x => x.type),
        truncated: lines.truncated
      };
    })()`);
    check('综合脏样本：cleanText 输出无 ** 星号残留', !rr.txt.includes('**'), rr.txt.slice(0, 160));
    check('综合脏样本：renderRichText DOM 无 ** 星号残留', !rr.domText.includes('**'));
    check('综合脏样本：输出无 ``` 代码围栏', !rr.txt.includes('```') && !rr.domText.includes('```'));
    check('综合脏样本：末尾空标题「脚本化批量」被移除',
      !rr.txt.includes('脚本化批量') && !rr.domText.includes('脚本化批量'), rr.txt.slice(-160));
    check('综合脏样本：结尾半句「该机属于」被移除',
      !rr.txt.includes('该机属于') && !rr.domText.includes('该机属于'), rr.txt.slice(-160));
    check('综合脏样本：重复标题「常用组合速查」只留一次',
      (rr.txt.match(/常用组合速查/g) || []).length === 1, rr.txt.slice(0, 200));
    check('综合脏样本：保留完整中文分节与命令示例',
      rr.txt.includes('【现象】') && rr.txt.includes(' vim -R +100') || rr.txt.includes('vim -R +100'),
      rr.txt.slice(0, 160));
    check('综合脏样本：cleanLines 标记为 truncated', rr.truncated === true, rr.truncated);

    // ---- 8.5 问题5：阶段结论标题与释义在前端完整渲染 ----
    const stageSample = [
      '阶段结论 · 已排查 1 步',
      '本次排查步骤较多，已在 1 步时汇总现有证据给出结论。',
      '下面内容基于已收集到的真实输出整理；若还需更深入分析，可把问题拆得更具体后重新发起。',
      '【现象】主机磁盘使用率偏高，当前共运行约 207 个进程。',
      '【建议】先归档历史日志，不要直接删除。',
    ].join('\n');
    const stage = await evalValue(`(() => {
      const R = window.AiOpsRender;
      const sample = ${JSON.stringify(stageSample)};
      const box = document.createElement('div');
      R.renderRichText(box, sample, { final: true });
      return { txt: R.cleanText(sample, { final: true }), domText: box.textContent || '' };
    })()`);
    check('阶段结论标题在前端完整渲染（含「已排查」）',
      stage.txt.includes('阶段结论') && stage.txt.includes('已排查')
      && stage.domText.includes('阶段结论'), stage.txt.slice(0, 160));
    check('阶段结论正文释义保留且无 ** 残留、无「最大重试」字样',
      stage.txt.includes('汇总现有证据') && !stage.txt.includes('最大重试')
      && !stage.txt.includes('**') && !stage.domText.includes('**'),
      stage.txt.slice(0, 160));

    // ---- 9. 首页截图 ----
    await maskHostAddresses();
    const mainPng = path.join(ASSETS, 'preview.png');
    const mainSize = await capture(mainPng);
    check('保存 assets/preview.png', mainSize > 1000, mainSize + ' bytes');

    if (uiErr.trim()) console.log('[INFO] ui_server stderr 非空（不影响断言，内容不回显）');
  } finally {
    try { if (ws) ws.close(); } catch { /* ignore */ }
    killTree(chrome);
    killTree(ui);
    // Chrome 子进程释放 profile 句柄有延迟：先等一拍，再用 Node 内建重试删除
    await sleep(400);
    try {
      fs.rmSync(profileDir, {
        recursive: true, force: true, maxRetries: 20, retryDelay: 300,
      });
    } catch { /* 目录已不可删时由系统 Temp 自行回收，不判为用例失败 */ }
  }

  console.log('\n结果: %d 通过 / %d 失败 / %d SKIP', PASS, FAIL, SKIP);
  if (failures.length) console.log('失败项: ' + failures.join(' | '));
  return FAIL ? 1 : 0;
}

main().then(code => process.exit(code)).catch(e => {
  console.log('[FAIL] L6 运行异常  -> ' + String(e && e.stack || e).slice(0, 400));
  console.log('\n结果: %d 通过 / %d 失败 / %d SKIP', PASS, FAIL + 1, SKIP);
  process.exit(1);
});
