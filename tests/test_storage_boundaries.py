from concurrent.futures import ThreadPoolExecutor

import pytest

from app.storage import Repository


def test_evidence_reused_by_two_tasks_has_separate_ownership(tmp_path):
    repo = Repository(tmp_path / "data")
    one = repo.create_task({"question": "第一个研究"})
    two = repo.create_task({"question": "第二个研究"})
    first = repo.add_evidence(one["id"], "web", "相同来源", "来源正文", source_key="shared-source")
    again = repo.add_evidence(one["id"], "web", "相同来源", "来源正文", source_key="shared-source")
    other = repo.add_evidence(two["id"], "web", "相同来源", "来源正文", source_key="shared-source")
    assert first["id"] == again["id"]
    assert first["id"] != other["id"]
    assert [e["id"] for e in repo.list_evidence(one["id"])] == [first["id"]]
    assert [e["id"] for e in repo.list_evidence(two["id"])] == [other["id"]]


def test_empty_document_scope_does_not_expand_to_all_documents(tmp_path):
    repo = Repository(tmp_path / "data")
    doc = repo.add_document("私有示例", "不应被未选择该文档的任务检索")
    assert repo.get_chunks([]) == []
    assert repo.get_chunks([doc["id"]])


def test_artifact_and_dataset_paths_stay_inside_data_dir(tmp_path):
    repo = Repository(tmp_path / "data")
    outside = tmp_path / "outside.txt"
    outside.write_text("not a public artifact", encoding="utf-8")
    with pytest.raises(ValueError, match="目录"):
        repo.add_artifact("task", "outside", str(outside), "text/plain", "table")
    with pytest.raises(ValueError, match="目录"):
        repo.add_dataset("outside", str(outside), ["x"], 1)


def test_concurrent_events_get_unique_increasing_sequences(tmp_path):
    repo = Repository(tmp_path / "data")
    task = repo.create_task({"question": "并发事件研究"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: repo.append_event(task["id"], "progress", {"index": index}), range(24)))
    events = repo.list_events(task["id"])
    assert [event["seq"] for event in events] == list(range(1, 25))
    assert len({event["id"] for event in events}) == 24
    assert [event["seq"] for event in repo.list_events(task["id"], after_seq=20)] == [21, 22, 23, 24]
