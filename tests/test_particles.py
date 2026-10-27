"""The particle-burst engine (engine/particles.py) and its checks (engine/effect_checks.py): physics, determinism, sprites, output, verdicts.
Synthetic sprites only: nothing here reads a media file from out/, reaches a provider or opens a socket."""
import hashlib
import io
import json
import math
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from stickerloom.engine import effect_checks as fx
from stickerloom.engine import ffmpeg as ff
from stickerloom.engine import particles as pt
from stickerloom.engine.config import EngineConfig

ROOT = Path(__file__).resolve().parent.parent       # the stickerloom/ folder: `import stickerloom` resolves to this checkout
CFG = EngineConfig()


def _sprite(mask, color):
    """Straight alpha: the colour is the same everywhere (even under the transparent pixels), only the alpha carries the anti-aliased shape."""
    im = np.zeros(mask.shape + (4,), np.uint8)
    im[..., :3] = color
    im[..., 3] = mask
    return im


def disc(size, color, margin=0):
    mask = np.zeros((size + 2 * margin, size + 2 * margin), np.uint8)
    c = size // 2 + margin
    cv2.circle(mask, (c, c), size // 2 - 3, 255, -1, cv2.LINE_AA)
    return _sprite(mask, color)


def star(size, color):
    mask = np.zeros((size, size), np.uint8)
    pts = []
    for k in range(10):
        r = size * 0.46 if k % 2 == 0 else size * 0.2
        t = -math.pi / 2 + k * math.pi / 5
        pts.append((size / 2 + r * math.cos(t), size / 2 + r * math.sin(t)))
    cv2.fillPoly(mask, [np.round(np.array(pts) * 16).astype(np.int32)], 255, cv2.LINE_AA, 4)
    return _sprite(mask, color)


COLORS = [(250, 200, 30), (220, 40, 40), (40, 120, 230), (30, 200, 90)]
SPRITES = [disc(300, COLORS[0]), star(400, COLORS[1]), disc(120, COLORS[2]), star(200, COLORS[3])]


def digest(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def cov(frames):
    return (frames[..., 3] >= fx.ALPHA_ON).mean((1, 2))


def centroid_y(frames, lo, hi):
    """Alpha-weighted mean y (0..1) of the opaque pixels of frames lo..hi-1."""
    a = frames[lo:hi, ..., 3].astype(np.float64)
    rows = a.sum(axis=(0, 2))
    return float((rows * np.arange(a.shape[1])).sum() / rows.sum() / a.shape[1])


def small(**kw):
    """Cheap params (256 px canvas) for the physics tests."""
    return pt.ParticleParams(size=256, **kw)


class ParamsTests(unittest.TestCase):
    def test_defaults_are_the_spec(self):
        p = pt.ParticleParams()
        self.assertEqual((p.gravity, p.magnitude, p.vortex, p.count), (1.0, 1.0, 0.0, 28))
        self.assertEqual((p.size_min, p.size_max, p.spin, p.lifetime, p.spread, p.pop), (0.10, 0.22, 1.0, 2.2, 360.0, 0.25))
        self.assertEqual((p.seed, p.fps, p.duration, p.size, p.origin, p.show_source), (1, 30, 3.0, 512, (0.5, 0.5), False))
        self.assertEqual(p.clamped(), p)                        # the defaults are inside every range

    def test_clamping(self):
        p = pt.ParticleParams(gravity=99, magnitude=-1, vortex=-9, count=2, spin=7, lifetime=10, spread=720, pop=0.0,
                              size_min=0.5, size_max=0.1, origin=(-1, 3), duration=9, fps=500, size=99999).clamped()
        self.assertEqual((p.gravity, p.magnitude, p.vortex, p.count, p.spin), (3.0, 0.0, -2.0, 4, 3.0))
        self.assertEqual(pt.ParticleParams(count=500).clamped().count, 80)
        self.assertEqual((p.spread, p.duration, p.origin, p.fps), (360.0, 3.0, (0.0, 1.0), 60.0))
        low = pt.ParticleParams(fps=1, duration=0.1).clamped()
        self.assertEqual((low.fps, low.duration), (20.0, 1.0))
        self.assertAlmostEqual(p.lifetime, p.duration - 0.4)    # a particle always dies before the clip's quiet tail
        self.assertGreaterEqual(p.size_max, p.size_min)
        self.assertGreater(p.pop, 0)
        self.assertEqual(p.size, 1024)
        self.assertEqual(p.clamped(), p)                        # idempotent
        self.assertTrue(0 <= pt.ParticleParams(seed=-5).clamped().seed < 2 ** 32)

    def test_nonsense_raises(self):
        for kw in ({"count": 0}, {"count": -3}, {"size": 0}, {"size": -5}, {"fps": 0}, {"duration": 0}, {"gravity": float("nan")},
                   {"magnitude": float("inf")}, {"vortex": "swirl"}, {"origin": (0.5,)}, {"origin": 3}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                pt.ParticleParams(**kw).clamped()
        with self.assertRaises(ValueError):
            pt.simulate(SPRITES, pt.ParticleParams(count=0))
        with self.assertRaises(ValueError):
            pt.trajectories(pt.ParticleParams(), n_sprites=0)

    def test_from_dict_and_presets(self):
        p = pt.ParticleParams.from_dict({"gravity": 0.5, "origin": [0.2, 0.3]}).clamped()
        self.assertEqual(p.gravity, 0.5)
        self.assertEqual(p.origin, (0.2, 0.3))
        self.assertEqual(json.loads(json.dumps(pt.ParticleParams().to_dict()))["origin"], [0.5, 0.5])
        with self.assertRaises(ValueError):
            pt.ParticleParams.from_dict({"gravity": 1, "wind": 3})
        self.assertEqual(set(pt.PRESETS), {"burst", "fountain", "vortex", "rain", "confetti"})
        self.assertEqual(pt.preset("burst"), pt.ParticleParams())
        self.assertEqual(pt.preset("vortex", seed=9).seed, 9)
        self.assertEqual(pt.preset("fountain", count=10).count, 10)
        with self.assertRaises(ValueError):
            pt.preset("nope")
        with self.assertRaises(ValueError):
            pt.preset("burst", wind=1)


class SpriteTests(unittest.TestCase):
    P = small(count=12)

    def test_validation_errors(self):
        empty = np.zeros((20, 20, 4), np.uint8)
        haze = empty.copy(); haze[..., 3] = 3                   # below the trim floor: not a subject
        for bad in ([], None, [empty], [empty, haze], [np.zeros((10, 10, 3), np.uint8)], [np.zeros((10, 10, 4), np.float32)],
                    [np.zeros((10, 4), np.uint8)], ["sprite"]):
            with self.subTest(bad=repr(bad)[:40]), self.assertRaises(ValueError):
                pt.simulate(bad, self.P)

    def test_transparent_sprites_are_ignored(self):
        empty = np.zeros((20, 20, 4), np.uint8)
        a = pt.simulate([disc(100, COLORS[0])], self.P)
        b = pt.simulate([empty, disc(100, COLORS[0]), empty], self.P)
        self.assertEqual(digest(a), digest(b))                  # the blanks neither crash nor shift the round-robin

    def test_trimmed_to_the_alpha_bbox(self):
        padded = disc(100, COLORS[0], margin=300)               # a 700 px image with a 100 px disc in the middle
        a = pt.simulate([padded], self.P)
        b = pt.simulate([padded[290:410, 290:410]], self.P)    # the same disc with a 10 px margin: trimmed to the same bbox
        self.assertEqual(digest(a), digest(b))

    def test_every_sprite_appears(self):
        frames = pt.simulate(SPRITES, small(count=28, seed=4))
        for col in COLORS:
            m = (frames[..., 3] == 255) & (np.abs(frames[..., :3].astype(int) - col).max(-1) <= 3)
            self.assertGreater(int(m.sum()), 50, col)

    def test_alpha_is_straight(self):
        col = (200, 100, 50)
        frames = pt.simulate([disc(200, col)], small(count=10, seed=2))[20:40]
        a = frames[..., 3]
        solid = a >= 200
        edge = (a >= 40) & (a < 200)
        self.assertGreater(int(solid.sum()), 1000)
        self.assertGreater(int(edge.sum()), 100)
        for m, tol in ((solid, 3), (edge, 10)):                 # colour does not darken with the alpha: un-premultiplied
            self.assertLessEqual(int(np.abs(frames[m][:, :3].astype(int) - col).max()), tol)
        self.assertEqual(int(frames[a == 0][:, :3].max()), 0)  # nothing under the transparent pixels

    def test_progress_callback(self):
        seen = []
        pt.simulate(SPRITES, self.P, on_frame=lambda i, n: seen.append((i, n)))
        self.assertEqual(seen, [(i, 90) for i in range(90)])


class SimulateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = small(count=24, seed=3)
        cls.frames = pt.simulate(SPRITES, cls.p)

    def test_shape_and_dtype(self):
        self.assertEqual(self.frames.shape, (90, 256, 256, 4))
        self.assertEqual(self.frames.dtype, np.uint8)
        self.assertEqual(pt.simulate(SPRITES, small(fps=24, duration=2.0, count=8)).shape, (48, 256, 256, 4))

    def test_starts_and_ends_with_nothing(self):
        self.assertFalse(self.frames[0].any())
        self.assertFalse(self.frames[-3:].any())
        self.assertFalse(self.frames[-6:].any())                # every particle is dead before duration - 0.2 s
        self.assertGreater(cov(self.frames).max(), 0.05)        # ... and something happens in between

    def test_by_construction_for_every_seed_and_shape(self):
        for seed in range(6):
            for kw in ({}, {"lifetime": 3.0, "count": 80}, {"duration": 1.0, "count": 30}, {"fps": 24, "magnitude": 3, "gravity": -2},
                       {"fps": 20, "duration": 1.0}, {"fps": 60, "duration": 2.0, "lifetime": 9.0}):
                p = small(seed=seed, **kw)
                tr = pt.trajectories(p, 4)
                self.assertTrue((tr.spawn >= 3).all())          # nothing is born before frame 3: the start is empty
                self.assertFalse(tr.visible[:3].any())
                dead_from = math.ceil((tr.p.duration - 0.2) * tr.p.fps)
                self.assertFalse(tr.visible[dead_from:].any(), (seed, kw))
                self.assertGreaterEqual(tr.frames - dead_from, 3)
                self.assertTrue((tr.spawn * (1 / tr.p.fps) + tr.life <= tr.p.duration - 0.2 + 1e-9).all())

    def test_deterministic(self):
        a = pt.simulate(SPRITES, small(count=16, seed=7))
        b = pt.simulate(SPRITES, small(count=16, seed=7))
        c = pt.simulate(SPRITES, small(count=16, seed=8))
        self.assertEqual(digest(a), digest(b))
        self.assertNotEqual(digest(a), digest(c))
        t1, t2 = pt.trajectories(small(seed=7), 4), pt.trajectories(small(seed=7), 4)
        self.assertTrue((t1.pos == t2.pos).all())

    def test_count_and_size_change_the_clip(self):
        few = cov(pt.simulate(SPRITES, small(count=5, seed=5))).sum()
        many = cov(pt.simulate(SPRITES, small(count=60, seed=5))).sum()
        self.assertGreater(many, few * 3)
        tiny = cov(pt.simulate(SPRITES, small(size_min=0.05, size_max=0.07, seed=5))).sum()
        big = cov(pt.simulate(SPRITES, small(size_min=0.22, size_max=0.28, seed=5))).sum()
        self.assertGreater(big, tiny * 4)
        self.assertEqual(pt.trajectories(small(count=33), 4).pos.shape[1], 33)

    def test_the_size_slider_scales_how_big_the_particles_look_and_its_default_changes_nothing(self):
        base = pt.simulate(SPRITES, small(seed=5))
        self.assertEqual(digest(base), digest(pt.simulate(SPRITES, small(seed=5, particle_size=1.0))), "Size 1 is the clip as it was")
        half = cov(pt.simulate(SPRITES, small(seed=5, particle_size=0.5))).sum()
        double = cov(pt.simulate(SPRITES, small(seed=5, particle_size=2.0))).sum()
        self.assertGreater(double, cov(base).sum() * 2)
        self.assertLess(half, cov(base).sum() * 0.5)
        self.assertEqual((small(particle_size=9).clamped().particle_size, small(particle_size=0).clamped().particle_size), (3.0, 0.25))

    def test_gravity_sign(self):
        slow = dict(magnitude=0.2, count=24, seed=3)
        down = pt.simulate(SPRITES, small(gravity=1.0, **slow))
        up = pt.simulate(SPRITES, small(gravity=-1.0, **slow))
        d_early, d_late = centroid_y(down, 8, 16), centroid_y(down, 24, 34)
        u_early, u_late = centroid_y(up, 8, 16), centroid_y(up, 24, 34)
        self.assertGreater(d_late, d_early + 0.05)              # positive gravity: later pieces are lower on the canvas
        self.assertLess(u_late, u_early - 0.05)                 # negative: they float up
        self.assertGreater(d_late, 0.5)
        self.assertLess(u_late, 0.5)

    def test_vortex_sign_flips_the_angular_momentum(self):
        def momentum(vortex):
            tr = pt.trajectories(small(vortex=vortex, gravity=0.0, count=40, seed=2), 4)
            rel = tr.pos[:-1] - 0.5
            vel = (tr.pos[1:] - tr.pos[:-1]) * tr.p.fps
            both = tr.visible[:-1] & tr.visible[1:]
            return float(((rel[..., 0] * vel[..., 1] - rel[..., 1] * vel[..., 0])[both]).mean())
        cw, none, ccw = momentum(1.0), momentum(0.0), momentum(-1.0)
        self.assertGreater(cw, 0.05)
        self.assertLess(ccw, -0.05)
        self.assertLess(abs(none), 0.02)                        # a plain burst has no net swirl
        self.assertGreater(momentum(2.0), cw)                   # stronger vortex, more swirl

    def test_magnitude_zero_stays_at_the_origin(self):
        p = small(magnitude=0.0, gravity=0.0, vortex=0.0, count=20, seed=6, origin=(0.3, 0.6))
        tr = pt.trajectories(p, 4)
        self.assertTrue(np.allclose(tr.pos, np.array([0.3, 0.6])))
        frames = pt.simulate(SPRITES, p)
        ys, xs = np.where(frames[..., 3].max(0) > 0)
        reach = math.hypot(max(abs(xs - 0.3 * 256).max(), 0), max(abs(ys - 0.6 * 256).max(), 0))
        self.assertLess(reach, 0.5 * math.sqrt(2) * 0.22 * 256 * 1.4)   # no farther than one big sprite's half diagonal
        far = pt.simulate(SPRITES, small(magnitude=1.0, gravity=0.0, count=20, seed=6, origin=(0.3, 0.6)))
        self.assertGreater(cov(far).sum(), 0)
        self.assertGreater(np.ptp(np.where(far[..., 3].max(0) > 0)[1]), 3 * np.ptp(xs))

    def test_spread_points_up(self):
        tr = pt.trajectories(small(spread=60.0, count=40, gravity=0.0, seed=1), 4)
        v = tr.pos[8, :, :] - 0.5                               # shortly after the first spawns
        moved = tr.visible[8]
        self.assertTrue((v[moved, 1] < 0).all())                # a 60 degree cone centred upward: every piece rises
        self.assertLess(float(np.abs(np.degrees(np.arctan2(v[moved, 0], -v[moved, 1]))).max()), 31)

    def test_pop_in_starts_from_nothing_and_grows(self):
        tr = pt.trajectories(small(count=8, seed=1), 2)
        i = 0
        born = int(tr.spawn[i])
        self.assertEqual(tr.scale[born, i], 0.0)
        self.assertGreater(tr.scale[born + 4, i], 0.3)
        self.assertGreater(tr.scale[born + 20, i], 0.9)
        self.assertEqual(tr.opacity[born + 10, i], 1.0)

    def test_back_to_front_by_size(self):
        tr = pt.trajectories(small(count=30, seed=1), 3)
        s = tr.size_frac[tr.draw_order]
        self.assertTrue((np.diff(s) >= 0).all())

    def test_presets_are_clean(self):
        for name in pt.PRESETS:
            with self.subTest(preset=name):
                p = replace(pt.preset(name), size=256)
                tr = pt.trajectories(p, 4)
                for arr in (tr.pos, tr.scale, tr.angle, tr.opacity, tr.life):
                    self.assertTrue(np.isfinite(arr).all())
                frames = pt.simulate(SPRITES, p)
                self.assertFalse(frames[0].any())
                self.assertFalse(frames[-3:].any())
                res = {c.id: c for c in fx.run(frames)}
                for cid in ("effect_empty_start", "effect_empty_end", "effect_has_burst", "effect_not_a_still"):
                    self.assertEqual(res[cid].verdict, "PASS", (name, res[cid].detail))

    def test_extreme_params_do_not_break(self):
        for kw in ({"gravity": 3, "magnitude": 3, "vortex": 2, "spin": 3, "count": 80}, {"gravity": -2, "magnitude": 3, "vortex": -2},
                   {"origin": (0.0, 1.0), "magnitude": 0.0}, {"lifetime": 99, "pop": 99}):
            with self.subTest(kw=kw):
                frames = pt.simulate(SPRITES, small(**kw))
                self.assertFalse(frames[0].any())
                self.assertFalse(frames[-3:].any())


class PerformanceTests(unittest.TestCase):
    CEILING = 6.0     # generous: a laptop does 512 px / 30 particles in about 0.5 s and 256 px in about 0.25 s

    def test_512_and_256(self):
        for size in (512, 256):
            t0 = time.perf_counter()
            frames = pt.simulate(SPRITES, pt.ParticleParams(count=30, size=size))
            dt = time.perf_counter() - t0
            print(f"\n  simulate: 90 frames, 30 particles, {size} px in {dt:.2f} s")
            self.assertEqual(frames.shape, (90, size, size, 4))
            self.assertLess(dt, self.CEILING)


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = pt.simulate(SPRITES, pt.ParticleParams(seed=5))        # the default 512 px clip

    def test_webm_round_trip(self):
        if not ff.has_vp9():
            self.skipTest("ffmpeg with libvpx-vp9 is not available: install a full ffmpeg build or `pip install imageio-ffmpeg`")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "fx.webm"
            info = pt.to_webm(self.frames, CFG, path)
            print(f"\n  webm: {info['size_bytes'] / 1024:.1f} KB at crf {info['crf']} ({info['encodes']} encodes)")
            self.assertEqual((info["frames"], info["fps"], info["seconds"]), (90, 30.0, 3.0))
            self.assertTrue(info["fits"])
            self.assertLessEqual(info["size_bytes"], CFG.video_max_bytes)
            self.assertEqual(path.read_bytes(), info["bytes"])
            self.assertEqual(len(info["bytes"]), info["size_bytes"])
            self.assertIn(info["crf"], CFG.crf_ladder)
            dec = ff.decode_full(path, 512, 512, 200, None)
            self.assertEqual(len(dec), 90)
            self.assertLess(int(dec[0][..., 3].max()), fx.ALPHA_ON)          # the start and the end survive the codec as nothing
            self.assertLess(int(dec[-1][..., 3].max()), fx.ALPHA_ON)
            res = {c.id: c.verdict for c in fx.run(dec)}
            self.assertEqual(set(res.values()), {"PASS"}, res)
            probe = ff.probe(path, vp9_native=True)
            self.assertEqual((probe["width"], probe["height"]), (512, 512))

    def test_webm_rejects_bad_frames(self):
        with self.assertRaises(ValueError):
            pt.to_webm(np.zeros((3, 8, 8, 3), np.uint8), CFG, "x.webm")

    def test_preview_webp(self):
        data = pt.preview_webp(self.frames, 192)
        self.assertEqual((data[:4], data[8:12]), (b"RIFF", b"WEBP"))
        im = Image.open(io.BytesIO(data))
        self.assertEqual(im.size, (192, 192))
        self.assertTrue(getattr(im, "is_animated", False))
        total = 0
        for i in range(im.n_frames):
            im.seek(i)
            im.load()                                           # Pillow fills info["duration"] when the frame is loaded
            total += im.info["duration"]
        self.assertAlmostEqual(total, 3000, delta=40)           # exactly one 3 s loop (identical neighbours may be merged: count time, not frames)
        self.assertEqual(im.info.get("loop"), 0)
        im.seek(0)
        self.assertLess(int(np.array(im.convert("RGBA"))[..., 3].max()), fx.ALPHA_ON)
        self.assertEqual(Image.open(io.BytesIO(pt.preview_webp(self.frames))).size, (256, 256))

    def test_preview_webp_same_size_and_errors(self):
        small_frames = pt.simulate(SPRITES, small(count=6, duration=1.0))
        self.assertEqual(Image.open(io.BytesIO(pt.preview_webp(small_frames, 256))).size, (256, 256))
        with self.assertRaises(ValueError):
            pt.preview_webp(np.zeros((0, 8, 8, 4), np.uint8))


class EffectCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = pt.simulate(SPRITES, small(count=24, seed=3))

    def verdicts(self, frames, **kw):
        res = fx.run(frames, **kw)
        self.assertTrue(all(c.verdict in ("PASS", "WARN") for c in res))      # never BLOCK
        return {c.id: c.verdict for c in res}

    def with_blob(self, idx, frames=None):
        out = (self.good if frames is None else frames).copy()
        for i in idx:
            cv2.circle(out[i], (128, 128), 60, (200, 50, 50, 255), -1)
        return out

    def test_good_clip_passes_everything(self):
        res = self.verdicts(self.good, cell_bounds=(0, 0, 256, 256))
        self.assertEqual(res, {"effect_empty_start": "PASS", "effect_empty_end": "PASS", "effect_has_burst": "PASS",
                               "effect_inside_cell": "PASS", "effect_not_a_still": "PASS"})
        self.assertNotIn("effect_inside_cell", self.verdicts(self.good))      # only checked when a cell is given

    def test_starts_non_empty(self):
        res = self.verdicts(self.with_blob([0, 1]))
        self.assertEqual(res["effect_empty_start"], "WARN")
        self.assertEqual(res["effect_empty_end"], "PASS")
        c = next(c for c in fx.run(self.with_blob([2])) if c.id == "effect_empty_start")
        self.assertEqual((c.verdict, c.data["frame"]), ("WARN", 2))           # the third frame counts too, the fourth does not
        self.assertEqual(self.verdicts(self.with_blob([3]))["effect_empty_start"], "PASS")

    def test_never_empties(self):
        res = self.verdicts(self.with_blob(range(60, 90)))
        self.assertEqual(res["effect_empty_end"], "WARN")
        self.assertEqual(res["effect_empty_start"], "PASS")

    def test_empty_throughout(self):
        res = self.verdicts(np.zeros((90, 256, 256, 4), np.uint8))
        self.assertEqual(res["effect_has_burst"], "WARN")
        self.assertEqual(res["effect_not_a_still"], "WARN")
        self.assertEqual((res["effect_empty_start"], res["effect_empty_end"]), ("PASS", "PASS"))

    def test_too_brief_or_too_faint(self):
        brief = np.zeros((90, 256, 256, 4), np.uint8)
        for i in range(30, 38):                                 # 8 frames of a big blob: peak is fine, the clip is not
            cv2.circle(brief[i], (128, 128), 60, (200, 50, 50, 255), -1)
        self.assertEqual(self.verdicts(brief)["effect_has_burst"], "WARN")
        faint = np.zeros((90, 256, 256, 4), np.uint8)
        for i in range(10, 80):
            cv2.circle(faint[i], (128, 128), 6, (200, 50, 50, 255), -1)
        self.assertEqual(self.verdicts(faint)["effect_has_burst"], "WARN")

    def test_leaves_its_cell(self):
        res = self.verdicts(self.good, cell_bounds=(80, 80, 176, 176))
        self.assertEqual(res["effect_inside_cell"], "WARN")
        c = next(c for c in fx.run(self.good, cell_bounds=(80, 80, 176, 176)) if c.id == "effect_inside_cell")
        self.assertGreater(c.value, fx.OUTSIDE_MAX)
        self.assertTrue(0 <= c.data["frame"] < 90)
        self.assertIn(f"frame {c.data['frame']}", c.detail)
        tight = np.zeros((90, 256, 256, 4), np.uint8)           # a dot that stays inside the cell is fine
        for i in range(90):
            cv2.circle(tight[i], (100 + i // 3, 128), 20, (1, 2, 3, 255), -1)
        self.assertEqual(self.verdicts(tight, cell_bounds=(64, 64, 192, 192))["effect_inside_cell"], "PASS")

    def test_a_still_is_a_still(self):
        still = np.zeros((90, 256, 256, 4), np.uint8)
        still[3:87, 100:150, 100:150] = (9, 9, 9, 255)
        self.assertEqual(self.verdicts(still)["effect_not_a_still"], "WARN")
        mover = np.zeros((90, 256, 256, 4), np.uint8)           # same coverage every frame, but it travels: motion
        for i in range(3, 87):
            mover[i, 100:150, 20 + i * 2:70 + i * 2] = (9, 9, 9, 255)
        self.assertEqual(self.verdicts(mover)["effect_not_a_still"], "PASS")

    def test_coverage_curve_and_serialisation(self):
        curve = fx.coverage_curve(self.good)
        self.assertEqual(len(curve), 90)
        self.assertEqual((curve[0], curve[-1]), (0.0, 0.0))
        self.assertTrue(all(0.0 <= c <= 1.0 for c in curve))
        self.assertAlmostEqual(curve[int(np.argmax(curve))], float(cov(self.good).max()))
        half = np.zeros((2, 10, 10, 4), np.uint8); half[1, :5, :, 3] = 255
        self.assertEqual(fx.coverage_curve(half), [0.0, 0.5])
        for c in fx.run(self.good, cell_bounds=(0, 0, 256, 256)):
            d = json.loads(json.dumps(c.to_dict()))
            self.assertEqual(set(d), {"id", "verdict", "detail", "value", "limit", "data"})
            self.assertEqual(c.ok, c.verdict == "PASS")

    def test_malformed_input_raises(self):
        for bad in (np.zeros((0, 8, 8, 4), np.uint8), np.zeros((3, 8, 8, 3), np.uint8), np.zeros((8, 8, 4), np.uint8), "frames"):
            with self.subTest(bad=repr(bad)[:30]), self.assertRaises(ValueError):
                fx.run(bad)
        with self.assertRaises(ValueError):
            fx.run(self.good, cell_bounds=(50, 50, 50, 90))


class BoundaryTests(unittest.TestCase):
    def test_engine_boundary(self):
        code = ("import sys, stickerloom.engine.particles, stickerloom.engine.effect_checks;"
                "bad=[m for m in ('fastapi','psycopg','langgraph','anthropic','pydantic','redis','openai') if m in sys.modules];"
                "sys.exit(1 if bad else 0)")
        self.assertEqual(subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode, 0)


if __name__ == "__main__":
    unittest.main()


class TrimSpriteTests(unittest.TestCase):
    """P6 of the UI/UX spec: a particle is stored as a TIGHT sprite (its alpha bounding box plus a little air), never as a 512 px sticker canvas."""

    def test_the_sprite_is_cropped_to_what_is_drawn_plus_a_little_air(self):
        from stickerloom.engine.particles import trim_sprite
        cell = np.zeros((600, 600, 4), np.uint8)
        cell[200:320, 100:260] = (250, 40, 40, 255)                      # 120 high, 160 wide
        cell[5, 5] = (255, 255, 255, 3)                                  # haze at or under ALPHA_TRIM is not subject
        out = trim_sprite(cell, pad=2)
        self.assertEqual(out.shape, (124, 164, 4))
        self.assertEqual(tuple(out[2, 2]), (250, 40, 40, 255))
        self.assertEqual(int(out[0, 0, 3]), 0, "the air is transparent")

    def test_a_pad_never_runs_off_the_picture_and_an_empty_cell_is_none(self):
        from stickerloom.engine.particles import trim_sprite
        edge = np.zeros((50, 50, 4), np.uint8)
        edge[0:10, 0:10] = (1, 2, 3, 255)
        self.assertEqual(trim_sprite(edge, pad=4).shape, (14, 14, 4))
        self.assertIsNone(trim_sprite(np.zeros((20, 20, 4), np.uint8)))
        with self.assertRaises(ValueError):
            trim_sprite(np.zeros((20, 20, 3), np.uint8))


class ErosionTests(unittest.TestCase):
    def test_erosion_is_default_off_and_strictly_validated(self):
        self.assertEqual(pt.ParticleParams().erosion_px, 0)
        self.assertEqual(pt.ParticleParams.from_dict({"erosion_px": 99}).clamped().erosion_px, 6)
        with self.assertRaises(ValueError):
            pt.ParticleParams(erosion_px=float("nan")).clamped()

    def test_keyed_alpha_is_eroded_without_mutating_the_source(self):
        image = np.zeros((20, 20, 4), np.uint8)
        image[3:17, 3:17] = (220, 80, 40, 255)
        original = image.copy()
        self.assertIs(pt.erode_sprites([image], 0)[0], image)
        result = pt.erode_sprites([image], 1)[0]
        self.assertLess(np.count_nonzero(result[..., 3]), np.count_nonzero(image[..., 3]))
        self.assertTrue(np.array_equal(image, original))
        self.assertTrue(np.array_equal(result[10, 10], image[10, 10]))

    def test_every_animated_frame_keeps_its_fps_and_receives_the_same_trim(self):
        image = np.full((2, 20, 20, 4), 255, np.uint8)
        image[:, :3, :, 3] = 0
        animation = pt.AnimatedSprite(image, 24)
        result = pt.erode_sprites([animation], 1)[0]
        self.assertEqual(result.fps, 24)
        self.assertEqual(result.frames.shape, image.shape)
        self.assertTrue(np.array_equal(result.frames[0], result.frames[1]))
        self.assertLess(np.count_nonzero(result.frames[..., 3]), np.count_nonzero(image[..., 3]))
