"""Sticker library: packs, imported/edited stickers, photo cutout, pack export (desktop builder).

Storage is plain files under <out>/library/: library.json + files/<group>/<name>. Writes are atomic and locked.
Naming follows runtime/names.py: `{media}-{subject}-{action}-{pack}-{UTC time}-{fingerprint}.{ext}`, the pack and the time being those of the moment the file was born in the library.
A sticker that came from a generation lives in `files/G012/` (the batch it came from), anything made in the library (imports, cut-outs, editor renders, trims) in `files/own/`.
`file` in library.json is that path relative to files/ (forward slashes). A move to another pack renames nothing: the pack of a sticker is library data, not part of its file name.
Older libraries (one flat folder, `img-001-<pack>-<name>`) are regrouped and renamed once, when the library is opened (`Library.migrate_layout`, flag `layout: 2` in library.json)."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import matte
from ..flow import pipeline as pl
from ..engine import ffmpeg as ff
from ..engine.chroma import calibrate, key_diff, key_image, remove_specks
from ..engine.config import EngineConfig
from ..engine.sheet import encode_static
from ..generation.prompter import slug
from ..runtime import names

# Animated WebP download limit (the editor's WebP/GIF downloads; Telegram needs none of it).
WA_ANIM_MAX = 500 * 1024
CUTOUT_MAX_SIDE = 1024
GREEN_RING_MIN = 40      # median key-channel excess on the border ring that means "this is a green/blue screen"



def readable_name(key: str) -> str:
    """'generic_emojis_laughing' -> 'Generic emojis laughing' ({subject} {action}), what the user sees; the file name stays in `file_name`."""
    t = " ".join(str(key).replace("-", " ").replace("_", " ").split())
    return (t[:1].upper() + t[1:])[:60] or "Sticker"


_GEN_NAME = re.compile(r"^(?:img|vid)-\d{3,}-[a-z0-9_]+-(?P<key>.+)$")      # a generation file name of the old convention
_GENERATION = re.compile(r"^G\d{3,}$")
LAYOUT = 2


def _legacy_names(db: dict) -> dict:
    """Stickers added before the readable name existed carry the file-style name; move it to `file_name` and show the readable one."""
    for p in db.get("packs", []):
        for s in p.get("stickers", []):
            if "file_name" not in s and (s.get("source") or {}).get("generation"):
                m = _GEN_NAME.match(s.get("name", ""))
                if m:
                    s["file_name"] = s["name"]
                    s["name"] = readable_name(m["key"])
    return db


class LibraryError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def decode_image(data: bytes) -> np.ndarray:
    """Any browser-common image (png/jpg/webp/gif first frame/bmp) -> RGBA uint8."""
    try:
        im = Image.open(io.BytesIO(data)); im.load()
    except Exception as e:
        raise LibraryError(f"not an image I can read: {e}")
    return np.array(im.convert("RGBA"))


def _bbox(alpha: np.ndarray, thr=127):
    ys, xs = np.where(alpha > thr)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else None


def _grabcut(rgb: np.ndarray) -> np.ndarray:
    """OpenCV GrabCut seeded with an inset rectangle. Offline, no weights; fine for simple backgrounds."""
    h, w = rgb.shape[:2]
    k = min(1.0, 640 / max(h, w))
    small = cv2.resize(rgb, (max(8, int(w * k)), max(8, int(h * k))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    mx, my = max(2, int(sw * 0.04)), max(2, int(sh * 0.04))
    mask = np.zeros((sh, sw), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), mask, (mx, my, sw - 2 * mx, sh - 2 * my), bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
    m = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n > 1:                                   # keep the main subject plus anything at least 15% of it
        big = st[1:, cv2.CC_STAT_AREA].max()
        keep = np.zeros(n, bool); keep[1:] = st[1:, cv2.CC_STAT_AREA] >= 0.15 * big
        m = keep[lab].astype(np.uint8)
    soft = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 1.2)
    return cv2.resize(soft, (w, h), interpolation=cv2.INTER_LINEAR)


def cutout(rgba: np.ndarray, cfg: EngineConfig | None = None, method: str = "auto"):
    """Photo/graphic -> transparent subject, cropped with a small pad. Returns (rgba, info).
    Already-transparent input is kept; green/blue screens use the engine's chroma key; anything else uses the learned matte
    (U2-Net via onnxruntime, see media/matte.py) when installed, else GrabCut. method: auto | matte | grabcut forces one of the last two."""
    cfg = cfg or EngineConfig()
    h, w = rgba.shape[:2]
    if max(h, w) > CUTOUT_MAX_SIDE:
        k = CUTOUT_MAX_SIDE / max(h, w)
        rgba = cv2.resize(rgba, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
        h, w = rgba.shape[:2]
    info = {"width": w, "height": h}
    if method not in ("auto", "matte", "grabcut"):
        raise LibraryError("method must be auto, matte or grabcut")
    if method == "auto" and (rgba[..., 3] < 250).mean() > 0.02:
        info["method"] = "existing_alpha"
        out = rgba.copy()
    else:
        rgb = np.ascontiguousarray(rgba[..., :3])
        ring = max(2, min(h, w) // 100)
        score = {}
        for ch in ("green", "blue"):
            m = np.zeros((h, w), bool); m[:ring] = m[-ring:] = True; m[:, :ring] = m[:, -ring:] = True
            score[ch] = float(np.median(key_diff(rgb, ch)[m]))
        ch = max(score, key=score.get)
        if method == "auto" and score[ch] >= GREEN_RING_MIN:
            c = replace(cfg, chroma=ch, border_px=ring)
            out = key_image(rgb, c, calibrate(rgb, ch, ring, None)).rgba
            info.update(method=f"chroma_{ch}", ring_excess=round(score[ch], 1))
        else:
            st = matte.status()
            if method == "matte" and not st["ok"]:
                raise LibraryError("AI matte is not available: " + st["reason"], 409)
            if method != "grabcut" and st["ok"]:
                a = remove_specks(matte.alpha(rgb), cfg.min_component_px)
                info["method"] = "matte_" + st["model"]
            else:
                a = remove_specks(_grabcut(rgb), cfg.min_component_px)
                info["method"] = "grabcut"
                if method == "auto":
                    info["hint"] = "GrabCut assumes the subject fills the frame; install the AI matte for photos (python -m stickerloom doctor)"
            out = np.dstack([rgb, np.clip(a * 255 + .5, 0, 255).astype(np.uint8)])
    bb = _bbox(out[..., 3])
    frac = float((out[..., 3] > 127).mean())
    info["foreground"] = round(frac, 3)
    if not bb or frac < 0.01:
        raise LibraryError("no subject found (cutout came back empty); try a photo with a clearer subject")
    if frac > 0.92:
        info["warning"] = "almost everything was kept; use Erase to remove the background"
    pad = max(4, int(0.02 * max(h, w)))
    x0, y0, x1, y1 = max(0, bb[0] - pad), max(0, bb[1] - pad), min(w, bb[2] + pad), min(h, bb[3] + pad)
    return np.ascontiguousarray(out[y0:y1, x0:x1]), info


def png_bytes(rgba: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    return buf.tobytes()


def validate_replace(data: bytes, ext: str, cfg: EngineConfig):
    """An edited file for a pack sticker -> (bytes, ext, kind): the Telegram limits, checked. Static (PNG/JPG/WebP):
    the editor's rules (512 px, 512 KB). Animated: WebM only (Telegram-style), 512 px, 256 KB. Anything else is refused
    in words."""
    ext = str(ext or "").lower().lstrip(".")
    if ext in ("png", "jpg", "jpeg", "webp"):
        body, out_ext, _ = validate_render(data, cfg)
        return body, out_ext, "static"
    if ext == "webm":
        if len(data) > cfg.video_max_bytes:
            raise LibraryError(f"{len(data) // 1024}KB is over Telegram's {cfg.video_max_bytes // 1024}KB video limit; shorten it")
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as t:
            t.write(data)
            tmp = t.name
        try:
            info = ff.probe(Path(tmp), vp9_native=True)
        finally:
            Path(tmp).unlink(missing_ok=True)
        if not info.get("width") or (info["width"], info["height"]) != (cfg.size, cfg.size):
            raise LibraryError(f"animated stickers are {cfg.size}x{cfg.size}, got {info.get('width')}x{info.get('height')}")
        return data, "webm", "animated"
    raise LibraryError("Replace with a PNG, JPG or WebP picture (a static sticker) or a WebM clip (an animated one)")


def validate_render(data: bytes, cfg: EngineConfig):
    """The editor's exported 512x512 canvas -> (bytes, ext, checks). Same rules as engine stickers."""
    rgba = decode_image(data)
    S = cfg.size
    checks = [("dimensions", rgba.shape[:2] == (S, S), f"{rgba.shape[1]}x{rgba.shape[0]}")]
    if not checks[0][1]:
        raise LibraryError(f"canvas must be {S}x{S}, got {rgba.shape[1]}x{rgba.shape[0]}")
    fg = int((rgba[..., 3] > 127).sum())
    checks.append(("foreground", fg >= cfg.min_foreground_px, f"{fg}px"))
    if fg < cfg.min_foreground_px:
        raise LibraryError("sticker is empty")
    body, ext = encode_static(rgba, cfg)
    checks.append(("size", len(body) <= cfg.static_max_bytes, f"{len(body) // 1024}KB {ext}"))
    if len(body) > cfg.static_max_bytes:
        raise LibraryError(f"{len(body) // 1024}KB is over the {cfg.static_max_bytes // 1024}KB limit; simplify the sticker")
    return body, ext, checks


