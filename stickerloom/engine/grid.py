"""Grid geometry: find where to cut a sticker sheet. Pure (numpy only).

Real AI sheets do not put their gutters on exact fractions (measured up to ~90 px off on 2K sheets), so cutting at
w/3 slices through characters. split_grid() cuts in the middle of the background band nearest each expected line;
when there is no clear band there, that cut falls back to equal division."""
from __future__ import annotations

import numpy as np

from .chroma import calibrate, key_diff

GUTTER_MIN = 0.97     # a column/row counts as gutter when at least this share of its pixels is background


def background_profile(rgb: np.ndarray, chroma: str, border_px: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Share of background pixels per column and per row (threshold calibrated from the sheet's outer ring)."""
    _, t = calibrate(rgb, chroma, border_px)
    bg = key_diff(rgb, chroma) > t
    return bg.mean(axis=0), bg.mean(axis=1)


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each True run."""
    f = np.concatenate([[False], flags, [False]]).astype(np.int8)
    d = np.diff(f)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _cuts(profile: np.ndarray, n: int) -> tuple[list[int], list[str]]:
    size = len(profile)
    edges, how = [0], []
    for k in range(1, n):
        expected = k * size / n
        lo, hi = int(expected - size / (2 * n)), int(expected + size / (2 * n))
        window = profile[lo:hi]
        level = float(window.max()) if len(window) else 0.0
        if level < GUTTER_MIN:
            edges.append(int(round(expected))); how.append("equal")
            continue
        runs = [(lo + a, lo + b) for a, b in _runs(window >= level - 0.005)]
        a, b = max(runs, key=lambda r: (r[1] - r[0], -abs((r[0] + r[1]) / 2 - expected)))
        edges.append(int((a + b) // 2)); how.append("gutter")
    edges.append(size)
    return edges, how


def split_grid(rgb: np.ndarray, rows: int, cols: int, chroma: str = "green", border_px: int = 4) -> tuple[list[tuple], dict]:
    """Row-major cell rects (x, y, w, h) plus info {xs, ys, method}. method: gutter | equal | mixed."""
    pc, pr = background_profile(rgb, chroma, border_px)
    xs, hx = _cuts(pc, cols)
    ys, hy = _cuts(pr, rows)
    rects = [(xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r]) for r in range(rows) for c in range(cols)]
    how = set(hx + hy)
    method = "equal" if how <= {"equal"} and how else "gutter" if how <= {"gutter"} and how else "mixed" if how else "single"
    return rects, {"xs": xs, "ys": ys, "method": method}


def equal_rects(width: int, height: int, rows: int, cols: int) -> tuple[list[tuple], dict]:
    """The EXACT equal rows x cols division of a width x height sheet (cell sizes differ by at most one pixel), for a sheet whose layout is KNOWN and whose
    cells are inputs to something else, not Telegram stickers (a particle sheet: a few small pieces on a screen, so no gutter profile means anything). Same shape as `split_grid`."""
    xs = [int(round(k * width / cols)) for k in range(cols + 1)]
    ys = [int(round(k * height / rows)) for k in range(rows + 1)]
    rects = [(xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r]) for r in range(rows) for c in range(cols)]
    return rects, {"xs": xs, "ys": ys, "method": "equal"}


def detect_grid(rgb: np.ndarray, chroma: str = "green", border_px: int = 4, max_n: int = 4) -> tuple[int, int]:
    """(rows, cols) from the number of interior gutter bands (at least 1% of the side wide). 1..max_n each."""
    pc, pr = background_profile(rgb, chroma, border_px)

    def count(p):
        size = len(p)
        bands = [(a, b) for a, b in _runs(p >= GUTTER_MIN) if b - a >= 0.01 * size and a > 0 and b < size]
        return max(1, min(max_n, len(bands) + 1))
    return count(pr), count(pc)


def scale_rects(rects: list, src_wh: tuple, dst_wh: tuple) -> list[tuple]:
    """Map cell rects measured on the sheet onto a video made from that sheet (image-to-video keeps the layout)."""
    sx, sy = dst_wh[0] / src_wh[0], dst_wh[1] / src_wh[1]
    out = []
    for x, y, w, h in rects:
        x0, y0 = int(round(x * sx)), int(round(y * sy))
        x1, y1 = int(round((x + w) * sx)), int(round((y + h) * sy))
        out.append((x0, y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2))   # even sizes for ffmpeg crop
    return out
