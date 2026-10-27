import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from stickerloom.engine.config import EngineConfig
from stickerloom.engine.sheet import encode_static, process_sheet
from stickerloom.engine.video import close_loop, loop_seam, process_video
from stickerloom.engine.grid import detect_grid, scale_rects, split_grid
from stickerloom.generation import prompter
from tests import synth

CFG = EngineConfig()


def alpha_bbox(data):
    a = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))[..., 3] > 127
    ys, xs = np.where(a)
    return xs.min(), ys.min(), xs.max(), ys.max()


class SheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = process_sheet(synth.make_sheet(), CFG)

    def test_statuses(self):
        # S2 is a ring: a stroke closes it. No stroke is the default since 2026-10-12, and then its hole blocks (holes), so cut with one here.
        by = {r.index: r for r in process_sheet(synth.make_sheet(), replace(CFG, outline_px=12))}
        self.assertEqual(by[5].reason, "empty_subject")
        self.assertEqual(by[6].reason, "inside_cell")
        for i in (1, 2, 3, 4, 7, 8, 9):
            self.assertEqual(by[i].status, "READY", (i, by[i].reason, by[i].report.checks))
            self.assertEqual(Image.open(io.BytesIO(by[i].data)).size, (512, 512))

    def test_failed_cells_are_dissected_and_ruled_out(self):
        by = {r.index: r for r in self.res}
        m = by[6].metrics   # subject touches the border: keying can't fix it -> dissect, then ruled out
        self.assertTrue(m["ruled_out"])
        self.assertEqual([a["name"] for a in m["attempts"]], ["default", "dissect"])
        self.assertIn("verdict", m["attempts"][1]["note"])
        m = by[5].metrics   # blank: re-keyed with the sheet-wide background and a lower threshold, then ruled out
        self.assertTrue(m["ruled_out"])
        self.assertEqual(len(m["attempts"]), 4)
        self.assertNotIn("attempts", by[1].metrics)   # healthy cells are not retried

    def test_detached_detail_survives(self):
        # cell 4: circle + sweat drop near the bottom; bbox must reach the drop
        self.assertGreaterEqual(self.res[3].metrics["bbox"][3], 178)

    def test_pack_consistent_scale(self):
        a, b = alpha_bbox(self.res[6].data), alpha_bbox(self.res[7].data)   # tall vs wide, same dimensions
        tall, wide = a[3] - a[1], b[2] - b[0]
        self.assertLess(abs(tall - wide) / tall, 0.02)

    def test_golden_determinism(self):
        h = lambda rs: hashlib.sha256(b"".join(r.data or b"" for r in rs)).hexdigest()
        self.assertEqual(h(self.res), h(process_sheet(synth.make_sheet(), CFG)))

    def test_blue_chroma_keeps_green_subject(self):
        cfg = EngineConfig(chroma="blue")
        r = process_sheet(synth.make_sheet(chroma="blue", subj=(30, 200, 40)), cfg)[0]
        self.assertEqual(r.status, "READY", r.report.checks)
        px = np.array(Image.open(io.BytesIO(r.data)).convert("RGBA"))[256, 256]
        self.assertGreater(px[1], px[0] + 50)   # still green, not keyed away

    def test_webp_fallback(self):
        rng = np.random.default_rng(1)
        img = np.dstack([rng.integers(0, 8, (512, 512, 3)).astype(np.uint8) * 30, np.full((512, 512), 255, np.uint8)])
        png, fmt = encode_static(img, EngineConfig(static_max_bytes=10 ** 9))
        self.assertEqual(fmt, "png")
        data, fmt = encode_static(img, EngineConfig(static_max_bytes=int(len(png) * 0.97)))
        self.assertEqual(fmt, "webp")
        self.assertTrue((np.array(Image.open(io.BytesIO(data)).convert("RGBA")) == img).all())   # lossless
        self.assertLess(len(data), len(png))

    def test_engine_boundary(self):
        code = ("import sys, stickerloom.engine.sheet, stickerloom.engine.video;"
                "bad=[m for m in ('fastapi','psycopg','langgraph','anthropic','pydantic') if m in sys.modules];"
                "sys.exit(1 if bad else 0)")
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 0)


