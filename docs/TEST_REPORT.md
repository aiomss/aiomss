# AI 运维控制台 · v1.2 全量测试报告

> 生成时间：**2026-11-28 10:39:15**；由 `scripts/test_all.py` 依据本次真实子进程输出自动生成。
> 所有数字均取自实跑 stdout，无占位、无编造。

## 一、一键回归与运行命令

```bash
cd ai-ops-console
python scripts/test_all.py          # 一键跑完 11 层并生成本报告
```

分层独立运行：

```bash
node scripts/test_render.mjs        # L1  前端富文本纯函数
python scripts/selftest.py         # L2  Python 逻辑（勿改，后端工程师维护）
python scripts/httpcheck.py        # L3+L4 进程内 HTTP / 前端契约 / Agent SSE 契约
python scripts/test_static.py      # L5  启动脚本字节级 / 配置结构 / 目录归类
node scripts/test_ui.mjs           # L6  Chrome CDP 端到端 + 两张截图
python scripts/test_terminal_capture.py  # L7 终端 VT 流 → 1:1 逐行纯文本
python scripts/test_audit_sessions.py    # L8 会话窗口 / 实录 / 原始流 / AI 上下文
```

| 层 | 名称 | 脚本 | 命令 |
| --- | --- | --- | --- |
| L1 | 前端富文本纯函数单测 | `scripts/test_render.mjs` | `node scripts/test_render.mjs` |
| L2 | Python 逻辑自测 | `scripts/selftest.py` | `python scripts/selftest.py` |
| L3 | HTTP 自检（原有 1-5 节） | `scripts/httpcheck.py` | `python scripts/httpcheck.py` |
| L4 | HTTP 契约（新增 6-7 节） | `scripts/httpcheck.py` | `python scripts/httpcheck.py` |
| L5 | 静态/字节/配置验收 | `scripts/test_static.py` | `python scripts/test_static.py` |
| L6 | 浏览器 UI CDP 端到端 | `scripts/test_ui.mjs` | `node scripts/test_ui.mjs` |
| L7 | 终端 1:1 屏幕还原单测 | `scripts/test_terminal_capture.py` | `python scripts/test_terminal_capture.py` |
| L8 | 会话窗口审计单测 | `scripts/test_audit_sessions.py` | `python scripts/test_audit_sessions.py` |
| L9 | 运维工作台纳管主机口径单测 | `scripts/test_workbench.py` | `python scripts/test_workbench.py` |
| L10 | 交互式服务控制台单测 | `scripts/test_console.py` | `python scripts/test_console.py` |
| L11 | 资产 / 监控告警 / 日志中心单测 | `scripts/test_ops_center.py` | `python scripts/test_ops_center.py` |

> L3 与 L4 来自同一次 `httpcheck.py` 运行：L3 为原有第 1–5 节（26 项断言，未删改），
> L4 为新增第 6 节「v1.2 前端契约」与第 7 节「Agent SSE 契约」（按 `小节结果:` 拆分统计）。

## 二、各层结果汇总（真实运行输出）

| 层 | 名称 | 通过 | 失败 | SKIP | 耗时(s) | 退出码 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| L1 | 前端富文本纯函数单测 | 53 | 0 | 0 | 0.31 | 0 |
| L2 | Python 逻辑自测 | 100 | 0 | 0 | 5.20 | 0 |
| L3 | HTTP 自检（原有 1-5 节） | 26 | 0 | 0 | 2.88 | 0 |
| L4 | HTTP 契约（新增 6-7 节） | 58 | 0 | 0 | 2.88 | 0 |
| L5 | 静态/字节/配置验收 | 114 | 0 | 0 | 0.26 | 0 |
| L6 | 浏览器 UI CDP 端到端 | 117 | 0 | 0 | 23.07 | 0 |
| L7 | 终端 1:1 屏幕还原单测 | 15 | 0 | 0 | 0.39 | 0 |
| L8 | 会话窗口审计单测 | 49 | 0 | 0 | 2.67 | 0 |
| L9 | 运维工作台纳管主机口径单测 | 73 | 0 | 0 | 0.53 | 0 |
| L10 | 交互式服务控制台单测 | 52 | 0 | 0 | 0.63 | 0 |
| L11 | 资产 / 监控告警 / 日志中心单测 | 132 | 0 | 0 | 0.93 | 0 |
| **总计** | **11 层** | **789** | **0** | **0 层** | — | — |

**实际执行用例总数：789（通过 789 / 失败 0）。**

> `selftest.py` 由后端工程师并行修复：本次实跑 **100 通过 / 0 失败**（exit=0），已全过，未触发重跑；本 runner 未修改该文件。

