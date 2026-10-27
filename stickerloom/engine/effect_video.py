"""Particle effects, the video side: a text-only Kling clip (a grid of burst cells on a key-colour screen, each cell empty at the start and at the end) becomes one Telegram video
sticker per cell. Pure engine code (no database, no model client): `cut_cells` reads the clip, `finish_cell` makes one sticker, `encode_and_check` is shared with the simulated mode.

What is enforced and what is only said (the owner's standing rule, 2026-10-02: never a block a person cannot get past): the Telegram limits (format, size, codec, dimensions, fps,
duration, no audio, alpha) are the only hard failures, because Telegram itself would reject the file. Everything about the effect (it starts empty, it ends empty, it is a burst,
it stays in its cell) is a WARN with the reason, and `settle` repairs the two most common faults by construction (a clip that has not quite emptied by its last frame is faded out;
one that starts with something already on screen is faded in), so the file always ends the way a Telegram effect does.

The screen behind the burst is measured too, on the returned clip BEFORE keying (`screen_of`, `effect_checks.screen_flatness` / `seam_line`): Kling sometimes draws panels, walls or
patterns instead of one flat colour. The numbers go into every cell's metrics (`screen_*`) and a bad screen is the WARN `key_is_seamless` (never a block)."""
from __future__ import annotations

import dataclasses
import math
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import effect_checks, ffmpeg as ff, particles, verify
from .chroma import calibrate, detect_key, key_image

FPS = 30
SECONDS = 3.0
NOT_FOR_BURSTS = {"loop_seam", "alpha_stable", "identity_kept", "sharpness", "motion_present"}     # anim checks about a character that stays itself: they say nothing about a burst
TECHNICAL = {"size_budget", "codec_vp9", "dimensions", "fps", "duration", "no_audio", "alpha_mode_tag", "alpha_decoded"}    # the checks of the anim stage Telegram itself enforces
MAX_FRAMES = 400


def cell_rects(width: int, height: int, rows: int, cols: int) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) of every cell of an equal rows x cols grid, row by row."""
    cw, ch = width // cols, height // rows
    return [(c * cw, r * ch, cw, ch) for r in range(rows) for c in range(cols)]


def resample(frames: np.ndarray, src_fps: float, dst_fps: int = FPS, seconds: float = SECONDS) -> np.ndarray:
    """The clip on Telegram's clock: `seconds` at `dst_fps` (90 frames), nearest frame in time, the last frame held if the source is shorter."""
    n = int(round(dst_fps * seconds))
    idx = np.minimum(np.round(np.arange(n) / dst_fps * src_fps).astype(int), len(frames) - 1)
    return frames[idx]


def square(frames: np.ndarray, size: int) -> np.ndarray:
    """RGBA frames resized to size x size on premultiplied alpha (no dark fringe)."""
    out = []
    for f in frames:
        if f.shape[0] == size and f.shape[1] == size:
            out.append(f)
            continue
        a = f[..., 3:4].astype(np.float32) / 255.0
        pm = np.concatenate([f[..., :3].astype(np.float32) * a, f[..., 3:4].astype(np.float32)], -1)
        interp = cv2.INTER_AREA if f.shape[0] > size else cv2.INTER_CUBIC
        r = cv2.resize(pm, (size, size), interpolation=interp)
        al = np.clip(r[..., 3:4], 0, 255)
        rgb = np.where(al > 0, r[..., :3] / np.maximum(al / 255.0, 1e-4), 0)
        out.append(np.concatenate([np.clip(rgb, 0, 255), al], -1).astype(np.uint8))
    return np.stack(out)


def settle(frames: np.ndarray, fps: int = FPS, ramp_seconds: float = 0.4) -> tuple[np.ndarray, dict]:
    """Make a clip start and end on an empty screen by construction. A tail that has not emptied is faded out over the last `ramp_seconds` (the last 3 frames fully empty); a head
    that starts with something already showing is faded in the same way. Returns (frames, {"tail_faded": bool, "head_faded": bool})."""
    cov = np.array(effect_checks.coverage_curve(frames))
    out = frames.copy()
    info = {"tail_faded": False, "head_faded": False}
    k = max(4, int(round(ramp_seconds * fps)))
    if cov[-3:].max() > effect_checks.EMPTY_MAX:
        ramp = np.concatenate([np.linspace(1.0, 0.0, k - 3), np.zeros(3)])
        out[-k:, ..., 3] = (out[-k:, ..., 3].astype(np.float32) * ramp[:, None, None]).astype(np.uint8)
        info["tail_faded"] = True
    if cov[:3].max() > effect_checks.EMPTY_MAX:
        ramp = np.concatenate([np.zeros(3), np.linspace(0.0, 1.0, k - 3)])
        out[:k, ..., 3] = (out[:k, ..., 3].astype(np.float32) * ramp[:, None, None]).astype(np.uint8)
        info["head_faded"] = True
    return out, info