class VideoTests(unittest.TestCase):
    def test_process_video_and_loop_close(self):
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / "v.mp4"
            synth.make_video(mp4)
            res = process_video(mp4, CFG, cells=[1, 9])
            for r in res:
                self.assertEqual(r.status, "READY", (r.reason, r.report.checks, r.metrics))
                m = r.metrics
                self.assertGreater(m["loop_seam_before"], m["loop_limit"])   # the clip really did not loop
                self.assertLessEqual(m["loop_seam"], m["loop_limit"])        # ...and was closed
                self.assertLess(m["frames_out"], m["frames"])
                self.assertLessEqual(len(r.data), CFG.video_max_bytes)

    def test_same_scale_cuts_every_cell_at_one_scale(self):
        """Same scale (anim_same_scale): every cell at the canvas / largest-cell-side scale, times Video size; keep/fit size each clip by its subject."""
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / "v.mp4"
            synth.make_video(mp4)
            for f in (1.0, 0.8):
                res = process_video(mp4, replace(CFG, anim_same_scale=True, anim_scale=f), cells=[1, 9])
                self.assertEqual({r.status for r in res}, {"READY"}, [r.reason for r in res])
                self.assertEqual({r.metrics["size_mode"] for r in res}, {"same"})
                self.assertEqual({r.metrics["scale"] for r in res}, {round(CFG.size / 200 * f, 4)})

    def test_presliced_clip_path(self):
        import cv2
        from stickerloom.engine import ffmpeg as ff
        from stickerloom.engine.video import process_clips
        frames = np.zeros((72, 320, 320, 4), np.uint8)
        for i in range(72):                       # opaque circle drifting right on a transparent background
            cv2.circle(frames[i], (60 + i, 160), 50, (250, 220, 20, 255), -1, cv2.LINE_AA)
            frames[i][..., 3][:40] = np.where(np.random.default_rng(i).random((40, 320)) < 0.3, 2, 0)   # VP9-style alpha haze
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "teddy_01_ (1).webm"
            ff.encode_webm(frames, 24, 30, path)
            (res,) = process_clips({1: {"webm": str(path)}}, CFG)
            self.assertEqual(res.status, "READY", (res.reason, res.report.checks, res.metrics))
            self.assertEqual(res.metrics["source"], "clip:webm")
            self.assertLessEqual(res.metrics["loop_seam"], res.metrics["loop_limit"])

    def test_close_loop_math(self):
        f = np.zeros((40, 8, 8, 4), np.uint8)
        f[..., 3] = 255
        f[..., 0] = np.arange(40)[:, None, None] * 6
        before = loop_seam(f[-1], f[0])
        after = loop_seam(*[close_loop(f, 6)[i] for i in (-1, 0)])
        self.assertLess(after, before / 4)

    def test_the_loop_is_closed_at_the_end_so_the_first_frames_are_never_a_dissolve(self):
        """the old fade made the clip START on the tail's pose and dissolve into the head, the ghost on the
        first frames (and on the thumbnail). Now the head is the source untouched and the last frames ease into frame 0."""
        import cv2
        n, m = 60, 6
        f = np.zeros((n, 96, 96, 4), np.uint8)
        for i in range(n):                                  # a disc on three quarters of a circle: no two frames alike (a dissolve would show as two discs) and it does not loop by itself
            a = 2 * np.pi * 0.75 * i / n
            cv2.circle(f[i], (int(48 + 30 * np.cos(a)), int(48 + 30 * np.sin(a))), 9, (240, 40, 40, 255), -1)
        out = close_loop(f, m)
        self.assertEqual(len(out), n - m)
        self.assertTrue(np.array_equal(out[: n - 2 * m], f[: n - 2 * m]))        # everything before the fade is the source, bit for bit
        self.assertTrue(np.array_equal(out[0], f[0]))                              # frame 0 (the thumbnail, the approved pose) is never blended
        self.assertEqual(loop_seam(out[-1], out[0]), 0.0)                          # the last frame IS frame 0 ...
        dist = [float(np.abs(out[n - 2 * m + j].astype(int) - f[0].astype(int)).mean()) for j in range(m)]
        self.assertEqual(dist, sorted(dist, reverse=True))                         # ... reached by a steady approach, never a jump back
        self.assertLess(loop_seam(out[-2], out[-1]), loop_seam(f[n - m - 2], f[n - m - 1]))      # and the last step is gentler than the clip's own motion


