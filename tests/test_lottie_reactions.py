"""Lottie style reactions: the engine's new fields (speed spread and pulse, the card flip, morph times, child bursts), the
self-contained export that embeds every Lottie it uses, the flow's storage and the native routes. Numbers and JSON only (rule 9); no provider."""
import http.client
import io
import json
import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np

from stickerloom.engine import lottie, particles as p
from stickerloom.flow import lottie_reactions as lr
from stickerloom.flow.particle_sets import SetError


def tiny(nm, fr=25, op=50, w=300, h=200):
    return {"v": "5.7.4", "fr": fr, "ip": 0, "op": op, "w": w, "h": h, "nm": nm, "ddd": 0,
            "assets": [{"id": "comp_0", "layers": [{"ddd": 0, "ind": 1, "ty": 2, "refId": "image_0"}]}, {"id": "image_0", "w": 10, "h": 10, "u": "", "p": "a.png", "e": 0}],
            "layers": [{"ddd": 0, "ind": 1, "ty": 0, "refId": "comp_0"}, {"ddd": 0, "ind": 2, "ty": 4}]}


class EngineTests(unittest.TestCase):
    def test_every_new_field_is_off_by_default_and_clamped(self):
        d = p.ParticleParams().to_dict()
        self.assertEqual([d[k] for k in ("speed_var", "speed_pulse", "flip", "flip_var", "morph_at", "morph_var", "children", "child_size_var")], [0] * 8)
        q = p.ParticleParams(speed_var=5, speed_pulse=-1, pulse_hz=99, flip=9, morph_at=9, children=40, child_size=9, child_size_var=-2, duration=2).clamped()
        self.assertEqual((q.speed_var, q.speed_pulse, q.pulse_hz, q.flip, q.morph_at, q.children, q.child_size, q.child_size_var), (1.0, 0.0, 6.0, 4.0, 2.0, 12, 3.0, 0.0))
        t = p.trajectories(p.preset("burst", seed=4))
        self.assertTrue((t.flipx == 1).all() and np.isinf(t.morph).all() and (t.parent == -1).all())

    def test_speed_variation_spreads_launch_speeds_and_the_pulse_changes_the_path_not_the_start(self):
        def first_step(**kw):
            t = p.trajectories(p.ParticleParams(seed=2, count=40, gravity=0, **kw))
            s = t.spawn
            return np.hypot(*(t.pos[s + 2, np.arange(40)] - t.pos[s + 1, np.arange(40)]).T)
        a, b = first_step(), first_step(speed_var=1.0)
        self.assertGreater(b.std() / b.mean(), a.std() / a.mean() * 1.5, "speed variation: the launch speeds differ more")
        steady = p.trajectories(p.ParticleParams(seed=2, gravity=0))
        pulse = p.trajectories(p.ParticleParams(seed=2, gravity=0, speed_pulse=0.8, pulse_hz=3))
        self.assertFalse(np.allclose(steady.pos, pulse.pos), "the pulse moves the particles")
        np.testing.assert_array_equal(steady.spawn, pulse.spawn)

    def test_the_cheap_spin_is_a_horizontal_flip_from_100_to_minus_100(self):
        t = p.trajectories(p.ParticleParams(seed=1, flip=1.0, duration=2.0))
        i = 0
        s = int(t.spawn[i])
        self.assertAlmostEqual(float(t.flipx[s, i]), 1.0, "starts at 100%")
        self.assertAlmostEqual(float(t.flipx[s + 15, i]), -1.0, places=6, msg="half a turn at 1/s = -100%")
        d = lottie.export_lottie_burst(p.ParticleParams(seed=1, flip=1.0, duration=2.0), tiny("o"))
        xs = [k["s"][0] for L in d["layers"] for k in L["ks"]["s"]["k"]]
        ys = [k["s"][1] for L in d["layers"] for k in L["ks"]["s"]["k"]]
        self.assertTrue(min(xs) < 0 < max(xs) and min(ys) >= 0, "x scale goes negative, y never does")

    def test_children_burst_from_their_parent_at_its_fade_start_sized_from_it(self):
        q = p.ParticleParams(seed=3, count=10, children=3, child_size=0.5, fade_start=0.5, fade_dur=0.4, duration=2.0)
        t = p.trajectories(q)
        kids = np.flatnonzero(t.parent >= 0)
        self.assertEqual(len(kids), 30)
        for c in kids[:6]:
            par, s = int(t.parent[c]), int(t.spawn[c])
            self.assertEqual(s, int(t.spawn[par]) + 15, "born when the parent's fade starts (0.5 s after its birth)")
            np.testing.assert_allclose(t.pos[s, c], t.pos[s, par], err_msg="from where the parent is")
            self.assertAlmostEqual(float(t.size_frac[c]), float(t.size_frac[par] * t.scale[s, par] * 0.5), places=9)
        self.assertEqual(float(t.opacity[int(1.8 * 30):].max()), 0.0, "children are gone by the end too")
        spread = p.trajectories(p.ParticleParams(seed=3, count=10, children=3, child_size_var=1.0, duration=2.0))
        rel = spread.size_frac[spread.parent >= 0]
        self.assertGreater(rel.max() / rel.min(), 3, "size variation: children differ a lot")

    def test_the_morph_starts_the_children_and_bursts_replace_the_parent(self):
        for burst in (0, 1):
            q = p.ParticleParams(seed=5, count=8, children=2, morph_at=0.5, morph_burst=burst, duration=2.0)
            t = p.trajectories(q)
            kids = np.flatnonzero(t.parent >= 0)
            self.assertEqual(len(kids), 16)
            for c in kids:
                par = int(t.parent[c])
                self.assertEqual(int(t.spawn[c]), int(t.spawn[par]) + 15, "born at the parent's morph (0.5 s)")
                self.assertTrue(np.isinf(t.morph[c]), "a child never morphs again")
            roots = np.flatnonzero(t.parent < 0)
            after = [float(t.opacity[int(t.spawn[r]) + 15 + 6:, r].max()) for r in roots]
            if burst:
                self.assertEqual(max(after), 0.0, "Bursts into children: the parent is gone after the crossfade")
            else:
                self.assertGreater(max(after), 0.5, "Turns into (1:1): the parent goes on as its target")

    def test_old_bursts_keep_their_bytes(self):
        a = p.trajectories(p.preset("confetti", seed=9))
        b = p.trajectories(p.preset("confetti", seed=9, flip_var=0.5, morph_var=0.5))
        np.testing.assert_array_equal(a.pos, b.pos)
        np.testing.assert_array_equal(a.opacity, b.opacity)


