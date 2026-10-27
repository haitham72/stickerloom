"""Phase 2 step S4: how often does a video made from the normalised video sheet leave its cell?

Reads only the result files (`out/G###/result.json`): for every video sheet whose video came back and was sliced, each of
its cells carries the verifier's verdicts (`anim_report`: inside_slot, cross_slot, inside_frame). The command counts the
share of cells flagged by any of them, the cross-cell interaction rate (cross_slot) and the subject size in the video, per
`slot_fill` (the gap the user slid for that sheet), so the default gap can be chosen from numbers, not from a guess.
Nothing is opened as media and nothing is spent."""
from __future__ import annotations

import json
import time
from pathlib import Path

GEOMETRY = ("inside_slot", "cross_slot", "inside_frame")


def _cells(res: dict):
    """(sheet, sticker) for every cell of every returned and sliced video sheet of one result."""
    by_idx = {s["index"]: s for s in res.get("stickers", [])}
    for v in res.get("video_sheets", []):
        if v.get("status") == "SLICED" and v.get("video"):
            for i in v.get("slots", []):
                if i in by_idx:
                    yield v, by_idx[i]


def _failed(st: dict) -> set:
    return {c["name"] for c in st.get("anim_report") or [] if not c.get("ok") and c.get("name") in GEOMETRY}


def measure(out: Path) -> dict:
    out = Path(out)
    groups: dict = {}
    gens = 0
    for d in sorted(out.glob("G[0-9][0-9][0-9]*")):
        f = d / "result.json"
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seen = False
        for v, st in _cells(res):
            seen = True
            fill = round(float(v.get("slot_fill") or 0.74), 2)
            g = groups.setdefault(fill, {"slot_fill": fill, "sheets": set(), "cells": 0, "ready": 0, "flagged": 0,
                                         "cross_slot": 0, "inside_slot": 0, "inside_frame": 0, "subject_px": []})
            g["sheets"].add(f"{d.name}/{v.get('id')}")
            g["cells"] += 1
            g["ready"] += 1 if st.get("anim_status") == "READY" else 0
            bad = _failed(st)
            g["flagged"] += 1 if bad else 0
            for name in bad:
                g[name] += 1
            px = (st.get("anim_metrics") or {}).get("subject_px_in_video")
            if isinstance(px, (int, float)):
                g["subject_px"].append(px)
        gens += 1 if seen else 0
    rows = []
    for fill in sorted(groups):
        g = groups[fill]
        n = g["cells"]
        px = g["subject_px"]
        rows.append({"slot_fill": fill, "sheets": len(g["sheets"]), "cells": n, "ready": g["ready"], "flagged": g["flagged"],
                     "flagged_share": round(g["flagged"] / n, 3) if n else None,
                     "cross_slot": g["cross_slot"], "cross_slot_rate": round(g["cross_slot"] / n, 3) if n else None,
                     "inside_slot": g["inside_slot"], "inside_frame": g["inside_frame"],
                     "subject_px_mean": round(sum(px) / len(px), 1) if px else None,
                     "subject_px_min": min(px) if px else None})
    tot_cells = sum(r["cells"] for r in rows)
    tot_flag = sum(r["flagged"] for r in rows)
    return {"generations": gens, "sheets": sum(r["sheets"] for r in rows), "cells": tot_cells, "flagged": tot_flag,
            "flagged_share": round(tot_flag / tot_cells, 3) if tot_cells else None, "by_slot_fill": rows}


