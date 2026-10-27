"""The verifier: ONE catalogue of every check on the golden path (docs/architecture.md, "Engine").

Python judges what is *correct*; humans (and from Phase 3 the VLM) judge what is *good*. A BLOCK failure is final: the
API refuses an APPROVE on it. A WARN is shown to the human at the gate. The verifier is deterministic (same bytes in,
same verdict out), never raises (a crash is a BLOCK `verifier_error`), never looks at meaning and never repairs pixels.

Every check is one function `fn(inp, cfg) -> Check`. Thresholds live in EngineConfig only. `run(stage, inp, cfg)` runs the
stage's checks in catalogue order. Checks may record metrics in `inp["metrics"]`; `run` adds the id of every failed WARN
to `metrics["warnings"]`. Pure: numpy / OpenCV only (the ffmpeg work stays in video.py and hands its results in).
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from .chroma import border_mask, key_diff

VERIFY_VERSION = 2          # stored on every result: Phase 2 can tell which rule set judged an old sticker
BLOCK, WARN = "BLOCK", "WARN"

# Which BLOCKs a person may "use anyway". A BLOCK is either
#   TECHNICAL: Telegram itself would reject the file (format, size, codec, dimensions, fps, duration, audio, alpha) or the verifier crashed: always final, or
#   JUDGEMENT: Python's opinion of how the picture looks (a character touching its cell, a hole, a seam in the loop): a human may allow it with a recorded click that can be taken back.
# A cell with NO picture at all (nothing was cut) can never be allowed, whatever the check (flow/gates.py `still_problem`).
# Sheet-level checks have their own click ("Cut it anyway", pipeline.recut); `sheet_decodes` (the file does not open) is the only hard stop there.
OVERRIDABLE = {
    "still": ("blank_cell", "foreground", "inside_cell", "no_spill", "holes"),
    "animation": ("inside_slot", "cross_slot", "loop_seam"),
    "video_sheet": ("no_outline_on_sheet", "video_specs", "layout_match"),
}
TECHNICAL = {
    "still": ("dimensions", "transparent_corners", "static_file"),
    "animation": ("size_budget", "codec_vp9", "dimensions", "fps", "duration", "no_audio", "alpha_mode_tag", "alpha_decoded"),
}
IMPLIES = {"blank_cell": ("foreground",)}      # a cell allowed although (almost) nothing was found would otherwise fail again on the same measure of the finished sticker


@dataclass
class Check:
    id: str                      # 'inside_slot'; also the reason string and the reviews.reason value
    stage: str                   # sheet | still | video_sheet | video | anim | pack
    severity: str                # BLOCK (final) | WARN (shown to the human)
    ok: bool
    value: object = None         # the measured metric
    limit: object = None         # the threshold it was compared with (from EngineConfig)
    detail: dict = field(default_factory=dict)   # e.g. {"frame": 41, "over_px": 6}
    note: str = ""               # one human line (what the console prints next to the check)
    reason: str | None = None    # reason string when it BLOCKs and differs from the id (blank_cell -> empty_subject)

    def to_dict(self) -> dict:
        # name/ok/detail keep the shape results have always had; the rest is what the gate UI shows
        return {"name": self.id, "ok": bool(self.ok), "detail": self.note, "severity": self.severity, "stage": self.stage,
                "value": _plain(self.value), "limit": _plain(self.limit), "data": _plain(self.detail)}


def _plain(v):
    """JSON-safe copy (numpy scalars and tuples become plain values)."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


@dataclass
class Report:
    """A view over list[Check]: the first failing BLOCK is the reason; WARN failures never fail a sticker."""
    items: list = field(default_factory=list)

    @property
    def checks(self) -> list:
        return [c.to_dict() for c in self.items]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.items if c.severity == BLOCK)

    @property
    def first_failure(self):
        c = next((c for c in self.items if not c.ok and c.severity == BLOCK), None)
        return (c.reason or c.id) if c else None

    @property
    def warnings(self) -> list:
        return [c.id for c in self.items if not c.ok and c.severity == WARN]

    def get(self, check_id: str):
        return next((c for c in self.items if c.id == check_id), None)


def _c(stage, cid, severity, ok, value=None, limit=None, note="", reason=None, **detail) -> Check:
    return Check(cid, stage, severity, bool(ok), value, limit, detail, str(note), reason)


# ---------- catalogue plumbing ----------
CATALOGUE: dict[str, list] = {}        # stage -> [(id, severity, fn, gate)] in catalogue order


def check(stage: str, cid: str, severity: str, gate: bool = False):
    """Register a check. gate=True: when it fails (BLOCK) the stage stops there (later checks need what it guards)."""
    def deco(fn):
        CATALOGUE.setdefault(stage, []).append((cid, severity, fn, gate))
        return fn
    return deco


# A PARTICLE sheet (`inp["particles"]`, a batch with `kind: particles`, docs/architecture.md ("Reactions")) is not a Telegram sticker: its layout is known and its cells are the inputs of a simulation, so no
# sticker rule may stop a cell. Only what makes a cell unusable at all still blocks: a file that does not open, a sheet with
# no key screen at all (one free click, "Cut it anyway"), a cell with nothing in it. Every other failing BLOCK is reported as a WARN with its note, and the cell stays READY.
PARTICLE_KEEP = {"sheet": ("sheet_decodes", "background_is_key"), "still": ("blank_cell",)}


