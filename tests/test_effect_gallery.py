"""The particles of a sticker (flow/effects.for_sticker, counts_for_pack, GET /api/packs/{id}/stickers/{sid}/particles and /api/packs/{id}/particles): what was created for a
sticker and what was saved from it, read from the effects and the library. Effects are made through the real flow functions (a simulated render, a clip cut into cells); no provider."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.flow import effects as fx
from stickerloom.media.library import Library
from tests.test_effect_video import make_clip
from tests.test_effects_flow import sticker_png

CFG = EngineConfig()


def build(out: Path, lib: Library):
    """A pack of three stickers; E001 simulated (renders for the first and the third), E002 video (a 2x2 clip for the two strawberries)."""
    pack = lib.create_pack("Fruits")
    s = [lib.add_bytes(pack["id"], sticker_png((220 - 40 * i, 40 + 30 * i, 60)), "png", n, "static", e)["id"]
         for i, (n, e) in enumerate([("Strawberry happy", "🍓"), ("Strawberry sad", "🍓"), ("Heart", "❤")])]
    fx.create(out, lib, pack_id=pack["id"], sticker_ids="all", mode="sim")
    fx.analyse(out, "E001")
    r1 = fx.sim_render(out, lib, "E001", s[0], CFG, {"gravity": 1.5})
    r3 = fx.sim_render(out, lib, "E001", s[2], CFG, {})
    fx.create(out, lib, pack_id=pack["id"], sticker_ids=[s[0], s[1]], mode="video")
    e2 = fx.analyse(out, "E002")
    gid = e2["groups"][0]["id"]
    mp4 = out / "jobs" / "J001" / "result.mp4"
    mp4.parent.mkdir(parents=True)
    make_clip(mp4, key=(0, 0, 255))
    fx.set_pieces(out, "E002", gid, elements=["strawberries", "tiny seeds"])
    fx.on_video_done(out, "E002", gid, {"id": "J001", "result": {"file": "jobs/J001/result.mp4"}, "cost": 4.5}, CFG)
    return pack, s, r1, r3, gid


class GalleryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):                                    # the renders are slow: build once, every test works on its own copy
        cls.base = Path(tempfile.mkdtemp())
        cls.pack, cls.s, cls.r1, cls.r3, cls.gid = build(cls.base, Library(cls.base))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self):
        self.out = Path(tempfile.mkdtemp()) / "out"
        shutil.copytree(self.base, self.out)
        self.lib = Library(self.out)

    def tearDown(self):
        shutil.rmtree(self.out.parent, ignore_errors=True)

    def g(self, i):
        return fx.for_sticker(self.out, self.lib, self.pack["id"], self.s[i])

    def test_a_sticker_lists_its_own_simulated_result_and_every_cell_of_its_groups_video(self):
        g = self.g(0)
        self.assertEqual((g["sticker"], g["can_make"], g["saved"], g["effects"]), (self.s[0], True, [], ["E002", "E001"]))
        sim = [i for i in g["created"] if i["mode"] == "sim"]
        vid = [i for i in g["created"] if i["mode"] == "video"]
        self.assertEqual([(i["effect"], i["result"], i["shared"]) for i in sim], [("E001", self.r1["id"], False)])
        self.assertEqual(sorted(i["cell"] for i in vid), [1, 2, 3, 4])
        self.assertTrue(all(i["shared"] and i["effect"] == "E002" for i in vid))
        self.assertEqual(sum(1 for i in vid if i["assigned"]), 1, "exactly one cell is the one add_to_pack gives this sticker")
        self.assertEqual([i["cell"] for i in vid if i["assigned"]], [1])
        one = sim[0]
        self.assertEqual((one["status"], one["usable"], one["missing"], one["added_to"]), ("READY", True, False, None))
        self.assertEqual(one["url"], f"/out/effects/E001/{self.r1['file']}")
        self.assertGreater(one["bytes"], 1000)
        self.assertIsInstance(one["warnings"], list)
        self.assertTrue((self.out / one["url"][len("/out/"):]).is_file())
        # the second strawberry shares the video (its assigned cell is the second), has no simulated result of its own
        g2 = self.g(1)
        self.assertEqual([i["mode"] for i in g2["created"]], ["video"] * 4)
        self.assertEqual([i["cell"] for i in g2["created"] if i["assigned"]], [2])
        # the heart: its simulated result only, never the strawberries' video
        g3 = self.g(2)
        self.assertEqual([(i["effect"], i["mode"]) for i in g3["created"]], [("E001", "sim")])

    def test_newest_first(self):
        times = [i["created"] for i in self.g(0)["created"]]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_saved_stickers_are_listed_with_their_pack_and_added_to_is_the_librarys_word(self):
        other = self.lib.create_pack("Elsewhere")
        fx.add_to_pack(self.out, self.lib, "E001", [self.r1["id"]], pack_id=other["id"])
        g = self.g(0)
        self.assertEqual(len(g["saved"]), 1)
        sv = g["saved"][0]
        self.assertEqual((sv["pack_id"], sv["pack"], sv["effect"], sv["result"], sv["source_sticker"], sv["missing"]), (other["id"], "Elsewhere", "E001", self.r1["id"], self.s[0], False))
        self.assertTrue(sv["url"].startswith("/lib/") and (self.lib.files / sv["file"]).is_file())
        made = next(i for i in g["created"] if i["result"] == self.r1["id"] and i["effect"] == "E001")
        self.assertEqual(made["added_to"], other["id"])
        self.assertEqual(self.g(2)["saved"], [], "the heart was not saved")
        # the saved sticker belongs to the heart's neighbour only
        self.assertEqual(self.g(1)["saved"], [])

    def test_a_stale_added_to_flag_is_corrected_when_the_saved_sticker_is_deleted(self):
        fx.add_to_pack(self.out, self.lib, "E001", [self.r1["id"]])
        self.assertEqual(fx.read(self.out, "E001")["results"][0]["added_to"], self.pack["id"], "the effect keeps its own flag")
        saved = self.g(0)["saved"][0]
        self.lib.delete_sticker(saved["pack_id"], saved["sticker_id"])
        g = self.g(0)
        self.assertEqual(g["saved"], [])
        made = next(i for i in g["created"] if i["effect"] == "E001" and i["result"] == self.r1["id"])
        self.assertIsNone(made["added_to"], "the flag in effect.json is stale: the library has no such sticker any more")
        self.assertEqual(fx.read(self.out, "E001")["results"][0]["added_to"], self.pack["id"], "a read never writes")

    def test_adding_one_cell_for_one_sticker_does_not_fill_the_pack_with_its_group(self):
        cell = next(i for i in self.g(0)["created"] if i["mode"] == "video" and i["assigned"])
        res = fx.add_to_pack(self.out, self.lib, cell["effect"], [cell["result"]], pack_id=self.pack["id"], sticker_ids=[self.s[0]])
        self.assertEqual([a["of"] for a in res["added"]], [self.s[0]])
        self.assertEqual(len(self.g(0)["saved"]), 1)
        self.assertEqual(self.g(1)["saved"], [], "the group's other sticker got nothing")
        self.assertEqual(next(i for i in self.g(0)["created"] if i["result"] == cell["result"])["added_to"], self.pack["id"])
        self.assertIsNone(next(i for i in self.g(1)["created"] if i["result"] == cell["result"])["added_to"], "added for the first sticker, not for the second")

    def test_a_failed_result_and_a_missing_file_are_listed_and_flagged(self):
        e = fx.read(self.out, "E001")
        e["results"].append({"id": "R090", "mode": "sim", "group": e["groups"][0]["id"], "sticker_id": self.s[0], "file": None, "bytes": 0, "status": "FAILED", "checks": [], "warnings": [], "blocks": ["size_budget"]})
        gone = next(r for r in e["results"] if r["id"] == self.r1["id"])
        fx._write(self.out, e)
        (self.out / "effects" / "E001" / gone["file"]).unlink()
        g = self.g(0)
        bad = next(i for i in g["created"] if i["result"] == "R090")
        self.assertEqual((bad["status"], bad["usable"], bad["missing"], bad["url"], bad["blocks"]), ("FAILED", False, True, None, ["size_budget"]))
        lost = next(i for i in g["created"] if i["effect"] == "E001" and i["result"] == self.r1["id"])
        self.assertEqual((lost["status"], lost["usable"], lost["missing"]), ("READY", False, True))
        counts = fx.counts_for_pack(self.out, self.lib, self.pack["id"])
        self.assertEqual(counts[self.s[0]]["created"], 4, "only the video's four usable cells count: not the failed one, not the file that is gone")

    def test_the_pack_counts_come_from_one_pass(self):
        counts = fx.counts_for_pack(self.out, self.lib, self.pack["id"])
        self.assertEqual(counts, {self.s[0]: {"created": 5, "saved": 0}, self.s[1]: {"created": 4, "saved": 0}, self.s[2]: {"created": 1, "saved": 0}})
        fx.add_to_pack(self.out, self.lib, "E001", [self.r3["id"]])
        self.assertEqual(fx.counts_for_pack(self.out, self.lib, self.pack["id"])[self.s[2]], {"created": 1, "saved": 1})
        lone = self.lib.create_pack("Empty")
        self.assertEqual(fx.counts_for_pack(self.out, self.lib, lone["id"]), {})

    def test_a_sticker_without_particles_is_empty_and_unknown_ones_are_404(self):
        extra = self.lib.add_bytes(self.pack["id"], sticker_png((9, 9, 9)), "png", "Plain", "static", "🙂")["id"]
        g = fx.for_sticker(self.out, self.lib, self.pack["id"], extra)
        self.assertEqual((g["created"], g["saved"], g["effects"], g["can_make"]), ([], [], [], True))
        for pack, sid in (("nope", extra), (self.pack["id"], "zzzz")):
            with self.assertRaises(fx.EffectError) as cm:
                fx.for_sticker(self.out, self.lib, pack, sid)
            self.assertEqual(cm.exception.code, 404)

    def test_a_sticker_moved_to_another_pack_keeps_its_particles(self):
        other = self.lib.create_pack("Moved to")
        self.lib.move_sticker(self.pack["id"], self.s[2], other["id"])
        self.assertEqual(len(fx.for_sticker(self.out, self.lib, other["id"], self.s[2])["created"]), 1)
        self.assertNotIn(self.s[2], fx.counts_for_pack(self.out, self.lib, self.pack["id"]))
        self.assertEqual(fx.counts_for_pack(self.out, self.lib, other["id"])[self.s[2]]["created"], 1)

    def test_reading_writes_nothing(self):
        before = {p: p.stat().st_mtime_ns for p in (self.out / "effects").rglob("*") if p.is_file()} | {self.lib.db_path: self.lib.db_path.stat().st_mtime_ns}
        self.g(0)
        fx.counts_for_pack(self.out, self.lib, self.pack["id"])
        after = {p: p.stat().st_mtime_ns for p in (self.out / "effects").rglob("*") if p.is_file()} | {self.lib.db_path: self.lib.db_path.stat().st_mtime_ns}
        self.assertEqual(before, after)


class GalleryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "in").mkdir(parents=True)
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, cfg=EngineConfig(), block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pack, cls.s, cls.r1, cls.r3, cls.gid = build(cls.c.out, cls.c.lib)
        cls.boss, t_boss = cls.c.users.create("Boss", "owner", True)           # once accounts exist every call needs a token
        cls.member, t_mia = cls.c.users.create("Mia")
        cls.O, cls.M = ({"Authorization": f"Bearer {t}"} for t in (t_boss, t_mia))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.c.release_writer()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json", **(self.O if headers is None else headers)})
        r = h.getresponse()
        data = r.read()
        h.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def test_the_sticker_route_returns_the_gallery_and_its_files_are_served(self):
        s, j = self.req("GET", f"/api/packs/{self.pack['id']}/stickers/{self.s[0]}/particles")
        self.assertEqual(s, 200, j)
        self.assertEqual((j["sticker"], j["can_make"], len(j["created"]), j["saved"]), (self.s[0], True, 5, []))
        self.assertEqual(set(j["created"][0]), {"effect", "result", "mode", "status", "bytes", "warnings", "blocks", "url", "missing", "added_to", "created", "shared", "group", "usable"} | ({"cell", "assigned"} if j["created"][0]["shared"] else set()))
        sim = next(i for i in j["created"] if i["mode"] == "sim")
        st, data = self.req("GET", sim["url"])
        self.assertEqual((st, bytes(data[:4])), (200, b"\x1aE\xdf\xa3"), "a WebM (EBML header)")
        self.assertEqual(self.req("GET", f"/api/packs/nope/stickers/{self.s[0]}/particles")[0], 404)
        self.assertEqual(self.req("GET", f"/api/packs/{self.pack['id']}/stickers/zzzz/particles")[0], 404)

    def test_saving_through_the_route_shows_up_in_the_gallery_and_the_counts(self):
        sim = next(i for i in self.req("GET", f"/api/packs/{self.pack['id']}/stickers/{self.s[2]}/particles")[1]["created"] if i["mode"] == "sim")
        s, a = self.req("POST", f"/api/effects/{sim['effect']}/add", {"results": [sim["result"]], "pack_id": self.pack["id"], "sticker_ids": [self.s[2]]})
        self.assertEqual(s, 200, a)
        j = self.req("GET", f"/api/packs/{self.pack['id']}/stickers/{self.s[2]}/particles")[1]
        self.assertEqual([x["pack_id"] for x in j["saved"]], [self.pack["id"]])
        self.assertEqual(j["created"][0]["added_to"], self.pack["id"])
        s, studio = self.req("GET", f"/api/packs/{self.pack['id']}/particles")
        counts = studio["counts"]          # the pack's studio answer is {pack_id, sets, bursts, counts}; `counts` is the old per-sticker map
        self.assertEqual((s, counts[self.s[2]]), (200, {"created": 1, "saved": 1, "sets": 0}))
        self.assertEqual(counts[self.s[0]]["created"], 2, "the badge counts versions: each run once, never once per slice")
        self.assertEqual(self.req("GET", "/api/packs/nope/particles")[0], 404)

    def test_a_member_gets_an_empty_answer_not_an_error(self):
        status, preview = self.req("GET", f"/api/packs/{self.pack['id']}/stickers/{self.s[0]}/particle-preview", headers=self.M)
        self.assertEqual((status, preview), (200, {"set": None, "url": None}))
        s, j = self.req("GET", f"/api/packs/{self.pack['id']}/stickers/{self.s[0]}/particles", headers=self.M)
        self.assertEqual((s, j["created"], j["saved"], j["can_make"]), (200, [], [], False))
        self.assertEqual(self.req("GET", f"/api/packs/{self.pack['id']}/particles", headers=self.M), (200, {}))
        self.assertEqual(self.req("GET", f"/api/packs/{self.pack['id']}/telegram", headers=self.M)[0], 403, "the rest of the pack surface stays owner only")
        self.assertEqual(self.req("GET", "/out/effects/E001/results/R001.webm", headers=self.M)[0], 403, "and so do the files")


if __name__ == "__main__":
    unittest.main()
