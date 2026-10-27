"""Durable sticker-owned particle sets (`docs/architecture.md ("Reactions")`).

P### stores owner sticker links, selected still/animated sprites, saved motion,
source jobs/credits and rendered bursts. Packs are derived read views; adding a
burst affirms it in a pack. E### is a working run, never an owner. Images,
clips and RGBA timelines live in cells/; previews/ and renders/ use the same
simulator. Delete is reversible; parent purge detaches without deleting sets.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path

from ..runtime import atomic

_LOCK = threading.RLock()
MAX_NAME = 60
MAX_ELEMENTS = 12
KINDS = ("drawn", "stickers", "video")
VERSION = 1
_MEDIA_CACHE = OrderedDict()
_MEDIA_CACHE_BYTES = 64 * 1024 * 1024


def _animated_media(clip: Path, *, timeline: Path | None = None, fps=None, px=512):
    """Load once per file/fit size, retaining alpha and clock. Cache is bounded to 64 MiB."""
    import math
    import numpy as np
    from ..engine import ffmpeg, particles
    path = timeline if timeline is not None and timeline.is_file() else clip
    stamp = path.stat()
    key = (str(path.resolve()), stamp.st_mtime_ns, stamp.st_size, fps, px)
    with _LOCK:
        if key in _MEDIA_CACHE:
            value = _MEDIA_CACHE.pop(key)
            _MEDIA_CACHE[key] = value
            return value
    if path.suffix.lower() == '.npz':
        with np.load(path, allow_pickle=False) as data:
            value = particles.AnimatedSprite(data['frames'], float(data['fps']))
    else:
        info = ffmpeg.probe(path)
        clock = float(fps or info.get('fps') or 30)
        count = min(400, max(1, math.ceil(float(info.get('duration') or 3) * clock)))
        frames = ffmpeg.decode_full(path, info['width'], info['height'], count, None)
        value = particles.AnimatedSprite(frames, clock)
    value = particles.fit_sprites([value], px)[0]
    value.frames.flags.writeable = False
    with _LOCK:
        _MEDIA_CACHE[key] = value
        while sum(v.frames.nbytes for v in _MEDIA_CACHE.values()) > _MEDIA_CACHE_BYTES:
            _MEDIA_CACHE.popitem(last=False)
    return value


class SetError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# ---------- storage ----------
def sets_dir(out: Path) -> Path:
    return Path(out) / "particles"


def trash_dir(out: Path) -> Path:
    return Path(out) / "trash" / "particles"


PREVIEW_KEEP = 24        # a previews/ folder is a cache (nothing stores its paths): only its newest files are kept


def prune_previews(d: Path, keep: int = PREVIEW_KEEP) -> int:
    """Delete all but the `keep` newest files of one previews/ folder (by modification time); a preview asked again is simply made again.
    Never touches renders/ or cells/. Returns how many were removed; a file in use or already gone is skipped."""
    try:
        files = sorted((f for f in Path(d).iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    gone = 0
    for f in files[keep:]:
        try:
            f.unlink()
            gone += 1
        except OSError:
            pass
    return gone


def sweep_previews(out: Path, keep: int = PREVIEW_KEEP) -> dict:
    """Every previews/ folder under out/particles (sets and emoji reactions) and out/effects (effect runs) pruned to its newest `keep`: {folders, removed}."""
    n, gone = 0, 0
    for root in (sets_dir(out), Path(out) / "effects"):
        for d in root.glob("**/previews"):
            if d.is_dir():
                n += 1
                gone += prune_previews(d, keep)
    return {"folders": n, "removed": gone}


def _dir(out: Path, pid: str) -> Path:
    pid = str(pid).upper()
    d = sets_dir(out) / pid
    if not pid.startswith("P") or not pid[1:].isdigit() or not (d / "set.json").is_file():
        raise SetError(f"no particle set {pid}", 404)
    return d


def _trash(out: Path, pid: str) -> Path:
    pid = str(pid).upper()
    d = trash_dir(out) / pid
    if not pid.startswith("P") or not pid[1:].isdigit() or not (d / "set.json").is_file():
        raise SetError(f"no deleted particle set {pid}", 404)
    return d


def _next_id(out: Path) -> str:
    n = max([int(p.name[1:]) for p in sets_dir(out).glob("P[0-9]*") if p.name[1:].isdigit()]
            + [int(p.name[1:]) for p in trash_dir(out).glob("P[0-9]*") if p.name[1:].isdigit()] or [0]) + 1
    return f"P{n:03d}"


def read(out: Path, pid: str) -> dict:
    return json.loads(atomic.read_text(_dir(out, pid) / "set.json"))


def read_deleted(out: Path, pid: str) -> dict:
    return json.loads(atomic.read_text(_trash(out, pid) / "set.json"))


def _write(out: Path, s: dict) -> dict:
    if "owner" in s:
        s.pop("packs", None)
    s["updated"] = round(time.time(), 3)
    atomic.write_text(_dir(out, s["id"]) / "set.json", json.dumps(s, indent=2, ensure_ascii=False))
    return s


def owners_for(lib, owners=None, packs=None) -> list[dict]:
    """Resolve live library stickers; pack ids are deprecated input aliases only."""
    if owners is not None and not isinstance(owners, list):
        raise SetError("owners must be a list of sticker ids or owner records")
    if lib is None:
        if owners:
            raise SetError("owner stickers require a library")
        return []
    with lib.lock:
        db = lib._load()
    requested = owners
    if requested is None:
        requested = [{"sticker_id": st["id"], "pack_id": p["id"]}
                     for pid in _clean_packs(packs, lib) for p in db.get("packs", []) if p["id"] == pid
                     for st in p.get("stickers", [])]
    result = []
    for item in requested:
        sid = item.get("sticker_id") if isinstance(item, dict) else item
        match = next(((p, st) for p in db.get("packs", []) for st in p.get("stickers", [])
                      if st["id"] == sid and (not isinstance(item, dict) or not item.get("pack_id") or item["pack_id"] == p["id"])), None)
        if match is None:
            raise SetError(f"no owner sticker {sid}", 404)
        p, st = match
        src = st.get("source") or {}
        row = {"sticker_id": sid, "pack_id": p["id"], "generation": src.get("generation"), "index": src.get("index")}
        if row not in result:
            result.append(row)
    return result


def _sync_links(lib, s):
    if lib is None:
        return
    ids = {o["sticker_id"] for o in s.get("owner", [])}
    with lib.lock:
        db = lib._load()
        changed = False
        for p in db.get("packs", []) + (db.get('trash') or {}).get('packs', []):
            for st in p.get("stickers", []):
                links = list(st.get("particles") or [])
                new = [x for x in links if x != s["id"]]
                if st["id"] in ids:
                    new = links if s["id"] in links else links + [s["id"]]
                if new != links:
                    st["particles"] = new
                    changed = True
        if changed:
            lib._save(db)


def _normalize_owner(out, lib, s, f):
    legacy = 'owner' not in s
    before = s.get('owner')
    if legacy:
        backup = f.with_name('set.json.pre-owner')
        if not backup.exists():
            atomic.write_bytes(backup, f.read_bytes())
        src = s.get('source') or {}
        ids = src.get('sticker_ids')
        if ids is None and src.get('effect'):
            from . import effects
            try:
                ids = [st['sticker_id'] for st in effects.read(out, src['effect']).get('stickers', [])]
            except effects.EffectError:
                ids = []
    else:
        ids = [o['sticker_id'] for o in before]
    if lib is not None:
        with lib.lock:
            db = lib._load()
        packs = db.get('packs', []) + (db.get('trash') or {}).get('packs', [])
        rows = []
        for sid in ids or []:
            pair = next(((p, st) for p in packs for st in p.get('stickers', [])
                         if st['id'] == sid and (not legacy or p['id'] in (s.get('packs') or []))), None)
            if pair:
                p, st = pair
                src = st.get('source') or {}
                row = {'sticker_id':sid, 'pack_id':p['id'], 'generation':src.get('generation'), 'index':src.get('index')}
                if row not in rows:
                    rows.append(row)
        s['owner'] = rows
    elif legacy:
        s['owner'] = []
    if legacy:
        s['version'] = 2
        s.pop('packs', None)
        hist(s, 'python', 'MIGRATE_OWNER', 'sticker owners; unresolvable sets stay detached')
    return legacy or before != s.get('owner')


def migrate(out: Path, lib, pid: str) -> dict:
    """Idempotent migration; owner pack/provenance follow sticker IDs, including pack trash."""
    with _LOCK:
        s = read(out, pid)
        if _normalize_owner(out, lib, s, _dir(out, pid) / 'set.json'):
            _write(out, s)
        _sync_links(lib, s)
        return s


def _derived(s):
    packs = list(dict.fromkeys(o["pack_id"] for o in s.get("owner", []) if o.get("pack_id")))
    affirmed = list(dict.fromkeys(p for r in s.get("renders", []) for p in r.get("affirmed_in", [])))
    return {**s, "packs": packs, "affirmed_in": affirmed, "detached": not s.get("owner")}


def link(out, lib, pid, sticker_ids, user="local", unlink=False):
    with _LOCK:
        s = migrate(out, lib, pid)
        if unlink:
            if not isinstance(sticker_ids, list):
                raise SetError("sticker_ids must be a list")
            ids = {x.get("sticker_id") if isinstance(x, dict) else x for x in sticker_ids}
            s["owner"] = [o for o in s["owner"] if o["sticker_id"] not in ids]
        else:
            rows = owners_for(lib, sticker_ids)
            s["owner"] += [o for o in rows if o not in s["owner"]]
        hist(s, user, "UNLINK" if unlink else "LINK", detail=sticker_ids)
        _write(out, s)
        _sync_links(lib, s)
    return view(out, lib, pid)


def for_sticker(out, lib, pack_id, sticker_id):
    from . import effects
    legacy = effects.for_sticker(out, lib, pack_id, sticker_id)
    mine = [s for s in list_sets(out, lib) if any(o["sticker_id"] == sticker_id for o in s["owner"])]
    runs = {}
    for cell in legacy.get("created", []):
        eid = cell["effect"]
        run = runs.setdefault(eid, {"effect": eid, "mode": cell.get("mode"), "cells": [], "saved": [], "imported_as": []})
        run["cells"].append(cell)
    for saved in legacy.get("saved", []):
        eid = saved.get("effect")
        if not eid:
            continue
        run = runs.setdefault(eid, {"effect": eid, "mode": saved.get("mode"), "cells": [], "saved": [], "imported_as": []})
        run["saved"].append(saved)
    for run in runs.values():
        run["imported_as"] = [s["id"] for s in mine if (s.get("source") or {}).get("effect") == run["effect"]
                              or any(str(c.get("import", "")).startswith(run["effect"] + "/") for c in s.get("cells", []))]
    return {**legacy, "sets": mine, "runs": list(runs.values()), "affirmed": any(pack_id in s["affirmed_in"] for s in mine),
            **rows_for_sticker(out, lib, sticker_id)}


def counts_for_pack(out, lib, pack_id):
    """{sticker id: {created, saved, sets}} for the pack grid's badge: one per particle VERSION of the sticker (a set it owns, saved or draft) plus one per
    older run not yet adopted as a row (a run counts once, never once per slice); `saved` is the pack stickers made from its bursts."""
    from . import effects
    legacy = effects._gallery(out, lib, pack_id)
    sets = list_sets(out, lib)
    adopted = {(x.get("source") or {}).get("effect") for x in sets}
    counts = {}
    for st in _lib_pack(lib, pack_id).get("stickers", []):
        n = sum(any(o["sticker_id"] == st["id"] for o in x["owner"]) for x in sets)
        g = legacy.get(st["id"]) or {}
        runs = {i["effect"] for i in g.get("created") or [] if i.get("usable") and i["effect"] not in adopted}
        saved = sum(1 for i in g.get("saved") or [] if not i.get("missing"))
        if n or runs or saved:
            counts[st["id"]] = {"created": n + len(runs), "saved": saved, "sets": n}
    return counts


def _generation_cells(out, lib, gn, db):
    from . import pipeline
    res = pipeline.read_result(out, gn)
    cells = []
    for st in res.get("stickers", []):
        if st.get("status") != "READY":
            continue
        matches = [(p, x) for p in db.get("packs", []) for x in p.get("stickers", [])
                   if str((x.get("source") or {}).get("generation", "")).upper() == f"G{gn:03d}" and (x.get("source") or {}).get("index") == st["index"]]
        matches.sort(key=lambda pair: pair[1].get("type") == "animated")
        p, x = matches[0] if matches else ({}, {})
        det = for_sticker(out, lib, p["id"], x["id"]) if matches else {"sets": [], "affirmed": False}
        cells.append({"index": st["index"], "key": st.get("key"), "png": st.get("png"), "sticker": x or None,
                      "link": {"pack_id": p["id"], "pack": p.get("name"), "sticker": x} if matches else None,
                      "offer_approve": not matches, **det})
    return cells


def for_generation(out, lib, gid):
    """The batch section's one read: per READY cell its library sticker (or the approval offer) and its particle rows; `family`: the cells of the other batches
    of its family (flow/groups.py) that are in a pack, so the variations of one idea share one particle list."""
    from . import groups
    gn = int(str(gid).upper().lstrip("G"))
    with lib.lock:
        db = lib._load()
    family = []
    for other in groups.members(out, gn):
        if other != gn:
            family += [{**c, "generation": f"G{other:03d}"} for c in _generation_cells(out, lib, other, db) if c["link"]]
    return {"generation": f"G{gn:03d}", "cells": _generation_cells(out, lib, gn, db), "family": family}


def detach_pack(out, lib, pack_id):
    """Purge detaches links, including trashed sets; it never deletes a set."""
    for root in (sets_dir(out), trash_dir(out)):
        for f in root.glob("P*/set.json"):
            s = json.loads(atomic.read_text(f))
            changed = _normalize_owner(out, lib, s, f)
            if not any(o.get('pack_id') == pack_id for o in s['owner']):
                if changed:
                    atomic.write_text(f, json.dumps(s, indent=2, ensure_ascii=False))
                continue
            s["owner"] = [o for o in s.get("owner", []) if o.get("pack_id") != pack_id]
            s.pop("packs", None)
            hist(s, "python", "DETACH_PURGED_PACK", detail=pack_id)
            atomic.write_text(f, json.dumps(s, indent=2, ensure_ascii=False))


def hist(s: dict, actor: str, decision: str, reason: str | None = None, detail=None) -> None:
    s.setdefault("history", []).append({"ts": round(time.time(), 3), "actor": actor, "decision": decision, "reason": reason, "detail": detail})


def _clean_name(name, fallback: str = "") -> str:
    n = " ".join(str(name or "").split())[:MAX_NAME].strip()
    return n or fallback


def _clean_elements(elements) -> list[str]:
    if elements is None:
        return []
    if not isinstance(elements, list) or any(not isinstance(x, str) for x in elements):
        raise SetError("elements must be a list of particle names")
    out: list[str] = []
    for e in elements:
        e = " ".join(str(e).split())[:60].strip(" .,;")
        if e and e.lower() not in {x.lower() for x in out}:
            out.append(e)
    return out[:MAX_ELEMENTS]


def _lib_pack(lib, pid: str) -> dict:
    """One pack of the library (the same call `flow/effects.py` makes), or `SetError` 404 when there is no such pack."""
    from ..media.library import LibraryError
    with lib.lock:
        db = lib._load()
    try:
        return lib._pack(db, pid)
    except LibraryError as ex:
        raise SetError(str(ex), getattr(ex, "code", 404)) from ex


def _clean_packs(packs, lib=None) -> list[str]:
    """Deprecated input: packs whose stickers will be linked. With a library, every id must exist (a typo would be a set that belongs to nothing and looks assigned)."""
    if packs is None:
        return []
    if not isinstance(packs, list) or any(not isinstance(x, str) for x in packs):
        raise SetError("packs must be a list of pack ids")
    out: list[str] = []
    for p in packs:
        p = str(p)
        if lib is not None:
            _lib_pack(lib, p)
        if p not in out:
            out.append(p)
    return out


def _clean_motion(motion) -> dict:
    """The set's default motion: {preset, params}. The preset must be one the engine has (`engine/particles.PRESETS`); the params are the engine's own
    `ParticleParams` keys, so a set's motion can never mean something the renderer does not read."""
    if not isinstance(motion, dict):
        return {}
    from ..engine import particles
    out = {}
    if "preset" in motion:
        p = str(motion["preset"])
        if p not in particles.PRESETS:
            raise SetError(f"preset must be one of {', '.join(particles.PRESETS)}")
        out["preset"] = p
    params = motion.get("params")
    if isinstance(params, dict):
        try:
            from . import effects as fx
            _, _, dynamics = fx.split_fit({k: v for k, v in params.items() if k != "preset"})
            particles.ParticleParams.from_dict(dynamics)
        except (ValueError, fx.EffectError) as e:
            raise SetError(str(e))
        out["params"] = params
    return out


