"""The durable half of idempotency: `Idempotency-Key` answers kept in Postgres for 24 hours (migration 008), behind the cache.

The cache (Redis, or memory when Redis is down) answers first and is what a running server uses. This is what still knows a key after a restart without Redis, so a repeated
click or a retried request never starts a second paid job. Best effort like every write-through: it only works for the real `out/` (the same rule as `sync.enabled`, so tests and
STICKERLOOM_OUT copies never touch the shared database), never raises, never slows the request (availability is cached per process). The key is stored as its sha256; the FIRST
answer wins; records older than 24 hours are not answered (and are pruned when a new one is written)."""
from __future__ import annotations

import hashlib
import json

from . import db, sync

TTL_HOURS = 24


def key_sha(key) -> str:
    return hashlib.sha256(str(key).strip().encode("utf-8")).hexdigest()


def _usable(out) -> bool:
    try:
        return bool(sync.enabled(out) and db.available())
    except Exception:
        return False


def get(out, scope: str, key) -> dict | None:
    """The stored first answer for this key and scope, or None (no record, too old, no database, or a temp out)."""
    if not _usable(out):
        return None
    try:
        with db.connect() as c:
            row = c.execute("select response from idempotency_keys where scope = %s and key_sha = %s and created_at > now() - make_interval(hours => %s)",
                            (scope, key_sha(key), TTL_HOURS)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def put(out, scope: str, key, response) -> None:
    """Keep the answer. The first writer wins (`on conflict do nothing`); old records are pruned on the way."""
    if not _usable(out):
        return
    try:
        with db.connect() as c:
            c.execute("delete from idempotency_keys where created_at < now() - make_interval(hours => %s)", (TTL_HOURS,))
            c.execute("insert into idempotency_keys (scope, key_sha, response) values (%s, %s, %s::jsonb) on conflict (scope, key_sha) do nothing",
                      (scope, key_sha(key), json.dumps(response, ensure_ascii=False, default=str)))
    except Exception:
        return