def anim_frames(path: Path, start: float, end: float, fps: float, cfg: EngineConfig):
    """Decode a sticker WEBM and pick frames for [start, end) resampled to `fps` (nearest source frame)."""
    info = ff.probe(path, vp9_native=True)
    src_fps, dur = info["fps"] or 30.0, info["duration"]
    if not info["width"] or dur <= 0:
        raise LibraryError("cannot read that animation (is ffmpeg with libvpx-vp9 installed? run: python -m stickerloom doctor)", 409)
    start, end = max(0.0, float(start)), min(dur, float(end))
    fps = max(1.0, min(float(fps), cfg.video_max_fps))
    if end - start < 0.1:
        raise LibraryError("trim range is too short")
    end = min(end, start + cfg.video_max_seconds)
    try:
        allf = ff.decode_full(path, info["width"], info["height"], int(dur * src_fps) + 3, None)
    except RuntimeError as e:
        raise LibraryError(str(e), 409)
    idx = [min(len(allf) - 1, int((start + k / fps) * src_fps + 1e-6)) for k in range(max(1, int(round((end - start) * fps))))]
    return allf[idx], fps, {"src_fps": round(src_fps, 2), "frames": len(idx), "start": round(start, 3), "end": round(end, 3)}


def anim_export(path: Path, start, end, fps, fmt: str, loop: bool, cfg: EngineConfig):
    """-> (bytes, mime, ext, info). webm = Telegram (VP9+alpha, <=256KB ladder); webp = WhatsApp (<=500KB); gif = anywhere (no soft alpha)."""
    frames, fps, info = anim_frames(path, start, end, fps, cfg)
    return encode_frames(frames, fps, fmt, loop, cfg, info)


