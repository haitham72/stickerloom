"""Live generation through the Higgsfield CLI, with a FAKE CLI (no credits are spent): the ticket-first rule, resume, the daily cap, the Kling 4k ban,
the model catalog, the credit roll-up, v2 prompts, and one full run through the console: sheet job -> stills -> approved video sheet -> Kling job -> sliced."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image

from stickerloom.generation import expander, higgsfield, jobs, model_calls, model_catalog, prompter, styles
from stickerloom.runtime import cache as cachemod
from stickerloom.services import llm
from stickerloom.generation import tasks as _tasks
from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.flow import pipeline as pl
from tests import synth
from tests.test_golden import shape_sheet


class FakeCLI:
    """Stands in for `higgsfield ... --json`: records every call and answers like the real CLI did on 2026-10-01."""

    def __init__(self, cost=2.0):
        self.calls, self.cost, self.n, self.kind_of, self.wait_hook = [], cost, 0, {}, None

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        if args[:2] == ["account", "status"]:
            return 0, json.dumps({"credits": 100.5, "email": "x@y", "subscription_plan_type": "creator"}), ""
        if args[:2] == ["generate", "cost"]:
            return 0, json.dumps({"credits": self.cost}), ""
        if args[:2] == ["generate", "create"]:
            self.n += 1
            jid = f"fake-job-{self.n}"
            self.kind_of[jid] = "mp4" if args[2] in ("kling3_0", "grok_video_v15", "grok_video_v15_lite") else "png"
            return 0, json.dumps([jid]), ""
        if args[:2] == ["generate", "wait"]:
            if self.wait_hook:
                self.wait_hook(args[2])
            return 0, json.dumps({"id": args[2], "status": "completed", "result_url": f"http://fake/{args[2]}.{self.kind_of[args[2]]}"}), ""
        if args[:2] == ["model", "list"]:
            return 0, "[]", ""
        return 1, "", "unknown command"


def png_bytes(arr) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    return buf.tobytes()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "in" / "Images_gen").mkdir(parents=True)
        (self.tmp / "in" / "videos_gen").mkdir(parents=True)
        self.out = self.tmp / "out"
        self.cli = FakeCLI()
        self.files = {}                                          # url suffix -> bytes the fake download writes
        self.patches = [mock.patch.object(higgsfield, "RUN", self.cli),
                        mock.patch.object(higgsfield, "binary", lambda: ("fake-hf", True)),
                        mock.patch.object(higgsfield, "download", self._download)]
        for p in self.patches:
            p.start()
        os.environ.pop("STICKERLOOM_DAILY_CREDITS", None)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.environ.pop("STICKERLOOM_DAILY_CREDITS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _download(self, url, dest):
        data = self.files.get(url.rsplit(".", 1)[-1], b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
        import hashlib
        return hashlib.sha256(data).hexdigest(), len(data)


class FulfilTests(Base):
    def sheet_job(self, **req):
        t = _tasks.reserve(self.out, self.tmp / "in", "falcon dancing")
        return jobs.create(self.out, "sheet", task=t["id"], request={"model": "nano_banana_flash", "options": {}, "prompt": "p", **req}), t

    def test_ticket_is_stored_before_waiting_and_the_run_is_priced_and_logged(self):
        j, t = self.sheet_job()
        seen = {}

        def at_wait(ticket):
            cur = jobs.read(self.out, j["id"])
            seen.update(status=cur["status"], ticket=cur["external_task_id"], mirrored=_tasks.read_task(self.out, t["id"])["external_task_id"])
        self.cli.wait_hook = at_wait
        done = jobs.fulfil(self.out, j["id"])
        self.assertEqual(seen, {"status": "CLAIMED", "ticket": "fake-job-1", "mirrored": "fake-job-1"})      # ticket first (rule 10)
        self.assertEqual((done["status"], done["cost"], done["model"], done["provider"]), ("DONE", 2.0, "nano_banana_flash", "higgsfield-cli"))
        self.assertTrue((self.out / done["result"]["file"]).is_file())
        create = next(c for c in self.cli.calls if c[:2] == ["generate", "create"])
        self.assertNotIn("--wait", create)                                                                   # create does not wait: the id would come too late
        self.assertEqual(create[create.index("--resolution") + 1], "2k")                                     # Nano Banana 2 at 2k by default
        row = json.loads((self.out / "model_calls.jsonl").read_text().splitlines()[-1])
        self.assertEqual((row["kind"], row["model"], row["cost"], row["external_task_id"]), ("IMAGE_SHEET", "nano_banana_flash", 2.0, "fake-job-1"))

    def test_several_paid_jobs_wait_at_the_same_time_but_are_created_one_by_one(self):
        """: several sheets "all at once". The lock now covers only deciding, the cap check, the creation and storing the ticket; the wait (a minute or two) is outside
        it, so jobs overlap there, up to STICKERLOOM_PAID_PARALLEL at once. Creation stays one at a time and every ticket is stored before anything waits (rule 10)."""
        import os, threading, time
        os.environ["STICKERLOOM_PAID_PARALLEL"] = "3"
        self.addCleanup(os.environ.pop, "STICKERLOOM_PAID_PARALLEL", None)
        js = [self.sheet_job()[0] for _ in range(3)]
        live = {"now": 0, "max": 0}
        mu = threading.Lock()

        def at_wait(ticket):
            with mu:
                live["now"] += 1
                live["max"] = max(live["max"], live["now"])
            time.sleep(0.4)
            with mu:
                live["now"] -= 1
        self.cli.wait_hook = at_wait
        res = []
        ts = [threading.Thread(target=lambda j=j: res.append(jobs.fulfil(self.out, j["id"]))) for j in js]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        self.assertEqual(sorted(r["status"] for r in res), ["DONE"] * 3)
        self.assertGreaterEqual(live["max"], 2, "the waits overlapped")
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 3)
        self.assertEqual(len({r["external_task_id"] for r in res}), 3, "three provider jobs, each with its own ticket")

    def test_the_parallel_limit_is_honoured_and_the_cap_counts_jobs_in_flight(self):
        import os, threading, time
        os.environ["STICKERLOOM_PAID_PARALLEL"] = "1"
        self.addCleanup(os.environ.pop, "STICKERLOOM_PAID_PARALLEL", None)
        live = {"now": 0, "max": 0}
        mu = threading.Lock()

        def at_wait(ticket):
            with mu:
                live["now"] += 1
                live["max"] = max(live["max"], live["now"])
            time.sleep(0.3)
            with mu:
                live["now"] -= 1
        self.cli.wait_hook = at_wait
        js = [self.sheet_job()[0] for _ in range(2)]
        ts = [threading.Thread(target=lambda j=j: jobs.fulfil(self.out, j["id"])) for j in js]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        self.assertEqual(live["max"], 1, "with a limit of 1 the old behaviour holds: one at a time")
        # the cap: 9 credits a day, 4 already spent above, 2 per call; two are in flight (4 + 2 + 2), the third (would be 10) is refused BEFORE it is created
        os.environ["STICKERLOOM_PAID_PARALLEL"] = "3"
        os.environ["STICKERLOOM_DAILY_CREDITS"] = "9"
        self.addCleanup(os.environ.pop, "STICKERLOOM_DAILY_CREDITS", None)
        before = len([c for c in self.cli.calls if c[:2] == ["generate", "create"]])
        a, b, c = (self.sheet_job()[0] for _ in range(3))
        started = threading.Event()
        self.cli.wait_hook = lambda t: (started.set(), time.sleep(0.5))
        outs = {}
        t1 = threading.Thread(target=lambda: outs.update(a=jobs.fulfil(self.out, a["id"])))
        t1.start(); started.wait(10)
        t2 = threading.Thread(target=lambda: outs.update(b=jobs.fulfil(self.out, b["id"])))
        t2.start(); time.sleep(0.1)
        outs["c"] = jobs.fulfil(self.out, c["id"])
        [t.join(30) for t in (t1, t2)]
        self.assertEqual((outs["c"]["status"], "daily credit cap" in (outs["c"]["error"] or "")), ("FAILED", True), "4 spent + 2 + 2 in flight + 2 would pass 9")
        self.assertEqual(len([x for x in self.cli.calls if x[:2] == ["generate", "create"]]) - before, 2)

    def test_a_text_only_video_job_needs_no_start_image(self):
        """The particle effects ask Kling for a clip that starts and ends on an empty screen: there is no picture to start from (request.t2v). Any other video job still refuses a
        missing start image."""
        j = jobs.create(self.out, "video", request={"model": "kling3_0", "options": {}, "prompt": "an empty green screen, then a burst, then empty again", "t2v": True, "label": "effect"})
        done = jobs.fulfil(self.out, j["id"])
        self.assertEqual((done["status"], done["model"]), ("DONE", "kling3_0"))
        create = next(c for c in self.cli.calls if c[:2] == ["generate", "create"])
        self.assertNotIn("--start-image", create)
        self.assertNotIn("--end-image", create)
        j2 = jobs.create(self.out, "video", request={"model": "kling3_0", "options": {}, "prompt": "p"})
        again = jobs.fulfil(self.out, j2["id"])
        self.assertEqual(again["status"], "FAILED")
        self.assertIn("start image", again["error"])

    def test_a_claimed_job_resumes_by_its_ticket_and_never_creates_a_second_paid_job(self):
        j, _ = self.sheet_job()
        jobs.claim(self.out, j["id"], "fake-job-9")
        jobs.update(self.out, j["id"], cost_estimate=2.0)
        self.cli.kind_of["fake-job-9"] = "png"
        done = jobs.fulfil(self.out, j["id"])
        self.assertEqual(done["status"], "DONE")
        self.assertFalse([c for c in self.cli.calls if c[:2] == ["generate", "create"]])

    def test_a_retry_waits_for_the_same_higgsfield_job_and_never_pays_twice(self):
        j, _ = self.sheet_job()
        boom = {"n": 0}

        def flaky(ticket):
            boom["n"] += 1
            if boom["n"] <= jobs.WAIT_RETRIES:
                raise higgsfield.HiggsError("Higgsfield API error (HTTP 503) request failed with status 503 Service Unavailable")
        self.cli.wait_hook = flaky
        failed = jobs.fulfil(self.out, j["id"])
        self.assertEqual((failed["status"], failed["external_task_id"]), ("TIMEOUT", "fake-job-1"))         # retries exhausted; the paid provider ticket still exists
        again = jobs.resume(self.out, j["id"])
        self.assertEqual((again["status"], again["external_task_id"], again["error"]), ("CLAIMED", "fake-job-1", None))
        done = jobs.fulfil(self.out, j["id"])
        self.assertEqual(done["status"], "DONE")
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1)               # one paid creation in total
        with self.assertRaises(jobs.JobError):
            jobs.resume(self.out, j["id"])                                                                     # a finished job has nothing to retry
        j2, _ = self.sheet_job()
        jobs.fail(self.out, j2["id"], "nothing was created")
        self.assertEqual(jobs.resume(self.out, j2["id"])["status"], "REQUESTED")                               # no ticket: asked again from scratch

    def test_a_requeue_of_a_ticketed_job_never_creates_a_second_paid_job(self):
        """`requeue` kept the ticket but set REQUESTED, so `fulfil` called `hf.create` again (a second charge) and `claim` overwrote the first ticket."""
        j, _ = self.sheet_job()
        self.cli.wait_hook = lambda t: (_ for _ in ()).throw(higgsfield.HiggsError("HTTP 503 while waiting"))
        failed = jobs.fulfil(self.out, j["id"])
        self.assertEqual((failed["status"], failed["external_task_id"]), ("TIMEOUT", "fake-job-1"))
        again = jobs.requeue(self.out, j["id"])
        self.assertEqual((again["status"], again["external_task_id"]), ("CLAIMED", "fake-job-1"))        # the ticket is waited for, not replaced
        self.cli.wait_hook = None
        self.assertEqual(jobs.fulfil(self.out, j["id"])["status"], "DONE")
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1)
        j2, _ = self.sheet_job()                                              # a job in any other state that holds a ticket is never created again either
        jobs.update(self.out, j2["id"], external_task_id="fake-job-7", status="REQUESTED")
        with self.assertRaises(jobs.JobError) as cm:
            jobs.fulfil(self.out, j2["id"])
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1)

    def test_two_runs_of_one_job_pay_once_and_the_loser_does_not_fail_the_winner(self):
        """both read the status before the paid lock, the loser paid, and its 409 handler marked the winner's job FAILED."""
        j, _ = self.sheet_job()
        waiting, release = threading.Event(), threading.Event()

        def hold(ticket):
            waiting.set()
            release.wait(10)
        self.cli.wait_hook = hold
        results = {}
        t1 = threading.Thread(target=lambda: results.__setitem__(1, jobs.fulfil(self.out, j["id"])))
        t1.start()
        self.assertTrue(waiting.wait(10))
        t2 = threading.Thread(target=lambda: results.__setitem__(2, jobs.fulfil(self.out, j["id"])))
        t2.start()
        time.sleep(0.3)                                                       # the second run is now parked on the paid lock
        release.set()
        t1.join(30)
        t2.join(30)
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1)
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "DONE")
        self.assertEqual(results[1]["status"], "DONE")
        self.assertIn(results[2]["status"], ("CLAIMED", "DONE"))               # the loser reports what is true at that moment (the winner may still be waiting), it does not run, pay or fail anything

    def test_the_daily_cap_counts_a_call_until_its_row_is_written(self):
        """`done()` ran after the paid lock was released, so a second call could read the spend before the first was booked."""
        os.environ["STICKERLOOM_DAILY_CREDITS"] = "3"
        j1, _ = self.sheet_job()
        j2, _ = self.sheet_job()
        real, in_done, go = jobs.done, threading.Event(), threading.Event()

        def slow_done(*a, **k):
            in_done.set()
            time.sleep(0.4)                                                   # the window in which the second job used to read a spend of 0
            return real(*a, **k)
        out = {}
        with mock.patch.object(jobs, "done", slow_done):
            t1 = threading.Thread(target=lambda: out.__setitem__(1, jobs.fulfil(self.out, j1["id"])))
            t1.start()
            self.assertTrue(in_done.wait(10))
            out[2] = jobs.fulfil(self.out, j2["id"])
            t1.join(30)
        self.assertEqual(out[1]["status"], "DONE")
        self.assertEqual(out[2]["status"], "FAILED")
        self.assertIn("daily credit cap", out[2]["error"])
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1)

    def test_provider_failure_marks_the_job_failed_with_the_reason(self):
        j, _ = self.sheet_job()
        self.cli.wait_hook = lambda t: (_ for _ in ()).throw(higgsfield.HiggsError("boom"))
        with mock.patch.object(higgsfield, "wait", side_effect=higgsfield.HiggsError("the job ended as 'failed'")):
            done = jobs.fulfil(self.out, j["id"])
        self.assertEqual(done["status"], "FAILED")
        self.assertIn("failed", done["error"])

    def test_the_daily_cap_refuses_before_anything_is_paid(self):
        os.environ["STICKERLOOM_DAILY_CREDITS"] = "1"
        j, _ = self.sheet_job()
        done = jobs.fulfil(self.out, j["id"])
        self.assertEqual(done["status"], "FAILED")
        self.assertIn("daily credit cap", done["error"])
        self.assertFalse([c for c in self.cli.calls if c[:2] == ["generate", "create"]])

    def test_the_daily_cap_applies_to_every_caller(self):
        os.environ["STICKERLOOM_DAILY_CREDITS"] = "1"
        for uid in ("local", "someone-else", "unknown"):
            with self.subTest(user=uid):
                before = len([c for c in self.cli.calls if c[:2] == ["generate", "create"]])
                job, _ = self.sheet_job(user=uid, role="owner")
                done = jobs.fulfil(self.out, job["id"])
                self.assertEqual(done["status"], "FAILED")
                self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]) - before, 0)
                self.assertIn("daily credit cap", done["error"])

    def test_kling_4k_is_never_possible(self):
        with self.assertRaises(model_catalog.CatalogError):
            model_catalog.resolve("video", "kling3_0", {"mode": "4k"})
        with self.assertRaises(higgsfield.HiggsError):
            higgsfield.cost("kling3_0", {"mode": "4k"}, "x")
        self.assertFalse([c for c in self.cli.calls if c[:2] == ["generate", "cost"]])
        # an unknown option or value is refused, never passed through
        with self.assertRaises(model_catalog.CatalogError):
            model_catalog.resolve("image", "nano_banana_flash", {"resolution": "8k"})
        with self.assertRaises(model_catalog.CatalogError):
            model_catalog.resolve("image", "nano_banana_flash", {"nonsense": "1"})

    def test_catalog_defaults_and_selections(self):
        self.assertEqual(model_catalog.resolve("image", None, {}), ("nano_banana_flash", {"aspect_ratio": "1:1", "resolution": "2k"}))
        self.assertEqual(model_catalog.resolve("video", None, {}), ("kling3_0", {"aspect_ratio": "1:1", "sound": "off", "mode": "pro", "duration": "3"}))
        self.assertEqual(model_catalog.resolve("image", "gpt_image_2_5", {"variant": "sunburst"})[1]["variant"], "sunburst")
        self.assertEqual(model_catalog.resolve("video", "grok_video_v15", {"resolution": "1080p"})[1]["resolution"], "1080p")
        self.assertEqual(model_catalog.resolve("video", "grok_video_v15_lite", {"duration": "5"}), ("grok_video_v15_lite", {"aspect_ratio": "1:1", "resolution": "1080p", "duration": "5"}))
        with self.assertRaises(model_catalog.CatalogError):
            model_catalog.resolve("video", "grok_video_v15_lite", {"resolution": "720p"})          # animation is always 1080 or more
        ids = {m["id"] for m in model_catalog.IMAGE}
        self.assertTrue({"nano_banana_flash", "nano_banana_pro", "nano_banana_2_lite", "gpt_image_2", "gpt_image_2_5",
                         "seedream_v5_pro", "seedream_5_0_flash", "seedream_v5_lite"} <= ids)
        # every model of the full Higgsfield dump is selectable too, with options built from its own parameters
        model_catalog.set_dump({"image": [{"job_type": "flux_2", "display_name": "FLUX.2", "params": [{"name": "aspect_ratio", "type": "string", "enum": ["1:1", "16:9"]},
                                {"name": "prompt", "type": "string"}, {"name": "resolution", "type": "string", "default": "2k", "enum": ["1k", "2k"]}]}],
                                "video": [], "counts": {"image": 1}})
        try:
            self.assertEqual(model_catalog.resolve("image", "flux_2", {"resolution": "1k"}), ("flux_2", {"aspect_ratio": "1:1", "resolution": "1k"}))
            self.assertEqual([m["id"] for m in model_catalog.catalog()["more"]["image"]], ["flux_2"])
        finally:
            model_catalog.set_dump(None)

    def test_the_ledger_adds_up_what_was_spent_today(self):
        j, t = self.sheet_job()
        jobs.fulfil(self.out, j["id"])
        j2, _ = self.sheet_job()
        self.cli.cost = 4.5
        jobs.fulfil(self.out, j2["id"])
        self.assertEqual(model_calls.spent_today(self.out), 6.5)
        self.assertEqual(len(model_calls.read_rows(self.out)), 2)


