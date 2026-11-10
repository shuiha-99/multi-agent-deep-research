import json
from pathlib import Path

import pytest

from app.config import Settings
from app.engine.tools import ResearchTools
from app.ingestion import ingest_dataset, load_samples
from app.storage import Repository


def build_tools(tmp_path, csv_content=None):
    settings = Settings(_env_file=None, data_dir=tmp_path, redis_url="", model_api_key="", embedding_api_key="", search_api_key="")
    repo = Repository(tmp_path)
    dataset = load_samples(repo)["datasets"][0] if csv_content is None else ingest_dataset(repo, "ratio.csv", csv_content)
    task = repo.create_task({"question": "核对退款金额占比", "mode": "demo", "dataset_ids": [dataset["id"]], "document_ids": [], "scope": {}})
    return ResearchTools(task, settings, repo), repo, dataset["id"]


@pytest.mark.asyncio
async def test_refund_amount_ratio_matches_order_amounts_and_saved_evidence(tmp_path):
    tools, repo, dataset_id = build_tools(tmp_path)
    result = await tools.aggregate_metrics(dataset_id, group_by="month")
    data = result["data"]
    assert data["totals"]["refund_amount_ratio"] == pytest.approx(180 / 3900)
    assert [row["refund_amount_ratio"] for row in data["rows"]] == pytest.approx([20 / 900, 60 / 1200, 100 / 1800])
    assert "退款金额占比" in data["definitions"]["refund_amount_ratio"]
    assert "非退款订单率" in data["definitions"]["refund_amount_ratio"]
    statistics = repo.get_artifact(result["artifact_ids"][0])
    persisted = json.loads(Path(statistics["path"]).read_text(encoding="utf-8"))
    assert persisted["totals"]["refund_amount_ratio"] == data["totals"]["refund_amount_ratio"]
    source = next(item for item in repo.list_evidence(tools.task["id"]) if item["id"] in result["evidence_ids"])
    assert json.loads(source["content"])["totals"]["refund_amount_ratio"] == data["totals"]["refund_amount_ratio"]


@pytest.mark.asyncio
@pytest.mark.parametrize("csv_content, expected", [
    (b"order_id,order_date,paid_amount,refund_amount\nA,2026-01-01,0,10\n", None),
    (b"order_id,order_date,paid_amount\nA,2026-01-01,100\n", None),
    (b"order_id,order_date,paid_amount,refund_amount\nA,2026-01-01,100,0\n", 0.0),
])
async def test_refund_amount_ratio_null_and_zero_boundaries(tmp_path, csv_content, expected):
    tools, _, dataset_id = build_tools(tmp_path, csv_content)
    result = await tools.aggregate_metrics(dataset_id, group_by="month")
    assert result["data"]["totals"]["refund_amount_ratio"] == expected
    assert result["data"]["rows"][0]["refund_amount_ratio"] == expected
