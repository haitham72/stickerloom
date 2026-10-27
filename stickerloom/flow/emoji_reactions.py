"""Reaction effects (Create > + New, console/ui reactions.js): one bank emoji -> its fixed motion, flying as the literal glyph -> a Lottie file.

No set, no pack, no sticker: the sprite IS the emoji glyph (`engine.emoji_glyph.render_emoji`), flown by the simulator (`engine.particles`), exported by
`engine.lottie`. Storage is out/particles/emoji/<token>/ (never a P### folder, so the set scans never see it): renders/L###.lottie.json + .png + .webp and
emoji.json (lottie[], history). docs/architecture.md ("Reactions"). Errors are particle_sets.SetError (the HTTP handler maps its code). Free: nothing here spends.
"""
from __future__ import annotations

import io
import json
import random
import threading
import time
import uuid
import zipfile
from pathlib import Path

from ..runtime import atomic
from .particle_sets import SetError, hist, prune_previews, sets_dir

_LOCK = threading.Lock()

EMOJI_OPTIONS = {"size_in_dur": 0.4, "size_out_dur": 0.4, "hue_var": 25.0}    # the screen's defaults; 0 / 0 / 0 is the plain look
EMOJI_SIM = ("magnitude", "gravity", "vortex", "spin", "count", "spread", "fade_start", "fade_dur", "fade_var")    # the Simulation group: ..., Fade out start, Fade out duration, Fade out variation
REACTION_SECONDS = 2.0                                                          # every reaction is 2 s and empty at its end, whatever the settings
# Where every emoji starts: the editor's first values, Reset's target, and the look
# a batch gives an emoji with no preset. The motion stays the emoji's own; only these values are normalised. GET emoji-motions hands them to the screen.
EMOJI_START = {"magnitude": 0.7, "gravity": 1.0, "vortex": 0.0, "spin": 1.0, "count": 28, "spread": 360.0,
               "particle_size": 0.3, "size_var": 0.38, "size_in_dur": 0.4, "size_out_dur": 0.4, "hue_var": 25.0, "fade_start": 0.0, "fade_dur": 0.0, "fade_var": 0.0}
EMOJI_PX = 160                                                                  # the glyph's longest side: sharp at the biggest a particle is drawn on 512


def emoji_dir(out: Path, token: str) -> Path:
    return sets_dir(out) / "emoji" / token                                      # never a P### name: the set scans skip it


def emoji_token(token) -> tuple[str, str]:
    """(token, glyph) of one bank emoji, named by its token ("kiss") or by the glyph itself (`actions.token_for_emoji`).
    409 when the bank has neither (the bank is fixed: an unknown emoji is a conflict with it, not a malformed request)."""
    from ..generation import actions as bank
    raw = str(token or "").strip()
    gen = bank.generation_bank()
    t = raw.lower() if raw.lower() in gen else (bank.token_for_emoji(raw) if raw else None)
    if not t or t not in gen:
        raise SetError(f"{raw or 'nothing'} is not an emoji of the bank", 409)
    return t, gen[t]["emoji"]


def emoji_burst(token, *, preset=None, seed=1, size_in_dur=None, size_out_dur=None, hue_var=None, particle_size=None, size_var=None, sim=None, size=512) -> tuple[str, str, str, object]:
    """(token, glyph, preset name, ParticleParams) of one bank emoji: its fixed motion (`emoji_motion.motion_for_token`), or a
    preset override (the override takes the preset's own look: the token's pinned values belong to its own preset), with the
    options on top (None = the screen's default). `particle_size` is how big every emoji looks (0.25-3, None = the motion's own, 1);
    `size_var` is how much the emojis differ in size (0 = all the same, 1 = from almost nothing to twice the average; None = the
    motion's own range): the motion's size range is re-spread around its own average. `sim` overrides the motion's own simulation
    (EMOJI_SIM: magnitude, gravity, vortex, spin, count, spread, fade_start, fade_dur, fade_var; a key left out keeps the motion's value; the engine clamps). Deterministic: same emoji + options + seed
    -> the same params. 400 for a bad option."""
    from ..engine import emoji_motion as em, particles as _p
    t, glyph = emoji_token(token)
    motion = em.motion_for_token(t)
    name = str(preset or motion["preset"])
    if name not in _p.PRESETS:
        raise SetError(f"unknown preset {name!r}; choose one of {', '.join(_p.PRESETS)}")
    over = dict(motion["params"]) if name == motion["preset"] else {}
    opts = {k: (EMOJI_OPTIONS[k] if v is None else v) for k, v in (("size_in_dur", size_in_dur), ("size_out_dur", size_out_dur), ("hue_var", hue_var))}
    if particle_size is not None:
        opts["particle_size"] = particle_size
    if sim is not None and not isinstance(sim, dict):
        raise SetError("sim must be an object")
    bad = sorted(set(sim or {}) - set(EMOJI_SIM))
    if bad:
        raise SetError(f"unknown simulation setting(s): {', '.join(bad)}")
    over.update({k: v for k, v in (sim or {}).items() if v is not None})
    for k, v in (*opts.items(), *((k, v) for k, v in over.items() if k in EMOJI_SIM), ("seed", seed), ("size", size), *((("size_var", size_var),) if size_var is not None else ())):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SetError(f"{k} must be a number")
    try:
        p = _p.preset(name, **over, **opts, seed=int(seed), size=int(size), duration=REACTION_SECONDS)
        if size_var is not None:
            mid, v = (p.size_min + p.size_max) / 2, min(max(float(size_var), 0.0), 1.0)
            p = _p.ParticleParams.from_dict({**p.to_dict(), "origin": p.origin, "size_min": mid * (1 - v), "size_max": mid * (1 + v)}).clamped()
        return t, glyph, name, p
    except ValueError as ex:
        raise SetError(str(ex))


