"""Particle effects, the lifecycle of one effect `E###` (docs/architecture.md ("Reactions")).

An effect belongs to a PACK of the library: a person picks a pack (an emoji pack) and some of its stickers, a vision model (or a built-in table) decides what bursts out of each
(`vision/effect_plan.py`), and the result is one 3-second Telegram video sticker per source sticker, added to the pack tagged with the source emoji. Two ways to make the burst:

- **"sim"**: the engine simulates it (`engine/particles.py`) from sprites (the pack's own stickers, or the cells of ONE particle sheet drawn for the whole effect, `effect.set`) with
  gravity / explosion / vortex sliders. The sprites are fitted to `sprite_px` x `scale` before simulating (latency). Preview and render are free.
- **"video"**: Kling draws it from text only (`generation/effect_prompts.py`, no start image); the returned clip is cut into cells (`engine/effect_video.py`), one result per cell.

Everything is a file under `out/effects/E###/` (`effect.json`, `src/`, `results/`, `previews/`): addressable by id and reproducible from what is stored (rule 11). Nothing here approves
anything for a person: **adding a result to a pack is the person's click**, recorded in the history. Only Telegram's own limits can make a result FAILED; every other check is a warning."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from ..engine import effect_video as ev, particles
from ..generation import effect_prompts as ep
from ..runtime import atomic
from ..vision import effect_plan

MODES = ("video", "sim")
MAX_STICKERS = 24
_LOCK = threading.RLock()


class EffectError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# ---------- storage ----------
def effects_dir(out: Path) -> Path:
    return Path(out) / "effects"


def _dir(out: Path, eid: str) -> Path:
    eid = str(eid).upper()
    d = effects_dir(out) / eid
    if not eid.startswith("E") or not eid[1:].isdigit() or not (d / "effect.json").is_file():
        raise EffectError(f"no effect {eid}", 404)
    return d


def next_id(out: Path) -> str:
    n = max([int(p.name[1:]) for p in effects_dir(out).glob("E[0-9]*") if p.name[1:].isdigit()] or [0]) + 1
    return f"E{n:03d}"


def read(out: Path, eid: str) -> dict:
    return json.loads(atomic.read_text(_dir(out, eid) / "effect.json"))


def _write(out: Path, e: dict) -> dict:
    e["updated"] = round(time.time(), 3)
    atomic.write_text(effects_dir(out) / e["id"] / "effect.json", json.dumps(e, indent=2, ensure_ascii=False))
    return e


def list_effects(out: Path) -> list[dict]:
    rows = []
    for p in sorted(effects_dir(out).glob("E[0-9]*")):
        try:
            e = json.loads(atomic.read_text(p / "effect.json"))
        except (OSError, ValueError):
            continue
        rows.append({k: e.get(k) for k in ("id", "pack_id", "pack_name", "mode", "status", "created", "updated", "title")} | {"stickers": len(e.get("stickers") or []), "results": len(e.get("results") or [])})
    return rows


def hist(e: dict, actor: str, decision: str, reason: str | None = None, detail=None) -> None:
    e.setdefault("history", []).append({"ts": round(time.time(), 3), "actor": actor, "decision": decision, "reason": reason, "detail": detail})


# ---------- the stickers of a pack ----------
def _pack(lib, pack_id: str) -> dict:
    from ..media.library import LibraryError
    with lib.lock:
        db = lib._load()
    try:
        return lib._pack(db, pack_id)
    except LibraryError as ex:
        raise EffectError(str(ex), getattr(ex, "code", 404))


def sticker_rgba(lib, s: dict) -> np.ndarray | None:
    """A sticker of the library as one RGBA picture: a static one as it is, an animated one as the frame with the most on screen (its first second)."""
    f = lib.files / s["file"]
    if not f.is_file():
        return None
    try:
        if s.get("type") == "animated" or f.suffix.lower() == ".webm":
            from ..engine import ffmpeg as ff
            fr = ff.decode_alpha(f, 30, (512, 512))
            if not len(fr):
                return None
            return np.asarray(fr[int(np.argmax([(x[..., 3] > 127).sum() for x in fr]))], np.uint8)
        return np.asarray(Image.open(f).convert("RGBA"), np.uint8)
    except Exception:
        return None


def _png(rgba: np.ndarray) -> bytes:
    b = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(b, "PNG")
    return b.getvalue()


# ---------- create / analyse ----------
def create(out: Path, lib, *, pack_id: str, sticker_ids, mode: str = "video", grid=(2, 2), user: str = "local", note: str = "") -> dict:
    if mode not in MODES:
        raise EffectError(f"mode must be one of {MODES}")
    rows, cols = (int(grid[0]), int(grid[1])) if not isinstance(grid, str) else tuple(int(x) for x in grid.lower().split("x"))
    if not (1 <= rows <= ep.MAX_GRID and 1 <= cols <= ep.MAX_GRID):
        raise EffectError(f"the grid is at most {ep.MAX_GRID}x{ep.MAX_GRID}")
    p = _pack(lib, pack_id)
    by_id = {s["id"]: s for s in p["stickers"]}
    ids = [s["id"] for s in p["stickers"]] if sticker_ids in (None, "all", ["all"]) else list(dict.fromkeys(str(i) for i in sticker_ids))
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise EffectError(f"not in this pack: {', '.join(missing[:3])}", 404)
    if not ids:
        raise EffectError("this pack has no stickers yet")
    if len(ids) > MAX_STICKERS:
        raise EffectError(f"at most {MAX_STICKERS} stickers at a time (pick some)")
    with _LOCK:
        eid = next_id(out)
        d = effects_dir(out) / eid
        (d / "src").mkdir(parents=True)
        stickers = []
        for i in ids:
            s = by_id[i]
            rgba = sticker_rgba(lib, s)
            if rgba is not None:
                (d / "src" / f"{i}.png").write_bytes(_png(rgba))
            stickers.append({"sticker_id": i, "name": s.get("name"), "emoji": s.get("emoji") or "🙂", "file": s.get("file"), "type": s.get("type"), "src": f"src/{i}.png" if rgba is not None else None})
        e = {"id": eid, "created": round(time.time(), 3), "user": user, "pack_id": pack_id, "pack_name": p["name"], "mode": mode, "grid": [rows, cols], "note": (note or "")[:300],
             "title": f"{p['name']} · effects", "status": "NEW", "stickers": stickers, "groups": [], "results": [], "video": {}, "history": []}
        hist(e, user, "CREATE", f"{len(ids)} sticker(s), {mode}")
        return _write(out, e)


def analyse(out: Path, eid: str, *, allowed=None, vlm=None) -> dict:
    """What bursts out of each sticker. `allowed` is the person's yes to sending the pictures to a vision model (None / False: the built-in table answers and the record says so)."""
    e = read(out, eid)
    d = _dir(out, eid)
    items = [{"id": s["sticker_id"], "name": s["name"], "emoji": s["emoji"], "png": (d / s["src"]).read_bytes() if s.get("src") and (d / s["src"]).is_file() else None} for s in e["stickers"]]
    r = effect_plan.analyse(items, vlm=vlm, allowed=allowed, note=e.get("note") or "", out=out)
    with _LOCK:
        e = read(out, eid)
        e.update(groups=r["groups"], per_sticker=r["per_sticker"], notes=r["notes"], analysed_by=r["model"] or "table", status="READY")
        drawn = e.get("set") or {}
        for g in e["groups"]:
            g.setdefault("sprites", {"generation": drawn["generation"], "picked": drawn.get("picked")} if drawn.get("status") == "DRAWN" and drawn.get("generation") else "own")
        hist(e, "python", "ANALYSE", ", ".join(f"{g['subject']}: {', '.join(g['elements'])}" for g in e["groups"])[:300], {"model": r["model"]})
        return _write(out, e)


