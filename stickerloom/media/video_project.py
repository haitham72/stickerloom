"""Video / GIF sticker projects (checkpoint 1E). One StickerProject per imported video or GIF, kept on disk under
out/library/projects/<id>/ : project.json (the editable state), source.<ext> (the untouched upload) and frames/ (preview frames).

Contract (see README, "Video / GIF projects"):
  import  -> preview frames + capability probe (chroma-key background removal is READY only for green/blue screens)
  update  -> autosave of the editable state (trim, fps, fit, GIF options, layers with timing); heavy data never enters it
  render  -> decode the trimmed range, optionally key the background, composite the client-baked layer PNGs on the frames
             whose time falls in each layer's timing range, encode WebM / WebP / GIF. The source and project survive any failure.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import matte
from ..engine import ffmpeg as ff
from ..engine.chroma import calibrate, key_diff, key_image
from ..engine.config import EngineConfig
from .library import GREEN_RING_MIN, LibraryError, encode_frames

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".gif"}
ALPHA_EXT = {".gif", ".webm", ".mov"}      # may carry transparency: preview frames are PNG
MAX_UPLOAD = 300 * 1024 * 1024
MAX_SOURCE_SECONDS = 120.0
PREVIEW_BOX = 360
PREVIEW_MAX_FRAMES = 240
GIF_MAX_SECONDS = 10.0
LAYER_TYPES = {"text", "emoji", "sticker"}
BG_STATUS = ("UNAVAILABLE", "READY", "PROCESSING", "READY_WITH_MASK", "ERROR")


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _layer(raw: dict, dur_ms: int) -> dict:
    t = raw.get("type")
    if t not in LAYER_TYPES:
        raise LibraryError(f"layer type must be one of {sorted(LAYER_TYPES)}")
    tf = raw.get("transform") or {}
    payload = raw.get("payload") or {}
    if len(json.dumps(payload)) > 8000:
        raise LibraryError("layer payload too large")
    out = {"id": str(raw.get("id") or uuid.uuid4().hex[:8])[:24], "type": t, "visible": bool(raw.get("visible", True)),
           "locked": bool(raw.get("locked", False)), "zIndex": int(_num(raw.get("zIndex"), 0, 999, 0)),
           "transform": {"x": _num(tf.get("x"), -256, 768, 256), "y": _num(tf.get("y"), -256, 768, 256),
                         "scale": _num(tf.get("scale"), 0.05, 20, 1), "rotation": _num(tf.get("rotation"), -360, 360, 0)},
           "opacity": _num(raw.get("opacity"), 0, 1, 1), "payload": payload}
    tm = raw.get("timing")
    if tm:
        a = int(_num(tm.get("startMs"), 0, dur_ms, 0))
        b = int(_num(tm.get("endMs"), 0, dur_ms, dur_ms))
        out["timing"] = {"startMs": min(a, b), "endMs": max(a, b)}
    return out


def _over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Alpha-composite src over dst (both HxWx4 uint8, straight alpha)."""
    a = src[..., 3:4].astype(np.float32) / 255.0
    if not a.any():
        return dst
    da = dst[..., 3:4].astype(np.float32) / 255.0
    oa = a + da * (1.0 - a)
    rgb = (src[..., :3] * a + dst[..., :3] * da * (1.0 - a)) / np.maximum(oa, 1e-6)
    return np.dstack([np.clip(rgb + .5, 0, 255), np.clip(oa * 255 + .5, 0, 255)]).astype(np.uint8)