def _glyph(glyph: str):
    from ..engine import emoji_glyph
    try:
        return emoji_glyph.render_emoji(glyph, EMOJI_PX)
    except RuntimeError as ex:                                                   # no colour emoji font on this machine
        raise SetError(str(ex), 503)


def emoji_preview(out: Path, token, *, preset=None, seed=1, size_in_dur=None, size_out_dur=None, hue_var=None, particle_size=None, size_var=None, sim=None, size=256, fit=False) -> dict:
    """The emoji's burst as a small looping WebP for the screen, by the same simulator as the Lottie (free). Cached by what it was
    made from, so the same options are the same file. `fit` (the bank's hover): the whole burst zoomed to stay inside its square, scaled, never cut."""
    import hashlib
    from ..engine import particles as _p
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not 64 <= size <= 512:
        raise SetError("size must be a whole number from 64 to 512")
    t, glyph, name, p = emoji_burst(token, preset=preset, seed=seed, size_in_dur=size_in_dur, size_out_dur=size_out_dur, hue_var=hue_var,
                                  particle_size=particle_size, size_var=size_var, sim=sim, size=int(size))
    img = _glyph(glyph)
    key = hashlib.sha256(json.dumps({"p": p.to_dict(), "glyph": glyph}, sort_keys=True).encode() + img.tobytes()).hexdigest()[:16]
    f = emoji_dir(out, t) / "previews" / f"{key}-{int(size)}{'-fit' if fit else ''}.webp"
    if not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(f, _p.preview_webp(_p.simulate([img], p, fit=bool(fit)), size=int(size)))
        prune_previews(f.parent)                                                 # a cache: only the newest PREVIEW_KEEP stay
    return {"token": t, "emoji": glyph, "preset": name, "params": p.to_dict(), "url": f"/out/particles/emoji/{t}/previews/{f.name}"}


def export_emoji_lottie(out: Path, token, *, preset=None, seed=1, size_in_dur=None, size_out_dur=None, hue_var=None, particle_size=None, size_var=None, sim=None, size=512, user: str = "local") -> dict:
    """The end of the Reaction effects flow: one bank emoji's fixed motion, flying as the literal glyph, as a playable Lottie JSON
    (web only; Telegram stays WebM). Stores `out/particles/emoji/<token>/renders/L###.lottie.json`, `L###.png` (the glyph asset,
    for inspection) and `L###.webp` (the same burst as a 256 px loop, for its card) and appends to that emoji's `emoji.json` (`lottie[]`, history `LOTTIE`). Same emoji + options + seed -> the same
    JSON bytes on the same machine (the glyph comes from its emoji font). 409 for an emoji outside the bank. Free."""
    from ..engine import lottie, particles as _p
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not 128 <= size <= 1024:
        raise SetError("size must be a whole number from 128 to 1024")
    t, glyph, name, p = emoji_burst(token, preset=preset, seed=seed, size_in_dur=size_in_dur, size_out_dur=size_out_dur, hue_var=hue_var,
                                  particle_size=particle_size, size_var=size_var, sim=sim, size=int(size))
    settings = {k: v for k, v in (("preset", preset), ("particle_size", particle_size), ("size_var", size_var), ("size_in_dur", size_in_dur),
                                  ("size_out_dur", size_out_dur), ("hue_var", hue_var), *((sim or {}).items())) if v is not None and v != ""}
    img = _glyph(glyph)
    png = _p.png_bytes(img)
    doc = lottie.export_lottie(p, png, size=p.size)
    loop = _p.preview_webp(_p.simulate([img], _p.ParticleParams.from_dict({**p.to_dict(), "size": 256})), size=256)
    doc["nm"] = f"{glyph} {t} ({name})"
    data = json.dumps(doc, separators=(",", ":"))
    d = emoji_dir(out, t)
    with _LOCK:                                                                  # one writer per emoji.json
        rec_f = d / "emoji.json"
        rec = json.loads(atomic.read_text(rec_f)) if rec_f.is_file() else {"token": t, "emoji": glyph, "lottie": [], "history": []}
        nums = [int(str(r.get("id"))[1:]) for r in rec.get("lottie") or [] if str(r.get("id", ""))[1:].isdigit()]
        rid = f"L{max(nums + [0]) + 1:03d}"
        (d / "renders").mkdir(parents=True, exist_ok=True)
        atomic.write_text(d / f"renders/{rid}.lottie.json", data)
        atomic.write_bytes(d / f"renders/{rid}.png", png)
        atomic.write_bytes(d / f"renders/{rid}.webp", loop)
        rec.setdefault("lottie", []).append({"id": rid, "preset": name, "params": p.to_dict(), "settings": settings, "file": f"renders/{rid}.lottie.json",
                                             "glyph": f"renders/{rid}.png", "preview": f"renders/{rid}.webp", "bytes": len(data), "created": round(time.time(), 3)})
        hist(rec, user, "LOTTIE", f"exported {rid} as Lottie ({name})", {"preset": name, "seed": p.seed})
        atomic.write_text(rec_f, json.dumps(rec, ensure_ascii=False, indent=1))
    return {"id": rid, "token": t, "emoji": glyph, "preset": name, "params": p.to_dict(), "settings": settings, "file": f"renders/{rid}.lottie.json",
            "url": f"/out/particles/emoji/{t}/renders/{rid}.lottie.json", "preview": f"/out/particles/emoji/{t}/renders/{rid}.webp",
            "layers": len(doc["layers"]), "assets": len(doc["assets"]), "bytes": len(data)}


