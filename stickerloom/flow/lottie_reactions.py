"""Lottie style reactions: every particle is a whole Lottie animation from the drop zone.

Emoji style (flow/emoji_reactions.py) stays as it is; this is the second, permanent style. No emoji bank, no glyphs, no motion presets: each file
in `lottie/` (`emoji_reactions.handpicked_dir`) is one reaction, named by its file. Its settings (the neutral engine defaults until saved),
its morph targets and its children live in out/particles/lottie/library.json `{files: {file: {settings, morph_targets, updated}}}`. The export
(`compose`) embeds the origin and every morph target in ONE self-contained Lottie (`engine.lottie.export_lottie_burst`), 512 x 512, 2 s.
docs/architecture.md. Errors are particle_sets.SetError (the HTTP layer maps its code). Free: nothing here spends.
"""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from pathlib import Path

from ..runtime import atomic
from .emoji_reactions import REACTION_SECONDS, handpicked_blob, handpicked_files
from .particle_sets import SetError, sets_dir

_LOCK = threading.Lock()
SIZE = 512                                                                      # the export's canvas = the dashboard's viewer
# The sliders, in the editor's order; every one is a ParticleParams field except size_var (re-spreads size_min / size_max around their middle)
# and origin_x / origin_y (the emitter's Position, the engine's origin).
SETTINGS = ("magnitude", "speed_var", "speed_pulse", "pulse_hz", "gravity", "vortex", "spin", "flip", "flip_var", "count", "spread",
            "particle_size", "size_var", "size_in_dur", "size_out_dur", "fade_start", "fade_dur", "fade_var",
            "morph_at", "morph_var", "morph_burst", "children", "child_size", "child_size_var", "seed",
            # the Trapcode Particular-style groups: Emitter, Particle, Physics, Aux
            "emitter", "origin_x", "origin_y", "emitter_w", "emitter_h", "emit_dur", "direction", "vel_dist", "lifetime", "life_var", "size_curve", "opacity_curve", "time_sampling",
            "air", "wind_x", "wind_y", "turbulence", "turb_scale", "turb_speed", "turb_size", "bounce", "floor_y", "slide", "aux_at", "aux_inherit", "aux_life")
# The neutral start: the engine's own defaults.
START = {"magnitude": 1.0, "speed_var": 0.0, "speed_pulse": 0.0, "pulse_hz": 2.0, "gravity": 1.0, "vortex": 0.0, "spin": 1.0, "flip": 0.0, "flip_var": 0.0,
         "count": 28, "spread": 360.0, "particle_size": 1.0, "size_var": 0.375, "size_in_dur": 0.0, "size_out_dur": 0.0,
         "fade_start": 0.0, "fade_dur": 0.0, "fade_var": 0.0, "morph_at": 0.0, "morph_var": 0.0, "morph_burst": 0, "children": 0, "child_size": 0.5, "child_size_var": 0.0, "seed": 1,
         "emitter": 0, "origin_x": 0.5, "origin_y": 0.5, "emitter_w": 0.0, "emitter_h": 0.0, "emit_dur": 0.0, "direction": 0.0, "vel_dist": 0.0, "lifetime": 1.6, "life_var": 0.0,
         "size_curve": 0, "opacity_curve": 0, "time_sampling": 0, "air": 0.0, "wind_x": 0.0, "wind_y": 0.0, "turbulence": 0.0, "turb_scale": 2.0,
         "turb_speed": 0.5, "turb_size": 0.0, "bounce": 0.0, "floor_y": 0.9, "slide": 0.0, "aux_at": 0, "aux_inherit": 0.0, "aux_life": 0.0}
