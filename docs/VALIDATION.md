# 验证记录

此文件记录实际执行结果，不把计划中的功能或截图中的指标作为已经验证的成绩。

以下为截至 2026-10-31 的实际验证状态。已执行完整验收：功能项通过，30 倍缓存加速目标未达到。

| 验证项 | 状态 | 说明 |
| --- | --- | --- |
| 样例数据业务口径 | 已核对 | 19 行、16 个订单、销售额 3900、退款 180、净销售额 3720；月销售额 900/1200/1800 |
| 后端自动测试 | 已通过 | 最终 tests/ 与 engine/tests 一次全量运行：56 passed in 29.85s；测试子进程空白在线凭据、独立数据 |
| 引擎测试 | 已通过 | 模拟接口回归与独立真实在线验收均完成；参数错误可在预算内纠正，权限/预算仍硬拒绝 |
| React 类型检查与构建 | 已通过 | 一键 setup 实际执行 uv 锁定安装、npm ci、tsc 与 Vite 生产构建 |
| 浏览器联调 | 已通过 | 示例流程 20 项；最终真实在线任务另 15 项，含桌面/390px、图表、下载、来源、事件与刷新 |
| 真实模型与搜索 | 已通过功能验收 | DeepSeek 主智能体与三个助手、Tavily、百炼 Embedding 实际协作；真实取消、新任务重试与纯数据路由通过 |
| Redis/embedding 真实集成 | 已通过 | 现有 WSL2 中项目专用 Redis 8.10.3/6380，AUTH、Search/COSINE KNN、1024 维知识检索与缓存通过；原 6379 保留 |
| 服务停止重启后的 checkpoint 恢复 | 已通过离线与在线强杀 | 真实在线任务重启后 attempt=2、有 checkpoint_resumed；已完成 5 次业务工具调用保持 5 次，未重复 |
| 语义缓存平均加速 30 倍 | 未达标 | 固定查询集实测约 1.388 倍：同批正确命中直连均值 3563.763ms / 完整缓存命中路径 2567.471ms |
| 语义缓存命中率 30%—50% | 本批落入区间，稳定性未验证 | 12 次查询、4 次语义命中（33.33%）、0 次预标注错命中；两个窗口各 2/6，精确探针另计 |

## 2026-10-31 最终验收

当前源码的自动回归为 `.venv/Scripts/python.exe -m pytest -q --basetemp .cache/pytest-complete-acceptance`，结果 **56 passed in 29.85s**；前端类型检查与生产构建通过。真实在线功能汇总为 `artifacts/acceptance/summary-2026-10-31.json`，包含 11 项组合功能检查及各次原始 run 的证据引用；历史失败记录保留，没有改写成成功。

运行环境采用 DeepSeek `deepseek-flash`、百炼 `text-embedding-v4` 1024 维、Tavily 与现有 Ubuntu WSL2 中的项目专用 Redis 8.10.3。官方 Redis 包仅解包到项目目录，校验 SHA256；实例使用本机 6380 及独立随机密码，原 Windows Redis 6379 未替换。来源、启动与停止见 `docs/REDIS_LOCAL.md`。

真实九工具独立验收覆盖互联网检索、网页正文、HTML 表格正例、Redis 知识向量检索、原文与元数据、实际统计及 PNG。HTML 表格与 `pandas.read_html` 独立解析的前三行逐格一致。综合图按实际需要调度三个助手，不要求每次研究调用所有工具。

真实模型请求进行时取消，原任务最终 cancelled；重试创建新任务且仅委派数据助手。强杀在线进程后保持原任务 ID，等待旧租约自然过期，再以 attempt=2 从检查点继续；已完成 inspect=1、aggregate=1、chart=3，恢复后计数相同。该结果只证明观测到的持久化边界，不承诺所有中断点的外部请求恰好执行一次。

验收暴露并修复了统计 JSON/CSV 产物编号混淆和业务参数错误直接中断图的问题：现在返回显式统计产物编号、发布工具参数枚举，模型可在预算内根据错误信息修正；越权和预算耗尽仍拒绝。退款金额占比由程序计算，明确不等于退款订单率；引用在方括号、反引号和纯文本形式下均做来源校验。图表说明与实际指标、有限归因要求也已收紧。

