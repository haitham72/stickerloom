"""Quality and timing numbers from what is already on disk: no new bookkeeping, no model, no network.

- **time to the first sticker**: from the batch's `requested` event to the first `sliced` event that finished, in seconds (the batch starts when the sheet is in hand, so a
  live run's provider wait is not included: that is in `out/jobs/J###.json` and `usage.typical`).
- **approval rates**: of the stickers a human has DECIDED at a gate (approved or rejected), the share approved, for the stills (G2) and for the animations (G4). Undecided ones
  count for neither side.
- **regeneration rate**: the share of batches that are a redo (`regen_of` set: one sticker made again as a 1x1 child).
- **failed batches**: batches with no READY sticker at all.
Counts only: what the numbers mean for taste is the owner's call (the vision judge is still uncalibrated, docs/architecture.md ("Measurements"))."""
from __future__ import annotations

import statistics
from pathlib import Path


def _rate(a: int, b: int):
    return round(a / (a + b), 3) if (a + b) else None


def first_sticker_seconds(events: list) -> float | None:
    start = next((e["ts"] for e in events if e.get("stage") == "requested" and isinstance(e.get("ts"), (int, float))), None)
    cut = next((e["ts"] for e in events if e.get("stage") == "sliced" and e.get("status") == "done" and isinstance(e.get("ts"), (int, float))), None)
    return round(cut - start, 1) if start is not None and cut is not None and cut >= start else None


def collect(out, owner: str | None = None) -> dict:
    """Numbers over every batch under `out/` (only `owner`'s when given: a member's own). Unreadable batches are skipped."""
    from . import pipeline as pl
    out = Path(out)
    rows, firsts = [], []
    tot = {"ready": 0, "approved": 0, "rejected": 0, "undecided": 0, "animated": 0, "anim_approved": 0, "anim_rejected": 0}
    redo = failed = 0
    for gid in pl.list_ids(out):
        try:
            res = pl.read_result(out, gid)
            events = pl.read_events(out, gid)
        except Exception:
            continue
        if owner is not None and res.get("owner", "local") != owner:
            continue
        st = [s for s in res.get("stickers") or [] if isinstance(s, dict)]
        ready = [s for s in st if s.get("status") == "READY"]
        appr = sum(1 for s in ready if (s.get("review") or {}).get("still") == "APPROVED")
        rej = sum(1 for s in ready if (s.get("review") or {}).get("still") == "REJECTED")
        anim = [s for s in ready if s.get("anim_status") == "READY"]
        aa = sum(1 for s in anim if (s.get("review") or {}).get("anim") == "APPROVED")
        ar = sum(1 for s in anim if (s.get("review") or {}).get("anim") == "REJECTED")
        first = first_sticker_seconds(events)
        if first is not None:
            firsts.append(first)
        tot["ready"] += len(ready)
        tot["approved"] += appr
        tot["rejected"] += rej
        tot["undecided"] += len(ready) - appr - rej
        tot["animated"] += len(anim)
        tot["anim_approved"] += aa
        tot["anim_rejected"] += ar
        redo += 1 if res.get("regen_of") else 0
        failed += 0 if ready else 1
        rows.append({"id": f"G{gid:03d}", "ready": len(ready), "approved": appr, "rejected": rej, "animated": len(anim), "first_sticker_s": first, "redo": bool(res.get("regen_of"))})
    n = len(rows)
    t = {"n": len(firsts)}
    if firsts:
        s = sorted(firsts)
        t.update(median=round(statistics.median(s), 1), p90=round(s[min(len(s) - 1, int(0.9 * len(s)))], 1), max=s[-1])
    else:
        t.update(median=None, p90=None, max=None)
    return {"batches": n, "stickers": tot, "approval_rate": {"still": _rate(tot["approved"], tot["rejected"]), "animation": _rate(tot["anim_approved"], tot["anim_rejected"])},
            "regeneration_rate": {"redo_batches": redo, "batches": n, "rate": round(redo / n, 3) if n else None},
            "time_to_first_sticker_s": t, "failed_batches": failed, "per_batch": rows}
