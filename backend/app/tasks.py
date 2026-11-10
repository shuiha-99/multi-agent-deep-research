from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from app.storage import now

logger = logging.getLogger("research.tasks")


class InstanceLock:
    """One local service per data directory, including on Windows."""
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if not self.handle.read(1):
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("该数据目录已有研究服务运行，请关闭重复实例或使用其他数据目录") from exc

    def close(self):
        if self.handle:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class TaskManager:
    def __init__(self, settings, repo, redis_state):
        self.settings = settings
        self.repo = repo
        self.redis = redis_state
        self.active = {}
        self.cancel_flags = {}
        self.lease_lost = set()
        self.stopping = False
        self.scheduler = None
        self.lock = InstanceLock(settings.data_dir / ".runtime.lock")

    async def start(self):
        self.lock.acquire()
        await self.redis.connect()
        for task in self.repo.recoverable_tasks():
            task_id = task["id"]
            if task["status"] == "cancel_requested":
                self.repo.update_task(task_id, status="cancelled", phase="已取消")
                self.repo.append_event(task_id, "task_cancelled", {"message": "重启后完成取消状态对账"})
            elif task["status"] in {"running", "recovering"}:
                self.repo.update_task(task_id, status="recovering", phase="等待从检查点恢复")
                self.repo.append_event(task_id, "task_recovery_scheduled", {"message": "服务重启，已安排检查点恢复"})
        # Redis loss must be repairable even after previously acknowledged outbox rows.
        for task in self.repo.list_tasks():
            if await self.redis.project(task):
                self.repo.mark_redis_synced(task["id"], task["version"])
        self.scheduler = asyncio.create_task(self._schedule(), name="research-scheduler")

    async def emit(self, task_id, event_type, data, agent="orchestrator", tool=None):
        if event_type == "phase":
            fields = {k: data[k] for k in ["phase", "progress"] if k in data}
            if fields:
                self.repo.update_task(task_id, **fields)
        elif event_type == "plan_updated" and "plan" in data:
            self.repo.update_task(task_id, plan=data["plan"])
        elif event_type in {"agent_started", "agent_completed", "agent_failed", "agent_cancelled"}:
            active_agents = set(self.repo.get_task(task_id).get("active_agents", []))
            if event_type == "agent_started":
                active_agents.add(agent)
            else:
                active_agents.discard(agent)
            self.repo.update_task(task_id, active_agents=sorted(active_agents),
                                  current_agent=",".join(sorted(active_agents)) or "orchestrator")
        task = self.repo.get_task(task_id)
        event = self.repo.append_event(task_id, event_type, {"attempt": task["attempt"], **data}, agent, tool)
        logger.info(json.dumps({"task_id": task_id, "event": event_type, "agent": agent, "tool": tool, "seq": event["seq"]}, ensure_ascii=False))
        if await self.redis.project(task):
            self.repo.mark_redis_synced(task_id, task["version"])
        return event

    async def _schedule(self):
        tick = 0
        while not self.stopping:
            try:
                tick += 1
                if tick % 12 == 0:
                    if not self.redis.available:
                        was_available = self.redis.available
                        await self.redis.connect()
                        if self.redis.available and not was_available:
                            for task in self.repo.list_tasks():
                                await self.redis.project(task)
                    for task in self.repo.pending_redis():
                        if await self.redis.project(task):
                            self.repo.mark_redis_synced(task["id"], task["version"])
                if len(self.active) < self.settings.max_tasks:
                    for task in reversed(self.repo.recoverable_tasks()):
                        if len(self.active) >= self.settings.max_tasks:
                            break
                        if task["id"] in self.active or task["status"] not in {"queued", "recovering"}:
                            continue
                        if task["mode"] == "live" and not self.redis.available:
                            continue
                        if not await self.redis.acquire(task["id"]):
                            continue
                        task = self.repo.transition_task(task["id"], "running", expected={"queued", "recovering"},
                                                        attempt=task["attempt"] + 1, phase="恢复研究" if task["status"] == "recovering" else "开始研究", owner=self.redis.owner)
                        if task:
                            self.cancel_flags[task["id"]] = False
                            self.active[task["id"]] = asyncio.create_task(self._run(task), name=task["id"])
                await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("task scheduler error")
                await asyncio.sleep(1)

    async def _heartbeat(self, task, work):
        while True:
            await asyncio.sleep(8)
            updated = self.repo.update_task(task["id"], heartbeat_at=now())
            await self.redis.project(updated)
            if self.redis.available:
                valid = await self.redis.renew(task["id"])
            else:
                valid = task["mode"] != "live"
            if not valid and task["mode"] == "live":
                self.lease_lost.add(task["id"])
                work.cancel()
                return

    async def _run(self, task):
        task_id = task["id"]
        work = asyncio.current_task()
        heartbeat = asyncio.create_task(self._heartbeat(task, work))
        async def emit(event_type, data, agent="orchestrator", tool=None):
            return await self.emit(task_id, event_type, data, agent, tool)
        try:
            await emit("task_started", {"message": "研究执行开始", "mode": task["mode"]})
            from app.engine.runner import run_research
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                result = await run_research(task, self.settings, self.repo, emit, lambda: self.cancel_flags.get(task_id, False))
            if self.cancel_flags.get(task_id):
                raise asyncio.CancelledError()
            report = result.get("report_markdown", "")
            if not report.strip():
                raise ValueError("研究执行结束但未生成有效报告")
            folder = self.settings.data_dir / "tasks" / task_id
            folder.mkdir(parents=True, exist_ok=True)
            temporary = folder / "report.md.tmp"
            temporary.write_text(report, encoding="utf-8")
            temporary.replace(folder / "report.md")
            self.repo.add_artifact(task_id, "研究报告.md", str(folder / "report.md"), "text/markdown", "report")
            self.repo.update_task(task_id, report_markdown=report, plan=result.get("plan", []), status="succeeded", phase="研究已完成", progress=100, error=None)
            await emit("task_completed", {"message": "报告已生成，可查看来源与下载"})
        except asyncio.CancelledError:
            if self.stopping:
                self.repo.update_task(task_id, status="recovering", phase="服务停止，等待下次恢复")
                self.repo.append_event(task_id, "task_interrupted", {"message": "服务停止，下次启动将扫描检查点"})
            elif task_id in self.lease_lost:
                self.repo.update_task(task_id, status="failed", phase="执行租约失效", error="Redis 连接或执行租约失效，任务已停止以避免重复执行")
                await emit("task_failed", {"message": "执行租约失效"})
            else:
                self.repo.update_task(task_id, status="cancelled", phase="已取消")
                await emit("task_cancelled", {"message": "任务已取消，已保留执行记录"})
        except TimeoutError:
            self.repo.update_task(task_id, status="failed", phase="执行超时", error="研究超过设定运行时限，请缩小范围或调整配置")
            await emit("task_failed", {"message": "研究超过设定运行时限"})
        except Exception as exc:
            # Store only a concise message; HTTP provider errors must not expose key-bearing URLs.
            message = str(exc)[:500] or type(exc).__name__
            for secret in [self.settings.model_api_key, self.settings.search_api_key, self.settings.embedding_api_key]:
                if secret:
                    message = message.replace(secret, "[已隐藏]")
            self.repo.update_task(task_id, status="failed", phase="研究失败", error=message)
            await emit("task_failed", {"message": message})
            logger.error("research task %s failed: %s", task_id, message)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.redis.release(task_id)
            final = self.repo.update_task(task_id, active_agents=[], current_agent="")
            if await self.redis.project(final):
                self.repo.mark_redis_synced(task_id, final["version"])
            self.active.pop(task_id, None)
            self.cancel_flags.pop(task_id, None)
            self.lease_lost.discard(task_id)

    async def cancel(self, task_id):
        task = self.repo.get_task(task_id)
        if task["status"] in {"succeeded", "failed", "cancelled"}:
            return task
        if task_id in self.active:
            self.repo.update_task(task_id, status="cancel_requested", phase="正在取消")
            self.cancel_flags[task_id] = True
            self.active[task_id].cancel()
        else:
            self.repo.update_task(task_id, status="cancelled", phase="已取消")
            await self.emit(task_id, "task_cancelled", {"message": "任务在开始前已取消"})
        return self.repo.get_task(task_id)

    async def close(self):
        self.stopping = True
        if self.scheduler:
            self.scheduler.cancel()
            await asyncio.gather(self.scheduler, return_exceptions=True)
        pending = list(self.active.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self.redis.close()
        self.lock.close()
