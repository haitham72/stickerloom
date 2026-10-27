"""Generation lifecycle: 7 stages, each a real step with an event line in out/G00N/events.jsonl.

requested -> sheet_picked -> keyed -> sliced -> video_requested -> video_picked -> video_sliced
Used by the CLI and by the console. Picking steps are lookups over prepared files; the rest is real processing."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from . import sources
from ..generation import prompter, tasks
from ..engine import chroma, verify
from ..engine.config import EngineConfig
from ..engine.grid import detect_grid, split_grid
from ..engine.render import apply_edge
from ..engine.sheet import encode_static, key_sheet, slice_cells, stitch_keyed
from ..engine.video import AnimationResult, AnimCache, pick_clip, process_clips, process_video

STAGES = ["requested", "sheet_picked", "keyed", "sliced", "video_requested", "video_picked", "video_sliced",
          "plan_reviewed", "stills_reviewed", "video_sheet_built", "video_sheet_reviewed", "video_returned", "anim_reviewed", "pack_final"]
SLICED = STAGES.index("sliced")     # every stage from here on has the stills on disk


class PipelineError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# ---------- storage ----------
_DIRS: dict = {}     # (base, id) -> the labelled folder found last time; checked on every use, so a move or a removal is never served stale


def labelled_dir(base: Path, ident: str) -> Path:
    """The folder of `ident` (G110, J057) under `base`: the bare `G110/` of a batch made before 2026-10-04, else its labelled
    `G111-dog_as_banana-20261005T093012/` (runtime/names.folder). The bare path when neither exists yet, so a caller that only builds a path
    (an older batch, a test fixture) gets exactly what it got before."""
    base = Path(base)
    bare = base / ident
    if bare.is_dir():
        return bare
    hit = _DIRS.get((str(base), ident))
    if hit is not None and hit.is_dir():
        return hit
    found = sorted(p for p in base.glob(ident + "-*") if p.is_dir()) if base.is_dir() else []
    if found:
        _DIRS[(str(base), ident)] = found[0]
        return found[0]
    return bare


def gen_dir(out: Path, gid: int) -> Path:
    return labelled_dir(out, f"G{gid:03d}")


def out_path(out: Path, rel: str) -> Path:
    """`G111/slices/x.png` (a `/out/...` URL or an asset key, which always name a batch by its id) -> the file under its labelled folder.
    Not resolved and not checked: the caller still confirms the result stays inside `out`."""
    import posixpath
    rel = posixpath.normpath(str(rel).replace("\\", "/"))     # `G001/../G002/x` names G002: resolve the dots before the id is looked up
    head, sep, rest = rel.partition("/")
    if re.fullmatch(r"G\d{3,}", head):
        return gen_dir(out, int(head[1:])) / rest if sep else gen_dir(out, int(head[1:]))
    return Path(out) / rel


def _ids_in(d: Path) -> list[int]:
    from ..runtime import names
    return sorted({int(i[1:]) for x in d.iterdir() if (i := names.folder_id(x.name)) and i[0] == "G" and x.is_dir()})


def list_ids(out: Path) -> list[int]:
    if not out.is_dir():
        return []
    return _ids_in(out)


def trash_batches_dir(out: Path) -> Path:
    return Path(out) / "trash" / "batches"


def removed_ids(out: Path) -> list[int]:
    """The batches in the trash (flow/batches.py): their numbers stay taken, so a Restore can never collide with a new batch."""
    d = trash_batches_dir(out)
    if not d.is_dir():
        return []
    return _ids_in(d)


def purge_ledger(out: Path) -> Path:
    """`out/trash/purged.jsonl`: one line per purge of a batch or a pack (flow/purge.py), append-only. It is also the memory that keeps a purged number taken."""
    return Path(out) / "trash" / "purged.jsonl"


def purged_ids(out: Path) -> list[int]:
    """The batch numbers that were purged for good (flow/purge.py): the folder is gone, the number stays taken (a new batch must never reuse it: the database keeps its history)."""
    f, ids = purge_ledger(out), set()
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("kind") == "batch" and str(r.get("number") or "").isdigit():
            ids.add(int(r["number"]))
    return sorted(ids)


def next_gid(out: Path) -> int:
    return max([0, *list_ids(out), *removed_ids(out), *purged_ids(out)]) + 1


def _batch_dirs(out: Path) -> list[tuple[int, Path]]:
    """Every live G### folder (bare `G110/` or labelled `G111-<slug>-<time>/`) as (number, path)."""
    from ..runtime import names
    out = Path(out)
    if not out.is_dir():
        return []
    rows = []
    for x in out.iterdir():
        if not x.is_dir():
            continue
        i = names.folder_id(x.name)
        if i and i[0] == "G":
            try:
                rows.append((int(i[1:]), x))
            except ValueError:
                continue
    return sorted(rows)


def orphan_batches(out: Path) -> list[dict]:
    """Live G### folders with no `result.json`: never a batch (Remove refuses them, the Studio skips them).

    A zero-file one is stale clutter (an allocation or a loop that left empty dirs behind); one WITH files
    but no result is an interrupted start: it is reported here and never auto-removed."""
    rows = []
    for gid, d in _batch_dirs(Path(out)):
        if (d / "result.json").is_file():
            continue
        try:
            files = sum(1 for _ in d.rglob("*") if _.is_file())
        except OSError:
            files = 0
        rows.append({"id": f"G{gid:03d}", "number": gid, "path": str(d), "files": files})
    return rows


def prune_orphans(out: Path) -> dict:
    """Delete the zero-file orphan batch folders (they hold no paid work, no result, and no database rows to purge).

    A folder that holds files but no `result.json` is KEPT and reported (an interrupted start: a human decides).
    A folder named by an open job (REQUESTED/CLAIMED) is kept too. Idempotent: re-running finds nothing.
    Returns {removed: [ids], kept: [{id, why}]}."""
    from ..generation import jobs as _jobs
    out = Path(out)
    open_gids = set()
    try:
        for j in _jobs.list(out):
            if j.get("status") in ("REQUESTED", "CLAIMED"):
                g = j.get("generation")
                try:
                    open_gids.add(int(str(g).lstrip("Gg")) if g not in (None, "") else -1)
                except ValueError:
                    continue
    except Exception:
        pass
    removed, kept = [], []
    for row in orphan_batches(out):
        gid, d, files = row["number"], Path(row["path"]), row["files"]
        if gid in open_gids:
            kept.append({"id": row["id"], "why": "an open job still names it; wait for the job, then prune again"})
            continue
        if files:
            kept.append({"id": row["id"], "why": f"it holds {files} file(s) but no result.json (an interrupted start); decide by hand, it never auto-deletes"})
            continue
        try:
            shutil.rmtree(d, ignore_errors=True)
            for k, v in list(_DIRS.items()):
                if v == d or (isinstance(v, Path) and str(v) == str(d)):
                    _DIRS.pop(k, None)
        except OSError as e:
            kept.append({"id": row["id"], "why": str(e)[:200]})
            continue
        removed.append(row["id"])
    return {"removed": removed, "kept": kept}


import contextvars

from ..runtime.principal import principal_id

OWNER = contextvars.ContextVar("stickerloom_owner", default=principal_id())     # who is acting: the one local principal, copied into the threads a request starts


def current_owner() -> str:
    return OWNER.get()


_IO_LOCK = threading.RLock()     # one reader or writer of result.json at a time inside this process


def serialized(fn):
    """A read-modify-write of result.json that must not interleave with another one in this process (two reviews of different stickers arriving together used to read the same
    snapshot, and the second write dropped the first decision): the whole call holds the lock (it is re-entrant, so a serialized function may call another)."""
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        with _IO_LOCK:
            return fn(*a, **k)
    return run


def _atomic_write(path: Path, data: bytes) -> None:
    """tmp file + os.replace. On Windows the replace fails with WinError 5 while anything (a polling request, an
    antivirus scan) has the target open, so in-process readers take the same lock and a foreign holder is retried."""
    from ..runtime import atomic
    with _IO_LOCK:
        atomic.write_bytes(path, data)            # a temp file unique to this write: two processes can never meet on one `.tmp`


def normalise(res: dict) -> dict:
    """Results written before 1F lack the review fields: give them empty ones so every reader can rely on the shape."""
    res.setdefault("reviews", {"plan": None, "video_sheet": {}, "pack": None})
    res.setdefault("video_sheets", [])
    res.setdefault("verify", {})
    res.setdefault("erode_px", EngineConfig().erode_px)   # generations written before the erosion setting
    for st in res["stickers"]:
        st.setdefault("tags", [st["key"]])
        st.setdefault("review", {"still": "BLOCKED" if st["status"] == "FAILED" else "PENDING", "anim": "NONE"})
        st.setdefault("history", [])
    return res


def read_result(out: Path, gid: int) -> dict:
    p = gen_dir(out, gid) / "result.json"
    if not p.exists():
        raise PipelineError(f"No generation G{gid:03d}", 404)
    with _IO_LOCK:
        from ..runtime import atomic
        return normalise(json.loads(atomic.read_text(p)))


def write_result(out: Path, gid: int, res: dict) -> None:
    _atomic_write(gen_dir(out, gid) / "result.json", json.dumps(res, indent=2, ensure_ascii=False).encode())
    try:  # Phase 3A write-through: Postgres mirrors the file store when it is up; never raises
        from ..store import sync
        sync.sync_result(out, gid, res)
    except Exception:
        pass


