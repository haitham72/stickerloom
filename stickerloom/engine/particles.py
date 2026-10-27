"""Particle burst: a few sprite images -> a 3 s Telegram video-sticker frame sequence. Pure, deterministic, no network.

The clip starts from nothing, bursts out of an origin (the centre by default), falls, and ends with nothing:
  * the first frames are fully transparent (no particle spawns before frame 3, and a particle is born at scale 0);
  * the last frames are fully transparent (every particle is dead and faded to alpha 0 before `duration - 0.2 s`).

Layout of the module
  ParticleParams   every knob, validated and clamped by `.clamped()`; PRESETS / preset() name the common looks.
  trajectories()   the physics only (positions, scale, angle, opacity per particle per frame): inspectable, no pixels.
  simulate()       trajectories + drawing -> frames `(N, size, size, 4) uint8`, STRAIGHT alpha.
  to_webm()        the engine's own VP9+alpha encoder path under `cfg.video_max_bytes`.
  preview_webp()   an animated WebP loop for the browser.
  png_bytes()      an RGBA array as PNG bytes.
The emoji glyph as a sprite is engine/emoji_glyph.py; the Lottie export of these trajectories is engine/lottie.py.

Units: positions are fractions of the canvas (0..1, y grows downward like an image); speeds are canvas/s; times are seconds.
Same seed + same params + same sprites -> the same bytes (one local `numpy.random.default_rng(seed)`, no global state).
Pure numpy / OpenCV / Pillow; ffmpeg is only reached through `to_webm` (lazy import of the engine's encoder).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, fields, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# ---- physics constants (canvas units) -------------------------------------------------------------------------------
G_CANVAS = 1.6          # gravity=1 pulls down at this many canvas/s^2
SPEED = 1.5             # magnitude=1 launches at SPEED * U(0.5, 1.1) canvas/s
VORTEX_W = 7.5          # vortex=1: the swirl's flow field turns at VORTEX_W rad/s around the origin (vortex=1.8 is about 2.1 turns a second)
VORTEX_RELAX = 2.5      # |vortex|>=1: a particle's velocity relaxes toward that field at this rate per second (weaker swirls relax in proportion)
DRAG = 0.9              # velocity decays by exp(-DRAG * dt): the burst slows down a little, never stops
GROWTH = 0.12           # a particle grows by this share over its life while flying
FADE_SHARE = 0.25       # the alpha fades to 0 over the last quarter of a particle's life
SPAWN_WINDOW = 0.35     # particles are born staggered over a 0.35 s window (shorter clips scale it down) ...
LEAD_FRAMES = 3         # ... that opens after this many empty frames: the first 3 frames are empty BY CONSTRUCTION (frame 0 certainly)
QUIET_TAIL = 0.2        # every particle is dead this long before the clip ends (last frames empty BY CONSTRUCTION)
MIN_LIFE = 0.3
MORPH_XFADE = 0.15      # seconds a particle crossfades at its morph (into its target, or out into its children)
ALPHA_TRIM = 4          # a sprite's alpha at or below this is haze, not subject: ignored when trimming
_POS_LIMIT = 50.0       # canvas units: positions are clipped here so nothing can overflow
AIR_K = 1.5             # air=1 adds this much drag per second (on top of DRAG)
WIND_K = 1.0            # wind=1 pushes at this many canvas/s^2
TURB_K = 2.0            # turbulence=1 pushes up to this many canvas/s^2, a seeded smooth field
# The settings modelled on Trapcode Particular: every one is off by default and draws
# its randoms from a SECOND generator (seed, 0x15), so a burst made before them keeps every byte.
EMITTERS = ("point", "ring", "box", "line")
SIZE_CURVES = ("none", "grow", "shrink", "swell", "pulse")
OPACITY_CURVES = ("none", "fade in", "flicker", "blink out")
TIME_SAMPLING = ("start at birth", "loop", "random start, loop", "random still")
AUX_AT = ("morph", "death", "bounce", "trail")


# ---- parameters ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ParticleParams:
    gravity: float = 1.0            # -2..3: positive pulls down, negative floats up
    magnitude: float = 1.0          # 0..3: explosion speed
    vortex: float = 0.0             # -2..2: swirl around the origin; positive = clockwise as seen on screen, negative = counter-clockwise
    count: int = 28                 # 4..80 particles
    size_min: float = 0.10          # sprite's longest side, fraction of the canvas (0.02..0.8)
    size_max: float = 0.22
    particle_size: float = 1.0      # 0.25..3: how big the particles look, a multiplier on size_min/size_max (the "Size" slider)
    erosion_px: int = 0             # 0..6: alpha fringe erosion on keyed sprites before preparation; off preserves old bytes
    spin: float = 1.0               # 0..3: rotation speed (0 = never rotates)
    lifetime: float = 2.2           # seconds a particle lives (<= duration - 0.4)
    spread: float = 360.0           # degrees of emission arc, centred upward when < 360
    pop: float = 0.25               # seconds of scale pop-in
    seed: int = 1
    fps: float = 30.0               # 20..60
    duration: float = 3.0           # seconds (1.0..3.0: Telegram's cap)
    size: int = 512                 # canvas side in px
    origin: tuple = (0.5, 0.5)      # fractions of the canvas
    show_source: bool = False       # reserved: the source sticker is not drawn yet
    size_in_dur: float = 0.0        # 0..1.5 s (<= lifetime/2): each particle zooms from nothing to full size over this long after its birth (0 = born full size)
    size_out_dur: float = 0.0       # 0..1.5 s (<= lifetime/2): ... and back to nothing over this long before its death (0 = no zoom out)
    hue_var: float = 0.0            # 0..180 degrees: each particle's colours turn by a seeded hue in [-hue_var, +hue_var] (0 = the sprite's own colours)
    fade_start: float = 0.0         # Fade out start, s after a particle's birth: its opacity starts dropping from 100% here
    fade_dur: float = 0.0           # Fade out duration, s: 100% -> 0% takes this long, linearly (0 = off: the old fade over the last quarter of its life)
    fade_var: float = 0.0           # Fade out variation, s: each particle's start and duration each move by a seeded +-fade_var (never below 0)
    speed_var: float = 0.0          # 0..1: each particle's launch speed is Speed x (1 +- speed_var), seeded (on top of the old 0.5-1.1 spread)
    speed_pulse: float = 0.0        # 0..1: each particle's speed swells and slows by this share, on its own seeded phase (0 = steady)
    pulse_hz: float = 2.0           # 0.2..6: how many swells a second
    flip: float = 0.0               # 0..4 turns/s: the cheap spin, horizontal scale 100% -> -100% -> 100% (a card flip, no rotation; 0 = off)
    flip_var: float = 0.0           # 0..1: each particle's flip rate +- this share
    morph_at: float = 0.0           # 0..2 s after birth: when a particle turns from its origin into a morph target (0 = no morph; Lottie style)
    morph_var: float = 0.0          # 0..1 s: +- per particle on morph_at
    children: int = 0               # 0..12: Morph auxiliary, each particle bursts this many children (same settings, one level) at its morph (its fade start with no morph)
    child_size: float = 0.5         # 0.1..3: a child's size at birth, x its parent's drawn size at that moment
    child_size_var: float = 0.0     # 0..1: a seeded log spread per child around child_size (most stay near it, a few reach 1x or 3x)
    morph_burst: int = 0            # 0 = at its morph a particle turns into its target (1:1); 1 = it bursts into its children (it fades out, they fly the target)
    # Emitter (Particular's Emitter group)
    emitter: int = 0                # EMITTERS: where particles are born; point = origin, ring = on a circle of emitter_w, box = inside emitter_w x emitter_h, line = along emitter_w
    emitter_w: float = 0.0          # 0..1 of the canvas
    emitter_h: float = 0.0          # 0..1 of the canvas (box)
    emit_dur: float = 0.0           # 0..2 s: Stream, births spread evenly-at-random over this long (0 = Burst, the short window)
    direction: float = 0.0          # -180..180 degrees: turns the emission arc (spread < 360), 0 = up
    vel_dist: float = 0.0           # 0..1: Velocity distribution, 0 = an even fan, 1 = most particles near the arc's middle
    # Particle
    life_var: float = 0.0           # 0..1: Life random, each particle's life x (1 +- share)
    size_curve: int = 0             # SIZE_CURVES: Size over life, multiplies the size
    opacity_curve: int = 0          # OPACITY_CURVES: Opacity over life, multiplies the opacity
    time_sampling: int = 0          # TIME_SAMPLING: how a Lottie particle plays its source (the Lottie export reads it)
    # Physics
    air: float = 0.0                # 0..3: Air resistance, more drag
    wind_x: float = 0.0             # -2..2: Wind, a steady push (+ = right)
    wind_y: float = 0.0             # -2..2: (+ = down)
    turbulence: float = 0.0         # 0..2: Turbulence field, affect position
    turb_scale: float = 2.0         # 0.5..6: the field's size (higher = smaller eddies)
    turb_speed: float = 0.5         # 0..3: Evolution speed
    turb_size: float = 0.0          # 0..1: Turbulence affect size
    bounce: float = 0.0             # 0..1: Bounce off the floor (0 = no floor)
    floor_y: float = 0.9            # 0.3..1 of the canvas height: the floor
    slide: float = 0.0              # 0..1: friction along the floor at each bounce
    # Aux system (Particular's Aux; stickerloom's children)
    aux_at: int = 0                 # AUX_AT: children are born at the morph (the default), at the particle's death, at its first bounce, or along its life (a trail)
    aux_inherit: float = 0.0        # 0..1: a child starts with this share of its parent's velocity
    aux_life: float = 0.0           # 0..2 s: a child's life (0 = Life)

    @classmethod
    def from_dict(cls, d: dict) -> "ParticleParams":
        """Strict: an unknown key is a ValueError (the JSON contract has no silent extras)."""
        names = {f.name for f in fields(cls)}
        bad = sorted(set(d) - names)
        if bad:
            raise ValueError(f"unknown particle parameter(s): {', '.join(bad)}")
        return cls(**d)

    def to_dict(self) -> dict:
        return {f.name: (list(getattr(self, f.name)) if f.name == "origin" else getattr(self, f.name)) for f in fields(self)}

    def clamped(self) -> "ParticleParams":
        """The same params with every field inside its range. Nonsense raises ValueError: a non-number, count < 1, size <= 0,
        fps <= 0, duration <= 0, an origin that is not two numbers. Out-of-range but meaningful values are clamped, not rejected."""
        def num(name, v):
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number, got {v!r}") from None
            if not math.isfinite(x):
                raise ValueError(f"{name} must be finite, got {v!r}")
            return x

        def lim(x, lo, hi):
            return min(max(x, lo), hi)

        count = num("count", self.count)
        if count < 1:
            raise ValueError(f"count must be at least 1, got {self.count!r}")
        size = num("size", self.size)
        if size <= 0:
            raise ValueError(f"size must be positive, got {self.size!r}")
        fps = num("fps", self.fps)
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {self.fps!r}")
        duration = num("duration", self.duration)
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {self.duration!r}")
        try:
            ox, oy = (num("origin", v) for v in self.origin)
        except TypeError:
            raise ValueError(f"origin must be two numbers, got {self.origin!r}") from None
        fps = lim(fps, 20.0, 60.0)
        duration = lim(duration, 1.0, 3.0)
        size_min = lim(num("size_min", self.size_min), 0.02, 0.8)
        size_max = lim(num("size_max", self.size_max), size_min, 0.8)      # size_max below size_min is raised to it
        lifetime = lim(num("lifetime", self.lifetime), MIN_LIFE, max(MIN_LIFE, duration - 0.4))
        envelope = min(1.5, lifetime / 2)
        return replace(
            self,
            gravity=lim(num("gravity", self.gravity), -2.0, 3.0),
            magnitude=lim(num("magnitude", self.magnitude), 0.0, 3.0),
            vortex=lim(num("vortex", self.vortex), -2.0, 2.0),
            count=int(lim(round(count), 4, 80)),
            size_min=size_min, size_max=size_max,
            particle_size=lim(num("particle_size", self.particle_size), 0.25, 3.0),
            spin=lim(num("spin", self.spin), 0.0, 3.0),
            erosion_px=int(lim(round(num("erosion_px", self.erosion_px)), 0, 6)),
            lifetime=lifetime,
            spread=lim(num("spread", self.spread), 10.0, 360.0),
            pop=lim(num("pop", self.pop), 1.0 / fps, max(1.0 / fps, lifetime / 2)),
            seed=int(num("seed", self.seed)) % (2 ** 32),
            fps=fps, duration=duration,
            size=int(lim(round(size), 16, 1024)),
            origin=(lim(ox, 0.0, 1.0), lim(oy, 0.0, 1.0)),
            show_source=bool(self.show_source),
            size_in_dur=lim(num("size_in_dur", self.size_in_dur), 0.0, envelope),
            size_out_dur=lim(num("size_out_dur", self.size_out_dur), 0.0, envelope),
            hue_var=lim(num("hue_var", self.hue_var), 0.0, 180.0),
            fade_start=lim(num("fade_start", self.fade_start), 0.0, duration),
            fade_dur=lim(num("fade_dur", self.fade_dur), 0.0, duration),
            fade_var=lim(num("fade_var", self.fade_var), 0.0, 1.0),
            speed_var=lim(num("speed_var", self.speed_var), 0.0, 1.0),
            speed_pulse=lim(num("speed_pulse", self.speed_pulse), 0.0, 1.0),
            pulse_hz=lim(num("pulse_hz", self.pulse_hz), 0.2, 6.0),
            flip=lim(num("flip", self.flip), 0.0, 4.0),
            flip_var=lim(num("flip_var", self.flip_var), 0.0, 1.0),
            morph_at=lim(num("morph_at", self.morph_at), 0.0, duration),
            morph_var=lim(num("morph_var", self.morph_var), 0.0, 1.0),
            children=int(lim(round(num("children", self.children)), 0, 12)),
            child_size=lim(num("child_size", self.child_size), 0.1, 3.0),
            child_size_var=lim(num("child_size_var", self.child_size_var), 0.0, 1.0),
            morph_burst=1 if num("morph_burst", self.morph_burst) >= 0.5 else 0,
            emitter=int(lim(round(num("emitter", self.emitter)), 0, len(EMITTERS) - 1)),
            emitter_w=lim(num("emitter_w", self.emitter_w), 0.0, 1.0),
            emitter_h=lim(num("emitter_h", self.emitter_h), 0.0, 1.0),
            emit_dur=lim(num("emit_dur", self.emit_dur), 0.0, 2.0),
            direction=lim(num("direction", self.direction), -180.0, 180.0),
            vel_dist=lim(num("vel_dist", self.vel_dist), 0.0, 1.0),
            life_var=lim(num("life_var", self.life_var), 0.0, 1.0),
            size_curve=int(lim(round(num("size_curve", self.size_curve)), 0, len(SIZE_CURVES) - 1)),
            opacity_curve=int(lim(round(num("opacity_curve", self.opacity_curve)), 0, len(OPACITY_CURVES) - 1)),
            time_sampling=int(lim(round(num("time_sampling", self.time_sampling)), 0, len(TIME_SAMPLING) - 1)),
            air=lim(num("air", self.air), 0.0, 3.0),
            wind_x=lim(num("wind_x", self.wind_x), -2.0, 2.0),
            wind_y=lim(num("wind_y", self.wind_y), -2.0, 2.0),
            turbulence=lim(num("turbulence", self.turbulence), 0.0, 2.0),
            turb_scale=lim(num("turb_scale", self.turb_scale), 0.5, 6.0),
            turb_speed=lim(num("turb_speed", self.turb_speed), 0.0, 3.0),
            turb_size=lim(num("turb_size", self.turb_size), 0.0, 1.0),
            bounce=lim(num("bounce", self.bounce), 0.0, 1.0),
            floor_y=lim(num("floor_y", self.floor_y), 0.3, 1.0),
            slide=lim(num("slide", self.slide), 0.0, 1.0),
            aux_at=int(lim(round(num("aux_at", self.aux_at)), 0, len(AUX_AT) - 1)),
            aux_inherit=lim(num("aux_inherit", self.aux_inherit), 0.0, 1.0),
            aux_life=lim(num("aux_life", self.aux_life), 0.0, 2.0),
        )


# Each preset has a measurable signature (tests/test_emoji_motion.py checks it on trajectories(), numbers only):
#   burst     radial outward, a 360 degree fan from the centre
#   fountain  from the bottom (0.5, 0.8), a cone of at most 90 degrees, launched upward, then falls back
#   vortex    tangential speed well above radial (vortex >= 1.5, magnitude <= 0.6), two or more turns around the origin, stays on the canvas
#   rain      from the top (0.5, 0.1), magnitude <= 0.5, gravity down, a spread of at most 120 degrees: it falls
#   confetti  48 or more particles, low gravity, fast spin, a wide fan
PRESETS: dict = {
    "burst": {},                                                                                   # the defaults: a 360 degree pop from the centre
    "fountain": {"spread": 70.0, "magnitude": 1.9, "gravity": 1.3, "count": 36, "origin": (0.5, 0.8), "spin": 1.5},
    "vortex": {"vortex": 1.8, "gravity": 0.1, "magnitude": 0.5, "spin": 2.0, "count": 36, "lifetime": 2.6, "size_min": 0.07, "size_max": 0.14},
    "rain": {"gravity": 0.9, "magnitude": 0.35, "count": 50, "size_min": 0.07, "size_max": 0.15, "origin": (0.5, 0.1), "spin": 0.6, "spread": 100.0},
    "confetti": {"count": 64, "size_min": 0.06, "size_max": 0.12, "spin": 3.0, "gravity": 0.4, "magnitude": 1.5, "spread": 160.0, "lifetime": 2.4},
}


def preset(name: str, **override) -> ParticleParams:
    """A named look (see PRESETS) with keyword overrides, already clamped. Unknown name or parameter -> ValueError."""
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; choose one of {', '.join(PRESETS)}")
    return ParticleParams.from_dict({**PRESETS[name], **override}).clamped()


# ---- physics --------------------------------------------------------------------------------------------------------
@dataclass
class Trajectories:
    """What `simulate` draws, before any pixel exists. Arrays are (frames, particles[, 2]); the particles are in spawn-draw order
    (draw back to front with `draw_order`)."""
    pos: np.ndarray          # (F, N, 2) canvas fractions of the particle centre (the origin until it spawns)
    scale: np.ndarray        # (F, N) pop-in x flight growth, 0 before spawn
    angle: np.ndarray        # (F, N) radians
    opacity: np.ndarray      # (F, N) 0..1: 1 while flying, with fade_dur: 1 until fade_start (+-fade_var) after birth, then linearly to 0 over fade_dur (+-fade_var), done by its death at the latest; else fades over the last quarter of the life
    visible: np.ndarray      # (F, N) bool: spawned, not dead, scale and opacity worth drawing
    size_frac: np.ndarray    # (N,) sprite's longest side as a fraction of the canvas at scale 1
    sprite: np.ndarray       # (N,) which sprite each particle shows
    hue: np.ndarray          # (N,) degrees each particle's colours are turned by (0 everywhere when hue_var is 0)
    spawn: np.ndarray        # (N,) first frame (>= 3)
    life: np.ndarray         # (N,) seconds
    draw_order: np.ndarray   # (N,) particle indices, smallest first (drawn first = behind)
    p: ParticleParams        # the clamped params the plan was made with
    flipx: np.ndarray = None     # (F, N) the card flip's horizontal scale, 1 -> -1 -> 1 (all 1 with flip 0)
    morph: np.ndarray = None     # (N,) seconds after birth each particle morphs (inf = never)
    pick: np.ndarray = None      # (N,) 0..1 which morph target
    parent: np.ndarray = None    # (N,) the root a child burst from (-1 = a root)
    sample: np.ndarray = None    # (N,) 0..1: where a Lottie particle starts in its source (time_sampling random start / random still)

    @property
    def frames(self) -> int:
        return self.pos.shape[0]


def _ease_out_back(t: np.ndarray, c1: float = 1.2) -> np.ndarray:
    u = t - 1.0
    return 1.0 + (c1 + 1.0) * u ** 3 + c1 * u ** 2


def trajectories(p: ParticleParams, n_sprites: int = 1) -> Trajectories:
    """The deterministic physics of one burst: seeded, vectorised, one semi-implicit Euler step per frame. With `children` > 0 every root
    particle bursts that many children (same settings, one level) from where it is at its morph (its fade start without one); they follow the roots in every array."""
    p = p.clamped()
    if n_sprites < 1:
        raise ValueError("n_sprites must be at least 1")
    n, F, dt = p.count, int(round(p.fps * p.duration)), 1.0 / p.fps
    rng = np.random.default_rng(p.seed)
    rng2 = np.random.default_rng([p.seed, 0x15])                    # the Particular-style settings draw only from here: an older burst keeps its bytes

    order = []                                                      # every sprite appears: seeded shuffles back to back
    while len(order) < n:
        order.extend(rng.permutation(n_sprites).tolist())
    sprite = np.array(order[:n], dtype=np.int64)

    slot = (rng.permutation(n) + rng.uniform(0.1, 0.9, n)) / n      # stratified emission: an even fan, not random clumps
    wheel = rng.uniform(0.0, 2 * math.pi)
    ang = _fan(p, wheel, _centred(slot, p.vel_dist))
    window = min(SPAWN_WINDOW, 0.15 * p.duration)
    if p.emit_dur > 0:                                               # Stream: births over emit_dur, each particle still alive MIN_LIFE before the quiet tail
        window = max(window, min(p.emit_dur, p.duration - QUIET_TAIL - MIN_LIFE - LEAD_FRAMES * dt))
    spawn = LEAD_FRAMES + np.rint(rng.uniform(0.0, window, n) * p.fps).astype(np.int64)     # never before frame 3: the start is empty
    speed = p.magnitude * SPEED * rng.uniform(0.5, 1.1, n)
    size_frac = np.minimum(rng.uniform(p.size_min, p.size_max, n) * p.particle_size, 0.9)
    omega = p.spin * rng.uniform(-2.0, 2.0, n)
    theta0 = p.spin * rng.uniform(-0.6, 0.6, n)
    jitter = rng.uniform(0.8, 1.0, n)
    hue = rng.uniform(-1.0, 1.0, n) * p.hue_var                     # drawn after every older draw: an older burst is unchanged
    fade_k = rng.uniform(-1.0, 1.0, (2, n)) * p.fade_var             # +- seconds on (start, duration)
    extra = _Extra.draw(rng, p, n)                                   # drawn LAST, for the same reason: speed spread, pulse, flip, morph
    speed = speed * extra.speed_k
    x2 = _Extra2.draw(rng2, p, n)
    life = np.minimum(p.lifetime * jitter * x2.life_k, (p.duration - QUIET_TAIL) - spawn * dt)          # dead before duration - 0.2 s
    life = np.maximum(life, 4 * dt)

    origin = np.tile(np.array(p.origin, dtype=np.float64), (n, 1)) + x2.at
    v0 = np.stack([np.cos(ang), np.sin(ang)], 1) * speed[:, None]
    field = rng2.uniform(0.0, 2 * math.pi, 8)                        # the turbulence field's phases, one field for every particle
    P, hit = _fly(p, F, dt, spawn, origin, v0, extra, field)
    scale, opacity, a, alive = _look(p, F, dt, spawn, life, fade_k, x2.phase)
    morphs = p.morph_at > 0
    m_at = np.maximum(p.morph_at + extra.morph_k, dt)                # seconds after birth each root morphs (when morphs)
    if morphs and p.morph_burst and p.children > 0:                  # Bursts into: the root fades out over MORPH_XFADE at its morph, its children take over
        cut = m_at[None, :] + MORPH_XFADE
        alive = alive & (a < cut)
        opacity = np.where(alive, opacity * np.clip((cut - a) / MORPH_XFADE, 0.0, 1.0), 0.0)
        scale = np.where(alive, scale, 0.0)
        life = np.minimum(life, m_at + MORPH_XFADE)

    parent = np.full(n, -1, dtype=np.int64)
    if p.children > 0:                                               # Morph auxiliary: one level of child bursts, every child drawn after the roots
        k = p.children
        par = np.repeat(np.arange(n), k)
        if p.aux_at == 3:                                            # trail: the children are born along the parent's life, evenly
            c_spawn = spawn[par] + np.rint((np.tile(np.arange(k), n) + 0.5) / k * life[par] * p.fps).astype(np.int64)
        else:
            if p.aux_at == 1:                                        # at its death
                f_start = spawn + np.rint(life * p.fps).astype(np.int64) - 1
            elif p.aux_at == 2:                                      # at its first bounce (never = no children)
                f_start = np.where(hit >= 0, hit, 10 ** 6)
            else:
                when = m_at if morphs else _fade_start_s(p, life, fade_k)    # the morph starts the children; no morph = the fade start
                f_start = spawn + np.rint(when * p.fps).astype(np.int64)
            c_spawn = np.repeat(f_start, k)
        f_last = int(round((p.duration - QUIET_TAIL) * p.fps)) - 4
        m = len(par)
        c_slot = (np.tile(np.arange(k), n) + rng.uniform(0.1, 0.9, m)) / k
        c_wheel = np.repeat(rng.uniform(0.0, 2 * math.pi, n), k)
        c_ang = _fan(p, c_wheel, c_slot)
        c_speed = p.magnitude * SPEED * rng.uniform(0.5, 1.1, m)
        c_rel = np.clip(p.child_size * np.exp(p.child_size_var * rng.standard_normal(m)), 0.1, 3.0)
        c_omega = p.spin * rng.uniform(-2.0, 2.0, m)
        c_theta0 = p.spin * rng.uniform(-0.6, 0.6, m)
        c_jitter = rng.uniform(0.8, 1.0, m)
        c_fade_k = rng.uniform(-1.0, 1.0, (2, m)) * p.fade_var
        c_extra = _Extra.draw(rng, p, m)
        c_x2 = _Extra2.draw(rng2, p, m)
        sel = np.flatnonzero((c_spawn > spawn[par]) & (c_spawn < f_last))
        if len(sel):
            par, c_spawn = par[sel], c_spawn[sel]
            c_origin = P[c_spawn, par]                                # where the parent is when its fade starts
            c_size = np.minimum(size_frac[par] * np.maximum(scale[c_spawn, par], 0.05) * c_rel[sel], 0.9)
            c_extra, c_x2 = c_extra.take(sel), c_x2.take(sel)
            c_v0 = np.stack([np.cos(c_ang[sel]), np.sin(c_ang[sel])], 1) * (c_speed[sel] * c_extra.speed_k)[:, None]
            if p.aux_inherit > 0:                                    # Inherit velocity: the parent's speed at that frame, carried on
                c_v0 = c_v0 + p.aux_inherit * (P[c_spawn, par] - P[np.maximum(c_spawn - 1, 0), par]) / dt
            c_lt = p.aux_life if p.aux_life > 0 else p.lifetime
            c_life = np.maximum(np.minimum(c_lt * c_jitter[sel] * c_x2.life_k, (p.duration - QUIET_TAIL) - c_spawn * dt), 4 * dt)
            c_fk = c_fade_k[:, sel]
            CP, _ = _fly(p, F, dt, c_spawn, c_origin, c_v0, c_extra, field)
            CP = np.where((np.arange(F)[:, None] < c_spawn[None, :])[..., None], c_origin[None, :, :], CP)
            c_scale, c_opacity, c_a, c_alive = _look(p, F, dt, c_spawn, c_life, c_fk, c_x2.phase)
            P = np.concatenate([P, CP], 1)
            scale, opacity, a, alive = (np.concatenate(x, 1) for x in ((scale, c_scale), (opacity, c_opacity), (a, c_a), (alive, c_alive)))
            spawn, life, size_frac = np.concatenate([spawn, c_spawn]), np.concatenate([life, c_life]), np.concatenate([size_frac, c_size])
            omega, theta0 = np.concatenate([omega, c_omega[sel]]), np.concatenate([theta0, c_theta0[sel]])
            sprite, hue = np.concatenate([sprite, sprite[par]]), np.concatenate([hue, hue[par]])
            parent = np.concatenate([parent, par])
            extra, x2 = extra.join(c_extra), x2.join(c_x2)

    if p.turb_size > 0:                                              # Turbulence affect size: the same field swells and shrinks the particles it carries
        tt = (np.arange(F) * dt)[:, None]
        k2 = 2 * math.pi * p.turb_scale
        scale = scale * (1.0 + p.turb_size * np.sin(k2 * P[..., 0] + 2 * math.pi * p.turb_speed * tt + field[6]) * np.cos(k2 * P[..., 1] + field[7]))
    angle = theta0[None, :] + omega[None, :] * a
    flipx = np.where(alive, np.cos(2 * math.pi * extra.flip_rate[None, :] * a), 1.0) if p.flip > 0 else np.ones_like(a)
    visible = alive & (scale > 0.02) & (opacity > 0.004)
    morph = np.maximum(p.morph_at + extra.morph_k, dt) if morphs else np.full(len(spawn), np.inf)
    morph[parent >= 0] = np.inf                                      # a child is born as the target: it never morphs again
    return Trajectories(P, scale, angle, opacity, visible, size_frac, sprite, hue, spawn, life,
                        np.argsort(size_frac, kind="stable"), p, flipx, morph, extra.pick, parent, x2.sample)


def _fan(p: ParticleParams, wheel, slot):
    if p.spread >= 360.0:
        return wheel + slot * 2 * math.pi
    return -math.pi / 2 + math.radians(p.direction) + math.radians(p.spread) * (slot - 0.5)      # y grows downward: -pi/2 is up


def _centred(slot, v: float):
    """Velocity distribution: v 0 = the slots as they are (an even fan), 1 = most of them pulled toward the middle of the arc."""
    if v <= 0:
        return slot
    d = 2.0 * (np.asarray(slot) - 0.5)
    return 0.5 + 0.5 * np.sign(d) * np.abs(d) ** (1.0 + 3.0 * v)


@dataclass
class _Extra2:
    """The per-particle draws of the Particular-style settings (2026-10-15), from the second generator."""
    at: np.ndarray           # (n, 2) where on the emitter each particle is born, relative to the origin
    life_k: np.ndarray       # life multiplier, 1 +- life_var
    phase: np.ndarray        # the flicker's phase
    sample: np.ndarray       # 0..1, where a Lottie particle starts in its source

    @classmethod
    def draw(cls, rng, p: ParticleParams, n: int) -> "_Extra2":
        u = rng.uniform(0.0, 1.0, (5, n))
        if p.emitter == 1:                                           # ring
            r = p.emitter_w / 2
            at = np.stack([np.cos(2 * math.pi * u[0]), np.sin(2 * math.pi * u[0])], 1) * r
        elif p.emitter == 2:                                         # box
            at = np.stack([(u[0] - 0.5) * p.emitter_w, (u[1] - 0.5) * p.emitter_h], 1)
        elif p.emitter == 3:                                         # line
            at = np.stack([(u[0] - 0.5) * p.emitter_w, np.zeros(n)], 1)
        else:
            at = np.zeros((n, 2))
        return cls(at, 1.0 + p.life_var * (2 * u[2] - 1), 2 * math.pi * u[3], u[4])

    def take(self, idx) -> "_Extra2":
        return _Extra2(*(getattr(self, f.name)[idx] for f in fields(self)))

    def join(self, other: "_Extra2") -> "_Extra2":
        return _Extra2(*(np.concatenate([getattr(self, f.name), getattr(other, f.name)]) for f in fields(self)))


@dataclass
class _Extra:
    """The per-particle draws added for Lottie style (2026-10-14), always drawn after the older ones so an older burst keeps its bytes."""
    speed_k: np.ndarray      # launch speed multiplier, 1 +- speed_var
    phase: np.ndarray        # the speed pulse's phase
    flip_rate: np.ndarray    # card flips per second, flip +- flip_var share
    morph_k: np.ndarray      # +- seconds on morph_at
    pick: np.ndarray         # 0..1: which morph target (the exporter maps it onto its list)

    @classmethod
    def draw(cls, rng, p: ParticleParams, n: int) -> "_Extra":
        u = rng.uniform(-1.0, 1.0, (4, n))
        return cls(1.0 + p.speed_var * u[0], math.pi * (u[1] + 1.0), p.flip * (1.0 + p.flip_var * u[2]), p.morph_var * u[3], rng.uniform(0.0, 1.0, n))

    def take(self, idx) -> "_Extra":
        return _Extra(*(getattr(self, f.name)[idx] for f in fields(self)))

    def join(self, other: "_Extra") -> "_Extra":
        return _Extra(*(np.concatenate([getattr(self, f.name), getattr(other, f.name)]) for f in fields(self)))


def _fly(p: ParticleParams, F: int, dt: float, spawn, origin, v0, extra: _Extra, field=None) -> tuple[np.ndarray, np.ndarray]:
    """((F, n, 2) positions, (n,) the frame of each particle's first bounce, -1 = none): gravity, drag (+ air), wind, the turbulence field, the swirl
    about each particle's own origin, the speed pulse on the displacement, the floor."""
    n = len(spawn)
    g_eff = p.gravity * G_CANVAS
    decay = math.exp(-(DRAG + AIR_K * p.air) * dt)
    hit = np.full(n, -1, dtype=np.int64)
    k2 = 2 * math.pi * p.turb_scale
    swirl = min(1.0, abs(p.vortex))                                  # the swirl is a flow field: velocity relaxes toward a rotation about the origin,
    turn = p.vortex * VORTEX_W                                       # and the centripetal term keeps the orbit (no centrifugal fling off the canvas)
    pos, vel = origin.copy(), np.zeros((n, 2))
    P = np.empty((F, n, 2))
    for f in range(F):
        flying = spawn < f
        if flying.any():
            acc = np.zeros_like(pos)
            acc[:, 1] = g_eff
            if p.wind_x or p.wind_y:
                acc[:, 0] += p.wind_x * WIND_K
                acc[:, 1] += p.wind_y * WIND_K
            if p.turbulence > 0 and field is not None:               # a smooth seeded field that drifts with Evolution speed
                w = 2 * math.pi * p.turb_speed * f * dt
                acc[:, 0] += p.turbulence * TURB_K * 0.5 * (np.sin(k2 * pos[:, 1] + w + field[0]) + np.sin(1.7 * k2 * (pos[:, 0] + pos[:, 1]) + 0.8 * w + field[1]))
                acc[:, 1] += p.turbulence * TURB_K * 0.5 * (np.sin(k2 * pos[:, 0] + 1.1 * w + field[2]) + np.sin(1.3 * k2 * (pos[:, 0] - pos[:, 1]) + 0.7 * w + field[3]))
            if swirl:
                rel = pos - origin
                r = np.hypot(rel[:, 0], rel[:, 1])[:, None] + 1e-6
                field = np.stack([-rel[:, 1], rel[:, 0]], 1) * turn
                tang = (rel[:, 0] * vel[:, 1] - rel[:, 1] * vel[:, 0])[:, None] / r
                acc += VORTEX_RELAX * swirl * (field - vel) - swirl * (tang * tang / r) * (rel / r)
            vel[flying] = (vel[flying] + acc[flying] * dt) * decay
            step = vel[flying] * dt
            if p.speed_pulse > 0:                                    # Speed pulse: each particle swells and slows on its own phase
                age = (f - spawn[flying]) * dt
                step = step * (1.0 + p.speed_pulse * np.sin(2 * math.pi * p.pulse_hz * age + extra.phase[flying]))[:, None]
            pos[flying] = np.clip(pos[flying] + step, -_POS_LIMIT, _POS_LIMIT)
            if p.bounce > 0:                                         # the floor: reflect, lose energy, slide
                under = flying & (pos[:, 1] > p.floor_y) & (vel[:, 1] > 0)
                if under.any():
                    pos[under, 1] = p.floor_y - (pos[under, 1] - p.floor_y) * p.bounce
                    vel[under, 1] = -vel[under, 1] * p.bounce
                    vel[under, 0] *= (1.0 - p.slide)
                    hit[under & (hit < 0)] = f
        born = spawn == f
        if born.any():
            pos[born] = origin[born]
            vel[born] = v0[born]
        P[f] = pos
    return P, hit


def _fade_start_s(p: ParticleParams, life, fade_k):
    """Seconds after birth each particle's fade starts (what `_look` uses): the Fade out start, else the last quarter of its life."""
    if p.fade_dur > 0:
        dt = 1.0 / p.fps
        f_end = np.minimum(np.maximum(p.fade_start + fade_k[0], 0.0) + np.maximum(p.fade_dur + fade_k[1], dt), life)
        return np.minimum(np.maximum(p.fade_start + fade_k[0], 0.0), f_end - dt)
    return life * (1.0 - FADE_SHARE)


def _look(p: ParticleParams, F: int, dt: float, spawn, life, fade_k, phase=None):
    """(scale, opacity, age, alive), each (F, n): pop-in, growth, Size in / out, the fade, and Size / Opacity over life."""
    frame_idx = np.arange(F)[:, None]
    age = (frame_idx - spawn[None, :]) * dt                         # (F, N), negative before spawn
    alive = (age >= 0) & (age < life[None, :])
    a = np.clip(age, 0.0, None)
    pop_scale = np.maximum(_ease_out_back(np.clip(a / p.pop, 0.0, 1.0)), 0.0)
    envelope = np.ones_like(a)                                       # Size in / Size out: multiplies the pop-in and the growth, never replaces them
    if p.size_in_dur > 0:
        envelope *= np.clip(a / p.size_in_dur, 0.0, 1.0)
    if p.size_out_dur > 0:
        envelope *= np.clip((life[None, :] - a) / p.size_out_dur, 0.0, 1.0)
    scale = np.where(alive, pop_scale * (1.0 + GROWTH * a / life[None, :]) * envelope, 0.0)
    if p.fade_dur > 0:                                               # Fade out: start and duration in seconds, per particle, linear 100% -> 0%
        f_end = np.minimum(np.maximum(p.fade_start + fade_k[0], 0.0) + np.maximum(p.fade_dur + fade_k[1], dt), life)     # 0% by its death at the latest
        f_start = np.minimum(np.maximum(p.fade_start + fade_k[0], 0.0), f_end - dt)
        x = np.clip((f_end[None, :] - a) / (f_end - f_start)[None, :], 0.0, 1.0)
        opacity = np.where(alive, x, 0.0)
    else:
        x = np.clip((life[None, :] - a) / (FADE_SHARE * life[None, :]), 0.0, 1.0)
        opacity = np.where(alive, x * x * (3.0 - 2.0 * x), 0.0)
    u = np.clip(a / life[None, :], 0.0, 1.0)                         # the share of its life a particle has lived
    if p.size_curve:
        curve = {1: 0.4 + 0.6 * u, 2: 1.0 - 0.7 * u, 3: 1.0 + 0.6 * np.sin(math.pi * u), 4: 1.0 + 0.2 * np.sin(6 * math.pi * u)}[p.size_curve]
        scale = scale * curve
    if p.opacity_curve:
        ph = (phase if phase is not None else np.zeros(len(spawn)))[None, :]
        curve = {1: np.clip(u / 0.25, 0.0, 1.0) ** 2,
                 2: 0.55 + 0.45 * np.sin(2 * math.pi * 8.0 * a + ph),
                 3: np.where(u > 0.7, 0.5 + 0.5 * np.cos(2 * math.pi * 6.0 * (u - 0.7)), 1.0)}[p.opacity_curve]
        opacity = opacity * curve
    return scale, opacity, a, alive


# ---- sprites --------------------------------------------------------------------------------------------------------
@dataclass
class AnimatedSprite:
    """One sprite's RGBA timeline. Every frame shares a canvas; playback begins at particle birth."""
    frames: np.ndarray
    fps: float = 30.0

    def __post_init__(self):
        if (not isinstance(self.frames, np.ndarray) or self.frames.dtype != np.uint8
                or self.frames.ndim != 4 or self.frames.shape[-1] != 4 or not len(self.frames)
                or not self.frames.shape[1] or not self.frames.shape[2]):
            raise ValueError("animated sprite frames must be non-empty RGBA uint8 (F, H, W, 4)")
        if not math.isfinite(float(self.fps)) or self.fps <= 0:
            raise ValueError("animated sprite fps must be positive and finite")


def trim_animation(sprite: AnimatedSprite, pad: int = 2) -> AnimatedSprite | None:
    """Crop once around the whole timeline, preserving internal movement and empty frames."""
    ys, xs = np.where((sprite.frames[..., 3] > ALPHA_TRIM).any(axis=0))
    if not len(ys):
        return None
    y0, y1 = max(0, int(ys.min()) - pad), min(sprite.frames.shape[1], int(ys.max()) + 1 + pad)
    x0, x1 = max(0, int(xs.min()) - pad), min(sprite.frames.shape[2], int(xs.max()) + 1 + pad)
    return AnimatedSprite(sprite.frames[:, y0:y1, x0:x1].copy(), sprite.fps)


@dataclass
class _Sprite:
    levels: list             # premultiplied float32 RGBA, a 1 px transparent border, each level half the one before
    content: list            # longest side of the drawn content at each level (the border excluded)


@dataclass
class _Animated:
    frames: list[_Sprite]
    fps: float

    def at(self, age: float) -> _Sprite:
        return self.frames[int(math.floor(max(0.0, age) * self.fps + 1e-9)) % len(self.frames)]


def trim_sprite(img, pad: int = 2):
    """A particle is a TIGHT sprite (the UI/UX spec P6): the RGBA picture cropped to its alpha bounding box (alpha at or below ALPHA_TRIM is haze, not subject) plus `pad` px of air that never runs
    off the picture. Never a 512 px sticker canvas. None when nothing is drawn. Pure."""
    if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 4:
        raise ValueError("a sprite must be an RGBA uint8 array of shape (H, W, 4)")
    ys, xs = np.where(img[..., 3] > ALPHA_TRIM)
    if len(ys) == 0:
        return None
    y0, y1 = max(0, int(ys.min()) - pad), min(img.shape[0], int(ys.max()) + 1 + pad)
    x0, x1 = max(0, int(xs.min()) - pad), min(img.shape[1], int(xs.max()) + 1 + pad)
    return img[y0:y1, x0:x1].copy()


def _prepare(img, max_long: int, keep_canvas: bool = False) -> _Sprite | None:
    """Trim to the alpha bbox (+1 px transparent padding), premultiply, shrink to the largest size it will ever be drawn at, and
    build a half-size pyramid so a small particle is never sampled from a big image (no aliasing). None when fully transparent."""
    if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 4:
        raise ValueError("a sprite must be an RGBA uint8 array of shape (H, W, 4)")
    ys, xs = np.where(img[..., 3] > ALPHA_TRIM)
    if len(ys) == 0 and not keep_canvas:
        return None
    crop = (img if keep_canvas else img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]).astype(np.float32) * (1.0 / 255.0)
    crop[..., :3] *= crop[..., 3:4]
    h, w = crop.shape[:2]
    r = min(1.0, max_long / max(h, w))
    cur = cv2.resize(crop, (max(1, round(w * r)), max(1, round(h * r))), interpolation=cv2.INTER_AREA) if r < 1.0 else crop
    levels, content = [], []
    while True:
        h, w = cur.shape[:2]
        levels.append(cv2.copyMakeBorder(cur, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0)))
        content.append(float(max(h, w)))
        if max(h, w) <= 8 or len(levels) >= 6:
            break
        cur = cv2.resize(cur, (max(1, round(w / 2)), max(1, round(h / 2))), interpolation=cv2.INTER_AREA)
    return _Sprite(levels, content)


