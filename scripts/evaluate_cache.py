"""Fixed tool-query cache evaluation; no agent or market-quality claims.

Run from the repository root with .venv/Scripts/python.exe scripts/evaluate_cache.py.
--mode live explicitly invokes the configured search and embedding services.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.engine.cache import SemanticCache  # noqa: E402
from app.engine.tools import ResearchTools  # noqa: E402
from app.storage import Repository, now  # noqa: E402


class DirectSearch(ResearchTools):
    """Reuse the production search adapter while bypassing its cache boundary."""

    def __init__(self, task, settings, repo, cache):
        super().__init__(task, settings, repo)
        self.cache = cache
        self.direct_api_ms = 0.0

    async def _external_cached(self, name, query, loader, **kwargs):
        started = time.perf_counter()
        value = await loader()
        self.direct_api_ms = (time.perf_counter() - started) * 1000
        value["fetched_at"] = now()
        return value


def average(values):
    return round(statistics.fmean(values), 3) if values else None


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def register_cached_sources(tools, payload):
    """Count current-task evidence registration inside the cached response path."""
    for item in payload["items"]:
        tools._evidence(
            "offline_example" if tools.mode == "demo" else "web_search",
            item["title"], item["content"], item["url"],
            {"is_demo": tools.mode == "demo", "fetched_at": payload.get("fetched_at"), "evaluation": True},
            source_key=tools._web_source_key("search", item["url"], item["title"], item["content"]),
        )


async def evaluate(args):
    workload_path = Path(args.cases).resolve()
    workload_bytes = workload_path.read_bytes()
    workload = json.loads(workload_bytes)
    cases = workload["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("固定查询集为空或 case id 重复")
    run_id = uuid4().hex
    data_dir = ROOT / ".cache" / "cache-eval" / args.mode / run_id
    settings = Settings(data_dir=data_dir)
    if args.threshold is not None:
        settings.semantic_threshold = args.threshold
    if args.mode == "live":
        required = ["embedding_model", "embedding_base_url", "embedding_api_key", "redis_url"]
        if settings.search_provider == "tavily":
            required += ["search_api_key"]
        elif settings.search_provider == "searxng":
            required += ["search_base_url"]
        else:
            raise ValueError("仅支持 tavily 或 searxng 搜索")
        missing = [key for key in required if not getattr(settings, key)]
        if missing:
            raise ValueError("在线评测缺少配置：" + ", ".join(missing))
    repo = Repository(data_dir)
    cache = SemanticCache(settings, args.mode)
    records, hit_baselines, hit_paths = [], [], []
    seen_gold_groups = set()
    try:
        await cache.initialize()
        for case in cases:
            task = repo.create_task({"question": case["query"], "mode": args.mode})
            tools = DirectSearch(task, settings, repo, cache)
            business_scope = {key: case[key] for key in ("start_date", "end_date", "region")}
            business_scope["category"] = case["entity"]
            scope = {"tool": "search_web", "tool_version": "1", "mode": args.mode,
                     "business_scope": business_scope, "entity": case["entity"], "intent": case["intent"],
                     "provider": settings.search_provider, "params": {"max_results": 4}, "evaluation_run": run_id}
            started = time.perf_counter()
            envelope, status, vector = await cache.get(case["query"], scope, semantic=True, exact=False)
            hit = status == "semantic_hit"
            false_hit = hit and envelope["gold_group"] != case["gold_group"]
            if hit:
                register_cached_sources(tools, envelope["payload"])
            else:
                result = await tools.search_web(case["query"], entity=case["entity"], intent=case["intent"])
                if not result["data"].get("items"):
                    raise ValueError("评测查询没有取得可核验的搜索来源，停止以避免缓存空结果抬高命中率")
                envelope = {"gold_group": case["gold_group"], "seed_case": case["id"], "payload": result["data"]}
                await cache.put(case["query"], scope, envelope, vector)
            path_ms = (time.perf_counter() - started) * 1000
            direct_ms = None
            if hit:
                # Matched comparison: directly repeat exactly this hit request, not a different cold query.
                await tools.search_web(case["query"], entity=case["entity"], intent=case["intent"])
                direct_ms = tools.direct_api_ms
                if not false_hit:
                    hit_baselines.append(direct_ms)
                    hit_paths.append(path_ms)
            # Read-only probes are kept outside the semantic timing and semantic denominator.
            _, exact_status, _ = await cache.get(case["query"], scope, semantic=False, exact=True)
            record = {"id": case["id"], "window": case["window"], "kind": case["kind"],
                      "query": case["query"], "status": status, "false_hit": false_hit,
                      "expected_reusable": case["gold_group"] in seen_gold_groups,
                      "returned_seed_case": envelope["seed_case"], "cache_path_ms": round(path_ms, 3),
                      "direct_api_ms": round(direct_ms, 3) if direct_ms is not None else None,
                      "exact_probe_status": exact_status, "task_id": task["id"]}
            records.append(record)
            seen_gold_groups.add(case["gold_group"])
            print(f"{case['id']}: {status}, false_hit={false_hit}")
    finally:
        await cache.close()
    hits = sum(row["status"] == "semantic_hit" for row in records)
    wrong = sum(row["false_hit"] for row in records)
    direct_mean, cache_mean = average(hit_baselines), average(hit_paths)
    windows = []
    for window in sorted({row["window"] for row in records}):
        group = [row for row in records if row["window"] == window]
        windows.append({"window": window, "requests": len(group),
                        "semantic_hits": sum(row["status"] == "semantic_hit" for row in group),
                        "semantic_hit_rate": sum(row["status"] == "semantic_hit" for row in group) / len(group),
                        "false_hits": sum(row["false_hit"] for row in group)})
    report = {
        "schema_version": 1, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode, "scope": "fixed_external_tool_query_sequence_not_full_agent_tasks",
        "synthetic": args.mode == "demo", "embedding_version": cache.version,
        "embedding_dimensions": cache.dimensions, "semantic_threshold": settings.semantic_threshold,
        "cache_ttl_seconds": settings.cache_ttl_seconds, "workload_sha256": hashlib.sha256(workload_bytes).hexdigest(),
        "python": platform.python_version(), "platform": platform.platform(),
        "cold_start": "fresh run namespace; Redis evaluation entries expire by configured TTL",
        "semantic_requests": len(records), "excluded_requests": 0, "semantic_hits": hits,
        "semantic_hit_rate": hits / len(records), "false_hits": wrong,
        "correct_hit_fraction": (hits - wrong) / hits if hits else None,
        "false_misses": sum(row["expected_reusable"] and row["status"] == "miss" for row in records),
        "exact_cache_disabled_in_semantic_run": True,
        "exact_readonly_probes": {"requests": len(records), "hits": sum(row["exact_probe_status"] == "exact_hit" for row in records)},
        "matched_correct_hits": len(hit_paths), "direct_api_mean_ms": direct_mean,
        "cache_full_hit_path_mean_ms": cache_mean,
        "direct_api_median_ms": percentile(hit_baselines, 0.5), "direct_api_p95_ms": percentile(hit_baselines, 0.95),
        "cache_full_hit_path_median_ms": percentile(hit_paths, 0.5), "cache_full_hit_path_p95_ms": percentile(hit_paths, 0.95),
        "matched_hit_speedup": direct_mean / cache_mean if cache_mean else None,
        "search_adapter_requests": len(records),
        "windows": windows, "records": records,
        "limitations": [
            "示例模式使用字符哈希和离线虚构网页，耗时不代表真实API或神经embedding，不添加模拟网络延迟。",
            "仅测试固定外部查询序列；没有执行完整助手任务，因此不代表整项研究耗时、模型调度质量或模型token节省。",
            "命中加速只比较同批正确命中：直连接口均值 / 包含embedding、检索、反序列化和当前任务来源登记的缓存路径均值。",
            "错命中由预标注等价组判断；在线搜索结果是否支持事实仍需人工核查，阈值校准须使用独立数据集。",
            "精确探针为单独的只读检测，不计入语义命中分子分母，也未证明组合模式整体命中率。",
            "30倍与30%-50%不作为本脚本必达标准；单次小样本结果不能证明稳定性。",
        ],
    }
    output = Path(args.output).resolve() if args.output else ROOT / "artifacts" / "cache-eval" / args.mode / f"{run_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output}")
    print(f"Semantic hits: {hits}/{len(records)}; false hits: {wrong}; matched speedup: {report['matched_hit_speedup']}")
    return 2 if wrong else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--cases", default=str(ROOT / "evals" / "cache_cases.json"))
    parser.add_argument("--threshold", type=float, help="Preselect threshold; use independent cases for calibration.")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.threshold is not None and not 0.5 <= args.threshold <= 1:
        parser.error("threshold must be in [0.5, 1]")
    try:
        return asyncio.run(evaluate(args))
    except Exception as exc:
        # Provider exception representations may contain sensitive request URLs or headers.
        print(f"Evaluation failed: {type(exc).__name__}. Check local configuration and service availability.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