def waived(names) -> set:
    """The check ids a human allowed (`still_override` / `anim_override` on the sticker) plus what they imply."""
    out = set(names or ())
    for k in list(out):
        out |= set(IMPLIES.get(k, ()))
    return out


def _apply_waiver(res: Check, allowed: set, metrics: dict) -> Check:
    """One waiver rule for freshly measured and stored checks alike."""
    if not res.ok and res.severity == BLOCK and res.id in allowed and res.reason != "verifier_error":
        res = replace(res, severity=WARN, note=(res.note + " (allowed by you)").strip())
        metrics.setdefault("waived", []).append(res.id)
    elif not res.ok and res.severity == WARN:
        metrics.setdefault("warnings", []).append(res.id)
    return res


def animation_verdict(checks: list, overrides, metrics: dict) -> Report:
    """Rejudge the same finished pixels from their stored checks, without running check functions.

    Only the catalogue's animation judgement calls may change severity. Measurements,
    technical failures and verifier crashes retain their original verdict.
    """
    allowed = waived(overrides) & set(OVERRIDABLE["animation"])
    metrics.pop("waived", None)
    metrics.pop("warnings", None)
    items = []
    suffix = " (allowed by you)"
    for raw in checks:
        c = Check(raw["name"], raw["stage"], raw["severity"], raw["ok"],
                  raw.get("value"), raw.get("limit"), raw.get("data") or {}, raw.get("detail") or "")
        if "error" in c.detail:
            c.reason = "verifier_error"
        if not c.ok and c.id in OVERRIDABLE["animation"] and c.reason != "verifier_error":
            c = replace(c, severity=BLOCK, note=c.note.removesuffix(suffix))
        items.append(_apply_waiver(c, allowed, metrics))
    return Report(items)


def run(stage: str, inp: dict, cfg, only: tuple | None = None) -> list[Check]:
    """Run every check of `stage` in catalogue order (or only the ids in `only`, which also skips their gate).
    Never raises: a crashing check is a BLOCK `verifier_error`.
    `inp['waive']`: check ids a human allowed (stored on the sticker). A failing BLOCK among them is kept in the report, downgraded to a WARN with
    ' (allowed by you)' on its note, and its id goes to `metrics['waived']` (never to `warnings`). A crashed check is never waived."""
    out: list[Check] = []
    m = inp.setdefault("metrics", {})
    allowed = waived(inp.get("waive"))
    for cid, severity, fn, gate in CATALOGUE.get(stage, []):
        if only is not None and cid not in only:
            continue
        t0 = time.perf_counter()
        try:
            res = fn(inp, cfg)
        except Exception as e:                                     # pragma: no cover - defensive
            res = Check(cid, stage, BLOCK, False, None, None, {"error": str(e)[:200], "trace": traceback.format_exc()[-400:]},
                        f"verifier crashed: {e}", "verifier_error")
        if res is None:                                            # not applicable to these inputs (e.g. no layout)
            continue
        res.detail.setdefault("ms", round((time.perf_counter() - t0) * 1000, 1))
        if inp.get("particles") and not res.ok and res.severity == BLOCK and res.id not in PARTICLE_KEEP.get(stage, ()) and res.reason != "verifier_error":
            res = replace(res, severity=WARN, reason=None, note=f"{res.note} (a particle is not a sticker: a warning, not a block)".strip())
        res = _apply_waiver(res, allowed, m)
        out.append(res)
        if gate and not res.ok and res.severity == BLOCK:
            break
    inp["_checks"] = out
    return out


def report(stage: str, inp: dict, cfg) -> Report:
    return Report(run(stage, inp, cfg))


# ---------- helpers ----------
def _img(inp: dict) -> np.ndarray:
    """The rendered 512x512 sticker, rendered lazily so a blank cell never pays for it."""
    if "img" not in inp:
        inp["img"] = inp["render"]()
    return inp["img"]


# ================= sheet (on arrival, before slicing) =================
@check("sheet", "sheet_decodes", BLOCK, gate=True)
def sheet_decodes(inp, cfg):
    img = inp.get("rgb")
    if img is None and inp.get("data"):
        arr = cv2.imdecode(np.frombuffer(inp["data"], np.uint8), cv2.IMREAD_COLOR)
        img = None if arr is None else cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    ok = img is not None and img.ndim == 3 and img.shape[2] == 3 and img.size > 0
    if ok:
        inp["rgb"] = img
    return _c("sheet", "sheet_decodes", BLOCK, ok, f"{img.shape[1]}x{img.shape[0]}" if ok else None, "RGB/RGBA image",
              f"{img.shape[1]}x{img.shape[0]}" if ok else "the file does not decode")


@check("sheet", "sheet_size", BLOCK)
def sheet_size(inp, cfg):
    if inp.get("particles"):
        return None                                    # a particle sheet is not a sticker sheet: its size says nothing about its cells
    side = int(min(inp["rgb"].shape[:2]))
    return _c("sheet", "sheet_size", BLOCK, side >= cfg.min_sheet_px, side, cfg.min_sheet_px, f"shortest side {side}px")


