"""Problems: every automatic failure (a server error, a paid job that ends FAILED, a refused Telegram send) is one line in `out/problems.jsonl`.

An append-only file, like the model-call ledger: what happened, where, and a fingerprint so the same failure can be counted. It never raises, because
recording a failure must never be the reason a request fails. `stickerloom doctor` reports how many problems were recorded."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()


def problems_path(out) -> Path:
    return Path(out) / "problems.jsonl"


def fingerprint(source: str, what: str, where: str = "") -> str:
    """The same failure from the same place folds to the same fingerprint (numbers and ids in the message are ignored)."""
    norm = re.sub(r"\d+", "#", f"{source}|{where}|{what}".lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def open_auto(out, *, source: str, issue: str, what: str, where: str = "", context: dict | None = None, **_ignored) -> dict:
    """Record one problem. Returns the row; never raises."""
    row = {"ts": round(time.time(), 3), "source": source, "issue": issue, "what": str(what)[:500], "where": str(where)[:200],
           "fingerprint": fingerprint(source, str(what), str(where)), "context": context or {}}
    try:
        line = json.dumps(row, ensure_ascii=False, default=str)
        p = problems_path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except (OSError, TypeError, ValueError):
        pass
    return row


def listing(out, limit: int | None = None) -> list[dict]:
    """The recorded problems, newest first."""
    rows = []
    try:
        for line in problems_path(out).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    rows.reverse()
    return rows[:limit] if limit else rows