def _group(e: dict, gid: str) -> dict:
    g = next((g for g in e["groups"] if g["id"] == gid), None)
    if not g:
        raise EffectError(f"no group {gid}", 404)
    return g


def group_elements(out: Path, eid: str, gid: str) -> list[str]:
    """The pieces of one group (404 when there is no such group): the picks of the old per-group routes."""
    return list(_group(read(out, eid), gid).get("elements") or [])


def set_pieces(out: Path, eid: str, gid: str, *, elements=None, subject=None, style=None, by: str = "you") -> dict:
    """The person edits what bursts (a group's subject / pieces / look). Linted like everything else; the screen colour follows the pieces."""
    with _LOCK:
        e = read(out, eid)
        g = _group(e, gid)
        try:
            clean = ep.lint_plan({"subject": subject if subject is not None else g["subject"], "elements": elements if elements is not None else g["elements"], "style": style if style is not None else g.get("style")})
        except ValueError as ex:
            raise EffectError(str(ex))
        g.update(clean, key=ep.key_colour_for(clean["elements"]), by="you")
        hist(e, by, "EDIT", f"{gid}: {', '.join(clean['elements'])}")
        return _write(out, e)


# ---------- mode "video": Kling from text ----------
def video_plan(out: Path, eid: str, gid: str, grid=None) -> dict:
    """What the page shows before anything is spent: the prompt that will be sent, the screen colour, the grid. (The price is asked of the provider by the caller.)"""
    e = read(out, eid)
    g = _group(e, gid)
    rows, cols = tuple(grid) if grid else tuple(e["grid"])
    d = ep.describe({"subject": g["subject"], "elements": g["elements"], "style": g.get("style")}, rows, cols)
    return {**d, "group": gid, "cells": rows * cols, "stickers": len(g["stickers"])}


def new_video_job(out: Path, eid: str, gid: str, *, grid=None, user: str = "local", model=None, options=None, particle_target=None) -> dict:
    """Create (not start) the text-only Kling job of a group. The caller shows the price and starts it with `fulfil_async(job, after=...)`."""
    from ..generation import jobs, model_catalog
    plan = video_plan(out, eid, gid, grid)
    m, params = model_catalog.resolve("video", model, options)
    job = jobs.create(out, "video", request={"model": m, "options": options or {}, "prompt": plan["prompt"], "t2v": True, "label": f"effect {eid} {gid}", "user": user,
                                            "effect": {"id": eid, "group": gid, "grid": plan["grid"], "template": plan["template"], "version": plan["version"], "key": plan["key"], "plan": plan["plan"], "particle_target": particle_target}})
    with _LOCK:
        e = read(out, eid)
        e["video"][gid] = {"job": job["id"], "grid": plan["grid"], "key": plan["key"], "status": "REQUESTED"}
        e["status"] = "VIDEO_REQUESTED"
        hist(e, user, "REQUEST", f"{gid}: video job {job['id']} ({plan['grid'][0]}x{plan['grid'][1]})", {"job": job["id"]})
        _write(out, e)
    return job


def on_video_done(out: Path, eid: str, gid: str, job: dict, cfg) -> dict:
    """The clip is back: cut it into cells, make one sticker of each, store the results. Never raises on a bad cell (it comes back FAILED with the reason)."""
    f = Path(out) / (job.get("result") or {}).get("file", "")
    e = read(out, eid)
    if any(r.get("job") == job["id"] for r in e.get("results", [])):
        return e
    v = e["video"].get(gid) or {}
    rows, cols = v.get("grid") or e["grid"]
    cells = ev.cut_cells(f, rows, cols, cfg, v.get("key") or "green")
    d = _dir(out, eid)
    (d / "results").mkdir(exist_ok=True)
    made = []
    for cell in cells:
        r = ev.finish_cell(cell, cfg)
        if cell.get("frames") is not None:
            import io
            import numpy as np
            from PIL import Image
            from ..engine.particles import trim_sprite, trim_animation, AnimatedSprite
            frames = cell["frames"]
            peak = frames[np.argmax((frames[..., 3] > 4).sum(axis=(1, 2)))]
            poster = trim_sprite(peak)
            if poster is not None:
                buf = io.BytesIO()
                Image.fromarray(poster).save(buf, format="PNG")
                sprite = f"results/sprite-{gid}-{cell['index']}.png"
                atomic.write_bytes(d / sprite, buf.getvalue())
                r["sprite_file"] = sprite
                timeline = trim_animation(AnimatedSprite(ev.resample(frames, cell['src_fps']), ev.FPS))
                buf = io.BytesIO()
                np.savez_compressed(buf, frames=timeline.frames, fps=timeline.fps)
                r['timeline_file'] = f"results/timeline-{gid}-{cell['index']}.npz"
                atomic.write_bytes(d / r['timeline_file'], buf.getvalue())
                r['fps'], r['frames'] = timeline.fps, len(timeline.frames)
            r["key"] = cell.get("key")
        made.append((cell["index"], r))
    with _LOCK:
        e = read(out, eid)
        if any(r.get('job') == job['id'] for r in e.get('results', [])):
            return e
        for idx, r in made:
            rid = f"R{len(e['results']) + 1:03d}"
            fname = None
            if r.get("data"):
                fname = f"results/{rid}.webm"
                atomic.write_bytes(d / fname, r["data"])
            e["results"].append({"id": rid, "mode": "video", "group": gid, "cell": idx, "file": fname, "bytes": len(r["data"]) if r.get("data") else 0, "status": r["status"],
                                 "checks": r["checks"], "warnings": r["warnings"], "blocks": r["blocks"], "metrics": r["metrics"], "job": job["id"], "sprite_file": r.get("sprite_file"), "key": r.get("key"),
                                 "timeline_file": r.get("timeline_file"), "fps": r.get('fps'), "frames": r.get('frames')})
        e["video"][gid] = {**(e["video"].get(gid) or {}), "status": "DONE", "cells": len(made), "cost": job.get("cost")}
        e["status"] = "RESULTS"
        hist(e, "python", "CUT", f"{gid}: {sum(1 for _, r in made if r['status'] == 'READY')} of {len(made)} cells are stickers", {"job": job["id"]})
        return _write(out, e)


