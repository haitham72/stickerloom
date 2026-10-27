"""Names proposed after looking at the pictures (vision/naming.py) and applied by the person (pipeline.set_titles): files, keys and search fields never change."""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from stickerloom.flow import pipeline as pl
from stickerloom.vision import consent, naming, transcribe


def make(out: Path, n: int = 3) -> int:
    gid = 7
    d = pl.gen_dir(out, gid)
    (d / "slices").mkdir(parents=True)
    stickers = []
    for i in range(1, n + 1):
        png = f"slices/img-x-{i}.png"
        (d / png).write_bytes(b"png-%d" % i)
        stickers.append({"index": i, "key": f"eid_greeting_{i}", "name": f"img-x-{i}", "status": "READY", "png": png, "tags": [], "emoji": ["x"],
                         "review": {"still": "PENDING", "anim": "NONE"}, "history": [], "anim_status": "NOT_REQUESTED", "anim_reason": None, "anim_metrics": {}, "webm": None, "reason": None, "report": [], "metrics": {},
                         "caption": {"text": f"picture number {i}", "text_visible": None, "model": "fake", "version": transcribe.CAPTION_VERSION,
                                     "png_sha": transcribe._sha(b"png-%d" % i), "ts": 1}})
    res = {"generation_id": f"G{gid:03d}", "number": gid, "stage": "sliced", "grid": [1, n], "stickers": stickers, "reviews": {"plan": None, "video_sheet": {}, "pack": None},
           "video_sheets": [], "verify": {}, "source": {}}
    (d / "result.json").write_text(json.dumps(res), encoding="utf-8")
    return gid


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.out = Path(self.td.name)
        self.gid = make(self.out)

    def tearDown(self):
        self.td.cleanup()

    def test_only_a_name_that_does_not_fit_is_proposed_and_nothing_changes_yet(self):
        ask = lambda items: {1: {"fits": True}, 2: {"fits": False, "name": "Happy boy holding a heart"}, 3: {"fits": False, "name": "eid greeting 3"}}
        rows = naming.propose(self.out, self.gid, allowed=True, ask=ask)
        self.assertEqual([(r["index"], r["fits"], r["name"]) for r in rows], [(1, True, None), (2, False, "Happy boy holding a heart"), (3, True, None)])   # 3: the "new" name is the old one
        self.assertEqual(naming.pending(self.out, self.gid), {2: "Happy boy holding a heart"})
        res = pl.read_result(self.out, self.gid)
        self.assertIsNone(res["stickers"][1].get("title"))                               # a proposal is not a rename
        self.assertEqual(res["stickers"][1]["name"], "img-x-2")

    def test_applying_sets_the_title_and_writes_a_history_line_and_leaves_names_and_keys(self):
        naming.propose(self.out, self.gid, allowed=True, ask=lambda items: {2: {"fits": False, "name": "Happy boy holding a heart"}})
        done = naming.apply(self.out, self.gid)
        self.assertEqual(done, {2: "Happy boy holding a heart"})
        s = pl.read_result(self.out, self.gid)["stickers"][1]
        self.assertEqual((s["title"], s["key"], s["name"]), ("Happy boy holding a heart", "eid_greeting_2", "img-x-2"))
        self.assertNotIn("title_proposal", s)
        h = s["history"][-1]
        self.assertEqual((h["stage"], h["actor"], h["decision"], h["reason"]), ("naming", "human", "TITLE", "Happy boy holding a heart"))
        self.assertEqual(naming.apply(self.out, self.gid), {})                            # nothing left to apply

    def test_no_consent_means_no_look_unless_every_caption_is_stored(self):
        res = pl.read_result(self.out, self.gid)
        res["stickers"][0].pop("caption")
        pl.write_result(self.out, self.gid, res)
        with self.assertRaises(consent.ConsentRequired):
            naming.propose(self.out, self.gid, allowed=None, ask=lambda i: None)

    def test_a_model_that_answers_nothing_proposes_nothing(self):
        rows = naming.propose(self.out, self.gid, allowed=True, ask=lambda items: None)
        self.assertTrue(all(r["fits"] for r in rows))
        self.assertEqual(naming.pending(self.out, self.gid), {})

    def test_two_edits_at_once_both_survive(self):
        """The review finding F2: read-modify-write of result.json used to interleave between two request threads."""
        from stickerloom.flow import gates
        gates.review(self.out, self.gid, "plan", "APPROVE", note="x")
        errors = []

        def one(i):
            try:
                gates.review(self.out, self.gid, "still", "APPROVE", i, "t")
            except Exception as e:
                errors.append(e)
        ts = [threading.Thread(target=one, args=(i,)) for i in (1, 2, 3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        got = [s["review"]["still"] for s in pl.read_result(self.out, self.gid)["stickers"]]
        self.assertEqual(got, ["APPROVED"] * 3)


if __name__ == "__main__":
    unittest.main()