def emoji_lotties(out: Path) -> list[dict]:
    """Every created reaction (one per Lottie export), newest first: {id, token, emoji, preset, params, url, preview, bytes, created}.
    `preview` is the export's own looping WebP (None for an export made before it was kept). Read-only, free."""
    rows = []
    for rec_f in sorted((sets_dir(out) / "emoji").glob("*/emoji.json")):
        try:
            rec = json.loads(atomic.read_text(rec_f))
        except (OSError, ValueError):
            continue
        t, base = rec.get("token") or rec_f.parent.name, f"/out/particles/emoji/{rec_f.parent.name}/"
        for x in rec.get("lottie") or []:
            pv = x.get("preview")
            rows.append({"id": x.get("id"), "token": t, "emoji": rec.get("emoji", ""), "preset": x.get("preset"), "params": x.get("params") or {},
                         "settings": x.get("settings"),
                         "url": base + x["file"], "preview": base + pv if pv and (rec_f.parent / pv).is_file() else None,
                         "bytes": x.get("bytes"), "created": x.get("created")})
    rows.sort(key=lambda r: (r.get("created") or 0, r["token"], r["id"] or ""), reverse=True)
    return rows


# ---------- presets: a saved set of editor settings ("Save preset"), applied to many emojis at once ----------
# A preset holds the request settings without the emoji: `preset` (a motion override; absent = each emoji keeps its own motion), the Simulation keys
# that were moved (EMOJI_SIM) and the Shape values. Applied to an emoji it is exactly what the editor would have sent for it. Stored in
# out/particles/emoji/presets.json (a file, so the per-emoji folder scan never sees it); ids preset01, preset02 ... are never reused.
PRESET_KEYS = ("preset", "particle_size", "size_var", "size_in_dur", "size_out_dur", "hue_var", *EMOJI_SIM)
MANY_MAX = 24                                                                   # emojis per Create: one request, a few seconds of simulation each


def _presets_file(out: Path) -> Path:
    return sets_dir(out) / "emoji" / "presets.json"


def _read_presets(out: Path) -> dict:
    f = _presets_file(out)
    return json.loads(atomic.read_text(f)) if f.is_file() else {"presets": [], "next": 1}


def _kwargs(settings: dict) -> dict:
    """A preset's (or a request's) settings as emoji_burst keywords: the Simulation keys go into `sim`."""
    s = dict(settings or {})
    return {**{k: s[k] for k in PRESET_KEYS if k in s and k not in EMOJI_SIM}, "sim": {k: s[k] for k in EMOJI_SIM if s.get(k) is not None}}


def _motion_of(settings: dict, token: str | None) -> str:
    """The motion a preset belongs to: its own override, else the motion of the emoji it was made on (presets are filed by motion)."""
    try:
        return emoji_burst(token or "happy", **_kwargs(settings))[2]
    except SetError:
        return str(settings.get("preset") or "")


def emoji_presets(out: Path) -> list[dict]:
    """The saved presets, oldest first: [{id, name, motion, token, settings, created}]; one saved before presets were filed by motion gets its motion
    from its settings (or `happy`'s). Read-only, free."""
    return [{**p, "motion": p.get("motion") or _motion_of(p.get("settings") or {}, p.get("token"))} for p in _read_presets(out)["presets"]]


def _clean_settings(settings) -> dict:
    if not isinstance(settings, dict):
        raise SetError("settings must be an object")
    bad = sorted(set(settings) - set(PRESET_KEYS))
    if bad:
        raise SetError(f"unknown preset setting(s): {', '.join(bad)}")
    return {k: v for k, v in settings.items() if v is not None and v != ""}


def save_emoji_preset(out: Path, settings: dict, *, name=None, token=None, user: str = "local") -> dict:
    """Save the editor's settings as the next preset (Preset 01, Preset 02 ...), filed under its motion (the override, else `token`'s own). Strict:
    an unknown key or a bad value is a 400, checked by building the burst once, so a preset that saves always applies."""
    clean = _clean_settings(settings)
    t = emoji_token(token)[0] if token else None
    motion = emoji_burst(t or "happy", **_kwargs(clean))[2]
    with _LOCK:
        data = _read_presets(out)
        n = int(data.get("next") or 1)
        rec = {"id": f"preset{n:02d}", "name": (str(name).strip()[:40] if name else "") or f"Preset {n:02d}", "motion": motion, "token": t,
               "settings": clean, "created": round(time.time(), 3), "user": user}
        data["presets"].append(rec)
        data["next"] = n + 1
        _presets_file(out).parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(_presets_file(out), json.dumps(data, ensure_ascii=False, indent=1))
    return rec


