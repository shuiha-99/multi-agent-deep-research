from pathlib import Path
import time

import pytest

from app.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path):
    # Explicitly isolate credentials and storage: tests never call paid services.
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "research",
        redis_url="",
        mode="demo",
        model_name="",
        model_base_url="",
        model_api_key="",
        search_provider="tavily",
        search_api_key="",
        search_base_url="",
        embedding_model="",
        embedding_base_url="",
        embedding_api_key="",
        demo_step_delay=0,
    )


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(settings)) as active:
        yield active


def wait_for_terminal(client, task_id, timeout=20):
    deadline = time.monotonic() + timeout
    task = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/research/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in {"succeeded", "failed", "cancelled"}:
            return task
        time.sleep(0.04)
    pytest.fail(f"Task did not reach a terminal state: {task}")


def load_samples(client):
    response = client.post("/api/samples/load")
    assert response.status_code == 200, response.text
    return response.json()


def create_sample_task(client, *, question=None, mode="demo"):
    samples = load_samples(client)
    payload = {
        "question": question or "分析2026年1月至3月咖啡用品销售变化，结合上传产品知识库和离线示例市场资料给出选品建议，并核对来源与数据限制",
        "mode": mode,
        "dataset_ids": [item["id"] for item in samples["datasets"]],
        "document_ids": [item["id"] for item in samples["documents"]],
        "scope": {"start_date": "2026-01-01", "end_date": "2026-03-31", "category": "咖啡用品"},
    }
    response = client.post("/api/research/tasks", json=payload)
    return response, payload
