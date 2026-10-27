"""Super reaction library: what the sandbox keeps of the super reactions made from the files in lottie.

Every dropped file has a RANDOM look until someone saves it (engine/super_reaction.py `random_settings`, seeded by the file name; Shuffle = the
next seed). **Save** keeps a version's settings, **Save as** adds another version of the same file (one source, several super reactions),
**Remove** deletes a saved version only: the file stays in the folder and, with no version left, shows its random look again. **Presets** are named
slider bundles (and a Light colour only when it is Solid or Gradient) that any file can load. One file: out/particles/super/library.json
`{versions: {V###: {id, file, name, look, created, updated}}, presets: {P###: {id, name, look, created}}, n: {...}}`; a look is
`{settings, glow, color?, colors?, seed?}`. The export (`export_zip`) uses these: every version, and every unsaved file's random look.
docs/architecture.md. Errors are particle_sets.SetError (the HTTP layer maps its code). Free: nothing here spends.
"""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from pathlib import Path

from ..runtime import atomic
from .emoji_reactions import handpicked_files
from .particle_sets import SetError, sets_dir

_LOCK = threading.Lock()
NAME_MAX = 60


def _path(out: Path) -> Path:
    return sets_dir(out) / "super" / "library.json"


def _read(out: Path) -> dict:
    f = _path(out)
    try:
        d = json.loads(atomic.read_text(f)) if f.is_file() else {}
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    for k in ("versions", "presets", "n"):
        if not isinstance(d.get(k), dict):
            d[k] = {}
    return d


def _write(out: Path, d: dict) -> None:
    f = _path(out)
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(f, json.dumps(d, ensure_ascii=False, indent=1))


def _next(d: dict, kind: str, prefix: str) -> str:
    n = int(d["n"].get(kind, 0)) + 1                       # a number is never reused after a remove
    d["n"][kind] = n
    return f"{prefix}{n:03d}"


def _name(name, fallback: str) -> str:
    s = " ".join(str(name or "").split())[:NAME_MAX]
    return s or fallback


def clean_look(look, *, preset: bool = False) -> dict:
    """A look the engine accepts: {settings (SUPER_KEYS numbers), glow, color?, colors?, seed?}. 400 for anything else. A preset keeps its
    colour only for Solid / Gradient (Auto follows each file's own colour)."""
    from ..engine import super_reaction as sr
    if not isinstance(look, dict):
        raise SetError("look must be an object")
    bad = sorted(set(look) - {"settings", "glow", "color", "colors", "seed"})
    if bad:
        raise SetError(f"unknown look field(s): {', '.join(bad)}")
    settings = look.get("settings") or {}
    if not isinstance(settings, dict):
        raise SetError("settings must be an object")
    extra = sorted(set(settings) - set(sr.SUPER_KEYS))
    if extra:
        raise SetError(f"unknown super setting(s): {', '.join(extra)}")
    for k, v in settings.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SetError(f"{k} must be a number")
    glow = str(look.get("glow") or "auto")
    if glow not in sr.GLOW_MODES:
        raise SetError(f"glow must be one of {', '.join(sr.GLOW_MODES)}")
    out = {"settings": {k: settings[k] for k in sr.SUPER_KEYS if k in settings}, "glow": glow}
    try:
        if glow == "solid":
            out["color"] = "#%02x%02x%02x" % sr.hex_rgb(look.get("color"))
        if glow == "gradient":
            cs = look.get("colors")
            if not isinstance(cs, (list, tuple)) or len(cs) != 2:
                raise SetError("a gradient takes two colours: [bottom, top]")
            out["colors"] = ["#%02x%02x%02x" % sr.hex_rgb(c) for c in cs]
    except ValueError as ex:
        raise SetError(str(ex))
    if not preset and look.get("seed") is not None:
        if isinstance(look["seed"], bool) or not isinstance(look["seed"], int):
            raise SetError("seed must be a whole number")
        out["seed"] = int(look["seed"])
    return out


def random_look(file: str, seed: int | None = None) -> dict:
    """The look of an unsaved file: its seeded random settings, Auto light."""
    from ..engine import super_reaction as sr
    s = sr.seed_of(file) if seed is None else int(seed)
    return {"settings": sr.random_settings(s), "glow": "auto", "seed": s}


def library(out: Path) -> dict:
    """GET /api/particles/super `library`: {files: [{file, name, random, versions: [...]}], presets: [...]}, files by name, versions oldest first.
    A version whose file left the folder is listed under `orphans` (kept, never deleted)."""
    d = _read(out)
    files = handpicked_files()
    names = {f["file"] for f in files}
    by = {}
    for v in sorted(d["versions"].values(), key=lambda v: v["id"]):
        by.setdefault(v["file"], []).append(v)
    rows = [{"file": f["file"], "name": f["name"], "random": random_look(f["file"]), "versions": by.get(f["file"], [])} for f in files]
    return {"files": rows, "presets": sorted(d["presets"].values(), key=lambda p: p["id"]),
            "orphans": [v for f, vs in by.items() if f not in names for v in vs]}