class ParticularTests(unittest.TestCase):
    """The Trapcode Particular-style settings (2026-10-15): off by default, drawn from their own generator, each doing what its name says."""

    def test_the_new_draws_never_move_an_older_burst(self):
        a = p.trajectories(p.ParticleParams(seed=5, children=3, morph_at=0.5, duration=2))
        b = p.trajectories(p.ParticleParams(seed=5, children=3, morph_at=0.5, duration=2, time_sampling=2))     # only draws from the second generator
        np.testing.assert_array_equal(a.pos, b.pos)
        np.testing.assert_array_equal(a.opacity, b.opacity)

    def test_emitters_place_the_births(self):
        box = p.trajectories(p.ParticleParams(seed=3, emitter=2, emitter_w=0.6, emitter_h=0.2, magnitude=0, gravity=0, duration=2))
        born = box.pos[box.spawn, np.arange(len(box.spawn))]
        self.assertTrue((np.abs(born[:, 0] - 0.5) <= 0.3 + 1e-9).all() and (np.abs(born[:, 1] - 0.5) <= 0.1 + 1e-9).all())
        self.assertGreater(np.ptp(born[:, 0]), 0.3, "spread over the box, not a point")
        ring = p.trajectories(p.ParticleParams(seed=3, emitter=1, emitter_w=0.5, magnitude=0, gravity=0, duration=2))
        r = np.hypot(*(ring.pos[ring.spawn, np.arange(len(ring.spawn))] - 0.5).T)
        np.testing.assert_allclose(r, 0.25, atol=1e-9)

    def test_stream_spreads_births_and_direction_turns_the_arc(self):
        burst = p.trajectories(p.ParticleParams(seed=3, duration=2))
        stream = p.trajectories(p.ParticleParams(seed=3, duration=2, emit_dur=1.0))
        self.assertGreater(np.ptp(stream.spawn), 2 * np.ptp(burst.spawn))          # 1 s of births against the 0.3 s burst window
        right = p.trajectories(p.ParticleParams(seed=3, duration=2, spread=20, direction=90, gravity=0))
        d = right.pos[-10] - 0.5
        self.assertTrue((d[:, 0] > np.abs(d[:, 1])).all(), "90 degrees from up is to the right")

    def test_air_wind_and_life_random(self):
        calm = p.trajectories(p.ParticleParams(seed=3, gravity=0, duration=2))
        thick = p.trajectories(p.ParticleParams(seed=3, gravity=0, duration=2, air=2))
        self.assertLess(np.abs(thick.pos[40] - 0.5).mean(), np.abs(calm.pos[40] - 0.5).mean())
        windy = p.trajectories(p.ParticleParams(seed=3, gravity=0, duration=2, wind_x=1.5))
        self.assertGreater(windy.pos[40, :, 0].mean(), calm.pos[40, :, 0].mean() + 0.1)
        self.assertGreater(np.ptp(p.trajectories(p.ParticleParams(seed=3, duration=2, life_var=0.8)).life), np.ptp(calm.life))

    def test_bounce_keeps_particles_above_the_floor_and_can_start_children(self):
        q = p.ParticleParams(seed=3, duration=2, gravity=2.5, magnitude=0.5, bounce=0.5, floor_y=0.8, children=2, aux_at=2)
        t = p.trajectories(q)
        roots = t.parent < 0
        self.assertLessEqual(float(t.pos[:, roots, 1].max()), 0.8 + 1e-9)
        kids = np.flatnonzero(t.parent >= 0)
        self.assertGreater(len(kids), 0)
        np.testing.assert_allclose(t.pos[t.spawn[kids], kids, 1], 0.8, atol=0.08)   # born on the floor, where the parent bounced

    def test_trail_and_death_children(self):
        q = p.ParticleParams(seed=3, duration=2, count=6, children=4, aux_at=3)
        t = p.trajectories(q)
        kids = np.flatnonzero(t.parent >= 0)
        self.assertGreater(len(kids), 12)
        for r in range(6):                                            # spread along the parent's life, not all at once
            ks = kids[t.parent[kids] == r]
            if len(ks) > 1:
                self.assertGreater(np.ptp(t.spawn[ks]), 5)
        d = p.trajectories(p.ParticleParams(seed=3, duration=2, count=6, children=2, aux_at=1, aux_life=0.3))
        dk = np.flatnonzero(d.parent >= 0)
        np.testing.assert_array_equal(d.spawn[dk], d.spawn[d.parent[dk]] + np.rint(d.life[d.parent[dk]] * 30).astype(int) - 1)
        self.assertTrue((d.life[dk] <= 0.3 + 1e-9).all())

    def test_over_life_curves(self):
        base = p.trajectories(p.ParticleParams(seed=3, duration=2))
        grow = p.trajectories(p.ParticleParams(seed=3, duration=2, size_curve=1))
        i = 0
        f0, f1 = int(base.spawn[i]) + 8, int(base.spawn[i]) + int(base.life[i] * 30) - 3
        self.assertLess(grow.scale[f0, i] / base.scale[f0, i], grow.scale[f1, i] / base.scale[f1, i])
        fin = p.trajectories(p.ParticleParams(seed=3, duration=2, opacity_curve=1))
        self.assertLess(fin.opacity[int(fin.spawn[i]) + 1, i], 0.2)

    def test_time_sampling_in_the_export(self):
        src = tiny("o", fr=30, op=10)                                 # a 10-frame source in a 60-frame burst
        once = lottie.export_lottie_burst(p.ParticleParams(seed=2, count=4, duration=2), src)
        self.assertTrue(all(L["op"] - L["ip"] <= 10 and "tm" not in L for L in once["layers"]), "start at birth: plays once, ends with its source")
        loop = lottie.export_lottie_burst(p.ParticleParams(seed=2, count=4, duration=2, time_sampling=1), src)
        L = loop["layers"][0]
        self.assertGreater(L["op"] - L["ip"], 10, "a loop lives its whole life")
        vals = [k["s"][0] for k in L["tm"]["k"]]
        self.assertEqual((min(vals), round(max(vals) * 30)), (0.0, 9), "the remap wraps inside the source")
        still = lottie.export_lottie_burst(p.ParticleParams(seed=2, count=4, duration=2, time_sampling=3), src)
        self.assertTrue(all(L["tm"]["a"] == 0 for L in still["layers"]))

    def test_presets_and_position_reach_the_engine(self):
        self.assertTrue(set(lr.PRESETS) >= {"Pop", "Fountain", "Confetti", "Snow", "Fireworks", "Orbit", "Trail"})
        for name, pr in lr.PRESETS.items():
            self.assertLessEqual(set(pr), set(lr.SETTINGS), name)
            d = lottie.export_lottie_burst(lr.params(pr), tiny("o"), [])
            self.assertTrue(0 < len(d["layers"]) <= lottie.MAX_PARTICLES * 2, name)
        self.assertEqual(lr.params({"origin_x": 0.2, "origin_y": 0.9}).origin, (0.2, 0.9))
        self.assertEqual(lr.params({}).to_dict()["lifetime"], 1.6)