def update_emoji_preset(out: Path, pid: str, *, settings=None, name=None, token=None) -> dict:
    """Change one preset in place (Presets > Update, or a new name): its settings and so its motion, and/or its name. 404 for an unknown id."""
    clean = _clean_settings(settings) if settings is not None else None
    with _LOCK:
        data = _read_presets(out)
        rec = next((p for p in data["presets"] if p["id"] == pid), None)
        if rec is None:
            raise SetError(f"no preset {pid}", 404)
        if clean is not None:
            t = emoji_token(token)[0] if token else rec.get("token")
            rec.update(settings=clean, token=t, motion=emoji_burst(t or "happy", **_kwargs(clean))[2], updated=round(time.time(), 3))
        if name is not None and str(name).strip():
            rec["name"] = str(name).strip()[:40]
        atomic.write_text(_presets_file(out), json.dumps(data, ensure_ascii=False, indent=1))
    return rec


def delete_emoji_preset(out: Path, pid: str) -> dict:
    """Remove one preset (the reactions made with it keep their own params). 404 for an unknown id."""
    with _LOCK:
        data = _read_presets(out)
        keep = [p for p in data["presets"] if p["id"] != pid]
        if len(keep) == len(data["presets"]):
            raise SetError(f"no preset {pid}", 404)
        data["presets"] = keep
        atomic.write_text(_presets_file(out), json.dumps(data, ensure_ascii=False, indent=1))
    return {"deleted": pid, "presets": keep}


def export_many(out: Path, tokens, *, preset_id=None, user: str = "local") -> dict:
    """Create one reaction per emoji with the same settings: a saved preset's, or none (each emoji's own motion with the screen's defaults).
    One emoji that fails does not stop the others: {reactions: [...as export_emoji_lottie, newest last], errors: [{token, error}]}."""
    if not isinstance(tokens, list) or not tokens:
        raise SetError("tokens must be a non-empty list")
    if len(tokens) > MANY_MAX:
        raise SetError(f"at most {MANY_MAX} emojis at once")
    settings = {}
    if preset_id:
        settings = next((p["settings"] for p in emoji_presets(out) if p["id"] == preset_id), None)
        if settings is None:
            raise SetError(f"no preset {preset_id}", 404)
    made, errors = [], []
    for token in dict.fromkeys(str(t) for t in tokens):                    # order kept, repeats dropped
        try:
            made.append(export_emoji_lottie(out, token, user=user, **_kwargs(settings or EMOJI_START)))
        except SetError as ex:
            errors.append({"token": token, "error": str(ex)})
    return {"reactions": made, "errors": errors, "preset": preset_id or None}


# ---------- batch create in the background: one run at a time, its state a file,
# progress and the tokens made so far readable while it runs, Cancel between two emojis. mode: "start" (each emoji's own motion from EMOJI_START),
# "preset" (one preset for all), "random" (each emoji gets a random preset of ITS OWN motion; none of that motion = the start look).
MODES = ("start", "preset", "random")
_BLIVE: dict = {}                       # run id -> {"cancel": bool} for the run this process is doing


def _batch_path(out: Path, bid: str) -> Path:
    return sets_dir(out) / "emoji" / "batches" / f"{bid}.json"