def _thumbs(rgb: np.ndarray, size: int = effect_checks.SCREEN_CELL, k: int = effect_checks.SCREEN_SAMPLES) -> np.ndarray:
    """`k` frames spread evenly over a decoded cell, each shrunk to size x size (the cell's share of the screen-analysis mosaic)."""
    idx = np.unique(np.linspace(0, len(rgb) - 1, k).round().astype(int))
    return np.stack([cv2.resize(rgb[i], (size, size), interpolation=cv2.INTER_AREA) for i in idx])


def screen_of(thumbs: list, mp4, rows: int, cols: int, key: str, size: tuple[int, int]) -> dict | None:
    """The flatness numbers of the whole clip's key-colour screen: `thumbs` are the cells' `_thumbs` in reading order, `mp4` the clip (its first two frames are read again at full size
    for the lines between cells). Returns `effect_checks.screen_flatness` + `seam_line` as {panel_step, quarter_step, bg_std, key_diff, frames, seam_line}, or None when it cannot
    be measured (it never raises: a measurement is a help, not a gate; `seam_line` alone is None when the first frames cannot be read again)."""
    try:
        n = min(len(t) for t in thumbs)
        sz = effect_checks.SCREEN_CELL
        mosaic = np.zeros((n, rows * sz, cols * sz, 3), np.uint8)
        for i, t in enumerate(thumbs):
            r, c = divmod(i, cols)
            mosaic[:, r * sz:(r + 1) * sz, c * sz:(c + 1) * sz] = t[:n]
        out = effect_checks.screen_flatness(mosaic, rows, cols, key)
    except Exception:
        return None
    try:
        out["seam_line"] = effect_checks.seam_line(ff.decode_cell(mp4, 0, 0, size[0], size[1], 2, None), rows, cols)
    except Exception:
        out["seam_line"] = None                                  # the other numbers still stand
    return out


def cut_cells(mp4, rows: int, cols: int, cfg, asked_key: str = "green") -> list[dict]:
    """Every cell of the returned clip as keyed RGBA frames at the source size: [{index, frames, src_fps, key, screen, error?}]. The screen colour that is REALLY there wins over the one
    that was asked for (as for sheets). A cell that cannot be keyed is returned with `error` and no frames, never raised: the others are still cut.

    `screen` is the flatness of the clip's screen (the same dict on every cell, `screen_of`, measured before keying) or None."""
    info = ff.probe(mp4)
    w, h, fps = int(info["width"]), int(info["height"]), float(info.get("fps") or 24.0)
    cells, thumbs = [], []
    for i, (x, y, cw, ch) in enumerate(cell_rects(w, h, rows, cols), 1):
        try:
            rgb = ff.decode_cell(mp4, x, y, cw, ch, MAX_FRAMES, None)
            key, _ = detect_key(rgb[0], cfg.border_px, asked_key, cfg.min_key_diff)
            c = dataclasses.replace(cfg, chroma=key)
            calib = calibrate(rgb[0], key, cfg.border_px, cfg.threshold)
            th = _thumbs(rgb)
            cells.append({"index": i, "frames": np.stack([key_image(f, c, calib).rgba for f in rgb]), "src_fps": fps, "key": key})
            thumbs.append(th)
        except Exception as e:
            thumbs.append(None)
            cells.append({"index": i, "frames": None, "src_fps": fps, "key": asked_key, "error": str(e)[:200]})
    keys = [c["key"] for c in cells if c.get("frames") is not None]
    screen = screen_of(thumbs, mp4, rows, cols, max(set(keys), key=keys.count), (w, h)) if keys and all(t is not None for t in thumbs) else None
    for c in cells:
        c["screen"] = dict(screen) if screen else None
    return cells


