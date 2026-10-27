"""Best-effort write-through: pipeline.write_result() calls sync_result(), which persists the
generation when Postgres is up. Never raises, never slows the pipeline: availability is cached per
process, and test temp dirs (any out/ that is not the default one) are skipped so suites stay clean."""
from __future__ import annotations

import os
from pathlib import Path

import threading
import time

from . import db
from . import repo

# Write-through is best effort and must never break the pipeline, but a failure must not be invisible either (audit 2026-10-02):
# every outcome is counted here and shown by `stickerloom doctor` and GET /api/health.
STATS: dict = {"ok": 0, "failed": 0, "skipped": 0, "last_error": None, "last_error_at": None, "last_ok_at": None}
_STATS_LOCK = threading.Lock()


def _note(kind: str, err: Exception | None = None) -> None:
    with _STATS_LOCK:
        STATS[kind] += 1
        if kind == "ok":
            STATS["last_ok_at"] = round(time.time(), 1)
        if err is not None:
            STATS["last_error"] = f"{type(err).__name__}: {err}"[:300]
            STATS["last_error_at"] = round(time.time(), 1)


def status() -> dict:
    with _STATS_LOCK:
        return dict(STATS, available=db.available())


def _default_out() -> Path:
    from ..runtime.paths import out_root
    try:
        return out_root().resolve()
    except Exception:
        return Path("")


def is_default_out(out) -> bool:
    """True only for the real out/ (tests and STICKERLOOM_OUT copies never touch the shared database)."""
    try:
        return Path(out).resolve() == _default_out() and os.environ.get("STICKERLOOM_OUT") in (None, "")
    except Exception:
        return False


def enabled(out) -> bool:
    flag = os.environ.get("STICKERLOOM_DB_WRITE", "").lower()
    if flag in ("0", "no", "off", "false"):
        return False
    if flag in ("1", "on", "true", "yes"):
        return True
    return is_default_out(out)  # default: only the real out/ (STICKERLOOM_OUT copies use `db import`)


def sync_job(out, job: dict) -> bool:
    """A job file changed (claim / done / fail): mirror it into `tasks` right away. Best effort, never raises."""
    try:
        if not enabled(out) or not db.available():
            _note("skipped")
            return False
        with db.connect() as c:
            ok = repo.save_job(c, job, Path(out))
        _note("ok")
        return ok
    except Exception as e:
        _note("failed", e)
        return False


def sync_model_call(out, line: str) -> bool:
    """A line was appended to out/model_calls.jsonl: mirror it into `model_calls`. Best effort, never raises."""
    try:
        if not enabled(out) or not db.available():
            _note("skipped")
            return False
        with db.connect() as c:
            ok = repo.save_model_call(c, line)
        _note("ok")
        return ok
    except Exception as e:
        _note("failed", e)
        return False


def sync_session(out, sess: dict) -> bool:
    """A chat session changed: mirror it into Postgres. Best effort, never raises."""
    try:
        if not enabled(out) or not db.available():
            _note("skipped")
            return False
        with db.connect() as c:
            repo.save_session(c, sess)
        _note("ok")
        return True
    except Exception as e:
        _note("failed", e)
        return False


def sync_pack_session(out, s: dict) -> bool:
    """A pack session's claim ledger changed (generation/claims.py): mirror it. Best effort, never raises."""
    try:
        if not enabled(out) or not db.available():
            _note("skipped")
            return False
        with db.connect() as c:
            repo.save_pack_session(c, s)
        _note("ok")
        return True
    except Exception as e:
        _note("failed", e)
        return False


def sync_result(out, gid: int, res: dict) -> bool:
    try:
        if not enabled(out):
            return False
        if not db.available():
            _note("skipped")
            return False
        with db.connect() as c:
            repo.save_generation(c, Path(out), gid)
        _note("ok")
        return True
    except Exception as e:
        _note("failed", e)
        return False
