"""WP6: the provider registry and the fake provider. Registry selection by
STICKERLOOM_PROVIDER, fake determinism (same spec, same sha256), the per-provider
model catalog, and one offline fulfil per kind through the real `jobs.fulfil`."""
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from stickerloom import providers
from stickerloom.generation import jobs, model_catalog
from stickerloom.providers.base import ProviderError, ProviderJob


def sheet_job(prompt="One illustration of 4 separate characters arranged in 2 rows of 2."):
    return ProviderJob(kind="image", model="fake_sheet", params={"aspect_ratio": "1:1", "resolution": "1k"}, prompt=prompt)


class RegistryTests(unittest.TestCase):
    def test_default_is_higgsfield(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKERLOOM_PROVIDER", None)
            p = providers.current()
        self.assertEqual((p.name, p.job_provider), ("higgsfield", "higgsfield-cli"))

    def test_env_selects_fake_and_openai(self):
        with mock.patch.dict(os.environ, {"STICKERLOOM_PROVIDER": "fake"}):
            self.assertEqual(providers.current().name, "fake")
        with mock.patch.dict(os.environ, {"STICKERLOOM_PROVIDER": "openai"}):
            self.assertEqual(providers.current().name, "openai")

    def test_unknown_provider_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {"STICKERLOOM_PROVIDER": "bogus"}):
            with self.assertRaises(ProviderError) as cm:
                providers.current()
        self.assertEqual(cm.exception.code, 400)

    def test_for_job_maps_stored_strings(self):
        self.assertEqual(providers.for_job("higgsfield-cli").name, "higgsfield")
        self.assertEqual(providers.for_job("fake").name, "fake")
        self.assertEqual(providers.for_job("openai").name, "openai")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKERLOOM_PROVIDER", None)
            self.assertEqual(providers.for_job(None).name, "higgsfield")      # a job from before providers
            self.assertEqual(providers.for_job("something-old").name, "higgsfield")

    def test_higgs_error_is_a_provider_error(self):
        from stickerloom.generation.higgsfield import HiggsError
        self.assertTrue(issubclass(HiggsError, ProviderError))
        self.assertTrue(HiggsError("HTTP 503").transient)
        self.assertFalse(HiggsError("HTTP 400 refused").transient)


class FakeProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = providers.FakeProvider()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_always_available_and_free(self):
        self.assertTrue(self.p.available())
        self.assertEqual(self.p.cost(sheet_job()), 0.0)
        self.assertEqual(self.p.account()["plan"], "fake")

    def test_sheet_round_trip(self):
        jid = self.p.create_image(sheet_job())
        res = self.p.wait(jid)
        self.assertEqual(res["status"], "completed")
        sha, n = self.p.download(res["result_url"], self.tmp / "s.png")
        img = cv2.imread(str(self.tmp / "s.png"))
        self.assertEqual(img.shape, (1024, 1024, 3))
        green = (img == (0, 255, 0)).all(-1)
        self.assertGreater(green.mean(), 0.5, "mostly flat chroma screen")
        self.assertLess(green.mean(), 0.95, "with shapes on it")
        self.assertEqual(sha, hashlib.sha256((self.tmp / "s.png").read_bytes()).hexdigest())
        self.assertEqual(n, (self.tmp / "s.png").stat().st_size)

    def test_same_spec_same_bytes(self):
        a = self.p.create_image(sheet_job())
        b = self.p.create_image(sheet_job())
        self.assertNotEqual(a, b, "two tickets")
        sa, _ = self.p.download(self.p.wait(a)["result_url"], self.tmp / "a.png")
        sb, _ = self.p.download(self.p.wait(b)["result_url"], self.tmp / "b.png")
        self.assertEqual(sa, sb)

    def test_grid_comes_from_the_prompt(self):
        job3 = ProviderJob(kind="image", model="fake_sheet", params={}, prompt="One illustration of 9 separate characters arranged in 3 rows of 3.")
        jid = self.p.create_image(job3)
        self.p.download(self.p.wait(jid)["result_url"], self.tmp / "g.png")
        img = cv2.imread(str(self.tmp / "g.png"))
        centres = [img[y, x].tolist() for y in (170, 512, 853) for x in (170, 512, 853)]
        self.assertTrue(all(c != [0, 255, 0] for c in centres), "a shape in every one of the 9 cells")

    def test_unknown_ticket_is_a_clean_404(self):
        with self.assertRaises(ProviderError) as cm:
            self.p.wait("fake-9999")
        self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(ProviderError):
            self.p.download("https://example.invalid/x.png", self.tmp / "x.png")

    def test_video_is_a_short_seamless_loop(self):
        jid = self.p.create_image(sheet_job())
        start = self.tmp / "start.png"
        self.p.download(self.p.wait(jid)["result_url"], start)
        v = ProviderJob(kind="video", model="fake_video", params={"duration": "3"}, prompt="Animate this 2x2 grid",
                        media={"start_image": str(start)})
        vid = self.p.create_video(v)
        sha, _ = self.p.download(self.p.wait(vid)["result_url"], self.tmp / "v.mp4")
        cap = cv2.VideoCapture(str(self.tmp / "v.mp4"))
        frames = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            frames.append(f)
        cap.release()
        self.assertEqual(len(frames), 72, "3 s at 24 fps")
        self.assertFalse(np.array_equal(frames[0], frames[36]), "it moves")
        self.assertTrue(np.array_equal(frames[0], frames[24]), "one period per second: the loop is seamless")
        vid2 = self.p.create_video(v)
        sha2, _ = self.p.download(self.p.wait(vid2)["result_url"], self.tmp / "v2.mp4")
        self.assertEqual(sha, sha2)

    def test_video_without_a_start_image_is_a_clean_error(self):
        vid = self.p.create_video(ProviderJob(kind="video", model="fake_video", params={}, prompt="x"))
        with self.assertRaises(ProviderError):
            self.p.download(self.p.wait(vid)["result_url"], self.tmp / "v.mp4")


