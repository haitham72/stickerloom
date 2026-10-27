"""A particle burst as a playable Lottie JSON (web only; Telegram stays WebM): engine/particles.py's deterministic trajectories mapped onto image layers
over the real sprite PNGs. Pure (numpy + Pillow + stdlib JSON); no new dependency.
"""
from __future__ import annotations

import base64
import io
import math

import numpy as np
from PIL import Image

from .particles import MORPH_XFADE, ParticleParams, hue_rotate, png_bytes, trajectories, trim_sprite

HUE_STEP = 10.0         # Lottie cannot turn an image's hue: each particle's hue is rounded to this step and every step used is one embedded asset


SLOT_ID = "emoji"          # the one external asset of an emoji-free preset: the chat app supplies the picture
SLOT_FILE = "emoji.png"
SLOT_PX = 160              # the side the app's emoji picture is assumed to have; particle scales are computed against it


def export_lottie(p: ParticleParams, emoji_png=None, *, size: int = 512, slot: bool = False) -> dict:
    """A playable Lottie JSON of one burst (web use only; Telegram stays WebM): the deterministic `trajectories()` mapped onto
    image layers (`ty: 2`) that show the real sprite, so any standard player (lottie-web, the LottieFiles preview) plays the
    picture flying, never a placeholder shape.

    `emoji_png` is the sprite as PNG bytes (RGBA), or a list of PNG bytes, one per sprite index. Each sprite is embedded ONCE as a
    base64 data URI asset (`e: 1`) and every particle that shows it references it. With `hue_var` > 0 the particle's hue is
    rounded to HUE_STEP degrees and each distinct (sprite, hue) is one more asset, tinted by hue_rotate(); with `hue_var` 0
    one sprite is exactly one asset. A layer lives only from its particle's birth to its death (`ip`/`op`), with one hold
    keyframe per frame for position, scale, rotation and opacity. Same params + same PNG bytes -> the same JSON. Pure.

    `slot=True` (an animation preset): NO picture and NO base64 anywhere. Every particle references ONE external image
    asset `{"id": "emoji", "u": "", "p": "emoji.png", "e": 0}` of SLOT_PX x SLOT_PX that the chat app replaces with the emoji it shows
    (lottie-web `assetsPath` / an image provider). Hue variation needs the picture, so a slot export has none (`hue_var` is ignored)."""
    if slot:
        return _slot_lottie(p, size)
    pngs = [emoji_png] if isinstance(emoji_png, (bytes, bytearray)) else list(emoji_png or [])
    if not pngs:
        raise ValueError("no sprite given")
    sprites = []
    for data in pngs:
        try:
            img = np.asarray(Image.open(io.BytesIO(bytes(data))).convert("RGBA"), np.uint8)
        except Exception:
            raise ValueError("a sprite must be PNG bytes") from None
        tight = trim_sprite(img, pad=1)
        if tight is None:
            raise ValueError("a sprite is fully transparent")
        sprites.append(tight)
    p = p.clamped()
    W = H = int(size)
    cap = max(8, math.ceil(min(p.size_max * p.particle_size, 0.9) * W * 1.2))      # the biggest it is drawn: no point embedding more pixels
    for k, img in enumerate(sprites):
        h, w = img.shape[:2]
        if max(h, w) > cap:
            r = cap / max(h, w)
            sprites[k] = np.asarray(Image.fromarray(img, "RGBA").resize((max(1, round(w * r)), max(1, round(h * r))), Image.LANCZOS), np.uint8)
    tr = trajectories(p, len(sprites))
    F, fps = tr.frames, float(p.fps)
    assets, ids = [], {}

    def asset(k: int, hue: float) -> tuple[str, int]:
        key = (k, hue)
        if key not in ids:
            img = sprites[k] if hue == 0 else hue_rotate(sprites[k], hue)
            ids[key] = f"sprite_{k}" + ("" if hue == 0 else f"_hue{int(hue):+d}")
            assets.append({"id": ids[key], "w": int(img.shape[1]), "h": int(img.shape[0]), "u": "",
                           "p": "data:image/png;base64," + base64.b64encode(png_bytes(img)).decode("ascii"), "e": 1})
        return ids[key], max(sprites[k].shape[:2])

    def keys(frames, vals):
        return {"a": 1, "k": [{"t": f, "s": v, "h": 1} for f, v in zip(frames, vals)]}

    layers = []
    for i in tr.draw_order[::-1].tolist():                          # Lottie draws its first layer on top: the biggest particle first, as simulate() does last
        alive = np.flatnonzero(tr.scale[:, i] > 0)
        if not len(alive):
            continue
        ip, op = int(alive[0]), int(alive[-1]) + 1
        fr = list(range(ip, op))
        hue = 0.0 if p.hue_var == 0 else float(round(float(tr.hue[i]) / HUE_STEP) * HUE_STEP)
        ref, long_side = asset(int(tr.sprite[i]), hue + 0.0)
        k_scale = 100.0 * float(tr.size_frac[i]) * W / long_side
        img_h, img_w = sprites[int(tr.sprite[i])].shape[:2]
        layers.append({
            "ddd": 0, "ind": len(layers) + 1, "ty": 2, "nm": f"particle {i + 1}", "refId": ref, "sr": 1, "ao": 0, "bm": 0,
            "ks": {
                "o": keys(fr, [[round(float(tr.opacity[f, i]) * 100.0, 1)] for f in fr]),
                "r": keys(fr, [[round(math.degrees(float(tr.angle[f, i])), 1)] for f in fr]),
                "p": keys(fr, [[round(float(tr.pos[f, i, 0]) * W, 1), round(float(tr.pos[f, i, 1]) * H, 1), 0] for f in fr]),
                "a": {"a": 0, "k": [img_w / 2.0, img_h / 2.0, 0]},
                "s": keys(fr, [[round(float(tr.scale[f, i]) * k_scale * float(tr.flipx[f, i]), 2), round(float(tr.scale[f, i]) * k_scale, 2), 100] for f in fr]),
            },
            "ip": ip, "op": op, "st": 0,
        })
    return {"v": "5.7.4", "fr": fps, "ip": 0, "op": F, "w": W, "h": H, "nm": f"reaction burst ({p.count} particles)",
            "ddd": 0, "assets": assets, "layers": layers}