def shuffle(file: str, seed) -> dict:
    """POST /api/particles/super/random: the random look for `seed` (Shuffle sends the last seed + 1)."""
    if file not in {f["file"] for f in handpicked_files()}:
        raise SetError(f"no Lottie file {file}", 404)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SetError("seed must be a whole number")
    return random_look(file, seed)


def save_version(out: Path, file: str, look, name=None) -> dict:
    """Save as (and the first Save of a random look): a new version V### of `file`. 404 for a file not in the folder."""
    if file not in {f["file"] for f in handpicked_files()}:
        raise SetError(f"no Lottie file {file}", 404)
    clean = clean_look(look)
    now = round(time.time(), 3)
    with _LOCK:
        d = _read(out)
        vid = _next(d, "version", "V")
        rec = {"id": vid, "file": file, "name": _name(name, Path(file).stem), "look": clean, "created": now, "updated": now}
        d["versions"][vid] = rec
        _write(out, d)
    return rec


def update_version(out: Path, vid: str, look=None, name=None) -> dict:
    """Save: the version's look and/or name. 404 for an unknown version."""
    clean = clean_look(look) if look is not None else None
    with _LOCK:
        d = _read(out)
        rec = d["versions"].get(vid)
        if rec is None:
            raise SetError(f"no version {vid}", 404)
        if clean is not None:
            rec["look"] = clean
        if name is not None:
            rec["name"] = _name(name, rec["name"])
        rec["updated"] = round(time.time(), 3)
        _write(out, d)
    return rec


def remove_version(out: Path, vid: str) -> dict:
    """Remove: the saved version only (the Lottie file stays in the folder). {removed, file}."""
    with _LOCK:
        d = _read(out)
        rec = d["versions"].pop(vid, None)
        if rec is None:
            raise SetError(f"no version {vid}", 404)
        _write(out, d)
    return {"removed": vid, "file": rec["file"]}


def save_preset(out: Path, look, name=None) -> dict:
    """Save as preset: P### with the look's sliders (and its colour for Solid / Gradient)."""
    clean = clean_look(look, preset=True)
    with _LOCK:
        d = _read(out)
        pid = _next(d, "preset", "P")
        rec = {"id": pid, "name": _name(name, f"Preset {pid[1:].lstrip('0') or '0'}"), "look": clean, "created": round(time.time(), 3)}
        d["presets"][pid] = rec
        _write(out, d)
    return rec


def remove_preset(out: Path, pid: str) -> dict:
    with _LOCK:
        d = _read(out)
        if d["presets"].pop(pid, None) is None:
            raise SetError(f"no preset {pid}", 404)
        _write(out, d)
    return {"removed": pid}


def export_zip(out: Path, files=None) -> bytes:
    """POST /api/particles/super/export.zip (superReactions.zip): for every file in the folder (or `files`), each saved version, or its random look
    when it has none, for EVERY screen bucket: `superReactions/<entry>/<bucket>.json`, plus manifest.json {superReactions: [{id, file, name,
    saved, look, color, buckets: {bucket: path}}], buckets, seconds, unit_px}. 404 for a named file not in the folder, 409 for an empty folder."""
    from ..engine import super_reaction as sr
    from .lottie_reactions import super_compose
    lib = library(out)
    rows = lib["files"] if files is None else [r for r in lib["files"] if r["file"] in set(map(str, files))]
    if files is not None:
        missing = sorted(set(map(str, files)) - {r["file"] for r in rows})
        if missing:
            raise SetError(f"no Lottie file {missing[0]}", 404)
    if not rows:
        raise SetError("no Lottie files in lottie", 409)
    entries = []
    for r in rows:
        if r["versions"]:
            entries += [{"id": v["id"], "file": r["file"], "name": v["name"], "saved": True, "look": v["look"]} for v in r["versions"]]
        else:
            entries.append({"id": None, "file": r["file"], "name": r["name"], "saved": False, "look": r["random"]})
    buf, used, man = io.BytesIO(), set(), []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for e in entries:
            folder = e["name"].replace("/", "-").replace("\\", "-") or Path(e["file"]).stem
            if folder in used:
                folder = f"{folder} ({e['id']})"
            used.add(folder)
            look = e["look"]
            row = {**e, "color": None, "buckets": {}}
            for b, aspect in sr.BUCKETS.items():
                c = super_compose(out, e["file"], aspect, glow=look.get("glow", "auto"), color=look.get("color"), colors=look.get("colors"),
                                  settings=look.get("settings"))
                arc = f"superReactions/{folder}/{b}.json"
                z.writestr(arc, json.dumps(c["lottie"], separators=(",", ":")))
                row["buckets"][b], row["color"] = arc, c["color"]
            man.append(row)
        z.writestr("manifest.json", json.dumps({"superReactions": man, "buckets": {k: round(v, 4) for k, v in sr.BUCKETS.items()},
                                                "seconds": sr.SUPER_SECONDS, "unit_px": sr.UNIT_PX}, ensure_ascii=False, indent=1))
    return buf.getvalue()