# Presets in the style of Particular Designer's gallery: a preset only fills the sliders (the origin and the morph targets stay), on top of START.
PRESETS = {
    "Pop": {"magnitude": 1.4, "gravity": 0.6, "count": 24, "size_curve": 3, "opacity_curve": 0, "spin": 1.2},
    "Fountain": {"spread": 50.0, "magnitude": 2.0, "gravity": 1.6, "count": 40, "emitter": 3, "emitter_w": 0.1, "emit_dur": 1.0, "origin_y": 0.85, "life_var": 0.3},
    "Confetti": {"count": 60, "spread": 120.0, "magnitude": 1.6, "gravity": 0.5, "air": 1.0, "flip": 2.0, "flip_var": 0.5, "turbulence": 0.6, "particle_size": 0.6, "life_var": 0.3},
    "Snow": {"count": 40, "magnitude": 0.1, "gravity": 0.25, "emitter": 3, "emitter_w": 1.0, "origin_y": 0.05, "emit_dur": 1.2, "turbulence": 0.5, "turb_speed": 0.3, "spin": 0.3, "particle_size": 0.5},
    "Fireworks": {"count": 8, "magnitude": 1.2, "gravity": 0.4, "spread": 40.0, "children": 8, "aux_at": 1, "aux_inherit": 0.3, "aux_life": 0.7, "child_size": 0.4, "opacity_curve": 3},
    "Orbit": {"vortex": 1.6, "magnitude": 0.6, "gravity": 0.0, "count": 20, "emitter": 1, "emitter_w": 0.4, "time_sampling": 1},
    "Rain of hearts": {"count": 36, "magnitude": 0.2, "gravity": 0.9, "emitter": 3, "emitter_w": 0.9, "origin_y": 0.05, "emit_dur": 1.0, "bounce": 0.4, "floor_y": 0.92, "slide": 0.3, "wind_x": 0.3},
    "Trail": {"count": 10, "magnitude": 1.3, "gravity": 0.3, "children": 6, "aux_at": 3, "aux_life": 0.4, "child_size": 0.35, "size_curve": 2},
}
_SIZE_MID = 0.16                                                                # the middle of the engine's default size range (0.10 .. 0.22)


def _lib_path(out: Path) -> Path:
    return sets_dir(out) / "lottie" / "library.json"


def _read(out: Path) -> dict:
    f = _lib_path(out)
    try:
        d = json.loads(atomic.read_text(f)) if f.is_file() else {}
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict) or not isinstance(d.get("files"), dict):
        d = {"files": {}}
    return d


def clean_settings(settings) -> dict:
    """Only known keys, only numbers (400 otherwise). Values are clamped by the engine, not here."""
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise SetError("settings must be an object")
    bad = sorted(set(settings) - set(SETTINGS))
    if bad:
        raise SetError(f"unknown setting(s): {', '.join(bad)}")
    out = {}
    for k, v in settings.items():
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SetError(f"{k} must be a number")
        out[k] = v
    return out


def _targets(files: set, origin: str, targets) -> list:
    if targets is None:
        return []
    if not isinstance(targets, list) or any(not isinstance(t, str) for t in targets):
        raise SetError("morph_targets must be a list of file names")
    seen = []
    for t in targets:
        if t not in files:
            raise SetError(f"no Lottie file {t}", 404)
        if t != origin and t not in seen:
            seen.append(t)
    return seen


def params(settings=None):
    """The ParticleParams of one reaction: START, the saved / sent settings on top, 2 s, 512 px, centred. 400 for a bad value."""
    from ..engine import particles as _p
    s = {**START, **clean_settings(settings)}
    v = min(max(float(s.pop("size_var")), 0.0), 1.0)
    s["origin"] = (float(s.pop("origin_x")), float(s.pop("origin_y")))         # the emitter's Position (Particular), the engine's origin
    try:
        return _p.ParticleParams.from_dict({**s, "size_min": _SIZE_MID * (1 - v) if v < 1 else 0.02, "size_max": _SIZE_MID * (1 + v),
                                            "duration": REACTION_SECONDS, "size": SIZE}).clamped()
    except ValueError as ex:
        raise SetError(str(ex))


def listing(out: Path) -> dict:
    """GET lottie-reactions: {files: [{file, name, bytes, w, h, fps, frames, layers, images, base64, saved, settings, morph_targets}], start, keys}."""
    lib = _read(out)["files"]
    rows = []
    for f in handpicked_files():
        rec = lib.get(f["file"]) or {}
        rows.append({**f, "saved": bool(rec), "settings": {**START, **(rec.get("settings") or {})}, "morph_targets": rec.get("morph_targets") or []})
    return {"files": rows, "start": START, "keys": list(SETTINGS), "presets": PRESETS, "seconds": REACTION_SECONDS, "size": SIZE}


def save(out: Path, file: str, settings=None, morph_targets=None) -> dict:
    """POST lottie-reactions/{file}: keep its settings and morph targets. 404 for a file not in the drop zone, 400 for a bad setting."""
    names = {f["file"] for f in handpicked_files()}
    if file not in names:
        raise SetError(f"no Lottie file {file}", 404)
    clean = clean_settings(settings)
    params(clean)                                                               # a value the engine refuses is a 400 now, not at export
    tg = _targets(names, file, morph_targets)
    with _LOCK:
        d = _read(out)
        d["files"][file] = {"settings": clean, "morph_targets": tg, "updated": round(time.time(), 3)}
        f = _lib_path(out)
        f.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(f, json.dumps(d, ensure_ascii=False, indent=1))
    return {"file": file, "settings": {**START, **clean}, "morph_targets": tg}


