"""The verifier catalogue, with PASS and FAIL fixtures for the checks named here. That every one of the 44 has both is proven by the table in
`tests/test_verify_fixtures.py`, and `tests/test_hardening.py` pins the catalogue's size, so a new check cannot be added without both being looked at."""
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from stickerloom.engine import verify
from stickerloom.engine.config import EngineConfig
from stickerloom.engine.grid import split_grid
from stickerloom.engine.sheet import process_sheet
from stickerloom.engine.video import check_returned_video, process_video
from stickerloom.engine.video_sheet import build_video_sheet
from tests import synth

CFG = EngineConfig()
SMALL = EngineConfig(min_sheet_px=256)          # the synthetic sheets are small; production sheets are 2K


def disc_sheet(size, centres, r=60, seed=7, extra=None):
    s = synth.bg(size, seed).copy()
    for cx, cy in centres:
        cv2.circle(s, (cx, cy), r, synth.YELLOW, -1, cv2.LINE_AA)
    for f in extra or []:
        f(s)
    return s


GRID9 = [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)]


def png(rgb):
    return cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))[1].tobytes()


def ck(checks, cid):
    return next(c for c in checks if c.id == cid)


def sheet_checks(rgb_or_bytes, grid=(3, 3), cfg=CFG):
    inp = {"grid": grid}
    inp["data" if isinstance(rgb_or_bytes, bytes) else "rgb"] = rgb_or_bytes
    return verify.run("sheet", inp, cfg)


