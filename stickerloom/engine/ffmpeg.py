"""ffmpeg helpers (subprocess only). Works on Windows/macOS/Linux: uses PATH ffmpeg, else imageio-ffmpeg's bundled binary.
No ffprobe needed: probing parses `ffmpeg -i` output."""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys

import numpy as np


def _has_vp9(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
        return "libvpx-vp9" in out
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """First ffmpeg that can encode VP9+alpha: $STICKERLOOM_FFMPEG, then PATH, then imageio-ffmpeg's bundled binary.
    (Anaconda's ffmpeg has no libvpx-vp9, so PATH alone is not enough.) Falls back to the first one found."""
    cands = []
    if os.environ.get("STICKERLOOM_FFMPEG"):
        cands.append(os.environ["STICKERLOOM_FFMPEG"])
    if shutil.which("ffmpeg"):
        cands.append(shutil.which("ffmpeg"))
    try:
        import imageio_ffmpeg
        cands.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    for c in cands:
        if _has_vp9(c):
            return c
    if cands:
        return cands[0]
    raise RuntimeError("ffmpeg not found: install a full build or `pip install imageio-ffmpeg`")


@functools.lru_cache(maxsize=1)
def has_vp9() -> bool:
    try:
        return _has_vp9(ffmpeg_exe())
    except RuntimeError:
        return False


LOW_PRIORITY = 0x00004000 if sys.platform == "win32" else 0     # BELOW_NORMAL_PRIORITY_CLASS: children of a render batch yield to the rest of the PC


def _run(args, **kw):
    return subprocess.run([ffmpeg_exe(), "-hide_banner", *args], capture_output=True, creationflags=LOW_PRIORITY, **kw)


def probe(path, vp9_native: bool = False) -> dict:
    args = (["-c:v", "libvpx-vp9"] if vp9_native else []) + ["-i", str(path)]
    txt = _run(args).stderr.decode("utf-8", "replace")
    info = {"codec": None, "width": 0, "height": 0, "fps": 0.0, "duration": 0.0,
            "audio": "Audio:" in txt, "alpha_mode": bool(re.search(r"alpha_mode\s*:\s*1", txt, re.I))}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", txt)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    v = re.search(r"Video:\s*(\w+)", txt)
    if v:
        info["codec"] = v[1]
    s = re.search(r"[, ](\d{2,5})x(\d{2,5})[, \[]", txt[txt.find("Video:"):] if v else txt)
    if s:
        info["width"], info["height"] = int(s[1]), int(s[2])
    f = re.search(r"([\d.]+)\s*fps", txt)
    if f:
        info["fps"] = float(f[1])
    return info


SWS_CHROMA = "bicubic+full_chroma_int+accurate_rnd"
"""How a provider's H.264 (yuv420p: colour at HALF resolution) becomes RGB: the colour is INTERPOLATED to every pixel. ffmpeg's default fast path copies each
colour sample to a 2x2 block, so the green-screen key (a colour difference) cut the subject's edge in 2-pixel steps, which the ~1.3x enlargement of a
returned video sheet made visible as a pixelated outline. Measured on synthetic H.264 (2026-10-09): edge error after the enlargement 0.086-0.092 -> 0.041-0.067."""


def decode_cell(path, x: int, y: int, w: int, h: int, max_frames: int, cap_fps: float | None) -> np.ndarray:
    """One cell of a green-screen video as RGB frames. Converted to RGB with interpolated colour (SWS_CHROMA) BEFORE the crop: a crop of yuv420p snaps to
    even pixels, which could shift a cell by one against its layout rectangle."""
    vf = (f"fps={cap_fps}," if cap_fps else "") + f"scale=flags={SWS_CHROMA},format=rgb24,crop={w}:{h}:{x}:{y}"
    p = _run(["-loglevel", "error", "-i", str(path), "-vf", vf, "-frames:v", str(max_frames),
              "-f", "rawvideo", "-pix_fmt", "rgb24", "-an", "-"])
    n = len(p.stdout) // (w * h * 3)
    if p.returncode != 0 or n == 0:
        raise RuntimeError("decode failed: " + p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)


def encode_webm(frames_rgba: np.ndarray, fps: float, crf: int, out_path) -> None:
    n, h, w, _ = frames_rgba.shape
    p = subprocess.run(
        [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
         "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-auto-alt-ref", "0", "-b:v", "0", "-crf", str(crf), "-deadline", "good", "-cpu-used", "4",
         "-row-mt", "1", "-an", str(out_path)],
        input=np.ascontiguousarray(frames_rgba).tobytes(), capture_output=True, creationflags=LOW_PRIORITY)
    if p.returncode != 0:
        raise RuntimeError("encode failed: " + p.stderr.decode("utf-8", "replace")[-300:])


def decode_alpha(webm, frames: int = 4, wh: tuple | None = None) -> np.ndarray:
    """Decode with libvpx-vp9 (the default vp9 decoder hides alpha, so probe-only checks lie). wh = (width, height) when the caller already knows it (saves an ffmpeg launch)."""
    p = _run(["-loglevel", "error", "-c:v", "libvpx-vp9", "-i", str(webm), "-frames:v", str(frames),
              "-f", "rawvideo", "-pix_fmt", "rgba", "-"])
    w, h = wh if wh else (lambda i: (i["width"], i["height"]))(probe(webm))
    n = len(p.stdout) // (w * h * 4) if w and h else 0
    return np.frombuffer(p.stdout[: n * w * h * 4], np.uint8).reshape(n, h, w, 4) if n else np.zeros((0, 1, 1, 4), np.uint8)


def decode_full(path, w: int, h: int, max_frames: int, cap_fps: float | None) -> np.ndarray:
    """Decode a whole (already sliced) clip to RGBA. .webm alpha needs the libvpx-vp9 decoder."""
    pre = ["-c:v", "libvpx-vp9"] if str(path).lower().endswith(".webm") else []
    vf = ["-vf", f"fps={cap_fps}"] if cap_fps else []
    p = _run(["-loglevel", "error", *pre, "-i", str(path), *vf, "-frames:v", str(max_frames), "-f", "rawvideo", "-pix_fmt", "rgba", "-an", "-"])
    n = len(p.stdout) // (w * h * 4)
    if p.returncode != 0 or n == 0:
        raise RuntimeError("decode failed: " + p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout[: n * w * h * 4], np.uint8).reshape(n, h, w, 4)


def encode_anim(frames_rgba: np.ndarray, fps: float, fmt: str, quality: int, loop: bool = True, colors: int = 256) -> bytes:
    """Animated WebP (libwebp_anim, keeps alpha) or GIF (1-bit alpha) from RGBA frames."""
    n, h, w, _ = frames_rgba.shape
    src = ["-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-"]
    if fmt == "webp":
        cmd = src + ["-c:v", "libwebp_anim", "-lossless", "0", "-q:v", str(quality), "-compression_level", "4",
                     "-loop", "0" if loop else "1", "-an", "-f", "webp", "-"]
    elif fmt == "gif":
        cmd = src + ["-vf", f"split[a][b];[a]palettegen=max_colors={int(colors)}:reserve_transparent=1[p];[b][p]paletteuse=alpha_threshold=128",
                     "-loop", "0" if loop else "-1", "-an", "-f", "gif", "-"]
    else:
        raise ValueError("format must be webp or gif")
    p = subprocess.run([ffmpeg_exe(), *cmd], input=np.ascontiguousarray(frames_rgba).tobytes(), capture_output=True, creationflags=LOW_PRIORITY)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(f"{fmt} encode failed: " + p.stderr.decode("utf-8", "replace")[-300:])
    return p.stdout


def extract_frames(path, out_dir, fps: float, box: int, ext: str, max_frames: int) -> int:
    """Write preview frames (fit inside box x box, even dims, alpha kept for png) to out_dir/0000.<ext>. Returns the count.
    Works for any container ffmpeg reads (mp4, mov, webm, gif...); ffmpeg applies the rotation metadata."""
    from pathlib import Path
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    pre = ["-c:v", "libvpx-vp9"] if str(path).lower().endswith(".webm") else []
    q = ["-q:v", "4"] if ext == "jpg" else []
    p = _run(["-loglevel", "error", *pre, "-i", str(path), "-vf",
              f"fps={fps:.4f},scale={box}:{box}:force_original_aspect_ratio=decrease:force_divisible_by=2",
              "-frames:v", str(max_frames), "-an", *q, "-start_number", "0", str(out_dir / f"%04d.{ext}")])
    n = len(list(out_dir.glob(f"*.{ext}")))
    if n == 0:
        raise RuntimeError("decode failed: " + p.stderr.decode("utf-8", "replace")[-300:])
    return n


def decode_scaled(path, start: float, dur: float, fps: float, ow: int, oh: int, crop: int | None = None) -> np.ndarray:
    """RGBA frames of [start, start+dur) resampled to fps, scaled to ow x oh (then centre-cropped to crop x crop if given)."""
    pre = ["-c:v", "libvpx-vp9"] if str(path).lower().endswith(".webm") else []
    vf = f"fps={fps:.4f},scale={ow}:{oh}" + (f",crop={crop}:{crop}" if crop else "")
    p = _run(["-loglevel", "error", *pre, "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-vf", vf,
              "-f", "rawvideo", "-pix_fmt", "rgba", "-an", "-"])
    w, h = (crop, crop) if crop else (ow, oh)
    n = len(p.stdout) // (w * h * 4)
    if p.returncode != 0 or n == 0:
        raise RuntimeError("decode failed: " + p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout[: n * w * h * 4], np.uint8).reshape(n, h, w, 4).copy()
