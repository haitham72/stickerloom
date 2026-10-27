"""The live event stream of a generation (Phase 4 Redis, read by Phase 5's SSE).

`pipeline.emit` already writes every event to `out/G###/events.jsonl` (the record). This module also puts the same event on a Redis
stream `stickerloom:u:<user>:events:G###` (capped, expiring), under the Phase 5 event names, with the Phase 1 stage kept in the payload so both
vocabularies stay searchable. The stream entry id is the SSE event id, so `Last-Event-ID` replay is native. Redis is disposable: when it is
down the in-memory fallback keeps the stream for this process, and the events.jsonl file remains the truth.

Stage -> event (docs/architecture.md): requested -> generation_started; sheet_picked -> sheet_generated; keyed -> sticker_processing; sliced ->
sticker_ready / sticker_failed (one per sticker, see `sticker_events`); video_requested / video_returned -> animation_started; video_cell ->
animation_ready / animation_failed; a gate decision -> review_decided; video_sheet_built -> video_sheet_ready; pack_final -> pack_complete;
any error -> generation_failed."""
from __future__ import annotations

TERMINAL = ("pack_complete", "generation_failed")
GATE_STAGES = ("plan_reviewed", "stills_reviewed", "video_sheet_reviewed", "anim_reviewed", "review")


def event_name(stage: str, status: str, decision: str | None = None) -> str | None:
    if status == "error":
        return "generation_failed"
    if stage == "pack_final":
        return "pack_complete"
    if stage in GATE_STAGES or decision:
        return "review_decided"
    return {
        ("requested", "done"): "generation_started", ("sheet_picked", "done"): "sheet_generated",
        ("keyed", "start"): "sticker_processing", ("keyed", "running"): "sticker_processing",
        ("video_requested", "done"): "animation_started", ("video_returned", "done"): "animation_started",
        ("video_cell", "done"): "animation_ready", ("video_sheet_built", "done"): "video_sheet_ready",
        ("sliced", "done"): "sheet_sliced",
    }.get((stage, status))


def stream_key(cache, gid: int) -> str:
    return cache.key("events", f"G{int(gid):03d}")


def publish(out, gid: int, ev: dict, cache=None) -> str | None:
    """Put one pipeline event on the stream. Never raises. Returns the stream id."""
    try:
        from . import cache as cachemod
        cache = cache or cachemod.default()
        name = event_name(str(ev.get("stage")), str(ev.get("status")), ev.get("decision"))
        if not name:
            return None
        d = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
        payload = {"event": name, "generation_id": f"G{int(gid):03d}", "stage": ev.get("stage"), "status": ev.get("status"), "ts": ev.get("ts"),
                   "ms": ev.get("ms"), "actor": ev.get("actor"), "decision": ev.get("decision"),
                   "gate": d.get("gate"), "index": d.get("index"), "trace_run_id": ev.get("trace_run_id")}
        return cache.xadd(stream_key(cache, gid), payload, maxlen=1000, ttl=24 * 3600)
    except Exception:
        return None


def sticker_events(out, gid: int, stickers: list, cache=None) -> None:
    """One event per sticker once the sheet is cut, in order: `sticker_ready` or `sticker_failed` with the asset url, so a client can show S1
    without waiting for the page to refresh."""
    try:
        from . import cache as cachemod
        cache = cache or cachemod.default()
        g = f"G{int(gid):03d}"
        for s in stickers:
            ok = s.get("status") == "READY"
            cache.xadd(stream_key(cache, gid), {"event": "sticker_ready" if ok else "sticker_failed", "generation_id": g, "sticker_id": f"{g}/S{s['index']}",
                                                "index": s["index"], "asset_url": f"/out/{g}/{s['png']}" if s.get("png") else None,
                                                "reason": None if ok else s.get("reason")}, maxlen=1000, ttl=24 * 3600)
    except Exception:
        pass


def read(gid: int, after: str = "0-0", cache=None, count: int = 200) -> list:
    """[(id, payload)] after an id: what an SSE client missed."""
    from . import cache as cachemod
    cache = cache or cachemod.default()
    return cache.xread(stream_key(cache, gid), after, count)


def sse_frame(sid: str, payload: dict) -> bytes:
    import json
    return (f"id: {sid}\nevent: {payload.get('event', 'message')}\ndata: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n").encode("utf-8")
