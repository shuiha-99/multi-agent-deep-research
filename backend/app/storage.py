"""Small, durable SQLite repository. Redis is a rebuildable runtime projection."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Repository:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "app.sqlite"
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS research_tasks(id TEXT PRIMARY KEY,status TEXT,updated_at TEXT,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_events(task_id TEXT,seq INTEGER,data TEXT NOT NULL,PRIMARY KEY(task_id,seq));
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS document_chunks(id TEXT PRIMARY KEY,document_id TEXT,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,task_id TEXT,source_key TEXT,data TEXT NOT NULL,UNIQUE(task_id,source_key));
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,task_id TEXT,path TEXT,data TEXT NOT NULL,UNIQUE(task_id,path));
                CREATE TABLE IF NOT EXISTS tool_runs(id TEXT PRIMARY KEY,task_id TEXT,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS redis_outbox(task_id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON research_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_runs_task ON tool_runs(task_id);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def safe_path(self, value: str | Path) -> Path:
        path = Path(value).resolve()
        if not path.is_relative_to(self.data_dir):
            raise ValueError("文件路径超出项目数据目录")
        return path

    def create_task(self, payload: dict) -> dict:
        task = {
            "id": identifier("task"), "title": payload.get("title") or payload["question"][:32],
            "question": payload["question"], "mode": payload.get("mode", "demo"),
            "status": "queued", "phase": "等待执行", "progress": 0, "attempt": 0, "version": 1,
            "created_at": now(), "updated_at": now(), "dataset_ids": payload.get("dataset_ids", []),
            "document_ids": payload.get("document_ids", []), "scope": payload.get("scope", {}),
            "plan": [], "report_markdown": None, "error": None,
        }
        task["thread_id"] = task["id"]
        with self.lock, self.connection() as db:
            self._write_task(db, task, insert=True)
        return task

    def _write_task(self, db, task, insert=False):
        verb = "INSERT INTO" if insert else "INSERT OR REPLACE INTO"
        db.execute(f"{verb} research_tasks VALUES(?,?,?,?)", (task["id"], task["status"], task["updated_at"], encode(task)))
        db.execute("INSERT OR REPLACE INTO redis_outbox VALUES(?,?)", (task["id"], encode(task)))

    def get_task(self, task_id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM research_tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_tasks(self):
        with self.connection() as db:
            rows = db.execute("SELECT data FROM research_tasks ORDER BY updated_at DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_task(self, task_id, **fields):
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM research_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            task = json.loads(row[0])
            fields.pop("id", None)
            task.update(fields)
            task["version"] = task.get("version", 0) + 1
            task["updated_at"] = now()
            self._write_task(db, task)
        return task

    def transition_task(self, task_id, status, *, expected=None, **fields):
        with self.lock:
            task = self.get_task(task_id)
            if not task:
                raise KeyError(task_id)
            if expected is not None and task["status"] not in expected:
                return None
            return self.update_task(task_id, status=status, **fields)

    def recoverable_tasks(self):
        return [t for t in self.list_tasks() if t["status"] in {"queued", "running", "recovering", "cancel_requested"}]

    def pending_redis(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM redis_outbox").fetchall()]

    def mark_redis_synced(self, task_id, version):
        with self.lock, self.connection() as db:
            db.execute("DELETE FROM redis_outbox WHERE task_id=? AND json_extract(data,'$.version')<=?", (task_id, version))

    def append_event(self, task_id, event_type, data, agent="orchestrator", tool=None):
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0]
            event = {"id": identifier("event"), "task_id": task_id, "seq": seq, "event_type": event_type,
                     "agent": agent, "tool": tool, "data": data, "created_at": now()}
            db.execute("INSERT INTO task_events VALUES(?,?,?)", (task_id, seq, encode(event)))
        return event

    def list_events(self, task_id, after_seq=0):
        with self.connection() as db:
            rows = db.execute("SELECT data FROM task_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 2000", (task_id, after_seq)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def add_document(self, name, text, metadata=None):
        doc_id = identifier("doc")
        chunks = []
        for i, start in enumerate(range(0, len(text), 880)):
            chunks.append({"id": f"{doc_id}_chunk_{i}", "document_id": doc_id, "text": text[start:start + 1000],
                           "position": i, "metadata": {"document_name": name, **(metadata or {})}})
        doc = {"id": doc_id, "name": name, "text": text, "metadata": metadata or {}, "chunk_count": len(chunks), "created_at": now()}
        with self.lock, self.connection() as db:
            db.execute("INSERT INTO documents VALUES(?,?)", (doc_id, encode(doc)))
            db.executemany("INSERT INTO document_chunks VALUES(?,?,?)", [(c["id"], doc_id, encode(c)) for c in chunks])
        return doc

    def _list(self, table):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(f"SELECT data FROM {table} ORDER BY rowid DESC").fetchall()]

    def _get(self, table, item_id):
        with self.connection() as db:
            row = db.execute(f"SELECT data FROM {table} WHERE id=?", (item_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_documents(self):
        return self._list("documents")

    def get_document(self, item_id):
        return self._get("documents", item_id)

    def get_chunks(self, document_ids=None):
        with self.connection() as db:
            if document_ids is None:
                rows = db.execute("SELECT data FROM document_chunks ORDER BY rowid").fetchall()
            elif not document_ids:
                return []
            else:
                marks = ",".join("?" for _ in document_ids)
                rows = db.execute(f"SELECT data FROM document_chunks WHERE document_id IN ({marks}) ORDER BY rowid", document_ids).fetchall()
        return [json.loads(row[0]) for row in rows]

    def add_dataset(self, name, path, columns, row_count, metadata=None):
        path = str(self.safe_path(path))
        item = {"id": identifier("dataset"), "name": name, "path": path, "columns": columns,
                "row_count": row_count, "metadata": metadata or {}, "created_at": now()}
        with self.lock, self.connection() as db:
            db.execute("INSERT INTO datasets VALUES(?,?)", (item["id"], encode(item)))
        return item

    def list_datasets(self):
        return self._list("datasets")

    def get_dataset(self, item_id):
        return self._get("datasets", item_id)

    def add_evidence(self, task_id, source_type, title, content, url=None, metadata=None, source_key=None):
        source_key = source_key or hashlib.sha256(f"{source_type}:{url}:{title}:{content}".encode()).hexdigest()
        with self.lock, self.connection() as db:
            row = db.execute("SELECT data FROM evidence WHERE task_id=? AND source_key=?", (task_id, source_key)).fetchone()
            if row:
                return json.loads(row[0])
            item = {"id": identifier("source"), "task_id": task_id, "source_type": source_type, "title": title,
                    "content": content, "url": url, "metadata": metadata or {}, "created_at": now()}
            db.execute("INSERT INTO evidence VALUES(?,?,?,?)", (item["id"], task_id, source_key, encode(item)))
        return item

    def list_evidence(self, task_id):
        return [r for r in self._list("evidence") if r["task_id"] == task_id]

    def add_artifact(self, task_id, name, path, media_type, kind, metadata=None):
        path = str(self.safe_path(path))
        with self.lock, self.connection() as db:
            row = db.execute("SELECT data FROM artifacts WHERE task_id=? AND path=?", (task_id, path)).fetchone()
            artifact_id = json.loads(row[0])["id"] if row else identifier("artifact")
            item = {"id": artifact_id, "task_id": task_id, "name": name, "path": path, "media_type": media_type,
                    "kind": kind, "metadata": metadata or {}, "created_at": now()}
            db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?)", (artifact_id, task_id, path, encode(item)))
        return item

    def list_artifacts(self, task_id):
        return [r for r in self._list("artifacts") if r["task_id"] == task_id]

    def get_artifact(self, item_id):
        return self._get("artifacts", item_id)

    def record_tool_run(self, task_id, agent, tool, status, duration_ms, cache_status="miss", details=None):
        item = {"id": identifier("call"), "task_id": task_id, "agent": agent, "tool": tool, "status": status,
                "duration_ms": duration_ms, "cache_status": cache_status, "details": details or {}, "created_at": now()}
        with self.lock, self.connection() as db:
            db.execute("INSERT INTO tool_runs VALUES(?,?,?)", (item["id"], task_id, encode(item)))
        return item

    def tool_runs(self, task_id=None):
        rows = self._list("tool_runs")
        return [r for r in rows if task_id is None or r["task_id"] == task_id]

    def metrics(self, task_id=None):
        rows = self.tool_runs(task_id)
        def successful(row):
            return row["status"] in {"ok", "success", "succeeded", "completed"}
        def summarize(group):
            return {"calls": len(group), "success_rate": sum(successful(r) for r in group) / len(group) if group else 0,
                    "average_duration_ms": round(sum(float(r["duration_ms"]) for r in group) / len(group), 2) if group else 0}
        stats = summarize(rows)
        stats["total_calls"] = stats.pop("calls")
        stats["exact_hits"] = sum(r["cache_status"] in {"exact", "exact_hit"} for r in rows)
        stats["semantic_hits"] = sum(r["cache_status"] in {"semantic", "semantic_hit"} for r in rows)
        stats["cache_hits"] = stats["exact_hits"] + stats["semantic_hits"]
        # Only external lookups enter this cache; analysis/delegation calls are
        # not cache misses and must not dilute its denominator.
        lookups = [r for r in rows if r["tool"] in {"search_web", "fetch_page", "extract_page_tables"}]
        stats["cache_lookups"] = len(lookups)
        stats["cache_hit_rate"] = stats["cache_hits"] / len(lookups) if lookups else 0
        stats["by_tool"] = [{"tool": t, **summarize([r for r in rows if r["tool"] == t])} for t in sorted({r["tool"] for r in rows})]
        stats["by_agent"] = [{"agent": a, **summarize([r for r in rows if r["agent"] == a])} for a in sorted({r["agent"] for r in rows})]
        tasks = [t for t in self.list_tasks() if task_id is None or t["id"] == task_id]
        stats["tasks"] = {"total": len(tasks), **{s: sum(t["status"] == s for t in tasks) for s in ["succeeded", "failed", "running", "queued", "cancelled", "recovering"]}}
        with self.connection() as db:
            query = "SELECT data FROM task_events WHERE json_extract(data,'$.event_type')='model_completed'"
            args = ()
            if task_id is not None:
                query += " AND task_id=?"
                args = (task_id,)
            model_events = [json.loads(row[0]) for row in db.execute(query, args)]
        usage = {key: 0 for key in ("input_tokens", "output_tokens", "total_tokens")}
        measured = 0
        for event in model_events:
            values = event["data"].get("usage_metadata") or {}
            reported = {key: value for key, value in values.items() if key in usage and type(value) is int and value >= 0}
            if reported:
                measured += 1
                for key, value in reported.items():
                    usage[key] += value
        stats["token_usage"] = {"available": bool(measured), "reported_calls": measured,
                                "completed_model_calls": len(model_events),
                                "coverage": "complete" if measured and measured == len(model_events) else "partial" if measured else "unavailable",
                                **usage, "note": "仅汇总服务实际返回的用量；缺失调用不估算"}
        return stats