## 三、6 个截图问题 × 11 个测试层 覆盖矩阵

每格格式：**状态** / 具体测试用例名 / 证据行（取自该层真实 stdout）。
`SKIP` 表示该层无对应测试用例或整层因环境缺失跳过，绝不计为 PASS。

| 截图问题 \ 层 | L1 前端富文本纯函数单测 | L2 Python 逻辑自测 | L3 HTTP 自检（原有 1-5 节） | L4 HTTP 契约（新增 6-7 节） | L5 静态/字节/配置验收 | L6 浏览器 UI CDP 端到端 | L7 终端 1:1 屏幕还原单测 | L8 会话窗口审计单测 | L9 运维工作台纳管主机口径单测 | L10 交互式服务控制台单测 | L11 资产 / 监控告警 / 日志中心单测 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 问题1 登录后取消自动连接，改为每台主机手动点「连接」 | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **PASS**<br>init() 起至文件尾不含 openTerminal( （<br>`[OK] init() 起至文件尾不含 openTerminal( （登录不自动连接）` | **PASS**<br>问题1: app.js init() 起至文件尾不含 ope<br>`[OK] 问题1: app.js init() 起至文件尾不含 openTerminal(` | **PASS**<br>登录/初始化未发出 /api/terminal/open（不<br>`[OK]   登录/初始化未发出 /api/terminal/open（不自动连接）` | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— |
| 问题2 只读命令误拦 / 自动修复提示外泄 / 结论只剩半句 | **PASS**<br>长文末尾半句被移除<br>`[OK]   长文末尾半句被移除` | **PASS**<br>只读: cat /etc/passwd<br>`[OK] 只读: cat /etc/passwd` | **SKIP**<br>该层无对应用例<br>— | **PASS**<br>问答流不向用户弹「自动修复」提示<br>`[OK] 问答流不向用户弹「自动修复」提示` | **PASS**<br>问题2: agent.py 所有 notice 标题均不含「<br>`[OK] 问题2: agent.py 所有 notice 标题均不含「自动修复」` | **PASS**<br>综合脏样本：结尾半句「该机属于」被移除<br>`[OK]   综合脏样本：结尾半句「该机属于」被移除` | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **PASS**<br>只执行 tail 只读命令<br>`[OK] 只执行 tail 只读命令` |
| 问题3 顶栏改名「API修改」、弹窗标题同步 | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **PASS**<br>首页含「API修改」按钮<br>`[OK] 首页含「API修改」按钮` | **PASS**<br>问题3: index.html 顶栏含「API修改」按钮<br>`[OK] 问题3: index.html 顶栏含「API修改」按钮` | **PASS**<br>顶栏按钮文字为「API修改」（不含「设置」）<br>`[OK]   顶栏按钮文字为「API修改」（不含「设置」）` | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— |
| 问题4 删除终端右上角 hint | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **PASS**<br>首页不含终端提示「Tab 补全 · vim / top 可用<br>`[OK] 首页不含终端提示「Tab 补全 · vim / top 可用 · Ctrl+C 中断」` | **PASS**<br>问题4: index.html 不含终端 hint<br>`[OK] 问题4: index.html 不含终端 hint` | **PASS**<br>页面不含终端 hint「Tab 补全 · vim / top<br>`[OK]   页面不含终端 hint「Tab 补全 · vim / top 可用 · Ctrl+C 中断」` | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— |
| 问题5 「阶段结论 · 已排查 N 步」及正文释义（不再提最大重试） | **SKIP**<br>该层无对应用例<br>— | **PASS**<br>跑满步数给出阶段结论（不是「请缩小范围」）<br>`[OK] 跑满步数给出阶段结论（不是「请缩小范围」）` | **SKIP**<br>该层无对应用例<br>— | **PASS**<br>跑满步数产出「阶段结论」标题<br>`[OK] 跑满步数产出「阶段结论」标题` | **PASS**<br>问题5: agent.py 含「阶段结论 · 已排查 N 步<br>`[OK] 问题5: agent.py 含「阶段结论 · 已排查 N 步」标题` | **PASS**<br>阶段结论标题在前端完整渲染（含「已排查」）<br>`[OK]   阶段结论标题在前端完整渲染（含「已排查」）` | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— |
| 问题6 命令问答卡住 / 富文本 **、围栏、重复空标题、半句 | **PASS**<br>标题行不残留 ** 星号<br>`[OK]   标题行不残留 ** 星号` | **PASS**<br>流程以 done 正常收尾<br>`[OK] 流程以 done 正常收尾` | **PASS**<br>问答接口返回 SSE<br>`[OK] 问答接口返回 SSE` | **PASS**<br>app.js 含 hideRunMeta<br>`[OK] app.js 含 hideRunMeta` | **PASS**<br>问题6: app.js 含 hideRunMeta 与 an<br>`[OK] 问题6: app.js 含 hideRunMeta 与 answer_done 收尾` | **PASS**<br>综合脏样本：cleanText 输出无 ** 星号残留<br>`[OK]   综合脏样本：cleanText 输出无 ** 星号残留` | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— | **SKIP**<br>该层无对应用例<br>— |

**矩阵统计：66 格 → PASS 25 / FAIL 0 / SKIP 41；6 个截图问题每个至少由 3 层 PASS 覆盖（L4、L5 两层对 6 个问题全部 PASS）。**

SKIP 格全部是「该层职责不涉及该问题」，而非漏测或假装通过：L1 是纯函数层（不涉 DOM / 界面文案），L2 是 Python 逻辑层（不涉前端 UI），L3 是既有第 1–5 节（不涉 v1.2 新增契约），L7 / L8 是终端审计专项层（只验证 VT 还原、1:1 实录、原始流与会话窗口，不涉历史截图问题）；这些问题由 L4 / L5 / L6 三层交叉覆盖。

### 覆盖矩阵读法示例

- 问题1 的硬证据是 L6 中 `Network.requestWillBeSent` 未出现 `/api/terminal/open`，以及 L4 中 `init() 起至文件尾不含 openTerminal(`。
- 问题2/5 的后端证据是 L4 第 7 节假 AI 跑满步数后产出「阶段结论」且正文含「阶段性结论」「最大重试」，同时问答流不弹「自动修复」提示。
- 问题6 由 L1 富文本纯函数与 L6 页面内 `window.AiOpsRender` 双重覆盖。

## 四、失败项明细

无失败用例。

## 五、交付物与截图校验

- `assets/preview.png`（首页截图）：73809 字节，PNG 魔数校验 **PASS**
- `assets/login-preview.png`（登录页截图）：567813 字节，PNG 魔数校验 **PASS**
- `scripts/test_render.mjs`：存在
- `scripts/selftest.py`：存在
- `scripts/httpcheck.py`：存在
- `scripts/test_static.py`：存在
- `scripts/ui_server.py`：存在
- `scripts/test_ui.mjs`：存在
- `scripts/test_terminal_capture.py`：存在
- `scripts/test_audit_sessions.py`：存在
- `scripts/test_all.py`：存在
- `docs/TEST_REPORT.md`：存在

## 六、环境说明

- Python：`Python 3.13.14`（`D:\git-repos\my_aiomss\venv\Scripts\python.exe`）
- Node：`v24.14.0`（使用全局 `WebSocket`，CDP 零第三方依赖）
- 浏览器：`C:\Program Files\Google\Chrome\Application\chrome.exe`
- L6 测试服务：`127.0.0.1:8899`，`scripts/ui_server.py` **同进程**签发 `auth.issue_session("admin")` 并运行 `backend.app:app`（内存 session 才有效）；Chrome CDP 端口 `9333`。**未占用 8848**。
- 本机 `HTTP_PROXY` 有值且 `NO_PROXY` 为空：Python `requests` 访问 127.0.0.1 需 `trust_env=False`；Node `fetch` 不受影响；Chrome 已加 `--no-proxy-server`。
- L4 第 7 节通过 monkeypatch `agent.AIClient` / `agent.store.get_profile` / `get_host` 注入本地假 AI（`command` 恒空、`done=False`），**不联网、不调用计费接口、不读取或回显任何真实 API Key**。
- 测试进程退出时会 `taskkill /T /F` 清理 Chrome 与 ui_server，并删除临时 user-data-dir。

## 七、已知限制

- `scripts/selftest.py` 与 `scripts/test_render.mjs` 为既有资产，本轮未修改；selftest 第 9 节由后端工程师并行修复，本次实跑结果见第二节。
- L6 依赖本机 Chrome/Edge；若探测不到会整层输出 `UI_SKIP=1` 并标 SKIP，不假装 PASS。
- L6 只验证「登录/初始化不自动连接」，未真实点击「连接」建立 SSH（避免连接真实主机与占用 8848）；危险命令审批、SSH 交互仍由 L2/L3 逻辑层覆盖。
- Ctrl+C 停止分支、真实桌面双击 `start.bat` 未在本轮自动化中演练（上一轮已人工验收真实启动输出与窗口保持），属部署验收范畴。
- 所有统计基于子进程 stdout 的 `结果: N 通过 / M 失败` 与 `[OK]/[FAIL]` 行，若某层输出格式变化会回退到行计数。
