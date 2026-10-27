"""Super reaction (Reactions 2.0): the emoji's own simulator look, played over the WHOLE screen instead of a 512 square.

The simulator works in canvas units (1 = the square's side). A super reaction keeps that unit as the screen's SHORT side and lets the canvas run
`aspect` units along the long one, so nothing is stretched: particles are born along the bottom (the emoji box), rise across the screen, and the
screen lights up with the emoji's colour. The canvas is one of a few aspect BUCKETS (a phone, a tablet, a desktop window ...); the player picks the
nearest and plays it with `xMidYMid slice`, so the small difference is cropped, never stretched. Wide screens get one bottom emitter per unit of
width (an emitter is at most one unit wide). Emoji-free like every preset: one external `emoji` asset, no base64. Pure: numpy + stdlib.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .lottie import SLOT_FILE, SLOT_ID, SLOT_PX, _layers
from .particles import EMITTERS, ParticleParams, trajectories

UNIT_PX = 512              # the short side of the exported canvas, px (= one simulator unit)
BUCKETS = {                # width / height of the screens a super reaction is made for
    "phone": 9 / 19.5, "phone-tall": 9 / 16, "tablet": 3 / 4, "square": 1.0,
    "tablet-wide": 4 / 3, "desktop": 16 / 10, "desktop-wide": 16 / 9, "ultrawide": 21 / 9,
}
GLOW_MODES = ("auto", "solid", "gradient", "none")
SUPER_SECONDS = 3.0        # the simulator's (and Telegram's) cap
SUPER_KEYS = ("count", "size", "rise", "float", "sway", "spread", "glow_strength", "pop")
SUPER_START = {"count": 36, "size": 1.0, "rise": 0.7, "float": 0.2, "sway": 0.4, "spread": 40.0, "glow_strength": 0.55, "pop": 1}
_LOOK = ("size_min", "size_max", "particle_size", "spin", "size_in_dur", "size_out_dur", "flip", "flip_var", "speed_var", "size_curve")


def bucket(aspect: float) -> tuple[str, float]:
    """The nearest bucket (by ratio, not difference) to a screen's width / height."""
    a = float(aspect)
    if not math.isfinite(a) or a <= 0:
        raise ValueError(f"aspect must be a positive number, got {aspect!r}")
    name = min(BUCKETS, key=lambda k: abs(math.log(BUCKETS[k] / a)))
    return name, BUCKETS[name]


def canvas(aspect: float) -> tuple[int, int]:
    """(W, H) px of a bucket's canvas: the short side is UNIT_PX."""
    _, a = bucket(aspect)
    return (UNIT_PX, round(UNIT_PX / a)) if a < 1 else (round(UNIT_PX * a), UNIT_PX)


def dominant_color(rgba: np.ndarray) -> tuple[int, int, int]:
    """The emoji's 'general colour': the strongest hue family of its opaque pixels (weighted by saturation x value), averaged in RGB.
    A grey/black/white emoji answers its plain average. Deterministic."""
    px = np.asarray(rgba, np.float64).reshape(-1, 4)
    px = px[px[:, 3] > 128][:, :3] / 255.0
    if not len(px):
        return (255, 200, 80)
    mx, mn = px.max(1), px.min(1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0)
    w = sat * mx
    if w.sum() < 0.05 * len(px):
        r, g, b = px.mean(0)
        return (round(r * 255), round(g * 255), round(b * 255))
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    hue = (np.degrees(np.arctan2(math.sqrt(3) * (g - b), 2 * r - g - b)) + 360.0) % 360.0
    bins = (hue // 20).astype(int)
    best = int(np.argmax(np.bincount(bins, weights=w, minlength=18)))
    sel = bins == best
    c = (px[sel] * w[sel, None]).sum(0) / w[sel].sum()
    c = c / max(c.max(), 1e-9)                                     # as bright as the hue allows: the glow is light, not mud
    return tuple(int(round(v * 255)) for v in c)


def hex_rgb(s) -> tuple[int, int, int]:
    t = str(s or "").strip().lstrip("#")
    if len(t) == 3:
        t = "".join(ch * 2 for ch in t)
    if len(t) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in t):
        raise ValueError(f"a colour is #rrggbb, got {s!r}")
    return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))


