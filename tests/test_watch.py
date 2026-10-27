"""History = the real watch folders, with Remove (to a trash that can be restored)."""
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2

from stickerloom.flow import pipeline as pl
from stickerloom.flow import watch
from tests import synth
from tests.test_golden import Api


def make(root: Path):
    for n, subj, video in ((1, "blob", True), (2, "blob", False), (3, "other", True)):
        d = root / "Images_gen" / f"img-{n:03d}-{subj}"
        d.mkdir(parents=True)
        cv2.imwrite(str(d / "sheet.png"), cv2.cvtColor(synth.make_sheet(seed=n), cv2.COLOR_RGB2BGR))
        if video:
            v = root / "videos_gen" / f"vid-{n:03d}-{subj}"
            v.mkdir(parents=True)
            (v / "clip.mp4").write_bytes(b"x" * 2048)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.inp, self.out = self.tmp / "in", self.tmp / "out"
        make(self.inp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rows_pair_image_and_video(self):
        rows = {r["number"]: r for r in watch.list_rows(self.inp, self.out, {("blob", "001"): ["G001", "G004"]})}
        self.assertEqual(sorted(rows), ["001", "002", "003"])
        self.assertEqual((rows["001"]["img"]["name"], rows["001"]["vid"]["name"]), ("img-001-blob", "vid-001-blob"))
        self.assertIsNone(rows["002"]["vid"])                                    # a sheet without a video says so
        self.assertEqual(rows["001"]["generations"], ["G001", "G004"])
        self.assertEqual(rows["001"]["vid"]["bytes"], 2048)
        self.assertTrue(watch.thumb_path(self.inp, self.out, "img-001-blob").is_file())

    def test_remove_goes_to_the_trash_and_can_be_restored(self):
        meta = watch.remove(self.inp, self.out, "001", "blob")
        self.assertFalse((self.inp / "Images_gen" / "img-001-blob").exists())
        self.assertFalse((self.inp / "videos_gen" / "vid-001-blob").exists())
        self.assertEqual([i["name"] for i in meta["items"]], ["img-001-blob", "vid-001-blob"])
        self.assertTrue((self.inp / "Images_gen" / "img-002-blob").exists())    # nothing else is touched
        self.assertEqual(len(watch.list_trash(self.out)), 1)
        self.assertEqual(watch.list_trash(self.out)[0]["bytes"] > 2048, True)
        watch.restore(self.inp, self.out, meta["id"])
        self.assertTrue((self.inp / "Images_gen" / "img-001-blob" / "sheet.png").is_file())     # back under its own, final name
        self.assertTrue((self.inp / "videos_gen" / "vid-001-blob" / "clip.mp4").is_file())
        self.assertEqual(watch.list_trash(self.out), [])

    def test_purge_and_refusals(self):
        meta = watch.remove(self.inp, self.out, "003", "other")
        watch.purge(self.out, meta["id"])
        self.assertEqual(watch.list_trash(self.out), [])
        for bad in (("../x", "blob"), ("001", "../etc"), ("1", "blob"), ("001", "")):
            with self.assertRaises(watch.WatchError):
                watch.remove(self.inp, self.out, *bad)
        with self.assertRaises(watch.WatchError):
            watch.remove(self.inp, self.out, "009", "blob")                      # no such folder
        with self.assertRaises(watch.WatchError):
            watch.purge(self.out, "../../in")                                    # only ids of trash entries
        m = watch.remove(self.inp, self.out, "001", "blob")
        (self.inp / "Images_gen" / "img-001-blob").mkdir()                      # the name came back: restore must not overwrite
        with self.assertRaises(watch.WatchError) as e:
            watch.restore(self.inp, self.out, m["id"])
        self.assertEqual(e.exception.code, 409)
        self.assertEqual(len(watch.list_trash(self.out)), 1)


class WatchApiTests(Api):
    def test_history_api(self):
        s, j = self.req("GET", "/api/watch")
        self.assertEqual(s, 200)
        self.assertTrue(j["images"].endswith("Images_gen") and j["videos"].endswith("videos_gen"))
        r = next(x for x in j["rows"] if x["subject"] == "blob")
        self.assertTrue(r["thumb"])
        s, body = self.req("GET", r["thumb"])
        self.assertEqual((s, body[:2]), (200, b"\xff\xd8"))                      # a small JPEG, not the 2K sheet
        self.assertEqual(self.req("POST", "/api/watch/remove", {"number": "../x", "subject": "blob"})[0], 400)
        self.assertEqual(self.req("POST", "/api/watch/remove", {"number": "009", "subject": "nothing"})[0], 404)
        self.assertEqual(self.req("GET", "/api/watch/thumb/..%2Fx")[0], 400)


if __name__ == "__main__":
    unittest.main()
