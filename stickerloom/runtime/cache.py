"""Redis, introduced in Phase 4: fast and DISPOSABLE. Postgres and the files are the truth; everything here can be rebuilt or
recomputed, nothing is written only here, and `FLUSHALL` mid-session costs cache misses and nothing else.

One class, two engines behind it. With Redis up (`STICKERLOOM_REDIS_URL`, default redis://localhost:6380/0; the generic REDIS_URL is
deliberately NOT read: .env may hold another project's) it uses Redis. Without it the same calls work on an in-process
store with the same semantics (TTL, SET NX locks, streams with ids), so the app, the tests and a laptop without Docker behave
the same, only slower and per-process. Every key is user-scoped: `stickerloom:u:{user}:...`, user = `local` until Phase 5 has users,
and every key carries the versions that affect its value, so a hit never crosses users or versions.

The engine boundary holds: nothing under stickerloom/engine imports this."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager

DEFAULT_URL = "redis://localhost:6380/0"


class Busy(Exception):
    """A lock is held by someone else (one turn at a time per session)."""


def _load_dotenv() -> None:
    from ..runtime import envfile
    envfile.load()

def digest(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]


class Cache:
    def __init__(self, url: str | None = None, user: str = "local", force_memory: bool = False):
        _load_dotenv()
        self.user = user
        self.url = url or os.environ.get("STICKERLOOM_REDIS_URL", DEFAULT_URL)
        self.stats: dict = {}
        self._r = None
        self._mem: dict = {}                # key -> (expires_at | None, value)
        self._streams: dict = {}            # key -> list[(id, fields)]
        self._seq = 0
        self._mu = threading.RLock()
        if not force_memory:
            try:
                import redis
                r = redis.Redis.from_url(self.url, socket_connect_timeout=0.5, socket_timeout=2, decode_responses=True)
                r.ping()
                self._r = r
            except Exception:
                self._r = None

    @property
    def engine(self) -> str:
        return "redis" if self._r is not None else "memory"

    def key(self, *parts) -> str:
        return f"stickerloom:u:{self.user}:" + ":".join(str(p) for p in parts)

    def _count(self, kind: str, hit: bool) -> None:
        s = self.stats.setdefault(kind, {"hit": 0, "miss": 0})
        s["hit" if hit else "miss"] += 1

    def _down(self) -> None:
        """Redis went away mid-run: carry on in memory (cache misses only, never an error)."""
        self._r = None

    # ---- plain values ---------------------------------------------------------------------------------------------
    def get(self, key: str, kind: str = "cache"):
        v = None
        if self._r is not None:
            try:
                raw = self._r.get(key)
                v = json.loads(raw) if raw is not None else None
            except Exception:
                self._down()
        if self._r is None:
            with self._mu:
                e = self._mem.get(key)
                if e and (e[0] is None or e[0] > time.time()):
                    v = json.loads(e[1])
                elif e:
                    self._mem.pop(key, None)
        self._count(kind, v is not None)
        return v

    def set(self, key: str, value, ttl: int | None = None) -> None:
        raw = json.dumps(value, ensure_ascii=False, default=str)
        if self._r is not None:
            try:
                self._r.set(key, raw, ex=ttl)
                return
            except Exception:
                self._down()
        with self._mu:
            self._mem[key] = (time.time() + ttl if ttl else None, raw)

    def delete(self, key: str) -> None:
        if self._r is not None:
            try:
                self._r.delete(key)
                return
            except Exception:
                self._down()
        with self._mu:
            self._mem.pop(key, None)

    def incr(self, key: str) -> int:
        if self._r is not None:
            try:
                return int(self._r.incr(key))
            except Exception:
                self._down()
        with self._mu:
            e = self._mem.get(key)
            n = int(json.loads(e[1])) + 1 if e else 1
            self._mem[key] = (None, json.dumps(n))
            return n

    def counter(self, key: str) -> int:
        if self._r is not None:
            try:
                return int(self._r.get(key) or 0)
            except Exception:
                self._down()
        with self._mu:
            e = self._mem.get(key)
            return int(json.loads(e[1])) if e else 0

    def cached(self, key: str, make, ttl: int | None = None, kind: str = "cache"):
        """Read-through: a hit returns the stored value; a miss computes with make(), stores and returns it."""
        v = self.get(key, kind)
        if v is not None:
            return v
        v = make()
        if v is not None:
            self.set(key, v, ttl)
        return v

    def window_count(self, key: str, ttl: int) -> int:
        """Count one event in a fixed window: the first call sets the expiry, later calls only count. Returns the count so far (rate limits)."""
        if self._r is not None:
            try:
                n = int(self._r.incr(key))
                if n == 1:
                    self._r.expire(key, ttl)
                return n
            except Exception:
                self._down()
        with self._mu:
            e = self._mem.get(key)
            if e and (e[0] is None or e[0] > time.time()):
                n = int(json.loads(e[1])) + 1
                self._mem[key] = (e[0], json.dumps(n))
            else:
                n = 1
                self._mem[key] = (time.time() + ttl, json.dumps(1))
            return n

    # ---- hashes (job progress: live display only, mirrors Postgres) ------------------------------------------------
    def hset(self, key: str, mapping: dict, ttl: int | None = None) -> None:
        mapping = {str(k): json.dumps(v, default=str) for k, v in mapping.items()}
        if self._r is not None:
            try:
                self._r.hset(key, mapping=mapping)
                if ttl:
                    self._r.expire(key, ttl)
                return
            except Exception:
                self._down()
        with self._mu:
            cur = {}
            e = self._mem.get(key)
            if e:
                cur = json.loads(e[1])
            cur.update(mapping)
            self._mem[key] = (time.time() + ttl if ttl else None, json.dumps(cur))

    def hgetall(self, key: str) -> dict:
        if self._r is not None:
            try:
                return {k: json.loads(v) for k, v in self._r.hgetall(key).items()}
            except Exception:
                self._down()
        with self._mu:
            e = self._mem.get(key)
            if e and (e[0] is None or e[0] > time.time()):
                return {k: json.loads(v) for k, v in json.loads(e[1]).items()}
        return {}

    # ---- locks (one turn at a time per session) ----------------------------------------------------------------------
    @contextmanager
    def lock(self, name: str, ttl_ms: int = 300_000):
        """SET NX PX: raises Busy at once when held. Released on exit; the expiry covers a crash."""
        key, token = self.key("lock", name), uuid.uuid4().hex
        got = False
        if self._r is not None:
            try:
                got = bool(self._r.set(key, token, nx=True, px=ttl_ms))
            except Exception:
                self._down()
        if self._r is None:
            with self._mu:
                e = self._mem.get(key)
                if e is None or (e[0] is not None and e[0] <= time.time()):
                    self._mem[key] = (time.time() + ttl_ms / 1000.0, json.dumps(token))
                    got = True
        if not got:
            raise Busy(f"{name} is busy: one turn at a time")
        try:
            yield
        finally:
            if self._r is not None:
                try:
                    if self._r.get(key) == token:
                        self._r.delete(key)
                except Exception:
                    pass
            with self._mu:
                e = self._mem.get(key)
                if e and json.loads(e[1]) == token:
                    self._mem.pop(key, None)

    # ---- streams (events per generation; the id is the SSE event id) --------------------------------------------------
    def xadd(self, stream: str, fields: dict, maxlen: int = 1000, ttl: int | None = None) -> str:
        flat = {str(k): json.dumps(v, default=str) for k, v in fields.items()}
        if self._r is not None:
            try:
                sid = self._r.xadd(stream, flat, maxlen=maxlen, approximate=True)
                if ttl:
                    self._r.expire(stream, ttl)
                return sid
            except Exception:
                self._down()
        with self._mu:
            self._seq += 1
            sid = f"{int(time.time() * 1000)}-{self._seq}"
            lst = self._streams.setdefault(stream, [])
            lst.append((sid, flat))
            del lst[:-maxlen]
            return sid

    def xread(self, stream: str, after: str = "0-0", count: int = 200) -> list[tuple[str, dict]]:
        """Entries after `after` (exclusive), oldest first: `Last-Event-ID` replay is native."""
        if self._r is not None:
            try:
                rows = self._r.xrange(stream, min=f"({after}" if after != "0-0" else "-", max="+", count=count)
                return [(sid, {k: json.loads(v) for k, v in f.items()}) for sid, f in rows]
            except Exception:
                self._down()
        with self._mu:
            def after_id(sid: str) -> bool:
                a = tuple(int(x) for x in after.split("-"))
                b = tuple(int(x) for x in sid.split("-"))
                return b > a
            return [(sid, {k: json.loads(v) for k, v in f.items()})
                    for sid, f in self._streams.get(stream, []) if after == "0-0" or after_id(sid)][:count]

    def flush_mine(self) -> None:
        """Drop every key of this user (tests and `doctor --stats`; never FLUSHALL: Redis may be shared)."""
        pat = self.key("*")
        if self._r is not None:
            try:
                for k in self._r.scan_iter(match=pat, count=500):
                    self._r.delete(k)
                return
            except Exception:
                self._down()
        with self._mu:
            self._mem.clear()
            self._streams.clear()

    def report(self) -> dict:
        out = {"engine": self.engine, "url": self.url if self._r is not None else None, "kinds": {}}
        for k, v in self.stats.items():
            n = v["hit"] + v["miss"]
            out["kinds"][k] = {**v, "hit_rate": round(v["hit"] / n, 3) if n else None}
        return out


_default: Cache | None = None
_default_lock = threading.Lock()


def default() -> Cache:
    global _default
    with _default_lock:
        if _default is None:
            _default = Cache()
        return _default


def reset_default() -> None:
    global _default
    with _default_lock:
        _default = None


def normalize_request(text: str) -> str:
    """Cache-key form of a request: case, whitespace and punctuation at the ends only. It must NEVER merge a semantic difference:
    "dog as banana" and "dog with bananas" are different keys (tests/test_cache.py)."""
    import re
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return t.strip(" .,;:!?\u061f\u060c\"'")
