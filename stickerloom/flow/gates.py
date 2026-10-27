"""The golden path's review gates (docs/architecture.md, "Engine"). The rules live here, in Python, and every phase keeps them:

  G1 plan -> sheet -> Python blocks bad cells -> G2 stills -> video sheet (built from the approved stills only) -> G3 video sheet
  -> returned video attached to A<n> -> Python blocks bad slots -> G4 animations -> G5 pack (stickers approved at G2 AND G4)

Python judges what is *correct* and nobody can approve a FAILED sticker as it stands. Telegram's own limits (format, size, codec) and a cell with no picture are final; every other BLOCK is a
judgement call that a human may "use anyway" with a recorded click that can be taken back (`allow_stills`, `allow_animations`, further down), after which the sticker is READY with the check kept
as a warning and the gate lets the person approve it. A human (from Phase 3 the VLM pre-reviews) judges what is *good*. Rejection never deletes. A sticker keeps its original S# through every
stage. Every decision is an event line and a history entry. The UI only displays what is enforced here (a 409 carries the reason)."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import pipeline as pl
from ..generation import prompter
from ..engine import verify
from ..engine.config import EngineConfig
from ..engine.video import check_returned_video, process_video, AnimCache
from ..engine.video_sheet import build_video_sheet

GATES = ("plan", "still", "video_sheet", "anim", "pack")
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
VIDEO_BLOCKS = ("video_decodes", "video_specs", "layout_match")      # a returned video that fails these is not the video of this sheet
ACTIVE = ("BUILT", "APPROVED", "VIDEO_RETURNED", "VIDEO_BLOCKED", "SLICED")
CUT = ("SLICED", "SUPERSEDED")        # a sheet whose video was sliced; SUPERSEDED = retired by "Regenerate video" (redo_video), kept with its files, never deleted


def refuse(msg: str):
    return pl.PipelineError(msg, 409)


def _stamp(decision: str, by: str, note=None, **extra) -> dict:
    return {"decision": decision, "by": by, "ts": round(time.time(), 3), "note": note, **extra}


def _stage(res: dict, stage: str) -> None:
    res["stage"] = stage


# ---------- approval-gate modes (STICKERLOOM_GATES=manual|auto|off) ----------
GATE_MODES = ("manual", "auto", "off")
GATES_ENV_VAR = "STICKERLOOM_GATES"
GATES_DEFAULT = "manual"

# Every BLOCK the verifier reports is either TECHNICAL (Telegram itself would reject the file: format, size,
# codec, dimensions, fps, duration, audio, alpha; or the check crashed) and final in every mode, or a JUDGEMENT
# (how the picture looks) that a human — or the auto gates — may allow with a recorded reason.
TECHNICAL_IDS = frozenset([c for ids in verify.TECHNICAL.values() for c in ids] + ["verifier_error"])


def _gates_file(out: Path) -> Path:
    return Path(out) / "gates.json"


def gates_mode(explicit=None, out=None) -> str:
    """The approval-gate policy: `explicit` wins, else STICKERLOOM_GATES, else the Studio switch
    (out/gates.json, POST /api/gates/setting), else manual. An unknown env/file value falls back to
    manual so a typo can never silently auto-approve; an unknown explicit value is refused."""
    if explicit is not None:
        mode = str(explicit).strip().lower()
        if mode not in GATE_MODES:
            raise pl.PipelineError(f"gates must be one of {', '.join(GATE_MODES)}")
        return mode
    raw = str(os.environ.get(GATES_ENV_VAR) or "").strip().lower()
    if raw:
        return raw if raw in GATE_MODES else GATES_DEFAULT
    if out is not None:
        try:
            stored = json.loads(_gates_file(out).read_text(encoding="utf-8"))
            if isinstance(stored, dict) and stored.get("mode") in GATE_MODES:
                return stored["mode"]
        except (OSError, ValueError):
            pass
    return GATES_DEFAULT


def set_gates_mode(out: Path, mode: str) -> str:
    """The Studio switch (read by every gates decision via gates_mode). Returns what was stored."""
    from ..runtime import atomic
    mode = gates_mode(explicit=mode)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    atomic.write_text(_gates_file(out), json.dumps({"mode": mode}))
    return mode


def _auto_for(by: str, out) -> bool:
    """Whether this APPROVE call resolves itself without a human click: headless drivers pass
    by="auto"; mode off never pauses, whatever the caller. Rejections always stay human."""
    return by == "auto" or gates_mode(out=out) == "off"


def is_technical_failure(c: dict) -> bool:
    """A stored check that fails for a TECHNICAL reason: final in every mode, never auto-overridden."""
    if c.get("ok", True) or c.get("severity") != verify.BLOCK:
        return False
    return c.get("name") in TECHNICAL_IDS or bool((c.get("data") or {}).get("error"))


def technical_blocks(report) -> list:
    """The TECHNICAL failures in a stored report (the names that stop the auto gates and the pack)."""
    return [c.get("name") for c in report or [] if is_technical_failure(c)]


def _waive_stored_report(report, names, metrics: dict) -> None:
    """Keep an allowed judgement block in the stored report as a warning (the verifier's waiver rule for
    already-cut pixels: no re-render, the same verdict the next cut would measure)."""
    allowed = verify.waived(names)
    for c in report or []:
        if (not c.get("ok") and c.get("severity") == verify.BLOCK and c.get("name") in allowed
                and not (c.get("data") or {}).get("error")):
            if "(allowed by you)" not in str(c.get("detail") or ""):
                c["detail"] = (str(c.get("detail") or "") + " (allowed by you)").strip()
            c["severity"] = verify.WARN
    waived = metrics.setdefault("waived", [])
    for n in sorted(set(names or ())):
        if n not in waived:
            waived.append(n)


def _auto_note(names) -> str:
    return "auto gates: allowed anyway: " + ", ".join(sorted(set(names)))


def _auto_stills(res: dict, decision: str, index, by: str) -> list[int]:
    """Auto mechanics for one stills APPROVE: verifier-passing stickers approve; judgement-blocked ones
    are auto-overridden with a logged reason; anything else (TECHNICAL, an empty cell, a sheet-level
    stop) stops the gate instead of approving."""
    if index in ("ready", "all"):
        todo = [s for s in res["stickers"]
                if s.get("status") in ("READY", "FAILED") and s.get("review", {}).get("still") in ("PENDING", "BLOCKED")]
    else:
        try:
            i = int(index)
        except (TypeError, ValueError):
            raise pl.PipelineError("index must be a sticker number or 'ready'")
        if not 1 <= i <= len(res["stickers"]):
            raise pl.PipelineError(f"index 1..{len(res['stickers'])} required")
        todo = [res["stickers"][i - 1]]
    for s in todo:
        if s.get("status") == "FAILED":
            why = still_problem(s)
            if why is not None:
                raise refuse(f"S{s['index']} stops the auto gates ({why})")
            names = _blocks(s.get("report"))
            s["still_override"] = sorted(set(s.get("still_override") or ()) | set(names))
            _waive_stored_report(s.get("report"), names, s.setdefault("metrics", {}))
            s["status"] = "READY"
            s.setdefault("review", {})["still"] = "PENDING"
            pl.hist(s, "still", by, "APPROVE", _auto_note(names))
    done = []
    for s in todo:
        if s.get("status") == "READY" and s.get("review", {}).get("still") == "PENDING":
            s["review"]["still"] = "APPROVED"
            pl.hist(s, "still", by, decision)
            done.append(s["index"])
    return done


def _auto_anims(res: dict, decision: str, index, by: str) -> list[int]:
    """Auto mechanics for one animations APPROVE: same contract as _auto_stills, rebuilding the verdict
    of an overridable-blocked animation from its finished clip (no decode, no encode)."""
    if index in ("ready", "all"):
        todo = [s["index"] for s in res["stickers"]
                if s.get("anim_status") in ("READY", "FAILED") and s.get("review", {}).get("anim") in ("PENDING", "BLOCKED")]
    else:
        try:
            i = int(index)
        except (TypeError, ValueError):
            raise pl.PipelineError("index must be a sticker number or 'ready'")
        if not 1 <= i <= len(res["stickers"]):
            raise pl.PipelineError(f"index 1..{len(res['stickers'])} required")
        todo = [i]
    for i in todo:
        s = res["stickers"][i - 1]
        if s.get("anim_status") == "FAILED":
            why = anim_problem(res, s)
            if why is not None:
                raise refuse(f"S{i} stops the auto gates ({why})")
            names = _blocks(s.get("anim_report"))
            s["anim_override"] = sorted(set(s.get("anim_override") or ()) | set(names))
            if not _materialize_anim_verdict(s, s["anim_override"]) or not _restore_anim_verdict(s):
                raise refuse(f"S{i} stops the auto gates (its animation was stopped before the encode: no finished clip to allow).")
            pl.hist(s, "video", by, "APPROVE", _auto_note(names))
    done = []
    for i in todo:
        s = res["stickers"][i - 1]
        if s.get("anim_status") == "READY" and s.get("review", {}).get("anim") in ("PENDING", "BLOCKED"):
            if s["review"]["anim"] == "BLOCKED" and not soft_block(s):
                raise refuse(f"S{i} is blocked by Python: nobody can approve it as it stands.")
            s["review"]["anim"] = "APPROVED"
            pl.hist(s, "anim", by, decision)
            done.append(i)
    return done


def _auto_sheet(res: dict, v: dict, by: str) -> None:
    """Auto mechanics for one video-sheet APPROVE: a sheet blocked only by judgement calls is
    auto-allowed with a logged reason; a TECHNICAL block or a verifier crash stops the gate."""
    checks = (v.get("verify") or []) + (v.get("video_checks") or [])
    failed = _blocks(checks)
    if not failed:
        return
    if _crashed(checks):
        raise refuse(f"{v['id']} stops the auto gates (the verifier crashed; this is not a judgement call).")
    hard = [k for k in failed if k not in verify.OVERRIDABLE["video_sheet"]]
    if hard:
        raise refuse(f"{v['id']} stops the auto gates ({', '.join(sorted(set(hard)))}: the file must decode and the approved slots must match).")
    names = sorted(set(failed))
    v["sheet_override"] = sorted(set(v.get("sheet_override") or ()) | set(names))
    v["verify"] = _waive_sheet_checks(v.get("verify") or [], v["sheet_override"])
    v["video_checks"] = _waive_sheet_checks(v.get("video_checks") or [], v["sheet_override"])
    v["blocked"] = next(iter(_blocks(v["verify"])), None)
    v["block"] = next(iter(_blocks(v["video_checks"])), None)
    v.setdefault("history", []).append({"ts": round(time.time(), 3), "actor": by, "action": "ALLOW", "checks": names})
    for i in v["slots"]:
        pl.hist(res["stickers"][i - 1], "video_sheet", by, "APPROVE", _auto_note(names), v["id"], {"override": names})


def _pack_technical_stop(res: dict, final: list[int]) -> str | None:
    """A TECHNICAL failure anywhere in a final sticker's stored checks: the pack never ships, in any mode."""
    hits = []
    for i in final:
        st = res["stickers"][i - 1]
        for c in (st.get("report") or []) + (st.get("anim_report") or []):
            if is_technical_failure(c):
                hits.append(f"S{i}/{c.get('name')}")
    if hits:
        return (f"pack_limits: TECHNICAL failure in the final stickers ({', '.join(sorted(set(hits)))}): "
                "the pack cannot ship in any gates mode.")
    return None


