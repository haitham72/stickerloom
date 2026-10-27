"""Per-emoji reaction motions (reactions branch): every predefined emoji owns one fixed animation, each preset is measurably
distinct, the Size in / Size out envelope and the hue variation are seeded, and the Lottie export plays the real sprite.
Numbers only: no media file is opened (rule 9)."""
import base64
import io
import json
import math
import unittest

import numpy as np
from PIL import Image

from stickerloom.engine import emoji_motion as em
from stickerloom.engine import emoji_glyph as glyph
from stickerloom.engine import lottie
from stickerloom.engine import particles as p

PRESETS = ("burst", "fountain", "vortex", "rain", "confetti")


def signature(params: p.ParticleParams) -> dict:
    """What a preset looks like, as numbers from trajectories(): the tangential / radial speed ratio about the origin, the median
    turns a particle makes around it, how far out 90% of the visible samples are, the share of visible motion that goes down,
    and the mean vertical speed over each particle's first 0.2 s (negative = launched upward)."""
    tr = p.trajectories(params)
    o = np.array(tr.p.origin)
    rel = tr.pos - o
    v = np.diff(tr.pos, axis=0) * tr.p.fps
    vis = tr.visible[1:] & tr.visible[:-1]
    r = np.linalg.norm(rel[1:], axis=-1) + 1e-9
    rad = (v * rel[1:]).sum(-1) / r
    tan = (rel[1:, :, 0] * v[..., 1] - rel[1:, :, 1] * v[..., 0]) / r
    th = np.unwrap(np.arctan2(rel[..., 1], rel[..., 0]), axis=0)
    turns = [np.ptp(th[tr.visible[:, i], i]) / (2 * math.pi) for i in range(tr.p.count) if tr.visible[:, i].any()]
    early = int(round(0.2 * tr.p.fps))
    launch = [v[s:s + early, i, 1].mean() for i, s in enumerate(tr.spawn.tolist())]
    return {"ratio": float(np.abs(tan[vis]).mean() / np.abs(rad[vis]).mean()), "turns": float(np.median(turns)),
            "r90": float(np.percentile(r[vis], 90)), "down": float((v[..., 1][vis] > 0).mean()), "launch_vy": float(np.mean(launch))}


def assert_signature(t: unittest.TestCase, preset: str, params: p.ParticleParams, label: str):
    q, sig = params.clamped(), signature(params)
    if preset == "burst":
        t.assertEqual(q.spread, 360.0, label)
        t.assertEqual(q.origin, (0.5, 0.5), label)
        t.assertLess(sig["ratio"], 0.6, f"{label}: a burst is radial ({sig})")
    elif preset == "fountain":
        t.assertEqual(q.origin, (0.5, 0.8), label)
        t.assertLessEqual(q.spread, 90.0, label)
        t.assertLess(sig["launch_vy"], -0.3, f"{label}: launched upward ({sig})")
        t.assertGreater(q.gravity, 0, f"{label}: and it falls back")
    elif preset == "vortex":
        t.assertGreaterEqual(abs(q.vortex), 1.5, label)
        t.assertLessEqual(q.magnitude, 0.6, label)
        t.assertGreater(sig["ratio"], 3.0, f"{label}: tangential well above radial ({sig})")
        t.assertGreaterEqual(sig["turns"], 2.0, f"{label}: two or more visible orbits ({sig})")
        t.assertLess(sig["r90"], 0.5, f"{label}: the swirl stays on the canvas ({sig})")
    elif preset == "rain":
        t.assertEqual(q.origin, (0.5, 0.1), label)
        t.assertLessEqual(q.magnitude, 0.5, label)
        t.assertGreater(q.gravity, 0, label)
        t.assertLessEqual(q.spread, 120.0, label)
        t.assertGreater(sig["down"], 0.8, f"{label}: it falls ({sig})")
    elif preset == "confetti":
        t.assertGreaterEqual(q.count, 48, label)
        t.assertLessEqual(q.gravity, 0.5, label)
        t.assertGreaterEqual(q.spin, 2.5, label)
        t.assertGreaterEqual(q.spread, 150.0, label)
        t.assertLess(sig["down"], 0.6, f"{label}: low gravity, it scatters rather than falls ({sig})")
    else:
        t.fail(f"unknown preset {preset}")


