"""Handpicked Lottie files: scan, origin -> morph
assign, library storage, zip + manifest. Pure flow calls on a temporary out/;
no provider, no server, no media judged by eye."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from stickerloom.flow import emoji_reactions as er
from stickerloom.flow.particle_sets import SetError

TINY = {"v": "5.7.4", "fr": 30, "ip": 0, "op": 30, "w": 512, "h": 512,
        "nm": "t", "ddd": 0, "assets": [], "layers": [{"ddd": 0, "ind": 1, "ty": 4}]}


class HandpickedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.lot = self.tmp / "lottie"
        self.lot.mkdir()
        (self.lot / "Sad Emoji.json").write_text(json.dumps({**TINY, "nm": "sad"}), encoding="utf-8")
        (self.lot / "Star Strike Emoji.json").write_text(json.dumps(TINY), encoding="utf-8")
        (self.lot / "notes.txt").write_text("not a lottie", encoding="utf-8")
        (self.lot / "broken.json").write_text("{nope", encoding="utf-8")

    def test_scan_lists_only_valid_lotties_with_metadata(self):
        files = er.handpicked_files(self.lot)
        self.assertEqual([f["file"] for f in files], ["Sad Emoji.json", "Star Strike Emoji.json"])
        sad = files[0]
        self.assertEqual((sad["w"], sad["h"], sad["frames"], sad["layers"]), (512, 512, 30, 1))
        self.assertEqual((sad["images"], sad["base64"]), (0, False))
        self.assertEqual(er.handpicked_files(self.tmp / "missing"), [])

    def test_assign_clear_and_validation(self):
        r = er.assign_handpicked(self.out, ["happy", "kiss"], "Sad Emoji.json", "Star Strike Emoji.json",
                                 lottie_dir=self.lot)
        self.assertEqual(r, {"assigned": 2, "cleared": 0})
        lib = er.library(self.out)
        self.assertEqual(lib["handpicked"],
                         {"happy": {"origin": "Sad Emoji.json", "morph": "Star Strike Emoji.json"},
                          "kiss": {"origin": "Sad Emoji.json", "morph": "Star Strike Emoji.json"}})
        r = er.assign_handpicked(self.out, ["kiss"], "", lottie_dir=self.lot)
        self.assertEqual(r, {"assigned": 0, "cleared": 1})
        self.assertEqual(list(er.library(self.out)["handpicked"]), ["happy"])
        with self.assertRaises(SetError):
            er.assign_handpicked(self.out, [], "Sad Emoji.json", lottie_dir=self.lot)
        with self.assertRaises(SetError):
            er.assign_handpicked(self.out, ["happy"], "Nope.json", lottie_dir=self.lot)
        with self.assertRaises(SetError):
            er.assign_handpicked(self.out, ["happy"], "Sad Emoji.json", "Nope.json", lottie_dir=self.lot)
        with self.assertRaises(SetError):
            er.assign_handpicked(self.out, ["not-a-bank-token-xyz"], "Sad Emoji.json", lottie_dir=self.lot)

    def test_blob_serves_bytes_and_refuses_escapes(self):
        blob = er.handpicked_blob("Sad Emoji.json", lottie_dir=self.lot)
        self.assertEqual(json.loads(blob)["nm"], "sad")
        for bad in ("../x.json", "a/b.json", "", "missing.json"):
            with self.assertRaises(SetError):
                er.handpicked_blob(bad, lottie_dir=self.lot)

    def test_zip_carries_used_files_and_manifest_map(self):
        er.assign_handpicked(self.out, ["happy"], "Sad Emoji.json", "Star Strike Emoji.json", lottie_dir=self.lot)
        with mock.patch.object(er, "handpicked_dir", return_value=self.lot):
            data = er.lib_zip(self.out)
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        self.assertIn("handpicked/Sad Emoji.json", names)
        self.assertIn("handpicked/Star Strike Emoji.json", names)
        m = json.loads(z.read("manifest.json"))
        self.assertEqual(m["handpicked"], {"happy": {"origin": "Sad Emoji.json", "morph": "Star Strike Emoji.json"}})
        self.assertTrue(any(p["id"] == "burst_01" for p in m["presets"]), "old manifest keys untouched")


if __name__ == "__main__":
    unittest.main()