# ---------- mode "sim": the engine's own burst ----------
DEFAULT_SPRITE_PX = 100                  # the sprites of a simulated burst are fitted into this many px BEFORE simulating (latency); `sprite_px` of the preview / render params
SPRITE_PX_RANGE = (32, 512)
SCALE_RANGE = (1, 4)                     # `scale` multiplies the fitted size: 100 px -> 200 / 300 / 400


def split_fit(overrides) -> tuple[int, float, dict]:
    """`sprite_px` (a whole number 32..512, default 100) and `scale` (1..4, default 1) out of the params of a preview / render; what is left are the particle params (strict, as before).
    Anything invalid -> EffectError 400."""
    if overrides is not None and not isinstance(overrides, dict):
        raise EffectError("params must be an object")
    o = dict(overrides or {})
    px, sc = o.pop("sprite_px", DEFAULT_SPRITE_PX), o.pop("scale", SCALE_RANGE[0])
    if isinstance(px, bool) or not isinstance(px, (int, float)) or not math.isfinite(px) or px != int(px) or not SPRITE_PX_RANGE[0] <= px <= SPRITE_PX_RANGE[1]:
        raise EffectError(f"sprite_px must be a whole number from {SPRITE_PX_RANGE[0]} to {SPRITE_PX_RANGE[1]}")
    if isinstance(sc, bool) or not isinstance(sc, (int, float)) or not math.isfinite(sc) or not SCALE_RANGE[0] <= sc <= SCALE_RANGE[1]:
        raise EffectError(f"scale must be a number from {SCALE_RANGE[0]} to {SCALE_RANGE[1]}")
    return int(px), (int(sc) if sc == int(sc) else float(sc)), o


def _batch_cells(out: Path, gn: int, picked=None) -> tuple[list[tuple[int, Path]], bool]:
    """(cell index, picture file) of the cells of batch G{gn} that can be a sprite, in cell order, and whether a cell is still being cut. With `picked` (the person's decision) exactly those
    cells with a picture count, whatever their verifier status; without it every READY cell that was not rejected does (a cell with only warnings is READY). A cell whose file is missing is skipped."""
    from . import pipeline as pl
    try:
        res = pl.read_result(out, gn)
    except pl.PipelineError:
        raise EffectError(f"the particles batch G{gn:03d} does not exist", 409)
    d = pl.gen_dir(out, gn)
    keep = None if picked is None else {int(i) for i in picked}
    cells = []
    for st in res["stickers"]:
        f = d / st["png"] if st.get("png") else None
        if f is None or not f.is_file():
            continue
        if (int(st["index"]) in keep) if keep is not None else (st.get("status") == "READY" and (st.get("review") or {}).get("still") != "REJECTED"):
            cells.append((int(st["index"]), f))
    return cells, any(st.get("status") == "PENDING" for st in res["stickers"])


def sprites_of(out: Path, lib, eid: str, gid: str) -> list[np.ndarray]:
    """The particles of a group: its own stickers (default), or the cells of the effect's drawn batch (`sprites: {"generation": 12, "picked": [1, 3]}`): the picked cells, or, for a record
    without `picked` (made before the set existed), every READY cell that was not rejected."""
    e = read(out, eid)
    g = _group(e, gid)
    src = g.get("sprites") or "own"
    pcs: list[np.ndarray] = []
    if isinstance(src, dict) and src.get("generation") is not None:
        gn = int(str(src["generation"]).upper().lstrip("G"))
        cells, pending = _batch_cells(out, gn, src.get("picked"))
        pcs = [np.asarray(Image.open(f).convert("RGBA"), np.uint8) for _, f in cells]
        if not pcs and pending:
            raise EffectError(f"the particles of G{gn:03d} are still being cut: try again in a moment", 409)
        if not pcs:
            raise EffectError("there are no particles to burst: the particles batch has no ready cell (none was cut, or all were rejected)" if src.get("picked") is None
                              else "none of the picked cells has a picture: pick others", 409)
    else:
        d = _dir(out, eid)
        for sid in g["stickers"]:
            s = next(x for x in e["stickers"] if x["sticker_id"] == sid)
            if s.get("src") and (d / s["src"]).is_file():
                pcs.append(np.asarray(Image.open(d / s["src"]).convert("RGBA"), np.uint8))
        if not pcs:
            raise EffectError("the stickers of this group have no picture", 409)
    return pcs


def _picked_of(v) -> list[int]:
    if not isinstance(v, list) or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in v):
        raise EffectError("picked must be a list of cell numbers (1 is the first cell)")
    return sorted(set(v))