def encode_frames(frames, fps: float, fmt: str, loop: bool, cfg: EngineConfig, info: dict | None = None, gif_colors: int = 256):
    """RGBA frames -> (bytes, mime, ext, info) with the platform size ladders. Shared by sticker trimming and video projects."""
    info = dict(info or {})
    info.update(format=fmt, fps=fps)
    try:
        if fmt == "webm":
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "o.webm"
                from ..engine.video import _encode_fit  # the same fit the animations get: the best crf that is under the budget
                info["crf"], data, _ = _encode_fit(frames, fps, cfg, out)
                if len(data) > cfg.video_max_bytes:
                    raise LibraryError(f"{len(data) // 1024}KB is over the {cfg.video_max_bytes // 1024}KB limit even at the lowest quality; trim it or lower the frame rate")
            mime = "video/webm"
        elif fmt == "webp":
            for q in (75, 60, 45, 30, 20):
                data = ff.encode_anim(frames, fps, "webp", q, loop); info["quality"] = q
                if len(data) <= WA_ANIM_MAX:
                    break
            if len(data) > WA_ANIM_MAX:
                raise LibraryError(f"{len(data) // 1024}KB is over WhatsApp's {WA_ANIM_MAX // 1024}KB animated limit; trim it or lower the frame rate")
            mime = "image/webp"
        elif fmt == "gif":
            data, mime = ff.encode_anim(frames, fps, "gif", 0, loop, gif_colors), "image/gif"
        else:
            raise LibraryError("format must be webm, webp or gif")
    except RuntimeError as e:
        raise LibraryError(str(e), 409)
    info["kb"] = max(1, len(data) // 1024)
    return data, mime, fmt, info


LIGHT_BYTES = 64 * 1024      # light exports aim for this size…
LIGHT_MAX_CRF = 46           # …but never trade quality past this rung of the crf ladder: a clip that cannot reach the size at crf 46 ships at crf 46, bigger


RETIME_S = 3.0            # Retime to 3 s: an export may stretch a shorter animation to Telegram's full 3 seconds…
SHORT_S = 2.95            # …when it is shorter than this (a 72-frame clip at 24 fps is 3.0 s and is left alone)


def _clip(path: Path):
    """(frames RGBA, seconds) of a stored animation, decoded with its alpha."""
    info = ff.probe(path, vp9_native=True)
    src_fps, dur, w, h = info["fps"] or 30.0, info["duration"], info["width"], info["height"]
    if not w or dur <= 0:
        raise LibraryError("cannot read that animation (is ffmpeg with libvpx-vp9 installed? run: python -m stickerloom doctor)", 409)
    frames = ff.decode_full(path, w, h, int(dur * src_fps) + 3, None)
    return frames, len(frames) / src_fps


def clip_seconds(path: Path) -> float | None:
    """How long a stored animation plays, in seconds (None when it cannot be read)."""
    try:
        info = ff.probe(path, vp9_native=True)
        return round(float(info["duration"]), 2) if info.get("duration") else None
    except Exception:
        return None


def retime_webm(path: Path, cfg: EngineConfig, seconds: float = RETIME_S):
    """The same frames played over `seconds` (a slower frame rate, nothing invented, the loop kept), fitted to Telegram's size limit.
    None when the clip is already at least SHORT_S long. Returns (bytes, {"retimed_from", "seconds", "fps", "frames", "crf", "kb"})."""
    import tempfile
    from ..engine.video import _encode_fit
    frames, secs = _clip(path)
    if secs >= SHORT_S:
        return None
    fps = round(len(frames) / seconds, 3)
    with tempfile.TemporaryDirectory() as td:
        crf, data, _ = _encode_fit(frames, fps, cfg, Path(td) / "retimed.webm")
    return data, {"retimed_from": round(secs, 2), "seconds": seconds, "fps": fps, "frames": len(frames), "crf": crf, "kb": max(1, len(data) // 1024)}


def light_webm(path: Path, cfg: EngineConfig, retime: bool = False):
    """A stored animation re-encoded smaller: decode with alpha, keep EVERY frame at its own frame rate, and take the best quality of the crf ladder that fits LIGHT_BYTES, never past LIGHT_MAX_CRF: a detailed sticker that cannot reach
    the size at that rung ships at it, bigger, with fits False. Retime stretches the same frames over 3 s.
    Returns (bytes, {"crf", "kb", "frames", "fps", "fits"}). The stored file is untouched; quality vs size is on the record."""
    import dataclasses
    import tempfile
    from ..engine.video import _encode_fit
    frames, secs = _clip(path)
    lcfg = dataclasses.replace(cfg, crf_ladder=tuple(c for c in cfg.crf_ladder if c <= LIGHT_MAX_CRF) or (LIGHT_MAX_CRF,))
    retimed_from = None
    if retime and secs < SHORT_S:                                   # Retime to 3 s: the same frames over the full 3 seconds
        retimed_from, secs = round(secs, 2), RETIME_S
    n = len(frames)
    fps = round(n / secs, 3)
    with tempfile.TemporaryDirectory() as td:
        crf, data, _ = _encode_fit(frames, fps, lcfg, Path(td) / "light.webm", start=len(lcfg.crf_ladder) // 2, budget=LIGHT_BYTES)
    meta = {"crf": crf, "kb": max(1, len(data) // 1024), "frames": n, "fps": fps, "fits": len(data) <= LIGHT_BYTES,
            **({"retimed_from": retimed_from, "seconds": RETIME_S} if retimed_from else {})}
    return data, meta


def pack_counts(pack: dict) -> dict:
    """Presentation counts, after the caller's existing pack visibility filter."""
    items = pack.get("stickers") or []
    reactions = sum(bool((item.get("source") or {}).get("particle_set")) for item in items)
    return {"sticker_count": len(items) - reactions, "reaction_count": reactions}


class Library:
    def __init__(self, out: Path):
        self.root = Path(out) / "library"
        self.files = self.root / "files"
        self.db_path = self.root / "library.json"
        self.lock = threading.RLock()
        self.files.mkdir(parents=True, exist_ok=True)
        try:
            self.migrate_layout()
        except Exception as e:                                       # never keep the app from starting over a tidy-up; the next start tries again
            print(f"[stickerloom] library layout migration skipped: {e}", flush=True)

    # ---- file names and folders
    @staticmethod
    def group_of(source: dict | None) -> str:
        """The folder of a file: the batch it came from (G012), else `own`."""
        g = str((source or {}).get("generation") or "")
        return g if _GENERATION.match(g) else "own"

    def _group_dir(self, source: dict | None) -> str:
        """The folder a NEW file goes to: its batch's folder name on disk (`G111-dog_as_banana-...` for a labelled batch, `G110` for an older one), else `own`.
        The record keeps the path, so nothing ever has to work the label out again."""
        g = self.group_of(source)
        return pl.gen_dir(self.root.parent, int(g[1:])).name if g != "own" else g

    @staticmethod
    def new_file_name(media: str, name: str, pack_slug: str, ext: str, created: float, sid: str, gen_file_name: str | None = None) -> str:
        """`img-falcon_stickers-open_arms-gold_pack-20261002T133320-a3f9c1.png`. Subject and action come from the generator's own file name when there is one, else from the sticker's name."""
        p = names.parse(gen_file_name or "")
        subject, action = (p["subject"], names.action_of(p["subject"], p["action"])) if p else (slug(name) or "sticker", "custom")
        return f"{names.build(media, subject, action, pack=pack_slug, when=created, seed=sid)}.{ext}"

    def migrate_layout(self) -> int:
        """One time: files from the flat folder go to `files/<group>/` under the new names. All or nothing (a failed move puts back what was moved and the flag stays unset).
        Returns the number of files moved."""
        with self.lock:
            if not self.db_path.exists():
                return 0
            raw = json.loads(self.db_path.read_text(encoding="utf-8"))
            if raw.get("layout", 1) >= LAYOUT:
                return 0
            db = _legacy_names(raw)
            plan = []
            for p in db.get("packs", []):
                for s in p.get("stickers", []):
                    if "/" in s["file"] or not (self.files / s["file"]).is_file():
                        continue
                    ext = s["file"].rsplit(".", 1)[-1]
                    media = "vid" if s["type"] == "animated" else "img"
                    new = f"{self.group_of(s.get('source'))}/" + self.new_file_name(media, s["name"], p["slug"], ext, float(s.get("created") or time.time()), s["id"], s.get("file_name"))
                    plan.append((s, s["file"], new))
            done = []
            try:
                for s, old, new in plan:
                    (self.files / new).parent.mkdir(parents=True, exist_ok=True)
                    (self.files / old).replace(self.files / new)
                    done.append((old, new))
            except OSError:
                for old, new in reversed(done):
                    (self.files / new).replace(self.files / old)
                raise
            for s, _, new in plan:
                s["file"] = new
            for p in db.get("packs", []):
                p.pop("next", None)                                  # the per-pack counter is gone with the counter names
            db["layout"] = LAYOUT
            self._save(db)
            return len(plan)

    # ---- persistence
    def _load(self) -> dict:
        if not self.db_path.exists():
            return {"packs": []}
        try:
            db = _legacy_names(json.loads(self.db_path.read_text(encoding="utf-8")))
            for p in db.get("packs", []) + db.get("trash", {}).get("packs", []):
                p.setdefault("owner", "local")
            return db
        except ValueError:
            shutil.copy(self.db_path, self.db_path.with_suffix(".corrupt.json"))
            return {"packs": []}

    def _save(self, db: dict) -> None:
        from ..runtime import atomic
        atomic.write_text(self.db_path, json.dumps(db, ensure_ascii=False, indent=1))

    def _pack(self, db, pid):
        p = next((p for p in db["packs"] if p["id"] == pid), None)
        if not p:
            raise LibraryError("no such pack", 404)
        return p

    # ---- reads
    def snapshot(self, recent: int = 30, owner: str | None = None) -> dict:
        with self.lock:
            db = self._load()
        if owner is not None:
            db["packs"] = [p for p in db["packs"] if p.get("owner", "local") == owner]
        db["packs"] = [dict(p, **pack_counts(p)) for p in db["packs"]]
        allst = [dict(s, pack_id=p["id"], pack=p["name"]) for p in db["packs"] for s in p["stickers"]]
        allst.sort(key=lambda s: s["created"], reverse=True)
        return {"packs": db["packs"], "recent": allst[:recent], "total": len(allst),
                "sticker_count": sum(p["sticker_count"] for p in db["packs"]), "reaction_count": sum(p["reaction_count"] for p in db["packs"])}

    # ---- packs
    def owns(self, pid: str, owner: str, trash: bool = False) -> bool:
        with self.lock:
            db = self._load()
            packs = db.get("packs", []) + (db.get("trash", {}).get("packs", []) if trash else [])
            return any(p["id"] == pid and p.get("owner", "local") == owner for p in packs)

    def owns_file(self, file: str, owner: str) -> bool:
        target = (self.files / file).resolve()
        if self.files.resolve() not in target.parents:
            return False
        return any((self.files / s["file"]).resolve() == target for p in self.snapshot(owner=owner)["packs"] for s in p["stickers"])

    def create_pack(self, name: str, owner: str = "local") -> dict:
        name = (name or "").strip() or "My Pack"
        with self.lock:
            db = self._load()
            p = {"id": uuid.uuid4().hex[:8], "name": name[:60], "slug": slug(name) or "pack", "cover": None, "owner": owner,
                 "created": time.time(), "stickers": []}
            db["packs"].append(p)
            self._save(db)
            return p

    def update_pack(self, pid: str, name=None, cover=None, order=None, lead=None, parent=None) -> dict:
        """`lead: true` is the Studio's *Assign as parent*: this pack leads its batch group in the Library (flow/groups.pack_groups); the newest wins.
        `parent`: a pack id nests this pack under that one as a SUB-PACK (the Packs column's drag of a pack onto a pack; nothing moves, every sticker
        stays where it is) and makes that pack its group's parent; "" takes it out again. A pack is never nested under itself or its own sub-pack."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            if parent is not None:
                if parent == "":
                    p.pop("parent", None)
                else:
                    by = {x["id"]: x for x in db["packs"]}
                    if parent not in by:
                        raise LibraryError("no such pack to nest under", 404)
                    q, seen = parent, set()
                    while q in by and q not in seen:                      # walk up from the new parent: meeting this pack would make a loop
                        if q == pid:
                            raise LibraryError("a pack cannot go under itself or one of its own sub-packs")
                        seen.add(q); q = by[q].get("parent")
                    p["parent"] = parent
                    by[parent]["lead_at"] = round(time.time(), 3)
            if lead:
                p["lead_at"] = round(time.time(), 3)
            if name is not None and name.strip():
                p["name"] = name.strip()[:60]
            if cover is not None:
                if cover and not any(s["id"] == cover for s in p["stickers"]):
                    raise LibraryError("cover must be a sticker of this pack")
                p["cover"] = cover or None
            if order is not None:
                by = {s["id"]: s for s in p["stickers"]}
                if sorted(order) != sorted(by):
                    raise LibraryError("order must list every sticker of the pack exactly once")
                p["stickers"] = [by[i] for i in order]
            self._save(db)
            return p

    # ---- the pack trash: Delete pack is SOFT. The pack leaves `packs` for `trash.packs` in library.json with its stickers and its files exactly
    # where they were; Restore puts it back with the same id (so its particle sets still point at it). Only `purge_pack` (flow/purge.py asks for it, after a typed or plain confirmation) deletes for good.
    def delete_pack(self, pid: str, by: str = "human") -> dict:
        """Move the pack to the trash. Nothing on disk changes."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            db["packs"].remove(p)
            db.setdefault("trash", {}).setdefault("packs", []).append(dict(p, deleted=round(time.time(), 3), deleted_by=by))
            self._save(db)
        return {"ok": True, "id": pid, "trashed": True, "name": p["name"], "stickers": len(p["stickers"])}

    def restore_pack(self, pid: str) -> dict:
        """Back from the trash under the same id and name, stickers and cover untouched. Never overwrites."""
        with self.lock:
            db = self._load()
            t = next((q for q in self._trashed(db) if q["id"] == pid), None)
            if t is None:
                raise LibraryError("that pack is not in the trash", 404)
            if any(q["id"] == pid for q in db["packs"]):
                raise LibraryError("a pack with that id exists now; the trashed one stays safe", 409)
            db["trash"]["packs"].remove(t)
            p = {k: v for k, v in t.items() if k not in ("deleted", "deleted_by")}
            db["packs"].append(p)
            self._save(db)
        return {"ok": True, "id": pid, "restored": True, "name": p["name"], "stickers": len(p["stickers"])}

    @staticmethod
    def _trashed(db: dict) -> list:
        return (db.get("trash") or {}).get("packs") or []

    def trashed_packs(self) -> list[dict]:
        """The packs in the trash, newest deleted first (full records: stickers, files, when and by whom)."""
        with self.lock:
            rows = [dict(p) for p in self._trashed(self._load())]
        return sorted(rows, key=lambda p: p.get("deleted") or 0, reverse=True)

    def _holders(self, db: dict, fname: str, skip_pid: str | None = None) -> list[dict]:
        """Every pack, live or in the trash, that holds a sticker on file `fname` (except `skip_pid`): [{id, name, trashed}]."""
        rows = []
        for trashed, packs in ((False, db["packs"]), (True, self._trashed(db))):
            for q in packs:
                if q["id"] != skip_pid and any(s["file"] == fname for s in q["stickers"]):
                    rows.append({"id": q["id"], "name": q["name"], "trashed": trashed})
        return rows

    def trash_report(self, pid: str) -> dict:
        """What purging the trashed pack `pid` would remove: its stickers with their files and bytes (`missing` when the file is already gone), and `shared`: the stickers whose file another pack
        (live or trashed) also holds, with those packs. A shared file is never deleted by this purge: it stays for the other packs."""
        with self.lock:
            db = self._load()
            p = next((q for q in self._trashed(db) if q["id"] == pid), None)
            if p is None:
                raise LibraryError("that pack is not in the trash", 404)
            files, shared = [], []
            for s in p["stickers"]:
                f = self.files / s["file"]
                try:
                    size = f.stat().st_size
                except OSError:
                    size = None
                other = self._holders(db, s["file"], skip_pid=pid)
                files.append({"sticker": s["id"], "name": s["name"], "file": s["file"], "bytes": size, "missing": size is None, "shared": bool(other)})
                if other:
                    shared.append({"sticker": s["id"], "name": s["name"], "file": s["file"], "also_in": other})
        return {"id": pid, "name": p["name"], "deleted": p.get("deleted"), "by": p.get("deleted_by"), "stickers": len(p["stickers"]), "files": files, "shared": shared,
                "bytes": sum(f["bytes"] or 0 for f in files if not f["shared"]), "from_batches": sorted({str((s.get("source") or {}).get("generation")) for s in p["stickers"] if (s.get("source") or {}).get("generation")})}

    def purge_pack(self, pid: str, confirm_shared: bool = False) -> dict:
        """Delete the trashed pack for good: its files, then its record. A sticker whose file another pack also holds is REFUSED (409, in words, naming the packs) until `confirm_shared`; confirmed,
        the pack's record goes and that file stays for the other pack(s) (deleting it would leave a pack pointing at nothing). Files first, record last: a run that stops half-way leaves the pack
        in the trash and running it again finishes (a file already gone is fine). Returns {id, name, stickers, files_removed, bytes, kept_shared[]}."""
        with self.lock:
            db = self._load()
            p = next((q for q in self._trashed(db) if q["id"] == pid), None)
            if p is None:
                if any(q["id"] == pid for q in db["packs"]):
                    raise LibraryError("that pack is not in the trash: delete it first, then it can be deleted for good", 409)
                raise LibraryError("that pack is not in the trash", 404)
            shared = {s["file"]: self._holders(db, s["file"], skip_pid=pid) for s in p["stickers"]}
            shared = {f: h for f, h in shared.items() if h}
            if shared and not confirm_shared:
                lines = "; ".join(f"“{s['name']}” is also in {', '.join(h['name'] + (' (in the trash)' if h['trashed'] else '') for h in shared[s['file']])}" for s in p["stickers"] if s["file"] in shared)
                raise LibraryError(f"“{p['name']}” has stickers that other packs use too: {lines}. Deleting this pack for good keeps those files for the other packs. Confirm to go on.", 409)
            gone = nbytes = 0
            for s in p["stickers"]:
                if s["file"] in shared:
                    continue
                f = self.files / s["file"]
                try:
                    size = f.stat().st_size
                except OSError:
                    size = None
                f.unlink(missing_ok=True)
                if size is not None:
                    gone, nbytes = gone + 1, nbytes + size
                if f.parent != self.files:                                  # a batch folder with nothing left in it goes away
                    try:
                        f.parent.rmdir()
                    except OSError:
                        pass
            db["trash"]["packs"].remove(p)
            self._save(db)
        return {"id": pid, "name": p["name"], "stickers": len(p["stickers"]), "files_removed": gone, "bytes": nbytes, "kept_shared": sorted(shared)}

    def _unlink(self, db, fname, skip=None):
        if not self._holders(db, fname, skip_pid=skip):                  # a file a pack in the trash still holds stays too
            f = self.files / fname
            f.unlink(missing_ok=True)
            if f.parent != self.files:                                  # a batch folder with nothing left in it goes away
                try:
                    f.parent.rmdir()
                except OSError:
                    pass

    # ---- stickers
    def add_bytes(self, pid: str, data: bytes, ext: str, name: str, kind: str = "static", emoji: str = "🙂",
                  source: dict | None = None, w: int = 512, h: int = 512, gen_file_name: str | None = None) -> dict:
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            media = "vid" if kind == "animated" else "img"
            base = slug(name) or "sticker"
            sid, now = uuid.uuid4().hex[:8], time.time()
            fname = f"{self._group_dir(source)}/" + self.new_file_name(media, name or base, p["slug"], ext, now, sid, gen_file_name)
            (self.files / fname).parent.mkdir(parents=True, exist_ok=True)
            (self.files / fname).write_bytes(data)
            s = {"id": sid, "name": (name or base)[:60], "file": fname, "type": kind, "emoji": emoji or "🙂",
                 "kb": max(1, len(data) // 1024), "w": w, "h": h, "source": source or {}, "created": now}
            p["stickers"].append(s)
            if not p["cover"]:
                p["cover"] = s["id"]
            self._save(db)
            return s

    def add_render(self, pid: str, png: bytes, name: str, emoji: str, cfg: EngineConfig, source=None) -> dict:
        body, ext, checks = validate_render(png, cfg)
        s = self.add_bytes(pid, body, ext, name, "static", emoji, source)
        s["checks"] = checks
        return s

    def add_from_generation(self, out: Path, pid: str, gid: int, index: int, kind: str = "static", name: str | None = None, emoji: str | None = None) -> dict:
        st = pl.state(out, gid)
        if st.get("kind") == "particles":
            raise LibraryError(f"{st['generation_id']} is a sheet of particles, not of stickers: a particle is not a 512 px deliverable. Use its cells as a particle set "
                               f"(POST /api/particles {{from_generation}}, or Use as particle set) and they burst for a pack.", 409)
        t = st["stickers"][index - 1]
        rel = t.get("webm") if kind == "animated" else t.get("png")
        if not rel or (kind == "animated" and t.get("anim_status") != "READY") or (kind != "animated" and t.get("status") != "READY"):
            raise LibraryError("that sticker is not READY" + (" (animate it first)" if kind == "animated" else ""), 409)
        f = pl.gen_dir(Path(out), gid) / rel
        if not f.is_file():
            raise LibraryError("sticker file is missing", 404)
        s = self.add_bytes(pid, f.read_bytes(), f.suffix.lstrip("."), (name or "").strip()[:60] or (t.get("title") or readable_name(t.get("key") or f.stem)), kind, (emoji or "").strip()[:20] or t.get("emoji") or "🙂",
                           {"generation": st["generation_id"], "index": index}, gen_file_name=t.get("name") or f.stem)
        return self.set_file_name(pid, s["id"], t.get("name") or f.stem)

    def replace_file(self, pid: str, sid: str, data: bytes, ext: str, kind: str | None = None, keep_prev: bool = False) -> dict:
        """New content for an existing sticker (same id, name, emoji, position, cover, particle links: the id never
        changes). A different extension renames the file (png -> webp); `kind` changes static <-> animated (the type
        follows the file). The replaced file is kept once (`prev_file`) for one undo."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            s = next((s for s in p["stickers"] if s["id"] == sid), None)
            if not s:
                raise LibraryError("no such sticker", 404)
            s.pop("prev_metadata", None)
            if keep_prev:
                cur = self.files / s["file"]
                if cur.is_file():
                    prel = (str(Path(s["file"]).parent / (Path(s["file"]).stem + ".prev" + Path(s["file"]).suffix))).replace("\\", "/")
                    (self.files / prel).parent.mkdir(parents=True, exist_ok=True)
                    (self.files / prel).write_bytes(cur.read_bytes())
                    old_prev = s.get("prev_file")
                    s["prev_file"] = prel
                    if old_prev and old_prev != prel:
                        self._unlink(db, old_prev)
            fname = Path(s["file"]).with_suffix("." + ext).as_posix()
            (self.files / fname).write_bytes(data)
            if fname != s["file"]:
                self._unlink(db, s["file"]); s["file"] = fname
            if kind in ("static", "animated") and kind != s.get("type"):
                s["type"] = kind
            s["kb"] = max(1, len(data) // 1024); s["edited"] = time.time()
            self._save(db)
            return s

    def undo_replace(self, pid: str, sid: str) -> dict:
        """Take back a replace: the kept previous file is the sticker again (one-shot; a second undo is refused)."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            s = next((s for s in p["stickers"] if s["id"] == sid), None)
            if not s:
                raise LibraryError("no such sticker", 404)
            prev = s.get("prev_file")
            f = self.files / prev if prev else None
            if not prev or not f.is_file():
                raise LibraryError("There is no replaced file to undo.", 409)
            data = f.read_bytes()
            ext = Path(prev).suffix.lstrip(".") or "png"
            fname = Path(s["file"]).with_suffix("." + ext).as_posix()
            (self.files / fname).write_bytes(data)
            if fname != s["file"]:
                self._unlink(db, s["file"]); s["file"] = fname
            s["type"] = "animated" if ext == "webm" else "static"
            s["kb"] = max(1, len(data) // 1024); s["edited"] = time.time()
            f.unlink(missing_ok=True)
            s.pop("prev_file", None)
            for key, value in s.pop("prev_metadata", {}).items():
                if value is None:
                    s.pop(key, None)
                else:
                    s[key] = value
            self._save(db)
            return s

    def add_emoji_from_generation(self, out: Path, pid: str, gid: int, index: int, kind: str, decision: str,
                                  mode: str = "add", name: str | None = None) -> dict:
        """Apply a manual batch's explicit duplicate decision within one pack. Repeated delivery is idempotent."""
        from ..generation.emoji_batches import identity
        from ..generation.emoji import first_emoji
        if decision not in ("new", "add", "overwrite"):
            raise LibraryError("Choose overwrite, add or an unused emoji first.")
        state = pl.state(out, gid)
        cell = state["stickers"][index - 1]
        glyph = first_emoji(cell.get("emoji"))
        source = {"generation": state["generation_id"], "index": index}
        with self.lock:
            db = self._load(); pack = self._pack(db, pid)
            same = [s for s in pack["stickers"] if all((s.get("source") or {}).get(k) == v for k, v in source.items())]
            delivered = next((s for s in same if s["type"] == kind), None)
            if delivered:
                return delivered
            matches = [s for s in pack["stickers"] if identity(first_emoji(s.get("emoji"))) == identity(glyph)]
            target = (same[-1] if same and kind == "animated" and mode == "replace" else
                      max(matches, key=lambda s: (s.get("emoji_version", 1), s.get("created", 0))) if decision == "overwrite" and matches else None)
            if decision == "new" and matches and not same:
                raise LibraryError(f"{glyph} was added to this pack after the preview. Choose overwrite or add first.", 409)
            if target:
                rel = cell.get("webm") if kind == "animated" else cell.get("png")
                status = cell.get("anim_status") if kind == "animated" else cell.get("status")
                file = pl.gen_dir(Path(out), gid) / (rel or "missing")
                if status != "READY" or not file.is_file():
                    raise LibraryError("That sticker is not READY.", 409)
                previous = {key: target.get(key) for key in ("source", "file_name", "emoji_version", "emoji")}
                self.replace_file(pid, target["id"], file.read_bytes(), file.suffix.lstrip("."), kind, keep_prev=True)
                db = self._load(); pack = self._pack(db, pid)
                saved = next(s for s in pack["stickers"] if s["id"] == target["id"])
                saved.update(source=source, emoji=glyph, file_name=cell.get("name") or file.stem,
                             emoji_version=target.get("emoji_version", 1), prev_metadata=previous)
            else:
                versions = {}
                for s in matches:
                    src = s.get("source") or {}
                    key = (src.get("generation"), src.get("index")) if src.get("generation") else s["id"]
                    versions[key] = max(versions.get(key, 0), s.get("emoji_version", 1))
                version = same[-1].get("emoji_version", 1) if same else max(len(versions), max(versions.values(), default=0)) + 1
                label = name or cell.get("title") or readable_name(cell.get("key"))
                label = re.sub(r"(?:[_ ]v?\d{2,})$", "", label, flags=re.I)
                if version > 1:
                    label = label[:56] + f" {version:02d}"
                added = self.add_from_generation(out, pid, gid, index, kind, label)
                db = self._load(); pack = self._pack(db, pid)
                saved = next(s for s in pack["stickers"] if s["id"] == added["id"])
                saved["emoji_version"] = version
            self._save(db)
            return saved

    def copies_of(self, pid: str, sid: str) -> list[dict]:
        """Other packs' stickers from the same batch sticker (generation + index): the offer after a replace."""
        with self.lock:
            db = self._load()
            src = {}
            for p in db["packs"]:
                if p["id"] == pid:
                    s = next((x for x in p["stickers"] if x["id"] == sid), None)
                    if s:
                        src = s.get("source") or {}
            if not src.get("generation") or src.get("index") is None:
                return []
            gen, idx = src["generation"], src["index"]
            return [{"pack_id": p["id"], "pack": p["name"], "id": x["id"], "name": x["name"]}
                    for p in db["packs"] if p["id"] != pid
                    for x in p["stickers"]
                    if (x.get("source") or {}).get("generation") == gen and (x.get("source") or {}).get("index") == idx]

    def refresh_from_generation(self, out: Path, gen_id: str, index: int, png: Path | None, webm: Path | None) -> int:
        """A sticker edited in the Studio is the single source: every pack copy of that generation sticker takes the new file (static copies the image,
        animated copies the animation). Names, emoji and order stay."""
        with self.lock:
            targets = [(p["id"], s["id"], s["type"]) for p in self._load()["packs"] for s in p["stickers"]
                       if (s.get("source") or {}).get("generation") == gen_id and (s.get("source") or {}).get("index") == index]
        n = 0
        for pid, sid, kind in targets:
            f = webm if kind == "animated" else png
            if f and f.is_file():
                self.replace_file(pid, sid, f.read_bytes(), f.suffix.lstrip(".")); n += 1
        return n

    def set_file_name(self, pid: str, sid: str, file_name: str) -> dict:
        """The generator's file name (img-027-generic_emojis-generic_emojis_laughing) is kept as metadata, apart from the readable name."""
        with self.lock:
            db = self._load(); s = next(s for s in self._pack(db, pid)["stickers"] if s["id"] == sid)
            s["file_name"] = file_name
            self._save(db)
            return s

    def static_twins(self, pid: str, gen_id: str, indices: list[int]) -> list[dict]:
        """The still stickers of this pack that came from the same sticker (generation + index) as the animated ones about to be added."""
        with self.lock:
            p = self._pack(self._load(), pid)
        return [s for s in p["stickers"] if s["type"] == "static" and (s.get("source") or {}).get("generation") == gen_id and (s.get("source") or {}).get("index") in indices]

    def replace_static_with_animated(self, pid: str, gen_id: str, indices: list[int]) -> int:
        """Each animated sticker takes the place (position, cover) of its still twin and the still is deleted."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid); n = 0
            for i in indices:
                src = lambda s: (s.get("source") or {}).get("generation") == gen_id and (s.get("source") or {}).get("index") == i
                old = next((s for s in p["stickers"] if s["type"] == "static" and src(s)), None)
                new = next((s for s in reversed(p["stickers"]) if s["type"] == "animated" and src(s)), None)
                if not old or not new:
                    continue
                at = p["stickers"].index(old)
                p["stickers"].remove(new); p["stickers"].remove(old)
                p["stickers"].insert(at, new)
                if p["cover"] == old["id"]:
                    p["cover"] = new["id"]
                self._unlink(db, old["file"]); n += 1
            self._save(db)
            return n

    def add_final(self, out: Path, pid: str, gid: int) -> dict:
        """G5 -> Library: add every sticker of the approved final pack (approved at G2 AND G4) as an animated sticker."""
        st = pl.state(out, gid)
        pack = st["reviews"].get("pack")
        if not pack or pack["decision"] != "APPROVE":
            raise LibraryError("The pack is not final yet (G5): approve it first.", 409)
        added = [self.add_from_generation(out, pid, gid, i, "animated") for i in pack["stickers"]]
        return {"added": len(added), "stickers": [a.get("id") for a in added] if isinstance(added[0], dict) else []}

    def set_telegram(self, pid: str, kind: str, block: dict) -> None:
        """Remember the Telegram set made from this pack (one per kind), so sending again only adds what is new."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            sets = [t for t in p.setdefault("telegram", {}).setdefault("sets", []) if t["kind"] != kind]
            p["telegram"]["sets"] = sets + [block]
            self._save(db)

    def record_export(self, pid: str, record: dict) -> None:
        """Remember a send of the whole pack (what it was, when, where) so that sending the same content again is recognised: `telegram.exports`, the last ten."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            ex = p.setdefault("telegram", {}).setdefault("exports", [])
            ex.append(record)
            p["telegram"]["exports"] = ex[-10:]
            self._save(db)

    def delete_sticker(self, pid: str, sid: str) -> None:
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            s = next((s for s in p["stickers"] if s["id"] == sid), None)
            if not s:
                raise LibraryError("no such sticker", 404)
            p["stickers"].remove(s)
            if p["cover"] == sid:
                p["cover"] = p["stickers"][0]["id"] if p["stickers"] else None
            self._unlink(db, s["file"])
            self._save(db)

    def delete_stickers(self, items: list[dict]) -> int:
        """Bulk delete: items = [{pack_id, id}, ...]. One save; a cover that goes is replaced by the pack's first sticker."""
        n = 0
        with self.lock:
            db = self._load()
            for it in items:
                p = next((p for p in db["packs"] if p["id"] == it.get("pack_id")), None)
                s = next((s for s in (p or {}).get("stickers", []) if s["id"] == it.get("id")), None)
                if not s:
                    continue
                p["stickers"].remove(s)
                if p["cover"] == s["id"]:
                    p["cover"] = p["stickers"][0]["id"] if p["stickers"] else None
                self._unlink(db, s["file"]); n += 1
            self._save(db)
        return n

    def move_sticker(self, pid: str, sid: str, to: str) -> dict:
        """Move a sticker to another pack. Nothing on disk changes: the pack is library data, not part of the file's name."""
        with self.lock:
            db = self._load(); src = self._pack(db, pid); dst = self._pack(db, to)
            if src is dst:
                raise LibraryError("sticker is already in that pack")
            s = next((s for s in src["stickers"] if s["id"] == sid), None)
            if not s:
                raise LibraryError("no such sticker", 404)
            src["stickers"].remove(s)
            if src["cover"] == sid:
                src["cover"] = src["stickers"][0]["id"] if src["stickers"] else None
            dst["stickers"].append(s)
            if not dst["cover"]:
                dst["cover"] = sid
            self._save(db)
            return s

    MEDIA_KEYS = ("file", "file_name", "type", "w", "h", "kb", "dur", "fps", "frames")

    def merge_pack(self, src: str, into: str, by: str = "human") -> dict:
        """Fold pack `src` into `into`. An animated sticker whose
        still twin (the same batch cell) is in `into` UPGRADES that still: the still keeps its id, name, emoji, place and particle links (sets point at it)
        and takes the animated file; nothing is deleted from disk. Every other sticker moves over as it is. The emptied pack goes to the trash (Restore)."""
        cell = lambda s: ((s.get("source") or {}).get("generation"), (s.get("source") or {}).get("index"))
        with self.lock:
            db = self._load(); a = self._pack(db, src); b = self._pack(db, into)
            if a is b:
                raise LibraryError("a pack cannot be merged into itself")
            upgraded = moved = 0
            for st in list(a["stickers"]):
                twin = next((x for x in b["stickers"] if x.get("type") == "static" and cell(x) == cell(st)), None) \
                    if st.get("type") == "animated" and cell(st)[0] and cell(st)[1] else None
                a["stickers"].remove(st)
                if twin:
                    for k in self.MEDIA_KEYS:
                        if k in st:
                            twin[k] = st[k]
                        else:
                            twin.pop(k, None)
                    twin["particles"] = list(dict.fromkeys((twin.get("particles") or []) + (st.get("particles") or [])))
                    twin["merged_from"] = {"pack": src, "sticker": st["id"], "at": round(time.time(), 3)}
                    upgraded += 1
                else:
                    b["stickers"].append(st); moved += 1
                    if not b.get("cover"):
                        b["cover"] = st["id"]
            a["cover"] = None
            self._save(db)
        trashed = False
        if not a["stickers"]:
            self.delete_pack(src, by=by); trashed = True
        return {"into": into, "upgraded": upgraded, "moved": moved, "trashed": trashed}

    def move_stickers(self, items: list[dict], to: str) -> dict:
        """Bulk move: items = [{pack_id, id}, ...] (a selection may span packs) into pack `to`, ALL OR NOTHING: everything is checked first (an unknown pack or sticker refuses the
        whole batch with nothing changed); stickers already in `to` are skipped. Only library.json changes (one load, one save): no file is renamed or moved."""
        with self.lock:
            db = self._load(); dst = self._pack(db, to)
            plan, seen, skipped = [], set(), 0
            for it in items:
                src = self._pack(db, str(it.get("pack_id") or ""))
                s = next((s for s in src["stickers"] if s["id"] == it.get("id")), None)
                if not s:
                    raise LibraryError("no such sticker", 404)
                if src is dst or s["id"] in seen:
                    skipped += 1
                    continue
                seen.add(s["id"])
                plan.append((src, s))
            if not items:
                raise LibraryError("nothing selected to move")
            for src, s in plan:
                src["stickers"].remove(s)
                if src["cover"] == s["id"]:
                    src["cover"] = src["stickers"][0]["id"] if src["stickers"] else None
                dst["stickers"].append(s)
                if not dst["cover"]:
                    dst["cover"] = s["id"]
            if plan:
                self._save(db)
            return {"moved": len(plan), "skipped": skipped, "to": to}

    def rename_sticker(self, pid: str, sid: str, name=None, emoji=None, action=None) -> dict:
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
            s = next((s for s in p["stickers"] if s["id"] == sid), None)
            if not s:
                raise LibraryError("no such sticker", 404)
            if action is not None:
                from ..generation import actions
                if not isinstance(action, str) or (action and action not in actions._BANK_OF):
                    raise LibraryError("Choose a canonical action from the emoji bank")
                if action:
                    s["action"] = action
                else:
                    s.pop("action", None)
            if name and name.strip():
                s["name"] = name.strip()[:60]
            if emoji:
                s["emoji"] = emoji
            self._save(db)
            return s

    def short_animations(self, pid: str) -> list[dict]:
        """The pack's animations shorter than SHORT_S, [{id, name, emoji, seconds}]: what an export asks about (Retime to 3 s or keep the length)."""
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
        rows = []
        for s in p["stickers"]:
            f = self.files / s["file"]
            if s.get("type") == "animated" and f.suffix.lower() == ".webm" and f.is_file():
                sec = clip_seconds(f)
                if sec is not None and sec < SHORT_S:
                    rows.append({"id": s["id"], "name": s.get("name"), "emoji": s.get("emoji"), "seconds": sec})
        return rows

    def export_zip(self, pid: str, light: bool = True, retime: bool = False) -> tuple[bytes, str]:
        """The pack as one downloadable zip (Export as .zip): every sticker file as stored (`.webm` for the animated ones,
        `.png` / `.webp` for the static ones), renamed to the export contract
        `{emoji}-{pack_slug}-{multi_action_tag}-{sNN}-{G###}-{date}.{ext}` (`media/export_names.py`), and a `manifest.json`
        (schema version 1: pack, assets with filename, sha256, tags and source cell). Source media stays intact; export resolutions are saved under out/.
        `light` (default on): animated stickers ship re-encoded small (every frame kept, the best quality down to crf 46 that fits ~64 KB each: `light_webm`; the row records crf/kb/frames so quality can be judged).
        `retime`: an animation shorter than SHORT_S plays its same frames over 3 s (`retime_webm`, or inside `light_webm`; the row records retimed_from).
        Every animated sticker also gets its grey stand-in beside it, `{its file stem}_grey.png`, from its still (`media/grey.py`; manifest `grey`).
        Returns (zip bytes, a safe file stem)."""
        import hashlib
        from . import grey as _grey
        from . import export_names as xn
        from . import export_resolutions as xr
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
        if not p["stickers"]:
            raise LibraryError("this pack has no stickers yet", 409)
        out = self.root.parent
        pslug = xn.slug_snake(p.get("slug") or p["name"]) or "pack"
        rows, buf, used, gens = [], io.BytesIO(), set(), set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:             # webm / png are already compressed
            greys = _grey.Writer(z)
            for ordinal, s in enumerate(p["stickers"], 1):
                f = self.files / s["file"]
                still = None
                if not f.is_file():
                    raise LibraryError(f"the file of '{s['name']}' is missing from the library", 404)
                data = f.read_bytes()
                src = s.get("source") or {}
                g = self.group_of(src)                                       # "G012" or "own"
                key, tags, created, bemojis = None, None, None, None
                try:
                    idx = int(src.get("index") or 0)
                except (TypeError, ValueError):
                    idx = 0
                index = idx if 1 <= idx <= 99 else ordinal
                if g != "own":
                    gens.add(g)
                    try:
                        st = pl.read_result(out, int(g[1:]))
                        cells = st.get("stickers") or []
                        t = cells[index - 1] if 1 <= index <= len(cells) else None
                        if t:
                            key, tags, bemojis = t.get("key"), t.get("tags"), t.get("emoji")
                            created = st.get("created")
                            if t.get("png"):
                                still = pl.gen_dir(out, int(g[1:])) / t["png"]       # the image sheet's cell of this sticker, for its grey stand-in
                    except Exception:
                        pass
                gid = int(g[1:]) if g != "own" else 0
                try:
                    date = xn.datestamp(created or f.stat().st_mtime)
                except OSError:
                    date = xn.datestamp()
                desc = xr.describe(out, f"G{gid:03d}/S{index}" if g != "own" else f"library/{s['id']}",
                                   emoji=s.get("emoji") or bemojis, key=key, tags=tags or [s.get("name")], action=s.get("action"),
                                   slug=pslug, index=index, gid=gid, date=date, exclude=pslug.split("_"))
                name = desc["stem"] + f.suffix.lower()
                if name in used:                                              # never overwrite one entry in the zip
                    base, n = name, 2
                    while name in used:
                        name = re.sub(r"(\.[a-z0-9]+)$", f"-{n}\\1", base)
                        n += 1
                used.add(name)
                light_meta = None
                if s["type"] == "animated" and f.suffix.lower() == ".webm" and (light or retime):
                    try:
                        if light:
                            data, light_meta = light_webm(f, EngineConfig(), retime=retime)
                        else:
                            rt = retime_webm(f, EngineConfig())
                            if rt:
                                data, light_meta = rt
                    except LibraryError:
                        light_meta = None     # unreadable file: ship it as stored, like before
                z.writestr(name, data)
                media = "video" if s["type"] == "animated" else "static"
                if media == "video":
                    greys.add(name, still, f, s["id"], desc["emoji"])
                rows.append({"sticker_id": s["id"], "source_generation": f"G{gid:03d}",
                             "source_cell": f"S{index}" if g != "own" else None, "action": desc["action"],
                             "emoji": desc["emoji"], "emojis": desc["emojis"], "tags": [*desc["tokens"], desc["emoji"]],
                             "media": media, "revision": 1, "export_date": date, "filename": name,
                             "sha256": hashlib.sha256(data).hexdigest(), "kb": max(1, len(data) // 1024),
                             **({"light": True, **light_meta} if light_meta and light else light_meta or {}),
                             **xr.manifest_fields(desc)})
            z.writestr("manifest.json", json.dumps({"schema_version": 1,
                             "pack": {"id": p["id"], "slug": pslug, "title": p["name"],
                                      "generations": sorted(gens)},
                             "assets": rows, **({"grey": greys.rows} if greys.rows else {})}, indent=2, ensure_ascii=False))
        return buf.getvalue(), slug(p["name"]) or "pack"

    def sticker_path(self, pid: str, sid: str) -> tuple[Path, dict]:
        with self.lock:
            db = self._load(); p = self._pack(db, pid)
        s = next((s for s in p["stickers"] if s["id"] == sid), None)
        if not s:
            raise LibraryError("no such sticker", 404)
        return self.files / s["file"], s

    def animate(self, pid: str, sid: str, start, end, fps, fmt: str, loop: bool, cfg: EngineConfig, save: bool = False, name: str = ""):
        path, s = self.sticker_path(pid, sid)
        if s["type"] != "animated":
            raise LibraryError("only animated stickers can be edited on the timeline")
        data, mime, ext, info = anim_export(path, start, end, fps, fmt, loop, cfg)
        if not save:
            return data, mime, ext, info
        if fmt != "webm":
            raise LibraryError("only WEBM can be saved back to the pack (webp/gif are downloads)")
        new = self.add_bytes(pid, data, "webm", name.strip() or s["name"] + " trimmed", "animated", s["emoji"],
                             {"from": s["id"], "trim": [info["start"], info["end"]], "fps": info["fps"]})
        new["info"] = info
        return new