def _layers(p: ParticleParams, tr, W: int, H: int, ref_of) -> list:
    """The particle layers of one burst: `ref_of(i) -> (asset id, its long side, (h, w))` names the picture each particle shows."""
    def keys(frames, vals):
        return {"a": 1, "k": [{"t": f, "s": v, "h": 1} for f, v in zip(frames, vals)]}
    layers = []
    for i in tr.draw_order[::-1].tolist():
        alive = np.flatnonzero(tr.scale[:, i] > 0)
        if not len(alive):
            continue
        ip, op = int(alive[0]), int(alive[-1]) + 1
        fr = list(range(ip, op))
        ref, long_side, (img_h, img_w) = ref_of(i)
        k_scale = 100.0 * float(tr.size_frac[i]) * W / long_side
        layers.append({
            "ddd": 0, "ind": len(layers) + 1, "ty": 2, "nm": f"particle {i + 1}", "refId": ref, "sr": 1, "ao": 0, "bm": 0,
            "ks": {
                "o": keys(fr, [[round(float(tr.opacity[f, i]) * 100.0, 1)] for f in fr]),
                "r": keys(fr, [[round(math.degrees(float(tr.angle[f, i])), 1)] for f in fr]),
                "p": keys(fr, [[round(float(tr.pos[f, i, 0]) * W, 1), round(float(tr.pos[f, i, 1]) * H, 1), 0] for f in fr]),
                "a": {"a": 0, "k": [img_w / 2.0, img_h / 2.0, 0]},
                "s": keys(fr, [[round(float(tr.scale[f, i]) * k_scale * float(tr.flipx[f, i]), 2), round(float(tr.scale[f, i]) * k_scale, 2), 100] for f in fr]),
            },
            "ip": ip, "op": op, "st": 0,
        })
    return layers


