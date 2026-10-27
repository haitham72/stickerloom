"""Replace with an edited file: the batch keeps its S#, the pack keeps its id, nothing is deleted. No network."""
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from stickerloom.flow import pipeline as pl
from stickerloom.flow import sources
from stickerloom.media.library import Library, LibraryError, validate_replace
from stickerloom.engine.config import EngineConfig
from tests import synth


def flip(png: bytes) -> bytes:
    rgba = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    rgba = cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(cv2.flip(rgba, 1), cv2.COLOR_RGBA2BGRA))
    assert ok
    return buf.tobytes()


class BatchReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.inp = root / "out", root / "inputs"
        sheet = self.inp / "Images_gen" / "img-001-teddy_bear"
        sheet.mkdir(parents=True)
        cv2.imwrite(str(sheet / "sheet.png"), cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))
        self.cfg = EngineConfig(min_sheet_px=256)
        self.gid = pl.start("teddy bear", self.out, self.inp)
        pl.run_stills(self.out, self.gid, self.cfg)
        st = pl.read_result(self.out, self.gid)["stickers"][0]
        self.assertEqual(st["status"], "READY")
        self.png = (pl.gen_dir(self.out, self.gid) / st["png"]).read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def test_replace_keeps_number_and_returns_to_approval(self):
        r = pl.replace_still(self.out, self.gid, 1, flip(self.png), self.cfg)
        self.assertEqual((r["index"], r["anim_from_previous"]), (1, False))
        res = pl.read_result(self.out, self.gid)
        st = res["stickers"][0]
        self.assertEqual((st["status"], st["review"]["still"]), ("READY", "PENDING"))
        self.assertEqual(st["history"][-1]["decision"], "EDIT")
        self.assertTrue((pl.gen_dir(self.out, self.gid) / "source" / "orig" / "replaced-S1.png").is_file())

    def test_replace_marks_kept_animation(self):
        res = pl.read_result(self.out, self.gid)
        fake = pl.gen_dir(self.out, self.gid) / "slices" / "fake.webm"
        fake.write_bytes(b"webm")
        res["stickers"][0].update(webm="slices/fake.webm", anim_status="READY")
        pl.write_result(self.out, self.gid, res)
        r = pl.replace_still(self.out, self.gid, 1, flip(self.png), self.cfg)
        self.assertTrue(r["anim_from_previous"])
        res = pl.read_result(self.out, self.gid)
        self.assertEqual((res["stickers"][0]["webm"], res["stickers"][0]["anim_status"]), ("slices/fake.webm", "READY"))
        self.assertTrue(fake.is_file())                                  # the animation file is untouched

    def test_reanimate_clears_the_flag(self):
        from unittest.mock import patch
        from stickerloom.engine.video import AnimationResult
        res = pl.read_result(self.out, self.gid)
        res["source"]["has_video"] = True                                   # pretend a video was prepared: the cut itself is faked below
        res["stickers"][0].update(webm="slices/fake.webm", anim_status="READY", anim_from_previous=True)
        pl.write_result(self.out, self.gid, res)

        def fake_cells(res_, cfg, cells, on_cell=None, cache=None, waive=None):
            out = [AnimationResult(1, "READY", "prepared video")]
            for r in out:
                on_cell and on_cell(r)
            return out

        with patch.object(pl, "animate_cells", side_effect=fake_cells):
            pl.run_animate(self.out, self.gid, self.cfg, "slice", 1)
        st = pl.read_result(self.out, self.gid)["stickers"][0]
        self.assertNotIn("anim_from_previous", st)

    def test_replace_keeps_an_override_and_a_block(self):
        res = pl.read_result(self.out, self.gid)
        res["stickers"][0].update(still_override=["inside_cell"], review={"still": "BLOCKED", "anim": "NONE"})
        pl.write_result(self.out, self.gid, res)
        pl.replace_still(self.out, self.gid, 1, flip(self.png), self.cfg)
        st = pl.read_result(self.out, self.gid)["stickers"][0]
        self.assertEqual((st["still_override"], st["review"]["still"]), (["inside_cell"], "BLOCKED"))

    def test_replace_refuses_a_bad_file_and_changes_nothing(self):
        with self.assertRaises(pl.PipelineError):
            pl.replace_still(self.out, self.gid, 1, b"not a picture", self.cfg)
        st = pl.read_result(self.out, self.gid)["stickers"][0]
        self.assertNotIn("EDIT", [h["decision"] for h in st["history"]])
        self.assertFalse((pl.gen_dir(self.out, self.gid) / "source" / "orig" / "replaced-S1.png").exists())
        self.assertEqual((pl.gen_dir(self.out, self.gid) / st["png"]).read_bytes(), self.png)

    def test_undo_restores_the_previous_file(self):
        res = pl.read_result(self.out, self.gid)
        (pl.gen_dir(self.out, self.gid) / "slices" / "fake.webm").write_bytes(b"webm")
        res["stickers"][0].update(webm="slices/fake.webm", anim_status="READY")
        pl.write_result(self.out, self.gid, res)
        pl.replace_still(self.out, self.gid, 1, flip(self.png), self.cfg)
        self.assertTrue(pl.read_result(self.out, self.gid)["stickers"][0]["anim_from_previous"])
        r = pl.replace_still(self.out, self.gid, 1, b"", self.cfg, undo=True)
        self.assertTrue(r["undone"])
        st = pl.read_result(self.out, self.gid)["stickers"][0]
        self.assertEqual((pl.gen_dir(self.out, self.gid) / st["png"]).read_bytes(), self.png)
        self.assertNotIn("anim_from_previous", st)                     # the picture is the original again
        with self.assertRaises(pl.PipelineError):
            pl.replace_still(self.out, self.gid, 1, b"", self.cfg, undo=True)   # one-shot


class PackReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.inp = root / "out", root / "inputs"
        sheet = self.inp / "Images_gen" / "img-001-teddy_bear"
        sheet.mkdir(parents=True)
        cv2.imwrite(str(sheet / "sheet.png"), cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))
        self.cfg = EngineConfig(min_sheet_px=256)
        self.gid = pl.start("teddy bear", self.out, self.inp)
        pl.run_stills(self.out, self.gid, self.cfg)
        self.png = (pl.gen_dir(self.out, self.gid) / pl.read_result(self.out, self.gid)["stickers"][0]["png"]).read_bytes()
        self.lib = Library(self.out)
        self.pack = self.lib.create_pack("Pack", owner="local")["id"]
        self.st = self.lib.add_render(self.pack, self.png, "Bear", "🐻", self.cfg, {"generation": "G001", "index": 1})

    def tearDown(self):
        self.tmp.cleanup()

    def test_replace_keeps_id_and_undoes(self):
        s = self.lib.replace_file(self.pack, self.st["id"], flip(self.png), "png", keep_prev=True)
        self.assertEqual((s["id"], s["name"], s["emoji"]), (self.st["id"], "Bear", "🐻"))
        self.assertIn("prev_file", s)
        back = self.lib.undo_replace(self.pack, self.st["id"])
        self.assertNotIn("prev_file", back)
        self.assertEqual((self.lib.files / back["file"]).read_bytes(), self.png)

    def test_copies_in_other_packs_are_offered(self):
        other = self.lib.create_pack("Other", owner="local")["id"]
        o = self.lib.add_render(other, self.png, "Bear", "🐻", self.cfg, {"generation": "G001", "index": 1})
        rows = self.lib.copies_of(self.pack, self.st["id"])
        self.assertEqual([(r["pack_id"], r["id"]) for r in rows], [(other, o["id"])])
        self.lib.replace_file(other, o["id"], flip(self.png), "png", keep_prev=True)
        mine, theirs = self.lib.sticker_path(self.pack, self.st["id"])[0], self.lib.sticker_path(other, o["id"])[0]
        self.assertEqual(mine.read_bytes(), self.png)
        self.assertEqual(theirs.read_bytes(), flip(self.png))

    def test_validate_replace_refuses(self):
        with self.assertRaises(LibraryError):
            validate_replace(b"junk", "bmp", self.cfg)
        with self.assertRaises(LibraryError):
            validate_replace(b"junk", "webm", self.cfg)
        body, ext, kind = validate_replace(self.png, "png", self.cfg)
        self.assertEqual((ext, kind), ("png", "static"))


if __name__ == "__main__":
    unittest.main()