if __name__ == "__main__":
    unittest.main()


def _grid_sheet(size, centres, r=40, seed=7):
    """Green sheet with one yellow disc per (cx, cy) centre; gutters fall wherever the discs leave room."""
    import cv2
    s = synth.bg(size, seed).copy()
    for cx, cy in centres:
        cv2.circle(s, (cx, cy), r, synth.YELLOW, -1, cv2.LINE_AA)
    return s


class HotSpotTests(unittest.TestCase):
    def test_hot_spot_canvas_coordinates_follow_configured_size(self):
        from stickerloom.engine.video import window_box
        for size in (256, 512, 768):
            self.assertEqual(window_box((10, 20, 100), [10, 20, 110, 120], size), [0, 0, size, size])

    """Resize on (anim_hotspot, 2026-10-15): the pixels opaque in at least half the frames size the sticker, so tear drops flying out do not shrink the face."""

    def test_drops_flying_out_do_not_change_the_hot_spot(self):
        from stickerloom.engine.video import hot_spot, size_window
        cfg = EngineConfig(outline_px=0, anim_hotspot=True)
        frames = []
        for i in range(10):
            f = np.zeros((400, 400, 4), np.uint8)
            f[150:250, 150:250] = (230, 200, 40, 255)                     # the face: there in every frame
            d = 20 + i * 18                                                 # a drop flying out to the corner, a new place every frame
            f[150 - d:160 - d, 150 - d:160 - d] = (60, 120, 230, 255)
            frames.append(f)
        h = hot_spot(frames, 0.5)
        self.assertEqual(h["core_bbox"], [150, 150, 250, 250])             # only the face
        self.assertEqual(h["core_centre"], [199.5, 199.5])
        union = [0, 0, 250, 250]
        mode, window, scale = size_window(cfg, (400, 400), {"core_bbox": h["core_bbox"], "core_centre": h["core_centre"], "bbox": union, "first_bbox": [130, 130, 250, 250]})
        self.assertEqual(mode, "hot")
        self.assertAlmostEqual(scale, cfg.hot_fill * cfg.size / 100)       # the face fills hot_fill of the canvas, whatever the drops do
        _, fit_window, fit = size_window(EngineConfig(anim_keep_size=False), (400, 400), {"bbox": union})
        self.assertIsNone(fit_window)
        self.assertLess(fit, scale / 2)                                    # the old fit shrank it to make room for the drop
        moved = size_window(EngineConfig(outline_px=0, anim_hotspot=True, anim_offset=(0.1, 0.0)), (400, 400), {"core_bbox": h["core_bbox"], "core_centre": h["core_centre"], "bbox": union})[1]
        self.assertAlmostEqual(window[0] - moved[0], 0.1 * window[2])       # Position moves the window, so the subject moves right by 10% of the canvas


class KeepSizeTests(unittest.TestCase):
    """Keep approved size (anim_keep_size): the animation starts exactly as its approved still, at its size and place, however far it moves."""

    def test_the_first_frame_lands_on_the_still_and_far_motion_is_cut_off(self):
        from stickerloom.engine.render import bbox_of
        from stickerloom.engine.video import keep_window, render_kept
        cfg = EngineConfig(outline_px=0)
        still = np.zeros((512, 512, 4), np.uint8)
        still[156:356, 136:336] = (200, 50, 50, 255)                       # the approved still: a 200 px square, left of centre
        frames = []
        for i in range(6):                                                  # the video: a 100 px square drifting right, far out on the last frame
            f = np.zeros((300, 300, 4), np.uint8)
            x = 100 + i * 30
            f[100:200, x:x + 100] = (200, 50, 50, 255)
            frames.append(f)
        window, s = keep_window(frames, still, cfg)
        self.assertAlmostEqual(s, 2.0, places=2)
        out = render_kept(frames, window, cfg)
        b = bbox_of(out[0][..., 3])
        self.assertTrue(all(abs(u - v) <= 2 for u, v in zip(b, (136, 156, 336, 356))), b)  # frame 0 IS the still: same size, same place
        self.assertEqual(bbox_of(out[-1][..., 3])[2], 512, "the far motion runs into the edge and is cut off, never shrunk")
        self.assertIsNone(keep_window(frames, None, cfg), "no still: the clip is fitted the old way")


