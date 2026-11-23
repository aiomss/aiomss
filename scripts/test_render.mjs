/* L1：前端富文本渲染纯函数单测（无需浏览器，Node 直接跑）
 * 运行： node scripts/test_render.mjs
 * 覆盖用户截图中的：Markdown 星号残留、代码围栏、孤立分隔线、重复标题、
 * 空标题（7. 脚本化批量）、半句结论（该机属于）、流式中间态、中文分节。
 */
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const R = require(path.join(here, '../frontend/assets/render.js'));

let PASS = 0, FAIL = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { PASS++; console.log('[OK]   ' + name); }
  else { FAIL++; failures.push(name); console.log('[FAIL] ' + name + (detail ? '  → ' + detail : '')); }
}

/* ---------- 1. 用户图7：标题行里的 ** 加粗残留 ---------- */
{
  const s = '1. **打开哪些文件、以什么布局打开**（分屏、标签页、diff）';
  const out = R.cleanText(s);
  check('标题行不残留 ** 星号', !out.includes('*'), out);
  check('标题行保留中文内容', out.includes('打开哪些文件') && out.includes('分屏'), out);
  check('数字列表行被识别为编号条目（不是标题）',
    R.cleanLines(s).lines[0].type === 'num', JSON.stringify(R.cleanLines(s).lines));
}

/* ---------- 2. 代码围栏 ---------- */
{
  const s = '四、常用组合速查\n```bash\nvim -R +100 file\nvim -d a.conf b.conf\n```\n后续说明';
  const r = R.cleanLines(s);
  const txt = R.cleanText(s);
  check('围栏标记 ``` 不出现在输出', !txt.includes('```'), txt);
  check('围栏内命令保留为代码块', r.lines.some(x => x.type === 'code' && x.text.includes('vim -R +100 file')),
    JSON.stringify(r.lines));
  check('围栏内第二行命令保留', txt.includes('vim -d a.conf b.conf'), txt);
  check('围栏块后的正文保留', txt.includes('后续说明'), txt);
  check('代码块内 ** 不被当成 Markdown 清理',
    R.cleanText('```\n**raw**\n```').includes('**raw**'), R.cleanText('```\n**raw**\n```'));
}

/* ---------- 3. 水平线 / 孤立破折号 ---------- */
{
  ['---', '***', '___', '--', '- - -', '-----'].forEach(x => {
    const txt = R.cleanText('前文说明。\n' + x + '\n后文说明。');
    check('分隔线被丢弃: ' + x, !txt.split('\n').includes(x) && txt.includes('后文说明'), txt);
  });
}

/* ---------- 4. 重复标题（图7 出现两次「四、常用组合速查」） ---------- */
{
  const s = '四、常用组合速查\n```bash\nvim -R file\n```\n四、常用组合速查\n1. 只读查看';
  const r = R.cleanLines(s, { final: false });
  const n = r.lines.filter(x => x.type === 'heading' && x.text.includes('常用组合速查')).length;
  check('相邻重复标题只保留一次', n === 1, '实际 ' + n + ' 次');
}

/* ---------- 5. 末尾空标题（图7「7. 脚本化批量」只有标题） ---------- */
{
  const s = '一、核心作用\nVim 启动参数决定三件事，下面逐条说明具体内容。\n'
    + '二、关键参数表\n-R 只读模式，+N 跳转到第 N 行，参数说明完整。\n'
    + '四、常用组合速查\n1. 只读查看日志并用 /pattern 搜索定位\n'
    + '7. 脚本化批量';
  const r = R.cleanLines(s, { final: true });
  check('final 时末尾空标题被丢弃', !r.lines.some(x => x.text.includes('脚本化批量')),
    JSON.stringify(r.lines.slice(-3)));
  check('final 时末尾空标题触发截断提示', r.truncated === true, 'truncated=' + r.truncated);
  const mid = R.cleanLines(s, { final: false });
  check('流式中间态不误删末尾标题', mid.lines.some(x => x.text.includes('脚本化批量')),
    JSON.stringify(mid.lines.slice(-2)));
}

/* ---------- 6. 半句结论（图2「该机属于」） ---------- */
{
  const s = '【现象】主机 203.0.113.10 为一台单系统盘的小规格实例，当前共运行约 207 个进程。\n'
    + '【证据】df -hT 显示 /dev/vda1 40G 已用 15G，可用 24G。\n'
    + '【根因/结论】\n· 该机属于';
  const r = R.cleanLines(s, { final: true });
  const txt = R.cleanText(s, { final: true });
  check('长文末尾半句被移除', !txt.includes('该机属于'), txt);
  check('移除半句后保留完整分节', txt.includes('【现象】') && txt.includes('【证据】'), txt);
  check('半句被标记 truncated', r.truncated === true, 'truncated=' + r.truncated);
  check('looksTruncatedLine(该机属于) = true', R.looksTruncatedLine('该机属于') === true);
  check('完整句不当成截断', R.looksTruncatedLine('该机属于一台单系统盘的小规格实例。') === false);
  check('命令行不当成半句', R.looksTruncatedLine('/usr/bin/vim -R file') === false);
  check('短答案末尾短句不误删', R.cleanText('一、作用\n只读打开。\n无。', { final: true }).includes('无。'));
}

