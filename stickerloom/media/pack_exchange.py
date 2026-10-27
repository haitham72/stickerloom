"""Pack exchange (WP9): export a library pack to a folder, and import such a folder back.

Export layout (`export_pack`)::

    <dest>/<pack-slug>/
        manifest.json
        stills/01-<name>.png      static stickers, always PNG
        animated/01-<name>.webm   animated stickers, as stored (Telegram's own
                                  animated format over the Bot API)

``manifest.json`` (``schema_version: 1``) lists every asset with its file name,
sha256, emoji, kind and original sticker id.

The Telegram target (``export_telegram_mock``) is MOCKED: it runs the offline
``services.telegram.plan`` (no token, no network, nothing sent, nothing
recorded) and answers the plan as a dry-run report.

Import (``import_pack``) verifies BEFORE writing anything: the manifest must
parse, every file must be present with a matching sha256, every still must
decode, every animation must probe. Only then a NEW pack is created and every
sticker is added with a FRESH id (``Library.add_bytes`` mints uuids), so the
imported ids always differ from the exported ones. ``{old_id: new_id}`` is
returned as ``id_map``.

Animated packs export webm only: there is no .tgs writer for raster art
(plan D8 / review G2.4 forbid TGS for raster art), and a .tgs cannot be
synthesised from a WebM clip.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import time
import zipfile
from pathlib import Path

from ..engine.config import EngineConfig

SCHEMA_VERSION = 1
MANIFEST = "manifest.json"
STATIC_DIR = "stills"
ANIMATED_DIR = "animated"
EXPORTS_DIR = "exports"


class PackExchangeError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _lib(out: Path):
    from .library import Library
    return Library(out)


def resolve_pack(lib, ref: str) -> dict:
    """A pack by id, slug or exact name. 404 when there is none, 409 when a name fits more than one."""
    packs = lib.snapshot()["packs"]
    if not packs:
        raise PackExchangeError("the library has no packs yet", 404)
    hit = next((p for p in packs if p["id"] == ref), None)
    if hit is not None:
        return hit
    hit = next((p for p in packs if p.get("slug") == ref), None)
    if hit is not None:
        return hit
    named = [p for p in packs if p["name"] == ref]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise PackExchangeError(f"{len(named)} packs are named '{ref}': use the pack id", 409)
    known = ", ".join(f"{p['id']} ({p['name']})" for p in packs[:8])
    raise PackExchangeError(f"no pack '{ref}' (packs: {known})", 404)


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_") or "sticker"
    return stem[:48]


def export_pack(out: Path, ref: str, dest: str | Path | None = None, owner: str | None = None) -> dict:
    """Write the pack to ``<dest>/<slug>/`` (default ``<out>/exports/<slug>/``).

    Static stickers are normalised to PNG (``media/library.png_bytes``);
    animated stickers ship as stored WebM. Returns the export report; the
    report's ``assets`` carry the original ``sticker_id`` each, so an import
    can answer the id remap.
    """
    from .library import decode_image, png_bytes
    out = Path(out)
    lib = _lib(out)
    pack = resolve_pack(lib, ref) if owner is None else _owned(lib, ref, owner)
    if not pack.get("stickers"):
        raise PackExchangeError("this pack has no stickers yet", 409)
    from ..generation.prompter import slug as _slug
    pslug = _slug(pack.get("slug") or pack["name"]) or "pack"
    root = (Path(dest) if dest else out / EXPORTS_DIR) / pslug
    stills, anims = root / STATIC_DIR, root / ANIMATED_DIR
    stills.mkdir(parents=True, exist_ok=True)
    anims.mkdir(parents=True, exist_ok=True)
    assets, used = [], set()

    def unique(folder: Path, stem: str, ext: str) -> str:
        base = f"{stem}{ext}"
        name, n = base, 2
        while name in used:
            name = f"{stem}-{n}{ext}"
            n += 1
        used.add(name)
        return name

    for ordinal, s in enumerate(pack["stickers"], 1):
        f = lib.files / s["file"]
        if not f.is_file():
            raise PackExchangeError(f"the file of '{s['name']}' is missing from the library", 404)
        data = f.read_bytes()
        stem = f"{ordinal:02d}-{_safe_stem(s.get('name'))}"
        if s["type"] == "animated":
            if f.suffix.lower() != ".webm":
                raise PackExchangeError(f"'{s['name']}' is animated but stored as {f.suffix or 'no extension'}", 409)
            media, folder, ext = "video", anims, ".webm"
            rgba = None
        else:
            try:
                rgba = decode_image(data)
            except Exception as e:
                raise PackExchangeError(f"'{s['name']}' cannot be read as a picture: {e}", 409) from e
            data = png_bytes(rgba)
            media, folder, ext = "static", stills, ".png"
        name = unique(folder, stem, ext)
        (folder / name).write_bytes(data)
        rel = f"{folder.name}/{name}"
        h, w = (int(rgba.shape[0]), int(rgba.shape[1])) if rgba is not None else (None, None)
        if rgba is None:
            w, h = _webm_dims(f)
        assets.append({"filename": rel, "media": media, "sticker_id": s["id"], "name": s.get("name") or "Sticker",
                       "emoji": s.get("emoji") or "🙂", "sha256": hashlib.sha256(data).hexdigest(),
                       "kb": max(1, len(data) // 1024), "w": w, "h": h,
                       "source": dict(s.get("source") or {})})
    manifest = {"schema_version": SCHEMA_VERSION, "format": "stickerloom-pack-export",
                "pack": {"id": pack["id"], "slug": pslug, "title": pack["name"],
                         "exported_at": round(time.time(), 3), "stickers": len(assets)},
                "assets": assets}
    (root / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    kinds = {"static": sum(1 for a in assets if a["media"] == "static"),
             "video": sum(1 for a in assets if a["media"] == "video")}
    return {"pack_id": pack["id"], "slug": pslug, "title": pack["name"], "dir": str(root),
            "manifest": str(root / MANIFEST), "assets": assets,
            "stickers": len(assets), **kinds}


def _owned(lib, ref: str, owner: str) -> dict:
    packs = lib.snapshot(owner=owner)["packs"]
    hit = next((p for p in packs if p["id"] == ref or p.get("slug") == ref or p["name"] == ref), None)
    if hit is None:
        raise PackExchangeError(f"no pack '{ref}'", 404)
    return hit


def _webm_dims(path: Path) -> tuple:
    from ..engine import ffmpeg as ff
    try:
        info = ff.probe(path)
    except Exception as e:
        raise PackExchangeError(f"'{path.name}' cannot be read as an animation: {e}", 409) from e
    if not info.get("width") or not info.get("height"):
        raise PackExchangeError(f"'{path.name}' has no video stream", 409)
    return int(info["width"]), int(info["height"])


def export_telegram_mock(out: Path, ref: str, owner: str | None = None) -> dict:
    """The Telegram target, MOCKED: the offline send plan, no token, no network.

    Nothing is sent and nothing is recorded (a mock must never feed the
    already-sent logic). ``bot`` stays None, so set names use the placeholder.
    """
    from ..services import telegram
    out = Path(out)
    lib = _lib(out)
    pack = resolve_pack(lib, ref) if owner is None else _owned(lib, ref, owner)
    if not pack.get("stickers"):
        raise PackExchangeError("this pack has no stickers yet", 409)
    plan = telegram.plan(lib, pack["id"], None)
    return {"mocked": True, "pack_id": pack["id"], "title": pack["name"],
            "fingerprint": telegram.fingerprint(lib, pack["id"]),
            "sets": [{"kind": s["kind"], "name": s["name"], "title": s["title"],
                      "stickers": len(s["items"]), "new": len(s["new"])} for s in plan["sets"]],
            "blocked": plan["blocked"], "warnings": plan["warnings"],
            "note": "mocked: the send plan only, no token, no network, nothing was sent"}


# ---------- import ----------

def _read_manifest(root: Path) -> dict:
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except OSError:
        raise PackExchangeError(f"no {MANIFEST} in {root} (export a pack first: stickerloom export <pack> --to dir)", 404)
    except ValueError as e:
        raise PackExchangeError(f"{MANIFEST} is not valid JSON: {e}", 400)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise PackExchangeError(f"{MANIFEST} is not a stickerloom pack export (schema_version {SCHEMA_VERSION})", 400)
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise PackExchangeError(f"{MANIFEST} lists no stickers", 400)
    return manifest


def _unpack_zip(path: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="stickerloom-import-"))
    try:
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():
                target = (tmp / member).resolve()
                if tmp.resolve() not in target.parents and target != tmp.resolve():
                    raise PackExchangeError(f"unsafe path in the zip: {member}", 400)
            z.extractall(tmp)
    except zipfile.BadZipFile as e:
        raise PackExchangeError(f"not a zip file: {e}", 400)
    return tmp


def _import_root(src: str | Path) -> tuple[Path, Path | None]:
    """(manifest dir, temp dir to clean up or None)."""
    src = Path(src)
    if src.is_dir():
        return src, None
    if zipfile.is_zipfile(src):
        tmp = _unpack_zip(src)
        if (tmp / MANIFEST).is_file():
            return tmp, tmp
        kids = [d for d in tmp.iterdir() if d.is_dir() and (d / MANIFEST).is_file()]
        if len(kids) == 1:
            return kids[0], tmp
        raise PackExchangeError(f"no {MANIFEST} at the zip root (nor in one top-level folder)", 400)
    if not src.exists():
        raise PackExchangeError(f"no such file or folder: {src}", 404)
    raise PackExchangeError(f"import a pack export folder or zip, not '{src.name}'", 400)


def verify_import(src: str | Path, cfg: EngineConfig | None = None) -> dict:
    """Check an export WITHOUT writing anything. Raises PackExchangeError
    listing EVERY problem found; returns the manifest plus warnings otherwise.
    Telegram-limit misses (512 px, size) are warnings, never blocks: the import
    is about the bytes, and the pack page judges Telegram-readiness itself."""
    import shutil
    from .library import decode_image
    cfg = cfg or EngineConfig()
    root, tmp = _import_root(src)
    try:
        manifest = _read_manifest(root)
    except Exception:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    problems, warnings, checked = [], [], []
    seen: set = set()
    for n, a in enumerate(manifest["assets"], 1):
        tag = f"asset {n}"
        if not isinstance(a, dict):
            problems.append(f"{tag}: not an object")
            continue
        name = a.get("filename") or ""
        media = a.get("media")
        if media not in ("static", "video"):
            problems.append(f"{tag}: media must be static or video")
            continue
        want_ext = ".png" if media == "static" else ".webm"
        if not name or Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
            problems.append(f"{tag}: unsafe file name '{name}'")
            continue
        if Path(name).suffix.lower() != want_ext:
            problems.append(f"{tag}: '{name}' must end with {want_ext}")
            continue
        if name in seen:
            problems.append(f"{tag}: '{name}' is listed twice")
            continue
        seen.add(name)
        f = root / name
        if not f.is_file():
            problems.append(f"{tag}: '{name}' is missing")
            continue
        data = f.read_bytes()
        if hashlib.sha256(data).hexdigest() != a.get("sha256"):
            problems.append(f"{tag}: '{name}' sha256 mismatch (the file changed after export)")
            continue
        if media == "static":
            try:
                rgba = decode_image(data)
            except Exception:
                problems.append(f"{tag}: '{name}' cannot be read as a picture")
                continue
            h, w = int(rgba.shape[0]), int(rgba.shape[1])
            if a.get("w") not in (None, w) or a.get("h") not in (None, h):
                problems.append(f"{tag}: '{name}' is {w}x{h}, the manifest says {a.get('w')}x{a.get('h')}")
                continue
            if (w, h) != (cfg.size, cfg.size):
                warnings.append(f"'{name}' is {w}x{h}, not Telegram's {cfg.size}x{cfg.size}")
        else:
            from ..engine import ffmpeg as ff
            try:
                info = ff.probe(f)
            except Exception:
                problems.append(f"{tag}: '{name}' cannot be read as an animation")
                continue
            if not info.get("width") or not info.get("height"):
                problems.append(f"{tag}: '{name}' has no video stream")
                continue
            w, h = int(info["width"]), int(info["height"])
            if a.get("w") not in (None, w) or a.get("h") not in (None, h):
                problems.append(f"{tag}: '{name}' is {w}x{h}, the manifest says {a.get('w')}x{a.get('h')}")
                continue
            if (w, h) != (cfg.size, cfg.size):
                warnings.append(f"'{name}' is {w}x{h}, not Telegram's {cfg.size}x{cfg.size}")
            if len(data) > cfg.video_max_bytes:
                warnings.append(f"'{name}' is {len(data) // 1024}KB, over Telegram's {cfg.video_max_bytes // 1024}KB video limit")
        if not (a.get("emoji") or "").strip():
            warnings.append(f"'{name}' has no emoji tag (Telegram needs at least one per sticker)")
        checked.append({"filename": name, "media": media, "kb": max(1, len(data) // 1024), "w": w, "h": h})
    if problems:
        raise PackExchangeError("import refused, nothing was written: " + "; ".join(problems[:8])
                                + (f" (+{len(problems) - 8} more)" if len(problems) > 8 else ""), 400)
    info = manifest.get("pack") or {}
    try:
        if problems:
            raise PackExchangeError("import refused, nothing was written: " + "; ".join(problems[:8])
                                    + (f" (+{len(problems) - 8} more)" if len(problems) > 8 else ""), 400)
        return {"pack": {"slug": info.get("slug") or "pack", "title": info.get("title") or "Imported pack"},
                "manifest": manifest, "checked": checked, "warnings": warnings}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def import_pack(out: Path, src: str | Path, name: str | None = None, owner: str = "local",
               cfg: EngineConfig | None = None) -> dict:
    """Verify an export, then import it as a NEW pack with fresh ids.

    Nothing is written until verification passes. Returns the new pack id,
    the ``{exported sticker id: new sticker id}`` map and any warnings.
    """
    import shutil
    out = Path(out)
    root, tmp = _import_root(src)
    try:
        report = verify_import(root, cfg)
    except Exception:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    manifest = report["manifest"]
    lib = _lib(out)
    title = (name or "").strip()[:60] or str(report["pack"]["title"])[:60]
    pack = lib.create_pack(title, owner=owner)
    id_map: dict = {}
    try:
        for a in manifest["assets"]:
            data = (root / a["filename"]).read_bytes()
            kind = "static" if a["media"] == "static" else "animated"
            s = lib.add_bytes(pack["id"], data, Path(a["filename"]).suffix.lstrip("."),
                              str(a.get("name") or "Sticker")[:60], kind,
                              (str(a.get("emoji") or "").strip() or "🙂")[:20],
                              {"imported_from": str((manifest.get("pack") or {}).get("slug") or "pack"),
                               "imported_pack": str((manifest.get("pack") or {}).get("id") or ""),
                               "imported_name": str(a.get("name") or "")[:60],
                               "imported_file": a["filename"]},
                              w=int(a.get("w") or 512), h=int(a.get("h") or 512))
            if a.get("sticker_id"):
                id_map[str(a["sticker_id"])] = s["id"]
    except Exception:
        try:
            lib.delete_pack(pack["id"])
        except Exception:
            pass
        raise
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"pack_id": pack["id"], "title": title, "added": len(manifest["assets"]),
            "id_map": id_map, "warnings": report["warnings"]}


def cli_summary(report: dict, kind: str) -> list[str]:
    """One-line-per-fact printout for the CLI."""
    if kind == "export":
        return [f"exported {report['stickers']} sticker(s) "
                f"({report['static']} static PNG, {report['video']} animated WebM) to {report['dir']}",
                f"manifest: {report['manifest']}"]
    if kind == "telegram":
        lines = [f"mocked Telegram send of '{report['title']}' (nothing was sent, no token, no network):"]
        for s in report["sets"]:
            lines.append(f"  {s['kind']}: set {s['name']} ({s['stickers']} stickers, {s['new']} new)")
        lines += [f"  blocked: {x}" for x in report["blocked"][:6]]
        lines += [f"  warning: {x}" for x in report["warnings"][:6]]
        return lines
    lines = [f"imported {report['added']} sticker(s) into new pack '{report['title']}' ({report['pack_id']})",
             f"ids remapped: {len(report['id_map'])}"]
    return lines + [f"  warning: {x}" for x in report["warnings"][:6]]
