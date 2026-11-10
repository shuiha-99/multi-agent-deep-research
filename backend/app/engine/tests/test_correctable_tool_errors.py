import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from app.config import Settings
from app.engine.middleware import BudgetExceeded, ResearchMiddleware, ToolExecutor
from app.engine.tools import ResearchTools
from app.storage import Repository


@pytest.mark.asyncio
async def test_bad_chart_argument_can_be_corrected_but_cannot_escape_budget(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, max_tool_calls=2)
    repo = Repository(tmp_path)
    task = repo.create_task({"question": "核对绘图参数", "mode": "demo"})
    async def emit(kind, data, agent="orchestrator", tool=None):
        return repo.append_event(task["id"], kind, data, agent, tool)
    middleware = ResearchMiddleware(ToolExecutor(ResearchTools(task, settings, repo), emit, lambda: False), "data")
    request = SimpleNamespace(tool_call={"id": "bad-chart", "name": "create_chart", "args": {"statistics_artifact_id": "csv_wrong_kind"}})
    async def invalid_handler(call):
        raise ValueError("需要当前任务生成的统计结果产物。")
    error = await middleware.awrap_tool_call(request, invalid_handler)
    assert isinstance(error, ToolMessage) and error.status == "error"
    assert error.tool_call_id == "bad-chart"
    assert json.loads(error.content)["correctable"] is True
    assert repo.tool_runs(task["id"])[0]["status"] == "failed"

    async def corrected_handler(call):
        return ToolMessage(content='{"status":"ok"}', tool_call_id=call.tool_call["id"])
    request.tool_call["id"] = "corrected-chart"
    success = await middleware.awrap_tool_call(request, corrected_handler)
    assert success.status == "success"
    assert [event["event_type"] for event in repo.list_events(task["id"])].count("tool_failed") == 1
    with pytest.raises(BudgetExceeded):
        await middleware.awrap_tool_call(request, corrected_handler)


@pytest.mark.asyncio
async def test_permission_denial_is_not_a_correctable_tool_message(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    repo = Repository(tmp_path)
    task = repo.create_task({"question": "核对工具权限", "mode": "demo"})
    async def emit(*args, **kwargs):
        return None
    middleware = ResearchMiddleware(ToolExecutor(ResearchTools(task, settings, repo), emit, lambda: False), "data")
    async def forbidden_handler(call):
        raise AssertionError("Unauthorized tools must not run")
    request = SimpleNamespace(tool_call={"id": "forbidden", "name": "execute", "args": {}})
    with pytest.raises(ValueError, match="权限"):
        await middleware.awrap_tool_call(request, forbidden_handler)
