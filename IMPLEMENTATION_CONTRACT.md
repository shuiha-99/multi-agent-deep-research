# Implementation contract

Local Python FastAPI + React TypeScript application. Root owns backend/app/{config.py,storage.py,main.py,tasks.py,ingestion.py,redis_state.py}, packaging/config/scripts. Engine agent owns backend/app/engine/**. Frontend agent owns frontend/**. Quality agent owns samples/**, tests/**, docs/**, README.md; coordinate dependencies before tests. Do not modify files owned by another agent without coordination.

## Backend contracts

`from app.config import Settings` (pydantic Settings): data_dir Path, mode default 'demo', redis_url str (empty permitted demo only), model_name str, model_base_url str, model_api_key str, search_provider str 'tavily'|'searxng', search_api_key str, search_base_url str, embedding_model str, embedding_base_url str, embedding_api_key str, embedding_dimensions int, max_tasks int=2, max_tool_calls int=36, max_model_calls int=18, task_timeout_seconds int=300, tool_timeout_seconds int=25, max_subtasks int=6, max_followups int=1, cache_ttl_seconds int=3600, semantic_threshold float=0.92, demo_step_delay float=0.25. `.data_dir / 'checkpoints.sqlite'` is graph db. All paths absolute in settings. No automatic reuse of machine credentials.

`from app.storage import Repository`: synchronous small SQLite operations. `Repository(settings.data_dir)` initializes DB. Methods return plain JSON dicts/lists:
- create_task(payload:dict)->task; get_task(id)->dict|None; list_tasks()->list; update_task(id, **fields)->task; recoverable_tasks()->list
- task fields: id, title, question, mode ('demo'|'live'), status, phase, progress, attempt, version, created_at, updated_at, dataset_ids:list[str], document_ids:list[str], scope:dict, plan:list[dict], report_markdown:str|None, error:str|None. Plan item: id, agent ('web'|'knowledge'|'data'), title, status.
- append_event(task_id,event_type,data:dict,agent='orchestrator',tool=None)->event; list_events(task_id,after_seq=0)->list. Event fields id,task_id,seq,event_type,agent,tool,data,created_at.
- add_document(name,text,metadata=None)->dict; list_documents(); get_document(id); get_chunks(document_ids:list[str]|None)->list with id,document_id,text,position,metadata. Documents fields id,name,text,metadata,chunk_count,created_at. Empty IDs means no documents in engine (do not silently expand scope).
- add_dataset(name,path:str,columns:list[str],row_count:int,metadata=None)->dict; list_datasets(); get_dataset(id) with id,name,path,columns,row_count,metadata,created_at.
- add_evidence(task_id,source_type,title,content,url=None,metadata=None,source_key=None)->dict; list_evidence(task_id); evidence fields id,task_id,source_type,title,content,url,metadata,created_at. Stable source_key deduplicates within task. Cached sources must be re-registered for the current task.
- add_artifact(task_id,name,path,media_type,kind,metadata=None)->dict; list_artifacts(task_id); get_artifact(id). Fields id,task_id,name,path,media_type,kind,metadata,created_at. Paths always confined under data_dir.
- record_tool_run(task_id,agent,tool,status,duration_ms,cache_status='miss',details=None)->dict; tool_runs(task_id=None)->list; metrics(task_id=None)->dict (root builds stats).
- `seed_samples()` handled by root ingestion, not Repository.

`from app.engine.runner import run_research`: async function `(task:dict, settings:Settings, repo:Repository, emit, cancelled)` -> dict `{report_markdown:str, plan:list[dict]}`. `emit` signature async `(event_type:str, data:dict, agent='orchestrator', tool=None)`. `cancelled()` sync bool; raise `asyncio.CancelledError` when set. Runner manages AsyncSqliteSaver lifetime with thread_id=task['id'], resumes existing checkpoint (not repeated original messages). Demo uses real LangGraph checkpoint and actual local tools with clearly labelled offline sources; live uses real DeepAgents 1+3+9. Emit 'plan_updated' data={'plan':...}, 'phase' data={'phase':..., 'progress':number}. Engine can call repo.update_task for plan only; root owns terminal statuses. Event names tool_started/tool_completed/tool_failed/agent_started/agent_completed/model_started/model_completed/checkpoint_saved/cache_hit and lifecycle names; free JSON data allowed.

## REST/UI contract

API on 127.0.0.1:8000, Vite 5173 proxy `/api` and WebSocket. API returns JSON directly, errors FastAPI {'detail':string}.
- GET /api/health -> {status:'ok',mode:'demo',version:'0.1.0',services:{redis:{available:bool,...},model:{configured:bool},search:{configured:bool},embedding:{configured:bool}},features:{...}}
- GET /api/config -> safe configuration summary, tool registry and missing_live_config:list[str]; never return keys.
- GET /api/documents -> list; POST multipart file /api/documents -> document. TXT/MD/PDF.
- GET /api/datasets -> list; POST multipart file /api/datasets -> dataset. CSV.
- POST /api/samples/load -> {documents:list,datasets:list}; idempotently load bundled samples.
- GET /api/research/tasks -> list; POST JSON /api/research/tasks {question,title?,mode:'demo'|'live',dataset_ids:[],document_ids:[],scope:{start_date?,end_date?,region?,category?}} -> 202 task. Default mode demo. Empty attachments allowed. Live missing config -> 422 understandable message.
- GET /api/research/tasks/{id} -> task enriched with metrics, artifacts; GET .../events?after_seq=0 -> event list; GET .../sources -> evidence list.
- WS /api/research/tasks/{id}/stream?after_seq=0 -> individual event JSON. Replay then live, reconnect client also fetches task; ping/pong optional.
- POST .../{id}/cancel -> task; POST .../{id}/retry -> NEW task with same input (failed/cancelled only).
- GET .../{id}/report -> {markdown:str}; GET .../{id}/report/download -> text/markdown attachment.
- GET /api/artifacts/{id} -> FileResponse with correct media type; GET /api/metrics -> global metrics.
- Metrics shape: {total_calls,success_rate,average_duration_ms,cache_hits,cache_hit_rate,exact_hits,semantic_hits,by_tool:[{tool,calls,success_rate,average_duration_ms}],by_agent:[{agent,calls}], tasks:{total,succeeded,failed,running}}. success_rate/cache_hit_rate float 0..1. UI tolerate additional fields.

Frontend: Chinese professional research workspace, restrained cream/navy/teal, no fake historical metrics or pre-existing completed tasks. Initial empty state has '加载示例资料' and create sample research action, honest demo banner; actual uploaded data and task reports. Task timeline, plan/agents, sources, markdown, actual charts, metrics, cancel/retry/download, configuration visibility. No mock fetch fallbacks.
