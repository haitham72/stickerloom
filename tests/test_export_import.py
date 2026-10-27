"""WP9 pack exchange: export a pack to a dir, import it into a fresh out/ dir.

The import verifies before writing anything, and the imported sticker ids
differ from the exported ones. The Telegram target is mocked (no token, no
network, nothing sent, nothing recorded).
"""
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from stickerloom.engine import ffmpeg as ff
from stickerloom.media.library import Library
from stickerloom.media import pack_exchange as px
from tests import synth


def png_bytes(seed=0):
    rgba = synth.sticker_rgba("disc", size=512)
    rgba[..., 0] = (rgba[..., 0].astype(int) + seed * 17) % 255
    b = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(b, "PNG")
    return b.getvalue()


def webm_bytes():
    frames = np.zeros((12, 512, 512, 4), np.uint8)
    for i in range(12):
        frames[i] = synth.sticker_rgba("disc")
        frames[i][..., 0] = (i * 10) % 255
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.webm"
        ff.encode_webm(frames, 24, 38, p)
        return p.read_bytes()


def make_pack(out, title="Round Trip"):
    lib = Library(out)
    pid = lib.create_pack(title)["id"]
    lib.add_bytes(pid, png_bytes(0), "png", "Happy Sun", "static", "😀")
    lib.add_bytes(pid, png_bytes(1), "png", "Calm Moon", "static", "🌙")
    lib.add_bytes(pid, webm_bytes(), "webm", "Dancing Star", "animated", "⭐")
    return lib, pid


class ExportImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out1 = self.tmp / "out1"
        self.lib, self.pid = make_pack(self.out1)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def exported_ids(self):
        return [s["id"] for s in self.lib.snapshot()["packs"][0]["stickers"]]

    def test_export_writes_pngs_webm_and_manifest(self):
        rep = px.export_pack(self.out1, self.pid, self.tmp / "exp")
        root = Path(rep["dir"])
        self.assertEqual(rep["stickers"], 3)
        self.assertEqual(rep["static"], 2)
        self.assertEqual(rep["video"], 1)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(len(manifest["assets"]), 3)
        for a in manifest["assets"]:
            f = root / a["filename"]
            self.assertTrue(f.is_file(), a["filename"])
            self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(), a["sha256"])
            if a["media"] == "static":
                self.assertTrue(a["filename"].endswith(".png"))
                rgba = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGBA"))
                self.assertEqual(rgba.shape[:2], (512, 512))
            else:
                self.assertTrue(a["filename"].endswith(".webm"))

    def test_round_trip_into_fresh_out_verifies_and_remaps_ids(self):
        rep = px.export_pack(self.out1, self.pid, self.tmp / "exp")
        old_ids = {a["sticker_id"] for a in rep["assets"]}
        self.assertEqual(set(self.exported_ids()), old_ids)
        out2 = self.tmp / "fresh"
        got = px.import_pack(out2, Path(rep["dir"]))
        self.assertEqual(got["added"], 3)
        lib2 = Library(out2)
        packs = lib2.snapshot()["packs"]
        self.assertEqual(len(packs), 1)
        new_ids = [s["id"] for s in packs[0]["stickers"]]
        self.assertEqual(len(got["id_map"]), 3)
        self.assertEqual(set(got["id_map"]), old_ids)                       # every exported id remapped
        self.assertEqual(set(got["id_map"].values()), set(new_ids))
        self.assertFalse(set(new_ids) & old_ids, "imported ids must differ from exported ones")
        by_name = {s["name"]: s for s in packs[0]["stickers"]}
        self.assertEqual(by_name["Happy Sun"]["emoji"], "😀")
        self.assertEqual(by_name["Dancing Star"]["type"], "animated")
        old_files = {a["name"]: (Path(rep["dir"]) / a["filename"]).read_bytes() for a in rep["assets"]}
        for s in packs[0]["stickers"]:
            self.assertEqual((lib2.files / s["file"]).read_bytes(), old_files[s["name"]])

    def test_import_from_zip(self):
        rep = px.export_pack(self.out1, self.pid, self.tmp / "exp")
        zp = self.tmp / "pack.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
            for f in Path(rep["dir"]).rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(rep["dir"]).as_posix())
        got = px.import_pack(self.tmp / "fresh", zp)
        self.assertEqual(got["added"], 3)
        self.assertFalse(set(self.exported_ids()) & set(got["id_map"].values()))

    def test_import_refuses_a_tampered_export_and_writes_nothing(self):
        rep = px.export_pack(self.out1, self.pid, self.tmp / "exp")
        first = next(a["filename"] for a in rep["assets"] if a["media"] == "static")
        f = Path(rep["dir"]) / first
        raw = bytearray(f.read_bytes())
        raw[-40] ^= 0xFF
        f.write_bytes(bytes(raw))
        out2 = self.tmp / "fresh"
        with self.assertRaises(px.PackExchangeError):
            px.import_pack(out2, Path(rep["dir"]))
        self.assertFalse((out2 / "library" / "library.json").exists(), "verify-before-write: nothing written")

    def test_import_refuses_a_missing_manifest(self):
        out2 = self.tmp / "fresh"
        with self.assertRaises(px.PackExchangeError):
            px.import_pack(out2, self.tmp)
        self.assertFalse((out2 / "library").exists())

    def test_telegram_target_is_mocked_with_no_token_and_no_network(self):
        env = {k: os.environ.pop(k, None) for k in ("STICKERLOOM_TELEGRAM_TOKEN", "STICKERLOOM_TELEGRAM_USER", "STICKERLOOM_TELEGRAM_API")}
        try:
            rep = px.export_telegram_mock(self.out1, self.pid)
        finally:
            for k, v in env.items():
                if v is not None:
                    os.environ[k] = v
        self.assertTrue(rep["mocked"])
        self.assertEqual(sum(s["stickers"] for s in rep["sets"]), 3)
        self.assertIn("nothing was sent", rep["note"])
        pack = self.lib.snapshot()["packs"][0]
        self.assertNotIn("exports", pack.get("telegram") or {}, "a mock must never feed the already-sent logic")

    def test_cli_export_and_import_round_trip(self):
        from stickerloom import cli as _cli
        old_out, old_in = os.environ.get("STICKERLOOM_OUT"), os.environ.get("STICKERLOOM_INPUT")
        try:
            os.environ["STICKERLOOM_OUT"] = str(self.out1)
            self.assertEqual(_cli.main(["export", self.pid, "--to", "dir", "--dest", str(self.tmp / "cli-exp")]), 0)
            self.assertEqual(_cli.main(["export", self.pid, "--to", "telegram"]), 0)
            kids = [d for d in (self.tmp / "cli-exp").iterdir() if d.is_dir()]
            self.assertEqual(len(kids), 1)
            os.environ["STICKERLOOM_OUT"] = str(self.tmp / "cli-fresh")
            self.assertEqual(_cli.main(["import", str(kids[0]), "--as", "CLI Copy"]), 0)
        finally:
            for k, v in (("STICKERLOOM_OUT", old_out), ("STICKERLOOM_INPUT", old_in)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        packs = Library(self.tmp / "cli-fresh").snapshot()["packs"]
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["name"], "CLI Copy")
        self.assertFalse(set(self.exported_ids()) & {s["id"] for s in packs[0]["stickers"]})


if __name__ == "__main__":
    unittest.main()
