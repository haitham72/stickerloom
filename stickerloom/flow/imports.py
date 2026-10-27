"""Bring a file into stickerloom, never twice.

A sheet you made yourself, or a Higgsfield result (downloaded by hand, `hf_20261004_033516_<job id>.mp4`, or picked from Higgsfield's own history) comes in through
one door. Before anything is made, `known` asks whether stickerloom already has it:
- by its **Higgsfield job id**: Higgsfield names its downloads after the job (`hf_<date>_<time>_<job id>.<ext>`), the same id stickerloom records as a job's ticket
  (`external_task_id` in `out/jobs/J###.json`, `out/tasks/*.json`, `out/model_calls.jsonl`);
- by its **bytes** (sha256): `out/model_calls.jsonl` keeps the hash of every result stickerloom downloaded, and `out/imports.jsonl` of every file imported.
A known file answers where it already is (the job, the batch, the effect) and nothing is made. Free: nothing here calls a paid provider."""
from __future__ import annotations

import hashlib
import json
import re
import time
import threading
import uuid
import contextvars
from pathlib import Path

from ..runtime import atomic

JOB_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
IMAGE = (".png", ".jpg", ".jpeg", ".webp")
VIDEO = (".mp4", ".mov", ".webm")
_LOCK = threading.RLock()


class ImportError(Exception):
    def __init__(self, message: str, code: int = 400, hint: dict | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint or {}          # extra answer fields (the import dialog's "Is this the result of …?" candidates)


def job_id_of(name: str) -> str | None:
    """The Higgsfield job id in a file name (`hf_20261004_033516_b16dc424-22fe-4ad2-8242-e8a36f10209b.mp4`), else None."""
    m = JOB_ID.search(str(name or ""))
    return m.group(0).lower() if m else None


def _ledger(out: Path) -> Path:
    return Path(out) / "imports.jsonl"


def _lines(p: Path):
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row
            except ValueError:
                continue
    except OSError:
        return


def known(out: Path, name: str = "", data: bytes | None = None, job_id: str | None = None) -> dict | None:
    """Where stickerloom already has this file, or None: {by: ticket|bytes, job?, generation?, effect?, import?}."""
    out = Path(out)
    tid = (job_id or job_id_of(name) or "").lower() or None
    sha = hashlib.sha256(data).hexdigest() if data else None
    if tid:
        for f in sorted((out / "jobs").glob("J*.json")):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(j.get("external_task_id") or "").lower() == tid:
                eff = ((j.get("request") or {}).get("effect") or {})
                if j.get("status") in ("FAILED", "CANCELLED", "DISMISSED") and not j.get("generation"):
                    continue  # A manually recovered result may still be imported.
                return {"by": "ticket", "job": j.get("id"), "generation": j.get("generation"), "effect": eff.get("id") if isinstance(eff, dict) else None,
                        "status": j.get("status"), "recoverable": j.get("status") != "DONE"}
        for f in sorted((out / "tasks").glob("*.json")):
            try:
                task = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(task.get("external_task_id") or "").lower() == tid:
                return {"by": "ticket", "task": task.get("id"), "generation": task.get("generation"), "status": task.get("status"), "recoverable": True}
    for d in _lines(out / "model_calls.jsonl"):
        if d.get("status") not in (None, "OK", "DONE") or not d.get("sha256"):
            continue
        if (tid and str(d.get("external_task_id") or "").lower() == tid) or (sha and d.get("sha256") == sha):
            return {"by": "ticket" if tid and str(d.get("external_task_id") or "").lower() == tid else "bytes", "job": d.get("job"), "generation": d.get("generation_id")}
    for d in _lines(_ledger(out)):
        if (tid and d.get("job_id") == tid) or (sha and d.get("sha256") == sha):
            return {"by": "ticket" if tid and d.get("job_id") == tid else "bytes", "import": d.get("id"), "generation": d.get("generation"),
                    "status": d.get("status", "ACCEPTED"), "recoverable": d.get("status") in ("PREPARING", "PROCESSING", "FAILED")}
    return None


def save(out: Path, name: str, data: bytes) -> Path:
    """The imported file, kept under `out/imports/` (never in the watch folders, which belong to the user)."""
    ext = Path(str(name or "")).suffix.lower()
    if ext not in IMAGE + VIDEO:
        raise ValueError(f"import a picture ({', '.join(IMAGE)}) or a video ({', '.join(VIDEO)})")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:60].strip("_") or "import"
    d = Path(out) / "imports"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{stem}-{uuid.uuid4().hex[:12]}{ext}"
    atomic.write_bytes(f, data)
    return f