def _doc(file: str) -> dict:
    try:
        return json.loads(handpicked_blob(file).decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SetError(f"{file} is not a readable Lottie", 409)


def compose(out: Path, file: str, settings=None, morph_targets=None) -> dict:
    """The reaction as ONE self-contained Lottie: the origin file and every morph target embedded once each. `settings` / `morph_targets` given =
    the editor's unsaved values (the viewer), None = the saved ones (the download). 404 for an unknown file, 400 for a bad setting or a too big burst."""
    from ..engine import lottie
    names = {f["file"] for f in handpicked_files()}
    if file not in names:
        raise SetError(f"no Lottie file {file}", 404)
    rec = _read(out)["files"].get(file) or {}
    s = (rec.get("settings") or {}) if settings is None else settings
    tg = _targets(names, file, (rec.get("morph_targets") or []) if morph_targets is None else morph_targets)
    try:
        return lottie.export_lottie_burst(params(s), _doc(file), [_doc(t) for t in tg], size=SIZE, name=Path(file).stem)
    except ValueError as ex:
        raise SetError(str(ex))


def export_zip(out: Path) -> bytes:
    """GET lottie-reactions.zip: every file of the drop zone composed with its saved settings, `<name>.json`, plus manifest.json
    {reactions: [{file, json, morph_targets, settings}], seconds, size}."""
    buf = io.BytesIO()
    man = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in handpicked_files():
            try:
                doc = compose(out, f["file"])
            except SetError:
                continue
            arc = f["file"]
            z.writestr(arc, json.dumps(doc, separators=(",", ":")))
            rec = _read(out)["files"].get(f["file"]) or {}
            man.append({"file": f["file"], "json": arc, "morph_targets": rec.get("morph_targets") or [], "settings": {**START, **(rec.get("settings") or {})}})
        z.writestr("manifest.json", json.dumps({"reactions": man, "seconds": REACTION_SECONDS, "size": SIZE}, ensure_ascii=False, indent=1))
    return buf.getvalue()


# ---------- super reaction (Reactions 2.0): a dropped file played over the WHOLE screen. For now the sources are the files in
# lottie, listed fresh on every request by name (drop a file = a new super reaction). engine/super_reaction.py; docs/architecture.md.
def super_listing() -> dict:
    """GET /api/particles/super: {files: [{file, name, w, h, fps, frames}], buckets {name: width/height}, glow_modes, keys, start}."""
    from ..engine import super_reaction as sr
    return {"files": [{k: f[k] for k in ("file", "name", "w", "h", "fps", "frames")} for f in handpicked_files()],
            "buckets": {k: round(v, 4) for k, v in sr.BUCKETS.items()}, "glow_modes": list(sr.GLOW_MODES),
            "keys": list(sr.SUPER_KEYS), "start": sr.SUPER_START}


def super_compose(out: Path, file: str, aspect, *, glow: str = "auto", color=None, colors=None, settings=None) -> dict:
    """POST /api/particles/super: {file, name, bucket, aspect, glow, color, lottie}. The file's saved Lottie-style look (sizes, spin, zoom; the neutral
    defaults unsaved) rises from the bottom of a canvas shaped like the screen (`aspect` = width / height), lit by `glow`. Free; nothing is stored.
    404 for a file not in the drop zone, 400 for a bad option."""
    from ..engine import super_reaction as sr
    if file not in {f["file"] for f in handpicked_files()}:
        raise SetError(f"no Lottie file {file}", 404)
    if isinstance(aspect, bool) or not isinstance(aspect, (int, float)):
        raise SetError("aspect must be a number (the screen's width / height)")
    rec = _read(out)["files"].get(file) or {}
    try:
        doc = sr.export_super(params(rec.get("settings") or {}), float(aspect), settings=settings, glow=str(glow), color=color, colors=colors,
                              source=_doc(file), name=Path(file).stem)
    except (TypeError, ValueError) as ex:
        raise SetError(str(ex))
    meta = doc.pop("stickerloom")
    return {"file": file, "name": Path(file).stem, "bucket": meta["bucket"], "aspect": meta["aspect"], "glow": meta["glow"], "color": meta["color"], "lottie": doc}

