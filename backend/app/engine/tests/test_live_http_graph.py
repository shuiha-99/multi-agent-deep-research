"""Exercise the real provider adapter and DeepAgents graph without network access."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings
from app.engine.middleware import ToolExecutor
from app.engine import runner
from app.engine.runner import build_chat_model, build_live_graph
from app.engine.tools import ResearchTools
from app.ingestion import load_samples
from app.storage import Repository


@pytest.mark.parametrize("provider", ["generic", "deepseek"])
def test_chat_completions_adapter_delegates_and_computes_report(tmp_path, monkeypatch, provider):
    """No SDK fake: mock only HTTP, then execute delegation and all data tools."""
    async def scenario():
        model_name = "deepseek-flash" if provider == "deepseek" else "research-http-test"
        base_url = "https://api.deepseek.com" if provider == "deepseek" else "https://model.test.invalid/v1"
        settings = Settings(_env_file=None, data_dir=tmp_path, demo_step_delay=0,
                            model_name=model_name, model_base_url=base_url,
                            model_api_key="isolated-test-key")
        repo = Repository(tmp_path)
        samples = load_samples(repo)
        dataset_id = samples["datasets"][0]["id"]
        task = repo.create_task({
            "question": "只分析所选咖啡用品订单，给出月销售额和图表。",
            "mode": "demo",  # Local test fixtures remain visibly marked as simulated.
            "dataset_ids": [dataset_id], "document_ids": [],
            "scope": {"start_date": "2026-01-01", "end_date": "2026-03-31"},
        })
        async def emit(event_type, data, agent="orchestrator", tool=None):
            return repo.append_event(task["id"], event_type, data, agent, tool)

        requests = []
        def completion(request):
            assert request.url == base_url + "/chat/completions"
            assert request.headers["authorization"] == "Bearer isolated-test-key"
            body = json.loads(request.content)
            assert body["model"] == model_name
            assert not body.get("stream")
            if provider == "deepseek":
                assert body["thinking"] == {"type": "disabled"}
            else:
                assert "thinking" not in body
            names = {t["function"]["name"] for t in body["tools"]}
            assert not {"execute", "read_file", "write_file", "edit_file", "delete", "ls", "grep", "glob"} & names
            requests.append(names)
            outputs = [m for m in body["messages"] if m["role"] == "tool"]
            tool_call = None
            if "task" in names:
                assert names <= {"task", "write_todos"}
                if not outputs:
                    tool_call = ("task", {"subagent_type": "data", "description": f"依次检查、按月统计并绘制数据集 {dataset_id}；返回证据及图表编号。"})
                    content = None
                else:
                    content = "# 研究报告\n\n" + outputs[-1]["content"] + "\n\n模拟订单仅用于验证计算链路。"
            else:
                assert {"inspect_dataset", "aggregate_metrics", "create_chart"} <= names
                assert names <= {"inspect_dataset", "aggregate_metrics", "create_chart", "write_todos"}
                # The specialist must not receive any sibling's business tools.
                assert not {"search_web", "read_chunk", "task"} & names
                if not outputs:
                    tool_call = ("inspect_dataset", {"dataset_id": dataset_id})
                    content = None
                elif len(outputs) == 1:
                    inspection = json.loads(outputs[-1]["content"])
                    assert inspection["data"]["row_count"] == 19
                    tool_call = ("aggregate_metrics", {"dataset_id": dataset_id, "group_by": "month"})
                    content = None
                elif len(outputs) == 2:
                    metrics = json.loads(outputs[-1]["content"])
                    assert metrics["data"]["totals"]["sales_amount"] == 3900
                    tool_call = ("create_chart", {"statistics_artifact_id": metrics["artifact_ids"][0]})
                    content = None
                else:
                    metrics = json.loads(outputs[1]["content"])
                    chart = json.loads(outputs[2]["content"])
                    content = (f"销售额 3900，去重订单 16，净销售额 3720。"
                               f"[{metrics['evidence_ids'][0]}]\n\n"
                               f"![实际统计图]({chart['data']['url']})")
            message = {"role": "assistant", "content": content}
            if tool_call:
                name, args = tool_call
                message["tool_calls"] = [{"id": f"http-call-{len(requests)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]
            return httpx.Response(200, json={
                "id": f"chatcmpl-test-{len(requests)}", "object": "chat.completion",
                "created": 1, "model": model_name,
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_call else "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            })

        tools = ResearchTools(task, settings, repo)
        await tools.initialize()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(completion), trust_env=False) as client:
                # Exercise the graph's default production model factory, with
                # only its HTTP transport replaced; never call a real service.
                monkeypatch.setattr(runner, "build_chat_model", lambda configured: build_chat_model(configured, http_async_client=client))
                executor = ToolExecutor(tools, emit, lambda: False)
                graph = await build_live_graph(task, settings, repo, tools, executor, emit, InMemorySaver())
                result = await graph.ainvoke({"messages": [{"role": "user", "content": task["question"]}]}, {"configurable": {"thread_id": task["id"]}})
            report = result["messages"][-1].content
            assert "销售额 3900" in report and "/api/artifacts/" in report
            runs = repo.tool_runs(task["id"])
            assert {r["tool"] for r in runs} == {"task", "inspect_dataset", "aggregate_metrics", "create_chart"}
            assert all(r["status"] == "success" for r in runs)
            assert all(r["agent"] == ("orchestrator" if r["tool"] == "task" else "data") for r in runs)
            assert len(requests) == 6  # root, four worker turns, root synthesis
            events = repo.list_events(task["id"])
            completed = [e for e in events if e["event_type"] == "model_completed"]
            assert len(completed) == 6
            assert all(e["data"]["usage_metadata"]["total_tokens"] == 30 for e in completed)
            plan = repo.get_task(task["id"])["plan"]
            assert len(plan) == 1 and plan[0]["agent"] == "data" and plan[0]["status"] == "completed"
            chart = next(a for a in repo.list_artifacts(task["id"]) if a["kind"] == "chart")
            assert Path(chart["path"]).read_bytes().startswith(b"\x89PNG")
            # The public dispatch schema contains precisely the three planned specialists.
            node = graph.nodes["tools"].bound
            dispatch = node.tools_by_name["task"]
            mappings = [c.cell_contents for c in dispatch.coroutine.__closure__ if isinstance(c.cell_contents, dict)]
            assert any(set(m) == {"web", "knowledge", "data"} for m in mappings)
        finally:
            await tools.close()
    asyncio.run(scenario())