def encode_and_check(frames: np.ndarray, cfg, *, cell_bounds=None, label: str = "effect") -> dict:
    """Encode `frames` (N,512,512,4 straight alpha) as the Telegram WebM and judge it. Returns {status, data, checks, metrics, warnings, blocks}.

    `status` is "FAILED" only when a Telegram limit is broken (`TECHNICAL`); the effect checks and every other anim check are WARNs the person can see and overrule."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "effect.webm"
        enc = particles.to_webm(frames, cfg, path)
        data = enc["bytes"]
        dec = ff.decode_alpha(path, 45, (frames.shape[2], frames.shape[1]))             # the first 1.5 s: the clip starts empty, the alpha check needs frames where something is on screen
        m = {"frames": int(len(frames)), "fps": enc["fps"], "crf": enc["crf"], "kb": round(len(data) / 1024, 1), "label": label}
        inp = {"data": data, "info": ff.probe(path), "info_native": ff.probe(path, vp9_native=True), "alpha": dec, "metrics": m, "frames_out": frames, "ref_alpha": None}
        m.update({"loop_seam": 0.0, "loop_limit": float(cfg.loop_seam_max)})            # both ends are empty: there is no seam to see
        anim = verify.run("anim", inp, cfg)
    checks = []
    for c in anim:
        if c.ok or c.id in NOT_FOR_BURSTS:
            continue
        if c.severity == verify.BLOCK and c.id in TECHNICAL:
            checks.append({"id": c.id, "verdict": "BLOCK", "detail": c.note, "value": c.value, "limit": c.limit})
        else:                                                    # a verdict about the effect itself, never final: shown, and the person decides
            checks.append({"id": c.id, "verdict": "WARN", "detail": c.note, "value": c.value, "limit": c.limit})
    eff = effect_checks.run(frames, cell_bounds=cell_bounds)
    checks += [{"id": c.id, "verdict": c.verdict, "detail": c.detail, "value": c.value, "limit": c.limit} for c in eff if c.verdict != "PASS"]
    blocks = [c["id"] for c in checks if c["verdict"] == "BLOCK"]
    return {"status": "FAILED" if blocks else "READY", "data": data, "checks": checks, "blocks": blocks,
            "warnings": [c["id"] for c in checks if c["verdict"] == "WARN"], "metrics": {**m, "coverage_peak": round(max(effect_checks.coverage_curve(frames)), 4)}}


def finish_cell(cell: dict, cfg, *, size: int | None = None) -> dict:
    """One cell of the returned clip -> one sticker: Telegram's clock, 512 square, empty ends by construction, encoded and judged."""
    if cell.get("frames") is None:
        return {"index": cell["index"], "status": "FAILED", "data": None, "blocks": ["cell_unreadable"], "warnings": [], "checks": [{"id": "cell_unreadable", "verdict": "BLOCK", "detail": cell.get("error") or "the cell could not be keyed"}], "metrics": {}}
    size = size or cfg.size
    fr = resample(cell["frames"], cell["src_fps"])
    fr = square(fr, size)
    raw_cov = effect_checks.coverage_curve(fr)
    fr, settled = settle(fr)
    r = encode_and_check(fr, cfg, label=f"cell {cell['index']}")
    r["index"] = cell["index"]
    r["metrics"].update({"key": cell.get("key"), "src_fps": round(cell["src_fps"], 2), "raw_end_coverage": round(max(raw_cov[-3:]), 4), "raw_start_coverage": round(max(raw_cov[:3]), 4), **settled})
    scr = cell.get("screen")
    if scr:                                                      # the clip's screen was measured before keying: the numbers travel with every cell, a bad screen is a WARN
        r["metrics"].update({f"screen_{k}": v for k, v in scr.items()})
        chk = effect_checks.screen_check(scr, cell.get("key") or "green")
        if not chk.ok:
            r["checks"].append({"id": chk.id, "verdict": chk.verdict, "detail": chk.detail, "value": chk.value, "limit": chk.limit})
            r["warnings"].append(chk.id)
    if settled["tail_faded"]:
        r["checks"].append({"id": "effect_tail_faded", "verdict": "WARN", "detail": "the last frames still had pieces on screen, so they were faded out to end empty like a Telegram effect", "value": r["metrics"]["raw_end_coverage"], "limit": effect_checks.EMPTY_MAX})
        r["warnings"].append("effect_tail_faded")
    return r
