"""Checkpoint recovery after forcibly terminating an owned API process.

This is a real process test with isolated offline data. It never uses the normal
preview on port 8000 and never sends a signal to a process it did not create.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from app.storage import Repository


ROOT = Path(__file__).resolve().parents[1]
PORT = 8124


def test_force_killed_process_resumes_checkpoint_without_repeating_tools(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", PORT))

    data_dir = tmp_path / "process-data"
    temp_dir = tmp_path / "process-temp"
    temp_dir.mkdir()
    # Explicit environment variables override any future .env configuration.
    child_env = {key: value for key, value in os.environ.items() if not key.startswith("RESEARCH_")}
    child_env.update({
        "PYTHONUTF8": "1", "TMP": str(temp_dir), "TEMP": str(temp_dir),
        "RESEARCH_DATA_DIR": str(data_dir), "RESEARCH_MODE": "demo",
        "RESEARCH_REDIS_URL": "", "RESEARCH_MODEL_NAME": "",
        "RESEARCH_MODEL_BASE_URL": "", "RESEARCH_MODEL_API_KEY": "",
        "RESEARCH_SEARCH_PROVIDER": "tavily", "RESEARCH_SEARCH_API_KEY": "",
        "RESEARCH_SEARCH_BASE_URL": "", "RESEARCH_EMBEDDING_MODEL": "",
        "RESEARCH_EMBEDDING_BASE_URL": "", "RESEARCH_EMBEDDING_API_KEY": "",
        "RESEARCH_DEMO_STEP_DELAY": "2", "RESEARCH_TASK_TIMEOUT_SECONDS": "90",
    })
    processes = []
    logs = []

    def start_server(label):
        stream = (tmp_path / f"{label}.log").open("w", encoding="utf-8")
        logs.append(stream)
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append(process)
        return process

    def get_json(client, path):
        response = client.get(path)
        response.raise_for_status()
        return response.json()

    def wait_ready(process, client):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, f"Owned API process exited unexpectedly: {process.returncode}"
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        raise AssertionError("Owned API process did not become ready")

    try:
        # The first task imports the actual engine (LangGraph/pandas/plotting)
        # in this fresh process. Allow that cold start without retrying failed
        # business requests; the explicit phase deadlines remain bounded.
        with httpx.Client(base_url=f"http://127.0.0.1:{PORT}",
                          timeout=httpx.Timeout(10, connect=1), trust_env=False) as client:
            first_process = start_server("before-kill")
            wait_ready(first_process, client)
            loaded = client.post("/api/samples/load")
            loaded.raise_for_status()
            dataset_id = loaded.json()["datasets"][0]["id"]
            response = client.post("/api/research/tasks", json={
                "question": "仅根据上传CSV计算销售额与订单量，不联网、不查询知识库",
                "mode": "demo", "dataset_ids": [dataset_id], "document_ids": [],
                "scope": {"start_date": "2026-01-01", "end_date": "2026-03-31"},
            })
            assert response.status_code == 202, response.text
            task_id = response.json()["id"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                task = get_json(client, f"/api/research/tasks/{task_id}")
                if task["progress"] == 88:
                    break
                assert task["status"] not in {"failed", "cancelled", "succeeded"}, task
                time.sleep(0.03)
            else:
                raise AssertionError("Did not reach the saved worker/report boundary")
            assert task["metrics"]["total_calls"] == 3
            assert task["attempt"] == 1

            # kill() is TerminateProcess on Windows and SIGKILL on POSIX. The
            # application cannot run its lifespan/finally shutdown handlers.
            first_process.kill()
            first_process.wait(timeout=10)
            persisted = Repository(data_dir)
            interrupted = persisted.get_task(task_id)
            assert interrupted["status"] == "running", interrupted
            assert interrupted["report_markdown"] is None
            assert not any(event["event_type"] == "task_interrupted" for event in persisted.list_events(task_id))

            child_env["RESEARCH_DEMO_STEP_DELAY"] = "0"
            restarted_process = start_server("after-restart")
            wait_ready(restarted_process, client)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                recovered = get_json(client, f"/api/research/tasks/{task_id}")
                if recovered["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Restarted process did not finish the interrupted task")
            assert recovered["status"] == "succeeded", recovered
            assert recovered["attempt"] == 2
            assert recovered["metrics"]["total_calls"] == 3
            assert "3,900.00" in recovered["report_markdown"]
            events = get_json(client, f"/api/research/tasks/{task_id}/events")
            assert any(event["event_type"] == "checkpoint_resumed" for event in events)
            assert len(get_json(client, "/api/research/tasks")) == 1
            summary = {
                "task_id": task_id, "first_pid": first_process.pid, "restart_pid": restarted_process.pid,
                "interruption": "forced_process_kill", "persisted_status_after_kill": interrupted["status"],
                "attempt": recovered["attempt"], "tools_before": 3,
                "tools_after": recovered["metrics"]["total_calls"],
                "checkpoint_resumed": True, "status": recovered["status"],
                "report_contains_expected_sales": True,
            }
            (tmp_path / "process-recovery-result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except Exception as exc:
        for stream in logs:
            stream.flush()
        tails = "\n\n".join(f"{path.name}:\n{path.read_text(encoding='utf-8', errors='replace')[-6000:]}"
                            for path in sorted(tmp_path.glob("*.log")))
        raise AssertionError(f"Process recovery verification failed: {exc}\nOwned API logs:\n{tails}") from exc
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        for stream in logs:
            stream.close()
