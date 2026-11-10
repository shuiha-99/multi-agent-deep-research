"""Service restart checks; live API recovery still requires provider verification."""
import time

from fastapi.testclient import TestClient

from app.storage import Repository
from conftest import create_sample_task, wait_for_terminal


def test_startup_recovers_running_task_before_first_checkpoint(settings):
    from app.main import create_app

    repo = Repository(settings.data_dir)
    task = repo.create_task({"question": "研究咖啡用品市场，使用离线示例资料", "mode": "demo"})
    repo.update_task(task["id"], status="running", attempt=1)
    with TestClient(create_app(settings)) as client:
        recovered = wait_for_terminal(client, task["id"])
        assert recovered["status"] == "succeeded", recovered.get("error")
        assert recovered["attempt"] >= 2
        assert len(client.get("/api/research/tasks").json()) == 1


def test_startup_finishes_pending_cancellation_without_rerunning(settings):
    from app.main import create_app

    repo = Repository(settings.data_dir)
    task = repo.create_task({"question": "已请求取消的研究", "mode": "demo"})
    repo.update_task(task["id"], status="cancel_requested", attempt=1)
    with TestClient(create_app(settings)) as client:
        result = wait_for_terminal(client, task["id"])
        assert result["status"] == "cancelled"
        assert result["metrics"]["total_calls"] == 0
        assert result["report_markdown"] is None


def test_completed_task_is_not_automatically_executed_again(settings):
    from app.main import create_app

    repo = Repository(settings.data_dir)
    task = repo.create_task({"question": "已完成的研究", "mode": "demo"})
    repo.update_task(task["id"], status="succeeded", progress=100, report_markdown="# 已保存报告", attempt=1)
    with TestClient(create_app(settings)) as client:
        result = client.get(f"/api/research/tasks/{task['id']}").json()
        assert result["status"] == "succeeded"
        assert result["report_markdown"] == "# 已保存报告"
        assert result["attempt"] == 1
        assert result["metrics"]["total_calls"] == 0


def test_cancel_and_explicit_retry_create_a_new_task(settings):
    from app.main import create_app

    settings.demo_step_delay = 0.5
    with TestClient(create_app(settings)) as client:
        response, _ = create_sample_task(client)
        assert response.status_code == 202
        task_id = response.json()["id"]
        cancelled = client.post(f"/api/research/tasks/{task_id}/cancel")
        assert cancelled.status_code in {200, 202}, cancelled.text
        result = wait_for_terminal(client, task_id)
        assert result["status"] == "cancelled", result
        retried = client.post(f"/api/research/tasks/{task_id}/retry")
        assert retried.status_code in {200, 202}, retried.text
        assert retried.json()["id"] != task_id
        assert client.get(f"/api/research/tasks/{task_id}").json()["status"] == "cancelled"


def test_restart_resumes_saved_graph_step_without_repeating_completed_tools(settings):
    from app.main import create_app

    settings.demo_step_delay = 0.8
    first_app = create_app(settings)
    with TestClient(first_app) as client:
        response, _ = create_sample_task(client, question="仅根据上传CSV计算销售额和订单量")
        assert response.status_code == 202
        task_id = response.json()["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            task = client.get(f"/api/research/tasks/{task_id}").json()
            if task["progress"] == 88:
                break
            assert task["status"] not in {"failed", "cancelled"}, task
            time.sleep(0.02)
        else:
            raise AssertionError("Did not reach report phase before interruption")
        before_calls = first_app.state.repo.metrics(task_id)["total_calls"]
        assert before_calls == 3
        # Leaving TestClient stops the owned service while report generation is pending.

    assert Repository(settings.data_dir).get_task(task_id)["status"] == "recovering"
    settings.demo_step_delay = 0
    with TestClient(create_app(settings)) as restarted:
        completed = wait_for_terminal(restarted, task_id)
        assert completed["status"] == "succeeded", completed.get("error")
        assert completed["attempt"] == 2
        assert completed["metrics"]["total_calls"] == before_calls
        events = restarted.get(f"/api/research/tasks/{task_id}/events").json()
        assert any(event["event_type"] == "checkpoint_resumed" for event in events)
        assert len(restarted.get("/api/research/tasks").json()) == 1
