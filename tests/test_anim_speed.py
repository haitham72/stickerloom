"""The animation speedups must not change a single value, and a finished animation must not be made twice."""
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from stickerloom.engine import render
from stickerloom.engine.config import EngineConfig
from stickerloom.engine.video import AnimCache, loop_seam, process_video, support
from tests import synth

CFG = EngineConfig()


def apply_edge_v1(rgb, alpha, outline_px, erode_px):
    """apply_edge as it was before the speedup (the reference)."""
    A = np.clip(alpha, 0, 1)
    if erode_px > 0:
        A = cv2.erode(A, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode_px + 1, 2 * erode_px + 1)))
    if outline_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * outline_px + 1, 2 * outline_px + 1))
        out_a = np.clip(np.maximum(cv2.GaussianBlur(cv2.dilate(A, k), (0, 0), 1.0), A), 0, 1)
    else:
        out_a = A
    denom = np.maximum(out_a, 1e-4)
    rgb = (rgb * A[..., None] + 255.0 * (out_a - A)[..., None]) / denom[..., None]
    return np.clip(np.dstack([rgb, out_a * 255.0]) + 0.5, 0, 255).astype(np.uint8)


def loop_seam_v1(last, first):
    a1, a2 = last[..., 3].astype(np.float32), first[..., 3].astype(np.float32)
    w = np.maximum(a1, a2) / 255.0
    d = np.abs(last[..., :3].astype(np.float32) - first[..., :3].astype(np.float32)).mean(-1)
    return float((w * d + np.abs(a1 - a2)).sum() / max(float(w.sum()), 1.0))


class SameValuesTests(unittest.TestCase):
    def test_apply_edge_is_bit_identical(self):
        rng = np.random.default_rng(7)
        for _ in range(8):
            A = (np.clip(rng.normal(0.3, 0.6, (256, 256)), 0, 1) * (rng.random((256, 256)) > 0.6)).astype(np.float32)
            rgb = rng.random((256, 256, 3), dtype=np.float32) * 255
            for o, e in ((12, 0), (0, 0), (12, 3), (4, 2)):
                self.assertTrue(np.array_equal(apply_edge_v1(rgb, A, o, e), render.apply_edge(rgb, A, o, e)), (o, e))
        z = np.zeros((64, 64), np.float32)
        self.assertTrue(np.array_equal(apply_edge_v1(np.zeros((64, 64, 3), np.float32), z, 12, 0), render.apply_edge(np.zeros((64, 64, 3), np.float32), z, 12, 0)))

    def test_the_loop_ends_on_the_frame_that_leads_back_to_frame_0_without_a_see_through_fade(self):
        import numpy as np
        from stickerloom.engine import video
        n, f = 30, np.zeros((30, 64, 64, 4), np.uint8)
        for i in range(n):                                   # a dot that goes right and comes back to its start at frame 24, then wanders off again
            x = 10 + (i if i <= 12 else 24 - i if i <= 24 else (i - 24) * 3)
            f[i, 20:30, x:x + 10] = (200, 40, 40, 255)
        end, seam = video.loop_end(f, limit=40.0)
        self.assertEqual(end, 24, "the clip ends just before the frame that matches frame 0: the wrap is one normal step")
        self.assertTrue(set(np.unique(f[:end][..., 3])) <= {0, 255}, "a cut blends nothing: no half-transparent frame at the wrap")

    def test_loop_seam_is_the_same_on_the_crop_where_the_sticker_exists(self):
        rng = np.random.default_rng(3)
        frames = np.zeros((6, 200, 200, 4), np.uint8)
        frames[:, 40:150, 60:170] = rng.integers(0, 256, (6, 110, 110, 4), dtype=np.uint8)
        for i in range(5):
            self.assertAlmostEqual(loop_seam_v1(frames[i], frames[i + 1]), loop_seam(frames[i], frames[i + 1]), places=3)
        crop = support(frames)
        self.assertEqual(crop.shape[1:3], (110, 110))
        self.assertAlmostEqual(loop_seam_v1(frames[5], frames[0]), loop_seam(crop[5], crop[0]), places=3)
        self.assertEqual(support(np.zeros((3, 8, 8, 4), np.uint8)).shape, (3, 8, 8, 4))       # nothing opaque: unchanged


class CacheTests(unittest.TestCase):
    def test_the_same_cell_is_not_made_twice(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            mp4 = td / "v.mp4"; synth.make_video(mp4)
            cache = AnimCache(td / "cache")
            t0 = time.perf_counter(); first = process_video(mp4, CFG, cells=[1, 5], cache=cache); cold = time.perf_counter() - t0
            t0 = time.perf_counter(); again = process_video(mp4, CFG, cells=[1, 5], cache=cache); warm = time.perf_counter() - t0
            self.assertEqual({r.status for r in first}, {"READY"})
            self.assertTrue(all(r.metrics.get("cache") == "hit" for r in again))
            self.assertFalse(any("cache" in r.metrics for r in first))
            by = lambda rs: {r.index: r for r in rs}
            for i in (1, 5):
                self.assertEqual(by(first)[i].data, by(again)[i].data)                         # the very same WEBM
                self.assertEqual(by(first)[i].report.checks, by(again)[i].report.checks)       # and the same verdicts
                self.assertEqual(by(first)[i].status, by(again)[i].status)
            self.assertLess(warm, cold / 3)
            # another edge finish is another animation; so is another cell
            other = process_video(mp4, replace(CFG, outline_px=12), cells=[1], cache=cache)     # 0 is the default now: a stroke is the other finish
            self.assertNotIn("cache", other[0].metrics)
            # a damaged entry is ignored, never trusted
            for j in (td / "cache").glob("*.json"):
                j.write_text("{broken", encoding="utf-8")
            self.assertFalse(any("cache" in r.metrics for r in process_video(mp4, CFG, cells=[1], cache=cache)))
            # the source file changed (new size/time) -> not the same animation
            mp4.write_bytes(mp4.read_bytes() + b"\0")
            self.assertFalse(any("cache" in r.metrics for r in process_video(mp4, CFG, cells=[5], cache=cache)))

    def test_workers_can_be_set_by_the_developer(self):
        import os
        from stickerloom.engine.config import default_workers
        old = os.environ.get("STICKERLOOM_ANIM_WORKERS")
        try:
            os.environ["STICKERLOOM_ANIM_WORKERS"] = "3"
            self.assertEqual((default_workers(), EngineConfig().anim_workers), (3, 3))
            os.environ["STICKERLOOM_ANIM_WORKERS"] = "nonsense"
            self.assertGreaterEqual(default_workers(), 1)
        finally:
            os.environ.pop("STICKERLOOM_ANIM_WORKERS", None) if old is None else os.environ.__setitem__("STICKERLOOM_ANIM_WORKERS", old)


if __name__ == "__main__":
    unittest.main()
