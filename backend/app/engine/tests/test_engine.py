import asyncio
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.ingestion import load_samples
from app.storage import Repository
from app.engine.cache import SemanticCache, cosine, demo_vector
from app.engine.middleware import ToolExecutor
from app.engine.registry import TOOL_REGISTRY
from app.engine.runner import build_live_graph, run_research
from app.engine.tools import ResearchTools, public_url


def test_tool_rejects_scope_expansion_and_bad_dates(tmp_path):
    async def scenario():
        settings, repo, task, emit = environment(tmp_path)
        tools = ResearchTools(task, settings, repo)
        dataset_id = task["dataset_ids"][0]
        result = await tools.aggregate_metrics(dataset_id, start_date="2025-01-01", end_date="2027-12-31")
        assert result["data"]["filters"]["start_date"] == "2026-01-01"
        assert result["data"]["filters"]["end_date"] == "2026-03-31"
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            await tools.aggregate_metrics(dataset_id, start_date="2026/01/01")
        with pytest.raises(ValueError, match="品类"):
            await tools.aggregate_metrics(dataset_id, category="茶具")
    asyncio.run(scenario())


def test_live_delegation_and_middleware_with_scripted_model(tmp_path):
    """No provider calls: exercise real DeepAgents subagent dispatch and middleware."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver

    class ScriptedModel(BaseChatModel):
        model_name: str = "research:test-scripted"
        dataset_id: str = ""
        bound_names: list[str] = []

        @property
        def _llm_type(self):
            return "research-scripted"

        def bind_tools(self, tools, **kwargs):
            return self.model_copy(update={"bound_names": [t.name if hasattr(t, "name") else t.get("name", "") for t in tools]})

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            observed = [m for m in messages if isinstance(m, ToolMessage)]
            if "task" in self.bound_names:
                if not observed:
                    message = AIMessage(content="", tool_calls=[{"name": "task", "args": {"subagent_type": "data", "description": "检查任务选中的数据集结构"}, "id": "delegate-test"}])
                else:
                    message = AIMessage(content="数据助手完成检查。" + str(observed[-1].content), usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
            elif "inspect_dataset" in self.bound_names:
                if not observed:
                    message = AIMessage(content="", tool_calls=[{"name": "inspect_dataset", "args": {"dataset_id": self.dataset_id}, "id": "inspect-test"}])
                else:
                    message = AIMessage(content=str(observed[-1].content))
            else:
                raise AssertionError("Unexpected model tool boundary")
            return ChatResult(generations=[ChatGeneration(message=message)])

    async def scenario():
        settings, repo, task, emit = environment(tmp_path)
        tools = ResearchTools(task, settings, repo)
        executor = ToolExecutor(tools, emit, lambda: False)
        model = ScriptedModel(dataset_id=task["dataset_ids"][0])
        graph = await build_live_graph(task, settings, repo, tools, executor, emit, InMemorySaver(), model_override=model)
        result = await graph.ainvoke({"messages": [{"role": "user", "content": "检查数据集"}]}, {"configurable": {"thread_id": task["id"]}})
        assert result["messages"][-1].content
        assert {r["tool"] for r in repo.tool_runs(task["id"])} == {"task", "inspect_dataset"}
        events = repo.list_events(task["id"])
        assert any(e["event_type"] == "model_completed" and e["data"].get("usage_metadata") for e in events)
        assert repo.get_task(task["id"])["plan"][0]["agent"] == "data"
        assert repo.get_task(task["id"])["plan"][0]["status"] == "completed"
    asyncio.run(scenario())


def environment(tmp_path, question="分析咖啡用品销售变化，结合知识库与市场资料给出建议"):
    settings = Settings(data_dir=tmp_path, demo_step_delay=0)
    repo = Repository(tmp_path)
    samples = load_samples(repo)
    task = repo.create_task({"question": question, "mode": "demo", "dataset_ids": [v["id"] for v in samples["datasets"]], "document_ids": [v["id"] for v in samples["documents"]], "scope": {"start_date": "2026-01-01", "end_date": "2026-03-31", "category": "咖啡用品"}})

    async def emit(event_type, data, agent="orchestrator", tool=None):
        return repo.append_event(task["id"], event_type, data, agent, tool)
    return settings, repo, task, emit


def test_all_tools_computation_and_checkpoint_reuse(tmp_path):
    async def scenario():
        settings, repo, task, emit = environment(tmp_path)
        result = await run_research(task, settings, repo, emit, lambda: False)
        actual = {r["tool"] for r in repo.tool_runs(task["id"])}
        assert actual == {t["name"] for t in TOOL_REGISTRY}
        assert "示例模式" in result["report_markdown"]
        assert "3,900.00" in result["report_markdown"]
        artifact = next(a for a in repo.list_artifacts(task["id"]) if a["kind"] == "statistics")
        metrics = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        assert metrics["totals"]["order_count"] == 16
        assert metrics["totals"]["row_count"] == 19
        assert metrics["totals"]["sales_amount"] == 3900
        assert metrics["totals"]["net_sales_amount"] == 3720
        assert metrics["rows"][1]["month_over_month"] == pytest.approx(1 / 3, abs=1e-6)
        assert metrics["rows"][2]["month_over_month"] == .5
        assert metrics["is_demo"] is True
        chart = next(a for a in repo.list_artifacts(task["id"]) if a["kind"] == "chart")
        assert Path(chart["path"]).read_bytes().startswith(b"\x89PNG")
        calls = len(repo.tool_runs(task["id"]))
        resumed = await run_research(task, settings, repo, emit, lambda: False)
        assert resumed["report_markdown"] == result["report_markdown"]
        assert len(repo.tool_runs(task["id"])) == calls
        assert (tmp_path / "checkpoints.sqlite").exists()
    asyncio.run(scenario())


def test_selected_resources_are_required(tmp_path):
    async def scenario():
        settings, repo, task, emit = environment(tmp_path)
        forbidden_doc = task["document_ids"][0]
        forbidden_dataset = task["dataset_ids"][0]
        task["document_ids"], task["dataset_ids"] = [], []
        tools = ResearchTools(task, settings, repo)
        await tools.initialize()
        try:
            assert (await tools.search_knowledge("咖啡"))["data"]["matches"] == []
            with pytest.raises(ValueError, match="未被本任务选中"):
                await tools.inspect_dataset(forbidden_dataset)
            with pytest.raises(ValueError, match="未被本任务选中"):
                await tools.get_document_metadata(forbidden_doc)
        finally:
            await tools.close()
    asyncio.run(scenario())


def test_cache_exact_provenance_and_scope(tmp_path):
    async def scenario():
        settings, repo, task, emit = environment(tmp_path)
        tools = ResearchTools(task, settings, repo)
        await tools.initialize()
        executor = ToolExecutor(tools, emit, lambda: False)
        args = {"query": "咖啡用品行业资料", "entity": "咖啡用品", "intent": "行业资料"}
        first = await executor.execute("search_web", args, agent="web")
        second = await executor.execute("search_web", args, agent="web")
        assert repo.tool_runs(task["id"])[0]["cache_status"] == "exact_hit"
        assert first["data"]["fetched_at"] == second["data"]["fetched_at"]
        new_task = repo.create_task({"question": "另一个研究", "mode": "demo", "scope": task["scope"]})
        next_tools = ResearchTools(new_task, settings, repo)
        await next_tools.initialize()
        cached = await next_tools.search_web(**args)
        assert set(cached["evidence_ids"]).isdisjoint(first["evidence_ids"])
        assert next_tools.last_cache_status == "exact_hit"
        next_tools.task["scope"] = {"start_date": "2025-01-01"}
        await next_tools.search_web(**args)
        assert next_tools.last_cache_status == "miss"
        await tools.close()
        await next_tools.close()
    asyncio.run(scenario())


def test_semantic_cosine_and_hard_entity_filter(tmp_path):
    async def scenario():
        settings = Settings(data_dir=tmp_path, semantic_threshold=.8)
        cache = SemanticCache(settings, "demo")
        await cache.initialize()
        scope = {"tool": "search_web", "entity": "咖啡用品", "intent": "趋势"}
        query = "分析咖啡用品销售趋势"
        # Direct vector injection proves the semantic retrieval branch and hard filter.
        vector = demo_vector("查看咖啡用品销售走势")
        await cache.put(query, scope, {"items": ["validated"]}, vector)
        value, status, _ = await cache.get("查看咖啡用品销售走势", scope)
        assert status == "semantic_hit" and value["items"] == ["validated"]
        value, status, _ = await cache.get("查看咖啡用品销售走势", {**scope, "entity": "茶具"})
        assert value is None and status == "miss"
        assert (await cache.get(query, scope))[1] == "exact_hit"
        await cache.put(query, scope, {"items": ["validated"]}, demo_vector(query))
        assert (await cache.get(query, scope, exact=False))[1] == "semantic_hit"
        assert (await cache.get(query, scope, exact=False, semantic=False))[1] == "miss"
        assert cosine([1, 0], [0, 1]) == 0
        assert cosine([1, 0], [1, 0]) == 1
    asyncio.run(scenario())


def test_same_url_changed_content_gets_its_own_citation(tmp_path):
    """Different query snippets and refreshed pages must not cite stale text."""
    async def scenario():
        import sqlite3
        settings, repo, task, emit = environment(tmp_path)
        tools = ResearchTools(task, settings, repo)
        await tools.initialize()
        try:
            source = {"title": "同一示例页面", "url": "sample://citation-version", "content": "第一段摘要：销售额100。", "html": "<table><tr><td>100</td></tr></table>"}
            tools._web_sources = [source]
            first = await tools.search_web("第一轮查询")
            source["content"] = "第二段摘要：退款额20。"
            second = await tools.search_web("第二轮查询")
            same = await tools.search_web("第三轮不同查询")
            assert first["evidence_ids"] != second["evidence_ids"]
            assert same["evidence_ids"] == second["evidence_ids"]
            by_id = {e["id"]: e for e in repo.list_evidence(task["id"])}
            assert by_id[first["evidence_ids"][0]]["content"] == first["data"]["items"][0]["content"]
            assert by_id[second["evidence_ids"][0]]["content"] == second["data"]["items"][0]["content"]

            page_before = await tools.fetch_page(source["url"])
            table_before = await tools.extract_page_tables(source["url"])
            # Expire the actual cache entry rather than sleeping for its TTL.
            with sqlite3.connect(tools.cache.path) as db:
                db.execute("UPDATE entries SET expires=0")
            source["content"] = "页面更新后的销售额200。"
            source["html"] = "<table><tr><td>200</td></tr></table>"
            page_after = await tools.fetch_page(source["url"])
            table_after = await tools.extract_page_tables(source["url"])
            assert page_before["evidence_ids"] != page_after["evidence_ids"]
            assert table_before["evidence_ids"] != table_after["evidence_ids"]
            assert (await tools.fetch_page(source["url"]))["evidence_ids"] == page_after["evidence_ids"]
            assert (await tools.extract_page_tables(source["url"]))["evidence_ids"] == table_after["evidence_ids"]
            by_id = {e["id"]: e for e in repo.list_evidence(task["id"])}
            assert by_id[page_after["evidence_ids"][0]]["content"] == page_after["data"]["content"]
            assert json.loads(by_id[table_after["evidence_ids"][0]]["content"]) == table_after["data"]["tables"]
        finally:
            await tools.close()
    asyncio.run(scenario())


def test_region_scope_is_applied_and_missing_column_rejected(tmp_path):
    async def scenario():
        import pandas as pd
        settings, repo, task, emit = environment(tmp_path)
        task["scope"]["region"] = "华东"
        tools = ResearchTools(task, settings, repo)
        dataset_id = task["dataset_ids"][0]
        with pytest.raises(ValueError, match="region 字段"):
            await tools.aggregate_metrics(dataset_id)
        dataset = repo.get_dataset(dataset_id)
        frame = pd.read_csv(dataset["path"], encoding="utf-8-sig")
        frame["region"] = ["华东"] + ["华南"] * (len(frame) - 1)
        frame.to_csv(dataset["path"], index=False, encoding="utf-8-sig")
        result = await tools.aggregate_metrics(dataset_id)
        assert result["data"]["filters"]["region"] == "华东"
        assert result["data"]["totals"]["sales_amount"] == 120
        assert result["data"]["totals"]["row_count"] == 1
    asyncio.run(scenario())


def test_role_tool_guards_and_model_failure_terminal_events(tmp_path):
    from types import SimpleNamespace
    from app.engine.middleware import ResearchMiddleware

    async def scenario():
        settings, repo, task, emit = environment(tmp_path)
        executor = ToolExecutor(ResearchTools(task, settings, repo), emit, lambda: False)
        async def forbidden_handler(request):
            raise AssertionError("An unauthorized handler must never run")
        for role, tool in (("orchestrator", "inspect_dataset"), ("data", "task"), ("data", "execute"), ("web", "read_chunk")):
            middleware = ResearchMiddleware(executor, role)
            with pytest.raises(ValueError, match="没有该工具"):
                await middleware.awrap_tool_call(SimpleNamespace(tool_call={"name": tool, "args": {}}), forbidden_handler)
        middleware = ResearchMiddleware(executor, "orchestrator")
        async def broken_model(request):
            raise RuntimeError("provider-exception-with-test-secret")
        with pytest.raises(RuntimeError):
            await middleware.awrap_model_call(None, broken_model)
        async def cancelled_model(request):
            raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await middleware.awrap_model_call(None, cancelled_model)
        events = repo.list_events(task["id"])
        assert [e["event_type"] for e in events] == ["model_started", "model_failed", "model_started", "model_cancelled"]
        assert "test-secret" not in json.dumps(events)
    asyncio.run(scenario())


def test_private_urls_rejected():
    async def scenario():
        for url in ("http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/", "file:///etc/passwd", "http://user:pass@example.com/"):
            with pytest.raises(ValueError):
                await public_url(url)
    asyncio.run(scenario())


def test_cache_namespaces_separate_embedding_endpoints(tmp_path):
    first = Settings(data_dir=tmp_path, embedding_model="shared-name", embedding_base_url="https://one.test.invalid/v1", embedding_api_key="test-key-one")
    second = Settings(data_dir=tmp_path, embedding_model="shared-name", embedding_base_url="https://two.test.invalid/v1", embedding_api_key="test-key-two")
    first_version, second_version = SemanticCache(first, "live").version, SemanticCache(second, "live").version
    assert first_version != second_version
    assert "shared-name" in first_version and "test-key-one" not in first_version


def test_live_graph_builds_with_exact_three_workers(tmp_path):
    async def scenario():
        from langchain_openai import ChatOpenAI
        from langgraph.checkpoint.memory import InMemorySaver
        settings, repo, task, emit = environment(tmp_path)
        tools = ResearchTools(task, settings, repo)
        executor = ToolExecutor(tools, emit, lambda: False)
        model = ChatOpenAI(model="test-compatible-model", api_key="test-not-real", base_url="https://example.invalid/v1")
        graph = await build_live_graph(task, settings, repo, tools, executor, emit, InMemorySaver(), model_override=model)
        # Bound ToolNode exposes the task tool; its schema enumerates only allowed workers.
        tool_node = graph.nodes["tools"].bound
        task_tool = tool_node.tools_by_name["task"]
        mappings = [cell.cell_contents for cell in task_tool.coroutine.__closure__ if isinstance(cell.cell_contents, dict)]
        assert any(set(mapping) == {"web", "knowledge", "data"} for mapping in mappings)
        # Profile exclusions concern model-visible schemas, not the internal
        # ToolNode registry. The HTTP-adapter test verifies those schemas and
        # the role guard test rejects fabricated calls before their handler.
    asyncio.run(scenario())