最终供用户演示的普通数据目录任务为 `task_2f05f5e6ddc94abb`（标题“在线演示：经营数据与选品建议”）：三个助手、18 次工具调用（含框架委派）、23 条来源、81 条事件，报告/JSON/CSV/PNG 均保存。销售额 3900、净销售额 3720、去重订单 16，退款金额占比 180/3900；经营 CSV 和上传文档仍明确是模拟资料，模型、搜索和向量链路为真实执行。

最终在线浏览器只读验收为 **15/15**，`artifacts/ui-live/result.json` 对应上述任务；实际 PNG、下载逐字一致、刷新无重复事件、在线说明、业务计划摘要、桌面与 390px 无溢出，未出现页面或控制台错误。截图已人工检查。原始最终报告及证据另存 `artifacts/acceptance/final-demo-report.md`、`final-demo-evidence.json`。

固定缓存集保持原顺序、原 0.92 阈值，关闭前置精确缓存：4/12 语义命中、0 次预标注错命中、1.388 倍同请求匹配加速；原始文件 `artifacts/acceptance/20261031T132935Z-41805c12/cache-evaluation.json`。这是工具查询序列评测，不把其等同为完整研究加速、生产命中稳定性或真实行业结论。**功能验收通过不等于原图所有性能指标均达标，30 倍目标仍未达到。**

以下保留前期历史验证，不能用旧状态替代上面的最终记录。

2026-10-30 初版整合验证：

```text
.venv/Scripts/python.exe -m pytest -q --basetemp .cache/pytest-acceptance
39 passed in 40.98s

powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
Python locked dependencies checked; npm ci completed; tsc -b && vite build passed.

powershell -ExecutionPolicy Bypass -File scripts/start.ps1
http://127.0.0.1:8000 serves the built workspace and API.
```

通过最终启动脚本启动常规 data/ 目录的服务后，另经 HTTP 创建了一条实际示例任务，成功保存报告、PNG、CSV 统计表和 JSON 统计数据：12 次工具调用、5 次外部缓存查询、13 条来源。它是明确标注的离线示例，不是在线研究成绩。

自动测试覆盖任务取消/重试、未选资料隔离、路径限制、有序事件、同源自定义端口 WebSocket、模型异常与取消、角色工具白名单、日期/品类/地区统计、精确/语义缓存分支、端点命名空间隔离、重启 checkpoint 续跑和用量统计。HTTP 模型测试只模拟提供商响应，实际执行 DeepAgents 主代理与数据助手、Pandas 计算及 PNG 绘图；真实模型质量、Redis Lua/向量命令和外部服务兼容性仍待集成验证。

`tests/test_process_recovery.py` 使用独立子进程和端口 8124，在进度 88 时强杀自身创建的进程；数据库保留 running 且没有优雅退出事件，再启动同一数据目录后核对检查点续跑、原任务 ID、attempt=2、3→3 次工具调用及正确销售额。最终全量证据位于 `.cache/pytest-acceptance/test_force_killed_process_resu0/process-recovery-result.json`。测试的 HTTP 读取上限为 10 秒以覆盖子进程首次冷启动导入，连接上限 1 秒，研究阶段总等待上限仍单独限制。

来源一致性回归同时核对：同一 URL 的不同搜索摘要、过期后更新的网页正文和表格分别保留对应证据版本；相同内容在当前任务内继续去重。

缓存加速评测需比较同批命中样例的直连 API 平均耗时与缓存路径平均耗时，缓存路径包含 embedding、检索、反序列化和必要的来源登记。整项研究耗时另报。阈值校准集与正式评测集分开。

2026-10-29 本地实际执行：

```text
.venv/Scripts/python.exe -m pytest tests -q --basetemp .cache/pytest-quality
21 passed in 18.02s

.venv/Scripts/python.exe -m pytest tests/test_api.py::test_demo_research_has_real_sources_metrics_and_downloadable_report tests/test_recovery.py::test_restart_resumes_saved_graph_step_without_repeating_completed_tools -q --basetemp .cache/pytest-quality-resume
2 passed in 23.46s
```

第二次运行包含新增的恢复用例，并加强统计 JSON 的销售额、订单量、退款、净销售额与模拟数据标识断言。这两次输出不相加为一次测试总数。

Windows 受限执行环境中，默认系统临时目录曾导致原子文件替换出现 PermissionError；将 pytest 的临时数据目录设置在项目 `.cache/` 后通过。普通本地环境是否受同样限制取决于运行权限。

