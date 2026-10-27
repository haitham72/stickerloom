"""Placeholder art for the Studio: a generated swatch for every style preset (console/assets/styles/ may hold real images, otherwise these are used).
`find()` returns a real file when one exists (png, jpg, webp, svg), otherwise a generated SVG, so the UI never shows a broken image."""
from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".svg")
SAFE = re.compile(r"^[a-z0-9_]{1,40}$")
BRAND = re.compile(r"^[a-z0-9_-]{1,40}\.(png|jpg|svg|webp)$")

# an abstract swatch for each style: one orb drawn the way the style looks. It claims nothing the style does not do (the first version drew a teddy head, which read as the subject of the
# style); a preset with no entry here gets a colour of its own derived from its id, so a new preset never shows a broken or identical tile.
STYLE_ART = {
    "flat_vector": ("#ffe9c9", "#f4a259", None),
    "toon_shade": ("#dff3ff", "#f4a259", "toon"),
    "glossy_3d": ("#e8e4ff", "url(#g)", "gloss"),
    "clay_3d": ("#fdebd8", "url(#c)", "clay"),
    "realistic": ("#e9e2d6", "url(#r)", "fur"),
    "hand_drawn": ("#fff7d6", "#f6c177", "sketch"),
    "minimal": ("#f4f6f8", "none", "ring"),
    "pixel_art": ("#e3f6e8", "#3fa66b", "pixel"),
    "watercolor": ("#eef6ff", "#7fb5e8", "wash"),
    "paper_cut": ("#fdf0e6", "#ef8a6b", "layers"),
    "pop_comic": ("#fff3c4", "#e8423f", "dots"),
    "kawaii": ("#ffe8f1", "#ffb3cf", "kawaii"),
}


def _derived(sid: str) -> tuple:
    hue = sum(ord(c) * (n + 1) for n, c in enumerate(sid)) % 360
    return (f"hsl({hue} 70% 93%)", f"hsl({hue} 62% 60%)", None)


def style_svg(sid: str) -> str:
    bg, orb, fx = STYLE_ART.get(sid) or _derived(sid)
    defs = ('<defs><radialGradient id="g" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#ffd9a8"/><stop offset=".55" stop-color="#f08a3c"/><stop offset="1" stop-color="#b95a1a"/></radialGradient>'
            '<radialGradient id="c" cx=".4" cy=".35" r=".8"><stop offset="0" stop-color="#f6c79a"/><stop offset="1" stop-color="#d9965b"/></radialGradient>'
            '<radialGradient id="r" cx=".4" cy=".35" r=".8"><stop offset="0" stop-color="#c79a63"/><stop offset="1" stop-color="#7d5630"/></radialGradient></defs>')
    cx = cy = 120
    body = f'<circle cx="{cx}" cy="{cy}" r="62" fill="{orb}"/>'
    if fx == "toon":
        body = f'<circle cx="{cx}" cy="{cy}" r="62" fill="{orb}" stroke="#4a3320" stroke-width="4"/><path d="M70 150a58 58 0 0 0 100 0 62 62 0 0 1-100 0z" fill="#00000026"/>'
    elif fx == "gloss":
        body += '<ellipse cx="98" cy="88" rx="26" ry="14" fill="#ffffff" opacity=".7" transform="rotate(-25 98 88)"/>'
    elif fx == "clay":
        body = '<ellipse cx="120" cy="188" rx="48" ry="9" fill="#00000014"/>' + body
    elif fx == "fur":
        body += "".join(f'<path d="M{72 + i * 10} {96 + (i % 3) * 18}l5 9M{76 + i * 10} {150 - (i % 2) * 14}l-4 10" stroke="#3b2a15" stroke-opacity=".45" stroke-width="2" fill="none"/>' for i in range(10))
    elif fx == "sketch":
        body = f'<circle cx="{cx}" cy="{cy}" r="62" fill="{orb}" stroke="#9a6a2e" stroke-width="3" stroke-dasharray="9 5"/>' + "".join(f'<path d="M{82 + i * 9} 160l12 -14" stroke="#9a6a2e" stroke-opacity=".55" stroke-width="2"/>' for i in range(8))
    elif fx == "ring":
        body = f'<circle cx="{cx}" cy="{cy}" r="58" fill="none" stroke="#475569" stroke-width="3"/><circle cx="{cx + 58}" cy="{cy - 8}" r="7" fill="#f59e0b"/>'
    elif fx == "pixel":
        px = 12
        body = "".join(f'<rect x="{x}" y="{y}" width="{px}" height="{px}" fill="{orb}"/>' for y in range(60, 180, px) for x in range(60, 180, px)
                       if (x + px / 2 - cx) ** 2 + (y + px / 2 - cy) ** 2 <= 58 ** 2)
    elif fx == "wash":
        body = ('<circle cx="104" cy="112" r="54" fill="#7fb5e8" opacity=".55"/><circle cx="138" cy="124" r="50" fill="#f4a6c0" opacity=".5"/>'
                '<circle cx="118" cy="146" r="44" fill="#ffd27a" opacity=".5"/>')
    elif fx == "layers":
        body = ('<circle cx="132" cy="136" r="58" fill="#00000018"/><circle cx="126" cy="130" r="58" fill="#f7c59f"/><circle cx="122" cy="126" r="48" fill="#00000018"/>'
                f'<circle cx="118" cy="122" r="48" fill="{orb}"/><circle cx="116" cy="118" r="30" fill="#ffffff55"/>')
    elif fx == "dots":
        body = (f'<circle cx="{cx}" cy="{cy}" r="62" fill="{orb}" stroke="#111" stroke-width="6"/>'
                + "".join(f'<circle cx="{x}" cy="{y}" r="3.4" fill="#00000040"/>' for y in range(118, 176, 12) for x in range(72, 172, 12) if (x - cx) ** 2 + (y - cy) ** 2 < 56 ** 2))
    elif fx == "kawaii":
        body += '<circle cx="98" cy="118" r="8" fill="#4a2a3a"/><circle cx="142" cy="118" r="8" fill="#4a2a3a"/><circle cx="90" cy="140" r="9" fill="#ff7aa8" opacity=".55"/><circle cx="150" cy="140" r="9" fill="#ff7aa8" opacity=".55"/>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">{defs}<rect width="240" height="240" rx="22" fill="{bg}"/>{body}</svg>'


def find(kind: str, name: str):
    """(bytes, content-type) for assets/<kind>/<name>.<ext>, else a generated placeholder. None for an unknown kind or unsafe name."""
    if kind == "brand":                       # real files only (the app logo, an SVG), no generated placeholder
        f = ASSETS / "brand" / name
        if not BRAND.match(name) or not f.is_file():
            return None
        return f.read_bytes(), {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}[f.suffix.lower()]
    if kind != "styles" or not SAFE.match(name):
        return None
    for ext in EXTS:
        f = ASSETS / kind / (name + ext)
        if f.is_file():
            return f.read_bytes(), {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}[ext]
    return style_svg(name).encode("utf-8"), "image/svg+xml"
