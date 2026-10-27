"""Phase 2 S2: jobs as files (create/claim/done/fail/requeue), the ticket-first rule, the ledger,
the prompt lab (offline) and the slot reviewer. A fake file stands in for the operator's download."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from stickerloom.generation import jobs
from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "in" / "Images_gen").mkdir(parents=True)
        (self.tmp / "in" / "videos_gen").mkdir(parents=True)
        self.out = self.tmp / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fake_operator_flow(self):
        j = jobs.create(self.out, "sheet", task="011", request={"prompt": "falcon"})
        self.assertEqual((j["id"], j["status"], j["provider"]), ("J001", "REQUESTED", "higgsfield-cli"))
        with self.assertRaises(jobs.JobError):  # done before claim
            jobs.done(self.out, "J001", __file__, "m")
        j = jobs.claim(self.out, "J001", "higgs-123")
        self.assertEqual((j["status"], j["external_task_id"]), ("CLAIMED", "higgs-123"))
        with self.assertRaises(jobs.JobError):  # claim twice
            jobs.claim(self.out, "J001", "higgs-123")
        fake = self.tmp / "sheet.png"
        fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        j = jobs.done(self.out, "J001", str(fake), "higgs-img-v1", cost=2.5)
        self.assertEqual(j["status"], "DONE")
        self.assertTrue((jobs.job_dir(self.out, "J001") / "result.png").is_file())
        self.assertEqual(j["result"]["sha256"], jobs.read(self.out, "J001")["result"]["sha256"])
        self.assertEqual([x["status"] for x in jobs.list(self.out)], ["DONE"])
        self.assertEqual(jobs.list(self.out, "REQUESTED"), [])

    def test_claim_stores_the_ticket_first(self):
        """A crashed operator re-run resumes by ticket instead of paying twice (rule 10)."""
        from stickerloom.generation import tasks as _t
        t = _t.reserve(self.out, self.tmp / "in", "falcon dancing")
        j = jobs.create(self.out, "sheet", task=t["id"], request={"prompt": "falcon"})
        jobs.claim(self.out, j["id"], "higgs-999")
        back = _t.read_task(self.out, t["id"])
        self.assertEqual(back["external_task_id"], "higgs-999")

    def test_fail_and_requeue(self):
        j = jobs.create(self.out, "video", request={})
        jobs.fail(self.out, j["id"], "provider 500")
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "FAILED")
        jobs.requeue(self.out, j["id"])
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "REQUESTED")
        with self.assertRaises(jobs.JobError):  # nothing to re-queue
            jobs.requeue(self.out, j["id"])

    def test_a_dismissed_failed_job_stays_off_the_queue_and_a_running_one_cannot_be(self):
        j = jobs.create(self.out, "sheet", request={})
        with self.assertRaises(jobs.JobError) as cm:
            jobs.dismiss(self.out, j["id"], by="U1")
        self.assertEqual(cm.exception.code, 409, "a job that can still spend never vanishes")
        jobs.fail(self.out, j["id"], "provider 500")
        d = jobs.dismiss(self.out, j["id"], by="U1")["dismissed"]
        self.assertEqual(d["by"], "U1")
        self.assertEqual(jobs.dismiss(self.out, j["id"], by="U2")["dismissed"], d, "the first click is the record")
        listed = next(x for x in jobs.list(self.out) if x["id"] == j["id"])
        self.assertEqual((listed["status"], listed["dismissed"]["by"]), ("FAILED", "U1"), "the job stays; the queue reads `dismissed`")

    def test_stale_jobs_show_timeout(self):
        j = jobs.create(self.out, "sheet", request={})
        p = jobs._path(self.out, j["id"])
        old = dict(json.loads(p.read_text(encoding="utf-8")))
        old["created_at"] -= jobs.timeout_s() + 10
        p.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "TIMEOUT")
        self.assertTrue(jobs.dismiss(self.out, j["id"], by="U1")["dismissed"])      # listed as TIMEOUT, so its x works (it used to 409 on every poll)
        jobs.requeue(self.out, j["id"])
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "REQUESTED")

    def test_every_call_is_a_ledger_line(self):
        j = jobs.create(self.out, "sheet", request={})
        jobs.claim(self.out, j["id"], "t")
        fake = self.tmp / "s.png"
        fake.write_bytes(b"data")
        jobs.done(self.out, j["id"], str(fake), "m")
        j2 = jobs.create(self.out, "video", request={})
        jobs.fail(self.out, j2["id"], "boom")
        lines = (self.out / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()
        kinds = [json.loads(l)["kind"] for l in lines]
        self.assertEqual(kinds, ["IMAGE_SHEET", "VIDEO"])
        self.assertTrue(all("bytes" not in l for l in lines))

    def test_api_round_trip(self):
        srv, _ = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def req(method, path, body=None):
                h = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                h.request(method, path, json.dumps(body) if body is not None else None,
                          {"Content-Type": "application/json"})
                r = h.getresponse()
                raw = r.read()
                h.close()
                try:
                    return r.status, json.loads(raw)
                except ValueError:
                    return r.status, raw
            s, j = req("POST", "/api/jobs", {"kind": "sheet", "request": {"prompt": "x"}})
            self.assertEqual((s, j["status"]), (200, "REQUESTED"))
            jid = j["id"]
            self.assertEqual(req("GET", "/api/jobs")[0], 200)
            s, _ = req("POST", f"/api/jobs/{jid}/claim", {"ticket": "t1"})
            self.assertEqual(s, 200)
            s, _ = req("POST", f"/api/jobs/{jid}/claim", {"ticket": "t1"})
            self.assertEqual(s, 409)  # claim twice is refused
            fake = self.tmp / "r.png"
            fake.write_bytes(b"png")
            s, done = req("POST", f"/api/jobs/{jid}/done", {"file": str(fake), "model": "m"})
            self.assertEqual((s, done["status"]), (200, "DONE"))
            self.assertEqual(req("GET", f"/api/jobs/{jid}")[1]["status"], "DONE")
            s, _ = req("POST", "/api/jobs", {"kind": "poster"})
            self.assertEqual(s, 400)
        finally:
            srv.shutdown()

    def test_prompt_lab_offline(self):
        from stickerloom.cli import main
        self.assertEqual(main(["prompt", "lab"]), 0)
        self.assertEqual(main(["prompt", "green frog with big eyes", "--grid", "2x2"]), 0)
        self.assertEqual(main(["prompt", ""]), 1)

    def test_slot_reviewer(self):
        from stickerloom.generation import expander
        from stickerloom.services import llm
        key, loaded = os.environ.pop("OPENAI_API_KEY", None), llm._ENV_LOADED
        prov = os.environ.get("STICKERLOOM_LLM_PROVIDER")
        os.environ["STICKERLOOM_LLM_PROVIDER"] = "openai"   # no key + openai = no backend, even when LM Studio runs here
        llm._ENV_LOADED = True  # never read the real key here: no live calls in tests
        try:
            good_slots = {"subject_description": "a falcon", "cells": [
                {"label": f"pose {i}", "tags": [f"falcon_pose_{i}"], "emoji": "\U0001F600"} for i in range(1, 10)]}
            self.assertTrue(expander.review(good_slots, 9, "falcon", "falcon")["ok"])  # lint passes, no call
            bad = dict(good_slots)
            bad["cells"] = [dict(c, label="same") for c in good_slots["cells"]]
            self.assertFalse(expander.review(bad, 9, "falcon", "falcon")["ok"])  # duplicates fail the lint
            fake_yes = lambda s, u, **k: (json.dumps({"ok": True, "problems": []}), {"model": "fake"})
            fake_no = lambda s, u, **k: (json.dumps({"ok": False, "problems": ["boring"]}), {"model": "fake"})
            self.assertTrue(expander.review(good_slots, 9, "falcon", "falcon", complete=fake_yes)["ok"])
            rv = expander.review(good_slots, 9, "falcon", "falcon", complete=fake_no)
            self.assertEqual((rv["ok"], rv["problems"]), (False, ["boring"]))
        finally:
            if key is not None:
                os.environ["OPENAI_API_KEY"] = key
            os.environ.pop("STICKERLOOM_LLM_PROVIDER", None) if prov is None else os.environ.__setitem__("STICKERLOOM_LLM_PROVIDER", prov)
            llm._ENV_LOADED = loaded


class TransientProviderFailureTests(unittest.TestCase):
    """J048, 2026-10-03: a Kling job was rendering at Higgsfield and COMPLETED, while a 503 while waiting wrote
    status FAILED over it - the video was paid for, never downloaded, and the UI showed an error with no way
    forward. A transient provider failure while WAITING is not a failure: the job goes to TIMEOUT, stays
    resumable on the SAME ticket, and keeps counting against the daily cap."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.out = self.tmp / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_5xx_while_waiting_is_timeout_not_failed(self):
        from stickerloom.generation import higgsfield as hf
        j = jobs.create(self.out, "video", task="048", request={"prompt": "superhero paris", "t2v": True})
        jobs.claim(self.out, j["id"], "81ba5956-7337-4dbc-80c1-dd4f8b1f4f8d")
        jobs.update(self.out, j["id"], cost_estimate=4.5, model="kling3_0", kind="video")

        class Fake:
            """503 twice, then the video is there: the retry inside the wait must get it without a new job."""
            def __init__(self): self.waits = 0; self.creates = 0
            def cost(self, *a, **k): return 4.5
            def create_video(self, *a, **k): self.creates += 1; return "a-second-ticket"
            def wait(self, ticket, timeout_s=None):
                self.waits += 1
                if self.waits < 3:
                    raise hf.HiggsError("Higgsfield API error (HTTP 503). request failed with status 503 Service Unavailable")
                return {"result_url": "https://example.invalid/v.mp4"}
            def download(self, url, dest):
                Path(dest).parent.mkdir(parents=True, exist_ok=True); Path(dest).write_bytes(b"video"); return "sha", b"video"
        fake = Fake()
        res = jobs.fulfil(self.out, j["id"], hf=fake)
        self.assertEqual(res["status"], "DONE", res)
        self.assertEqual(fake.creates, 0, "a retry must never create a second paid job")
        self.assertEqual(fake.waits, 3, "the wait is retried while the provider answers 5xx")
        self.assertEqual(res["external_task_id"], "81ba5956-7337-4dbc-80c1-dd4f8b1f4f8d")

    def test_a_503_with_no_ticket_stays_failed(self):
        from stickerloom.generation import higgsfield as hf
        j = jobs.create(self.out, "video", task="049", request={"prompt": "superhero paris", "t2v": True})
        jobs.update(self.out, j["id"], kind="video", model="kling3_0")

        class Fake:
            def cost(self, *a, **k): return 4.5
            def create_video(self, *a, **k): raise hf.HiggsError("Higgsfield API error (HTTP 503). request failed with status 503 Service Unavailable")
            def wait(self, ticket, timeout_s=None): raise AssertionError("must not wait without a ticket")
            def download(self, url, dest): raise AssertionError("must not download")
        res = jobs.fulfil(self.out, j["id"], hf=Fake())
        self.assertEqual(res["status"], "FAILED", "nothing was created at the provider, so there is nothing to resume")

    def test_a_refused_prompt_stays_final(self):
        from stickerloom.generation import higgsfield as hf
        j = jobs.create(self.out, "video", task="050", request={"prompt": "nope", "t2v": True})
        jobs.claim(self.out, j["id"], "t-050"); jobs.update(self.out, j["id"], kind="video", model="kling3_0")

        class Fake:
            def cost(self, *a, **k): return 4.5
            def wait(self, ticket, timeout_s=None):
                raise hf.HiggsError("HTTP 400 the prompt was refused")
            def download(self, url, dest): raise AssertionError("must not download")
        res = jobs.fulfil(self.out, j["id"], hf=Fake())
        self.assertEqual(res["status"], "FAILED", "a 4xx is final: it is not a hiccup to be retried forever")

    def test_a_parked_timeout_still_counts_against_the_daily_cap(self):
        j = jobs.create(self.out, "video", task="051", request={"prompt": "x", "t2v": True})
        jobs.claim(self.out, j["id"], "t-051")
        jobs.update(self.out, j["id"], cost_estimate=4.5, model="kling3_0", kind="video")
        self.assertEqual(jobs._inflight(self.out), 4.5)
        jobs.update(self.out, j["id"], status="TIMEOUT")
        self.assertEqual(jobs._inflight(self.out), 4.5, "a TIMEOUT job is still rendering at the provider and will still be charged")
        jobs.update(self.out, j["id"], status="FAILED")
        self.assertEqual(jobs._inflight(self.out), 0.0, "a FAILED job is not in flight; resume() puts it back on the same ticket")

    def test_error_classification(self):
        from stickerloom.generation.higgsfield import HiggsError
        self.assertTrue(HiggsError("Higgsfield API error (HTTP 503). request failed with status 503").transient)
        self.assertTrue(HiggsError("HTTP 429").transient)
        self.assertFalse(HiggsError("HTTP 400 the prompt was refused").transient)
        self.assertFalse(HiggsError("Error: invalid prompt").transient, "a failure we cannot classify stays final")


