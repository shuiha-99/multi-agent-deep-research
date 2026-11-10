from __future__ import annotations
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import PROJECT_ROOT, Settings
from app.ingestion import ingest_dataset, ingest_document, load_samples
from app.redis_state import RedisState
from app.storage import Repository
from app.tasks import TaskManager

TOOLS = [
    {"name": "search_web", "agent": "web", "description": "检索公开信息"},
    {"name": "fetch_page", "agent": "web", "description": "读取网页正文和来源"},
    {"name": "extract_page_tables", "agent": "web", "description": "提取网页表格"},
    {"name": "search_knowledge", "agent": "knowledge", "description": "检索选中知识资料"},
    {"name": "read_chunk", "agent": "knowledge", "description": "读取原文和相邻上下文"},
    {"name": "get_document_metadata", "agent": "knowledge", "description": "核对资料版本和适用范围"},
    {"name": "inspect_dataset", "agent": "data", "description": "检查数据字段与质量"},
    {"name": "aggregate_metrics", "agent": "data", "description": "执行经营指标统计"},
    {"name": "create_chart", "agent": "data", "description": "根据真实统计生成图表"},
]


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date | None = None
    end_date: date | None = None
    region: str = Field(default="", max_length=100)
    category: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def dates_ordered(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        return self


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=2, max_length=6000)
    title: str | None = Field(default=None, max_length=100)
    mode: Literal["demo", "live"] = "demo"
    dataset_ids: list[str] = Field(default_factory=list, max_length=5)
    document_ids: list[str] = Field(default_factory=list, max_length=20)
    scope: Scope = Field(default_factory=Scope)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    repo = Repository(settings.data_dir)
    redis_state = RedisState(settings.redis_url)
    manager = TaskManager(settings, repo, redis_state)

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        try:
            await manager.start()
            yield
        finally:
            await manager.close()

    app = FastAPI(title="Commerce Research · 多智能体深度研究", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.repo = repo
    app.state.manager = manager
    app.state.redis = redis_state
    origins = {"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"}
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    def task_or_404(task_id):
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(404, "研究任务不存在")
        return task

    def enrich(task):
        return {**task, "metrics": repo.metrics(task["id"]), "artifacts": repo.list_artifacts(task["id"])}

    def public_config():
        missing = settings.missing_live_config()
        return {"mode": settings.mode, "model_name": settings.model_name, "search_provider": settings.search_provider,
                "embedding_model": settings.embedding_model, "tools": TOOLS,
                "agents": [{"name": "orchestrator", "label": "主智能体"}, {"name": "web", "label": "联网研究"},
                           {"name": "knowledge", "label": "知识库检索"}, {"name": "data", "label": "数据分析"}],
                "missing_live_config": missing, "live_ready": not missing and redis_state.available and redis_state.vector_search_available,
                "redis": redis_state.summary(), "max_tasks": settings.max_tasks, "max_tool_calls": settings.max_tool_calls,
                "max_model_calls": settings.max_model_calls, "task_timeout_seconds": settings.task_timeout_seconds,
                "cache_ttl_seconds": settings.cache_ttl_seconds, "semantic_threshold": settings.semantic_threshold,
                "note": "示例模式使用规则规划与离线样例，在线模式使用 DeepAgents 和真实服务。性能目标尚未实测。"}

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.1.0", "mode": settings.mode,
                "services": {"redis": redis_state.summary(), "model": {"configured": bool(settings.model_name and settings.model_base_url and settings.model_api_key)},
                             "search": {"configured": bool(settings.search_api_key if settings.search_provider == "tavily" else settings.search_base_url)},
                             "embedding": {"configured": bool(settings.embedding_model and settings.embedding_base_url and settings.embedding_api_key)}},
                "features": {"demo": True, "live": not settings.missing_live_config() and redis_state.available and redis_state.vector_search_available,
                             "tools": 9, "checkpoint": True, "websocket": True, "performance_verified": False}}

    @app.get("/api/config")
    async def config():
        return public_config()

    @app.get("/api/documents")
    async def documents():
        return repo.list_documents()

    @app.get("/api/datasets")
    async def datasets():
        return repo.list_datasets()

    async def uploaded(file, handler):
        content = await file.read(settings.max_upload_bytes + 1)
        await file.close()
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "文件超过 10 MB，请拆分后上传")
        name = Path(file.filename or "未命名文件").name[:150]
        try:
            return await asyncio.to_thread(handler, repo, name, content)
        except Exception as exc:
            raise HTTPException(422, f"文件解析失败：{str(exc)[:200]}") from exc

    @app.post("/api/documents", status_code=201)
    async def upload_document(file: UploadFile = File(...)):
        return await uploaded(file, ingest_document)

    @app.post("/api/datasets", status_code=201)
    async def upload_dataset(file: UploadFile = File(...)):
        return await uploaded(file, ingest_dataset)

    @app.post("/api/samples/load")
    async def samples():
        return await asyncio.to_thread(load_samples, repo)

    @app.get("/api/research/tasks")
    async def tasks():
        return repo.list_tasks()

    @app.post("/api/research/tasks", status_code=202)
    async def new_task(payload: TaskInput):
        for item_id in payload.document_ids:
            if not repo.get_document(item_id):
                raise HTTPException(422, f"知识资料不存在：{item_id}")
        for item_id in payload.dataset_ids:
            if not repo.get_dataset(item_id):
                raise HTTPException(422, f"数据集不存在：{item_id}")
        if payload.mode == "live":
            missing = settings.missing_live_config()
            if missing:
                raise HTTPException(422, "在线模式配置尚未完成：" + "、".join(missing))
            if not redis_state.available or not redis_state.vector_search_available:
                await redis_state.connect()
            if not redis_state.available:
                raise HTTPException(422, "在线模式需要 Redis，请启动 Redis 或检查 RESEARCH_REDIS_URL")
            if not redis_state.vector_search_available:
                raise HTTPException(422, "Redis 已连接，但未确认 FT.CREATE/FT.SEARCH 向量检索命令；请使用支持向量搜索的 Redis 并检查命令权限")
        if sum(t["status"] in {"queued", "running", "recovering", "cancel_requested"} for t in repo.list_tasks()) >= 20:
            raise HTTPException(429, "待处理任务较多，请等待现有研究结束")
        task = repo.create_task(payload.model_dump(mode="json", exclude_none=True))
        await manager.emit(task["id"], "task_created", {"message": "研究任务已创建", "mode": task["mode"]})
        return task

    @app.get("/api/research/tasks/{task_id}")
    async def task_detail(task_id: str):
        return enrich(task_or_404(task_id))

    @app.get("/api/research/tasks/{task_id}/events")
    async def events(task_id: str, after_seq: int = 0):
        task_or_404(task_id)
        return repo.list_events(task_id, max(0, after_seq))

    @app.get("/api/research/tasks/{task_id}/sources")
    async def sources(task_id: str):
        task_or_404(task_id)
        return repo.list_evidence(task_id)

    @app.post("/api/research/tasks/{task_id}/cancel")
    async def cancel(task_id: str):
        task_or_404(task_id)
        return await manager.cancel(task_id)

    @app.post("/api/research/tasks/{task_id}/retry", status_code=202)
    async def retry(task_id: str):
        task = task_or_404(task_id)
        if task["status"] not in {"failed", "cancelled"}:
            raise HTTPException(409, "只有失败或已取消的任务可以重新研究")
        return await new_task(TaskInput(**{k: task[k] for k in ["question", "title", "mode", "dataset_ids", "document_ids", "scope"]}))

    @app.get("/api/research/tasks/{task_id}/report")
    async def report(task_id: str):
        task = task_or_404(task_id)
        if not task.get("report_markdown"):
            raise HTTPException(409, "报告尚未生成")
        return {"markdown": task["report_markdown"]}

    @app.get("/api/research/tasks/{task_id}/report/download")
    async def download(task_id: str):
        task = task_or_404(task_id)
        if not task.get("report_markdown"):
            raise HTTPException(409, "报告尚未生成")
        return Response(task["report_markdown"], media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="research-{task_id}.md"'})

    @app.get("/api/artifacts/{artifact_id}")
    async def artifact(artifact_id: str):
        item = repo.get_artifact(artifact_id)
        if not item:
            raise HTTPException(404, "研究产物不存在")
        try:
            path = repo.safe_path(item["path"])
        except ValueError:
            raise HTTPException(404, "研究产物不存在")
        if not path.is_file():
            raise HTTPException(404, "研究产物文件已不存在")
        return FileResponse(path, media_type=item["media_type"], headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/api/metrics")
    async def metrics():
        return repo.metrics()

    @app.websocket("/api/research/tasks/{task_id}/stream")
    async def stream(websocket: WebSocket, task_id: str, after_seq: int = 0):
        origin = websocket.headers.get("origin")
        # start.ps1 accepts a custom port. Allow that page's own loopback origin,
        # while retaining explicit development origins and rejecting other sites.
        same_local_origin = False
        if origin:
            parsed = urlsplit(origin)
            same_local_origin = (parsed.scheme in {"http", "https"}
                                 and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                                 and parsed.netloc == websocket.headers.get("host")
                                 and not parsed.path and not parsed.query and not parsed.fragment)
        if (origin and origin not in origins and not same_local_origin) or not repo.get_task(task_id):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        cursor = max(0, after_seq)
        try:
            while True:
                for event in repo.list_events(task_id, cursor):
                    await websocket.send_json(event)
                    cursor = event["seq"]
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=0.35)
                    if message == "ping":
                        await websocket.send_json({"type": "pong"})
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError):
            return

    dist = PROJECT_ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