def read_events(out: Path, gid: int) -> list[dict]:
    p = gen_dir(out, gid) / "events.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def emit(out: Path, gid: int, stage: str, status: str, ms: int = 0, detail=None, actor: str | None = None, decision: str | None = None) -> None:
    ev = {"ts": round(time.time(), 3), "stage": stage, "status": status, "ms": ms, "detail": detail}
    if actor:                                  # gate decisions: who decided (python | human | vlm) and what
        ev["actor"], ev["decision"] = actor, decision
    line = json.dumps(ev, ensure_ascii=False)
    with open(gen_dir(out, gid) / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:                                       # one line in the terminal for a result or a failure (runtime/activity.py)
        from ..runtime import activity as _activity
        _activity.event(gid, ev)
    except Exception:
        pass
    try:                                       # the same event on the Redis stream (Phase 5 names); events.jsonl stays the record
        from ..runtime import events as _events
        _events.publish(out, gid, ev)
    except Exception:
        pass


def latest_id(out: Path) -> int:
    ids = list_ids(out)
    if not ids:
        raise PipelineError("No generations yet. Run: create \"<prompt>\"", 404)
    return ids[-1]


def media_name(kind: str, gid: int, task_slug: str, key: str, created: float | None = None) -> str:
    """The file stem of a sticker: `{media}-{subject}-{action}-{UTC time}-{fingerprint}` (runtime/names.py, 2026-10-02), e.g. img-teddy_bear_school-with_a_book-20261002T133320-a3f9c1.
    The time is the batch's creation time and the fingerprint carries the batch number, so no two files of any batch ever share a name. Batches made before this kept their
    `img-001-teddy_bear_school-teddy_bear_with_a_book` names (never renamed); `names.parse` reads both."""
    from ..runtime import names
    return names.build(kind, task_slug, names.action_of(task_slug, key), when=created, seed=f"G{gid:03d}|{key}")


def load_rgb(path: Path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PipelineError(f"Cannot decode {path.name}", 422)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


class Stage:
    """Context manager: emits start / done|error with timing, plus optional pacing for live demos."""
    def __init__(self, out, gid, stage, pace=0.0, detail=None):
        self.out, self.gid, self.stage, self.pace, self.detail = out, gid, stage, pace, detail

    def __enter__(self):
        self.t0 = time.perf_counter()
        emit(self.out, self.gid, self.stage, "start", 0, self.detail)
        return self

    def __exit__(self, et, ev, tb):
        ms = int((time.perf_counter() - self.t0) * 1000)
        if ev:
            from ..obs.scrub import scrub_paths
            emit(self.out, self.gid, self.stage, "error", ms, scrub_paths(str(ev))[:300])      # events.jsonl, the SSE stream and tracing all carry this text
            return False
        if self.pace:
            time.sleep(self.pace)
        emit(self.out, self.gid, self.stage, "done", ms, getattr(self, "result", None))


# ---------- create ----------
def next_variant(out: Path, subject: str, n: int) -> int:
    """Each new generation of a subject takes the next prepared variant (001 -> 002 -> ... -> wrap), like a fresh generation."""
    for gid in reversed(list_ids(out)):
        try:
            r = read_result(out, gid)
        except Exception:
            continue
        if r["source"]["subject"] == subject:
            return r["source"]["variant"] % n + 1
    return 1


def generations_by_folder(out: Path) -> dict:
    """{(subject, folder number): ['G028', ...]} - which generations were made from which watch folder (oldest first)."""
    found: dict = {}
    for gid in list_ids(out):
        try:
            s = read_result(out, gid)["source"]
        except Exception:
            continue
        found.setdefault((s["subject"], s.get("subject_id")), []).append(f"G{gid:03d}")
    return found


def list_inputs(inp: Path) -> list[dict]:
    return [{"subject": subj, "variants": [
        {"variant": p.variant, "folder": p.subject_id, "sheet": p.sheet.name, "video": p.video.name if p.video else None,
         "clips": len(p.clips), "clips_dup_of": p.clips_dup_of, "pairing": p.pairing, "plan": p.plan.name if p.plan else None} for p in picks]}
        for subj, picks in sources.scan(inp).items()]


def _allocate(out: Path, task_slug: str, created: float) -> tuple[int, Path]:
    """The next number and its labelled folder, `G111-dog_as_banana-20261005T093012/` (with `source/` made). A bare `G111/` could only be made once
    (the second mkdir failed); labels differ, so a race with another process is caught by looking: two folders for one number -> ours goes, take the next."""
    from ..runtime import names
    with _IO_LOCK:
        for _ in range(20):
            gid = next_gid(out)
            d = out / names.folder(f"G{gid:03d}", task_slug, when=created)
            try:
                (d / "source").mkdir(parents=True)
            except FileExistsError:
                continue
            if [x for x in out.glob(f"G{gid:03d}*") if x.is_dir() and names.folder_id(x.name) == f"G{gid:03d}" and x != d]:
                shutil.rmtree(d, ignore_errors=True)
                continue
            return gid, d
    raise PipelineError("could not allocate a batch number: another process keeps taking them", 503)


def start(prompt: str, out: Path, inp: Path, variant: int | None = None, pick: sources.Pick | None = None, parent: int | None = None,
          grid: tuple | None = None, task: dict | None = None, regen_of: str | None = None, regen_plan: dict | None = None,
          outline: int | None = None, erode: int | None = None, kind: str | None = None, prepared: bool | None = None, sheet_model: str | None = None) -> int:
    """Synchronous part: match source, run the prompter, allocate G00N. Returns the id fast.
    grid: the user's choice (3x3 / 2x2). A prepared sheet's own layout wins: it is measured from its gutters.
    kind: "particles" marks the batch as a particle sheet (docs/architecture.md ("Reactions")): its layout is the plan's, it is cut into EXACT equal cells and no sticker rule can stop a cell (verify.PARTICLE_KEEP).
    prepared: this batch serves a prepared watch-folder set (its real source, recorded in result.json, 0 credits, no job or ledger line).
    None (the default) detects it: the sheet lives under the watch folder. A live provider sheet and a hand import do not."""
    if kind not in (None, "particles"):
        raise PipelineError("kind must be 'particles' or left out")
    if outline is not None and not 0 <= int(outline) <= 40:
        raise PipelineError("outline must be 0 (none) to 40 px")
    if erode is not None and not 0 <= int(erode) <= 8:
        raise PipelineError("erode must be 0 (none) to 8 px")
    if pick is None:
        base = sources.find(inp, prompt, 1, grid)                    # only a prepared sheet drawn in the asked grid (None: any)
        if base:
            picks = sources.scan(inp)[base.subject]
            pick = picks[min(max(variant or 1, 1), len(picks)) - 1]      # explicit folder, else the first: it never advances by itself
    if not pick:
        raise PipelineError(f"No prepared set for that. Try: {', '.join(sources.known_subjects(inp)) or '(none found)'}", 404)
    if prepared is None:
        try:                                        # a live provider sheet and a hand import live under out/, never under the watch folder
            prepared = Path(pick.sheet).resolve().is_relative_to(Path(inp).resolve())
        except (OSError, ValueError):
            prepared = False
    if regen_plan:  # 1x1 regeneration of one sticker: the plan is that sticker's own (key, tags, emoji) in a single-cell template
        plan = regen_plan
    elif task and task.get("plan"):   # a task reserved in the Inbox: its saved plan (template + slots) is the plan
        plan = prompter.validate_plan(json.loads(json.dumps(task["plan"])))
    elif pick.plan:   # hand-written prompts that match the sheet actually generated
        try:
            plan = prompter.validate_plan(json.loads(pick.plan.read_text(encoding="utf-8")))
        except (ValueError, OSError) as e:
            raise PipelineError(f"Bad prompts file {pick.plan.name}: {e}", 422)
    else:
        measured = detect_grid(load_rgb(pick.sheet))
        plan = prompter.expand(prompt, measured if measured in prompter.GRIDS else tuple(grid or (2, 2)))
    out.mkdir(parents=True, exist_ok=True)
    created = round(time.time(), 3)
    gid, d = _allocate(out, plan["task_slug"], created)
    (d / "slices").mkdir()
    (d / "prompts.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    res = {
        "generation_id": f"G{gid:03d}", "number": gid, "owner": current_owner(), "created": created, "parent": parent, "prompt": prompt, "task": plan["task"], "task_slug": plan["task_slug"],
        "source": {"subject": pick.subject, "subject_id": pick.subject_id, "variant": pick.variant,
                   "n_variants": pick.n_variants, "sheet": pick.sheet.name, "video": pick.video.name if pick.video else None,
                   "has_video": pick.has_video, "pairing": pick.pairing, "clips_dup_of": pick.clips_dup_of,
                   "prepared": bool(prepared),
                   "clips": {str(n): {f: str(p) for f, p in d.items()} for n, d in pick.clips.items()}, "sheet_path": str(pick.sheet),
                   "video_path": str(pick.video) if pick.video else None},
        "grid": plan["grid"], "stage": "requested", "error": None, "plan_source": "task " + task["id"] if task else pick.plan.name if pick.plan else "stub (generation/prompter.py)",
        "sheet_prompt": plan.get("sheet_prompt", ""), "video_prompt": plan.get("video_prompt", ""), "custom_prompts": sorted(plan.get("custom") or {}),
        "template_id": plan.get("template_id"), "template_version": plan.get("template_version"), "slots": plan.get("slots"),
        "task_id": task["id"] if task else None, "name_key": plan["task_slug"], "regen_of": regen_of,
        **({"kind": kind} if kind else {}),                # only a particle sheet says so: every other batch is written exactly as before
        **({"sheet_model": sheet_model} if sheet_model else {}),     # the image model that drew the sheet (a live job); the Studio shows it as "current", next to the picker for the next one
        "verify_version": verify.VERIFY_VERSION,
        "outline_px": int(outline) if outline is not None else EngineConfig().outline_px,    # the white die-cut stroke is a choice, kept with the generation
        "erode_px": int(erode) if erode is not None else EngineConfig().erode_px,          # fringe trim, kept with the generation; 0 = none
        "reviews": {"plan": (task or {}).get("plan_review"), "video_sheet": {}, "pack": None}, "video_sheets": [], "verify": {},
        "stickers": [new_sticker(gid, plan["task_slug"], s, created) for s in plan["stickers"]],
    }
    write_result(out, gid, res)
    emit(out, gid, "requested", "done", 0, {"prompt": prompt, "task_slug": plan["task_slug"]})
    return gid


def approve_plan(out: Path, gid: int, note: str) -> None:
    """Pressing Generate on a prepared folder is the human's G1 decision (the same as saving a task in the Inbox)."""
    res = read_result(out, gid)
    if not res["reviews"].get("plan"):
        res["reviews"]["plan"] = {"decision": "APPROVE", "by": "human", "ts": round(time.time(), 3), "note": note}
        write_result(out, gid, res)


def new_sticker(gid: int, task_slug: str, s: dict, created: float | None = None) -> dict:
    return {"index": s["index"], "key": s["key"], "tags": s.get("tags") or [s["key"]], "emoji": s["emoji"], "prompt": s["prompt"],
            "name": media_name("img", gid, task_slug, s["key"], created),
            "status": "PENDING", "reason": None, "report": [], "metrics": {}, "png": None,
            "anim_status": "NOT_REQUESTED", "anim_reason": None, "anim_metrics": {}, "webm": None,
            "review": {"still": "PENDING", "anim": "NONE"}, "history": []}


@serialized
def set_titles(out: Path, gid: int, titles: dict, actor: str = "human", via: str = "") -> dict:
    """Give stickers a display TITLE ({index: "Happy boy with a red heart"}): what people read in the chat, the pack and the library. The file name and the key never change (the database,
    the pool and the pack use them). Every change is a history line of the sticker (`naming`). Returns {index: title} of what changed."""
    res = read_result(out, gid)
    changed = {}
    for k, v in titles.items():
        i = int(k)
        title = " ".join(str(v or "").split())[:60]
        if not title or not 1 <= i <= len(res["stickers"]):
            continue
        st = res["stickers"][i - 1]
        if st.get("title") == title:
            continue
        old = st.get("title")
        st["title"] = title
        st.pop("title_proposal", None)
        hist(st, "naming", actor, "TITLE", title, detail={"was": old, "via": via})
        changed[i] = title
    if changed:
        write_result(out, gid, res)
    return changed


def hist(st: dict, stage: str, actor: str, decision: str, reason: str | None = None, ref: str | None = None, detail=None) -> None:
    """One line of a sticker's path through the golden path (Phase 2: one `reviews` row)."""
    st.setdefault("history", []).append({"ts": round(time.time(), 3), "stage": stage, "actor": actor, "decision": decision,
                                         "reason": reason, "ref": ref, "detail": detail})


def block_detail(report: list) -> dict | None:
    """The first failing BLOCK check of a report (dicts as stored in result.json), trimmed for a history line."""
    c = next((c for c in report if not c.get("ok") and c.get("severity", "BLOCK") == "BLOCK"), None)
    if not c:
        return None
    return {"check": c["name"], "value": c.get("value"), "limit": c.get("limit"), "note": c.get("detail"), "data": c.get("data")}


def cfg_for(res: dict, cfg: EngineConfig) -> EngineConfig:
    """The engine config of one generation: the server's, with the edge finish that was chosen for it
    (outline_px 0 = no stroke, erode_px 0 = no trim) and its Motion size switch (keep_size -> anim_keep_size)."""
    kw = {}
    if res.get("outline_px") is not None:
        kw["outline_px"] = int(res["outline_px"])
    if res.get("erode_px") is not None:
        kw["erode_px"] = int(res["erode_px"])
    if res.get("anim_scale") is not None:     # the batch's Video size: every animation of the batch (a sticker's own value wins, cell_cfgs)
        kw["anim_scale"] = float(res["anim_scale"])
    if res.get("keep_size") is not None:      # the batch's Motion switch: keep the approved still's size (True) or fit the whole motion (False)
        kw["anim_keep_size"] = bool(res["keep_size"])
    if res.get("same_scale"):                 # ... or Same scale, which wins: every sticker at one scale from its cell
        kw["anim_same_scale"] = True
    if res.get("resize") is not None:         # the batch's Resize box, which wins over both: on = each sticker by its hot spot, off = one scale
        kw["anim_hotspot"], kw["anim_same_scale"] = bool(res["resize"]), not res["resize"]
    if res.get("key_colour") == "blue":       # the sheet came with a blue screen (green is the default and is never written): keyer, despill and video sheet follow it
        kw["chroma"] = "blue"
    return cfg if not kw else replace(cfg, **kw)


# A sheet that fails a check of the sheet stage is NOT thrown away. Two touching characters used to cost eight good stickers. Now only a file that does not open stops the cut; a layout problem (the grid it found is not the grid
# planned, a small sheet) is recorded on every sticker as a warning and the sheet is cut anyway: every cell is then judged on its OWN checks and a human decides at G2. A sheet
# with no key screen at all (`background_is_key`) still stops, because every cell would come out wrong, but ONE click ("Cut it anyway", `recut`) cuts it.
SHEET_FATAL = {"sheet_decodes"}                  # nothing can be cut from a file that does not open
SHEET_PROCEED = {"grid_detected", "sheet_size"}  # layout problems: cut anyway, say so
PARTICLE_INSET = 0.02                            # a particle sheet: this share of a cell's shorter side is wiped at every cell border (an image model draws white divider lines between cells; the prompt keeps 20 % margins, so no piece loses anything)


def run_stills(out: Path, gid: int, cfg: EngineConfig, pace: float = 0.0) -> None:
    res = read_result(out, gid)
    base = cfg
    cfg = cfg_for(res, cfg)
    d = gen_dir(out, gid)
    try:
        blocked = None
        with Stage(out, gid, "sheet_picked", pace) as s:
            src = Path(res["source"]["sheet_path"])
            dest = d / "source" / f"sheet{src.suffix.lower()}"
            if not (dest.exists() and src.resolve() == dest.resolve()):          # a recut reads the copy this batch already holds
                shutil.copyfile(src, dest)
            res["source"]["sheet_copy"] = f"source/{dest.name}"
            rows, cols = res.get("grid") or (3, 3)
            asked = (res.get("slots") or {}).get("key_colour", "green")
            try:
                key, key_score = chroma.detect_key(load_rgb(dest), base.border_px, asked, base.min_key_diff)      # the screen the sheet REALLY has wins over the one that was asked for
            except Exception:
                key, key_score = asked, {}
            if key == "blue":
                res["key_colour"] = "blue"          # written only for blue: green is the default
            else:
                res.pop("key_colour", None)
            cfg = cfg_for(res, replace(base, chroma="blue") if key == "blue" else replace(base, chroma="green"))
            if res.get("task_id"):
                tasks.annotate_key(out, res["task_id"], key, asked)
            vin = {"data": dest.read_bytes(), "grid": (rows, cols), "chroma": cfg.chroma}
            if res.get("kind") == "particles":                       # a particle sheet: its layout is the plan's (no detection), cut into exact equal cells, no sticker rule blocks a cell
                vin["particles"] = True
            checks = verify.run("sheet", vin, cfg)                  # the sheet is judged on arrival, before anything is sliced
            res["verify"]["sheet"] = [c.to_dict() for c in checks]
            hard = [c for c in checks if not c.ok and c.severity == verify.BLOCK]
            forced = bool(res.get("cut_anyway"))                  # the human clicked "Cut it anyway"
            blocked = next((c for c in hard if c.id in SHEET_FATAL or (c.id not in SHEET_PROCEED and not forced)), None)
            issues = [c for c in hard if c is not blocked]
            res["sheet_issues"] = [{"check": c.id, "value": _json(c.value), "limit": _json(c.limit), "note": c.note, "forced": c.id not in SHEET_PROCEED} for c in issues]
            sheet = vin.get("rgb")
            if sheet is not None:
                res["source"]["sheet_size"] = [int(sheet.shape[1]), int(sheet.shape[0])]
            s.result = {"subject": res["source"]["subject"], "variant": res["source"]["variant"],
                        "of": res["source"]["n_variants"], "file": src.name,
                        "blocked": blocked.id if blocked else None, "cut_anyway": [c.id for c in issues],
                        "warnings": [c.id for c in checks if not c.ok and c.severity == verify.WARN]}
            res["stage"] = "sheet_picked"; write_result(out, gid, res)
        if blocked:
            with Stage(out, gid, "sliced", pace) as s:
                for st in res["stickers"]:
                    st.update(status="FAILED", reason=blocked.id, report=[blocked.to_dict()], metrics={"sheet_blocked": True})
                    st["review"]["still"] = "BLOCKED"
                    hist(st, "sheet", "python", "BLOCK", blocked.id, detail={"check": blocked.id, "value": _json(blocked.value), "limit": _json(blocked.limit), "note": blocked.note})
                s.result = {"ready": 0, "failed": len(res["stickers"]), "blocked": blocked.id}
                res["stage"] = "sliced"; write_result(out, gid, res)
            return
        with Stage(out, gid, "keyed", pace) as s:
            rects, ginfo = vin["rects"], vin["split_info"]
            particles = res.get("kind") == "particles"
            inset = max(4, round(PARTICLE_INSET * min(min(r[2], r[3]) for r in rects))) if particles else 0
            res["source"]["grid"] = {"rows": rows, "cols": cols, "rects": [list(r) for r in rects], **ginfo, **({"inset_px": inset} if particles else {})}
            cells = key_sheet(sheet, cfg, rects, inset, particles)
            ok, buf = cv2.imencode(".png", cv2.cvtColor(stitch_keyed(cells, sheet.shape), cv2.COLOR_RGBA2BGRA))
            (d / "source" / "keyed.png").write_bytes(buf.tobytes())
            res["source"]["keyed"] = "source/keyed.png"
            s.result = {"bg": cells[0].keyed.bg, "threshold": [round(c.keyed.t, 1) for c in cells],
                        "grid": f"{rows}x{cols}", "cut": ginfo["method"], "xs": ginfo["xs"], "ys": ginfo["ys"]}
            res["stage"] = "keyed"; write_result(out, gid, res)
        with Stage(out, gid, "sliced", pace) as s:
            (d / "source" / "plain").mkdir(exist_ok=True)
            waive = {st["index"]: set(st.get("still_override") or []) for st in res["stickers"]}          # blocks a human allowed, kept across every cut of this sheet
            for st, r in zip(res["stickers"], slice_cells(cells, cfg, waive=waive)):
                st.update(status=r.status, reason=r.reason, report=r.report.checks, metrics=r.metrics)
                for iss in res.get("sheet_issues") or []:        # the sheet had a layout problem and was cut anyway: this cell may be mis-cut, the human decides
                    hist(st, "sheet", "python", "WARN", iss["check"], detail={**iss, "cut_anyway": True})
                    st["metrics"] = {**(st.get("metrics") or {}), "sheet_issue": iss["check"]}
                if r.data:
                    st["png"] = f"slices/{st['name']}.{r.fmt}"
                    (d / st["png"]).write_bytes(r.data)
                if r.plain is not None:    # the outline-free twin the video sheet is built from
                    ok, buf = cv2.imencode(".png", cv2.cvtColor(r.plain, cv2.COLOR_RGBA2BGRA))
                    (d / "source" / "plain" / f"S{st['index']}.png").write_bytes(buf.tobytes())
                if r.status == "READY":
                    st["review"]["still"] = "PENDING"
                    hist(st, "sliced", "python", "PASS", detail={"warnings": r.metrics.get("warnings", [])})
                else:
                    st["review"]["still"] = "BLOCKED"
                    hist(st, "sliced", "python", "BLOCK", r.reason, detail=block_detail(r.report.checks))
                try:                       # sticker_ready / sticker_failed, one per sticker as it is cut (Redis stream, best effort)
                    from ..runtime import events as _events
                    _events.sticker_events(out, gid, [st])
                except Exception:
                    pass
            ready = sum(1 for st in res["stickers"] if st["status"] == "READY")
            s.result = {"ready": ready, "failed": len(res["stickers"]) - ready}
            res["stage"] = "sliced"; write_result(out, gid, res)
    except Exception as e:
        res["error"] = str(e)[:300]; write_result(out, gid, res)


def recut_check(out: Path, gid: int) -> None:
    """The refusals of `recut`, raised before anything is queued so the page gets a 409 and not a silent no-op."""
    res = read_result(out, gid)
    if res.get("stage") not in ("sliced", "stills_reviewed") and not res.get("error"):
        raise PipelineError("This batch is not waiting on its sheet.", 409)
    if res.get("video_sheets") or any((st.get("review") or {}).get("still") in ("APPROVED", "REJECTED") for st in res["stickers"]):
        raise PipelineError("Stickers of this batch were already decided: cutting again would lose those decisions.", 409)


def recut(out: Path, gid: int, cfg: EngineConfig, pace: float = 0.0, by: str = "human") -> None:
    """The human's "Cut it anyway": cut a batch whose sheet the sheet stage stopped (a sheet with no key screen, or an older batch from before layout problems were cut anyway), from the
    sheet that is stored, free of charge. Every sticker's history gets the click, every cell is then judged on its own checks and the human decides at G2 as usual. Refused once any
    sticker has a human decision or a video sheet was built from the stills (their decisions would be lost)."""
    recut_check(out, gid)
    res = read_result(out, gid)
    src = Path(res["source"].get("sheet_path") or "")
    kept = res["source"].get("sheet_copy")
    if not src.is_file() and kept and (gen_dir(out, gid) / kept).is_file():
        res["source"]["sheet_path"] = str(gen_dir(out, gid) / kept)             # the original download moved: the copy stored in the batch is the same file
    elif not src.is_file():
        raise PipelineError("The stored sheet is missing: make the sheet again.", 409)
    res["cut_anyway"] = True
    res.pop("error", None)
    for st in res["stickers"]:
        hist(st, "sheet", by, "APPROVE", "cut anyway", detail={"override": True})
    write_result(out, gid, res)
    run_stills(out, gid, cfg, pace)


def recut_as_particles(out: Path, gid: int, cfg: EngineConfig, pace: float = 0.0, by: str = "human") -> None:
    """A batch that was drawn as a particle sheet before batches knew they were particles (G100: a 2x2 sheet with a white divider cross, read as "3x3", three cells blocked `inside_cell`) is cut
    again as one, from the sheet it stores, free: exact equal cells, no sticker rule blocks a cell (docs/architecture.md ("Reactions")). Same refusals as `recut` (a decided sticker or a video sheet would lose
    its decision); a batch that already is a particle batch is refused (nothing would change)."""
    recut_check(out, gid)
    res = read_result(out, gid)
    if res.get("kind") == "particles":
        raise PipelineError("This batch was already cut as particles.", 409)
    src = Path(res["source"].get("sheet_path") or "")
    kept = res["source"].get("sheet_copy")
    if not src.is_file() and kept and (gen_dir(out, gid) / kept).is_file():
        res["source"]["sheet_path"] = str(gen_dir(out, gid) / kept)
    elif not src.is_file():
        raise PipelineError("The stored sheet is missing: make the sheet again.", 409)
    res["kind"] = "particles"
    res.pop("error", None)
    res.pop("cut_anyway", None)
    for st in res["stickers"]:
        hist(st, "sheet", by, "PASS", "cut again as particles", detail={"kind": "particles"})
    write_result(out, gid, res)
    run_stills(out, gid, cfg, pace)


@serialized
def recut_cells(out: Path, gid: int, indexes: list, cfg: EngineConfig) -> None:
    """Cut these cells of a batch again from the sheet it stores (free: no provider), the way `run_stills` cut them, but only these, with the blocks a human allowed (`still_override`) kept as
    warnings. Used by "Use it anyway" and by taking it back (flow/gates.py `allow_stills`). A cell that comes out READY enters G2 as pending (a decision it already has is kept); a cell that is
    blocked again goes back to BLOCKED and loses the file it had (it comes back, the same, with the next click). The pack-wide scale and the neighbours' hashes come from the whole sheet, so the
    cell comes out exactly as the first cut would have made it."""
    res = read_result(out, gid)
    d = gen_dir(out, gid)
    kept, grid = res["source"].get("sheet_copy"), res["source"].get("grid")
    if not kept or not grid or not (d / kept).is_file():
        raise PipelineError("The stored sheet is missing: make the sheet again.", 409)
    cfg = cfg_for(res, replace(cfg, chroma="blue" if res.get("key_colour") == "blue" else "green"))
    want = {int(i) for i in indexes}
    cells = key_sheet(load_rgb(d / kept), cfg, [tuple(r) for r in grid["rects"]], grid.get("inset_px", 0), res.get("kind") == "particles")      # a particle batch is cut again as one
    waive = {st["index"]: set(st.get("still_override") or []) for st in res["stickers"]}
    (d / "source" / "plain").mkdir(parents=True, exist_ok=True)
    for r in slice_cells(cells, cfg, waive=waive, only=want):
        st = res["stickers"][r.index - 1]
        old = st.get("png")
        st.update(status=r.status, reason=r.reason, report=r.report.checks, metrics=r.metrics, rendered_at=round(time.time(), 3))
        for iss in res.get("sheet_issues") or []:
            st["metrics"]["sheet_issue"] = iss["check"]
        plain = d / "source" / "plain" / f"S{st['index']}.png"
        if r.data:
            st["png"] = f"slices/{st['name']}.{r.fmt}"
            if old and old != st["png"]:
                (d / old).unlink(missing_ok=True)
            (d / st["png"]).write_bytes(r.data)
        elif old:
            (d / old).unlink(missing_ok=True)
            st["png"] = None
        if r.plain is not None:
            ok, buf = cv2.imencode(".png", cv2.cvtColor(r.plain, cv2.COLOR_RGBA2BGRA))
            plain.write_bytes(buf.tobytes())
        else:
            plain.unlink(missing_ok=True)
        if r.status == "READY":
            if st["review"]["still"] not in ("APPROVED", "REJECTED"):
                st["review"]["still"] = "PENDING"
            hist(st, "sliced", "python", "PASS", detail={"warnings": r.metrics.get("warnings", []), "waived": r.metrics.get("waived", [])})
        else:
            st["review"]["still"] = "BLOCKED"
            hist(st, "sliced", "python", "BLOCK", r.reason, detail=block_detail(r.report.checks))
        try:
            from ..runtime import events as _events
            _events.sticker_events(out, gid, [st])
        except Exception:
            pass
    write_result(out, gid, res)


def record_anim(d: Path, st: dict, r, ref: str, preserve_finished: bool = False) -> None:
    """Store one animation result on its sticker: status, metrics, file, review state and the verifier's history line."""
    if not preserve_finished:                         # any genuine new cut makes remembered verdicts of the old pixels stale
        if st.get("anim_finished") or st.get("webm"):
            st["anim_verdict_stale"] = True
        st.pop("anim_finished", None)
    st.update(anim_status=r.status, anim_reason=r.reason, anim_metrics=r.metrics, anim_report=r.report.checks)
    if r.data:
        st["webm"] = f"slices/{st['name'].replace('img-', 'vid-', 1)}.webm"
        (d / st["webm"]).write_bytes(r.data)
        st["anim_at"] = round(time.time(), 3)         # a new cut keeps the file name: the screens add ?v=anim_at so a browser never plays the old one
    oob = next((c for c in (r.report.checks if r.status == "READY" else []) if c["name"] == "inside_frame" and not c["ok"]), None)
    if oob:       # made, but the character leaves its cell: kept for inspection, blocked for review so it cannot be added
        st["review"]["anim"] = "BLOCKED"
        hist(st, "video", "python", "BLOCK", "inside_frame", ref, {"check": "inside_frame", "value": oob.get("value"), "limit": oob.get("limit"), "note": oob.get("detail"), "data": oob.get("data")})
    elif r.status == "READY":
        st["review"]["anim"] = "PENDING"
        hist(st, "video", "python", "PASS", ref=ref, detail={"warnings": r.metrics.get("warnings", [])})
    else:
        st["review"]["anim"] = "BLOCKED"
        hist(st, "video", "python", "BLOCK", r.reason, ref, block_detail(r.report.checks) or {"check": r.reason, "note": r.metrics.get("error")})


def _json(v):
    return v.item() if hasattr(v, "item") else v


# ---------- Studio edit: layers over a sticker, exported to BOTH its image and its animation ----------
def _original(d: Path, st: dict, kind: str) -> Path:
    """The file as the generator made it, kept once in source/orig/; every Studio edit starts from it, so re-editing never stacks on a previous edit."""
    rel = st["webm"] if kind == "webm" else st["png"]
    orig = d / "source" / "orig" / (Path(rel).name if kind == "webm" else f"S{st['index']}.png")
    if not orig.exists():
        orig.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(d / rel, orig)
    return orig


def studio_edit_open(out: Path, gid: int, index: int, projects, cfg: EngineConfig) -> dict:
    """Open (or reopen) the layered edit of a sticker that has an animation: a video project made from the ORIGINAL animation, whose layers are the
    edit. The project lives in the Studio; nothing is baked into the generator's files until `studio_edit_commit`."""
    res = read_result(out, gid)
    if not 1 <= index <= len(res["stickers"]):
        raise PipelineError(f"index 1..{len(res['stickers'])} required")
    st = res["stickers"][index - 1]
    if st.get("anim_status") != "READY" or not st.get("webm"):
        raise PipelineError("This sticker has no animation yet: edit its image from the Stickers view.", 409)
    pid = (st.get("edit") or {}).get("project")
    if pid:
        try:
            projects.get(pid)
            return {"project": pid, "reused": True}
        except Exception:
            pass
    d = gen_dir(out, gid)
    base = _original(d, st, "webm")
    proj = projects.create(base.read_bytes(), st["name"] + ".webm")
    fps = int(max(6, min(cfg.video_max_fps, round((st.get("anim_metrics") or {}).get("fps") or 24))))
    projects.update(proj["id"], {"video": {"fps": fps}, "name": st["key"].replace("_", " ")[:60]})
    st["edit"] = {"project": proj["id"], "layers": [], "at": None}
    write_result(out, gid, res)
    return {"project": proj["id"], "reused": False}


def studio_edit_commit(out: Path, gid: int, index: int, projects, overlays: dict, cfg: EngineConfig, lib=None) -> dict:
    """Export the Studio edit: the animation is re-rendered from the original with the layers (with their timing), and the same layers are composited
    onto the original image (all visible layers, timing ignored). Both files are replaced in place (same names, same S#); the originals stay in
    source/orig/; the pack copies of this sticker take the new files."""
    import numpy as np
    from ..media.library import LibraryError, decode_image, png_bytes, validate_render
    from ..media.video_project import _over
    res = read_result(out, gid)
    st = res["stickers"][index - 1]
    pid = (st.get("edit") or {}).get("project")
    if not pid:
        raise PipelineError("Open the edit first.", 409)
    try:
        proj = projects.get(pid)
        data, _mime, ext, info = projects.render(pid, "webm", overlays)
    except LibraryError as e:
        raise PipelineError(str(e), e.code if hasattr(e, "code") else 409)
    if ext != "webm" or len(data) > cfg.video_max_bytes:
        raise PipelineError(f"The edited animation is {len(data) // 1024} KB, over Telegram's {cfg.video_max_bytes // 1024} KB limit. Remove a layer or shorten it.", 409)
    d = gen_dir(out, gid)
    base_png = _original(d, st, "png")
    img = decode_image(base_png.read_bytes())
    drawn = []
    for L in sorted(proj["layers"], key=lambda x: x["zIndex"]):
        if L["visible"] and L["id"] in overlays:
            ov = decode_image(overlays[L["id"]])
            if ov.shape[:2] != img.shape[:2]:
                import cv2
                ov = cv2.resize(ov, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
            img = _over(img, ov)
            drawn.append({"type": L["type"], "text": (L.get("payload") or {}).get("text") or (L.get("payload") or {}).get("char"), "timing": L.get("timing")})
    try:
        body, ext2, _ = validate_render(png_bytes(img), cfg)
    except LibraryError as e:
        raise PipelineError(str(e), 409)
    (d / st["webm"]).write_bytes(data)
    cur = Path(st["png"])
    if ext2 != cur.suffix.lstrip("."):
        (d / st["png"]).unlink(missing_ok=True)
        st["png"] = str(cur.with_suffix("." + ext2)).replace("\\", "/")
    (d / st["png"]).write_bytes(body)
    now = round(time.time(), 3)
    st["edited"], st["edited_at"] = True, now
    st["edit"].update(layers=drawn, at=now)
    st["metrics"]["kb"] = max(1, len(body) // 1024)
    st.setdefault("anim_metrics", {})["kb"] = round(len(data) / 1024, 1)
    st["anim_metrics"]["edited"] = True
    hist(st, "video", "human", "EDIT", reason="edited in the Studio: image and animation", detail={"layers": drawn})
    write_result(out, gid, res)
    emit(out, gid, "still_edited", "done", 0, {"index": index, "studio": True, "layers": len(drawn)}, "human", "EDIT")
    refreshed = lib.refresh_from_generation(out, res["generation_id"], index, d / st["png"], d / st["webm"]) if lib else 0
    merged = rebuild_sheet(out, gid)                                    # the image of this edit goes back into the sheet too (same layout, same S#)
    return {"index": index, "edited_at": now, "layers": len(drawn), "kb_video": round(len(data) / 1024, 1), "kb_image": st["metrics"]["kb"], "pack_copies": refreshed, "sheet_fixed": merged}


# ---------- judge animations that were made before the border check existed ----------
def recheck_bounds(out: Path, gid: int, cfg: EngineConfig) -> dict:
    """Animations made by older code have no `inside_frame` verdict, so a character that leaves its cell was never flagged. This runs that one check on
    the SOURCE cell of every READY animation that lacks a verdict (decode + key only, no render, no encode, ~1 s for 9 cells), records it exactly as a
    new animation would be (the check in `anim_report`, a warning, `review.anim = BLOCKED` on a failure, a history line) and marks the sticker
    `bounds_checked`. A sticker the human already rejected stays rejected. Video-sheet slots have `inside_slot` and are skipped."""
    from concurrent.futures import ThreadPoolExecutor
    from ..engine import ffmpeg as ff
    from ..engine.grid import scale_rects
    from ..engine.video import bounds_check, keyed_cell, keyed_clip
    res = read_result(out, gid)
    cfg = cfg_for(res, cfg)
    src = res["source"]
    clips = {int(k): {f: Path(p) for f, p in v.items()} for k, v in src.get("clips", {}).items()}
    mp4 = Path(src["video_path"]) if src.get("video_path") and Path(src["video_path"]).is_file() else None
    todo = []
    for st in res["stickers"]:
        names = {c["name"] for c in st.get("anim_report") or []}
        how = (st.get("anim_metrics") or {}).get("source") or ""
        if st["anim_status"] == "READY" and not st.get("bounds_checked") and not names & {"inside_frame", "inside_slot"} and (how == "3x3 mp4" or how.startswith("clip:")):
            todo.append((st["index"], how))
    if not todo:
        return {"checked": 0, "flagged": []}
    geo = None
    if mp4 and any(h == "3x3 mp4" for _, h in todo):
        info = ff.probe(mp4)
        g = src.get("grid")
        W, H = info["width"], info["height"]
        vr = scale_rects(g["rects"], tuple(src["sheet_size"]), (W, H)) if g else [(c * (W // 3), r * (H // 3), W // 3, H // 3) for r in range(3) for c in range(3)]
        cap = cfg.video_max_fps if info["fps"] > cfg.video_max_fps else None
        fps = cfg.video_max_fps if cap else info["fps"]
        geo = (vr, cap, int(math.floor(cfg.video_max_seconds * fps)))

    def one(item):
        i, how = item
        try:
            if how == "3x3 mp4":
                if not geo:
                    return i, None
                keyed = keyed_cell(mp4, geo[0][i - 1], geo[1], geo[2], cfg)
            else:
                fmt = how.split(":", 1)[1]
                if i not in clips or fmt not in clips[i]:
                    return i, None
                keyed = keyed_clip(clips[i][fmt], cfg)
            return i, bounds_check(keyed, cfg)
        except Exception:
            return i, None
    with ThreadPoolExecutor(max_workers=max(1, min(cfg.anim_workers, len(todo)))) as ex:
        checks = list(ex.map(one, todo))
    flagged = []
    for i, chk in checks:
        if chk is None:
            continue
        st = res["stickers"][i - 1]
        st["bounds_checked"] = True
        st.setdefault("anim_report", []).append(chk.to_dict())
        if not chk.ok:
            st.setdefault("anim_metrics", {}).setdefault("warnings", []).append("inside_frame")
            if st["review"]["anim"] != "REJECTED":
                st["review"]["anim"] = "BLOCKED"
            hist(st, "video", "python", "BLOCK", "inside_frame", "bounds recheck",
                 {"check": "inside_frame", "value": chk.value, "limit": chk.limit, "note": chk.note, "data": chk.to_dict()["data"]})
            flagged.append(i)
    write_result(out, gid, res)
    emit(out, gid, "bounds_rechecked", "done", 0, {"checked": [i for i, c in checks if c is not None], "flagged": flagged})
    return {"checked": sum(c is not None for _, c in checks), "flagged": flagged}


def particle_sprites(out: Path, gid: int, res: dict | None = None) -> dict:
    """{cell index: PNG bytes} of the TIGHT sprites of a batch's cells (the UI/UX spec P6): each READY cell cropped out of the keyed sheet (`source/keyed.png`, native resolution) and trimmed to what is
    drawn. A particle set stores these, never the 512 px sticker of the cell (a sticker canvas is the deliverable of a sticker). {} when the keyed sheet is not there (the caller then keeps the
    slice file). Pure read."""
    from ..engine.particles import trim_sprite
    res = res or read_result(out, gid)
    d = gen_dir(out, gid)
    f = d / (res["source"].get("keyed") or "-")
    if not f.is_file():
        return {}
    im = cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None or im.ndim != 3 or im.shape[2] != 4:
        return {}
    sheet = cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)
    out_ = {}
    for st in res["stickers"]:
        c = (st.get("metrics") or {}).get("cell")
        if st.get("status") != "READY" or not c:
            continue
        x, y, w, h = (int(v) for v in c)
        sprite = trim_sprite(sheet[y:y + h, x:x + w])
        if sprite is not None:
            ok, buf = cv2.imencode(".png", cv2.cvtColor(sprite, cv2.COLOR_RGBA2BGRA))
            out_[int(st["index"])] = buf.tobytes()
    return out_


def rebuild_sheet(out: Path, gid: int) -> dict | None:
    """Merge the stickers a person edited back into ONE sheet (the UI/UX spec P8): `source/sheet_fixed.png` is the batch's own sheet with every edited cell replaced by its current picture, same
    layout, so each keeps its S# by position (a re-cut of it gives the same cells). The generator's raw sheet, the keyed sheet and every slice stay exactly as they are. Returns
    {"file", "cells"}, or None when nothing was edited or the sheet is not there (older batches). Never raises: the edit it follows is already saved."""
    from ..engine.sheet import merge_cells
    try:
        res = read_result(out, gid)
        src = res["source"]
        d = gen_dir(out, gid)
        rects = (src.get("grid") or {}).get("rects")
        raw = d / (src.get("sheet_copy") or "-")
        fixes, bgs = {}, {}
        for st in res["stickers"]:
            f = d / (st.get("png") or "-")
            if st.get("edited") and st["status"] == "READY" and f.is_file():
                im = cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_UNCHANGED)
                if im is not None and im.ndim == 3 and im.shape[2] == 4:
                    fixes[st["index"]] = cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)
                    if (st.get("metrics") or {}).get("bg"):
                        bgs[st["index"]] = tuple(st["metrics"]["bg"])
        if not fixes or not rects or not raw.is_file():
            return None
        merged = merge_cells(load_rgb(raw), rects, fixes, bgs)
        ok, buf = cv2.imencode(".png", cv2.cvtColor(merged, cv2.COLOR_RGB2BGR))
        (d / "source" / "sheet_fixed.png").write_bytes(buf.tobytes())
        res = read_result(out, gid)
        res["source"]["sheet_fixed"], res["source"]["sheet_fixed_cells"] = "source/sheet_fixed.png", sorted(fixes)
        write_result(out, gid, res)
        emit(out, gid, "sheet_merged", "done", 0, {"cells": sorted(fixes)}, "python", "MERGE")
        return {"file": "source/sheet_fixed.png", "cells": sorted(fixes)}
    except Exception:
        return None


# ---------- replace a still with a file edited outside the app (Photoshop), back into the same place ----------
def replace_still(out: Path, gid: int, index: int, png: bytes, cfg: EngineConfig, lib=None, undo: bool = False) -> dict:
    """Replace one still with a file edited outside the app, in place: same file name, same S#. The new picture is
    re-checked (the finished-image checks; the keying verdicts and any override stay as Python recorded them); the
    sticker returns to waiting for approval (a cell Python blocked stays blocked). Its animation is kept, but it is
    marked from-the-previous-picture (`anim_from_previous`) until it is animated again. The replaced file is kept
    once (`source/orig/replaced-S#.png`) for one undo. Rejection never deletes."""
    from ..media.library import LibraryError, decode_image, validate_render
    res = read_result(out, gid)
    if not 1 <= index <= len(res["stickers"]):
        raise PipelineError(f"index 1..{len(res['stickers'])} required")
    st = res["stickers"][index - 1]
    d = gen_dir(out, gid)
    prev = d / "source" / "orig" / f"replaced-S{index}.png"
    if undo:
        if not prev.is_file():
            raise PipelineError(f"S{index} has no replaced file to undo.", 409)
        body = prev.read_bytes()
        try:
            body, ext, _ = validate_render(body, cfg)
        except LibraryError as e:
            raise PipelineError(str(e), 409)
        f = d / st["png"]
        if ext != f.suffix.lstrip("."):
            f.unlink(missing_ok=True)
            st["png"] = str(Path(st["png"]).with_suffix("." + ext)).replace("\\", "/")
            f = d / st["png"]
        f.write_bytes(body)
        prev.unlink()
        st["edited"], st["edited_at"] = True, round(time.time(), 3)
        st["metrics"]["kb"] = max(1, len(body) // 1024)
        st.pop("anim_from_previous", None)                              # the picture is the original again, so is the animation
        if st["review"]["still"] != "BLOCKED":
            st["review"]["still"] = "PENDING"
        hist(st, "still", "human", "UNDO", reason="replaced file taken back: the previous picture is back")
        write_result(out, gid, res)
        emit(out, gid, "still_edited", "done", 0, {"index": index, "undo": True}, "human", "UNDO")
        refreshed = lib.refresh_from_generation(out, res["generation_id"], index, d / st["png"], None) if lib else 0
        merged = rebuild_sheet(out, gid)
        return {"index": index, "undone": True, "pack_copies": refreshed, "sheet_fixed": merged}
    if st["status"] != "READY" or not st.get("png"):
        raise PipelineError(f"S{index} is not a READY still.", 409)
    try:
        body, ext, _ = validate_render(png, cfg)
    except LibraryError as e:
        raise PipelineError(str(e), 409)
    rgba = decode_image(body)
    metrics: dict = {}
    report = verify.run("still", {"plain": rgba[..., 3], "metrics": metrics, "img": rgba,
                                  "render": lambda: rgba, "waive": st.get("still_override") or (),
                                  "encode": lambda img, c: (body, ext)}, cfg, only=APPEARANCE_CHECKS)
    block = next((c for c in report if not c.ok and c.severity == verify.BLOCK), None)
    if block:
        raise PipelineError(f"The edited file fails the {block.id} check ({block.note or block.limit}): fix it outside and try again.", 409)
    f = d / st["png"]
    prev.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(f, prev)                                        # one previous: a second replace moves it on
    if ext != f.suffix.lstrip("."):
        f.unlink(missing_ok=True)
        st["png"] = str(Path(st["png"]).with_suffix("." + ext)).replace("\\", "/")
        f = d / st["png"]
    f.write_bytes(body)
    st["edited"], st["edited_at"] = True, round(time.time(), 3)
    st["metrics"]["kb"] = max(1, len(body) // 1024)
    if st["review"]["still"] != "BLOCKED":
        st["review"]["still"] = "PENDING"                           # back to waiting for approval; an override stays possible
    if st.get("webm"):
        st["anim_from_previous"] = True                             # the animation is kept: it shows the previous picture until it is made again
    hist(st, "still", "human", "EDIT", reason="replaced with a file edited outside the app")
    write_result(out, gid, res)
    emit(out, gid, "still_edited", "done", 0, {"index": index, "replaced": True}, "human", "EDIT")
    refreshed = lib.refresh_from_generation(out, res["generation_id"], index, d / st["png"], None) if lib else 0
    merged = rebuild_sheet(out, gid)
    return {"index": index, "edited_at": st["edited_at"], "kb": st["metrics"]["kb"], "has_animation": bool(st.get("webm")),
            "anim_from_previous": bool(st.get("anim_from_previous")), "pack_copies": refreshed, "sheet_fixed": merged}


# ---------- edit a still in place (the sticker editor's Save, opened from Generate) ----------
def edit_still(out: Path, gid: int, index: int, png: bytes, cfg: EngineConfig, lib=None) -> dict:
    """Replace one still with the editor's 512x512 result, in place: same file name, same S#. The original is kept once in source/orig/ so
    nothing is lost; the animation (made from the video, not from the still) is untouched."""
    from ..media.library import LibraryError, validate_render
    res = read_result(out, gid)
    if not 1 <= index <= len(res["stickers"]):
        raise PipelineError(f"index 1..{len(res['stickers'])} required")
    st = res["stickers"][index - 1]
    if st["status"] != "READY" or not st.get("png"):
        raise PipelineError(f"S{index} is not a READY still.", 409)
    try:
        body, ext, _ = validate_render(png, cfg)
    except LibraryError as e:
        raise PipelineError(str(e), 409)
    d = gen_dir(out, gid)
    f, orig = d / st["png"], d / "source" / "orig" / f"S{index}.png"
    if not orig.exists():
        orig.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, orig)
    if ext != f.suffix.lstrip("."):            # over the PNG limit: Telegram takes a lossless WebP just as well; the file keeps its name, the extension follows
        f.unlink(missing_ok=True)
        st["png"] = str(Path(st["png"]).with_suffix("." + ext)).replace("\\", "/")
        f = d / st["png"]
    f.write_bytes(body)
    st["edited"], st["edited_at"] = True, round(time.time(), 3)
    st["metrics"]["kb"] = max(1, len(body) // 1024)
    hist(st, "still", "human", "EDIT", reason="edited in the sticker editor")
    write_result(out, gid, res)
    emit(out, gid, "still_edited", "done", 0, {"index": index}, "human", "EDIT")
    refreshed = lib.refresh_from_generation(out, res["generation_id"], index, d / st["png"], None) if lib else 0
    merged = rebuild_sheet(out, gid)                                    # the edited slices go back into one sheet (same layout, same S#)
    return {"index": index, "edited_at": st["edited_at"], "kb": st["metrics"]["kb"], "has_animation": st.get("anim_status") == "READY", "pack_copies": refreshed, "sheet_fixed": merged}


# ---------- appearance (re-finish the edge of an existing generation) ----------
# The full "still" stage judges keying (needs the cell); an appearance re-render only re-judges the
# finished image. These checks run on the new file; the keying-stage verdicts (blank_cell, inside_cell,
# no_spill, chroma_risk, single_subject, duplicate_cell) and their metrics stay as Python recorded them.
APPEARANCE_CHECKS = ("dimensions", "transparent_corners", "foreground", "holes", "edge_trimmed", "static_file")


def edge_preview(out: Path, gid: int, index: int, outline: int, erode: int, px: int = 420) -> bytes:
    """One sticker as it would look with this stroke and trim, rendered from its stroke-free twin by the same function the real render uses. Nothing is stored:
    the Studio shows it on ONE thumbnail while a slider moves, and the edge only becomes real on Apply, when the video is generated, or when the stickers go into a pack."""
    if not 0 <= int(outline) <= 40 or not 0 <= int(erode) <= 8:
        raise PipelineError("outline must be 0..40 px and erode 0..8 px")
    plain = gen_dir(out, gid) / "source" / "plain" / f"S{int(index)}.png"
    if not plain.is_file():
        raise PipelineError("This sticker has no stroke-free original to preview.", 404)
    rgba = cv2.cvtColor(cv2.imdecode(np.fromfile(str(plain), np.uint8), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA)
    fin = apply_edge(rgba[..., :3].astype(np.float32), rgba[..., 3].astype(np.float32) / 255.0, int(outline), int(erode))
    if px and max(fin.shape[:2]) > px:
        k = px / max(fin.shape[:2])
        fin = cv2.resize(fin, (max(1, round(fin.shape[1] * k)), max(1, round(fin.shape[0] * k))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(fin, cv2.COLOR_RGBA2BGRA))
    return buf.tobytes()


VIDEO_SIZE = (0.5, 1.5)          # Video size: from half to one and a half times


def cell_cfgs(res: dict, cfg: EngineConfig, cells: list[int]) -> list[tuple[EngineConfig, list[int]]]:
    """The cells grouped by the config each one is animated with: the batch's (cfg, from cfg_for) unless the sticker has its own Video size
    (`anim_scale` on the sticker) or Position (`anim_offset`), which replace the batch's for that sticker. One group per distinct pair, in cell order."""
    groups: dict[tuple, list[int]] = {}
    for i in cells:
        st = res["stickers"][i - 1]
        own = st.get("anim_scale")
        off = tuple(float(v) for v in st.get("anim_offset") or cfg.anim_offset)
        groups.setdefault((float(own) if own is not None else float(cfg.anim_scale), off), []).append(i)
    base = (float(cfg.anim_scale), tuple(float(v) for v in cfg.anim_offset))
    return [(cfg if k == base else replace(cfg, anim_scale=k[0], anim_offset=k[1]), cs) for k, cs in groups.items()]


def set_video_size(out: Path, gid: int, scale, index: int | None = None) -> dict:
    """Video size after the video is received: the batch's (every animation) or, with `index`, one sticker's, which then
    wins over the batch's; `scale` None clears that sticker's own value (it follows the batch again) or resets the batch to 1.0. Stored only:
    the animations change when they are cut again (`gates.reslice`, free, from the stored video)."""
    res = read_result(out, gid)
    if scale is not None:
        scale = round(float(scale), 3)
        if not VIDEO_SIZE[0] <= scale <= VIDEO_SIZE[1]:
            raise PipelineError(f"video size must be {VIDEO_SIZE[0]:g} to {VIDEO_SIZE[1]:g} (1 = as it is)")
    if index is None:
        if scale is None or scale == 1.0:
            res.pop("anim_scale", None)
        else:
            res["anim_scale"] = scale
    else:
        n = len(res["stickers"])
        if not 1 <= int(index) <= n:
            raise PipelineError(f"index 1..{n} required")
        st = res["stickers"][int(index) - 1]
        if scale is None:
            st.pop("anim_scale", None)
        else:
            st["anim_scale"] = scale
    write_result(out, gid, res)
    return {"id": gid, "anim_scale": res.get("anim_scale", 1.0),
            "stickers": {s["index"]: s["anim_scale"] for s in res["stickers"] if s.get("anim_scale") is not None}}


OFFSET_MAX = 0.5                                 # Position: at most half the canvas each way


def set_position(out: Path, gid: int, index: int, offset) -> dict:
    """One sticker's Position: (dx, dy) as a share of the canvas, + = right / down, applied by moving the cut's window, so the
    stored video is enough (`gates.reslice`, free). None or (0, 0) clears it."""
    res = read_result(out, gid)
    n = len(res["stickers"])
    if not 1 <= int(index) <= n:
        raise PipelineError(f"index 1..{n} required")
    st = res["stickers"][int(index) - 1]
    if offset is None or not any(float(v) for v in offset):
        st.pop("anim_offset", None)
    else:
        if len(offset) != 2 or any(not -OFFSET_MAX <= float(v) <= OFFSET_MAX for v in offset):
            raise PipelineError(f"offset must be [dx, dy], each {-OFFSET_MAX:g} to {OFFSET_MAX:g}")
        st["anim_offset"] = [round(float(v), 4) for v in offset]
    write_result(out, gid, res)
    return {"id": gid, "index": int(index), "anim_offset": st.get("anim_offset")}


def set_resize(out: Path, gid: int, on: bool) -> dict:
    """The batch's Resize box. On: each animation is sized and placed by its HOT
    SPOT (the pixels opaque in at least half the frames) matched to its approved still, so drops flying out no longer shrink the face. Off: every
    sticker is cut from its cell at ONE scale, as on the sheet. It replaces Keep approved size / Fit motion / Same scale (still read on older batches).
    Stored only: the animations change when they are cut again (`gates.reslice`, free, from the stored video)."""
    res = read_result(out, gid)
    res["resize"] = bool(on)
    write_result(out, gid, res)
    return {"id": gid, "resize": bool(on)}


def set_keep_size(out: Path, gid: int, on: bool, same: bool | None = None) -> dict:
    """The batch's Motion switch "Keep approved size": True (the default) keeps every animation at its approved still's size and place, cutting off
    what moves past the frame; False shrinks a clip until its whole motion fits. Stored on the batch (cfg_for reads it); the animations already made
    change only when they are cut again (`gates.reslice`, free, from the stored video). `same` True = Same scale: every sticker cut
    from its cell at ONE scale, uniform sizes as on the sheet; it wins over keep/fit; False clears it, None leaves it."""
    res = read_result(out, gid)
    res["keep_size"] = bool(on)
    if same is not None:
        if same:
            res["same_scale"] = True
        else:
            res.pop("same_scale", None)
    write_result(out, gid, res)
    return {"id": gid, "keep_size": bool(on), "same_scale": bool(res.get("same_scale"))}


def set_appearance(out: Path, gid: int, cfg: EngineConfig, outline: int | None = None, erode: int | None = None) -> dict:
    """Change the edge finish stored with a generation and re-render its stills from the outline-free
    `source/plain/S#.png` (cheap: no re-keying). Animation files keep their old edge, so any sticker with a
    webm is marked STALE: the next Animate re-renders its frames from the source video. Returns the new state."""
    if outline is not None and not 0 <= int(outline) <= 40:
        raise PipelineError("outline must be 0 (none) to 40 px")
    if erode is not None and not 0 <= int(erode) <= 8:
        raise PipelineError("erode must be 0 (none) to 8 px")
    res = read_result(out, gid)
    if outline is not None:
        res["outline_px"] = int(outline)
    if erode is not None:
        res["erode_px"] = int(erode)
    cfg = cfg_for(res, cfg)
    d = gen_dir(out, gid)
    rerendered, stale = 0, []
    for st in res["stickers"]:
        if st["status"] != "READY":
            continue
        plain = d / "source" / "plain" / f"S{st['index']}.png"
        if st.get("edited") or not plain.exists():              # an edited still keeps the user's pixels                            # generations written before the plain twins
            continue
        rgba = cv2.cvtColor(cv2.imdecode(np.fromfile(str(plain), np.uint8), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA)
        fin = apply_edge(rgba[..., :3].astype(np.float32), rgba[..., 3].astype(np.float32) / 255.0,
                         cfg.outline_px, cfg.erode_px)
        metrics: dict = {}
        img_bgra = cv2.cvtColor(fin, cv2.COLOR_RGBA2BGRA)
        ok, buf = cv2.imencode(".png", img_bgra)
        data = buf.tobytes()
        report = verify.run("still", {"plain": rgba[..., 3], "metrics": metrics, "img": fin,
                                      "render": lambda: fin, "waive": st.get("still_override") or (),
                                      "encode": lambda img, c: (data, "png")}, cfg, only=APPEARANCE_CHECKS)
        fresh = {c.id: c.to_dict() for c in report}
        if any(c["severity"] == "BLOCK" and not c["ok"] for c in fresh.values()):
            continue                                     # never writes a file the verifier BLOCKs; the old still stands
        kept = [c for c in st.get("report", []) if c.get("name") not in fresh]
        order = [c for c, _, _, _ in verify.CATALOGUE.get("still", [])]
        st["report"] = sorted(kept + list(fresh.values()), key=lambda c: order.index(c["name"]) if c["name"] in order else 99)
        st.get("metrics", {}).update({k: v for k, v in metrics.items() if k in ("holes", "kb", "format")})
        st["metrics"]["outline_px"], st["metrics"]["erode_px"] = cfg.outline_px, cfg.erode_px
        ok, buf = cv2.imencode(".png", cv2.cvtColor(fin, cv2.COLOR_RGBA2BGRA))
        (d / st["png"]).write_bytes(buf.tobytes())
        st["rendered_at"] = round(time.time(), 3)                  # the page puts it in the image URL, so the new edge is fetched, not the cached old one
        hist(st, "appearance", "human", "PASS", detail={"outline_px": cfg.outline_px, "erode_px": cfg.erode_px})
        rerendered += 1
        if st.get("webm"):
            st["anim_status"] = "STALE"
            st["anim_reason"] = ("edge changed: applied again from the stored video" if any(v.get("video") for v in res.get("video_sheets", []))
                                 else "edge changed: Animate again to apply it")
            st["review"]["anim"] = "NONE"
            stale.append(st["index"])
    write_result(out, gid, res)
    emit(out, gid, "appearance", "done", 0, {"outline_px": res["outline_px"], "erode_px": res["erode_px"],
                                             "rerendered": rerendered, "stale": stale})
    return {"ok": True, "outline_px": res["outline_px"], "erode_px": res["erode_px"],
            "rerendered": rerendered, "stale": stale}


# ---------- more ----------
def more(out: Path, inp: Path, from_id: int | None = None) -> int:
    from_id = from_id or latest_id(out)
    res = read_result(out, from_id)
    s = res["source"]
    nxt = sources.variant_of(inp, s["subject"], s["variant"] + 1)
    if not nxt:
        raise PipelineError(f"That's all {s['n_variants']} prepared variations of {s['subject']}.", 409)
    return start(res["prompt"], out, inp, pick=nxt, parent=from_id,
                   outline=res.get("outline_px"), erode=res.get("erode_px"))   # a batch keeps the session's edge finish


def regen(out: Path, inp: Path, gid: int, index: int, subject: str | None = None) -> int:
    """Regenerate ONE sticker as a 1x1 run through the same engine. It becomes a new generation (`regen_of: G00N/S#`) whose single
    sticker goes through G2 -> video -> G4 alone; the parent is never modified."""
    from . import gates
    res = read_result(out, gid)
    if not 1 <= index <= len(res["stickers"]):
        raise PipelineError(f"index 1..{len(res['stickers'])} required")
    subj = subject or res["source"]["subject"]
    picks = sources.scan(inp).get(subj)
    if not picks:
        raise PipelineError(f"No prepared sheet for '{subj}'. Put a 1x1 sheet in img-NNN-{subj}/.", 404)
    pick = picks[next_variant(out, subj, len(picks)) - 1]
    if detect_grid(load_rgb(pick.sheet)) != (1, 1):
        raise PipelineError(f"{pick.sheet.name} is not a 1x1 sheet (one character on one canvas).", 409)
    new = start(res["prompt"], out, inp, pick=pick, parent=gid, regen_of=f"{res['generation_id']}/S{index}", regen_plan=gates.regen_plan(res, index),
                outline=res.get("outline_px"), erode=res.get("erode_px"))
    plan = res["reviews"].get("plan")
    if plan and plan["decision"] == "APPROVE":
        r2 = read_result(out, new)
        r2["reviews"]["plan"] = {"decision": "APPROVE", "by": plan["by"], "ts": plan["ts"], "note": f"inherited from {res['generation_id']}"}
        write_result(out, new, r2)
    return new


# ---------- animate ----------
def check_animate(out: Path, gid: int, scope: str, index: int | None) -> dict:
    res = read_result(out, gid)
    if not res["source"]["has_video"]:
        raise PipelineError("No animation prepared for this variation.", 409)
    if STAGES.index(res["stage"]) < SLICED:
        raise PipelineError("Stills are not ready yet.", 409)
    if scope not in ("pack", "slice"):
        raise PipelineError("scope must be 'pack' or 'slice'")
    if scope == "slice":
        n = len(res["stickers"])
        if not index or not 1 <= index <= n:
            raise PipelineError(f"index 1..{n} required for scope 'slice'")
        if res["stickers"][index - 1]["status"] != "READY":
            raise PipelineError(f"Slice {index} is not a READY still.", 409)
    return res


def animate_cells(res: dict, cfg: EngineConfig, cells: list[int], on_cell=None, cache: AnimCache | None = None, waive: dict | None = None) -> list[AnimationResult]:
    """Animate these cells of a generation from its prepared sources (pre-sliced clips where there are any, else the 3x3 mp4).
    Nothing is saved here; `cache` (out/cache/anim) lets the same source cell come back without being rendered again.
    waive = {cell: {check ids}}: the blocks a human allowed for that cell (`anim_override`), kept as warnings."""
    clips = {int(k): {f: Path(p) for f, p in v.items()} for k, v in res["source"].get("clips", {}).items()}
    mp4 = Path(res["source"]["video_path"]) if res["source"].get("video_path") else None
    use_clips = [i for i in cells if i in clips]
    use_mp4 = [i for i in cells if i not in clips and mp4]
    missing = [i for i in cells if i not in use_clips and i not in use_mp4]
    results = []
    for i in missing:
        results.append(AnimationResult(i, "FAILED", "no_video_source"))
        on_cell and on_cell(results[-1])
    for c_cfg, group in cell_cfgs(res, cfg, use_clips):                        # a sticker with its own Video size is animated with its own config
        results += process_clips({i: clips[i] for i in group}, c_cfg, on_cell=on_cell, cache=cache, waive=waive)
    g = res["source"].get("grid")
    for c_cfg, group in cell_cfgs(res, cfg, use_mp4):
        results += process_video(mp4, c_cfg, group, on_cell=on_cell, rects=g["rects"] if g else None,
                                 sheet_wh=tuple(res["source"]["sheet_size"]) if g else None, cache=cache, waive=waive)
    return results


def run_animate(out: Path, gid: int, cfg: EngineConfig, scope: str, index: int | None = None, pace: float = 0.0) -> dict:
    res = check_animate(out, gid, scope, index)
    cfg = cfg_for(res, cfg)
    d = gen_dir(out, gid)
    wanted = [index] if scope == "slice" else [s["index"] for s in res["stickers"] if s["status"] == "READY" and s["review"]["still"] != "REJECTED"]
    todo = [i for i in wanted if res["stickers"][i - 1]["anim_status"] != "READY" or res["stickers"][i - 1].get("anim_from_previous")]
    emit(out, gid, "video_requested", "done", 0, {"scope": scope if scope == "pack" else f"slice S{index}", "cells": todo})
    if not todo:
        return {"noop": True, "message": "Already animated."}
    clips = {int(k): {f: Path(p) for f, p in v.items()} for k, v in res["source"].get("clips", {}).items()}
    mp4 = Path(res["source"]["video_path"]) if res["source"].get("video_path") else None
    use_clips = [i for i in todo if i in clips]
    use_mp4 = [i for i in todo if i not in clips and mp4]
    missing = [i for i in todo if i not in use_clips and i not in use_mp4]
    try:
        with Stage(out, gid, "video_picked", pace) as s:
            info = {"mode": "pre-sliced clips" if use_clips and not use_mp4 else "3x3 mp4" if use_mp4 and not use_clips else "mixed",
                    "clips": len(use_clips), "from_mp4": len(use_mp4), "missing": missing}
            if use_clips:
                info["clip_format"] = next(iter(pick_clip(clips[use_clips[0]], cfg)))
            if use_mp4:
                from ..engine import ffmpeg as ff
                pi = ff.probe(mp4)
                info.update(file=mp4.name, size=f"{pi['width']}x{pi['height']}", fps=pi["fps"], duration=round(pi["duration"], 2), codec=pi["codec"])
            s.result = info
            res["source"]["video_info"] = info
            res["stage"] = "video_picked"; write_result(out, gid, res)
        with Stage(out, gid, "video_sliced", pace) as s:
            def on_cell(r):
                st = res["stickers"][r.index - 1]
                record_anim(d, st, r, "prepared video")
                write_result(out, gid, res)
                emit(out, gid, "video_cell", "done", 0, {"index": r.index, "status": r.status, "reason": r.reason})
            for i in todo:
                res["stickers"][i - 1]["anim_status"] = "PROCESSING"
            write_result(out, gid, res)
            waive = {i: set(res["stickers"][i - 1].get("anim_override") or []) for i in todo}          # blocks a human allowed, kept when the cell is animated again
            results = animate_cells(res, cfg, todo, on_cell, AnimCache(out / "cache" / "anim"), waive=waive)
            for r in results:
                if r.status == "READY":
                    res["stickers"][r.index - 1].pop("anim_from_previous", None)    # made again after a replaced still: the animation is the current picture
            s.result = {"ready": sum(r.status == "READY" for r in results), "failed": sum(r.status != "READY" for r in results)}
            res["stage"] = "video_sliced"; write_result(out, gid, res)
    except Exception as e:
        res["error"] = str(e)[:300]
        for i in todo:       # cells the job never reached go back to "not requested", else the UI shows "Animating…" forever
            if res["stickers"][i - 1]["anim_status"] == "PROCESSING":
                res["stickers"][i - 1]["anim_status"] = "NOT_REQUESTED"
        write_result(out, gid, res)
    return {"noop": False}


def state(out: Path, gid: int) -> dict:
    res = read_result(out, gid)
    from . import gates
    res["events"] = read_events(out, gid)
    res["stages"] = STAGES
    res["gate"] = gates.gate_info(res)
    res["final"] = gates.final_indices(res)
    res["problem"] = problem_of(out, res)
    res["allow"] = gates.allow_info(res)               # what the page may offer as "Use it anyway" / "Take it back" (flow/gates.py)
    return res


def problem_of(out: Path, res: dict) -> dict | None:
    """Plain words for a sheet the sheet stage blocked (flow/explain.py), with the paid job that brought it (its cost is read from the job file)."""
    from . import explain
    p = explain.sheet_problem(res)
    if p and p.get("received") and p["received"].get("job"):
        try:
            from ..generation import jobs
            j = jobs.read(out, p["received"]["job"])
            p["received"]["cost"] = j.get("cost")
        except Exception:
            pass
    return p


def _grid_of(res: dict) -> tuple:
    """The sheet's own layout out of result.json (2x2, 3x3, whatever was measured); 3x3 when it was never written."""
    g = res.get("grid") or [3, 3]
    try:
        return (max(1, int(g[0])), max(1, int(g[1])))
    except (TypeError, ValueError, IndexError):
        return (3, 3)


def _held_by_particle_rows(out: Path) -> set[int]:
    """The batches that are AI particle sheets already held by a particle set (`out/particles/P###/set.json`: its source or a sheet drawn for it).
    They are shown inside the sticker's particle rows; a particle sheet no set holds stays in the batch list, so nothing disappears."""
    from ..runtime import atomic
    held = set()
    for f in sorted((out / "particles").glob("P[0-9]*/set.json")):
        try:
            s = json.loads(atomic.read_text(f))
        except (OSError, ValueError):
            continue
        for g in [(s.get("source") or {}).get("generation")] + [sh.get("generation") for sh in s.get("sheets") or []]:
            m = re.match(r"^G?(\d+)$", str(g or "").upper())
            if m:
                held.add(int(m.group(1)))
    return {g for g in held if (gen_dir(out, g) / "result.json").is_file() and read_result(out, g).get("kind") == "particles"}


def _history_item(out: Path, gid: int, edited: float) -> dict:
    r = read_result(out, gid)
    d = gen_dir(out, gid)
    ready = [s for s in r["stickers"] if s.get("status") == "READY" and s.get("png")]
    try:
        created = r.get("created") or round((d / "prompts.json").stat().st_mtime, 3)
    except OSError:
        created = None
    rows, cols = _grid_of(r)
    cells = []
    for s in r["stickers"]:
        row, col = divmod(int(s["index"]) - 1, cols)
        png = s.get("png")
        cells.append({"index": int(s["index"]), "row": row, "col": col,
                      "png": f"{png}?e={s.get('rendered_at') or s.get('edited_at') or 0}" if png else None,
                      "status": s.get("status"), "animated": s.get("anim_status") == "READY"})
    from . import groups
    return {"id": gid, "generation_id": r["generation_id"], "prompt": r.get("prompt") or r["source"].get("subject", ""), "created": created,
            "edited": round(edited, 3), "stage": r["stage"], "error": r.get("error"), "ready": len(ready),
            "animated": sum(1 for s in ready if s.get("anim_status") == "READY"),
            "grid": [rows, cols], "cells": sorted(cells, key=lambda c: c["index"]),
            "outline_px": r.get("outline_px"), "relation": groups.relation(r), **({"kind": r["kind"]} if r.get("kind") else {})}


def history(out: Path, offset: int = 0, limit: int = 5) -> dict:
    """Every batch FAMILY, the most recently EDITED first (any change to any of its batches counts), a page at a time (docs/architecture.md, "Batch
    groups"). An item is the family's root batch (title, times, counts, the stickers as the sheet's own grid: `grid: [rows, cols]`, `cells: [{index, row, col,
    png, status, animated}]`) with `variants`: every batch of the family as the same item shape, root first. A batch alone is a family of one."""
    from . import groups
    held = _held_by_particle_rows(out)
    edited_at = {}
    for gid in list_ids(out):
        if gid in held:                          # a particle sheet a sticker's row holds lives under that sticker, not in this list
            continue
        try:
            edited_at[gid] = (gen_dir(out, gid) / "result.json").stat().st_mtime
        except OSError:
            continue
    fams = groups.families(out, list(edited_at))
    packs = _packs(out, fams)
    order = sorted(packs, key=lambda p: (max(edited_at[g] for r in packs[p] for g in fams[r]), p), reverse=True)
    offset, limit = max(0, int(offset)), max(1, min(int(limit), 50))
    items = []
    for pid in order[offset:offset + limit]:
        batches = []
        for root in packs[pid]:
            try:
                variants = [_history_item(out, g, edited_at[g]) for g in fams[root]]
            except Exception:
                continue
            batches.append({**variants[0], "edited": max(v["edited"] for v in variants), "variants": variants})
        if batches:
            items.append({**batches[0], "edited": max(b["edited"] for b in batches), "batches": batches})
    return {"items": items, "more": offset + limit < len(order), "total": len(order)}


PACK_GAP_S = 6 * 3600     # batches of one request made within this of the previous one are one pack in Earlier batches


def _packs(out: Path, fams: dict) -> dict:
    """The batches of ONE pack, as Earlier batches shows them. Derived when read, nothing stored: families of the same request (its preset grid name left out,
    `actions.strip_presets`) by the same person, each made within PACK_GAP_S of the previous one, are one pack. Within a pack an emoji pack's batches are in
    grid order (core 1-9, social 10-18, reactions 19-27, daily 28-36), any other in the order they were made. {pack id (its first root): [family roots]}."""
    from ..generation import actions
    info, linked = {}, {}            # linked: an explicit link (dropped on another batch in Earlier batches, groups.set_pack) beats the derived grouping
    for root in fams:
        try:
            r = read_result(out, root)
        except Exception:
            info[root] = (None, root, 0.0, None)
            continue
        key = (r.get("owner", "local"), actions.strip_presets(str(r.get("prompt") or "")).strip().lower())
        info[root] = (key if key[1] else None, root, float(r.get("created") or 0), (r.get("slots") or {}).get("preset"))
        link = str(r.get("pack") or "").upper().lstrip("G")
        if link.isdigit():
            linked[root] = int(link)
    grid = {k: i for i, k in enumerate(actions.PRESETS)}
    out_packs, last = {}, {}
    for root in sorted(fams, key=lambda x: (info[x][2], x)):
        key, _, created, _ = info[root]
        prev = last.get(key) if key else None
        if prev and created - prev[1] <= PACK_GAP_S:
            out_packs[prev[0]].append(root)
            last[key] = (prev[0], created)
        else:
            out_packs[root] = [root]
            if key:
                last[key] = (root, created)
    where = {r: pid for pid, roots in out_packs.items() for r in roots}
    for root, to in linked.items():
        if to == root:                                   # taken out on purpose: a pack of its own
            pid = where[root]
            if len(out_packs[pid]) > 1:
                rest = [x for x in out_packs.pop(pid) if x != root]
                out_packs[rest[0]] = rest                # the others stay one pack, under their first batch
                for x in rest:
                    where[x] = rest[0]
                out_packs[root] = [root]
                where[root] = root
            continue
        dest = where.get(to)
        if dest is None or dest == where.get(root):
            continue
        out_packs[where[root]].remove(root)
        if not out_packs[where[root]]:
            del out_packs[where[root]]
        out_packs[dest].append(root)
        where[root] = dest
    for pid, roots in out_packs.items():
        roots.sort(key=lambda x: (grid.get(info[x][3], 99) if info[x][3] else 99, info[x][2], x))
    return out_packs


def summary(out: Path) -> list[dict]:
    rows = []
    for gid in reversed(list_ids(out)):
        try:
            r = read_result(out, gid)
        except Exception:
            continue
        s = r["source"]
        rows.append({"id": gid, "generation_id": r["generation_id"], "owner": r.get("owner", "local"), "prompt": r["prompt"], "stage": r["stage"],
                     "subject": s["subject"], "variant": s["variant"], "error": r.get("error"),
                     "folder": f"img-{s.get('subject_id')}-{s['subject']}" if s.get("subject_id") else None})
    return rows