def set_sprites(out: Path, eid: str, gid: str, source) -> dict:
    """Where a group's particles come from: "own" or {"generation": N[, "picked": [cells]]} (a drawn particle sheet cut by the engine)."""
    if source != "own" and not (isinstance(source, dict) and source.get("generation") is not None):
        raise EffectError('sprites must be "own" or {"generation": N}')
    if source != "own":
        try:
            gn = int(str(source["generation"]).upper().lstrip("G"))
        except ValueError:
            raise EffectError("generation must be a batch number")
        source = {"generation": gn, **({"picked": _picked_of(source["picked"])} if source.get("picked") is not None else {})}
    with _LOCK:
        e = read(out, eid)
        _group(e, gid)["sprites"] = source
        hist(e, "you", "EDIT", f"{gid}: particles from {source if source == 'own' else 'G%03d' % source['generation']}")
        return _write(out, e)


def params_for(out: Path, eid: str, sticker_id: str, overrides: dict | None = None) -> particles.ParticleParams:
    """The preset the sticker's mood picked, with the person's slider values on top. Unknown keys raise (the contract is the dataclass); `sprite_px` / `scale` are not particle params
    (`split_fit`) and are ignored here."""
    e = read(out, eid)
    g = next((g for g in e["groups"] if sticker_id in g["stickers"]), None)
    if not g:
        raise EffectError(f"{sticker_id} is not part of this effect", 404)
    try:
        return particles.preset(g["preset"].get(sticker_id, "burst"), **split_fit(overrides)[2])
    except ValueError as ex:
        raise EffectError(str(ex))


def _digest(sprites, p, fit=()) -> str:
    """What a preview was made from: every pixel of every sprite (not a prefix: the top rows of a sticker are all transparent), the particle params and the resize settings."""
    h = hashlib.sha256(json.dumps({"p": p.to_dict(), "fit": list(fit)}, sort_keys=True).encode())
    for s in sprites:
        if isinstance(s, particles.AnimatedSprite):
            h.update(f"animated:{s.fps}".encode())
            s = s.frames
        h.update(str(s.shape).encode())
        h.update(np.ascontiguousarray(s).tobytes())
    return h.hexdigest()[:16]


def _fit(pcs: list[np.ndarray], px: int, scale) -> list[np.ndarray]:
    return particles.fit_sprites(pcs, max(1, round(px * scale)))


def sim_preview(out: Path, lib, eid: str, sticker_id: str, overrides: dict | None = None, size: int = 256) -> dict:
    """The burst as a small looping WebP, rendered by the same engine as the final file (the sliders are live). Cached by what it was made from (sprites, params, `sprite_px`, `scale`).
    A previews/ folder is a throwaway cache (nothing stores its paths): each new write keeps only the newest files (`particle_sets.prune_previews`)."""
    from .particle_sets import prune_previews
    e = read(out, eid)
    g = next((g for g in e["groups"] if sticker_id in g["stickers"]), None)
    if not g:
        raise EffectError(f"{sticker_id} is not part of this effect", 404)
    px, sc, _ = split_fit(overrides)
    p = params_for(out, eid, sticker_id, overrides)
    pcs = sprites_of(out, lib, eid, g["id"])
    key = _digest(pcs, p, (px, sc)) + f"-{int(size)}"
    f = _dir(out, eid) / "previews" / f"{key}.webp"
    if not f.is_file():
        small = particles.ParticleParams.from_dict({**p.to_dict(), "size": int(size)})
        fr = particles.simulate(_fit(pcs, px, sc), small)
        f.parent.mkdir(exist_ok=True)
        atomic.write_bytes(f, particles.preview_webp(fr, size=int(size)))
        prune_previews(f.parent)
    return {"url": f"/out/effects/{e['id']}/previews/{f.name}", "file": f"previews/{f.name}", "params": {**p.to_dict(), "sprite_px": px, "scale": sc}, "sprites": len(pcs)}


def sim_render(out: Path, lib, eid: str, sticker_id: str, cfg, overrides: dict | None = None) -> dict:
    """The final 512 px WebM of one sticker's burst, judged. A result is stored whatever the checks say (only Telegram's own limits make it FAILED); the person decides."""
    e = read(out, eid)
    g = next((g for g in e["groups"] if sticker_id in g["stickers"]), None)
    if not g:
        raise EffectError(f"{sticker_id} is not part of this effect", 404)
    px, sc, _ = split_fit(overrides)
    p = params_for(out, eid, sticker_id, overrides)
    pcs = sprites_of(out, lib, eid, g["id"])
    fr = particles.simulate(_fit(pcs, px, sc), p)
    r = ev.encode_and_check(fr, cfg, label=f"{sticker_id} burst")
    d = _dir(out, eid)
    (d / "results").mkdir(exist_ok=True)
    with _LOCK:
        e = read(out, eid)
        rid = f"R{len(e['results']) + 1:03d}"
        fname = None
        if r.get("data"):
            fname = f"results/{rid}.webm"
            atomic.write_bytes(d / fname, r["data"])
        res = {"id": rid, "mode": "sim", "group": g["id"], "sticker_id": sticker_id, "file": fname, "bytes": len(r["data"]) if r.get("data") else 0, "status": r["status"], "checks": r["checks"],
               "warnings": r["warnings"], "blocks": r["blocks"], "metrics": r["metrics"], "params": {**p.to_dict(), "sprite_px": px, "scale": sc}}
        e["results"].append(res)
        e["status"] = "RESULTS"
        hist(e, "python", "RENDER", f"{sticker_id}: {r['status']} {res['bytes'] // 1024} KB", {"result": rid, "warnings": r["warnings"]})
        _write(out, e)
    return res


# ---------- the particle SET: ONE sheet drawn for the whole effect, shared by every sticker (an ordinary batch of kind "particles", its cells are the sprites) ----------
def _grid_of(grid) -> tuple[int, int]:
    try:
        rows, cols = (tuple(int(x) for x in grid.lower().split("x")) if isinstance(grid, str) else tuple(int(x) for x in grid)) if grid else (2, 2)
    except (TypeError, ValueError):
        raise EffectError("the grid is 2x2 or 3x3")
    if rows != cols or rows not in (2, 3):
        raise EffectError("the particle sheet is 2x2 or 3x3")
    return rows, cols


def _default_grid(e: dict) -> tuple[int, int]:
    try:
        return _grid_of((e.get("set") or {}).get("grid") or e.get("grid"))
    except EffectError:
        return 2, 2