def sparkle_png(px: int = 64) -> bytes:
    return p.png_bytes(p.default_sprite(px))


class EmojiMotionTests(unittest.TestCase):
    def test_core_36_all_pinned(self):
        self.assertEqual(len(em.CORE_MOTIONS), 36)
        for token, row in em.CORE_MOTIONS.items():
            self.assertIn(row["preset"], PRESETS, token)
            p.preset(row["preset"], **row["params"])               # every pinned row is a valid simulator call

    def test_motion_for_token_is_deterministic(self):
        self.assertEqual(em.motion_for_token("laugh"), em.motion_for_token("laugh"))
        self.assertEqual(em.motion_for_token("laugh")["preset"], "fountain")
        a, b = em.motion_for_token("school"), em.motion_for_token("school")   # extended bank: hash fallback, still a known preset and stable
        self.assertEqual(a, b)
        self.assertIn(a["preset"], PRESETS)

    def test_each_preset_has_its_signature(self):
        for name in PRESETS:
            with self.subTest(preset=name):
                assert_signature(self, name, p.preset(name), name)

    def test_every_core_token_keeps_its_presets_signature(self):
        for token, row in em.CORE_MOTIONS.items():
            with self.subTest(token=token):
                assert_signature(self, row["preset"], p.preset(row["preset"], **row["params"]), token)

    def test_presets_are_distinct_from_each_other(self):
        sig = {n: signature(p.preset(n)) for n in PRESETS}
        self.assertGreater(sig["vortex"]["ratio"], 5 * max(sig[n]["ratio"] for n in ("burst", "rain", "confetti")), sig)
        self.assertGreater(sig["vortex"]["turns"], 5 * sig["burst"]["turns"], sig)
        self.assertGreater(sig["rain"]["down"], sig["confetti"]["down"] + 0.25, sig)
        self.assertLess(sig["fountain"]["launch_vy"], 0)
        self.assertGreater(sig["rain"]["launch_vy"], sig["fountain"]["launch_vy"])

    def test_vortex_sign_is_the_direction(self):
        cw, ccw = signature(p.preset("vortex")), signature(p.preset("vortex", vortex=-1.8))
        self.assertAlmostEqual(cw["turns"], ccw["turns"], delta=0.3)
        tr = p.trajectories(p.preset("vortex", vortex=-1.8))
        rel = tr.pos[:-1] - 0.5
        vel = np.diff(tr.pos, axis=0)
        both = tr.visible[:-1] & tr.visible[1:]
        self.assertLess(float((rel[..., 0] * vel[..., 1] - rel[..., 1] * vel[..., 0])[both].mean()), 0)