# ---------- what a person sees ----------
def view(out: Path, lib, pid: str) -> dict:
    """The set as the API shows it: the record with the cell files resolved to `/out/` urls, how many cells there are and how many are picked
    (the wizard's numbers), `used_in` the names of the packs it is assigned to, and `trashed: false`. A pure read: nothing is rendered, nothing is written."""
    s = _derived(migrate(out, lib, pid))
    d = _dir(out, pid)
    cells = [cell_view(out, d, c) for c in s.get("cells") or []]
    picked = [c["n"] for c in cells if c.get("picked")]
    return {**s, "cells": cells, "n_cells": len(cells), "n_picked": len(picked), "picked": picked, "renders": [render_view(out, s["id"], r) for r in s.get("renders") or []],
            "used_in": pack_names(lib, s.get("packs") or []), "drawing": drawing(s), "trashed": False}


def cell_view(out: Path, d: Path, c: dict, base: str = "particles") -> dict:
    """A cell with its file resolved to an `/out/` url (`base` is the folder the set lives in: `particles`, or `trash/particles` for a deleted one), and `missing` when the file is gone."""
    f = d / (c.get("file") or "")
    ok = bool(c.get("file") and f.is_file())
    clip = d / (c.get('clip') or '')
    animated = bool(c.get('clip') and clip.is_file())
    clock = c.get('fps') or 30.0
    return {**c, "url": f"/out/{base}/{d.name}/{c['file']}" if ok else None, "missing": not ok,
            "clip_url": f"/out/{base}/{d.name}/{c['clip']}" if animated else None,
            "type": "animated" if animated else "static", "fps": clock if animated else None,
            "duration": round(c['frames'] / clock, 3) if animated and c.get('frames') else None}


def pack_names(lib, ids) -> list[dict]:
    """The packs a set is in, by id and name (the Library card says "used in: Barbie, Princess"). A pack that is gone is marked `missing`, never dropped silently."""
    out = []
    for p in ids:
        try:
            pk = _lib_pack(lib, p) if lib is not None else {"name": p}
            out.append({"id": p, "name": pk.get("name") or p})
        except SetError:
            out.append({"id": p, "name": p, "missing": True})
    return out


def list_sets(out: Path, lib) -> list[dict]:
    """Every set that is not in the trash, newest first: the Library > Particles cards (cells strip, name, used_in, cost, created)."""
    rows = []
    for p in sorted(sets_dir(out).glob("P[0-9]*")):
        try:
            s = _derived(migrate(out, lib, p.name))
        except (OSError, ValueError):
            continue
        d = p
        cells = [cell_view(out, d, c) for c in s.get("cells") or []]
        picked = [c for c in cells if c.get("picked")]
        rows.append({"id": s.get("id", p.name), "name": s.get("name") or p.name, "created": s.get("created"), "user": s.get("user"),
                     "owner": s.get("owner", []), "affirmed_in": s.get("affirmed_in", []), "detached": s.get("detached"), "kind": (s.get("source") or {}).get("kind"), "elements": list(s.get("elements") or []), "packs": list(s.get("packs") or []),
                     "used_in": pack_names(lib, s.get("packs") or []), "cells": cells, "picked": [c["n"] for c in picked],
                     "n_cells": len(cells), "n_picked": len(picked), "renders": len(s.get("renders") or []),
                     "credits": s.get("credits"), "drawing": drawing(s), "sheets": list(s.get("sheets") or []), "motion": s.get("motion") or {}, "source": s.get("source") or {}, "trashed": False})
    rows.sort(key=lambda r: (r.get("created") or 0, r["id"]), reverse=True)
    return rows


def for_pack(out: Path, lib, pack_id: str) -> dict:
    """The pack's particle studio in one read: the union of its stickers’ owned sets and every burst rendered FOR it. A burst belongs to the pack it was rendered for,
    whether or not its set is assigned there (a stand-alone set can burst for a pack)."""
    all_ = list_sets(out, lib)
    mine = [r for r in all_ if pack_id in (r.get("packs") or [])]
    bursts = []
    for r in all_:
        try:
            s = read(out, r["id"])
        except SetError:
            continue
        for b in s.get("renders") or []:
            if b.get("pack_id") != pack_id:
                continue
            v = render_view(out, r["id"], b)
            bursts.append({"set": r["id"], "set_name": r["name"], **{k: v.get(k) for k in ("id", "preset", "status", "bytes", "warnings", "blocks", "checks", "params", "added_to", "created", "url", "missing")}})
    bursts.sort(key=lambda b: (b.get("created") or 0, b["set"], b.get("id") or ""), reverse=True)
    return {"pack_id": pack_id, "sets": mine, "bursts": bursts}


# ---------- make one: the working session becomes a durable asset ----------
def _png_size(data: bytes) -> tuple[int, int]:
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def _batch_rows(out: Path, gn: int, picked=None) -> tuple[list, dict, dict | None]:
    """(cell index, slice file, sticker) of the cells of batch G{gn} that have a picture, the TIGHT sprites of those cells (`pipeline.particle_sprites`: cropped out of the keyed sheet, never the 512 px
    sticker), and the batch's result (None when it cannot be read). With `picked` only those cells."""
    from . import pipeline as pl
    try:
        res = pl.read_result(out, gn)
    except Exception:
        return [], {}, None
    d = pl.gen_dir(out, gn)
    chosen = {int(i) for i in picked} if picked else None
    rows = []
    for st in res.get("stickers") or []:
        f = d / (st.get("png") or "")
        if not st.get("png") or not f.is_file() or (chosen is not None and int(st["index"]) not in chosen):
            continue
        rows.append((int(st["index"]), f, st))
    return sorted(rows, key=lambda r: r[0]), (pl.particle_sprites(out, gn, res) if rows else {}), res


def _cell_record(n: int, idx: int, data: bytes, sprite: bool, st: dict, extra: dict | None = None) -> dict:
    """The record of a stored cell: where it came from (`cell`), whether it is a tight sprite (the only thing a particle should be; the slice file is the fallback of a batch with no keyed
    sheet), its size, and what Python noticed."""
    w, h = _png_size(data)
    return {"n": n, "cell": idx, "file": f"cells/c{n:02d}.png", "status": st.get("status"), "warnings": list(((st.get("metrics") or {}).get("warnings")) or []), "key": st.get("key"),
            "sprite": sprite, "w": w, "h": h, "picked": True, "created": round(time.time(), 3), **(extra or {})}


