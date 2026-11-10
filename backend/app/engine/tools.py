from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import re
import socket
import struct
import threading
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from .cache import SemanticCache, canonical, cosine
from .registry import TOOL_AGENTS

_PLOT_LOCK = threading.Lock()


async def _public_target(url: str) -> tuple[str, str]:
    """Resolve every redirect independently and reject private/ambiguous targets."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("网页地址必须为不含凭据的 HTTP(S) 公网 URL。")
    if parsed.port and parsed.port not in {80, 443}:
        raise ValueError("网页读取仅允许标准 HTTP(S) 端口。")
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("网页域名无法解析。") from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0].split("%")[0]).is_global for a in addresses):
        raise ValueError("网页读取拒绝本机、内网、保留和链路本地地址。")
    return addresses[0][4][0], parsed.hostname.encode("idna").decode("ascii")


async def public_url(url: str) -> str:
    await _public_target(url)
    return url


def _jsonable_frame(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))


class ResearchTools:
    """Nine tools scoped to exactly one task's selected resource IDs."""

    def __init__(self, task, settings, repo):
        self.task, self.settings, self.repo = task, settings, repo
        self.mode = task["mode"]
        self.cache = SemanticCache(settings, self.mode)
        self._cache_status = ContextVar(f"cache_status_{task['id']}", default="miss")
        self.cache_metadata = {}
        self.task_dir = (Path(settings.data_dir) / "tasks" / task["id"]).resolve()
        if not self.task_dir.is_relative_to(Path(settings.data_dir).resolve()):
            raise ValueError("无效的任务目录。")
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self._web_sources = None
        self._vectors = {}

    @property
    def last_cache_status(self):
        return self._cache_status.get()

    @last_cache_status.setter
    def last_cache_status(self, value):
        self._cache_status.set(value)

    async def initialize(self):
        await self.cache.initialize()

    async def close(self):
        await self.cache.close()

    def _result(self, summary, *, data=None, evidence=None, artifacts=None, **extra):
        return {"status": "ok", "summary": summary, "data": data, "evidence_ids": [e["id"] for e in (evidence or [])], "artifact_ids": [a["id"] for a in (artifacts or [])], **extra}

    def _evidence(self, kind, title, content, url=None, metadata=None, source_key=None):
        return self.repo.add_evidence(self.task["id"], kind, title, str(content)[:24000], url=url, metadata=metadata or {}, source_key=source_key)

    @staticmethod
    def _web_source_key(kind, url, title, content):
        # A URL can return different search snippets or change after cache TTL.
        # Keep citations bound to the text actually observed by the model.
        digest = hashlib.sha256(canonical({"title": title, "content": content}).encode()).hexdigest()
        return f"{kind}:{url}:{digest}"

    def _sources(self):
        if self._web_sources is None:
            sample_file = Path(__file__).resolve().parents[3] / "samples" / "web_sources.json"
            if not sample_file.exists():
                raise ValueError("未找到离线网页样例，请检查 samples/web_sources.json。")
            self._web_sources = json.loads(sample_file.read_text(encoding="utf-8-sig"))
        return self._web_sources

    async def _external_cached(self, name, query, loader, *, entity="", intent="", semantic=False, params=None):
        endpoint = self.settings.search_base_url.rstrip("/") or ("https://api.tavily.com" if self.settings.search_provider == "tavily" else "")
        endpoint_id = hashlib.sha256(endpoint.encode()).hexdigest()[:12]
        scope = {"tool": name, "tool_version": "1", "mode": self.mode, "business_scope": self.task.get("scope", {}), "entity": entity, "intent": intent, "provider": self.settings.search_provider, "endpoint_id": endpoint_id, "params": params or {}}
        # Semantic reuse requires explicit subject+intent; generic unscoped prompts only use exact keys.
        allow_semantic = bool(semantic and entity.strip() and intent.strip())
        value, cache_status, vector = await self.cache.get(query, scope, semantic=allow_semantic)
        self.last_cache_status = cache_status
        if value is not None:
            return value
        value = await loader()
        value["fetched_at"] = datetime.now(timezone.utc).isoformat()
        await self.cache.put(query, scope, value, vector)
        return value

    async def search_web(self, query: str, entity: str = "", intent: str = "", max_results: int = 4) -> dict:
        """Search public web. Specify a stable entity and intent for safe semantic reuse."""
        query = query.strip()
        if not query or len(query) > 2000:
            raise ValueError("搜索问题不能为空，且不能超过 2000 字符。")
        max_results = min(max(int(max_results), 1), 6)

        async def load():
            if self.mode == "demo":
                items = [{"title": s["title"], "url": s["url"], "content": s.get("content", "")[:3500], "published_at": s.get("published_at"), "is_demo": True} for s in self._sources()]
                # Actual local matching. No claim of live network or neural search.
                qv = await self.cache.vector(query)
                scored = [(cosine(qv, await self.cache.vector(v["title"] + v["content"])), v) for v in items]
                items = [v for _, v in sorted(scored, key=lambda row: row[0], reverse=True)][:max_results]
                return {"items": items, "source_mode": "synthetic_offline", "note": "离线虚构资料，仅验证流程，不代表真实市场信息。"}
            provider = self.settings.search_provider
            async with httpx.AsyncClient(timeout=self.settings.tool_timeout_seconds, trust_env=False) as client:
                if provider == "tavily":
                    if not self.settings.search_api_key:
                        raise ValueError("Tavily 搜索尚未配置密钥。")
                    base = (self.settings.search_base_url or "https://api.tavily.com").rstrip("/")
                    response = await client.post(base + "/search", json={"api_key": self.settings.search_api_key, "query": query, "max_results": max_results, "search_depth": "basic", "include_raw_content": False})
                elif provider == "searxng":
                    if not self.settings.search_base_url:
                        raise ValueError("SearXNG 搜索尚未配置接口地址。")
                    response = await client.get(self.settings.search_base_url.rstrip("/") + "/search", params={"q": query, "format": "json", "language": "zh-CN"})
                else:
                    raise ValueError("搜索服务仅支持 tavily 或 searxng。")
                response.raise_for_status()
                results = response.json().get("results", [])[:max_results]
            items = [{"title": str(v.get("title", "未命名来源"))[:500], "url": str(v.get("url", "")), "content": str(v.get("content", ""))[:5000], "published_at": v.get("published_date")} for v in results if urlsplit(str(v.get("url", ""))).scheme in {"https", "http"}]
            return {"items": items, "source_mode": "live", "note": "搜索摘要需要结合原文核查。"}

        raw = await self._external_cached("search_web", query, load, entity=entity, intent=intent, semantic=True, params={"max_results": max_results})
        evidence = [self._evidence("offline_example" if self.mode == "demo" else "web_search", v["title"], v["content"], v["url"], {"is_demo": self.mode == "demo", "published_at": v.get("published_at"), "fetched_at": raw.get("fetched_at"), "cache_status": self.last_cache_status}, source_key=self._web_source_key("search", v["url"], v["title"], v["content"])) for v in raw["items"]]
        return self._result(f"找到 {len(evidence)} 个{'离线示例' if self.mode == 'demo' else '公开'}来源。", data=raw, evidence=evidence)

    async def _page(self, url):
        if url.startswith("sample://"):
            if self.mode != "demo":
                raise ValueError("在线任务不允许把离线样例当作联网来源。")
            source = next((v for v in self._sources() if v["url"] == url), None)
            if not source:
                raise ValueError("离线页面不存在。")
            return {"url": url, "html": source.get("html", ""), "title": source["title"], "content": source.get("content", ""), "is_demo": True}
        if self.mode == "demo":
            raise ValueError("示例模式只读取 sample:// 离线页面。真实网页请使用在线模式。")
        current = url
        async with httpx.AsyncClient(timeout=self.settings.tool_timeout_seconds, follow_redirects=False, trust_env=False) as client:
            for _ in range(5):
                address, hostname = await _public_target(current)
                # Pin the validated address to close the DNS-rebinding window; keep
                # the original Host and TLS SNI so certificate checks remain active.
                pinned_url = httpx.URL(current).copy_with(host=address)
                async with client.stream("GET", pinned_url, headers={"Host": hostname, "User-Agent": "ResearchWorkbench/0.1 (+read-only research)"}, extensions={"sni_hostname": hostname}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        current = urljoin(current, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    if not any(t in response.headers.get("content-type", "").lower() for t in ("text/html", "text/plain", "application/xhtml")):
                        raise ValueError("当前网页工具只读取 HTML 或纯文本页面。")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise ValueError("网页超过 2 MB 限制。")
                    html = body.decode(response.encoding or "utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "iframe"]):
                    tag.decompose()
                title = soup.title.get_text(" ", strip=True) if soup.title else current
                content = soup.get_text(" ", strip=True)
                return {"url": current, "html": html, "title": title[:500], "content": content[:40000], "is_demo": False}
        raise ValueError("网页重定向次数超过限制。")

    async def fetch_page(self, url: str) -> dict:
        """Read a public page, keeping text and source provenance."""
        raw = await self._external_cached("fetch_page", url, lambda: self._page(url))
        evidence = self._evidence("offline_example" if raw["is_demo"] else "web_page", raw["title"], raw["content"], raw["url"], {"is_demo": raw["is_demo"], "fetched_at": raw.get("fetched_at")}, source_key=self._web_source_key("page", raw["url"], raw["title"], raw["content"]))
        return self._result("已读取页面正文。", data={k: v for k, v in raw.items() if k != "html"}, evidence=[evidence])

    async def extract_page_tables(self, url: str) -> dict:
        """Extract actual HTML table cells; an empty result means no table was found."""
        raw = await self._external_cached("fetch_page", url, lambda: self._page(url))
        soup = BeautifulSoup(raw["html"], "html.parser")
        tables = []
        for table in soup.find_all("table")[:8]:
            rows = [[cell.get_text(" ", strip=True)[:500] for cell in tr.find_all(["th", "td"])[:20]] for tr in table.find_all("tr")[:100]]
            if rows:
                tables.append({"rows": rows, "caption": table.caption.get_text(" ", strip=True) if table.caption else "", "source_url": raw["url"]})
        evidence = self._evidence("offline_example" if raw["is_demo"] else "web_table", raw["title"] + " · 表格", canonical(tables), raw["url"], {"is_demo": raw["is_demo"], "fetched_at": raw.get("fetched_at"), "table_count": len(tables)}, source_key=self._web_source_key("tables", raw["url"], raw["title"], tables))
        return self._result(f"提取到 {len(tables)} 个表格。", data={"tables": tables, "is_demo": raw["is_demo"]}, evidence=[evidence])

    def _chunks(self):
        ids = self.task.get("document_ids", [])
        return self.repo.get_chunks(ids) if ids else []

    async def search_knowledge(self, query: str, limit: int = 4) -> dict:
        """Vector retrieval over ONLY the task's selected document IDs."""
        chunks = self._chunks()
        limit = min(max(1, int(limit)), 8)
        if not chunks:
            return self._result("本任务未选择知识资料，或所选资料没有可检索正文。", data={"matches": [], "embedding_mode": self.cache.version})
        query_vector = await self.cache.vector(query)
        if self.mode == "demo":
            matches = []
            for chunk in chunks:
                vec = await self.cache.vector(chunk["text"])
                matches.append((cosine(query_vector, vec), chunk))
            chosen = sorted(matches, key=lambda row: row[0], reverse=True)[:limit]
        else:
            # Chunk text is durable in SQLite; Redis index can be rebuilt.
            redis = self.cache.redis
            suffix = hashlib.sha256(f"{self.cache.version}:{self.cache.dimensions}".encode()).hexdigest()[:12]
            index, prefix = f"research_knowledge_{suffix}", f"research:knowledge:{suffix}:"
            try:
                await redis.execute_command("FT.CREATE", index, "ON", "HASH", "PREFIX", "1", prefix, "SCHEMA", "document", "TAG", "vector", "VECTOR", "FLAT", "6", "TYPE", "FLOAT32", "DIM", str(self.cache.dimensions), "DISTANCE_METRIC", "COSINE")
            except Exception as exc:
                if "Index already exists" not in str(exc):
                    raise
            for chunk in chunks:
                key = prefix + chunk["id"]
                if not await redis.exists(key):
                    vec = await self.cache.vector(chunk["text"])
                    await redis.hset(key, mapping={"document": chunk["document_id"], "vector": struct.pack(f"<{len(vec)}f", *vec)})
            escape = lambda s: re.sub(r"([^A-Za-z0-9_])", r"\\\1", s)
            document_filter = "|".join(escape(v) for v in self.task["document_ids"])
            result = await redis.execute_command("FT.SEARCH", index, f"(@document:{{{document_filter}}})=>[KNN {limit} @vector $vec AS distance]", "PARAMS", "2", "vec", struct.pack(f"<{len(query_vector)}f", *query_vector), "SORTBY", "distance", "RETURN", "1", "distance", "DIALECT", "2")
            by_id = {c["id"]: c for c in chunks}
            chosen = []
            for i in range(1, len(result), 2):
                chunk_id = result[i].decode().removeprefix(prefix)
                fields = dict(zip(result[i+1][::2], result[i+1][1::2]))
                if chunk_id in by_id:
                    chosen.append((1 - float(fields[b"distance"]), by_id[chunk_id]))
        evidence, data = [], []
        for score, chunk in chosen:
            document = self.repo.get_document(chunk["document_id"])
            e = self._evidence("knowledge", document["name"], chunk["text"], metadata={"document_id": document["id"], "chunk_id": chunk["id"], "position": chunk.get("position"), "similarity": round(score, 4), "embedding_mode": self.cache.version}, source_key="chunk:" + chunk["id"])
            evidence.append(e)
            data.append({"chunk_id": chunk["id"], "document_id": document["id"], "document_name": document["name"], "similarity": round(score, 4), "text": chunk["text"][:2500], "evidence_id": e["id"]})
        return self._result(f"检索到 {len(data)} 个相关片段，需根据原文核查是否支持结论。", data={"matches": data, "embedding_mode": self.cache.version}, evidence=evidence)

    async def read_chunk(self, chunk_id: str) -> dict:
        """Read an allowed chunk and adjacent context from selected documents."""
        chunks = self._chunks()
        chunk = next((v for v in chunks if v["id"] == chunk_id), None)
        if not chunk:
            raise ValueError("片段不存在或不属于本任务选择的资料。")
        siblings = [v for v in chunks if v["document_id"] == chunk["document_id"]]
        index = next(i for i, v in enumerate(siblings) if v["id"] == chunk_id)
        context = "\n\n".join(v["text"] for v in siblings[max(0, index-1):index+2])
        document = self.repo.get_document(chunk["document_id"])
        evidence = self._evidence("knowledge", document["name"] + " · 原文上下文", context, metadata={"document_id": document["id"], "chunk_id": chunk_id}, source_key="context:" + chunk_id)
        return self._result("已读取原文与相邻上下文。", data={"text": context, "document_id": document["id"], "chunk_id": chunk_id}, evidence=[evidence])

    async def get_document_metadata(self, document_id: str) -> dict:
        """Read allowed document provenance and version metadata."""
        if document_id not in self.task.get("document_ids", []):
            raise ValueError("该文档未被本任务选中。")
        document = self.repo.get_document(document_id)
        if not document:
            raise ValueError("文档不存在。")
        data = {k: v for k, v in document.items() if k != "text"}
        evidence = self._evidence("document_metadata", document["name"] + " · 资料信息", canonical(data), metadata={"document_id": document_id}, source_key="metadata:" + document_id)
        return self._result("已读取文档信息；缺失发布日期时不推断时间。", data=data, evidence=[evidence])

    def _dataset(self, dataset_id):
        if dataset_id not in self.task.get("dataset_ids", []):
            raise ValueError("该数据集未被本任务选中。")
        dataset = self.repo.get_dataset(dataset_id)
        if not dataset:
            raise ValueError("数据集不存在。")
        path = Path(dataset["path"]).resolve()
        if not path.is_relative_to(Path(self.settings.data_dir).resolve()):
            raise ValueError("数据集路径超出项目数据目录。")
        return dataset, pd.read_csv(path, encoding="utf-8-sig", dtype={"order_id": "string", "product_id": "string"})

    async def inspect_dataset(self, dataset_id: str) -> dict:
        """Inspect data quality and grain before choosing metrics."""
        dataset, frame = await asyncio.to_thread(self._dataset, dataset_id)
        date_range = None
        if "order_date" in frame:
            dates = pd.to_datetime(frame["order_date"], errors="coerce", format="mixed", utc=True)
            if dates.notna().any():
                date_range = {"start": dates.min().date().isoformat(), "end": dates.max().date().isoformat(), "invalid_dates": int(dates.isna().sum())}
        data = {"dataset_id": dataset_id, "name": dataset["name"], "row_count": len(frame), "columns": [{"name": col, "dtype": str(frame[col].dtype), "missing": int(frame[col].isna().sum())} for col in frame.columns], "sample": _jsonable_frame(frame.head(5)), "date_range": date_range, "is_demo": bool(dataset.get("metadata", {}).get("is_demo") or dataset.get("metadata", {}).get("simulated")), "grain": "CSV 一行视为一条商品明细；订单量按 order_id 去重。"}
        evidence = self._evidence("dataset", dataset["name"] + " · 数据概况", canonical(data), metadata={"dataset_id": dataset_id, "is_demo": data["is_demo"]}, source_key="dataset:" + dataset_id)
        return self._result(f"检查了 {len(frame)} 行、{len(frame.columns)} 列。", data=data, evidence=[evidence])

    def _aggregate(self, dataset_id, group_by, start_date, end_date, category):
        dataset, frame = self._dataset(dataset_id)
        required = {"order_date", "order_id", "paid_amount"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError("缺少计算所需字段：" + "、".join(sorted(missing)))
        dates = pd.to_datetime(frame["order_date"], errors="coerce", format="mixed", utc=True)
        amounts = pd.to_numeric(frame["paid_amount"], errors="coerce")
        if dates.isna().any() or amounts.isna().any() or not amounts.map(math.isfinite).all() or frame["order_id"].isna().any():
            raise ValueError("日期、paid_amount 或 order_id 存在无效值，请先清洗数据。")
        frame = frame.copy()
        frame["paid_amount"] = amounts
        frame["_date"] = dates.dt.strftime("%Y-%m-%d")
        frame["month"] = dates.dt.strftime("%Y-%m")
        for column in ("refund_amount", "quantity"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
                if frame[column].isna().any() or not frame[column].map(math.isfinite).all():
                    raise ValueError(f"{column} 含无效数字。")
        for value in (start_date, end_date):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError("统计日期必须采用 YYYY-MM-DD 格式。") from exc
        if start_date and end_date and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期。")
        if start_date:
            frame = frame[frame["_date"] >= start_date]
        if end_date:
            frame = frame[frame["_date"] <= end_date]
        if category:
            if "category" not in frame:
                raise ValueError("按品类筛选需要 category 字段。")
            frame = frame[frame["category"].astype(str) == category]
        region = self.task.get("scope", {}).get("region", "") or ""
        if region:
            if "region" not in frame:
                raise ValueError("任务限定了地区，按地区筛选需要数据集包含 region 字段。")
            frame = frame[frame["region"].astype(str) == region]
        if frame.empty:
            raise ValueError("所选时间、地区或品类范围内没有可分析的数据。")
        if group_by not in {"month", "category", "product_id", "none"}:
            raise ValueError("group_by 仅支持 month、category、product_id、none。")
        if group_by != "none" and group_by not in frame:
            raise ValueError(f"缺少分组字段 {group_by}。")

        def stats(part):
            paid = float(part["paid_amount"].sum())
            orders = int(part["order_id"].nunique())
            refund = float(part["refund_amount"].sum()) if "refund_amount" in part else None
            return {"sales_amount": round(paid, 2), "order_count": orders, "quantity": float(part["quantity"].sum()) if "quantity" in part else None, "refund_amount": round(refund, 2) if refund is not None else None, "refund_amount_ratio": refund / paid if refund is not None and paid != 0 else None, "net_sales_amount": round(paid-refund, 2) if refund is not None else None, "average_order_value": round(paid/orders, 2) if orders else None, "row_count": len(part)}

        rows = [dict(group="全部", **stats(frame))] if group_by == "none" else [dict(group=str(key), **stats(part)) for key, part in frame.groupby(group_by, sort=True, dropna=False)]
        if group_by == "month":
            previous = None
            previous_month = None
            for row in rows:
                adjacent = previous_month is not None and pd.Period(row["group"], "M") - pd.Period(previous_month, "M") == pd.offsets.MonthEnd(1)
                row["month_over_month"] = round((row["sales_amount"]-previous)/previous, 6) if adjacent and previous else None
                previous, previous_month = row["sales_amount"], row["group"]
        return {"dataset_id": dataset_id, "dataset_name": dataset["name"], "is_demo": bool(dataset.get("metadata", {}).get("is_demo") or dataset.get("metadata", {}).get("simulated")), "group_by": group_by, "filters": {"start_date": start_date, "end_date": end_date, "category": category, "region": region}, "date_range": {"start": frame["_date"].min(), "end": frame["_date"].max()}, "totals": stats(frame), "rows": rows, "definitions": {"sales_amount": "所选商品明细 paid_amount 求和，退款单列", "order_count": "所选明细 order_id 去重", "refund_amount_ratio": "退款金额占比 = 明细退款金额 / 销售额（比例值，非退款订单率）；销售额为零或缺少退款字段时不计算", "average_order_value": "销售额 / 去重订单量", "net_sales_amount": "销售额 - 明细退款金额；不等于利润", "month_over_month": "相邻自然月销售额变化；前期为零或月份缺失时不计算"}, "limitations": ["统计以所提供明细与时间覆盖为限，未进行行业总体推断。", "退款按明细中的归属统计；跨期退款需要业务系统提供独立发生日期。", "月份仅覆盖已有记录日期，部分月份不能当作完整自然月比较。"]}

    async def aggregate_metrics(self, dataset_id: str, group_by: Literal["month", "category", "product_id", "none"] = "month", start_date: str = "", end_date: str = "", category: str = "") -> dict:
        """Compute real sales/order/refund metrics. Use returned statistics_artifact_id for create_chart; csv_artifact_id is download-only, not chart input."""
        scope = self.task.get("scope", {})
        for value in (start_date, end_date, scope.get("start_date"), scope.get("end_date")):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError("统计日期必须采用 YYYY-MM-DD 格式。") from exc
        # A specialist may narrow a user scope, but cannot broaden it.
        start_date = max(filter(None, [start_date, scope.get("start_date", "")]), default="")
        end_date = min(filter(None, [end_date, scope.get("end_date", "")]), default="")
        scoped_category = scope.get("category", "") or ""
        if scoped_category and category and scoped_category != category:
            raise ValueError("工具品类参数与任务限定范围不一致。")
        category = scoped_category or category
        data = await asyncio.to_thread(self._aggregate, dataset_id, group_by, start_date, end_date, category)
        signature = hashlib.sha256(canonical({"dataset": dataset_id, "group": group_by, "filters": data["filters"]}).encode()).hexdigest()[:14]
        path = self.task_dir / f"metrics-{signature}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(canonical(data), encoding="utf-8")
        temp.replace(path)
        artifact = self.repo.add_artifact(self.task["id"], "经营指标.json", str(path), "application/json", "statistics", metadata={"dataset_id": dataset_id, "is_demo": data["is_demo"]})
        csv_path = path.with_suffix(".csv")
        await asyncio.to_thread(pd.DataFrame(data["rows"]).to_csv, csv_path, index=False, encoding="utf-8-sig")
        csv_artifact = self.repo.add_artifact(self.task["id"], "经营指标.csv", str(csv_path), "text/csv", "table", metadata={"dataset_id": dataset_id})
        evidence = self._evidence("statistics", data["dataset_name"] + " · 指标统计", canonical(data), metadata={"dataset_id": dataset_id, "artifact_id": artifact["id"], "is_demo": data["is_demo"]}, source_key="stats:" + signature)
        return self._result(f"已按 {group_by} 计算 {len(data['rows'])} 组指标。", data=data, evidence=[evidence], artifacts=[artifact, csv_artifact], statistics_artifact_id=artifact["id"], csv_artifact_id=csv_artifact["id"])

    def _plot(self, data, path, metric):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        from matplotlib.font_manager import FontProperties
        labels = [str(v["group"]) for v in data["rows"]]
        values = [float(v[metric]) for v in data["rows"]]
        font = None
        for candidate in (Path("C:/Windows/Fonts/msyh.ttc"), Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc")):
            if candidate.exists():
                font = FontProperties(fname=str(candidate))
                break
        with _PLOT_LOCK:
            figure = Figure(figsize=(10, 4.6), facecolor="#faf8f2", layout="constrained")
            ax = figure.subplots()
            ax.set_facecolor("#faf8f2")
            if data["group_by"] == "month":
                ax.plot(labels, values, marker="o", linewidth=2.5, markersize=7, color="#13766e")
                ax.fill_between(range(len(values)), values, color="#13766e", alpha=.08)
            else:
                ax.bar(labels, values, color="#13766e", width=.6)
            title = {"sales_amount": "销售额", "order_count": "去重订单量", "net_sales_amount": "净销售额", "average_order_value": "客单价"}[metric]
            ax.set_title(("模拟数据 · " if data["is_demo"] else "") + title, fontproperties=font, loc="left", pad=18, fontsize=15)
            ax.set_ylabel(title, fontproperties=font)
            for tick in ax.get_xticklabels():
                if font:
                    tick.set_fontproperties(font)
            ax.grid(axis="y", alpha=.15)
            ax.spines[["top", "right"]].set_visible(False)
            ax.spines[["left", "bottom"]].set_color("#d9dfdc")
            ax.tick_params(colors="#546267")
            figure.savefig(path, dpi=150)

    async def create_chart(self, statistics_artifact_id: str, metric: Literal["sales_amount", "order_count", "net_sales_amount", "average_order_value"] = "sales_amount") -> dict:
        """Plot this task's statistics JSON artifact. Pass aggregate_metrics.statistics_artifact_id, never csv_artifact_id or a CSV/table artifact ID."""
        artifact = self.repo.get_artifact(statistics_artifact_id)
        if not artifact or artifact["task_id"] != self.task["id"] or artifact["kind"] != "statistics":
            raise ValueError("需要当前任务生成的统计结果产物。")
        source = Path(artifact["path"]).resolve()
        if not source.is_relative_to(self.task_dir):
            raise ValueError("统计产物路径超出任务目录。")
        data = json.loads(source.read_text(encoding="utf-8"))
        if metric not in {"sales_amount", "order_count", "net_sales_amount", "average_order_value"}:
            raise ValueError("不支持的图表指标。")
        if any(row.get(metric) is None for row in data["rows"]):
            raise ValueError("数据缺少该指标所需字段。")
        path = source.with_name(source.stem + "-" + metric + ".png")
        await asyncio.to_thread(self._plot, data, path, metric)
        chart = self.repo.add_artifact(self.task["id"], "销售趋势.png", str(path), "image/png", "chart", metadata={"metric": metric, "statistics_artifact_id": statistics_artifact_id, "is_demo": data["is_demo"]})
        return self._result("图表已由实际统计结果生成。", data={"url": f"/api/artifacts/{chart['id']}", "metric": metric}, artifacts=[chart])

    def langchain_tools(self, executor=None):
        from langchain_core.tools import StructuredTool
        tools = []
        for name in TOOL_AGENTS:
            method = getattr(self, name)
            tools.append(StructuredTool.from_function(coroutine=method, name=name, description=method.__doc__ or name))
        return tools