class DownloadTlsTests(unittest.TestCase):
    """On some Windows PCs one malformed entry in the certificate store makes Python's default TLS context raise `[ASN1: NOT_ENOUGH_DATA]`. The Higgsfield CLI
    (node) still worked, the job was created and PAID, and then stickerloom's own download of the result failed: 'could not download the result: [ASN1: NOT_ENOUGH_DATA]'."""

    def test_the_result_download_uses_the_tolerant_tls_context(self):
        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"\x89PNG-bytes"
        seen = {}

        def fake_urlopen(url, timeout=None, context=None):
            seen["context"] = context
            return Answer()
        marker = object()
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("stickerloom.services.telegram._ssl_context", lambda: marker):
            sha, n = higgsfield.download("https://cdn.example/result.png", Path(td) / "sub" / "r.png")
            self.assertEqual((n, (Path(td) / "sub" / "r.png").read_bytes()), (10, b"\x89PNG-bytes"))
        self.assertIs(seen["context"], marker)

    def test_a_malformed_store_entry_is_skipped_not_fatal(self):
        import ssl
        from stickerloom.services import telegram
        try:
            import certifi
        except ImportError:
            self.skipTest("certifi is not installed")
        pem = Path(certifi.where()).read_text(encoding="ascii")
        good = ssl.PEM_cert_to_DER_cert(pem[pem.index("-----BEGIN CERTIFICATE-----"):pem.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")])
        store = [(b"\x30\x03garbage", "x509_asn", set()), (good, "x509_asn", set())]            # the bad one first: it must not stop the good one
        telegram._CTX = None
        try:
            with mock.patch.object(ssl, "create_default_context", side_effect=ssl.SSLError("[ASN1: NOT_ENOUGH_DATA] not enough data")), \
                    mock.patch.object(ssl, "enum_certificates", lambda name: store, create=True):
                ctx = telegram._ssl_context()
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(ctx.check_hostname)
            self.assertGreaterEqual(ctx.cert_store_stats()["x509_ca"], 1)
        finally:
            telegram._CTX = None


class PromptV2Tests(unittest.TestCase):
    def test_v2_prompts_avoid_the_words_that_caused_the_white_outline_and_the_checkerboard(self):
        p = prompter.expand("teddy bear", (3, 3))
        self.assertEqual(p["template_version"], prompter.TEMPLATE_VERSION)
        sheet = p["sheet_prompt"].lower()
        for bad in ("sticker", "grid", "cell", "gap", "row-major", "panel line"):
            self.assertNotIn(bad, sheet.replace("no tiled panels", ""), bad)
        self.assertIn("character 1:", sheet)
        self.assertIn("no outline", sheet)
        self.assertIn("single, seamless, solid pure green", sheet)

    def test_wide_emotions_one_per_mood_and_a_motion_for_every_character(self):
        p = prompter.expand("teddy bear", (3, 3))
        self.assertEqual(len({c["emoji"] for c in p["slots"]["cells"]}), 9)
        self.assertEqual(len({c["label"] for c in p["slots"]["cells"]}), 9)
        lines = [l for l in p["video_prompt"].splitlines() if ' (cell ' in l]
        self.assertEqual(len(lines), 9)
        self.assertTrue(all(', then ' in line and 'Action 1:' not in line for line in lines))
        self.assertEqual(prompter.expand("teddy bear", (3, 3))["slots"], p["slots"])          # deterministic
        self.assertNotEqual([c["label"] for c in prompter.expand("a fox", (3, 3))["slots"]["cells"]], [c["label"] for c in p["slots"]["cells"]])

    def test_a_saved_v1_plan_rebuilds_to_the_v1_wording(self):
        slots = prompter.expand("teddy bear", (3, 3))["slots"]
        v1 = prompter.render_plan(slots, "sheet_3x3", 1)
        self.assertIn("sticker sheet", v1["sheet_prompt"])
        self.assertIn("1. ", v1["sheet_prompt"])

    def test_style_presets_change_the_style_line_only(self):
        a = prompter.render_plan(dict(prompter.expand("teddy bear", (3, 3))["slots"], style_id="glossy_3d"), "sheet_3x3", 2)["sheet_prompt"]
        b = prompter.render_plan(dict(prompter.expand("teddy bear", (3, 3))["slots"], style_id="toon_shade"), "sheet_3x3", 2)["sheet_prompt"]
        self.assertIn(styles.PHRASE["glossy_3d"], a)
        self.assertIn(styles.PHRASE["toon_shade"], b)
        ids = [s["id"] for s in styles.PRESETS]
        self.assertGreaterEqual(len(ids), 12, "a choice, not six")
        self.assertEqual(len(ids), len(set(ids)), "an id is one preset")
        self.assertIn(styles.DEFAULT, ids)
        self.assertEqual(len({s["phrase"] for s in styles.PRESETS}), len(ids), "two presets never say the same thing")
        for s in styles.PRESETS:
            self.assertNotIn("sticker", s["phrase"].lower(), "the word sticker makes image models draw a white die-cut border: " + s["id"])
            self.assertTrue(s["label"] and s["hint"], s["id"])


class LiveConsoleTests(Base):
    def test_unspecified_http_plan_grid_defaults_to_2x2(self):
        status, plan = self.req('POST', '/api/plan', {'prompt': 'generic emojis', 'ai': False})
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan['grid'], [2, 2])
        self.assertEqual(len(plan['stickers']), 4)
        status, explicit = self.req('POST', '/api/plan', {'prompt': 'generic emojis', 'grid': '3x3', 'ai': False})
        self.assertEqual((status, len(explicit['stickers'])), (200, 9))

    """The whole chain through the HTTP API with the fake CLI: sheet job -> unchanged stills run -> G2 -> video sheet -> G3 -> Kling job -> sliced."""

    def setUp(self):
        super().setUp()
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.files["png"] = png_bytes(shape_sheet(1200, [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)]))

    def tearDown(self):
        self.c.wait_jobs(90)                       # every background provider job ends while the fake CLI is still plugged in
        self.srv.shutdown()
        super().tearDown()

    def req(self, method, path, body=None, headers=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json", **(headers or {})})
        r = h.getresponse(); data = r.read(); h.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def until(self, fn, what, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.3)
        self.fail("timeout: " + what)

    def test_history_lists_every_batch_five_at_a_time_and_survives_opening_another(self):
        import json as _j
        for n in range(1, 8):                                                                       # seven finished batches on disk
            d = self.out / f"G{n:03d}"
            (d / "slices").mkdir(parents=True)
            (d / "prompts.json").write_text("{}", encoding="utf-8")
            res = {"generation_id": f"G{n:03d}", "number": n, "prompt": f"batch {n}", "stage": "sliced", "error": None, "source": {"subject": "blob"},
                   "stickers": [{"index": i, "key": f"k{i}", "status": "READY", "png": f"slices/S{i}.png", "anim_status": "READY" if n % 2 else "NOT_REQUESTED"} for i in range(1, 10)]}
            (d / "result.json").write_text(_j.dumps(res), encoding="utf-8")
        p1 = self.req("GET", "/api/history?limit=5")[1]
        self.assertEqual(([i["generation_id"] for i in p1["items"]], p1["more"], p1["total"]), (["G007", "G006", "G005", "G004", "G003"], True, 7))
        p2 = self.req("GET", "/api/history?limit=5&offset=5")[1]
        self.assertEqual(([i["generation_id"] for i in p2["items"]], p2["more"]), (["G002", "G001"], False))
        self.assertEqual((p1["items"][0]["ready"], p1["items"][0]["animated"], len(p1["items"][0]["cells"]), p1["items"][0]["prompt"]), (9, 9, 9, "batch 7"))

    def _one_batch_on_disk(self, gid: str, grid, animated=()):
        d = self.out / gid
        (d / "slices").mkdir(parents=True)
        (d / "prompts.json").write_text("{}", encoding="utf-8")
        n = grid[0] * grid[1]
        res = {"generation_id": gid, "number": int(gid[1:]), "prompt": "a sad owl", "stage": "sliced", "error": None, "grid": list(grid),
               "source": {"subject": "owl"},
               "stickers": [{"index": i, "key": f"k{i}", "status": "READY", "png": f"slices/S{i}.png",
                             "anim_status": "READY" if i in animated else "NOT_REQUESTED"} for i in range(1, n + 1)]}
        (d / "result.json").write_text(json.dumps(res), encoding="utf-8")

    def test_every_history_card_carries_its_own_grid(self):
        """A batch's card shows its stickers as the sheet's own grid (3x3 or 2x2), not four thumbnails: GET /api/history reads the grid out of result.json and names every cell."""
        self._one_batch_on_disk("G002", (3, 3), animated=(1, 5, 9))
        self._one_batch_on_disk("G001", (2, 2), animated=(2,))
        items = self.req("GET", "/api/history?limit=5")[1]["items"]
        self.assertEqual(len(items), 1, "the same request groups both batches in one project")
        by = {b["generation_id"]: b for i in items for b in i["batches"]}
        g3, g2 = by["G002"], by["G001"]
        self.assertEqual(g3["grid"], [3, 3])
        self.assertEqual([c["index"] for c in g3["cells"]], list(range(1, 10)))
        self.assertEqual([(c["row"], c["col"]) for c in g3["cells"]], [(r, c) for r in range(3) for c in range(3)])
        self.assertEqual([c["animated"] for c in g3["cells"]], [True, False, False, False, True, False, False, False, True])
        self.assertTrue(g3["cells"][0]["png"].startswith("slices/S1.png?e="))                     # the cache-buster the browser needs
        self.assertEqual((g3["ready"], g3["animated"], g3["stage"]), (9, 3, "sliced"))
        self.assertEqual(g2["grid"], [2, 2])
        self.assertEqual([(c["row"], c["col"]) for c in g2["cells"]], [(0, 0), (0, 1), (1, 0), (1, 1)])
        self.assertNotIn("thumbs", g3, "the four-thumbnail shortcut is gone: a card draws the whole grid")

    def test_a_batch_without_a_grid_or_a_picture_still_has_a_card(self):
        """An unfinished batch (no grid read yet, one cell with no png) must not break the list: 3x3 is the default and a cell with no picture is still a cell."""
        d = self.out / "G001"
        (d / "slices").mkdir(parents=True)
        (d / "prompts.json").write_text("{}", encoding="utf-8")
        res = {"generation_id": "G001", "number": 1, "prompt": "", "stage": "requested", "error": None, "source": {"subject": ""},
               "stickers": [{"index": 1, "key": "waving", "status": "PENDING", "png": None}]}
        (d / "result.json").write_text(json.dumps(res), encoding="utf-8")
        it = self.req("GET", "/api/history?limit=5")[1]["items"][0]
        self.assertEqual((it["grid"], it["ready"], it["animated"]), ([3, 3], 0, 0))
        self.assertEqual([(c["index"], c["png"], c["status"]) for c in it["cells"]], [(1, None, "PENDING")])

    def test_models_account_usage_and_assets_endpoints(self):
        s, j = self.req("GET", "/api/models")
        self.assertEqual(s, 200)
        self.assertEqual((j["defaults"], j["default_style"]), ({"image": "nano_banana_flash", "video": "kling3_0"}, "flat_vector"))
        self.assertEqual([m["id"] for m in j["video"]], ["kling3_0", "grok_video_v15", "grok_video_v15_lite"])
        self.assertEqual([s["id"] for s in j["styles"]], [s["id"] for s in styles.PRESETS])      # the page gets exactly the presets, however many there are
        s, j = self.req("GET", "/api/higgsfield")
        self.assertEqual((s, j["available"], j["credits"], j["plan"]), (200, True, 100.5, "creator"))
        for kind, name in (("styles", "glossy_3d"), ("vendors", "openai"), ("vendors", "google")):
            s, raw = self.req("GET", f"/assets/{kind}/{name}")
            self.assertEqual(s, 200)
            self.assertIn(b"<svg", raw)                                                  # real file or generated placeholder, never a broken image
        self.assertEqual(self.req("GET", "/assets/styles/..%2Fserver")[0], 404)         # nothing outside the assets folder
        s, j = self.req("POST", "/api/live/cost", {"kind": "image"})
        self.assertEqual((s, j["credits"], j["model"]), (200, 2.0, "nano_banana_flash"))
        s, j = self.req("POST", "/api/live/cost", {"kind": "video", "model": "kling3_0", "options": {"mode": "4k"}})
        self.assertEqual(s, 400)

    def _ai_answer(self):
        names = ["laughing", "crying", "angry", "shocked", "in_love", "proud", "scared", "sleepy", "scheming"]
        return json.dumps({"subject_description": "a small cartoon owl", "cells": [
            {"label": f"owl {n.replace('_', ' ')} with a huge expression", "motion": f"the owl acts {n} with a big bounce", "key": n, "tags": [], "emoji": ["🦉"]} for n in names]})

    def test_the_ai_enhancer_is_only_used_when_asked_and_never_while_typing(self):
        calls = []

        def fake(system, user, **kw):
            calls.append(user)
            return self._ai_answer(), {"model": "fake-llm"}
        os.environ["STICKERLOOM_OUT"] = str(self.out)                                   # the expander's ledger line must not land in the real out/
        try:
            with mock.patch.object(llm, "complete", fake), mock.patch.object(llm, "configured", lambda: True):
                s, j = self.req("POST", "/api/plan", {"prompt": "owl", "grid": "3x3", "style_id": "flat_vector"})       # what the page sends while typing
                self.assertEqual((s, j["expanded_by"], calls), (200, "deterministic", []))
                s, j = self.req("POST", "/api/live/sheet", {"prompt": "owl", "ai": False})
                self.assertEqual((s, j["expanded_by"], calls), (200, "deterministic", []))                           # switch off: straight into the template
                s, j = self.req("POST", "/api/live/sheet", {"prompt": "owl", "ai": True})
                self.assertEqual((s, j["expanded_by"], len(calls)), (200, "ai", 1))                                  # switch on: expanded first, then sent
                job = self.until(lambda: (lambda x: x if x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "job")
                sent = next(c for c in self.cli.calls if c[:2] == ["generate", "create"] and "owl laughing" in c[c.index("--prompt") + 1])
                self.assertIn("a small cartoon owl", sent[sent.index("--prompt") + 1])                                # the AI's concepts are in what was sent
                self.assertEqual(job["cost"], 2.0)
            with mock.patch.object(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("quota"))), mock.patch.object(llm, "configured", lambda: True):
                s, j = self.req("POST", "/api/live/sheet", {"prompt": "owl", "ai": True})
                self.assertEqual((s, j["expanded_by"]), (200, "deterministic"))                                       # falls back, and says why
                self.assertIn("quota", j["expand_error"])
        finally:
            os.environ.pop("STICKERLOOM_OUT", None)

    def test_a_repeated_idempotency_key_never_starts_a_second_paid_job(self):
        """`/api/live/sheet|video` ignored the header, so a double click, a retry after a lost answer or a second tab paid twice."""
        mem = cachemod.Cache(force_memory=True)
        with mock.patch.object(cachemod, "default", lambda: mem):
            key = {"Idempotency-Key": "click-1"}
            s1, a = self.req("POST", "/api/live/sheet", {"prompt": "owl"}, key)
            s2, b = self.req("POST", "/api/live/sheet", {"prompt": "owl"}, key)
            self.assertEqual((s1, s2), (200, 200))
            self.assertEqual((b["job"], b.get("idempotent")), (a["job"], True))
            self.until(lambda: self.req("GET", "/api/jobs/" + a["job"])[1]["status"] == "DONE", "the one job")
            self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1)
            s3, c3 = self.req("POST", "/api/live/sheet", {"prompt": "owl"}, {"Idempotency-Key": "click-2"})     # a new click is a new job
            self.assertNotEqual(c3["job"], a["job"])
            s4, d = self.req("POST", "/api/live/sheet", {"prompt": "owl"})                                       # no key: nothing to compare, it just runs (as before)
            self.assertNotIn(d["job"], (a["job"], c3["job"]))

    def test_two_requests_in_flight_at_once_with_one_key_start_exactly_one_job(self):
        """The review's missing test: the key is held under a cache lock while the first request runs, so a second one that arrives in that window is refused (409), and a retry
        afterwards is answered with the first job. Sequential tests could not tell a lock from no lock."""
        mem = cachemod.Cache(force_memory=True)
        real = _tasks.reserve

        def slow(*a, **k):
            time.sleep(0.8)                                                  # the window in which the double click arrives
            return real(*a, **k)
        out = []
        go = threading.Barrier(2)

        def post():
            go.wait()
            out.append(self.req("POST", "/api/live/sheet", {"prompt": "owl"}, {"Idempotency-Key": "double-click"}))
        with mock.patch.object(cachemod, "default", lambda: mem), mock.patch.object(_tasks, "reserve", slow):
            ts = [threading.Thread(target=post) for _ in range(2)]
            [t.start() for t in ts]
            [t.join(60) for t in ts]
            codes = sorted(s for s, _ in out)
            self.assertEqual(codes, [200, 409], out)                        # one runs, the other is told it is still running
            first = next(j for s, j in out if s == 200)
            self.assertIn("still running", next(j for s, j in out if s == 409)["error"])
            s, again = self.req("POST", "/api/live/sheet", {"prompt": "owl"}, {"Idempotency-Key": "double-click"})
            self.assertEqual((s, again["job"], again.get("idempotent")), (200, first["job"], True))      # the retry gets the first answer
        self.until(lambda: self.req("GET", "/api/jobs/" + first["job"])[1]["status"] == "DONE", "the one job")
        self.assertEqual(len(list((self.out / "jobs").glob("J*.json"))), 1)
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1)

    def test_reference_images_are_uploaded_stored_and_sent_with_the_sheet_job(self):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request("POST", "/api/live/ref?name=angel.png", png_bytes(shape_sheet(300, [(150, 150)])), {"Content-Type": "application/octet-stream"})
        r = h.getresponse(); ref = json.loads(r.read()); h.close()
        self.assertEqual((r.status, ref["id"], ref["file"]), (200, "R001", "refs/R001.png"))
        self.assertTrue((self.out / ref["file"]).is_file())
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request("POST", "/api/live/ref?name=x.png", b"not an image", {}); r = h.getresponse(); r.read(); h.close()
        self.assertEqual(r.status, 400)                                                              # only real images are stored
        s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob", "refs": ["R001"], "model": "nano_banana_pro"})
        self.assertEqual(s, 200, j)
        self.until(lambda: self.req("GET", "/api/jobs/" + j["job"])[1]["status"] == "DONE", "job")
        create = next(c for c in self.cli.calls if c[:3] == ["generate", "create", "nano_banana_pro"])
        self.assertEqual(create[create.index("--image-references") + 1], str(self.out / "refs" / "R001.png"))
        self.assertIn(prompter.REFERENCE_CLAUSE, create[create.index("--prompt") + 1])
        job = self.req("GET", "/api/jobs/" + j["job"])[1]
        self.assertEqual((job["request"]["refs"], job["params"]["references"]), (["refs/R001.png"], 1))   # reproducible from the stored job
        # P12 of the UI/UX spec: a tweak or a new action says what the picture is FOR, instead of the default "change only the expression and the pose"
        clause = "Reference: the attached image is the sheet to keep. Apply only this change: cuter."
        s, k = self.req("POST", "/api/live/sheet", {"prompt": "blob", "refs": ["R001"], "model": "nano_banana_pro", "ref_clause": clause})
        self.assertEqual(s, 200, k)
        self.until(lambda: self.req("GET", "/api/jobs/" + k["job"])[1]["status"] == "DONE", "job with its own clause")
        text = [c for c in self.cli.calls if c[:3] == ["generate", "create", "nano_banana_pro"]][-1]
        text = text[text.index("--prompt") + 1]
        self.assertIn(clause, text)
        self.assertNotIn(prompter.REFERENCE_CLAUSE, text, "the default clause would contradict a detail change")
        s, k = self.req("POST", "/api/live/sheet", {"prompt": "blob", "model": "nano_banana_pro", "ref_clause": clause})
        self.until(lambda: self.req("GET", "/api/jobs/" + k["job"])[1]["status"] == "DONE", "job without a picture")
        text = [c for c in self.cli.calls if c[:3] == ["generate", "create", "nano_banana_pro"]][-1]
        self.assertNotIn(clause, text[text.index("--prompt") + 1], "no picture attached: no sentence about it")
        self.assertEqual(self.req("POST", "/api/live/sheet", {"prompt": "blob", "refs": ["R001"], "ref_clause": "x" * 7000})[0], 400)
        self.assertEqual(self.req("POST", "/api/live/sheet", {"prompt": "blob", "refs": ["R999"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/live/sheet", {"prompt": "blob", "refs": ["R001"] * 5})[0], 400)
        model_catalog.set_dump({"image": [{"job_type": "no_refs", "display_name": "No Refs", "params": [{"name": "prompt", "type": "string"}]}], "video": [], "counts": {}})
        try:
            s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob", "refs": ["R001"], "model": "no_refs"})
            self.assertEqual(s, 400)
            self.assertIn("does not take reference images", j["error"])
        finally:
            model_catalog.set_dump(None)

    def test_one_click_animation_builds_and_approves_the_video_sheet_itself(self):
        s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob"})
        job = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "sheet job")
        gid = int(job["generation"][1:])
        self.until(lambda: (lambda x: x if x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{gid}")[1]), "stills")
        s, v = self.req("POST", "/api/live/video", {"generation": gid})                     # no sheet named: the click is the decision
        self.assertEqual(s, 200, v)
        st = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertIn(next(x for x in st["video_sheets"] if x["id"] == "A1")["status"], ("APPROVED", "VIDEO_RETURNED", "SLICED", "VIDEO_BLOCKED"))
        create = self.until(lambda: next((c for c in self.cli.calls if c[:3] == ["generate", "create", "kling3_0"]), None), "the Kling create call")   # sent by the job thread
        self.assertEqual(create[create.index("--mode") + 1], "pro")                          # always pro: 1440 px, never std
        prompt = create[create.index("--prompt") + 1]
        self.assertIn("Create a very expressive animation of each character, each having its own personality with a wide range of emotions.", prompt)
        self.assertIn('animate concurrently', prompt)
        lines = [line for line in prompt.splitlines() if ' (cell ' in line]
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(', then ' in line for line in lines))
        self.assertNotIn('Action 1:', prompt)
        self.assertLess(len(prompt), 2500, 'two short local actions per cell, not a long sheet-wide storyboard')
        self.assertEqual(self.req("POST", "/api/live/cost", {"kind": "video", "model": "kling3_0", "options": {"mode": "std"}})[0], 400)   # std is not offered any more

    def _stills_ready(self, prompt="blob"):
        s, j = self.req("POST", "/api/live/sheet", {"prompt": prompt})
        job = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "sheet job")
        gid = int(job["generation"][1:])
        self.until(lambda: (lambda x: x if x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{gid}")[1]), "stills")
        return gid

    def _png(self, path):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request("GET", path); r = h.getresponse(); data = r.read(); h.close()
        return r.status, r.getheader("Content-Type"), data

    def _idle(self, gid):
        return self.until(lambda: (lambda x: x if not x["busy"] else None)(self.req("GET", f"/api/generations/{gid}")[1]), "the batch to be idle")

    def test_the_edge_is_a_preview_until_applied_then_one_snapshot_that_undo_restores(self):
        import io
        gid = self._stills_ready()
        slices = pl.gen_dir(self.out, gid) / "slices"
        stamp = lambda: sorted((p.name, p.stat().st_mtime_ns) for p in slices.glob("*"))
        before, files = self.req("GET", f"/api/generations/{gid}")[1], stamp()

        def visible(o, e=0):
            s, ct, data = self._png(f"/api/generations/{gid}/edge_preview?index=1&outline={o}&erode={e}&px=200")
            self.assertEqual((s, ct), (200, "image/png"))
            return int((np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))[..., 3] > 128).sum())
        self.assertGreater(visible(12), visible(0))                                   # a stroke adds area, a trim takes it away
        self.assertLess(visible(0, 3), visible(0, 0))
        for o in range(0, 14, 2):                                                    # dragging a slider: every position is only a render
            visible(o)
        after = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual(stamp(), files)                                              # nothing was written or re-rendered
        self.assertEqual((after["outline_px"], after.get("edge_history")), (before["outline_px"], before.get("edge_history")))
        self.assertEqual(self._png(f"/api/generations/{gid}/edge_preview?index=1&outline=99&erode=0")[0], 400)

        # Apply: ONE snapshot (seeded with how the batch started), the whole batch re-rendered
        was = before["outline_px"]
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/edge", {"outline": 6, "erode": 1, "via": "apply"})[0], 202)
        res = self._idle(gid)
        self.assertEqual((res["outline_px"], res["erode_px"]), (6, 1))
        self.assertEqual([(h["outline"], h["erode"], h["via"]) for h in res["edge_history"]], [(was, 0, "initial"), (6, 1, "apply")])
        self.assertTrue(all((s.get("metrics") or {}).get("outline_px") == 6 for s in res["stickers"] if s["status"] == "READY"))
        # the same edge again changes nothing and adds no snapshot
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/edge", {"outline": 6, "erode": 1})[0], 202)
        self.assertEqual(len(self._idle(gid)["edge_history"]), 2)
        # Undo goes back to the previous snapshot (and is itself recorded, never a deletion)
        prev = res["edge_history"][-2]
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/edge", {"outline": prev["outline"], "erode": prev["erode"], "via": "undo"})[0], 202)
        res = self._idle(gid)
        self.assertEqual((res["outline_px"], res["erode_px"]), (was, 0))
        self.assertEqual([h["via"] for h in res["edge_history"]], ["initial", "apply", "undo"])
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/edge", {})[0], 400)

    def test_a_plan_handed_in_is_the_plan_that_runs_and_is_not_planned_again(self):
        """The chat shows a plan card and Create used to plan the request AGAIN (a second model call, different cells). The approved plan is now handed to the sheet job in-process and rebuilt
        from its cells and slots: the task holds exactly those cells."""
        from stickerloom.agent.graph import compact_plan
        plan = compact_plan(_tasks.preview("owl", "3x3", "flat_vector", False))
        keys = [f"approved_{i}" for i in range(1, 10)]
        for st, k in zip(plan["stickers"], keys):
            st["key"] = k
        plan["slots"]["cells"] = [dict(c, label=k.replace("_", " ")) for c, k in zip(plan["slots"]["cells"], keys)] if plan["slots"].get("cells") else plan["slots"].get("cells")
        r = self.c.live("sheet", {"prompt": "owl", "grid": "3x3", "style_id": "flat_vector", "ai": False}, base_plan=plan)
        task = _tasks.read_task(self.out, r["task"])
        self.assertEqual(task["request"]["slots"], plan["slots"], "the slots of the card are the slots of the task")
        self.c.wait_jobs(60)
        s, via_http = self.req("POST", "/api/live/sheet", {"prompt": "owl", "grid": "3x3", "style_id": "flat_vector", "ai": False, "base_plan": plan})
        self.assertEqual(s, 200)
        self.assertNotEqual(_tasks.read_task(self.out, via_http["task"])["request"]["slots"], plan["slots"], "the HTTP layer ignores a plan in the body: only code in this process can hand one in")
        self.c.wait_jobs(60)

    def test_a_pending_edge_becomes_real_exactly_at_video_generation_and_at_the_pack(self):
        gid = self._stills_ready()
        was = self.req("GET", f"/api/generations/{gid}")[1]["outline_px"]
        self.assertFalse(self.c.commit_edge(gid, None, None, "video"))                # nothing pending: nothing happens
        s, v = self.req("POST", "/api/live/video", {"generation": gid, "outline": 5, "erode": 1})   # image -> video
        self.assertEqual(s, 200, v)
        res = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual((res["outline_px"], res["erode_px"], res["edge_history"][-1]["via"]), (5, 1, "video"))
        self.c.wait_jobs(90)
        self.assertTrue(self.c.commit_edge(gid, 3, 0, "pack"))                         # video -> pack (the add endpoint calls this)
        res = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual((res["outline_px"], res["edge_history"][-1]["via"], res["edge_history"][0]["outline"]), (3, "pack", was))
        self.assertFalse(self.c.commit_edge(gid, 3, 0, "pack"))                        # already exactly this edge: no second snapshot
        self.assertEqual(len(self.req("GET", f"/api/generations/{gid}")[1]["edge_history"]), 3)

    def test_a_blue_screen_sheet_is_keyed_as_blue_and_marked_only_because_it_is_blue(self):
        from stickerloom.flow import pipeline as pl
        from stickerloom.engine import chroma
        green = shape_sheet(1200, [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)])
        blue = np.ascontiguousarray(green[..., [0, 2, 1]])                                  # the same sheet on a blue screen
        self.assertEqual(chroma.detect_key(green)[0], "green")
        self.assertEqual(chroma.detect_key(blue)[0], "blue")
        self.assertEqual(chroma.detect_key(np.full((300, 300, 3), 128, np.uint8), asked="blue")[0], "blue")     # no screen at all: the colour that was asked, and the sheet check blocks it
        self.assertEqual(pl.cfg_for({"key_colour": "blue"}, EngineConfig()).chroma, "blue")
        self.assertEqual(pl.cfg_for({}, EngineConfig()).chroma, "green")
        self.files["png"] = png_bytes(blue)
        gid = self._stills_ready("blue blob")
        res = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual(res["key_colour"], "blue")
        self.assertGreaterEqual(sum(1 for s in res["stickers"] if s["status"] == "READY"), 7)       # keyed, not refused by the green check
        self.assertFalse([c for c in res["verify"]["sheet"] if not c["ok"] and c["name"] == "background_is_key"])
        task = _tasks.read_task(self.out, res["task_id"])
        self.assertEqual((task["key_colour"], task.get("key_detected")), ("blue", True))           # green was asked, the model returned blue
        for i in range(1, 10):                                                                      # the video sheet follows: a blue screen, and the video prompt says nothing else
            self.req("POST", f"/api/generations/{gid}/review", {"gate": "still", "decision": "APPROVE", "index": i})
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/video_sheet")[0], 200)
        vs = np.asarray(Image.open(pl.gen_dir(self.out, gid) / "video_sheet" / "A1" / "sheet.png").convert("RGB"))
        self.assertEqual(vs[3, 3].tolist(), [0, 0, 255])
        self.assertIn("flat blue background", self.c.video_prompt_for(gid, "A1").lower())
        # a green sheet leaves no mark at all
        self.files["png"] = png_bytes(green)
        gid2 = self._stills_ready("green blob")
        res2 = self.req("GET", f"/api/generations/{gid2}")[1]
        self.assertNotIn("key_colour", res2)
        task2 = _tasks.read_task(self.out, res2["task_id"])
        self.assertNotIn("key_colour", task2)

    def test_generated_stays_out_of_git(self):
        from stickerloom.runtime.paths import REPO
        self.assertIn("generated/", (REPO / ".gitignore").read_text(encoding="utf-8").splitlines())

    def test_the_secrets_and_locks_of_out_are_never_committed(self):
        """`out/` is tracked on purpose (docs/architecture.md), but the token digests, the signed-link key, the locks and the logs are not part of that."""
        import subprocess
        from stickerloom.runtime.paths import REPO
        lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        for must in ("stickerloom/out/users.json", "stickerloom/out/.asset_secret", "stickerloom/out/.writer.lock", "stickerloom/out/.paid.lock", "stickerloom/out/*.log", "**/telegram.json"):
            self.assertIn(must, lines)
        try:
            tracked = subprocess.run(["git", "ls-files", "stickerloom/out"], cwd=REPO, capture_output=True, text=True, timeout=60).stdout.split()
        except (OSError, subprocess.SubprocessError):
            return                                                            # no git here (an exported copy): the ignore lines above are what is left to check
        for bad in ("stickerloom/out/users.json", "stickerloom/out/.asset_secret", "stickerloom/out/.writer.lock", "stickerloom/out/.paid.lock"):
            self.assertNotIn(bad, tracked)
        self.assertFalse([f for f in tracked if f.startswith("stickerloom/out/") and f.count("/") == 2 and f.endswith(".log")])

    def test_the_gap_is_a_slider_and_loop_is_a_choice(self):
        gid = self._stills_ready()
        # the preview sheet follows the gap: a fuller slot means bigger stickers on the same canvas
        import io
        def ink(fill):
            h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            h.request("GET", f"/api/generations/{gid}/sheet_preview?fill={fill}&px=300"); r = h.getresponse(); data = r.read(); h.close()
            self.assertEqual((r.status, r.getheader("Content-Type")), (200, "image/png"))
            im = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)
            self.assertEqual(max(im.shape[:2]), 300)
            return float(((np.abs(im - np.array([0, 255, 0])).max(-1)) > 40).mean())
        self.assertGreater(ink(0.9), ink(0.5) * 1.5)
        # an old sheet built at the default gap is rejected (not deleted) and rebuilt when the slider moved; Loop on brings the wording and the end image back
        for i in range(1, 10):
            self.assertEqual(self.req("POST", f"/api/generations/{gid}/review", {"gate": "still", "decision": "APPROVE", "index": i})[0], 200)
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/video_sheet")[0], 200)
        s, v = self.req("POST", "/api/live/video", {"generation": gid, "slot_fill": 0.6, "loop": True})
        self.assertEqual(s, 200, v)
        st = self.req("GET", f"/api/generations/{gid}")[1]
        sheets = {x["id"]: x for x in st["video_sheets"]}
        self.assertEqual((sheets["A1"]["status"], sheets["A2"]["slot_fill"]), ("REJECTED", 0.6))
        self.assertEqual(sheets["A1"]["slot_fill"], self.c.cfg.slot_fill)
        create = self.until(lambda: next((c for c in self.cli.calls if c[:3] == ["generate", "create", "kling3_0"]), None), "the Kling create call")      # the job runs in a thread
        self.assertEqual(create[create.index("--start-image") + 1], create[create.index("--end-image") + 1])
        self.assertIn("seamless loop", create[create.index("--prompt") + 1])
        # the plan keeps the choice too: Loop off is the default in the saved video prompt
        task = self.req("GET", "/api/tasks")[1]["tasks"][0]
        self.assertFalse(task["plan"]["slots"]["loop"])
        self.assertNotIn("seamless loop", task["plan"]["video_prompt"].lower())
        self.assertNotIn("end on the starting pose", task["plan"]["video_prompt"].lower())

    def test_from_a_request_to_a_sliced_kling_animation(self):
        s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob", "grid": "3x3", "style_id": "toon_shade", "outline": 12})
        self.assertEqual(s, 200, j)
        self.assertEqual((j["job"], j["estimate"], j["model"]), ("J001", 2.0, "nano_banana_flash"))
        task_id = j["task"]
        # the job finishes, the unchanged stills run starts by itself and the job points at the generation
        job = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/J001")[1]), "sheet job done")
        gid = int(job["generation"][1:])
        st = self.until(lambda: (lambda x: x if x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{gid}")[1]), "stills")
        self.assertEqual(st["template_version"], prompter.TEMPLATE_VERSION)
        self.assertIn(styles.PHRASE["toon_shade"], st["sheet_prompt"])
        self.assertEqual(sum(1 for t in st["stickers"] if t["status"] == "READY"), 9)
        self.assertEqual(self.req("GET", f"/api/tasks/{task_id}")[1]["external_task_id"], "fake-job-1")        # the Higgsfield id, stored before waiting
        # no video sheet yet and no approval: nothing may be sent to Kling
        s, j = self.req("POST", "/api/live/video", {"generation": gid, "sheet": "A1"})
        self.assertEqual(s, 404)
        for i in range(1, 10):
            s, j = self.req("POST", f"/api/generations/{gid}/review", {"gate": "still", "decision": "APPROVE", "index": i})
            self.assertEqual(s, 200, j)
        s, j = self.req("POST", f"/api/generations/{gid}/video_sheet")
        self.assertEqual(s, 200, j)
        s, j = self.req("POST", "/api/live/video", {"generation": gid, "sheet": "A1"})
        self.assertEqual(s, 409, j)                                                                            # G3 not approved: a human decides first
        self.assertIn("G3", j["error"])
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/review", {"gate": "video_sheet", "decision": "APPROVE", "index": "A1"})[0], 200)
        base = pl.out_path(self.out, st["generation_id"])
        entry = next(v for v in self.req("GET", f"/api/generations/{gid}")[1]["video_sheets"] if v["id"] == "A1")
        mp4 = self.tmp / "kling.mp4"
        synth.make_layout_video(mp4, np.array(Image.open(base / entry["file"]).convert("RGB")), json.loads((base / entry["layout"]).read_text()), size=600, frames=45)
        self.files["mp4"] = mp4.read_bytes()
        self.cli.cost = 4.5
        s, j = self.req("POST", "/api/live/video", {"generation": gid, "sheet": "A1", "model": "kling3_0", "options": {"mode": "pro"}})
        self.assertEqual((s, j["estimate"]), (200, 4.5), j)
        vjob = self.until(lambda: (lambda x: x if x["status"] in ("DONE", "FAILED") else None)(self.req("GET", f"/api/jobs/{j['job']}")[1]), "video job")
        self.assertEqual(vjob["status"], "DONE", vjob)
        create = [c for c in self.cli.calls if c[:3] == ["generate", "create", "kling3_0"]][0]
        self.assertEqual(create[create.index("--mode") + 1], "pro")
        self.assertNotIn("--end-image", create)                                                                     # Loop is off by default: no forced return to the first pose
        self.assertNotIn("loop", create[create.index("--prompt") + 1].lower())                                      # and no loop wording that makes the stickers bounce
        video_prompt = create[create.index("--prompt") + 1]
        self.assertIn('Top-left (cell 1):', video_prompt)
        self.assertIn('Top-right (cell 3):', video_prompt)
        final = self.until(lambda: (lambda x: x if next(v for v in x["video_sheets"] if v["id"] == "A1")["status"] in ("SLICED", "VIDEO_BLOCKED") and not x["busy"] else None)(
            self.req("GET", f"/api/generations/{gid}")[1]), "slicing")
        self.assertEqual(next(v for v in final["video_sheets"] if v["id"] == "A1")["status"], "SLICED")
        # a light preview of the returned video exists for the browser, next to the full-size original
        sheet = next(v for v in final["video_sheets"] if v["id"] == "A1")
        self.assertTrue((pl.out_path(self.out, st["generation_id"]) / sheet["video"]).is_file())
        self.assertTrue(sheet.get("preview") and (pl.out_path(self.out, st["generation_id"]) / sheet["preview"]).is_file())
        # changing the edge afterwards never loses the video: it is re-applied to the stored video, no new job and no credits
        jobs_before = len(self.req("GET", "/api/jobs")[1]["jobs"])
        s, r = self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 6, "erode": 1, "reslice": True})
        self.assertEqual((s, r["outline_px"], r["erode_px"], r.get("resliced")), (200, 6, 1, True), r)
        again = self.until(lambda: (lambda x: x if not x["busy"] and all(t["anim_status"] in ("READY", "FAILED") for t in x["stickers"]) else None)(
            self.req("GET", f"/api/generations/{gid}")[1]), "re-applied animations")
        self.assertEqual(next(v for v in again["video_sheets"] if v["id"] == "A1")["status"], "SLICED")
        notready = [(t["index"], t["anim_status"], t.get("anim_reason")) for t in again["stickers"] if t["anim_status"] != "READY"]
        self.assertGreaterEqual(9 - len(notready), 7, notready)                       # a stroke can tip a borderline synthetic clip over a Python check; never more than that
        self.assertTrue(all(st_ == "FAILED" and why for _, st_, why in notready), notready)   # and every one that is not ready says which check
        self.assertFalse([t for t in again["stickers"] if t["anim_status"] == "STALE"])
        self.assertEqual(len(self.req("GET", "/api/jobs")[1]["jobs"]), jobs_before)                                  # nothing was sent to Higgsfield again
        s, r2 = self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 6, "erode": 1, "reslice": True})   # the same edge again is a cache hit
        again2 = self.until(lambda: (lambda x: x if not x["busy"] and not any(t["anim_status"] in ("STALE", "PROCESSING") for t in x["stickers"]) else None)(
            self.req("GET", f"/api/generations/{gid}")[1]), "cached re-apply")
        self.assertTrue(all((t.get("anim_metrics") or {}).get("cache") == "hit" for t in again2["stickers"] if t["anim_status"] == "READY"))
        # a Size / Resize / Position TEST: ONE FRAME of one sticker, rendered on the fly, nothing in the batch changes (gates.anim_preview, 2026-10-15)
        one = next(t for t in again2["stickers"] if t["anim_status"] == "READY")
        s, pv = self.req("GET", f"/api/generations/{gid}/anim_preview?index={one['index']}&scale=0.8&resize=0&dx=0.1&frame=5")
        self.assertEqual(s, 200, pv)
        self.assertRegex(pv["url"], rf"^/out/G{gid:03d}/preview/anim-S{one['index']}\.png\?v=[0-9a-f]{{12}}$", "a file, never base64")
        self.assertEqual(self.req("GET", pv["url"].split("?")[0])[0], 200)
        self.assertEqual((pv["index"], pv["scale"], pv["mode"], pv["offset"], pv["frame"]), (one["index"], 0.8, "same", [0.1, 0.0], 5))
        self.assertGreater(pv["frames"], 5)
        s, hot = self.req("GET", f"/api/generations/{gid}/anim_preview?index={one['index']}&resize=1")
        self.assertEqual((s, hot["mode"]), (200, "hot"), hot)
        self.assertEqual(len(hot["core_canvas"]), 4)                                     # the hot spot the cut measured, drawn on the frame
        after = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual([(t.get("anim_scale"), t.get("anim_offset"), t.get("webm")) for t in after["stickers"]], [(t.get("anim_scale"), t.get("anim_offset"), t.get("webm")) for t in again2["stickers"]])
        self.assertEqual((after.get("anim_scale"), after.get("resize")), (again2.get("anim_scale"), again2.get("resize")), "a test is never stored")
        self.assertEqual(self.req("GET", f"/api/generations/{gid}/anim_preview?index={one['index']}&scale=3")[0], 400)
        self.assertEqual(self.req("GET", f"/api/generations/{gid}/anim_preview?index={one['index']}&dx=0.9")[0], 400)
        # the batch's Resize box and one sticker's Position + Size, stored (reslice off here: the cut itself is the slicing test above)
        s, rz = self.req("POST", f"/api/generations/{gid}/resize", {"on": False})
        self.assertEqual((s, rz["resize"]), (200, False), rz)
        s, po = self.req("POST", f"/api/generations/{gid}/position", {"index": one["index"], "offset": [0.1, -0.05], "scale": 0.9})
        self.assertEqual((s, po["anim_offset"], po["anim_scale"]), (200, [0.1, -0.05], 0.9), po)
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/position", {"index": one["index"], "offset": [0.8, 0]})[0], 400)
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/resize", {"on": "yes"})[0], 400)
        st2 = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual((st2["resize"], st2["stickers"][one["index"] - 1]["anim_offset"]), (False, [0.1, -0.05]))
        self.req("POST", f"/api/generations/{gid}/position", {"index": one["index"], "offset": None, "scale": None})
        h = self.req("GET", "/api/history?limit=1")[1]
        self.assertEqual((len(h["items"]), h["items"][0]["generation_id"], h["items"][0]["animated"], h["more"] is False), (1, st["generation_id"], sum(1 for t in again2["stickers"] if t["anim_status"] == "READY"), True))
        u = self.req("GET", "/api/usage")[1]
        self.assertEqual((u["credits_spent"], u["runs"][0]["generation"]), (6.5, st["generation_id"]))
        self.assertEqual(sorted(m["credits"] for m in u["by_model"]), [2.0, 4.5])


    # ---------- the Prompt tab: a hand-written prompt is what is sent (2026-10-02, `prompter.apply_custom`)

    def _creates(self, model):
        return [c for c in self.cli.calls if c[:3] == ["generate", "create", model]]

    def test_the_prompt_tab_sends_the_sheet_prompt_the_user_typed(self):
        """`sheet_prompt` + `from_generation`: a new sheet from that batch's own plan (same cells, same tags) with the user's own wording, and the batch says so."""
        gid = self._stills_ready("blob")
        st = self.req("GET", f"/api/generations/{gid}")[1]
        before = len(self._creates("nano_banana_flash"))
        mine = "3x3 sticker sheet, nine owls in equal cells.\nBackground: flat pure green (#00FF00).\nMy own wording, exactly as typed."
        s, j = self.req("POST", "/api/live/sheet", {"prompt": st["prompt"], "from_generation": gid, "sheet_prompt": mine})
        self.assertEqual(s, 200, j)
        call = self.until(lambda: (self._creates("nano_banana_flash") or [None])[before] if len(self._creates("nano_banana_flash")) > before else None, "the sheet create call")
        self.assertEqual(call[call.index("--prompt") + 1], mine)                                   # the CLI got the user's text, not the template's
        job = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "sheet job")
        new = int(job["generation"][1:])
        fresh = self.until(lambda: (lambda x: x if x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{new}")[1]), "stills")
        self.assertEqual((fresh["sheet_prompt"], fresh["custom_prompts"]), (mine, ["sheet_prompt"]))
        self.assertEqual([(t["index"], t["key"], t["tags"], t["emoji"]) for t in fresh["stickers"]],
                         [(t["index"], t["key"], t["tags"], t["emoji"]) for t in st["stickers"]])  # same cells and tags as the batch it came from
        self.assertEqual(self.req("GET", f"/api/tasks/{j['task']}")[1]["plan"]["custom"], {"sheet_prompt": mine})
        self.assertIs(self.req("GET", "/api/jobs/" + j["job"])[1]["request"]["custom_prompt"], True)
        # the key still guards a hand-written run: the same key twice pays once
        key = {"Idempotency-Key": "typed-once-" + uuid.uuid4().hex}
        other = mine + " A second wording."
        s1, first = self.req("POST", "/api/live/sheet", {"prompt": st["prompt"], "from_generation": gid, "sheet_prompt": other}, key)
        s2, again = self.req("POST", "/api/live/sheet", {"prompt": st["prompt"], "from_generation": gid, "sheet_prompt": other}, key)
        self.assertEqual((s1, s2, again["job"], again.get("idempotent")), (200, 200, first["job"], True))
        self.assertNotEqual(first["job"], j["job"])

    def test_a_hand_written_video_prompt_is_sent_verbatim_and_kept_with_the_sheet(self):
        gid = self._stills_ready("blob")
        mine = "animate every owl in place, no camera move, no cut"
        s, j = self.req("POST", "/api/live/video", {"generation": gid, "video_prompt": mine})
        self.assertEqual(s, 200, j)
        create = self.until(lambda: next((c for c in self._creates("kling3_0")), None), "the Kling create call")
        self.assertEqual(create[create.index("--prompt") + 1], mine)
        self.assertIs(self.req("GET", "/api/jobs/" + j["job"])[1]["request"]["custom_prompt"], True)
        st = self.until(lambda: (lambda x: x if next((v for v in x["video_sheets"] if v["id"] == "A1"), {}).get("video_prompt_sent") else None)(
            self.req("GET", f"/api/generations/{gid}")[1]), "the returned video")
        sheet = next(v for v in st["video_sheets"] if v["id"] == "A1")
        self.assertEqual((sheet["video_prompt_sent"], sheet["video_prompt_custom"]), (mine, True))

    def _video_for_the_sheet_being_sent(self, gid):
        """The fake provider answers with a video drawn from the sheet that was just sent (the latest approved one), so slicing really happens."""
        def hook(_jid):
            res = pl.read_result(self.out, gid)
            v = next(x for x in reversed(res["video_sheets"]) if x["status"] == "APPROVED")
            d, mp4 = pl.gen_dir(self.out, gid), self.tmp / f"{v['id']}.mp4"
            synth.make_layout_video(mp4, np.array(Image.open(d / v["file"]).convert("RGB")), json.loads((d / v["layout"]).read_text()), size=600, frames=45)
            self.files["mp4"] = mp4.read_bytes()
        self.cli.wait_hook = hook

    def _sliced(self, gid, aid):
        return self.until(lambda: (lambda x: x if next((v for v in x["video_sheets"] if v["id"] == aid), {}).get("status") in ("SLICED", "VIDEO_BLOCKED") and not x["busy"]
                                   and not any(t["anim_status"] == "PROCESSING" for t in x["stickers"]) else None)(self.req("GET", f"/api/generations/{gid}")[1]), f"{aid} sliced")

    def test_regenerate_video_in_the_same_batch_with_another_model_and_my_prompt(self):
        gid = self._stills_ready("blob")
        self._video_for_the_sheet_being_sent(gid)
        s, j = self.req("POST", "/api/live/video", {"generation": gid})
        self.assertEqual(s, 200, j)
        first = self._sliced(gid, "A1")
        self.assertEqual(next(v for v in first["video_sheets"] if v["id"] == "A1")["status"], "SLICED")
        d = pl.gen_dir(self.out, gid)
        old = {t["index"]: (d / t["webm"]).read_bytes() for t in first["stickers"] if t["anim_status"] == "READY"}
        self.assertTrue(old)
        mine = "every blob spins once in place, no camera move"
        s, j = self.req("POST", "/api/live/video", {"generation": gid, "redo": True, "video_prompt": mine,
                                                    "model": "grok_video_v15_lite", "options": {"duration": "5"}, "slot_fill": 0.6})
        self.assertEqual(s, 200, j)
        create = self.until(lambda: next(iter(self._creates("grok_video_v15_lite")), None), "the Grok Lite create call")
        self.assertEqual(create[create.index("--prompt") + 1], mine)                      # what the person read is what is sent
        self.assertIn("1080p", create)
        after = self._sliced(gid, "A2")
        sheets = {v["id"]: v for v in after["video_sheets"]}
        self.assertEqual((sheets["A1"]["status"], sheets["A2"]["status"]), ("SUPERSEDED", "SLICED"))
        self.assertEqual(sheets["A2"]["slots"], sheets["A1"]["slots"])                       # the same kept stickers, the same S#
        self.assertAlmostEqual(sheets["A2"]["slot_fill"], 0.6)                                # the gap chosen before re-animating is the one sent
        self.assertTrue((d / sheets["A1"]["video"]).is_file())                               # the retired video stays on disk
        self.assertEqual(sheets["A2"]["video_prompt_sent"], mine)
        self.assertEqual((after.get("sheet_model"), sheets["A1"].get("model"), sheets["A2"].get("model")),
                         ("nano_banana_flash", "kling3_0", "grok_video_v15_lite"))       # {current model} -> {next model}, both recorded
        self.assertEqual(after["generation_id"], first["generation_id"])                     # same batch, no new G###
        self.assertTrue(after["reviews"]["video_sheet"]["A1"]["superseded"])
        for t in after["stickers"]:
            if t["index"] in old:
                ver = t["anim_versions"][-1]
                self.assertEqual(ver["sheet"], "A1")
                self.assertEqual((d / ver["webm"]).read_bytes(), old[t["index"]])            # the earlier clip is kept as a version
            if t["anim_status"] == "READY":
                self.assertEqual(t["review"]["anim"], "PENDING")                             # G4 asks again for the new animation
        # the animation row: pick A1 back (re-cut from its stored video, free), the one in use cannot be removed, another one can
        jobs_before = len(self.req("GET", "/api/jobs")[1]["jobs"])
        s, r = self.req("POST", f"/api/generations/{gid}/pick_video", {"sheet": "A1"})
        self.assertEqual((s, r["changed"], r["was"]), (200, True, "A2"), r)
        back = self._sliced(gid, "A1")
        sheets = {v["id"]: v for v in back["video_sheets"]}
        self.assertEqual((sheets["A1"]["status"], sheets["A2"]["status"]), ("SLICED", "SUPERSEDED"))
        self.assertEqual(len(self.req("GET", "/api/jobs")[1]["jobs"]), jobs_before)                       # no new video, no credits
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/remove_video", {"sheet": "A1"})[0], 409)   # in use: pick another one first
        s, r = self.req("POST", f"/api/generations/{gid}/remove_video", {"sheet": "A2"})
        self.assertEqual((s, r["removed"]), (200, "A2"))
        self.assertEqual(next(v for v in self.req("GET", f"/api/generations/{gid}")[1]["video_sheets"] if v["id"] == "A2")["status"], "REJECTED")
        # while a returned video is being cut, regenerating is refused in words (never two cuts at once)
        from stickerloom.flow import gates
        res = pl.read_result(self.out, gid)
        next(v for v in res["video_sheets"] if v["id"] == "A1")["status"] = "VIDEO_RETURNED"
        pl.write_result(self.out, gid, res)
        with self.assertRaises(pl.PipelineError) as e:
            gates.redo_video(self.out, gid)
        self.assertIn("being cut", str(e.exception))

    def test_regenerate_is_the_next_generation_of_the_same_batch_and_one_is_picked(self):
        gid = self._stills_ready("blob")
        g1 = f"G{gid:03d}"
        fam = self.req("GET", f"/api/generations/{gid}/family")[1]
        self.assertEqual((fam["root"], fam["picked"], [m["n"] for m in fam["members"]]), (g1, g1, [1]))
        s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob", "from_generation": gid, "parent": g1, "regen_of": g1, "model": "nano_banana_pro"})
        self.assertEqual(s, 200, j)
        job = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "sheet job")
        g2 = job["generation"]
        self.assertNotEqual(g2, g1)                                                                      # a new generation...
        fam = self.req("GET", f"/api/generations/{int(g2[1:])}/family")[1]
        self.assertEqual(fam["root"], g1)                                                               # ...of the same batch
        self.assertEqual([(m["generation_id"], m["n"], m["relation"]) for m in fam["members"]], [(g1, 1, None), (g2, 2, "redo")])
        self.assertEqual(fam["picked"], g2)                                                             # the newest is picked until someone picks
        self.assertEqual(self.req("GET", f"/api/generations/{int(g2[1:])}")[1].get("sheet_model"), "nano_banana_pro")
        s, fam = self.req("POST", f"/api/generations/{gid}/pick", {})
        self.assertEqual((s, fam["picked"]), (200, g1))
        self.assertEqual(self.req("GET", f"/api/generations/{int(g2[1:])}/family")[1]["picked"], g1)    # one pick per batch, kept on the root
        # gen 3, then Delete gen 1 (the root, and the main): gen 2 and gen 3 stay ONE batch and the main moves to a survivor
        s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob", "from_generation": gid, "parent": g1, "regen_of": g1})
        g3 = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "sheet job 3")["generation"]
        self._idle(int(g3[1:]))
        s, r = self.req("POST", f"/api/generations/{gid}/remove", {})
        self.assertEqual(s, 200, r)
        self.assertEqual(r["batch"]["members"], [g2, g3])
        fam = self.req("GET", f"/api/generations/{int(g3[1:])}/family")[1]
        self.assertEqual((fam["root"], [m["generation_id"] for m in fam["members"]], fam["picked"]), (g2, [g2, g3], g3))

    def test_next_batch_plans_the_next_unused_actions_and_spends_nothing(self):
        gid = self._stills_ready("blob")
        res = pl.read_result(self.out, gid)
        n = len(self.cli.calls)
        s, nb = self.req("POST", "/api/plan/next", {"gens": [gid]})
        self.assertEqual(s, 200, nb)
        self.assertFalse(nb.get("complete"), nb)
        self.assertEqual((nb["next"]["kind"], len(nb["next"]["tokens"]), len(nb["stickers"])), ("actions", 9, 9))
        from stickerloom.generation import actions
        used = {h[0] for st in res["stickers"] if (h := actions.canonical_mood(st.get("key"), st.get("tags")))}
        self.assertFalse(used & set(nb["next"]["tokens"]), "nothing this batch already drew")
        self.assertEqual(list(nb["next"]["tokens"]), actions.next_tokens(used))
        self.assertTrue(nb["sheet_prompt"])
        self.assertFalse([c for c in self.cli.calls[n:] if c[:2] == ["generate", "create"]])              # a prompt to read, nothing spent
        self.assertEqual((nb["existing"], nb["batches_max"]), (1, 3), "1 batch on screen: batches 2 to 4 may be made in one Generate")
        s, more = self.req("POST", "/api/plan/more", {"prompt": nb["prompt"], "plan": nb, "n": 9, "gens": [gid]})
        self.assertEqual((s, more["existing"], len(more["plans"])), (200, 1, 2), "never past batch 4")
        drawn = [set(nb["next"]["tokens"])] + [{h[0] for st in p["stickers"] if (h := actions.canonical_mood(st.get("key"), st.get("tags")))} for p in more["plans"]]
        self.assertFalse(used & set().union(*drawn), "the session's own actions are skipped")
        self.assertEqual(len(set().union(*drawn)), 27, "and none twice between the new batches")
        self.assertFalse([c for c in self.cli.calls[n:] if c[:2] == ["generate", "create"]])
        self.assertEqual(self.req("POST", "/api/plan/next", {"gens": []})[0], 409)

    def test_an_empty_or_absurd_hand_written_prompt_is_refused_and_starts_nothing(self):
        for body in ({"prompt": "blob", "sheet_prompt": "   "}, {"prompt": "blob", "sheet_prompt": ""},
                     {"prompt": "blob", "sheet_prompt": 7}, {"prompt": "blob", "sheet_prompt": "x" * (prompter.MAX_PROMPT + 1)}):
            body["sheet_prompt"] = body["sheet_prompt"] if body["sheet_prompt"] != "" else " "
            s, j = self.req("POST", "/api/live/sheet", body)
            self.assertEqual(s, 400, (body["sheet_prompt"][:12] if isinstance(body["sheet_prompt"], str) else body["sheet_prompt"], s, j))
        gid = self._stills_ready("blob")
        self.assertEqual(self.req("POST", "/api/live/video", {"generation": gid, "video_prompt": "  "})[0], 400)
        self.assertEqual(self.req("POST", "/api/live/video", {"generation": gid, "video_prompt": "z" * (prompter.MAX_PROMPT + 1)})[0], 400)
        self.assertEqual(self.req("POST", "/api/live/sheet", {"prompt": "blob", "from_generation": "nope"})[0], 400)
        self.assertEqual([c for c in self.cli.calls if c[:3] == ["generate", "create", "kling3_0"]], [])      # nothing was sent
        self.assertEqual(len(self.req("GET", "/api/tasks")[1]["tasks"]), 1)                                   # only the one real task (the stills of gid)

    def test_a_member_cannot_start_a_new_sheet_from_a_batch_it_cannot_see(self):
        gid = self._stills_ready("blob")
        _, tok = self.c.users.create("Amira", "member", True)                                                # accounts on: every other caller now needs a token
        mine = {"Authorization": f"Bearer {tok}"}
        s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob", "from_generation": gid, "sheet_prompt": "mine"}, mine)
        self.assertEqual((s, j["error"]), (404, "No such batch"), "a stranger's batch is 404, never 403 and never readable")
        s, j = self.req("POST", "/api/live/sheet", {"prompt": "blob", "sheet_prompt": "mine"}, mine)
        self.assertEqual(s, 200, j)                                                                          # its own request is fine: only the other batch was refused
        self.until(lambda: len(self._creates("nano_banana_flash")) == 2 or None, "the member's own sheet create call")
        self.assertEqual(len(self._creates("nano_banana_flash")), 2)


if __name__ == "__main__":
    unittest.main()
