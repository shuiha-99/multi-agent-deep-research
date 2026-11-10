"""Scoped cosine cache; offline feature hashing is always labelled as a demo.

Live entries use Redis hashes + Search COSINE vector indexes, not a scan over keys.
Cached payloads contain provider data only, never task-owned evidence IDs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any

import httpx


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return -1.0
    denom = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / denom if denom else 0.0


def demo_vector(text: str, dimensions: int = 128) -> list[float]:
    """Deterministic char n-grams, NOT a neural/production embedding model."""
    text = re.sub(r"\s+", "", text.casefold())
    result = [0.0] * dimensions
    for n in (1, 2, 3):
        for i in range(max(0, len(text) - n + 1)):
            digest = hashlib.blake2b(text[i:i+n].encode(), digest_size=8).digest()
            result[int.from_bytes(digest[:4], "little") % dimensions] += 1 if digest[4] & 1 else -1
    norm = math.sqrt(sum(x * x for x in result)) or 1.0
    return [x / norm for x in result]


class SemanticCache:
    def __init__(self, settings, mode: str):
        self.settings, self.mode = settings, mode
        self.redis = None
        self.dimensions = 128 if mode == "demo" else int(settings.embedding_dimensions)
        endpoint_id = hashlib.sha256(settings.embedding_base_url.rstrip("/").encode()).hexdigest()[:12]
        self.version = "demo-char-ngram-v1" if mode == "demo" else f"{settings.embedding_model}@{endpoint_id}"
        self.path = Path(settings.data_dir) / "demo-cache.sqlite"
        self._ready = False

    async def initialize(self):
        if self.mode == "demo":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, scope TEXT, query TEXT, vector TEXT, payload TEXT, expires REAL)")
            return
        if not self.settings.redis_url:
            raise ValueError("在线语义缓存需要 REDIS_URL。")
        from redis.asyncio import Redis
        # Raw FT.SEARCH responses below are parsed as RESP2 arrays. Newer
        # redis-py defaults to RESP3, whose response shape is different.
        self.redis = Redis.from_url(self.settings.redis_url, socket_connect_timeout=3, socket_timeout=8, protocol=2)
        await self.redis.ping()
        # Namespace includes model and dimensionality; never mix incompatible vectors.
        suffix = hashlib.sha256(f"{self.version}:{self.dimensions}".encode()).hexdigest()[:12]
        self.index, self.prefix = f"research_cache_{suffix}", f"research:cache:{suffix}:"
        try:
            await self.redis.execute_command(
                "FT.CREATE", self.index, "ON", "HASH", "PREFIX", "1", self.prefix,
                "SCHEMA", "scope", "TAG", "exact", "TAG", "vector", "VECTOR", "FLAT", "6",
                "TYPE", "FLOAT32", "DIM", str(self.dimensions), "DISTANCE_METRIC", "COSINE",
            )
        except Exception as exc:
            if "Index already exists" not in str(exc):
                raise ValueError("Redis 不支持所需的 Search/COSINE 向量索引，请使用支持向量搜索的 Redis 部署。") from exc
        self._ready = True

    async def close(self):
        if self.redis:
            await self.redis.aclose()

    async def vector(self, text: str) -> list[float]:
        if self.mode == "demo":
            return demo_vector(text)
        # Explicit settings only: never fall through to ambient host credentials.
        if not (self.settings.embedding_api_key and self.settings.embedding_base_url and self.settings.embedding_model):
            raise ValueError("在线向量检索需要配置 embedding 模型、接口地址和密钥。")
        async with httpx.AsyncClient(timeout=self.settings.tool_timeout_seconds, trust_env=False) as client:
            response = await client.post(
                self.settings.embedding_base_url.rstrip("/") + "/embeddings",
                headers={"Authorization": "Bearer " + self.settings.embedding_api_key},
                json={"model": self.settings.embedding_model, "input": text[:16000]},
            )
            response.raise_for_status()
            values = response.json()["data"][0]["embedding"]
        if len(values) != self.dimensions or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError("Embedding 返回维度与 EMBEDDING_DIMENSIONS 不一致或包含无效值。")
        return [float(v) for v in values]

    def _scope(self, scope: dict, query: str) -> tuple[str, str]:
        # Numbers and product-like latin identifiers are hard conditions as well.
        guarded = dict(scope, numeric_tokens=sorted(set(re.findall(r"\d+(?:\.\d+)?", query))),
                       identifiers=sorted(set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", query.casefold()))),
                       embedding_version=self.version)
        scope_hash = hashlib.sha256(canonical(guarded).encode()).hexdigest()
        exact_hash = hashlib.sha256((scope_hash + re.sub(r"\s+", " ", query.strip().casefold())).encode()).hexdigest()
        return scope_hash, exact_hash

    async def get(self, query: str, scope: dict, *, semantic: bool = True, exact: bool = True) -> tuple[Any | None, str, list[float] | None]:
        scope_hash, exact_hash = self._scope(scope, query)
        if self.mode == "demo":
            with sqlite3.connect(self.path) as conn:
                conn.execute("DELETE FROM entries WHERE expires < ?", (time.time(),))
                exact_payload = conn.execute("SELECT payload FROM entries WHERE key=?", (exact_hash,)).fetchone() if exact else None
                if exact_payload:
                    return json.loads(exact_payload[0]), "exact_hit", None
                if not semantic:
                    return None, "miss", None
                vec = await self.vector(query)
                rows = conn.execute("SELECT vector,payload FROM entries WHERE scope=? ORDER BY expires DESC LIMIT 256", (scope_hash,)).fetchall()
                scored = [(cosine(vec, json.loads(v)), p) for v, p in rows]
                if scored:
                    score, payload = max(scored, key=lambda entry: entry[0])
                    if score >= self.settings.semantic_threshold:
                        return json.loads(payload), "semantic_hit", vec
                return None, "miss", vec
        exact_payload = await self.redis.hget(self.prefix + exact_hash, "payload") if exact else None
        if exact_payload:
            return json.loads(exact_payload), "exact_hit", None
        if not semantic:
            return None, "miss", None
        vec = await self.vector(query)
        raw = struct.pack(f"<{len(vec)}f", *vec)
        results = await self.redis.execute_command(
            "FT.SEARCH", self.index, f"(@scope:{{{scope_hash}}})=>[KNN 1 @vector $vec AS distance]",
            "PARAMS", "2", "vec", raw, "SORTBY", "distance", "RETURN", "2", "payload", "distance", "DIALECT", "2",
        )
        if results and int(results[0]) > 0:
            fields = dict(zip(results[2][::2], results[2][1::2]))
            if float(fields[b"distance"]) <= 1 - self.settings.semantic_threshold:
                return json.loads(fields[b"payload"]), "semantic_hit", vec
        return None, "miss", vec

    async def put(self, query: str, scope: dict, payload: Any, vector: list[float] | None = None):
        scope_hash, exact_hash = self._scope(scope, query)
        if vector is None:
            vector = await self.vector(query)
        ttl = int(self.settings.cache_ttl_seconds)
        if self.mode == "demo":
            with sqlite3.connect(self.path) as conn:
                conn.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)", (exact_hash, scope_hash, query, canonical(vector), canonical(payload), time.time() + ttl))
        else:
            raw = struct.pack(f"<{len(vector)}f", *vector)
            pipe = self.redis.pipeline(transaction=True)
            pipe.hset(self.prefix + exact_hash, mapping={"scope": scope_hash, "exact": exact_hash, "vector": raw, "payload": canonical(payload)})
            pipe.expire(self.prefix + exact_hash, ttl)
            await pipe.execute()
