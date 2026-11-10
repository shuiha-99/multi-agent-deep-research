import pytest

from app.config import Settings
from app.engine.tools import ResearchTools
from app.ingestion import load_samples
from app.storage import Repository


def environment(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, redis_url="", model_api_key="", search_api_key="", embedding_api_key="")
    repo = Repository(tmp_path)
    dataset = load_samples(repo)["datasets"][0]
    task = repo.create_task({"question": "验证统计图表契约", "mode": "demo", "dataset_ids": [dataset["id"]], "document_ids": []})
    return ResearchTools(task, settings, repo), repo, dataset["id"]


def test_statistics_tools_publish_allowed_group_and_chart_metric_enums(tmp_path):
    tools, _, _ = environment(tmp_path)
    registry = {tool.name: tool for tool in tools.langchain_tools()}
    aggregate_schema = registry["aggregate_metrics"].args_schema.model_json_schema()
    chart_schema = registry["create_chart"].args_schema.model_json_schema()
    assert set(aggregate_schema["properties"]["group_by"]["enum"]) == {"month", "category", "product_id", "none"}
    assert set(chart_schema["properties"]["metric"]["enum"]) == {"sales_amount", "order_count", "net_sales_amount", "average_order_value"}
    assert "statistics_artifact_id" in registry["aggregate_metrics"].description
    assert "never csv_artifact_id" in registry["create_chart"].description


@pytest.mark.asyncio
async def test_aggregate_separates_statistics_json_and_csv_ids_for_chart_input(tmp_path):
    tools, repo, dataset_id = environment(tmp_path)
    result = await tools.aggregate_metrics(dataset_id)
    statistics = repo.get_artifact(result["statistics_artifact_id"])
    csv = repo.get_artifact(result["csv_artifact_id"])
    assert statistics["kind"] == "statistics" and statistics["media_type"] == "application/json"
    assert csv["kind"] == "table" and csv["media_type"] == "text/csv"
    assert result["statistics_artifact_id"] != result["csv_artifact_id"]
    assert result["artifact_ids"] == [result["statistics_artifact_id"], result["csv_artifact_id"]]
    with pytest.raises(ValueError, match="统计结果产物"):
        await tools.create_chart(result["csv_artifact_id"])
    chart = await tools.create_chart(result["statistics_artifact_id"], metric="sales_amount")
    saved_chart = repo.get_artifact(chart["artifact_ids"][0])
    assert saved_chart["metadata"]["statistics_artifact_id"] == result["statistics_artifact_id"]
    assert saved_chart["metadata"]["metric"] == "sales_amount"
