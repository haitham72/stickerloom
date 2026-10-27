"""The project map: ONE JSON tree of everything a batch belongs to, so the Studio's map column never stitches it
from four calls in the browser (rule 11). A pure read of what exists; nothing is migrated or written.

    Project = one idea: a "pack of batches" (pipeline._packs: the same request by the same person within PACK_GAP_S, or a drag-and-drop link)
     └ Batch = one set of stickers: a family (flow/groups.py), its root's request
        └ Sheet = one try at that batch: each generation of the family (the first try, a redo, an edit, a joined one)
           └ Video = one animation of that sheet: result.json `video_sheets` (A1, A2 …), the one the stickers are cut from marked `used`

`project_map(out, gid, viewer)`: the project of batch `gid`; `viewer` limits it to the batches that person may see (an owner sees all, a member their own:
`Console.visible`). The jobs in flight of the project's batches ride on their sheet (`jobs`), so a sheet being made shows as such."""
from __future__ import annotations

from pathlib import Path

from . import gates, groups
from . import pipeline as pl

ACTIVE_JOBS = ("REQUESTED", "CLAIMED")
LABEL = {None: "First try", "redo": "Redo", "edit": "Edited", "joined": "Joined"}


def _title(text: str) -> str:
    from ..generation import actions
    t = " ".join(actions.strip_presets(str(text or "")).replace("_", " ").split())
    return (t[:1].upper() + t[1:]) if t else "Untitled"


def _sheet_status(res: dict) -> str:
    """making (no stickers yet) | failed (an error, or every cell blocked: the sheet is not cut) | ready."""
    sts = res.get("stickers") or []
    if res.get("error"):
        return "failed"
    if not sts or res.get("stage") in ("requested", "sheet_picked"):
        return "making"
    if all(s.get("status") == "FAILED" for s in sts) and any(not c.get("ok") and c.get("severity") != "WARN" for c in (res.get("verify") or {}).get("sheet") or []):
        return "not_cut"
    return "ready"


def _video(res: dict, v: dict, used: dict | None, base: str) -> dict:
    sts = {int(s["index"]): s for s in res.get("stickers") or []}
    slots = [int(i) for i in v.get("slots") or []]
    ready = sum(1 for i in slots if (sts.get(i) or {}).get("anim_status") == "READY") if used and used.get("id") == v["id"] else None
    return {"id": v["id"], "status": v.get("status"), "used": bool(used and used.get("id") == v["id"]), "slots": len(slots),
            "thumb": base + v["file"] if v.get("file") else None, "animated": ready, "blocked": bool(v.get("blocked"))}


def _sheet(out: Path, gid: int, n: int, root_prompt: str, jobs_of: dict) -> dict | None:
    try:
        res = pl.read_result(out, gid)
    except Exception:
        return None
    gidn = res["generation_id"]
    base = f"/out/{gidn}/"
    src = res.get("source") or {}
    used = gates.cut_sheet(res)
    sts = res.get("stickers") or []
    rel = groups.relation(res)
    return {"id": gidn, "number": int(res["number"]), "n": n, "label": LABEL.get(rel, "Try") if n > 1 or rel else "First try", "relation": rel,
            "prompt": res.get("prompt") or "", "prompt_changed": bool(res.get("prompt")) and (res.get("prompt") or "").strip() != root_prompt.strip(),
            "status": _sheet_status(res), "stage": res.get("stage"), "thumb": base + src["sheet_copy"] if src.get("sheet_copy") else None,
            "counts": {"stickers": len(sts), "ready": sum(1 for s in sts if s.get("status") == "READY"),
                       "blocked": sum(1 for s in sts if s.get("status") == "FAILED"),
                       "animated": sum(1 for s in sts if s.get("anim_status") == "READY")},
            "videos": [_video(res, v, used, base) for v in res.get("video_sheets") or [] if v.get("status") != "REJECTED"],
            "jobs": jobs_of.get(gidn, []), "created": res.get("created")}


def project_map(out: Path, gid: int, viewer=None) -> dict:
    """{project: {id, title, request}, current: "G###", batches: [{root, title, picked, sheets: [sheet…]}]}; `viewer(gid) -> bool` filters batches."""
    gid = int(gid)
    pl.read_result(out, gid)                                     # an unknown batch is the caller's 404 (PipelineError / FileNotFoundError)
    held = pl._held_by_particle_rows(out)
    ids = [g for g in pl.list_ids(out) if g not in held and (viewer is None or viewer(g))]
    if gid not in ids:
        ids.append(gid)
    fams = groups.families(out, ids)
    root = groups.root_of(out, gid)
    packs = pl._packs(out, fams)
    pid = next((p for p, roots in packs.items() if root in roots), root)
    from ..generation import jobs as _jobs
    jobs_of: dict[str, list] = {}
    for j in _jobs.list(out):
        if j.get("status") in ACTIVE_JOBS and j.get("generation"):
            jobs_of.setdefault(str(j["generation"]).upper(), []).append({"id": j["id"], "kind": j.get("kind"), "status": j["status"]})
    batches = []
    for r in packs.get(pid, [root]):
        try:
            rres = pl.read_result(out, r)
        except Exception:
            continue
        fam = groups.family(out, r)
        rprompt = rres.get("prompt") or (rres.get("source") or {}).get("subject", "")
        sheets = [s for s in (_sheet(out, m["id"], m["n"], rprompt, jobs_of) for m in fam["members"]) if s]
        batches.append({"root": f"G{r:03d}", "title": _title(rprompt), "picked": fam["picked"], "preset": (rres.get("slots") or {}).get("preset"), "sheets": sheets})
    try:
        first = pl.read_result(out, pid)
    except Exception:
        first = pl.read_result(out, gid)
    request = first.get("prompt") or (first.get("source") or {}).get("subject", "")
    return {"project": {"id": f"G{pid:03d}", "title": _title((first.get("source") or {}).get("subject") or request), "request": request},
            "current": f"G{gid:03d}", "batches": batches}
