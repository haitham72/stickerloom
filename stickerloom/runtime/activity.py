"""The server's terminal, in a few words per thing that happened.

One line per event, on stderr: `08:51:12 [stickerloom] J068 generating video (kling3_0) for G121`. What prints:
- a paid job: created (generating), its result arrived (received, model, cost, seconds), failed or timed out (ERROR + why) — `jobs._write`;
- a batch: its stickers cut, its animations cut, added to a pack, any step that failed (ERROR) — `pipeline.emit`;
- an import, an Export to collection (ok or refused), a Telegram send.
Polling and page loads never print. `STICKERLOOM_ACTIVITY=0` turns it off (the test suite does); `STICKERLOOM_ACCESS_LOG=1` is the separate per-request JSON log.
The files stay the record (events.jsonl, jobs, model_calls.jsonl, ...): this is only what you see go by. Never raises."""
from __future__ import annotations

import os
import sys
import time


def on() -> bool:
    return str(os.environ.get("STICKERLOOM_ACTIVITY", "1")).strip().lower() not in ("0", "false", "no", "off")


def say(text: str, error: bool = False) -> None:
    if not on():
        return
    try:
        line = f"{time.strftime('%H:%M:%S')} [stickerloom] {'ERROR ' if error else ''}{' '.join(str(text).split())}"
        print(line[:400], file=sys.stderr, flush=True)
    except Exception:
        pass


def job(old: str | None, j: dict) -> None:
    """A job file is written: say it when its status changed to one worth seeing."""
    st = j.get("status")
    if st == old:
        return
    req = j.get("request") or {}
    model = j.get("model") or req.get("model") or "?"
    what = j.get("kind") or "job"
    where = f" for {j['generation']}" if j.get("generation") else ""
    if st == "REQUESTED" and old is None:
        label = str(req.get("label") or "")[:60]
        say(f"{j['id']} generating {what} ({model}){where}" + (f': "{label}"' if label and what != "video" else ""))
    elif st == "DONE":
        secs = round(j["completed_at"] - j["created_at"]) if j.get("completed_at") and j.get("created_at") else None
        cost = f", {j['cost']} credits" if j.get("cost") is not None else ""
        say(f"{j['id']} received {what} ({model}{cost}{f', {secs} s' if secs is not None else ''}){where}"
            + (" [recovered by hand]" if j.get("recovered_by") else ""))
    elif st in ("FAILED", "TIMEOUT"):
        say(f"{j['id']} {what} {st.lower()}{where}: {str(j.get('error') or 'no reason given')[:200]}", error=True)


def event(gid: int, ev: dict) -> None:
    """A batch event (pipeline.emit): only the results and the failures."""
    g, stage, d = f"G{int(gid):03d}", ev.get("stage"), ev.get("detail")
    if ev.get("status") == "error":
        say(f"{g} {str(stage).replace('_', ' ')}: {str(d if not isinstance(d, dict) else d.get('blocked') or d)[:200]}", error=True)
        return
    if ev.get("status") != "done" or not isinstance(d, dict):
        return
    if stage in ("sliced", "video_sliced") and "ready" in d:
        bad = f", {d['failed']} blocked" if d.get("failed") else ""
        say(f"{g} {'stickers' if stage == 'sliced' else 'animations'} cut: {d['ready']} ready{bad}")
    elif stage == "added_to_pack":
        say(f"{g} added {len(d.get('stickers') or [])} {d.get('kind', '')} sticker(s) to pack {d.get('pack')}")
