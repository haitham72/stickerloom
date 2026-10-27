"""Phase 3C core: a photo -> cutout -> the Phase 1 edge finish -> a validated 512 sticker.
Synthetic green-screen photo, so the chroma path runs with no weights and no network."""
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


def green_photo(path: Path):
    img = np.full((400, 500, 3), (0, 200, 0), np.uint8)          # green screen
    cv2.rectangle(img, (150, 100), (350, 330), (30, 60, 220), -1)  # red "subject"
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


class PhotoTests(unittest.TestCase):
    def test_photo_to_sticker(self):
        from stickerloom.cli import photo_cmd
        from stickerloom.engine.config import EngineConfig
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "dog.jpg"
            green_photo(src)
            out = Path(td) / "out"
            args = type("A", (), {"file": str(src), "method": "auto", "outline": 12, "erode": 0})()
            self.assertEqual(photo_cmd(out, args, EngineConfig()), 0)
            files = sorted((out / "photo").glob("photo-dog.*"))
            self.assertEqual([f.suffix for f in files], [".json", ".png"])
            rgba = cv2.imdecode(np.fromfile(str(out / "photo" / "photo-dog.png"), np.uint8),
                                cv2.IMREAD_UNCHANGED)
            self.assertEqual(rgba.shape, (512, 512, 4))  # a real 512 sticker with alpha
            self.assertTrue((rgba[..., 3] == 0).any() and (rgba[..., 3] > 127).any())

    def test_bad_input_fails_cleanly(self):
        from stickerloom.cli import photo_cmd
        from stickerloom.engine.config import EngineConfig
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            args = type("A", (), {"file": str(Path(td) / "missing.jpg"), "method": "auto",
                                  "outline": 12, "erode": 0})()
            self.assertEqual(photo_cmd(out, args, EngineConfig()), 1)
            bad = Path(td) / "x.txt"
            bad.write_text("not an image", encoding="utf-8")
            args = type("A", (), {"file": str(bad), "method": "auto", "outline": 12, "erode": 0})()
            self.assertEqual(photo_cmd(out, args, EngineConfig()), 1)


if __name__ == "__main__":
    unittest.main()
