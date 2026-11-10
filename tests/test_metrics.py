from app.storage import Repository


def test_metrics_separate_cache_lookups_from_analysis_and_keep_usage_scoped(tmp_path):
    repo = Repository(tmp_path / "metrics")
    task = repo.create_task({"question": "统计样例"})
    other = repo.create_task({"question": "其他任务"})
    assert task["thread_id"] == task["id"]
    for tool, cache_status in [("search_web", "miss"), ("fetch_page", "exact_hit"),
                               ("search_web", "semantic_hit"), ("aggregate_metrics", "miss"),
                               ("create_chart", "miss")]:
        repo.record_tool_run(task["id"], "data" if tool in {"aggregate_metrics", "create_chart"} else "web",
                             tool, "success", 20, cache_status)
    repo.append_event(task["id"], "model_completed", {"usage_metadata": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}})
    repo.append_event(task["id"], "model_completed", {"usage_metadata": None})
    repo.append_event(other["id"], "model_completed", {"usage_metadata": {"total_tokens": 999}})
    metrics = repo.metrics(task["id"])
    assert metrics["total_calls"] == 5
    assert metrics["cache_lookups"] == 3
    assert metrics["exact_hits"] == metrics["semantic_hits"] == 1
    assert metrics["cache_hit_rate"] == 2 / 3
    assert metrics["token_usage"]["total_tokens"] == 14
    assert metrics["token_usage"]["available"] is True
    assert metrics["token_usage"]["coverage"] == "partial"
    assert metrics["token_usage"]["reported_calls"] == 1
    assert metrics["token_usage"]["completed_model_calls"] == 2


def test_no_provider_usage_is_reported_as_unavailable(tmp_path):
    repo = Repository(tmp_path / "metrics")
    metrics = repo.metrics()
    assert metrics["cache_hit_rate"] == 0
    assert metrics["token_usage"]["available"] is False
    assert metrics["token_usage"]["coverage"] == "unavailable"