def _slot_lottie(p: ParticleParams, size: int) -> dict:
    """An emoji-free preset: one external asset, every particle referencing it (see export_lottie, slot=True)."""
    p = p.clamped()
    W = H = int(size)
    tr = trajectories(p, 1)
    layers = _layers(p, tr, W, H, lambda i: (SLOT_ID, SLOT_PX, (SLOT_PX, SLOT_PX)))
    return {"v": "5.7.4", "fr": float(p.fps), "ip": 0, "op": tr.frames, "w": W, "h": H, "nm": f"reaction preset ({p.count} particles)", "ddd": 0,
            "assets": [{"id": SLOT_ID, "w": SLOT_PX, "h": SLOT_PX, "u": "", "p": SLOT_FILE, "e": 0}], "layers": layers}


# ---- Lottie style: every particle is a whole Lottie animation ----------------------------------------------------------------
MAX_PARTICLES = 400        # roots + children: a bigger burst is refused (each particle is a precomp layer, a morph adds one more)


def _prefixed(layers: list, pre: str) -> list:
    """The source's layers with every asset reference renamed into its own namespace (two embedded files never collide)."""
    out = []
    for L in layers:
        L = dict(L)
        if "refId" in L:
            L["refId"] = pre + str(L["refId"])
        out.append(L)
    return out


def _embed(doc: dict, k: int) -> tuple[list, dict]:
    """(assets, info) of one source Lottie embedded as precomp `L<k>`: its own assets renamed `L<k>_<id>`, its top layers as the precomp."""
    cid, pre = f"L{k}", f"L{k}_"
    assets = []
    for a in doc.get("assets") or []:
        a = dict(a)
        a["id"] = pre + str(a.get("id"))
        if isinstance(a.get("layers"), list):
            a["layers"] = _prefixed(a["layers"], pre)
        assets.append(a)
    assets.append({"id": cid, "nm": str(doc.get("nm") or cid), "layers": _prefixed(doc.get("layers") or [], pre)})
    fr = float(doc.get("fr") or 30.0)
    info = {"w": int(doc.get("w") or 512), "h": int(doc.get("h") or 512), "fr": fr, "ip": float(doc.get("ip") or 0.0), "op": float(doc.get("op") or 0.0)}
    return assets, info


