"""Grey stand-ins for every exported animation: beside each video in a Download .zip, the animation's own first visible
frame (its still when the video cannot be read; 2026-10-15) as a flat grey silhouette — one colour, 75% white and 25% black (#BFBFBF), the frame's own alpha kept as the shape —
40% transparent (60% opacity: a solid pixel's alpha is 153), right next to its original under the original's own name plus `_grey.png`
(`🤣-falcon-laugh-s08-G112-20261008.webm` -> `🤣-falcon-laugh-s08-G112-20261008_grey.png`), compressed hard: a palette PNG with 4 alpha levels
(transparent, two edge steps, solid), about 2-3 KB a sticker. They are listed in `manifest.json` under `grey`, never under `assets`, so Export to
collection still sends only the stickers. Pillow + numpy only."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np

GREY = 191                 # 75% white + 25% black: round(0.75 * 255)
ALPHA_LEVELS = 4           # transparent, 1/3, 2/3, solid: soft edges at a fraction of the bytes of a full alpha
OPACITY = 0.6              # 40% transparent: the whole silhouette, edges included, at 60%
FIRST_FRAMES = 8           # the first visible frame is looked for among these (a clip may open on empty frames)

def grey_png(alpha: np.ndarray) -> bytes:
    """A flat #BFBFBF silhouette of `alpha` (HxW uint8), 40% transparent, as a palette PNG with ALPHA_LEVELS transparency steps."""
    from PIL import Image
    step = 255 // (ALPHA_LEVELS - 1)
    q = np.round(alpha.astype(np.float32) / step).astype(np.uint8)          # 0 .. ALPHA_LEVELS-1, used directly as the palette index
    img = Image.fromarray(q, "P")
    img.putpalette([GREY, GREY, GREY] * ALPHA_LEVELS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True, transparency=bytes(round(min(255, i * step) * OPACITY) for i in range(ALPHA_LEVELS)))
    return buf.getvalue()


def alpha_of(still: Path | None, video: Path | None) -> np.ndarray | None:
    """The shape to grey: the ANIMATION's own first visible frame (what is shipped beside it), else the sticker's still. None when neither can be
    read."""
    from PIL import Image
    if video is not None and Path(video).is_file():
        try:
            from ..engine import ffmpeg as ff
            info = ff.probe(video, vp9_native=True)
            for f in ff.decode_full(video, info["width"], info["height"], FIRST_FRAMES, None):
                if (f[..., 3] > 25).any():
                    return f[..., 3]
        except Exception:
            pass
    if still is not None and Path(still).is_file():
        try:
            return np.array(Image.open(still).convert("RGBA"))[..., 3]
        except Exception:
            pass
    return None


class Writer:
    """Adds the grey PNGs to an open zip, one per animation, each beside its original: `{original stem}_grey.png`."""

    def __init__(self, z):
        self.z, self.rows = z, []

    def add(self, original: str, still: Path | None, video: Path | None, sticker_id: str, emoji: str | None = None) -> None:
        a = alpha_of(still, video)
        if a is None:
            return
        name = f"{Path(original).stem}_grey.png"           # the originals' names are unique in the zip, so these are too
        data = grey_png(a)
        self.z.writestr(name, data)
        self.rows.append({"sticker_id": sticker_id, "emoji": emoji, "original": original, "filename": name, "kb": round(len(data) / 1024, 1)})