def set_from_effect(out: Path, lib, eid: str, *, name=None, packs=None, owners=None, target=None, picked=None, mode=None, user: str = "local") -> dict:
    """**Use as particle set** (`POST /api/particles {from_effect: E###}`): save what this run drew as a durable `P###`.

    The drawn sheet's cells become the set's cells as TIGHT SPRITES (copied into `out/particles/P###/cells/`, so the set survives the `E###` and the batch; a particle is never a 512 px sticker
    canvas); a set made of the pack's own stickers has no cells file yet (its particles are the stickers, recorded in `source.kind: stickers`); a video set's cells are the
    keyed cell clips and its render is the clip itself. `packs` defaults to the effect's own pack. Nothing is deleted: the effect and its batch stay."""
    from . import effects as fx
    e = fx.view(out, eid)
    s0 = fx.set_of(e) or {}
    gen = s0.get("generation")
    if mode is not None and mode not in ('video', 'sim'):
        raise SetError('mode must be video or sim')
    kind = "video" if (mode or e.get("mode")) == "video" else "drawn"
    notes: list[str] = []
    rows, sprites = ([], {}) if gen is None or kind == 'video' else _batch_rows(out, int(gen), picked)[:2]
    if not rows:
        kind = "stickers" if kind != "video" else kind
        notes.append("This set has no drawn cells yet: the burst uses the pack's own stickers (generate more to add drawn particles).")
    default_name = f"{e.get('pack_name') or 'particles'} particles"
    if owners is None and packs is None and lib is not None:
        with lib.lock:
            db = lib._load()
        live = {st['id'] for pk in db.get('packs', []) for st in pk.get('stickers', [])}
        resolved_owners = owners_for(lib, [st['sticker_id'] for st in e.get('stickers', []) if st['sticker_id'] in live])
    else:
        resolved_owners = owners_for(lib, owners, packs)
    with _LOCK:
        previous = read(out, target) if target else None
        pid = previous["id"] if previous else _next_id(out)
        offset = max([c["n"] for c in (previous or {}).get("cells", [])] or [0])
        d = sets_dir(out) / pid
        (d / "cells").mkdir(parents=True, exist_ok=True)
        cells: list[dict] = []
        for idx, f, st in rows:
            token = f"{eid}/G{gen}/S{idx}"
            if token in ((previous or {}).get("imports") or []):
                continue
            n = offset + len(cells) + 1
            data = sprites.get(idx) or f.read_bytes()
            atomic.write_bytes(d / f"cells/c{n:02d}.png", data)
            cells.append(_cell_record(n, idx, data, idx in sprites, st, {"import": token}))
        if kind == "video":
            keep = None if picked is None else {int(i) for i in picked}
            for r in e.get("results", []):
                if r.get("mode") != "video" or not r.get("file"):
                    continue
                if keep is not None and int(r.get('cell', 0)) not in keep:
                    continue
                token = f"{eid}/{r['id']}"
                if token in ((previous or {}).get("imports") or []):
                    continue
                n = offset + len(cells) + 1
                animation = None
                if r.get("sprite_file"):
                    data = (fx._dir(out, eid) / r["sprite_file"]).read_bytes()
                else:
                    import io
                    import numpy as np
                    from PIL import Image
                    from ..engine import particles
                    animation = _animated_media(fx._dir(out, eid) / r['file'])
                    frames = animation.frames
                    peak = frames[np.argmax((frames[..., 3] > 4).sum(axis=(1, 2)))]
                    poster = particles.trim_sprite(peak)
                    if poster is None:
                        notes.append(f"Cell {r.get('cell')} has no visible pixels; choose another sprite")
                        continue
                    buf = io.BytesIO()
                    Image.fromarray(poster).save(buf, format="PNG")
                    data = buf.getvalue()
                atomic.write_bytes(d / f"cells/c{n:02d}.png", data)
                rec = _cell_record(n, r.get("cell", n), data, True, {"status": r["status"], "key": r.get("key"), "metrics": {"warnings": r.get("warnings", [])}})
                if r.get("file"):
                    clip = f"cells/c{n:02d}.webm"
                    atomic.write_bytes(d / clip, (fx._dir(out, eid) / r["file"]).read_bytes())
                    rec["clip"] = clip
                    rec['fps'] = r.get('fps') or 30.0
                    rec['frames'] = r.get('frames') or 90
                    if r.get('timeline_file'):
                        rec['timeline'] = f"cells/c{n:02d}.npz"
                        atomic.write_bytes(d / rec['timeline'], (fx._dir(out, eid) / r['timeline_file']).read_bytes())
                    elif animation is not None:
                        rec['timeline'] = f"cells/c{n:02d}.npz"
                        rec['fps'], rec['frames'] = animation.fps, len(animation.frames)
                        buf = io.BytesIO()
                        np.savez_compressed(buf, frames=animation.frames, fps=animation.fps)
                        atomic.write_bytes(d / rec['timeline'], buf.getvalue())
                rec["import"] = token
                rec["job"] = r.get("job") or (e.get('video', {}).get(r.get('group')) or {}).get('job')
                cells.append(rec)
            s0 = {**s0, "job": next((v.get("job") for v in e.get("video", {}).values() if v.get("job")), None)}
        pack_ids = list(dict.fromkeys(o['pack_id'] for o in resolved_owners))
        s = {"id": pid, "name": _clean_name(name, default_name), "created": round(time.time(), 3), "user": user,
             "elements": _clean_elements(s0.get("elements") or [x for g in e.get("groups") or [] for x in g.get("elements") or []]),
             "source": {"sticker_ids": [x["sticker_id"] for x in e.get("stickers") or []], "kind": kind, "effect": eid, "generation": f"G{int(gen):03d}" if gen is not None else None, "particles": bool(cells) and all(c["sprite"] for c in cells),
                        "job": s0.get("job"), "grid": s0.get("grid"), "by": s0.get("by"), "options": list(s0.get("options") or [])},
             "plan": {"subject": fx._subject_of(e), "style": fx._style_of(e)},          # what *Generate more* draws about, kept here so the set outlives its run
             "cells": cells, "motion": {}, "saved_at": None, "owner": resolved_owners, "renders": [], "credits": _job_cost(out, s0.get("job")) if cells else 0.0, "notes": notes, "history": []}
        if not cells and kind == "stickers":                 # the particles ARE the stickers the run was made with: remember which (a burst needs them long after the run)
            s["source"]["sticker_ids"] = [x["sticker_id"] for x in e.get("stickers") or []]
        if previous:
            s = {**previous, "cells": previous.get("cells", []) + cells,
                 "source": {**(previous.get("source") or {}), **s["source"]},
                 "credits": round(float(previous.get("credits") or 0) + (s["credits"] if cells else 0), 3)}
        if kind == "video":
            old_source = (previous or {}).get('source') or {}
            old_jobs = list(dict.fromkeys(list(old_source.get('jobs') or []) + ([old_source['job']] if old_source.get('job') else [])
                                         + [sh['job'] for sh in (previous or {}).get('sheets', []) if sh.get('job')]))
            new_jobs = list(dict.fromkeys(c.get('job') for c in cells if c.get('job')))
            if cells and not new_jobs and s0.get('job'):
                new_jobs = [s0['job']]
            all_jobs = list(dict.fromkeys(old_jobs + new_jobs))
            s["source"]["jobs"] = all_jobs
            s["source"]["job"] = all_jobs[-1] if all_jobs else None
            def recorded_cost(job):
                return _job_cost(out, job) or next((float(v.get('cost') or 0) for v in e.get('video', {}).values() if v.get('job') == job), 0.0)
            s["credits"] = round(float((previous or {}).get('credits') or 0) + sum(recorded_cost(j) for j in new_jobs if j not in old_jobs), 3)
        s.setdefault("imports", []).extend(c["import"] for c in cells if c.get("import"))
        hist(s, user, "APPEND" if previous else "CREATE", f"from {eid}: {len(cells)} cell(s), {len(pack_ids)} pack(s)", {"effect": eid, "generation": s["source"].get("generation")})
        atomic.write_text(d / "set.json", json.dumps(s, indent=2, ensure_ascii=False))
    with fx._LOCK:                                           # the run points at the set saved from it (the wizard goes on with the set: its motion and finish steps are the set's)
        rec = fx.read(out, eid)
        rec.setdefault("sets", [])
        if pid not in rec["sets"]:
            rec["sets"].append(pid)
        fx.hist(rec, user, "SAVE", f"saved as particle set {pid}", {"set": pid})
        fx._write(out, rec)
    return view(out, lib, pid)


def set_from_generation(out: Path, lib, gid, *, name=None, packs=None, owners=None, picked=None, user: str = "local") -> dict:
    """The import path for a sheet of particles that is already a batch (`POST /api/particles {from_generation: G###}`): its cells become a set, as tight sprites, with no effect behind it. Only a
    batch that was CUT AS PARTICLES (exact equal cells, no sticker rule) qualifies: a sheet cut as stickers (gutter detection, Python's sticker blocks, the 512 px canvas) is refused with the
    reason and the free way out (cut it again as particles), never guessed at. Nothing is changed or deleted in the batch."""
    from . import pipeline as pl
    try:
        gn = int(str(gid).upper().lstrip("G"))
    except ValueError:
        raise SetError("from_generation must be a batch id like G101")
    rows, sprites, res = _batch_rows(out, gn, picked)
    if res is None:
        raise SetError(f"no batch G{gn:03d}", 404)
    if res.get("kind") != "particles":
        raise SetError(f"G{gn:03d} was cut as stickers (its layout was detected and Python's sticker rules ran on every cell), so its cells are not particles yet. Cut it again as particles, free: "
                       f"POST /api/generations/{gn}/recut_particles. The batch is not changed.", 409)
    if not rows:
        raise SetError(f"no cell of G{gn:03d} has a picture" + (" among the ones you picked" if picked else "") + ": a cell with nothing in it cannot be a particle.", 409)
    owners_for(lib, owners, packs)
    with _LOCK:
        pid = _next_id(out)
        d = sets_dir(out) / pid
        (d / "cells").mkdir(parents=True)
        cells = []
        for n, (idx, f, st) in enumerate(rows, 1):
            data = sprites.get(idx) or f.read_bytes()
            atomic.write_bytes(d / f"cells/c{n:02d}.png", data)
            cells.append(_cell_record(n, idx, data, idx in sprites, st))
        s = {"id": pid, "name": _clean_name(name, f"{res.get('prompt') or 'particles'}"), "created": round(time.time(), 3), "user": user, "elements": [],
             "source": {"kind": "drawn", "effect": None, "generation": f"G{gn:03d}", "particles": all(c["sprite"] for c in cells), "job": None, "grid": res.get("grid")},
             "cells": cells, "motion": {}, "saved_at": None, "owner": owners_for(lib, owners, packs), "renders": [], "credits": 0.0, "notes": [], "history": []}
        hist(s, user, "CREATE", f"from G{gn:03d}: {len(cells)} cell(s)", {"generation": f"G{gn:03d}"})
        atomic.write_text(d / "set.json", json.dumps(s, indent=2, ensure_ascii=False))
    return view(out, lib, pid)


def _job_cost(out: Path, job) -> float:
    """What the sheet job behind a drawn set cost (the Library card says "about N credits spent"); 0.0 when there is no job or no cost was recorded."""
    if not job:
        return 0.0
    from ..generation import jobs
    try:
        return float(jobs.read(out, str(job)).get("cost") or 0.0)
    except Exception:
        return 0.0


def set_from_slices(out, lib, slices, *, owners=None, name=None, target=None, user="local"):
    """Copy selected batch slices into a new or existing sticker-owned set. Free."""
    if not isinstance(slices, list) or not slices:
        raise SetError("select at least one slice")
    rows = []
    for item in slices:
        gn = int(str(item["generation"]).upper().lstrip("G"))
        batch, sprites, _ = _batch_rows(out, gn, [int(item["index"])])
        rows += [(idx, sprites.get(idx) or f.read_bytes(), idx in sprites, st, gn) for idx, f, st in batch]
    if not rows:
        raise SetError("the selected slices have no picture", 409)
    with _LOCK:
        v = view(out, lib, target) if target else create(out, lib, owners=owners, name=name, kind="stickers", user=user)
        s = read(out, v["id"])
        n = max([c["n"] for c in s.get("cells", [])] or [0])
        for idx, data, sprite, st, gn in rows:
            n += 1
            atomic.write_bytes(_dir(out, s["id"]) / f"cells/c{n:02d}.png", data)
            s["cells"].append(_cell_record(n, idx, data, sprite, st, {"generation": f"G{gn:03d}"}))
        s["source"].setdefault("slices", []).extend(slices)
        hist(s, user, "APPEND_SLICES", detail=slices)
        _write(out, s)
    return view(out, lib, s["id"])