class EnvelopeAndHueTests(unittest.TestCase):
    def test_new_fields_are_strict_clamped_and_serialised(self):
        d = p.ParticleParams().to_dict()
        for k in ("size_in_dur", "size_out_dur", "hue_var"):
            self.assertEqual(d[k], 0.0, "the honest default reproduces the old look")
        q = p.ParticleParams(size_in_dur=9, size_out_dur=-1, hue_var=400, lifetime=1.2).clamped()
        self.assertEqual((q.size_in_dur, q.size_out_dur, q.hue_var), (0.6, 0.0, 180.0), "envelope <= lifetime/2, hue <= 180")
        self.assertEqual(p.ParticleParams(size_in_dur=9, lifetime=2.6).clamped().size_in_dur, 1.3)
        self.assertEqual(p.ParticleParams(size_in_dur=9, lifetime=2.6, duration=3).clamped().size_in_dur, 1.3)
        self.assertEqual(p.ParticleParams.from_dict(json.loads(json.dumps(q.to_dict()))).clamped(), q)
        with self.assertRaises(ValueError):
            p.ParticleParams(hue_var="lots").clamped()
        with self.assertRaises(ValueError):
            p.ParticleParams.from_dict({"hue": 10})

    def test_fade_out_start_duration_and_variation(self):
        d = p.ParticleParams().to_dict()
        self.assertEqual((d["fade_start"], d["fade_dur"], d["fade_var"]), (0.0, 0.0, 0.0), "off by default: an old burst keeps its bytes")
        q = p.ParticleParams(fade_start=-1, fade_dur=9, fade_var=5, duration=2).clamped()
        self.assertEqual((q.fade_start, q.fade_dur, q.fade_var), (0.0, 2.0, 1.0))
        fps = 30
        t = p.trajectories(p.preset("burst", seed=3, count=12, duration=2.0, fade_start=0.4, fade_dur=0.5))
        for i in range(12):
            s0 = int(t.spawn[i])
            self.assertEqual(float(t.opacity[s0 + 12, i]), 1.0, "100% until 0.4 s after birth")
            self.assertAlmostEqual(float(t.opacity[s0 + 12 + 6, i]), 0.6, places=6, msg="linear: 0.2 s into a 0.5 s fade = 60%")
            self.assertEqual(float(t.opacity[s0 + 12 + 15, i]), 0.0, "0% after 0.5 s")
        v = p.trajectories(p.preset("burst", seed=3, count=12, duration=2.0, fade_start=0.4, fade_dur=0.5, fade_var=0.3))
        np.testing.assert_array_equal(t.pos, v.pos, "the fade never moves a particle")
        gone = [int(np.flatnonzero(v.opacity[:, i] > 0)[-1] - v.spawn[i]) for i in range(12)]
        self.assertGreater(max(gone) - min(gone), 5, "variation: the particles fade at different times")
        late = p.trajectories(p.preset("burst", seed=3, count=12, duration=2.0, fade_start=1.5, fade_dur=2.0))
        self.assertEqual(float(late.opacity[int(1.8 * fps):].max()), 0.0, "always gone by the end, however late the fade is set")

    def test_envelope_zooms_in_from_nothing_and_out_to_nothing(self):
        base = p.preset("burst", count=12, seed=4, lifetime=2.0)
        env = p.preset("burst", count=12, seed=4, lifetime=2.0, size_in_dur=0.5, size_out_dur=0.4)
        a, b = p.trajectories(base), p.trajectories(env)
        np.testing.assert_array_equal(a.pos, b.pos, "the envelope never moves a particle")
        fps = a.p.fps
        for i in range(12):
            s, life = int(a.spawn[i]), float(a.life[i])
            k = np.arange(a.frames) - s
            age = k / fps
            ramp_in = np.clip(age / 0.5, 0, 1)
            ramp_out = np.clip((life - age) / 0.4, 0, 1)
            alive = (k >= 0) & (age < life)
            np.testing.assert_allclose(b.scale[alive, i], a.scale[alive, i] * ramp_in[alive] * ramp_out[alive], rtol=1e-9, atol=1e-12)
            mid = s + int(round(0.5 * fps))
            self.assertAlmostEqual(b.scale[s + int(round(0.25 * fps)), i] / a.scale[s + int(round(0.25 * fps)), i], 0.5, delta=0.04)
            self.assertAlmostEqual(b.scale[mid, i], a.scale[mid, i], delta=1e-9, msg="100% once the zoom-in is over")
        last = [int(np.flatnonzero(b.scale[:, i] > 0)[-1]) for i in range(12)]
        self.assertTrue(all(b.scale[f, i] < 0.1 * a.scale[f, i] + 1e-9 for i, f in enumerate(last)), "near nothing just before death")

    def test_zero_envelope_and_zero_hue_change_nothing(self):
        for name in PRESETS:
            with self.subTest(preset=name):
                a = p.trajectories(p.preset(name, seed=5))
                b = p.trajectories(p.preset(name, seed=5, size_in_dur=0, size_out_dur=0, hue_var=0))
                c = p.trajectories(p.preset(name, seed=5, hue_var=120))
                for x in ("pos", "scale", "angle", "opacity", "spawn", "life", "size_frac"):
                    np.testing.assert_array_equal(getattr(a, x), getattr(b, x))
                    np.testing.assert_array_equal(getattr(a, x), getattr(c, x), f"the hue draw comes last: {x} is unchanged")
                self.assertFalse(a.hue.any())

    def test_hue_is_seeded_bounded_and_leaves_alpha(self):
        a = p.trajectories(p.preset("burst", seed=8, hue_var=40))
        b = p.trajectories(p.preset("burst", seed=8, hue_var=40))
        c = p.trajectories(p.preset("burst", seed=9, hue_var=40))
        np.testing.assert_array_equal(a.hue, b.hue)
        self.assertFalse(np.array_equal(a.hue, c.hue))
        self.assertLessEqual(float(np.abs(a.hue).max()), 40.0)
        self.assertGreater(float(np.ptp(a.hue)), 20.0, "the particles differ")
        rgb = (np.arange(16 * 16 * 3) % 251).astype(np.uint8).reshape(16, 16, 3)
        img = np.dstack([rgb, np.full((16, 16), 200, np.uint8)])
        np.testing.assert_array_equal(p.hue_rotate(img, 0), img)
        turned = p.hue_rotate(img, 90)
        np.testing.assert_array_equal(turned[..., 3], img[..., 3])
        self.assertGreater(int(np.abs(turned[..., :3].astype(int) - rgb).max()), 30)
        np.testing.assert_allclose(p.hue_matrix(0), np.eye(3), atol=1e-6)
        grey = np.array([0.4, 0.4, 0.4], np.float32)
        np.testing.assert_allclose(p.hue_matrix(77) @ grey, grey, atol=1e-5, err_msg="grey has no hue to turn")

    def test_simulated_hue_turns_colour_not_alpha(self):
        red = np.zeros((24, 24, 4), np.uint8)
        red[4:20, 4:20] = (230, 30, 30, 255)
        kw = dict(count=8, seed=3, size=96)
        plain = p.simulate([red], p.preset("burst", **kw))
        tinted = p.simulate([red], p.preset("burst", hue_var=120, **kw))
        again = p.simulate([red], p.preset("burst", hue_var=120, **kw))
        np.testing.assert_array_equal(tinted, again, "same seed, same bytes")
        np.testing.assert_array_equal(plain[..., 3], tinted[..., 3], "alpha untouched")
        on = tinted[..., 3] > 200
        self.assertGreater(int(np.abs(tinted[..., :3][on].astype(int) - plain[..., :3][on].astype(int)).max()), 60)