class SheetStageTests(unittest.TestCase):
    good = disc_sheet(1200, GRID9, r=80)

    def test_all_pass_on_a_clean_sheet(self):
        cs = sheet_checks(png(self.good))
        self.assertTrue(all(c.ok for c in cs), [(c.id, c.note) for c in cs if not c.ok])
        self.assertEqual([c.id for c in cs], ["sheet_decodes", "sheet_size", "background_is_key", "grid_detected", "cut_clean", "background_flat"])

    def test_sheet_decodes(self):
        cs = sheet_checks(b"not an image")
        self.assertEqual([c.id for c in cs], ["sheet_decodes"])      # a gate: nothing else runs on a file that does not decode
        self.assertFalse(cs[0].ok)

    def test_sheet_size(self):
        self.assertTrue(ck(sheet_checks(self.good), "sheet_size").ok)
        small = sheet_checks(disc_sheet(600, [(x // 2, y // 2) for x, y in GRID9], r=40))
        self.assertFalse(ck(small, "sheet_size").ok)
        self.assertTrue(ck(sheet_checks(small and disc_sheet(600, [(x // 2, y // 2) for x, y in GRID9], r=40), cfg=SMALL), "sheet_size").ok)

    def test_background_is_key(self):
        self.assertTrue(ck(sheet_checks(self.good), "background_is_key").ok)
        grey = np.full((1200, 1200, 3), 128, np.uint8)
        cv2.circle(grey, (600, 600), 200, synth.YELLOW, -1)
        self.assertFalse(ck(sheet_checks(grey), "background_is_key").ok)

    def test_grid_detected(self):
        self.assertTrue(ck(sheet_checks(self.good, (3, 3)), "grid_detected").ok)
        c = ck(sheet_checks(self.good, (2, 2)), "grid_detected")
        self.assertFalse(c.ok)
        self.assertEqual((c.value, c.limit), ("3x3", "2x2"))

    def test_cut_clean(self):
        self.assertTrue(ck(sheet_checks(self.good), "cut_clean").ok)
        blob = disc_sheet(1200, [(600, 600)], r=560)               # one giant subject: no gutter anywhere near the thirds
        c = ck(sheet_checks(blob), "cut_clean")
        self.assertFalse(c.ok)
        self.assertEqual(c.severity, verify.WARN)

    def test_background_flat(self):
        self.assertTrue(ck(sheet_checks(self.good), "background_flat").ok)
        grad = disc_sheet(1200, GRID9, r=80)
        ramp = np.linspace(110, 255, 1200)[None, :]                # a strong gradient screen: still keyable, but it keys worse
        grad[..., 1] = np.where(grad[..., 0] < 100, ramp.astype(np.uint8), grad[..., 1])
        c = ck(sheet_checks(grad), "background_flat")
        self.assertFalse(c.ok)
        self.assertEqual(c.severity, verify.WARN)


def one_cell(draw, size=300, cfg=CFG):
    s = synth.bg(size, 3).copy()
    draw(s)
    rects, _ = split_grid(s, 1, 1)
    return process_sheet(s, cfg, rects)[0]


class StillStageTests(unittest.TestCase):
    def test_holes(self):
        plain = one_cell(lambda s: cv2.circle(s, (150, 150), 80, synth.YELLOW, -1, cv2.LINE_AA))
        self.assertEqual(plain.status, "READY")
        self.assertNotIn("holes", plain.metrics.get("warnings", []))

        def holed(r):
            return lambda s: (cv2.circle(s, (150, 150), 80, synth.YELLOW, -1, cv2.LINE_AA), cv2.circle(s, (150, 150), r, tuple(int(v) for v in s[2, 2]), -1))
        small = one_cell(holed(30))                                   # a green part keyed away: a WARN for the human
        self.assertEqual(small.status, "READY")
        self.assertIn("holes", small.metrics["warnings"])
        big = one_cell(holed(65))                                     # most of the subject is hole: BLOCK, and keying again cannot fix it
        self.assertEqual((big.status, big.reason), ("FAILED", "holes"))
        self.assertTrue(big.metrics["ruled_out"])

    def test_single_subject(self):
        self.assertNotIn("single_subject", one_cell(lambda s: cv2.circle(s, (150, 150), 60, synth.YELLOW, -1)).metrics.get("warnings", []))

        def two(s):
            cv2.circle(s, (90, 150), 45, synth.YELLOW, -1)
            cv2.circle(s, (215, 150), 45, synth.RED, -1)
        r = one_cell(two)
        self.assertEqual(r.status, "READY")
        self.assertIn("single_subject", r.metrics["warnings"])

    def test_duplicate_cell(self):
        def sheet(shapes):
            s = synth.bg(600, 5).copy()
            for (cx, cy), shape in zip([(150, 150), (450, 150), (150, 450), (450, 450)], shapes):
                if shape == "disc":
                    cv2.circle(s, (cx, cy), 80, synth.YELLOW, -1, cv2.LINE_AA)
                elif shape == "tall":
                    cv2.rectangle(s, (cx - 40, cy - 110), (cx + 40, cy + 110), synth.RED, -1)
                elif shape == "wide":
                    cv2.rectangle(s, (cx - 110, cy - 40), (cx + 110, cy + 40), synth.YELLOW, -1)
                else:
                    cv2.fillPoly(s, [np.array([[cx, cy - 90], [cx - 90, cy + 70], [cx + 90, cy + 70]])], (60, 90, 220), cv2.LINE_AA)
            rects, _ = split_grid(s, 2, 2)
            return process_sheet(s, CFG, rects)
        distinct = sheet(["disc", "tall", "wide", "tri"])
        self.assertTrue(all("duplicate_cell" not in r.metrics.get("warnings", []) for r in distinct), [r.metrics.get("warnings") for r in distinct])
        rep = sheet(["disc", "disc", "wide", "tri"])                  # the model repeated a pose in cell 2
        self.assertIn("duplicate_cell", rep[1].metrics["warnings"])
        self.assertNotIn("duplicate_cell", rep[0].metrics.get("warnings", []))
        self.assertEqual(rep[1].report.get("duplicate_cell").detail["of"], 1)

    def test_blank_cell_keeps_its_reason(self):
        r = one_cell(lambda s: None)
        self.assertEqual((r.status, r.reason), ("FAILED", "empty_subject"))
        self.assertEqual(r.report.get("blank_cell").reason, "empty_subject")


def stickers_for(slots, **kw):
    shapes = {1: "disc", 2: "tall", 3: "wide", 4: "tri", 5: "disc", 6: "tall", 7: "wide", 8: "tri", 9: "disc"}
    return {i: synth.sticker_rgba(shapes[i], **kw) for i in slots}


def build(approved=(1, 2, 3, 4, 7, 8, 9), cfg=CFG, **kw):
    return build_video_sheet(stickers_for(range(1, 10), **kw), list(approved), cfg, (3, 3))


class VideoSheetTests(unittest.TestCase):
    def test_golden_hash_and_layout(self):
        s1, lay1 = build()
        s2, lay2 = build()
        self.assertEqual(hashlib.sha256(s1.tobytes()).hexdigest(), hashlib.sha256(s2.tobytes()).hexdigest())
        self.assertEqual(lay1, lay2)
        self.assertEqual(s1.shape, (2048, 2048, 3))
        self.assertEqual([sl["sticker"] for sl in lay1["slots"]], ["S1", "S2", "S3", "S4", None, None, "S7", "S8", "S9"])
        for sl in lay1["slots"]:
            x, y, w, h = sl["rect"]
            if sl["sticker"] is None:            # blank slots are PURE key colour
                self.assertTrue((s1[y:y + h, x:x + w] == np.array([0, 255, 0], np.uint8)).all())
            else:                                 # the subject stays inside its slot with a generous margin (<= 55% of the slot)
                sx, sy, sw, sh = sl["subject_rect"]
                fill = EngineConfig().slot_fill
                self.assertLessEqual(max(sw, sh), fill * min(w, h) + 1)
                self.assertGreaterEqual(min(sx - x, sy - y, x + w - sx - sw, y + h - sy - sh), (1 - fill) / 2 * min(w, h) - 1)

    def test_checks_pass_on_a_built_sheet(self):
        sheet, lay = build()
        cs = verify.run("video_sheet", {"sheet": sheet, "layout": lay, "approved": [1, 2, 3, 4, 7, 8, 9]}, CFG)
        self.assertTrue(all(c.ok for c in cs), [(c.id, c.note) for c in cs])

    def test_slots_match_approved(self):
        sheet, lay = build()
        self.assertFalse(ck(verify.run("video_sheet", {"sheet": sheet, "layout": lay, "approved": [1, 2, 3, 4, 5, 7, 8, 9]}, CFG), "slots_match_approved").ok)
        dirty = sheet.copy()
        x, y, w, h = lay["slots"][4]["rect"]
        dirty[y + 10, x + 10] = (255, 0, 0)                           # one stray pixel in blank slot 5
        c = ck(verify.run("video_sheet", {"sheet": dirty, "layout": lay, "approved": [1, 2, 3, 4, 7, 8, 9]}, CFG), "slots_match_approved")
        self.assertFalse(c.ok)

    def test_no_outline_on_sheet(self):
        sheet, lay = build(ring=14)                                   # stickers that still carry the white die-cut outline
        c = ck(verify.run("video_sheet", {"sheet": sheet, "layout": lay, "approved": [1, 2, 3, 4, 7, 8, 9]}, CFG), "no_outline_on_sheet")
        self.assertFalse(c.ok)
        self.assertEqual(c.detail["slots"], [1, 2, 3, 4, 7, 8, 9])
        white = build_video_sheet({1: synth.sticker_rgba("disc", colour=(255, 255, 255))}, [1], CFG, (1, 1))       # a white subject has no ring
        self.assertTrue(ck(verify.run("video_sheet", {"sheet": white[0], "layout": white[1], "approved": [1]}, CFG), "no_outline_on_sheet").ok)

    def test_refuses_bad_input(self):
        with self.assertRaises(ValueError):
            build_video_sheet({}, [], CFG)
        with self.assertRaises(ValueError):
            build_video_sheet({1: synth.sticker_rgba("disc")}, [2], CFG)


class VideoStageTests(unittest.TestCase):
    sheet, lay = build()
    info = {"codec": "h264", "width": 600, "height": 600, "fps": 30.0, "duration": 2.0, "audio": False}

    def run_video(self, **kw):
        small = cv2.resize(self.sheet, (600, 600), interpolation=cv2.INTER_AREA)
        inp = {"info": self.info, "first_frame": small, "layout": self.lay, "sheet": self.sheet, "blank_share": {5: 0.0, 6: 0.0}}
        inp.update(kw)
        return verify.run("video", inp, CFG)

    def test_pass(self):
        cs = self.run_video()
        self.assertTrue(all(c.ok for c in cs), [(c.id, c.note) for c in cs])

    def test_video_decodes(self):
        cs = self.run_video(first_frame=None)
        self.assertEqual([c.id for c in cs], ["video_decodes"])
        self.assertFalse(cs[0].ok)

    def test_video_specs(self):
        self.assertFalse(ck(self.run_video(info=dict(self.info, duration=0.4)), "video_specs").ok)

    def test_layout_match(self):
        other, _ = build_video_sheet({i: synth.sticker_rgba("tri" if i % 2 else "wide") for i in range(1, 10)}, [1, 2, 3, 4, 7, 8, 9], CFG, (3, 3))
        c = ck(self.run_video(first_frame=cv2.resize(other, (600, 600), interpolation=cv2.INTER_AREA)), "layout_match")
        self.assertFalse(c.ok)                  # a video made from some other sheet
        self.assertTrue(c.detail["bad"])

    def test_blank_slots_stay_empty(self):
        self.assertFalse(ck(self.run_video(blank_share={5: 0.0, 6: 0.04}), "blank_slots_stay_empty").ok)


def frames_of(fn, n=20, size=200):
    out = np.zeros((n, size, size, 4), np.uint8)
    for t in range(n):
        fn(out[t], t)
    return out


def disc_at(cx, cy, r=30):
    def draw(f, t):
        cv2.circle(f, (int(cx(t)), int(cy(t))), r, (250, 220, 20, 255), -1)
    return draw


class SlotStageTests(unittest.TestCase):
    def test_inside_slot(self):
        ok = frames_of(disc_at(lambda t: 100, lambda t: 100))
        self.assertTrue(ck(verify.run("slot", {"slot_frames": ok}, CFG), "inside_slot").ok)
        drift = frames_of(disc_at(lambda t: 100 - t * 4.5, lambda t: 100))            # slides left until it touches the slot border
        c = ck(verify.run("slot", {"slot_frames": drift}, CFG), "inside_slot")
        self.assertFalse(c.ok)
        self.assertGreater(c.detail["frame"], 10)
        self.assertGreater(c.detail["over_px"], 0)
        self.assertIsNone(verify.run("slot", {}, CFG)[0] if False else None)          # no layout -> the check does not apply (returns None)

    def test_inside_frame_is_the_still_border_rule_per_frame(self):
        ok = frames_of(disc_at(lambda t: 100, lambda t: 100))
        self.assertTrue(ck(verify.run("slot", {"cell_frames": ok}, CFG), "inside_frame").ok)
        drift = frames_of(disc_at(lambda t: 100 - t * 4.5, lambda t: 100))            # leaves its cell: the video is cropped
        c = ck(verify.run("slot", {"cell_frames": drift}, CFG), "inside_frame")
        self.assertFalse(c.ok)
        self.assertGreater(c.detail["frame"], 10)
        self.assertGreater(c.detail["frames_over"], 0)
        self.assertIsNone(next((x for x in verify.run("slot", {"slot_frames": ok}, CFG) if x.id == "inside_frame"), None))   # a video-sheet slot has inside_slot instead

    def test_out_of_bounds_animation_is_made_but_blocked_for_review(self):
        import tempfile
        from pathlib import Path
        from stickerloom.flow import pipeline as pl
        from stickerloom.engine.video import AnimationResult
        drift = frames_of(disc_at(lambda t: 100 - t * 4.5, lambda t: 100))
        rep = verify.Report(verify.run("slot", {"cell_frames": drift}, CFG))
        self.assertTrue(rep.ok)                                   # a WARN: the engine does not cancel the video
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "slices").mkdir()
            st = {"name": "img-001-a-b", "review": {"still": "APPROVED", "anim": "NONE"}, "history": []}
            pl.record_anim(Path(td), st, AnimationResult(1, "READY", None, rep, {}, b"webm"), "prepared video")
            self.assertEqual((st["anim_status"], st["review"]["anim"]), ("READY", "BLOCKED"))
            self.assertTrue((Path(td) / st["webm"]).exists())     # kept to look at
            self.assertEqual((st["history"][-1]["decision"], st["history"][-1]["reason"]), ("BLOCK", "inside_frame"))
            st["anim_finished"] = {"plain": {"anim_status": "FAILED"}}
            pl.record_anim(Path(td), st, AnimationResult(1, "READY", None, rep, {}, b"webm"), "new cut")
            self.assertNotIn("anim_finished", st, "a genuine new cut invalidates remembered verdicts of the old pixels")
            st["anim_finished"] = {"plain": {"anim_status": "FAILED"}}
            pl.record_anim(Path(td), st, AnimationResult(1, "READY", None, rep, {}, b"webm"), "verdict switch", preserve_finished=True)
            self.assertIn("plain", st["anim_finished"], "the verdict-switch render keeps the other finished verdict")

    def test_cross_slot(self):
        self.assertTrue(ck(verify.run("slot", {"slot_frames": frames_of(disc_at(lambda t: 100, lambda t: 100))}, CFG), "cross_slot").ok)

        def two(f, t):
            cv2.circle(f, (100, 100), 30, (250, 220, 20, 255), -1)
            cv2.circle(f, (170, 100), 14, (250, 220, 20, 255), -1)      # a second character in the gutter, not touching the border
        c = ck(verify.run("slot", {"slot_frames": frames_of(two)}, CFG), "cross_slot")
        self.assertFalse(c.ok)
        self.assertGreaterEqual(c.detail["px"], CFG.min_component_px)


class AnimExtraTests(unittest.TestCase):
    def test_identity_kept(self):
        ref = synth.sticker_rgba("disc")[..., 3]
        same = np.stack([synth.sticker_rgba("disc")] * 3)
        self.assertTrue(verify.identity_kept({"ref_alpha": ref, "frames_out": same}, CFG).ok)
        other = np.stack([synth.sticker_rgba("wide")] * 3)
        c = verify.identity_kept({"ref_alpha": ref, "frames_out": other}, CFG)
        self.assertFalse(c.ok)
        self.assertEqual(c.severity, verify.WARN)
        self.assertIsNone(verify.identity_kept({"frames_out": same}, CFG))      # no reference -> not applicable

    def test_motion_present(self):
        self.assertTrue(verify.motion_present({"metrics": {"motion": 4.0}}, CFG).ok)
        self.assertFalse(verify.motion_present({"metrics": {"motion": 0.0}}, CFG).ok)

    def test_alpha_stable(self):
        steady = np.stack([synth.sticker_rgba("disc")] * 6)
        self.assertTrue(verify.alpha_stable({"frames_out": steady}, CFG).ok)
        pump = np.stack([synth.sticker_rgba("disc") if t % 2 else np.zeros((512, 512, 4), np.uint8) for t in range(6)])
        pump[::2, 200:260, 200:260] = 255
        self.assertFalse(verify.alpha_stable({"frames_out": pump}, CFG).ok)

    def test_sharpness_is_what_survives_the_encode(self):
        from stickerloom.engine.video import edge_energy
        sharp = synth.sticker_rgba("disc").copy()
        yy, xx = np.mgrid[:512, :512]
        sharp[..., :3] = (((xx // 4 + yy // 4) % 2) * 255).astype(np.uint8)[..., None]          # texture: a flat disc has no edge detail to lose
        blur = cv2.GaussianBlur(sharp, (0, 0), 3)
        self.assertGreater(edge_energy(sharp), edge_energy(blur))                          # a blur loses edge detail
        ok = verify.sharpness({"metrics": {"sharp_kept": 1.0}}, CFG)
        self.assertTrue(ok.ok)
        bad = verify.sharpness({"metrics": {"sharp_kept": 0.5}}, CFG)
        self.assertFalse(bad.ok)
        self.assertEqual(bad.severity, verify.WARN)                                        # a warning: it never blocks an animation
        self.assertIsNone(verify.sharpness({"metrics": {}}, CFG))                          # not measured -> not applicable


class PackTests(unittest.TestCase):
    def st(self, key="a", emoji="😀", kind="static", n=1000):
        return {"key": key, "emoji": emoji, "kind": kind, "bytes": n}

    def test_pack_limits(self):
        self.assertTrue(verify.run("pack", {"stickers": [self.st("a"), self.st("b", kind="animated", n=200 * 1024)]}, CFG)[0].ok)
        for bad in ([], [self.st("a"), self.st("a")], [self.st("a", emoji="")], [self.st("a", kind="animated", n=300 * 1024)], [self.st(str(i)) for i in range(121)]):
            self.assertFalse(verify.run("pack", {"stickers": bad}, CFG)[0].ok, bad[:2])


class RunnerTests(unittest.TestCase):
    def test_stored_animation_uses_the_same_waiver_and_keeps_hard_failures(self):
        seam = verify.Check("loop_seam", "anim", verify.BLOCK, False, 40, 12, note="loop is open")
        warning = verify.Check("sharpness", "anim", verify.WARN, False, note="soft")
        metrics = {"waived": ["old"], "warnings": ["old"], "kb": 42}
        report = verify.animation_verdict(verify.Report([seam, warning]).checks, ["loop_seam"], metrics)
        self.assertTrue(report.ok)
        self.assertEqual((report.get("loop_seam").ok, report.get("loop_seam").severity), (False, verify.WARN))
        self.assertEqual(metrics, {"waived": ["loop_seam"], "warnings": ["sharpness"], "kb": 42})
        report = verify.animation_verdict(report.checks, [], metrics)
        self.assertEqual(report.first_failure, "loop_seam")
        self.assertEqual(report.get("loop_seam").note, seam.note)
        self.assertNotIn("waived", metrics)
        hard = verify.Check("size_budget", "anim", verify.BLOCK, False)
        crash = verify.Check("loop_seam", "anim", verify.BLOCK, False, detail={"error": "crash"}, reason="verifier_error")
        for check in (hard, crash):
            report = verify.animation_verdict([check.to_dict()], [check.id], {})
            self.assertFalse(report.ok)
            self.assertEqual(report.first_failure, check.reason or check.id)

    def test_existing_clip_switches_without_slicing_or_animating(self):
        from unittest import mock
        from stickerloom.flow import gates as G, pipeline as pl
        seam = verify.Check("loop_seam", "anim", verify.BLOCK, False, note="loop is open")
        for source in (("sheet", "A1"), ("prepared", None)):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as td:
                st = {"index": 1, "anim_status": "FAILED", "anim_reason": "loop_seam", "anim_report": [seam.to_dict()],
                      "anim_metrics": {}, "webm": "clip.webm", "review": {"anim": "BLOCKED"}, "history": []}
                res = {"stickers": [st]}
                clip = Path(td) / "clip.webm"
                clip.write_bytes(b"finished clip")
                with mock.patch.object(pl, "read_result", return_value=res), mock.patch.object(pl, "write_result"), \
                     mock.patch.object(G, "check_allow", return_value=(st, source[1])), mock.patch.object(G, "anim_source", return_value=source), \
                     mock.patch.object(G, "slice_video", side_effect=AssertionError("no render")), \
                     mock.patch.object(G, "reanimate_prepared", side_effect=AssertionError("no render")):
                    for allow, status in ((True, "READY"), (False, "FAILED"), (True, "READY")):
                        G.allow_animations(Path(td), 1, [1], allow, CFG)
                        self.assertEqual(st["anim_status"], status)
                        self.assertEqual(clip.read_bytes(), b"finished clip")
                self.assertEqual([h["actor"] for h in st["history"]], ["human"] * 3)

    def test_short_slot_report_cannot_hide_a_finished_clips_loop_failure(self):
        from stickerloom.flow import gates as G
        slot = verify.Check("inside_slot", "slot", verify.BLOCK, False)
        seam = verify.Check("loop_seam", "anim", verify.BLOCK, False)
        full = verify.animation_verdict([slot.to_dict(), seam.to_dict()], ["inside_slot", "loop_seam"], {})
        st = {"webm": "clip.webm", "anim_status": "FAILED", "anim_reason": "inside_slot", "anim_report": [slot.to_dict()],
              "anim_override": [], "anim_metrics": {}, "review": {"anim": "BLOCKED"},
              "anim_finished": {"inside_slot,loop_seam": {"webm": "clip.webm", "anim_report": full.checks}}}
        G._remember_anim_pair(st)
        verdict = st["anim_finished"]["inside_slot"]
        self.assertEqual((verdict["anim_status"], verdict["anim_reason"]), ("FAILED", "loop_seam"))
        self.assertFalse(next(c for c in verdict["anim_report"] if c["name"] == "loop_seam")["ok"])

    def test_a_crashing_check_is_a_block_not_an_exception(self):
        cs = verify.run("pack", {}, CFG)                               # inputs missing: the check raises KeyError inside
        self.assertEqual((cs[0].id, cs[0].ok, cs[0].severity, cs[0].reason), ("pack_limits", False, verify.BLOCK, "verifier_error"))

    def test_warn_never_fails_a_report_and_block_is_final(self):
        W = verify.Check("w", "still", verify.WARN, False)
        B = verify.Check("b", "still", verify.BLOCK, False, reason="why")
        self.assertTrue(verify.Report([W]).ok)
        rep = verify.Report([W, B])
        self.assertFalse(rep.ok)
        self.assertEqual((rep.first_failure, rep.warnings), ("why", ["w"]))
        self.assertEqual(set(rep.checks[0]), {"name", "ok", "detail", "severity", "stage", "value", "limit", "data"})

    def test_deterministic(self):
        s = disc_sheet(1200, GRID9, r=80)
        a = [(c.id, c.ok, c.value) for c in sheet_checks(s)]
        self.assertEqual(a, [(c.id, c.ok, c.value) for c in sheet_checks(s)])


class LayoutSlicingTests(unittest.TestCase):
    """Engine-level slice of the golden-path scenario: slots 1 and 2 drift out of their slots; the rest stay."""

    def test_drifting_slots_are_blocked_by_inside_slot(self):
        sheet, lay = build()
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / "v.mp4"
            synth.make_layout_video(mp4, sheet, lay, size=600, frames=60, drift={1: (-70, 0), 2: (0, -70)})
            checks = check_returned_video(mp4, lay, sheet, CFG)
            self.assertTrue(all(c.ok for c in checks), [(c.id, c.note) for c in checks if not c.ok])
            res = {r.index: r for r in process_video(mp4, CFG, cells=[1, 2, 3, 4, 7, 8, 9], layout=lay)}
        for i in (1, 2):
            self.assertEqual((res[i].status, res[i].reason), ("FAILED", "inside_slot"), (i, res[i].reason, res[i].metrics))
            self.assertIsNone(res[i].data)
            self.assertGreater(res[i].report.get("inside_slot").detail["over_px"], 0)
        for i in (3, 4, 7, 8, 9):
            self.assertEqual(res[i].status, "READY", (i, res[i].reason, res[i].report.checks))
            self.assertGreater(res[i].metrics["subject_px_in_video"], 0)
            self.assertEqual(res[i].metrics["source"], "video sheet")

    def test_blank_slot_invented_character(self):
        sheet, lay = build()
        busy = sheet.copy()
        x, y, w, h = lay["slots"][4]["rect"]
        cv2.circle(busy, (x + w // 2, y + h // 2), 150, (250, 220, 20), -1)      # the model drew a character into blank slot 5
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / "v.mp4"
            synth.make_layout_video(mp4, busy, dict(lay, slots=[dict(sl, sticker=sl["sticker"] or "S5") if sl["slot"] == 5 else sl for sl in lay["slots"]]), size=600, frames=30)
            cs = check_returned_video(mp4, lay, sheet, CFG)
        self.assertFalse(ck(cs, "blank_slots_stay_empty").ok)
        self.assertTrue(ck(cs, "layout_match").ok)                     # the approved slots still match: only the generation's video is flagged


if __name__ == "__main__":
    unittest.main()
