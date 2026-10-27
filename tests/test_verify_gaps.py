"""PASS and FAIL fixtures for the verifier checks no other test named (HANDOFF: a check never seen to fail proves nothing), and a ratchet: a check cannot be added to the
catalogue without a test naming it."""
import re
import unittest
from pathlib import Path

import numpy as np

from stickerloom.engine import verify
from stickerloom.engine.config import EngineConfig

CFG = EngineConfig()
S = CFG.size


def run(stage, cid, inp):
    """The one check, on its own (`only` also skips the stage's gate), as the Check it returned."""
    out = verify.run(stage, dict(inp), CFG, only=(cid,))
    return out[0] if out else None


def rgba(size=S, corners_clear=True):
    a = np.zeros((size, size, 4), np.uint8)
    a[100:400, 100:400] = (200, 80, 60, 255)
    if not corners_clear:
        a[0, 0, 3] = 255
    return a


class StillChecks(unittest.TestCase):
    def test_dimensions(self):
        ok = run("still", "dimensions", {"render": lambda: rgba()})
        self.assertTrue(ok.ok)
        for shape in ((400, 400, 4), (S, S, 3), (S, S - 1, 4)):
            bad = run("still", "dimensions", {"render": lambda shape=shape: np.zeros(shape, np.uint8)})
            self.assertFalse(bad.ok, shape)
            self.assertEqual(bad.severity, verify.BLOCK)

    def test_transparent_corners(self):
        self.assertTrue(run("still", "transparent_corners", {"render": lambda: rgba()}).ok)
        for corner in ((0, 0), (0, S - 1), (S - 1, 0), (S - 1, S - 1)):
            def render(corner=corner):
                a = rgba()
                a[corner[0], corner[1], 3] = 255
                return a
            self.assertFalse(run("still", "transparent_corners", {"render": render}).ok, corner)

    def test_no_spill_blocks_key_colour_left_on_the_edge_band(self):
        """The pixel analysis is `_edge_analysis`; this pins the decision on its numbers: up to max(20, 0.1% of the subject) key-coloured edge pixels pass."""
        def with_spill(spill, n):
            return {"_spill": {"spill": spill, "n": n, "risk": 0.0}, "metrics": {}}
        self.assertTrue(run("still", "no_spill", with_spill(0, 20000)).ok)
        self.assertTrue(run("still", "no_spill", with_spill(20, 1000)).ok)                         # the floor of 20 px
        self.assertTrue(run("still", "no_spill", with_spill(100, 100000)).ok)                      # 0.1% of 100 000
        self.assertFalse(run("still", "no_spill", with_spill(21, 1000)).ok)
        bad = run("still", "no_spill", with_spill(101, 100000))
        self.assertFalse(bad.ok)
        self.assertEqual((bad.severity, bad.value, bad.limit), (verify.BLOCK, 101, 100.0))

    def test_static_file_is_the_encoded_size_against_the_telegram_budget(self):
        small = run("still", "static_file", {"render": lambda: rgba(), "encode": lambda img, cfg: (b"x" * 1024, "png")})
        self.assertTrue(small.ok)
        self.assertEqual(small.value, 1.0)
        big = run("still", "static_file", {"render": lambda: rgba(), "encode": lambda img, cfg: (b"x" * (CFG.static_max_bytes + 1), "webp")})
        self.assertFalse(big.ok)
        self.assertEqual(big.severity, verify.BLOCK)


def info(**kw):
    return {"codec": "vp9", "width": S, "height": S, "fps": 30.0, "duration": 2.9, "audio": False, "alpha_mode": "1", **kw}


class AnimChecks(unittest.TestCase):
    def test_size_budget(self):
        self.assertTrue(run("anim", "size_budget", {"data": b"x" * 100_000, "metrics": {"crf": 30}}).ok)
        self.assertTrue(run("anim", "size_budget", {"data": b"x" * CFG.video_max_bytes, "metrics": {"crf": 30}}).ok)            # exactly the budget passes
        bad = run("anim", "size_budget", {"data": b"x" * (CFG.video_max_bytes + 1), "metrics": {"crf": 40}})
        self.assertFalse(bad.ok)
        self.assertIn("crf40", bad.note)

    def test_codec_vp9(self):
        self.assertTrue(run("anim", "codec_vp9", {"info": info()}).ok)
        for codec in ("h264", "vp8", "av1"):
            self.assertFalse(run("anim", "codec_vp9", {"info": info(codec=codec)}).ok, codec)

    def test_dimensions(self):
        self.assertTrue(run("anim", "dimensions", {"info": info()}).ok)
        for w, h in ((480, 512), (512, 256), (1024, 1024)):
            bad = run("anim", "dimensions", {"info": info(width=w, height=h)})
            self.assertFalse(bad.ok, (w, h))
            self.assertEqual(bad.value, f"{w}x{h}")

    def test_no_audio(self):
        self.assertTrue(run("anim", "no_audio", {"info": info()}).ok)
        self.assertFalse(run("anim", "no_audio", {"info": info(audio=True)}).ok)

    def test_alpha_mode_tag_is_read_from_either_probe(self):
        self.assertTrue(run("anim", "alpha_mode_tag", {"info": info(alpha_mode=None), "info_native": {"alpha_mode": "1"}}).ok)
        self.assertTrue(run("anim", "alpha_mode_tag", {"info": info(alpha_mode="1"), "info_native": {"alpha_mode": None}}).ok)
        self.assertFalse(run("anim", "alpha_mode_tag", {"info": info(alpha_mode=None), "info_native": {"alpha_mode": None}}).ok)

    def test_alpha_decoded_needs_real_transparency_in_the_decoded_frames(self):
        def frames(lo, hi):
            f = np.full((3, 8, 8, 4), 128, np.uint8)
            f[0, 0, 0, 3], f[0, 1, 1, 3] = lo, hi
            return f
        self.assertTrue(run("anim", "alpha_decoded", {"alpha": frames(0, 255)}).ok)
        self.assertFalse(run("anim", "alpha_decoded", {"alpha": np.full((3, 8, 8, 4), 255, np.uint8)}).ok)          # opaque everywhere: the alpha was lost
        self.assertFalse(run("anim", "alpha_decoded", {"alpha": np.zeros((3, 8, 8, 4), np.uint8)}).ok)               # transparent everywhere: the picture was lost
        self.assertFalse(run("anim", "alpha_decoded", {"alpha": np.zeros((0, 8, 8, 4), np.uint8)}).ok)               # nothing decoded


