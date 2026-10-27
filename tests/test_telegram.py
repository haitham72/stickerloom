"""Send to Telegram (checkpoint 1H): rules, names, plan, send, re-send, errors, the zip, the page's API. Against a stdlib fake Bot API."""
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from stickerloom.services import telegram as tg
from stickerloom.console.server import serve
from stickerloom.engine import ffmpeg as ff
from stickerloom.engine import verify
from stickerloom.engine.config import EngineConfig
from stickerloom.media.library import Library
from tests import synth
from tests.fake_telegram import BOT, TOKEN, USER, FakeServer

CFG = EngineConfig()


def png(shape="disc", ring=0, size=512):
    rgba = synth.sticker_rgba(shape, size=size, ring=ring)
    b = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(b, "PNG")
    return b.getvalue()


def webm(frames=12):
    f = np.zeros((frames, 512, 512, 4), np.uint8)
    for i in range(frames):
        f[i] = synth.sticker_rgba("disc")
        f[i][..., 0] = (i * 10) % 255
    import tempfile as tf
    with tf.TemporaryDirectory() as td:
        p = Path(td) / "a.webm"
        ff.encode_webm(f, 24, 38, p)
        return p.read_bytes()


class Rules(unittest.TestCase):
    def test_emoji_list(self):
        for s, want in (("👍", ["👍"]), ("👍🏽", ["👍🏽"]), ("👨‍👩‍👧", ["👨‍👩‍👧"]), ("🇦🇪", ["🇦🇪"]), ("1️⃣", ["1️⃣"]), ("😀 😂", ["😀", "😂"]), ("😀😂", ["😀", "😂"]), ("", []), ("❤️,🔥", ["❤️", "🔥"])):
            self.assertEqual(tg.split_emoji(s), want, s)

    def test_names(self):
        n = tg.set_name("Teddy Bear Pack!", "_v", "stickerloombot")
        self.assertEqual(n, "teddy_bear_pack_v_by_stickerloombot")
        self.assertEqual(verify.tg_set_name_problems(n, "stickerloomBot"), [])                  # the _by_ tail is case-insensitive
        self.assertTrue(tg.set_name("123 numbers", "", "b").startswith("p_"))              # must start with a letter
        self.assertLessEqual(len(tg.set_name("x" * 200, "_s", "stickerloombot")), 64)
        self.assertTrue(verify.tg_set_name_problems("no_tail", "stickerloombot"))               # must end with _by_<bot>
        self.assertTrue(verify.tg_set_name_problems("bad__double_by_stickerloombot", "stickerloombot"))
        self.assertTrue(verify.tg_set_name_problems("9starts_by_stickerloombot", "stickerloombot"))

    def sticker(self, **kw):
        base = {"kind": "static", "bytes": 100_000, "alpha": True, "w": 512, "h": 512, "emoji": ["😀"], "stroke": True}
        base.update(kw)
        return verify.run("telegram", base, CFG)

    def test_static_rules(self):
        self.assertTrue(all(c.ok for c in self.sticker()))
        for bad in ({"bytes": 600 * 1024}, {"alpha": False}, {"w": 500, "h": 500}, {"w": 600, "h": 512}, {"emoji": []}, {"emoji": ["😀"] * 21}):
            c = verify.run("telegram", {"kind": "static", "bytes": 1000, "alpha": True, "w": 512, "h": 512, "emoji": ["😀"], **bad}, CFG)[0]
            self.assertFalse(c.ok, bad)
            self.assertEqual(c.severity, verify.BLOCK)

    def test_stroke_is_a_warning_not_a_block(self):
        ok = verify.run("telegram", {"kind": "static", "bytes": 1, "alpha": True, "w": 512, "h": 512, "emoji": ["😀"], "stroke": False}, CFG)
        self.assertTrue(ok[0].ok)                                                          # the sticker is acceptable...
        self.assertFalse(ok[1].ok)                                                         # ...but the missing stroke is flagged
        self.assertEqual(ok[1].severity, verify.WARN)
        self.assertTrue(verify.has_white_stroke(np.array(Image.open(io.BytesIO(png(ring=12))).convert("RGBA"))))
        self.assertFalse(verify.has_white_stroke(np.array(Image.open(io.BytesIO(png())).convert("RGBA"))))

    def test_video_rules(self):
        good = {"codec": "vp9", "width": 512, "height": 512, "fps": 30.0, "duration": 3.0, "audio": False}
        run = lambda **o: verify.run("telegram", {"kind": "video", "bytes": 200_000, "alpha": True, "w": 512, "h": 512, "emoji": ["😀"], "info": {**good, **o.pop("info", {})}, **o}, CFG)[0]
        self.assertTrue(run().ok)
        for bad in ({"info": {"codec": "h264"}}, {"info": {"fps": 60.0}}, {"info": {"duration": 4.5}}, {"info": {"audio": True}}, {"bytes": 300 * 1024}, {"alpha": False}):
            self.assertFalse(run(**bad).ok, bad)

    def test_set_rules(self):
        st = [{"key": "a"}, {"key": "b"}]
        run = lambda **o: verify.run("telegram_set", {"stickers": st, "name": "x_by_stickerloombot", "bot": "stickerloombot", "title": "X", **o}, CFG)[0]
        self.assertTrue(run().ok)
        self.assertFalse(run(stickers=[{"key": "a"}, {"key": "a"}]).ok)                    # unique keys
        self.assertFalse(run(stickers=[{"key": str(i)} for i in range(121)]).ok)           # 120 at most
        self.assertFalse(run(stickers=[]).ok)
        self.assertFalse(run(name="x_by_otherbot").ok)
        self.assertFalse(run(title="").ok)


class LibFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = Library(self.tmp / "out")
        self.out = self.tmp / "out"
        self.pid = self.lib.create_pack("Teddy Pack")["id"]
        self.fake = FakeServer().__enter__()
        self.old = {k: os.environ.get(k) for k in ("STICKERLOOM_TELEGRAM_API", "STICKERLOOM_TELEGRAM_TOKEN", "STICKERLOOM_TELEGRAM_USER")}
        os.environ["STICKERLOOM_TELEGRAM_API"] = self.fake.url
        for k in ("STICKERLOOM_TELEGRAM_TOKEN", "STICKERLOOM_TELEGRAM_USER"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.fake.__exit__()
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def connect(self, user=USER):
        return tg.save_config(self.out, TOKEN, user)

    def add_static(self, name, emoji="😀", stroke=True):
        return self.lib.add_bytes(self.pid, png(ring=12 if stroke else 0), "png", name, "static", emoji)

    def add_video(self, name, emoji="🔥"):
        return self.lib.add_bytes(self.pid, webm(), "webm", name, "animated", emoji)


class Connect(LibFixture):
    def test_connect_proves_the_token_and_never_shows_it(self):
        self.assertEqual(tg.status(self.out)["configured"], False)
        with self.assertRaises(tg.TelegramError):
            tg.save_config(self.out, "not a token", USER)
        with self.assertRaises(tg.TelegramError):
            tg.save_config(self.out, TOKEN, "@username")                                 # the id is a number
        with self.assertRaises(tg.TelegramError) as e:
            tg.save_config(self.out, "999999999:AAWrongTokenWrongTokenWrongToken", USER)
        self.assertIn("token is wrong", str(e.exception))
        st = self.connect()
        self.assertEqual((st["configured"], st["bot"], st["user_id"]), (True, BOT, USER))
        self.assertNotIn(TOKEN, json.dumps(st))
        self.assertTrue((self.out / "telegram.json").is_file())
        self.assertEqual(tg.disconnect(self.out)["configured"], False)

    def test_environment_wins_over_the_file(self):
        os.environ["STICKERLOOM_TELEGRAM_TOKEN"], os.environ["STICKERLOOM_TELEGRAM_USER"] = TOKEN, USER
        self.assertEqual(tg.status(self.out)["configured"], True)
        self.assertTrue(tg.status(self.out)["from_env"])


class Plan(LibFixture):
    def test_mixed_pack_becomes_two_sets(self):
        self.add_static("Happy", "😀")
        self.add_video("Fire", "🔥")
        p = tg.plan(self.lib, self.pid, BOT, None, CFG)
        self.assertEqual([(s["kind"], s["name"]) for s in p["sets"]], [("video", "teddy_pack_v_by_stickerloombot"), ("static", "teddy_pack_s_by_stickerloombot")])
        self.assertEqual(p["blocked"], [])
        self.assertEqual(p["sets"][0]["items"][0]["emoji"], ["🔥"])

    def test_one_kind_has_no_suffix_and_problems_are_named_before_any_call(self):
        self.add_static("Plain", stroke=False)
        p = tg.plan(self.lib, self.pid, BOT, None, CFG)
        self.assertEqual(p["sets"][0]["name"], "teddy_pack_by_stickerloombot")
        self.assertEqual(len(p["warnings"]), 1)                                          # no white stroke: a warning
        self.assertIn("no white stroke", p["warnings"][0])
        big = tg.plan(self.lib, self.pid, BOT, None, replace(CFG, static_max_bytes=100))
        self.assertTrue(any("limit" in b for b in big["blocked"]))
        self.assertEqual(self.fake.f.calls, [])                                          # planning never touches the network

    def test_a_pack_without_a_connected_bot_still_shows_its_plan(self):
        self.add_static("A")
        p = tg.plan(self.lib, self.pid, None, None, CFG)
        self.assertTrue(p["sets"][0]["name"].endswith("_by_yourbot"))


class Send(LibFixture):
    def test_create_resend_and_add_only_what_is_new(self):
        self.connect()
        a, b = self.add_static("One", "😀"), self.add_video("Two", "🔥")
        r = tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertEqual({s["kind"]: (s["added"], s["name"]) for s in r["sets"]},
                         {"video": (1, "teddy_pack_v_by_stickerloombot"), "static": (1, "teddy_pack_s_by_stickerloombot")})
        self.assertEqual(sorted(self.fake.f.sets), ["teddy_pack_s_by_stickerloombot", "teddy_pack_v_by_stickerloombot"])
        self.assertEqual(self.fake.f.sets["teddy_pack_v_by_stickerloombot"]["kind"], "video")
        self.assertEqual(self.fake.f.sets["teddy_pack_s_by_stickerloombot"]["stickers"][0]["emoji"], "😀")
        saved = next(p for p in self.lib.snapshot()["packs"] if p["id"] == self.pid)["telegram"]["sets"]
        self.assertEqual({t["kind"] for t in saved}, {"video", "static"})
        self.assertTrue(all(i["file_unique_id"] for t in saved for i in t["items"]))
        n_calls = len(self.fake.f.calls)

        r = tg.send(self.out, self.lib, self.pid, None, CFG)                                # again: nothing is created twice
        self.assertEqual([s["added"] for s in r["sets"]], [0, 0])
        self.assertEqual([m for m, _ in self.fake.f.calls[n_calls:] if m in ("createNewStickerSet", "addStickerToSet")], [])

        c = self.add_static("Three", "🎉")
        r = tg.send(self.out, self.lib, self.pid, None, CFG)                                # one new sticker: added to the existing set
        self.assertEqual({s["kind"]: s["added"] for s in r["sets"]}, {"video": 0, "static": 1})
        self.assertEqual(len(self.fake.f.sets["teddy_pack_s_by_stickerloombot"]["stickers"]), 2)
        self.assertIn("addStickerToSet", [m for m, _ in self.fake.f.calls])
        saved = next(p for p in self.lib.snapshot()["packs"] if p["id"] == self.pid)["telegram"]["sets"]
        self.assertEqual([i["sticker_id"] for t in saved if t["kind"] == "static" for i in t["items"]], [a["id"], c["id"]])

    def test_the_pack_is_shown_inside_telegram(self):
        """A set made by a bot is invisible until its link is opened: the bot writes the owner the link and sends the first sticker."""
        self.connect()
        self.add_static("One", "😀"); self.add_static("Two", "🎉")
        r = tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertTrue(r["notified"])
        kinds = [m for m, _, _ in self.fake.f.messages]
        self.assertEqual(kinds, ["sendMessage", "sendSticker"])
        self.assertIn("https://t.me/addstickers/teddy_pack_by_stickerloombot", self.fake.f.messages[0][2])
        self.assertEqual(self.fake.f.messages[1][2], "FILE00001")
        n = len(self.fake.f.messages)
        r = tg.send(self.out, self.lib, self.pid, None, CFG)                           # nothing new -> no second message
        self.assertEqual((r["notified"], len(self.fake.f.messages)), (False, n))

    def test_a_muted_owner_does_not_fail_the_send(self):
        self.connect()
        self.fake.f.mute = True
        self.add_static("One", "😀")
        r = tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertEqual(r["sets"][0]["added"], 1)                                      # the pack exists
        self.assertFalse(r["notified"])
        self.assertIn("press Start", r["notify_error"])

    def test_more_than_50_goes_in_batches(self):
        self.connect()
        data = png(ring=12)
        for i in range(53):
            self.lib.add_bytes(self.pid, data, "png", f"S{i:02d}", "static", "😀")
        tg.send(self.out, self.lib, self.pid, None, CFG)
        methods = [m for m, _ in self.fake.f.calls if m in ("createNewStickerSet", "addStickerToSet")]
        self.assertEqual((methods.count("createNewStickerSet"), methods.count("addStickerToSet")), (1, 3))
        self.assertEqual(len(self.fake.f.sets["teddy_pack_by_stickerloombot"]["stickers"]), 53)

    def test_errors_are_plain_and_the_token_never_leaks(self):
        self.add_static("One")
        with self.assertRaises(tg.TelegramError) as e:
            tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertIn("not connected", str(e.exception))
        self.connect(user="777")                                                           # a user who never pressed Start
        with self.assertRaises(tg.TelegramError) as e:
            tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertIn("press Start", str(e.exception))
        self.fake.f.started.add("777")
        self.fake.f.taken.add("teddy_pack_by_stickerloombot")
        with self.assertRaises(tg.TelegramError) as e:
            tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertIn("already taken", str(e.exception))
        self.assertEqual(e.exception.code, 409)
        for text in self.fake.f.responses + [str(e.exception)]:
            self.assertNotIn(TOKEN, text)

    def test_rate_limit_waits_and_retries(self):
        self.connect()
        self.add_static("One")
        self.fake.f.fail_429 = 2
        r = tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertEqual(r["sets"][0]["added"], 1)

    def test_unreachable_telegram_is_a_plain_error_without_the_token(self):
        self.connect()
        self.add_static("One")
        os.environ["STICKERLOOM_TELEGRAM_API"] = "http://127.0.0.1:9"                           # nothing listens there
        with self.assertRaises(tg.TelegramError) as e:
            tg.send(self.out, self.lib, self.pid, None, CFG)
        self.assertIn("Cannot reach Telegram", str(e.exception))
        self.assertNotIn(TOKEN, str(e.exception))

    def test_a_blocked_sticker_stops_everything_before_the_network(self):
        self.connect()
        self.add_static("One")
        n = len(self.fake.f.calls)
        with self.assertRaises(tg.TelegramError) as e:
            tg.send(self.out, self.lib, self.pid, None, replace(CFG, static_max_bytes=100))
        self.assertIn("Not sent", str(e.exception))
        self.assertEqual(len(self.fake.f.calls), n)


class Zip(LibFixture):
    def test_upload_folder_for_the_stickers_bot(self):
        self.add_static("Happy Face", "😀")
        self.add_video("Fire", "🔥")
        data, name = tg.zip_for_stickers_bot(self.lib, self.pid)
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(name, "Teddy Pack")
        names = z.namelist()
        self.assertIn("01-happy_face.png", names)
        self.assertIn("02-fire.webm", names)
        txt = z.read("stickers.txt").decode()
        self.assertIn("01-happy_face.png\t😀\tstatic", txt)
        self.assertIn("02-fire.webm\t🔥\tvideo", txt)
        self.assertIn("/newvideo", z.read("HOW-TO.txt").decode())


def poll_send(req, tid, timeout=30):
    """Poll a queued Telegram send until it finishes (like the page's tgWatch)."""
    end = time.time() + timeout
    while time.time() < end:
        s, st = req("GET", f"/api/telegram/sends/{tid}")
        assert s == 200, st
        if st["state"] in ("done", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError("queued Telegram send never finished")


class Api(LibFixture):
    """The page's endpoints, against the fake: connect, plan, send, zip. The token is never in a response."""
    def setUp(self):
        super().setUp()
        self.add_static("One", "😀")
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, block=False)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        super().tearDown()

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse(); raw = r.read(); h.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def test_flow(self):
        self.assertEqual(self.req("GET", "/api/telegram")[1]["configured"], False)
        s, plan = self.req("GET", f"/api/packs/{self.pid}/telegram")
        self.assertEqual((s, plan["sets"][0]["name"].endswith("_by_yourbot")), (200, True))        # plan works before connecting
        self.assertEqual(self.req("POST", f"/api/packs/{self.pid}/telegram", {})[0], 409)           # but sending needs a connection
        self.assertEqual(self.req("POST", "/api/telegram/config", {"token": "x", "user_id": "1"})[0], 400)
        s, st = self.req("POST", "/api/telegram/config", {"token": TOKEN, "user_id": USER})
        self.assertEqual((s, st["configured"], st["bot"]), (200, True, BOT))
        s, q = self.req("POST", f"/api/packs/{self.pid}/telegram", {})
        self.assertEqual(s, 202, q)
        r = poll_send(self.req, q["id"])
        self.assertEqual(r["state"], "done", r)
        self.assertEqual(r["result"]["sets"][0]["link"], "https://t.me/addstickers/teddy_pack_by_stickerloombot")
        s, q2 = self.req("POST", f"/api/packs/{self.pid}/telegram", {})
        r2 = poll_send(self.req, q2["id"])
        self.assertEqual(r2["result"]["sets"][0]["added"], 0)
        self.assertEqual(self.req("POST", f"/api/packs/{self.pid}/telegram", {"mode": "nope"})[0], 400)
        self.assertEqual(self.req("GET", "/api/telegram/sends/T0")[0], 404)
        s, z = self.req("GET", f"/api/packs/{self.pid}/telegram.zip")
        self.assertEqual((s, z[:2]), (200, b"PK"))
        s, z = self.req("GET", f"/api/packs/{self.pid}/export.zip")                                      # the plain download (no Telegram wording)
        self.assertEqual((s, z[:2]), (200, b"PK"))
        self.assertEqual(self.req("GET", "/api/packs/nope/export.zip")[0], 404)
        for path in ("/api/telegram", f"/api/packs/{self.pid}/telegram"):
            self.assertNotIn(TOKEN, json.dumps(self.req("GET", path)[1]))
        self.assertEqual(self.req("POST", "/api/telegram/disconnect")[1]["configured"], False)


if __name__ == "__main__":
    unittest.main()


class NeverTwice(Send):
    """Deployment branch: the same pack content is never sent twice; Replace and Send as a new set are explicit, recorded and never the default."""

    def test_the_same_content_is_answered_from_the_record_without_calling_telegram(self):
        self.add_static("One", "😀")
        self.connect()
        first = tg.send(self.out, self.lib, self.pid)
        self.assertFalse(first["already"])
        calls = len(self.fake.f.calls)
        again = tg.send(self.out, self.lib, self.pid)
        self.assertTrue(again["already"])
        self.assertEqual(len(self.fake.f.calls), calls, "not one call reached Telegram")
        self.assertEqual([s["added"] for s in again["sets"]], [0])
        self.assertEqual(again["sets"][0]["link"], first["sets"][0]["link"])

    def test_a_retyped_name_or_a_duplicated_pack_is_still_the_same_pack(self):
        self.add_static("One", "😀")
        self.connect()
        tg.send(self.out, self.lib, self.pid)
        self.lib.update_pack(self.pid, name="A different name")
        self.assertTrue(tg.send(self.out, self.lib, self.pid)["already"], "the name is not the identity")

    def test_changed_bytes_change_the_fingerprint_and_the_send_goes_ahead(self):
        s = self.add_static("One", "😀")
        self.connect()
        before = tg.fingerprint(self.lib, self.pid)
        tg.send(self.out, self.lib, self.pid)
        self.lib.replace_file(self.pid, s["id"], png(ring=0), "png")
        self.assertNotEqual(before, tg.fingerprint(self.lib, self.pid))
        self.assertFalse(tg.send(self.out, self.lib, self.pid)["already"])

    def test_replace_sends_on_purpose_and_new_set_makes_a_second_numbered_set(self):
        self.add_static("One", "😀")
        self.connect()
        tg.send(self.out, self.lib, self.pid)
        rep = tg.send(self.out, self.lib, self.pid, mode="replace")
        self.assertFalse(rep["already"])
        self.assertEqual(rep["mode"], "replace")
        n = tg.send(self.out, self.lib, self.pid, mode="new_set")
        self.assertFalse(n["already"])
        self.assertEqual(len(self.fake.f.sets), 2, "a second set exists")
        self.assertNotEqual(n["sets"][0]["name"], rep["sets"][0]["name"])
        pack = next(p for p in self.lib.snapshot()["packs"] if p["id"] == self.pid)
        self.assertEqual([e["mode"] for e in pack["telegram"]["exports"]], ["once", "replace", "new_set"], "every send is recorded, with how")

    def test_an_unknown_mode_is_refused_and_sends_nothing(self):
        self.add_static("One", "😀")
        self.connect()
        with self.assertRaises(tg.TelegramError):
            tg.send(self.out, self.lib, self.pid, mode="twice")
        self.assertEqual(self.fake.f.sets, {})


class QueuedSend(LibFixture):
    """The send queue: refused at once when that is possible, one worker sends in order, the state file reports it."""
    def test_unconnected_send_fails_on_the_queue_without_touching_the_network(self):
        self.add_static("One", "😀")
        st = tg.enqueue_send(self.out, self.pid, lambda: tg.send(self.out, self.lib, self.pid, None, CFG), "tester")
        self.assertEqual(st["state"], "queued")
        self.assertTrue(tg.wait(timeout=30))
        done = tg.send_state(self.out, st["id"])
        self.assertEqual(done["state"], "failed")
        self.assertIn("not connected", done["error"])
        self.assertEqual(self.fake.f.sets, {})

    def test_full_send_against_the_fake(self):
        self.connect()
        self.add_static("One", "😀")
        st = tg.enqueue_send(self.out, self.pid, lambda: tg.send(self.out, self.lib, self.pid, None, CFG), "tester")
        self.assertTrue(tg.wait(timeout=60))
        done = tg.send_state(self.out, st["id"])
        self.assertEqual(done["state"], "done", done)
        self.assertEqual(done["result"]["sets"][0]["link"], "https://t.me/addstickers/teddy_pack_by_stickerloombot")

    def test_unknown_send_is_none(self):
        self.assertIsNone(tg.send_state(self.out, "T0"))
        self.assertIsNone(tg.send_state(self.out, "bogus"))


class ApiStdlib(LibFixture):
    """The same queue contract on serve --stdlib."""
    def setUp(self):
        super().setUp()
        self.add_static("One", "😀")
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, block=False, stdlib=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        super().tearDown()

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse(); raw = r.read(); h.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def test_send_is_queued_and_polled(self):
        self.assertEqual(self.req("POST", f"/api/packs/{self.pid}/telegram", {})[0], 409)   # unconnected: refused at once
        s, st = self.req("POST", "/api/telegram/config", {"token": TOKEN, "user_id": USER})
        self.assertEqual(s, 200, st)
        s, q = self.req("POST", f"/api/packs/{self.pid}/telegram", {})
        self.assertEqual(s, 202, q)
        done = poll_send(self.req, q["id"])
        self.assertEqual(done["state"], "done", done)
        self.assertEqual(self.req("GET", "/api/telegram/sends/T0")[0], 404)
