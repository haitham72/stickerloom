"""G3: the video sheet. Re-composes the APPROVED stickers on a flat key-colour canvas, same slot positions as the stills,
rejected slots left empty, extra margin, no outline. Pure and deterministic (golden-hash tested): arrays in, array + layout out.

The outline is added once, after the returned video is keyed (Part A: never both), so the stickers given here must be rendered
with outline_px=0. Slot n always holds S<n>, so a 7-sticker sheet still reports in the original numbering."""
from __future__ import annotations

import cv2
import numpy as np

from . import verify

KEY_RGB = {"green": (0, 255, 0), "blue": (0, 0, 255)}


def slot_rects(canvas: int, rows: int, cols: int) -> list[tuple]:
    """Row-major (x, y, w, h); edges at i*canvas//n, so the slots tile the canvas exactly."""
    xs = [c * canvas // cols for c in range(cols + 1)]
    ys = [r * canvas // rows for r in range(rows + 1)]
    return [(xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r]) for r in range(rows) for c in range(cols)]


def _bbox(alpha: np.ndarray, floor: int = 25):
    ys, xs = np.where(alpha > floor)
    return None if not len(xs) else (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def build_video_sheet(stickers: dict, approved: list, cfg, grid: tuple = (3, 3)) -> tuple[np.ndarray, dict]:
    """stickers: {index: RGBA uint8 (no outline)}; approved: the S# that go on the sheet. -> (sheet_rgb, layout)."""
    rows, cols = grid
    approved = sorted(set(approved))
    if not approved:
        raise ValueError("no approved sticker to put on the video sheet")
    if any(i not in stickers or not 1 <= i <= rows * cols for i in approved):
        raise ValueError("an approved sticker is missing or outside the grid")
    key = np.array(KEY_RGB[cfg.chroma], np.float32)
    canvas = cfg.sheet_canvas
    rects = slot_rects(canvas, rows, cols)
    boxes = {i: _bbox(stickers[i][..., 3]) for i in approved}
    if any(b is None for b in boxes.values()):
        raise ValueError("an approved sticker has no subject")
    longest = max(max(b[2] - b[0], b[3] - b[1]) for b in boxes.values())
    slot_min = min(min(w, h) for _, _, w, h in rects)
    f = cfg.slot_fill * slot_min / longest                 # ONE scale for the whole sheet: the stickers keep their relative sizes
    sheet = np.empty((canvas, canvas, 3), np.uint8)
    sheet[:] = np.array(KEY_RGB[cfg.chroma], np.uint8)
    slots = []
    for n, (x, y, w, h) in enumerate(rects, 1):
        entry = {"slot": n, "sticker": None, "rect": [x, y, w, h], "subject_rect": None, "scale": 0.0, "subject_px": 0}
        if n in boxes:
            x0, y0, x1, y1 = boxes[n]
            crop = stickers[n][y0:y1, x0:x1].astype(np.float32)
            a = crop[..., 3:4] / 255.0
            pm = np.concatenate([crop[..., :3] * a, a], -1)            # premultiply -> resize -> composite: no key-colour fringe
            nw, nh = max(1, round((x1 - x0) * f)), max(1, round((y1 - y0) * f))
            pm = cv2.resize(pm, (nw, nh), interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
            ox, oy = x + (w - nw) // 2, y + (h - nh) // 2
            out = pm[..., :3] + (1.0 - pm[..., 3:4]) * key
            sheet[oy:oy + nh, ox:ox + nw] = np.clip(out + 0.5, 0, 255).astype(np.uint8)
            entry.update(sticker=f"S{n}", subject_rect=[ox, oy, nw, nh], scale=round(float(f), 5), subject_px=int((pm[..., 3] > 0.03).sum()))
        slots.append(entry)
    layout = {"canvas": [canvas, canvas], "key_rgb": list(KEY_RGB[cfg.chroma]), "grid": [rows, cols], "slots": slots, "verify_version": verify.VERIFY_VERSION}
    return sheet, layout