def render(m: dict) -> str:
    if not m["cells"]:
        return ("No returned video sheet has been sliced yet: nothing to measure. Make a video from a video sheet "
                "(Studio -> Animate), then run this again.")
    lines = [f"{m['cells']} cells in {m['sheets']} video sheet(s) of {m['generations']} generation(s): "
             f"{m['flagged']} flagged ({m['flagged_share']:.1%}); target: none",
             "gap = 1 - slot_fill.  flagged = any of inside_slot / cross_slot / inside_frame failed.",
             "slot_fill  gap  sheets  cells  flagged  share   cross_slot  inside_slot  inside_frame  subject_px(mean/min)"]
    for r in m["by_slot_fill"]:
        lines.append(f"{r['slot_fill']:<9.2f} {round((1 - r['slot_fill']) * 100):>3}%  {r['sheets']:>6}  {r['cells']:>5}  "
                     f"{r['flagged']:>7}  {r['flagged_share']:>5.1%}   {r['cross_slot']:>9}  {r['inside_slot']:>11}  "
                     f"{r['inside_frame']:>12}  {r['subject_px_mean']}/{r['subject_px_min']}")
    return "\n".join(lines)


def record(out: Path, m: dict, path: Path) -> Path:
    """Append this measurement to docs/architecture.md ("Measurements") (created on first use)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else "# Phase 2 measurements\n\nEach block is one run of `python -m stickerloom measure-cells --record`.\n"
    block = f"\n## measure-cells {time.strftime('%Y-%m-%d %H:%M')}\n\n```\n{render(m)}\n```\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(head + block)
    return path


# ---- sharpness: how soft is each stored animation next to its own approved still (the reworked `sharpness` check, measured on real cells) -----------------
def sharpness(out: Path, cfg=None) -> dict:
    """`detail_vs_still` for every READY animation under out/G###: the animation's edge detail over its still's, and what blur radius that equals.
    Reads the stored PNG and WEBM only (decoded with ffmpeg); nothing is looked at, nothing is written."""
    import numpy as np
    from PIL import Image
    from ..engine import ffmpeg as ff
    from ..engine.config import EngineConfig
    from ..engine.video import detail_vs_ref, soft_sigma
    cfg = cfg or EngineConfig()
    out = Path(out)
    rows: dict = {}
    for d in sorted(out.glob("G[0-9][0-9][0-9]*")):
        try:
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for st in res.get("stickers", []):
            if not (st.get("png") and st.get("webm") and st.get("anim_status") == "READY"):
                continue
            try:
                still = np.array(Image.open(d / st["png"]).convert("RGBA"))
                info = ff.probe(d / st["webm"], vp9_native=True)
                frames = ff.decode_full(d / st["webm"], int(info["width"]), int(info["height"]), 6, None)
                if frames.shape[1:3] != still.shape[:2]:
                    import cv2
                    still = cv2.resize(still, (frames.shape[2], frames.shape[1]), interpolation=cv2.INTER_AREA)
                dv = detail_vs_ref(frames, still)
            except Exception:
                continue
            if dv is None:
                continue
            rows.setdefault(d.name, []).append((dv, soft_sigma(still, dv)))
    by = []
    for g, vals in rows.items():
        dv = [v[0] for v in vals]
        by.append({"generation": g, "cells": len(vals), "mean_detail": round(sum(dv) / len(dv), 2), "min_detail": round(min(dv), 2),
                   "mean_sigma": round(sum(v[1] for v in vals) / len(vals), 1), "flagged": sum(1 for x in dv if x < cfg.min_detail_vs_still)})
    n = sum(r["cells"] for r in by)
    return {"threshold": cfg.min_detail_vs_still, "cells": n, "flagged": sum(r["flagged"] for r in by), "by_generation": by}


def render_sharpness(m: dict) -> str:
    if not m["cells"]:
        return "No animated sticker with a still was found: nothing to measure."
    lines = [f"{m['cells']} animations, {m['flagged']} softer than {m['threshold']:.2f}x of their still's edge detail",
             "generation  cells  mean_detail  min_detail  mean_blur_px  flagged"]
    for r in m["by_generation"]:
        lines.append(f"{r['generation']:<10} {r['cells']:>6}  {r['mean_detail']:>11.2f}  {r['min_detail']:>10.2f}  {r['mean_sigma']:>12.1f}  {r['flagged']:>7}")
    return "\n".join(lines)
