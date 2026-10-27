"""The video side of the particle effects on a SYNTHETIC clip (a 2x2 grid of bursts on a green screen made here), never a real video."""
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from stickerloom.engine import effect_checks, effect_video as ev, ffmpeg as ff
from stickerloom.engine.config import EngineConfig

CFG = EngineConfig()
SIDE, FPS, N = 480, 24, 73


def make_clip(path, key=(0, 255, 0), empty_tail=(True, True, True, True), dark_cell=None, walls=0, lines=0):
    """2x2 cells, a small burst of coloured discs per cell: empty at the start, bursts at 0.4 s, falls, empty again at the end unless `empty_tail[i]` is False.
    The screen is one flat colour unless asked otherwise: `dark_cell=i` shades cell i by 40 levels (a panel), `walls=d` darkens the outer 30 px of every cell by d levels (walls),
    `lines=d` draws a 6 px line d levels darker on the boundaries between the cells."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (SIDE, SIDE))
    rng = np.random.default_rng(3)
    ang = rng.uniform(0, 6.28, (4, 7)); spd = rng.uniform(40, 90, (4, 7))
    back = np.zeros((SIDE, SIDE, 3), np.int16); back[:] = key[::-1]             # BGR
    keych = int(np.argmax(key[::-1]))                                           # the channel that carries the screen colour
    if dark_cell is not None:
        y0, x0 = (dark_cell // 2) * 240, (dark_cell % 2) * 240
        back[y0:y0 + 240, x0:x0 + 240, keych] -= 40
    if walls:
        for y0 in (0, 240):
            for x0 in (0, 240):
                cell = back[y0:y0 + 240, x0:x0 + 240, keych]
                cell[:30] -= walls; cell[-30:] -= walls; cell[:, :30] -= walls; cell[:, -30:] -= walls
    if lines:
        back[237:243, :, keych] -= lines
        back[:, 237:243, keych] -= lines
    back = np.clip(back, 0, 255).astype(np.uint8)
    for f in range(N):
        t = f / FPS
        img = back.copy()
        for ci in range(4):
            cx0, cy0 = (ci % 2) * 240 + 120, (ci // 2) * 240 + 120
            live = 0.4 <= t <= (2.6 if empty_tail[ci] else 3.1)
            if not live:
                continue
            for k in range(7):
                tt = t - 0.4
                x = cx0 + np.cos(ang[ci, k]) * spd[ci, k] * tt * 0.5
                y = cy0 + np.sin(ang[ci, k]) * spd[ci, k] * tt * 0.5 + 25 * tt * tt
                cv2.circle(img, (int(x), int(y)), 14, (60 + 25 * k, 90, 220 - 20 * k), -1)
        w.write(img)
    w.release()


@unittest.skipUnless(shutil.which("ffmpeg") or ff.ffmpeg_exe(), "ffmpeg is needed")
class EffectVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.mp4 = cls.tmp / "fx.mp4"
        make_clip(cls.mp4, empty_tail=(True, False, True, True))
        cls.cells = ev.cut_cells(cls.mp4, 2, 2, CFG, "green")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_cell_rects_and_resample_and_square(self):
        self.assertEqual(ev.cell_rects(480, 480, 2, 2), [(0, 0, 240, 240), (240, 0, 240, 240), (0, 240, 240, 240), (240, 240, 240, 240)])
        fr = np.zeros((73, 8, 8, 4), np.uint8)
        self.assertEqual(ev.resample(fr, 24.0).shape[0], 90, "Telegram's clock: 3 s at 30 fps")
        self.assertEqual(ev.square(np.zeros((2, 20, 20, 4), np.uint8), 512).shape, (2, 512, 512, 4))

    def test_every_cell_is_cut_and_keyed(self):
        self.assertEqual([c["index"] for c in self.cells], [1, 2, 3, 4])
        for c in self.cells:
            self.assertEqual(c["key"], "green")
            self.assertIsNone(c.get("error"))
            cov = effect_checks.coverage_curve(c["frames"])
            self.assertLess(max(cov[:3]), 0.003, "the clip starts on an empty screen")
            self.assertGreater(max(cov), 0.01, "something bursts")

    def test_the_screen_that_is_there_wins_over_the_one_asked_for(self):
        blue = self.tmp / "blue.mp4"
        make_clip(blue, key=(0, 0, 255))
        cells = ev.cut_cells(blue, 2, 2, CFG, "green")
        self.assertEqual({c["key"] for c in cells}, {"blue"})

    def test_a_good_cell_becomes_a_sticker_a_late_one_is_faded_and_says_so(self):
        good, late = ev.finish_cell(self.cells[0], CFG), ev.finish_cell(self.cells[1], CFG)
        for r in (good, late):
            self.assertEqual(r["status"], "READY", r["checks"])
            self.assertEqual(r["blocks"], [])
            self.assertLessEqual(len(r["data"]), CFG.video_max_bytes)
            self.assertEqual(r["metrics"]["frames"], 90)
        self.assertNotIn("effect_tail_faded", good["warnings"])
        self.assertIn("effect_tail_faded", late["warnings"], "it still had pieces on screen at the end: faded, and said")
        self.assertTrue(late["metrics"]["tail_faded"])
        self.assertGreater(late["metrics"]["raw_end_coverage"], 0.003)

    def test_settle_makes_both_ends_empty_by_construction(self):
        fr = np.zeros((90, 64, 64, 4), np.uint8)
        fr[:, 20:40, 20:40] = (255, 0, 0, 255)                       # on screen all the time: no empty start, no empty end
        out, info = ev.settle(fr)
        self.assertEqual(info, {"tail_faded": True, "head_faded": True})
        cov = effect_checks.coverage_curve(out)
        self.assertEqual((max(cov[:3]), max(cov[-3:])), (0.0, 0.0))
        self.assertGreater(max(cov), 0.05, "the middle is untouched")

    def test_an_effect_check_never_blocks_only_a_telegram_limit_does(self):
        r = ev.encode_and_check(np.zeros((90, 512, 512, 4), np.uint8), CFG)           # nothing on screen at all
        self.assertTrue(set(r["blocks"]) <= ev.TECHNICAL, "only Telegram's own limits may be a block")
        self.assertIn("effect_has_burst", r["warnings"], "an empty clip is a warning a person can read")

    def test_an_unreadable_cell_is_reported_not_raised(self):
        r = ev.finish_cell({"index": 7, "frames": None, "src_fps": 24.0, "error": "no key"}, CFG)
        self.assertEqual((r["status"], r["blocks"]), ("FAILED", ["cell_unreadable"]))


@unittest.skipUnless(shutil.which("ffmpeg") or ff.ffmpeg_exe(), "ffmpeg is needed")
class ScreenFlatnessTests(unittest.TestCase):
    """The key-colour screen of the returned clip is measured BEFORE keying: a flat one passes, panels, walls and lines are the WARN `key_is_seamless` (never a block)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.cut = {}
        for name, kw in (("flat", {}), ("blue", {"key": (0, 0, 255)}), ("quadrant", {"dark_cell": 3}), ("walls", {"walls": 30}), ("lines", {"lines": 90}), ("faint_line", {"lines": 25})):
            mp4 = cls.tmp / f"{name}.mp4"
            make_clip(mp4, **kw)
            cls.cut[name] = ev.cut_cells(mp4, 2, 2, CFG, "green")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def judge(self, name):
        return ev.finish_cell(self.cut[name][0], CFG)

    def test_a_flat_screen_passes_and_its_numbers_travel_with_the_cell(self):
        for name in ("flat", "blue"):
            scr = self.cut[name][0]["screen"]
            self.assertLess(scr["panel_step"], 3, scr)
            self.assertLess(scr["quarter_step"], 3, scr)
            self.assertLess(scr["bg_std"], 2, scr)
            self.assertLess(scr["seam_line"], 5, scr)
            self.assertEqual(scr["frames"], 16)
            r = self.judge(name)
            self.assertNotIn("key_is_seamless", r["warnings"], r["checks"])
            self.assertEqual((r["status"], r["blocks"]), ("READY", []))
            self.assertEqual({k: r["metrics"][f"screen_{k}"] for k in scr}, scr, "the numbers are in the metrics of every cell")
        self.assertEqual({tuple(sorted(c["screen"].items())) for c in self.cut["flat"]}, {tuple(sorted(self.cut["flat"][0]["screen"].items()))}, "one screen, the same numbers on every cell")

    def test_a_visible_darker_quadrant_is_a_warning(self):
        scr = self.cut["quadrant"][0]["screen"]
        self.assertGreater(scr["quarter_step"], 25, scr)
        r = self.judge("quadrant")
        self.assertIn("key_is_seamless", r["warnings"])
        chk = next(c for c in r["checks"] if c["id"] == "key_is_seamless")
        self.assertEqual(chk["verdict"], "WARN")
        self.assertIn("panels or patterns", chk["detail"])
        self.assertIn("use it anyway", chk["detail"].lower())
        self.assertEqual((r["status"], r["blocks"]), ("READY", []), "never a block: the person can use it anyway")

    def test_walls_at_the_edges_of_every_area_are_a_warning(self):
        scr = self.cut["walls"][0]["screen"]
        self.assertGreater(scr["panel_step"], 20, scr)
        chk = next(c for c in self.judge("walls")["checks"] if c["id"] == "key_is_seamless")
        self.assertEqual((chk["verdict"], chk["limit"]), ("WARN", effect_checks.SCREEN_PANEL_MAX))
        self.assertGreater(chk["value"], chk["limit"])

    def test_a_strong_line_on_the_boundary_is_a_warning_a_faint_one_is_not(self):
        self.assertGreater(self.cut["lines"][0]["screen"]["seam_line"], effect_checks.SCREEN_LINE_MAX)
        self.assertIn("key_is_seamless", self.judge("lines")["warnings"])
        faint = self.cut["faint_line"][0]["screen"]["seam_line"]
        self.assertTrue(10 < faint < effect_checks.SCREEN_LINE_MAX, faint)
        self.assertNotIn("key_is_seamless", self.judge("faint_line")["warnings"], "the clip rated great had a 27-level line and keyed cleanly")

    def test_the_check_is_pure_and_only_ever_a_warning(self):
        ok = effect_checks.screen_check({"panel_step": 3.0, "quarter_step": 3.0, "bg_std": 1.6, "seam_line": 27.2}, "blue")
        self.assertEqual((ok.id, ok.verdict, ok.ok), ("key_is_seamless", "PASS", True))
        bad = effect_checks.screen_check({"panel_step": 20.0, "quarter_step": 9.0, "bg_std": 11.0, "seam_line": 2.0}, "green")
        self.assertEqual((bad.verdict, bad.data["metric"]), ("WARN", "panel_step"), "the one that is furthest over its limit is named: 20/10 beats 11/6")
        self.assertIn("the green screen has panels or patterns", bad.detail)
        self.assertEqual(effect_checks.screen_check({"panel_step": None, "quarter_step": None, "bg_std": None}).verdict, "PASS", "nothing measured, nothing to say")
        for scr in ({"panel_step": 999.0, "quarter_step": 999.0, "bg_std": 999.0, "seam_line": 999.0}, {}):
            self.assertIn(effect_checks.screen_check(scr).verdict, {"PASS", "WARN"})

    def test_no_measurement_adds_nothing(self):
        cell = dict(self.cut["quadrant"][0], screen=None)
        r = ev.finish_cell(cell, CFG)
        self.assertNotIn("key_is_seamless", r["warnings"])
        self.assertFalse([k for k in r["metrics"] if k.startswith("screen_")])
        self.assertIsNone(ev.screen_of([None], self.tmp / "flat.mp4", 2, 2, "green", (480, 480)), "a measurement never raises")

    def test_the_mosaic_is_checked_for_its_shape(self):
        with self.assertRaises(ValueError):
            effect_checks.screen_flatness(np.zeros((2, 100, 100, 3), np.uint8), 2, 2)


if __name__ == "__main__":
    unittest.main()