def _describe(plan: dict, rows: int, cols: int) -> dict:
    """The prompt template's own description of a rows x cols sheet of particles (`generation/effect_prompts`: `describe_particles`, formerly `describe_pieces`)."""
    fn = getattr(ep, "describe_particles", None) or ep.describe_pieces
    try:
        return fn(plan, rows, cols)
    except ValueError as ex:
        raise EffectError(str(ex))


def set_of(e: dict) -> dict | None:
    """The effect's particle set: the stored `set`, else (a record made before the set existed: a group with `pieces` / `sprites: {generation: N}`) one derived from that group, flagged
    `legacy: true`. None when nothing was ever asked for."""
    s = e.get("set")
    if isinstance(s, dict):
        return s
    for g in e.get("groups") or []:
        sp, pc = g.get("sprites"), g.get("pieces") or {}
        gn = pc.get("generation") if pc.get("generation") is not None else sp.get("generation") if isinstance(sp, dict) else None
        if gn is None and not pc:
            continue
        return {"grid": [int(x) for x in (pc.get("grid") or e.get("grid") or (2, 2))], "elements": list(g.get("elements") or []), "options": [], "source": None, "by": None,
                "status": pc.get("status") or ("DRAWN" if gn is not None else "REQUESTED"), "job": pc.get("job"), "generation": int(str(gn).upper().lstrip("G")) if gn is not None else None,
                "picked": sp.get("picked") if isinstance(sp, dict) else None, "legacy": True}
    return None


def _base_set(out: Path, e: dict) -> dict:
    """The stored set to build on (a copy, no `legacy` flag): the record's own, or the one derived from an old drawn group, with `picked` as a list (an old record had none: the cells that
    would be used)."""
    s = {k: v for k, v in (set_of(e) or {}).items() if k != "legacy"}
    if s and s.get("picked") is None:
        try:
            s["picked"] = [i for i, _ in _batch_cells(out, s["generation"])[0]] if s.get("generation") is not None else []
        except EffectError:
            s["picked"] = []
    return s


def view(out: Path, eid: str) -> dict:
    """The effect as the API shows it: the stored record, with `set` filled for an older record that has a drawn group (`legacy: true`, `picked` = the cells that would be used)."""
    e = read(out, eid)
    if not isinstance(e.get("set"), dict):
        s = _base_set(out, e)
        if s:
            e["set"] = {**s, "legacy": True}
    return e


def _subject_of(e: dict) -> str:
    """What the sheet says the particles burst out of: the subject of the group with the most stickers, else the pack's name."""
    gs = sorted(e.get("groups") or [], key=lambda g: -len(g.get("stickers") or []))
    return (gs[0]["subject"] if gs else None) or e.get("pack_name") or "the emoji"


def _style_of(e: dict) -> str | None:
    gs = sorted(e.get("groups") or [], key=lambda g: -len(g.get("stickers") or []))
    return gs[0].get("style") if gs else None


def _emoji_of(e: dict) -> str:
    """The emoji tag of the particle cells: the pack's most common one (a cell of the plan needs one)."""
    c = Counter(s.get("emoji") for s in e.get("stickers") or [] if s.get("emoji"))
    return c.most_common(1)[0][0] if c else "🙂"


def _picks(e: dict, elements, n: int, truncate: bool = False) -> list[str]:
    """The particles the person picked (1..n; fewer than n are cycled as variants by the sheet's template, more than n is a 400). Without any: the set's own picks, else the groups' pieces,
    cut to the first n (that is not the person's pick, so it is never an error). `truncate` does the same for picks that came from an old per-group route. Linted like everything else."""
    if elements is None:
        elements, truncate = (set_of(e) or {}).get("elements") or [x for g in e.get("groups") or [] for x in g.get("elements") or []], True
    if not isinstance(elements, list) or any(not isinstance(x, str) for x in elements):
        raise EffectError("elements must be a list of particle names")
    try:
        clean = ep.lint_plan({"subject": _subject_of(e), "elements": elements, "style": _style_of(e)})["elements"]
    except ValueError as ex:
        raise EffectError(str(ex))
    if not clean:
        raise EffectError("pick at least one particle")
    if len(clean) > n and truncate:
        clean = clean[:n]
    if len(clean) > n:
        raise EffectError(f"pick at most {n} particles for a sheet of {n} cells (fewer are repeated in other sizes and angles)")
    return clean


def particles_plan(out: Path, eid: str, grid=None, elements=None, truncate: bool = False) -> dict:
    """What the page shows before anything is spent: the prompt that will be sent, the cells, the screen colour, the grid, `outline: 0`, `picks` (the particles the sheet is drawn from).
    (The price is asked of the provider by the caller.)"""
    e = read(out, eid)
    rows, cols = _grid_of(grid) if grid else _default_grid(e)
    els = _picks(e, elements, rows * cols, truncate)
    d = _describe({"subject": _subject_of(e), "elements": els, "style": _style_of(e)}, rows, cols)
    return {**d, "id": eid, "n": rows * cols, "outline": 0, "kind": "particles", "picks": els}


def particles_base_plan(out: Path, eid: str, grid, elements) -> dict:
    """The plan the normal sheet path accepts (`tasks.reserve(base_plan=...)`) for the set's sheet: template `sheet_2x2|sheet_3x3` v3 for the cell layout, the particles prompt kept as
    `custom.sheet_prompt` so it is exactly what is sent, one cell per particle (key = the particle name, emoji = the pack's most common one)."""
    e = read(out, eid)
    rows, cols = _grid_of(grid)
    d = _describe({"subject": _subject_of(e), "elements": _picks(e, elements, rows * cols), "style": _style_of(e)}, rows, cols)
    return sheet_base_plan(d, rows, cols, _emoji_of(e), "effect", e["id"])