def erode_sprites(sprites, px: int) -> list:
    """Trim keyed RGBA alpha using the existing edge morphology, before sprite preparation.

    Already-keyed cached art keeps its prior despill; RGB is never keyed again. Each animated
    frame is processed identically. Zero returns the original inputs without copying or RNG draws.
    """
    if not px:
        return sprites
    from .render import apply_edge
    def erode(img):
        return apply_edge(img[..., :3].astype(np.float32), img[..., 3].astype(np.float32) / 255, 0, px)
    return [AnimatedSprite(np.stack([erode(frame) for frame in img.frames]), img.fps)
            if isinstance(img, AnimatedSprite) else erode(img) for img in sprites]


def _prepare_all(sprites, p: ParticleParams) -> list:
    if sprites is None or len(sprites) == 0:
        raise ValueError("no sprites given")
    max_long = max(8, math.ceil(min(p.size_max * p.particle_size, 0.9) * p.size * 1.5))      # the biggest a particle gets: size_max x Size x pop overshoot x growth
    ready = []
    for img in erode_sprites(sprites, p.erosion_px):
        if isinstance(img, AnimatedSprite):
            trimmed = trim_animation(img, pad=0)
            if trimmed is not None:
                ready.append(_Animated([_prepare(fr, max_long, keep_canvas=True) for fr in trimmed.frames], trimmed.fps))
        else:
            spr = _prepare(img, max_long)
            if spr is not None:
                ready.append(spr)
    if not ready:
        raise ValueError("no usable sprite: every sprite is fully transparent")
    return ready