def set_from_stickers(out, lib, sticker_ids, parent_pack_id, *, owners=None, name=None, target=None, user="local"):
    """Recover ordinary library stickers as tight particle sprites, preserving the originals."""
    import io
    import numpy as np
    from PIL import Image
    from . import effects
    from ..engine import particles
    if not isinstance(sticker_ids, list) or not sticker_ids:
        raise SetError('select at least one sticker')
    owners = owners_for(lib, owners, packs=[parent_pack_id])
    if not owners:
        raise SetError('choose a parent pack with stickers')
    with lib.lock:
        db = lib._load()
    rows = []
    for sid in dict.fromkeys(sticker_ids):
        st = next((st for p in db.get('packs', []) for st in p.get('stickers', []) if st['id']==sid), None)
        if st is None:
            raise SetError(f'no source sticker {sid}', 404)
        clip = (lib.files / st['file']) if st.get('type') == 'animated' or Path(st['file']).suffix.lower() == '.webm' else None
        animation = None
        if clip is not None:
            try:
                animation = _animated_media(clip)
                rgba = animation.frames[np.argmax((animation.frames[..., 3] > 4).sum(axis=(1, 2)))]
            except (OSError, ValueError, RuntimeError) as ex:
                raise SetError(f"{st.get('name') or sid} has no readable animation: {ex}. Choose another sticker", 409) from ex
        else:
            rgba = effects.sticker_rgba(lib, st)
        if rgba is None:
            raise SetError(f"{st.get('name') or sid} has no readable picture", 409)
        poster = particles.trim_sprite(rgba)
        if poster is None:
            raise SetError(f"{st.get('name') or sid} has no visible picture. Choose another sticker", 409)
        buf = io.BytesIO()
        Image.fromarray(poster).save(buf, format='PNG')
        rows.append((st, buf.getvalue(), clip, animation))
    with _LOCK:
        v = view(out, lib, target) if target else create(out, lib, owners=owners, name=name or 'Recovered particles', kind='stickers', user=user)
        s = read(out, v['id'])
        n = max([c['n'] for c in s['cells']] or [0])
        for st, data, clip, animation in rows:
            n += 1
            atomic.write_bytes(_dir(out, s['id']) / f'cells/c{n:02d}.png', data)
            rec = _cell_record(n, (st.get('source') or {}).get('index') or n, data, True, {'status':'READY','key':st.get('name')})
            rec['source_sticker'] = st['id']
            rec['source_pack'] = next(p['id'] for p in db.get('packs', []) if any(x['id'] == st['id'] for x in p.get('stickers', [])))
            if clip is not None:
                rec['clip'] = f"cells/c{n:02d}{clip.suffix.lower()}"
                atomic.write_bytes(_dir(out, s['id']) / rec['clip'], clip.read_bytes())
                rec['fps'], rec['frames'] = animation.fps, len(animation.frames)
                rec['timeline'] = f"cells/c{n:02d}.npz"
                buf = io.BytesIO()
                np.savez_compressed(buf, frames=animation.frames, fps=animation.fps)
                atomic.write_bytes(_dir(out, s['id']) / rec['timeline'], buf.getvalue())
            s['cells'].append(rec)
        s['source'].setdefault('recovered_stickers', []).extend(sticker_ids)
        hist(s, user, 'RECOVER_STICKERS', detail={'stickers':sticker_ids,'parent_pack_id':parent_pack_id})
        _write(out, s)
    if target:
        link(out, lib, target, owners, user)
    return view(out, lib, s['id'])


def create(out: Path, lib, *, name=None, elements=None, packs=None, owners=None, kind: str = "drawn", user: str = "local") -> dict:
    """A stand-alone set the person names now (no sheet yet): `Library > Particles > New particle set`. Its cells arrive with *Generate more*."""
    if kind not in KINDS:
        raise SetError(f"kind must be one of {', '.join(KINDS)}")
    owners_for(lib, owners, packs)
    with _LOCK:
        pid = _next_id(out)
        d = sets_dir(out) / pid
        (d / "cells").mkdir(parents=True)
        s = {"id": pid, "name": _clean_name(name, "particles"), "created": round(time.time(), 3), "user": user, "elements": _clean_elements(elements),
             "source": {"kind": kind}, "cells": [], "motion": {}, "saved_at": None, "owner": owners_for(lib, owners, packs), "renders": [], "credits": 0.0, "notes": [], "history": []}
        if kind == "stickers":
            s["source"]["sticker_ids"] = [o["sticker_id"] for o in s["owner"]]
        hist(s, user, "CREATE", f"a new set ({kind})")
        atomic.write_text(d / "set.json", json.dumps(s, indent=2, ensure_ascii=False))
    return view(out, lib, pid)


# ---------- Generate more: another sheet, whose cut cells are APPENDED ----------
# `set.sheets[]` is the list of sheets drawn FOR this set after it was saved: {n, job, generation, grid, elements, status, estimate, cost, by, created, appended[], skipped[], error}.
# REQUESTED (the job exists) -> DRAWN (it became a batch, being cut) -> DONE (the cells are in the set). NO_CELLS (cut, but no cell has a picture: the free "Cut it anyway" of the batch may
# still change that) and FAILED (the job failed) are looked at again on every read, so the set always reflects the stored batch and job (rule 11: reproducible from stored data).
OPEN_SHEETS = ("REQUESTED", "DRAWN")


def drawing(s: dict) -> bool:
    return any(sh.get("status") in OPEN_SHEETS for sh in s.get("sheets") or [])


_REQ_LEAD = re.compile(r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:create|make|generate|draw|give\s+me|i\s+want|i\s+need|build|do)?\s*"
                       r"(?:me\s+)?(?:some\s+|a\s+set\s+of\s+|a\s+|an\s+)?(?:new\s+)?(?:particle\s+effects?|particle\s+sets?|particles?|reaction\s+effects?|reaction\s+sets?|reactions?|bursts?)?\s*(?:for|of|with|from|showing|:)?\s*", re.I)
_REQ_SPLIT = re.compile(r"\s*(?:,|;|&|\+|/|\band\b|\bplus\b|\bwith\b)\s*", re.I)


def _singular(word: str) -> str:
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ches", "shes", "xes", "sses")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def request_elements(text: str, limit: int) -> list[str]:
    """The reactions a plain request names: "create particles for lipsticks and ribbons" -> ["lipstick", "ribbon"] (and "reactions for laugh, cry" -> ["laugh", "cry"]). The lead-in ("create particles
    for") goes, the rest splits on commas, "and", "&", "+", "/"; each is singular (a sprite is one thing), at most MAX_WORDS words, at most `limit`
    (the sheet's cells). Deterministic: no model is asked, so the same words always give the same set."""
    from ..generation import effect_prompts as ep
    t = _REQ_LEAD.sub("", " ".join(str(text or "").split()))
    t = re.sub(r"\b(?:particle\s+effects?|particles?|reaction\s+effects?|reactions?)\b", " ", t, flags=re.I)
    out: list[str] = []
    for part in _REQ_SPLIT.split(t):
        words = re.sub(r"[^\w\s'-]", " ", part).split()
        words = [w for w in words if w.lower() not in ("some", "a", "an", "the", "of", "little", "tiny", "small", "please")][: ep.MAX_WORDS]
        if not words:
            continue
        name = " ".join(words[:-1] + [_singular(words[-1])]).lower()
        if name not in out:
            out.append(name)
    return out[: max(1, int(limit))]


def from_request(out: Path, lib, text: str, grid="2x2", kind: str = "drawn", user: str = "local") -> dict:
    """A stand-alone particle set from a plain request (the Studio's Particles choice, the chat's "particles on their own"): the elements it names,
    no sticker owns it (`owner: []`), and its `plan` is what its sheet is drawn from. Free: the sheet is priced and made by `more` (estimate, then go)."""
    from . import effects as fx
    from ..generation import effect_prompts as ep
    try:
        rows, cols = fx._grid_of(grid)
    except fx.EffectError as ex:
        raise SetError(str(ex))
    els = request_elements(text, rows * cols)
    if not els:
        raise SetError("name the particles, for example: particles for lipsticks and ribbons")
    try:
        plan = ep.lint_plan({"subject": " and ".join(els), "elements": els, "style": None})
    except ValueError as ex:
        raise SetError(str(ex))
    v = create(out, lib, name=", ".join(e.title() for e in plan["elements"]) + " particles", elements=plan["elements"], kind=kind, user=user)
    with _LOCK:
        s = read(out, v["id"])
        s["plan"] = plan
        s["request"] = " ".join(str(text or "").split())[:300]
        hist(s, user, "PLAN", f"from the request: {', '.join(plan['elements'])}", {"grid": f"{rows}x{cols}"})
        _write(out, s)
    return {**view(out, lib, v["id"]), "plan": plan, "grid": f"{rows}x{cols}"}


def _subject_style(out: Path, lib, s: dict) -> tuple[str, str | None]:
    """What a new sheet for this set is drawn about: what the set was saved with, else its run's, else its first pack's name, else its own name (without the word particles)."""
    p = s.get("plan") or {}
    if p.get("subject"):
        return p["subject"], p.get("style")
    eid = (s.get("source") or {}).get("effect")
    if eid:
        from . import effects as fx
        try:
            e = fx.read(out, eid)
            return fx._subject_of(e), fx._style_of(e)
        except fx.EffectError:
            pass
    if lib is not None:
        names = [n["name"] for n in pack_names(lib, s.get("packs") or []) if not n.get("missing")]
        if names:
            return names[0], None
    return " ".join(w for w in str(s.get("name") or "").split() if w.lower() not in ("particles", "particle")) or "the emoji", None


def _emoji_for(lib, s: dict) -> str:
    """The emoji tag of the cells of a particle sheet (a cell of a plan needs one): the commonest emoji of the packs the set is in, else 🙂 (the same fallback an effect's sheet has)."""
    from collections import Counter
    c: Counter = Counter()
    for pid in s.get("packs") or []:
        try:
            pk = _lib_pack(lib, pid) if lib is not None else {}
        except SetError:
            continue
        c.update(st.get("emoji") for st in pk.get("stickers") or [] if st.get("emoji"))
    return c.most_common(1)[0][0] if c else "🙂"


def more_plan(out: Path, lib, pid: str, grid=None, elements=None) -> dict:
    """What the page shows before anything is spent: the prompt that will be sent, the cells, the screen colour, the grid, `outline: 0`, `picks` (the particles the sheet is drawn from).
    Without `elements` it draws the set's own (the first ones when it has more than the sheet has cells); the person's own pick must fit the sheet (400). The price is asked by the caller."""
    from . import effects as fx
    from ..generation import effect_prompts as ep
    s = read(out, pid)
    last = (s.get("sheets") or [{}])[-1].get("grid") or (s.get("source") or {}).get("grid")
    try:
        rows, cols = fx._grid_of(grid if grid else last)
    except fx.EffectError as ex:
        raise SetError(str(ex))
    subject, style = _subject_style(out, lib, s)
    fresh = elements is not None
    names = elements if fresh else list(s.get("elements") or [])
    if not isinstance(names, list) or any(not isinstance(x, str) for x in names):
        raise SetError("elements must be a list of particle names")
    try:
        picks = ep.lint_plan({"subject": subject, "elements": names, "style": style})["elements"]
    except ValueError as ex:
        raise SetError(str(ex))
    if len(picks) > rows * cols:
        if fresh:
            raise SetError(f"pick at most {rows * cols} particles for a sheet of {rows * cols} cells (fewer are repeated in other sizes and angles)")
        picks = picks[:rows * cols]
    try:
        d = fx._describe({"subject": subject, "elements": picks, "style": style}, rows, cols)
    except fx.EffectError as ex:
        raise SetError(str(ex))
    return {**d, "id": str(pid).upper(), "n": rows * cols, "outline": 0, "kind": "particles", "picks": picks}


def more_base_plan(out: Path, lib, pid: str, grid, elements) -> dict:
    """The plan the normal sheet path accepts for the set's new sheet (the same builder as an effect's: `effects.sheet_base_plan`), the cells' emoji being the set's packs' commonest."""
    from . import effects as fx
    plan = more_plan(out, lib, pid, grid, elements)
    rows, cols = plan["grid"]
    return fx.sheet_base_plan(plan, rows, cols, _emoji_for(lib, read(out, pid)), "set", plan["id"])