def _bsave(out: Path, st: dict) -> None:
    p = _batch_path(out, st["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(st, ensure_ascii=False))


def all_tokens() -> list[str]:
    from ..generation import actions as bank
    return list(bank.generation_bank())


def start_batch(out: Path, tokens=None, *, mode: str = "start", preset_id=None, only_missing: bool = False, user: str = "local") -> dict:
    """Queue one batch create and answer its state at once ({id, state: running, total, done: 0, ...}). `tokens` None = the whole bank;
    `only_missing` drops the emojis that already have a reaction. 409 while another run is going."""
    if mode not in MODES:
        raise SetError(f"mode must be one of {', '.join(MODES)}")
    presets = emoji_presets(out)
    if mode == "preset" and not any(p["id"] == preset_id for p in presets):
        raise SetError(f"no preset {preset_id}", 404)
    if mode == "random" and not presets:
        raise SetError("there is no saved preset to pick from", 409)
    if tokens is None:
        todo = all_tokens()
    elif isinstance(tokens, list) and tokens:
        todo = [emoji_token(t)[0] for t in dict.fromkeys(str(t) for t in tokens)]
    else:
        raise SetError("tokens must be a non-empty list, or left out for every emoji")
    if only_missing:
        have = {r["token"] for r in emoji_lotties(out)}
        todo = [t for t in todo if t not in have]
    if not todo:
        raise SetError("nothing to create: every chosen emoji already has a reaction", 409)
    with _LOCK:
        if _BLIVE:
            raise SetError("a batch create is already running: wait for it or cancel it", 409)
        bid = f"B{int(time.time() * 1000):x}{uuid.uuid4().hex[:4]}"
        _BLIVE[bid] = {"cancel": False}
    st = {"id": bid, "state": "running", "mode": mode, "preset_id": preset_id, "total": len(todo), "done": 0, "made": [], "errors": [],
          "user": user, "started": round(time.time(), 3)}
    _bsave(out, st)
    threading.Thread(target=_brun, args=(Path(out), st, todo, presets), name="emoji-batch", daemon=True).start()
    return st


def _brun(out: Path, st: dict, todo: list, presets: list) -> None:
    rnd = random.Random(st["id"])                                             # the same run picks the same way
    by_motion: dict = {}
    for p in presets:
        by_motion.setdefault(p["motion"], []).append(p)
    one = next((p for p in presets if p["id"] == st.get("preset_id")), None)
    try:
        for t in todo:
            if _BLIVE.get(st["id"], {}).get("cancel"):
                st["state"] = "cancelled"
                break
            try:
                if st["mode"] == "preset":
                    settings, pick = one["settings"], one["id"]
                elif st["mode"] == "random":
                    own = emoji_burst(t, **_kwargs(EMOJI_START))[2]
                    cand = by_motion.get(own) or []
                    p = rnd.choice(cand) if cand else None
                    settings, pick = (p["settings"], p["id"]) if p else (EMOJI_START, None)
                else:
                    settings, pick = EMOJI_START, None
                r = export_emoji_lottie(out, t, user=st["user"], **_kwargs(settings))
                st["made"].append({"token": t, "id": r["id"], "preset_id": pick})
            except Exception as ex:                                          # one emoji never stops the others
                st["errors"].append({"token": t, "error": str(ex)[:200]})
            st["done"] += 1
            _bsave(out, st)
        if st["state"] == "running":
            st["state"] = "done"
    finally:
        st["finished"] = round(time.time(), 3)
        _bsave(out, st)
        with _LOCK:
            _BLIVE.pop(st["id"], None)


def batch_state(out: Path, bid: str) -> dict:
    """One run's state; a run this process is not doing any more (the server restarted) reads as stopped. 404 for an unknown id."""
    import re
    if not re.fullmatch(r"B[0-9a-f]+", str(bid or "")):
        raise SetError(f"no batch {bid}", 404)
    try:
        st = json.loads(atomic.read_text(_batch_path(out, bid)))
    except (OSError, ValueError):
        raise SetError(f"no batch {bid}", 404)
    if st.get("state") == "running" and bid not in _BLIVE:
        st["state"] = "stopped"
        st["note"] = "The server restarted during this run; what was made is kept, start again for the rest"
    return st


def batches(out: Path, limit: int = 5) -> list[dict]:
    """The newest runs first (without their long made / errors lists), so a reloaded screen finds a run still going."""
    d = sets_dir(out) / "emoji" / "batches"
    rows = []
    for f in sorted(d.glob("B*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit] if d.is_dir() else []:
        st = batch_state(out, f.stem)
        rows.append({k: v for k, v in st.items() if k not in ("made", "errors")} | {"errors": len(st.get("errors") or [])})
    return rows


def cancel_batch(out: Path, bid: str) -> dict:
    st = batch_state(out, bid)
    if bid in _BLIVE:
        _BLIVE[bid]["cancel"] = True
        st["cancelling"] = True
    return st


def export_zip(out: Path, tokens=None) -> tuple[bytes, int]:
    """Export: All | Selected. The newest Lottie of every chosen emoji (None = every emoji that has one) in one zip, `<token>.lottie.json`, plus
    manifest.json [{token, emoji, id, preset, file}] and the emojis asked for that have none (`missing`). (zip bytes, how many)."""
    newest: dict = {}
    for r in emoji_lotties(out):                                              # newest first: the first seen per token is its newest
        newest.setdefault(r["token"], r)
    want = list(newest) if tokens is None else [str(t) for t in dict.fromkeys(tokens)]
    rows, missing, buf = [], [], io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in want:
            r = newest.get(t)
            f = emoji_dir(out, t) / "renders" / f"{r['id']}.lottie.json" if r else None
            if not f or not f.is_file():
                missing.append(t)
                continue
            z.writestr(f"{t}.lottie.json", f.read_bytes())
            rows.append({"token": t, "emoji": r["emoji"], "id": r["id"], "preset": r["preset"], "file": f"{t}.lottie.json"})
        z.writestr("manifest.json", json.dumps({"reactions": rows, "missing": missing}, ensure_ascii=False, indent=1))
    if not rows:
        raise SetError("none of these emojis has a reaction yet", 409)
    return buf.getvalue(), len(rows)


# ---------- the animation preset library. A few presets, each a
# variation of one motion: id `{motion}_{NN}` (burst_01, burst_02 ...), exported as `{motion}_{NN}.json`, a Lottie with NO picture and NO base64:
# one external image asset `emoji` the chat app fills with the emoji it shows. Every bank emoji holds one preset (`assign`; unassigned = its own
# motion's first variation), so the wall's hover plays that style with that emoji. One file: out/particles/emoji/library.json.
LIB_MOTIONS = ("burst", "fountain", "vortex", "rain", "confetti")
_VARIATIONS = (("soft", {"magnitude": 0.5, "count": 18, "particle_size": 0.25}), ("normal", {}), ("big", {"magnitude": 1.0, "count": 40, "particle_size": 0.4}))
LIB_KEYS = tuple(k for k in PRESET_KEYS if k not in ("preset", "hue_var"))      # a preset's settings; its motion is its group, hue needs the picture


def _lib_path(out: Path) -> Path:
    return sets_dir(out) / "emoji" / "library.json"


def _seed() -> list[dict]:
    now = round(time.time(), 3)
    base = {k: v for k, v in EMOJI_START.items() if k in LIB_KEYS}
    return [{"id": f"{m}_{n:02d}", "motion": m, "n": n, "name": f"{m.capitalize()} {label}", "settings": {**base, **extra}, "created": now}
            for m in LIB_MOTIONS for n, (label, extra) in enumerate(_VARIATIONS, 1)]


def _lib_read(out: Path) -> dict:
    f = _lib_path(out)
    if f.is_file():
        try:
            d = json.loads(atomic.read_text(f))
            if isinstance(d, dict) and isinstance(d.get("presets"), list):
                d.setdefault("assign", {})
                d.setdefault("handpicked", {})
                return d
        except (OSError, ValueError):
            pass
    d = {"presets": _seed(), "assign": {}, "handpicked": {}}          # a fresh library has every key the readers use
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(f, json.dumps(d, ensure_ascii=False, indent=1))
    return d


def _lib_write(out: Path, d: dict) -> None:
    atomic.write_text(_lib_path(out), json.dumps(d, ensure_ascii=False, indent=1))


def library(out: Path) -> dict:
    """{presets: [{id, motion, n, name, settings, created}] by motion then number, assign: {token: preset id} (only the explicit ones), motions}."""
    d = _lib_read(out)
    order = {m: i for i, m in enumerate(LIB_MOTIONS)}
    return {"presets": sorted(d["presets"], key=lambda p: (order.get(p["motion"], 99), p["n"])), "assign": d["assign"], "motions": list(LIB_MOTIONS), "handpicked": d["handpicked"]}


def _lib_settings(settings) -> dict:
    if not isinstance(settings, dict):
        raise SetError("settings must be an object")
    bad = sorted(set(settings) - set(LIB_KEYS) - {"preset", "hue_var"})
    if bad:
        raise SetError(f"unknown preset setting(s): {', '.join(bad)}")
    return {k: v for k, v in settings.items() if k in LIB_KEYS and v is not None and v != ""}


def _lib_check(motion: str, clean: dict) -> None:
    """A preset that saves always plays: built once on `happy`, in its motion."""
    emoji_burst("happy", **_kwargs({**clean, "preset": motion}))


def lib_preset(out: Path, pid: str) -> dict:
    p = next((x for x in _lib_read(out)["presets"] if x["id"] == pid), None)
    if p is None:
        raise SetError(f"no preset {pid}", 404)
    return p


def add_lib_preset(out: Path, motion: str, settings: dict, *, name=None) -> dict:
    """A new variation of `motion`: the next number of that motion (burst_04 after burst_03), never a reused one."""
    if motion not in LIB_MOTIONS:
        raise SetError(f"motion must be one of {', '.join(LIB_MOTIONS)}")
    clean = _lib_settings(settings)
    _lib_check(motion, clean)
    with _LOCK:
        d = _lib_read(out)
        n = max([p["n"] for p in d["presets"] if p["motion"] == motion] + [d.get("last", {}).get(motion, 0)]) + 1
        if n > 99:
            raise SetError(f"{motion} already has 99 variations", 409)
        rec = {"id": f"{motion}_{n:02d}", "motion": motion, "n": n, "name": (str(name).strip()[:40] if name else "") or f"{motion.capitalize()} {n:02d}",
               "settings": clean, "created": round(time.time(), 3)}
        d["presets"].append(rec)
        d.setdefault("last", {})[motion] = n
        _lib_write(out, d)
    return rec


def update_lib_preset(out: Path, pid: str, *, settings=None, name=None) -> dict:
    with _LOCK:
        d = _lib_read(out)
        rec = next((p for p in d["presets"] if p["id"] == pid), None)
        if rec is None:
            raise SetError(f"no preset {pid}", 404)
        if settings is not None:
            clean = _lib_settings(settings)
            _lib_check(rec["motion"], clean)
            rec["settings"] = clean
        if name is not None and str(name).strip():
            rec["name"] = str(name).strip()[:40]
        rec["updated"] = round(time.time(), 3)
        _lib_write(out, d)
    return rec


def delete_lib_presets(out: Path, ids) -> dict:
    """Remove several presets at once (a motion's x removes all its variations; Select + Remove): their emojis go back to their motion's first
    variation; their numbers are never reused. At least one preset stays (409). 404 when an id is unknown."""
    ids = [str(x) for x in dict.fromkeys(ids or [])]
    if not ids:
        raise SetError("ids must be a non-empty list")
    with _LOCK:
        d = _lib_read(out)
        have = {p["id"]: p for p in d["presets"]}
        bad = [x for x in ids if x not in have]
        if bad:
            raise SetError(f"no preset {', '.join(bad)}", 404)
        if len(ids) >= len(d["presets"]):
            raise SetError("at least one preset must stay", 409)
        last = d.setdefault("last", {})                    # the highest number each motion ever had, so a number is never reused
        for x in ids:
            m = have[x]["motion"]
            last[m] = max(last.get(m, 0), have[x]["n"])
        d["presets"] = [p for p in d["presets"] if p["id"] not in ids]
        freed = [t for t, v in d["assign"].items() if v in ids]
        for t in freed:
            del d["assign"][t]
        _lib_write(out, d)
    return {"deleted": ids, "unassigned": len(freed)}


def delete_lib_preset(out: Path, pid: str) -> dict:
    """Remove one preset (see delete_lib_presets)."""
    r = delete_lib_presets(out, [pid])
    return {"deleted": pid, "unassigned": r["unassigned"]}


def assign_lib(out: Path, tokens=None, preset_id=None) -> dict:
    """Give emojis a preset: one preset (`preset_id`), or `random` = a random variation of each emoji's OWN motion (any preset when its motion has
    none). tokens None = every bank emoji. Instant: nothing is rendered. {assigned: n}."""
    from ..engine import emoji_motion as em
    d = _lib_read(out)
    todo = all_tokens() if tokens is None else [emoji_token(t)[0] for t in dict.fromkeys(str(t) for t in (tokens or []))]
    if not todo:
        raise SetError("tokens must be a non-empty list, or left out for every emoji")
    if preset_id != "random" and not any(p["id"] == preset_id for p in d["presets"]):
        raise SetError(f"no preset {preset_id}", 404)
    rnd = random.Random("|".join(todo) + str(len(d["presets"])))
    by = {}
    for p in d["presets"]:
        by.setdefault(p["motion"], []).append(p["id"])
    with _LOCK:
        d = _lib_read(out)
        for t in todo:
            if preset_id == "random":
                try:
                    own = em.motion_for_token(t)["preset"]
                except Exception:
                    own = None
                pool = by.get(own) or [p["id"] for p in d["presets"]]
                d["assign"][t] = rnd.choice(pool)
            else:
                d["assign"][t] = preset_id
        _lib_write(out, d)
    return {"assigned": len(todo)}


def lib_lottie(out: Path, pid: str, size: int = 512) -> bytes:
    """`{motion}_{NN}.json`: the preset as an emoji-free Lottie (engine.lottie slot export: one external `emoji` asset, no base64). Not stored."""
    from ..engine import lottie
    rec = lib_preset(out, pid)
    p = emoji_burst("happy", **_kwargs({**rec["settings"], "preset": rec["motion"]}), size=int(size))[3]
    doc = lottie.export_lottie(p, slot=True, size=int(size))
    doc["nm"] = rec["id"]
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def unicode_key(glyph: str) -> str:
    """The app's key for an emoji: lowercase hex code points joined by '-', without U+FE0F (so ❤️ and ❤ are the same key): 🤣 -> 1f923."""
    return "-".join(f"{ord(ch):x}" for ch in str(glyph) if ord(ch) != 0xFE0F)


def _by_unicode(assign: dict) -> dict:
    """{unicode key: preset id} for every bank emoji (manifest.json `unicode`, what a chat app looks up)."""
    from ..generation import actions as bank
    gen = bank.generation_bank()
    return {unicode_key(gen[t]["emoji"]): pid for t, pid in assign.items() if t in gen and gen[t].get("emoji")}


def lib_zip(out: Path) -> bytes:
    """Every preset as `{id}.json` + manifest.json {presets: [{id, motion, variation, name, file}], assign: {token: id} for every bank emoji, slot}."""
    from ..engine import emoji_motion as em, lottie
    lib = library(out)
    first = {}
    for p in lib["presets"]:
        first.setdefault(p["motion"], p["id"])
    assign = {}
    for t in all_tokens():
        if t in lib["assign"]:
            assign[t] = lib["assign"][t]
            continue
        try:
            own = em.motion_for_token(t)["preset"]
        except Exception:
            own = None
        assign[t] = first.get(own) or lib["presets"][0]["id"]
    hp = {t: v for t, v in (lib.get("handpicked") or {}).items() if isinstance(v, dict) and v.get("origin")}
    used = sorted({v["origin"] for v in hp.values()} | {v["morph"] for v in hp.values() if v.get("morph")})
    blobs = {}
    base = handpicked_dir()
    for name in used:
        f = base / name
        try:
            if f.is_file() and f.stat().st_size <= 2 * 1024 * 1024:
                blobs[name] = f.read_bytes()
        except OSError:
            pass
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in lib["presets"]:
            z.writestr(f"{p['id']}.json", lib_lottie(out, p["id"]))
        for name, blob in blobs.items():
            z.writestr(f"handpicked/{name}", blob)
        z.writestr("manifest.json", json.dumps({"presets": [{"id": p["id"], "motion": p["motion"], "variation": p["n"], "name": p["name"], "file": f"{p['id']}.json"}
                                                             for p in lib["presets"]],
                                                "assign": assign, "unicode": _by_unicode(assign),
                                                "slot": {"asset": lottie.SLOT_ID, "file": lottie.SLOT_FILE, "px": lottie.SLOT_PX},
                                                 "handpicked": hp},
                                               ensure_ascii=False, indent=1))
    return buf.getvalue()


# ---------- handpicked Lottie files ----------
# The dashboard lists them; Select + Assign gives emojis an origin file and an optional
# morph file ({tokens, origin, morph?}). Stored in library.json `handpicked[token]`. Files are
# referenced, never rendered: assigning is instant. The zip carries the used files as
# handpicked/<file> and the manifest maps them; the chat app plays origin, then morph.
def handpicked_dir() -> Path:
    """The drop zone: lottie, or STICKERLOOM_LOTTIE_DIR. Missing = empty list, never an error."""
    import os
    env = os.environ.get("STICKERLOOM_LOTTIE_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "lottie"


def handpicked_files(lottie_dir=None) -> list:
    """Every *.json directly inside the drop zone that parses as a Lottie (a dict with a layers list):
    {file, name, bytes, w, h, fps, frames, layers, images, base64} sorted by name. Unreadable,
    oversized (>2 MB) or non-Lottie files are skipped, never an error."""
    d = Path(lottie_dir) if lottie_dir is not None else handpicked_dir()
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            if not f.is_file() or f.stat().st_size > 2 * 1024 * 1024:
                continue
            raw = f.read_text(encoding="utf-8")
            doc = json.loads(raw)
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("layers"), list):
            continue
        assets = doc.get("assets") if isinstance(doc.get("assets"), list) else []
        images = sum(1 for a in assets if isinstance(a, dict) and (str(a.get("p", "")).lower().endswith((".png", ".jpg", ".jpeg", ".webp")) or str(a.get("p", "")).startswith("data:")))
        try:
            frames = int(doc.get("op", 0)) - int(doc.get("ip", 0))
        except (TypeError, ValueError):
            frames = 0
        out.append({"file": f.name, "name": f.stem, "bytes": f.stat().st_size,
                    "w": doc.get("w", 0), "h": doc.get("h", 0), "fps": doc.get("fr", 0), "frames": frames,
                    "layers": len(doc["layers"]), "images": images, "base64": "data:" in raw})
    return out


def handpicked_names(lottie_dir=None) -> set:
    return {x["file"] for x in handpicked_files(lottie_dir)}


def handpicked_blob(name: str, *, lottie_dir=None) -> bytes:
    """The raw bytes of one dropped file. Unknown names, escapes and oversized files are a 404, never a read outside the zone."""
    clean = str(name or "")
    if not clean or clean in (".", "..") or "/" in clean or chr(92) in clean:
        raise SetError(f"no handpicked file {clean}", 404)
    if clean not in handpicked_names(lottie_dir):
        raise SetError(f"no handpicked file {clean}", 404)
    d = Path(lottie_dir) if lottie_dir is not None else handpicked_dir()
    f = d / clean
    try:
        if not f.is_file() or f.stat().st_size > 2 * 1024 * 1024:
            raise SetError(f"no handpicked file {clean}", 404)
        return f.read_bytes()
    except OSError:
        raise SetError(f"no handpicked file {clean}", 404)


def assign_handpicked(out: Path, tokens, origin, morph=None, *, lottie_dir=None) -> dict:
    """Give emojis a handpicked origin file and an optional morph file: {tokens (required, explicit: never every emoji),
    origin (a dropped file; "" clears the entry), morph (a dropped file; None/""/missing = none)}. {assigned, cleared}."""
    files = handpicked_names(lottie_dir)
    todo = [emoji_token(t)[0] for t in dict.fromkeys(str(t) for t in (tokens or []))]
    if not todo:
        raise SetError("tokens must be a non-empty list (handpicked files are assigned deliberately, never to every emoji)")
    origin = (origin or "").strip()
    morph = (morph or "").strip() or None
    for name, label in ((origin, "origin"), (morph, "morph")):
        if name and name not in files:
            raise SetError(f"no handpicked file {name} for {label}", 404)
    with _LOCK:
        d = _lib_read(out)
        hp = d.setdefault("handpicked", {})
        assigned, cleared = 0, 0
        for t in todo:
            if not origin:
                if t in hp:
                    del hp[t]
                    cleared += 1
                continue
            rec = {"origin": origin}
            if morph:
                rec["morph"] = morph
            hp[t] = rec
            assigned += 1
        _lib_write(out, d)
    return {"assigned": assigned, "cleared": cleared}


# ---------- super reaction (Reactions 2.0): hold an emoji, release, and its preset plays over the WHOLE screen ----------
def held_preset(out: Path, token: str) -> dict:
    """The preset an emoji holds (the same rule as the wall): its assigned one, else its motion's first variation, else the first preset."""
    from ..engine import emoji_motion as em
    d = _lib_read(out)
    by_id = {p["id"]: p for p in d["presets"]}
    if d["assign"].get(token) in by_id:
        return by_id[d["assign"][token]]
    own = em.motion_for_token(token)["preset"]
    ps = library(out)["presets"]
    return next((p for p in ps if p["motion"] == own), ps[0])


def super_lottie(out: Path, token, aspect, *, glow: str = "auto", color=None, colors=None, settings=None, preset_id=None) -> dict:
    """{token, emoji, preset, bucket, color, lottie}: the emoji's held preset (or `preset_id`) as a full-screen, emoji-free Lottie for the screen
    bucket nearest `aspect` (width / height), lit with the emoji's own colour (glow 'auto'), a `color`, a two-colour `colors` gradient, or 'none'.
    Free and deterministic; nothing is stored. 400 for a bad option, 404 for an unknown preset, 409 for an emoji outside the bank."""
    from ..engine import super_reaction as sr
    t, glyph = emoji_token(token)
    rec = lib_preset(out, preset_id) if preset_id else held_preset(out, t)
    p = emoji_burst(t, **_kwargs({**rec["settings"], "preset": rec["motion"]}))[3]
    rgba = None
    if glow == "auto":
        try:
            rgba = _glyph(glyph)
        except SetError:                                                         # no colour emoji font: the engine's warm default
            rgba = None
    try:
        doc = sr.export_super(p, float(aspect), settings=settings, glow=str(glow), color=color, colors=colors, emoji_rgba=rgba)
    except (TypeError, ValueError) as ex:
        raise SetError(str(ex))
    meta = doc.pop("stickerloom")
    return {"token": t, "emoji": glyph, "preset": rec["id"], "bucket": meta["bucket"], "aspect": meta["aspect"], "glow": meta["glow"],
            "color": meta["color"], "lottie": doc}
