from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as redis

from app.redis_state import RedisState


@pytest.mark.asyncio
@pytest.mark.parametrize("commands, expected", [([None, None], False), ([["ft.create"], ["ft.search"]], True)])
async def test_connected_redis_checks_search_commands_without_losing_basic_availability(monkeypatch, commands, expected):
    client = MagicMock()
    client.ping = AsyncMock(return_value=True)
    client.execute_command = AsyncMock(return_value=commands)
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: client)
    state = RedisState("redis://127.0.0.1:1/0")
    assert await state.connect() is True
    assert state.available is True
    assert state.vector_search_available is expected
    client.execute_command.assert_awaited_once_with("COMMAND", "INFO", "FT.CREATE", "FT.SEARCH")
    assert state.summary()["vector_search_available"] is expected


def test_old_redis_is_connected_but_online_mode_is_not_ready(settings, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    # Explicit test values only; no paid service can be reached by this test.
    settings.model_name = "test-model"
    settings.model_base_url = "https://model.test.invalid/v1"
    settings.model_api_key = "test-only-model-key"
    settings.embedding_model = "test-embedding"
    settings.embedding_base_url = "https://embedding.test.invalid/v1"
    settings.embedding_api_key = "test-only-embedding-key"
    settings.search_api_key = "test-only-search-key"
    settings.redis_url = "redis://127.0.0.1:1/0"
    client = MagicMock()
    client.ping = AsyncMock(return_value=True)
    client.execute_command = AsyncMock(return_value=[None, None])
    client.aclose = AsyncMock()
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: client)
    application = create_app(settings)
    with TestClient(application) as active:
        config = active.get("/api/config").json()
        assert config["missing_live_config"] == []
        assert config["redis"]["available"] is True
        assert config["redis"]["vector_search_available"] is False
        assert config["live_ready"] is False
        health = active.get("/api/health").json()
        assert health["features"]["live"] is False
        assert health["features"]["demo"] is True
        response = active.post("/api/research/tasks", json={"question": "验证在线研究配置", "mode": "live"})
        assert response.status_code == 422
        assert "FT.CREATE/FT.SEARCH" in response.json()["detail"]
        assert active.get("/api/research/tasks").json() == []
        assert "test-only-" not in str(config)
