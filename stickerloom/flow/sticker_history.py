"""The generation history of every sticker of a batch, for the page: one summary per sticker with its decisions grouped by stage.

A sticker's `history` in result.json is the append-only list of everything that happened to it (`pipeline.hist`): the python checks, the human decisions, the
vision pre-reviews, every stroke / trim snapshot. It is long (a real batch holds 600 lines for 9 stickers, 40 of them "appearance" snapshots for one sticker), so the
Studio shows it folded: the sticker's latest decision on one line, then the stages in the order they first happened, each with its count and its newest
decision, and the lines of a stage on demand. Pure function over the result dict; no I/O, no model, never raises on an odd line.
"""
from __future__ import annotations

PER_STICKER = 400                                  # the newest lines kept per sticker (the count stays the truth)
_MAX_FACTS = 6                                     # at most this many facts per line


def _text(v):
    return None if v is None else str(v)


def _facts(d) -> dict:
    """The small, readable part of a line's detail: scalars and short lists ("outline_px": 8, "override": ["cross_slot"], a BLOCK's check / value / limit / note). Empty values,
    the raw measurements (`data`, nested objects) and anything long stay out: the page shows a history, not a dump."""
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        if len(out) >= _MAX_FACTS:
            break
        if k == "data" or v is None or v == [] or v == {} or isinstance(v, dict):
            continue
        if isinstance(v, (bool, int, float)):
            out[str(k)] = v
        elif isinstance(v, str):
            out[str(k)] = v[:120]
        elif isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[str(k)] = [x if not isinstance(x, str) else x[:40] for x in v[:6]]
    return out


def _brief(h: dict) -> dict:
    """The line as the page needs it: when, who, what, why, which video sheet it was about, and its facts."""
    out = {"ts": h.get("ts"), "actor": _text(h.get("actor")), "decision": _text(h.get("decision")), "reason": _text(h.get("reason"))}
    if h.get("ref"):
        out["ref"] = _text(h.get("ref"))
    facts = _facts(h.get("detail"))
    if facts:
        out["detail"] = facts
    return out


def _last(stage: str, h: dict) -> dict:
    return {"ts": h.get("ts"), "stage": stage, "actor": _text(h.get("actor")), "decision": _text(h.get("decision")), "reason": _text(h.get("reason"))}


def sticker_summary(batch: dict, gid: str, s: dict, per_sticker: int = PER_STICKER) -> dict:
    lines = [h for h in (s.get("history") or []) if isinstance(h, dict)]
    order, count, last = [], {}, {}
    for h in lines:                                # stages in the order they first happened
        st = str(h.get("stage"))
        if st not in count:
            order.append(st)
            count[st] = 0
        count[st] += 1
        last[st] = h
    keep = lines[-per_sticker:] if per_sticker > 0 else []
    kept: dict[str, list] = {st: [] for st in order}
    for h in reversed(keep):                       # newest first
        kept[str(h.get("stage"))].append(_brief(h))
    newest = lines[-1] if lines else None
    return {"id": f"{gid}/S{s.get('index')}", "index": s.get("index"), "key": s.get("key"), "name": s.get("name"), "emoji": s.get("emoji"),
            "status": s.get("status"), "anim_status": s.get("anim_status"), "png": s.get("png"), "review": s.get("review") or {},
            "lines": len(lines), "shown": len(keep), "last": _last(str(newest.get("stage")), newest) if newest else None,
            "stages": [{"stage": st, "count": count[st], "last": _brief(last[st]), "lines": kept[st]} for st in order]}


def batch_history(res: dict, gid: str, index: int | None = None, per_sticker: int = PER_STICKER) -> dict:
    """{generation_id, stickers: [summary, ...]} for every sticker of the batch, or only sticker `index`."""
    out = []
    for s in res.get("stickers") or []:
        if not isinstance(s, dict) or (index is not None and s.get("index") != index):
            continue
        out.append(sticker_summary(res, gid, s, per_sticker))
    return {"generation_id": gid, "stickers": out}
