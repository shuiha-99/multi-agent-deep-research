"""Opt-in live acceptance using this project's configured paid services.

The script owns an isolated API process/data directory. It never controls port
8000, changes .env, flushes Redis, or prints credentials. Each stage is recorded
independently so a failed stage cannot be mistaken for complete acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.storage import Repository  # noqa: E402

TERMINAL = {"succeeded", "failed", "cancelled"}
BUSINESS_TOOLS = {"search_web", "fetch_page", "extract_page_tables", "search_knowledge",
                  "read_chunk", "get_document_metadata", "inspect_dataset", "aggregate_metrics", "create_chart"}
DATA_TOOLS = {"inspect_dataset", "aggregate_metrics", "create_chart"}


class Acceptance:
    def __init__(self, args):
        self.args = args
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        self.output = ROOT / "artifacts" / "acceptance" / self.run_id
        self.output.mkdir(parents=True)
        self.data_dir = ROOT / ".cache" / "live-acceptance" / self.run_id
        self.data_dir.mkdir(parents=True)
        self.settings = Settings()
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{args.port}",
                                   timeout=httpx.Timeout(20, connect=3), trust_env=False)
        self.processes = []
        self.log_streams = []
        self.current_process = None
        self.resources = None
        self.report = {"run_id": self.run_id, "started_at": datetime.now(timezone.utc).isoformat(),
                       "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       "real_services": True, "data_isolation": True, "synthetic_business_data": True,
                       "api_port": args.port, "checks": [], "tasks": {}, "passed": False,
                       "scope": "configured live APIs and isolated local application; no production-capacity claim"}

    def safe(self, value):
        if isinstance(value, dict):
            return {k: self.safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.safe(v) for v in value]
        if isinstance(value, str):
            for secret in (self.settings.model_api_key, self.settings.embedding_api_key, self.settings.search_api_key):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return value

    def save(self):
        (self.output / "result.json").write_text(json.dumps(self.safe(self.report), ensure_ascii=False, indent=2), encoding="utf-8")

    def check(self, name, passed=True, **details):
        row = {"name": name, "passed": bool(passed), **details}
        self.report["checks"].append(row)
        self.save()
        print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def start(self, label):
        stream = (self.output / f"api-{label}.log").open("w", encoding="utf-8")
        self.log_streams.append(stream)
        child_env = dict(os.environ)
        child_env.update({"PYTHONUTF8": "1", "RESEARCH_DATA_DIR": str(self.data_dir),
                          "RESEARCH_TASK_TIMEOUT_SECONDS": "600", "RESEARCH_TOOL_TIMEOUT_SECONDS": "60",
                          "RESEARCH_MAX_TASKS": "1"})
        temporary = self.data_dir / "tmp"
        temporary.mkdir(exist_ok=True)
        child_env.update({"TMP": str(temporary), "TEMP": str(temporary)})
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
             "--host", "127.0.0.1", "--port", str(self.args.port)],
            cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.processes.append(process)
        self.current_process = process
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned API process exited before ready")
            try:
                if self.client.get("/api/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.1)
        raise TimeoutError("Owned API did not become ready")

    def task(self, task_id):
        return self.request("GET", f"/api/research/tasks/{task_id}").json()

    def events(self, task_id):
        return self.request("GET", f"/api/research/tasks/{task_id}/events").json()

    def create(self, title, question, *, documents=True):
        payload = {"title": title, "question": question, "mode": "live",
                   "dataset_ids": [self.resources["datasets"][0]["id"]],
                   "document_ids": [d["id"] for d in self.resources["documents"]] if documents else [],
                   "scope": {"start_date": "2026-01-01", "end_date": "2026-03-31", "category": "咖啡用品"}}
        return self.request("POST", "/api/research/tasks", json=payload).json()

    def wait(self, task_id, timeout=610):
        deadline = time.monotonic() + timeout
        last_notice = 0
        while time.monotonic() < deadline:
            task = self.task(task_id)
            if task["status"] in TERMINAL:
                return task
            if time.monotonic() - last_notice > 20:
                print(f"WAIT {task_id} {task['status']} calls={task['metrics']['total_calls']}", flush=True)
                last_notice = time.monotonic()
            time.sleep(.15)
        raise TimeoutError("Live task did not reach a terminal state")

    def record_task(self, label, task):
        events = self.events(task["id"])
        sources = self.request("GET", f"/api/research/tasks/{task['id']}/sources").json()
        bundle = {"task": task, "events": events, "sources": sources}
        (self.output / f"task-{label}.json").write_text(json.dumps(self.safe(bundle), ensure_ascii=False, indent=2), encoding="utf-8")
        if task.get("report_markdown"):
            (self.output / f"report-{label}.md").write_text(self.safe(task["report_markdown"]), encoding="utf-8")
        self.report["tasks"][label] = {"id": task["id"], "status": task["status"], "attempt": task["attempt"],
                                        "metrics": task["metrics"], "sources": len(sources),
                                        "tools": sorted({e["tool"] for e in events if e["event_type"] == "tool_completed"}),
                                        "workers": sorted({e["agent"] for e in events if e["event_type"] == "agent_started"})}
        self.save()
        return events, sources

    def validate_result(self, label, task, *, data_only=False):
        events, sources = self.record_task(label, task)
        assert task["status"] == "succeeded", self.safe(task.get("error") or task["status"])
        assert any(value in task["report_markdown"] for value in ("3900", "3,900")), "Report omits expected sales total"
        assert any(value in task["report_markdown"] for value in ("3720", "3,720")), "Report omits expected net sales"
        assert "模拟" in task["report_markdown"], "Report must identify synthetic business data"
        assert sources and all(s["task_id"] == task["id"] for s in sources)
        assert any(e["event_type"] == "model_completed" for e in events)
        assert task["metrics"]["token_usage"]["available"]
        statistics = [a for a in task["artifacts"] if a["kind"] == "statistics"]
        charts = [a for a in task["artifacts"] if a["kind"] == "chart"]
        assert statistics and charts
        data = self.request("GET", f"/api/artifacts/{statistics[0]['id']}").json()
        assert data["is_demo"] is True
        assert data["totals"]["sales_amount"] == 3900
        assert data["totals"]["net_sales_amount"] == 3720
        assert data["totals"]["order_count"] == 16
        png = self.request("GET", f"/api/artifacts/{charts[0]['id']}").content
        assert png.startswith(b"\x89PNG")
        (self.output / f"chart-{label}.png").write_bytes(png)
        markdown = self.request("GET", f"/api/research/tasks/{task['id']}/report/download").text
        assert markdown == task["report_markdown"]
        workers = {e["agent"] for e in events if e["event_type"] == "agent_started"}
        if data_only:
            assert workers == {"data"}
        self.check(label, task_id=task["id"], business_totals=data["totals"], workers=sorted(workers),
                   actual_chart=True, markdown_download_matches=True)
        return events, sources

    def comprehensive(self):
        task = self.create("真实在线综合验收（模拟经营数据）", (
            "这是一次在线综合验收，请分别委派 data、knowledge、web 三个专业助手。"
            "分析已选模拟订单在2026年1月至3月的咖啡用品月销售额、去重订单量、退款与净销售额，生成实际统计图；data按inspect_dataset、aggregate_metrics、create_chart执行。"
            "knowledge从已选产品及经营资料检索，读取相关片段与文档元数据，说明口径与数据局限。"
            "web仅搜索一次咖啡用品公开资料作为背景；另读取 https://docs.python.org/3/library/csv.html 并尝试提取页面表格，用作CSV数据处理的技术核对，明确该文档不是市场证据。"
            "每个助手工作完成即可返回，不追加补查；最终给一份简明中文报告，保留真实证据编号和图表链接，明确店铺数据为模拟，公开信息不能证明店铺变化原因。"
        ))
        task = self.wait(task["id"])
        events, sources = self.validate_result("comprehensive", task)
        assert {e["agent"] for e in events if e["event_type"] == "agent_started"} == {"web", "knowledge", "data"}
        assert any(s["source_type"] in {"web_search", "web_page", "web_table"} and s.get("url", "").startswith("http") for s in sources)
        used = {e["tool"] for e in events if e["event_type"] == "tool_completed"}
        self.check("live_graph_business_tool_observation", tools=sorted(used & BUSINESS_TOOLS),
                   not_needed_or_missing=sorted(BUSINESS_TOOLS - used),
                   note="All nine capabilities are independently verified in the tools stage; each graph may select tools by need.")

    def cancel_retry(self):
        task = self.create("取消与重试验收", "仅分析已选CSV，依次检查数据、按月统计并生成图表。不要联网，不查知识库；最终仅简明报告销售额、订单量、退款、净销售额及模拟数据限制。", documents=False)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            events = self.events(task["id"])
            if any(e["event_type"] == "model_started" for e in events):
                break
            assert self.task(task["id"])["status"] not in TERMINAL
            time.sleep(.05)
        else:
            raise TimeoutError("Did not observe an actual model request before cancellation")
        self.request("POST", f"/api/research/tasks/{task['id']}/cancel")
        cancelled = self.wait(task["id"], timeout=20)
        self.record_task("cancelled", cancelled)
        assert cancelled["status"] == "cancelled"
        self.check("cancel_live_inflight_model", task_id=task["id"])
        retry = self.request("POST", f"/api/research/tasks/{task['id']}/retry").json()
        assert retry["id"] != task["id"]
        completed = self.wait(retry["id"])
        self.validate_result("retry_data_only", completed, data_only=True)
        assert self.task(task["id"])["status"] == "cancelled"

    def recovery(self):
        task = self.create("真实在线检查点恢复验收", "仅分析已选CSV：委派data助手依次检查数据、按月统计并生成图表。不要联网，不查知识库。助手完成后，主智能体写一份简明中文报告，解释销售额、订单量、退款、净销售额和模拟数据局限，并保留引用及图表。", documents=False)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            observed = self.task(task["id"])
            events = self.events(task["id"])
            done = [e for e in events if e["event_type"] == "tool_completed" and e["tool"] in DATA_TOOLS]
            worker_finished = [e for e in events if e["event_type"] == "agent_completed" and e["agent"] == "data"]
            root_waiting = worker_finished and any(e["event_type"] == "model_started" and e["agent"] == "orchestrator" and e["seq"] > worker_finished[-1]["seq"] for e in events)
            if DATA_TOOLS <= {e["tool"] for e in done} and root_waiting and observed["status"] == "running":
                break
            if observed["status"] in TERMINAL:
                self.record_task("recovery_boundary_failed", observed)
                raise AssertionError(observed.get("error") or "Task finished before recovery boundary")
            time.sleep(.04)
        else:
            raise TimeoutError("Did not reach a saved live worker/root-synthesis boundary")
        before = Counter(e["tool"] for e in done)
        self.current_process.kill()
        self.current_process.wait(timeout=10)
        persisted = Repository(self.data_dir)
        interrupted = persisted.get_task(task["id"])
        assert interrupted["status"] == "running"
        assert not any(e["event_type"] == "task_interrupted" for e in persisted.list_events(task["id"]))
        self.start("recovery")
        recovered = self.wait(task["id"], timeout=180)
        events, _ = self.validate_result("recovered", recovered, data_only=True)
        after = Counter(e["tool"] for e in events if e["event_type"] == "tool_completed" and e["tool"] in DATA_TOOLS)
        assert recovered["attempt"] == 2
        assert any(e["event_type"] == "checkpoint_resumed" for e in events)
        assert before == after
        self.check("forced_process_live_checkpoint_resume", task_id=task["id"], attempt=recovered["attempt"],
                   business_tools_before=dict(before), business_tools_after=dict(after),
                   redis_lease_wait_included=True)

    def tool_guards(self):
        async def scenario():
            from app.engine.tools import ResearchTools
            from app.engine.middleware import ToolExecutor
            repo = Repository(self.data_dir)
            dataset_id = self.resources["datasets"][0]["id"]
            document_ids = [d["id"] for d in self.resources["documents"]]
            task = repo.create_task({"question": "真实九工具及错误路径检查", "mode": "live", "dataset_ids": [dataset_id], "document_ids": document_ids,
                                     "scope": {"start_date": "2026-01-01", "end_date": "2026-03-31", "category": "咖啡用品"}})
            # This is a direct tool diagnostic, not a job for the API scheduler.
            repo.update_task(task["id"], status="cancelled", phase="独立工具错误检查")
            async def emit(kind, data, agent="orchestrator", tool=None):
                return repo.append_event(task["id"], kind, data, agent, tool)
            tools = ResearchTools(task, self.settings.model_copy(update={"data_dir": self.data_dir}), repo)
            try:
                await tools.initialize()
                executor = ToolExecutor(tools, emit, lambda: False)
                try:
                    await executor.execute("inspect_dataset", {"dataset_id": "not_selected_dataset"}, agent="data")
                except ValueError:
                    pass
                else:
                    raise AssertionError("Unselected dataset was unexpectedly accepted")
                assert repo.tool_runs(task["id"])[0]["status"] == "failed"
                assert any(e["event_type"] == "tool_failed" for e in repo.list_events(task["id"]))
                await executor.execute("inspect_dataset", {"dataset_id": dataset_id}, agent="data")
                statistics = await executor.execute("aggregate_metrics", {"dataset_id": dataset_id}, agent="data")
                assert statistics["data"]["totals"]["sales_amount"] == 3900
                assert statistics["data"]["totals"]["order_count"] == 16
                await executor.execute("create_chart", {"statistics_artifact_id": statistics["artifact_ids"][0]}, agent="data")
                found = await executor.execute("search_knowledge", {"query": "订单粒度、销售额、退款与产品选品说明"}, agent="knowledge")
                assert found["data"]["matches"]
                await executor.execute("read_chunk", {"chunk_id": found["data"]["matches"][0]["chunk_id"]}, agent="knowledge")
                await executor.execute("get_document_metadata", {"document_id": document_ids[0]}, agent="knowledge")
                search = await executor.execute("search_web", {"query": "咖啡用品 消费 趋势", "entity": "咖啡用品", "intent": "公开背景检索", "max_results": 2}, agent="web")
                assert search["data"]["items"] and all(v["url"].startswith("http") for v in search["data"]["items"])
                page_url = "https://docs.python.org/3/library/stdtypes.html"
                page = await executor.execute("fetch_page", {"url": page_url}, agent="web")
                assert page["data"]["is_demo"] is False
                tables = await executor.execute("extract_page_tables", {"url": page_url}, agent="web")
                assert tables["data"]["is_demo"] is False
                assert tables["data"]["tables"], "Positive HTML table source must yield real cells"
                import pandas as pd
                async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=True) as direct:
                    source = await direct.get(page_url)
                    source.raise_for_status()
                assert len(source.content) < 2 * 1024 * 1024
                frames = await asyncio.to_thread(pd.read_html, io.StringIO(source.text))
                expected = [[str(c) for c in frames[0].columns]] + [[str(c) for c in row] for row in frames[0].head(2).values.tolist()]
                actual = tables["data"]["tables"][0]["rows"][:3]
                normalize = lambda rows: [[re.sub(r"\s+", "", str(c)) for c in row] for row in rows]
                assert normalize(actual) == normalize(expected)
                self.check("real_html_table_cells_match_independent_parser", task_id=task["id"],
                           source_size_bytes=len(source.content), compared_rows=actual,
                           table_count=len(tables["data"]["tables"]))
                successful = {r["tool"] for r in repo.tool_runs(task["id"]) if r["status"] == "success"}
                assert successful == BUSINESS_TOOLS
                self.check("all_nine_live_tools", task_id=task["id"], tools=sorted(successful),
                           real_search_results=len(search["data"]["items"]), actual_page_tables=len(tables["data"]["tables"]),
                           synthetic_order_sales=3900, synthetic_order_count=16)
            finally:
                await tools.close()
            # This direct diagnostic is deliberately not a queued research task.
            repo.update_task(task["id"], status="cancelled", phase="独立工具错误检查已结束")
            self.check("real_tool_scope_error_is_recorded", task_id=task["id"])
        asyncio.run(scenario())

    def cache(self):
        destination = self.output / "cache-evaluation.json"
        result = subprocess.run([sys.executable, "-X", "utf8", "scripts/evaluate_cache.py", "--mode", "live", "--output", str(destination)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=240)
        if not destination.exists():
            raise RuntimeError("Live cache evaluation did not produce its report")
        evaluation = json.loads(destination.read_text(encoding="utf-8"))
        self.report["cache_evaluation"] = {key: evaluation.get(key) for key in
             ("semantic_requests", "semantic_hits", "semantic_hit_rate", "false_hits", "matched_hit_speedup",
              "direct_api_mean_ms", "cache_full_hit_path_mean_ms", "workload_sha256", "windows")}
        self.check("live_cache_fixed_workload", result.returncode == 0 and evaluation["false_hits"] == 0,
                   **self.report["cache_evaluation"])
        self.check("screenshot_30x_target", bool(evaluation.get("matched_hit_speedup") and evaluation["matched_hit_speedup"] >= 30),
                   acceptance_target_only=True, actual=evaluation.get("matched_hit_speedup"))
        self.check("screenshot_30_to_50_percent_target", .3 <= evaluation["semantic_hit_rate"] <= .5,
                   acceptance_target_only=True, actual=evaluation["semantic_hit_rate"])

    def run(self):
        missing = self.settings.missing_live_config()
        if missing:
            raise ValueError("Missing live configuration field names: " + ", ".join(missing))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.args.port))
        self.start("initial")
        config = self.request("GET", "/api/config").json()
        assert config["live_ready"], "Configured Redis/vector/model services are not ready"
        self.report["services"] = {key: config.get(key) for key in ("model_name", "embedding_model", "search_provider")}
        self.resources = self.request("POST", "/api/samples/load").json()
        self.check("isolated_live_application_ready", services=self.report["services"])
        for name in self.args.stages.split(","):
            method = {"tools": self.tool_guards, "comprehensive": self.comprehensive,
                      "cancel_retry": self.cancel_retry, "recovery": self.recovery, "cache": self.cache}[name]
            try:
                method()
            except Exception as exc:
                self.check(name, False, error_type=type(exc).__name__, message=self.safe(str(exc))[:600])
                # Do not leave a failed stage running and competing with later tests.
                try:
                    for task in self.request("GET", "/api/research/tasks").json():
                        if task["status"] not in TERMINAL:
                            self.request("POST", f"/api/research/tasks/{task['id']}/cancel")
                except Exception:
                    pass
        required = [row for row in self.report["checks"] if not row.get("acceptance_target_only")]
        self.report["functional_passed"] = all(row["passed"] for row in required)
        self.report["passed"] = all(row["passed"] for row in self.report["checks"])
        self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def close(self):
        self.client.close()
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        for stream in self.log_streams:
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8125)
    parser.add_argument("--stages", default="tools,comprehensive,cancel_retry,recovery,cache")
    args = parser.parse_args()
    allowed = {"tools", "comprehensive", "cancel_retry", "recovery", "cache"}
    if not set(args.stages.split(",")) <= allowed:
        parser.error("Unknown acceptance stage")
    acceptance = Acceptance(args)
    try:
        acceptance.run()
    except Exception as exc:
        acceptance.check("setup_or_harness", False, error_type=type(exc).__name__, message=acceptance.safe(str(exc))[:500])
    finally:
        acceptance.close()
    print(f"Result: {acceptance.output / 'result.json'}", flush=True)
    return 0 if acceptance.report.get("functional_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