class VideoSizeTests(unittest.TestCase):
    """Video size after the video is received (anim_scale): one more factor on keep / fit; a sticker's own size wins over the batch's."""

    def test_the_factor_scales_the_clip_and_a_sticker_value_wins(self):
        from stickerloom.engine.video import _finish
        from stickerloom.flow.pipeline import cell_cfgs
        still = np.zeros((512, 512, 4), np.uint8)
        still[156:356, 156:356] = (200, 50, 50, 255)
        frames = [np.zeros((300, 300, 4), np.uint8) for _ in range(12)]
        for f in frames:
            f[100:200, 100:200] = (200, 50, 50, 255)
        base = EngineConfig(outline_px=0)
        a = _finish(1, frames, 24.0, base, {}, ref_alpha=still).metrics
        b = _finish(1, frames, 24.0, replace(base, anim_scale=0.5), {}, ref_alpha=still).metrics
        self.assertAlmostEqual(b["scale"], a["scale"] * 0.5, places=3)
        self.assertEqual(b["video_size"], 0.5)
        res = {"stickers": [{"index": 1}, {"index": 2, "anim_scale": 1.2}, {"index": 3}]}
        groups = cell_cfgs(res, replace(base, anim_scale=0.9), [1, 2, 3])
        self.assertEqual(sorted((c.anim_scale, cs) for c, cs in groups), [(0.9, [1, 3]), (1.2, [2])])


class GridTests(unittest.TestCase):
    def test_off_third_gutters_are_cut_in_the_gutter(self):
        # columns centred at 70 / 220 / 470 on a 600 sheet: disc 2 spans 180..260, crossing the thirds line at 200
        cols, rows = (70, 220, 470), (100, 300, 500)
        s = _grid_sheet(600, [(x, y) for y in rows for x in cols])
        self.assertEqual(detect_grid(s), (3, 3))
        rects, info = split_grid(s, 3, 3)
        self.assertEqual(info["method"], "gutter")
        self.assertTrue(110 < info["xs"][1] < 180 and 260 < info["xs"][2] < 430, info["xs"])
        self.assertEqual([r.status for r in process_sheet(s, CFG, rects)], ["READY"] * 9)
        thirds = process_sheet(s, CFG)                      # the old equal split cuts disc 2 / 5 / 8
        self.assertIn("inside_cell", {r.reason for r in thirds})

    def test_two_by_two_and_single(self):
        s = _grid_sheet(400, [(100, 100), (300, 100), (100, 300), (300, 300)], r=50)
        self.assertEqual(detect_grid(s), (2, 2))
        rects, _ = split_grid(s, 2, 2)
        self.assertEqual([r.status for r in process_sheet(s, CFG, rects)], ["READY"] * 4)
        one = _grid_sheet(300, [(150, 150)], r=60)
        self.assertEqual(detect_grid(one), (1, 1))
        rects, info = split_grid(one, 1, 1)
        self.assertEqual((rects, info["method"]), ([(0, 0, 300, 300)], "single"))
        self.assertEqual([r.status for r in process_sheet(one, CFG, rects)], ["READY"])

    def test_video_uses_the_measured_rects(self):
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / "v.mp4"
            synth.make_video(mp4)                          # 600x600 video made from a 1200x1200 "sheet"
            sheet_rects = [(c * 400, r * 400, 400, 400) for r in range(3) for c in range(3)]
            self.assertEqual(scale_rects(sheet_rects, (1200, 1200), (600, 600))[4], (200, 200, 200, 200))
            res = process_video(mp4, CFG, cells=[5], rects=sheet_rects, sheet_wh=(1200, 1200))
            self.assertEqual(res[0].status, "READY", (res[0].reason, res[0].metrics))