/* ---------- 7. 中文分节与列表 ---------- */
{
  const s = '【现象】 磁盘使用率偏高\n【证据】\n· df -h 显示 / 已用 90%\n'
    + '· du -sh /var/log 显示 12G\n【建议】先归档日志，不要直接删除。';
  const r = R.cleanLines(s, { final: true });
  check('【】分节解析为 section', r.lines.filter(x => x.type === 'section').length === 3,
    JSON.stringify(r.lines));
  check('· 列表解析为 bullet', r.lines.filter(x => x.type === 'bullet').length === 2,
    JSON.stringify(r.lines));
  check('列表前缀被去掉', !R.cleanText(s).includes('· df'), R.cleanText(s));
  check('section 标签保留', R.cleanText(s).includes('【现象】'), R.cleanText(s));
}

/* ---------- 8. Markdown 标题 / 行内代码 / 引用 ---------- */
{
  check('## 标题去掉 #', R.cleanText('## 关键参数表').includes('关键参数表') &&
    !R.cleanText('## 关键参数表').includes('#'));
  check('行内代码反引号被清理', !R.cleanText('使用 `vim -R` 打开').includes('`'),
    R.cleanText('使用 `vim -R` 打开'));
  check('引用符 > 被清理', !R.cleanText('> 注意：只读模式').includes('> '),
    R.cleanText('> 注意：只读模式'));
  check('数字列表被识别', R.cleanLines('1. 只读打开\n2. 跳到第 100 行')
    .lines.every(x => x.type === 'heading' || x.type === 'bullet' || x.type === 'num'));
  check('CRLF 换行正常', R.cleanText('一、作用\r\n只读打开。').includes('只读打开。'));
  check('空输入不抛异常', Array.isArray(R.cleanLines('').lines) && R.cleanLines('').lines.length === 0);
}

/* ---------- 9. 幂等与真实样本综合 ---------- */
{
  const sample = [
    'Vim 启动参数决定三件事：',
    '1. **打开哪些文件、以什么布局打开**（分屏、标签页、diff）',
    '2. **打开后定位到哪里**（行号 / 搜索命中 / tag / 上次编辑位置）',
    '-- ',
    '> 注意 view +100 file 与 vim -R +100 file 完全等价。',
    '```bash',
    'vim -R +100 /var/log/app.log',
    '```',
    '四、常用组合速查',
    '```bash',
    'vim -R +100 /var/log/app.log',
    '```',
    '四、常用组合速查',
    '1. 只读看日志并定位搜索',
    '7. 脚本化批量',
  ].join('\n');
  const txt = R.cleanText(sample, { final: true });
  check('综合样本无 ** 残留', !txt.includes('**'), txt.slice(0, 200));
  check('综合样本无 ``` 围栏', !txt.includes('```'));
  check('综合样本无孤立 -- 行', !txt.split('\n').includes('--'), txt.slice(0, 200));
  check('综合样本无空标题残留', !txt.includes('脚本化批量'));
  check('综合样本重复标题只留一次',
    (txt.match(/常用组合速查/g) || []).length === 1, txt);
  check('综合样本保留命令示例', txt.includes('vim -R +100 /var/log/app.log'));
  const once = R.cleanText(sample, { final: false });
  check('cleanText 幂等（流式态）', R.cleanText(once, { final: false }) === once);
  check('CRLF 输入被规范化', !R.cleanText('一、作用\r\n只读打开。\r\n').includes('\r'));
}

/* ---------- 10. DOM 渲染（用极简 fake DOM，验证节点类型） ---------- */
{
  const nodes = [];
  const fakeDoc = {
    createElement: (tag) => ({
      tagName: tag.toUpperCase(), className: '', childNodes: [], textContent: '',
      appendChild(c) { this.childNodes.push(c); return c; },
    }),
    createTextNode: (t) => ({ nodeType: 3, textContent: t }),
  };
  const container = {
    ownerDocument: fakeDoc, childNodes: [], innerHTML: '',
    appendChild(n) { this.childNodes.push(n); nodes.push(n); return n; },
  };
  const r = R.renderRichText(container,
    '## 关键参数\n使用 `vim -R` 与 **只读模式** 打开。\n```bash\nvim -R +100 file\n```\n【建议】 复核。',
    { final: true });
  const flat = [];
  const walk = (n) => { flat.push(n); (n.childNodes || []).forEach(walk); };
  nodes.forEach(walk);
  check('DOM 渲染生成 strong 节点', flat.some(n => n.tagName === 'STRONG'));
  check('DOM 渲染生成 code 节点', flat.some(n => n.tagName === 'CODE'));
  check('DOM 渲染生成 pre 代码块', flat.some(n => n.tagName === 'PRE'));
  check('DOM 渲染文本不含 **', !flat.some(n => (n.textContent || '').includes('**')));
  check('DOM 渲染返回结构含 truncated 字段', typeof r.truncated === 'boolean');
}

/* ---------- 11. 回归：结论不能被清成空白（final 清理误删最后一行） ---------- */
{
  // 只有一行小节标题 / 只有一个标题的结论，绝不能被清成空
  for (const s of ['一、结论', '七、总结与建议', '【建议】', '结论']) {
    const out = R.cleanText(s, { final: true });
    check('结论不空白: ' + JSON.stringify(s), String(out).trim().length > 0, '=> ' + JSON.stringify(out));
  }
  // 多行结论的最后一行即便像半句，也不能把全部内容删空
  const multi = '【现象】主机磁盘使用率偏高。\n【建议】先归档历史日志';
  const out2 = R.cleanText(multi, { final: true });
  check('多行结论清理后仍有正文', out2.trim().length > 0 && out2.includes('磁盘使用率'), JSON.stringify(out2));
}

console.log('\n结果: %d 通过 / %d 失败', PASS, FAIL);
if (FAIL) { console.log('失败项: ' + failures.join(' | ')); process.exit(1); }
