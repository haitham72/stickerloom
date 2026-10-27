"""Every paid or model call is one line in out/model_calls.jsonl (what, parameters, latency,
credits if shown, output path). Phase 3A mirrors it into the model_calls table (write-through, and `db import`).
Never raises; never holds bytes. `cost` is Higgsfield credits, not dollars."""
from __future__ import annotations

import json
import time
from pathlib import Path


def log_path(out=None) -> Path:
    if out is not None:
        return Path(out) / "model_calls.jsonl"
    from ..runtime.paths import out_root
    return out_root() / "model_calls.jsonl"


def append(out, kind: str, provider: str, model: str, status: str = "OK", latency_ms: int | None = None,
           tokens_in: int | None = None, tokens_out: int | None = None, cost=None, attempt: int = 1,
           seed=None, prompt_version: str | None = None, generation_id: str | None = None,
           sticker_id: str | None = None, error: str | None = None, extra: dict | None = None) -> None:
    row = {"ts": round(time.time(), 3), "kind": kind, "provider": provider, "model": model,
           "status": status, "attempt": attempt, "latency_ms": latency_ms,
           "tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost, "seed": seed,
           "prompt_version": prompt_version, "generation_id": generation_id, "sticker_id": sticker_id,
           "error": (str(error)[:500] if error else None)}
    for k, v in (extra or {}).items():
        if isinstance(v, (bytes, bytearray)):
            continue
        try:
            json.dumps(v)
            row[k] = v
        except (TypeError, ValueError):
            row[k] = str(v)[:500]
    line = json.dumps(row, ensure_ascii=False)
    try:
        p = log_path(out)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        return
    try:  # Phase 3A write-through: the row key is the sha256 of this exact line, so import never duplicates it
        from ..store import sync
        sync.sync_model_call(p.parent, line)
    except Exception:
        pass


# ---------- reading the ledger back (the daily cap and the queue's time estimate) ----------

def read_rows(out) -> list[dict]:
    rows = []
    try:
        for line in log_path(out).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def _day_start(now: float | None = None) -> float:
    t = time.localtime(now or time.time())
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _credits(r: dict) -> float:
    try:
        return float(r.get("cost") or 0) if r.get("status", "OK") == "OK" else 0.0
    except (TypeError, ValueError):
        return 0.0


def spent_today(out) -> float:
    """Credits spent since local midnight, from the ledger (the daily cap `STICKERLOOM_DAILY_CREDITS` reads this)."""
    t0 = _day_start()
    return round(sum(_credits(r) for r in read_rows(out) if (r.get("ts") or 0) >= t0 and str(r.get("provider", "")).startswith(("higgsfield", "openai"))), 2)


def typical(out) -> dict:
    """Median seconds per 'image:<model>' / 'video:<model>' from the ledger, for the queue's 'about 2:45' estimate."""
    by: dict = {}
    for r in read_rows(out):
        if r.get("status", "OK") == "OK" and r.get("latency_ms") and r.get("kind") in ("IMAGE_SHEET", "IMAGE_SINGLE", "VIDEO") \
                and str(r.get("provider", "")).startswith(("higgsfield", "openai")):
            by.setdefault(("video:" if r["kind"] == "VIDEO" else "image:") + str(r.get("model")), []).append(r["latency_ms"] / 1000)
    return {k: round(sorted(v)[len(v) // 2]) for k, v in by.items()}
