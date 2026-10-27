"""Learned background matte (optional provider). U2-Net / IS-Net through onnxruntime and nothing else (no rembg, torch or network).
Photos and video frames that are not a green/blue screen use it; without it the app falls back to OpenCV GrabCut.
Model file (Apache-2.0 U2-Net): $STICKERLOOM_MATTE_MODEL, else the first of stickerloom/models/*.onnx, else ~/.u2net/*.onnx.
Preference order: isnet-general-use (best), u2net, u2net_human_seg, u2netp (4.6MB, fastest)."""
from __future__ import annotations

import functools
import os
import threading
from pathlib import Path

import cv2
import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
PREFER = ("isnet-general-use", "u2net", "u2net_human_seg", "silueta", "u2netp")   # photos: best first
FAST = ("u2netp", "silueta", "u2net_human_seg", "u2net", "isnet-general-use")        # video frames: fastest first
_lock = threading.RLock()      # session creation and inference are both single-threaded (concurrent init crashes onnxruntime)


def _find(fast: bool = False):
    if os.environ.get("STICKERLOOM_MATTE_MODEL"):
        p = Path(os.environ["STICKERLOOM_MATTE_MODEL"])
        return p if p.is_file() else None
    for d in (MODEL_DIR, Path.home() / ".u2net"):
        for name in (FAST if fast else PREFER):
            if (d / f"{name}.onnx").is_file():
                return d / f"{name}.onnx"
    return None


@functools.lru_cache(maxsize=2)
def _make(path: str):
    import onnxruntime as ort
    so = ort.SessionOptions(); so.log_severity_level = 3
    return ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])


def _session(fast: bool = False):
    path = _find(fast)
    if not path:
        raise FileNotFoundError("no matte model")
    with _lock:
        return _make(str(path)), path


def status(fast: bool = False) -> dict:
    """{'ok': bool, 'model': name|None, 'reason': str}. Cheap after the first call."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return {"ok": False, "model": None, "reason": "onnxruntime is not installed (pip install onnxruntime)"}
    if not _find(fast):
        return {"ok": False, "model": None, "reason": "no matte model found (put u2netp.onnx in stickerloom/models/, or set STICKERLOOM_MATTE_MODEL)"}
    try:
        return {"ok": True, "model": _session(fast)[1].stem, "reason": ""}
    except Exception as e:
        return {"ok": False, "model": None, "reason": f"matte model failed to load: {e}"}


def alpha(rgb: np.ndarray, fast: bool = False) -> np.ndarray:
    """uint8 HxWx3 RGB -> float32 HxW alpha in [0,1] (soft, full resolution). fast=True picks the smallest model (video frames)."""
    with _lock:
        sess, path = _session(fast)
        return _alpha(sess, path, rgb)


def _alpha(sess, path, rgb):
    shape = sess.get_inputs()[0].shape
    size = shape[2] if isinstance(shape[2], int) else 320
    isnet = "isnet" in path.stem
    h, w = rgb.shape[:2]
    x = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    if isnet:
        x = (x / 255.0 - 0.5)
    else:
        x = x / max(float(x.max()), 1e-6)
        x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    inp = np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)
    out = sess.run(None, {sess.get_inputs()[0].name: inp})[0]
    m = np.squeeze(out).astype(np.float32)
    m = (m - m.min()) / max(float(m.max() - m.min()), 1e-6)
    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_CUBIC)
    m = np.clip((m - 0.12) / 0.76, 0.0, 1.0)      # mild S-curve: trims the grey halo, keeps soft edges
    return np.clip(cv2.GaussianBlur(m, (0, 0), 0.8), 0, 1).astype(np.float32)