@check("sheet", "background_is_key", BLOCK)
def background_is_key(inp, cfg):
    from .chroma import border_mask
    rgb, chroma = inp["rgb"], inp.get("chroma") or cfg.chroma
    med = float(np.median(key_diff(rgb, chroma)[border_mask(rgb.shape[0], rgb.shape[1], cfg.border_px)]))
    return _c("sheet", "background_is_key", BLOCK, med >= cfg.min_key_diff, round(med, 1), cfg.min_key_diff,
              f"outer ring is {med:.0f} {chroma}-ward; a sheet without a {chroma} screen cannot be chroma keyed (matte keyer, Phase 3C)")


@check("sheet", "grid_detected", BLOCK)
def grid_detected(inp, cfg):
    if inp.get("particles"):
        return None                                    # the layout of a particle sheet is KNOWN (rows x cols of the plan): nothing is detected, `cut_clean` cuts it equally
    from .grid import detect_grid
    want = tuple(inp.get("grid") or (3, 3))
    got = detect_grid(inp["rgb"], inp.get("chroma") or cfg.chroma, cfg.border_px)
    inp["detected_grid"] = got
    return _c("sheet", "grid_detected", BLOCK, got == want, f"{got[0]}x{got[1]}", f"{want[0]}x{want[1]}", f"found {got[0]}x{got[1]}, plan says {want[0]}x{want[1]}")


@check("sheet", "cut_clean", WARN)
def cut_clean(inp, cfg):
    from .grid import equal_rects, split_grid
    rows, cols = inp.get("grid") or (3, 3)
    if inp.get("particles"):                           # exact equal cells, no gutter detection (sparse pieces make a gutter profile lie)
        rects, info = equal_rects(inp["rgb"].shape[1], inp["rgb"].shape[0], rows, cols)
        inp["rects"], inp["split_info"] = rects, info
        return _c("sheet", "cut_clean", WARN, True, "equal", "equal", f"particle sheet: {rows}x{cols} equal cells", xs=info["xs"], ys=info["ys"])
    rects, info = split_grid(inp["rgb"], rows, cols, inp.get("chroma") or cfg.chroma, cfg.border_px)
    inp["rects"], inp["split_info"] = rects, info
    return _c("sheet", "cut_clean", WARN, info["method"] in ("gutter", "single"), info["method"], "gutter", f"cut method: {info['method']}",
              xs=info["xs"], ys=info["ys"])


@check("sheet", "background_flat", WARN)
def background_flat(inp, cfg):
    from .chroma import calibrate
    from .grid import background_profile
    rgb, chroma = inp["rgb"], inp.get("chroma") or cfg.chroma
    pc, pr = background_profile(rgb, chroma, cfg.border_px)
    _, t = calibrate(rgb, chroma, cfg.border_px)
    sel = np.zeros(rgb.shape[:2], bool)
    sel[:, pc >= 0.97] = True
    sel[pr >= 0.97, :] = True
    sel &= key_diff(rgb, chroma) > t
    ch = 1 if chroma == "green" else 2
    std = float(rgb[..., ch][sel].std()) if sel.any() else 0.0
    return _c("sheet", "background_flat", WARN, std <= cfg.max_bg_std, round(std, 2), cfg.max_bg_std, f"key-channel std-dev {std:.1f} over the gutters")


# ================= still (one cell -> one sticker) =================
@check("still", "blank_cell", BLOCK, gate=True)
def blank_cell(inp, cfg):
    c = inp["cell"]
    ok = bool(c.bbox and c.fg_px >= cfg.min_foreground_px)
    return _c("still", "blank_cell", BLOCK, ok, c.fg_px, cfg.min_foreground_px, "foreground below minimum", reason="empty_subject")


@check("still", "edge_trimmed", WARN)
def edge_trimmed(inp, cfg):
    """The appearance pass kept the subject: the re-rendered alpha still covers most of the plain alpha.
    `inp['plain']` is the alpha before trimming; absent (normal run) this is not applicable."""
    plain = inp.get("plain")
    if plain is None:
        return None
    after = int((_img(inp)[..., 3] > 127).sum())
    before = max(int((plain > 127).sum()), 1)
    share = after / before
    return _c("still", "edge_trimmed", WARN, share >= 0.90, round(share, 4), 0.90,
              f"erosion kept {share * 100:.1f}% of the subject")


@check("still", "dimensions", BLOCK)
def dimensions(inp, cfg):
    img = _img(inp)
    return _c("still", "dimensions", BLOCK, img.shape == (cfg.size, cfg.size, 4), list(img.shape), [cfg.size, cfg.size, 4], img.shape)


@check("still", "transparent_corners", BLOCK)
def transparent_corners(inp, cfg):
    a, S = _img(inp)[..., 3], cfg.size
    return _c("still", "transparent_corners", BLOCK, all(a[y, x] == 0 for y in (0, S - 1) for x in (0, S - 1)))


@check("still", "foreground", BLOCK)
def foreground(inp, cfg):
    n = int((_img(inp)[..., 3] > 127).sum())
    return _c("still", "foreground", BLOCK, n >= cfg.min_foreground_px, n, cfg.min_foreground_px)


@check("still", "inside_cell", BLOCK)
def inside_cell(inp, cfg):
    c = inp["cell"]
    return _c("still", "inside_cell", BLOCK, c.edge_px <= cfg.edge_touch_px, c.edge_px, cfg.edge_touch_px, f"{c.edge_px}px on cell border")