def record(out: Path, name: str, data: bytes, generation: str | None, by: str, job_id: str | None = None) -> dict:
    """One line per import in `out/imports.jsonl`: what came in (name, hash, Higgsfield job id) and the batch it became."""
    row = {"id": "I" + uuid.uuid4().hex, "at": round(time.time(), 3), "name": str(name or "")[:200], "sha256": hashlib.sha256(data).hexdigest(),
           "job_id": (job_id or job_id_of(name) or "").lower() or None, "generation": generation, "by": by, "status": "ACCEPTED"}
    with _LOCK:
        Path(out).mkdir(parents=True, exist_ok=True)
        rows = list(_lines(_ledger(out))) + [row]
        atomic.write_text(_ledger(out), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return row


def _update(out: Path, iid: str, **values) -> dict:
    with _LOCK:
        rows = list(_lines(_ledger(out)))
        row = next(r for r in rows if r["id"] == iid)
        row.update(values)
        atomic.write_text(_ledger(out), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return row


def generation_id(value) -> int:
    if not re.fullmatch(r"G?0*[1-9][0-9]*", str(value or ""), re.I):
        raise ImportError("generation must be a batch number, such as G104")
    return int(str(value).upper().removeprefix("G"))


def job_id(value) -> str:
    """A local job number (J023), as the import dialog's explicit choice carries it."""
    if not re.fullmatch(r"J?0*[1-9][0-9]*", str(value or ""), re.I):
        raise ImportError("job must be a job number, such as J023")
    return "J" + str(int(str(value).upper().removeprefix("J"))).zfill(3)


def own_failed_jobs(out: Path, ticket: str | None, user_id: str | None) -> list[dict]:
    """The person's own FAILED/TIMEOUT jobs holding this provider ticket, newest first. A job belongs to the
    person who paid for it (`request.user`); a job before users is everyone's (`local`)."""
    from ..generation import jobs
    if not ticket:
        return []
    rows = []
    for j in jobs.list(out):
        if j.get("status") not in ("FAILED", "TIMEOUT"):
            continue
        if str(j.get("external_task_id") or "").lower() != str(ticket).lower():
            continue
        who = str((j.get("request") or {}).get("user") or "local")
        if who != str(user_id or "local") and who != "local":
            continue
        rows.append(j)
    rows.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
    return rows


def candidates(out: Path, ticket: str | None, user_id: str | None) -> list[dict]:
    """What the import dialog offers as "Is this the result of …?": the person's own failed jobs holding this
    ticket, newest first, with the prompt, the time and a thumbnail (the destination batch's sheet for a video
    job; a sheet job has no picture yet, so the dialog shows the file being imported)."""
    from . import pipeline as pl
    rows = []
    for j in own_failed_jobs(out, ticket, user_id):
        req = j.get("request") or {}
        thumb = None
        if j.get("generation"):
            try:
                res = pl.read_result(Path(out), int(str(j["generation"]).upper().lstrip("G")))
                rel = (res.get("source") or {}).get("sheet_copy")
                if rel:
                    thumb = f"/out/{j['generation']}/{rel}"
            except Exception:
                pass
        rows.append({"job": j["id"], "kind": j.get("kind"), "prompt": req.get("label") or req.get("prompt") or "",
                     "at": j.get("created_at"), "generation": j.get("generation"), "task": j.get("task"),
                     "status": j.get("status"), "thumb": thumb})
    return rows


def destination(c, generation, sheet=None):
    from . import pipeline as pl, gates
    gid = generation_id(generation)
    res = pl.read_result(c.out, gid)
    aid = sheet or next((v["id"] for v in res.get("video_sheets", []) if v.get("status") in ("APPROVED", "VIDEO_BLOCKED")), None)
    if not aid or not re.fullmatch(r"A[1-9][0-9]*", str(aid)):
        raise ImportError("Approve a video sheet (G3) in the destination batch first", 409)
    v = gates.sheet_of(res, aid)
    if v.get("status") not in ("APPROVED", "VIDEO_BLOCKED"):
        raise ImportError("Approve this video sheet (G3) before importing its video", 409)
    return gid, aid


def first_frame(video: Path) -> Path:
    """The first frame of an imported video as a PNG beside it: the sheet of a video imported on its own (an animated sheet starts on its still)."""
    from ..engine import ffmpeg as ff
    png = video.with_name(video.stem + "-frame0.png")
    pre = ["-c:v", "libvpx-vp9"] if video.suffix.lower() == ".webm" else []
    p = ff._run(["-y", "-loglevel", "error", *pre, "-i", str(video), "-frames:v", "1", "-an", str(png)])
    if p.returncode != 0 or not png.is_file():
        raise ImportError("The first frame of this video cannot be read; choose a valid MP4, MOV or WebM")
    return png


def _linkable(out: Path, user: dict, ticket: str | None, jid: str, name: str) -> dict:
    """The failed job an explicit dialog choice (or the single automatic match) completes: FAILED/TIMEOUT,
    holding a provider ticket (this file's, when the file names one), a sheet or a video job."""
    from ..generation import jobs
    try:
        lj = jobs.read(out, job_id(jid))
    except jobs.JobError as e:
        raise ImportError(str(e), getattr(e, "code", 404))
    if lj.get("status") not in ("FAILED", "TIMEOUT"):
        raise ImportError(f"{lj['id']} is {lj['status']}: only a failed job is completed by a manual download.", 409)
    if not lj.get("external_task_id"):
        raise ImportError(f"{lj['id']} holds no provider ticket: nothing links it to this file.", 409)
    if ticket and str(ticket).lower() != str(lj["external_task_id"]).lower():
        raise ImportError(f"{lj['id']} is waiting for a different provider job.", 409)
    if lj.get("kind") not in ("sheet", "video", "single"):
        raise ImportError(f"{lj['id']} is a {lj.get('kind')} job: a manual download completes a sheet or a video job.", 409)
    return lj


def _recover(c, user: dict, name: str, data: bytes, lj: dict, prompt: str = "") -> tuple[int, dict]:
    """A manual download completes the failed job holding its ticket (`recovered_by: "manual import"`), with no
    second charge. A sheet job's batch is built from the job's own saved plan and carries its external task id;
    a video job attaches to the job's own destination batch and sheet, without asking for them. The existing
    recovery actions (`generation/recovery.py`) stay for when the provider itself can still download."""
    from . import pipeline as pl, gates, sources
    from ..generation import jobs, tasks
    from ..media.video_project import MAX_UPLOAD
    ticket = str(lj.get("external_task_id") or "")
    if not data or len(data) > MAX_UPLOAD:
        raise ImportError("empty upload" if not data else "file too large", 400 if not data else 413)
    ext = Path(name).suffix.lower()
    if ext not in IMAGE + VIDEO:
        raise ImportError("Import a PNG, JPEG, WebP, MP4, MOV or WebM file")
    want_video = lj.get("kind") == "video"
    if want_video and ext not in VIDEO:
        raise ImportError(f"{lj['id']} is a video job: import the video file, not {ext or 'this'}.", 409)
    if not want_video and ext not in IMAGE:
        raise ImportError(f"{lj['id']} is a sheet job: import the sheet picture, not {ext or 'this'}.", 409)
    task = None
    if want_video:
        if not lj.get("generation"):
            raise ImportError(f"{lj['id']} has no batch yet: it cannot take a video.", 409)
    else:
        if not lj.get("task"):
            raise ImportError(f"{lj['id']} has no saved plan: import the sheet by hand instead.", 409)
        try:
            task = tasks.read_task(c.out, str(lj["task"]))
        except Exception:
            raise ImportError(f"The plan of {lj['id']} is gone: import the sheet by hand instead.", 409)
        if not task.get("plan"):
            raise ImportError(f"The plan of {lj['id']} is gone: import the sheet by hand instead.", 409)
    if not c.lock.acquire(blocking=False):
        raise ImportError("busy: a job is running, wait for it to finish", 409)
    handed_off, row, gid, aid, pick = False, None, None, None, None
    try:
        dest = jobs.job_dir(c.out, lj["id"]) / ("recovered" + ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(dest, data)
        if want_video:
            from ..engine.ffmpeg import probe
            try:
                info = probe(dest)
                if not info.get("codec") or not info.get("width") or not info.get("height"):
                    raise ValueError("no video stream")
            except Exception as e:
                dest.unlink(missing_ok=True)
                raise ImportError("This video cannot be opened; choose a valid MP4, MOV or WebM") from e
            gid, aid = destination(c, lj["generation"], (lj.get("request") or {}).get("sheet"))
        else:
            try:
                pl.load_rgb(dest)
            except Exception as e:
                dest.unlink(missing_ok=True)
                raise ImportError("This image cannot be opened; choose a valid PNG, JPEG or WebP") from e
        digest = hashlib.sha256(data).hexdigest()
        jobs.update(c.out, lj["id"], status="DONE", error=None, completed_at=round(time.time(), 3),
                    recovered_by="manual import",
                    result={"file": str(dest.relative_to(c.out)).replace("\\", "/"), "sha256": digest, "bytes": len(data)})
        if want_video:
            gates.attach_video(c.out, gid, aid, data, name, custom=True)
            pl.emit(c.out, gid, "video_returned", "done", 0, {"recovered": lj["id"], "ticket": ticket}, "human", "RECOVERED")
            work = lambda: gates.slice_video(c.out, gid, aid, c.cfg, c.pace)
        else:
            pick = sources.Pick(subject=tasks.subject_of(task["plan"]), subject_id=str(task.get("id") or lj["id"]),
                                variant=1, n_variants=1, sheet=dest, video=None)
            tok = pl.OWNER.set(user["id"])
            try:
                gid = pl.start(task.get("prompt") or prompt or pick.subject.replace("_", " "), c.out, c.inp, pick=pick, task=task)
            finally:
                pl.OWNER.reset(tok)
            tasks.link_generation(c.out, str(task["id"]), gid)
            jobs.attach_generation(c.out, lj["id"], gid)
            res = pl.read_result(c.out, gid)
            for st in res["stickers"]:
                pl.hist(st, "sheet", "human", "RECOVERED", f"recovered from a manual download of task {ticket}")
            pl.write_result(c.out, gid, res)
            work = lambda: pl.run_stills(c.out, gid, c.cfg, c.pace)
        row = record(c.out, name, data, f"G{gid:03d}", user["id"], ticket)
        _update(c.out, row["id"], status="PROCESSING", file=str(dest), kind="video" if want_video else "sheet",
                **({"sheet": aid} if want_video else {}))
        ctx = contextvars.copy_context()
        ctx.run(pl.OWNER.set, user["id"])

        def run():
            try:
                work()
                res = pl.read_result(c.out, gid)
                _update(c.out, row["id"], status="FAILED" if res.get("error") else "READY", error=res.get("error"))
            except Exception as e:
                _update(c.out, row["id"], status="FAILED", error=str(e)[:300])
            finally:
                c.lock.release()
        threading.Thread(target=ctx.run, args=(run,), daemon=True).start()
        handed_off = True
        out = {"id": gid, "kind": "video" if want_video else "sheet", "import": row["id"], "job": lj["id"], "recovered": True}
        if want_video:
            out["sheet"] = aid
        else:
            out["subject"] = pick.subject
        return 202, out
    finally:
        if not handed_off:
            c.lock.release()


def _likeness(a: Path, b: Path) -> float:
    """How far apart two pictures look (0 = the same): both shrunk to 48x48, the mean absolute difference per channel, 0..255. Layout decides it: an
    image sheet and a video sheet of the same stickers put them in different places and at different sizes."""
    import cv2
    import numpy as np
    from . import pipeline as pl
    x, y = (cv2.resize(pl.load_rgb(p), (48, 48), interpolation=cv2.INTER_AREA).astype(np.float32) for p in (a, b))
    return float(np.abs(x - y).mean())


def sheet_match(res: dict, d: Path, frame: Path) -> dict:
    """Which sheet of generation `res` an imported video was animated from: its first frame against the image sheet and against every video sheet the
    generation still shows (not removed). The closest wins. {sheet: "image" | "A#", distance, distances: {...}}."""
    cands = {}
    src = res.get("source") or {}
    img = d / src["sheet_copy"] if src.get("sheet_copy") else Path(src.get("sheet_path") or "")
    if img.is_file():
        cands["image"] = img
    for v in res.get("video_sheets") or []:
        if v.get("status") != "REJECTED" and v.get("file") and (d / v["file"]).is_file():
            cands[v["id"]] = d / v["file"]
    if not cands:
        raise ImportError("This batch has no sheet to match the video against yet", 409)
    dist = {k: round(_likeness(frame, p), 2) for k, p in cands.items()}
    best = min(dist, key=lambda k: (dist[k], k != "image"))
    return {"sheet": best, "distance": dist[best], "distances": dist}


def _into_batch(c, user: dict, name: str, data: bytes, batch, prompt: str = "", grid_ok: bool = False, again: bool = False, grid=None) -> tuple[int, dict]:
    """Import INSIDE a batch.
    - A picture becomes the NEXT GENERATION of this batch, like Regenerate: the batch's own plan (cells, tags, emoji) and naming, `parent` = the batch's root,
      `regen_of` = its main generation; the stills are cut from the picture.
    - A video attaches to the batch's MAIN generation. Which sheet it was animated from is read from its first frame (`sheet_match`), no question asked:
      the image sheet -> it is the batch's prepared 3x3 video and animates the kept stickers (the earlier clips kept as versions; when its first frame
      shows another grid than the batch's, a 409 with `detected_grid` asks first and `grid_ok` cuts it with the batch's grid anyway); a video sheet -> attached
      as the next animation (a sheet that already has a video is retired first with `redo_video`, and a new one is built at the same gap).
    The answer says which one was used. Free: nothing here calls a provider. Never twice by accident: the same bytes answer where they already are,
    unless `again`."""
    from . import pipeline as pl, gates, groups, sources
    from ..generation import prompter
    ext = Path(name).suffix.lower()
    gid0 = generation_id(batch)
    fam = groups.family(c.out, gid0)
    main = generation_id(fam["picked"])
    res = pl.read_result(c.out, main)
    if res.get("kind") == "particles":
        raise ImportError("This batch is a particle sheet: import into a sticker batch", 409)
    hit = None if again else known(c.out, "", data)
    if hit:
        return 200, {"duplicate": True, "batch": fam["root"], **hit}
    if not c.lock.acquire(blocking=False):
        raise ImportError("busy: a job is running, wait for it to finish", 409)
    handed_off, row = False, None
    try:
        f = save(c.out, name, data)
        info: dict = {"batch": fam["root"], "main": fam["picked"]}
        if ext in IMAGE:
            try:
                rgb = pl.load_rgb(f)
            except Exception as e:
                f.unlink(missing_ok=True)
                raise ImportError("This image cannot be opened; choose a valid PNG, JPEG or WebP") from e
            try:
                plan = prompter.validate_plan(json.loads((pl.gen_dir(c.out, main) / "prompts.json").read_text(encoding="utf-8")))
            except (OSError, ValueError) as e:
                raise ImportError(f"{res['generation_id']} has no saved plan to cut this picture with", 409) from e
            from ..engine.grid import detect_grid
            detected = tuple(map(int, grid.split("x"))) if grid else detect_grid(rgb, c.cfg.chroma)
            if detected in ((2, 2), (3, 3)) and tuple(plan["grid"]) != detected:
                plan = prompter.expand(res.get("prompt") or prompt or res["task"], detected)
            src = res.get("source") or {}
            pick = sources.Pick(subject=src.get("subject") or res["task_slug"], subject_id=str(src.get("subject_id") or "import"), variant=1, n_variants=1, sheet=f, video=None)
            tok = pl.OWNER.set(user["id"])
            try:
                gid = pl.start(res.get("prompt") or prompt or res["task"], c.out, c.inp, pick=pick, parent=generation_id(fam["root"]), regen_of=res["generation_id"],
                               regen_plan=plan, outline=res.get("outline_px"), erode=res.get("erode_px"), prepared=False)
            finally:
                pl.OWNER.reset(tok)
            new = pl.read_result(c.out, gid)
            new["plan_source"] = f"imported picture ({plan['grid'][0]}x{plan['grid'][1]}) into {res['generation_id']}"
            new["imported"] = {"name": str(name)[:200], "into": fam["root"]}
            for st in new["stickers"]:
                pl.hist(st, "sheet", "human", "IMPORT", f"imported {Path(name).name} into the batch of {fam['root']}")
            pl.write_result(c.out, gid, new)
            work = lambda: pl.run_stills(c.out, gid, c.cfg, c.pace)
            kind, info["generation"] = "sheet", f"G{gid:03d}"
            said = f"Imported as the next generation of {fam['root']} ({new['generation_id']}), cut as {plan['grid'][0]}x{plan['grid'][1]}."
        else:
            from ..engine.ffmpeg import probe
            try:
                vi = probe(f)
                if not vi.get("codec") or not vi.get("width") or not vi.get("height"):
                    raise ValueError("no video stream")
            except Exception as e:
                f.unlink(missing_ok=True)
                raise ImportError("This video cannot be opened; choose a valid MP4, MOV or WebM") from e
            if pl.STAGES.index(res["stage"]) < pl.SLICED or res.get("error"):
                raise ImportError(f"The stickers of {res['generation_id']} are not cut yet: import the video once they are", 409)
            if any(s.get("anim_status") == "PROCESSING" for s in res["stickers"]) or any(v.get("status") == "VIDEO_RETURNED" for v in res["video_sheets"]):
                raise ImportError("A video is being cut into animations in this batch: wait for it to finish", 409)
            gid, d = main, pl.gen_dir(c.out, main)
            frame = first_frame(f)
            m = sheet_match(res, d, frame)
            info["match"] = m
            g = (res.get("source") or {}).get("grid") or {}
            if m["sheet"] == "image" and g.get("rows") and not grid_ok:     # ask when the video shows another grid than the batch's
                from ..engine.grid import detect_grid
                want = (int(g["rows"]), int(g["cols"]))
                try:
                    seen = detect_grid(pl.load_rgb(frame), pl.cfg_for(res, c.cfg).chroma)
                except Exception:
                    seen = want                  # an unreadable gutter profile is not a reason to stop
                if tuple(seen) != want:
                    f.unlink(missing_ok=True)
                    raise ImportError(f"This video looks like a {seen[0]}x{seen[1]} grid, but {res['generation_id']} is {want[0]}x{want[1]}", 409,
                                      hint={"detected_grid": list(seen), "batch_grid": list(want), "cells": len(res["stickers"])})
            if m["sheet"] == "image":
                gates.redo_video(c.out, gid, user["id"], note="replaced by an imported video of the image sheet")      # a sliced video sheet is retired, its clips kept
                res = pl.read_result(c.out, gid)
                now = round(time.time(), 3)
                for st in res["stickers"]:              # clips from an earlier prepared video are kept as versions too, never overwritten unkept
                    if st.get("anim_status") == "READY" and st.get("webm") and (d / st["webm"]).is_file():
                        keep = Path("slices") / "versions" / f"{Path(st['webm']).stem}-import{int(now)}.webm"
                        if not any(x.get("webm") == keep.as_posix() for x in st.get("anim_versions") or []):
                            (d / keep).parent.mkdir(parents=True, exist_ok=True)
                            (d / keep).write_bytes((d / st["webm"]).read_bytes())
                            st.setdefault("anim_versions", []).append({"sheet": "prepared", "webm": keep.as_posix(), "review": (st.get("review") or {}).get("anim"), "ts": now})
                    if st.get("status") == "READY" and st["review"].get("still") != "REJECTED":
                        st["anim_status"] = "NOT_REQUESTED"
                    pl.hist(st, "video", "human", "IMPORT", f"imported {Path(name).name}: the batch's 3x3 video (matched the image sheet)")
                res["source"].update(video_path=str(f), video=f.name, has_video=True)
                pl.write_result(c.out, gid, res)
                work = lambda: pl.run_animate(c.out, gid, c.cfg, "pack", None, c.pace)
                aid = None
                said = f"The video matched {res['generation_id']}'s image sheet: it is cut as the batch's 3x3 video into the kept stickers."
            else:
                v = gates.sheet_of(res, m["sheet"])
                if v["status"] in gates.CUT:      # that sheet already has its video: retire the one in use, build the next at the same gap
                    gates.redo_video(c.out, gid, user["id"], note="replaced by an imported video")
                    from dataclasses import replace as _replace
                    fill = float(v.get("slot_fill") or c.cfg.slot_fill)
                    cur = gates.active_sheet(pl.read_result(c.out, gid))
                    if cur and cur["status"] in ("BUILT", "APPROVED") and abs(float(cur.get("slot_fill") or c.cfg.slot_fill) - fill) > 0.004:
                        gates.review(c.out, gid, "video_sheet", "REJECT", cur["id"], "another gap: an imported video")
                    aid = gates.quick_sheet(c.out, gid, _replace(c.cfg, slot_fill=fill))["sheet"]
                else:
                    aid = v["id"]
                    if v["status"] == "BUILT":
                        gates.quick_sheet(c.out, gid, c.cfg)
                gates.attach_video(c.out, gid, aid, data, name, custom=True)
                work = lambda: gates.slice_video(c.out, gid, aid, c.cfg, c.pace)
                said = (f"The video matched {res['generation_id']}'s video sheet {m['sheet']}: attached as animation {aid[1:]}"
                        + (f" (built again from {m['sheet']} at the same gap)" if aid != m["sheet"] else "") + ".")
            kind, info["generation"], info["sheet"] = "video", res["generation_id"], aid
        row = record(c.out, name, data, info["generation"], user["id"])
        _update(c.out, row["id"], status="PROCESSING", file=str(f), kind=kind, sheet=info.get("sheet"), batch=fam["root"])
        ctx = contextvars.copy_context()
        ctx.run(pl.OWNER.set, user["id"])

        def run():
            try:
                work()
                r = pl.read_result(c.out, gid)
                _update(c.out, row["id"], status="FAILED" if r.get("error") else "READY", error=r.get("error"))
            except Exception as e:
                _update(c.out, row["id"], status="FAILED", error=str(e)[:300])
            finally:
                c.lock.release()
        threading.Thread(target=ctx.run, args=(run,), daemon=True).start()
        handed_off = True
        from ..runtime import activity
        activity.say(f"imported {Path(name).name} into {fam['root']}: {said}")
        return 202, {"id": gid, "kind": kind, "import": row["id"], "message": said, **info}
    except Exception as e:
        if row:
            _update(c.out, row["id"], status="FAILED", error=str(e)[:300])
        raise
    finally:
        if not handed_off:
            c.lock.release()



def import_file(c, user: dict, name: str, data: bytes, prompt: str = "", generation=None, sheet=None, job_id=None, retry=False, job=None, as_new=False,
                batch=None, grid_ok=False, again=False, grid=None) -> tuple[int, dict]:
    """One serialized import lifecycle. Console holds the cross-process writer lock.

    The ledger reserves bytes before mutation, records the batch before background
    work, and makes failures addressable. An explicit retry reuses that batch.

    `job` (a local J id, the dialog's explicit choice) or exactly one of the person's own failed jobs
    holding this file's ticket completes that job instead (`_recover`): no new batch, no second charge.
    `as_new` imports as an unrelated batch even when a failed job matches.
    `batch` (any G### of a batch) imports INSIDE that batch (`_into_batch`): a picture is its next generation, a video its next animation;
    `grid_ok` cuts a video of the image sheet with the batch's grid although its first frame looks like another one.
    `again` imports bytes stickerloom already has once more (into `batch`, or as a new batch), instead of answering where they already are.
    """
    from . import pipeline as pl, gates, sources
    from ..media.video_project import MAX_UPLOAD
    if grid not in (None, "2x2", "3x3"):
        raise ImportError("Grid must be Auto, 2x2 or 3x3")
    if not data or len(data) > MAX_UPLOAD:
        raise ImportError("empty upload" if not data else "file too large", 400 if not data else 413)
    ext = Path(name).suffix.lower()
    if ext not in IMAGE + VIDEO:
        raise ImportError("Import a PNG, JPEG, WebP, MP4, MOV or WebM file")
    with _LOCK:
        if batch is not None and str(batch).strip():
            if grid:
                from . import groups
                main = generation_id(groups.family(c.out, generation_id(batch))["picked"])
                existing = pl.read_result(c.out, main)
                if tuple(existing.get("grid") or (3, 3)) != tuple(map(int, grid.split("x"))):
                    return import_file(c, user, name, data, prompt=prompt, as_new=True, again=again, grid=grid)
            return _into_batch(c, user, name, data, batch, prompt, grid_ok=grid_ok or bool(grid), again=again, grid=grid)
        if grid and generation is not None and ext in VIDEO:
            existing = pl.read_result(c.out, generation_id(generation))
            if tuple(existing.get("grid") or (3, 3)) != tuple(map(int, grid.split("x"))):
                generation, sheet, as_new = None, None, True
        if job:
            return _recover(c, user, name, data, _linkable(c.out, user, job_id or job_id_of(name), job, name), prompt)
        ticket = job_id or job_id_of(name)
        if ticket and not as_new and generation is None and sheet is None and not retry:
            cands = own_failed_jobs(c.out, ticket, user["id"])
            if len(cands) > 1:
                raise ImportError(f"This file names provider job {ticket}, which matches {len(cands)} of your failed jobs. Choose which one it completes.",
                                  409, hint={"candidates": candidates(c.out, ticket, user["id"])})
            if len(cands) == 1:
                return _recover(c, user, name, data, cands[0], prompt)
        hit = None if again else known(c.out, "" if as_new else name, data, None if as_new else job_id)      # as new: only the same bytes are a duplicate, not the failed job's id; again: none
        if hit and not (retry and hit.get("import") and hit.get("recoverable")):
            return 200, {"duplicate": True, **hit}
        if not c.lock.acquire(blocking=False):
            raise ImportError("busy: a job is running, wait for it to finish", 409)
        row = None
        handed_off = False
        try:
            old = next((r for r in _lines(_ledger(c.out)) if hit and r.get("id") == hit.get("import")), None)
            if old and old.get("by") != user["id"]:
                raise ImportError("not found", 404)
            is_video = ext in VIDEO
            resume_video = False
            # a video with no destination is a pack of its own: its first frame is the sheet, the video animates it (Library > + > Import pack)
            if old and old.get("generation"):
                alone = is_video and not old.get("sheet")
            else:
                alone = is_video and generation is None and sheet is None
            if is_video and not alone and old and old.get("generation"):
                gid, aid = generation_id(old["generation"]), old.get("sheet")
                res = pl.read_result(c.out, gid)
                status = gates.sheet_of(res, aid).get("status")
                if status == "SLICED" and not res.get("error"):
                    _update(c.out, old["id"], status="READY", error=None)
                    return 200, {"duplicate": True, **hit, "status": "READY", "recoverable": False}
                resume_video = status == "VIDEO_RETURNED"
                if not resume_video:
                    gid, aid = destination(c, gid, aid)
            if is_video and not alone and not resume_video:
                if not old or not old.get("generation"):
                    gid, aid = destination(c, generation, sheet)
            elif not is_video or alone:
                gid, aid = None, None
            f = Path(old["file"]) if old and old.get("file") and Path(old["file"]).is_file() else save(c.out, name, data)
            if is_video:
                from ..engine.ffmpeg import probe
                try:
                    info = probe(f)
                    if not info.get("codec") or not info.get("width") or not info.get("height"):
                        raise ValueError("no video stream")
                except Exception as e:
                    raise ImportError("This video cannot be opened; choose a valid MP4, MOV or WebM") from e
            if not is_video:
                try:
                    pl.load_rgb(f)
                except Exception as e:
                    f.unlink(missing_ok=True)
                    raise ImportError("This image cannot be opened; choose a valid PNG, JPEG or WebP") from e
            row = old or record(c.out, name, data, None, user["id"], job_id)
            _update(c.out, row["id"], status="PREPARING", file=str(f), kind="video" if is_video else "sheet", sheet=aid)
            if is_video and not alone:
                _update(c.out, row["id"], generation=f"G{gid:03d}")
                if not resume_video:
                    gates.attach_video(c.out, gid, aid, data, name, custom=True)
                work = lambda: gates.slice_video(c.out, gid, aid, c.cfg, c.pace)
            else:
                subject = subject_of(name, prompt)
                # A crash after start but before the ledger update is recovered by source path.
                previous = old.get("generation") if old else None
                if old and not previous and old.get("file"):
                    for n in pl.list_ids(c.out):
                        try:
                            saved = pl.read_result(c.out, n)
                        except pl.PipelineError as e:
                            if e.code == 404:
                                continue
                            raise
                        if old["file"] in (saved.get("source", {}).get("sheet_path"), saved.get("source", {}).get("video_path")):
                            previous = f"G{n:03d}"
                            break
                if previous:
                    gid = generation_id(previous)
                else:
                    pick = sources.Pick(subject=re.sub(r"\W+", "_", subject.lower()).strip("_")[:40] or "import", subject_id="import", variant=1, n_variants=1,
                                        sheet=first_frame(f) if alone else f, video=f if alone else None)
                    tok = pl.OWNER.set(user["id"])
                    try:
                        from ..generation import prompter
                        plan = prompter.expand(subject, tuple(map(int, grid.split("x")))) if grid else None
                        gid = pl.start(subject, c.out, c.inp, pick=pick, regen_plan=plan)
                    finally:
                        pl.OWNER.reset(tok)

                def work():
                    pl.run_stills(c.out, gid, c.cfg, c.pace)
                    if alone and not pl.read_result(c.out, gid).get("error"):      # the stills are cut: animate them from the same video, then wait for the person
                        pl.run_animate(c.out, gid, c.cfg, "pack", None, c.pace)
            _update(c.out, row["id"], generation=f"G{gid:03d}", status="PROCESSING")
            ctx = contextvars.copy_context()
            ctx.run(pl.OWNER.set, user["id"])

            def run():
                try:
                    work()
                    res = pl.read_result(c.out, gid)
                    _update(c.out, row["id"], status="FAILED" if res.get("error") else "READY", error=res.get("error"))
                except Exception as e:
                    _update(c.out, row["id"], status="FAILED", error=str(e)[:300])
                finally:
                    c.lock.release()
            threading.Thread(target=ctx.run, args=(run,), daemon=True).start()
            handed_off = True
            from ..runtime import activity
            activity.say(f"imported {Path(name).name} as G{gid:03d} ({'video' if is_video else 'sheet'})")
            return 202, {"id": gid, "kind": "video" if is_video else "sheet", "import": row["id"], **({"sheet": aid} if is_video and not alone else {"subject": subject})}
        except Exception as e:
            if row:
                _update(c.out, row["id"], status="FAILED", error=str(e)[:300])
            raise
        finally:
            if not handed_off:
                c.lock.release()


def history(c, size: int = 40) -> dict:
    from .. import providers as _providers
    from ..generation import higgsfield as hf
    if not 1 <= size <= 100:
        raise ImportError("size must be 1 to 100")
    if _providers.current().name != "higgsfield":
        raise ImportError("Provider history needs STICKERLOOM_PROVIDER=higgsfield", 503)
    if not hf.available():
        raise ImportError("The Higgsfield CLI is not installed or signed in", 503)
    rows = []
    try:
        for kind in ("image", "video"):
            got = hf._json(["generate", "list", f"--{kind}", "--size", str(size), "--json"], timeout=90)
            if not isinstance(got, list) or any(not isinstance(r, dict) for r in got):
                raise ImportError("Higgsfield returned an unreadable job list", 502)
            for r in got:
                params = r.get("params") if isinstance(r.get("params"), dict) else {}
                rows.append({"id": r.get("id"), "kind": kind, "model": r.get("job_type"), "status": r.get("status"), "created": r.get("created_at"),
                             "prompt": str(params.get("prompt") or "")[:200], "thumb": r.get("min_result_url") or r.get("thumbnail_url") or r.get("result_url"), "known": known(c.out, job_id=r.get("id"))})
    except hf.HiggsError as e:
        raise ImportError(str(e), 502) from e
    rows.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    return {"jobs": rows}


def import_job(c, user: dict, job: str, **options) -> tuple[int, dict]:
    from .. import providers as _providers
    from ..generation import higgsfield as hf
    from ..media.video_project import MAX_UPLOAD
    from urllib.parse import urlparse
    import tempfile
    if _providers.current().name != "higgsfield":
        raise ImportError("Provider import needs STICKERLOOM_PROVIDER=higgsfield", 503)
    if not JOB_ID.fullmatch(job or ""):
        raise ImportError("id must be a Higgsfield job id")
    local_job = options.pop("local_job", None)
    hit = known(c.out, job_id=job)
    if hit and not options.get("retry") and not local_job:
        return 200, {"duplicate": True, **hit}
    try:
        g = hf._json(["generate", "get", job, "--json"], timeout=60)
        g = g[0] if isinstance(g, list) and len(g) == 1 else g
        if not isinstance(g, dict):
            raise ImportError("Higgsfield returned an unreadable job", 502)
        url = g.get("result_url")
        if g.get("status") != "completed" or not isinstance(url, str) or not url:
            raise ImportError("That Higgsfield job has no finished file yet", 409)
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ImportError("Higgsfield returned an invalid result URL", 502)
        ext = Path(parsed.path).suffix.lower()
        if ext not in IMAGE + VIDEO:
            raise ImportError("Higgsfield result must be a supported image or video", 400)
        if ext in VIDEO and not local_job and not (hit and options.get("retry") and hit.get("import")) and (options.get("generation") or options.get("sheet")):
            destination(c, options.get("generation"), options.get("sheet"))
        with tempfile.TemporaryDirectory(prefix="stickerloom-hf-import-") as tmp:
            f = Path(tmp) / ("result" + ext)
            hf.download(url, f, max_bytes=MAX_UPLOAD)
            data = f.read_bytes()
    except hf.HiggsError as e:
        raise ImportError(str(e), 413 if "too large" in str(e) else 502) from e
    if local_job:
        options["job"] = local_job
    return import_file(c, user, f"hf_{job}{ext}", data, job_id=job, **options)


def subject_of(name: str, prompt: str = "") -> str:
    """What the batch is about: the person's words, else the file's name without Higgsfield's date and job id."""
    if str(prompt or "").strip():
        return " ".join(str(prompt).split())[:120]
    s = JOB_ID.sub(" ", Path(str(name or "")).stem)
    s = re.sub(r"[_-]+", " ", s)
    s = re.sub(r"\bhf\b|\b\d{6,8}\b", " ", s, flags=re.I)
    return " ".join(s.split())[:120] or "imported sheet"