def active_sheet(res: dict):
    """The video sheet the stills decisions are locked to: the latest one that was not rejected."""
    return next((v for v in reversed(res["video_sheets"]) if v["status"] in ACTIVE), None)


def cut_sheet(res: dict, index=None):
    """The video sheet the stickers' animations are cut from now: the latest one with a sliced video (holding `index` when given). A sheet retired by
    redo_video stays the source until the new sheet's video is sliced, so the stickers never lose their animation while the new video is made."""
    has = [v for v in reversed(res.get("video_sheets") or []) if v.get("video") and (index is None or int(index) in v["slots"])]
    return next((v for v in has if v.get("status") == "SLICED"), None) or next((v for v in has if v.get("status") == "SUPERSEDED"), None)     # the picked one first


def prepared_source(res: dict) -> bool:
    """The batch has a prepared source to cut animations from again: a 3x3 video of the image sheet (an imported one too) or pre-sliced clips."""
    src = res.get("source") or {}
    return bool(src.get("has_video") and (src.get("video_path") or src.get("clips")))


def can_reslice(res: dict) -> bool:
    """reslice has something to cut again: a sliced video sheet, else a prepared source with animations made from it."""
    return bool(cut_sheet(res)) or (prepared_source(res) and any(st.get("webm") for st in res["stickers"]))


def sheet_of(res: dict, aid: str) -> dict:
    v = next((v for v in res["video_sheets"] if v["id"] == aid), None)
    if not v:
        raise pl.PipelineError(f"No video sheet {aid}", 404)
    return v


def final_indices(res: dict) -> list[int]:
    return [s["index"] for s in res["stickers"] if s["review"]["still"] == "APPROVED" and s["review"]["anim"] == "APPROVED"]


def _plan_ok(res: dict) -> None:
    p = res["reviews"].get("plan")
    if not p:
        raise refuse("Approve the plan first (G1).")
    if p["decision"] != "APPROVE":
        raise refuse("The plan was rejected (G1): nothing can move on from it.")


def gate_info(res: dict) -> dict:
    """What the page should ask for next (computed, never stored): the active gate and a one-line instruction."""
    S = res["stickers"]
    if pl.STAGES.index(res["stage"]) < pl.SLICED:
        return {"active": "wait", "message": "The stills are being made."}
    p = res["reviews"].get("plan")
    if not p:
        return {"active": "plan", "message": "G1: approve or reject the plan."}
    if p["decision"] != "APPROVE":
        return {"active": "rejected", "message": "The plan was rejected."}
    pend = [s["index"] for s in S if s["status"] == "READY" and s["review"]["still"] == "PENDING"]
    sheet = active_sheet(res)
    animated = any(s["anim_status"] in ("READY", "FAILED") for s in S)       # animated from the prepared video: no video sheet involved
    if pend and not sheet and not animated:
        return {"active": "still", "message": f"G2: approve or reject the stills ({len(pend)} pending).", "pending": pend}
    if not sheet and not animated:
        return {"active": "video_sheet_build", "message": "Build the video sheet from the approved stills."}
    st = sheet["status"] if sheet else "SLICED"
    if st == "BUILT":
        return {"active": "video_sheet", "message": f"G3: approve or reject video sheet {sheet['id']}.", "sheet": sheet["id"]}
    if st in ("APPROVED", "VIDEO_BLOCKED"):
        return {"active": "video_upload", "message": f"Make the video from {sheet['id']} in your own tool, then upload it.", "sheet": sheet["id"],
                "blocked": sheet.get("block")}
    if st == "VIDEO_RETURNED":
        return {"active": "wait", "message": "Slicing the returned video."}
    apend = [s["index"] for s in S if s["anim_status"] == "READY" and s["review"]["anim"] == "PENDING"]
    if apend:
        return {"active": "anim", "message": f"G4: approve or reject the animations ({len(apend)} pending).", "pending": apend}
    if not res["reviews"].get("pack"):
        return {"active": "pack", "message": "G5: approve the final pack.", "final": final_indices(res)}
    return {"active": "done", "message": "The pack is final.", "final": final_indices(res)}


# ---------- reviews ----------
@pl.serialized
def review(out: Path, gid: int, gate: str, decision: str, index=None, note=None, by: str = "human") -> dict:
    decision = str(decision).upper()
    if gate not in GATES:
        raise pl.PipelineError(f"gate must be one of {', '.join(GATES)}")
    if decision not in ("APPROVE", "REJECT"):
        raise pl.PipelineError("decision must be APPROVE or REJECT")
    res = pl.read_result(out, gid)
    fn = {"plan": _g_plan, "still": _g_still, "video_sheet": _g_sheet, "anim": _g_anim, "pack": _g_pack}[gate]
    msg = fn(out, gid, res, decision, index, note, by)
    pl.write_result(out, gid, res)
    return {"ok": True, "gate": gate, "decision": decision, **msg, "gate_info": gate_info(res)}


def _g_plan(out, gid, res, decision, index, note, by):
    if any(s["review"]["still"] in ("APPROVED", "REJECTED") for s in res["stickers"]) or active_sheet(res):
        raise refuse("Locked: stills were already reviewed from this plan.")
    res["reviews"]["plan"] = _stamp(decision, by, note)
    if pl.STAGES.index(res["stage"]) >= pl.SLICED:
        _stage(res, "plan_reviewed")
    pl.emit(out, gid, "plan_reviewed", "done", 0, {"gate": "plan", "note": note}, by, decision)
    return {}


def _targets(res: dict, index, decision: str, which: str, ready_field: str, ready_value: str):
    """The stickers a per-sticker gate acts on: one index, or every still-pending READY sticker for index 'ready'."""
    S = res["stickers"]
    if index in ("ready", "all"):
        return [s for s in S if s[ready_field] == ready_value and s["review"][which] == "PENDING"]
    try:
        i = int(index)
    except (TypeError, ValueError):
        raise pl.PipelineError("index must be a sticker number or 'ready'")
    if not 1 <= i <= len(S):
        raise pl.PipelineError(f"index 1..{len(S)} required")
    s = S[i - 1]
    if s[ready_field] != ready_value or s["review"][which] == "BLOCKED":
        if not (which == "anim" and s[ready_field] == ready_value and soft_block(s)):
            why = s["reason"] if which == "still" else s.get("anim_reason")
            may = (still_problem(s) if which == "still" else anim_problem(res, s)) is None
            raise refuse(f"S{i} is blocked by Python ({why or s[ready_field].lower()}): nobody can approve or reject it as it stands."
                         + (" It is a judgement call: \u201cUse it anyway\u201d allows it (POST /allow)." if may else ""))
    return [s]


def soft_block(s: dict) -> bool:
    """An animation that WAS made and failed only WARN-level checks (the character leaves its cell). It is switched off by default
    (review.anim BLOCKED), but Python's verdict on it is a warning, not a block, so the human may include it anyway."""
    failed = [c for c in s.get("anim_report") or [] if not c.get("ok")]
    return s.get("anim_status") == "READY" and bool(failed) and all(c.get("severity") == "WARN" for c in failed)


def _g_still(out, gid, res, decision, index, note, by):
    _plan_ok(res)
    if pl.STAGES.index(res["stage"]) < pl.SLICED or res.get("error"):
        raise refuse("The stills are not sliced yet.")
    sh = active_sheet(res)
    if sh:
        raise refuse(f"Locked: video sheet {sh['id']} was built from these decisions. Reject {sh['id']} to change them.")
    done = []
    if decision == "APPROVE" and _auto_for(by, out):
        done = _auto_stills(res, decision, index, by)
        for i in done:
            pl.emit(out, gid, "review", "done", 0, {"gate": "still", "index": i, "note": note}, by, decision)
    else:
        for s in _targets(res, index, decision, "still", "status", "READY"):
            s["review"]["still"] = "APPROVED" if decision == "APPROVE" else "REJECTED"
            pl.hist(s, "still", by, decision, reason=note)
            pl.emit(out, gid, "review", "done", 0, {"gate": "still", "index": s["index"], "note": note}, by, decision)
            done.append(s["index"])
    if not any(s["status"] == "READY" and s["review"]["still"] == "PENDING" for s in res["stickers"]):
        _stage(res, "stills_reviewed")
        pl.emit(out, gid, "stills_reviewed", "done", 0, {"approved": [s["index"] for s in res["stickers"] if s["review"]["still"] == "APPROVED"]}, by, decision)
    return {"indices": done}


