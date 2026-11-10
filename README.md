# 电商多智能体深度研究系统

一个本地运行的研究工作台，围绕“任务规划 → 信息检索 → 数据分析 → 报告生成”组织电商研究任务。界面使用 React + TypeScript，后端使用 FastAPI；研究执行接入 DeepAgents、LangChain 与 LangGraph，业务记录和检查点分别保存到 SQLite。

项目提供两种模式：

| 模式 | 行为 |
| --- | --- |
| 示例模式 | 无需外部密钥；使用明确标注的模拟订单、上传资料和离线虚构网页，实际运行数据计算、绘图和本地工作流 |
| 在线模式 | 使用自行配置的模型、搜索、embedding 和 Redis；真实联网结果与示例来源分开保存 |

2026-10-31 已完成真实 DeepSeek、Tavily、百炼 Embedding 与 Redis Search 的在线功能验收，包括真实研究、取消重试与进程强杀后恢复。固定 12 查询的缓存评测为 4 次语义命中（33.33%）、0 次错命中、命中路径平均加速约 1.39 倍；图片中的 30 倍目标未达到，小样本结果不代表生产稳定性。详细证据见 [docs/VALIDATION.md](docs/VALIDATION.md)。

## 本地启动

需要 Python 3.11—3.13、Node.js 和 npm（当前锁定的 Vite 支持 Node 18、20 或 22 及以上）。下面的命令均在项目根目录执行。Windows 可以使用项目脚本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
# 本机已准备项目专用 Redis；重启电脑后先启动它。
powershell -ExecutionPolicy Bypass -File scripts/redis-start.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

setup 安装依赖并构建前端；start 在 `http://127.0.0.1:8000` 运行后端和构建后的工作台。开发时可用 `scripts/dev.ps1` 启动后端 8000 与前端 5173。首次配置与手动启动方式如下。

本机在线运行使用现有 Ubuntu WSL2 中的项目专用 Redis 8.10.3，端口 `6380`，原有 `6379` 服务保留。首次准备用 `scripts/redis-setup.ps1`；运行数据与随机密码保存在 `data/redis8/`，本机 `.env` 已连接该实例。复制项目到其他机器时需重新准备 Redis 和服务配置，不能复制空白模板就视为在线就绪。详见 [docs/REDIS_LOCAL.md](docs/REDIS_LOCAL.md)。纯离线示例可以跳过 Redis。

```powershell
uv sync --locked --group dev
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

使用已有 `.env` 时保留原文件；密钥只填在本地文件，避免提交或放入前端环境变量。Python 的锁文件是 `uv.lock`，前端使用 `frontend/package-lock.json`；没有 uv 时可执行 `python -m venv .venv`，再用 `.venv/Scripts/python.exe -m pip install -r requirements.txt` 安装导出的固定版本。受限 Windows 临时目录的问题由 setup 脚本通过项目 `.cache/tmp` 处理。

终端一启动后端：

```powershell
uv run --no-sync uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

终端二启动前端：

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开前端显示的地址，通常是 `http://127.0.0.1:5173`。接口文档在 `http://127.0.0.1:8000/docs`。Vite 将 `/api` 与 WebSocket 请求代理到后端。

点击“加载示例资料”后创建研究，或上传自己的 CSV、TXT、Markdown、PDF。示例数据的预期指标和说明位于 `samples/manifest.json`。详细演示步骤见 [docs/DEMO.md](docs/DEMO.md)。

## 在线服务配置

配置项均以 `RESEARCH_` 开头，见 `.env.example`。模型和 embedding 使用兼容接口，可分别设置服务地址、模型名称与密钥。

| 配置 | 用途 |
| --- | --- |
| `RESEARCH_MODEL_BASE_URL`、`RESEARCH_MODEL_NAME`、`RESEARCH_MODEL_API_KEY` | 工具调用模型地址、名称与密钥 |
| `RESEARCH_SEARCH_PROVIDER` | `tavily` 或 `searxng` |
| `RESEARCH_SEARCH_BASE_URL`、`RESEARCH_SEARCH_API_KEY` | 搜索服务地址或密钥 |
| `RESEARCH_EMBEDDING_BASE_URL`、`RESEARCH_EMBEDDING_MODEL`、`RESEARCH_EMBEDDING_API_KEY` | embedding 服务地址、模型与密钥 |
| `RESEARCH_REDIS_URL` | Redis 连接地址；完整在线模式需要相应服务 |
| `RESEARCH_SEMANTIC_THRESHOLD`、`RESEARCH_CACHE_TTL_SECONDS` | 语义匹配阈值和缓存有效期 |
| `RESEARCH_MAX_TOOL_CALLS`、`RESEARCH_MAX_MODEL_CALLS`、`RESEARCH_TASK_TIMEOUT_SECONDS` | 调用和运行预算 |