def scene_params(p: ParticleParams, aspect: float, settings: dict | None = None) -> tuple[list[ParticleParams], list[tuple[float, float]]]:
    """The tiles of one super reaction: (params per bottom emitter, its (dx, dy) shift in units). The emoji keeps its LOOK (sizes, spin,
    zoom in/out, flip); its placement becomes full-screen: a line emitter on the bottom, launched up, floating, swaying, a 1.6 s stream."""
    s = {**SUPER_START, **{k: v for k, v in (settings or {}).items() if v is not None}}
    bad = sorted(set(s) - set(SUPER_KEYS))
    if bad:
        raise ValueError(f"unknown super setting(s): {', '.join(bad)}")
    _, a = bucket(aspect)
    w_u, h_u = (1.0, 1.0 / a) if a < 1 else (a, 1.0)
    tiles = max(1, math.ceil(w_u - 1e-6))
    base = p.clamped()
    look = {k: getattr(base, k) for k in _LOOK}
    look["particle_size"] = base.particle_size * float(s["size"])          # Size: x the look's own (the engine clamps 0.25..3)
    total = int(min(max(round(float(s["count"])), 4), 80 * tiles))
    params, shifts = [], []
    for k in range(tiles):
        n = total // tiles + (1 if k < total % tiles else 0)
        q = replace(ParticleParams(), **look, seed=base.seed + 101 * k, fps=base.fps, duration=SUPER_SECONDS, lifetime=2.6, life_var=0.25,
                    count=max(4, n), emitter=EMITTERS.index("line"), emitter_w=min(1.0, w_u / tiles), origin=(0.5, 0.5),
                    emit_dur=1.6, direction=0.0, spread=float(s["spread"]) / h_u, vel_dist=0.4,      # the fan opens with the launch speed: narrower on a tall screen
                    magnitude=float(s["rise"]) * h_u, gravity=-float(s["float"]) * h_u,
                    turbulence=float(s["sway"]), turb_scale=1.5, turb_speed=0.6, vortex=0.0, hue_var=0.0, size=UNIT_PX).clamped()
        params.append(q)
        shifts.append(((k + 0.5) * w_u / tiles - 0.5, h_u + 0.04 - 0.5))      # the emitter just under the bottom edge: they rise into view
    return params, shifts


def _ease(f: float, v: float, out: bool = False) -> dict:
    return {"t": f, "s": [v], "i": {"x": [0.4], "y": [1]}, "o": {"x": [0.6], "y": [0]}} if not out else {"t": f, "s": [v]}


def _static(v) -> dict:
    return {"a": 0, "k": v}


def glow_layer(W: int, H: int, F: int, fps: float, c1, c2, strength: float, ind: int) -> dict:
    """A full-screen linear gradient from the bottom edge (c1, strength) up to the top (c2; transparent unless a gradient), fading in fast and out
    before the end. A plain shape layer: lottie-web and lottie-ios both play it."""
    gradient = c2 is not None
    top = c2 if gradient else c1
    stops = [0, *(v / 255 for v in c1), 1, *(v / 255 for v in top)] + [0, 1, 1, 0.35 if gradient else 0]
    peak = round(100.0 * min(max(float(strength), 0.0), 1.0), 1)
    f_in, f_hold, f_out = round(0.35 * fps), round(1.6 * fps), F - 1
    shape = {"ty": "gr", "nm": "glow", "it": [
        {"ty": "rc", "d": 1, "s": _static([W, H]), "p": _static([0, 0]), "r": _static(0)},
        {"ty": "gf", "o": _static(100), "r": 1, "bm": 0, "t": 1, "g": {"p": 2, "k": _static(stops)},
         "s": _static([0, H / 2]), "e": _static([0, -H / 2])},
        {"ty": "tr", "p": _static([0, 0]), "a": _static([0, 0]), "s": _static([100, 100]), "r": _static(0), "o": _static(100)},
    ]}
    return {"ddd": 0, "ind": ind, "ty": 4, "nm": "glow", "sr": 1, "ao": 0, "bm": 0, "ip": 0, "op": F, "st": 0,
            "ks": {"o": {"a": 1, "k": [_ease(0, 0), _ease(f_in, peak), _ease(f_hold, peak * 0.7), _ease(f_out, 0, True)]},
                   "r": _static(0), "p": _static([W / 2, H / 2, 0]), "a": _static([0, 0, 0]), "s": _static([100, 100, 100])},
            "shapes": [shape]}