class CloseLoopTests(unittest.TestCase):
    """A grey frame at the wrap, once per loop: close_loop() blended RGBA per channel, so a
    transparent tail frame dragged the character halfway to black at partial coverage. Coverage must not colour the fade."""

    def _clip(self, n=24):
        import numpy as np
        f = np.zeros((n, 64, 64, 4), np.uint8)
        for i in range(n):
            f[i, 20:40, 10 + i:26 + i, :3] = (200, 40, 40); f[i, 20:40, 10 + i:26 + i, 3] = 255
        f[1:, 20:40, 40:, :] = 0                      # the tail is empty: the character walked off
        return f

    def test_partial_coverage_keeps_the_colour(self):
        import numpy as np
        from stickerloom.engine.video import close_loop
        out = close_loop(self._clip(), 6)
        a = out[..., 3]
        part = out[..., :3][(a > 5) & (a < 250)]
        self.assertTrue(part.size, "the fade must produce partial coverage to be worth testing")
        for ch, want in enumerate((200, 40, 40)):
            self.assertAlmostEqual(float(part[:, ch].mean()), want, delta=6, msg=f"channel {ch} drifted towards black at partial coverage")

    def test_the_loop_still_closes(self):
        import numpy as np
        from stickerloom.engine.video import close_loop
        out = close_loop(self._clip(), 6)
        self.assertTrue(np.array_equal(out[0], out[-1]), "the last frame must still BE frame 0, or the wrap jumps")

    def test_a_short_clip_is_untouched(self):
        import numpy as np
        from stickerloom.engine.video import close_loop
        f = self._clip(n=8)
        self.assertTrue(np.array_equal(close_loop(f, 6), f))


if __name__ == "__main__":
    unittest.main()
