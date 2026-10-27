"""Super reaction (Reactions 2.0, 2026-10-20): engine/super_reaction.py and flow/lottie_reactions.py super_listing / super_compose.
Numbers only; no provider, no picture opened. A temporary drop zone (STICKERLOOM_LOTTIE_DIR) and a temporary out/."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from stickerloom.engine import lottie, particles as P, super_reaction as sr

SOURCE = {"v": "5.7.4", "fr": 30, "ip": 0, "op": 30, "w": 100, "h": 100, "assets": [], "layers": [
    {"ty": 4, "ind": 1, "ip": 0, "op": 30, "st": 0, "ks": {}, "shapes": [{"ty": "gr", "it": [
        {"ty": "el", "s": {"a": 0, "k": [80, 80]}, "p": {"a": 0, "k": [0, 0]}},
        {"ty": "fl", "c": {"a": 0, "k": [0.9, 0.1, 0.15, 1]}, "o": {"a": 0, "k": 100}}]}]}]}


def _coverage(aspect, settings=None):
    W, H = sr.canvas(aspect)
    xs, ys = [], []
    for q, (dx, dy) in zip(*sr.scene_params(P.preset("burst"), aspect, settings)):
        t = P.trajectories(q, 1)
        pos = t.pos + np.array([dx, dy])
        xs += list(pos[..., 0][t.visible] * sr.UNIT_PX / W)
        ys += list(pos[..., 1][t.visible] * sr.UNIT_PX / H)
    return np.array(xs), np.array(ys)


class SuperEngine(unittest.TestCase):
    def test_buckets_and_canvas(self):
        self.assertEqual(sr.bucket(390 / 844)[0], "phone")
        self.assertEqual(sr.bucket(1440 / 900)[0], "desktop")
        self.assertEqual(sr.canvas(9 / 19.5), (512, 1109))
        self.assertEqual(min(sr.canvas(16 / 9)), sr.UNIT_PX)
        with self.assertRaises(ValueError):
            sr.bucket(0)

    def test_fills_the_whole_screen_from_the_bottom(self):
        for aspect in sr.BUCKETS.values():
            xs, ys = _coverage(aspect)
            inside = (xs >= 0) & (xs <= 1) & (ys >= 0) & (ys <= 1)
            self.assertGreater(inside.mean(), 0.75, aspect)
            q10, q90 = np.percentile(ys[inside], [10, 90])
            self.assertLess(q10, 0.25, aspect)            # reaches the top quarter
            self.assertGreater(q90, 0.75, aspect)         # and starts in the bottom quarter
            self.assertGreater(np.percentile(xs[inside], 90) - np.percentile(xs[inside], 10), 0.5, aspect)   # across the width

    def test_the_pop_floats_up_wiggling_until_the_end(self):
        def pop(settings=None, source=None):
            d = sr.export_super(P.preset("burst"), 0.46, settings=settings, source=source)
            return d, next(L for L in d["layers"] if L["nm"] == "pop")
        d, L = pop()
        self.assertEqual(L["op"], d["op"])                                    # it stays to the end, no longer gone after 1.25 s
        ys = [k["s"][1] for k in L["ks"]["p"]["k"]]
        xs = [k["s"][0] for k in L["ks"]["p"]["k"]]
        self.assertLess(ys[-1], ys[0] - 0.1 * d["h"])                        # it climbs
        self.assertGreater(max(xs) - min(xs), 0.02 * d["w"])                 # it wiggles left and right
        self.assertEqual(L["ks"]["o"]["k"][-1]["s"], [0])                    # and fades out at the end
        wide = pop({"sway": 1.5})[1]
        self.assertGreater(max(k["s"][0] for k in wide["ks"]["p"]["k"]) - min(k["s"][0] for k in wide["ks"]["p"]["k"]), max(xs) - min(xs))   # Sway
        _, Ls = pop(source=SOURCE)
        self.assertEqual((Ls["ty"], Ls["refId"]), (0, "L0"))
        self.assertIn("tm", Ls)                                               # a short source loops while it floats

    def test_size_scales_the_particles(self):
        base = sr.scene_params(P.preset("burst"), 1.0)[0][0].particle_size
        self.assertAlmostEqual(sr.scene_params(P.preset("burst"), 1.0, {"size": 2})[0][0].particle_size, base * 2)
        self.assertEqual(sr.scene_params(P.preset("burst"), 1.0, {"size": 99})[0][0].particle_size, 3.0)      # the engine's clamp

    def test_wide_screens_get_one_emitter_per_unit(self):
        self.assertEqual(len(sr.scene_params(P.preset("burst"), 21 / 9)[0]), 3)
        self.assertEqual(len(sr.scene_params(P.preset("burst"), 9 / 19.5)[0]), 1)

    def test_emoji_free_slot_and_deterministic(self):
        a = sr.export_super(P.preset("burst"), 0.46)
        self.assertEqual(json.dumps(a), json.dumps(sr.export_super(P.preset("burst"), 0.46)))
        self.assertNotIn("base64", json.dumps(a))
        self.assertEqual([x["id"] for x in a["assets"]], [lottie.SLOT_ID])
        self.assertEqual((a["w"], a["h"]), sr.canvas(0.46))
        self.assertEqual([L["ind"] for L in a["layers"]], list(range(1, len(a["layers"]) + 1)))
        self.assertEqual(a["layers"][0]["nm"], "pop")
        self.assertEqual(a["layers"][-1]["nm"], "glow")

    def test_glow_modes(self):
        g = sr.export_super(P.preset("burst"), 1.0, glow="gradient", colors=["#ff0000", "#00f"])
        stops = g["layers"][-1]["shapes"][0]["it"][1]["g"]["k"]["k"]
        self.assertEqual(stops[:8], [0, 1.0, 0.0, 0.0, 1, 0.0, 0.0, 1.0])
        self.assertEqual(g["stickerloom"]["color"], "#ff0000")
        self.assertNotIn("glow", [L["nm"] for L in sr.export_super(P.preset("burst"), 1.0, glow="none")["layers"]])
        self.assertEqual(sr.export_super(P.preset("burst"), 1.0, glow="solid", color="#123456")["stickerloom"]["color"], "#123456")
        for bad in ({"glow": "rainbow"}, {"glow": "solid", "color": "red"}, {"glow": "gradient", "colors": ["#fff"]}, {"settings": {"nope": 1}}):
            with self.assertRaises(ValueError):
                sr.export_super(P.preset("burst"), 1.0, **bad)

    def test_dominant_color(self):
        img = np.zeros((10, 10, 4), np.uint8)
        img[..., 3] = 255
        img[:7, :, :3] = (220, 30, 40)          # mostly red
        img[7:, :, :3] = (250, 250, 250)        # a white highlight
        r, g, b = sr.dominant_color(img)
        self.assertGreater(r, 200)
        self.assertLess(max(g, b), 60)
        self.assertEqual(sr.dominant_color(np.zeros((4, 4, 4), np.uint8)), (255, 200, 80))

    def test_lottie_source_and_its_colour(self):
        d = sr.export_super(P.preset("burst"), 1.6, source=SOURCE, name="dot")
        self.assertIn("L0", [a["id"] for a in d["assets"]])
        self.assertEqual(d["layers"][0]["refId"], "L0")
        r, g, b = (int(d["stickerloom"]["color"][i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(r, 200)
        self.assertLess(max(g, b), 60)

    def test_lottie_burst_shift_default_is_unchanged(self):
        p = P.preset("burst", count=8)
        a = lottie.export_lottie_burst(p, SOURCE)
        self.assertEqual(json.dumps(a), json.dumps(lottie.export_lottie_burst(p, SOURCE, shift=(0.0, 0.0))))
        b = lottie.export_lottie_burst(p, SOURCE, shift=(0.0, 1.0))
        ka, kb = a["layers"][0]["ks"]["p"]["k"][0]["s"], b["layers"][0]["ks"]["p"]["k"][0]["s"]
        self.assertAlmostEqual(kb[1] - ka[1], 512.0, delta=0.2)


class SuperFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "lottie").mkdir()
        (root / "lottie" / "Red dot.json").write_text(json.dumps(SOURCE), encoding="utf-8")
        (root / "lottie" / "not a lottie.json").write_text("{}", encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.env = os.environ.get("STICKERLOOM_LOTTIE_DIR")
        os.environ["STICKERLOOM_LOTTIE_DIR"] = str(root / "lottie")

    def tearDown(self):
        if self.env is None:
            os.environ.pop("STICKERLOOM_LOTTIE_DIR", None)
        else:
            os.environ["STICKERLOOM_LOTTIE_DIR"] = self.env
        self.tmp.cleanup()

    def test_listing_is_the_folder_by_name(self):
        from stickerloom.flow import lottie_reactions as lr
        j = lr.super_listing()
        self.assertEqual([(f["file"], f["name"]) for f in j["files"]], [("Red dot.json", "Red dot")])
        self.assertIn("phone", j["buckets"])
        self.assertEqual(j["glow_modes"], list(sr.GLOW_MODES))

    def test_random_look_is_seeded_by_the_file(self):
        from stickerloom.flow import super_reactions as sl
        a, b = sl.random_look("Red dot.json"), sl.random_look("Red dot.json")
        self.assertEqual(a, b)
        self.assertEqual(a["seed"], sr.seed_of("Red dot.json"))
        self.assertNotEqual(sl.shuffle("Red dot.json", a["seed"] + 1)["settings"], a["settings"])
        for k, (lo, hi, _) in sr.RANDOM_RANGES.items():
            self.assertTrue(lo <= a["settings"][k] <= hi, k)

    def test_save_save_as_remove_and_presets(self):
        from stickerloom.flow import super_reactions as sl
        look = {"settings": {"size": 1.5, "count": 30}, "glow": "solid", "color": "#00FF00"}
        v1 = sl.save_version(self.out, "Red dot.json", look)
        v2 = sl.save_version(self.out, "Red dot.json", {**look, "glow": "auto"}, name="Red dot big")
        self.assertEqual((v1["id"], v1["name"], v1["look"]["color"]), ("V001", "Red dot", "#00ff00"))
        self.assertEqual([v["id"] for v in sl.library(self.out)["files"][0]["versions"]], ["V001", "V002"])
        self.assertEqual(sl.update_version(self.out, "V002", {"settings": {"size": 2}, "glow": "none"})["look"]["glow"], "none")
        sl.remove_version(self.out, "V001")
        self.assertEqual(sl.save_version(self.out, "Red dot.json", look)["id"], "V003")      # a number is never reused
        self.assertTrue((self.out.parent / "lottie" / "Red dot.json").is_file())             # Remove never touches the file
        p = sl.save_preset(self.out, {"settings": {"size": 0.5}, "glow": "auto", "seed": 4}, name="Tiny")
        self.assertNotIn("seed", p["look"])
        self.assertEqual([x["name"] for x in sl.library(self.out)["presets"]], ["Tiny"])
        sl.remove_preset(self.out, p["id"])
        for bad in ({"settings": {"nope": 1}}, {"glow": "rainbow"}, {"glow": "solid", "color": "red"}, {"x": 1}, {"settings": {"size": "big"}}):
            with self.assertRaises(sl.SetError):
                sl.save_version(self.out, "Red dot.json", bad)
        for call in (lambda: sl.save_version(self.out, "missing.json", look), lambda: sl.update_version(self.out, "V999", look),
                     lambda: sl.remove_version(self.out, "V999"), lambda: sl.remove_preset(self.out, "P999")):
            with self.assertRaises(sl.SetError) as e:
                call()
            self.assertEqual(e.exception.code, 404)

    def test_export_is_what_is_saved_else_random(self):
        import io
        import zipfile
        from stickerloom.flow import super_reactions as sl
        m = json.loads(zipfile.ZipFile(io.BytesIO(sl.export_zip(self.out))).read("manifest.json"))
        self.assertEqual([(r["name"], r["saved"]) for r in m["superReactions"]], [("Red dot", False)])
        sl.save_version(self.out, "Red dot.json", {"settings": {"size": 1.5}, "glow": "solid", "color": "#00ff00"}, name="Green")
        z = zipfile.ZipFile(io.BytesIO(sl.export_zip(self.out)))
        m = json.loads(z.read("manifest.json"))
        self.assertEqual([(r["id"], r["name"], r["saved"], r["color"]) for r in m["superReactions"]], [("V001", "Green", True, "#00ff00")])
        self.assertEqual(set(m["superReactions"][0]["buckets"]), set(sr.BUCKETS))
        doc = json.loads(z.read("superReactions/Green/phone.json"))
        self.assertEqual((doc["w"], doc["h"]), sr.canvas(sr.BUCKETS["phone"]))
        with self.assertRaises(sl.SetError) as e:
            sl.export_zip(self.out, ["missing.json"])
        self.assertEqual(e.exception.code, 404)

    def test_compose_and_errors(self):
        from stickerloom.flow import lottie_reactions as lr
        r = lr.super_compose(self.out, "Red dot.json", 390 / 844, glow="solid", color="#00ff00")
        self.assertEqual((r["bucket"], r["color"], r["name"]), ("phone", "#00ff00", "Red dot"))
        self.assertNotIn("stickerloom", r["lottie"])
        with self.assertRaises(lr.SetError) as e:
            lr.super_compose(self.out, "missing.json", 1.0)
        self.assertEqual(e.exception.code, 404)
        for bad in ({"aspect": "wide"}, {"glow": "rainbow"}):
            with self.assertRaises(lr.SetError) as e:
                lr.super_compose(self.out, "Red dot.json", **{"aspect": 1.0, **bad})
            self.assertEqual(e.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