def pop_layer(W: int, H: int, F: int, fps: float, ind: int, src: dict | None = None, settings: dict | None = None) -> dict:
    """The big one in the centre (the Instagram release): in with an overshoot and a beat, then it FLOATS: it climbs slowly (Rise, Float), wiggles left and right
    and tilts with it (Sway), is sized by Size, and fades out over the clip's last 0.6 s. `src` = a source Lottie's info (w, h, fr, ip, op): the pop
    is that animation, LOOPED while it floats (precomp L0 with a time remap), else the emoji slot picture."""
    s = {**SUPER_START, **(settings or {})}
    w, h = (src["w"], src["h"]) if src else (SLOT_PX, SLOT_PX)
    u = min(W, H)
    k = 100.0 * 0.45 * u * min(max(float(s["size"]), 0.25), 3.0) ** 0.5 / max(w, h)     # Size, gentler on the big one
    f0, f1, f2 = 3, 3 + round(0.22 * fps), 3 + round(0.38 * fps)                         # in, overshoot, settled
    fade = max(f2 + 1, F - 1 - round(0.6 * fps))
    sway = max(0.0, float(s["sway"]))
    climb = H * min(0.55, 0.12 + 0.35 * max(0.0, float(s["rise"])) + 0.25 * max(0.0, float(s["float"])))   # px over the float
    amp, hz = u * (0.02 + 0.05 * min(sway, 1.5)), 0.45 + 0.25 * min(sway, 1.5)                             # the wiggle: width and pace
    T = max(1e-6, (F - 1 - f2) / fps)

    def sk(t, v):
        return {"t": t, "s": [v, v, 100], "i": {"x": [0.4], "y": [1]}, "o": {"x": [0.6], "y": [0]}}

    def hold(frames, vals):
        return {"a": 1, "k": [{"t": t, "s": v, "h": 1} for t, v in zip(frames, vals)]}
    fr = list(range(f0, F))
    pos, rot = [], []
    for t in fr:
        q = max(0.0, (t - f2) / fps)                                     # seconds of floating
        e = q / T
        rise = climb * (0.7 * e + 0.3 * e * e)                           # slow, a little faster as it goes (Float)
        wig = math.sin(2 * math.pi * hz * q) * min(1.0, q / 0.4)         # eases into the wiggle
        pos.append([round(W / 2 + amp * wig, 2), round(H / 2 - rise, 2), 0])
        rot.append([round(6.0 * (0.3 + min(sway, 1.5)) * wig * -1, 2)])  # it leans into the swing
    layer = {"ddd": 0, "ind": ind, "ty": 2, "nm": "pop", "refId": SLOT_ID, "sr": 1, "ao": 0, "bm": 0, "ip": f0, "op": F, "st": 0,
             "ks": {"o": {"a": 1, "k": [_ease(f0, 100), _ease(fade, 100), _ease(F - 1, 0, True)]},
                    "r": hold(fr, rot), "p": hold(fr, pos), "a": _static([w / 2, h / 2, 0]),
                    "s": {"a": 1, "k": [sk(f0, 0), sk(f1, k * 1.18), sk(f2, k), {"t": F - 1, "s": [k * 0.92, k * 0.92, 100]}]}}}
    if src:
        sr = fps / src["fr"]
        n_src = max(1.0, float(src.get("op", 0) - src["ip"]))
        layer.update({"ty": 0, "refId": "L0", "w": w, "h": h, "sr": round(sr, 6), "st": round(f0 - src["ip"] * sr, 4),
                      "tm": hold(fr, [[round((src["ip"] + math.floor(((t - f0) / sr) % n_src)) / fps, 5)] for t in fr])})   # loops while it floats
    return layer


