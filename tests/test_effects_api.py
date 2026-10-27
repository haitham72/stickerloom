"""The /api/effects contract through the real server: create (async analysis), edit the pieces, estimate and start a text-only video (price first, `go` to spend), the simulated preview and
render, add to the pack. The provider is a fake CLI; nothing is spent."""
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.generation import higgsfield
from tests.test_effects_flow import sticker_png
from tests.test_live import FakeCLI


class EffectsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "in" / "Images_gen").mkdir(parents=True)
        (cls.tmp / "in" / "videos_gen").mkdir(parents=True)
        cls.cli = FakeCLI(cost=4.5)
        cls.patches = [mock.patch.object(higgsfield, "RUN", cls.cli), mock.patch.object(higgsfield, "binary", lambda: ("fake-hf", True))]
        [p.start() for p in cls.patches]
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, cfg=EngineConfig(), block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pack = cls.c.lib.create_pack("Fruits")
        cls.sid = cls.c.lib.add_bytes(cls.pack["id"], sticker_png(), "png", "Strawberry", "static", "🍓")["id"]
        cls.sid2 = cls.c.lib.add_bytes(cls.pack["id"], sticker_png((30, 90, 200)), "png", "Heart", "static", "❤")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        [p.stop() for p in cls.patches]
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse(); data = r.read(); h.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def wait_ready(self, eid, want=("READY",)):
        end = time.time() + 60
        while time.time() < end:
            s, j = self.req("GET", f"/api/effects/{eid}")
            if j.get("status") in want:
                return j
            time.sleep(0.2)
        self.fail("effect never became " + str(want))

    def test_the_whole_contract(self):
        s, j = self.req("POST", "/api/effects", {"pack_id": self.pack["id"], "sticker_ids": "all", "mode": "sim"})
        self.assertEqual(s, 202, j)
        eid = j["id"]
        e = self.wait_ready(eid)
        self.assertEqual({g["subject"] for g in e["groups"]}, {"strawberry", "heart"})
        self.assertEqual(e["analysed_by"], "table", "no consent was given, so no picture went to a model")
        self.assertIn(eid, [r["id"] for r in self.req("GET", "/api/effects")[1]["effects"]])
        gid = next(g["id"] for g in e["groups"] if g["subject"] == "strawberry")

        s, e2 = self.req("POST", f"/api/effects/{eid}/plan", {"group": gid, "elements": ["red berries", "tiny seeds"]})
        self.assertEqual((s, next(g for g in e2["groups"] if g["id"] == gid)["by"]), (200, "you"))
        s, bad = self.req("POST", f"/api/effects/{eid}/plan", {"group": gid, "elements": ["a sticker of a berry"]})
        self.assertEqual(s, 400)
        self.assertIn("sticker", bad["error"])

        s, pv = self.req("POST", f"/api/effects/{eid}/preview", {"sticker_id": self.sid, "params": {"gravity": 1.4, "vortex": 0.6}})
        self.assertEqual(s, 200, pv)
        s, f = self.req("GET", pv["url"])
        self.assertEqual((s, bytes(f[:4]) if isinstance(f, bytes) else None), (200, b"RIFF"))
        self.assertEqual(self.req("POST", f"/api/effects/{eid}/preview", {"sticker_id": self.sid, "params": {"bogus": 1}})[0], 400)

        s, r = self.req("POST", f"/api/effects/{eid}/render", {"sticker_id": self.sid, "params": {"gravity": 1.4}})
        self.assertEqual((s, r["status"]), (200, "READY"), r)
        s, a = self.req("POST", f"/api/effects/{eid}/add", {"results": [r["id"]]})
        self.assertEqual(s, 200, a)
        pack = next(p for p in self.c.lib.snapshot()["packs"] if p["id"] == self.pack["id"])
        self.assertTrue(any((x.get("source") or {}).get("effect") == eid and x["emoji"] == "🍓" for x in pack["stickers"]))

    def test_a_paid_video_shows_its_price_and_starts_only_with_go(self):
        s, j = self.req("POST", "/api/effects", {"pack_id": self.pack["id"], "sticker_ids": [self.sid], "mode": "video", "grid": "2x2"})
        e = self.wait_ready(j["id"])
        gid = e["groups"][0]["id"]
        s, est = self.req("POST", f"/api/effects/{j['id']}/estimate", {"group": gid})
        self.assertEqual((s, est["credits"], est["cells"], est["key"]), (200, 4.5, 4, "blue"))
        self.assertIn("only the blue background", est["prompt"]) if "blue" in est["prompt"] else self.assertIn("only the green background", est["prompt"])
        before = len([c for c in self.cli.calls if c[:2] == ["generate", "create"]])
        s, no = self.req("POST", f"/api/effects/{j['id']}/video", {"group": gid})
        self.assertEqual(s, 409, "no go-ahead, no spending")
        self.assertEqual(no["estimate"]["credits"], 4.5)
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), before)
        with mock.patch.object(type(self.c), "fulfil_async", lambda self_, jid, after=None: None):         # the job is created and handed over; running it is the queue's business
            s, ok = self.req("POST", f"/api/effects/{j['id']}/video", {"group": gid, "go": True})
        self.assertEqual((s, ok["estimate"]), (202, 4.5), ok)
        e = self.req("GET", f"/api/effects/{j['id']}")[1]
        self.assertEqual((e["status"], e["video"][gid]["job"]), ("VIDEO_REQUESTED", ok["job"]))

    def test_errors_are_json_with_the_right_codes(self):
        self.assertEqual(self.req("GET", "/api/effects/E999")[0], 404)
        self.assertEqual(self.req("POST", "/api/effects", {"pack_id": "nope"})[0], 404)
        self.assertEqual(self.req("POST", "/api/effects", {"pack_id": self.pack["id"], "mode": "x"})[0], 400)

    def test_the_chat_tools_open_the_same_effect_as_the_route(self):
        from stickerloom.agent.tools import ConsoleTools, ToolError
        tools = ConsoleTools(self.c)
        self.assertIn(self.pack["id"], [p["id"] for p in tools.packs()])
        r = tools.effects_start(self.pack["id"], allowed=False)
        self.assertEqual((r["pack_name"], r["count"]), ("Fruits", 2))
        for _ in range(100):
            code, rec = self.req("GET", f"/api/effects/{r['id']}")
            if rec.get("status") in ("READY", "ERROR"):
                break
            time.sleep(0.1)
        self.assertEqual(rec["status"], "READY")
        member = ConsoleTools(self.c, {"id": "u2", "role": "member"})
        self.assertEqual(member.packs(), [])
        with self.assertRaises(ToolError):
            member.effects_start(self.pack["id"])


if __name__ == "__main__":
    unittest.main()
