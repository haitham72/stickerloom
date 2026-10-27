from __future__ import annotations

import os

from dataclasses import dataclass, asdict, field


def default_workers() -> int:
    """Cells animated at the same time. STICKERLOOM_ANIM_WORKERS (or `serve --workers N`) overrides; lower it if the PC hits a limit (RAM, CPU, disk)."""
    try:
        n = int(os.environ.get("STICKERLOOM_ANIM_WORKERS", ""))
    except ValueError:
        n = 0
    return max(1, n) if n else max(1, min(6, (os.cpu_count() or 4) // 2))


@dataclass(frozen=True)
class EngineConfig:
    size: int = 512
    fit: float = 0.775            # target: longest side / size
    max_fit: float = 0.85         # never exceed this
    outline_px: int = 0           # 0 = no white die-cut outline (the default: a stroke is opt-in per batch)
    erode_px: int = 0             # 0 disables: trims N px of key fringe before the outline (always, outline on or off)
    chroma: str = "green"         # "green" | "blue"
    threshold: int | None = None  # None = auto-calibrate from the border ring
    border_px: int = 4
    min_component_px: int = 64
    min_foreground_px: int = 400
    edge_touch_px: int = 8
    despill_band_px: int = 3
    chroma_risk_warn: float = 0.03  # share of subject pixels near the key colour -> warning (Phase 3: blue re-key)
    static_max_bytes: int = 512 * 1024
    video_max_fps: float = 30.0
    video_max_seconds: float = 3.0
    video_max_bytes: int = 256 * 1024
    crf_ladder: tuple = (30, 34, 38, 42, 46, 50, 54, 58, 62)       # finer rungs: the fit lands closer to the 256 KB budget (measured 2026-10-01: crf 46 left 8-25% unused)
    anim_workers: int = field(default_factory=default_workers)     # cells animated at the same time (each is keyed, rendered and encoded independently)
    loop_seam_max: float = 12.0     # absolute floor
    loop_seam_ratio: float = 1.0    # seam may be up to this x the clip's own frame-to-frame motion (1.5 let a lively clip snap at the wrap: G146 S1-S4)
    loop_fade_frames: int = 6
    anim_scale: float = 1.0        # the animation's size after it is received, on top of keep/fit: 0.9 = 10% smaller, 1.1 = 10% larger (the batch's Video size, or one sticker's)
    anim_keep_size: bool = True    # an animation keeps its APPROVED still's size and place (what leaves the 512 frame is cut off); False = shrink the clip until its whole motion fits (the old way)
    anim_hotspot: bool = False     # Resize on: size and place each animation by its HOT SPOT, the pixels opaque in >= hot_share of the frames, matched to the approved still; wins over keep/fit
    hot_share: float = 0.5         # the share of frames a pixel must be opaque in to be part of the hot spot (tear drops flying out are in a few frames only)
    hot_fill: float = 0.8          # the hot spot's longest side over the canvas when there is no approved still to match
    anim_offset: tuple = (0.0, 0.0)    # the sticker's own Position: (dx, dy) as a share of the canvas, + = right / down
    anim_same_scale: bool = False  # wins over hotspot/keep/fit: every cell of the video at ONE scale (the largest cell side -> the canvas), so the stickers keep the sheet's relative sizes
    clip_prefer: tuple = ("mov", "webm")   # pre-sliced clip formats, best first (ProRes 4444 has the cleanest alpha)
    clip_alpha_floor: int = 12               # alpha below this is haze (VP9 alpha leaves values 1-3) -> 0

    # ---- verifier thresholds (verify.py reads them from here only; measured on the 10 real sheets, 2026-10-01) ----
    min_sheet_px: int = 1024        # production sheets are 2K; the real samples are 2048
    min_key_diff: float = 60.0      # median key difference on the sheet's outer ring (real: 135-209; synthetic: ~205)
    max_bg_std: float = 12.0        # key-channel std-dev over the gutters (real: 1.2-2.3; the noisy synthetic: ~8)
    min_hole_share: float = 0.01    # holes: enclosed transparent area / subject area, WARN above this ...
    max_hole_share: float = 0.30    # ... BLOCK above this (real: at most 0.074)
    min_part_share: float = 0.12    # single_subject: a second component this large (share of the subject) is a second character
    dup_hamming: int = 4            # duplicate_cell: dHash distance at or below this = the model repeated a pose (real minimum: 5)
    min_video_s: float = 1.0        # a returned video shorter than this is not usable
    min_layout_iou: float = 0.6     # layout_match: first video frame vs the video sheet, per slot
    blank_slot_max_share: float = 0.002   # blank_slots_stay_empty: foreground share of a slot that was blank
    slot_edge_frac: float = 0.015   # inside_slot: edge band = this x the slot's short side (min 2 px)
    min_identity_iou: float = 0.5   # identity_kept: shape IoU of video frame 0 vs the approved still
    min_motion: float = 0.5         # motion_present: median frame-to-frame change
    min_sharp_kept: float = 0.8     # sharpness: share of the edge detail that survives the encode (real: 1.00-1.03 at the budget)
    min_detail_vs_still: float = 0.75   # sharpness: the animation's edge detail over its still's (real 2026-10-02: sharp batches 0.89 and up, the soft 320 px / Kling std batch 0.58-0.72)
    max_area_cv: float = 0.35       # alpha_stable: coefficient of variation of the subject area over the frames
    slot_fill: float = 0.74         # video sheet: the largest subject's longest side is at most this share of its slot (>= 13% margin). 0.55 -> 0.66 -> 0.74: each step took a quarter off the gap between two stickers
    sheet_canvas: int = 2048        # video sheet canvas (square)
    pack_max: int = 120             # Telegram sticker pack limit
    tg_emoji_max: int = 20          # Telegram: 1-20 emoji per sticker

    def to_dict(self) -> dict:
        return asdict(self)
