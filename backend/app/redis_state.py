"""Versioned Redis task metadata, indexes and renewable execution leases."""
from __future__ import annotations
import json
import logging
import uuid
import redis.asyncio as redis

logger = logging.getLogger("research.redis")

UPDATE = """
local current = tonumber(redis.call('HGET', KEYS[1], 'version') or '-1')
local incoming = tonumber(ARGV[1])
if incoming < current then return 0 end
local old = redis.call('HGET', KEYS[1], 'status')
if old then redis.call('SREM', ARGV[4] .. old, ARGV[2]) end
redis.call('HSET', KEYS[1], 'version', ARGV[1], 'status', ARGV[3], 'payload', ARGV[5])
local p = cjson.decode(ARGV[5])
for _, field in ipairs({'id','thread_id','phase','progress','attempt','owner','heartbeat_at','updated_at','current_agent'}) do
  redis.call('HSET', KEYS[1], field, tostring(p[field] or ''))
end
redis.call('HSET', KEYS[1], 'task_id', ARGV[2], 'thread_id', p['thread_id'] or ARGV[2])
redis.call('SADD', ARGV[4] .. ARGV[3], ARGV[2])
return 1
"""
RENEW = "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('EXPIRE',KEYS[1],ARGV[2]) else return 0 end"
RELEASE = "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end"


class RedisState:
    def __init__(self, url: str):
        self.url = url
        self.client = redis.from_url(url, protocol=2, decode_responses=True, socket_connect_timeout=1, socket_timeout=2) if url else None
        if self.client:
            # COMMAND INFO returns nil entries for unknown commands. Keep those
            # entries intact rather than using redis-py's full COMMAND parser.
            self.client.set_response_callback("COMMAND", lambda response: response)
        self.available = False
        self.vector_search_available = False
        self.owner = uuid.uuid4().hex

    async def connect(self):
        self.vector_search_available = False
        if not self.client:
            return False
        try:
            self.available = bool(await self.client.ping())
            if self.available:
                try:
                    commands = await self.client.execute_command("COMMAND", "INFO", "FT.CREATE", "FT.SEARCH")
                    self.vector_search_available = isinstance(commands, list) and len(commands) == 2 and all(commands)
                except (redis.RedisError, OSError):
                    # Basic metadata and leases can still work without Search,
                    # or when an ACL prevents checking the Search commands.
                    self.vector_search_available = False
        except (redis.RedisError, OSError):
            self.available = False
        return self.available

    async def project(self, task):
        if not self.client or not self.available:
            return False
        try:
            await self.client.eval(UPDATE, 1, f"research:task:{task['id']}", task["version"], task["id"], task["status"],
                                   "research:status:", json.dumps(task, ensure_ascii=False))
            return True
        except (redis.RedisError, OSError):
            self.available = False
            return False

    async def acquire(self, task_id, ttl=30):
        if not self.client or not self.available:
            return True
        try:
            return bool(await self.client.set(f"research:lease:{task_id}", self.owner, nx=True, ex=ttl))
        except (redis.RedisError, OSError):
            self.available = False
            return False

    async def renew(self, task_id, ttl=30):
        if not self.client or not self.available:
            return False
        try:
            return bool(await self.client.eval(RENEW, 1, f"research:lease:{task_id}", self.owner, ttl))
        except (redis.RedisError, OSError):
            self.available = False
            return False

    async def release(self, task_id):
        if self.client and self.available:
            try:
                await self.client.eval(RELEASE, 1, f"research:lease:{task_id}", self.owner)
            except (redis.RedisError, OSError):
                self.available = False

    async def close(self):
        if self.client:
            await self.client.aclose()

    def summary(self):
        if not self.available:
            note = "未连接；示例模式使用 SQLite，在线模式需启动 Redis"
        elif not self.vector_search_available:
            note = "已连接，任务元数据和租约可用；未确认 Search 向量检索命令，在线研究尚未就绪"
        else:
            note = "已连接，任务元数据、租约和 Search 命令可用；向量索引将在执行时验证"
        return {"available": self.available, "configured": bool(self.url),
                "vector_search_available": bool(self.available and self.vector_search_available), "note": note}