@unittest.skipUnless(glyph.emoji_font(), "no colour emoji font on this machine")
class RenderEmojiTests(unittest.TestCase):
    def test_the_glyph_is_a_tight_coloured_rgba_sprite(self):
        a = glyph.render_emoji("\U0001F618", 128)
        self.assertEqual(a.dtype, np.uint8)
        self.assertEqual(a.ndim, 3)
        self.assertEqual(a.shape[2], 4)
        self.assertEqual(max(a.shape[:2]), 128)
        self.assertTrue((a[..., 3] > 200).sum() > 0.3 * a.shape[0] * a.shape[1], "mostly glyph, little air")
        solid = a[a[..., 3] > 200][:, :3]
        self.assertGreater(len(np.unique(solid // 16, axis=0)), 8, "a colour emoji, not one flat colour")
        np.testing.assert_array_equal(a, glyph.render_emoji("\U0001F618", 128), "deterministic")
        self.assertEqual(glyph.render_emoji("\U0001F618", 128).shape, a.shape)
        self.assertFalse(np.array_equal(a, glyph.render_emoji("\U0001F62D", 128)), "another emoji is another picture")

    def test_nonsense_is_refused(self):
        with self.assertRaises(ValueError):
            glyph.render_emoji("  ")
        with self.assertRaises(ValueError):
            glyph.render_emoji("\U0001F618", 4)


class LottieTests(unittest.TestCase):
    def test_layers_are_image_layers_of_one_shared_embedded_asset(self):
        params = p.preset("burst", count=6, seed=7)
        png = sparkle_png()
        doc = lottie.export_lottie(params, png)
        self.assertEqual(doc["v"], "5.7.4")
        self.assertEqual((doc["w"], doc["h"], doc["op"], doc["fr"]), (512, 512, 90, 30.0))
        self.assertEqual(len(doc["assets"]), 1, "the sprite is embedded once")
        asset = doc["assets"][0]
        self.assertTrue(asset["p"].startswith("data:image/png;base64,"))
        self.assertEqual(asset["e"], 1)
        img = Image.open(io.BytesIO(base64.b64decode(asset["p"].split(",", 1)[1])))
        self.assertEqual((img.mode, img.size), ("RGBA", (asset["w"], asset["h"])))
        self.assertEqual(len(doc["layers"]), 6)
        for layer in doc["layers"]:
            self.assertEqual(layer["ty"], 2, "an image layer, never a placeholder shape")
            self.assertEqual(layer["refId"], asset["id"])
            self.assertNotIn("shapes", layer)
            ip, op = layer["ip"], layer["op"]
            self.assertTrue(0 < ip < op <= doc["op"])
            for prop in ("o", "r", "p", "s"):
                ks = layer["ks"][prop]["k"]
                self.assertEqual([k["t"] for k in ks], list(range(ip, op)))
                self.assertTrue(all(k.get("h") == 1 for k in ks))
            self.assertEqual(layer["ks"]["a"]["k"][:2], [asset["w"] / 2, asset["h"] / 2])
        self.assertEqual(sorted(x["ind"] for x in doc["layers"]), list(range(1, 7)))

    def test_layer_scale_draws_the_simulated_size(self):
        params = p.preset("burst", count=4, seed=2)
        doc = lottie.export_lottie(params, sparkle_png(200))
        tr = p.trajectories(params)
        a = doc["assets"][0]
        top_first = tr.draw_order[::-1].tolist()                    # Lottie's first layer is on top: the biggest particle, drawn last by simulate()
        for layer, i in zip(doc["layers"], top_first):
            self.assertEqual(layer["ip"], int(tr.spawn[i]) + 1, "the layer starts when its particle first has a size")
            for k in layer["ks"]["s"]["k"]:                      # the drawn longest side is size_frac x 512 x the simulated scale
                want = tr.scale[k["t"], i] * 100 * tr.size_frac[i] * 512 / max(a["w"], a["h"])
                self.assertAlmostEqual(k["s"][0], want, delta=0.006)
            for k in layer["ks"]["p"]["k"]:
                self.assertAlmostEqual(k["s"][0], tr.pos[k["t"], i, 0] * 512, delta=0.051)

    def test_hue_variation_adds_tinted_assets_and_is_deterministic(self):
        params = p.preset("confetti", count=48, seed=3, hue_var=60, size_in_dur=0.3, size_out_dur=0.3)
        png = sparkle_png()
        a, b = lottie.export_lottie(params, png), lottie.export_lottie(params, png)
        self.assertEqual(json.dumps(a), json.dumps(b), "same params + same PNG -> the same JSON")
        ids = [x["id"] for x in a["assets"]]
        self.assertGreater(len(ids), 1)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(ids), 2 * 60 / lottie.HUE_STEP + 1)
        self.assertTrue({layer["refId"] for layer in a["layers"]} <= set(ids))
        self.assertNotEqual(json.dumps(lottie.export_lottie(p.preset("confetti", count=48, seed=4, hue_var=60), png)), json.dumps(a))

    def test_several_sprites_are_one_asset_each(self):
        doc = lottie.export_lottie(p.preset("burst", count=10, seed=1), [sparkle_png(), p.png_bytes(np.full((20, 30, 4), 255, np.uint8))])
        self.assertEqual(len(doc["assets"]), 2)
        self.assertEqual({layer["refId"] for layer in doc["layers"]}, {x["id"] for x in doc["assets"]})

    def test_bad_sprites_are_refused(self):
        with self.assertRaises(ValueError):
            lottie.export_lottie(p.preset("burst"), [])
        with self.assertRaises(ValueError):
            lottie.export_lottie(p.preset("burst"), b"not a png")
        with self.assertRaises(ValueError):
            lottie.export_lottie(p.preset("burst"), p.png_bytes(np.zeros((8, 8, 4), np.uint8)))


class DefaultSpriteTests(unittest.TestCase):
    def test_default_sprite_is_a_tight_rgba_sparkle(self):
        a = p.default_sprite()
        b = p.default_sprite()
        self.assertEqual(a.shape, (128, 128, 4))
        self.assertEqual(a.dtype, np.uint8)
        np.testing.assert_array_equal(a, b)
        self.assertTrue((a[..., 3] > 200).any(), "a solid core")
        self.assertTrue((a[..., 3] == 0).any(), "transparent corners")
        trimmed = p.trim_sprite(a)
        self.assertIsNotNone(trimmed)
        self.assertTrue(max(trimmed.shape[:2]) < 128, "mostly air around the sparkle")


if __name__ == "__main__":
    unittest.main()
