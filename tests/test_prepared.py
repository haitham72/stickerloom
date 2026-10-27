"""Prefer-prepared: the signature rule, the setting, and the batch marking. Isolated out/inp; no paid calls."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2

from stickerloom.flow import pipeline as pl
from stickerloom.flow import sources
from stickerloom.engine.config import EngineConfig
from tests import synth

# The signature rule (docs/generation.md, "Prepared instead of paid"): a request is served from the watch folder
# when one whole word of a prepared subject's folder name, singularised and ignoring generic, appears as a whole
# word of the request.
PHRASES = [
    ("teddy bear for school", "teddy_bear"),
    ("teddy", "teddy_bear"),
    ("bear in a teddy costume", "teddy_bear"),
    ("bear", "teddy_bear"),
    ("emoji keyboard", "generic_emojis"),
    ("generic emojis laughing", "generic_emojis"),
    ("emojis", "generic_emojis"),
    ("emoji", "generic_emojis"),
    ("a dragon dancing", None),
    ("school stickers", None),
]


def make_watch(root: Path) -> Path:
    inp = root / "inputs"
    for kind, nnn, subject in (("img", "001", "teddy_bear"), ("vid", "001", "teddy_bear"),
                               ("img", "005", "generic_emojis"), ("vid", "005", "generic_emojis")):
        d = inp / ("Images_gen" if kind == "img" else "videos_gen") / f"{kind}-{nnn}-{subject}"
        d.mkdir(parents=True)
        (d / f"sheet{nnn}.png").write_bytes(b"not opened by the scanner")
    return inp


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inp = make_watch(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_ten_phrases(self):
        for phrase, want in PHRASES:
            with self.subTest(phrase=phrase):
                self.assertEqual(sources.match_subject(self.inp, phrase), want)

    def test_find_takes_variant_one_by_default(self):
        pick = sources.find(self.inp, "teddy bear for school")
        self.assertEqual((pick.subject, pick.variant), ("teddy_bear", 1))

    def test_no_match_without_a_signature_word(self):
        self.assertIsNone(sources.find(self.inp, "a dragon dancing"))


class PreferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_on(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKERLOOM_PREFER_PREPARED", None)
            self.assertTrue(sources.prefer_prepared(self.out))

    def test_env_off(self):
        with patch.dict(os.environ, {"STICKERLOOM_PREFER_PREPARED": "0"}):
            self.assertFalse(sources.prefer_prepared(self.out))

    def test_file_switch_and_env_wins(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKERLOOM_PREFER_PREPARED", None)
            self.assertFalse(sources.set_prefer_prepared(self.out, False))
            self.assertFalse(sources.prefer_prepared(self.out))
            self.assertEqual(json.loads((self.out / "prepared.json").read_text()), {"prefer": False})
        with patch.dict(os.environ, {"STICKERLOOM_PREFER_PREPARED": "yes"}):
            self.assertTrue(sources.prefer_prepared(self.out))


class MarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.inp = root / "out", root / "inputs"
        sheet = self.inp / "Images_gen" / "img-001-teddy_bear"
        sheet.mkdir(parents=True)
        cv2.imwrite(str(sheet / "sheet.png"), cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))

    def tearDown(self):
        self.tmp.cleanup()

    def test_watch_folder_batch_is_marked_prepared(self):
        gid = pl.start("teddy bear for school", self.out, self.inp)
        res = pl.read_result(self.out, gid)
        self.assertTrue(res["source"]["prepared"])
        self.assertIsNone(res.get("task_id"))

    def test_import_style_pick_is_not_marked_prepared(self):
        f = self.out / "imports" / "sheet.png"
        f.parent.mkdir(parents=True)
        f.write_bytes((self.inp / "Images_gen" / "img-001-teddy_bear" / "sheet.png").read_bytes())
        pick = sources.Pick(subject="import", subject_id="import", variant=1, n_variants=1, sheet=f, video=None)
        gid = pl.start("imported sheet", self.out, self.inp, pick=pick)
        self.assertFalse(pl.read_result(self.out, gid)["source"]["prepared"])


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.inp = root / "out", root / "inputs"
        sheet = self.inp / "Images_gen" / "img-001-teddy_bear"
        sheet.mkdir(parents=True)
        cv2.imwrite(str(sheet / "sheet.png"), cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))
        self.c = SimpleNamespace(out=self.out, inp=self.inp, lock=threading.Lock(), cfg=EngineConfig(min_sheet_px=256),
                                 pace=0, submit=lambda fn: fn())
        from stickerloom.agent.tools import ConsoleTools
        self.tools = ConsoleTools(self.c)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_serves_prepared_for_free(self):
        with patch("stickerloom.generation.higgsfield.available", return_value=True), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKERLOOM_PREFER_PREPARED", None)
            r = self.tools.create("teddy bear for school")
        self.assertEqual((r["estimate"], r["live"]), (0, False))
        self.assertTrue(r["prepared"])
        res = pl.read_result(self.out, 1)
        self.assertTrue(res["source"]["prepared"])
        self.assertIsNone(res.get("task_id"))

    def test_create_force_live_skips_prepared(self):
        seen = {}

        def live(what, body, **kw):
            seen["what"] = what
            return {"job": "J001", "task": "001", "estimate": 2.0}

        self.c.live = live
        with patch("stickerloom.generation.higgsfield.available", return_value=True), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKERLOOM_PREFER_PREPARED", None)
            r = self.tools.create("teddy bear for school", force_live=True)
        self.assertEqual((r["job"], r["live"]), ("J001", True))
        self.assertNotIn("prepared", r)

    def test_create_honours_prefer_off(self):
        seen = {}

        def live(what, body, **kw):
            seen["what"] = what
            return {"job": "J001", "task": "001", "estimate": 2.0}

        self.c.live = live
        with patch("stickerloom.generation.higgsfield.available", return_value=True), \
                patch.dict(os.environ, {"STICKERLOOM_PREFER_PREPARED": "0"}):
            r = self.tools.create("teddy bear for school")
        self.assertEqual(seen.get("what"), "sheet")
        self.assertTrue(r["live"])


if __name__ == "__main__":
    unittest.main()
