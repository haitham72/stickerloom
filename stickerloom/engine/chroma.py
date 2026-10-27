"""Colour-difference chroma key. Shared by the still (sheet) and video paths."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Keyed:
    rgba: np.ndarray   # uint8 HxWx4 (despilled colour, keyed alpha)
    bg: list           # sampled background RGB
    t: float           # threshold actually used


def key_diff(rgb: np.ndarray, chroma: str = "green") -> np.ndarray:
    a = rgb.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (g - np.maximum(r, b)) if chroma == "green" else (b - np.maximum(r, g))


def border_mask(h: int, w: int, px: int) -> np.ndarray:
    m = np.zeros((h, w), bool)
    m[:px] = True; m[-px:] = True; m[:, :px] = True; m[:, -px:] = True
    return m


def detect_key(rgb: np.ndarray, border_px: int = 4, asked: str = "green", min_diff: float = 60.0) -> tuple[str, dict]:
    """Which screen the sheet really has, judged on its outer ring: the colour whose key difference is highest. Image tools drift (a blue screen when green was asked,
    or the other way round), and the plan may have asked for blue on purpose (a green subject). When neither colour is clear, `asked` is returned so the sheet check
    still blocks it with the colour that was expected. Returns (colour, {green: score, blue: score})."""
    m = border_mask(rgb.shape[0], rgb.shape[1], border_px)
    score = {ch: round(float(np.median(key_diff(rgb, ch)[m])), 1) for ch in ("green", "blue")}
    best = max(score, key=score.get)
    return (best if score[best] >= min_diff else asked), score


def calibrate(rgb: np.ndarray, chroma: str, border_px: int = 4, override: float | None = None, validate: bool = False):
    """Sample the REAL background from the outer ring (AI tools never give a true #00FF00).
    validate=True: raises ValueError if the key difference is too low (wrong chroma key used)."""
    m = border_mask(rgb.shape[0], rgb.shape[1], border_px)
    bg = np.median(rgb[m], axis=0)
    med = float(np.median(key_diff(rgb, chroma)[m]))
    if validate and med < 30.0:
        raise ValueError(f"Background key difference too low ({med:.1f}) for chroma={chroma}: likely wrong screen colour detected")
    t = float(override) if override else max(0.5 * med, 8.0)
    return [int(v) for v in bg], t


def alpha_from_diff(d: np.ndarray, t: float) -> np.ndarray:
    # d >= t transparent, d <= t/2 opaque, linear between.
    return np.clip(2.0 * (t - d) / t, 0.0, 1.0).astype(np.float32)


def remove_specks(alpha: np.ndarray, min_px: int) -> np.ndarray:
    """Drop connected components below min_px. No erosion: it eats thin outlines and lashes."""
    mask = (alpha > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_px
    kept = keep[lab].astype(np.uint8)
    kept = cv2.dilate(kept, np.ones((5, 5), np.uint8)) > 0   # keep anti-aliased edge pixels
    return np.where(kept, alpha, 0.0).astype(np.float32)


def despill(rgb: np.ndarray, alpha: np.ndarray, chroma: str, band: int) -> np.ndarray:
    """Clamp the key channel to max(other two) in the edge band ONLY (global despill turns yellow orange)."""
    trans = (alpha < 0.5).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    edge = ((cv2.dilate(trans, k) > 0) | (alpha < 0.98)) & (alpha > 0)
    out = rgb.copy()
    ch = 1 if chroma == "green" else 2
    others = [c for c in (0, 1, 2) if c != ch]
    cap = np.maximum(out[..., others[0]], out[..., others[1]])
    out[..., ch] = np.where(edge, np.minimum(out[..., ch], cap), out[..., ch])
    return out


def key_image(rgb: np.ndarray, cfg, calib=None) -> Keyed:
    bg, t = calib or calibrate(rgb, cfg.chroma, cfg.border_px, cfg.threshold)
    alpha = alpha_from_diff(key_diff(rgb, cfg.chroma), t)
    alpha = remove_specks(alpha, cfg.min_component_px)
    clean = despill(rgb, alpha, cfg.chroma, cfg.despill_band_px)
    rgba = np.dstack([clean, np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)])
    return Keyed(rgba, bg, t)