def _g_anim(out, gid, res, decision, index, note, by):
    sh = active_sheet(res)
    if decision == "APPROVE" and sh and (sh.get("blocked") or sh.get("block")):
        raise refuse(f"Video sheet {sh['id']} is blocked. Use it anyway or correct it before approving animations.")
    if not any(s["anim_status"] in ("READY", "FAILED") for s in res["stickers"]):
        raise refuse("The video has not been sliced yet.")
    if res["reviews"].get("pack") and res["reviews"]["pack"]["decision"] == "APPROVE":
        raise refuse("Locked: the pack is already final (G5). Reject the pack to change animation decisions.")
    done = []
    if decision == "APPROVE" and _auto_for(by, out):
        done = _auto_anims(res, decision, index, by)
        for i in done:
            pl.emit(out, gid, "review", "done", 0, {"gate": "anim", "index": i, "note": note}, by, decision)
    else:
        for s in _targets(res, index, decision, "anim", "anim_status", "READY"):
            override = s["review"]["anim"] == "BLOCKED" and decision == "APPROVE"
            s["review"]["anim"] = "APPROVED" if decision == "APPROVE" else "REJECTED"
            pl.hist(s, "anim", by, decision, reason="included anyway: " + ", ".join(c["name"] for c in s["anim_report"] if not c.get("ok")) if override else note)
            pl.emit(out, gid, "review", "done", 0, {"gate": "anim", "index": s["index"], "note": note}, by, decision)
            done.append(s["index"])
    if not any(s["anim_status"] == "READY" and s["review"]["anim"] == "PENDING" for s in res["stickers"]):
        _stage(res, "anim_reviewed")
        pl.emit(out, gid, "anim_reviewed", "done", 0, {"approved": [s["index"] for s in res["stickers"] if s["review"]["anim"] == "APPROVED"]}, by, decision)
    return {"indices": done}


def _g_sheet(out, gid, res, decision, index, note, by):
    v = sheet_of(res, str(index or ""))
    if v["status"] not in ("BUILT", "APPROVED", "REJECTED"):
        raise refuse(f"Locked: {v['id']} already has a video attached.")
    if decision == "APPROVE" and v.get("blocked"):
        if _auto_for(by, out):
            _auto_sheet(res, v, by)
        if v.get("blocked"):
            raise refuse(f"{v['id']} is blocked by Python ({v['blocked']}): it cannot be approved.")
    v["status"] = "APPROVED" if decision == "APPROVE" else "REJECTED"
    res["reviews"]["video_sheet"][v["id"]] = _stamp(decision, by, note)
    for slot in v["slots"]:
        pl.hist(res["stickers"][slot - 1], "video_sheet", by, decision, reason=note, ref=v["id"])
    _stage(res, "video_sheet_reviewed")
    pl.emit(out, gid, "video_sheet_reviewed", "done", 0, {"sheet": v["id"], "note": note}, by, decision)
    return {"sheet": v["id"], "status": v["status"]}


def _g_pack(out, gid, res, decision, index, note, by):
    sh = active_sheet(res)
    if decision == "APPROVE" and sh and (sh.get("blocked") or sh.get("block")):
        raise refuse(f"Video sheet {sh['id']} is blocked. Use it anyway or correct it before approving the pack.")
    if not any(s["anim_status"] in ("READY", "FAILED") for s in res["stickers"]):
        raise refuse("The video has not been sliced yet.")
    undecided = [s["index"] for s in res["stickers"] if s["anim_status"] == "READY" and s["review"]["anim"] == "PENDING"]
    if decision == "APPROVE":
        if undecided:
            raise refuse(f"Decide the animations first (G4): S{', S'.join(map(str, undecided))} still pending.")
        final = final_indices(res)
        if not final:
            raise refuse("No sticker is approved at both G2 and G4.")
        stop = _pack_technical_stop(res, final)
        if stop:
            raise refuse(stop)
        d = pl.gen_dir(out, gid)
        items = [{"key": res["stickers"][i - 1]["key"], "emoji": res["stickers"][i - 1]["emoji"], "kind": "animated",
                  "bytes": (d / res["stickers"][i - 1]["webm"]).stat().st_size} for i in final]
        chk = verify.run("pack", {"stickers": items}, EngineConfig())[0]
        if not chk.ok:
            raise refuse(f"pack_limits: {chk.note}")
        res["reviews"]["pack"] = _stamp("APPROVE", by, note, stickers=final)
        for i in final:
            pl.hist(res["stickers"][i - 1], "pack", by, "APPROVE", reason=note)
        _stage(res, "pack_final")
        pl.emit(out, gid, "pack_final", "done", 0, {"stickers": final}, by, "APPROVE")
        return {"final": final}
    res["reviews"]["pack"] = _stamp("REJECT", by, note, stickers=[])
    pl.emit(out, gid, "pack_final", "done", 0, {"note": note}, by, "REJECT")
    return {"final": []}