class TelegramChecks(unittest.TestCase):
    def static(self, **kw):
        return {"kind": "static", "w": S, "h": S, "bytes": 100_000, "alpha": True, "emoji": ["x"], **kw}

    def video(self, **kw):
        return {"kind": "video", "w": S, "h": S, "bytes": 100_000, "alpha": True, "emoji": ["x"], "info": info(), **kw}

    def test_telegram_sticker_passes_a_valid_static_and_a_valid_video(self):
        for inp in (self.static(), self.video(), self.static(w=S, h=300), self.static(emoji=["x"] * CFG.tg_emoji_max)):
            c = run("telegram", "telegram_sticker", inp)
            self.assertTrue(c.ok, (inp, c.note))

    def test_telegram_sticker_names_each_problem(self):
        cases = [(self.static(w=513), "one side must be exactly"), (self.static(w=400, h=400), "one side must be exactly"),
                 (self.static(bytes=CFG.static_max_bytes + 1), "KB is over"), (self.static(alpha=False), "no transparency"),
                 (self.static(emoji=[]), "0 emoji"), (self.static(emoji=["x"] * (CFG.tg_emoji_max + 1)), "emoji"),
                 (self.video(bytes=CFG.video_max_bytes + 1), "KB is over"), (self.video(info=info(codec="h264")), "VP9 is required"),
                 (self.video(info=info(fps=60.0)), "fps"), (self.video(info=info(duration=4.0)), "max"), (self.video(info=info(audio=True)), "audio")]
        for inp, word in cases:
            c = run("telegram", "telegram_sticker", inp)
            self.assertFalse(c.ok, word)
            self.assertEqual(c.severity, verify.BLOCK)
            self.assertTrue(any(word in p for p in c.detail["problems"]), (word, c.detail["problems"]))

    def test_telegram_stroke_is_a_warning_for_static_stickers_only(self):
        ok = run("telegram", "telegram_stroke", self.static(stroke=True))
        self.assertTrue(ok.ok)
        bad = run("telegram", "telegram_stroke", self.static(stroke=False))
        self.assertFalse(bad.ok)
        self.assertEqual(bad.severity, verify.WARN)                                                  # a warning never blocks (a human decides)
        self.assertIsNone(run("telegram", "telegram_stroke", self.video(stroke=False)))              # not applicable to video
        self.assertIsNone(run("telegram", "telegram_stroke", self.static(stroke=None)))              # not measured: not applicable


class NeverRaises(unittest.TestCase):
    """`gates.py` calls `verify.run` directly for the pack and video_sheet stages: a malformed input there must be a recorded BLOCK, never a traceback out of the gate."""

    def test_a_malformed_pack_is_a_block_with_a_reason(self):
        for stickers in ([{"emoji": "x", "kind": "animated", "bytes": 10}],              # no key
                         [{"key": "a", "kind": "animated"}],                              # no byte count
                         [None], "not a list", 5):
            out = verify.run("pack", {"stickers": stickers}, CFG)
            self.assertEqual(len(out), 1, stickers)
            self.assertFalse(out[0].ok)
            self.assertEqual((out[0].severity, out[0].reason), (verify.BLOCK, "verifier_error"))
        out = verify.run("pack", {}, CFG)                                                  # no `stickers` at all
        self.assertEqual((out[0].ok, out[0].reason), (False, "verifier_error"))

    def test_a_malformed_video_sheet_input_is_blocks_not_an_exception(self):
        out = verify.run("video_sheet", {"sheet": None, "layout": {}, "approved": []}, CFG)
        self.assertTrue(out)
        self.assertTrue(all(isinstance(c, verify.Check) for c in out))
        self.assertTrue(any((not c.ok) and c.severity == verify.BLOCK for c in out))
        self.assertTrue(all(c.reason == "verifier_error" for c in out if not c.ok))
        out = verify.run("video_sheet", {}, CFG)
        self.assertTrue(any((not c.ok) and c.reason == "verifier_error" for c in out))

    def test_an_unknown_stage_is_an_empty_report(self):
        self.assertEqual(verify.run("no_such_stage", {}, CFG), [])


class Ratchet(unittest.TestCase):
    def test_every_check_of_the_catalogue_is_named_by_some_test(self):
        """Not a proof that each has a FAIL fixture, but a check nobody mentions can no longer be added."""
        text = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in Path(__file__).parent.glob("test_*.py"))
        unnamed = []
        for stage, rows in verify.CATALOGUE.items():
            for cid, _sev, fn, _gate in rows:
                if not (re.search(rf"""['"]{re.escape(cid)}['"]""", text) or re.search(rf"\b{re.escape(fn.__name__)}\b", text)):
                    unnamed.append(f"{stage}.{cid}")
        self.assertEqual(unnamed, [], "a verifier check no test names: give it a PASS and a FAIL fixture")


if __name__ == "__main__":
    unittest.main()