def _edge_analysis(inp, cfg) -> dict:
    """Key colour left ON THE EDGE (between subject and outline) versus deep inside the subject (the subject's own colours).
    Measured on the real sheets: every pixel the old whole-subject rule failed on was interior, with 0 on the edge."""
    if "_spill" not in inp:
        c, img = inp["cell"], _img(inp)
        opaque = img[..., 3] > 127
        keyish = opaque & (key_diff(img[..., :3], cfg.chroma) > c.keyed.t / 4)
        dist = cv2.distanceTransform(opaque.astype(np.uint8), cv2.DIST_L2, 3)
        edge = keyish & (dist <= cfg.outline_px + cfg.despill_band_px + 3)
        spill, inner = int(edge.sum()), int((keyish & ~edge).sum())
        n = int(opaque.sum())
        inp["_spill"] = {"spill": spill, "n": n, "risk": round(inner / max(n, 1), 4)}
        inp["metrics"]["spill_px"], inp["metrics"]["chroma_risk"] = spill, inp["_spill"]["risk"]
    return inp["_spill"]


@check("still", "no_spill", BLOCK)
def no_spill(inp, cfg):
    e = _edge_analysis(inp, cfg)
    limit = max(20, 0.001 * e["n"])
    return _c("still", "no_spill", BLOCK, e["spill"] <= limit, e["spill"], limit, f"{e['spill']}px on the edge band")


@check("still", "chroma_risk", WARN)
def chroma_risk(inp, cfg):
    e = _edge_analysis(inp, cfg)     # a warning, not a block: the human judges it at G2; Phase 3 re-keys on blue
    return _c("still", "chroma_risk", WARN, e["risk"] <= cfg.chroma_risk_warn, e["risk"], cfg.chroma_risk_warn,
              f"{e['risk'] * 100:.1f}% of the subject is key-coloured")


def _hole_share(alpha, min_px):
    m = (alpha > 127).astype(np.uint8)
    cs, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    area = int(m.sum())
    if hier is None or not area:
        return 0.0, 0
    holes = [cv2.contourArea(c) for c, h in zip(cs, hier[0]) if h[3] >= 0 and cv2.contourArea(c) >= min_px]
    return float(sum(holes)) / area, len(holes)


@check("still", "holes", WARN)
def holes(inp, cfg):
    """Transparent regions enclosed by the subject's outer contour that survive the die-cut outline: a green part of the subject
    keyed away. Measured on the finished sticker, so the gap inside a thin ring (which the outline closes) is not a hole.
    With no outline the holes stay visible and block: add a stroke or pick another cell."""
    share, n = _hole_share(_img(inp)[..., 3], cfg.min_component_px)
    inp["metrics"]["holes"] = round(share, 4)
    sev = BLOCK if share > cfg.max_hole_share else WARN
    return _c("still", "holes", sev, share <= cfg.min_hole_share, round(share, 4), cfg.max_hole_share if sev == BLOCK else cfg.min_hole_share,
              f"{n} enclosed hole(s), {share * 100:.1f}% of the subject", count=n)


@check("still", "single_subject", WARN)
def single_subject(inp, cfg):
    """More than one large part: two characters in one cell, or a neighbour bleeding in."""
    c = inp["cell"]
    m = (c.keyed.rgba[..., 3] > 127).astype(np.uint8)
    bw, bh = c.bbox[2] - c.bbox[0], c.bbox[3] - c.bbox[1]
    k = max(3, int(0.03 * max(bw, bh)))                 # parts closer than this belong to one character (the outline would join them)
    n, lab = cv2.connectedComponents(cv2.dilate(m, np.ones((2 * k + 1, 2 * k + 1), np.uint8)), connectivity=8)
    area = max(int(m.sum()), 1)
    parts = sorted((int(((lab == i) & (m > 0)).sum()) for i in range(1, n)), reverse=True)
    second = parts[1] / area if len(parts) > 1 else 0.0
    return _c("still", "single_subject", WARN, second < cfg.min_part_share, round(second, 4), cfg.min_part_share,
              f"{len(parts)} part(s); the second is {second * 100:.1f}% of the subject", parts=len(parts))


def cell_hash(rgba: np.ndarray) -> np.ndarray:
    """64-bit dHash of the keyed alpha-weighted luminance of the subject's bounding box."""
    al = rgba[..., 3]
    ys, xs = np.where(al > 25)
    if not len(xs):
        return np.zeros(64, bool)
    crop = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    lum = cv2.cvtColor(crop[..., :3], cv2.COLOR_RGB2GRAY).astype(np.float32) * (crop[..., 3] / 255.0)
    g = cv2.resize(lum, (9, 8), interpolation=cv2.INTER_AREA)
    return (g[:, 1:] > g[:, :-1]).flatten()


@check("still", "duplicate_cell", WARN)
def duplicate_cell(inp, cfg):
    """The model repeated a pose: this cell's hash is within dup_hamming of an earlier cell of the same sheet."""
    hashes, c = inp.get("hashes"), inp["cell"]
    if not hashes or c.index not in hashes:
        return None
    best, other = 64, None
    for j, h in hashes.items():
        if j < c.index and h is not None:
            d = int((hashes[c.index] != h).sum())
            if d < best:
                best, other = d, j
    return _c("still", "duplicate_cell", WARN, best > cfg.dup_hamming, best, cfg.dup_hamming,
              f"hash distance {best} to cell {other}" if other else "no earlier cell", of=other)