def export_lottie_burst(p: ParticleParams, origin: dict, targets: list | None = None, *, size: int = 512, name: str = "lottie reaction",
                        shift: tuple = (0.0, 0.0)) -> dict:
    """A Lottie-style burst: `origin` (a Lottie document) flying as every particle, each its own instance (its own playhead from its birth), never a
    raster. `targets` (Lottie documents) are the morph targets: with `morph_at` > 0 each particle picks one (seeded) and crossfades into it
    MORPH_XFADE s long at its morph time, the target playing from that moment. Children (`children` > 0) burst at the morph and fly a picked target
    (the origin with none); with `morph_burst` the root fades out into them instead of turning (1:1). The result embeds the origin
    and every target ONCE each as precomp assets (`L0`, `L1`, ...; their own assets renamed `L<k>_...`), however many particles show them; each source
    plays at its own frame rate (`sr`) and ends when it ends (no loop). Same params + same documents -> the same JSON. Pure."""
    if not isinstance(origin, dict) or not isinstance(origin.get("layers"), list):
        raise ValueError("the origin is not a Lottie animation")
    targets = [t for t in (targets or []) if isinstance(t, dict)]
    if any(not isinstance(t.get("layers"), list) for t in targets):
        raise ValueError("a morph target is not a Lottie animation")
    p = p.clamped()
    W = H = int(size)
    tr = trajectories(p, 1)
    if tuple(shift) != (0.0, 0.0):                                   # a super reaction's bottom emitters (engine/super_reaction.py); (0, 0) = the old bytes
        tr.pos = tr.pos + np.array(shift, dtype=np.float64)
    N = len(tr.spawn)
    if N > MAX_PARTICLES:
        raise ValueError(f"{N} particles is more than {MAX_PARTICLES}: lower Amount or Children")
    fps, F = float(p.fps), tr.frames
    assets, info = [], []
    for k, doc in enumerate([origin, *targets]):
        a, i = _embed(doc, k)
        assets.extend(a)
        info.append(i)
    xf = max(1, int(round(MORPH_XFADE * fps)))

    def keys(frames, vals):
        return {"a": 1, "k": [{"t": f, "s": v, "h": 1} for f, v in zip(frames, vals)]}

    def layer(i: int, k: int, ip: int, op: int, start: int, fade) -> dict | None:
        src = info[k]
        sr = fps / src["fr"]                                         # the source plays at its own speed
        ts = p.time_sampling                                         # 0 = from its birth, once; else a time remap (tm, in seconds of the main comp)
        if not ts:
            end = start + int(math.ceil((src["op"] - src["ip"]) * sr))   # ... and ends when it ends
            op = min(op, end)
        if op <= ip:
            return None
        fr = list(range(ip, op))
        tm = None
        if ts:
            n_src = max(1.0, float(src["op"] - src["ip"]))
            off = float(tr.sample[i]) * n_src if ts >= 2 else 0.0
            if ts == 3:                                              # random still: one frame of the source, held
                tm = {"a": 0, "k": round((src["ip"] + math.floor(off)) / fps, 5)}
            else:                                                    # loop (from its birth, or from a random point)
                tm = keys(fr, [[round((src["ip"] + math.floor(((f - start) / sr + off) % n_src)) / fps, 5)] for f in fr])
        long_side = float(max(src["w"], src["h"]))
        k_scale = 100.0 * float(tr.size_frac[i]) * W / long_side
        return {
            "ddd": 0, "ind": 0, "ty": 0, "nm": f"particle {i + 1}" + ("" if k == 0 else f" -> L{k}"), "refId": f"L{k}", "sr": round(sr, 6),
            "w": src["w"], "h": src["h"], "ao": 0, "bm": 0,
            "ks": {
                "o": keys(fr, [[round(float(tr.opacity[f, i]) * fade(f) * 100.0, 1)] for f in fr]),
                "r": keys(fr, [[round(math.degrees(float(tr.angle[f, i])), 1)] for f in fr]),
                "p": keys(fr, [[round(float(tr.pos[f, i, 0]) * W, 1), round(float(tr.pos[f, i, 1]) * H, 1), 0] for f in fr]),
                "a": {"a": 0, "k": [src["w"] / 2.0, src["h"] / 2.0, 0]},
                "s": keys(fr, [[round(float(tr.scale[f, i]) * k_scale * float(tr.flipx[f, i]), 2), round(float(tr.scale[f, i]) * k_scale, 2), 100] for f in fr]),
            },
            "ip": ip, "op": op, "st": round(start - src["ip"] * sr, 4), **({"tm": tm} if tm else {}),
        }

    layers = []
    for i in tr.draw_order[::-1].tolist():                          # Lottie draws its first layer on top: the biggest particle first
        alive = np.flatnonzero(tr.scale[:, i] > 0)
        if not len(alive):
            continue
        ip, op = int(alive[0]), int(alive[-1]) + 1
        birth = int(tr.spawn[i])
        k_pick = 1 + min(len(targets) - 1, int(float(tr.pick[i]) * len(targets))) if targets else 0
        if int(tr.parent[i]) >= 0:                                   # a child is born as a target (the origin with no target), never morphs again
            L = layer(i, k_pick, ip, op, birth, lambda f: 1.0)
            if L:
                layers.append(L)
            continue
        m = birth + int(round(float(tr.morph[i]) * fps)) if targets and math.isfinite(float(tr.morph[i])) and not (p.morph_burst and p.children > 0) else None
        if m is not None and ip < m < op:
            k = k_pick
            out_ = layer(i, 0, ip, min(op, m + xf), birth, lambda f, m=m: min(1.0, max(0.0, (m + xf - f) / xf)))
            in_ = layer(i, k, m, op, m, lambda f, m=m: min(1.0, max(0.0, (f - m + 1) / xf)))
            layers.extend(L for L in (in_, out_) if L)
        else:
            L = layer(i, 0, ip, op, birth, lambda f: 1.0)
            if L:
                layers.append(L)
    for n_, L in enumerate(layers, 1):
        L["ind"] = n_
    return {"v": "5.7.4", "fr": fps, "ip": 0, "op": F, "w": W, "h": H, "nm": f"{name} ({N} particles)", "ddd": 0, "assets": assets, "layers": layers}
