"""One telemetry boundary used by both LangChain middleware and offline workers."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import uuid4

import httpx
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from .registry import TOOL_AGENTS

logger = logging.getLogger("research.engine")


class BudgetExceeded(RuntimeError):
    pass


class ToolExecutor:
    def __init__(self, tools, emit, cancelled):
        self.tools, self.emit, self.cancelled = tools, emit, cancelled
        self.settings, self.repo, self.task = tools.settings, tools.repo, tools.task
        history = self.repo.list_events(self.task["id"])
        self.calls = sum(e["event_type"] == "tool_started" for e in history)
        self.model_calls = sum(e["event_type"] == "model_started" for e in history)
        self.subtasks = sum(e["event_type"] == "agent_started" for e in history)
        self.worker_counts = {agent: sum(e["event_type"] == "agent_started" and e["agent"] == agent for e in history) for agent in ("web", "knowledge", "data")}
        self._counter_lock = asyncio.Lock()
        self._plan_lock = asyncio.Lock()
        self._delegate_semaphore = asyncio.Semaphore(2)

    def check_cancel(self):
        if self.cancelled():
            raise asyncio.CancelledError()

    async def delegation_status(self, call_id, agent, description, status):
        async with self._plan_lock:
            plan = self.repo.get_task(self.task["id"]).get("plan", [])
            item = next((p for p in plan if p["id"] == call_id), None)
            if item:
                item["status"] = status
            else:
                plan.append({"id": call_id, "agent": agent, "title": description[:120] or "专业助手研究", "description": description[:2500], "status": status})
            self.repo.update_task(self.task["id"], plan=plan)
            await self.emit("plan_updated", {"plan": plan})

    async def execute(self, name, args, *, agent=None, handler=None):
        self.check_cancel()
        agent = agent or TOOL_AGENTS.get(name, "orchestrator")
        if name in TOOL_AGENTS and agent != TOOL_AGENTS[name]:
            raise ValueError("助手没有该工具的使用权限。")
        async with self._counter_lock:
            if self.calls >= self.settings.max_tool_calls:
                raise BudgetExceeded("已达到工具调用预算，请缩小问题范围后重试。")
            self.calls += 1
        call_id, started = str(uuid4()), time.perf_counter()
        self.tools.last_cache_status = "miss"
        await self.emit("tool_started", {"call_id": call_id, "arguments": self._safe_args(args), "message": f"开始调用 {name}"}, agent=agent, tool=name)
        try:
            async def invoke():
                if handler:
                    return await handler()
                return await getattr(self.tools, name)(**args)
            attempts = 0
            while True:
                self.check_cancel()
                try:
                    timeout = self.settings.task_timeout_seconds if name == "task" else self.settings.tool_timeout_seconds
                    async with asyncio.timeout(timeout):
                        result = await invoke()
                    break
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    attempts += 1
                    if attempts >= 2:
                        raise
                    await self.emit("tool_retry", {"call_id": call_id, "attempt": attempts+1, "message": "临时网络错误，进行一次有限重试。"}, agent=agent, tool=name)
            self.check_cancel()
            elapsed = round((time.perf_counter()-started)*1000, 2)
            cache = self.tools.last_cache_status
            returned_error = getattr(result, "status", None) == "error" or (isinstance(result, dict) and result.get("status") == "error")
            status = "failed" if returned_error else "success"
            self.repo.record_tool_run(self.task["id"], agent, name, status, elapsed, cache_status=cache, details={"call_id": call_id, "attempts": attempts+1})
            if cache in {"exact_hit", "semantic_hit"}:
                await self.emit("cache_hit", {"call_id": call_id, "cache_status": cache, "embedding_mode": self.tools.cache.version, "message": "复用相同范围内仍有效的工具结果。"}, agent=agent, tool=name)
            summary = result.get("summary") if isinstance(result, dict) else None
            await self.emit("tool_failed" if returned_error else "tool_completed", {"call_id": call_id, "duration_ms": elapsed, "cache_status": cache, "message": summary or ("工具返回错误。" if returned_error else "调用完成。")}, agent=agent, tool=name)
            logger.info(json.dumps({"task_id": self.task["id"], "agent": agent, "tool": name, "call_id": call_id, "status": status, "duration_ms": elapsed}, ensure_ascii=False))
            return result
        except asyncio.CancelledError:
            elapsed = round((time.perf_counter()-started)*1000, 2)
            self.repo.record_tool_run(self.task["id"], agent, name, "cancelled", elapsed, details={"call_id": call_id})
            await self.emit("tool_cancelled", {"call_id": call_id, "message": "工具调用已取消。"}, agent=agent, tool=name)
            raise
        except Exception as exc:
            elapsed = round((time.perf_counter()-started)*1000, 2)
            # Avoid external exception strings which may include provider credentials/headers.
            message = str(exc) if isinstance(exc, (ValueError, BudgetExceeded)) else f"工具请求失败（{type(exc).__name__}），请检查接口或数据。"
            self.repo.record_tool_run(self.task["id"], agent, name, "failed", elapsed, details={"call_id": call_id, "error_type": type(exc).__name__})
            await self.emit("tool_failed", {"call_id": call_id, "duration_ms": elapsed, "message": message}, agent=agent, tool=name)
            raise ValueError(message) from exc

    @staticmethod
    def _safe_args(args):
        # The public tool schemas contain no credentials, but keep a defensive filter.
        return {k: (str(v)[:1000] if not isinstance(v, (int, float, bool)) else v) for k, v in args.items() if not any(s in k.lower() for s in ("key", "token", "password", "secret"))}


class ResearchMiddleware(AgentMiddleware):
    """Installed separately on the root and all three specialists."""

    def __init__(self, executor: ToolExecutor, agent: str):
        super().__init__()
        self.executor, self.agent = executor, agent

    async def awrap_tool_call(self, request, handler):
        name, args = request.tool_call["name"], request.tool_call.get("args", {})
        allowed = {"task", "write_todos"} if self.agent == "orchestrator" else {name for name, owner in TOOL_AGENTS.items() if owner == self.agent}
        if name not in allowed:
            raise ValueError("助手没有该工具的使用权限。")
        if name == "task":
            worker = args.get("subagent_type", args.get("subagentType", ""))
            if worker not in {"web", "knowledge", "data"}:
                raise ValueError("只能委派给 web、knowledge 或 data 三个专业助手。")
            async with self.executor._counter_lock:
                if self.executor.subtasks >= self.executor.settings.max_subtasks:
                    raise BudgetExceeded("已达到子任务预算。")
                if self.executor.worker_counts[worker] >= 1 + self.executor.settings.max_followups:
                    raise BudgetExceeded("该助手已达到首轮执行及补查预算。")
                self.executor.subtasks += 1
                self.executor.worker_counts[worker] += 1
            await self.executor.emit("agent_started", {"message": f"委派给 {worker} 助手", "description": str(args.get("description", ""))[:1500]}, agent=worker)
            call_id = request.tool_call.get("id", str(uuid4()))
            description = str(args.get("description", ""))
            await self.executor.delegation_status(call_id, worker, description, "running")
            try:
                async with self.executor._delegate_semaphore:
                    result = await self.executor.execute(name, args, agent="orchestrator", handler=lambda: handler(request))
            except BaseException:
                await self.executor.delegation_status(call_id, worker, description, "failed")
                raise
            await self.executor.delegation_status(call_id, worker, description, "completed")
            await self.executor.emit("agent_completed", {"message": "助手已返回摘要与证据。"}, agent=worker)
            return result
        if name == "write_todos":
            await self.executor.emit("plan_proposed", {"message": "主智能体更新研究计划。", "todos": args.get("todos", [])}, agent="orchestrator")
        try:
            return await self.executor.execute(name, args, agent=self.agent, handler=lambda: handler(request))
        except ValueError as exc:
            # Recoverable input/data errors belong in the model's tool loop.
            # The executor has already recorded this failed call. Role guards
            # above and BudgetExceeded remain hard boundaries outside this path.
            return ToolMessage(
                content=json.dumps({"status": "error", "error_code": "invalid_tool_arguments",
                                    "message": str(exc), "correctable": True}, ensure_ascii=False),
                tool_call_id=request.tool_call.get("id", str(uuid4())), name=name, status="error",
            )

    async def awrap_model_call(self, request, handler):
        self.executor.check_cancel()
        async with self.executor._counter_lock:
            if self.executor.model_calls >= self.executor.settings.max_model_calls:
                raise BudgetExceeded("已达到模型调用预算。")
            self.executor.model_calls += 1
        started = time.perf_counter()
        await self.executor.emit("model_started", {"message": "模型正在规划或综合资料。"}, agent=self.agent)
        try:
            response = await handler(request)
            self.executor.check_cancel()
        except asyncio.CancelledError:
            await self.executor.emit("model_cancelled", {"message": "模型调用已取消。", "duration_ms": round((time.perf_counter()-started)*1000, 2)}, agent=self.agent)
            raise
        except Exception as exc:
            await self.executor.emit("model_failed", {"message": "模型请求失败，请检查服务配置或调用预算。", "error_type": type(exc).__name__, "duration_ms": round((time.perf_counter()-started)*1000, 2)}, agent=self.agent)
            raise
        usage = {}
        for message in getattr(response, "result", []):
            values = getattr(message, "usage_metadata", None) or {}
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                if isinstance(values.get(key), int):
                    usage[key] = usage.get(key, 0) + values[key]
        await self.executor.emit("model_completed", {"message": "模型调用完成。", "duration_ms": round((time.perf_counter()-started)*1000, 2), "usage_metadata": usage or None}, agent=self.agent)
        return response