@check("still", "static_file", BLOCK)
def static_file(inp, cfg):
    data, fmt = inp["encode"](_img(inp), cfg)
    inp["data"], inp["fmt"] = data, fmt
    m = inp["metrics"]
    m["kb"], m["format"] = round(len(data) / 1024, 1), fmt
    return _c("still", "static_file", BLOCK, len(data) <= cfg.static_max_bytes, m["kb"], cfg.static_max_bytes // 1024, f"{m['kb']}KB {fmt}")



# ================= slot (a returned video's slot, BEFORE the encode: a block here saves the encode) =================
def _slot_edge(frames, cfg) -> int:
    return max(2, int(round(cfg.slot_edge_frac * min(frames[0].shape[:2]))))


@check("slot", "inside_slot", BLOCK)
def inside_slot(inp, cfg):
    """In any frame the subject reaches the slot border (within the edge band). Reports the first frame and the overshoot."""
    frames = inp.get("slot_frames")
    if frames is None:
        return None
    edge = _slot_edge(frames, cfg)
    h, w = frames[0].shape[:2]
    first, worst = None, 0
    for t, f in enumerate(frames):
        ys, xs = np.where(f[..., 3] > 127)
        if not len(xs):
            continue
        gap = int(min(xs.min(), w - 1 - xs.max(), ys.min(), h - 1 - ys.max()))
        if gap < edge:
            first = t if first is None else first
            worst = max(worst, edge - gap)
    return _c("slot", "inside_slot", BLOCK, first is None, worst, 0,
              f"frame {first}, {worst}px over the {edge}px edge band" if first is not None else "inside", frame=first, over_px=worst, edge_px=edge)


@check("slot", "inside_frame", WARN)
def inside_frame(inp, cfg):
    """The video twin of the still's inside_cell, for a cell cut from a prepared 3x3 video or a pre-sliced clip: in any frame, more than
    edge_touch_px opaque pixels in the border ring (border_px wide) means the character leaves its cell and the animation is cropped.
    A WARN for the engine (the video is still made so it can be looked at); the pipeline turns it into a blocked review so it cannot be added."""
    frames = inp.get("cell_frames")
    if frames is None:
        return None
    ring = border_mask(frames[0].shape[0], frames[0].shape[1], cfg.border_px)
    counts = [int((f[..., 3][ring] > 127).sum()) for f in frames]
    worst = max(counts)
    first = next((t for t, n in enumerate(counts) if n > cfg.edge_touch_px), None)
    return _c("slot", "inside_frame", WARN, worst <= cfg.edge_touch_px, worst, cfg.edge_touch_px,
              f"frame {first}: {counts[first]}px on the cell border ({sum(n > cfg.edge_touch_px for n in counts)} of {len(counts)} frames over)" if first is not None
              else f"{worst}px on the cell border at most", frame=first, frames_over=sum(n > cfg.edge_touch_px for n in counts))


@check("slot", "cross_slot", BLOCK)
def cross_slot(inp, cfg):
    """Foreground in the gutter between slots in any frame, apart from the slot's own subject: a character touching or merging."""
    frames = inp.get("slot_frames")
    if frames is None:
        return None
    h, w = frames[0].shape[:2]
    core = np.zeros((h, w), bool)
    core[h // 4:h - h // 4, w // 4:w - w // 4] = True
    first, worst = None, 0
    for t, f in enumerate(frames):
        m = (f[..., 3] > 127).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            area = int(st[i, cv2.CC_STAT_AREA])
            if area >= cfg.min_component_px and not core[lab == i].any():     # a part that never enters the slot's core
                first = t if first is None else first
                worst = max(worst, area)
    return _c("slot", "cross_slot", BLOCK, first is None, worst, 0,
              f"{worst}px of foreign foreground, first at frame {first}" if first is not None else "gutter clean", frame=first, px=worst)


# ================= anim (one encoded WEBM) =================
@check("anim", "size_budget", BLOCK)
def size_budget(inp, cfg):
    n = len(inp["data"])
    return _c("anim", "size_budget", BLOCK, n <= cfg.video_max_bytes, n // 1024, cfg.video_max_bytes // 1024, f"{n // 1024}KB @crf{inp['metrics']['crf']}")


@check("anim", "codec_vp9", BLOCK)
def codec_vp9(inp, cfg):
    return _c("anim", "codec_vp9", BLOCK, inp["info"]["codec"] == "vp9", inp["info"]["codec"], "vp9", inp["info"]["codec"])


@check("anim", "dimensions", BLOCK)
def anim_dimensions(inp, cfg):
    i = inp["info"]
    return _c("anim", "dimensions", BLOCK, (i["width"], i["height"]) == (cfg.size, cfg.size), f"{i['width']}x{i['height']}", f"{cfg.size}x{cfg.size}",
              f"{i['width']}x{i['height']}")


@check("anim", "fps", BLOCK)
def anim_fps(inp, cfg):
    f = inp["info"]["fps"]
    return _c("anim", "fps", BLOCK, 0 < f <= cfg.video_max_fps + 0.01, f, cfg.video_max_fps, f)


@check("anim", "duration", BLOCK)
def anim_duration(inp, cfg):
    d = round(inp["info"]["duration"], 3)
    return _c("anim", "duration", BLOCK, inp["info"]["duration"] <= cfg.video_max_seconds + 0.02, d, cfg.video_max_seconds, d)


@check("anim", "no_audio", BLOCK)
def no_audio(inp, cfg):
    return _c("anim", "no_audio", BLOCK, not inp["info"]["audio"])


@check("anim", "alpha_mode_tag", BLOCK)
def alpha_mode_tag(inp, cfg):
    return _c("anim", "alpha_mode_tag", BLOCK, inp["info_native"]["alpha_mode"] or inp["info"]["alpha_mode"])


@check("anim", "alpha_decoded", BLOCK)
def alpha_decoded(inp, cfg):
    al = inp["alpha"]
    ok = len(al) > 0 and al[..., 3].min() < 8 and al[..., 3].max() > 247
    return _c("anim", "alpha_decoded", BLOCK, ok)


@check("anim", "loop_seam", BLOCK)
def loop_seam_check(inp, cfg):
    m = inp["metrics"]
    return _c("anim", "loop_seam", BLOCK, m["loop_seam"] <= m["loop_limit"], m["loop_seam"], m["loop_limit"], f"{m['loop_seam']} <= {m['loop_limit']}")


def shape_iou(a: np.ndarray, b: np.ndarray, side: int = 64) -> float:
    """IoU of two binary masks after trimming each to its bbox and fitting it (aspect kept) into side x side."""
    def norm(m):
        ys, xs = np.where(m)
        if not len(xs):
            return np.zeros((side, side), bool)
        c = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8)
        s = side / max(c.shape)
        c = cv2.resize(c, (max(1, round(c.shape[1] * s)), max(1, round(c.shape[0] * s))), interpolation=cv2.INTER_AREA) > 0
        out = np.zeros((side, side), bool)
        y0, x0 = (side - c.shape[0]) // 2, (side - c.shape[1]) // 2
        out[y0:y0 + c.shape[0], x0:x0 + c.shape[1]] = c
        return out
    a, b = norm(a), norm(b)
    u = int((a | b).sum())
    return float((a & b).sum()) / u if u else 0.0


@check("anim", "identity_kept", WARN)
def identity_kept(inp, cfg):
    """Alpha IoU of the first frame vs the approved still (same normalisation): a low value means the video drifted to another pose."""
    ref, out = inp.get("ref_alpha"), inp.get("frames_out")
    if ref is None or out is None:
        return None
    if getattr(ref, "ndim", 2) == 3:                    # the approved still as RGBA (sharpness needs its colours); identity uses its alpha
        ref = ref[..., 3]
    iou = shape_iou(ref > 127, out[0][..., 3] > 127)
    return _c("anim", "identity_kept", WARN, iou >= cfg.min_identity_iou, round(iou, 3), cfg.min_identity_iou, f"first frame matches the still at IoU {iou:.2f}")


@check("anim", "motion_present", WARN)
def motion_present(inp, cfg):
    mo = inp["metrics"].get("motion")
    if mo is None:
        return None
    return _c("anim", "motion_present", WARN, mo >= cfg.min_motion, mo, cfg.min_motion, f"median frame-to-frame change {mo}")


@check("anim", "alpha_stable", WARN)
def alpha_stable(inp, cfg):
    out = inp.get("frames_out")
    if out is None or len(out) < 2:
        return None
    area = np.array([(f[..., 3] > 127).sum() for f in out], np.float64)
    cv = float(area.std() / max(area.mean(), 1.0))
    return _c("anim", "alpha_stable", WARN, cv <= cfg.max_area_cv, round(cv, 3), cfg.max_area_cv, f"subject area varies by {cv * 100:.0f}% over the frames")


@check("anim", "sharpness", WARN)
def sharpness(inp, cfg):
    """Two things can make an animation softer than it should be: the encode (`sharp_kept`: decoded edge energy over the encoder's input) and everything
    BEFORE it (`detail_vs_still`: the animation's edge energy over the approved still's, which sees a low-resolution source cell or a cheaper video
    model). The check fails on the worse of the two; the note says which and how soft it is in pixels of blur (`soft_sigma`)."""
    m = inp["metrics"]
    k, d = m.get("sharp_kept"), m.get("detail_vs_still")
    if k is None and d is None:
        return None
    if d is not None and (k is None or d < cfg.min_detail_vs_still or d <= k):
        note = f"{d:.2f}x of the still's edge detail (as soft as the still blurred {m.get('soft_sigma', 0):.1f} px)"
        return _c("anim", "sharpness", WARN, d >= cfg.min_detail_vs_still and (k is None or k >= cfg.min_sharp_kept), d, cfg.min_detail_vs_still, note)
    return _c("anim", "sharpness", WARN, k >= cfg.min_sharp_kept, k, cfg.min_sharp_kept, f"{k:.2f}x of the edge detail survives the encode")


# ================= video_sheet (G3 build) =================
@check("video_sheet", "slots_match_approved", BLOCK)
def slots_match_approved(inp, cfg):
    sheet, lay, approved = inp["sheet"], inp["layout"], sorted(inp["approved"])
    key = np.array(lay["key_rgb"], np.int16)
    filled = sorted(int(sl["sticker"][1:]) for sl in lay["slots"] if sl["sticker"])
    bad = []
    for sl in lay["slots"]:
        x, y, w, h = sl["rect"]
        non_key = int((np.abs(sheet[y:y + h, x:x + w].astype(np.int16) - key).max(-1) > 0).sum())
        if sl["sticker"] is None and non_key:
            bad.append((sl["slot"], "blank slot is not pure key colour", non_key))
        if sl["sticker"] is not None and non_key < cfg.min_foreground_px // 4:
            bad.append((sl["slot"], "filled slot has no subject", non_key))
    ok = filled == approved and not bad
    return _c("video_sheet", "slots_match_approved", BLOCK, ok, filled, approved,
              "filled slots are exactly the approved stickers; blank slots are pure key colour" if ok else f"filled {filled}, approved {approved}, problems {bad}", problems=bad)


@check("video_sheet", "no_outline_on_sheet", BLOCK)
def no_outline_on_sheet(inp, cfg):
    """A white die-cut ring around a coloured subject: the outline is added once, after the returned video."""
    sheet, lay = inp["sheet"], inp["layout"]
    key = np.array(lay["key_rgb"], np.int16)
    found = []
    for sl in lay["slots"]:
        if not sl["sticker"]:
            continue
        x, y, w, h = sl["rect"]
        crop = sheet[y:y + h, x:x + w].astype(np.int16)
        fg = (np.abs(crop - key).max(-1) > 24).astype(np.uint8)
        dist_in = cv2.distanceTransform(fg, cv2.DIST_L2, 3)        # distance from each foreground pixel to the nearest background pixel
        white = (crop.min(-1) >= 235) & ((crop.max(-1) - crop.min(-1)) <= 25)
        outer = (dist_in >= 2.5) & (dist_in <= 6.0)            # just inside the anti-aliased edge: a die-cut ring is white here, a subject is not
        # a ring is white all the way round while the subject itself is not white (a white bunny has no ring: it is white throughout)
        if outer.sum() >= 50 and white[outer].mean() >= 0.9 and white[fg > 0].mean() < 0.7:
            found.append(sl["slot"])
    return _c("video_sheet", "no_outline_on_sheet", BLOCK, not found, found, [], "no white ring around the subjects" if not found else f"white outline found in slot(s) {found}", slots=found)


# ================= video (returned, before slicing) =================
@check("video", "video_decodes", BLOCK, gate=True)
def video_decodes(inp, cfg):
    i = inp["info"]
    ok = bool(i.get("codec") and i.get("width") and inp.get("first_frame") is not None)
    return _c("video", "video_decodes", BLOCK, ok, i.get("codec"), None, f"{i.get('codec')} {i.get('width')}x{i.get('height')}" if ok else "the video does not decode")


@check("video", "video_specs", BLOCK)
def video_specs(inp, cfg):
    i = inp["info"]
    ok = i["fps"] > 0 and i["duration"] >= cfg.min_video_s
    return _c("video", "video_specs", BLOCK, ok, round(i["duration"], 2), cfg.min_video_s, f"{i['width']}x{i['height']} {i['fps']} fps, {i['duration']:.2f}s")


def _slot_mask(rgb, cfg, chroma):
    from .chroma import alpha_from_diff, calibrate
    _, t = calibrate(rgb, chroma, cfg.border_px, None)
    return alpha_from_diff(key_diff(rgb, chroma), t) > 0.5


@check("video", "layout_match", BLOCK)
def layout_match(inp, cfg):
    """First frame vs the video sheet, per filled slot: proves the video was made from THIS sheet and slot n still holds S#n."""
    lay, sheet, frame = inp["layout"], inp["sheet"], inp["first_frame"]
    W, H = lay["canvas"]
    fh, fw = frame.shape[:2]
    chroma = inp.get("chroma") or cfg.chroma
    ious = {}
    for sl in lay["slots"]:
        if not sl["sticker"]:
            continue
        x, y, w, h = sl["rect"]
        vx0, vy0, vx1, vy1 = round(x * fw / W), round(y * fh / H), round((x + w) * fw / W), round((y + h) * fh / H)
        a = _slot_mask(frame[vy0:vy1, vx0:vx1], cfg, chroma)
        b = _slot_mask(cv2.resize(sheet[y:y + h, x:x + w], (vx1 - vx0, vy1 - vy0), interpolation=cv2.INTER_AREA), cfg, chroma)
        u = int((a | b).sum())
        ious[sl["slot"]] = round(float((a & b).sum()) / u, 3) if u else 0.0
    worst = min(ious.values()) if ious else 0.0
    bad = [k for k, v in ious.items() if v < cfg.min_layout_iou]
    return _c("video", "layout_match", BLOCK, bool(ious) and not bad, worst, cfg.min_layout_iou,
              f"lowest slot IoU {worst:.2f}" + (f"; slot(s) {bad} do not match the sheet" if bad else ""), ious=ious, bad=bad)


@check("video", "blank_slots_stay_empty", BLOCK)
def blank_slots_stay_empty(inp, cfg):
    """Foreground in a slot that was blank on the sheet: the model invented a character. Blocks the generation's video, not the stickers."""
    shares = inp.get("blank_share") or {}
    bad = {k: round(v, 4) for k, v in shares.items() if v > cfg.blank_slot_max_share}
    return _c("video", "blank_slots_stay_empty", BLOCK, not bad, max(shares.values()) if shares else 0.0, cfg.blank_slot_max_share,
              "blank slots stayed empty" if not bad else f"foreground appeared in blank slot(s) {sorted(bad)}", bad=bad)


# ================= pack (G5) =================
@check("pack", "pack_limits", BLOCK)
def pack_limits(inp, cfg):
    st = inp["stickers"]
    keys = [s["key"] for s in st]
    problems = []
    if not 1 <= len(st) <= cfg.pack_max:
        problems.append(f"{len(st)} stickers (1-{cfg.pack_max})")
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        problems.append(f"duplicate keys {dup}")
    for s in st:
        if not s.get("emoji"):
            problems.append(f"{s['key']}: no emoji")
        cap = cfg.video_max_bytes if s["kind"] == "animated" else cfg.static_max_bytes
        if s["bytes"] > cap:
            problems.append(f"{s['key']}: {s['bytes'] // 1024}KB > {cap // 1024}KB")
    return _c("pack", "pack_limits", BLOCK, not problems, len(st), cfg.pack_max, "pack within Telegram limits" if not problems else "; ".join(problems), problems=problems)


# ================= telegram (Send to Telegram, checkpoint 1H): judged BEFORE any network call =================
import re as _re

_TG_NAME = _re.compile(r"^[A-Za-z](?:[A-Za-z0-9]|_(?!_))*$")


def tg_set_name_problems(name: str, bot: str | None) -> list[str]:
    """Telegram's rules for a sticker set name: 1-64 chars, letters/digits/underscore, starts with a letter, no double
    underscore, and it must end with _by_<botusername> (case-insensitive)."""
    p = []
    if not 1 <= len(name) <= 64:
        p.append("the name must be 1-64 characters")
    if not _TG_NAME.match(name or ""):
        p.append("use letters, digits and single underscores, starting with a letter")
    if bot and not name.lower().endswith(f"_by_{bot.lower()}"):
        p.append(f"the name must end with _by_{bot}")
    return p


def has_white_stroke(rgba: np.ndarray) -> bool:
    """The die-cut outline Telegram asks for on static stickers: the outermost ring of the subject is white all the way round
    while the subject itself is not white (a white subject has no ring)."""
    fg = (rgba[..., 3] > 127).astype(np.uint8)
    if not fg.any():
        return False
    dist = cv2.distanceTransform(fg, cv2.DIST_L2, 3)
    white = (rgba[..., :3].min(-1) >= 235) & ((rgba[..., :3].max(-1).astype(np.int16) - rgba[..., :3].min(-1)) <= 25)
    ring = (dist >= 2.5) & (dist <= 6.0)
    return bool(ring.sum() >= 50 and white[ring].mean() >= 0.85 and white[fg > 0].mean() < 0.7)


@check("telegram", "telegram_sticker", BLOCK)
def telegram_sticker(inp, cfg):
    """One sticker against Telegram's format table (video: WebM VP9 + alpha; static: PNG/WebP + transparency)."""
    kind, p = inp["kind"], []
    cap = cfg.video_max_bytes if kind == "video" else cfg.static_max_bytes
    w, h = inp["w"], inp["h"]
    if max(w, h) != cfg.size or min(w, h) > cfg.size:
        p.append(f"{w}x{h}: one side must be exactly {cfg.size} and neither above it")
    if inp["bytes"] > cap:
        p.append(f"{inp['bytes'] // 1024} KB is over the {cap // 1024} KB limit")
    if not inp.get("alpha"):
        p.append("no transparency")
    if kind == "video":
        i = inp["info"]
        if i.get("codec") != "vp9":
            p.append(f"codec {i.get('codec')} (VP9 is required)")
        if i.get("fps", 0) > cfg.video_max_fps + 0.01:
            p.append(f"{i['fps']} fps (max {cfg.video_max_fps:g})")
        if i.get("duration", 0) > cfg.video_max_seconds + 0.05:
            p.append(f"{i['duration']:.1f} s (max {cfg.video_max_seconds:g})")
        if i.get("audio"):
            p.append("has an audio track")
    n = len(inp.get("emoji") or [])
    if not 1 <= n <= cfg.tg_emoji_max:
        p.append(f"{n} emoji (1-{cfg.tg_emoji_max} required)")
    return _c("telegram", "telegram_sticker", BLOCK, not p, inp["bytes"], cap, "; ".join(p) if p else "meets Telegram's format", problems=p)


@check("telegram", "telegram_stroke", WARN)
def telegram_stroke(inp, cfg):
    if inp["kind"] != "static" or inp.get("stroke") is None:
        return None
    return _c("telegram", "telegram_stroke", WARN, bool(inp["stroke"]), None, None,
              "has the white stroke Telegram asks for on static stickers" if inp["stroke"] else "no white stroke: Telegram asks for one on static stickers (generate with the outline on)")


@check("telegram_set", "telegram_set", BLOCK)
def telegram_set(inp, cfg):
    """One set: 1-120 stickers of one kind, unique keys, a valid name ending _by_<bot>."""
    st, p = inp["stickers"], []
    if not 1 <= len(st) <= cfg.pack_max:
        p.append(f"{len(st)} stickers (1-{cfg.pack_max})")
    keys = [s["key"] for s in st]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        p.append(f"duplicate keys {dup}")
    p += tg_set_name_problems(inp["name"], inp.get("bot"))
    if not 1 <= len(inp.get("title", "")) <= 64:
        p.append("the title must be 1-64 characters")
    return _c("telegram", "telegram_set", BLOCK, not p, len(st), cfg.pack_max, "; ".join(p) if p else "set is valid", problems=p)
