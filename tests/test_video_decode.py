"""The green-screen video decode keeps the subject's edge smooth (engine/ffmpeg.decode_cell, SWS_CHROMA): a provider's H.264 stores colour at half resolution,
and copying each colour sample to 2x2 pixels made the key cut the edge in 2-pixel steps (the pixelated outline of 2026-10-09). Uses the local ffmpeg; no network."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from stickerloom.engine import ffmpeg as ff
from stickerloom.engine.chroma import alpha_from_diff, calibrate, key_diff

S, SS = 240, 8


def scene(col):
    big = np.zeros((S * SS, S * SS, 3), np.uint8)
    big[:] = (40, 200, 60)
    pts = np.array([[S * SS * 0.2, S * SS * 0.8], [S * SS * 0.5, S * SS * 0.15], [S * SS * 0.85, S * SS * 0.7]], np.int32)
    mask = np.zeros(big.shape[:2], np.float32)
    for img, v in ((big, col), (mask, 1)):
        cv2.fillPoly(img, [pts], v)
        cv2.circle(img, (int(S * SS * .5), int(S * SS * .6)), int(S * SS * .22), v, -1)
    return cv2.resize(big, (S, S), interpolation=cv2.INTER_AREA), cv2.resize(mask, (S, S), interpolation=cv2.INTER_AREA)


class DecodeEdgeTests(unittest.TestCase):
    def test_the_keyed_edge_of_an_h264_cell_follows_the_true_outline(self):
        td = Path(tempfile.mkdtemp())
        errs = []
        for col in [(230, 120, 40), (240, 220, 60), (120, 60, 30)]:
            rgb, truth = scene(col)
            src, mp4 = td / "f.png", td / "v.mp4"
            cv2.imwrite(str(src), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            subprocess.run([ff.ffmpeg_exe(), "-y", "-loglevel", "error", "-loop", "1", "-i", str(src), "-t", "0.2", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-crf", "18", str(mp4)], check=True)
            f = ff.decode_cell(mp4, 0, 0, S, S, 1, None)[0]
            _, t = calibrate(f, "green", 4)
            a = alpha_from_diff(key_diff(f, "green"), t)
            band = cv2.dilate(((truth > 0.01) & (truth < 0.99)).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            errs.append(float(np.abs(a - truth)[band].mean()))
        self.assertLess(max(errs), 0.06, f"edge error per colour {errs} (the copied-colour decode measured 0.075-0.078)")

    def test_a_cell_off_the_even_grid_is_cut_exactly(self):
        td = Path(tempfile.mkdtemp())
        img = np.zeros((64, 64, 3), np.uint8)
        img[:] = (40, 200, 60)
        img[21:31, 13:23] = (250, 250, 250)                  # a white square at odd coordinates
        cv2.imwrite(str(td / "f.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        subprocess.run([ff.ffmpeg_exe(), "-y", "-loglevel", "error", "-loop", "1", "-i", str(td / "f.png"), "-t", "0.2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-crf", "10", str(td / "v.mp4")], check=True)
        f = ff.decode_cell(td / "v.mp4", 13, 21, 10, 10, 1, None)[0]
        self.assertGreater(float(f.mean()), 200, "the crop lands on the square, not a pixel beside it")


if __name__ == "__main__":
    unittest.main()