def fit_sprites(sprites: list, px: int) -> list:
    """Resize the sprites BEFORE simulating, for latency (a sticker is 512 px, a burst never draws a piece bigger than ~170 px): each sprite is trimmed to its alpha bbox and fitted into
    `px` x `px` (aspect kept, LANCZOS on premultiplied colour, never enlarged). A fully transparent sprite is returned as it is (`simulate` ignores it). Same order, same count."""
    px = int(px)
    if px < 1:
        raise ValueError("px must be positive")
    out = []
    for img in sprites:
        if isinstance(img, AnimatedSprite):
            trimmed = trim_animation(img, pad=0)
            if trimmed is None:
                out.append(img)
                continue
            h, w = trimmed.frames.shape[1:3]
            if max(h, w) > px:
                ratio = px / max(h, w)
                wh = (max(1, round(w * ratio)), max(1, round(h * ratio)))
                fitted = [np.asarray(Image.fromarray(np.ascontiguousarray(fr), "RGBA").resize(wh, Image.LANCZOS), np.uint8) for fr in trimmed.frames]
                trimmed = AnimatedSprite(np.stack(fitted), img.fps)
            out.append(trimmed)
            continue
        if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 4:
            raise ValueError("a sprite must be an RGBA uint8 array of shape (H, W, 4)")
        ys, xs = np.where(img[..., 3] > ALPHA_TRIM)
        if len(ys) == 0:
            out.append(img)
            continue
        crop = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = crop.shape[:2]
        if max(h, w) > px:
            k = px / max(h, w)
            crop = np.asarray(Image.fromarray(np.ascontiguousarray(crop), "RGBA").resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS), np.uint8)
        out.append(np.ascontiguousarray(crop))
    return out