# ---------- G3: the video sheet ----------
def _read_rgba(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGBA"))


def build_sheet(out: Path, gid: int, cfg: EngineConfig) -> dict:
    """Build video sheet A<n> from the stills approved at G2: same slot positions, rejected slots blank, no outline, flat key colour."""
    res = pl.read_result(out, gid)
    cfg = pl.cfg_for(res, cfg)                       # a blue-screen batch gets a blue video sheet
    _plan_ok(res)
    if pl.STAGES.index(res["stage"]) < pl.SLICED or res.get("error"):
        raise refuse("The stills are not sliced yet.")
    if active_sheet(res):
        raise refuse(f"Video sheet {active_sheet(res)['id']} is already built. Reject it to build another.")
    undecided = [s["index"] for s in res["stickers"] if s["status"] == "READY" and s["review"]["still"] == "PENDING"]
    if undecided:
        raise refuse(f"Decide the stills first (G2): S{', S'.join(map(str, undecided))} still pending.")
    approved = [s["index"] for s in res["stickers"] if s["review"]["still"] == "APPROVED"]
    if not approved:
        raise refuse("No still is approved yet (G2): there is nothing to put on a video sheet.")
    d = pl.gen_dir(out, gid)
    rows, cols = res["grid"]
    plain = {i: _read_rgba(d / "source" / "plain" / f"S{i}.png") for i in approved}
    t0 = time.perf_counter()
    sheet, layout = build_video_sheet(plain, approved, cfg, (rows, cols))
    aid = f"A{len(res['video_sheets']) + 1}"
    folder = d / "video_sheet" / aid
    folder.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    (folder / "sheet.png").write_bytes(buf.tobytes())
    (folder / "layout.json").write_text(json.dumps(layout, indent=1), encoding="utf-8")
    checks = verify.run("video_sheet", {"sheet": sheet, "layout": layout, "approved": approved}, cfg)
    blocked = next((c for c in checks if not c.ok and c.severity == verify.BLOCK), None)
    entry = {"id": aid, "slots": approved, "grid": [rows, cols], "file": f"video_sheet/{aid}/sheet.png", "layout": f"video_sheet/{aid}/layout.json",
             "video": None, "status": "BUILT", "ts": round(time.time(), 3), "verify": [c.to_dict() for c in checks],
             "blocked": blocked.id if blocked else None, "canvas": layout["canvas"], "video_prompt": res.get("video_prompt", ""),
             "slot_fill": cfg.slot_fill}
    res["video_sheets"].append(entry)
    for i in approved:
        pl.hist(res["stickers"][i - 1], "video_sheet", "python", "BLOCK" if blocked else "PASS", blocked.id if blocked else None, aid)
    _stage(res, "video_sheet_built")
    pl.write_result(out, gid, res)
    pl.emit(out, gid, "video_sheet_built", "error" if blocked else "done", int((time.perf_counter() - t0) * 1000),
            {"sheet": aid, "slots": approved, "blocked": blocked.id if blocked else None}, "python", "BLOCK" if blocked else "PASS")
    if blocked:
        raise refuse(f"{aid} was built but Python blocked it ({blocked.id}: {blocked.note}).")
    return {"sheet": aid, "slots": approved, "gate_info": gate_info(res)}


# ---------- 7-8: the returned video ----------
def _make_preview(src: Path) -> str | None:
    """A light copy (720 px wide at most, no audio) for the browser; the full-size original stays the engine's source. Optional: None on any failure."""
    import subprocess
    dest = src.with_name("preview.mp4")
    try:
        from ..engine import ffmpeg as ff
        exe = ff.ffmpeg_exe()
        enc = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
        codec = ["-c:v", "libx264", "-crf", "28", "-preset", "veryfast"] if "libx264" in enc else ["-c:v", "mpeg4", "-qscale:v", "6"]
        r = subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale='min(720,iw)':-2", "-an", *codec, "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", str(dest)], capture_output=True, timeout=120)
        return dest.name if r.returncode == 0 and dest.is_file() else None
    except Exception:
        return None


def preview_sheet(out: Path, gid: int, cfg: EngineConfig, fill: float, px: int = 420) -> bytes:
    """The video sheet the kept stickers would make at this fill, as a small PNG, built on the fly and not stored: the Studio shows it while the gap slider moves."""
    from dataclasses import replace
    res = pl.read_result(out, gid)
    cfg = pl.cfg_for(res, cfg)
    kept = [s["index"] for s in res["stickers"] if s["status"] == "READY" and s["review"]["still"] != "REJECTED"]
    if not kept:
        raise refuse("There are no kept stickers to put on a video sheet.")
    d = pl.gen_dir(out, gid)
    missing = [i for i in kept if not (d / "source" / "plain" / f"S{i}.png").is_file()]
    if missing:        # a batch cut before the pictures were kept (or rejected before the blue-screen logic): say so in words, never a 500
        raise refuse(f"No stored picture for {', '.join(f'S{i}' for i in missing)} in G{gid:03d}, so the video sheet cannot be previewed. "
                     "The batch was cut before its pictures were kept. Drop those stickers from the set, or make the batch again.")
    plain = {i: _read_rgba(d / "source" / "plain" / f"S{i}.png") for i in kept}
    sheet, _ = build_video_sheet(plain, kept, replace(cfg, slot_fill=float(fill)), tuple(res["grid"]))
    scale = px / max(sheet.shape[:2])
    small = cv2.resize(sheet, (max(1, round(sheet.shape[1] * scale)), max(1, round(sheet.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    return buf.tobytes()


# ---------- "Use it anyway": a human allows what Python blocked as a judgement call ----------
# any rejected image or video must give the person an option to allow it. Every BLOCK is either TECHNICAL (Telegram itself would reject the file: format, size, codec,
# dimensions, fps, duration, audio, alpha; or the check crashed) and final, or a JUDGEMENT (how the picture looks) and a recorded click may allow it (engine/verify.py `OVERRIDABLE`). A cell with NO
# picture (nothing was cut) can never be allowed. The click is stored on the sticker (`still_override` / `anim_override`: the check ids), in its history (actor human), and the verifier honours it
# on every later cut (verify.run `waive`: the block stays in the report as a warning "allowed by you"). Taking it back restores the block. Sheet-level problems have "Cut it anyway" (pipeline.recut).
KINDS = ("still", "animation")
OVERRIDABLE = verify.OVERRIDABLE["animation"]            # slot geometry of a returned video (inside_slot, cross_slot) and a loop that does not close
OVERRIDABLE_STILL = verify.OVERRIDABLE["still"]


def _blocks(report) -> list[str]:
    return [c["name"] for c in report or [] if not c.get("ok") and c.get("severity") == "BLOCK"]


def _crashed(report) -> bool:
    return any(not c.get("ok") and c.get("severity") == "BLOCK" and (c.get("data") or {}).get("error") for c in report or [])


def _technical(bad: list) -> str:
    return f"This one failed a technical check ({', '.join(bad)}: format, size or codec) and cannot be allowed."


def still_problem(st: dict) -> str | None:
    """None when a human may allow this blocked still; else, in plain words, why not."""
    if st.get("status") != "FAILED":
        return "This sticker was not blocked, so there is nothing to allow."
    m = st.get("metrics") or {}
    if m.get("sheet_blocked"):
        return "The whole sheet was stopped, not this one sticker: use \u201cCut it anyway\u201d on the sheet."
    rep = st.get("report") or []
    failed = _blocks(rep)
    bad = [f for f in failed if f not in OVERRIDABLE_STILL] + (["verifier_error"] if _crashed(rep) else [])
    if bad:
        return _technical(sorted(set(bad)))
    if not failed:
        return "Python recorded no check for this block, so there is nothing to allow."
    if not m.get("bbox") or not m.get("fg_px"):
        return "Nothing was cut from this cell (it is empty), so there is no picture to use."
    return None


def anim_source(res: dict, index: int):
    """Where an animation was cut from: ('sheet', video sheet id) for a returned video, ('prepared', None) for a prepared 3x3 video or pre-sliced clips, None when it has no source."""
    st = res["stickers"][int(index) - 1]
    how = str((st.get("anim_metrics") or {}).get("source") or "")
    v = cut_sheet(res, index)
    if v and how in ("", "video sheet"):
        return "sheet", v["id"]
    src = res.get("source") or {}
    if how in ("3x3 mp4", "") or how.startswith("clip:"):
        if src.get("video_path") or str(int(index)) in (src.get("clips") or {}):
            return "prepared", None
    return None


def anim_problem(res: dict, st: dict) -> str | None:
    """None when a human may allow this blocked animation; else, in plain words, why not."""
    if st.get("anim_status") != "FAILED":
        return "This animation was not blocked, so there is nothing to allow."
    if not anim_source(res, st["index"]):
        return "Only an animation cut from a returned or a prepared video can be allowed."
    rep = st.get("anim_report") or []
    failed = _blocks(rep)
    if not failed:
        return f"Nothing usable was cut for this sticker ({st.get('anim_reason') or 'no reason recorded'}), so there is nothing to allow."
    bad = [f for f in failed if f not in OVERRIDABLE] + (["verifier_error"] if _crashed(rep) else [])
    if bad:
        return _technical(sorted(set(bad)))
    return None


def allow_problem(res: dict, st: dict, kind: str = "animation", allow: bool = True) -> str | None:
    """Why this sticker cannot be allowed (allow=True) or have its permission taken back (allow=False) right now, in plain words; None when it can."""
    if kind == "still":
        sh = active_sheet(res)
        if sh:
            return f"Locked: video sheet {sh['id']} was built from these decisions. Reject {sh['id']} to change them."
        if allow:
            return still_problem(st)
        if not st.get("still_override"):
            return "Nothing was allowed for this sticker."
        if st.get("edited"):
            return "This sticker was edited after it was allowed: taking the permission back would lose the edit."
        return None
    if allow:
        return anim_problem(res, st)
    if not st.get("anim_override"):
        return "Nothing was allowed for this sticker."
    if not anim_source(res, st["index"]):
        return "Only an animation cut from a returned or a prepared video can be changed."
    return None


def check_allow(out: Path, gid: int, index: int, allow: bool = True, kind: str = "animation") -> tuple[dict, str | None]:
    """Validate a human override on one sticker and return (the sticker, the video sheet id of its animation or None). Raises 409 with the reason when it cannot be allowed."""
    if kind not in KINDS:
        raise pl.PipelineError("kind must be 'still' or 'animation'")
    res = pl.read_result(out, gid)
    if not 1 <= int(index) <= len(res["stickers"]):
        raise pl.PipelineError(f"index 1..{len(res['stickers'])} required")
    st = res["stickers"][int(index) - 1]
    why = allow_problem(res, st, kind, allow)
    if why:
        raise refuse(why)
    src = anim_source(res, index) if kind == "animation" else None
    return st, (src[1] if src else None)


def allowable(res: dict, allow: bool = True, kind: str = "animation") -> list[int]:
    """The stickers a human can allow right now (allow=True) or take the permission back from (allow=False)."""
    if kind == "video_sheet":
        return [v["id"] for v in res["video_sheets"] if sheet_problem(res, v, allow) is None]
    return [st["index"] for st in res["stickers"] if allow_problem(res, st, kind, allow) is None]


def sheet_problem(res: dict, v: dict, allow: bool = True) -> str | None:
    if v["status"] == "REJECTED":
        return "This video sheet was rejected. Build or select a current sheet first."
    if (res["reviews"].get("pack") or {}).get("decision") == "APPROVE":
        return "The pack is final. Reject G5 before changing its video sheet."
    if not allow:
        return None if v.get("sheet_override") else "Nothing was allowed on this video sheet."
    checks = (v.get("verify") or []) + (v.get("video_checks") or [])
    failed = _blocks(checks)
    if _crashed(checks):
        return "The verifier crashed; this is not a judgement call."
    hard = [k for k in failed if k not in verify.OVERRIDABLE["video_sheet"]]
    if hard:
        return f"This sheet cannot be allowed ({', '.join(hard)}). The file must decode and the approved slots must match."
    return None if failed else "This video sheet has no blocked judgement call to allow."


def _waive_sheet_checks(checks: list, names) -> list:
    """Use the verifier's existing waiver, retaining failed measurements as WARNs."""
    allowed = verify.waived(names) & set(verify.OVERRIDABLE["video_sheet"])
    out = []
    for c in checks:
        item = verify.Check(c["name"], c["stage"], c["severity"], c["ok"], c.get("value"), c.get("limit"),
                            c.get("data") or {}, c.get("detail") or "",
                            "verifier_error" if (c.get("data") or {}).get("error") else None)
        if not item.ok and item.id in verify.OVERRIDABLE["video_sheet"]:
            from dataclasses import replace
            item = replace(item, severity=verify.BLOCK, note=item.note.removesuffix(" (allowed by you)"))
        out.append(verify._apply_waiver(item, allowed, {}).to_dict())
    return out


@pl.serialized
def allow_sheet(out: Path, gid: int, indexes: list, allow: bool, cfg: EngineConfig, pace: float = 0.0) -> None:
    """Recorded human permission on G3 and returned-video judgement calls; never requests a new video."""
    res = pl.read_result(out, gid)
    todo = [sheet_of(res, str(aid)) for aid in sorted(set(indexes))]
    for v in todo:
        why = sheet_problem(res, v, allow)
        if why:
            raise refuse(why)
    reslice = []
    for v in todo:
        names = sorted(set(_blocks((v.get("verify") or []) + (v.get("video_checks") or [])))) if allow else v["sheet_override"]
        v["sheet_override"] = sorted(set(v.get("sheet_override") or []) | set(names)) if allow else []
        for field in ("verify", "video_checks"):
            v[field] = _waive_sheet_checks(v.get(field) or [], v["sheet_override"])
        v["blocked"] = next(iter(_blocks(v["verify"])), None)
        v["block"] = next(iter(_blocks(v["video_checks"])), None)
        v.setdefault("history", []).append({"ts": round(time.time(), 3), "actor": "human", "action": "ALLOW" if allow else "UNALLOW", "checks": names})
        for i in v["slots"]:
            pl.hist(res["stickers"][i - 1], "video_sheet", "human", "APPROVE" if allow else "REJECT",
                    ("allowed anyway: " if allow else "allowance withdrawn: ") + ", ".join(names), v["id"], {"override": names})
        if v["block"] or v["blocked"]:
            if v.get("video"):
                v["status"] = "VIDEO_BLOCKED"
            elif v["status"] == "APPROVED":
                v["status"] = "BUILT"
        elif allow and v.get("video") and v["status"] == "VIDEO_BLOCKED":
            reslice.append(v["id"])
        pl.emit(out, gid, "video_sheet_allowed" if allow else "video_sheet_allow_withdrawn", "done", 0,
                {"sheet": v["id"], "checks": names}, "human", "APPROVE" if allow else "REJECT")
    pl.write_result(out, gid, res)
    for aid in reslice:
        slice_video(out, gid, aid, cfg, pace)


def allow_info(res: dict) -> dict:
    """What the page needs to draw 'Use it anyway' (computed, never stored; `state()['allow']`): per kind ('still', 'animation'), `can` = indexes a person may allow now, `allowed` = indexes that carry
    a permission, `undo` = the allowed ones whose permission can be taken back now, `why` = {index: the plain-words reason of every blocked sticker}, `final` = {index: why it cannot be allowed}."""
    from . import explain
    out = {}
    for kind in KINDS:
        key = "still_override" if kind == "still" else "anim_override"
        stat = "status" if kind == "still" else "anim_status"
        rep = "report" if kind == "still" else "anim_report"
        info = {"can": [], "allowed": [], "undo": [], "why": {}, "final": {}}
        for st in res["stickers"]:
            i = st["index"]
            if st.get(key):
                info["allowed"].append(i)
                if allow_problem(res, st, kind, False) is None:
                    info["undo"].append(i)
            if st.get(stat) != "FAILED":
                continue
            first = (_blocks(st.get(rep)) or [None])[0] or st.get("reason" if kind == "still" else "anim_reason")
            info["why"][str(i)] = explain.block_words(first)
            problem = allow_problem(res, st, kind, True)
            if problem is None:
                info["can"].append(i)
            else:
                info["final"][str(i)] = problem
        out[kind] = info
    info = {"can": [], "allowed": [], "undo": [], "why": {}, "final": {}}
    from . import explain
    for v in res["video_sheets"]:
        if v["status"] == "REJECTED":
            continue
        aid = v["id"]
        if v.get("sheet_override"):
            info["allowed"].append(aid)
            if sheet_problem(res, v, False) is None:
                info["undo"].append(aid)
        bad = _blocks((v.get("verify") or []) + (v.get("video_checks") or []))
        if bad:
            info["why"][aid] = explain.block_words(bad[0])
            problem = sheet_problem(res, v)
            if problem is None:
                info["can"].append(aid)
            else:
                info["final"][aid] = problem
    out["video_sheet"] = info
    return out


_ANIM_FINISHED_FIELDS = ("anim_status", "anim_reason", "anim_metrics", "anim_report", "webm")


def _anim_verdict_key(names) -> str:
    """Stable key for one finished verifier state; the same overrides always mean the same verdict for this cut."""
    return ",".join(sorted(names or ())) or "plain"


def _remember_anim_verdict(st: dict) -> bool:
    """Keep the finished state record_anim wrote, without history (a later click must remain in history)."""
    if st.get("anim_status") not in ("READY", "FAILED") or not st.get("webm") or not st.get("anim_report"):
        return False
    state = {k: copy.deepcopy(st.get(k)) for k in _ANIM_FINISHED_FIELDS}
    state["review"] = st.get("review", {}).get("anim")
    st.setdefault("anim_finished", {})[_anim_verdict_key(st.get("anim_override"))] = state
    return True


def _restore_anim_verdict(st: dict) -> bool:
    """Swap to an already-finished verdict of this exact cut. No decoder, verifier or encoder runs."""
    state = (st.get("anim_finished") or {}).get(_anim_verdict_key(st.get("anim_override")))
    if not state:
        return False
    for k in _ANIM_FINISHED_FIELDS:
        st[k] = copy.deepcopy(state.get(k))
    st.setdefault("review", {})["anim"] = state.get("review")
    return True


def _materialize_anim_verdict(st: dict, overrides) -> bool:
    """Build the requested verdict from the finished clip's stored checks, using the verifier's one waiver catalogue.

    Some slot failures happen before encoding, so the first allow still has to render. Once either verdict has a
    finished clip, changing only the override can materialise its opposite without decoding or encoding again.
    """
    if not st.get("webm") or not st.get("anim_report"):
        return False
    key = _anim_verdict_key(overrides)
    if key in (st.get("anim_finished") or {}):
        return True
    checks = st["anim_report"]
    if not any(c.get("stage") == "anim" for c in checks):
        # A slot pre-check can stop before encoding. Its short report cannot
        # waive away an unseen later failure on the clip we still hold.
        checks = next((s["anim_report"] for s in (st.get("anim_finished") or {}).values()
                       if s.get("webm") == st["webm"] and
                       any(c.get("stage") == "anim" for c in s.get("anim_report") or [])), None)
        if checks is None:
            return False
    metrics = copy.deepcopy(st.get("anim_metrics") or {})
    report = verify.animation_verdict(copy.deepcopy(checks), overrides, metrics)
    oob = report.get("inside_frame")
    state = {
        "anim_status": "READY" if report.ok else "FAILED",
        "anim_reason": report.first_failure,
        "anim_metrics": metrics,
        "anim_report": report.checks,
        "webm": st["webm"],
        "review": "BLOCKED" if not report.ok or (oob and not oob.ok) else "PENDING",
    }
    st.setdefault("anim_finished", {})[key] = state
    return True


def _remember_anim_pair(st: dict) -> None:
    """After a verdict-switch render, keep it and materialise its opposite from the same report and clip."""
    st.pop("anim_verdict_stale", None)
    _remember_anim_verdict(st)
    current = set(st.get("anim_override") or ())
    other = () if current else sorted({c.get("name") for c in st.get("anim_report") or []
                                       if not c.get("ok") and c.get("name") in verify.OVERRIDABLE["animation"]})
    if current or other:
        _materialize_anim_verdict(st, other)


@pl.serialized
def allow_stills(out: Path, gid: int, indexes: list, allow: bool, cfg: EngineConfig, pace: float = 0.0) -> None:  # pace: unused, the same call shape as allow_animations
    """The human override on stills: allow cells that Python blocked as a judgement call (or take the permission back), several at once and cut again in ONE pass from the stored sheet.
    Each decision is stored on its sticker (`still_override`: the check ids) and in its history (actor human); the cell is cut again with those checks as warnings and enters G2 like any other
    (READY, pending), and every later cut of the sheet keeps the permission. Taking it back restores the block. Free: nothing is sent to a provider."""
    indexes = sorted({int(i) for i in indexes})
    for i in indexes:
        check_allow(out, gid, i, allow, "still")
    res = pl.read_result(out, gid)
    for i in indexes:
        st = res["stickers"][i - 1]
        if allow:
            failed = _blocks(st.get("report"))
            st["still_override"] = sorted(set(st.get("still_override") or []) | set(failed))
            pl.hist(st, "still", "human", "APPROVE", "allowed anyway: " + ", ".join(failed), detail={"override": failed})
        else:
            was = st.get("still_override") or []
            st["still_override"] = []
            pl.hist(st, "still", "human", "REJECT", "allowance withdrawn: " + ", ".join(was), detail={"override": was})
    pl.write_result(out, gid, res)
    pl.recut_cells(out, gid, indexes, cfg)
    pl.emit(out, gid, "still_allowed" if allow else "still_allow_withdrawn", "done", 0, {"indexes": indexes}, "human", "APPROVE" if allow else "REJECT")


def allow_animations(out: Path, gid: int, indexes: list, allow: bool, cfg: EngineConfig, pace: float = 0.0) -> None:
    """The human override on animations: allow animations that Python blocked as a judgement call (leaving or crossing their slot, a loop that does not close) or take the permission back, several
    at once and cut again in ONE pass. Each decision is stored on its sticker (`anim_override`) and in its history (actor human); the check is downgraded to a warning on the next cut, and every
    later cut keeps it: a re-slice of the returned video, or Animate again on a prepared video."""
    indexes = sorted({int(i) for i in indexes})
    by_sheet: dict = {}
    for i in indexes:
        aid = check_allow(out, gid, i, allow, "animation")[1]
        by_sheet.setdefault(aid, []).append(i)
    res = pl.read_result(out, gid)
    restored = set()
    for i in indexes:
        st = res["stickers"][i - 1]
        aid = anim_source(res, i)[1]
        remembered = bool(st.get("anim_finished"))
        _remember_anim_verdict(st)              # the state being left is the one the next switch can restore
        if allow:
            failed = _blocks(st.get("anim_report"))
            st["anim_override"] = sorted(set(st.get("anim_override") or []) | set(failed))
            if remembered or not st.get("anim_verdict_stale"):
                _materialize_anim_verdict(st, st["anim_override"])
            if _restore_anim_verdict(st):
                restored.add(i)
            pl.hist(st, "video", "human", "APPROVE", "allowed anyway: " + ", ".join(failed), aid, {"override": failed})
        else:
            was = st.get("anim_override") or []
            st["anim_override"] = []
            if remembered or not st.get("anim_verdict_stale"):
                _materialize_anim_verdict(st, ())
            if _restore_anim_verdict(st):
                restored.add(i)
            pl.hist(st, "video", "human", "REJECT", "allowance withdrawn: " + ", ".join(was), aid, {"override": was})
    pl.write_result(out, gid, res)
    for aid, idx in by_sheet.items():
        idx = [i for i in idx if i not in restored]
        if not idx:
            continue
        if aid:
            slice_video(out, gid, aid, cfg, pace, only=idx, remember_verdicts=True)
        else:
            reanimate_prepared(out, gid, idx, cfg, pace, remember_verdicts=True)


def allow_animation(out: Path, gid: int, index: int, allow: bool, cfg: EngineConfig, pace: float = 0.0) -> None:
    allow_animations(out, gid, [index], allow, cfg, pace)


def allow_cells(out: Path, gid: int, kind: str, indexes: list, allow: bool, cfg: EngineConfig, pace: float = 0.0) -> None:
    """One entry for the route: `kind` 'still' or 'animation'."""
    {"still": allow_stills, "animation": allow_animations, "video_sheet": allow_sheet}[kind](out, gid, indexes, allow, cfg, pace)


def reanimate_prepared(out: Path, gid: int, indexes: list, cfg: EngineConfig, pace: float = 0.0, remember_verdicts: bool = False) -> None:
    """Animate these cells again from the batch's prepared video (or its pre-sliced clips) with the blocks a human allowed (`anim_override`) kept as warnings. No new video, no credits;
    the same cells come back from the animation cache when nothing changed."""
    res = pl.read_result(out, gid)
    cfg = pl.cfg_for(res, cfg)
    d = pl.gen_dir(out, gid)
    todo = sorted({int(i) for i in indexes})
    try:
        with pl.Stage(out, gid, "video_sliced", pace) as s:
            def on_cell(r):
                st = res["stickers"][r.index - 1]
                pl.record_anim(d, st, r, "prepared video", preserve_finished=remember_verdicts)
                if remember_verdicts:
                    _remember_anim_pair(st)
                pl.write_result(out, gid, res)
                pl.emit(out, gid, "video_cell", "done", 0, {"index": r.index, "status": r.status, "reason": r.reason})
            for i in todo:
                st = res["stickers"][i - 1]
                if not remember_verdicts:
                    if st.get("webm") or st.get("anim_finished"):
                        st["anim_verdict_stale"] = True
                    st.pop("anim_finished", None)
                st["anim_status"] = "PROCESSING"
            pl.write_result(out, gid, res)
            waive = {i: set(res["stickers"][i - 1].get("anim_override") or []) for i in todo}
            results = pl.animate_cells(res, cfg, todo, on_cell, AnimCache(out / "cache" / "anim"), waive=waive)
            s.result = {"ready": sum(r.status == "READY" for r in results), "failed": sum(r.status != "READY" for r in results)}
            res["stage"] = "video_sliced"
            pl.write_result(out, gid, res)
    except Exception as e:
        res["error"] = str(e)[:300]
        for i in todo:
            if res["stickers"][i - 1]["anim_status"] == "PROCESSING":
                res["stickers"][i - 1]["anim_status"] = "FAILED"
        pl.write_result(out, gid, res)


def reslice(out: Path, gid: int, cfg: EngineConfig, pace: float = 0.0, only: list | None = None) -> None:
    """Apply the batch's CURRENT stroke, trim, Video size and Keep approved size to the animations, from the video that is already stored: no new video,
    no credits. The source is the sliced video sheet they come from (cut_sheet), else the prepared 3x3 video or clips (an imported video of the image
    sheet, Keep approved size did nothing there). Repeated settings come back from the animation cache at once."""
    res = pl.read_result(out, gid)
    v = cut_sheet(res)                     # only the sheet the animations come from (a superseded one while its successor is being made)
    if v:
        if not v.get("preview"):                         # a video stored before the light preview existed: make it now
            pv = _make_preview(pl.gen_dir(out, gid) / v["video"])
            if pv:
                r2 = pl.read_result(out, gid)
                next(x for x in r2["video_sheets"] if x["id"] == v["id"])["preview"] = f"video_sheet/{v['id']}/{pv}"
                pl.write_result(out, gid, r2)
        slice_video(out, gid, v["id"], cfg, pace, only=only)              # only: one sticker's Video size changed, the others stay as they are
    elif prepared_source(res):             # run_animate cuts only what is not READY: mark the animations to cut again STALE first
        cells = [st["index"] for st in res["stickers"] if st.get("webm") and not st.get("edited") and st["status"] == "READY"
                 and st["review"].get("still") != "REJECTED" and (only is None or st["index"] in only)]     # the cells run_animate cuts
        if not cells:
            return
        for i in cells:
            res["stickers"][i - 1]["anim_status"] = "STALE"
        pl.write_result(out, gid, res)
        pl.run_animate(out, gid, cfg, "slice" if len(cells) == 1 else "pack", cells[0] if len(cells) == 1 else None, pace)


def reloop(out: Path, gid: int, cfg: EngineConfig, lib=None) -> dict:
    """Make a batch's animations again from its stored videos with the current engine, then refresh the copies in the packs. Free: no new video, no credits; every S# and review stays."""
    res = pl.read_result(out, gid)
    v = cut_sheet(res)
    src = res.get("source") or {}
    if not v and not prepared_source(res):
        return {"id": gid, "animations": 0, "pack_copies": 0}
    edited = [st["index"] for st in res["stickers"] if st.get("edited") and st.get("webm")]
    if edited:                                            # text or drawing baked in the Studio: slicing again would lose it
        return {"id": gid, "animations": 0, "pack_copies": 0, "skipped": f"edited in the Studio: {', '.join('S' + str(i) for i in edited)}"}
    if v and not (pl.gen_dir(out, gid) / v["video"]).is_file():
        return {"id": gid, "animations": 0, "pack_copies": 0, "skipped": "stored video is missing: " + v["id"]}
    if not v and src.get("video_path") and not Path(src["video_path"]).is_file():
        return {"id": gid, "animations": 0, "pack_copies": 0, "skipped": "stored video is missing: " + Path(src["video_path"]).name}
    decisions = {s["index"]: dict(s.get("review") or {}) for s in res["stickers"]}
    previously_ready = {s["index"] for s in res["stickers"] if s.get("anim_status") == "READY"}
    prior_stage = res.get("stage")
    reslice(out, gid, cfg)
    res, d, copies, n = pl.read_result(out, gid), pl.gen_dir(out, gid), 0, 0
    for st in res["stickers"]:
        for kind, decision in decisions.get(st["index"], {}).items():
            if decision in ("APPROVED", "REJECTED"):
                st.setdefault("review", {})[kind] = decision
        if st.get("anim_status") == "READY" and st.get("webm"):
            # Reprocessing the same approved source is not a new human review.
            st["review"] = decisions.get(st["index"], st["review"])
            n += 1
            if lib is not None and not res.get("error"):
                copies += lib.refresh_from_generation(out, res["generation_id"], st["index"], None, d / st["webm"])
    if not res.get("error") and all(s.get("anim_status") == "READY" for s in res["stickers"] if s["index"] in previously_ready):
        res["stage"] = prior_stage
    pl.write_result(out, gid, res)
    if res.get("error"):
        return {"id": gid, "animations": 0, "pack_copies": 0, "error": res["error"]}
    return {"id": gid, "animations": n, "pack_copies": copies}


def attach_video(out: Path, gid: int, aid: str, data: bytes, filename: str = "video.mp4", sent_prompt: str | None = None, custom: bool = False,
                 model: str | None = None) -> dict:
    """Synchronous part: validate the gate order and store the upload next to the sheet it belongs to. Slicing is slice_video().
    `sent_prompt` is the text the video model was really given (kept on the sheet, `video_prompt_custom` when the user wrote it)."""
    res = pl.read_result(out, gid)
    v = sheet_of(res, aid)
    if v["status"] not in ("APPROVED", "VIDEO_BLOCKED"):
        raise refuse(f"{aid} is {v['status']}: approve the video sheet (G3) before attaching its video." if v["status"] in ("BUILT", "REJECTED")
                     else f"{aid} already has its video sliced.")
    if not data:
        raise pl.PipelineError("empty upload")
    ext = Path(filename).suffix.lower()
    ext = ext if ext in VIDEO_EXT else ".mp4"
    d = pl.gen_dir(out, gid)
    for old in (d / "video_sheet" / aid).glob("video.*"):
        old.unlink()
    dest = d / "video_sheet" / aid / f"video{ext}"
    dest.write_bytes(data)
    pv = _make_preview(dest)
    v.update(video=f"video_sheet/{aid}/{dest.name}", video_name=filename, status="VIDEO_RETURNED", block=None, video_flags=[], video_checks=[],
             preview=f"video_sheet/{aid}/{pv}" if pv else None)
    if sent_prompt:
        v.update(video_prompt_sent=sent_prompt, video_prompt_custom=bool(custom))
    if model:
        v["model"] = model                    # the video model that made this sheet's video: the Studio's "current", next to the picker for the next one
    _stage(res, "video_returned")
    pl.write_result(out, gid, res)
    return {"sheet": aid, "bytes": len(data)}


def anim_preview(out: Path, gid: int, index: int, cfg: EngineConfig, scale=None, resize=None, offset=None, frame: int = 0) -> tuple[bytes, dict]:
    """ONE FRAME of one sticker's animation as it would be cut with this Size (`scale`, None = what it has now), Resize (`resize`, None = the batch's)
    and Position (`offset` (dx, dy), None = the sticker's), from the stored video: the same rects, key and size_window as the real re-cut, but one
    frame and no encode.
    Nothing is written to the batch. Returns (the PNG, {index, frame, frames, mode, scale, offset, core_canvas, centre_canvas}); the route writes it
    to the batch's preview/ folder (never base64). The source is the sliced video sheet, else the prepared 3x3 video or clip (an imported video too)."""
    import io
    from dataclasses import replace
    from ..engine.video import preview_frame
    res = pl.read_result(out, gid)
    v = cut_sheet(res)
    if not v and not prepared_source(res):
        raise pl.PipelineError("This batch has no cut video to preview", 409)
    if not 1 <= int(index) <= len(res["stickers"]) or (v and int(index) not in v["slots"]):
        raise pl.PipelineError(f"S{index} is not on the video sheet", 404)
    st, d = res["stickers"][int(index) - 1], pl.gen_dir(out, gid)
    if scale is not None and not pl.VIDEO_SIZE[0] <= float(scale) <= pl.VIDEO_SIZE[1]:
        raise pl.PipelineError("scale must be from 0.5 to 1.5")
    if offset is not None and (len(offset) != 2 or any(not -pl.OFFSET_MAX <= float(x) <= pl.OFFSET_MAX for x in offset)):
        raise pl.PipelineError(f"offset must be [dx, dy], each {-pl.OFFSET_MAX:g} to {pl.OFFSET_MAX:g}")
    c_cfg = pl.cfg_for(res, cfg)
    own = st.get("anim_scale")
    f = float(scale) if scale is not None else float(own if own is not None else c_cfg.anim_scale)
    off = tuple(float(x) for x in (offset if offset is not None else st.get("anim_offset") or (0.0, 0.0)))
    c_cfg = replace(c_cfg, anim_scale=round(f, 2), anim_offset=off,
                    **({"anim_hotspot": bool(resize), "anim_same_scale": not resize} if resize is not None else {}))
    ref = _read_rgba(d / st["png"]) if st.get("png") else None
    m, src = st.get("anim_metrics") or {}, res.get("source") or {}
    clip = (src.get("clips") or {}).get(str(int(index)))
    try:
        if v:
            img, meta = preview_frame(d / v["video"], c_cfg, int(index), frame, m, ref, layout=json.loads((d / v["layout"]).read_text(encoding="utf-8")))
        elif clip:
            from ..engine.video import pick_clip
            img, meta = preview_frame(pick_clip({k: Path(x) for k, x in clip.items()}, c_cfg)[1], c_cfg, int(index), frame, m, ref, clip=True)
        else:
            if not src.get("video_path") or not Path(src["video_path"]).is_file():
                raise pl.PipelineError("The stored video of this batch is missing", 409)
            g = src.get("grid")
            img, meta = preview_frame(Path(src["video_path"]), c_cfg, int(index), frame, m, ref, rects=g["rects"] if g else None,
                                      sheet_wh=tuple(src["sheet_size"]) if g else None)
    except ValueError as e:
        raise pl.PipelineError(f"S{index} cannot be previewed: {e}", 422)
    buf = io.BytesIO()
    Image.fromarray(img, "RGBA").save(buf, "PNG")
    return buf.getvalue(), {**meta, "index": int(index), "scale": round(f, 2), "offset": list(off)}


def slice_video(out: Path, gid: int, aid: str, cfg: EngineConfig, pace: float = 0.0, only: list | None = None,
                remember_verdicts: bool = False) -> None:
    """Background job: video-stage checks on the returned video, then each approved slot is decoded from the layout's exact
    rectangles, re-keyed on every frame, boundary-checked and encoded. Python's blocks are recorded on the sticker's history."""
    res = pl.read_result(out, gid)
    cfg = pl.cfg_for(res, cfg)
    v = sheet_of(res, aid)
    d = pl.gen_dir(out, gid)
    try:
        if v.get("blocked"):
            v["status"] = "VIDEO_BLOCKED"
            pl.write_result(out, gid, res)
            return
        layout = json.loads((d / v["layout"]).read_text(encoding="utf-8"))
        sheet = np.array(Image.open(d / v["file"]).convert("RGB"))
        mp4 = d / v["video"]
        with pl.Stage(out, gid, "video_returned", pace) as s:
            info = {}
            checks = check_returned_video(mp4, layout, sheet, cfg, on_probe=lambda i: info.update(i))
            allowed = verify.waived(v.get("sheet_override")) & set(verify.OVERRIDABLE["video_sheet"])
            checks = [verify._apply_waiver(c, allowed, {}) for c in checks]
            v["video_checks"] = [c.to_dict() for c in checks]
            v["video_info"] = {k: info.get(k) for k in ("codec", "width", "height", "fps", "duration")}
            block = next((c for c in checks if not c.ok and c.severity == verify.BLOCK and c.id in VIDEO_BLOCKS), None)
            flag = next((c for c in checks if not c.ok and c.id == "blank_slots_stay_empty"), None)
            v["video_flags"] = [flag.id] if flag else []
            s.result = {"sheet": aid, "blocked": block.id if block else None, "flags": v["video_flags"], **v["video_info"]}
            if block:
                v["status"], v["block"] = "VIDEO_BLOCKED", block.id
                for i in v["slots"]:
                    pl.hist(res["stickers"][i - 1], "video", "python", "BLOCK", block.id, aid, {"check": block.id, "value": block.value, "limit": block.limit, "note": block.note})
                _stage(res, "video_returned"); pl.write_result(out, gid, res)
                return
            if flag:   # blocks the generation's video, not the stickers: the approved slots are still sliced
                pl.emit(out, gid, "video_flag", "done", 0, {"sheet": aid, "check": flag.id, "note": flag.note}, "python", "BLOCK")
            _stage(res, "video_returned"); pl.write_result(out, gid, res)
        with pl.Stage(out, gid, "video_sliced", pace) as s:
            refs = {}
            todo = [i for i in v["slots"] if only is None or i in only]
            for i in todo:
                st = res["stickers"][i - 1]
                if not remember_verdicts:                         # a real re-cut invalidates both old verdict snapshots
                    if st.get("webm") or st.get("anim_finished"):
                        st["anim_verdict_stale"] = True
                    st.pop("anim_finished", None)
                if st.get("png"):
                    refs[i] = _read_rgba(d / st["png"])               # RGBA: identity_kept uses its alpha, sharpness its edge detail
                st["anim_status"], st["anim_reason"] = "PROCESSING", None

            def on_cell(r):
                st = res["stickers"][r.index - 1]
                pl.record_anim(d, st, r, aid, preserve_finished=remember_verdicts)
                if remember_verdicts:
                    _remember_anim_pair(st)
                pl.write_result(out, gid, res)
                pl.emit(out, gid, "video_cell", "done", 0, {"index": r.index, "status": r.status, "reason": r.reason}, "python", "PASS" if r.status == "READY" else "BLOCK")
            pl.write_result(out, gid, res)
            waive = {i: set(res["stickers"][i - 1].get("anim_override") or []) for i in todo}          # slot blocks a human allowed, kept across every re-slice
            results = []
            for c_cfg, group in pl.cell_cfgs(res, cfg, todo):                   # a sticker with its own Video size is cut with its own config
                results += process_video(mp4, c_cfg, cells=group, on_cell=on_cell, layout=layout, refs=refs, cache=AnimCache(out / "cache" / "anim"), waive=waive)
            v["status"] = "SUPERSEDED" if v["status"] == "SUPERSEDED" else "SLICED"       # re-cutting a retired sheet's video (an edge change) never brings it back
            s.result = {"ready": sum(r.status == "READY" for r in results), "failed": sum(r.status != "READY" for r in results)}
            _stage(res, "video_sliced"); pl.write_result(out, gid, res)
    except Exception as e:
        res["error"] = str(e)[:300]
        pl.write_result(out, gid, res)


# ---------- the simple flow: one click per step ----------
def _ensure_plan(out: Path, gid: int, note: str) -> dict:
    res = pl.read_result(out, gid)
    if not res["reviews"].get("plan"):
        pl.approve_plan(out, gid, note)
        res = pl.read_result(out, gid)
    _plan_ok(res)
    return res


def _approve_stills(out: Path, gid: int, res: dict, note: str) -> None:
    """The stickers that were not dropped (x) and passed Python are approved at G2; skipped once a video sheet locks the stills."""
    if active_sheet(res) or not any(s["status"] == "READY" and s["review"]["still"] == "PENDING" for s in res["stickers"]):
        return
    review(out, gid, "still", "APPROVE", "ready", note)


def _keep_clips(res: dict, d: Path, v: dict, now: float) -> None:
    """Copy each sticker's current clip (cut from sheet `v`) to slices/versions/<name>-<sheet>.webm and list it in `anim_versions`: nothing is overwritten unkept."""
    for i in v["slots"]:
        st = res["stickers"][i - 1]
        if st.get("webm") and (d / st["webm"]).is_file():
            keep = Path("slices") / "versions" / f"{Path(st['webm']).stem}-{v['id']}.webm"
            (d / keep).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(d / st["webm"], d / keep)
            st.setdefault("anim_versions", []).append({"sheet": v["id"], "webm": keep.as_posix(), "review": (st.get("review") or {}).get("anim"), "ts": now})


@pl.serialized
def pick_video(out: Path, gid: int, aid: str, by: str = "human") -> dict:
    """Pick animation `aid` (an earlier video of this generation) as the one the stickers are cut from: the current one becomes SUPERSEDED (its clips
    kept as versions), `aid` becomes SLICED again; the caller then re-cuts `aid` from its stored video (slice_video: free, no new video). G4 asks again."""
    res = pl.read_result(out, gid)
    v = sheet_of(res, aid)
    cur = cut_sheet(res)
    if not v.get("video") or v["status"] not in CUT:
        raise refuse(f"{aid} has no animations to pick (it is {v['status'].lower()}).")
    if any(x.get("status") == "VIDEO_RETURNED" for x in res["video_sheets"]) or any(s.get("anim_status") == "PROCESSING" for s in res["stickers"]):
        raise refuse("A video is being cut into animations: wait for it to finish, then pick.")
    if cur and cur["id"] == aid:
        return {"picked": aid, "changed": False}
    now = round(time.time(), 3)
    if cur:
        _keep_clips(res, pl.gen_dir(out, gid), cur, now)
        cur["status"] = "SUPERSEDED"
    v["status"] = "SLICED"
    res["reviews"]["video_sheet"][aid] = _stamp("APPROVE", by, "picked again", picked=True)
    for i in v["slots"]:
        pl.hist(res["stickers"][i - 1], "video_sheet", by, "APPROVE", "picked", aid)
    pl.write_result(out, gid, res)
    return {"picked": aid, "changed": True, "was": cur["id"] if cur else None}


@pl.serialized
def remove_video(out: Path, gid: int, aid: str, by: str = "human") -> dict:
    """Take animation `aid` out of the row: REJECTED (a recorded click), its sheet and video stay on disk. The animation the stickers come from now
    cannot be removed: pick another one first."""
    res = pl.read_result(out, gid)
    v = sheet_of(res, aid)
    cur = cut_sheet(res)
    if cur and cur["id"] == aid:
        raise refuse(f"{aid} is the animation in use: pick another one first.")
    if v["status"] == "VIDEO_RETURNED":
        raise refuse(f"{aid} is being cut into animations: wait for it to finish.")
    v["status"], v["removed_at"] = "REJECTED", round(time.time(), 3)
    res["reviews"]["video_sheet"][aid] = _stamp("REJECT", by, "removed from the animations")
    pl.write_result(out, gid, res)
    return {"removed": aid}


@pl.serialized
def redo_video(out: Path, gid: int, by: str = "human", note: str | None = None) -> dict:
    """"Regenerate video" in the SAME batch: the current video sheet is retired (SUPERSEDED, a recorded human decision; its sheet, video
    and layout stay on disk) and each sticker's current clip is kept as a version (slices/versions/<name>-<sheet>.webm, `anim_versions`). The caller then
    builds the next sheet (quick_sheet: A2 from the same kept stills, same S#) and sends it with the prompt and model the person chose; until that video is
    sliced the stickers keep the retired sheet's animations (cut_sheet). A sheet still waiting for its video is reused, nothing is retired. Free by itself."""
    res = pl.read_result(out, gid)
    v = active_sheet(res)
    if not v or v["status"] in ("BUILT", "APPROVED"):
        return {"retired": None}
    if v["status"] == "VIDEO_RETURNED":
        raise refuse("The video is still being cut into animations: wait for it to finish, then regenerate.")
    d, now = pl.gen_dir(out, gid), round(time.time(), 3)
    cut = v["status"] == "SLICED"              # a VIDEO_BLOCKED sheet never produced clips: it is simply rejected, and the stickers' clips are not its own
    if cut:
        _keep_clips(res, d, v, now)
    for i in v["slots"]:
        pl.hist(res["stickers"][i - 1], "video_sheet", by, "REJECT", "regenerate", v["id"], {"note": note or "replaced by a new video in the same batch"})
    v["status"] = "SUPERSEDED" if cut else "REJECTED"
    v["superseded_at"] = now
    res["reviews"]["video_sheet"][v["id"]] = _stamp("REJECT", by, note or "regenerate: a new video in the same batch", superseded=cut)
    pl.write_result(out, gid, res)
    pl.emit(out, gid, "video_sheet_reviewed", "done", 0, {"sheet": v["id"], "note": "regenerate"}, by, "REJECT")
    return {"retired": v["id"]}


def quick_sheet(out: Path, gid: int, cfg: EngineConfig) -> dict:
    """'Make a video': approve the kept stills, build the video sheet and approve it for sending (the download is the decision)."""
    res = _ensure_plan(out, gid, "approved by pressing Make a video")
    _approve_stills(out, gid, res, "approved by Make a video")
    res = pl.read_result(out, gid)
    sheet = active_sheet(res)
    if not sheet:
        build_sheet(out, gid, cfg)
        res = pl.read_result(out, gid)
        sheet = active_sheet(res)
    if sheet["status"] == "BUILT":
        review(out, gid, "video_sheet", "APPROVE", sheet["id"], "approved by Make a video")
    return {"sheet": sheet["id"]}


def quick_add(out: Path, gid: int, lib, pack_id: str | None = None, pack_name: str | None = None, mode: str = "add", names: dict | None = None, owner: str | None = None) -> dict:
    """'Add to pack': approve what was kept at G2 / G4, approve the final pack (G5) and add it to a Library pack.
    Animated stickers when the video was made, the stills when it was not. Anything already added to that pack is skipped.
    mode: when an animated sticker's still is already in the pack, 'add' keeps both, 'replace' puts the animated one in the still's place."""
    res = pl.read_result(out, gid)
    manual = (res.get("slots") or {}).get("emoji_batch")
    target = (manual or {}).get("pack_id")
    if manual and not target and pack_id:
        owned = next((p for p in lib.snapshot(owner=owner or res.get("owner", "local"))["packs"] if p["id"] == pack_id), None)
        if not owned or owned.get("stickers"):
            raise refuse("This manual batch was planned for a new pack. Plan it for the existing pack before adding it there.")
    if target:
        if pack_id and pack_id != target:
            raise refuse("This manual batch was planned for another pack. Use its chosen pack or make a new plan.")
        if not lib.owns(target, owner or res.get("owner", "local")):
            raise refuse("No such target pack")
        pack_id = target
    res = _ensure_plan(out, gid, "approved by pressing Add")
    _approve_stills(out, gid, res, "approved by Add")
    res = pl.read_result(out, gid)
    animated = any(s["anim_status"] == "READY" for s in res["stickers"])
    if animated:
        pack = res["reviews"].get("pack")
        if pack and pack["decision"] == "APPROVE":
            review(out, gid, "pack", "REJECT", note="reopened to add more")
        if any(s["anim_status"] == "READY" and s["review"]["anim"] == "PENDING" for s in pl.read_result(out, gid)["stickers"]):
            review(out, gid, "anim", "APPROVE", "ready", "approved by Add")
        res = pl.read_result(out, gid)
        keep = final_indices(res)
        kind = "animated"
        if not keep:
            raise refuse("Nothing to add: every sticker was dropped or blocked.")
        review(out, gid, "pack", "APPROVE", note="approved by Add")
    else:
        keep = [s["index"] for s in res["stickers"] if s["status"] == "READY" and s["review"]["still"] == "APPROVED"]
        kind = "static"
        if not keep:
            raise refuse("Nothing to add: every sticker was dropped or blocked.")
    pid = pack_id or (lib.create_pack(pack_name or "My stickers", owner=owner or res.get("owner", "local"))["id"])
    res = pl.read_result(out, gid)
    if manual and not target:
        manual["pack_id"] = pid
        res["slots"]["emoji_batch"] = manual
        pl.write_result(out, gid, res)
    done = res.setdefault("added", {}).setdefault(pid, [])
    added = []
    for i in keep:
        key = f"{kind}:{i}"
        if key in done:
            continue
        o = (names or {}).get(str(i)) or {}
        if manual:
            entry = next((e for e in manual["entries"] if e["index"] == i), None)
            if not entry or entry["emoji"] != res["stickers"][i - 1]["emoji"]:
                raise refuse("This manual batch's decisions no longer match its stickers. Plan it again.")
            if o.get("emoji") and o["emoji"] != entry["emoji"]:
                raise refuse("Changing a manual batch's emoji needs a new duplicate check. Paste the revised batch in chat.")
            lib.add_emoji_from_generation(out, pid, gid, i, kind, entry["choice"], mode, o.get("name"))
        else:
            lib.add_from_generation(out, pid, gid, i, kind, o.get("name"), o.get("emoji"))
        done.append(key)
        added.append(i)
    replaced = 0
    if kind == "animated" and mode == "replace" and added:
        replaced = lib.replace_static_with_animated(pid, res["generation_id"], added)
        done[:] = [k for k in done if not (k.startswith("static:") and int(k.split(":")[1]) in added)]
    pl.write_result(out, gid, res)
    pl.emit(out, gid, "added_to_pack", "done", 0, {"pack": pid, "kind": kind, "stickers": added, "replaced_stills": replaced}, "human", "APPROVE")
    return {"added": len(added), "already": len(keep) - len(added), "replaced": replaced, "kind": kind, "pack_id": pid, "indices": keep}


@pl.serialized
def drop(out: Path, gid: int, index: int, dropped: bool) -> dict:
    """The x on a tile: drop a sticker from the set (a human reject at the stage it is in), or bring it back. Never deletes."""
    res = pl.read_result(out, gid)
    _plan_ok(res) if res["reviews"].get("plan") else pl.approve_plan(out, gid, "approved by the first decision")
    res = pl.read_result(out, gid)
    animated = any(s["anim_status"] in ("READY", "FAILED") for s in res["stickers"])
    st = res["stickers"][int(index) - 1]
    gate = "anim" if animated and st["anim_status"] == "READY" else "still"
    if gate == "anim" and res["reviews"].get("pack") and res["reviews"]["pack"]["decision"] == "APPROVE":
        review(out, gid, "pack", "REJECT", note="reopened: a sticker was changed")
    review(out, gid, gate, "REJECT" if dropped else "APPROVE", int(index), "dropped from the set" if dropped else "brought back")
    return {"index": int(index), "dropped": dropped, "gate": gate}


# ---------- 1x1 regeneration ----------
def regen_plan(res: dict, index: int) -> dict:
    """The plan of ONE sticker, regenerated as a 1x1 sheet through the same engine."""
    st = res["stickers"][index - 1]
    slots = res.get("slots") or {}
    cell = next((c for c in slots.get("cells", []) if c["pos"] == index), None)
    desc = slots.get("subject_description") or res["task"]
    label = cell["label"] if cell else st["prompt"]
    new = {"subject_description": desc, "style_id": slots.get("style_id", "flat_vector"), "mode": "single_1x1",
           "cells": [{"pos": 1, "label": label, "tags": st["tags"], "emoji": st["emoji"], **({"motion": cell["motion"]} if cell and cell.get("motion") else {})}], "action_guidance": slots.get("action_guidance", "default"),
           "key_colour": slots.get("key_colour", "green")}
    parent_ver = int(res.get("template_version") or 0)    # the parent's own template version when it is >= 2 and the 1x1 file exists (a saved plan keeps its wording); never v1: its style line says "flat vector sticker illustration" whatever the batch was (a clay batch regenerated as flat)
    ver = parent_ver if parent_ver >= 2 and (prompter.TEMPLATES / f"single_1x1_v{parent_ver}.txt").is_file() else prompter.TEMPLATE_VERSION
    built = prompter.render_plan(new, "single_1x1", ver)
    return prompter.validate_plan({"task": res["task"], "task_slug": res["task_slug"], "grid": [1, 1], "template_id": "single_1x1", "template_version": ver,
                                   "slots": new, "sheet_prompt": built["sheet_prompt"], "video_prompt": built["video_prompt"],
                                   "stickers": [{"index": 1, "id": "prompt01", "prompt": built["prompts"][1], "key": st["key"], "tags": st["tags"], "emoji": st["emoji"]}]})


# ---------- search ----------
def search(out: Path, q: str, limit: int = 200) -> list[dict]:
    """File search across every generation's stickers: by key, tag, name, emoji, task or prompt words. Phase 2 swaps in Postgres behind the same route."""
    words = [w for w in re.findall(r"[a-z0-9_]+", q.lower()) if w]
    rows = []
    for gid in reversed(pl.list_ids(out)):
        try:
            res = pl.read_result(out, gid)
        except Exception:
            continue
        for s in res["stickers"]:
            hay = " ".join([s["key"], s["name"], res["task_slug"], res.get("name_key") or "", res["prompt"], s["emoji"], *s["tags"]]).lower()
            if all(w in hay for w in words):
                rows.append({"generation": res["generation_id"], "id": gid, "index": s["index"], "key": s["key"], "tags": s["tags"], "name": s["name"],
                             "task_slug": res["task_slug"], "status": s["status"], "reason": s["reason"], "review": s["review"], "anim_status": s["anim_status"],
                             "png": s["png"], "webm": s["webm"], "emoji": s["emoji"], "final": s["index"] in final_indices(res)})
                if len(rows) >= limit:
                    return rows
    return rows
