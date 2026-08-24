"""Job queue and progress bus.

Redis-backed when `REDIS_URL` points at a live server, in-process otherwise.
That fallback is not a toy: it lets a single container run the API and its
worker together, which is exactly what a small deployment or a demo wants.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from nexus.config import settings
from nexus.util.logging import get_logger

log = get_logger(__name__)

QUEUE_KEY = "nexus:jobs:queue"
PRIORITY_QUEUE_KEY = "nexus:jobs:priority"
CHANNEL_PREFIX = "nexus:progress:"
CANCEL_KEY = "nexus:jobs:cancelled"
BLOCKING_TIMEOUT_S = 5


class _InProcess:
    """Single-process implementation used when Redis is unavailable."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.history: dict[str, list[dict]] = defaultdict(list)
        self.cancelled: set[str] = set()


_local = _InProcess()


class Bus:
    def __init__(self) -> None:
        self._redis = None
        self._checked = False

    async def redis(self):
        """Connect lazily; a Redis that is down degrades to in-process, it does
        not take the API with it."""
        if self._checked:
            return self._redis
        self._checked = True
        try:
            import redis.asyncio as aioredis

            # socket_timeout must exceed the longest blocking command we issue
            # (BRPOP), or the client raises while the server is happily waiting —
            # which reads as a dead queue and takes the worker down with it.
            client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=BLOCKING_TIMEOUT_S * 2,
                socket_connect_timeout=5,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            await asyncio.wait_for(client.ping(), timeout=3.0)
            self._redis = client
            log.info("bus_backend", extra={"backend": "redis"})
        except Exception as exc:
            log.warning("bus_redis_unavailable",
                        extra={"error": str(exc)[:200], "backend": "in-process"})
            self._redis = None
        return self._redis

    # ── queue ────────────────────────────────────────────────────────────

    async def enqueue(self, job_id: str, *, priority: bool = False) -> None:
        client = await self.redis()
        if client:
            await client.lpush(PRIORITY_QUEUE_KEY if priority else QUEUE_KEY, job_id)
        else:
            await _local.queue.put(job_id)

    async def dequeue(self, timeout: int = BLOCKING_TIMEOUT_S) -> str | None:
        """Block for up to `timeout` seconds. Returns None on an empty queue —
        a quiet queue is the normal case, never an error."""
        client = await self.redis()
        if client:
            try:
                result = await client.brpop([PRIORITY_QUEUE_KEY, QUEUE_KEY], timeout=timeout)
            except (TimeoutError, ConnectionError, OSError) as exc:
                log.debug("bus_dequeue_retry", extra={"error": str(exc)[:160]})
                return None
            return result[1] if result else None
        try:
            return await asyncio.wait_for(_local.queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def queue_depth(self) -> int:
        client = await self.redis()
        if client:
            return int(await client.llen(QUEUE_KEY)) + int(await client.llen(PRIORITY_QUEUE_KEY))
        return _local.queue.qsize()

    # ── cancellation ─────────────────────────────────────────────────────

    async def request_cancel(self, job_id: str) -> None:
        client = await self.redis()
        if client:
            await client.sadd(CANCEL_KEY, job_id)
            await client.expire(CANCEL_KEY, 86400)
        else:
            _local.cancelled.add(job_id)

    async def is_cancelled(self, job_id: str) -> bool:
        client = await self.redis()
        if client:
            return bool(await client.sismember(CANCEL_KEY, job_id))
        return job_id in _local.cancelled

    async def clear_cancel(self, job_id: str) -> None:
        client = await self.redis()
        if client:
            await client.srem(CANCEL_KEY, job_id)
        else:
            _local.cancelled.discard(job_id)

    # ── progress ─────────────────────────────────────────────────────────

    async def publish(self, job_id: str, event: dict[str, Any]) -> None:
        client = await self.redis()
        if client:
            payload = json.dumps(event)
            key = f"{CHANNEL_PREFIX}{job_id}"
            async with client.pipeline() as pipe:
                pipe.publish(key, payload)
                pipe.rpush(f"{key}:log", payload)
                pipe.ltrim(f"{key}:log", -500, -1)
                pipe.expire(f"{key}:log", 86400)
                await pipe.execute()
            return

        history = _local.history[job_id]
        history.append(event)
        del history[:-500]
        for queue in list(_local.subscribers.get(job_id, ())):
            queue.put_nowait(event)

    async def replay(self, job_id: str) -> list[dict]:
        """Events already emitted, so a late-connecting client sees the whole run."""
        client = await self.redis()
        if client:
            raw = await client.lrange(f"{CHANNEL_PREFIX}{job_id}:log", 0, -1)
            return [json.loads(item) for item in raw]
        return list(_local.history.get(job_id, []))

    async def subscribe(self, job_id: str) -> AsyncIterator[dict]:
        client = await self.redis()
        if client:
            pubsub = client.pubsub()
            await pubsub.subscribe(f"{CHANNEL_PREFIX}{job_id}")
            try:
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        yield json.loads(message["data"])
            finally:
                await pubsub.unsubscribe(f"{CHANNEL_PREFIX}{job_id}")
                await pubsub.aclose()
            return

        queue: asyncio.Queue = asyncio.Queue()
        _local.subscribers[job_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            _local.subscribers[job_id].discard(queue)

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._checked = False


bus = Bus()