class FakeFulfilTests(unittest.TestCase):
    """Through the real `jobs.fulfil` with STICKERLOOM_PROVIDER=fake: no patches, no network."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "in").mkdir()
        self.out = self.tmp / "out"
        self.env = mock.patch.dict(os.environ, {"STICKERLOOM_PROVIDER": "fake",
                                                "STICKERLOOM_NO_REAL_CLI": "1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sheet_job_finishes_offline(self):
        from stickerloom.generation import prompter
        prompt = prompter.expand("a happy cactus", (2, 2))["sheet_prompt"]
        j = jobs.create(self.out, "sheet", request={"prompt": prompt})
        self.assertEqual(j["provider"], "fake")
        done = jobs.fulfil(self.out, j["id"])
        self.assertEqual(done["status"], "DONE", done.get("error"))
        self.assertEqual(done["model"], "fake_sheet")
        self.assertTrue((self.out / done["result"]["file"]).is_file())
        self.assertEqual(done["cost"], 0.0)

    def test_video_job_finishes_offline(self):
        from stickerloom.generation import prompter
        start = self.out / "start.png"
        start.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(start), np.full((512, 512, 3), (0, 255, 0), np.uint8))
        prompt = prompter.expand("a happy cactus", (2, 2))["video_prompt"]
        j = jobs.create(self.out, "video", request={"prompt": prompt, "start_image": str(start)})
        done = jobs.fulfil(self.out, j["id"])
        self.assertEqual(done["status"], "DONE", done.get("error"))
        self.assertEqual(done["model"], "fake_video")
        self.assertTrue(str((self.out / done["result"]["file"])).endswith(".mp4"))


class CatalogProviderTests(unittest.TestCase):
    def test_fake_defaults(self):
        with mock.patch.dict(os.environ, {"STICKERLOOM_PROVIDER": "fake"}):
            self.assertEqual(model_catalog.resolve("image", None, {})[0], "fake_sheet")
            self.assertEqual(model_catalog.resolve("video", None, {})[0], "fake_video")
            self.assertEqual(model_catalog.catalog()["provider"], "fake")

    def test_higgsfield_defaults_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKERLOOM_PROVIDER", None)
            self.assertEqual(model_catalog.resolve("image", None, {}),
                             ("nano_banana_flash", {"aspect_ratio": "1:1", "resolution": "2k"}))
            self.assertEqual(model_catalog.resolve("video", None, {}),
                             ("kling3_0", {"aspect_ratio": "1:1", "sound": "off", "mode": "pro", "duration": "3"}))

    def test_no_vendor_logos_in_any_catalog(self):
        for name in ("higgsfield", "fake", "openai"):
            for kind in ("image", "video"):
                for m in model_catalog.catalog(provider=name)[kind]:
                    self.assertNotIn("logo", m, f"{name}/{kind}/{m['id']}")

    def test_unknown_provider_is_a_catalog_error(self):
        with self.assertRaises(model_catalog.CatalogError):
            model_catalog.resolve("image", None, {}, provider="bogus")


if __name__ == "__main__":
    unittest.main()