2026-10-30 浏览器实际执行：

`tests/ui-smoke.cjs` 启动了独立测试数据目录、显式空白在线凭据的 API 服务，以及全新无用户配置的 Chromium headless 实例。测试结束后关闭了它创建的浏览器和 API 服务。未连接现有浏览器，也未使用其他项目凭据。

20 项检查全部通过，记录见 `artifacts/ui/result.json`：

- 空状态没有虚构任务；通过页面加载 19 行示例 CSV，完成真实工具执行。报告核对销售额 3900、净销售额 3720、去重订单量 16；主示例执行 12 次工具调用，9 种工具均出现在运行统计中。
- 1500 × 690 PNG 图表实际加载；13 条来源可见；下载的 Markdown 与已保存报告逐字一致。文档与 CSV 通过页面上传、解析并显示。
- 自定义“仅根据上传 CSV”任务只调用 data 助手，调用 3 次工具。刷新页面后任务、报告和 21 条执行事件正确恢复，列表无重复。
- 主页面 WebSocket 接收实际执行帧；独立新连接携带 `after_seq=25`，重放剩余 24 条有序事件，未混入早于游标的事件。
- 1440px 桌面报告与空页面、390px 手机配置/报告/创建对话框均无横向溢出；桌面与手机截图已人工查看。浏览器无页面异常、可操作控制台错误、可操作请求失败或非本地网络请求。

截图：`artifacts/ui/report-desktop.png`、`report-mobile.png`、`report-chart.png`、`metrics-desktop.png`、`settings-desktop.png`、`create-mobile.png`。下载由浏览器接管时产生的正常 `net::ERR_ABORTED` 单独记录为 `download_handoffs`，并已通过下载文件内容验证。

脚本可使用 `PLAYWRIGHT_LIBRARY_PATH` 和 `PLAYWRIGHT_EXECUTABLE_PATH` 指向已有 Playwright 与浏览器；未设置时使用正常安装的 Playwright。`UI_TEST_PORT` 可指定隔离服务端口，`UI_DELIVERY_URL=''` 可关闭对常规 8000 交付页面的额外只读检查。当前 Windows 执行将 `TMP`/`TEMP` 与下载目录放在项目 `.cache/` 中，以避免系统临时目录权限影响。

## 在线服务配置检查

本轮仅使用用户填写在本项目 `.env` 中的凭据，未读取其他项目的密钥，也未在记录中显示凭据。

- 发现 Embedding 误填为对话模型 `qwen-plus`，已改为 `text-embedding-v4`、1024 维，原密钥保持。
- DeepSeek `deepseek-flash` 实际基础请求返回 HTTP 200；随后通过生产模型适配器完成真实两轮工具调用与结果回传。适配器对官方 DeepSeek 接口显式关闭思考，其他提供商不接收该专用参数。
- 百炼 Embedding 实际请求返回 HTTP 200，向量长度为 1024，与配置一致。
- Tavily 实际搜索返回 HTTP 200，并返回包含 URL 的有效结果。
- 用户补入 Redis 密码后，发现连接串将密码放在用户名位置（少了冒号）；已仅修正 URL 格式，密码内容保持。随后使用 RESP2 实际 AUTH/PING 均成功，INFO 确认版本 5.0.14.1；COMMAND INFO 显示缺少 FT.CREATE/FT.SEARCH，因此当前服务不能完成项目要求的 Redis 向量检索，完整在线研究仍未就绪。
- Redis 客户端显式使用 RESP2，兼容当前服务握手，并保持原始 FT.SEARCH 数组返回值的解析契约。引擎 mock 测试已覆盖协议及向量响应解析；它不代替真实 Redis 集成验证。

这些结果只证明基础服务和模型工具协议可用，不代表完整在线多智能体研究、缓存命中效果或线上恢复已经通过。

Redis 就绪判断补充：已将连接与向量能力分别展示，并在缺少 FT.CREATE/FT.SEARCH 时拒绝在线任务，示例模式可继续使用。新 mock 回归及关联 API 检查 5 passed；前端类型检查与构建通过。重启后实际 HTTP 检查确认 available=true、vector_search_available=false、live_ready=false，在线创建返回 422 且没有新增任务。