def sheet_base_plan(d: dict, rows: int, cols: int, emoji: str, link_key: str, link_id: str) -> dict:
    """The plan the normal sheet path accepts for a sheet of particles, from the prompt template's own description `d` (`_describe`): shared by an effect's sheet (`link_key` "effect")
    and a particle set's *Generate more* (`link_key` "set"), so both are cut and priced exactly the same way."""
    from ..generation import prompter
    cells, stickers = [], []
    for c in d["cells"]:
        key = prompter.slug(c["label"]) or f"particle_{c['pos']}"
        cell = {"pos": c["pos"], "label": c["label"], "tags": prompter.clean_tags(key), "emoji": emoji}
        cells.append(cell)
        stickers.append({"index": c["pos"], "id": f"prompt{c['pos']:02d}", "prompt": f"{c['label']}, isolated and centred", "key": key, "tags": cell["tags"], "emoji": emoji})
    subj = f"{d['plan']['subject']} particles"
    tid = prompter.TEMPLATE_OF[(rows, cols)]
    slots = {"subject_description": f"separate small particles that burst out of {ep._theme(d['plan']['subject'])}", "style_id": "flat_vector", "mode": tid, "cells": cells,
             "action_guidance": "default", "key_colour": d["key"], "loop": False}
    return {"task": subj, "task_slug": prompter.slug(subj) or "particles", "subject": subj, "context": "", "kind": "default", "grid": [rows, cols], "template_id": tid,
            "template_version": prompter.TEMPLATE_VERSION, "slots": slots, "guidelines": {}, "sheet_prompt": d["prompt"], "video_prompt": "", "stickers": stickers,
            "custom": {"sheet_prompt": d["prompt"]}, link_key: {"id": link_id, "template": d["template"], "version": d["version"]}}


def request_particles(out: Path, eid: str, job: str, grid, elements, user: str = "local") -> dict:
    """The sheet job exists: the set says so (`status` REQUESTED, the job, the picks) so the page can poll the job. Called before the job runs, so the link can never be overwritten by it.
    A set that was drawn before keeps its generation and picks until the new sheet is cut."""
    with _LOCK:
        e = read(out, eid)
        prev = _base_set(out, e)
        rows, cols = int(grid[0]), int(grid[1])
        e["set"] = {"options": [], "source": None, "by": "you", "picked": [], **prev, "grid": [rows, cols], "elements": list(elements), "status": "REQUESTED", "job": job}
        hist(e, user, "REQUEST", f"particle sheet job {job} ({rows}x{cols}): {', '.join(elements)}"[:300], {"job": job})
        return _write(out, e)


def link_particles(out: Path, eid: str, generation, job: str | None = None, grid=None) -> dict:
    """The sheet became a batch (the sheet job is DONE; `Console.start_from_job` runs in thread mode and in queue mode alike): EVERY group's sprites are its cells
    (`{generation: N, picked: [all cells]}`). The cells are cut equally and judged by the particle rules of the normal stills run; `sprites_of` uses the picked ones that have a picture
    and answers 409 until there is one."""
    gn = int(str(generation).upper().lstrip("G"))
    with _LOCK:
        e = read(out, eid)
        prev = _base_set(out, e)
        rows, cols = _grid_of(grid or prev.get("grid") or e.get("grid"))
        picked = list(range(1, rows * cols + 1))
        e["set"] = {"options": [], "source": None, "by": "you", "elements": [], **prev, "grid": [rows, cols], "status": "DRAWN", "generation": gn, "job": job or prev.get("job"), "picked": picked}
        for g in e.get("groups") or []:
            g["sprites"] = {"generation": gn, "picked": list(picked)}
        hist(e, "python", "LINK", f"the particles are the cells of G{gn:03d}", {"generation": f"G{gn:03d}", "job": e["set"]["job"]})
        return _write(out, e)


def recut_check(out: Path, eid: str) -> int:
    """The drawn batch of the effect's set, when it can be cut again as particles (an old sheet from before batches knew they were particles): its number, else EffectError 409 with the reason."""
    from . import pipeline as pl
    gn = (set_of(read(out, eid)) or {}).get("generation")
    if gn is None:
        raise EffectError("there is no drawn particle sheet yet: draw the particles first", 409)
    try:
        pl.recut_check(out, gn)
        if pl.read_result(out, gn).get("kind") == "particles":
            raise pl.PipelineError("This sheet was already cut as particles.", 409)
    except pl.PipelineError as ex:
        raise EffectError(str(ex), ex.code)
    return gn


def pick_particles(out: Path, eid: str, indexes, user: str = "you") -> dict:
    """The person's decision about which cells of the drawn sheet are particles (`set.picked`, and every group's `sprites.picked`). A cell can be picked whatever its verifier status (a warning is
    not a verdict); only a cell with no picture cannot (400). Recorded in the history."""
    idx = _picked_of(indexes)
    if not idx:
        raise EffectError("pick at least one cell")
    with _LOCK:
        e = read(out, eid)
        s = _base_set(out, e)
        if s.get("generation") is None:
            raise EffectError("there is no drawn particle sheet yet: draw the particles first", 409)
        have = {i for i, _ in _batch_cells(out, s["generation"], idx)[0]}
        bad = [i for i in idx if i not in have]
        if bad:
            raise EffectError(f"cell {', '.join(map(str, bad))} has no picture (it was not cut): pick other cells", 400)
        e["set"] = {**s, "picked": idx}
        for g in e.get("groups") or []:
            g["sprites"] = {"generation": s["generation"], "picked": list(idx)}
        hist(e, user, "PICK", f"particles picked: cell {', '.join(map(str, idx))} of G{s['generation']:03d}", {"picked": idx, "generation": s["generation"]})
        return _write(out, e)


