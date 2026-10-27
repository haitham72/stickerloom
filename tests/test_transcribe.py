"""Per-frame vision (vision/transcribe.py): one caption per cell of a sheet, grid-agnostic, stored with the sticker, never touching `review.*`, and the consent rule that
decides whether any image may be sent to a vision model at all ("Allow AI vision of generated media?", asked once)."""
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from stickerloom.runtime import cache as cachemod
from stickerloom.vision import consent, transcribe
from stickerloom.vision.judge import VisionJudge, judge_generation


def png(color):
    b = io.BytesIO()
    Image.new("RGBA", (64, 64), color).save(b, "PNG")
    return b.getvalue()


class Fake:
    """Stands in for the vision model: records every call, answers a caption that names the colour of the image it was shown."""

    def __init__(self):
        self.calls = []

    def __call__(self, system, user, images=None):
        self.calls.append({"system": system, "user": user, "n": len(images or [])})
        px = Image.open(io.BytesIO(images[0])).convert("RGB").getpixel((30, 30))
        return json.dumps({"caption": f"a character in rgb {px[0]} {px[1]} {px[2]} waving", "text_visible": None}), {"model": "fake-vlm"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.cache = cachemod.Cache(force_memory=True)
        self.fake = Fake()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def batch(self, gid=1, rows=2, cols=2):
        d = self.out / f"G{gid:03d}"
        (d / "slices").mkdir(parents=True)
        st = []
        for i in range(1, rows * cols + 1):
            (d / "slices" / f"s{i}.png").write_bytes(png((i * 20, 100, 200, 255)))
            st.append({"index": i, "key": f"k{i}", "name": f"n{i}", "status": "READY", "png": f"slices/s{i}.png", "review": {"still": "PENDING", "anim": "PENDING"}})
        st[0]["judge"] = {"decision": "APPROVE", "reasons": []}
        st[1]["judge"] = {"decision": "REJECT", "reasons": ["WEAK_CONCEPT"]}
        (d / "result.json").write_text(json.dumps({"id": gid, "prompt": "cat", "grid": [rows, cols], "stickers": st}), encoding="utf-8")
        return gid

    def vlm(self):
        return VisionJudge(complete=self.fake, out=self.out, cache=self.cache)


class CaptionTests(Base):
    def test_a_2x2_sheet_gets_four_captions_with_its_grid_read_from_result_json(self):
        gid = self.batch(rows=2, cols=2)
        caps = transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        self.assertEqual([c.index for c in caps], [1, 2, 3, 4])
        self.assertEqual([(c.row, c.col) for c in caps], [(0, 0), (0, 1), (1, 0), (1, 1)])
        self.assertEqual({c.grid for c in caps}, {(2, 2)})
        self.assertEqual(len(self.fake.calls), 4)
        self.assertIn("rgb 20 100 200", caps[0].caption)                                           # each caption comes from its own cell's image
        self.assertEqual((caps[0].verdict, caps[1].verdict, caps[2].verdict), ("APPROVE", "REJECT", None))
        self.assertEqual(caps[1].reasons, ["WEAK_CONCEPT"])                                         # the verdict is the judge's, already on the sticker: no second call for it
        self.assertEqual({c.generation_id for c in caps}, {"G001"})

    def test_a_3x3_sheet_uses_the_same_code(self):
        gid = self.batch(rows=3, cols=3)
        caps = transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        self.assertEqual((len(caps), caps[8].row, caps[8].col, caps[8].grid), (9, 2, 2, (3, 3)))

    def test_a_second_call_does_not_reach_the_model_and_force_does(self):
        gid = self.batch()
        transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        n = len(self.fake.calls)
        again = transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        self.assertEqual(len(self.fake.calls), n)
        self.assertTrue(all(c.cached for c in again))
        self.assertEqual(len(transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True, force=True)), 4)
        self.assertEqual(len(self.fake.calls), n + 4)

    def test_captions_are_stored_with_the_sticker_and_read_back_without_consent_or_a_model(self):
        gid = self.batch()
        transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        res = json.loads((self.out / "G001" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(res["stickers"][0]["caption"]["model"], "fake-vlm")
        n = len(self.fake.calls)
        sheet = transcribe.sheet_with_captions(self.out, gid)                                       # read-only: what is stored, no model, no consent
        self.assertEqual((sheet["grid"], len(sheet["cells"]), sheet["missing"]), ([2, 2], 4, 0))
        self.assertEqual(sheet["cells"][2]["caption"], "a character in rgb 60 100 200 waving")
        self.assertEqual(len(self.fake.calls), n)
        self.assertEqual(sheet["cells"][0]["png"], "slices/s1.png")

    def test_an_edited_sticker_is_captioned_again_and_the_review_is_never_touched(self):
        gid = self.batch()
        transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        (self.out / "G001" / "slices" / "s1.png").write_bytes(png((250, 0, 0, 255)))                  # the user edited it
        n = len(self.fake.calls)
        caps = transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        self.assertEqual(len(self.fake.calls), n + 1)
        self.assertIn("rgb 250 0 0", caps[0].caption)
        res = json.loads((self.out / "G001" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual({s["review"]["still"] for s in res["stickers"]}, {"PENDING"})              # a caption never approves or rejects anything

    def test_an_unreadable_model_answer_leaves_that_cell_without_a_caption_and_the_rest_intact(self):
        gid = self.batch()

        def flaky(system, user, images=None):                                                        # the first cell's image always gets nonsense, twice (the repair round fails too)
            px = Image.open(io.BytesIO(images[0])).convert("RGB").getpixel((30, 30))
            if px[0] == 20:
                return "not json at all", {"model": "m"}
            return json.dumps({"caption": "ok", "text_visible": None}), {"model": "m"}
        caps = transcribe.captions_for(self.out, gid, vlm=VisionJudge(complete=flaky, out=self.out, cache=self.cache), allowed=True)
        self.assertEqual(len(caps), 4)
        self.assertIsNone(caps[0].caption)
        self.assertTrue(caps[0].error)
        self.assertEqual([c.caption for c in caps[1:]], ["ok", "ok", "ok"])
        res = json.loads((self.out / "G001" / "result.json").read_text(encoding="utf-8"))
        self.assertNotIn("caption", res["stickers"][0])                                              # a failure is never stored as a caption


class ConsentTests(Base):
    def test_without_consent_nothing_is_sent_to_a_model(self):
        gid = self.batch()
        for allowed in (None, False):
            with self.assertRaises(consent.ConsentRequired):
                transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=allowed)
            with self.assertRaises(consent.ConsentRequired):
                judge_generation(self.out, gid, "still", judge=self.vlm(), allowed=allowed)
        self.assertEqual(self.fake.calls, [])
        res = json.loads((self.out / "G001" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual({s["review"]["still"] for s in res["stickers"]}, {"PENDING"})              # stickers stay READY and unjudged
        self.assertFalse(any("history" in s for s in res["stickers"]))

    def test_the_consent_rule(self):
        consent.require(True)
        for bad in (None, False, "yes", 1, 0, ""):
            with self.assertRaises(consent.ConsentRequired):
                consent.require(bad)
        self.assertEqual(consent.ConsentRequired("x").code, 409)
        self.assertTrue(consent.asked({"allow_vlm": True}) and consent.asked({"allow_vlm": False}) and not consent.asked({"allow_vlm": None}) and not consent.asked({}))

    def test_stored_captions_are_readable_without_consent(self):
        gid = self.batch()
        transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=True)
        again = transcribe.captions_for(self.out, gid, vlm=self.vlm(), allowed=None)                 # nothing needs a model: no consent is needed to read what exists
        self.assertEqual(len(again), 4)


class RouteTests(Base):
    """GET reads what is stored (no model, no consent); POST generates and needs `allow_vlm: true` in the request."""

    def setUp(self):
        super().setUp()
        import http.client
        import threading
        from unittest import mock
        from stickerloom.console.server import serve
        (self.tmp / "in").mkdir()
        self.patches = [mock.patch.object(cachemod, "default", lambda: self.cache),
                        mock.patch.object(transcribe, "VisionJudge", lambda out=None, **k: VisionJudge(complete=self.fake, out=out, cache=self.cache))]
        for p in self.patches:
            p.start()
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, block=False)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.http = http.client.HTTPConnection
        self.mine, self.t_mine = self.c.users.create("Mine")
        self.theirs, self.t_theirs = self.c.users.create("Theirs")
        self.gid = self.batch()
        res_path = self.out / "G001" / "result.json"
        res = json.loads(res_path.read_text(encoding="utf-8"))
        res["owner"] = self.mine["id"]
        res_path.write_text(json.dumps(res), encoding="utf-8")

    def tearDown(self):
        self.srv.shutdown()
        self.c.release_writer()
        self.srv.server_close()
        for p in self.patches:
            p.stop()
        super().tearDown()

    def call(self, method, path, body=None, token=None):
        h = self.http("127.0.0.1", self.srv.server_address[1], timeout=30)
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {"Sec-Fetch-Site": "same-origin"})}
        h.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = h.getresponse()
        data = json.loads(r.read())
        h.close()
        return r.status, data

    def test_reading_needs_no_consent_and_shows_what_is_missing(self):
        s, d = self.call("GET", "/api/generations/1/captions")
        self.assertEqual((s, d["grid"], d["missing"], d["ready"]), (200, [2, 2], 4, 4))
        self.assertEqual(self.fake.calls, [])

    def test_generating_without_consent_is_a_409_that_says_so_and_sends_nothing(self):
        for body in (None, {}, {"allow_vlm": False}, {"allow_vlm": "yes"}):
            s, d = self.call("POST", "/api/generations/1/captions", body if body is not None else {})
            self.assertEqual((s, d.get("consent_required")), (409, True), body)
        s, d = self.call("POST", "/api/generations/1/judge", {})
        self.assertEqual((s, d.get("consent_required")), (409, True))                                  # the judge asks for the same consent
        self.assertEqual(self.fake.calls, [])

    def test_with_consent_it_runs_in_the_background_and_the_page_reads_the_result(self):
        import time
        s, d = self.call("POST", "/api/generations/1/captions", {"allow_vlm": True})
        self.assertEqual((s, d["id"]), (202, 1))
        end = time.time() + 20
        while time.time() < end:
            s, d = self.call("GET", "/api/generations/1/captions")
            if d["missing"] == 0:
                break
            time.sleep(0.2)
        self.assertEqual(d["missing"], 0)
        self.assertEqual(len(self.fake.calls), 4)
        self.assertIn("rgb 20 100 200", d["cells"][0]["caption"])
        self.assertEqual((d["cells"][0]["verdict"], d["cells"][1]["verdict"]), ("APPROVE", "REJECT"))

    def test_a_missing_batch_is_a_404_and_a_stranger_gets_one_too(self):
        self.assertEqual(self.call("GET", "/api/generations/9/captions")[0], 404)
        self.assertEqual(self.call("POST", "/api/generations/9/captions", {"allow_vlm": True})[0], 404)
        self.assertEqual(self.call("GET", "/api/generations/1/captions", token=self.t_mine)[0], 200)
        self.assertEqual(self.call("GET", "/api/generations/1/captions", token=self.t_theirs)[0], 404)
        self.assertEqual(self.call("POST", "/api/generations/1/captions", {"allow_vlm": True}, token=self.t_theirs)[0], 404)


if __name__ == "__main__":
    unittest.main()
