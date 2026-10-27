"""The reworked sharpness check: it must SEE an animation that is softer than its still (the old encode-only ratio read 1.00 for those)."""
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from stickerloom.flow import measure
from stickerloom.engine import ffmpeg as ff, verify
from stickerloom.engine.config import EngineConfig
from stickerloom.engine.video import detail_vs_ref, edge_energy, soft_sigma


def textured(size=256, seed=3):
    """A subject with fine edges: random 6 px blocks inside a disc, transparent outside."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, (size // 6 + 1, size // 6 + 1, 3), dtype=np.uint8)
    rgb = np.kron(blocks, np.ones((6, 6, 1), np.uint8))[:size, :size]
    yy, xx = np.mgrid[:size, :size]
    a = (((yy - size / 2) ** 2 + (xx - size / 2) ** 2) < (size * 0.42) ** 2).astype(np.uint8) * 255
    return np.dstack([rgb, a])


def blurred(rgba, sigma):
    out = rgba.copy()
    out[..., :3] = cv2.GaussianBlur(np.ascontiguousarray(rgba[..., :3]), (0, 0), sigma)
    return out


class MeasureTheMetric(unittest.TestCase):
    def test_edge_energy_falls_monotonically_with_blur(self):
        s = textured()
        e = [edge_energy(blurred(s, g) if g else s) for g in (0, 0.5, 1, 2, 3)]
        self.assertEqual(e, sorted(e, reverse=True))

    def test_a_clip_as_sharp_as_its_still_reads_about_one(self):
        s = textured()
        frames = np.stack([s, s, s])
        self.assertAlmostEqual(detail_vs_ref(frames, s), 1.0, places=2)
        self.assertEqual(soft_sigma(s, 1.0), 0.0)

    def test_a_softer_clip_reads_low_and_the_sigma_is_about_right(self):
        s = textured()
        for true in (1.0, 2.0, 3.0):
            frames = np.stack([blurred(s, true)] * 3)
            d = detail_vs_ref(frames, s)
            self.assertLess(d, 0.95)
            self.assertAlmostEqual(soft_sigma(s, d), true, delta=0.35)
        self.assertLess(detail_vs_ref(np.stack([blurred(s, 2.0)] * 3), s), EngineConfig().min_detail_vs_still)

    def test_no_still_or_no_edges_is_not_a_number(self):
        s = textured()
        self.assertIsNone(detail_vs_ref(np.stack([s]), None))
        self.assertIsNone(detail_vs_ref(np.stack([s]), np.zeros_like(s)))


class TheCheck(unittest.TestCase):
    cfg = EngineConfig()

    def run_check(self, **metrics):
        r = verify.sharpness({"metrics": metrics}, self.cfg)
        return r

    def test_softness_before_the_encode_is_flagged_with_its_size_in_pixels(self):
        r = self.run_check(sharp_kept=1.0, detail_vs_still=0.62, soft_sigma=1.7)
        self.assertFalse(r.ok)
        self.assertEqual(r.severity, verify.WARN)
        self.assertIn("1.7 px", r.note)

    def test_a_sharp_clip_passes(self):
        self.assertTrue(self.run_check(sharp_kept=1.0, detail_vs_still=0.95, soft_sigma=0.3).ok)
        self.assertTrue(self.run_check(sharp_kept=1.02, detail_vs_still=1.1).ok)

    def test_the_encode_alone_can_still_fail(self):
        self.assertFalse(self.run_check(sharp_kept=0.5).ok)
        self.assertTrue(self.run_check(sharp_kept=0.95).ok)
        self.assertFalse(self.run_check(sharp_kept=0.5, detail_vs_still=0.9).ok)

    def test_nothing_measured_nothing_said(self):
        self.assertIsNone(self.run_check())

    def test_identity_kept_takes_the_still_as_rgba(self):
        s = textured()
        r = verify.identity_kept({"ref_alpha": s, "frames_out": np.stack([s])}, self.cfg)
        self.assertTrue(r.ok)
        r2 = verify.identity_kept({"ref_alpha": s[..., 3], "frames_out": np.stack([s])}, self.cfg)    # the old form still works
        self.assertTrue(r2.ok)


class MeasureOverStoredAnimations(unittest.TestCase):
    def test_a_report_over_a_folder_of_results(self):
        if not ff.has_vp9():
            self.skipTest("this ffmpeg has no libvpx-vp9")
        s = textured(128)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            for gid, soft in ((1, 0.0), (2, 2.5)):
                d = out / f"G{gid:03d}"
                (d / "slices").mkdir(parents=True)
                from PIL import Image
                Image.fromarray(s, "RGBA").save(d / "slices" / "a.png")
                frames = np.stack([blurred(s, soft) if soft else s] * 8)
                ff.encode_webm(frames, 24, 30, d / "slices" / "a.webm")
                res = {"stickers": [{"index": 1, "png": "slices/a.png", "webm": "slices/a.webm", "anim_status": "READY"}]}
                (d / "result.json").write_text(json.dumps(res), encoding="utf-8")
            rep = measure.sharpness(out)
        by = {r["generation"]: r for r in rep["by_generation"]}
        self.assertEqual(rep["cells"], 2)
        self.assertGreater(by["G001"]["mean_detail"], 0.85)
        self.assertLess(by["G002"]["mean_detail"], 0.75)
        self.assertEqual((by["G001"]["flagged"], by["G002"]["flagged"]), (0, 1))
        self.assertIn("G002", measure.render_sharpness(rep))


if __name__ == "__main__":
    unittest.main()
