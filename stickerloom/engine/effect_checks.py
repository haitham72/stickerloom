"""Checks for a particle-effect clip (`engine/particles.py`): is it empty at both ends, does it show a burst, does it stay in its cell, does it move; and, for a video drawn from
text, is the key-colour screen behind it ONE flat colour (`screen_flatness`, `seam_line`, `screen_check`: the id `key_is_seamless`).

The verdict is ONLY "PASS" or "WARN", never "BLOCK". This is the owner's standing rule: Python must not hard-block a result he can look at;
he must always be able to use it anyway. A WARN is a line shown next to the preview, not a gate.

What is NOT here: the technical limits of a Telegram video sticker (WEBM/VP9 + alpha, 512x512, 30 fps, 3 s, 256 KB). The existing verifier
(`engine/verify.py`) enforces them on the encoded file; these checks only judge the pixels of the effect itself.

Pure numpy; deterministic; no I/O. Every threshold is a module constant so the UI and the tests read the same numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ALPHA_ON = 12            # alpha at or above this counts as "something is drawn" (below it is VP9 haze: the engine's clip_alpha_floor)
EDGE_FRAMES = 3          # the first and the last 3 frames must be empty
EMPTY_MAX = 0.003        # empty = at most 0.3% of the canvas covered
BURST_MIN = 0.015        # the peak covers at least 1.5% of the canvas ...
BURST_FRAMES = 20        # ... and something is visible (coverage above EMPTY_MAX) in at least 20 frames
OUTSIDE_MAX = 0.002      # at most 0.2% of a frame's opaque pixels may lie outside the cell
MOTION_COVERAGE = 0.0005 # motion: the coverage changes by at least 0.05% of the canvas per frame on average (between frames that show something) ...
MOTION_PATH = 0.01       # ... or the alpha centroid travels at least 1% of the canvas side in total
PASS, WARN = "PASS", "WARN"

# ---- the screen of a video from text (measured on the returned clip BEFORE keying; every number is in 0..255 colour levels) ----
# Calibrated 2026-10-03 on the three real Kling clips (docs/architecture.md ("Reactions")), numbers as (panel_step, quarter_step, bg_std, seam_line):
#   J037 2x2, rated great:                       (2.8, 3.0, 1.6, 27.2)     J038 3x3, poor, patches of darker blue: (4.2, 5.8, 8.5, 0.8)
#   J039 2x2 from the app, drew walls and panels: (20.0, 9.0, 11.0, 2.0)
# A limit sits well above the clean clip and below the clip that drew walls; the screen text in the console reads `key_is_seamless`.
SCREEN_CELL = 96           # the analysis mosaic holds every cell at 96 x 96 px ...
SCREEN_BLOCK = 12          # ... cut into blocks of 12 px (8 x 8 blocks per cell)
SCREEN_SAMPLES = 16        # frames spread evenly over the clip
SCREEN_KEY_RATIO = 0.6     # a pixel is screen when its key difference (the keyer's own measure) is at least 60% of the frame's median
SCREEN_BG_SHARE = 0.9      # a block is read as screen when at least 90% of its pixels are screen (a piece and its fringe are left out)
SCREEN_MIN_KEY = 20.0      # median key difference below this: there is no key-colour screen to measure
SCREEN_PANEL_MAX = 10.0    # the outer ring of every cell differs from its inside by more than this: walls, a box, a vignette
SCREEN_QUARTER_MAX = 10.0  # the cells' own screen colours differ from each other by more than this: panels of different shades (no real clip is above it yet)
SCREEN_STD_MAX = 6.0       # the screen colour varies by more than this from block to block: a pattern, a texture, patches of shade
SCREEN_LINE_MAX = 40.0     # a straight line on a cell boundary differs from the screen by more than this (a faint 27-level line was in the clip rated great, and keys cleanly)


@dataclass(frozen=True)
class Check:
    id: str                       # effect_empty_start ...
    verdict: str                  # "PASS" or "WARN", never "BLOCK"
    detail: str                   # one human line
    value: object = None          # the measured number
    limit: object = None          # what it was compared with
    data: dict = field(default_factory=dict)     # e.g. {"frame": 41}

    @property
    def ok(self) -> bool:
        return self.verdict == PASS

    def to_dict(self) -> dict:
        return {"id": self.id, "verdict": self.verdict, "detail": self.detail, "value": self.value, "limit": self.limit, "data": dict(self.data)}


def _alpha(frames) -> np.ndarray:
    if not isinstance(frames, np.ndarray) or frames.ndim != 4 or frames.shape[3] != 4 or len(frames) == 0:
        raise ValueError("frames must be a non-empty array of shape (N, H, W, 4)")
    return frames[..., 3]


def coverage_curve(frames: np.ndarray, alpha_min: int = ALPHA_ON) -> list:
    """Per frame, the share of the canvas whose alpha is at least `alpha_min` (0..1)."""
    a = _alpha(frames)
    return [float(x) for x in (a >= alpha_min).mean(axis=(1, 2))]


def _p90(values) -> float | None:
    v = [x for x in values if x is not None and not np.isnan(x)]
    return float(np.percentile(v, 90)) if v else None


def _key_diff(rgb: np.ndarray, key: str) -> np.ndarray:
    a = rgb.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (g - np.maximum(r, b)) if key == "green" else (b - np.maximum(r, g))


def screen_flatness(mosaic: np.ndarray, rows: int, cols: int, key: str = "green", cell: int = SCREEN_CELL, block: int = SCREEN_BLOCK) -> dict:
    """How flat the key-colour screen is, on sampled frames of the clip BEFORE keying (particle pixels are left out, so only the screen is measured).

    `mosaic` is (N, rows*cell, cols*cell, 3) uint8: N frames, every cell of the clip resized to `cell` px and put back in place. Per frame the screen pixels are the ones whose key
    difference is at least 60% of the frame's median (the keyer's own scale); they are averaged per block of `block` px, and only blocks that are nearly all screen are read:
      panel_step    the outer ring of every cell against its inside (walls, a box, a vignette): the largest channel difference of the two medians
      quarter_step  the largest difference between the screen colours of two cells (panels of different shades)
      bg_std        the standard deviation of the screen colour over the blocks that have no particle next to them (a pattern, a texture), largest channel
    Each is the 90th percentile over the frames, so one odd frame does not decide and a wall that shows for a third of the clip does. A number is None when it cannot be measured.
    Returns {"panel_step", "quarter_step", "bg_std", "key_diff", "frames"}."""
    if mosaic.ndim != 4 or mosaic.shape[3] != 3 or mosaic.shape[1] != rows * cell or mosaic.shape[2] != cols * cell or cell % block:
        raise ValueError("mosaic must be (N, rows*cell, cols*cell, 3) with the cell a multiple of the block")
    per = cell // block
    nby, nbx = rows * per, cols * per
    iy, ix = np.divmod(np.arange(nby), per)[1][:, None], np.divmod(np.arange(nbx), per)[1][None, :]
    dist = np.minimum(np.minimum(iy, per - 1 - iy), np.minimum(ix, per - 1 - ix))
    ring, inner = dist == 0, dist >= 2
    panel, quarter, std, kd_med = [], [], [], []
    for fr in mosaic:
        kd = _key_diff(fr, key)
        med = float(np.median(kd))
        if med < SCREEN_MIN_KEY:
            continue
        kd_med.append(med)
        m = (kd >= SCREEN_KEY_RATIO * med).astype(np.float32).reshape(nby, block, nbx, block)
        f = fr.astype(np.float32).reshape(nby, block, nbx, block, 3)
        share = m.mean(axis=(1, 3))
        colour = (f * m[..., None]).sum(axis=(1, 3)) / np.maximum(m.sum(axis=(1, 3)), 1.0)[..., None]
        valid = share >= SCREEN_BG_SHARE
        a, b = valid & ring, valid & inner
        panel.append(float(np.abs(np.median(colour[a], axis=0) - np.median(colour[b], axis=0)).max()) if a.sum() >= 6 and b.sum() >= 6 else None)
        meds = []
        for r in range(rows):
            for c in range(cols):
                v = valid[r * per:(r + 1) * per, c * per:(c + 1) * per]
                if v.sum() >= 6:
                    meds.append(np.median(colour[r * per:(r + 1) * per, c * per:(c + 1) * per][v], axis=0))
        quarter.append(float(max(np.abs(x - y).max() for x in meds for y in meds)) if len(meds) >= 2 else None)
        pad = np.pad(valid, 1)
        core = np.logical_and.reduce([pad[dy:dy + nby, dx:dx + nbx] for dy in range(3) for dx in range(3)])      # blocks whose eight neighbours are screen too: no particle fringe
        std.append(float(colour[core].std(axis=0).max()) if core.sum() >= 10 else None)
    out = {"panel_step": _p90(panel), "quarter_step": _p90(quarter), "bg_std": _p90(std)}
    return {**{k: (round(v, 1) if v is not None else None) for k, v in out.items()}, "key_diff": round(float(np.median(kd_med)), 1) if kd_med else None, "frames": len(kd_med)}


def seam_line(frames: np.ndarray, rows: int, cols: int) -> float:
    """The strongest straight line drawn on a boundary between two cells, from the first frames of the clip (where the screen is empty): `frames` is (k, H, W, 3) at full size.
    The colour of every column (row) is the median down the column (along the row); at each boundary the profile within a few pixels of it is compared with the profile a little
    further away on both sides. Returns the largest channel difference in colour levels (0.0 when the clip is one cell)."""
    f = np.asarray(frames, np.float32).mean(axis=0)
    worst = 0.0
    for prof, n in ((np.median(f, axis=0), cols), (np.median(f, axis=1), rows)):
        size = len(prof)
        near = max(3, size // 120)
        for s in range(1, n):
            pos = s * (size // n)
            ref = [prof[max(0, pos - 5 * near):max(0, pos - 2 * near)], prof[pos + 2 * near:pos + 5 * near]]
            ref = np.concatenate([x for x in ref if len(x)])
            if not len(ref):
                continue
            worst = max(worst, float(np.abs(prof[max(0, pos - near):pos + near] - np.median(ref, axis=0)).max()))
    return round(worst, 1)


def screen_check(m: dict, key: str = "green") -> Check:
    """`key_is_seamless`: PASS, or a WARN (never a block: the person can use the clip anyway) naming the worst of the measured faults of `screen_flatness` and `seam_line`."""
    tests = [("panel_step", SCREEN_PANEL_MAX, "the edges of each area differ from its middle by {v:.1f} levels (limit {l:.0f})"),
             ("quarter_step", SCREEN_QUARTER_MAX, "the areas of the screen differ in colour by {v:.1f} levels (limit {l:.0f})"),
             ("bg_std", SCREEN_STD_MAX, "the screen colour varies by {v:.1f} levels from place to place (limit {l:.0f})"),
             ("seam_line", SCREEN_LINE_MAX, "a straight line of {v:.1f} levels runs between the areas (limit {l:.0f})")]
    bad = [(m[k] / lim, k, lim, text) for k, lim, text in tests if m.get(k) is not None and m[k] > lim]
    if not bad:
        return Check("key_is_seamless", PASS, f"the {key} screen is one flat colour", m.get("panel_step"), SCREEN_PANEL_MAX)
    _, k, lim, text = max(bad)
    return Check("key_is_seamless", WARN, f"the {key} screen has panels or patterns: {text.format(v=m[k], l=lim)}; keying may eat the pieces. Use it anyway or make another take", m[k], lim, {"metric": k})


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _edge(cov: list, frames_idx: list, check_id: str, where: str) -> Check:
    worst = max(frames_idx, key=lambda i: cov[i])
    v = cov[worst]
    if v <= EMPTY_MAX:
        return Check(check_id, PASS, f"the {where} {EDGE_FRAMES} frames are empty (at most {_pct(v)} covered)", v, EMPTY_MAX)
    return Check(check_id, WARN, f"the {where} {EDGE_FRAMES} frames are not empty: frame {worst} covers {_pct(v)} (limit {_pct(EMPTY_MAX)}); "
                 "the effect should start from nothing and end with nothing", v, EMPTY_MAX, {"frame": worst})


def run(frames: np.ndarray, *, cell_bounds: tuple | None = None) -> list:
    """The effect checks on RGBA frames `(N, H, W, 4)`, as a list of `Check` (verdict PASS or WARN only):
      effect_empty_start   the first 3 frames cover <= 0.3% of the canvas
      effect_empty_end     the last 3 frames cover <= 0.3% of the canvas
      effect_has_burst     the peak covers >= 1.5% of the canvas and something is visible in >= 20 frames ("visible" = above the 0.3% empty line)
      effect_inside_cell   only when `cell_bounds=(x0, y0, x1, y1)` (pixels, x1/y1 exclusive) is given: at most 0.2% of any frame's opaque pixels outside it
      effect_not_a_still   the alpha coverage or the alpha centroid changes from frame to frame (motion is present)
    Malformed frames raise ValueError (a caller bug); everything about the clip itself is a verdict."""
    a = _alpha(frames)
    n, h, w = a.shape
    on = a >= ALPHA_ON
    count = on.sum(axis=(1, 2))
    cov = (count / float(h * w)).tolist()
    edge = min(EDGE_FRAMES, n)
    out = [_edge(cov, list(range(edge)), "effect_empty_start", "first"),
           _edge(cov, list(range(n - edge, n)), "effect_empty_end", "last")]

    peak = max(cov)
    visible = sum(1 for c in cov if c > EMPTY_MAX)
    if peak >= BURST_MIN and visible >= BURST_FRAMES:
        out.append(Check("effect_has_burst", PASS, f"peak {_pct(peak)} of the canvas, visible in {visible} frames", peak, BURST_MIN,
                         {"visible_frames": visible, "peak_frame": int(np.argmax(cov))}))
    else:
        why = f"the peak covers only {_pct(peak)} (needs {_pct(BURST_MIN)})" if peak < BURST_MIN else f"it is visible in only {visible} frames (needs {BURST_FRAMES})"
        out.append(Check("effect_has_burst", WARN, f"no clear burst: {why}", peak, BURST_MIN,
                         {"visible_frames": visible, "peak_frame": int(np.argmax(cov))}))

    if cell_bounds is not None:
        x0, y0, x1, y1 = (int(v) for v in cell_bounds)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("cell_bounds must be (x0, y0, x1, y1) with x1 > x0 and y1 > y0, inside the canvas")
        inside = on[:, y0:y1, x0:x1].sum(axis=(1, 2))
        frac = np.where(count > 0, (count - inside) / np.maximum(count, 1), 0.0)
        worst = int(np.argmax(frac))
        v = float(frac[worst])
        if v <= OUTSIDE_MAX:
            out.append(Check("effect_inside_cell", PASS, f"inside the cell in every frame (worst {_pct(v)} outside)", v, OUTSIDE_MAX, {"frame": worst}))
        else:
            out.append(Check("effect_inside_cell", WARN, f"frame {worst} has {_pct(v)} of its opaque pixels outside the cell (limit {_pct(OUTSIDE_MAX)})",
                             v, OUTSIDE_MAX, {"frame": worst, "outside_px": int(count[worst] - inside[worst])}))

    # Motion is judged between neighbouring frames that both show something: an effect appearing from nothing is not motion, a still is.
    held = np.flatnonzero((count[:-1] > 0) & (count[1:] > 0))
    change = float(np.mean([abs(cov[i + 1] - cov[i]) for i in held])) if len(held) else 0.0
    path = 0.0
    if len(held):
        gx, gy = np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64)

        def centre(i):
            wgt = np.where(on[i], a[i], 0)
            col, row = wgt.sum(axis=0, dtype=np.float64), wgt.sum(axis=1, dtype=np.float64)
            tot = col.sum()
            return (col * gx).sum() / tot / w, (row * gy).sum() / tot / h
        cache = {}
        for i in held:
            for j in (i, i + 1):
                if j not in cache:
                    cache[j] = centre(j)
            path += float(np.hypot(cache[i + 1][0] - cache[i][0], cache[i + 1][1] - cache[i][1]))
    msg = f"coverage changes {_pct(change)} per frame, the centroid travels {path * 100:.1f}% of the canvas"
    data = {"coverage_change": change, "centroid_path": path}
    if change >= MOTION_COVERAGE or path >= MOTION_PATH:
        out.append(Check("effect_not_a_still", PASS, "it moves: " + msg, change, MOTION_COVERAGE, data))
    else:
        out.append(Check("effect_not_a_still", WARN, "it looks like a still: " + msg, change, MOTION_COVERAGE, data))
    return out