class ExportTests(unittest.TestCase):
    def test_the_export_embeds_origin_and_targets_once_with_unique_ids(self):
        q = p.ParticleParams(seed=2, count=12, morph_at=0.4, morph_var=0.1, duration=2.0)
        d = lottie.export_lottie_burst(q, tiny("origin"), [tiny("t1", fr=30), tiny("t2", fr=60)])
        self.assertEqual((d["w"], d["h"], d["op"], d["fr"]), (512, 512, 60, 30.0))
        ids = [a["id"] for a in d["assets"]]
        self.assertEqual(len(ids), len(set(ids)), "no collision between the embedded files")
        self.assertEqual([i for i in ids if i in ("L0", "L1", "L2")], ["L0", "L1", "L2"], "each source once, as a precomp")
        self.assertIn("L1_comp_0", ids)
        inner = next(a for a in d["assets"] if a["id"] == "L1_comp_0")
        self.assertEqual(inner["layers"][0]["refId"], "L1_image_0", "references inside a source follow its renamed assets")
        refs = {L["refId"] for L in d["layers"]}
        self.assertEqual(refs, {"L0", "L1", "L2"} & refs | {"L0"})
        self.assertTrue(all(L["ty"] == 0 for L in d["layers"]), "every particle is a precomp layer, never an image")
        sr = {L["refId"]: L["sr"] for L in d["layers"]}
        self.assertAlmostEqual(sr["L0"], 30 / 25)
        self.assertNotIn("data:", json.dumps(d), "nothing base64 added")

    def test_morph_crossfades_at_the_morph_time(self):
        q = p.ParticleParams(seed=2, count=6, morph_at=0.5, duration=2.0)
        d = lottie.export_lottie_burst(q, tiny("o", op=200), [tiny("t", op=200)])
        t = p.trajectories(q)
        targets = [L for L in d["layers"] if L["refId"] == "L1"]
        self.assertEqual(len(targets), 6, "every particle morphs")
        births = sorted(int(s) for s in t.spawn)
        self.assertEqual(sorted(L["ip"] for L in targets), [b + 15 for b in births], "the target starts 0.5 s after birth")
        self.assertTrue(all(L["st"] == L["ip"] for L in targets), "its playhead starts there")

    def test_children_fly_the_target_and_a_burst_parent_has_no_target_layer(self):
        q = p.ParticleParams(seed=2, count=5, children=2, morph_at=0.4, morph_burst=1, duration=2.0)
        d = lottie.export_lottie_burst(q, tiny("o", op=200), [tiny("t", op=200)])
        t = p.trajectories(q)
        roots, kids = int((t.parent < 0).sum()), int((t.parent >= 0).sum())
        refs = [L["refId"] for L in d["layers"]]
        self.assertEqual((refs.count("L0"), refs.count("L1")), (roots, kids), "roots fly the origin only, every child is the target")

    def test_a_burst_too_big_is_refused(self):
        with self.assertRaises(ValueError):
            lottie.export_lottie_burst(p.ParticleParams(count=80, children=12, fade_start=0.3, fade_dur=0.3, duration=2.0), tiny("o"))


class FlowAndRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.out.mkdir()
        lot = self.tmp / "lottie"
        lot.mkdir()
        (lot / "Sad Emoji.json").write_text(json.dumps(tiny("sad")), encoding="utf-8")
        (lot / "Star.json").write_text(json.dumps(tiny("star", fr=30)), encoding="utf-8")
        patch = mock.patch.dict("os.environ", {"STICKERLOOM_LOTTIE_DIR": str(lot)})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_listing_save_compose_and_zip(self):
        j = lr.listing(self.out)
        self.assertEqual([f["file"] for f in j["files"]], ["Sad Emoji.json", "Star.json"])
        self.assertEqual(j["files"][0]["settings"], lr.START, "the neutral start until saved")
        self.assertEqual((j["seconds"], j["size"]), (2.0, 512))
        r = lr.save(self.out, "Sad Emoji.json", {"count": 10, "morph_at": 0.5}, ["Star.json", "Sad Emoji.json", "Star.json"])
        self.assertEqual(r["morph_targets"], ["Star.json"], "itself and repeats dropped")
        self.assertTrue(lr.listing(self.out)["files"][0]["saved"])
        doc = lr.compose(self.out, "Sad Emoji.json")
        self.assertEqual({a["id"] for a in doc["assets"] if "layers" in a} >= {"L0", "L1"}, True)
        with self.assertRaises(SetError) as e:
            lr.save(self.out, "nope.json", {})
        self.assertEqual(e.exception.code, 404)
        with self.assertRaises(SetError) as e:
            lr.save(self.out, "Star.json", {"bogus": 1})
        self.assertEqual(e.exception.code, 400)
        with zipfile.ZipFile(io.BytesIO(lr.export_zip(self.out))) as z:
            self.assertEqual(sorted(z.namelist()), ["Sad Emoji.json", "Star.json", "manifest.json"])
            man = json.loads(z.read("manifest.json"))
            self.assertEqual(man["reactions"][0]["morph_targets"], ["Star.json"])

    def test_the_native_routes(self):
        from stickerloom.console.server import serve
        from stickerloom.engine.config import EngineConfig
        srv, _ = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]

        def req(method, path, body=None):
            h = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
            r = h.getresponse()
            data = r.read()
            h.close()
            try:
                return r.status, json.loads(data)
            except ValueError:
                return r.status, data
        try:
            s, j = req("GET", "/api/particles/lottie-reactions")
            self.assertEqual((s, len(j["files"])), (200, 2))
            s, j = req("POST", "/api/particles/lottie-reactions/Sad%20Emoji.json/preview", {"settings": {"count": 6, "flip": 1}, "morph_targets": []})
            self.assertEqual((s, j["w"], j["assets"][-1]["id"]), (200, 512, "L0"))
            self.assertEqual(req("POST", "/api/particles/lottie-reactions/Sad%20Emoji.json", {"settings": {"count": 6}})[0], 200)
            s, j = req("GET", "/api/particles/lottie-reactions/Sad%20Emoji.json/export.json")
            self.assertEqual((s, j["nm"]), (200, "Sad Emoji (6 particles)"))
            self.assertEqual(req("POST", "/api/particles/lottie-reactions/x.json", {})[0], 404)
            self.assertEqual(req("POST", "/api/particles/lottie-reactions/Star.json", {"settings": {"nope": 1}})[0], 400)
            self.assertEqual(req("POST", "/api/particles/lottie-reactions/Star.json", {"extra": 1})[0], 400)
            s, data = req("GET", "/api/particles/lottie-reactions.zip")
            self.assertEqual((s, data[:2]), (200, b"PK"))
        finally:
            srv.shutdown()



