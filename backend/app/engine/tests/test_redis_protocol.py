"""Pin the wire protocol used by the raw Search response parser, without I/O."""
import asyncio
import json
from unittest.mock import AsyncMock

from redis.asyncio import Redis

from app.config import Settings
from app.engine.cache import SemanticCache


def test_live_cache_pins_resp2_and_parses_search_array(tmp_path, monkeypatch):
    async def scenario():
        settings = Settings(_env_file=None, data_dir=tmp_path,
                            redis_url="redis://127.0.0.1:1/0",
                            embedding_model="isolated-model", embedding_dimensions=8)
        payload = {"items": [{"title": "cached provider result"}]}
        commands = []

        async def execute(client, *args):
            commands.append(args)
            if args[0] == "FT.CREATE":
                return b"OK"
            assert args[0] == "FT.SEARCH"
            return [1, b"cache-key", [b"payload", json.dumps(payload).encode(), b"distance", b"0.01"]]

        monkeypatch.setattr(Redis, "ping", AsyncMock(return_value=True))
        monkeypatch.setattr(Redis, "hget", AsyncMock(return_value=None))
        monkeypatch.setattr(Redis, "execute_command", execute)
        cache = SemanticCache(settings, "live")
        monkeypatch.setattr(cache, "vector", AsyncMock(return_value=[1.0] + [0.0] * 7))
        try:
            await cache.initialize()
            # Inspect the real client's pool options, not only from_url args.
            assert cache.redis.connection_pool.connection_kwargs["protocol"] == 2
            result, status, vector = await cache.get("same scope query", {"entity": "coffee"})
            assert status == "semantic_hit" and result == payload
            assert vector == [1.0] + [0.0] * 7
            assert [command[0] for command in commands] == ["FT.CREATE", "FT.SEARCH"]
        finally:
            await cache.close()

    asyncio.run(scenario())