# ---------- the candidate particles for the set: ONE picture, the model's (or the table's) short list ----------
def _sheet_png(out: Path, gn: int) -> bytes | None:
    """The master sheet of batch G{gn}, downscaled to at most 1024 px (PNG), or None when it is not there."""
    from . import pipeline as pl
    try:
        res = pl.read_result(out, gn)
    except pl.PipelineError:
        return None
    gd = pl.gen_dir(out, gn)
    for f in [gd / (res["source"].get("sheet_copy") or "-"), *sorted((gd / "source").glob("sheet.*")), Path(res["source"].get("sheet_path") or "-")]:
        if not f.is_file():
            continue
        try:
            im = Image.open(f)
            im.load()
        except Exception:
            continue
        im = im.convert("RGB")
        im.thumbnail((1024, 1024), Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()
    return None


def _contact_png(d: Path, e: dict) -> bytes | None:
    """A contact sheet of the effect's stickers (at most 9, spread over the pack) on grey, one picture for the model; None when no sticker has a picture."""
    have = [d / s["src"] for s in e.get("stickers") or [] if s.get("src") and (d / s["src"]).is_file()]
    if not have:
        return None
    if len(have) > 9:
        have = [have[i] for i in sorted({round(k * (len(have) - 1) / 8) for k in range(9)})]
    side = 1 if len(have) == 1 else 2 if len(have) <= 4 else 3
    tile = min(512, 1020 // side)
    sheet = Image.new("RGBA", (tile * side, tile * side), (200, 200, 200, 255))
    for k, f in enumerate(have):
        im = Image.open(f).convert("RGBA")
        im.thumbnail((tile - 12, tile - 12), Image.LANCZOS)
        sheet.alpha_composite(im, ((k % side) * tile + (tile - im.width) // 2, (k // side) * tile + (tile - im.height) // 2))
    b = io.BytesIO()
    sheet.convert("RGB").save(b, "PNG")
    return b.getvalue()


def _source_picture(out: Path, lib, e: dict) -> tuple[bytes | None, dict]:
    """The ONE picture the model looks at: the master sheet of the batch the effect's stickers were cut from (the batch most of them came from), else a contact sheet of the stickers."""
    with lib.lock:
        db = lib._load()
    by_id = {s["id"]: s for p in db.get("packs", []) for s in p.get("stickers", [])}
    gens = []
    for st in e.get("stickers") or []:
        m = re.fullmatch(r"G(\d{3,})", str(((by_id.get(st["sticker_id"]) or {}).get("source") or {}).get("generation") or ""))
        if m:
            gens.append(int(m[1]))
    for gn, _ in Counter(gens).most_common():
        png = _sheet_png(out, gn)
        if png:
            return png, {"kind": "batch", "generation": gn}
    return _contact_png(_dir(out, e["id"]), e), {"kind": "contact"}


def suggest(out: Path, lib, eid: str, grid=None, *, allowed=None, vlm=None) -> dict:
    """Candidate particles for the effect's ONE shared set (8-12 short names). The vision model looks at one picture (the batch's own master sheet, else a contact sheet of the stickers) with the
    person's yes (`allowed`); without it, without a model or on nonsense the built-in table answers and `by` says so. Stored in `effect.set.options`; the person's picks are `/particles`."""
    e = read(out, eid)
    rows, cols = _grid_of(grid) if grid else _default_grid(e)
    png, source = _source_picture(out, lib, e)
    r = effect_plan.suggest_options(png, kind=source["kind"], stickers=[{"name": s.get("name"), "emoji": s.get("emoji")} for s in e["stickers"]], pack_name=e.get("pack_name") or "",
                                    grid=(rows, cols), allowed=allowed, vlm=vlm, out=out)
    with _LOCK:
        e = read(out, eid)
        prev = _base_set(out, e)
        s = {"elements": [], "picked": [], **prev, "options": r["options"], "source": source, "by": r["by"]}
        if prev.get("status") not in ("REQUESTED", "DRAWN"):         # a sheet that is drawn (or being drawn) keeps its grid and status; only the candidates change
            s.update(grid=[rows, cols], status="SUGGESTED")
        if r.get("model"):
            s["model"] = r["model"]
        else:
            s.pop("model", None)
        e["set"] = s
        hist(e, "python", "SUGGEST", f"{len(r['options'])} candidate particles by {r['by']} from {'the master sheet G%03d' % source['generation'] if source['kind'] == 'batch' else 'a contact sheet of the stickers'}",
             {"by": r["by"], "model": r.get("model"), "source": source})
        _write(out, e)
    return {"options": r["options"], "n": rows * cols, "grid": [rows, cols], "source": source, "by": r["by"], **({"model": r["model"]} if r.get("model") else {}), "notes": r["notes"]}


# ---------- add to the pack (the person's click) ----------
def targets(e: dict, result_ids=None) -> list[tuple[dict, dict]]:
    """(source sticker, result) pairs. A sim result belongs to its sticker; a video result (one per cell) goes to the stickers of its group in order, cell by cell, round robin."""
    res = {r["id"]: r for r in e["results"]}
    chosen = [res[i] for i in (result_ids or [r["id"] for r in e["results"] if r["status"] == "READY"]) if i in res]
    by_sid = {s["sticker_id"]: s for s in e["stickers"]}
    pairs = []
    for r in chosen:
        if r["mode"] == "sim":
            pairs.append((by_sid[r["sticker_id"]], r))
    for gid in {r["group"] for r in chosen if r["mode"] == "video"}:
        g = _group(e, gid)
        cells = sorted((r for r in chosen if r["mode"] == "video" and r["group"] == gid), key=lambda r: r["cell"])
        for n, sid in enumerate(g["stickers"]):
            if cells:
                pairs.append((by_sid[sid], cells[n % len(cells)]))
    return pairs


def add_to_pack(out: Path, lib, eid: str, result_ids=None, pack_id: str | None = None, user: str = "local", sticker_ids=None) -> dict:
    """Put results into a pack as animated stickers tagged with their source sticker's emoji. This click is the person's approval of the effect (history `APPROVE`); a FAILED result (a
    Telegram limit is broken) cannot be added, everything else can, warnings included."""
    e = read(out, eid)
    pairs = targets(e, result_ids)
    if sticker_ids:                                  # a sticker's own gallery adds a take for THAT sticker only (a video cell would otherwise go to every sticker of its group)
        keep = {str(i) for i in sticker_ids}
        pairs = [(s, r) for s, r in pairs if s["sticker_id"] in keep]
    if not pairs:
        raise EffectError("there is nothing to add yet: render an effect first", 409)
    bad = [r["id"] for _, r in pairs if r["status"] != "READY" or not r.get("file")]
    if bad:
        raise EffectError(f"{', '.join(sorted(set(bad)))} breaks a Telegram limit (see its checks) and cannot be added", 409)
    pack_id = pack_id or e["pack_id"]
    d = _dir(out, eid)
    added = []
    for s, r in pairs:
        name = f"{(s.get('name') or 'sticker')[:40]} burst"
        st = lib.add_bytes(pack_id, (d / r["file"]).read_bytes(), "webm", name, "animated", s.get("emoji") or "🙂",
                           source={"effect": eid, "result": r["id"], "source_sticker": s["sticker_id"], "source_pack": e["pack_id"]})
        added.append({"sticker": st["id"], "name": st["name"], "result": r["id"], "of": s["sticker_id"]})
    with _LOCK:
        e = read(out, eid)
        for r in e["results"]:
            if r["id"] in {a["result"] for a in added}:
                r["added_to"] = pack_id
        e["status"] = "DONE"
        hist(e, user, "APPROVE", f"{len(added)} effect sticker(s) added to the pack", {"pack": pack_id, "added": added})
        _write(out, e)
    return {"added": added, "pack_id": pack_id}


# ---------- the particles of a sticker: what was created for it and what was saved (a pure read) ----------
def _lib_index(lib) -> tuple[dict, dict]:
    """(sticker id -> (pack id, pack name) for every library sticker, (effect, result) -> [library stickers made from it]) from one read of the library."""
    from urllib.parse import quote
    with lib.lock:
        db = lib._load()
    where, made = {}, {}
    for p in db.get("packs", []):
        for s in p.get("stickers", []):
            where[s["id"]] = (p["id"], p["name"])
            src = s.get("source") or {}
            if src.get("effect"):
                made.setdefault((src["effect"], src.get("result")), []).append(
                    {"sticker_id": s["id"], "name": s.get("name"), "emoji": s.get("emoji"), "pack_id": p["id"], "pack": p["name"], "effect": src["effect"], "result": src.get("result"),
                     "source_sticker": src.get("source_sticker"), "file": s.get("file"), "url": "/lib/" + quote(s.get("file") or ""), "kb": s.get("kb"), "created": s.get("created"),
                     "missing": not (lib.files / (s.get("file") or "\0")).is_file()})
    return where, made


def _gallery(out: Path, lib, pack_id: str, only: str | None = None) -> dict[str, dict]:
    """sticker id -> {created: [...], saved: [...], effects: [...]} for the stickers of `pack_id` (or just `only`). An effect counts for a sticker when the sticker is in the pack now
    (it may have been moved since) or when the effect was made for that pack. Reads effect.json files only; nothing is written, nothing is rendered."""
    where, made = _lib_index(lib)
    in_pack = {sid for sid, (pid, _) in where.items() if pid == pack_id}
    want = {only} if only else in_pack
    gal: dict[str, dict] = {}

    def slot(sid):
        return gal.setdefault(sid, {"created": [], "saved": [], "effects": []})
    for p in sorted(effects_dir(out).glob("E[0-9]*")):
        try:
            e = json.loads(atomic.read_text(p / "effect.json"))
        except (OSError, ValueError):
            continue
        eid = e.get("id") or p.name
        mine = [s["sticker_id"] for s in e.get("stickers") or [] if s["sticker_id"] in want and (s["sticker_id"] in in_pack or e.get("pack_id") == pack_id)]
        if not mine:
            continue
        groups = {g["id"]: g for g in e.get("groups") or []}
        for sid in mine:
            for r in e.get("results") or []:
                shared = r.get("mode") == "video"
                g = groups.get(r.get("group")) or {}
                if shared and sid not in (g.get("stickers") or []):
                    continue
                if not shared and r.get("sticker_id") != sid:
                    continue
                f = (p / r["file"]) if r.get("file") else None
                got = [m for m in made.get((eid, r["id"])) or [] if m["source_sticker"] == sid]            # saved FOR this sticker (a video cell may have been saved for another sticker of its group)
                item = {"effect": eid, "result": r["id"], "mode": r.get("mode"), "status": r.get("status"), "bytes": r.get("bytes") or 0, "warnings": list(r.get("warnings") or []),
                        "blocks": list(r.get("blocks") or []), "url": f"/out/effects/{eid}/{r['file']}" if r.get("file") else None, "missing": not (f and f.is_file()),
                        "added_to": got[0]["pack_id"] if got else None,                      # the library's word, not the flag the effect kept (a sticker deleted since is not "added")
                        "created": round(f.stat().st_mtime, 3) if f and f.is_file() else e.get("created"), "shared": shared, "group": r.get("group")}
                item["usable"] = item["status"] == "READY" and not item["missing"]
                if shared:
                    cells = sorted(x["cell"] for x in e["results"] if x.get("mode") == "video" and x.get("group") == r.get("group"))
                    item["cell"] = r.get("cell")
                    item["assigned"] = bool(cells) and cells[(g.get("stickers") or []).index(sid) % len(cells)] == r.get("cell")      # the cell `add_to_pack` gives this sticker
                slot(sid)["created"].append(item)
            if eid not in slot(sid)["effects"]:
                slot(sid)["effects"].append(eid)
    for lst in made.values():
        for m in lst:
            if m["source_sticker"] in want:
                slot(m["source_sticker"])["saved"].append(m)
    for v in gal.values():
        v["created"].sort(key=lambda i: (i["created"] or 0, i["effect"], i["result"]), reverse=True)
        v["saved"].sort(key=lambda i: i["created"] or 0, reverse=True)
        v["effects"] = sorted(v["effects"], reverse=True)
    return gal


def for_sticker(out: Path, lib, pack_id: str, sticker_id: str) -> dict:
    """Every particle made for one sticker, newest first. `created`: the results of the effects that include it (a simulated result is its own; every cell of its group's video is a take,
    `shared: true`, `assigned` marks the cell `add_to_pack` would give it). `saved`: the library stickers made from them, in any pack. A missing file or a FAILED result is listed and flagged
    (`missing`, `usable: false`). 404 when the pack does not exist or the sticker is in neither the pack nor any effect."""
    _pack(lib, pack_id)
    g = _gallery(out, lib, pack_id, only=str(sticker_id))
    row = g.get(str(sticker_id))
    if row is None:
        with lib.lock:
            db = lib._load()
        if not any(s["id"] == sticker_id for p in db.get("packs", []) if p["id"] == pack_id for s in p["stickers"]):
            raise EffectError(f"no sticker {sticker_id} in this pack", 404)
        row = {"created": [], "saved": [], "effects": []}
    return {"sticker": str(sticker_id), "pack_id": pack_id, **row, "can_make": True}


def counts_for_pack(out: Path, lib, pack_id: str) -> dict[str, dict]:
    """{sticker id: {created, saved}} for the stickers of a pack that have any particles (created counts the usable ones: READY with its file), one pass for the whole pack."""
    _pack(lib, pack_id)
    res = {}
    for sid, v in _gallery(out, lib, pack_id).items():
        n, s = sum(1 for i in v["created"] if i["usable"]), sum(1 for i in v["saved"] if not i["missing"])
        if n or s:
            res[sid] = {"created": n, "saved": s}
    return res