def request_more(out: Path, pid: str, job: str, grid, elements, estimate=None, user: str = "local") -> dict:
    """The sheet job exists: the set says so (REQUESTED), before the job runs, so the page sees it at once and the later link is never overwritten. Nothing else of the set changes."""
    with _LOCK:
        s = read(out, pid)
        sheets = s.setdefault("sheets", [])
        sh = {"n": len(sheets) + 1, "job": job, "generation": None, "grid": [int(grid[0]), int(grid[1])], "elements": list(elements), "status": "REQUESTED", "estimate": estimate,
              "cost": None, "by": user, "created": round(time.time(), 3), "appended": [], "skipped": [], "error": None}
        sheets.append(sh)
        hist(s, user, "MORE", f"particle sheet job {job} ({sh['grid'][0]}x{sh['grid'][1]}): {', '.join(sh['elements'])}"[:300], {"job": job, "sheet": sh["n"]})
        _write(out, s)
    return sh


def link_more(out: Path, pid: str, job: str, generation) -> None:
    """The sheet job is DONE and became a batch (`Console.start_from_job`): the sheet is DRAWN and names its batch. The cells arrive with `settle`, once they are cut."""
    gn = int(str(generation).upper().lstrip("G"))
    with _LOCK:
        s = read(out, pid)
        for sh in s.get("sheets") or []:
            if sh.get("job") == job and sh.get("status") != "DONE":
                sh.update(status="DRAWN", generation=f"G{gn:03d}", error=None)
                hist(s, "python", "LINK", f"sheet {sh['n']} is G{gn:03d}", {"generation": f"G{gn:03d}", "job": job})
                _write(out, s)
                return


def settle(out: Path, pid: str) -> dict:
    """Bring the set up to date with the batches and jobs it is waiting for: every sheet that is not DONE is looked at, and the cells of a sheet that is cut are APPENDED (numbered after the
    last cell, picked; a cell with no picture is skipped and listed). Idempotent: a DONE sheet is never read again, so calling it twice adds nothing twice. Nothing existing is touched.
    Returns the stored set."""
    with _LOCK:
        s = read(out, pid)
        changed = False
        for sh in s.get("sheets") or []:
            if sh.get("status") != "DONE":
                changed = _settle_sheet(out, s, sh) or changed
        if changed:
            _write(out, s)
        return s


def settle_open(out: Path) -> int:
    """`settle` for every set that has a sheet it is still waiting for (the catch-up before the list is read). Returns how many were looked at."""
    n = 0
    for p in sorted(sets_dir(out).glob("P[0-9]*")):
        try:
            s = json.loads(atomic.read_text(p / "set.json"))
        except (OSError, ValueError):
            continue
        if any(sh.get("status") != "DONE" for sh in s.get("sheets") or []):
            try:
                settle(out, p.name)
                n += 1
            except SetError:
                pass
    return n


def _settle_sheet(out: Path, s: dict, sh: dict) -> bool:
    from . import pipeline as pl
    from ..generation import jobs
    was = (sh.get("status"), sh.get("error"), list(sh.get("skipped") or []))
    gen = sh.get("generation")
    if gen is None:                                              # the sheet job has not become a batch (yet)
        try:
            job = jobs.read(out, sh["job"])
        except Exception:
            return False
        if job.get("status") in ("FAILED", "TIMEOUT"):
            sh.update(status="FAILED", error=str(job.get("error") or "the sheet job timed out")[:300])
        elif sh.get("status") == "FAILED":                       # it was requeued
            sh.update(status="REQUESTED", error=None)
        return (sh.get("status"), sh.get("error"), list(sh.get("skipped") or [])) != was
    gn = int(str(gen).upper().lstrip("G"))
    try:
        res = pl.read_result(out, gn)
    except pl.PipelineError:
        return False
    if res.get("stage") != "sliced":
        if res.get("error"):
            sh.update(status="FAILED", error=str(res["error"])[:300])
        return (sh.get("status"), sh.get("error"), list(sh.get("skipped") or [])) != was
    d = pl.gen_dir(out, gn)
    have = [(int(st["index"]), d / st["png"], st) for st in res.get("stickers") or [] if st.get("png") and (d / st["png"]).is_file()]
    skipped = sorted(int(st["index"]) for st in res.get("stickers") or [] if not (st.get("png") and (d / st["png"]).is_file()))
    if not have:
        sh.update(status="NO_CELLS", skipped=skipped, error="No cell of this sheet came out. If its background was not a green screen, open the batch and cut it anyway; otherwise draw it again.")
        return (sh.get("status"), sh.get("error"), list(sh.get("skipped") or [])) != was
    base = max([int(c["n"]) for c in s.get("cells") or []] + [0])
    (sets_dir(out) / s["id"] / "cells").mkdir(parents=True, exist_ok=True)
    sprites = pl.particle_sprites(out, gn, res)                  # tight sprites cut out of the keyed sheet, never the 512 px sticker of the cell
    added = []
    for k, (idx, f, st) in enumerate(sorted(have, key=lambda r: r[0]), 1):
        n = base + k
        data = sprites.get(idx) or f.read_bytes()
        atomic.write_bytes(sets_dir(out) / s["id"] / f"cells/c{n:02d}.png", data)
        s.setdefault("cells", []).append(_cell_record(n, idx, data, idx in sprites, st, {"sheet": sh["n"], "generation": f"G{gn:03d}"}))
        added.append(n)
    try:
        cost = jobs.read(out, sh["job"]).get("cost")
    except Exception:
        cost = None
    cost = float(cost if cost is not None else (sh.get("estimate") or 0))
    s["credits"] = round(float(s.get("credits") or 0) + cost, 3)
    s["elements"] = _clean_elements(list(s.get("elements") or []) + list(sh.get("elements") or []))
    sh.update(status="DONE", appended=added, skipped=skipped, error=None, cost=cost)
    hist(s, "python", "APPEND", f"{len(added)} new cell(s) from G{gn:03d} (sheet {sh['n']})", {"generation": f"G{gn:03d}", "cells": added, "skipped": skipped})
    return True


# ---------- bursts: the engine's simulation, pointed at a set's cells, for a pack ----------
MAX_EMOJI = 20                                                  # Telegram's emoji_list holds 1-20 emoji per sticker


def render_view(out: Path, pid: str, r: dict) -> dict:
    """A rendered burst as the API shows it: the stored record with `set` and the file as an `/out/` url (`missing` when the file is gone)."""
    f = sets_dir(out) / str(pid).upper() / (r.get("file") or "-")
    ok = bool(r.get("file") and f.is_file())
    return {**r, "set": str(pid).upper(), "url": f"/out/particles/{str(pid).upper()}/{r['file']}" if ok else None, "missing": not ok}


def _pick_pack(s: dict, lib, pack_id, required: bool) -> str | None:
    """The pack a burst is for: the one asked for (404 when it does not exist), else the set's only pack. A burst is always FOR a pack, so `required` ones without either are a 400."""
    if pack_id:
        _lib_pack(lib, str(pack_id))
        return str(pack_id)
    packs = _derived(s).get("packs") or []
    if len(packs) == 1:
        return packs[0]
    if required:
        raise SetError("Which pack is the burst for? Send pack_id" + (f" (this set is in {', '.join(n['name'] for n in pack_names(lib, packs))})" if packs else " (this set is in no pack yet)"))
    return None


def sprites_of(out: Path, lib, s: dict, pack_id: str | None, sprite_px=512) -> tuple[list, str]:
    """The particles that fly: the set's PICKED cells (an unpicked cell stays on disk and does not fly). A set of kind "stickers" has no cells: the pack's own stickers are its particles, the ones its run
    was made with (`source.sticker_ids`), else every sticker of `pack_id`. A drawn set with no cell yet has none (409). Returns (RGBA arrays, "cells" | "stickers"); 409/400 with the reason when there is nothing to burst."""
    import numpy as np
    from PIL import Image
    d = sets_dir(out) / s["id"]
    if s.get("cells"):
        pcs = []
        for c in s['cells']:
            if not c.get('picked'):
                continue
            if c.get('clip') and (d / c['clip']).is_file():
                try:
                    pcs.append(_animated_media(d / c['clip'], timeline=d / c['timeline'] if c.get('timeline') else None,
                                               fps=c.get('fps'), px=sprite_px))
                except (OSError, ValueError, RuntimeError) as ex:
                    raise SetError(f"animated sprite {c['n']} could not be decoded: {ex}. Unpick it or import it again", 409) from ex
            elif c.get('file') and (d / c['file']).is_file():
                pcs.append(np.asarray(Image.open(d / c['file']).convert('RGBA'), np.uint8))
        if not pcs:
            raise SetError("none of the picked cells has a picture: pick others", 409)
        return pcs, "cells"
    if (s.get("source") or {}).get("kind") != "stickers":              # a drawn (or video) set with no cell yet has no particles: its stickers must not fly in their place
        raise SetError("this set has no particles yet: draw some with Generate more", 409)
    from . import effects as fx
    with lib.lock:
        db = lib._load()
    every = {x["id"]: x for p in db.get("packs", []) for x in p.get("stickers", [])}
    ids = (s.get("source") or {}).get("sticker_ids")
    if ids:
        picked = [every[i] for i in ids if i in every]
    elif pack_id:
        picked = next((p["stickers"] for p in db.get("packs", []) if p["id"] == pack_id), [])
    else:
        raise SetError("this set's particles are the pack's own stickers: say which pack (pack_id)")
    pcs = []
    for x in picked[:fx.MAX_STICKERS]:
        f = lib.files / x['file']
        if x.get('type') == 'animated' or f.suffix.lower() == '.webm':
            try:
                pcs.append(_animated_media(f, px=sprite_px))
            except (OSError, ValueError, RuntimeError) as ex:
                raise SetError(f"{x.get('name') or x['id']} could not be decoded: {ex}. Choose another sprite", 409) from ex
        else:
            a = fx.sticker_rgba(lib, x)
            if a is not None:
                pcs.append(a)
    if not pcs:
        raise SetError("there are no particles to burst: this set uses the pack's own stickers and none of them has a picture. Draw some with Generate more", 409)
    return pcs, "stickers"


def _burst(s: dict, preset, params) -> tuple[str, int, float, object]:
    """(preset name, sprite_px, scale, ParticleParams): the preset asked for, else the set's default motion, else burst; the set's default params under the person's. Strict, like every other
    contract of the engine: an unknown preset or parameter is a 400 with the reason."""
    from . import effects as fx
    from ..engine import particles
    mo = s.get("motion") or {}
    base = {k: v for k, v in (mo.get("params") or {}).items() if k != "preset"}
    if params is not None and not isinstance(params, dict):
        raise SetError("params must be an object")
    try:
        px, sc, rest = fx.split_fit({**base, **(params or {})})
    except fx.EffectError as ex:
        raise SetError(str(ex))
    name = str(preset or mo.get("preset") or "burst")
    try:
        return name, px, sc, particles.preset(name, **rest)
    except ValueError as ex:
        raise SetError(str(ex))


def preview(out: Path, lib, pid: str, *, pack_id=None, preset=None, params=None, size=256) -> dict:
    """The burst as a small looping WebP, rendered by the same engine as the final file (the sliders are live). Cached by what it was made from (the cells, the params, `sprite_px`, `scale`,
    the size), so the same sliders are the same file. Free."""
    from . import effects as fx
    from ..engine import particles
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not 64 <= size <= 512:
        raise SetError("size must be a whole number from 64 to 512")
    s = read(out, pid)
    pack = _pick_pack(s, lib, pack_id, required=False)
    name, px, sc, p = _burst(s, preset, params)
    pcs, source = sprites_of(out, lib, s, pack, sprite_px=max(1, round(px * sc)))
    key = fx._digest(pcs, p, (px, sc)) + f"-{int(size)}"
    f = _dir(out, pid) / "previews" / f"{key}.webp"
    if not f.is_file():
        small = particles.ParticleParams.from_dict({**p.to_dict(), "size": int(size)})
        frames = particles.simulate(fx._fit(pcs, px, sc), small)
        f.parent.mkdir(exist_ok=True)
        atomic.write_bytes(f, particles.preview_webp(frames, size=int(size)))
        prune_previews(f.parent)
    return {"url": f"/out/particles/{s['id']}/previews/{f.name}", "file": f"previews/{f.name}", "params": {**p.to_dict(), "sprite_px": px, "scale": sc}, "preset": name,
            "sprites": len(pcs), "source": source, "pack_id": pack}


