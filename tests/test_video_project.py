import base64
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from stickerloom.media import matte
from stickerloom.engine import ffmpeg as ff
from stickerloom.engine.config import EngineConfig
from stickerloom.media.library import Library, LibraryError
from stickerloom.media.video_project import Projects, decode_overlays

W, H, N, FPS = 240, 160, 60, 30            # 2 s, landscape


def make_clip(path: Path, bg=(0, 200, 0), noisy=False):
    frames = []
    rng = np.random.default_rng(1)
    for t in range(N):
        f = np.zeros((H, W, 3), np.uint8); f[:] = bg
        if noisy:
            f = np.clip(f.astype(int) + rng.integers(-60, 60, f.shape), 0, 255).astype(np.uint8)
        cv2.circle(f, (40 + t * 2, 80), 26, (250, 200, 20), -1, cv2.LINE_AA)
        frames.append(f)
    if path.suffix == ".gif":
        ims = [Image.fromarray(f) for f in frames[::3]]
        ims[0].save(path, save_all=True, append_images=ims[1:], duration=100, loop=0)
        return
    codec = "libx264" if "libx264" in subprocess.run([ff.ffmpeg_exe(), "-hide_banner", "-encoders"], capture_output=True).stdout.decode() else "mpeg4"
    p = subprocess.Popen([ff.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                          "-c:v", codec, "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    p.stdin.write(np.stack(frames).tobytes()); p.stdin.close(); p.wait()


def overlay_png(color=(255, 0, 0, 255)):
    a = np.zeros((512, 512, 4), np.uint8); a[400:480, 20:200] = color
    ok, b = cv2.imencode(".png", cv2.cvtColor(a, cv2.COLOR_RGBA2BGRA)); return b.tobytes()


class VideoProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp()); cls.cfg = EngineConfig()
        cls.pr = Projects(cls.tmp / "out", cls.cfg)
        make_clip(cls.tmp / "green.mp4"); make_clip(cls.tmp / "noisy.mp4", bg=(90, 90, 110), noisy=True); make_clip(cls.tmp / "a.gif")
        cls.green = cls.pr.create((cls.tmp / "green.mp4").read_bytes(), "green.mp4")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_import_video_and_gif(self):
        p = self.green
        self.assertEqual((p["source"]["kind"], p["source"]["frameExt"]), ("video", "jpg"))
        self.assertAlmostEqual(p["source"]["duration"], 2.0, delta=0.15); self.assertAlmostEqual(p["source"]["aspect"], W / H, places=1)
        self.assertGreaterEqual(p["source"]["frames"], 20)
        fr = cv2.imread(str(self.pr.frame_path(p["id"], 0))); self.assertEqual(max(fr.shape[:2]), 360)
        g = self.pr.create((self.tmp / "a.gif").read_bytes(), "a.gif")
        self.assertEqual((g["source"]["kind"], g["source"]["frameExt"]), ("gif", "png"))
        self.assertTrue(self.pr.frame_path(g["id"], 0).is_file())
        with self.assertRaises(LibraryError):
            self.pr.create(b"not a video", "x.mp4")
        with self.assertRaises(LibraryError):
            self.pr.create(b"x", "x.txt")
        self.assertEqual(len(self.pr.list()), 2)              # failed imports leave nothing behind
        self.pr.delete(g["id"]); self.assertEqual(len(self.pr.list()), 1)

    def test_bg_removal_capability(self):
        self.assertEqual((self.green["videoBackgroundRemoval"]["status"], self.green["videoBackgroundRemoval"]["chroma"]), ("READY", "green"))
        n = self.pr.create((self.tmp / "noisy.mp4").read_bytes(), "noisy.mp4")
        rm = n["videoBackgroundRemoval"]
        if matte.status(True)["ok"]:                          # a normal background is READY through the AI matte provider
            self.assertEqual((rm["status"], rm["provider"]), ("READY", "matte"))
            n = self.pr.update(n["id"], {"videoBackgroundRemoval": {"enabled": True}, "video": {"trimStartMs": 0, "trimEndMs": 400, "fps": 10}})
            frames, _, info = self.pr.compose(n["id"], {}, "webp")
            self.assertEqual(info["keyed"], "matte"); self.assertEqual(frames.shape[-1], 4)
            m = self.pr.mask_path(n["id"], 0); self.assertTrue(m.is_file())
            self.assertEqual(cv2.imread(str(m), cv2.IMREAD_UNCHANGED).shape[2], 4)
        else:
            self.assertEqual(rm["status"], "UNAVAILABLE"); self.assertIn("AI matte", rm["reason"])
            with self.assertRaises(LibraryError):             # cannot be switched on when unavailable
                self.pr.update(n["id"], {"videoBackgroundRemoval": {"enabled": True}})
        self.pr.delete(n["id"])

    def test_autosave_clamps_and_keeps(self):
        pid = self.green["id"]
        layer = {"id": "t1", "type": "text", "transform": {"x": 9999, "y": 100, "scale": 2, "rotation": 10}, "opacity": 5,
                 "timing": {"startMs": 900, "endMs": 100}, "payload": {"text": "SUN"}}
        p = self.pr.update(pid, {"video": {"trimStartMs": 500, "trimEndMs": 520, "fps": 99}, "layers": [layer], "gif": {"enabled": True, "quality": 4}})
        self.assertGreaterEqual(p["video"]["trimEndMs"] - p["video"]["trimStartMs"], 100); self.assertEqual(p["video"]["fps"], 30)
        L = p["layers"][0]
        self.assertEqual((L["transform"]["x"], L["opacity"], L["timing"]), (768, 1, {"startMs": 100, "endMs": 900})); self.assertEqual(p["gif"]["quality"], 16)
        self.assertEqual(self.pr.get(pid)["layers"][0]["payload"]["text"], "SUN")
        with self.assertRaises(LibraryError):
            self.pr.update(pid, {"layers": [{"type": "video"}]})
        self.pr.update(pid, {"video": {"trimStartMs": 0, "trimEndMs": 1000, "fps": 12}, "layers": [], "gif": {"enabled": False, "quality": 256}})

    def test_compose_layer_timing_fit_and_key(self):
        pid = self.green["id"]
        self.pr.update(pid, {"video": {"trimStartMs": 0, "trimEndMs": 1000, "fps": 10}, "canvas": {"fit": "contain"},
                             "layers": [{"id": "t1", "type": "text", "timing": {"startMs": 300, "endMs": 599}, "payload": {}}]})
        frames, fps, info = self.pr.compose(pid, {"t1": overlay_png()}, "webp")
        self.assertEqual((frames.shape[1:], fps, info["frames"]), ((512, 512, 4), 10.0, 10))
        has = [bool((f[440, 100] == (255, 0, 0, 255)).all()) for f in frames]
        self.assertEqual(has, [False] * 3 + [True] * 3 + [False] * 4)          # frames at 0.3, 0.4, 0.5 s
        self.assertEqual(frames[0][5, 256, 3], 0)                              # landscape 'contain' leaves transparent bars
        self.assertEqual(frames[0][256, 256, 3], 255)
        self.pr.update(pid, {"canvas": {"fit": "cover"}})
        cov, _, _ = self.pr.compose(pid, {}, "webp"); self.assertTrue((cov[..., 3] == 255).all())
        self.pr.update(pid, {"canvas": {"fit": "contain"}, "videoBackgroundRemoval": {"enabled": True}})
        keyed, _, info = self.pr.compose(pid, {}, "webp")
        self.assertEqual(info["keyed"], "green"); y = 5 + (512 - int(512 * H / W)) // 2 + 4
        self.assertEqual(keyed[0][y, 256, 3], 0)                               # the green screen is gone
        self.assertGreater(int(keyed[0, :, :, 3].astype(bool).sum()), 1000)   # the subject stays
        self.pr.update(pid, {"videoBackgroundRemoval": {"enabled": False}, "layers": []})

    def test_render_formats_and_limits(self):
        pid = self.green["id"]
        self.pr.update(pid, {"video": {"trimStartMs": 0, "trimEndMs": 2000, "fps": 12},
                             "layers": [{"id": "t1", "type": "text", "payload": {}}], "gif": {"enabled": True, "loop": True, "quality": 64}})
        data, mime, ext, info = self.pr.render(pid, "webp", {"t1": overlay_png()})
        self.assertEqual((data[:4], data[8:12]), (b"RIFF", b"WEBP")); self.assertLessEqual(len(data), 500 * 1024)
        self.assertFalse(info["clipped"]); self.assertEqual(info["frames"], 24)
        from dataclasses import replace
        short = Projects(self.tmp / "out", replace(self.cfg, video_max_seconds=1.0))       # sticker cap applies to webm/webp, not gif
        _, _, ci = short.compose(pid, {}, "webp"); self.assertTrue(ci["clipped"]); self.assertEqual(ci["frames"], 12)
        self.assertFalse(short.compose(pid, {}, "gif")[2]["clipped"])
        data, mime, ext, info = self.pr.render(pid, "gif", {"t1": overlay_png()})
        self.assertEqual(data[:6], b"GIF89a"); self.assertEqual(info["frames"], 24)   # gif keeps up to 10 s
        with self.assertRaises(LibraryError):
            self.pr.render(pid, "webp", {"t1": b"not a png"})
        if ff._has_vp9(ff.ffmpeg_exe()):
            data, mime, ext, info = self.pr.render(pid, "webm", {"t1": overlay_png()})
            self.assertEqual(mime, "video/webm"); self.assertLessEqual(len(data), 256 * 1024)
        self.assertEqual(self.pr.get(pid)["source"]["file"], "source.mp4")               # source and project untouched

    def test_overlays_decoding(self):
        b = base64.b64encode(overlay_png()).decode()
        self.assertEqual(list(decode_overlays({"a": "data:image/png;base64," + b})), ["a"])
        with self.assertRaises(LibraryError):
            decode_overlays({"a": "nope"})


if __name__ == "__main__":
    unittest.main()
