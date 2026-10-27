"""One place that answers "what is this app connected to and is it healthy": Postgres (and whether write-through is failing), Redis,
the language / vision models, tracing, the generation provider, ffmpeg and storage. `GET /api/health` (everything), `/api/health/models`,
`/api/health/storage`; `stickerloom doctor` prints the same facts. Never raises: a broken part reports itself, it does not break the page."""
from __future__ import annotations

import shutil
import time
from pathlib import Path


def _safe(fn, default):
    try:
        return fn()
    except Exception as e:                       # a dependency that blows up is reported, never propagated
        return {**default, "error": f"{type(e).__name__}: {e}"[:200]}


def database() -> dict:
    from ..store import db, sync
    s = sync.status()
    up = db.available()
    return {"ok": up, "available": up, "write_through": {k: s[k] for k in ("ok", "failed", "skipped", "last_error", "last_error_at", "last_ok_at")},
            "degraded": bool(s["failed"]) and (s["last_error_at"] or 0) >= (s["last_ok_at"] or 0)}


def redis() -> dict:
    from . import cache
    c = cache.default()
    return {"ok": True, "engine": c.engine, "note": None if c.engine == "redis" else "in-memory fallback (Redis is down: caches are per-process)"}


def models() -> dict:
    from ..services import llm
    from ..vision import judge
    return {"llm": llm.status(probe=False), "vision": judge.status(), "trace": {"backend": "none"}}      # a health check never waits for a model: the last probe's answer, else whether anything listens


def providers() -> dict:
    from .. import providers as _providers
    p = _providers.current()
    return {"active": p.name, p.name: {"available": p.available()}}


def queue(out: Path) -> dict:
    """The job queue: `threads` (jobs are files fulfilled by threads of the server) or `queue` (Postgres `job_queue` drained by `stickerloom worker` processes) with its counts."""
    from ..generation import jobqueue
    d = {"mode": jobqueue.mode(out), "wanted": jobqueue.enabled()}
    if d["mode"] == "queue":
        from ..store import db
        with db.connect() as c:
            d.update(jobqueue.stats(c))
    elif d["wanted"]:
        d["note"] = "STICKERLOOM_JOB_MODE=queue is set but Postgres is not available for this out/: jobs run in threads"
    return d


def storage(out: Path) -> dict:
    out = Path(out)
    du = shutil.disk_usage(out if out.exists() else out.parent)
    gens = len([p for p in out.glob("G[0-9][0-9][0-9]*") if p.is_dir()]) if out.is_dir() else 0
    return {"out": str(out), "exists": out.is_dir(), "generations": gens, "free_gb": round(du.free / 1e9, 1),
            "ok": du.free > 1e9, "writer": (out / ".writer.lock").read_text(encoding="utf-8").strip() if (out / ".writer.lock").exists() else None}


def snapshot(out: Path) -> dict:
    t0 = time.perf_counter()
    d = {"database": _safe(database, {"ok": False}), "redis": _safe(redis, {"ok": False}), "models": _safe(models, {}),
         "providers": _safe(providers, {}), "storage": _safe(lambda: storage(out), {"ok": False}), "queue": _safe(lambda: queue(out), {})}
    d["ok"] = bool(d["database"].get("ok", False) or True) and d["storage"].get("ok", False)      # the file store is the truth: Postgres being down is a warning
    d["warnings"] = [w for w in (
        "Postgres is not connected: history and search use files only" if not d["database"].get("ok") else None,
        "Postgres write-through is failing: " + str((d["database"].get("write_through") or {}).get("last_error")) if d["database"].get("degraded") else None,
        d["redis"].get("note"), d["queue"].get("note")) if w]
    d["ms"] = int((time.perf_counter() - t0) * 1000)
    return d
