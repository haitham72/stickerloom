"""History = the real watch folders (inputs/Images_gen and inputs/videos_gen), with Remove.

A sheet folder img-NNN-<subject> and its video folder vid-NNN-<subject> are one row. Remove never deletes outright: the folders move
to <out>/trash/<id>/ (the media is not in git, so a mistaken click must be undoable) and can be restored under their own names
(the names in the watch folders are final) or deleted for good from the trash list. Names are validated against the folder
pattern, so nothing outside the two watch folders can be touched."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import sources

SIDES = {"img": "Images_gen", "vid": "videos_gen"}
ID_RE = re.compile(r"^\d{8}-\d{6}-\d{3}-[a-z0-9_]+$")


class WatchError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def _size(folder: Path) -> tuple[int, list[dict]]:
    files = [{"name": f.name, "size": f.stat().st_size, "dir": f.parent.relative_to(folder).as_posix()} for f in sorted(folder.rglob("*")) if f.is_file()]
    return sum(f["size"] for f in files), files


def _side(folder: Path | None) -> dict | None:
    if not folder or not folder.is_dir():
        return None
    total, files = _size(folder)
    return {"name": folder.name, "bytes": total, "files": files[:12], "n_files": len(files)}


def list_rows(inp: Path, out: Path, made: dict) -> list[dict]:
    """One row per folder number, image side and video side together. `made` = pipeline.generations_by_folder(out)."""
    rows: dict = {}
    for kind, sub in SIDES.items():
        base = Path(inp) / sub
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            m = sources.DIR_RE.match(d.name)
            if d.is_dir() and m and m[1] == kind:
                r = rows.setdefault((m[2], m[3]), {"number": m[2], "subject": m[3], "img": None, "vid": None})
                r[kind] = _side(d)
    out_rows = []
    for (num, subj), r in sorted(rows.items()):
        r["generations"] = made.get((subj, num), [])
        r["thumb"] = f"/api/watch/thumb/{r['img']['name']}" if r["img"] and any(f["name"].lower().endswith((".jpg", ".jpeg", ".png", ".webp")) for f in r["img"]["files"]) else None
        out_rows.append(r)
    return out_rows


def thumb_path(inp: Path, out: Path, name: str, px: int = 160) -> Path:
    """A small cached JPEG of the first image in an image folder (the sheets are 2K, a list of them must stay light)."""
    import cv2
    import numpy as np
    m = sources.DIR_RE.match(name)
    if not m or m[1] != "img":
        raise WatchError("not an image folder", 400)
    d = Path(inp) / SIDES["img"] / name
    imgs = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in sources.IMG_EXT) if d.is_dir() else []
    if not imgs:
        raise WatchError("no image in that folder", 404)
    src = imgs[0]
    dest = Path(out) / "thumbs" / f"{name}-{int(src.stat().st_mtime)}-{px}.jpg"
    if not dest.is_file():
        im = cv2.imdecode(np.fromfile(str(src), np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            raise WatchError("cannot decode the image", 422)
        h, w = im.shape[:2]
        k = px / max(h, w)
        small = cv2.resize(im, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA)
        dest.parent.mkdir(parents=True, exist_ok=True)
        cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tofile(str(dest))
    return dest


def trash_dir(out: Path) -> Path:
    return Path(out) / "trash"


def remove(inp: Path, out: Path, number: str, subject: str) -> dict:
    """Move img-NNN-<subject> and vid-NNN-<subject> (whichever exist) to the trash. Returns the trash entry."""
    if not re.fullmatch(r"\d{3}", number or "") or not re.fullmatch(r"[A-Za-z0-9_]+", subject or ""):
        raise WatchError("bad folder id")
    items = []
    for kind, sub in SIDES.items():
        d = Path(inp) / sub / f"{kind}-{number}-{subject}"
        if d.is_dir():
            items.append((kind, sub, d))
    if not items:
        raise WatchError(f"No folder {number}-{subject} in the watch folders", 404)
    tid = time.strftime("%Y%m%d-%H%M%S") + f"-{number}-{subject.lower()}"
    dest = trash_dir(out) / tid
    moved = []
    for kind, sub, d in items:
        target = dest / sub / d.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), str(target))
        moved.append({"side": sub, "name": d.name})
    meta = {"id": tid, "removed": round(time.time(), 3), "number": number, "subject": subject, "items": moved}
    (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def list_trash(out: Path) -> list[dict]:
    t = trash_dir(out)
    rows = []
    if t.is_dir():
        for d in sorted(t.iterdir(), reverse=True):
            f = d / "meta.json"
            if d.is_dir() and f.is_file():
                try:
                    meta = json.loads(f.read_text(encoding="utf-8"))
                    meta["bytes"] = sum(_size(d / i["side"] / i["name"])[0] for i in meta["items"] if (d / i["side"] / i["name"]).is_dir())
                    rows.append(meta)
                except Exception:
                    continue
    return rows


def _entry(out: Path, tid: str) -> tuple[Path, dict]:
    if not ID_RE.match(tid or ""):
        raise WatchError("bad trash id")
    d = trash_dir(out) / tid
    f = d / "meta.json"
    if not f.is_file():
        raise WatchError("Not in the trash", 404)
    return d, json.loads(f.read_text(encoding="utf-8"))


def restore(inp: Path, out: Path, tid: str) -> dict:
    d, meta = _entry(out, tid)
    for i in meta["items"]:                                    # refuse first, move after: never half-restore
        if (Path(inp) / i["side"] / i["name"]).exists():
            raise WatchError(f"{i['side']}/{i['name']} exists again: rename or remove it first", 409)
    for i in meta["items"]:
        src = d / i["side"] / i["name"]
        if src.is_dir():
            (Path(inp) / i["side"]).mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(Path(inp) / i["side"] / i["name"]))
    shutil.rmtree(d, ignore_errors=True)
    return meta


def purge(out: Path, tid: str) -> dict:
    d, meta = _entry(out, tid)
    shutil.rmtree(d)
    return meta
