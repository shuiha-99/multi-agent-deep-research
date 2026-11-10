import io

from conftest import create_sample_task, load_samples, wait_for_terminal


EXPECTED_TOOLS = {
    "search_web", "fetch_page", "extract_page_tables", "search_knowledge", "read_chunk",
    "get_document_metadata", "inspect_dataset", "aggregate_metrics", "create_chart",
}


def test_health_config_and_registry_are_honest_without_credentials(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["services"]["model"]["configured"] is False
    config = client.get("/api/config")
    assert config.status_code == 200
    body = config.json()
    assert body["missing_live_config"]
    assert {tool["name"] for tool in body["tools"]} == EXPECTED_TOOLS


def test_samples_load_idempotently(client):
    first = load_samples(client)
    second = load_samples(client)
    assert {d["id"] for d in first["documents"]} == {d["id"] for d in second["documents"]}
    assert {d["id"] for d in first["datasets"]} == {d["id"] for d in second["datasets"]}
    assert first["datasets"][0]["row_count"] == 19


def test_live_mode_rejects_missing_configuration_instead_of_using_demo(client):
    response, _ = create_sample_task(client, mode="live")
    assert response.status_code == 422
    assert client.get("/api/research/tasks").json() == []


def test_unknown_resources_are_rejected_before_creating_task(client):
    response = client.post("/api/research/tasks", json={"question": "分析这个数据", "mode": "demo", "dataset_ids": ["dataset_missing"]})
    assert response.status_code in {400, 404, 422}
    assert client.get("/api/research/tasks").json() == []


def test_csv_and_markdown_upload(client):
    data = b"order_id,order_date,paid_amount,refund_amount\nA,2026-03-01,120,0\n"
    response = client.post("/api/datasets", files={"file": ("orders.csv", io.BytesIO(data), "text/csv")})
    assert response.status_code == 201, response.text
    assert response.json()["row_count"] == 1
    doc = client.post("/api/documents", files={"file": ("notes.md", "# 产品说明\n仅用于上传验证".encode(), "text/markdown")})
    assert doc.status_code == 201, doc.text
    assert doc.json()["chunk_count"] >= 1


def test_unsupported_file_is_rejected(client):
    response = client.post("/api/documents", files={"file": ("program.exe", b"not a document", "application/octet-stream")})
    assert response.status_code in {400, 415, 422}


def test_reversed_dates_and_blank_questions_are_rejected(client):
    for payload in [
        {"question": "  "},
        {"question": "研究咖啡用品", "scope": {"start_date": "2026-03-31", "end_date": "2026-01-01"}},
    ]:
        assert client.post("/api/research/tasks", json=payload).status_code == 422
    assert client.get("/api/research/tasks").json() == []


def test_configuration_never_returns_secret_values(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    settings.model_api_key = "model-key-test-sentinel"
    settings.search_api_key = "search-key-test-sentinel"
    settings.embedding_api_key = "embedding-key-test-sentinel"
    with TestClient(create_app(settings)) as active:
        for path in ["/api/config", "/api/health"]:
            response = active.get(path)
            assert response.status_code == 200
            assert "key-test-sentinel" not in response.text


def test_demo_research_has_real_sources_metrics_and_downloadable_report(client):
    response, _ = create_sample_task(client)
    assert response.status_code == 202, response.text
    task = wait_for_terminal(client, response.json()["id"])
    assert task["status"] == "succeeded", task.get("error")
    assert task["progress"] == 100
    report = client.get(f"/api/research/tasks/{task['id']}/report")
    assert report.status_code == 200
    markdown = report.json()["markdown"]
    assert "示例" in markdown or "模拟" in markdown
    assert "3900" in markdown or "3,900" in markdown
    sources = client.get(f"/api/research/tasks/{task['id']}/sources").json()
    assert sources
    assert all(source["task_id"] == task["id"] for source in sources)
    events = client.get(f"/api/research/tasks/{task['id']}/events").json()
    assert [event["seq"] for event in events] == sorted({event["seq"] for event in events})
    assert EXPECTED_TOOLS <= {event["tool"] for event in events if event["event_type"] == "tool_completed"}
    assert task["metrics"]["total_calls"] >= 9
    assert task["metrics"]["success_rate"] == 1
    assert any(artifact["kind"] == "chart" for artifact in task["artifacts"])
    for artifact in task["artifacts"]:
        result = client.get(f"/api/artifacts/{artifact['id']}")
        assert result.status_code == 200
        assert result.content
        if artifact["kind"] == "statistics":
            statistics = result.json()
            assert statistics["is_demo"] is True
            assert statistics["totals"]["sales_amount"] == 3900
            assert statistics["totals"]["order_count"] == 16
            assert statistics["totals"]["refund_amount"] == 180
            assert statistics["totals"]["net_sales_amount"] == 3720
            assert [row["sales_amount"] for row in statistics["rows"]] == [900, 1200, 1800]
    download = client.get(f"/api/research/tasks/{task['id']}/report/download")
    assert download.status_code == 200
    assert "attachment" in download.headers.get("content-disposition", "")


def test_websocket_replays_events_from_cursor(client):
    response, _ = create_sample_task(client, question="仅根据上传CSV分析咖啡用品销售额")
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"])
    assert task["status"] == "succeeded", task.get("error")
    events = client.get(f"/api/research/tasks/{task['id']}/events").json()
    assert events
    cursor = events[-1]["seq"] - 1
    with client.websocket_connect(f"/api/research/tasks/{task['id']}/stream?after_seq={cursor}") as socket:
        event = socket.receive_json()
        assert event["seq"] == cursor + 1
        assert event["task_id"] == task["id"]


def test_data_only_task_does_not_need_network_or_unselected_documents(client):
    samples = load_samples(client)
    response = client.post("/api/research/tasks", json={
        "question": "仅根据上传CSV计算销售额和订单量，不联网，不查询知识库",
        "mode": "demo", "dataset_ids": [samples["datasets"][0]["id"]], "document_ids": [],
    })
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"])
    assert task["status"] == "succeeded", task.get("error")
    assert {item["agent"] for item in task["plan"]} == {"data"}
    sources = client.get(f"/api/research/tasks/{task['id']}/sources").json()
    assert not any(source["source_type"] in {"web", "knowledge"} for source in sources)


def test_websocket_supports_same_origin_custom_local_port_and_rejects_other_sites(settings):
    import pytest
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app.main import create_app

    application = create_app(settings)
    task = application.state.repo.create_task({"question": "自定义端口测试"})
    application.state.repo.update_task(task["id"], status="succeeded", progress=100)
    application.state.repo.append_event(task["id"], "task_completed", {"message": "完成"})
    with TestClient(application, base_url="http://127.0.0.1:8123") as active:
        route = f"ws://127.0.0.1:8123/api/research/tasks/{task['id']}/stream"
        with active.websocket_connect(route, headers={"Origin": "http://127.0.0.1:8123"}) as socket:
            assert socket.receive_json()["event_type"] == "task_completed"
        with pytest.raises(WebSocketDisconnect):
            with active.websocket_connect(route, headers={"Origin": "https://unrelated.example"}):
                pass