ECHO_PX = 160   # Echo shows the burst at 80 CSS px (studio.css .ch-particle): twice that stays sharp on a 2x screen and is a ~2.5x lighter file than 256


def preview_for_sticker(out: Path, lib, pack_id: str, sticker_id: str) -> dict:
    """Echo reaction: this sticker's latest particles with their saved motion. Free. The latest is its newest **saved** version (the highest v# under the sticker,
    `rows_for_sticker`'s order); only a sticker with no saved version yet falls back to its newest linked draft."""
    with lib.lock:
        pack = _lib_pack(lib, pack_id)
        sticker = next((st for st in pack.get("stickers", []) if st["id"] == sticker_id), None)
        if sticker is None:
            raise SetError(f"no sticker {sticker_id} in {pack_id}", 404)
        ordered = list(sticker.get("particles") or [])
    owned = [s for s in list_sets(out, lib) if any(o["sticker_id"] == sticker_id for o in s["owner"])]
    if not owned:
        return {"set": None, "url": None}
    full = {s["id"]: read(out, s["id"]) for s in owned}
    rows = [s for s in owned if saved(full[s["id"]])]
    if rows:
        chosen = max(rows, key=lambda s: (full[s["id"]].get("saved_at") or full[s["id"]].get("created") or 0, s["id"]))
        return {**preview(out, lib, chosen["id"], pack_id=pack_id, size=ECHO_PX), "set": chosen["id"], "motion": full[chosen["id"]].get("motion") or {}}
    available = {s["id"]: s for s in owned}
    chosen = next((available[pid] for pid in reversed(ordered) if pid in available), None)
    chosen = chosen or max(owned, key=lambda s: (s.get("created", 0), s["id"]))
    return {**preview(out, lib, chosen["id"], pack_id=pack_id, size=ECHO_PX), "set": chosen["id"], "motion": chosen.get("motion") or {}}


def render(out: Path, lib, pid: str, cfg, *, pack_id=None, preset=None, params=None, user: str = "local") -> dict:
    """The final 512 px WebM of the burst for a pack, judged and stored under `renders/` (R001.webm ...) with its checks. A render is stored whatever the checks say: **only Telegram's own limits
    make it FAILED** (`engine/effect_video.TECHNICAL`), every other check is a warning the person sees and decides on (rule 10); a failed one is kept too (rejection never deletes)."""
    from . import effects as fx
    from ..engine import effect_video as ev, particles
    s = read(out, pid)
    pack = _pick_pack(s, lib, pack_id, required=bool(_derived(s).get("packs")) or (s.get("source") or {}).get("kind") == "stickers")   # a stand-alone set renders on its own
    name, px, sc, p = _burst(s, preset, params)
    pcs, source = sprites_of(out, lib, s, pack, sprite_px=max(1, round(px * sc)))
    r = ev.encode_and_check(particles.simulate(fx._fit(pcs, px, sc), p), cfg, label=f"{s['id']} burst")
    d = _dir(out, pid)
    with _LOCK:
        s = read(out, pid)
        nums = [int(x["id"][1:]) for x in s.get("renders") or [] if str(x.get("id", "")).startswith("R") and str(x["id"][1:]).isdigit()]
        rid = f"R{max(nums + [0]) + 1:03d}"
        fname = None
        if r.get("data"):
            fname = f"renders/{rid}.webm"
            (d / "renders").mkdir(exist_ok=True)
            atomic.write_bytes(d / fname, r["data"])
        rec = {"id": rid, "pack_id": pack, "preset": name, "params": {**p.to_dict(), "sprite_px": px, "scale": sc}, "source": source, "sprites": len(pcs), "file": fname,
               "bytes": len(r["data"]) if r.get("data") else 0, "status": r["status"], "checks": r["checks"], "warnings": r["warnings"], "blocks": r["blocks"], "metrics": r["metrics"],
               "created": round(time.time(), 3), "added_to": None, "added": []}
        s.setdefault("renders", []).append(rec)
        hist(s, "python", "RENDER", f"{rid} for {pack}: {r['status']} {rec['bytes'] // 1024} KB ({name})", {"render": rid, "pack": pack, "warnings": r["warnings"]})
        _write(out, s)
    return render_view(out, pid, rec)


def _pack_emoji(lib, pack_id: str) -> str:
    """The emoji tag of a burst: the pack's own emoji, commonest first (Telegram needs at least one, and takes at most 20). Added bursts (reactions, source.particle_set) never count: they would echo the burst emoji back."""
    from collections import Counter
    from ..services import telegram
    c: Counter = Counter()
    for st in _lib_pack(lib, pack_id).get("stickers") or []:
        if (st.get("source") or {}).get("particle_set"):
            continue
        c.update(telegram.split_emoji(st.get("emoji")))
    return "".join(e for e, _ in c.most_common(MAX_EMOJI)) or "🙂"


def add(out: Path, lib, pid: str, renders=None, pack_id=None, sticker_id=None, user: str = "local", new_pack: str | None = None) -> dict:
    """Put rendered bursts into a pack as animated stickers tagged with the pack's emoji. This click is the person's approval of the burst (history `APPROVE`). Without `renders` it takes every READY
    burst rendered for that pack that is not in a pack yet. A FAILED render (a Telegram limit is broken) cannot be added; a warning never stops it. The same burst is not put in the same pack
    twice by accident (409): render another to have a second."""
    with _LOCK:
        s = read(out, pid)
        by_id = {r["id"]: r for r in s.get("renders") or []}
        if renders is not None and (not isinstance(renders, list) or any(not isinstance(x, str) for x in renders)):
            raise SetError("renders must be a list of burst ids")
        first = by_id.get(renders[0]) if renders else None
        fresh = None
        if new_pack and not pack_id:                    # "Make a pack of it": a stand-alone set's bursts start a pack of their own (then Send to Telegram as any pack)
            fresh = lib.create_pack(str(new_pack))["id"]
        pack = fresh or _pick_pack(s, lib, pack_id or (first or {}).get("pack_id"), required=True)
        if renders:
            missing = [x for x in renders if x not in by_id]
            if missing:
                raise SetError(f"no burst {', '.join(missing)} in {s['id']}", 404)
            chosen = [by_id[x] for x in dict.fromkeys(renders)]
        else:
            chosen = [r for r in s.get("renders") or [] if r.get("pack_id") == pack and r.get("status") == "READY" and not r.get("added_to")]
        if not chosen:
            raise SetError("there is nothing to add yet: render a burst first", 409)
        bad = [r["id"] for r in chosen if r.get("status") != "READY" or not r.get("file")]
        if bad:
            raise SetError(f"{', '.join(bad)} breaks a Telegram limit (see its checks) and cannot be added", 409)
        twice = [r["id"] for r in chosen if any(a.get("pack") == pack for a in r.get("added") or [])]
        if twice:
            raise SetError(f"{', '.join(twice)} is already in that pack: render another to add a second", 409)
        owners = [o["sticker_id"] for o in s.get("owner") or []]
        parent = str(sticker_id) if sticker_id and str(sticker_id) in owners else (owners[0] if owners else None)
        emoji, d, added = ("✨" if fresh else _pack_emoji(lib, pack)), _dir(out, pid), []
        for r in chosen:
            st = lib.add_bytes(pack, (d / r["file"]).read_bytes(), "webm", f"{s.get('name') or s['id']} · {r['preset']}"[:60], "animated", emoji,
                               source={"kind": (s.get("source") or {}).get("kind"), "particle_set": s["id"], "render": r["id"], "preset": r["preset"], "source_pack": r.get("pack_id"),
                                       "parent_sticker": parent})
            r.setdefault("affirmed_in", []).append(pack)
            r["added_to"] = pack
            r.setdefault("added", []).append({"sticker": st["id"], "pack": pack, "of": parent, "ts": round(time.time(), 3)})
            added.append({"sticker": st["id"], "name": st["name"], "render": r["id"]})
        hist(s, user, "APPROVE", f"{len(added)} burst(s) added to the pack", {"pack": pack, "added": added})
        _write(out, s)
    return {"added": added, "pack_id": pack}


# ---------- edit ----------
def update(out: Path, lib, pid: str, *, name=None, elements=None, packs=None, picked=None, motion=None, save: bool = False, user: str = "local") -> dict:
    """Rename, re-pick the cells, set the default motion, or move the set between packs. A pick never deletes a file: an unpicked cell stays
    (`generate more` appends; nothing is lost). `save` is the person's **Save**: the set becomes (or stays) a saved row under its stickers and its motion is replaced."""
    with _LOCK:
        s = read(out, pid)
        if name is not None:
            s["name"] = _clean_name(name, s["name"])
        if elements is not None:
            s["elements"] = _clean_elements(elements)
        if packs is not None:
            s["owner"] = owners_for(lib, packs=packs)
        if motion is not None:
            s["motion"] = _clean_motion(motion)
        if picked is not None:
            keep = _clean_picks(picked)
            have = {int(c["n"]) for c in s.get("cells") or []}
            bad = sorted(set(keep) - have)
            if bad:
                raise SetError(f"cell {', '.join(map(str, bad))} is not in this set")
            if not keep:
                raise SetError("a set needs at least one picked cell (or its own stickers)")
            for c in s.get("cells") or []:
                c["picked"] = int(c["n"]) in keep
        if save:
            s["saved_at"] = round(time.time(), 3)
        hist(s, user, "SAVE" if save else "EDIT", ", ".join(k for k, v in (("name", name is not None), ("elements", elements is not None), ("packs", packs is not None),
                                                   ("picked", picked is not None), ("motion", motion is not None)) if v))
        _write(out, s)
    return view(out, lib, pid)


def _clean_picks(picked) -> list[int]:
    if not isinstance(picked, list) or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in picked):
        raise SetError("picked must be a list of cell numbers (1 is the first cell)")
    return sorted({int(i) for i in picked})


def assign(out: Path, lib, pid: str, packs, user: str = "local") -> dict:
    """Deprecated one-release alias: link the pack's stickers."""
    return link(out, lib, pid, owners_for(lib, packs=packs), user)


def unassign(out: Path, lib, pid: str, packs, user: str = "local") -> dict:
    """Deprecated one-release alias: unlink the pack's stickers."""
    return link(out, lib, pid, owners_for(lib, packs=packs), user, unlink=True)


def duplicate(out: Path, lib, pid: str, *, name=None, user: str = "local", renders: bool = True) -> dict:
    """A copy of the set under a new id, cells and all, with the same sticker owners. The copy is independent: unpicking or deleting one leaves the other."""
    with _LOCK:
        s = migrate(out, lib, pid)
        src = _dir(out, pid)
        nid = _next_id(out)
        dst = sets_dir(out) / nid
        (dst / "cells").mkdir(parents=True)
        for c in s.get("cells") or []:
            for field in ('file', 'clip', 'timeline'):
                f = src / (c.get(field) or '')
                if f.is_file():
                    atomic.write_bytes(dst / c[field], f.read_bytes())
        if renders and (src / "renders").is_dir():
            shutil.copytree(src / "renders", dst / "renders")
        copy = {**s, "id": nid, "name": _clean_name(name, f"{s.get('name')} (copy)"), "created": round(time.time(), 3), "user": user, "history": [],
                "source": {**(s.get("source") or {}), "duplicated_from": pid},
                "renders": [{**r, 'added_to':None, 'added':[], 'affirmed_in':[]} for r in s.get('renders', [])] if renders else []}
        hist(copy, user, "DUPLICATE", f"a copy of {pid}")
        atomic.write_text(dst / "set.json", json.dumps(copy, indent=2, ensure_ascii=False))
    return view(out, lib, nid)


