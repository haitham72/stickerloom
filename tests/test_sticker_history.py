"""The per-sticker generation history of a batch (flow/sticker_history.py, GET /api/generations/<id>/history): one summary per sticker, its decisions grouped by stage,
newest first, trimmed for the page; and the route that serves it (a member sees only their own batch)."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.runtime import cache as cachemod
from stickerloom.flow import sticker_history


def line(ts, stage, actor, decision, reason=None, detail=None):
    return {"ts": ts, "stage": stage, "actor": actor, "decision": decision, "reason": reason, "ref": None, "detail": detail}


def batch():
    st = []
    for i in (1, 2):
        h = [line(100 + i, "sliced", "python", "PASS"), line(110 + i, "still", "human", "APPROVE"), line(120 + i, "video_sheet", "python", "PASS")]
        h += [line(130 + k, "appearance", "human", "PASS", detail={"outline": k}) for k in range(i * 30)]          # S1: 30 lines, S2: 60 lines of stroke / trim snapshots
        blocked = line(300, "video", "python", "BLOCK", "inside_slot", {"check": "inside_slot", "value": 0.4, "limit": 0.2, "note": "leaves its slot", "data": {"big": "x" * 5000}, "nested": {"a": 1}, "warnings": []})
        blocked["ref"] = "A1"
        h.append(blocked)
        h.append(line(310, "anim", "human", "REJECT", "dropped from the set"))
        st.append({"index": i, "key": f"cat_{i}", "name": f"img-001-cat-cat_{i}", "emoji": ["x"], "status": "READY", "anim_status": "READY",
                   "png": f"slices/img-001-cat-cat_{i}.png", "review": {"still": "APPROVED", "anim": "REJECTED"}, "history": h})
    st.append({"index": 3, "key": "cat_3", "status": "FAILED", "anim_status": "NOT_REQUESTED", "png": None, "review": {"still": "PENDING", "anim": "PENDING"}})   # no history at all
    return {"id": 1, "stickers": st}


class SummaryTests(unittest.TestCase):
    def test_every_sticker_is_summarised_with_its_stages_in_the_order_they_happened(self):
        d = sticker_history.batch_history(batch(), "G001")
        self.assertEqual((d["generation_id"], [s["id"] for s in d["stickers"]]), ("G001", ["G001/S1", "G001/S2", "G001/S3"]))
        s1, s2, s3 = d["stickers"]
        self.assertEqual([g["stage"] for g in s1["stages"]], ["sliced", "still", "video_sheet", "appearance", "video", "anim"])
        self.assertEqual((s1["lines"], s2["lines"], s3["lines"]), (3 + 30 + 2, 3 + 60 + 2, 0))
        self.assertEqual(s3["stages"], [])
        self.assertEqual(s1["last"], {"ts": 310, "stage": "anim", "actor": "human", "decision": "REJECT", "reason": "dropped from the set"})        # the summary line: the latest decision
        g = {x["stage"]: x for x in s2["stages"]}
        self.assertEqual((g["appearance"]["count"], g["still"]["count"]), (60, 1))
        self.assertEqual(g["appearance"]["last"]["ts"], 189)

    def test_lines_are_newest_first_trimmed_and_capped(self):
        d = sticker_history.batch_history(batch(), "G001", per_sticker=40)
        s2 = d["stickers"][1]
        app = next(g for g in s2["stages"] if g["stage"] == "appearance")
        self.assertEqual((len(app["lines"]), app["count"]), (38, 60))                            # 40 newest lines of 65: video + anim + the 38 newest strokes; the count stays the truth
        self.assertEqual([l["ts"] for l in app["lines"]], sorted((l["ts"] for l in app["lines"]), reverse=True))
        self.assertEqual(sum(len(g["lines"]) for g in s2["stages"]), 40)                         # the newest 40 of 65 are kept, `lines` still says 65
        self.assertEqual(s2["lines"], 65)
        self.assertEqual(s2["shown"], 40)
        blocked = next(g for g in d["stickers"][0]["stages"] if g["stage"] == "video")["lines"][0]
        self.assertEqual(blocked["detail"], {"check": "inside_slot", "value": 0.4, "limit": 0.2, "note": "leaves its slot"})   # a BLOCK keeps its reason, never its raw data
        self.assertEqual(next(g for g in d["stickers"][0]["stages"] if g["stage"] == "appearance")["lines"][0]["detail"], {"outline": 29})    # a small fact is kept
        self.assertEqual(blocked["ref"], "A1")
        self.assertLess(len(json.dumps(d)), 20000)

    def test_facts_keep_the_small_readable_part_of_a_detail(self):
        f = sticker_history._facts
        self.assertEqual(f({"outline_px": 8, "erode_px": 0, "warnings": [], "override": ["cross_slot"], "data": {"x": 1}, "deep": {"a": 1}, "gone": None}),
                         {"outline_px": 8, "erode_px": 0, "override": ["cross_slot"]})
        self.assertEqual(f({"note": "x" * 500})["note"], "x" * 120)
        self.assertEqual(f({"many": list(range(20))})["many"], list(range(6)))
        self.assertEqual(len(f({f"k{i}": i for i in range(20)})), 6)
        self.assertEqual((f(None), f("text"), f([1])), ({}, {}, {}))

    def test_one_sticker_and_unknown_indexes(self):
        d = sticker_history.batch_history(batch(), "G001", index=2)
        self.assertEqual([s["index"] for s in d["stickers"]], [2])
        self.assertEqual(sticker_history.batch_history(batch(), "G001", index=9)["stickers"], [])

    def test_a_result_with_no_stickers_or_odd_lines_never_raises(self):
        self.assertEqual(sticker_history.batch_history({"stickers": []}, "G002")["stickers"], [])
        odd = {"stickers": [{"index": 1, "key": "a", "status": "READY", "history": [None, {"stage": "still"}, {"ts": "x", "stage": 5, "decision": None}]}]}
        out = sticker_history.batch_history(odd, "G003")["stickers"][0]
        self.assertEqual(out["lines"], 2)                                                          # the None is skipped, the rest is kept as it is


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from stickerloom.console.server import serve
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "in").mkdir()
        cls.mem = cachemod.Cache(force_memory=True)
        cls.patch = mock.patch.object(cachemod, "default", lambda: cls.mem)
        cls.patch.start()
        out = cls.tmp / "out"
        cls.srv, cls.c = serve(out, cls.tmp / "in", 0, block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.mine, cls.t_mine = cls.c.users.create("Mine")
        cls.theirs, cls.t_theirs = cls.c.users.create("Theirs")
        for gid, owner in ((1, cls.mine["id"]), (2, cls.theirs["id"])):
            d = out / f"G{gid:03d}"
            d.mkdir(parents=True)
            res = batch()
            res.update(id=gid, owner=owner, prompt="cat", grid=[3, 3])
            (d / "result.json").write_text(json.dumps(res), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.c.release_writer()
        cls.srv.server_close()
        cls.patch.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, token=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h.request("GET", path, headers={"Authorization": f"Bearer {token}"} if token else {"Sec-Fetch-Site": "same-origin"})
        r = h.getresponse()
        body = json.loads(r.read())
        h.close()
        return r.status, body

    def test_the_owner_reads_the_history_of_any_batch_and_one_sticker_of_it(self):
        s, d = self.get("/api/generations/1/history")
        self.assertEqual((s, d["generation_id"], len(d["stickers"])), (200, "G001", 3))
        s, d = self.get("/api/generations/1/history?index=2")
        self.assertEqual((s, [x["index"] for x in d["stickers"]]), (200, [2]))
        self.assertEqual(self.get("/api/generations/99/history")[0], 404)
        self.assertEqual(self.get("/api/generations/1/history?index=x")[0], 400)

    def test_a_stranger_gets_a_404_not_a_403(self):
        self.assertEqual(self.get("/api/generations/1/history", self.t_mine)[0], 200)
        self.assertEqual(self.get("/api/generations/2/history", self.t_mine)[0], 404)


if __name__ == "__main__":
    unittest.main()
