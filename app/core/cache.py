"""Redis cache for the search path.

Invalidation is version-based rather than key-scanning. A counter lives at
`trials:version` and is baked into every cache key; the sync task INCRs it,
which makes every previously cached entry unreachable in one operation. The
naive alternative -- KEYS trials:* followed by DEL -- is O(keyspace) and
blocks the single-threaded Redis event loop for the duration.

Orphaned entries from old versions are never read again and fall out on their
own TTL, so the TTL is a garbage-collection backstop, not the primary
correctness mechanism.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

SEARCH_TTL = 300  # seconds; backstop only, see module docstring
VERSION_KEY = "trials:version"

_client: aioredis.Redis | None = None


def get_client() -> aioredis.Redis:
    """Lazy singleton. decode_responses=True so values come back as str and we
    are not sprinkling .decode() through the call sites."""
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def get_version() -> int:
    """Missing key means nothing has synced yet; treat that as version 0
    rather than erroring, so a cold Redis does not break search."""
    raw = await get_client().get(VERSION_KEY)
    return int(raw) if raw else 0


async def bump_version() -> int:
    """Called at the end of a sync. INCR is atomic, so concurrent syncs cannot
    land on the same version."""
    return int(await get_client().incr(VERSION_KEY))


def make_key(version: int, **params: Any) -> str:
    """Hash the params rather than concatenating them: query strings are
    user-supplied, arbitrarily long, and may contain characters that make
    keys awkward to inspect. sha1 is fine here -- this is a cache key, not
    a security boundary."""
    blob = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.sha1(blob.encode()).hexdigest()
    return f"search:v{version}:{digest}"


async def get_json(key: str) -> Any | None:
    raw = await get_client().get(key)
    return json.loads(raw) if raw else None


async def set_json(key: str, value: Any, ttl: int = SEARCH_TTL) -> None:
    await get_client().set(key, json.dumps(value, default=str), ex=ttl)


def bump_version_sync() -> int:
    """Sync counterpart for the Celery task.

    The async client binds its connection to the loop that created it, and
    the worker runs asyncio.run() per task -- the same stale-loop trap the
    SQLAlchemy engine hit. A background sync has just spent minutes crawling
    an HTTP API; blocking for one INCR costs nothing, so the sync client is
    the simpler correct answer rather than managing another lifecycle.
    """
    import redis

    with redis.from_url(settings.REDIS_URL, decode_responses=True) as client:
        return int(client.incr(VERSION_KEY))


async def aclose() -> None:
    """Close the client and clear the singleton.

    Needed in two places: FastAPI's lifespan shutdown, and between tests --
    pytest-asyncio gives each test its own event loop, so a client cached
    from an earlier test holds a connection bound to a closed loop. The
    module-level singleton assumes one loop per process, which is true under
    uvicorn and false everywhere else.
    """
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