class PreviewCacheTests(unittest.TestCase):
    def test_previews_keep_only_the_newest_and_never_touch_renders(self):
        import os
        from stickerloom.flow import particle_sets as ps
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        prev, rend = ps.sets_dir(out) / "emoji" / "happy" / "previews", ps.sets_dir(out) / "emoji" / "happy" / "renders"
        prev.mkdir(parents=True)
        rend.mkdir(parents=True)
        for i in range(30):
            f = prev / f"{i:02d}.webp"
            f.write_bytes(b"x")
            os.utime(f, (1000 + i, 1000 + i))
        (rend / "L001.webp").write_bytes(b"keep")
        run = out / "effects" / "E001" / "previews"                          # an effect run's previews are the same cache
        run.mkdir(parents=True)
        for i in range(30):
            f = run / f"{i:02d}.webp"
            f.write_bytes(b"x")
            os.utime(f, (1000 + i, 1000 + i))
        self.assertEqual(ps.sweep_previews(out, keep=24), {"folders": 2, "removed": 12})
        left = sorted(f.name for f in prev.iterdir())
        self.assertEqual((len(left), left[0]), (24, "06.webp"), "the 6 oldest went")
        self.assertTrue((rend / "L001.webp").is_file(), "renders are never a cache")
        left_run = sorted(f.name for f in run.iterdir())
        self.assertEqual((len(left_run), left_run[0]), (24, "06.webp"), "effect runs are swept too")

if __name__ == "__main__":
    unittest.main()
