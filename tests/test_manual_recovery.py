"""A manual download completes its failed job instead of starting an unrelated batch. Fake jobs shaped like
J022-J025 (FAILED with their Higgsfield task ids); Higgsfield is never called."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2

from stickerloom.flow import imports as im
from stickerloom.flow import pipeline as pl
from stickerloom.generation import jobs, tasks
from stickerloom.engine.config import EngineConfig
from tests import synth

T1 = "b16dc424-22fe-4ad2-8242-e8a36f10209b"
T2 = "c27ed535-33ff-5be3-9353-f1a47f21310c"


def fail_sheet_job(out, inp, prompt, ticket, user="local"):
    t = tasks.reserve(out, inp, prompt, ai=False)
    j = jobs.create(out, "sheet", task=t["id"], request={"user": user, "label": prompt, "model": "nano-banana-2"})
    jobs.claim(out, j["id"], ticket)
    return jobs.fail(out, j["id"], "certificate verify failed: [ASN1: NOT_ENOUGH_DATA]"), t


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.inp = root / "out", root / "inputs"
        self.c = SimpleNamespace(out=self.out, inp=self.inp, lock=threading.Lock(), cfg=EngineConfig(min_sheet_px=256), pace=0)
        self.user = {"id": "local"}
        self.data = cv2.imencode(".png", cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))[1].tobytes()

    def tearDown(self):
        with self.c.lock:
            pass
        import shutil
        shutil.rmtree(self.tmp.name, ignore_errors=True)            # a background ticket draft may still be writing

    def wait(self):
        with self.c.lock:
            pass

    def ledger(self):
        f = self.out / "model_calls.jsonl"
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    def test_single_match_links_automatically(self):
        lj, t = fail_sheet_job(self.out, self.inp, "teddy bear for school", T1)
        before = self.ledger()
        code, r = im.import_file(self.c, self.user, f"hf_20261004_033516_{T1}.png", self.data)
        self.wait()
        self.assertEqual(code, 202)
        self.assertEqual((r["job"], r["recovered"]), (lj["id"], True))
        done = jobs.read(self.out, lj["id"])
        self.assertEqual((done["status"], done["recovered_by"]), ("DONE", "manual import"))
        self.assertEqual(done["generation"], f"G{r['id']:03d}")
        res = pl.read_result(self.out, r["id"])
        self.assertEqual(res["task_id"], t["id"])                       # the job's own plan (cells, tags, emoji)
        t2 = tasks.read_task(self.out, t["id"])
        self.assertEqual(t2["external_task_id"], T1)                    # the batch carries the job's provider ticket
        self.assertIn(f"G{r['id']:03d}", t2["generations"])              # the task carries the batch
        line = next(s for s in res["stickers"][0]["history"] if s["decision"] == "RECOVERED")
        self.assertEqual((line["actor"], line["reason"]), ("human", f"recovered from a manual download of task {T1}"))
        self.assertEqual(self.ledger(), before)                         # no second charge, no invented ledger line
        self.assertEqual(len(jobs.list(self.out)), 1)                   # no second job

    def test_second_upload_finds_the_batch(self):
        lj, _ = fail_sheet_job(self.out, self.inp, "teddy bear for school", T1)
        _, first = im.import_file(self.c, self.user, f"hf_{T1}.png", self.data)
        self.wait()
        code, hit = im.import_file(self.c, self.user, "renamed.png", self.data)
        self.assertEqual((code, hit["generation"]), (200, f"G{first['id']:03d}"))

    def test_two_matches_ask_with_own_jobs_only(self):
        a, _ = fail_sheet_job(self.out, self.inp, "first sheet", T1)
        b, _ = fail_sheet_job(self.out, self.inp, "second sheet", T1)
        fail_sheet_job(self.out, self.inp, "someone else", T1, user="stranger")
        with self.assertRaises(im.ImportError) as got:
            im.import_file(self.c, self.user, f"hf_{T1}.png", self.data)
        self.assertEqual(got.exception.code, 409)
        cands = got.exception.hint["candidates"]
        self.assertEqual({c["job"] for c in cands}, {a["id"], b["id"]})  # own only: the stranger's job is not offered
        self.assertTrue(all("prompt" in c and "at" in c and "thumb" in c for c in cands))
        code, r = im.import_file(self.c, self.user, f"hf_{T1}.png", self.data, job=a["id"])
        self.wait()
        self.assertEqual((code, r["job"]), (202, a["id"]))
        self.assertEqual(jobs.read(self.out, b["id"])["status"], "FAILED")

    def test_explicit_choice_must_be_failed(self):
        lj, _ = fail_sheet_job(self.out, self.inp, "teddy bear for school", T1)
        jobs.update(self.out, lj["id"], status="DONE")
        with self.assertRaises(im.ImportError) as got:
            im.import_file(self.c, self.user, f"hf_{T1}.png", self.data, job=lj["id"])
        self.assertEqual(got.exception.code, 409)

    def test_as_new_skips_the_link(self):
        lj, _ = fail_sheet_job(self.out, self.inp, "teddy bear for school", T1)
        code, r = im.import_file(self.c, self.user, f"hf_{T1}.png", self.data, as_new=True)
        self.wait()
        self.assertEqual(code, 202)
        self.assertNotIn("recovered", r)
        self.assertEqual(jobs.read(self.out, lj["id"])["status"], "FAILED")

    def test_video_attaches_to_the_jobs_own_destination(self):
        from stickerloom.flow import gates
        lj = jobs.create(self.out, "video", task="003", generation="G007",
                         request={"user": "local", "label": "teddy dance", "sheet": "A2", "model": "kling-v3"})
        jobs.claim(self.out, lj["id"], T2)
        jobs.fail(self.out, lj["id"], "certificate verify failed")
        (self.out / "G007").mkdir(parents=True)                                     # the batch folder the recovered video belongs to
        res = {"error": None, "video_sheets": [{"id": "A2", "status": "APPROVED"}]}
        with patch.object(pl, "read_result", return_value=res), \
                patch("stickerloom.engine.ffmpeg.probe", return_value={"codec": "vp9", "width": 512, "height": 512}), \
                patch.object(gates, "attach_video") as put, patch.object(gates, "slice_video"):
            code, r = im.import_file(self.c, self.user, f"hf_20261004_033516_{T2}.mp4", b"stored video")
            self.wait()
            self.assertEqual((code, r["job"], r["sheet"]), (202, lj["id"], "A2"))
            put.assert_called_once()
            self.assertEqual((put.call_args.args[1], put.call_args.args[2]), (7, "A2"))  # the job's own batch and sheet
        done = jobs.read(self.out, lj["id"])
        self.assertEqual((done["status"], done["recovered_by"]), ("DONE", "manual import"))

    def test_candidates_route_shape(self):
        lj, _ = fail_sheet_job(self.out, self.inp, "teddy bear for school", T1)
        rows = im.candidates(self.out, T1, "local")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job"], lj["id"])
        self.assertIn("prompt", rows[0])


if __name__ == "__main__":
    unittest.main()