class Projects:
    def __init__(self, out: Path, cfg: EngineConfig | None = None):
        self.root = Path(out) / "library" / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or EngineConfig()
        self.lock = threading.RLock()

    # ---- storage
    def _dir(self, pid: str) -> Path:
        if not pid or not pid.isalnum():
            raise LibraryError("no such project", 404)
        d = self.root / pid
        if not (d / "project.json").is_file():
            raise LibraryError("no such project", 404)
        return d

    def _write(self, d: Path, proj: dict) -> None:
        tmp = d / "project.tmp"
        tmp.write_text(json.dumps(proj, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, d / "project.json")

    def get(self, pid: str) -> dict:
        with self.lock:
            return json.loads((self._dir(pid) / "project.json").read_text(encoding="utf-8"))

    def list(self) -> list:
        out = []
        for f in self.root.glob("*/project.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                out.append({"id": p["id"], "name": p["name"], "updatedAt": p["updatedAt"], "kind": p["source"]["kind"],
                            "duration": p["source"]["duration"], "packId": p.get("packId"), "layers": len(p["layers"])})
            except (ValueError, KeyError):
                continue
        return sorted(out, key=lambda x: x["updatedAt"], reverse=True)

    def delete(self, pid: str) -> None:
        with self.lock:
            shutil.rmtree(self._dir(pid))

    def frame_path(self, pid: str, n: int) -> Path:
        p = self.get(pid)
        f = self._dir(pid) / "frames" / f"{int(n):04d}.{p['source']['frameExt']}"
        if not f.is_file():
            raise LibraryError("no such frame", 404)
        return f

    def mask_path(self, pid: str, n: int) -> Path:
        """Preview frame n with the AI matte applied (PNG, cached). Only for clips whose provider is the matte."""
        p = self.get(pid)
        if p["videoBackgroundRemoval"].get("provider") != "matte":
            raise LibraryError("this clip does not use the AI matte", 404)
        import cv2
        d = self._dir(pid) / "frames_matte"
        f = d / f"{int(n):04d}.png"
        if not f.is_file():
            src = self.frame_path(pid, n)
            im = cv2.imread(str(src), cv2.IMREAD_COLOR)
            rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
            a = matte.alpha(rgb, fast=True)
            out = np.dstack([rgb, np.clip(a * 255 + .5, 0, 255).astype(np.uint8)])
            d.mkdir(exist_ok=True)
            cv2.imwrite(str(f), cv2.cvtColor(out, cv2.COLOR_RGBA2BGRA))
        return f

    # ---- import
    def create(self, data: bytes, filename: str) -> dict:
        ext = Path(filename or "").suffix.lower()
        if ext not in VIDEO_EXT:
            raise LibraryError(f"unsupported file type {ext or '(none)'}; use mp4, mov, webm, mkv, avi or gif")
        if not data:
            raise LibraryError("empty upload")
        pid = uuid.uuid4().hex[:10]
        d = self.root / pid
        d.mkdir(parents=True)
        try:
            src = d / f"source{ext}"
            src.write_bytes(data)
            try:
                info = ff.probe(src, vp9_native=ext == ".webm")
            except RuntimeError as e:
                raise LibraryError(str(e), 409)
            if not info["width"]:
                raise LibraryError("could not decode that video (unsupported or corrupt file)")
            dur = info["duration"]
            if dur > MAX_SOURCE_SECONDS:
                raise LibraryError(f"video is {dur:.0f}s; trim it below {MAX_SOURCE_SECONDS:.0f}s first (stickers are a few seconds long)")
            pfps = 12.0 if dur <= 0 else max(2.0, min(12.0, PREVIEW_MAX_FRAMES / dur))
            fext = "png" if ext in ALPHA_EXT else "jpg"
            try:
                n = ff.extract_frames(src, d / "frames", pfps, PREVIEW_BOX, fext, PREVIEW_MAX_FRAMES)
            except RuntimeError as e:
                raise LibraryError(str(e), 409)
            if dur <= 0:
                dur = n / pfps
            import cv2
            im = cv2.imread(str(d / "frames" / f"0000.{fext}"), cv2.IMREAD_UNCHANGED)
            fh, fw = im.shape[:2]
            cap = self._capability(im)
            src_fps = info["fps"] or 12.0
            keep_ms = int(min(dur, self.cfg.video_max_seconds) * 1000)
            proj = {
                "id": pid, "name": (Path(filename).stem or "video")[:60], "type": "animated", "version": 1,
                "source": {"file": src.name, "kind": "gif" if ext == ".gif" else "video", "w": info["width"], "h": info["height"],
                           "aspect": round(fw / fh, 5), "duration": round(dur, 3), "fps": round(src_fps, 2), "previewFps": round(pfps, 4),
                           "frames": n, "frameExt": fext, "hasAlpha": cap["hasAlpha"]},
                "canvas": {"size": self.cfg.size, "fit": "contain"},
                "video": {"trimStartMs": 0, "trimEndMs": keep_ms if dur * 1000 > keep_ms else int(dur * 1000),
                          "currentTimeMs": 0, "fps": int(min(15, max(6, round(src_fps))))},
                "gif": {"enabled": False, "loop": True, "quality": 256},
                "videoBackgroundRemoval": {"enabled": False, "status": cap["status"], "provider": cap.get("provider"),
                                           "chroma": cap.get("chroma"), "model": cap.get("model"), "reason": cap.get("reason")},
                "layers": [], "packId": None, "export": {"format": "webm", "name": "", "emoji": "🙂"},
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            with self.lock:
                self._write(d, proj)
            return proj
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise

    def _capability(self, first) -> dict:
        """Video background removal is a provider capability. The built-in provider is the chroma key: READY only when the
        border ring of the first frame is a green/blue screen. Anything else needs a learned matte (Phase 3C provider)."""
        if first.ndim == 3 and first.shape[2] == 4 and (first[..., 3] < 250).mean() > 0.02:
            return {"status": "UNAVAILABLE", "hasAlpha": True, "reason": "The clip already has transparency."}
        rgb = np.ascontiguousarray(first[..., 2::-1] if first.ndim == 3 else first)   # BGR -> RGB
        h, w = rgb.shape[:2]
        ring = max(2, min(h, w) // 100)
        m = np.zeros((h, w), bool); m[:ring] = m[-ring:] = True; m[:, :ring] = m[:, -ring:] = True
        score = {ch: float(np.median(key_diff(rgb, ch)[m])) for ch in ("green", "blue")}
        ch = max(score, key=score.get)
        if score[ch] >= GREEN_RING_MIN:
            return {"status": "READY", "hasAlpha": False, "provider": "chroma", "chroma": ch}
        st = matte.status(True)
        if st["ok"]:
            return {"status": "READY", "hasAlpha": False, "provider": "matte", "model": st["model"]}
        return {"status": "UNAVAILABLE", "hasAlpha": False,
                "reason": "Removing a normal background from video needs the AI matte (" + st["reason"] + "). Green or blue screens work without it."}

    # ---- autosave
    def update(self, pid: str, patch: dict) -> dict:
        with self.lock:
            d = self._dir(pid)
            p = json.loads((d / "project.json").read_text(encoding="utf-8"))
            dur_ms = int(p["source"]["duration"] * 1000)
            if "name" in patch and str(patch["name"]).strip():
                p["name"] = str(patch["name"]).strip()[:60]
            if isinstance(patch.get("canvas"), dict) and patch["canvas"].get("fit") in ("contain", "cover"):
                p["canvas"]["fit"] = patch["canvas"]["fit"]
            v = patch.get("video")
            if isinstance(v, dict):
                a = int(_num(v.get("trimStartMs", p["video"]["trimStartMs"]), 0, dur_ms, 0))
                b = int(_num(v.get("trimEndMs", p["video"]["trimEndMs"]), 0, dur_ms, dur_ms))
                if b - a < 100:
                    b = min(dur_ms, a + 100); a = max(0, b - 100)
                p["video"].update(trimStartMs=a, trimEndMs=b, currentTimeMs=int(_num(v.get("currentTimeMs"), 0, dur_ms, 0)),
                                  fps=int(_num(v.get("fps", p["video"]["fps"]), 1, self.cfg.video_max_fps, 12)))
            g = patch.get("gif")
            if isinstance(g, dict):
                p["gif"].update(enabled=bool(g.get("enabled", p["gif"]["enabled"])), loop=bool(g.get("loop", p["gif"]["loop"])),
                                quality=int(_num(g.get("quality"), 16, 256, p["gif"]["quality"])))
            r = patch.get("videoBackgroundRemoval")
            if isinstance(r, dict) and "enabled" in r:
                want = bool(r["enabled"])
                st = p["videoBackgroundRemoval"]
                if want and st["status"] not in ("READY", "READY_WITH_MASK", "ERROR"):
                    raise LibraryError(st.get("reason") or "background removal is not available for this clip", 409)
                st["enabled"] = want
                if want and st["status"] == "ERROR":
                    st["status"] = "READY"; st.pop("error", None)
            if isinstance(patch.get("layers"), list):
                if len(patch["layers"]) > 40:
                    raise LibraryError("too many layers (40 max)")
                p["layers"] = [_layer(x, dur_ms) for x in patch["layers"]]
            if "packId" in patch:
                p["packId"] = patch["packId"] or None
            e = patch.get("export")
            if isinstance(e, dict):
                if e.get("format") in ("webm", "webp", "gif"):
                    p["export"]["format"] = e["format"]
                p["export"]["name"] = str(e.get("name", p["export"]["name"]))[:60]
                p["export"]["emoji"] = str(e.get("emoji", p["export"]["emoji"]) or "🙂")[:8]
            p["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._write(d, p)
            return p

    # ---- render
    def compose(self, pid: str, overlays: dict[str, bytes], fmt: str):
        """-> (frames RGBA uint8 Nx512x512x4, fps, info). The decode + optional key + layer compositing; no encoding."""
        import cv2
        p = self.get(pid)
        d = self._dir(pid)
        cfg, s, v = self.cfg, p["source"], p["video"]
        size = cfg.size
        cap_s = GIF_MAX_SECONDS if fmt == "gif" else cfg.video_max_seconds
        start = v["trimStartMs"] / 1000.0
        dur = min((v["trimEndMs"] - v["trimStartMs"]) / 1000.0, cap_s)
        info = {"clipped": (v["trimEndMs"] - v["trimStartMs"]) / 1000.0 > cap_s + 1e-6, "start": round(start, 3)}
        fps = float(max(1, min(v["fps"], cfg.video_max_fps)))
        r = s["aspect"]
        fit = p["canvas"]["fit"]
        if fit == "cover":
            ow, oh = (int(round(size * r)), size) if r >= 1 else (size, int(round(size / r)))
            ow, oh = max(ow, size), max(oh, size)
        else:
            ow, oh = (size, max(2, int(round(size / r)))) if r >= 1 else (max(2, int(round(size * r))), size)
        src = d / s["file"]
        try:
            frames = ff.decode_scaled(src, start, dur, fps, ow, oh, size if fit == "cover" else None)
        except RuntimeError as e:
            raise LibraryError(str(e), 409)
        rm = p["videoBackgroundRemoval"]
        if rm["enabled"] and rm["status"] in ("READY", "READY_WITH_MASK", "ERROR"):
            try:
                if rm.get("provider") == "matte":
                    prev, outs = None, []
                    for f in frames:
                        a = matte.alpha(np.ascontiguousarray(f[..., :3]), fast=True)
                        a = a if prev is None else 0.7 * a + 0.3 * prev          # light temporal smoothing against flicker
                        prev = a
                        outs.append(np.dstack([f[..., :3], np.clip(a * 255 + .5, 0, 255).astype(np.uint8)]))
                    frames = np.stack(outs); info["keyed"] = "matte"
                else:
                    ch = rm.get("chroma") or "green"
                    c = replace(cfg, chroma=ch)
                    calib = calibrate(np.ascontiguousarray(frames[0][..., :3]), ch, max(2, min(frames.shape[1:3]) // 100), None)
                    frames = np.stack([key_image(np.ascontiguousarray(f[..., :3]), c, calib).rgba for f in frames])
                    info["keyed"] = ch
            except Exception as e:                       # the project and source stay intact
                with self.lock:
                    q = self.get(pid); q["videoBackgroundRemoval"].update(status="ERROR", error=str(e)[:200]); self._write(d, q)
                raise LibraryError("Background removal failed. Your video and edits are still saved. Try again, or continue without removal.", 409)
        if fit == "contain":
            n, h, w, _ = frames.shape
            canvas = np.zeros((n, size, size, 4), np.uint8)
            y0, x0 = (size - h) // 2, (size - w) // 2
            canvas[:, y0:y0 + h, x0:x0 + w] = frames
            frames = canvas
        layers = []
        for L in sorted(p["layers"], key=lambda x: x["zIndex"]):
            if not L["visible"] or L["id"] not in overlays:
                continue
            buf = cv2.imdecode(np.frombuffer(overlays[L["id"]], np.uint8), cv2.IMREAD_UNCHANGED)
            if buf is None or buf.ndim != 3 or buf.shape[2] != 4:
                raise LibraryError(f"layer {L['id']} overlay must be a PNG with alpha")
            if buf.shape[:2] != (size, size):
                buf = cv2.resize(buf, (size, size), interpolation=cv2.INTER_AREA)
            layers.append((L, cv2.cvtColor(buf, cv2.COLOR_BGRA2RGBA)))
        drawn = 0
        for k in range(len(frames)):
            t_ms = start * 1000.0 + k * 1000.0 / fps
            f = frames[k]
            for L, ov in layers:
                tm = L.get("timing")
                if tm and not (tm["startMs"] <= t_ms <= tm["endMs"]):
                    continue
                f = _over(f, ov); drawn += 1
            frames[k] = f
        info.update(layers=len(layers), overlay_draws=drawn, frames=len(frames))
        return frames, fps, info

    def render(self, pid: str, fmt: str, overlays: dict[str, bytes]):
        p = self.get(pid)
        frames, fps, info = self.compose(pid, overlays, fmt)
        colors = p["gif"]["quality"] if fmt == "gif" else 256
        loop = p["gif"]["loop"] if fmt == "gif" else True
        data, mime, ext, info = encode_frames(frames, fps, fmt, loop, self.cfg, info, colors)
        if p["videoBackgroundRemoval"]["enabled"] and p["videoBackgroundRemoval"]["status"] in ("READY", "ERROR"):
            with self.lock:
                q = self.get(pid); q["videoBackgroundRemoval"]["status"] = "READY_WITH_MASK"; q["videoBackgroundRemoval"].pop("error", None)
                self._write(self._dir(pid), q)
        return data, mime, ext, info


def decode_overlays(raw: dict) -> dict[str, bytes]:
    out = {}
    for k, v in (raw or {}).items():
        if not isinstance(v, str) or "," not in v:
            raise LibraryError("overlays must be PNG data URLs")
        out[str(k)] = base64.b64decode(v.split(",", 1)[1])
    return out