def lottie_rgba(doc: dict) -> np.ndarray:
    """A Lottie's static fill and stroke colours as pseudo-pixels (N, 4) for dominant_color: every layer and precomp, each colour once per use."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("ty") in ("fl", "st") and isinstance(x.get("c"), dict) and x["c"].get("a") in (0, None):
                k = x["c"].get("k")
                if isinstance(k, list) and len(k) >= 3 and all(isinstance(v, (int, float)) for v in k[:3]):
                    rgb = [float(v) for v in k[:3]]
                    if max(rgb) <= 1.0:
                        rgb = [v * 255 for v in rgb]
                    found.append([*rgb, 255])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(doc.get("layers"))
    walk(doc.get("assets"))
    return np.array(found or [[0, 0, 0, 0]], np.float64)


def export_super(p: ParticleParams, aspect: float, *, settings: dict | None = None, glow: str = "auto", color=None, colors=None,
                 emoji_rgba=None, source: dict | None = None, name: str = "super reaction") -> dict:
    """A super reaction as one Lottie of the bucket nearest `aspect` (width / height). `p` is the look (sizes, spin, zoom; kept).
    The particles are the emoji slot (emoji-free, one external `emoji` asset), or, with `source` (a Lottie document, a dropped file in lottie),
    instances of that animation, embedded once (engine.lottie Lottie style). glow: 'auto' (the dominant colour of `emoji_rgba`, or of the source's
    fills; else a warm default), 'solid' (`color` #rrggbb), 'gradient' (`colors` = [bottom, top]), 'none'. Same inputs -> the same JSON."""
    from .lottie import export_lottie_burst
    if glow not in GLOW_MODES:
        raise ValueError(f"glow must be one of {', '.join(GLOW_MODES)}")
    s = {**SUPER_START, **{k: v for k, v in (settings or {}).items() if v is not None}}
    bname, a = bucket(aspect)
    W, H = canvas(a)
    params, shifts = scene_params(p, a, settings)
    layers, assets, src = [], [{"id": SLOT_ID, "w": SLOT_PX, "h": SLOT_PX, "u": "", "p": SLOT_FILE, "e": 0}], None
    for q, (dx, dy) in zip(params, shifts):
        if source is not None:
            d = export_lottie_burst(q, source, size=UNIT_PX, name=name, shift=(dx, dy))
            layers += d["layers"]
            assets = d["assets"]
        else:
            tr = trajectories(q, 1)
            tr.pos = tr.pos + np.array([dx, dy])
            layers += _layers(q, tr, UNIT_PX, UNIT_PX, lambda i: (SLOT_ID, SLOT_PX, (SLOT_PX, SLOT_PX)))
    if source is not None:
        src = {"w": int(source.get("w") or 512), "h": int(source.get("h") or 512), "fr": float(source.get("fr") or 30.0), "ip": float(source.get("ip") or 0.0),
               "op": float(source.get("op") or 0.0)}
    F, fps = int(round(params[0].fps * SUPER_SECONDS)), float(params[0].fps)
    if int(s["pop"]):
        layers.insert(0, pop_layer(W, H, F, fps, 0, src, s))
    c1 = c2 = None
    if glow == "auto":
        rgba = emoji_rgba if emoji_rgba is not None else (lottie_rgba(source) if source is not None else None)
        c1 = dominant_color(rgba) if rgba is not None else (255, 170, 60)
    elif glow == "solid":
        c1 = hex_rgb(color)
    elif glow == "gradient":
        if not isinstance(colors, (list, tuple)) or len(colors) != 2:
            raise ValueError("a gradient takes two colours: [bottom, top]")
        c1, c2 = hex_rgb(colors[0]), hex_rgb(colors[1])
    if c1 is not None:
        layers.append(glow_layer(W, H, F, fps, c1, c2, float(s["glow_strength"]), 0))     # last = drawn behind the particles
    for i, layer in enumerate(layers, 1):
        layer["ind"] = i
    return {"v": "5.7.4", "fr": fps, "ip": 0, "op": F, "w": W, "h": H, "nm": f"{name} ({bname})", "ddd": 0, "assets": assets, "layers": layers,
            "stickerloom": {"bucket": bname, "aspect": round(a, 4), "glow": glow, "color": None if c1 is None else "#%02x%02x%02x" % c1}}


# A file nobody has saved yet still has a look of its own: seeded by its name,
# so it is the same on every visit; Shuffle asks for the next seed. (lo, hi, step) per setting, inside the sandbox's slider ranges.
RANDOM_RANGES = {"size": (0.7, 1.6, 0.05), "count": (24, 60, 1), "rise": (0.5, 0.95, 0.05), "float": (0.1, 0.4, 0.05),
                 "sway": (0.1, 0.9, 0.05), "spread": (20, 70, 1), "glow_strength": (0.35, 0.75, 0.05)}


def seed_of(name: str) -> int:
    """The seed a file's random look starts from: its name, stable across machines and runs."""
    import zlib
    return zlib.crc32(str(name).encode("utf-8")) & 0x7FFFFFFF


def random_settings(seed: int) -> dict:
    """A random super reaction look: every RANDOM_RANGES setting, rounded to its slider step, plus the pop. Same seed -> same settings."""
    import random
    rnd = random.Random(int(seed))
    out = {}
    for k, (lo, hi, step) in RANDOM_RANGES.items():
        v = round(round(rnd.uniform(lo, hi) / step) * step, 4)
        out[k] = int(v) if float(step).is_integer() else v
    out["pop"] = 1
    return out
