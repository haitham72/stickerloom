"""One emoji glyph as a particle sprite: the Win+. glyph itself, drawn with the system's colour emoji font. Pure, deterministic for a given font file.

Used by Reaction effects (flow/emoji_reactions.py): the sprite IS the emoji, rendered here, then flown by engine/particles.py and exported by engine/lottie.py.
Pillow only; no network (rule 3).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .particles import trim_sprite

# The Win+. glyph itself, drawn by the system's colour emoji font (COLR on Windows, sbix on macOS, CBDT on Linux). A bitmap
# font only opens at its own strike sizes (Noto 109, Apple 160), so each size is tried in turn; a scalable one takes the first.
EMOJI_FONTS = (
    str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguiemj.ttf"),     # Segoe UI Emoji
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf",
)
_EMOJI_STRIKES = (109, 160, 137, 128, 96, 64)


def emoji_font() -> str | None:
    """The first colour emoji font of EMOJI_FONTS present on this machine, or None."""
    return next((f for f in EMOJI_FONTS if Path(f).is_file()), None)


def render_emoji(glyph: str, px: int = 160, font_path: str | None = None) -> np.ndarray:
    """One emoji glyph as a tight RGBA uint8 sprite whose longest side is `px` (trimmed to its alpha box like trim_sprite, 2 px of air).
    Deterministic for a given font file. Without a text-shaping library a ZWJ sequence draws as its parts side by side.
    ValueError for an empty glyph; RuntimeError when no colour emoji font is found or none draws the glyph."""
    glyph, px = str(glyph or "").strip(), int(px)
    if not glyph:
        raise ValueError("no emoji given")
    if px < 8:
        raise ValueError("px must be at least 8")
    fonts = [font_path] if font_path else [f for f in EMOJI_FONTS if Path(f).is_file()]
    for path in fonts:
        for size in _EMOJI_STRIKES:
            try:
                font = ImageFont.truetype(str(path), size)
            except OSError:
                continue
            w, h = size * (len(glyph) + 2), size * 2
            im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((w / 2, h / 2), glyph, font=font, anchor="mm", embedded_color=True)
            tight = trim_sprite(np.asarray(im), pad=0)
            if tight is None:
                break                                                # the font opened but has nothing for this glyph: try the next font
            th, tw = tight.shape[:2]
            k = (px - 4) / max(th, tw)
            fit = Image.fromarray(np.ascontiguousarray(tight), "RGBA").resize((max(1, round(tw * k)), max(1, round(th * k))), Image.LANCZOS)
            out = Image.new("RGBA", (fit.width + 4, fit.height + 4), (0, 0, 0, 0))
            out.paste(fit, (2, 2))
            return np.asarray(out, np.uint8).copy()
    raise RuntimeError("no colour emoji font on this machine draws " + repr(glyph) + " (looked for Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji)")