# ---------- rows: every saved version of a sticker's particles is one row under that sticker ----------
def saved(s: dict) -> bool:
    """A set is a saved row once the person pressed Save (or Save as new). Sets made before rows existed have no `saved_at` key and count as saved;
    a set made by the editor starts as a draft (`saved_at: None`) until Save."""
    return "saved_at" not in s or s.get("saved_at") is not None


def kind_label(s: dict) -> str:
    """How the row was made, in the words the sticker window shows."""
    src = s.get("source") or {}
    animated = any(c.get("clip") for c in s.get("cells") or [])
    if src.get("kind") == "video":
        return "Kling from scratch"
    if src.get("kind") == "drawn":
        return "AI image sprites, animated" if animated else "AI image sprites"
    if src.get("recovered_stickers") or animated:
        return "Animated sprites"
    if not s.get("cells"):
        return "The sticker itself"
    return "Sprites"


def _row(out: Path, s: dict, sticker_id: str) -> dict:
    d = _dir(out, s["id"])
    cells = [cell_view(out, d, c) for c in s.get("cells") or []]
    renders = [render_view(out, s["id"], r) for r in s.get("renders") or []]
    ready = [r for r in renders if r.get("status") == "READY" and r.get("url")]
    in_pack = [{"sticker_id": a.get("sticker"), "pack_id": a.get("pack"), "render": r["id"]} for r in s.get("renders") or [] for a in r.get("added") or []
               if a.get("of") in (None, sticker_id)]
    src = s.get("source") or {}
    return {"id": s["id"], "name": s.get("name") or s["id"], "kind": src.get("kind"), "label": kind_label(s), "created": s.get("created"),
            "saved_at": s.get("saved_at", s.get("created")), "saved": saved(s), "motion": s.get("motion") or {},
            "sprites": [{k: c.get(k) for k in ("n", "url", "clip_url", "type", "picked", "missing")} for c in cells],
            "n_sprites": sum(1 for c in cells if c.get("picked")) or len(cells),
            "preview": ready[-1]["url"] if ready else None, "renders": len(renders), "credits": s.get("credits") or 0.0,
            "addable": next(({"render": r["id"], "pack_id": r.get("pack_id")} for r in reversed(ready) if not r.get("added")), None),
            "job": src.get("job"), "effect": src.get("effect"), "shared_with": max(0, len(s.get("owner") or []) - 1), "in_pack": in_pack}


def rows_for_sticker(out: Path, lib, sticker_id: str) -> dict:
    """The particle versions of one sticker: `rows` are the saved ones, oldest first, numbered v1, v2...; `drafts` are sets the editor started for it
    and nobody saved yet (shown apart, never lost). A set shared by several stickers is a row under each of them. A pure read."""
    mine = []
    for p in sorted(sets_dir(out).glob("P[0-9]*")):
        try:
            s = _derived(migrate(out, lib, p.name))
        except (OSError, ValueError, SetError):
            continue
        if any(o.get("sticker_id") == str(sticker_id) for o in s.get("owner") or []):
            mine.append(s)
    rows = sorted((x for x in mine if saved(x)), key=lambda x: (x.get("saved_at") or x.get("created") or 0, x["id"]))
    drafts = sorted((x for x in mine if not saved(x)), key=lambda x: (x.get("created") or 0, x["id"]))
    out_rows = [{**_row(out, x, str(sticker_id)), "version": i + 1} for i, x in enumerate(rows)]
    return {"sticker": str(sticker_id), "rows": out_rows, "drafts": [_row(out, x, str(sticker_id)) for x in drafts]}


def save_as_new(out: Path, lib, pid: str, *, motion=None, name=None, user: str = "local") -> dict:
    """**Save as new**: the same sprites with the new motion become a new row under the same stickers; the row it came from is unchanged.
    Bursts rendered with the old motion stay with the old row (they do not show the new motion)."""
    v = duplicate(out, lib, pid, name=name, user=user, renders=False)
    with _LOCK:
        s = read(out, v["id"])
        if motion is not None:
            s["motion"] = _clean_motion(motion)
        s["saved_at"] = round(time.time(), 3)
        s["name"] = _clean_name(name, str(s.get("name") or "").replace(" (copy)", "")) or s["id"]
        hist(s, user, "SAVE", f"saved as a new row from {pid}", {"from": pid})
        _write(out, s)
    return view(out, lib, v["id"])


def adopt_effects(out: Path, lib, user: str = "python", cfg=None) -> list[dict]:
    """One-time and idempotent: every older effect run (`E###`) that produced something and was never saved as a set becomes a saved row under the
    stickers it was made for. A Kling run's cut clips become ONE row of animated sprites (never one particle per slice); a simulated run's bursts become
    the row's renders. A run whose AI particle sheet was DRAWN but never saved as a set becomes a row of that sheet's cells; a sheet that an older version cut
    as stickers is cut again as particles first (free, from the sheet it stores: `pipeline.recut_as_particles`). Nothing is deleted or paid; the run stays where it
    is and points at its row (`sets`). Runs that produced nothing are left alone."""
    from . import effects as fx, pipeline
    from ..engine.config import EngineConfig
    made = []
    for p in sorted(fx.effects_dir(out).glob("E[0-9]*")):
        try:
            e = json.loads(atomic.read_text(p / "effect.json"))
        except (OSError, ValueError):
            continue
        results = [r for r in e.get("results") or [] if r.get("file") and (p / r["file"]).is_file()]
        sheet = fx.set_of(e) or {}
        if not e.get("sets") and not results and sheet.get("generation") is not None and sheet.get("status") == "DRAWN":
            gn = int(sheet["generation"])
            if pipeline.read_result(out, gn).get("kind") != "particles":
                pipeline.recut_as_particles(out, gn, cfg or EngineConfig(), by=user)
            v = set_from_effect(out, lib, p.name, mode="sim", user=user)
            with _LOCK:
                s = read(out, v["id"])
                s["name"] = f"{e.get('pack_name') or 'Particles'} · AI pieces (G{gn:03d})"
                s["saved_at"] = e.get("created") or s.get("created")
                hist(s, user, "ADOPT", f"saved as a row from the drawn sheet G{gn:03d} of {p.name}", {"effect": p.name, "generation": f"G{gn:03d}"})
                _write(out, s)
            made.append({"effect": p.name, "set": s["id"], "mode": "drawn", "owners": [o["sticker_id"] for o in s.get("owner") or []]})
            continue
        if e.get("sets") or not results:
            continue
        mode = "video" if any(r.get("mode") == "video" for r in results) else "sim"
        v = set_from_effect(out, lib, p.name, mode=mode, user=user)
        with _LOCK:
            s = read(out, v["id"])
            if mode == "sim":
                (_dir(out, s["id"]) / "renders").mkdir(exist_ok=True)
                for i, r in enumerate(results, 1):
                    rid = f"R{i:03d}"
                    atomic.write_bytes(_dir(out, s["id"]) / f"renders/{rid}.webm", (p / r["file"]).read_bytes())
                    s.setdefault("renders", []).append({"id": rid, "pack_id": e.get("pack_id"), "preset": "burst", "params": r.get("params") or {}, "source": "own",
                                                        "file": f"renders/{rid}.webm", "bytes": r.get("bytes") or 0, "status": r.get("status"), "checks": r.get("checks") or [],
                                                        "warnings": r.get("warnings") or [], "blocks": r.get("blocks") or [], "metrics": r.get("metrics") or {},
                                                        "created": e.get("created"), "added_to": None, "added": [], "from": f"{p.name}/{r['id']}"})
                s["motion"] = {"preset": "burst", "params": results[0].get("params") or {}}
            s["name"] = f"{e.get('pack_name') or 'Particles'} · {'Kling' if mode == 'video' else 'sprites'} ({p.name})"
            s["saved_at"] = e.get("created") or s.get("created")
            hist(s, user, "ADOPT", f"saved as a row from the older run {p.name}", {"effect": p.name})
            _write(out, s)
        made.append({"effect": p.name, "set": s["id"], "mode": mode, "owners": [o["sticker_id"] for o in s.get("owner") or []]})
    return made


# ---------- delete / restore: nothing is destroyed on a click (rule 9's spirit) ----------
def delete(out: Path, lib, pid: str, *, confirm_packs: bool = False, user: str = "local") -> dict:
    """Move the set to `out/trash/particles/P###/`. A set that is assigned to packs **says which** and needs `confirm_packs` (the screen asks first):
    without it the call is refused with the list, so a set in use can never disappear under a pack by accident. Nothing is deleted from disk."""
    with _LOCK:
        s = migrate(out, lib, pid)
        used = _derived(s)["packs"]
        if used and not confirm_packs:
            raise SetError(f"This set is used by {', '.join(n['name'] for n in pack_names(lib, used))}. Deleting it takes it off those packs; "
                           f"confirm to go on.", 409)
        hist(s, user, "DELETE", f"moved to the trash ({', '.join(used) or 'stand-alone'})")
        _write(out, s)
        src, dst = _dir(out, pid), trash_dir(out) / str(pid).upper()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _sync_links(lib, {"id": s["id"], "owner": []})
    return {"ok": True, "id": str(pid).upper(), "trashed": True, "was_in": used}


def restore(out: Path, lib, pid: str, user: str = "local") -> dict:
    """Put a deleted set back, under the same id and the same packs. Refused when that id is taken by a live set (duplicate it instead)."""
    with _LOCK:
        s = read_deleted(out, pid)
        if (sets_dir(out) / str(pid).upper() / "set.json").is_file():
            raise SetError(f"{str(pid).upper()} is in use again; duplicate that one instead.", 409)
        dst = sets_dir(out) / str(pid).upper()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(_trash(out, pid)), str(dst))
        s = read(out, pid)
        hist(s, user, "RESTORE", "back from the trash")
        _write(out, s)
    return view(out, lib, pid)


def list_deleted(out: Path, lib) -> list[dict]:
    """The trash, newest deleted first (`GET /api/particles/deleted`): the card of every set that was deleted, enough to recognise it (name, cells strip with urls into the trash folder, where
    it was used) and to restore it later with `restore`. A pure read: nothing is moved."""
    rows = []
    for p in sorted(trash_dir(out).glob("P[0-9]*")):
        try:
            s = _derived(json.loads(atomic.read_text(p / "set.json")))
        except (OSError, ValueError):
            continue
        cells = [cell_view(out, p, c, "trash/particles") for c in s.get("cells") or []]
        picked = [c["n"] for c in cells if c.get("picked")]
        gone = [h for h in s.get("history") or [] if h.get("decision") == "DELETE"]
        rows.append({"id": s.get("id", p.name), "name": s.get("name") or p.name, "created": s.get("created"), "kind": (s.get("source") or {}).get("kind"),
                     "elements": list(s.get("elements") or []), "packs": list(s.get("packs") or []), "used_in": pack_names(lib, s.get("packs") or []), "cells": cells, "picked": picked,
                     "n_cells": len(cells), "n_picked": len(picked), "credits": s.get("credits"), "deleted": True, "trashed": True,
                     "deleted_at": (gone[-1] if gone else (s.get("history") or [{}])[-1]).get("ts") or s.get("updated")})
    rows.sort(key=lambda r: (r.get("deleted_at") or 0, r["id"]), reverse=True)
    return rows