class PrompterGridTests(unittest.TestCase):
    def test_expand_follows_the_grid(self):
        self.assertEqual(len(prompter.expand("teddy bear")["stickers"]), 9)
        p = prompter.expand("teddy bear", (2, 2))
        self.assertEqual((p["grid"], len(p["stickers"])), ([2, 2], 4))
        self.assertIn("2 rows of 2", p["sheet_prompt"])

    def test_validate_infers_grid_and_rejects_odd_counts(self):
        st = lambda n: [{"index": i, "prompt": "p", "key": f"k{i}", "emoji": "x"} for i in range(1, n + 1)]
        self.assertEqual(prompter.validate_plan({"stickers": st(4)})["grid"], [2, 2])
        self.assertEqual(prompter.validate_plan({"stickers": st(1)})["grid"], [1, 1])
        with self.assertRaises(ValueError):
            prompter.validate_plan({"stickers": st(5)})


class SpillTests(unittest.TestCase):
    def test_green_inside_the_subject_is_a_warning_not_spill(self):
        import cv2
        s = _grid_sheet(300, [(150, 150)], r=80)
        cv2.circle(s, (150, 150), 35, (120, 170, 110), -1)        # a muted green patch deep inside the subject (stays opaque)
        rects, _ = split_grid(s, 1, 1)
        r = process_sheet(s, CFG, rects)[0]
        self.assertEqual(r.status, "READY", r.report.checks)
        self.assertLessEqual(r.metrics["spill_px"], 20)
        self.assertGreater(r.metrics["chroma_risk"], CFG.chroma_risk_warn)
        self.assertEqual(r.metrics["warnings"], ["chroma_risk"])


class MergeCellsTests(unittest.TestCase):
    """P8 of the UI/UX spec: an edited slice is merged back into ONE sheet. Same layout, so the same S# by position; only the fixed cells change; the generator's separator lines inside a fixed
    cell are gone (they were a drawing artefact of the generated image, the cut itself did nothing wrong)."""

    def test_only_the_fixed_cell_changes_and_the_separator_line_in_it_is_gone(self):
        from stickerloom.engine.sheet import merge_cells
        sheet = synth.bg(1200, 3).copy()
        sheet[:, 596:604] = 255                                              # a white separator line the image model drew down the middle
        sheet[596:604, :] = 255
        rects = [(c * 600, r * 600, 600, 600) for r in range(2) for c in range(2)]
        fix = np.zeros((512, 512, 4), np.uint8)
        fix[156:356, 156:356] = (220, 40, 40, 255)
        bg = tuple(int(v) for v in np.median(sheet[10:60, 10:60].reshape(-1, 3), axis=0))
        out = merge_cells(sheet, rects, {2: fix}, {2: bg})
        self.assertEqual((out.shape, out.dtype), (sheet.shape, np.uint8))
        keep = np.ones(sheet.shape[:2], bool)
        keep[0:600, 600:1200] = False
        self.assertTrue(np.array_equal(out[keep], sheet[keep]), "every pixel outside the fixed cell is byte for byte what it was")
        self.assertTrue((out[0:600, 600:604] != 255).any(axis=-1).all(), "the separator column that fell inside the fixed cell is gone")
        self.assertEqual(tuple(out[300, 900]), (220, 40, 40), "the edited sticker sits in the middle of its cell")
        self.assertEqual(tuple(out[5, 1195]), bg, "the rest of the cell is the sheet's own key background")

    def test_nothing_to_merge_is_the_sheet_untouched_and_a_bad_index_is_refused(self):
        from stickerloom.engine.sheet import merge_cells
        sheet = synth.bg(600, 1)
        rects = [(0, 0, 300, 300), (300, 0, 300, 300)]
        self.assertTrue(np.array_equal(merge_cells(sheet, rects, {}, {}), sheet))
        with self.assertRaises(ValueError):
            merge_cells(sheet, rects, {3: np.zeros((8, 8, 4), np.uint8)}, {})
