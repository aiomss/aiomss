/* ============================================================
   运维工作台扩展页面（app.js 之后加载）
   约束：只展示「已纳管主机真实采集」或「用户显式输入」的数据。
   任何没有来源的数字都留空并显示空态，绝不生成演示数据。
   ============================================================ */
(function () {
  'use strict';

  const OWNED_PAGES = new Set([
    'hosts', 'rooms', 'platform', 'db_assets', 'db_query', 'db_audit',
    'db_monitor', 'db_slow', 'db_assistant', 'alert_center', 'domain_alert'
  ]);

  let screenOpen = false;
  let screenTimer = null;
  let lastView = 'terminal';

  function byId(id) { return document.getElementById(id); }
  function text(v) { return v === null || v === undefined || v === '' ? '—' : String(v); }
  function pct(v) { return v === null || v === undefined || v === '' ? '—' : String(v) + '%'; }
  function msg(t) { const m = byId('opsPageMsg'); if (m) m.textContent = t || ''; }
  function btn(label, fn, cls) { return opsBtn(label, fn, cls); }

  function card(title, note) {
    const box = el('div', 'odb-card');
    const h = el('h3', '', title);
    if (note) h.appendChild(el('span', '', note));
    box.appendChild(h);
    return box;
  }

  function bars(items, valueKey, labelKey, warn) {
    const box = el('div');
    const rows = items || [];
    const max = Math.max(1, ...rows.map(r => Number(r[valueKey]) || 0));
    rows.forEach(r => {
      const line = el('div', 'odb-hbar');
      line.appendChild(el('label', '', text(r[labelKey])));
      const track = el('div', 'track');
      const fill = el('span');
      fill.style.width = Math.max(2, Math.round((Number(r[valueKey]) || 0) / max * 100)) + '%';
      if (warn) fill.style.background = 'linear-gradient(90deg,#e8a02a,#ffcf62)';
      track.appendChild(fill); line.appendChild(track);
      line.appendChild(el('b', '', String(r[valueKey])));
      box.appendChild(line);
    });
    if (!rows.length) box.appendChild(el('div', 'odb-note', '当前没有可统计的数据。'));
    return box;
  }

  function donut(items, colors) {
    const wrap = el('div', 'odb-donut');
    const rows = (items || []).filter(x => (Number(x.value) || 0) > 0);
    if (!rows.length) {
      wrap.appendChild(el('div', 'odb-note', '还没有可统计的用户录入字段，保持空态。'));
      return wrap;
    }
    const total = rows.reduce((s, x) => s + Number(x.value), 0);
    const pie = el('div', 'odb-pie');
    let acc = 0;
    const stops = rows.map((r, i) => {
      const from = acc / total * 100;
      acc += Number(r.value);
      return colors[i % colors.length] + ' ' + from.toFixed(2) + '% ' +
        (acc / total * 100).toFixed(2) + '%';
    });
    pie.style.background = 'conic-gradient(' + stops.join(',') + ')';
    wrap.appendChild(pie);
    const legend = el('div', 'odb-legend');
    rows.forEach((r, i) => {
      const line = el('div');
      const dot = el('i'); dot.style.background = colors[i % colors.length];
      line.appendChild(dot);
      line.appendChild(document.createTextNode(r.name + ' · ' + r.value));
      legend.appendChild(line);
    });
    wrap.appendChild(legend);
    return wrap;
  }

  const PIE_COLORS = ['#3f51d6', '#12a89a', '#df9a24', '#e2608a', '#3c7cf0', '#7b61ff'];

  /* ---------------- 主机管理：增删改导入导出 ---------------- */
  async function renderHosts() {
    if (!OPS.catalogMeta) OPS.catalogMeta = await api('/api/ops/catalog/meta');
    const tools = byId('opsPageTools'), body = byId('opsPageBody');
    const kw = el('input'); kw.type = 'text'; kw.placeholder = '按名称 / 地址 / 地域 / 机房搜索';
    tools.appendChild(kw);
    tools.appendChild(btn('查询', () => load()));
    tools.appendChild(btn('＋ 新增主机', openNewHost, 'primary'));
    tools.appendChild(btn('⬆ 导入', () => byId('hostImportFile').click()));
    tools.appendChild(btn('⬇ 导出', () => {
      location.href = '/api/ops/hosts/export';
      msg('导出文件为脱敏台账，不含密码或口令散列。');
    }));
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('主机管理')));

    const split = el('div', 'ops-split');
    const side = el('div', 'ops-side');
    side.appendChild(el('div', 'ops-side-title', '资产分组 · 按地域'));
    const searchGroup = el('input'); searchGroup.placeholder = '搜索分组';
    side.appendChild(searchGroup);
    const groupBox = el('div', 'ops-group-list');
    side.appendChild(groupBox);
    split.appendChild(side);
    const main = el('div', 'ops-split-main'); split.appendChild(main);
    body.appendChild(split);

    let all = [], group = '';
    function filtered() {
      const q = (kw.value || '').toLowerCase();
      return all.filter(h => (!group || (h.region || '未分配地域') === group) &&
        (!q || JSON.stringify(h).toLowerCase().includes(q)));
    }
    function groups() {
      const m = {};
      all.forEach(h => {
        const k = h.region || '未分配地域';
        m[k] = (m[k] || 0) + 1;
      });
      return Object.keys(m).sort().map(k => ({ name: k, count: m[k] }));
    }
    function renderGroups() {
      const q = (searchGroup.value || '').toLowerCase();
      groupBox.innerHTML = '';
      const allBtn = btn('全部主机 (' + all.length + ')', () => { group = ''; renderGroups(); load(); });
      if (!group) allBtn.classList.add('active');
      groupBox.appendChild(allBtn);
      groups().filter(g => !q || g.name.toLowerCase().includes(q)).forEach(g => {
        const b = btn(g.name + ' (' + g.count + ')', () => { group = g.name; renderGroups(); load(); });
        if (group === g.name) b.classList.add('active');
        groupBox.appendChild(b);
      });
    }
    async function load() {
      const r = await api('/api/ops/hosts');
      all = r.rows || [];
      renderGroups();
      main.innerHTML = '';
      main.appendChild(opsTable([
        { key: 'name', label: '主机名称' },
        { key: 'host', label: 'IP 地址', render: row => text(row.host) + ':' + text(row.port) },
        { key: 'username', label: '用户名' },
        { key: 'region', label: '地域' }, { key: 'room', label: '机房' },
        { key: 'biz_line', label: '业务线' },
        { key: 'app_level', label: '应用等级' },
        { key: 'vendor', label: '品牌' }, { key: 'usage', label: '用途' },
        { key: 'asset_age', label: '使用年限' },
        { key: 'cpu', label: 'CPU', render: row => pct(row.cpu) },
        { key: 'mem', label: '内存', render: row => pct(row.mem) },
        { key: 'disk', label: '磁盘', render: row => pct(row.disk) },
        { key: 'maintenance', label: '维保' },
        { key: 'monitored', label: '监控', render: row => opsBadge(row.monitored,
            row.monitored === '是' ? 'ok' : 'warn') },
        { key: '_op', label: '操作', render: row => {
          const wrap = el('span', 'ops-row-actions');
          wrap.appendChild(btn('编辑', () => {
            const h = S.hosts.find(x => x.id === row.id);
            if (h) editHost(h); else msg('主机连接信息未在当前会话中找到，请刷新后重试。');
          }));
          wrap.appendChild(btn('连接', () => {
            setView('terminal');
            const sel = byId('termHostSelect');
            if (sel) { sel.value = row.id; sel.dispatchEvent(new Event('change')); byId('btnTermConnect').click(); }
          }));
          wrap.appendChild(btn('删除', async () => {
            if (!confirm('确定删除主机「' + row.name + '」？')) return;
            await api('/api/hosts/' + encodeURIComponent(row.id), { method: 'DELETE' });
            msg('已删除主机「' + row.name + '」，数据已同步。');
            await loadState(false); await load();
          }));
          return wrap;
        } },
      ], filtered(), '尚未纳管主机。点「＋ 新增主机」或「⬆ 导入」录入；不会生成示例主机。'));
      msg('共 ' + filtered().length + ' / ' + r.total + ' 台 · ' + r.note);
    }
    kw.onchange = load; searchGroup.oninput = renderGroups;
    const file = byId('hostImportFile');
    file.onchange = async () => {
      const f = file.files && file.files[0];
      if (!f) return;
      try {
        const raw = await f.text();
        let hosts;
        if (raw.trim().startsWith('[')) hosts = JSON.parse(raw);
        else if (raw.trim().startsWith('{')) {
          const obj = JSON.parse(raw);
          hosts = Array.isArray(obj.hosts) ? obj.hosts : [obj];
        } else hosts = csvHosts(raw);
        const r = await jsonPost('/api/ops/hosts/import', { hosts: hosts });
        msg('导入完成：成功 ' + r.imported + ' / 跳过 ' + r.skipped +
          (r.errors && r.errors.length ? ' · ' + r.errors.map(e =>
            '#' + (e.index + 1) + ' ' + e.error).join('；') : '') + ' · 数据已同步。');
        await loadState(false); await load();
      } catch (e) { msg('导入失败：' + e.message); }
      file.value = '';
    };
    await load();
  }
  function csvHosts(raw) {
    const lines = raw.split(/\r?\n/).filter(x => x.trim());
    if (lines.length < 2) throw new Error('CSV 至少需要表头与一行数据');
    const heads = lines[0].split(',').map(x => x.trim());
    return lines.slice(1).map(line => {
      const cells = line.split(',');
      const obj = {};
      heads.forEach((h, i) => { obj[h] = (cells[i] || '').trim(); });
      return obj;
    });
  }

  /* ---------------- 机房管理 ---------------- */
  async function renderRooms() {
    const r = await api('/api/ops/rooms');
    const tools = byId('opsPageTools'), body = byId('opsPageBody');
    tools.appendChild(btn('＋ 新增主机', openNewHost, 'primary'));
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('机房管理')));
    const split = el('div', 'ops-split');
    const side = el('div', 'ops-side');
    side.appendChild(el('div', 'ops-side-title', '机房树 · 来自主机字段'));
    const list = el('div', 'ops-group-list');
    (r.rows || []).forEach(room => {
      const b = btn(room.name + ' (' + room.hosts + ')', () => {
        body.querySelectorAll('.ops-room-body').forEach(x => x.classList.add('hidden'));
        const target = body.querySelector('.ops-room-body[data-room="' + CSS.escape(room.name) + '"]');
        if (target) target.classList.remove('hidden');
      });
      list.appendChild(b);
    });
    side.appendChild(list); split.appendChild(side);
    const main = el('div', 'ops-split-main');
    (r.rows || []).forEach((room, i) => {
      const box = el('div', 'ops-room-body' + (i ? ' hidden' : ''));
      box.dataset.room = room.name;
      box.appendChild(el('h3', '', room.name + ' · ' + room.hosts + ' 台纳管主机'));
      box.appendChild(opsTable([
        { key: 'name', label: '主机' },
        { key: 'regions', label: '地域', render: row => (row.regions || []).join('、') },
        { key: 'monitored', label: '有快照', cls: 'num' },
        { key: 'names', label: '主机清单', render: row => (row.names || []).join('、') },
      ], [room], '该机房没有纳管主机。'));
      main.appendChild(box);
    });
    if (!(r.rows || []).length) main.appendChild(el('div', 'odb-note',
      '纳管主机尚未填写机房字段。请到「主机管理」编辑主机并填写机房。'));
    split.appendChild(main); body.appendChild(split);
    msg('共 ' + r.total + ' 个机房 · ' + r.note);
  }

  /* ---------------- 平台导航 ---------------- */
  async function renderPlatform() {
    let stats = { summary: {} };
    try { stats = await api('/api/ops/dashboard/detail?' + wbWindowQuery()); } catch (e) { /* 空态即可 */ }
    const tools = byId('opsPageTools');
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('平台导航')));
    const grid = el('div', 'plat-grid');
    const s = stats.summary || {};
    [
      ['资产管理', '9 个菜单入口', '▣', 'dashboard', true,
        '已纳管 ' + (s.managed || 0) + ' 台'],
      ['容器管理', '预留入口', '▤', '', false, '未接入，不展示示例数据'],
      ['DB 数据库', '7 个菜单入口', '▥', 'db_assets', true,
        '已录入 ' + (s.db_assets || 0) + ' 个'],
      ['日志中心', '7 个菜单入口', '▤', 'log_manage', true,
        '已配置 ' + (s.log_sources || 0) + ' 个日志源'],
      ['告警中心', '8 个菜单入口', '⚠', 'alert_center', true,
        '活跃 ' + (s.active_alerts || 0) + ' 条'],
      ['系统管理', '预留入口', '★', '', false, '未接入'],
    ].forEach(t => {
      const tile = el('div', 'plat-tile' + (t[4] ? ' key' : ''));
      tile.appendChild(el('div', 'plat-ico', t[2]));
      const info = el('div');
      info.appendChild(el('b', '', t[0]));
      info.appendChild(el('small', '', t[1] + ' · ' + t[5]));
      tile.appendChild(info);
      if (t[3]) tile.onclick = () => wbSelectPage(t[3]);
      else tile.title = '尚未接入；不做模拟数据展示';
      grid.appendChild(tile);
    });
    byId('opsPageBody').appendChild(grid);
    byId('opsPageBody').appendChild(el('div', 'plat-sub',
      '橙框入口为已接入模块；其余入口只占位，不生成演练数据。'));
    msg('平台导航按用户已录入/已采集的数据显示计数，不引用外部站点的示例数字。');
  }

  /* ---------------- 数据库管理 ---------------- */
  async function renderDbAssets() {
    // 数据库资产自带「新增资产」按钮，进入本页就预加载字段元数据；
    // 即使此处失败，opsOpenForm 也会按需再取一次并给出可见错误。
    try { await ensureCatalogMeta(); } catch (e) { msg('⚠ 台账字段读取失败：' + e.message); }
    const r = await api('/api/ops/db/assets');
    const tools = byId('opsPageTools');
    tools.appendChild(btn('＋ 新增资产', () => opsOpenForm('catalog', 'database', null), 'primary'));
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('数据库资产')));
    byId('opsPageBody').appendChild(opsTable([
      { key: 'name', label: '实例名称' }, { key: 'type', label: '类型' },
      { key: 'version', label: '版本' }, { key: 'address', label: 'IP-端口' },
      { key: 'env', label: '环境' }, { key: 'org', label: '业务系统' },
      { key: 'monitor', label: '监控接入', render: row =>
          row.monitor === '内置监控' ? '● 内置监控' : '○ 监控未配置' },
      { key: 'alive', label: '存活状态' }, { key: 'auth', label: '认证状态' },
      { key: '_op', label: '操作', render: row => {
        const wrap = el('span', 'ops-row-actions');
        // 复用资产台账弹窗；host_id 选项由 app.js 自动渲染为下拉
        wrap.appendChild(btn('编辑', () => {
          if (!OPS.catalogMeta) return msg('台账字段读取中，请稍后重试。');
          opsOpenForm('catalog', 'database', Object.assign({}, row, { category: 'database' }));
        }));
        wrap.appendChild(btn('删除', async () => {
          if (!confirm('删除数据库资产「' + row.name + '」？')) return;
          await api('/api/ops/catalog/' + encodeURIComponent(row.id), { method: 'DELETE' });
          await wbSelectPage('db_assets'); msg('已删除数据库资产。');
        }));
        return wrap;
      } },
    ], r.rows, '尚未录入数据库资产。点「＋ 新增资产」录入并关联纳管主机。'));
    msg('共 ' + r.total + ' 个 · ' + r.note);
  }

  const DB_KIND_PAGE = { db_query: 'query', db_audit: 'audit', db_slow: 'slow' };
  const DB_KIND_LABEL = { query: 'SQL 查询记录', audit: 'SQL 审核工单', slow: '慢日志样本' };
  async function renderDbRecords(page) {
    const kind = DB_KIND_PAGE[page];
    const r = await api('/api/ops/db/records?kind=' + kind);
    const tools = byId('opsPageTools');
    tools.appendChild(btn('＋ 新增记录', () =>
      opsOpenForm('db_' + kind, '', null), 'primary'));
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('数据库记录')));
    byId('opsPageBody').appendChild(opsTable([
      { key: 'title', label: '标题' },
      { key: 'host_id', label: '关联主机', render: row => {
        const h = (S.hosts || []).find(x => x.id === row.host_id);
        return h ? h.name : (row.host_id || '—');
      } },
      { key: 'status', label: '状态' },
      { key: 'sql', label: 'SQL / 样本' },
      { key: 'result', label: '结果 / 结论' },
      { key: 'updated_at', label: '更新时间' },
      { key: '_op', label: '操作', render: row => opsActions(
          () => opsOpenForm('db_' + kind, '', row), async () => {
            if (!confirm('删除记录「' + row.title + '」？')) return;
            await api('/api/ops/db/records/' + encodeURIComponent(row.id), { method: 'DELETE' });
            await wbSelectPage(page); msg('已删除记录。');
          }) },
    ], r.rows, '暂无' + DB_KIND_LABEL[kind] + '。所有记录都由用户输入或粘贴，系统不会伪造执行结果。'));
    msg('共 ' + r.total + ' 条 · ' + r.note);
  }

  async function renderDbMonitor() {
    const r = await api('/api/ops/db/monitor?' + wbWindowQuery());
    const tools = byId('opsPageTools');
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('数据库监控')));
    byId('opsPageBody').appendChild(opsTable([
      { key: 'name', label: '数据库资产' }, { key: 'host', label: '关联纳管主机' },
      { key: 'address', label: '地址' },
      { key: 'cpu', label: 'CPU', render: row => pct(row.cpu) },
      { key: 'mem', label: '内存', render: row => pct(row.mem) },
      { key: 'disk', label: '磁盘', render: row => pct(row.disk) },
      { key: 'net_errors', label: '网络错误', cls: 'num' },
      { key: 'gpu_count', label: 'GPU', cls: 'num' },
      { key: 'at', label: '采集时间' },
    ], r.rows, '没有关联了纳管主机且已有采集快照的数据库资产。'));
    msg('共 ' + r.total + ' 台 · ' + r.window_label + ' · ' + r.note);
  }

  async function renderDbAssistant() {
    const [assets, query, audit] = await Promise.all([
      api('/api/ops/db/assets'), api('/api/ops/db/records?kind=query'),
      api('/api/ops/db/records?kind=audit')]);
    const box = el('div', 'odb-card');
    box.appendChild(el('h3', '', '数据库助手 · 基于本机已登记信息的只读分析'));
    box.appendChild(el('div', 'odb-note',
      '助手不会编造库内数据。请先在「数据资产」录入数据库并关联纳管主机，'
      + '或先在「SQL 查询 / SQL 审核」录入你自己的查询与结论。'));
    const q = el('textarea'); q.rows = 6; q.className = 'db-assistant-input';
    q.placeholder = '例如：结合已登记的 MySQL 资产与采集值，帮我列出需要人工核对的项';
    box.appendChild(q);
    box.appendChild(btn('✦ 交给 AI 分析', () => {
      const metrics = (assets.rows || []).map(a =>
        a.name + '(' + a.engine + ')：CPU=' + pct(a.cpu) + ' 内存=' + pct(a.mem) +
        ' 磁盘=' + pct(a.disk) + ' 采集时间=' + text(a.at)).join('\n');
      const context = '【用户录入的数据库资产】\n' + (metrics || '无') +
        '\n【用户录入的查询记录】' + (query.total || 0) + ' 条' +
        '\n【用户录入的审核工单】' + (audit.total || 0) + ' 条' +
        '\n【问题】' + (q.value || '请给出核对清单');
      wbAskAi(context);
    }, 'primary'));
    byId('opsPageBody').appendChild(box);
    msg('资产 ' + assets.total + ' 个 · 查询记录 ' + query.total + ' 条 · 审核工单 ' + audit.total + ' 条。');
  }

  /* ---------------- 告警中心 ---------------- */
  async function renderAlertCenter() {
    const r = await api('/api/ops/alert/center?' + wbWindowQuery());
    const tools = byId('opsPageTools');
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('告警中心')));
    const s = r.summary || {};
    const kpis = el('div', 'odb-kpis');
    [['活跃告警', s.active, '条', 'pink'], ['严重', s.critical, '条', ''],
     ['警告', s.warning, '条', ''], ['今日告警', s.today, '条', 'amber']]
      .forEach(k => {
        const c = el('div', 'odb-kpi ' + k[3]);
        c.appendChild(el('small', '', k[0]));
        const b = el('b', '', String(k[1])); b.appendChild(el('i', '', k[2]));
        c.appendChild(b); kpis.appendChild(c);
      });
    byId('opsPageBody').appendChild(kpis);
    const grid = el('div', 'odb-grid');
    const g1 = card('告警群组 Top5', r.generated_at);
    g1.appendChild(bars(r.top_groups || [], 'count', 'name', true)); grid.appendChild(g1);
    const g2 = card('告警名称 Top5');
    g2.appendChild(bars(r.top_names || [], 'count', 'name', true)); grid.appendChild(g2);
    const g3 = card('告警级别分布');
    g3.appendChild(bars(r.distribution || [], 'count', 'name')); grid.appendChild(g3);
    const g4 = card('最新活跃告警');
    g4.appendChild(opsTable([
      { key: 'time', label: '时间' }, { key: 'level', label: '级别' },
      { key: 'host', label: '主机' }, { key: 'message', label: '内容' },
    ], r.recent || [], '所选时间窗内没有采集告警。'));
    grid.appendChild(g4);
    byId('opsPageBody').appendChild(grid);
    msg('统计于 ' + r.generated_at + ' · ' + r.note);
  }

  /* ---------------- 域名告警：用户录入 + 纳管主机只读探测 ---------------- */
  async function renderDomainAlert() {
    const r = await api('/api/ops/domains');
    const tools = byId('opsPageTools');
    tools.appendChild(btn('＋ 新增域名', () => opsOpenForm('domain', '', null), 'primary'));
    tools.appendChild(btn('⇩ 检测全部', async () => {
      msg('正在从各域名指定的纳管主机执行只读探测…');
      try {
        const res = await jsonPost('/api/ops/domains/check', {});
        msg('探测完成：' + res.success + '/' + res.total + ' 成功' +
          (res.failed ? '，失败：' + res.results.filter(x => !x.ok).map(x =>
            x.name + ': ' + x.error).join('；') : '') + ' · 数据已同步。');
        await wbSelectPage('domain_alert');
      } catch (e) { msg('探测失败：' + e.message); }
    }));
    tools.appendChild(btn('⟳ 全局刷新', () => globalRefresh('域名告警')));
    byId('opsPageBody').appendChild(opsTable([
      { key: 'name', label: '域名' }, { key: 'tags', label: '标签' },
      { key: 'host_name', label: '探测主机' },
      { key: 'status', label: '状态', render: row => opsBadge(row.status || '未探测',
          row.status === '正常' ? 'ok' : row.status ? 'hi' : '') },
      { key: 'http_status', label: '状态码' },
      { key: 'latency_ms', label: '响应(ms)', cls: 'num' },
      { key: 'ssl_expire', label: 'SSL 证书' },
      { key: 'checked_at', label: '最近检查' },
      { key: 'enabled', label: '启用', render: row => row.enabled ? '是' : '否' },
      { key: '_op', label: '操作', render: row => {
        const wrap = el('span', 'ops-row-actions');
        wrap.appendChild(btn('检测', async () => {
          msg('正在检测 ' + row.name + '…');
          const res = await jsonPost('/api/ops/domains/check', { id: row.id });
          msg((res.results || []).map(x => x.ok
            ? x.name + ' 状态 ' + x.http_status + ' · ' + x.latency_ms + 'ms'
            : x.name + ' 失败：' + x.error).join('；') + ' · 数据已同步。');
          await wbSelectPage('domain_alert');
        }));
        wrap.appendChild(btn('编辑', () => opsOpenForm('domain', '', row)));
        wrap.appendChild(btn('删除', async () => {
          if (!confirm('删除域名「' + row.name + '」？')) return;
          await api('/api/ops/domains/' + encodeURIComponent(row.id), { method: 'DELETE' });
          await wbSelectPage('domain_alert'); msg('已删除域名。');
        }));
        return wrap;
      } },
    ], r.rows, '尚未配置域名。域名与探测主机都由用户输入；未探测前没有状态，不做模拟检查。'));
    msg('共 ' + r.total + ' 个域名 · ' + r.note);
  }

  /* ---------------- 通用表单：数据库记录 / 域名 ---------------- */
  function openForm(kind, row) {
    OPS.form = { kind, row, defs: [], inputs: {} };
    const box = byId('opsRecordFields'); box.innerHTML = '';
    byId('opsRecordMsg').textContent = '';
    const defs = [];
    if (kind.startsWith('db_')) {
      const k = kind.slice(3);
      byId('opsRecordTitle').textContent = (row ? '编辑' : '新增') + DB_KIND_LABEL[k];
      byId('opsRecordTip').textContent = '记录由你填写或粘贴，系统不执行 SQL、不伪造结果。';
      defs.push({ key: 'title', label: '标题 *', required: true });
      defs.push({ key: 'host_id', label: '关联纳管主机', type: 'select',
        options: [{ value: '', label: '不关联' }].concat((S.hosts || []).map(h =>
          ({ value: h.id, label: h.name }))) });
      defs.push({ key: 'status', label: '状态 / 风险' });
      defs.push({ key: 'sql', label: k === 'slow' ? '慢日志样本' : 'SQL 语句',
        type: 'textarea', full: true });
      defs.push({ key: 'result', label: k === 'audit' ? '审核结论' : '你看到的结果',
        type: 'textarea', full: true });
      defs.push({ key: 'note', label: '备注', full: true });
    } else if (kind === 'domain') {
      byId('opsRecordTitle').textContent = (row ? '编辑' : '新增') + '域名告警';
      byId('opsRecordTip').textContent = '域名与探测主机由你填写；检测只读，在所选纳管主机上执行。';
      defs.push({ key: 'name', label: '域名 *（不含协议）', required: true,
        placeholder: 'example.com' });
      defs.push({ key: 'host_id', label: '探测主机 *', required: true, type: 'select',
        options: (S.hosts || []).map(h => ({ value: h.id, label: h.name })) });
      defs.push({ key: 'tags', label: '标签' });
      defs.push({ key: 'enabled', label: '启用', type: 'checkbox', value: true });
      defs.push({ key: 'note', label: '备注', full: true });
    }
    const inputs = {};
    defs.forEach(d => {
      const lab = el('label', d.full ? 'full' : '');
      lab.appendChild(el('span', '', d.label));
      let input;
      if (d.type === 'select') {
        input = el('select');
        (d.options || []).forEach(o => {
          const opt = el('option', '', o.label); opt.value = o.value; input.appendChild(opt);
        });
      } else if (d.type === 'checkbox') {
        input = el('input'); input.type = 'checkbox';
      } else if (d.type === 'textarea') {
        input = el('textarea'); input.rows = 3;
      } else {
        input = el('input'); input.type = 'text';
        if (d.placeholder) input.placeholder = d.placeholder;
      }
      const value = row && row[d.key] !== undefined ? row[d.key] : d.value;
      if (d.type === 'checkbox') input.checked = !!value;
      else input.value = value === undefined || value === null ? '' : String(value);
      inputs[d.key] = input; lab.appendChild(input); box.appendChild(lab);
    });
    OPS.form = { kind, row, defs, inputs };
    openModal('modalOpsRecord');
  }

  async function saveForm(f, payload) {
    if (f.kind.startsWith('db_')) {
      const kind = f.kind.slice(3);
      const body = Object.assign({}, payload, { kind: kind });
      if (f.row) body.id = f.row.id;
      return jsonPost('/api/ops/db/records', body);
    }
    if (f.kind === 'domain') {
      const body = Object.assign({}, payload);
      if (f.row) body.id = f.row.id;
      return jsonPost('/api/ops/domains', body);
    }
    return { ok: false, message: '未知表单类型' };
  }

  /* ---------------- 运维大屏 ---------------- */
  function openScreen() {
    lastView = byId('workbenchView').classList.contains('hidden') ? 'terminal' : 'workbench';
    screenOpen = true;
    byId('screenView').classList.remove('hidden');
    byId('termPanel').classList.add('hidden');
    byId('workbenchView').classList.add('hidden');
    tickClock(); renderScreen();
    if (screenTimer) clearInterval(screenTimer);
    screenTimer = setInterval(tickClock, 1000);
  }
  function closeScreen() {
    screenOpen = false;
    if (document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
    byId('screenView').classList.add('hidden');
    if (screenTimer) { clearInterval(screenTimer); screenTimer = null; }
    setView(lastView, false);
  }
  function tickClock() {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    byId('screenTime').textContent = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    byId('screenDate').textContent = d.getFullYear() + '/' + p(d.getMonth() + 1) + '/' + p(d.getDate());
  }
  function sBar(label, value, max, warn, unit) {
    const line = el('div', 'screen-brow' + (warn ? ' warn' : ''));
    line.appendChild(el('label', '', label));
    const t = el('div', 'track'), f = el('span');
    f.style.width = Math.max(1, Math.round((Number(value) || 0) / Math.max(1, max) * 100)) + '%';
    t.appendChild(f); line.appendChild(t);
    const b = el('b', '', String(value));
    if (unit) b.appendChild(el('i', 'screen-unit', unit));
    line.appendChild(b);
    return line;
  }
  async function renderScreen() {
    if (!screenOpen) return;
    const box = byId('screenMsg');
    box.textContent = '正在读取纳管主机快照与真实告警…';
    try {
      const sw = byId('screenWindow');
      const windowParam = sw ? 'window=' + encodeURIComponent(sw.value || '1h') : wbWindowQuery();
      const d = await api('/api/ops/screen?' + windowParam);
      box.textContent = '窗口：' + d.window_label + ' · 生成于 ' + d.generated_at;
      const cov = (d.kpis || []).find(k => k.label === '监控覆盖率');
      const ring = document.querySelector('.screen-ring');
      if (ring) {
        ring.style.setProperty('--deg', Math.min(100, Math.max(0, Number(cov ? cov.value : 0) * 3.6)) + 'deg');
        byId('screenCoverage').textContent = (cov ? cov.value : 0) + '%';
      }
      const kp = byId('screenKpis'); kp.innerHTML = '';
      (d.kpis || []).filter(k => k.label !== '监控覆盖率').forEach(k => {
        const c = el('div', 'screen-kpi ' + (k.tone === 'danger' ? 'hi' : k.tone === 'success' ? 'ok' : ''));
        c.appendChild(el('small', '', k.label));
        const b = el('b', '', String(k.value)); b.appendChild(el('i', '', k.unit));
        c.appendChild(b); kp.appendChild(c);
      });
      const al = byId('screenAlerts'); al.innerHTML = '';
      (d.alerts || []).forEach(a => {
        const line = el('div', 'screen-alert-line' + (a.level === '严重' ? ' hi' : ''));
        line.appendChild(el('span', 'st'));
        line.appendChild(el('span', 'grow', a.host + ' · ' + a.message));
        line.appendChild(el('time', '', (a.time || '').slice(11, 16)));
        al.appendChild(line);
      });
      if (!(d.alerts || []).length) al.appendChild(el('div', 'screen-empty', '该时间窗内没有采集告警。'));

      const tr = byId('screenTrend'); tr.innerHTML = '';
      const trend = d.alert_trend || [];
      const tmax = Math.max(1, ...trend.map(x => x.count));
      trend.forEach(x => {
        const b = el('div', 'bar');
        const i = el('i'); i.style.height = Math.max(2, x.count / tmax * 90) + '%';
        b.appendChild(i); b.appendChild(el('small', '', x.label)); tr.appendChild(b);
      });
      if (!trend.length) tr.appendChild(el('div', 'screen-empty', '时间窗内没有告警记录，趋势为空。'));

      const ad = byId('screenAlertDist'); ad.innerHTML = '';
      const levels = {};
      (d.alerts || []).forEach(a => { levels[a.level || '未分级'] = (levels[a.level || '未分级'] || 0) + 1; });
      Object.keys(levels).forEach(k => ad.appendChild(sBar(k, levels[k], (d.alerts || []).length, k === '严重')));
      if (!Object.keys(levels).length) ad.appendChild(el('div', 'screen-empty', '无告警分布数据。'));

      const as = byId('screenAssets'); as.innerHTML = '';
      const amax = Math.max(1, ...(d.assets || []).map(x => x.value));
      (d.assets || []).forEach(x => as.appendChild(sBar(x.label, x.value, amax)));
      const rg = byId('screenRegions'); rg.innerHTML = '';
      (d.regions || []).forEach(x => rg.appendChild(sBar(x.name, x.hosts,
        Math.max(1, ...(d.regions || []).map(y => y.hosts)))));

      const res = byId('screenResources'); res.innerHTML = '';
      (d.resources || []).forEach(m => {
        const r = el('div', 'screen-res');
        r.appendChild(el('b', '', m.host));
        r.appendChild(el('small', '', '采集于 ' + text(m.at)));
        r.appendChild(sBar('CPU', m.cpu === null ? 0 : Math.round(m.cpu), 100, (m.cpu || 0) >= 85, '%'));
        r.appendChild(sBar('内存', m.mem === null ? 0 : Math.round(m.mem), 100, (m.mem || 0) >= 85, '%'));
        r.appendChild(sBar('磁盘', m.disk === null ? 0 : Math.round(m.disk), 100, (m.disk || 0) >= 85, '%'));
        res.appendChild(r);
      });
      if (!(d.resources || []).length) res.appendChild(el('div', 'screen-empty',
        '尚未采集纳管主机，大屏不展示任何模拟指标。'));
      const metricsBox = byId('screenMetrics'); metricsBox.innerHTML = '';
      [['CPU 最高', 'cpu'], ['内存最高', 'mem'], ['磁盘最高', 'disk']].forEach(x => {
        const top = (d.resources || []).filter(m => m[x[1]] !== null && m[x[1]] !== undefined)
          .sort((a, b) => b[x[1]] - a[x[1]]).slice(0, 3);
        const group = el('div', 'screen-res');
        group.appendChild(el('b', '', x[0]));
        top.forEach((m, i) => group.appendChild(sBar(
          (i + 1) + '. ' + m.host, Math.round(m[x[1]]), 100, m[x[1]] >= 85, '%')));
        if (!top.length) group.appendChild(el('div', 'screen-empty', '暂无采集值。'));
        metricsBox.appendChild(group);
      });
      byId('screenNote').textContent = d.note;
    } catch (e) {
      box.textContent = '大屏读取失败：' + e.message;
    }
  }

  /* ---------------- 刷新与分派 ---------------- */
  async function refreshAll() {
    await wbLoad();
    if (!byId('wbPageGeneric').classList.contains('hidden') && OPS.page !== 'dashboard') {
      opsSetHead(OPS.page);
      await renderOpsPage(OPS.page);
    }
    if (screenOpen) await renderScreen();
  }
  async function afterHostSaved() {
    await loadState(false);
    if (!byId('workbenchView').classList.contains('hidden')) {
      if (OPS.page === 'hosts') {
        opsSetHead('hosts'); await renderOpsPage('hosts');
      } else await refreshAll();
    }
  }
  async function render(page) {
    switch (page) {
      case 'hosts': return renderHosts();
      case 'rooms': return renderRooms();
      case 'platform': return renderPlatform();
      case 'db_assets': return renderDbAssets();
      case 'db_query': case 'db_audit': case 'db_slow': return renderDbRecords(page);
      case 'db_monitor': return renderDbMonitor();
      case 'db_assistant': return renderDbAssistant();
      case 'alert_center': return renderAlertCenter();
      case 'domain_alert': return renderDomainAlert();
      default: return;
    }
  }

  function toggleScreenFull() {
    const box = byId('screenView');
    if (!box) return;
    if (!document.fullscreenElement) {
      if (box.requestFullscreen) box.requestFullscreen().catch(() => {});
    } else if (document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
  }

  window.OpsExt = {
    OWNED_PAGES, render, refreshAll, afterHostSaved,
    openScreen, closeScreen, renderScreen, openForm, saveForm, toggleScreenFull
  };

  if (byId('btnScreen')) byId('btnScreen').onclick = openScreen;
  if (byId('btnScreenClose')) byId('btnScreenClose').onclick = closeScreen;
  if (byId('btnScreenFull')) byId('btnScreenFull').onclick = toggleScreenFull;
  if (byId('screenWindow')) byId('screenWindow').onchange = () => {
    if (screenOpen) renderScreen();
  };
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape' && screenOpen) closeScreen();
  });
})();