# ---------- reactions: the per-emoji map + pack rows + lottie + migration (Reaction effects, one emoji to Lottie, is flow/emoji_reactions.py) ----------
def emoji_motions(out: Path | None = None, lib=None) -> list[dict]:
    """The generic 1800 wall (read-only): every bank token as emoji:animation.

    Merges `generation.actions.generation_bank()` (glyph + order, >1800) with
    `engine.emoji_motion.motion_for_token` (fixed preset+params) and the
    face/body motion sentences. No spending, no writing. Falls back to the
    core 36 when the bank cannot be read (tests without the full bank).
    """
    from ..engine import emoji_motion as em
    try:
        from ..generation import actions as bank
        gen = bank.generation_bank()
        face = getattr(bank, "FACE_SENTENCES", {})
        body = getattr(bank, "BODY_SENTENCES", {})
    except Exception:
        gen, face, body = {}, {}, {}
    if not gen:
        try:
            from ..generation.actions import ACTION_BANK
            gen = dict(ACTION_BANK)
        except Exception:
            gen = {}
    rows = []
    for token, row in gen.items():
        glyph = (row or {}).get("emoji", "")
        mo = em.motion_for_token(token)
        sent = (face.get(token) or body.get(token) or ("", ""))
        rows.append({"token": token, "emoji": glyph,
                     "preset": mo["preset"], "params": mo["params"],
                     "label": (sent[0] if isinstance(sent, (list, tuple)) and len(sent) > 0 else ""),
                     "motion": (sent[1] if isinstance(sent, (list, tuple)) and len(sent) > 1 else "")})
    if not rows:
        for token in sorted(em.CORE_MOTIONS):
            mo = em.motion_for_token(token)
            rows.append({"token": token, "emoji": "", "preset": mo["preset"],
                         "params": mo["params"], "label": "", "motion": ""})
    return rows


def reactions_for_pack(out: Path, lib, pack_id: str) -> dict:
    """This pack's reaction variations: one row per emoji in the pack.

    Each row inherits its motion 1:1 from the emoji map (locked 🔒); the pack
    varies only sprite art + Size/Energy. Rows resolve the pack's stickers by
    emoji token (`actions.token_for_emoji`), attach the sticker-owned sets
    (`for_sticker` rows) and the pack's bursts. Professional and simple: at
    most the pack's own emojis, never the whole 1800.
    """
    from ..engine import emoji_motion as em
    try:
        from ..generation import actions as bank
        _tok = getattr(bank, "token_for_emoji", None)
    except Exception:
        _tok = None
    try:
        pack = _lib_pack(lib, pack_id)
    except SetError:
        raise SetError(f"no such pack {pack_id}", 404)
    stickers = pack.get("stickers") or []
    by_token: dict[str, dict] = {}
    for st in stickers:
        glyphs = st.get("emoji") or ""
        glyph = str(glyphs).strip().split()[0] if isinstance(glyphs, str) else (list(glyphs)[0] if glyphs else "")
        token = None
        if _tok is not None and glyph:
            try:
                token = _tok(glyph)
            except Exception:
                token = None
        if not token:
            token = str(st.get("key") or st.get("name") or "happy").strip().lower().split()[0]
        row = by_token.setdefault(token, {"token": token, "emoji": glyph, "stickers": []})
        row["stickers"].append({"id": st.get("id"), "name": st.get("name"), "emoji": st.get("emoji")})
        if glyph and not row.get("emoji"):
            row["emoji"] = glyph
    motions = {m["token"]: m for m in emoji_motions(out, lib)}
    out_rows = []
    for token, row in sorted(by_token.items()):
        mo = motions.get(token) or {"preset": em.motion_for_token(token)["preset"],
                                    "params": em.motion_for_token(token)["params"],
                                    "label": "", "motion": ""}
        sets = []
        for st in row["stickers"]:
            try:
                det = for_sticker(out, lib, pack_id, st["id"])
                sets.extend(det.get("rows") or [])
            except Exception:
                continue
        out_rows.append({**row, "preset": mo["preset"], "params": mo["params"],
                         "label": mo.get("label", ""), "motion": mo.get("motion", ""),
                         "locked": True, "sets": sets})
    return {"pack_id": pack_id, "reactions": out_rows}


def _sprite_pngs(pcs) -> list[bytes]:
    """The flying sprites as PNG bytes for a Lottie asset each: a still as it is, an animated sprite as its fullest frame (Lottie
    image layers are stills: an animated sprite's own motion is not carried)."""
    import numpy as np
    from ..engine import particles as _p
    out = []
    for pc in pcs:
        if isinstance(pc, _p.AnimatedSprite):
            pc = pc.frames[int(np.argmax(pc.frames[..., 3].reshape(len(pc.frames), -1).sum(1)))]
        out.append(_p.png_bytes(pc))
    return out


def export_lottie(out: Path, lib, pid: str, *, preset: str | None = None, params: dict | None = None) -> dict:
    """Optional third output (web only): the set's burst as a playable Lottie JSON.

    The same deterministic trajectories as the preview and the render (`_burst`: the set's motion under the person's params),
    with the set's picked sprites embedded as PNG image assets (`engine.lottie.export_lottie`), so a player shows the real
    sprites flying. Telegram stays WebM. Stored under `renders/L###.lottie.json` beside the WebM renders. Free."""
    from ..engine import lottie, particles
    s = migrate(out, lib, pid)
    pack = _pick_pack(s, lib, None, required=False)
    name, px, sc, base = _burst(s, preset, params)
    pcs, _source = sprites_of(out, lib, s, pack, sprite_px=max(1, round(px * sc)))
    try:
        doc = lottie.export_lottie(base, _sprite_pngs(particles.erode_sprites(pcs, base.erosion_px)), size=base.size)
    except ValueError as ex:
        raise SetError(str(ex))
    d = _dir(out, pid)
    (d / "renders").mkdir(exist_ok=True)
    with _LOCK:
        s = read(out, pid)
        nums = [int(str(r.get("id"))[1:]) for r in s.get("lottie") or [] if str(r.get("id", "")).startswith("L") and str(r.get("id"))[1:].isdigit()]
        rid = f"L{max(nums + [0]) + 1:03d}"
        fname = f"renders/{rid}.lottie.json"
        atomic.write_text(d / fname, json.dumps(doc, separators=(",", ":")))
        s.setdefault("lottie", []).append({"id": rid, "preset": name, "params": base.to_dict(), "file": fname, "created": round(time.time(), 3)})
        hist(s, "local", "LOTTIE", f"exported {rid} as Lottie ({name})", {"preset": name})
        _write(out, s)
    return {"id": rid, "set": str(pid).upper(), "preset": name, "params": base.to_dict(),
            "file": fname, "url": f"/out/particles/{str(pid).upper()}/{fname}", "layers": len(doc["layers"])}


def adopt_emoji_map(out: Path, lib, user: str = "python") -> list[dict]:
    """Migrate random particle sets onto the emoji map (idempotent, free).

    Keeps every byte (cells, clips, renders untouched). Replaces free motion
    with `emoji_motion.motion_for_token(token of the set's first owner
    sticker emoji)`; records any Size/Energy delta the pack had tuned.
    Writes `set.json.pre-emoji-map` once. History line ADOPT_EMOJI.
    """
    from ..engine import emoji_motion as em
    try:
        from ..generation import actions as bank
        _tok = getattr(bank, "token_for_emoji", None)
    except Exception:
        _tok = None
    made = []
    for p in sorted(sets_dir(out).glob("P[0-9]*")):
        try:
            s = json.loads(atomic.read_text(p / "set.json"))
        except (OSError, ValueError):
            continue
        if (s.get("emoji_motion") or {}).get("version") == 1:
            continue
        owners = s.get("owner") or []
        token = None
        if _tok is not None:
            for o in owners:
                try:
                    pk = _lib_pack(lib, o.get("pack_id"))
                    st = next((x for x in pk.get("stickers") or [] if x.get("id") == o.get("sticker_id")), {})
                    glyph = str(st.get("emoji") or "").strip().split()
                    if glyph and _tok(glyph[0]):
                        token = _tok(glyph[0])
                        break
                except Exception:
                    continue
        if not token:
            token = str(((s.get("elements") or ["happy"])[0])).strip().lower().split()[0]
        mo = em.motion_for_token(token)
        with _LOCK:
            s = read(out, p.name)
            if not (p / "set.json.pre-emoji-map").is_file():
                (p / "set.json.pre-emoji-map").write_bytes((p / "set.json").read_bytes())
            old = s.get("motion") or {}
            keep = {k: old.get("params", {}).get(k) for k in ("particle_size", "magnitude") if isinstance(old.get("params"), dict)}
            params = dict(mo["params"])
            for k, v in keep.items():
                if isinstance(v, (int, float)):
                    params[k] = v
            s["motion"] = {"preset": mo["preset"], "params": params}
            s["emoji_motion"] = {"version": 1, "token": token}
            hist(s, user, "ADOPT_EMOJI", f"motion mapped to emoji {token} ({mo['preset']})", {"token": token})
            _write(out, s)
        made.append({"set": s["id"], "token": token, "preset": mo["preset"]})
    return made


def ensure_default_sprite(out: Path, lib, pid: str, user: str = "local") -> dict:
    """A bank reaction with no drawn sprites yet gets the procedural sparkle as its first picked cell (free, instant).

    Idempotent: a set with any picked cell that still has its picture keeps it and nothing is written. Otherwise the sparkle
    (`engine.particles.default_sprite`) is stored as `cells/cNN.png` and picked, so preview/render work at once; a person
    replaces it later through Generate more. History `DEFAULT_SPRITE`."""
    import io
    from PIL import Image
    from ..engine import particles as _particles
    s = migrate(out, lib, pid)
    d = _dir(out, pid)
    have = [c for c in s.get("cells") or []
            if c.get("picked") and c.get("file") and (d / c["file"]).is_file()]
    if have:
        return {**view(out, lib, pid), "default": False}
    img = _particles.default_sprite()
    buf = io.BytesIO()
    Image.fromarray(img, "RGBA").save(buf, "PNG")
    data = buf.getvalue()
    w, h = _png_size(data)
    with _LOCK:
        s = read(out, pid)
        have = [c for c in s.get("cells") or []
                if c.get("picked") and c.get("file") and (d / c["file"]).is_file()]
        if have:
            return {**view(out, lib, pid), "default": False}
        n = max([int(c.get("n") or 0) for c in s.get("cells") or []] + [0]) + 1
        (d / "cells").mkdir(exist_ok=True)
        atomic.write_bytes(d / f"cells/c{n:02d}.png", data)
        s.setdefault("cells", []).append({"n": n, "cell": 0, "file": f"cells/c{n:02d}.png", "status": "READY",
                                          "warnings": [], "key": "spark", "sprite": True, "w": w, "h": h,
                                          "picked": True, "created": round(time.time(), 3), "default": True})
        hist(s, user, "DEFAULT_SPRITE", "the procedural sparkle until drawn sprites replace it", {"cell": n})
        _write(out, s)
    return {**view(out, lib, pid), "default": True}


def bank_states(out: Path, lib) -> list[dict]:
    """The emoji bank with per-token build state for Library > Reactions > Create (read-only, free).

    Every motion from `emoji_motions()` plus: `state` (`finished` when a covering set holds a READY render, else
    `unclaimed`), `building` (a covering set is drawing), `set_id` (the covering set to reuse: the finished one,
    else the newest covering one, else null) and `n_picked` (its picked sprites, so the UI can say "has sprites").
    A set covers the tokens in its `elements`."""
    covering: dict[str, list[str]] = {}
    rows = {r["id"]: r for r in list_sets(out, lib)}
    for r in rows.values():
        for t in r.get("elements") or []:
            covering.setdefault(str(t).strip().lower(), []).append(r["id"])
    full: dict[str, dict] = {}
    for pid in {pid for pids in covering.values() for pid in pids}:
        try:
            full[pid] = read(out, pid)
        except (SetError, OSError, ValueError):
            continue
    out_rows = []
    for m in emoji_motions(out, lib):
        pids = covering.get(m["token"], [])
        recs = [(pid, full.get(pid)) for pid in pids if pid in full]
        ready = next((pid for pid, s in recs
                      if any((r or {}).get("status") == "READY" for r in (s.get("renders") or []))), None)
        newest = max(pids, key=lambda pid: ((rows[pid].get("created") or 0), pid)) if pids else None
        picked = max([rows[pid].get("n_picked") or 0 for pid in pids] + [0])
        building = any(bool(rows[pid].get("drawing")) for pid in pids)
        out_rows.append({**m, "state": "finished" if ready else "unclaimed", "building": building,
                         "set_id": ready or newest, "n_picked": picked})
    return out_rows