模型与 embedding 地址填写服务的 API 基础地址，例如服务要求的 `/v1` 前缀；不填写完整的 `/chat/completions` 或 `/embeddings` 请求路径。搜索服务填站点基础地址，适配器追加 `/search`。

若已安装 Docker，可启动项目提供的 Redis：

```powershell
docker compose up -d redis
```

Docker 是另一种运行方式，映射到本机 `6380`；不要与 WSL 项目实例同时占用该端口，切换时需同步 `.env` 中的认证配置。当前实际验收使用的是上述 WSL 实例。

在线向量索引需要 Redis Search/COSINE 支持，只有基础键值能力的旧版 Redis 不满足要求。示例模式没有 Redis 时使用 SQLite 缓存与字符特征哈希，只用于离线演示，不等于真实 embedding 模型或 Redis 向量检索效果。

修改配置后重启后端。在线配置缺失时任务应明确拒绝或报告错误，不使用其他应用中的环境凭证。服务需要支持所使用的模型工具调用格式；搜索和 embedding 的真实响应还需联调验证。

## 九个业务工具

| 助手 | 工具 |
| --- | --- |
| 联网研究 | `search_web`、`fetch_page`、`extract_page_tables` |
| 知识库 | `search_knowledge`、`read_chunk`、`get_document_metadata` |
| 数据分析 | `inspect_dataset`、`aggregate_metrics`、`create_chart` |

这是本项目为复现描述所定义的工具清单，不是截图原项目源码中的已知名称。外部能力包含互联网、自有知识库与数据处理服务；框架内置的规划、文件和委派工具不计入这九项。

数据分析只提供受限参数化统计，模型不执行任意 Python 或 SQL。销售额、退款与净销售额分别列出；订单量按订单编号去重。知识检索限于当前任务选中的文档。

## 状态、恢复与观察

业务任务、证据、产物与过程事件保存在 `data/app.sqlite`；图检查点保存在 `data/checkpoints.sqlite`；任务文件在 `data/tasks/`。Redis 维护运行时投影、任务租约和缓存，SQLite 提供持久化恢复依据。

服务重启后自动扫描可恢复任务，保留任务标识继续执行或对账。取消请求会得到最终确认，显式重试创建新任务。检查点不是进程现场快照，外部请求在成功后但尚未持久化时中断，恢复后仍可能重试和产生调用费用。

页面通过 WebSocket 接收实际过程事件，刷新与重连后可补取持久化事件并查询状态。工具记录用于汇总频次、耗时、成功率和缓存命中；缓存命中请求加速与整项研究加速分开评测。

## 验证

```powershell
uv run --no-sync pytest --basetemp .cache/pytest-local
cd frontend
npm run build
```

测试使用临时目录和离线示例，不调用付费接口。真实结果与尚未验证的部分记录于 [docs/VALIDATION.md](docs/VALIDATION.md)。

缓存专项可用 `.venv/Scripts/python.exe scripts/evaluate_cache.py --mode demo` 跑固定查询集；在线配置后使用 `--mode live`，会真实调用搜索与 embedding。脚本把精确探针、语义命中、错命中和同批命中的耗时对照分开，默认报告输出到 `artifacts/cache-eval/<mode>/`。这是工具查询层评测，完整智能体连续任务、真实服务恢复及截图性能目标仍需另行验证，详见 [docs/CACHE_EVALUATION.md](docs/CACHE_EVALUATION.md)。

当前实现状态以该记录及实际运行结果为准。示例资料不代表真实经营、行业事实或生产容量；没有真实接口验证前，不把模型质量、线上恢复或缓存性能作为已验证成果。

原始设计与图片逐项对应关系见 [项目实现方案.md](项目实现方案.md)。
代码模块和两种执行路径见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