# ---- hue ------------------------------------------------------------------------------------------------------------
def hue_matrix(deg: float) -> np.ndarray:
    """The 3x3 hue rotation of CSS `filter: hue-rotate(deg)` (a turn about the grey axis, luminance weights 0.213/0.715/0.072).
    Linear in RGB, so it applies to premultiplied colour as it is. 0 degrees is the identity."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928],
                     [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283],
                     [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072]], np.float32)


def hue_rotate(img: np.ndarray, deg: float) -> np.ndarray:
    """A straight-alpha RGBA uint8 picture with its colours turned by `deg` degrees (hue_matrix); alpha untouched. Pure."""
    if abs(deg) <= 1e-9:
        return img.copy()
    rgb = img[..., :3].astype(np.float32) @ hue_matrix(deg).T
    return np.dstack([np.clip(rgb + 0.5, 0, 255).astype(np.uint8), img[..., 3]])


# ---- drawing --------------------------------------------------------------------------------------------------------
def fit_zoom(tr: "Trajectories", origin: tuple) -> float:
    """The zoom (<= 1) about `origin` that keeps every visible particle, its whole sprite, inside the canvas: the burst SCALED to fit
    instead of cut at the edges (the hover preview). 1.0 when it already fits."""
    vis = tr.visible
    if not vis.any():
        return 1.0
    h = 0.5 * (tr.size_frac[None, :] * tr.scale)[vis] * 1.42          # a rotated sprite's half-diagonal, as a share of the canvas
    dx, dy = tr.pos[..., 0][vis] - origin[0], tr.pos[..., 1][vis] - origin[1]
    z = 1.0
    for room, need in ((origin[0], np.max(h - dx)), (1 - origin[0], np.max(h + dx)), (origin[1], np.max(h - dy)), (1 - origin[1], np.max(h + dy))):
        if need > 1e-6:
            z = min(z, max(room, 0.0) / need)
    return float(max(0.05, z))


def simulate(sprites: list, p: ParticleParams, on_frame=None, fit: bool = False) -> np.ndarray:
    """Draw one burst. `sprites` are static RGBA uint8 arrays or AnimatedSprite timelines (trimmed to their union alpha bbox; fully transparent ones are ignored;
    ValueError when none is usable). Returns `(N, size, size, 4) uint8` with N = round(fps * duration) (90 at the defaults) and STRAIGHT
    alpha (composited premultiplied, then un-premultiplied). Frame 0 and the last frames are empty by construction. Pieces may leave the
    canvas. With more particles than sprites every sprite appears; with fewer particles than sprites only `count` of them can.
    `on_frame(i, n)` is called after each frame is drawn (progress). `fit`: the whole burst is zoomed about its origin until nothing leaves
    the canvas (`fit_zoom`), the same motion smaller, never cut."""
    p = p.clamped()
    ready = _prepare_all(sprites, p)
    tr = trajectories(p, len(ready))
    S, F = p.size, tr.frames
    z = fit_zoom(tr, p.origin) if fit else 1.0
    frames = np.zeros((F, S, S, 4), np.uint8)
    canvas = np.zeros((S, S, 4), np.float32)                      # premultiplied, 0..1
    ox = (p.origin[0] + (tr.pos[..., 0] - p.origin[0]) * z) * S - 0.5        # pixel-centre convention: the canvas centre is (S - 1) / 2
    oy = (p.origin[1] + (tr.pos[..., 1] - p.origin[1]) * z) * S - 0.5
    cos, sin = np.cos(tr.angle), np.sin(tr.angle)
    length = tr.size_frac[None, :] * S * tr.scale * z             # the sprite's longest side in px, per frame
    sprite_of = tr.sprite.tolist()
    turn = {i: hue_matrix(h) for i, h in enumerate(tr.hue.tolist()) if abs(h) > 1e-9}
    for f in range(F):
        idx = tr.draw_order[tr.visible[f, tr.draw_order]].tolist()
        dirty = None
        for i in idx:
            spr = ready[sprite_of[i]]
            if isinstance(spr, _Animated):
                spr = spr.at((f - int(tr.spawn[i])) / p.fps)
            L = float(length[f, i])
            if L < 0.5:
                continue
            k = min(len(spr.levels) - 1, max(0, int(math.floor(math.log2(spr.content[0] / L)))))
            lvl, k_len = spr.levels[k], spr.content[k]
            sc = L / k_len
            c, s = float(cos[f, i]), float(sin[f, i])
            ph, pw = lvl.shape[:2]
            hx = 0.5 * sc * (abs(c) * pw + abs(s) * ph) + 1.5
            hy = 0.5 * sc * (abs(s) * pw + abs(c) * ph) + 1.5
            cx, cy = float(ox[f, i]), float(oy[f, i])
            x0, x1 = max(0, math.floor(cx - hx)), min(S, math.ceil(cx + hx) + 1)
            y0, y1 = max(0, math.floor(cy - hy)), min(S, math.ceil(cy + hy) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            scx, scy = (pw - 1) / 2.0, (ph - 1) / 2.0
            fx = float(tr.flipx[f, i])                              # the card flip: x scaled before the rotation
            M = np.array([[sc * c * fx, -sc * s, (cx - x0) - (sc * c * fx * scx - sc * s * scy)],
                          [sc * s * fx, sc * c, (cy - y0) - (sc * s * fx * scx + sc * c * scy)]])
            buf = cv2.warpAffine(lvl, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            if i in turn:                                          # linear, so it is exact on premultiplied colour; alpha untouched
                buf[..., :3] = np.clip(buf[..., :3] @ turn[i].T, 0.0, buf[..., 3:4])
            op = float(tr.opacity[f, i])
            if op < 0.999:
                buf *= op
            roi = canvas[y0:y1, x0:x1]
            roi *= 1.0 - buf[..., 3:4]
            roi += buf
            dirty = (x0, y0, x1, y1) if dirty is None else (min(dirty[0], x0), min(dirty[1], y0), max(dirty[2], x1), max(dirty[3], y1))
        if dirty is not None:
            x0, y0, x1, y1 = dirty
            reg = canvas[y0:y1, x0:x1]
            a = reg[..., 3]
            a8 = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
            rgb = np.clip(reg[..., :3] * (255.0 / np.maximum(a, 1e-6))[..., None] + 0.5, 0, 255).astype(np.uint8)
            rgb[a8 == 0] = 0
            frames[f, y0:y1, x0:x1, :3] = rgb
            frames[f, y0:y1, x0:x1, 3] = a8
            reg[...] = 0.0
        if on_frame is not None:
            on_frame(f, F)
    return frames


# ---- output ---------------------------------------------------------------------------------------------------------
def to_webm(frames: np.ndarray, cfg, path, fps: float | None = None, budget: int | None = None) -> dict:
    """Encode with the engine's own Telegram path (VP9 + alpha, `engine.video._encode_fit` walks the crf ladder to the best quality that fits
    `budget`, default `cfg.video_max_bytes`) and write the file to `path`. `fps` defaults to `cfg.video_max_fps`; pass the clip's own fps when it differs.
    Returns {bytes, crf, size_bytes, frames, fps, seconds, fits, encodes}. `bytes` is the file's content. If even the last rung is over the
    budget the file is still written and `fits` is False: format/size/codec limits are judged by the existing verifier, not here."""
    from . import ffmpeg as ff
    from .video import _encode_fit
    frames = np.ascontiguousarray(frames)
    if frames.ndim != 4 or frames.shape[3] != 4 or frames.dtype != np.uint8 or len(frames) == 0:
        raise ValueError("frames must be a non-empty uint8 array of shape (N, H, W, 4)")
    if not ff.has_vp9():
        raise RuntimeError("ffmpeg with libvpx-vp9 is not available")
    fps = float(fps if fps else cfg.video_max_fps)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    crf, data, encodes = _encode_fit(frames, fps, cfg, path, budget=budget)
    path.write_bytes(data)                                         # _encode_fit leaves the LAST attempt on disk, not necessarily the chosen one
    limit = budget if budget is not None else cfg.video_max_bytes
    return {"bytes": data, "crf": crf, "size_bytes": len(data), "frames": len(frames), "fps": fps,
            "seconds": round(len(frames) / fps, 3), "fits": len(data) <= limit, "encodes": encodes}


def preview_webp(frames: np.ndarray, size: int = 256, fps: float = 30.0) -> bytes:
    """An animated, looping, alpha-keeping WebP of the clip at `size` x `size` for the browser preview (Pillow only). The frame durations
    are spread so the loop is exactly `len(frames) / fps` long (33/34 ms alternating at 30 fps). The encoder may merge identical
    neighbouring frames (the empty end), so count the duration, not the frames, when judging it."""
    if frames.ndim != 4 or frames.shape[3] != 4 or len(frames) == 0:
        raise ValueError("frames must be a non-empty array of shape (N, H, W, 4)")
    size = int(size)
    if size < 1:
        raise ValueError("size must be positive")
    src = frames if frames.shape[1] == size and frames.shape[2] == size else None
    ims = []
    for fr in frames:
        if src is None:
            a = fr[..., 3:4].astype(np.float32)                    # shrink premultiplied: straight-alpha resizing would bleed the colour of invisible pixels
            pm = cv2.resize(np.concatenate([fr[..., :3].astype(np.float32) * a / 255.0, a], -1), (size, size), interpolation=cv2.INTER_AREA)
            al = pm[..., 3:4]
            rgb = np.where(al > 0.5, pm[..., :3] * 255.0 / np.maximum(al, 1e-3), 0.0)
            fr = np.clip(np.concatenate([rgb, al], -1) + 0.5, 0, 255).astype(np.uint8)
        ims.append(Image.fromarray(np.ascontiguousarray(fr), "RGBA"))
    n = len(ims)
    durations = [max(1, round((i + 1) * 1000.0 / fps) - round(i * 1000.0 / fps)) for i in range(n)]
    buf = io.BytesIO()
    ims[0].save(buf, "WEBP", save_all=True, append_images=ims[1:], duration=durations, loop=0, quality=80, method=2, exact=True)
    return buf.getvalue()


def png_bytes(img: np.ndarray) -> bytes:
    """An RGBA uint8 array as PNG bytes (Pillow, deterministic for the same pixels)."""
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(img), "RGBA").save(buf, "PNG")
    return buf.getvalue()


# ---- default sprite -----------------------------------------------------------------------------------------------------
def default_sprite(px: int = 128) -> np.ndarray:
    """A procedural fallback sprite (RGBA uint8): a soft four-point sparkle on transparency. Deterministic, free, no network.

    Bank reactions start life with this while they have no drawn sprites yet (a person replaces it later through Generate more).
    Pure numpy / OpenCV."""
    px = int(px)
    if px < 8:
        raise ValueError("px must be at least 8")
    yy, xx = np.mgrid[0:px, 0:px].astype(np.float64)
    cx = cy = (px - 1) / 2.0
    dx, dy = xx - cx, yy - cy
    r = np.sqrt(dx * dx + dy * dy) / (px / 2.0)
    glow = np.clip(1.0 - r, 0.0, 1.0) ** 2.2
    arm = np.clip(1.0 - np.abs(dx) / (px * 0.045), 0.0, 1.0) * np.clip(1.0 - np.abs(dy) / (px * 0.42), 0.0, 1.0)
    arm = np.maximum(arm, np.clip(1.0 - np.abs(dy) / (px * 0.045), 0.0, 1.0) * np.clip(1.0 - np.abs(dx) / (px * 0.42), 0.0, 1.0))
    core = np.clip(1.0 - r / 0.16, 0.0, 1.0) ** 1.5
    alpha = np.clip(glow * 0.55 + arm * 0.75 + core, 0.0, 1.0)
    rgb = np.full((px, px, 3), 255.0)
    rgba = np.dstack([rgb, alpha * 255.0])
    return np.clip(rgba + 0.5, 0, 255).astype(np.uint8)
